"""Bindings connect robot-program devices (PWM ports, encoders, gyros...) to things in the MuJoCo world.

Each binding reads outputs from HalState and/or writes inputs back as protocol updates.
"""

from __future__ import annotations

import math
import re
from typing import Any

import mujoco
import numpy as np

from . import i18n
from .motors import DCMotor, MOTORS, _m, get_motor
from .protocol import HalState

Update = tuple[str, str, dict[str, Any]]
NEUTRAL_EPS = 1e-3
LIMIT_EPS = 1e-3          # a joint this close to its range end counts as on the limit switch


def _joint(model: mujoco.MjModel, name: str) -> tuple[int, int]:
    jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
    if jid < 0:
        raise ValueError(i18n.t("Joint '{name}' not found in the MuJoCo model", name=name))
    if int(model.jnt_type[jid]) not in (int(mujoco.mjtJoint.mjJNT_HINGE), int(mujoco.mjtJoint.mjJNT_SLIDE)):
        raise ValueError(i18n.t("Joint '{name}' must be a hinge or slide joint", name=name))
    return int(model.jnt_qposadr[jid]), int(model.jnt_dofadr[jid])


# Phoenix 6 device kinds in `ctre = "<Kind>:<id>"` and the SimDevice names Phoenix registers for them.
CTRE_DEVICES = {"TalonFX": "Talon FX (v6)", "CANcoder": "CANcoder (v6)", "Pigeon2": "Pigeon 2 (v6)"}
_CTRE_KIND_FOR = {"motor": "TalonFX", "encoder": "CANcoder", "gyro": "Pigeon2"}


def _ctre_device(cfg: dict, kind: str) -> str:
    """The SimDevice name for `ctre = "TalonFX:1"` -> "Talon FX (v6)[1]" (an explicit `device` wins).

    `kind` is the binding list ("motor", "encoder", "gyro"), which fixes the Phoenix kind it accepts."""
    spec = str(cfg["ctre"])
    name, sep, ident = spec.partition(":")
    if name not in CTRE_DEVICES or not sep:
        raise ValueError(i18n.t("{ctre_setting}: expected \"{kind_id}\" with Kind one of {devices} (e.g. "
                                "\"{example}\")", devices=', '.join(CTRE_DEVICES),
                                ctre_setting="ctre = {}".format(repr(spec)), kind_id="<Kind>:<id>",
                                example="TalonFX:1"))
    want = _CTRE_KIND_FOR[kind]
    if name != want:
        raise ValueError(i18n.t("{ctre_setting} in a [[{kind}]] binding: only {want} is supported there", kind=kind,
                                want=want, ctre_setting="ctre = {}".format(repr(spec))))
    ident = ident.strip()
    if not ident.isdigit() or not 0 <= int(ident) <= 62:
        raise ValueError(i18n.t("{ctre_setting}: the CAN id must be an integer 0-62",
                                ctre_setting="ctre = {}".format(repr(spec))))
    if "device" in cfg:
        return str(cfg["device"])
    return f"{CTRE_DEVICES[name]}[{int(ident)}]"


# REV SPARK kinds in `rev = "<Kind>:<id>"` and the helper SimDevice names (`ScrimmageREV:<device>`) the robot-side
# helper registers for them. The Abs kinds are the absolute encoder wired to that SPARK's data port.
REV_DEVICES = {"SparkMax": "SparkMax", "SparkFlex": "SparkFlex"}
# `>motor` codes for the helper (HALSim can't carry a string into a SimDevice value); 0 = keep the SPARK's usual motor
REV_MOTOR_CODES = {"NEO": 1, "NEO550": 2, "NeoVortex": 3}
_REV_SHORT = re.compile(r"\s*Spark(Max|Flex)(Abs)?\s*:")


def binds_rev(raw) -> bool:
    """Does a raw config bind any REV SPARK (a `rev` key, or a SparkMax/SparkFlex short form in part ids)?"""
    if isinstance(raw, dict):
        return "rev" in raw or any(binds_rev(v) for v in raw.values())
    if isinstance(raw, list):
        return any(binds_rev(v) for v in raw)
    return isinstance(raw, str) and _REV_SHORT.match(raw) is not None


def binds_ph(raw) -> bool:
    """Does a raw config put a part's valve on a REV Pneumatic Hub (ids valve = "PH:..." or { ph = ... })?"""
    parts = raw.get("part", []) if isinstance(raw, dict) else []
    for part in parts if isinstance(parts, list) else []:
        ids = part.get("ids") if isinstance(part, dict) else None
        valve = ids.get("valve") if isinstance(ids, dict) else None
        if (isinstance(valve, str) and valve.strip().startswith("PH:")) or (isinstance(valve, dict) and "ph" in valve):
            return True
    return False
_REV_KINDS_FOR = {"motor": {"SparkMax": "SparkMax", "SparkFlex": "SparkFlex"},
                  "encoder": {"SparkMaxAbs": "SparkMax", "SparkFlexAbs": "SparkFlex"}}


def _rev_device(cfg: dict, kind: str) -> str:
    """The helper SimDevice name for `rev = "SparkMax:3"` -> "SparkMax[3]" (encoders: "SparkMaxAbs:3" -> "SparkMax[3]").

    `kind` is the binding list ("motor" or "encoder"), which fixes the REV kinds it accepts."""
    spec = str(cfg["rev"])
    name, sep, ident = spec.partition(":")
    allowed = _REV_KINDS_FOR[kind]
    if not sep or (name not in allowed and name not in REV_DEVICES and name.removesuffix("Abs") not in REV_DEVICES):
        raise ValueError(i18n.t("{rev_setting}: expected \"{kind_id}\" with Kind one of {allowed} (e.g. \"{example}\")",
                                allowed=', '.join(allowed), rev_setting="rev = {}".format(repr(spec)),
                                kind_id="<Kind>:<id>", example="{}:1".format(next(iter(allowed)))))
    if name not in allowed:
        raise ValueError(i18n.t("{rev_setting} in a [[{kind}]] binding: only {allowed} is supported there", kind=kind,
                                allowed=i18n.t(" or ", context="list separator").join(allowed),
                                rev_setting="rev = {}".format(repr(spec))))
    ident = ident.strip()
    if not ident.isdigit() or not 0 <= int(ident) <= 62:
        raise ValueError(i18n.t("{rev_setting}: the CAN id must be an integer 0-62",
                                rev_setting="rev = {}".format(repr(spec))))
    return f"{allowed[name]}[{int(ident)}]"


def _scrimmage_name(cfg: dict) -> str:
    """The fast-mode device name in `scrimmage = "<name>"` (any non-empty string without spaces)."""
    name = cfg["scrimmage"]
    if not isinstance(name, str) or not name or any(c.isspace() for c in name):
        raise ValueError(i18n.t("{scrimmage_setting}: expected a non-empty device name without spaces (e.g. "
                                "\"{fl_drive}\")", scrimmage_setting="scrimmage = {}".format(repr(name)),
                                fl_drive="fl_drive"))
    return name


def _one_of(cfg: dict, keys: tuple[str, ...], what: str) -> None:
    given = [k for k in keys if k in cfg]
    if len(given) > 1:
        raise ValueError(i18n.t("{what} sets {given}: give only one of {keys}", what=what,
                                given=i18n.t(" and ", context="list separator").join(given), keys=', '.join(keys)))


def resolve_device_key(hal: HalState, key: tuple[str, str]) -> str | None:
    """The device id a binding's `device_key` refers to, or None if the robot program never created it.

    A key is (type, device name), except when the device name starts with "<": then it is "<field=value"
    and names the first `type` device whose `<field` equals the integer value. HALSim names quadrature
    Encoders by index, so their key is ("Encoder", "<channel_a=<dio>")."""
    typ, dev = key
    if dev.startswith("<") and "=" in dev:
        field, _, value = dev.partition("=")
        return hal.find(typ, field, int(value))
    return dev if (typ, dev) in hal.devices else None


def pigeon_attitude(quat, omega_world) -> tuple[float, float, float, np.ndarray]:
    """(yaw, pitch, roll) in degrees and body-frame angular rates (deg/s) for a Pigeon 2.

    Pigeon 2 axes are X forward, Y left, Z up (the MJCF body frame here). Angles are intrinsic Z-Y-X
    Euler angles, each positive by the right-hand rule: yaw about Z (CCW from above), pitch about Y
    (so nose *down* is positive), roll about X (left side up is positive). Rates are the body's world
    angular velocity rotated into the body frame: [about X, about Y, about Z]."""
    yaw, pitch, roll = pigeon_angles(quat)
    rot = np.empty(9)
    mujoco.mju_quat2Mat(rot, np.asarray(quat, float))
    rates = rot.reshape(3, 3).T @ np.asarray(omega_world, float)
    return yaw, pitch, roll, np.degrees(rates)


def pigeon_angles(quat) -> tuple[float, float, float]:
    """`pigeon_attitude`'s (yaw, pitch, roll) in degrees, without the rates."""
    w, x, y, z = (float(v) for v in quat)
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


def _motor_from_cfg(cfg: dict) -> DCMotor:
    count = int(cfg.get("count", 1))
    spec = cfg.get("motor", "CIM")
    if isinstance(spec, dict):  # custom motor: {stall_torque=, stall_current=, free_current=, free_rpm=}
        base = _m(spec.get("voltage", 12.0), spec["stall_torque"], spec["stall_current"],
                  spec["free_current"], spec["free_rpm"])
        return base.gearbox(count)
    return get_motor(spec, count)


def _coupled_dofs(model: mujoco.MjModel, joint: str) -> list[tuple[int, float]]:
    """The dofs a motor's back-EMF damping goes on, as (dof, share of b): the motor's own joint, plus every joint
    tied to it by a joint equality (a stage chain). The constraint solver sees the damping force as an explicit
    smooth force, but MuJoCo then treats it implicitly per dof; if only the motor's dof is damped the chain
    comes apart and blows up. Splitting the damping in proportion to each dof's reference inertia (so every dof
    has the same b_i / M_ii) makes the implicit correction uniform and the chain stays together. With
    q_i = c_i * q_motor, the split keeps the dissipation: sum b_i c_i^2 = b."""
    jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint)
    ratio = {jid: 1.0}                              # d(q_j)/d(q_motor)
    todo = [jid]
    eqs = [e for e in range(model.neq) if int(model.eq_type[e]) == int(mujoco.mjtEq.mjEQ_JOINT) and model.eq_active0[e]]
    while todo:
        j = todo.pop()
        for e in eqs:
            dep, drv, c1 = int(model.eq_obj1id[e]), int(model.eq_obj2id[e]), float(model.eq_data[e, 1])
            if dep < 0 or drv < 0 or c1 == 0.0:     # a single-joint equality (obj2 = -1) couples nothing
                continue
            if j == drv and dep not in ratio:       # q_dep = c1 * q_drv
                ratio[dep] = ratio[j] * c1
                todo.append(dep)
            elif j == dep and drv not in ratio:
                ratio[drv] = ratio[j] / c1
                todo.append(drv)
    if len(ratio) == 1:
        return [(int(model.jnt_dofadr[jid]), 1.0)]
    weights = {j: float(model.dof_M0[model.jnt_dofadr[j]]) * c * c for j, c in ratio.items()}
    total = sum(weights.values())
    return [(int(model.jnt_dofadr[j]), weights[j] / total / (ratio[j] ** 2)) for j in ratio]


_NONE: dict = {}    # a device the robot program hasn't told us of (never written to)


class MotorBinding:
    """A motor controller output (PWM speed, CANMotor percentOutput or a Phoenix 6 TalonFX's applied
    voltage) driving a joint through a gearbox.

    Torque at the joint = Kt * I * gear_ratio * efficiency, with I computed from the applied voltage and
    the back-EMF of the motor shaft (joint speed * gear_ratio). Brake mode at neutral shorts the motor
    (V = 0, back-EMF braking); coast mode at neutral opens it (I = 0).

    Phoenix 6 TalonFX (`ctre = "TalonFX:<id>"`): the robot program's `<motorVoltage` is the voltage across
    the rotor, already in the rotor's own frame (Phoenix applies the `MotorOutput.Inverted` config before
    it). The binding writes the raw rotor back as `Rotor Sensor >rawPositionInput` (rotations) and
    `>velocity` (rotations/s) = joint revs * gear_ratio, also before `Inverted`: Phoenix's getPosition()
    applies `Inverted` itself. `invert = true` models a rotor that is mechanically reversed against the
    joint, flipping both the voltage and the sensor, like the PWM/CAN `invert`. `gear_ratio` is motor
    radians per joint unit (rad for hinges, m for slides), so rotor revs = joint position * gear_ratio / 2pi.

    `neutral` defaults to "coast" for a TalonFX, Phoenix 6's factory NeutralMode: set `neutral = "brake"`
    when your code configures NeutralModeValue.Brake. (PWM and CANMotor bindings default to "brake".)

    `count > 1` with one Talon models the other motors on the gearbox as implicit followers: the bound
    Talon's voltage drives all of them and the reported currents are totals. Don't also bind the follower
    Talons to the same joint (that would apply their torque twice).

    REV SPARK (`rev = "SparkMax:<id>"`): the helper's `<motorVoltage` is the voltage across the motor after the SPARK's
    inversion. The SPARK is sent `>current` per motor (the total / count), since that is what one SPARK measures and
    its smart current limit acts on; the binding's own `current` (logs) stays the total.

    Fast mode (`scrimmage = "<name>"`): the robot's fast-mode library creates a `ScrimmageMotor:<name>` device. Its
    `<motorVoltage` is the voltage across the motor after the device's own inversion (0 while disabled) and `<brake`
    its neutral mode. The binding writes `>position` (rotor rotations since the start), `>velocity` (rotor rps) and
    `>vbus`, with the REV path's sign and gear handling.

    External (`external = True`, set by the test range's shooter): another model (the launcher model's shaft) runs
    the joint. `apply` still reads the command and sets `command` and `voltage`, but applies no torque and no back-EMF
    damping, and the model reports its currents through `set_shaft_currents`; World.step leaves them out of its
    battery total (the range charges the shaft's supply current once). `updates` reads the joint, which the model
    mirrors, so the rotor sensor and the fast-mode encoder see its shaft. `open_circuit` is true while the motor
    coasts at neutral.
    """

    def __init__(self, cfg: dict, model: mujoco.MjModel):
        self.name = cfg.get("name", cfg["joint"])
        _one_of(cfg, ("pwm", "can", "ctre", "rev", "scrimmage"), i18n.t("motor '{name}'", name=self.name))
        if "pwm" in cfg:
            self.kind, self.device = "PWM", str(cfg["pwm"])
        elif "can" in cfg:
            self.kind, self.device = "CANMotor", str(cfg["can"])
        elif "ctre" in cfg:
            self.kind, self.device = "CTRE", _ctre_device(cfg, "motor")
        elif "rev" in cfg:
            self.kind, self.device = "REV", _rev_device(cfg, "motor")
        elif "scrimmage" in cfg:
            self.kind, self.device = "Scrimmage", _scrimmage_name(cfg)
        else:
            raise ValueError(i18n.t("motor '{name}' needs {pwm_setting}, {can_setting}, {ctre_setting}, "
                                    "{rev_setting} or {scrimmage_setting}", name=self.name,
                                    pwm_setting="pwm = <port>", can_setting="can = \"<Device[id]>\"",
                                    ctre_setting="ctre = \"TalonFX:<id>\"", rev_setting="rev = \"SparkMax:<id>\"",
                                    scrimmage_setting="scrimmage = \"<name>\""))
        self.device_key = ({"REV": "ScrimmageREV", "Scrimmage": "ScrimmageMotor", "PWM": "PWM"}.get(self.kind, "CANMotor"),
                           self.device)
        self.model = model
        self.qadr, self.dadr = _joint(model, cfg["joint"])
        self.damping_dofs = _coupled_dofs(model, cfg["joint"])
        if self.kind == "REV" and "motor" not in cfg:     # the SPARK's usual brushless motor
            cfg = {**cfg, "motor": "NeoVortex" if self.device.startswith("SparkFlex") else "NEO"}
        self.motor_name = cfg.get("motor", "CIM") if isinstance(cfg.get("motor", "CIM"), str) else "custom"
        self.motor = _motor_from_cfg(cfg)
        self._r = self.motor.R          # the winding resistance (ohms) and velocity constant, cached: the bus solve
        self._kv = self.motor.Kv        # evaluates every motor's supply current a few times a step
        self.ratio = float(cfg.get("gear_ratio", 1.0))
        self.eff = float(cfg.get("efficiency", 1.0))
        self.sign = -1.0 if cfg.get("invert", False) else 1.0
        m = self.motor              # the back-EMF damping on the joint (see drive)
        self._damping = self.eff * m.Kt * self.ratio ** 2 / (m.Kv * m.R)
        self.brake = cfg.get("neutral", "coast" if self.kind == "CTRE" else "brake") == "brake"
        self.limits = None                                # (lower, upper) joint range when limits = true (REV)
        if cfg.get("limits", False):
            if self.kind != "REV":
                raise ValueError(i18n.t("motor '{name}': {limits_setting} is only for rev motors", name=self.name,
                                        limits_setting="limits = true"))
            jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, cfg["joint"])
            if not model.jnt_limited[jid]:
                raise ValueError(i18n.t("motor '{name}': {limits_setting} needs joint '{joint}' to have a range "
                                        "({range_setting} in the MJCF)", name=self.name, joint=cfg['joint'],
                                        limits_setting="limits = true", range_setting='range="lo hi"'))
            self.limits = (float(model.jnt_range[jid][0]), float(model.jnt_range[jid][1]))
        # live values, useful for logging
        self.command = 0.0
        self.voltage = 0.0
        self.current = 0.0
        self.supply_current = 0.0
        self.external = False
        self.open_circuit = False
        # this step's output, from read(): a voltage, or a duty cycle (then _volts is unused)
        self._shaft_speed = 0.0
        self._emf = 0.0         # the motor's back-EMF (V) at the step's start
        self._volts = 0.0
        self._duty: float | None = 0.0
        self._neutral = True
        self.idle = True

    def set_shaft_currents(self, stator: float, supply: float) -> None:
        """An external binding's stator and supply current (A), from the model that runs its joint."""
        self.current = stator
        self.supply_current = supply

    def read_command(self, hal: HalState) -> float:
        if self.kind == "PWM":
            if not hal.get("PWM", self.device, "<init", False):
                return 0.0
            return float(hal.get("PWM", self.device, "<speed", 0.0) or 0.0)
        cmd = float(hal.get("CANMotor", self.device, "<percentOutput", 0.0) or 0.0)
        brake = hal.get("CANMotor", self.device, "<brakeMode", None)
        if brake is not None:
            self.brake = bool(brake)
        return cmd

    def apply(self, hal: HalState, data: mujoco.MjData, vbatt: float, enabled: bool = True) -> None:
        """Read the robot program's output and drive the joint on bus voltage `vbatt` (`read` then `drive`)."""
        self.read(hal, data, enabled)
        self.drive(data, vbatt)

    def read(self, hal: HalState, data: mujoco.MjData, enabled: bool = True) -> None:
        """Take this step's output from the robot program: a voltage (Phoenix, the REV helper, fast mode) or a duty
        cycle (PWM, CANMotor) that becomes a voltage once the bus voltage is known. Changes nothing in the world."""
        # a Python float: everything this step's motor math and the bus solve derive from it runs several times
        # faster than on numpy scalars (the same arithmetic, the same bits)
        self._shaft_speed = float(data.qvel[self.dadr]) * self.ratio
        self._emf = self._shaft_speed / self._kv
        if self.kind in ("CTRE", "REV", "Scrimmage"):
            # Phoenix, the REV helper and fast mode already output 0 V when disabled; still respect `enabled`.
            dev = hal.devices.get(self.device_key, _NONE)       # hal.get(type, device, ...), one lookup
            volts = float(dev.get("<motorVoltage", 0.0) or 0.0) if enabled else 0.0
            if self.kind == "Scrimmage":
                brake = dev.get("<brake", None)
                if brake is not None:
                    self.brake = bool(brake)
            self._volts, self._duty = volts * self.sign, None
            neutral = abs(volts) < NEUTRAL_EPS
        else:
            # Disabled robots output neutral, exactly like a real roboRIO.
            cmd = max(-1.0, min(1.0, self.read_command(hal))) * self.sign if enabled else 0.0
            self._volts, self._duty = 0.0, cmd
            neutral = abs(cmd) < NEUTRAL_EPS
        self._neutral = neutral
        self.open_circuit = neutral and not self.brake
        # draws no supply current whatever the bus (supply_at is 0): the bus solve leaves it out
        self.idle = self.external or self.open_circuit or not (self._volts if self._duty is None else self._duty)

    def volts_at(self, bus: float) -> float:
        """The voltage across the motor on bus voltage `bus`: the duty cycle's share of it, or the requested voltage
        clamped to it (a controller can't put out more than its supply)."""
        if self._duty is not None:
            return self._duty * bus
        return max(-bus, min(bus, self._volts))

    def supply_at(self, bus: float) -> float:
        """The supply current (A) this motor would draw on bus voltage `bus`: its current at the shaft's speed at the
        step's start, times its duty cycle (an external motor's is its model's: 0 here). Negative while it
        regenerates; World clamps the total, so one motor's braking can offset another's draw but never charges the
        battery."""
        return self.supply_and_slope(bus)[0]

    def supply_and_slope(self, bus: float) -> tuple[float, float]:
        """supply_at(bus) and its derivative with respect to the bus voltage (A/V), for World's Newton solve. On a
        duty cycle d the current is (d bus - emf) d / R; a requested voltage V clamped to the bus draws
        (bus - emf sign V) / R, unclamped (V - emf) V / (R bus)."""
        if self.external or self.open_circuit:
            return 0.0, 0.0
        r = self._r
        if self._duty is not None:
            d = self._duty
            return (d * bus - self._emf) / r * d, d * d / r     # motor.current at the start speed, times duty
        volts = self._volts
        if not volts:
            return 0.0, 0.0
        if abs(volts) >= bus:       # clamped to the bus (full duty); also the limit as the bus falls to 0
            s = 1.0 if volts > 0.0 else -1.0
            return (max(bus, 0.0) - s * self._emf) / r, 1.0 / r
        c = (volts - self._emf) * volts / r
        return c / bus, -c / (bus * bus)

    def drive(self, data: mujoco.MjData, vbatt: float, damping: np.ndarray | None = None,
              qfrc: np.ndarray | None = None) -> None:
        """Apply this step's output on bus voltage `vbatt`: the voltage, currents, torque and back-EMF damping
        (World.step passes the model's dof_damping and data's qfrc_applied, fetched once for every motor)."""
        duty = self._duty
        volts = duty * vbatt if duty is not None else max(-vbatt, min(vbatt, self._volts))     # volts_at(vbatt)
        self.command = duty if duty is not None else self._volts / max(vbatt, 1e-6)
        if self.external:
            self.voltage = 0.0 if self.open_circuit else volts
            return
        if self.open_circuit:                       # neutral and not braking
            self.voltage = self.current = 0.0
            self.supply_current = self.supply_at(vbatt)
            return                                  # open circuit: no torque, no back-EMF damping
        self.voltage = volts
        self.current = (volts - self._emf) / self._r      # motor.current at the start speed
        self.supply_current = self.supply_at(vbatt)
        # Torque = Kt * (V - w_shaft/Kv)/R * g * eff. The back-EMF term is a damping force on the joint,
        # b * qvel with b = eff * Kt * g^2 / (Kv * R); it is huge at elevator gearing, so it goes to MuJoCo's
        # implicit dof_damping (World.step resets that each step) and only the voltage term is applied explicitly.
        if damping is None:
            damping = self.model.dof_damping
        if qfrc is None:
            qfrc = data.qfrc_applied
        b = self._damping
        for dof, share in self.damping_dofs:
            damping[dof] += b * share
        m = self.motor
        qfrc[self.dadr] += m.Kt * (volts / m.R) * self.ratio * self.eff      # m.torque(V / R)

    def updates(self, vbatt: float, data: mujoco.MjData) -> list[Update]:
        if self.kind == "CTRE":
            # gear_ratio is motor radians per joint unit for hinge and slide joints alike (see apply()).
            rev = data.qpos[self.qadr] / (2 * math.pi) * self.ratio * self.sign
            rps = data.qvel[self.dadr] / (2 * math.pi) * self.ratio * self.sign
            return [("CANMotor", self.device, {">supplyVoltage": vbatt}),
                    ("CANEncoder", self.device + "/Rotor Sensor", {">rawPositionInput": rev, ">velocity": rps})]
        if self.kind == "REV":
            # motor shaft rotations since the start (the SPARK's power-on) and rpm, in the physical motor frame (the
            # helper applies the SPARK's inversion)
            rot = (data.qpos[self.qadr] - self.model.qpos0[self.qadr]) * self.ratio * self.sign / (2 * math.pi)
            rpm = data.qvel[self.dadr] * self.ratio * self.sign * 60.0 / (2 * math.pi)
            # the current is one motor's (the binding's is the gearbox total): what the SPARK and its current limit see
            out = {">position": rot, ">velocity": rpm, ">current": abs(self.current) / self.motor.count, ">vbus": vbatt,
                   ">motor": float(REV_MOTOR_CODES.get(self.motor_name, 0))}
            if self.limits is not None:
                pos = float(data.qpos[self.qadr])
                out[">fwdLimit"] = bool(pos >= self.limits[1] - LIMIT_EPS)
                out[">revLimit"] = bool(pos <= self.limits[0] + LIMIT_EPS)
            return [("ScrimmageREV", self.device, out)]
        if self.kind == "Scrimmage":
            # rotor rotations since the start and rps, in the physical motor frame (the library applies its inversion)
            rot = (data.qpos[self.qadr] - self.model.qpos0[self.qadr]) * self.ratio * self.sign / (2 * math.pi)
            rps = data.qvel[self.dadr] * self.ratio * self.sign / (2 * math.pi)
            return [("ScrimmageMotor", self.device, {">position": rot, ">velocity": rps, ">vbus": vbatt})]
        if self.kind != "CANMotor":
            return []
        return [("CANMotor", self.device, {">motorCurrent": abs(self.current), ">supplyCurrent": 0.0,
                                           ">busVoltage": vbatt})]


class EncoderBinding:
    """Joint position/velocity reported as a roboRIO quadrature Encoder (matched by channel_a),
    as a CANEncoder SimDevice (rotations, rotations/s), or as a Phoenix 6 CANcoder.

    CANcoder (`ctre = "CANcoder:<id>"`): `>rawPositionInput` = joint revs * ratio * sign + offset, the
    raw magnet position. Phoenix's getPosition() adds the device's `MagnetOffset` config on top (and
    quantizes the raw input to 1/4096 rev), so `offset` models where the magnet sits, not the config.

    REV absolute encoder (`rev = "SparkMaxAbs:<id>"`): `>absRotations` = joint revs * ratio * sign + offset, the
    unwrapped magnet position; REVLib applies the SPARK's own inversion and zeroOffset on top. As with a CANcoder,
    `offset` is where the magnet sits (e.g. a module calibrated in a different orientation from the one it's
    mounted in).

    Fast mode (`scrimmage = "<name>"`): `>position` (rotations) and `>velocity` (rps) of the magnet, unwrapped, in the
    same frame as the CANcoder's `>rawPositionInput` (the fast-mode library applies the CANcoder's own config)."""

    def __init__(self, cfg: dict, model: mujoco.MjModel):
        _one_of(cfg, ("channel_a", "can", "ctre", "rev", "scrimmage"), i18n.t("encoder on '{joint}'",
                                                                              joint=cfg.get("joint")))
        self.name = cfg.get("name")
        self.qadr, self.dadr = _joint(model, cfg["joint"])
        # encoder revolutions per joint revolution (e.g. gear_ratio if the encoder is on the motor)
        self.ratio = float(cfg.get("ratio", 1.0))
        self.sign = -1.0 if cfg.get("invert", False) else 1.0
        jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, cfg["joint"])
        self.is_slide = int(model.jnt_type[jid]) == int(mujoco.mjtJoint.mjJNT_SLIDE)
        if "channel_a" in cfg:
            self.kind, self.channel_a = "Encoder", int(cfg["channel_a"])
            self.cpr = float(cfg.get("counts_per_rev", 2048))
        elif "can" in cfg:
            self.kind, self.device = "CANEncoder", str(cfg["can"])
        elif "ctre" in cfg:
            self.kind, self.device = "CTRE", _ctre_device(cfg, "encoder")
            self.offset = float(cfg.get("offset", 0.0))
        elif "rev" in cfg:
            self.kind, self.device = "REV", _rev_device(cfg, "encoder")
            self.offset = float(cfg.get("offset", 0.0))
        elif "scrimmage" in cfg:
            self.kind, self.device = "Scrimmage", _scrimmage_name(cfg)
            self.offset = float(cfg.get("offset", 0.0))
        else:
            raise ValueError(i18n.t("encoder needs {channel_a_setting}, {can_setting}, {ctre_setting}, {rev_setting} "
                                    "or {scrimmage_setting}", channel_a_setting="channel_a = <dio>",
                                    can_setting="can = \"<Device[id]>\"", ctre_setting="ctre = \"CANcoder:<id>\"",
                                    rev_setting="rev = \"SparkMaxAbs:<id>\"",
                                    scrimmage_setting="scrimmage = \"<name>\""))
        # HALSim names quadrature Encoders by index, so their key matches on <channel_a (see resolve_device_key)
        self.device_key = (("Encoder", f"<channel_a={self.channel_a}") if self.kind == "Encoder"
                           else ({"REV": "ScrimmageREV", "Scrimmage": "ScrimmageEncoder"}.get(self.kind, "CANEncoder"), self.device))

    def _revs(self, data: mujoco.MjData) -> tuple[float, float]:
        per = 1.0 if self.is_slide else 2.0 * math.pi  # slide joints: 1 "rev" per meter
        pos = data.qpos[self.qadr] / per * self.ratio * self.sign
        vel = data.qvel[self.dadr] / per * self.ratio * self.sign
        return pos, vel

    def updates(self, hal: HalState, data: mujoco.MjData) -> list[Update]:
        pos, vel = self._revs(data)
        if self.kind == "CANEncoder":
            return [("CANEncoder", self.device, {">position": pos, ">velocity": vel})]
        if self.kind == "REV":
            return [("ScrimmageREV", self.device, {">absRotations": pos + self.offset})]
        if self.kind == "Scrimmage":
            return [("ScrimmageEncoder", self.device, {">position": pos + self.offset, ">velocity": vel})]
        if self.kind == "CTRE":
            return [("CANEncoder", self.device, {">rawPositionInput": pos + self.offset, ">velocity": vel,
                                                  ">supplyVoltage": 5.0})]
        dev = hal.find("Encoder", "<channel_a", self.channel_a)
        if dev is None or not hal.get("Encoder", dev, "<init", False):
            return []
        # The sim HAL does not apply reverse_direction itself; real hardware does it in the FPGA.
        if hal.get("Encoder", dev, "<reverse_direction", False):
            pos, vel = -pos, -vel
        counts_per_s = vel * self.cpr
        period = (1.0 / counts_per_s) if abs(counts_per_s) > 1e-6 else 1e9  # seconds per count, signed
        return [("Encoder", dev, {">count": int(round(pos * self.cpr)), ">period": period})]


class GyroBinding:
    """Body heading reported as a WPILib Gyro SimDevice (e.g. ADXRS450[0]), or a Phoenix 6 Pigeon 2.

    Real FRC gyros report clockwise-positive angles, so cw_positive defaults to true; the physics
    world is counter-clockwise positive (z up).

    Pigeon 2 (`ctre = "Pigeon2:<id>"`): `>rawYawInput` is the unwrapped yaw in degrees, CCW positive,
    which Pigeon2.getYaw() returns as is (starting from 0 wherever the robot points). Pitch, roll and
    the angular rates follow `pigeon_attitude`: the body frame is the Pigeon's (X forward, Y left, Z up).
    Phoenix 6 sim (26.1.2) returns these body-frame rates from getAngularVelocity{X,Y,Z}World unrotated,
    so XWorld/YWorld are body-frame values whenever the robot is yawed; ZWorld is right while it is level.

    SimDevice gyros: `axis` picks the value names `angle_<axis>`/`rate_<axis>` (ADXRS450 and friends); `angle` and
    `rate` name them outright for devices that use other names, e.g. WPILib's ADIS16470_IMU
    (`device = "ADIS16470[0]", angle = "gyro_angle_z", rate = "gyro_rate_z", cw_positive = false`).

    Fast mode (`scrimmage = "<name>"`): `>yaw` (degrees, unwrapped) and `>rate` (deg/s), both CCW positive."""

    def __init__(self, cfg: dict, model: mujoco.MjModel):
        self.name = cfg.get("name")
        _one_of(cfg, ("ctre", "scrimmage", "device"), i18n.t("gyro"))
        self.scrimmage = "scrimmage" in cfg
        if "ctre" in cfg:
            self.typ, self.device = "CANGyro", _ctre_device(cfg, "gyro")
            self.ctre = True
        elif self.scrimmage:
            self.typ, self.device = "ScrimmageGyro", _scrimmage_name(cfg)
            self.ctre = False
        else:
            self.device = str(cfg["device"])
            self.typ = cfg.get("type", "Gyro")  # "Gyro" or "CANGyro"
            self.ctre = False
        self.device_key = (self.typ, self.device)
        bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, cfg["body"])
        if bid < 0:
            raise ValueError(i18n.t("Body '{body}' not found", body=cfg['body']))
        self.body = bid
        axis = cfg.get("axis", "x")  # which angle_*/rate_* values the device uses for yaw
        self.angle_value = str(cfg.get("angle", f"angle_{axis}"))
        self.rate_value = str(cfg.get("rate", f"rate_{axis}"))
        self.sign = -1.0 if cfg.get("cw_positive", True) else 1.0
        self._last_yaw: float | None = None
        self._unwrapped = 0.0

    def reset(self) -> None:
        self._last_yaw = None
        self._unwrapped = 0.0

    def yaw(self, data: mujoco.MjData) -> float:
        w, x, y, z = data.xquat[self.body]
        yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
        if self._last_yaw is None:
            self._unwrapped = 0.0  # gyros start at zero wherever the robot is pointed
        else:
            d = (yaw - self._last_yaw + math.pi) % (2 * math.pi) - math.pi
            self._unwrapped += d
        self._last_yaw = yaw
        return self._unwrapped

    def updates(self, data: mujoco.MjData) -> list[Update]:
        ang = self.yaw(data)
        if self.scrimmage:
            return [("ScrimmageGyro", self.device, {">yaw": math.degrees(ang),
                                                 ">rate": math.degrees(float(data.cvel[self.body][2]))})]
        if self.ctre:
            # cvel is [rot(3), lin(3)] in the world frame
            _, pitch, roll, rates = pigeon_attitude(data.xquat[self.body], data.cvel[self.body][:3])
            return [("CANGyro", self.device, {
                ">rawYawInput": math.degrees(ang), ">pitch": pitch, ">roll": roll,
                ">angularVelX": float(rates[0]), ">angularVelY": float(rates[1]), ">angularVelZ": float(rates[2]),
                ">supplyVoltage": 12.0,
            })]
        # world-frame angular velocity: cvel is [rot(3), lin(3)] about the subtree COM, in world frame
        rate_z = float(data.cvel[self.body][2])
        return [(self.typ, self.device, {
            ">connected": True,
            f">{self.angle_value}": math.degrees(ang) * self.sign,
            f">{self.rate_value}": math.degrees(rate_z) * self.sign,
        })]


class AnalogBinding:
    """Joint position mapped linearly to an analog input voltage (potentiometer)."""

    def __init__(self, cfg: dict, model: mujoco.MjModel):
        self.port = str(cfg["port"])
        self.device_key = ("AI", self.port)
        self.qadr, _ = _joint(model, cfg["joint"])
        self.scale = float(cfg.get("volts_per_unit", 1.0))  # volts per rad (hinge) or per m (slide)
        self.offset = float(cfg.get("offset", 0.0))

    def updates(self, hal: HalState, data: mujoco.MjData) -> list[Update]:
        v = min(5.0, max(0.0, self.offset + self.scale * data.qpos[self.qadr]))
        return [("AI", self.port, {">voltage": v})]


class DigitalBinding:
    """A limit switch: true while the joint is inside [min, max]. roboRIO switches are usually
    wired active-low, so active_low = true (default) reports False when the switch is pressed."""

    def __init__(self, cfg: dict, model: mujoco.MjModel):
        self.channel = str(cfg["channel"])
        self.device_key = ("DIO", self.channel)
        self.qadr, _ = _joint(model, cfg["joint"])
        self.lo = float(cfg.get("min", -np.inf))
        self.hi = float(cfg.get("max", np.inf))
        self.active_low = bool(cfg.get("active_low", True))

    def updates(self, hal: HalState, data: mujoco.MjData) -> list[Update]:
        pressed = self.lo <= data.qpos[self.qadr] <= self.hi
        return [("DIO", self.channel, {"<>value": (not pressed) if self.active_low else pressed})]


__all__ = ["MotorBinding", "EncoderBinding", "GyroBinding", "AnalogBinding", "DigitalBinding", "MOTORS"]
