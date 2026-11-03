"""A robot's drivetrain, as the drive and turn blocks drive it: a tank drive or a swerve drive, open loop.

A drive wheel is a joint whose body carries a tread geom, as parts/assemble.py marks them. A swerve module is a
drive wheel hanging from a steer joint, a vertical hinge (within VERTICAL of its body's z) on the wheel's parent
body. The robot is a swerve drive when every tread wheel is on a module and every module's steer and wheel have a
motor; a tank drive when it has tread-wheel motors and no wheel is steered; neither (None) otherwise.

A block points the modules first (`steer_targets`: each module turned the least way, up to 90° either way, so a
wheel may drive backward), then drives the wheels at a set voltage (`wheel_volts`), each signed by where it rolls
right then: its rolling direction (its axis in the world, crossed with the world's z) against the way it should go.
For a drive that is the chassis's +x, -x, +y or -y (forward, back, left, right); for a turn of sign s (+ turns left,
counterclockwise seen from above) it is s * (-p_y, p_x)/|p| at the wheel's place p in the chassis frame. A tank
drive has no modules to point; its turn comes out as each side's wheels flipped (a left turn drives the left side,
p_y > 0, backward).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import mujoco
import numpy as np

STEER_TOL_DEG = 3.0       # a module is pointed when its steer joint is within this of its target (our call)
STEER_TIMEOUT = 1.0       # s: the wheels start after this even if a module isn't pointed yet (our call)
VERTICAL = 0.99           # a hinge axis this close to its body's z is a steer (azimuth) joint, as terrain/drive.py's

# The chassis-frame (x, y) of each direction.
DIRECTIONS = {"forward": (1.0, 0.0), "back": (-1.0, 0.0), "left": (0.0, 1.0), "right": (0.0, -1.0)}


@dataclass
class Module:
    steer: str                # the steer joint
    wheel: str                # the drive wheel's joint
    pos: np.ndarray           # the module's (x, y) in the chassis frame (its steer joint's anchor)


@dataclass
class Drivetrain:
    kind: str                 # "tank" | "swerve"
    wheels: list[str]         # the drive wheels' joints, each with a motor
    modules: list[Module]     # empty for a tank drive
    world: object = field(repr=False)
    places: dict[str, np.ndarray] = field(default_factory=dict, repr=False)   # wheel -> its (x, y), chassis frame
    targets: dict[str, float] = field(default_factory=dict)                   # steer joint -> qpos, as last pointed

    def _chassis(self) -> np.ndarray:
        return self.world.data.xmat[self.world.robot_body].reshape(3, 3)

    def _rolls(self, wheel: str) -> np.ndarray:
        """The wheel's rolling direction now, (x, y) in the chassis frame, unit length."""
        m, d = self.world.model, self.world.data
        r = (self._chassis().T @ np.cross(d.xaxis[m.joint(wheel).id], (0.0, 0.0, 1.0)))[:2]
        return r / max(float(np.linalg.norm(r)), 1e-9)

    def _wanted(self, wheel: str, direction: str | None, turn: float) -> np.ndarray:
        if turn:
            p = self.places[wheel]
            return math.copysign(1.0, turn) * np.array([-p[1], p[0]]) / max(float(np.linalg.norm(p)), 1e-9)
        return np.array(DIRECTIONS[direction or "forward"])

    def steer_targets(self, direction: str | None, turn: float = 0.0) -> dict[str, float]:
        """Steer joint -> the qpos that points its module along the drive direction, or along the turn; kept as the
        targets `steered` checks."""
        m, d = self.world.model, self.world.data
        self.targets = {}
        for mod in self.modules:
            r, want = self._rolls(mod.wheel), self._wanted(mod.wheel, direction, turn)
            delta = math.atan2(r[0] * want[1] - r[1] * want[0], float(r @ want))
            if abs(delta) > math.pi / 2:
                delta -= math.copysign(math.pi, delta)          # drive it backward
            j = m.joint(mod.steer).id
            q = float(d.qpos[m.jnt_qposadr[j]])
            self.targets[mod.steer] = q + delta * math.copysign(1.0, float(d.xaxis[j][2]))
        return dict(self.targets)

    def steered(self, data) -> bool:
        """Every steer joint is within STEER_TOL_DEG of its target."""
        m, tol = self.world.model, math.radians(STEER_TOL_DEG)
        return all(abs(float(data.qpos[m.jnt_qposadr[m.joint(s).id]]) - q) <= tol for s, q in self.targets.items())

    def wheel_volts(self, direction: str | None, turn: float, volts: float) -> dict[str, float]:
        """Each wheel's volts: `volts`, signed by whether the wheel rolls the way it should go, as it points now."""
        return {w: volts if float(self._rolls(w) @ self._wanted(w, direction, turn)) >= 0.0 else -volts
                for w in self.wheels}


def _hinges(m, body: int) -> list[int]:
    start = int(m.body_jntadr[body])
    return [k for k in range(start, start + int(m.body_jntnum[body]))
            if int(m.jnt_type[k]) == int(mujoco.mjtJoint.mjJNT_HINGE)]


def _rest_pose(world) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The robot body's rotation and origin and every joint's anchor at the model's rest pose, worked out once per
    world (on a scratch MjData: the world's own is left alone) and kept on it: the palette, the problems and each run
    ask for the drivetrain."""
    rest = getattr(world, "_drive_rest_pose", None)
    if rest is None:
        m = world.model
        d = mujoco.MjData(m)
        mujoco.mj_kinematics(m, d)
        rest = (d.xmat[world.robot_body].reshape(3, 3).copy(), d.xpos[world.robot_body].copy(), d.xanchor.copy())
        world._drive_rest_pose = rest
    return rest


def drivetrain(world, motors) -> Drivetrain | None:
    """The robot's drivetrain, from its tread wheels and `motors` (hal_drive.driven_joints: joint -> binding)."""
    m = world.model
    wheel_bodies = sorted({int(m.geom_bodyid[m.geom(g).id]) for g in (world.parts.tread_geoms if world.parts else ())})
    wheels, steers = [], []          # per tread wheel: its joint, and its steer joint (None: not steered)
    for b in wheel_bodies:
        joint = next((m.joint(k).name for k in _hinges(m, b) if m.joint(k).name), None)
        steer = next((m.joint(k).name for k in _hinges(m, int(m.body_parentid[b]))
                      if abs(float(m.jnt_axis[k][2])) > VERTICAL), None)
        wheels.append(joint)
        steers.append(steer)
    if not wheels:
        return None
    rot, origin, anchors = _rest_pose(world)

    def place(joint: str) -> np.ndarray:
        return (rot.T @ (anchors[m.joint(joint).id] - origin))[:2].copy()

    if all(steers):
        if not all(w in motors and s in motors for w, s in zip(wheels, steers)):
            return None
        modules = [Module(s, w, place(s)) for w, s in zip(wheels, steers)]
        return Drivetrain("swerve", list(wheels), modules, world, {mod.wheel: mod.pos for mod in modules})
    if any(steers):
        return None
    driven = [w for w in wheels if w in motors]
    if not driven:
        return None
    return Drivetrain("tank", driven, [], world, {w: place(w) for w in driven})
