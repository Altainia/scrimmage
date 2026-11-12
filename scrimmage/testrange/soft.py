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
- Its squash across the tube is MuJoCo's soft contact: a negative solref (stiffness k, damping b, mass-independent
  units) with impedance SOFT_SOLIMP. Its bending is the ball joints' springs.

Its numbers follow a rule, not a fit at build time (R10). The spike fitted the ring once, at `stiffness` FIT_STIFFNESS
and restitution 0.3 (FIT_CONTACT_K, FIT_JOINT_K). The world scales from there:
- the contact stiffness and the joint stiffness are in proportion to the kind's `stiffness`;
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
from dataclasses import dataclass

import mujoco
import numpy as np

from .build import BALL_FRICTION, RIGID_SOLMIX
from .pieces import PieceKind, piece_color

RING_SEGMENTS = 16  # source: soft-note spike, candidate (b)
POOL_SIZE = 2  # choice: spec D8
SOFT_REACH = 0.25  # choice: spec D8
SOFT_LINGER = 0.25  # choice: spec D8
SOFT_SOLIMP = (0.2, 0.2, 0.001, 0.5, 2.0)  # source: spike: d = 0.2 keeps a static squeeze within 1.25x of an impact
FIT_STIFFNESS = 2000.0  # source: soft-note spike, chain soft fit
FIT_CONTACT_K = 11211.4  # source: soft-note spike, chain soft fit
FIT_JOINT_K = 0.10276  # source: soft-note spike, chain soft fit
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
    """The ring's sizes from the kind: its outer diameter is its solids' widest reach from the axis, its inner the
    hole's (none: a solid ring, inner 0)."""
    a = np.asarray(kind.axis, dtype=float)
    pts = kind._points
    radial = pts - np.outer(pts @ a, a)
    outer = 2.0 * float(np.linalg.norm(radial, axis=1).max())
    inner = kind.hole.diameter if kind.hole is not None else 0.0
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
    k = FIT_CONTACT_K * scale
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

def _mat(q) -> np.ndarray:
    m = np.zeros(9)
    mujoco.mju_quat2Mat(m, np.asarray(q, dtype=float))
    return m.reshape(3, 3)


@dataclass(frozen=True)
class _Chain:
    """A ring's addresses in a compiled model: its bodies (s0 first, consecutive), its free joint's qpos and dof
    addresses, and its ball joints' (in segment order)."""

    bodies: np.ndarray
    free_q: int
    free_v: int
    ball_q: np.ndarray
    ball_v: np.ndarray

    @property
    def q(self) -> slice:
        """The ring's qpos rows (its joints' are consecutive: 7 + 4 per ball joint)."""
        return slice(self.free_q, self.free_q + 7 + 4 * len(self.ball_q))

    @property
    def v(self) -> slice:
        return slice(self.free_v, self.free_v + 6 + 3 * len(self.ball_v))


def _chain(model: mujoco.MjModel, ring: str) -> _Chain:
    b0 = model.body(f"{ring}.s0").id
    bodies = b0 + np.arange(RING_SEGMENTS)
    jnt = model.body_jntadr[bodies]
    return _Chain(bodies, int(model.jnt_qposadr[jnt[0]]), int(model.jnt_dofadr[jnt[0]]),
                  model.jnt_qposadr[jnt[1:]].astype(int), model.jnt_dofadr[jnt[1:]].astype(int))


def _segments(model: mujoco.MjModel, ch: _Chain, qpos: np.ndarray, qvel: np.ndarray | None = None):
    """Each segment's center of mass (n, 3), rotation (n, 3, 3), and, with qvel, its center's velocity (n, 3) and
    its angular velocity (n, 3), all in the world frame: the ring's own forward kinematics, from qpos and qvel
    alone (MuJoCo's xipos after mj_step are the step's start)."""
    n = len(ch.bodies)
    x, rot = np.zeros((n, 3)), np.zeros((n, 3, 3))
    v, w = np.zeros((n, 3)), np.zeros((n, 3))
    f = ch.free_q
    x[0], rot[0] = qpos[f:f + 3], _mat(qpos[f + 3:f + 7])
    if qvel is not None:
        v[0] = qvel[ch.free_v:ch.free_v + 3]                    # the free joint's linear velocity: world frame
        w[0] = rot[0] @ qvel[ch.free_v + 3:ch.free_v + 6]       # its angular velocity: the body's own frame
    for k in range(1, n):
        b, j = ch.bodies[k], model.body_jntadr[ch.bodies[k]]
        pre = rot[k - 1] @ _mat(model.body_quat[b])
        anchor = x[k - 1] + rot[k - 1] @ model.body_pos[b] + pre @ model.jnt_pos[j]
        qa = ch.ball_q[k - 1]
        rot[k] = pre @ _mat(qpos[qa:qa + 4])
        x[k] = anchor - rot[k] @ model.jnt_pos[j]
        if qvel is not None:
            va = v[k - 1] + np.cross(w[k - 1], anchor - x[k - 1])
            da = ch.ball_v[k - 1]
            w[k] = w[k - 1] + rot[k] @ qvel[da:da + 3]          # a ball joint's angular velocity: the child's frame
            v[k] = va + np.cross(w[k], x[k] - anchor)
    com = x + np.einsum("nij,nj->ni", rot, model.body_ipos[ch.bodies])
    if qvel is not None:
        v = v + np.cross(w, com - x)
    return com, rot, v, w


def _rest(model: mujoco.MjModel, ch: _Chain) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The ring as built (unturned: the NOTE's own frame is the world's): each segment's center of mass about the
    ring's (n, 3), and s0's origin about it and its quaternion."""
    com, _, _, _ = _segments(model, ch, model.qpos0)
    m = model.body_mass[ch.bodies]
    c = (m[:, None] * com).sum(0) / m.sum()
    f = ch.free_q
    return com - c, model.qpos0[f:f + 3] - c, model.qpos0[f + 3:f + 7].copy()


def ring_rest(model: mujoco.MjModel, ring: str) -> np.ndarray:
    """(RING_SEGMENTS, 3): each segment's center of mass in the NOTE's own frame (its origin the ring's center of
    mass)."""
    return _rest(model, _chain(model, ring))[0]


def write_rigid(model: mujoco.MjModel, data: mujoco.MjData, ring: str, pos, quat, vel, ang_vel) -> None:
    """Put the ring at the rigid NOTE's pose (its center of mass at `pos`, turned by `quat`, w x y z), moving with
    its velocity field (`vel` at the center, spin `ang_vel`, world frame), its ball joints at rest."""
    ch = _chain(model, ring)
    _, p0, q0 = _rest(model, ch)
    rq = _mat(quat)
    arm = rq @ p0
    q_s0 = np.zeros(4)
    mujoco.mju_mulQuat(q_s0, np.asarray(quat, dtype=float), q0)
    f, dv = ch.free_q, ch.free_v
    data.qpos[f:f + 3] = np.asarray(pos, dtype=float) + arm
    data.qpos[f + 3:f + 7] = q_s0
    data.qvel[dv:dv + 3] = np.asarray(vel, dtype=float) + np.cross(ang_vel, arm)
    data.qvel[dv + 3:dv + 6] = _mat(q_s0).T @ np.asarray(ang_vel, dtype=float)
    for qa, da in zip(ch.ball_q, ch.ball_v):
        data.qpos[qa:qa + 4] = (1.0, 0.0, 0.0, 0.0)
        data.qvel[da:da + 3] = 0.0


def read_rigid(model: mujoco.MjModel, data: mujoco.MjData, ring: str) -> tuple[np.ndarray, np.ndarray, np.ndarray,
                                                                                np.ndarray]:
    """The rigid NOTE that best stands for the ring now: its center of mass, the Kabsch rotation of the segments'
    centers against ring_rest (w x y z), its momentum over its mass, and its spin from its angular momentum about
    the center (the segments' summed inertia there)."""
    ch = _chain(model, ring)
    rest = _rest(model, ch)[0]
    com, rot, v, w = _segments(model, ch, data.qpos, data.qvel)
    m = model.body_mass[ch.bodies]
    mass = float(m.sum())
    c = (m[:, None] * com).sum(0) / mass
    vc = (m[:, None] * v).sum(0) / mass
    r = com - c
    # Kabsch: the rotation taking rest onto r, least squares
    u, _, vt = np.linalg.svd(rest.T @ r)
    s = np.diag([1.0, 1.0, float(np.sign(np.linalg.det(vt.T @ u.T)))])
    best = vt.T @ s @ u.T
    ang = (m[:, None] * np.cross(r, v - vc)).sum(0)
    inertia = np.zeros((3, 3))
    for k, b in enumerate(ch.bodies):
        world = rot[k] @ _mat(model.body_iquat[b])
        ib = world @ np.diag(model.body_inertia[b]) @ world.T
        ang += ib @ w[k]
        inertia += ib + m[k] * (float(r[k] @ r[k]) * np.eye(3) - np.outer(r[k], r[k]))
    return c, _quat_of(best), vc, np.linalg.solve(inertia, ang)


class ParkedRings:
    """The pool's rings held where they were built (PARK_Z, under the field): a parked ring touches nothing, so it
    falls; after each step each awake one is put back at rest, as a parked ball slot is (Balls.after_step). One asleep
    was put back the step it fell asleep and hasn't moved since."""

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, rings: list[str]):
        self.data = data
        chains = [_chain(model, r) for r in rings]
        self.trees = np.array([model.body_treeid[c.bodies[0]] for c in chains], dtype=int)
        self._q = [c.q for c in chains]
        self._v = [c.v for c in chains]
        self._q0 = [model.qpos0[c.q].copy() for c in chains]

    def hold(self) -> None:
        d = self.data
        for k in np.flatnonzero(d.tree_asleep[self.trees] < 0):
            d.qpos[self._q[k]] = self._q0[k]
            d.qvel[self._v[k]] = 0.0
