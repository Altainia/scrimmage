"""The air system in a World: each cylinder's valve read from the robot program's HAL (a CTRE PCM's solenoids), its
force written to the cylinder's actuator, the compressor's current put on the battery, and the PCM's inputs sent back.

Every step, before mj_step:

  - each cylinder's valve state comes from command() if the Test tab set it, else from the HAL:
      single solenoid    output on -> "extend", off -> "retract" (invert swaps them)
      double solenoid    forward on -> "extend", reverse on -> "retract", neither -> "hold",
                         both -> "hold" and a one-time warning naming the valve
      no device ID       "retract" (the assembler already warned)
    A disabled robot's solenoid outputs are off, as on a real robot.
  - the extension is the tendon's length past its retracted length, clamped to [0, stroke];
  - the AirSystem steps and each force (N, positive extends) goes to data.ctrl. MuJoCo keeps ctrl, so it is written
    every step.

The compressor runs (in the model) when the robot is enabled and the closed loop of any PCM a valve names is on, or
always_closed_loop is set (the Test tab's runs). Its current joins the bus voltage solve as a constant load.
"""
from __future__ import annotations

import mujoco

from ..bindings import Update
from ..protocol import HalState
from .model import AirConfig, AirSystem

KEYS = ("tanks", "tank_ml", "compressor", "working_psi", "start_psi", "switch_on_psi", "switch_off_psi")


def parse_section(section: dict | None) -> AirConfig:
    """The config's [pneumatics] table as an AirConfig (the defaults for keys left out)."""
    section = section or {}
    if not isinstance(section, dict):
        raise ValueError("[pneumatics] must be a table")
    for k in section:
        if k not in KEYS:
            raise ValueError(f"[pneumatics]: unknown key {k!r}; the keys are {', '.join(KEYS)}")
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
        # The CTRE PCMs a valve names, by CAN number: their closed loop runs the compressor, and they get its inputs.
        self._pcms = sorted({v["id"] for v in self._valves.values() if v["module"] == "PCM"})
        self._overrides: dict[str, str] = {}
        self._warned: set[str] = set()

    def command(self, name: str, state: str) -> None:
        """Set a cylinder's valve state ("extend", "retract" or "hold") in place of the HAL's, until reset()."""
        self.system.set_valve(name, state)      # checks the name and the state
        self._overrides[name] = state

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
        x = float(self.world.data.ten_length[self._tendon[name]]) - spec.length
        return min(max(x, 0.0), spec.stroke)

    def before_physics(self, hal: HalState, enabled: bool, dt: float) -> None:
        for name in self.names:
            self.system.set_valve(name, self._overrides.get(name) or self._valve_state(name, hal, enabled))
        closed_loop = self.always_closed_loop or any(
            hal.get("CTREPCM", str(n), "<closed_loop", False) for n in self._pcms)
        forces = self.system.step(dt, {n: self.extension(n) for n in self.names}, enabled, bool(closed_loop))
        ctrl = self.world.data.ctrl
        for name, f in forces.items():
            ctrl[self._actuator[name]] = f

    def _valve_state(self, name: str, hal: HalState, enabled: bool) -> str:
        v = self._valves.get(name)
        if v is None:
            return "retract"            # no device ID: the assembler's warning says it won't move
        if v["module"] != "PCM":
            return "retract"            # a REV Pneumatic Hub's valves: read through its own device (not yet)

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
                self._warn(f"{v['name']}: both of the double solenoid's channels ({v['forward']} and "
                           f"{v['reverse']}) are on, so the valve holds where it is; turn one off")
            return "hold"
        return "extend" if fwd else "retract" if rev else "hold"

    def _warn(self, text: str) -> None:
        self.warnings.append(text)
        self.world.warnings.append(text)

    def supply_and_slope(self, bus: float) -> tuple[float, float]:
        """The compressor's current (A) from the battery, which doesn't depend on the bus voltage here."""
        return self.system.compressor_amps, 0.0

    def sensor_updates(self) -> list[Update]:
        s = self.system
        data = {">pressure_switch": s.switch_calls_for_air, ">current": round(s.compressor_amps, 3),
                ">on": s.compressor_on}
        return [("CTREPCM", str(n), dict(data)) for n in self._pcms]
