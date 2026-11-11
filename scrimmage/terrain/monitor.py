"""The robot terrain signals, read every physics step: is the robot on the ground, tipping, or stranded?

The ground is the carpet and the range's field elements: the geoms in ELEMENT_GROUP, and nothing else, so a ball is
never ground. The drive wheels are the robot's tread geoms (Built.tread_geoms). A tread wheel is down when it touches
the ground, or its bottom is within WHEEL_DOWN_GAP of it: a wheel scrubbing sideways in a turn loses contact on most
physics steps while it is still on the carpet, and contact alone would flicker.

- `robot.ground_gap` (m): 0 while any tread wheel is down, else the smallest distance from a tread wheel's
  lowest point straight down to the ground (one mj_ray per wheel, in ELEMENT_GROUP only).
- `robot.wheels_down`: how many tread wheels are down.
- `robot.air_time` (s): how long no robot geom has touched anything outside the robot (truly airborne), 0 otherwise.
- `robot.pitch`, `robot.roll` (deg): the Pigeon 2's, as `bindings.pigeon_attitude` gives them: pitch nose down
  positive, roll left side up positive, so they match what a team's gyro logs show.
- `robot.tilt` (deg): the angle between the chassis's z axis and vertical, 0 to 180.
- `robot.z` (m): the chassis origin's height.
- `robot.stranded`: 1 once fewer than STRANDED_WHEELS tread wheels have been down for more than STRANDED_S of physics
  steps on which the robot touches something (it rests on balls or an element's edge): steps in the air pause the
  clock, so a robot bouncing on balls is still stranded, and a long jump isn't. Two wheels down reset it, and so does
  hanging: touching a bar or a switch (scrimmage/terrain/climb.py's holds) and no ground.
- `robot.tipped`: 1 once tilt passes TIP_DEG, latched until reset.

Scenario checks tick every lockstep step, several physics steps apart, so `sample()` peak-holds the values since the
last `consume()`: the largest gap, air time, tilt and z, the pitch and roll of largest magnitude (sign kept), the fewest
wheels down, and stranded if it was at any step. The check runner consumes once per tick, after every check has read.
"""

from __future__ import annotations

import math

import mujoco
import numpy as np

from ..bindings import pigeon_angles
from ..flexcontact import contact_ids, id_body, id_name, n_ids
from ..parts.field import ELEMENT_GROUP
from .climb import hold_geoms

TIP_DEG = 60.0            # deg: tipped over past this tilt
STRANDED_S = 0.5          # s: stranded after this long with too few wheels down, resting on something
STRANDED_WHEELS = 2       # tread wheels down below which the robot can't drive itself off
WHEEL_DOWN_GAP = 0.005    # m: a tread wheel this close to the ground is down, touching it on this step or not
RAY_LIFT = 0.01           # m: each ray starts this far above the wheel's lowest point, so a wheel sunk into its
                          # contact still finds the surface it is sinking into

ROBOT_SIGNALS = ("ground_gap", "wheels_down", "air_time", "pitch", "roll", "tilt", "z", "stranded", "tipped")
WHEEL_SIGNALS = ("ground_gap", "wheels_down", "stranded")    # these need tread wheels
_DOWN = np.array([0.0, 0.0, -1.0])
_CYLINDER, _SPHERE, _CAPSULE, _BOX = (int(t) for t in (mujoco.mjtGeom.mjGEOM_CYLINDER, mujoco.mjtGeom.mjGEOM_SPHERE,
                                                       mujoco.mjtGeom.mjGEOM_CAPSULE, mujoco.mjtGeom.mjGEOM_BOX))


class TerrainMonitor:
    def __init__(self, world):
        m = world.model
        self.world, self.model, self.data = world, m, world.data
        self.body = world.robot_body
        root = m.body_rootid[world.robot_body]
        # per contact id (flexcontact: the geoms, then each flex): part of the robot; the ground (a flex is neither)
        self._robot = m.body_rootid[id_body(m)] == root
        self._ground = np.concatenate([m.geom_group == ELEMENT_GROUP, np.zeros(m.nflex, bool)]) & ~self._robot
        names = list(world.parts.tread_geoms) if world.parts is not None else []
        self.treads = np.array([m.geom(n).id for n in names], dtype=np.int64)
        self._tread_index = np.full(n_ids(m), -1, dtype=np.int64)
        self._tread_index[self.treads] = np.arange(len(self.treads))
        self._wheels = [(int(g), int(m.geom_type[g]), m.geom_size[g].tolist(), float(m.geom_rbound[g]))
                        for g in self.treads]
        self._robot_l = self._robot.tolist()        # plain lists for the per-step loops
        self._ground_l = self._ground.tolist()
        self._tread_index_l = self._tread_index.tolist()
        layout = world.parts.layout if world.parts is not None else None
        # The robot's own hinge and slide joints: (name, qpos address, rest position), for the report's notes.
        self._joints = []
        for j in range(m.njnt):
            name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, j)
            if (name and int(m.jnt_type[j]) in (int(mujoco.mjtJoint.mjJNT_HINGE), int(mujoco.mjtJoint.mjJNT_SLIDE))
                    and m.body_rootid[m.jnt_bodyid[j]] == root):
                adr = int(m.jnt_qposadr[j])
                self._joints.append((name, adr, float(m.qpos0[adr])))
        self._platform_qpos = dict(layout.platforms) if layout is not None else {}    # name -> (tilt_plus, tilt_minus)
        # Per contact id: a bar, chain or switch the robot can hang from; None on a range without one, which pays
        # nothing.
        holds = [g for geoms in hold_geoms(m, layout).values() for g in geoms]
        self._hold_l = None
        if holds:
            self._hold_l = [False] * n_ids(m)
            for g in holds:
                self._hold_l[g] = True
        self.platforms: list[str] = sorted(self._platform_qpos)
        self._groups = np.array([1 if g == ELEMENT_GROUP else 0 for g in range(mujoco.mjNGROUP)], dtype=np.uint8)
        self._gid = np.array([-1], dtype=np.int32)
        self._pnt = np.zeros(3)
        self._dt = float(m.opt.timestep)
        self.reset()

    @property
    def has_treads(self) -> bool:
        return len(self.treads) > 0

    def reset(self) -> None:
        """Clear the timers, the latches and the peak-hold (the world was reset)."""
        self._air = 0.0
        self._stuck = 0.0
        self._stranded = False
        self._tipped = False
        self.first_tipped: tuple[float, float, tuple] | None = None     # (t, tilt deg, (x, y, heading deg))
        self.first_stranded: tuple[float, str] | None = None             # (t, what the chassis rests on)
        # The robot's joints when each latched, as {name: (position, rest)}: a note says where the elevator was then.
        self.tipped_joints: dict[str, tuple[float, float]] = {}
        self.stranded_joints: dict[str, tuple[float, float]] = {}
        self._peak: dict | None = None

    def _joint_snapshot(self) -> dict[str, tuple[float, float]]:
        q = self.data.qpos
        return {name: (float(q[adr]), rest) for name, adr, rest in self._joints}

    def consume(self) -> None:
        """Start a new peak-hold: the next sample() covers the physics steps from here."""
        self._peak = None

    def sample(self) -> dict[str, float]:
        """Every `robot.*` signal, peak-held since the last consume(); with no physics step since, the current values."""
        if self._peak is None:
            v = self._read()[0]
            return self._with_state(v)
        return dict(self._peak)

    def platform_angle(self, name: str) -> float:
        """Platform `name`'s tilt in degrees, positive when its +x end is down."""
        a, b = self._platform_qpos[name]
        return math.degrees(float(self.data.qpos[a] + self.data.qpos[b]))

    def after_physics(self) -> None:
        v, touching, contacts = self._read()
        d = self.data
        self._air = 0.0 if touching else self._air + self._dt
        if not self.has_treads or v["robot.wheels_down"] >= STRANDED_WHEELS or self._hanging(contacts[1]):
            self._stuck = 0.0
        elif touching:      # in the air the clock pauses: a robot bouncing on balls is still stuck on them
            self._stuck += self._dt
        self._stranded = self._stuck > STRANDED_S
        if self._stranded and self.first_stranded is None:
            self.first_stranded = (round(float(d.time), 4), self._rests_on(contacts))
            self.stranded_joints = self._joint_snapshot()
        if v["robot.tilt"] > TIP_DEG and not self._tipped:
            self._tipped = True
            x, y, yaw = self.world.robot_pose()
            self.first_tipped = (round(float(d.time), 4), v["robot.tilt"], (x, y, math.degrees(yaw)))
            self.tipped_joints = self._joint_snapshot()
        v = self._with_state(v)
        p = self._peak
        if p is None:
            self._peak = v
            return
        for k in ("robot.air_time", "robot.tilt", "robot.z", "robot.stranded", "robot.tipped"):
            if v[k] > p[k]:
                p[k] = v[k]
        for k in ("robot.pitch", "robot.roll"):
            if abs(v[k]) > abs(p[k]):
                p[k] = v[k]
        if self.has_treads:
            if v["robot.ground_gap"] > p["robot.ground_gap"]:
                p["robot.ground_gap"] = v["robot.ground_gap"]
            if v["robot.wheels_down"] < p["robot.wheels_down"]:
                p["robot.wheels_down"] = v["robot.wheels_down"]

    def _hanging(self, other: list[int]) -> bool:
        """Whether the robot hangs: touching a bar, chain or switch (contact ids `other` it touches) and no ground."""
        held = self._hold_l
        if held is None or not any(held[o] for o in other):
            return False
        ground = self._ground_l
        return not any(ground[o] for o in other)

    def _with_state(self, v: dict) -> dict:
        v["robot.air_time"] = self._air
        v["robot.stranded"] = int(self._stranded)
        v["robot.tipped"] = int(self._tipped)
        return v

    def _read(self) -> tuple[dict, bool, tuple]:
        """This step's instantaneous values, whether the robot touches anything outside itself, and the contacts
        between the robot and the rest as (robot geoms, other contact ids)."""
        d = self.data
        robot = self._robot_l
        mine, other = [], []
        for g1, g2 in contact_ids(self.model, d).tolist():   # plain Python: a handful of contacts, cheaper than numpy
            r1 = robot[g1]
            if r1 != robot[g2]:
                mine.append(g1 if r1 else g2)
                other.append(g2 if r1 else g1)
        touching = bool(mine)
        b = self.body
        _, pitch, roll = pigeon_angles(d.xquat[b])
        v = {"robot.pitch": pitch,          # the Pigeon 2's: a right-hand turn about y, nose down positive
             "robot.roll": roll,
             "robot.tilt": math.degrees(math.acos(max(-1.0, min(1.0, float(d.xmat[b][8]))))),
             "robot.z": float(d.xpos[b][2]),
             "robot.ground_gap": None, "robot.wheels_down": None}
        if self.has_treads:
            ground, index = self._ground_l, self._tread_index_l
            on = {index[s] for s, o in zip(mine, other) if ground[o] and index[s] >= 0}
            gaps = [0.0 if i in on else self._gap(*w) for i, w in enumerate(self._wheels)]
            down = sum(1 for x in gaps if x <= WHEEL_DOWN_GAP)
            v["robot.ground_gap"] = 0.0 if down else min(gaps)
            v["robot.wheels_down"] = down
        return v, touching, (mine, other)

    def _gap(self, gid: int, typ: int, size, rbound: float) -> float:
        """How far tread geom `gid`'s lowest point is above the ground straight beneath it (0 if at or below)."""
        d = self.data
        x, y, z = d.geom_xpos[gid].tolist()
        r = d.geom_xmat[gid].tolist()                   # the geom's rotation, row-major
        if typ in (_CYLINDER, _CAPSULE):
            ax, ay, az = r[2], r[5], r[8]               # its axis (the geom's z) in the world
            h = math.copysign(size[1], az)              # to the lower end
            x, y, z = x - h * ax, y - h * ay, z - h * az
            if typ == _CAPSULE:
                z -= size[0]
            else:                                       # then down the rim, perpendicular to the axis
                ox, oy, oz = az * ax, az * ay, az * az - 1.0
                n = math.sqrt(ox * ox + oy * oy + oz * oz)
                if n > 1e-9:
                    k = size[0] / n
                    x, y, z = x + k * ox, y + k * oy, z + k * oz
        elif typ == _SPHERE:
            z -= size[0]
        elif typ == _BOX:
            z -= abs(r[6]) * size[0] + abs(r[7]) * size[1] + abs(r[8]) * size[2]
        else:
            z -= rbound
        bottom = z
        pnt = self._pnt
        pnt[0], pnt[1], pnt[2] = x, y, z + RAY_LIFT
        # Only the ground's group: no ball, and no robot geom (none is in it). A hand-written model's carpet isn't in
        # the group, so a ray can miss: the carpet is then the z = 0 plane.
        dist = mujoco.mj_ray(self.model, d, pnt, _DOWN, self._groups, 1, -1, self._gid)
        gap = bottom if dist < 0.0 else dist - RAY_LIFT
        return max(0.0, gap)

    def _rests_on(self, contacts) -> str:
        """What the stranded robot rests on: the body (or, for the world's own geoms, the geom) its first contact
        with something other than the ground touches; else the first ground contact that isn't a tread wheel's."""
        m = self.model
        mine, other = contacts
        pick = [o for o in other if not self._ground_l[o]]
        if not pick:
            pick = [o for s, o in zip(mine, other) if self._tread_index_l[s] < 0] or list(other)
        if not pick:
            return "nothing"
        g = int(pick[0])
        if g >= m.ngeom:        # a flex (a chain)
            return id_name(m, g)
        b = int(m.geom_bodyid[g])
        name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, b) if b != 0 else None
        return name or mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g) or f"geom {g}"
