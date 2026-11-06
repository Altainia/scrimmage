"""The air system in a World: each cylinder's valve read from the robot program's HAL (a CTRE PCM's solenoids, or a
REV Pneumatic Hub's through the lockstep extension's "ScrimmagePH:<module>" SimDevice), its force written to the
cylinder's actuator, the compressor's current put on the battery, and the module's inputs sent back.

Every step, before mj_step:

  - each cylinder's valve state comes from command() if the Test tab set it, else from the HAL:
      single solenoid    output on -> "extend", off -> "retract" (invert swaps them)
      double solenoid    forward on -> "extend", reverse on -> "retract", neither -> "hold",
                         both -> "hold" and a one-time warning naming the valve
      no device ID       "retract" (the assembler already warned)
    A PCM channel's output is ("Solenoid", "<id>,<ch>", "<output"); a hub channel's is bit ch of
    ("ScrimmagePH", "<id>", "<solenoids").
    A disabled robot's solenoid outputs are off, as on a real robot.
  - the extension is the tendon's length now (carried forward from mj_step's pre-integration length, see
    tendon_length) past its retracted length, clamped to [0, stroke];
  - the AirSystem steps and each force (N, positive extends) goes to data.ctrl. MuJoCo keeps ctrl, so it is written
    every step.

The compressor runs (in the model) when the robot is enabled and the closed loop of any module a valve names is on
(with no valve naming one, any module the robot program made), or always_closed_loop is set (the Test tab's runs).
Its current joins the bus voltage solve as a constant load.
"""
from __future__ import annotations

import mujoco

from .. import i18n
from ..bindings import Update
from ..checks import Said
from ..protocol import HalState
from .model import AirConfig, AirSystem

PH_TYPE = "ScrimmagePH"     # the lockstep extension's SimDevice per REV Pneumatic Hub: "ScrimmagePH:<module>"
KEYS = ("tanks", "tank_ml", "compressor", "working_psi", "start_psi", "switch_on_psi", "switch_off_psi")


def parse_section(section: dict | None) -> AirConfig:
    """The config's [pneumatics] table as an AirConfig (the defaults for keys left out)."""
    section = section or {}
    if not isinstance(section, dict):
        raise ValueError(i18n.t("{pneumatics_table} must be a table", pneumatics_table="[pneumatics]"))
    for k in section:
        if k not in KEYS:
            raise ValueError(i18n.t("{pneumatics_table}: unknown key {key}; the keys are {keys}", key=repr(k),
                                    keys=', '.join(KEYS), pneumatics_table="[pneumatics]"))
    return AirConfig(**section)


class PneumaticsRuntime:
    def __init__(self, world, built, cfg_section: dict | None):
        self.world = world
        self.system = AirSystem(parse_section(cfg_section), built.cylinders)
        self.names = [c.name for c in built.cylinders]
        self.warnings: list[str] = []
        self.always_closed_loop = False     # the Test tab's runs: the compressor runs as if closed loop were on
        m = world.model
        self._specs = {c.name: c for c in built.cylinders}
        self._tendon = {c.name: m.tendon(f"{c.name}.cylinder").id for c in built.cylinders}
        self._actuator = {c.name: m.actuator(f"{c.name}.cylinder").id for c in built.cylinders}
        self._valves = {v["cylinder"]: v for v in built.bindings.get("valve", [])}
        # The CTRE PCMs and REV Pneumatic Hubs a valve names, by CAN number: their closed loop runs the compressor,
        # and they get its inputs.
        self._pcms = sorted({v["id"] for v in self._valves.values() if v["module"] == "PCM"})
        self._hubs = sorted({v["id"] for v in self._valves.values() if v["module"] == "PH"})
        # With no valve naming a module, the compressor is on whichever module the robot program makes (a
        # Compressor object alone makes one): a real module's closed loop runs its compressor on the pressure switch
        # whether or not any valve is wired to it. Found in the HAL every step (modules_in).
        self._named = bool(self._pcms or self._hubs)
        self._overrides: dict[str, str] = {}
        self._warned: set[str] = set()

    def command(self, name: str, state: str) -> None:
        """Set a cylinder's valve state ("extend", "retract" or "hold") in place of the HAL's, until reset()."""
        self.system.set_valve(name, state)      # checks the name and the state
        self._overrides[name] = state

    def spring_state(self, name: str) -> str:
        """The valve's state with its coils off: a double solenoid's detent holds; a single solenoid's spring
        retracts, or extends when the valve is inverted. One with no device ID counts as a single, not inverted."""
        v = self._valves.get(name)
        if v is not None and v["reverse"] is not None:
            return "hold"
        return "extend" if v is not None and v["invert"] else "retract"

    def reset(self) -> None:
        self.system.reset()
        self._overrides.clear()

    def reprime(self) -> None:
        """The cylinders were moved back without a full reset (World.reset_robot): refill their chambers for where
        they are now, keeping the tanks' air and the Test tab's overrides."""
        self.system.reprime()

    def extension(self, name: str) -> float:
        """How far the cylinder's rod is out, in m (0 to its stroke)."""
        spec = self._specs[name]
        x = self.tendon_length(name) - spec.length
        return min(max(x, 0.0), spec.stroke)

    def state(self) -> dict:
        """The air for get_state: the tanks' (stored) and the regulator's (working) pressures in psi, whether the
        compressor is running, and how far each cylinder's rod is out, in m (extension_m, as joints are in m)."""
        s = self.system
        return {"stored_psi": round(s.stored_psi, 2), "working_psi": round(s.working_psi, 2),
                "compressor_on": s.compressor_on,
                "cylinders": {n: {"extension_m": round(self.extension(n), 4)} for n in self.names}}

    def tendon_length(self, name: str) -> float:
        """The cylinder's tendon length (m) now. mj_step leaves ten_length at the position before it integrated,
        one step old; an air spring driven from a one-step-old position is unstable at any stiffness (nothing
        damps it enough). So it is carried forward by this step's motion: q_now = q_before + dt qvel, and qvel is
        already the new velocity, so L_now = ten_length + dt (ten_J . qvel). Exact for a slide; first order for
        turning joints. Right after a pose is set by hand (set_pose, move_body), ten_length is already current and
        qvel may be the old velocity, so for that one step the length is off by dt times it: a millimeter or less."""
        m, d = self.world.model, self.world.data
        tid = self._tendon[name]
        adr, nnz = int(m.ten_J_rowadr[tid]), int(m.ten_J_rownnz[tid])
        rate = float(d.ten_J[adr:adr + nnz] @ d.qvel[m.ten_J_colind[adr:adr + nnz]])
        return float(d.ten_length[tid]) + m.opt.timestep * rate

    def modules_in(self, hal: HalState) -> None:
        """With no valve naming a module, the CTRE PCMs and REV Pneumatic Hubs the robot program has made become the
        modules whose closed loop runs the compressor and that get its inputs."""
        if self._named:
            return
        self._pcms = sorted(int(dev) for typ, dev in hal.devices if typ == "CTREPCM" and dev.isdigit())
        self._hubs = sorted(int(dev) for typ, dev in hal.devices if typ == PH_TYPE and dev.isdigit())

    def before_physics(self, hal: HalState, enabled: bool, dt: float) -> None:
        for name in self.names:
            self.system.set_valve(name, self._overrides.get(name) or self._valve_state(name, hal, enabled))
        self.modules_in(hal)
        closed_loop = (self.always_closed_loop
                       or any(hal.get("CTREPCM", str(n), "<closed_loop", False) for n in self._pcms)
                       or any(hal.get(PH_TYPE, str(n), "<closed_loop", False) for n in self._hubs))
        forces = self.system.step(dt, {n: self.extension(n) for n in self.names}, enabled, bool(closed_loop))
        ctrl = self.world.data.ctrl
        for name, f in forces.items():
            ctrl[self._actuator[name]] = f

    def _valve_state(self, name: str, hal: HalState, enabled: bool) -> str:
        v = self._valves.get(name)
        if v is None:
            return "retract"            # no device ID: the assembler's warning says robot code can't move it
        if v["module"] == "PH":
            mask = int(hal.get(PH_TYPE, str(v["id"]), "<solenoids", 0) or 0)

            def coil(channel: int) -> bool:
                return enabled and bool(mask >> channel & 1)
        else:
            def coil(channel: int) -> bool:
                return enabled and bool(hal.get("Solenoid", f"{v['id']},{channel}", "<output", False))

        if v["reverse"] is None:
            return "extend" if coil(v["forward"]) != v["invert"] else "retract"
        fwd, rev = coil(v["forward"]), coil(v["reverse"])
        if v["invert"]:
            fwd, rev = rev, fwd
        if fwd and rev:
            if v["name"] not in self._warned:
                self._warned.add(v["name"])
                self._warn(Said(f"{v['name']}: both of the double solenoid's channels ({v['forward']} and "
                                f"{v['reverse']}) are on, so the valve holds where it is; turn one off",
                                lambda v=v: i18n.t("{valve}: both of the double solenoid's channels ({forward} and "
                                                   "{reverse}) are on, so the valve holds where it is; turn one off",
                                                   valve=v["name"], forward=v["forward"], reverse=v["reverse"])))
            return "hold"
        return "extend" if fwd else "retract" if rev else "hold"

    def _warn(self, text: str) -> None:
        """A warning made while the robot runs (on the simulation's or bridge's thread): machine text in the run's
        notes and MCP state, a Said so the page words it for people when it shows it."""
        self.warnings.append(text)
        self.world.warnings.append(text)

    def supply_and_slope(self, bus: float) -> tuple[float, float]:
        """The compressor's current (A) from the battery, which doesn't depend on the bus voltage here."""
        return self.system.compressor_amps, 0.0

    def sensor_updates(self) -> list[Update]:
        s = self.system
        amps = round(s.compressor_amps, 3)
        pcm = {">pressure_switch": s.switch_calls_for_air, ">current": amps, ">on": s.compressor_on}
        hub = {">pressure_switch": s.switch_calls_for_air, ">compressor_on": s.compressor_on,
               ">compressor_current": amps}
        return ([("CTREPCM", str(n), dict(pcm)) for n in self._pcms]
                + [(PH_TYPE, str(n), dict(hub)) for n in self._hubs])

    def missing_hub_warnings(self, hal: HalState) -> list[str]:
        """What to say, once the robot has had time to open its modules, about each valve whose REV Pneumatic Hub the
        HAL holds no ScrimmagePH device for: with none at all, the lockstep extension (the only relay for a hub) isn't
        loaded; with others there, the robot never opened that hub."""
        opened = sorted(int(dev) for typ, dev in hal.devices if typ == PH_TYPE and dev.isdigit())
        out = []
        for v in self._valves.values():
            if v["module"] != "PH" or v["id"] in opened:
                continue
            if not opened:
                out.append(Said(f"{v['name']} is on a REV Pneumatic Hub, which needs the lockstep extension "
                                "(`--scrimmage-lockstep` for RobotPy, the Gradle init script for Java)",
                                lambda v=v: i18n.t("{valve} is on a REV Pneumatic Hub, which needs the lockstep "
                                                   "extension ({flag} for RobotPy, the Gradle init script for Java)",
                                                   valve=v["name"], flag="`--scrimmage-lockstep`")))
            else:
                hubs = f"Pneumatic Hub{'s' if len(opened) > 1 else ''} {', '.join(map(str, opened))}"
                out.append(Said(f"{v['name']}: the robot never opened Pneumatic Hub {v['id']} (it opened {hubs}); "
                                "check the hub's number in the valve's ids",
                                lambda v=v, opened=opened: i18n.tn(
                                    "{valve}: the robot never opened Pneumatic Hub {hub} (it opened Pneumatic Hub "
                                    "{hubs}); check the hub's number in the valve's {ids_key}",
                                    "{valve}: the robot never opened Pneumatic Hub {hub} (it opened Pneumatic Hubs "
                                    "{hubs}); check the hub's number in the valve's {ids_key}", len(opened),
                                    valve=v["name"], hub=v["id"], hubs=", ".join(map(str, opened)), ids_key="ids")))
        return out

    def warn_missing_hubs(self, hal: HalState) -> list[str]:
        """missing_hub_warnings, each also put in the world's warnings once (so run reports show it); returns them."""
        msgs = self.missing_hub_warnings(hal)
        for msg in msgs:
            if msg not in self.world.warnings:
                self._warn(msg)
        return msgs
