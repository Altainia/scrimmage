"""The MuJoCo world plus all device bindings, stepped together."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

import mujoco
import numpy as np

from .bindings import AnalogBinding, DigitalBinding, EncoderBinding, GyroBinding, MotorBinding, Update
from .motors import Battery
from .parts.assemble import parse_start_pose
from .protocol import HalState

BUS_TOLERANCE = 1e-3        # V: the bus voltage solve stops this close to the battery's own answer (>vin_voltage is to 1 mV)
BUS_MAX_ITERATIONS = 60
BUS_NEWTON_STEPS = 3      # supply evaluations Newton gets before the bracketed solve takes over


def _reject_duplicate_devices(*groups) -> None:
    """Two bindings on one device would fight over its inputs/outputs: say which.

    A REV SPARK's motor and absolute encoder share its helper device on purpose, so REV bindings are
    only compared within their own group."""
    seen: dict = {}
    for g, group in enumerate(groups):
        for i, b in enumerate(group, 1):
            key = (g, b.device_key) if getattr(b, "kind", None) == "REV" else b.device_key
            other = seen.setdefault(key, (b, _label(b, i)))
            if other[0] is not b:
                typ, dev = b.device_key
                dev = dev.replace("<channel_a=", "encoder channel A ")
                raise ValueError(f"{typ} {dev} is bound by {other[1]} and {_label(b, i)}: "
                                 "give each device one binding")


def _label(b, index: int) -> str:
    if getattr(b, "label", None):
        return b.label
    kind = type(b).__name__.removesuffix("Binding").lower()
    name = getattr(b, "name", None)
    return f"{kind} {name}" if name else f"{kind} #{index}"


class World:
    def __init__(self, cfg: dict, cfg_dir: Path):
        mcfg = cfg.get("model", {})
        rcfg = cfg.get("robot", {})
        self.parts = None
        if "part" not in cfg and "mjcf" not in mcfg:
            hint = " (did you mean [[part]]?)" if "parts" in cfg else ""
            raise ValueError("describe the robot with [[part]] entries (see docs/parts.md) or "
                             f"[model] mjcf = <file>{hint}")
        if "range" in cfg and "part" not in cfg:
            from .parts.model import PartError
            raise PartError("a range needs a robot built from [[part]]: [model] mjcf robots can't have a [range]")
        start_pose = parse_start_pose(rcfg.get("start_pose"))
        self.warnings: list[str] = []
        if "part" in cfg:
            if "mjcf" in mcfg:
                raise ValueError("use either [model] mjcf = <hand-written model> or [[part]] entries, not both")
            for k in ("motor", "encoder", "gyro"):
                if cfg.get(k):
                    raise ValueError(f"[[{k}]] entries don't go with [[part]]: give the part's slot an ID in its "
                                     "ids table instead")
            if "body" in rcfg:
                raise ValueError("[robot] body is set by the root part when the robot is built from [[part]]")
            from .parts.assemble import assemble
            self.parts = assemble(cfg, cfg_dir)
            self.model = self.parts.model
            self.warnings = list(self.parts.warnings)
            bind = {**cfg, **self.parts.bindings}
            body_name = self.parts.robot_body
        else:
            path = (cfg_dir / mcfg["mjcf"]).resolve()
            self.model = mujoco.MjModel.from_xml_path(str(path))
            bind = cfg
            body_name = rcfg.get("body", "chassis")
        if "timestep" in mcfg:
            self.model.opt.timestep = float(mcfg["timestep"])
        self.data = mujoco.MjData(self.model)
        self.robot_body_name = body_name
        self.robot_body = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, body_name)
        if self.robot_body < 0:
            raise ValueError(f"robot body '{body_name}' not in model")
        self.start_pose = start_pose  # (x, y, heading_deg) or None to use the MJCF pose; parts bake it into qpos0
        jnt = self.model.body_jntadr[self.robot_body]
        self.free_qadr = int(self.model.jnt_qposadr[jnt]) if (
            jnt >= 0 and int(self.model.jnt_type[jnt]) == int(mujoco.mjtJoint.mjJNT_FREE)) else None
        self.free_dadr = int(self.model.jnt_dofadr[jnt]) if self.free_qadr is not None else None
        self._geom_pos0 = self.model.geom_pos.copy()    # move_object edits static geoms; reset() restores them
        self._geom_quat0 = self.model.geom_quat.copy()
        self._base_damping = self.model.dof_damping.copy()   # motors add their back-EMF damping on top, each step

        b = cfg.get("battery", {})
        self.battery = Battery(b.get("nominal_voltage", 12.0), b.get("resistance", 0.02))
        self.vbatt = self.battery.nominal_voltage
        self.bus_evaluations = 0    # supply current evaluations by the bus voltage solve so far (a cost check)

        m = self.model
        self.motors = [MotorBinding(c, m) for c in bind.get("motor", [])]
        self.encoders = [EncoderBinding(c, m) for c in bind.get("encoder", [])]
        self.gyros = [GyroBinding(c, m) for c in bind.get("gyro", [])]
        self.analogs = [AnalogBinding(c, m) for c in cfg.get("analog", [])]
        self.digitals = [DigitalBinding(c, m) for c in cfg.get("digital", [])]
        self.test_range = None      # the test range's balls (scrimmage/testrange), for a config with a [range]
        if self.parts is not None and self.parts.range is not None:
            from .testrange.runtime import RangeRuntime
            self.test_range = RangeRuntime(self, self.parts, cfg, cfg_dir)
        elif "shooter" in cfg:
            from .testrange.shooter import parse_shooter
            parse_shooter(cfg, self.parts, cfg_dir)     # raises: a [shooter] runs on a [range]
        _reject_duplicate_devices(self.motors, self.encoders, self.gyros, self.analogs, self.digitals,
                                  self.test_range.devices() if self.test_range is not None else [])
        # The terrain signals (scrimmage/terrain): read every physics step on a range with field elements, or once a
        # scenario asks for one (terrain_monitor()); otherwise None, and stepping costs nothing extra.
        self.terrain = None
        # A Python controller in place of the robot program (terrain sweep's driver): called every step after the
        # motors drive and before mj_step, it may add to qfrc_applied and dof_damping (both reset each step).
        self.external: Callable[[mujoco.MjData], None] | None = None
        if self.parts is not None and self.parts.range is not None and self.parts.range.elements:
            self.terrain_monitor()
        # The climb signals (scrimmage/terrain/climb.py): read every physics step on a range with a bar or a switch,
        # or once a scenario asks for one (climb_monitor()); otherwise None, and stepping costs nothing extra.
        self.climb = None
        layout = self.parts.layout if self.parts is not None else None
        if layout is not None and (layout.bars or layout.switches):
            self.climb_monitor()
        self.reset()

    def terrain_monitor(self):
        """The world's TerrainMonitor, made on first use."""
        if self.terrain is None:
            from .terrain.monitor import TerrainMonitor
            self.terrain = TerrainMonitor(self)
        return self.terrain

    def climb_monitor(self):
        """The world's ClimbMonitor, made on first use."""
        if self.climb is None:
            from .terrain.climb import ClimbMonitor
            self.climb = ClimbMonitor(self)
        return self.climb

    def part_tree(self) -> list[dict] | None:
        """Each part instance with its slots (kind and device ID), mounts and prefixed joints; None for a hand-written model."""
        if self.parts is None:
            return None
        return [{"name": i.name, "source": i.source, "attach": i.attach,
                 "slots": {slot: {"kind": kind, "id": i.ids.get(slot)} for slot, kind in i.part.slot_kinds().items()},
                 "mounts": [m.name for m in i.part.mounts],
                 "joints": [f"{i.name}.{j}" for j in i.part.joint_names()]} for i in self.parts.instances]

    @property
    def time(self) -> float:
        return float(self.data.time)

    def reset(self) -> None:
        self.model.geom_pos[:] = self._geom_pos0
        self.model.geom_quat[:] = self._geom_quat0
        self.model.dof_damping[:] = self._base_damping
        mujoco.mj_resetData(self.model, self.data)
        self._apply_start_pose()
        if self.test_range is not None:
            self.test_range.reset()
        mujoco.mj_forward(self.model, self.data)
        for g in self.gyros:
            g.reset()
        self.vbatt = self.battery.nominal_voltage
        if self.terrain is not None:
            self.terrain.reset()
        if self.climb is not None:
            self.climb.reset()

    def reset_robot(self) -> None:
        """Put the robot's joints (its pose and every mechanism) back to qpos0 at rest, the robot at its start pose;
        field objects stay. A range's shooter shaft (its launcher model's) comes to rest too, and the balls in the
        launcher go back to the robot's hopper (see RangeRuntime.reset_robot)."""
        m, d = self.model, self.data
        root = m.body_rootid[self.robot_body]
        for j in range(m.njnt):
            if m.body_rootid[m.jnt_bodyid[j]] != root:
                continue
            qa, da = int(m.jnt_qposadr[j]), int(m.jnt_dofadr[j])
            n, nv = (7, 6) if int(m.jnt_type[j]) == int(mujoco.mjtJoint.mjJNT_FREE) else (
                (4, 3) if int(m.jnt_type[j]) == int(mujoco.mjtJoint.mjJNT_BALL) else (1, 1))
            d.qpos[qa:qa + n] = m.qpos0[qa:qa + n]
            d.qvel[da:da + nv] = 0
        self._apply_start_pose()
        if self.test_range is not None:
            self.test_range.reset_robot()
        mujoco.mj_forward(m, d)
        if self.terrain is not None:
            self.terrain.reset()
        if self.climb is not None:
            self.climb.reset()

    def _apply_start_pose(self) -> None:
        """A hand-written model's robot starts at [robot] start_pose rather than the MJCF's pose (a parts robot's
        start pose is built into its model)."""
        if self.parts is None and self.start_pose is not None and self.free_qadr is not None:
            x, y, hdg = self.start_pose
            q = self.free_qadr
            self.data.qpos[q:q + 2] = [x, y]
            h = math.radians(hdg) / 2
            self.data.qpos[q + 3:q + 7] = [math.cos(h), 0, 0, math.sin(h)]

    def set_robot_pose(self, x: float, y: float, heading_deg: float) -> None:
        """Teleport the robot (at rest). Only physics moves: robot code isn't told directly, but its gyro
        sees the heading jump (as the shortest-angle change) and wheel odometry is not corrected.
        For a parts robot (x, y, heading) is where the start-pose frame goes, like [robot] start_pose: the root
        part's pos and rot are applied below it, so the root body ends up offset from (x, y)."""
        if self.free_qadr is None:
            raise ValueError("the robot body has no free joint, so it can't be moved")
        q = self.free_qadr
        h = math.radians(heading_deg) / 2
        frame_q = np.array([math.cos(h), 0, 0, math.sin(h)])
        if self.parts is None:
            self.data.qpos[q:q + 2] = [x, y]
            self.data.qpos[q + 3:q + 7] = frame_q
        else:
            off_pos, off_quat = self.parts.root_offset
            pos, quat = np.zeros(3), np.zeros(4)
            mujoco.mju_rotVecQuat(pos, np.asarray(off_pos, float), frame_q)
            mujoco.mju_mulQuat(quat, frame_q, np.asarray(off_quat, float))
            self.data.qpos[q:q + 2] = [x + pos[0], y + pos[1]]
            self.data.qpos[q + 3:q + 7] = quat
        self.data.qvel[self.free_dadr:self.free_dadr + 6] = 0
        mujoco.mj_forward(self.model, self.data)

    def set_robot_force(self, fx: float, fy: float) -> None:
        """A horizontal force (N, world frame) on the robot body, applied until changed."""
        self.data.xfrc_applied[self.robot_body, :3] = [fx, fy, 0.0]

    def movable_objects(self) -> list[str]:
        """Bodies with a free joint (other than the robot and the range's balls), and named static geoms that aren't
        planes."""
        m = self.model
        names = []
        for b in range(1, m.nbody):
            j = m.body_jntadr[b]
            if b != self.robot_body and j >= 0 and int(m.jnt_type[j]) == int(mujoco.mjtJoint.mjJNT_FREE):
                n = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, b)
                if n and not n.startswith("ball."):
                    names.append(n)
        for g in range(m.ngeom):
            if m.geom_bodyid[g] == 0 and int(m.geom_type[g]) != int(mujoco.mjtGeom.mjGEOM_PLANE):
                n = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g)
                if n:
                    names.append(n)
        return sorted(names)

    def object_z(self, name: str) -> float | None:
        """Current height of a movable object (free body's qpos z, or static geom's z); None if not movable."""
        m = self.model
        b = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, name)
        j = int(m.body_jntadr[b]) if b >= 0 else -1
        if b != self.robot_body and j >= 0 and int(m.jnt_type[j]) == int(mujoco.mjtJoint.mjJNT_FREE):
            return float(self.data.qpos[int(m.jnt_qposadr[j]) + 2])
        g = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, name)
        if g >= 0 and m.geom_bodyid[g] == 0:
            return float(m.geom_pos[g][2])
        return None

    def move_object(self, name: str, x: float, y: float, z: float, yaw_deg: float) -> None:
        m = self.model
        h = math.radians(yaw_deg) / 2
        quat = [math.cos(h), 0, 0, math.sin(h)]
        b = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, name)
        if b == self.robot_body:
            raise ValueError("that's the robot: use set_pose to move it")
        if name in self.movable_objects():
            j = int(m.body_jntadr[b]) if b >= 0 else -1
            if j >= 0 and int(m.jnt_type[j]) == int(mujoco.mjtJoint.mjJNT_FREE):
                qa, da = int(m.jnt_qposadr[j]), int(m.jnt_dofadr[j])
                self.data.qpos[qa:qa + 7] = [x, y, z, *quat]
                self.data.qvel[da:da + 6] = 0
            else:
                g = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, name)
                if g < 0 or m.geom_bodyid[g] != 0:
                    raise ValueError(f"{name!r} can't be moved; movable objects: {', '.join(self.movable_objects())}")
                m.geom_pos[g] = [x, y, z]
                m.geom_quat[g] = quat
            mujoco.mj_forward(m, self.data)
            return
        raise ValueError(f"no movable object {name!r}; movable objects: {', '.join(self.movable_objects()) or 'none'}")

    def step(self, hal: HalState) -> None:
        """Advance one physics timestep using the robot program's latest outputs."""
        self.data.qfrc_applied[:] = 0.0
        self.model.dof_damping[:] = self._base_damping
        # If the robot program has never reported DS state, don't second-guess its outputs.
        enabled = bool(hal.get("DriverStation", "", ">enabled", True))
        for mb in self.motors:
            mb.read(hal, self.data, enabled)
        rng = self.test_range
        self.vbatt = self._bus_voltage(rng)
        damping, qfrc = self.model.dof_damping, self.data.qfrc_applied
        for mb in self.motors:
            mb.drive(self.data, self.vbatt, damping, qfrc)
        if self.external is not None:
            self.external(self.data)
        if rng is None:
            mujoco.mj_step(self.model, self.data)
        else:
            rng.before_physics(hal, self.vbatt, self.model.opt.timestep)
            mujoco.mj_step(self.model, self.data)
            rng.after_physics()
        if self.terrain is not None:
            self.terrain.after_physics()
        if self.climb is not None:
            self.climb.after_physics()

    def _bus_voltage(self, rng) -> float:
        """The battery's terminal voltage this step: V = nominal - R * (the supply current every motor draws at V).

        Solved within the step, not from the last step's currents: with that one-step lag a heavy load (many motors
        at full output from rest) overshoots to 0 V, draws nothing the next step, recovers to nominal, and so on,
        flipping every step. Each motor's current is taken at its shaft's speed at the step's start (the shooter
        shaft's at its model's); a duty cycle becomes a share of V, a requested voltage is clamped to V. The total is
        clamped at 0: motors braking (regenerating) offset the others' draw, but the battery isn't charged.

        Newton's method from the last step's answer (the load changes little step to step, and each motor's supply
        current is a simple function of V with an easy derivative): one evaluation when nothing draws, two at a steady
        load, and a final Newton step from the last evaluation, so the answer is far closer than BUS_TOLERANCE. If
        Newton leaves [0, nominal] or doesn't settle in BUS_NEWTON_STEPS evaluations, the root is bracketed by nominal
        (where any draw puts the bus below it) and 0 V and found by regula falsi (Illinois). At 0 V every controller's
        output is 0 V, so the only draw left is a motor plugged (spinning against its command, its back-EMF opposite
        in sign to it, driving current through it from the battery's side): if even that would pull the bus below
        0 V, the bracket fails and the bus is taken as 0 V (a total brownout), as the old battery model's clamp
        gave."""
        b = self.battery
        v0, r = b.nominal_voltage, b.resistance
        loads = [mb for mb in self.motors if not mb.idle]
        if rng is not None and rng.shooter is not None:
            loads.append(rng)
        if r <= 0.0 or not loads:
            return v0

        def excess(v: float) -> tuple[float, float]:     # > 0: the battery holds the bus above v; and d/dv
            self.bus_evaluations += 1
            total = slope = 0.0
            for load in loads:
                i, di = load.supply_and_slope(v)
                total += i
                slope += di
            if total <= 0.0:
                return v0 - v, -1.0
            return v0 - r * total - v, -1.0 - r * slope

        v = self.vbatt if 0.0 < self.vbatt <= v0 else v0
        last = math.inf                             # the previous evaluation's |excess|
        for _ in range(BUS_NEWTON_STEPS):
            f, df = excess(v)
            if df >= 0.0:
                break                               # not heading for a root: bracket it instead
            nv = v - f / df
            # Close to the root Newton converges quadratically, e' = K e^2, so with K estimated from the last two
            # evaluations this step lands about |f|^3 / last^2 from it; accept it when that is well inside the
            # tolerance (a robot whose load jumps each step, as wheels stick and slip, then costs 2 evaluations).
            err = abs(f)
            if err < BUS_TOLERANCE or (last < math.inf and err < 0.5 * last
                                       and err ** 3 < 0.1 * BUS_TOLERANCE * last * last):
                if 0.0 <= nv <= v0:
                    return v0 if nv > v0 - BUS_TOLERANCE else nv
                if err < BUS_TOLERANCE:
                    return max(0.0, min(v0, v))
            if not 0.0 <= nv <= v0:
                break
            v, last = nv, err
        hi_f = excess(v0)[0]
        if hi_f > -BUS_TOLERANCE:
            return v0
        lo_f = excess(0.0)[0]
        if lo_f <= 0.0:
            return 0.0                              # plugged motors pull even 0 V down: see above
        lo, hi = 0.0, v0
        side = 0
        for _ in range(BUS_MAX_ITERATIONS):
            v = (lo * hi_f - hi * lo_f) / (hi_f - lo_f)
            f = excess(v)[0]
            if abs(f) < BUS_TOLERANCE:
                return max(0.0, v)
            if f > 0.0:
                lo, lo_f = v, f
                if side == 1:
                    hi_f /= 2.0
                side = 1
            else:
                hi, hi_f = v, f
                if side == -1:
                    lo_f /= 2.0
                side = -1
        return max(0.0, 0.5 * (lo + hi))

    def sensor_updates(self, hal: HalState) -> list[Update]:
        ups: list[Update] = [("RoboRIO", "", {">vin_voltage": round(self.vbatt, 3)})]
        for mb in self.motors:
            ups += mb.updates(self.vbatt, self.data)
        for e in self.encoders:
            ups += e.updates(hal, self.data)
        for g in self.gyros:
            ups += g.updates(self.data)
        for a in self.analogs:
            ups += a.updates(hal, self.data)
        for dg in self.digitals:
            ups += dg.updates(hal, self.data)
        if self.test_range is not None:
            ups += self.test_range.sensor_updates(hal)
        return ups

    def robot_pose(self) -> tuple[float, float, float]:
        """Ground-truth (x, y, heading_rad) of the robot body, CCW positive."""
        x, y = self.data.xpos[self.robot_body][:2]
        w, qx, qy, qz = self.data.xquat[self.robot_body]
        yaw = math.atan2(2 * (w * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))
        return float(x), float(y), yaw

    def robot_speed(self) -> float:
        return float(np.linalg.norm(self.data.cvel[self.robot_body][3:5]))

    def joint_states(self) -> dict[str, tuple[float, float]]:
        """Position and velocity of every named hinge and slide joint."""
        m, d = self.model, self.data
        out = {}
        for j in range(m.njnt):
            if int(m.jnt_type[j]) not in (int(mujoco.mjtJoint.mjJNT_HINGE), int(mujoco.mjtJoint.mjJNT_SLIDE)):
                continue
            name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, j)
            if name:
                out[name] = (float(d.qpos[m.jnt_qposadr[j]]), float(d.qvel[m.jnt_dofadr[j]]))
        return out

    def robot_contacts(self) -> set[str]:
        """Names of geoms (and their bodies) outside the robot that the robot is touching right now."""
        m, d = self.model, self.data
        root = m.body_rootid[self.robot_body]
        out: set[str] = set()
        for c in d.contact[:d.ncon]:
            b1, b2 = m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]
            in1, in2 = m.body_rootid[b1] == root, m.body_rootid[b2] == root
            if in1 == in2:
                continue
            g, b = (c.geom2, b2) if in1 else (c.geom1, b1)
            for name in (mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g),
                         mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, b)):
                if name:
                    out.add(name)
        return out

    def contact_names(self) -> list[str]:
        """Geom and body names outside the robot, which contact checks can name."""
        m = self.model
        root = m.body_rootid[self.robot_body]
        names = {mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g) for g in range(m.ngeom)
                 if m.body_rootid[m.geom_bodyid[g]] != root}
        names |= {mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, b) for b in range(m.nbody)
                  if m.body_rootid[b] != root}
        return sorted(n for n in names if n)
