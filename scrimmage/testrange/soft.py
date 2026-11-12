"""A soft NOTE in the world (spec D8): a ring of RING_SEGMENTS capsules joined by ball joints, swapped in for a rigid
NOTE near a robot (the soft-note spike's candidate (b)).

The ring:
- segment j is body `<name>.s<j>`, a capsule `<name>.g<j>` along the ring's center line, its frame at the capsule's
  middle with x along the ring's tangent and z along the kind's axis. s0 carries the free joint `<name>.free`; each
  later segment hangs from the one before by a ball joint `<name>.j<j>` at its start, with a bending and twisting
  spring and a damper. The equality `<name>.close` connects the last segment's end to s0's start, and the pair is
  excluded from contact.
- The ring lies in the plane across the kind's axis through the kind's body origin (its center of mass), so a rigid
  slot's pose maps onto it: a ring built at `pos` is the rigid NOTE at `pos`, unturned.
- Its squash across the tube is MuJoCo's soft contact: a negative solref (stiffness k, damping b, per unit of the
  contact's effective mass) with impedance SOFT_SOLIMP. Its bending is the ball joints' springs.

Its numbers follow a rule, not a fit at build time (R10). The spike fitted the ring once, at `stiffness` FIT_STIFFNESS
and restitution 0.3 (FIT_CONTACT_K, FIT_JOINT_K). The world scales from there:
- the contact stiffness and the joint stiffness are in proportion to the kind's `stiffness`. The contact
  stiffness is also scaled by FIT_MASS / the kind's mass: MuJoCo's negative solref is per unit of the contact's
  effective mass, so without it a NOTE twice as heavy would squeeze twice as hard (R10, extended in task 5's review);
- the contact damping is DAMPING_SCALE x the closed form 2 zeta(e) sqrt(k), zeta(e) = -ln e / sqrt(pi^2 + ln^2 e);
- the joint damping gives one segment swinging about its joint a damping ratio of JOINT_ZETA.

MuJoCo's soft contact is not a plain spring: with impedance d a static load sees d / (1 - d) times the stiffness an
impact does, so SOFT_SOLIMP's d = 0.2 keeps the squeeze and the drop within 1.25x of each other. That is also why the
closed form alone gives too much damping, and why the scale was measured (R10). tests/test_range_soft.py checks the
rule against both of the spike's fitted foams.

A range with a soft kind builds POOL_SIZE rings per soft kind, `soft.<k>`, parked under the floor with PARKED_BITS
(build.add_range); a parked ring is held in place (ParkedRings) and may sleep.
"""

from __future__ import annotations

import math
import weakref
from dataclasses import dataclass

import mujoco
import numpy as np

from .build import BALL_FRICTION, RIGID_SOLMIX
from .pieces import SOFT_SEGMENTS, PieceKind, piece_color, ring_diameters

RING_SEGMENTS = SOFT_SEGMENTS  # source: soft-note spike, candidate (b)
POOL_SIZE = 2  # choice: spec D8
SOFT_REACH = 0.25  # choice: spec D8
SOFT_LINGER = 0.25  # choice: spec D8
SOFT_SOLIMP = (0.2, 0.2, 0.001, 0.5, 2.0)  # source: spike: d = 0.2 keeps a static squeeze within 1.25x of an impact
FIT_STIFFNESS = 2000.0  # source: soft-note spike, chain soft fit
FIT_CONTACT_K = 11211.4  # source: soft-note spike, chain soft fit
FIT_JOINT_K = 0.10276  # source: soft-note spike, chain soft fit
FIT_MASS = 0.235  # source: soft-note spike: the fit's NOTE mass (the 2024-note preset's)
JOINT_ZETA = 0.3  # source: spike
DAMPING_SCALE = 0.85  # source: derived, R10: the spike's two fits, b / (2 zeta(0.3) sqrt k) = 0.849 soft, 0.860 firm
CLOSE_SOLREF = (0.004, 1.0)  # source: spike's closing connect
POOL_PREFIX = "soft"  # the pool's rings are soft.0, soft.1, ...


def damping_ratio_for(e: float) -> float:
    """A damped spring's damping ratio for restitution e (0 < e < 1): -ln e / sqrt(pi^2 + ln^2 e)."""
    le = math.log(e)
    return -le / math.sqrt(math.pi ** 2 + le * le)


@dataclass(frozen=True)
class RingParams:
    contact_k: float    # the soft contact's stiffness (MuJoCo's negative solref units)
    contact_b: float    # its damping
    joint_k: float      # each ball joint's bending and twisting stiffness, N m/rad
    joint_b: float      # its damping, N m s/rad


@dataclass(frozen=True)
class _Dims:
    big_r: float        # the tube's center-line radius, (diameter + inner_diameter) / 4
    r: float            # the tube's radius, (diameter - inner_diameter) / 4
    half: float         # each capsule's half-length, big_r sin(pi / n)
    m: float            # each segment's mass
    inertia: tuple[float, float, float]     # each segment's, about its own axes (x along the capsule)


def _dims(kind: PieceKind) -> _Dims:
    """The ring's sizes from the kind (pieces.ring_diameters; a soft kind always has a hole, pieces._check_soft)."""
    outer, inner = ring_diameters(kind)
    assert inner is not None, "a soft ring needs a hole"
    big_r, r = (outer + inner) / 4.0, (outer - inner) / 4.0
    n = RING_SEGMENTS
    half = big_r * math.sin(math.pi / n)
    m = kind.mass / n
    length = 2.0 * half
    # a solid capsule's inertia, taken as a cylinder's of its length (the spike's)
    ixx = 0.5 * m * r * r
    iyy = m * (3.0 * r * r + length * length) / 12.0
    return _Dims(big_r, r, half, m, (ixx, iyy, iyy))


def ring_params(kind: PieceKind) -> RingParams:
    """The ring's contact and joint numbers for the kind's `stiffness` and `restitution` (R10)."""
    scale = kind.stiffness / FIT_STIFFNESS
    k = FIT_CONTACT_K * scale * (FIT_MASS / kind.mass)
    kj = FIT_JOINT_K * scale
    d = _dims(kind)
    i_joint = d.inertia[1] + d.m * d.half * d.half      # a segment about its joint: I_seg + m (l/2)^2
    return RingParams(contact_k=k, contact_b=DAMPING_SCALE * 2.0 * damping_ratio_for(kind.restitution) * math.sqrt(k),
                      joint_k=kj, joint_b=2.0 * JOINT_ZETA * math.sqrt(kj * i_joint))


def _basis(axis) -> np.ndarray:
    """Columns u, v, a: a right-handed frame whose a is the kind's axis (u = x, v = y for axis z)."""
    a = np.asarray(axis, dtype=float)
    a = a / np.linalg.norm(a)
    ref = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 0.0, 1.0])
    u = ref - (ref @ a) * a
    u /= np.linalg.norm(u)
    return np.column_stack([u, np.cross(a, u), a])


def _quat_of(rot: np.ndarray) -> np.ndarray:
    q = np.zeros(4)
    mujoco.mju_mat2Quat(q, np.ascontiguousarray(rot, dtype=float).ravel())
    return q


def _rz(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def add_ring(spec: mujoco.MjSpec, name: str, kind: PieceKind, pos, bits: dict) -> None:
    """A soft ring of kind `kind` into `spec`, its center at `pos`, unturned (see the module docstring); `bits` its
    geoms' contype and conaffinity."""
    n, d, p = RING_SEGMENTS, _dims(kind), ring_params(kind)
    frame = _basis(kind.axis)
    dth = 2.0 * math.pi / n
    th = dth / 2.0
    radius_c = d.big_r * math.cos(math.pi / n)      # each capsule's middle lies on the chord
    c0 = frame @ np.array([radius_c * math.cos(th), radius_c * math.sin(th), 0.0])
    rel_pos = [d.half + d.half * math.cos(dth), d.half * math.sin(dth), 0.0]
    rel_quat = list(_quat_of(_rz(dth)))
    contact = {"condim": 6, "friction": [kind.friction, *BALL_FRICTION[1:]], "solref": [-p.contact_k, -p.contact_b],
               "solimp": list(SOFT_SOLIMP), "solmix": RIGID_SOLMIX}
    rgba = [*piece_color(kind), 1.0]
    parent = spec.worldbody
    for j in range(n):
        if j == 0:
            body = parent.add_body(name=f"{name}.s0", pos=[float(x) for x in np.asarray(pos, dtype=float) + c0],
                                   quat=list(_quat_of(frame @ _rz(th + math.pi / 2.0))))
            body.add_freejoint(name=f"{name}.free")
        else:
            body = parent.add_body(name=f"{name}.s{j}", pos=list(rel_pos), quat=rel_quat)
            body.add_joint(name=f"{name}.j{j}", type=mujoco.mjtJoint.mjJNT_BALL, pos=[-d.half, 0.0, 0.0],
                           stiffness=[p.joint_k, 0.0, 0.0], damping=[p.joint_b, 0.0, 0.0])
        body.mass, body.ipos, body.inertia, body.explicitinertial = d.m, [0.0, 0.0, 0.0], list(d.inertia), True
        body.add_geom(name=f"{name}.g{j}", type=mujoco.mjtGeom.mjGEOM_CAPSULE, size=[d.r, d.half, 0.0],
                      quat=[math.sqrt(0.5), 0.0, math.sqrt(0.5), 0.0], density=0, rgba=rgba, **contact, **bits)
        parent = body
    eq = spec.add_equality(name=f"{name}.close", type=mujoco.mjtEq.mjEQ_CONNECT, objtype=mujoco.mjtObj.mjOBJ_BODY,
                           name1=f"{name}.s{n - 1}", name2=f"{name}.s0")
    eq.data[:3] = [d.half, 0.0, 0.0]
    eq.solref = list(CLOSE_SOLREF)
    spec.add_exclude(bodyname1=f"{name}.s{n - 1}", bodyname2=f"{name}.s0")




# ------------------------------------------------------------------------------------------- the ring's state

def _mats(q: np.ndarray) -> np.ndarray:
    """(n, 4) quaternions (w x y z) as (n, 3, 3) rotation matrices (MuJoCo's own, one call each: cheaper than numpy
    on a few rows)."""
    out = np.empty((len(q), 9))
    for k in range(len(q)):
        mujoco.mju_quat2Mat(out[k], q[k])
    return out.reshape(-1, 3, 3)


_P1, _P2 = [1, 2, 0], [2, 0, 1]


def _cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Row-wise cross products of (n, 3) arrays (np.cross costs several times more on a few rows)."""
    return a[:, _P1] * b[:, _P2] - a[:, _P2] * b[:, _P1]


def _apply(rot: np.ndarray, v: np.ndarray) -> np.ndarray:
    """(n, 3, 3) @ (n, 3), row by row."""
    return (rot @ v[:, :, None])[:, :, 0]


class Ring:
    """One ring's addresses and constants in a compiled model, worked out once (ring_view caches it): its bodies (s0
    first, consecutive), its joints' qpos and dof rows (consecutive: 7 + 4 per ball joint, 6 + 3), each segment's
    fixed transform from the one before, and its rest shape."""

    def __init__(self, model: mujoco.MjModel, ring: str):
        b0 = model.body(f"{ring}.s0").id
        self.bodies = b0 + np.arange(RING_SEGMENTS)
        jnt = model.body_jntadr[self.bodies]
        self.free_q, self.free_v = int(model.jnt_qposadr[jnt[0]]), int(model.jnt_dofadr[jnt[0]])
        self.q = slice(self.free_q, self.free_q + 7 + 4 * (RING_SEGMENTS - 1))
        self.v = slice(self.free_v, self.free_v + 6 + 3 * (RING_SEGMENTS - 1))
        self.tree = int(model.body_treeid[b0])
        self.m = model.body_mass[self.bodies].copy()
        self.mass = float(self.m.sum())
        self._ipos = model.body_ipos[self.bodies].copy()
        self._iframe = _mats(model.body_iquat[self.bodies])
        self._idiag = model.body_inertia[self.bodies].copy()
        # a later segment's frame before its joint turns: the previous one's turned by B and moved by p; its joint
        # sits at a in its own frame. Segment k = R_{k-1} (B_k Q_k), at x_{k-1} + R_{k-1} (p_k + B_k a_k - B_k Q_k a_k)
        b = self.bodies[1:]
        self._bmat = _mats(model.body_quat[b])
        self._a = model.jnt_pos[jnt[1:]].copy()
        self._c = model.body_pos[b] + np.einsum("nij,nj->ni", self._bmat, self._a)   # each anchor in the parent
        self._local = np.zeros((RING_SEGMENTS - 1, 4, 4))      # scratch: each later segment's local transform
        self._local[:, 3, 3] = 1.0
        self.qpos0 = model.qpos0[self.q].copy()
        com, _, _, _ = self._segments(self.qpos0, None)
        c = (self.m[:, None] * com).sum(0) / self.mass
        self.rest = com - c                                     # each segment's center about the ring's
        self.p0 = self.qpos0[:3] - c                            # s0's origin about it, and its quaternion
        self.q0 = self.qpos0[3:7].copy()

    def _segments(self, qpos: np.ndarray, qvel: np.ndarray | None):
        """From the ring's own qpos (and qvel) rows: each segment's center of mass (n, 3), rotation (n, 3, 3), and
        with qvel its center's velocity and its angular velocity (n, 3), world frame. The ring's own forward
        kinematics: after mj_step MuJoCo's xipos describe the step's start."""
        n = RING_SEGMENTS
        # each segment's pose as a 4x4 transform: s0's from the free joint, each later one the one before's times its
        # own local transform (B_k Q_k, moved so its joint's anchor stays put)
        t = np.zeros((n, 4, 4))
        t[:, 3, 3] = 1.0
        t[0, :3, :3] = _mats(qpos[None, 3:7])[0]
        t[0, :3, 3] = qpos[:3]
        local = self._local
        local[:, :3, :3] = self._bmat @ _mats(qpos[7:].reshape(-1, 4))     # B_k Q_k
        local[:, :3, 3] = self._c - _apply(local[:, :3, :3], self._a)
        for k in range(1, n):
            np.matmul(t[k - 1], local[k - 1], out=t[k])
        rot, x = t[:, :3, :3], t[:, :3, 3]
        com = x + _apply(rot, self._ipos)
        if qvel is None:
            return com, rot, None, None
        # angular velocity: s0's from the free joint (its own frame), each ball joint's in its child's frame
        rel = np.empty((n, 3))
        rel[0] = rot[0] @ qvel[3:6]
        rel[1:] = _apply(rot[1:], qvel[6:].reshape(-1, 3))
        w = np.cumsum(rel, axis=0)
        # each joint's anchor (s0's origin for s0), and its velocity, carried along the chain
        anchor = np.empty((n, 3))
        anchor[0] = x[0]
        anchor[1:] = x[:-1] + _apply(rot[:-1], self._c)
        step = np.zeros((n, 3))
        step[1:] = _cross(w[:-1], anchor[1:] - anchor[:-1])
        va = qvel[:3] + np.cumsum(step, axis=0)
        return com, rot, va + _cross(w, com - anchor), w

    def read(self, data: mujoco.MjData, spin: bool = True):
        """read_rigid, for this ring; the spin only when `spin` (None otherwise)."""
        com, rot, v, w = self._segments(data.qpos[self.q], data.qvel[self.v])
        c = self.m @ com / self.mass
        vc = self.m @ v / self.mass
        r = com - c
        u, _, vt = np.linalg.svd(self.rest.T @ r)              # Kabsch: the rotation taking rest onto r
        s = np.diag([1.0, 1.0, float(np.sign(np.linalg.det(vt.T @ u.T)))])
        quat = _quat_of(vt.T @ s @ u.T)
        if not spin:
            return c, quat, vc, None
        frame = rot @ self._iframe
        inertia = (frame * self._idiag[:, None, :]) @ frame.transpose(0, 2, 1)     # each segment's, world frame
        ang = self.m @ _cross(r, v - vc) + _apply(inertia, w).sum(0)
        mr = self.m[:, None] * r
        total = inertia.sum(0) + float((mr * r).sum()) * np.eye(3) - r.T @ mr       # parallel axes, summed
        return c, quat, vc, np.linalg.solve(total, ang)

    def write(self, data: mujoco.MjData, pos, quat, vel, ang_vel) -> None:
        """write_rigid, for this ring."""
        rq = _mats(np.asarray(quat, dtype=float)[None])[0]
        arm = rq @ self.p0
        q_s0 = np.zeros(4)
        mujoco.mju_mulQuat(q_s0, np.asarray(quat, dtype=float), self.q0)
        qpos, qvel = np.zeros(self.q.stop - self.q.start), np.zeros(self.v.stop - self.v.start)
        qpos[:3] = np.asarray(pos, dtype=float) + arm
        qpos[3:7] = q_s0
        qpos[7::4] = 1.0                                        # the ball joints at rest
        ang_vel = np.asarray(ang_vel, dtype=float)
        qvel[:3] = np.asarray(vel, dtype=float) + np.cross(ang_vel, arm)
        qvel[3:6] = _mats(q_s0[None])[0].T @ ang_vel             # the free joint's spin: s0's own frame
        data.qpos[self.q] = qpos
        data.qvel[self.v] = qvel


_RINGS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()     # model -> {ring name: Ring}


def ring_view(model: mujoco.MjModel, ring: str) -> Ring:
    """Ring `ring` of `model`, worked out once per model."""
    rings = _RINGS.setdefault(model, {})
    if ring not in rings:
        rings[ring] = Ring(model, ring)
    return rings[ring]


def ring_rest(model: mujoco.MjModel, ring: str) -> np.ndarray:
    """(RING_SEGMENTS, 3): each segment's center of mass in the NOTE's own frame (its origin the ring's center of
    mass). The ring is built unturned, so the NOTE's frame is the world's turned by nothing."""
    return ring_view(model, ring).rest


def write_rigid(model: mujoco.MjModel, data: mujoco.MjData, ring: str, pos, quat, vel, ang_vel) -> None:
    """Put the ring at the rigid NOTE's pose (its center of mass at `pos`, turned by `quat`, w x y z), moving with
    its velocity field (`vel` at the center, spin `ang_vel`, world frame), its ball joints at rest."""
    ring_view(model, ring).write(data, pos, quat, vel, ang_vel)


def read_rigid(model: mujoco.MjModel, data: mujoco.MjData, ring: str, spin: bool = True):
    """The rigid NOTE that best stands for the ring now: (center of mass, the Kabsch rotation of the segments'
    centers against ring_rest (w x y z), its momentum over its mass, and its spin from its angular momentum about
    the center with the segments' summed inertia there). The spin, a 3x3 solve, is worked out only when `spin`
    (it is None otherwise): a per-step shadow needs only the pose and velocity, a swap back the spin too."""
    return ring_view(model, ring).read(data, spin)


class ParkedRings:
    """The pool's rings held where they were built (PARK_Z, under the field): a parked ring touches nothing, so it
    falls; after each step each awake one is put back at rest, as a parked ball slot is (Balls.after_step). One asleep
    was put back the step it fell asleep and hasn't moved since."""

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, rings: list[str]):
        self.data = data
        self.rings = [ring_view(model, r) for r in rings]
        self.trees = np.array([r.tree for r in self.rings], dtype=int)

    def hold(self) -> None:
        d = self.data
        for k in np.flatnonzero(d.tree_asleep[self.trees] < 0):
            ring = self.rings[k]
            d.qpos[ring.q] = ring.qpos0
            d.qvel[ring.v] = 0.0
