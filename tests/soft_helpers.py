"""Scenes for one soft ring alone (testrange/soft.py), on the world's options (implicitfast, an elliptic cone, impratio
10, Newton, 1 ms):
- the soft-note spike's squeeze and drop, whose numbers are launcher/soft_scenes.py's;
- K6's acceptance scenes (K6-R11, from the K6 spike's k6/scenes.py): `slam` (sliding flat into a wall), `press` (a
  roller pressing the NOTE onto a thin plate), `taco` (the ring folded and pressed face to face) and `slab` (a slab
  2 mm over the carpet driving at it).
Segment speeds come from the ring's own kinematics (Ring.speeds, K6-R8), never MuJoCo's cvel."""

from __future__ import annotations

import math

import mujoco
import numpy as np

from scrimmage.flexcontact import contact_ids
from scrimmage.launcher import soft_scenes as sc
from scrimmage.testrange import soft
from scrimmage.testrange.pieces import PieceKind

TIMESTEP = 0.001        # the world's
ROLL_Q = [math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0]     # a cylinder's axis turned onto -y
BITS = {"contype": 1, "conaffinity": 1}
STILL = 0.02            # m/s: still (its center's speed plus its spin times the ring's radius), the spike's
IN = 0.0254
PLATE_FRICTION = [0.4, 0.005, 0.0001]  # source: k6 scenes: polycarbonate on foam (docs/parts.md)
SETTLE_STEPS = 300      # source: k6 scenes: the ring settles on the carpet before a scene starts

# slam: the ring slides flat into a fixed wall
WALL_X = 0.30           # source: k6 scenes.slam: the wall's near face (m), clear of the ring (outer radius 0.18 m)
WALL_HALF = (0.3, 0.25)  # source: k6 scenes.slam: the wall's half width and half height (m)
WALL_Z = 0.253          # source: k6 scenes.slam: the wall's center height (m), just off the carpet
SLAM_STEPS = 600        # source: k6 scenes.slam: 0.6 s, long enough for the slowest hit to land and rebound

# press: a roller presses the NOTE onto a plate held in the air
PLATE_TOP = 0.2         # source: k6 scenes.press: the plate's top (m), clear of everything
PLATE_HALF = 1.5        # source: k6 scenes.press: the plate's half size (m)
PRESS_ROLLER_R = 1.0 * IN  # source: k6 scenes.press: a 2 in roller
PRESS_ROLLER_HALF = 0.25   # source: k6 scenes.press: the roller's half length (m), across the 14 in NOTE
PRESS_ROLLER_FRICTION = [1.0, 0.005, 0.0001]  # source: k6 scenes.press: a grippy tread
PRESS_ROLLER_MASS = 0.25   # source: k6 scenes.press: kg
PRESS_CLEAR = 0.002     # source: k6 scenes.press: the roller starts 2 mm over the NOTE
PRESS_SETTLE_STEPS = 200  # source: k6 scenes.press
PRESS_RAMP_S = 0.3      # source: k6 scenes.press: the force ramps up over 0.3 s
PRESS_SLIDE_S = (0.3, 0.4)  # source: k6 scenes.press: the roller is dragged along x from 0.3 s to 0.4 s
PRESS_S = 0.6           # source: k6 scenes.press

# taco: the ring flat on the carpet, folded up about a diameter and pressed between two plates
TACO_PLATE_HALF = (0.25, 0.01, 0.3)  # source: k6 scenes.taco: each plate's half sizes (m), its normal along y
TACO_PLATE_Z = 0.3      # source: k6 scenes.taco
TACO_CLEAR = 0.02       # source: k6 scenes.taco: the plates start 2 cm wider than the ring
TACO_LIFTED = 4         # source: k6 scenes.taco: the segments farthest from the fold line are lifted
TACO_LIFT_BELOW = 0.14  # source: k6 scenes.taco: a lifted segment is pulled up only while under 14 cm
TACO_CLOSE_S, TACO_HOLD_S = 1.0, 0.5  # source: k6 scenes.taco

# slab: a slab 2 mm over the carpet drives at the NOTE, which a roller pulls toward it
SLAB_TOP = 2.04 * IN    # source: k6 scenes.slab: just over the 2 in NOTE
SLAB_HALF = (0.25, 0.3)  # source: k6 scenes.slab: half length and half width (m)
SLAB_FRONT = 0.40       # source: k6 scenes.slab: its front face at the start (m)
SLAB_S = 1.0            # source: k6 scenes.slab
SLAB_ON_CARPET = 0.06   # source: k6 scenes.slab: the pull acts while the ring's center is under 6 cm up
SLAB_SURFACE = 3.4      # source: k6 scenes.slab: a roller's surface speed (m/s); it stops pulling past it


def _spec(gravity: bool) -> mujoco.MjSpec:
    spec = mujoco.MjSpec()
    o = spec.option
    o.timestep, o.integrator, o.cone = TIMESTEP, mujoco.mjtIntegrator.mjINT_IMPLICITFAST, mujoco.mjtCone.mjCONE_ELLIPTIC
    o.impratio, o.solver = 10, mujoco.mjtSolver.mjSOL_NEWTON
    o.gravity = [0.0, 0.0, -9.81] if gravity else [0.0, 0.0, 0.0]
    return spec


def state(model, data, ring: str = "n"):
    """read_rigid's center, rotation, velocity and spin, and the deepest contact now (m)."""
    pen = float(-data.contact.dist[:data.ncon].min()) if data.ncon else 0.0
    return (*soft.read_rigid(model, data, ring), max(pen, 0.0))


def _carpet(spec: mujoco.MjSpec) -> None:
    spec.worldbody.add_geom(name="carpet", type=mujoco.mjtGeom.mjGEOM_PLANE, size=[20.0, 20.0, 0.1],
                            friction=list(sc.CARPET_FRICTION), **BITS)


_BAD = (mujoco.mjtWarning.mjWARN_BADQPOS, mujoco.mjtWarning.mjWARN_BADQVEL, mujoco.mjtWarning.mjWARN_BADQACC)


def finite_now(d: mujoco.MjData) -> bool:
    """The state is finite and MuJoCo never reset it: mj_step puts a model back at qpos0 when a position, velocity
    or acceleration goes bad, and that counts as blowing up too."""
    return (bool(np.isfinite(d.qpos).all() and np.isfinite(d.qvel).all())
            and not any(d.warning[w].number for w in _BAD))


def energy(m: mujoco.MjModel, d: mujoco.MjData, ring: str = "n") -> float:
    """The ring's energy (J): each segment's kinetic energy (its center's translation and its spin, from the ring's
    own kinematics, Ring._segments) plus its weight times its center's height."""
    r = soft.ring_view(m, ring)
    com, rot, v, w = r._segments(d.qpos[r.q], d.qvel[r.v])
    wl = np.einsum("nji,nj->ni", rot @ r._iframe, w)       # spin in each segment's principal frame
    kinetic = 0.5 * float(r.m @ (v * v).sum(1) + (r._idiag * wl * wl).sum())
    return kinetic - float(r.m @ (com @ m.opt.gravity))


def squeeze(kind: PieceKind, gap: float = sc.SQUEEZE_GAP) -> float:
    """The ring flat, no gravity, between two mocap rollers on a line across its middle, closing from
    SQUEEZE_START_GAP to `gap` (SQUEEZE_GAP, a 25 % squeeze, by default) and held: the top roller's normal force (N),
    averaged over the last SQUEEZE_READ_S; NaN if it blew up."""
    spec = _spec(gravity=False)
    r = sc.ROLLER_DIAMETER / 2.0
    for name, sign in (("top", 1.0), ("bot", -1.0)):
        b = spec.worldbody.add_body(name=name, mocap=True, pos=[0.0, 0.0, sign * (sc.SQUEEZE_START_GAP / 2.0 + r)])
        b.add_geom(name=name, type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[r, sc.ROLLER_WIDTH / 2.0, 0.0], quat=ROLL_Q,
                   friction=list(sc.ROLLER_FRICTION), condim=6, **BITS)
    soft.add_ring(spec, "n", kind, (0.0, 0.0, 0.0), BITS)
    m = spec.compile()
    d = mujoco.MjData(m)
    top = m.geom("top").id
    total = sc.SQUEEZE_CLOSE_S + sc.SQUEEZE_HOLD_S
    forces, f6 = [], np.zeros(6)
    for _ in range(int(round(total / TIMESTEP))):
        t = d.time
        now = sc.SQUEEZE_START_GAP + (gap - sc.SQUEEZE_START_GAP) * min(t / sc.SQUEEZE_CLOSE_S, 1.0)
        d.mocap_pos[0] = [0.0, 0.0, now / 2.0 + r]
        d.mocap_pos[1] = [0.0, 0.0, -(now / 2.0 + r)]
        mujoco.mj_step(m, d)
        if t > total - sc.SQUEEZE_READ_S:
            ids = contact_ids(m, d)
            f = 0.0
            for i in np.flatnonzero((ids == top).any(axis=1)):
                mujoco.mj_contactForce(m, d, int(i), f6)
                f += f6[0]
            forces.append(f)
    return float(np.mean(forces)) if finite_now(d) else math.nan


def drop(kind: PieceKind) -> dict:
    """The ring flat, its bottom DROP_HEIGHT above a carpet plane: its restitution e = sqrt(rebound apex above rest
    / DROP_HEIGHT), whether it stayed finite, the deepest contact (m), the time from its first touch until it last
    moved faster than STILL (s), its center's height at the end (m), and `energy_gain`, its highest energy over its
    start (slam's measure)."""
    spec = _spec(gravity=True)
    _carpet(spec)
    dims = soft._dims(kind)
    soft.add_ring(spec, "n", kind, (0.0, 0.0, sc.DROP_HEIGHT + dims.r), BITS)
    m = spec.compile()
    d = mujoco.MjData(m)
    apex, phase, touched, deepest, finite = -1.0, "fall", None, 0.0, True
    moving = 0.0        # the last time it moved faster than STILL
    e0 = top = energy(m, d)
    for _ in range(int(round(sc.DROP_TIME_S / TIMESTEP))):
        mujoco.mj_step(m, d)
        if not finite_now(d):
            finite = False
            break
        top = max(top, energy(m, d))
        c, _, v, w, pen = state(m, d)
        deepest = max(deepest, pen)
        if touched is None and d.ncon > 0:
            touched = d.time
        if float(np.linalg.norm(v)) + float(np.linalg.norm(w)) * dims.big_r >= STILL:
            moving = d.time
        if touched is not None:
            if phase == "fall" and v[2] > 0:
                phase = "rise"
            if phase == "rise":
                apex = max(apex, c[2])
                if v[2] < 0:
                    phase = "done"
    c, _, v, w, _ = state(m, d)
    rest = c[2]
    e = math.sqrt(max(apex - rest, 0.0) / sc.DROP_HEIGHT) if apex > 0 else 0.0
    return {"e": e, "finite": finite, "deepest": deepest, "settle_s": moving - (touched or 0.0),
            "rest_z": float(rest), "energy_gain": top / e0 if finite else math.inf}


def _settle(m: mujoco.MjModel, d: mujoco.MjData, steps: int = SETTLE_STEPS) -> None:
    for _ in range(steps):
        mujoco.mj_step(m, d)


def _deepest_on(m: mujoco.MjModel, d: mujoco.MjData, geom: int) -> float:
    """The deepest contact (m) on geom `geom` now, 0 for none."""
    ids = contact_ids(m, d)
    hit = np.flatnonzero((ids == geom).any(axis=1))
    return max(0.0, float(-d.contact.dist[hit].min())) if hit.size else 0.0


def _pair(spec: mujoco.MjSpec, body: str) -> None:
    """The ring meets the priority part through explicit pairs with its own squash (K6-R2, Task 5)."""
    soft.pair_rings(spec, ["n"], [spec.body(body)])


def slam(kind: PieceKind, v: float, thickness: float, priority: int = 0, pairs: bool = False) -> dict:
    """The ring settled flat on the carpet, then sent sliding at `v` (m/s) into a fixed wall `thickness` thick (its
    near face at WALL_X), for SLAM_STEPS. With `pairs` the wall is on its own body, `wallb` (excluding the world body
    would drop the carpet too), and the ring meets it through soft.pair_rings. Returns `deepest` (m, the deepest
    contact on the wall), `through` (a segment's center past the wall's far face at the end), `vmax` (m/s, the
    fastest segment), `finite` and `energy_gain` (the ring's highest energy over its start)."""
    spec = _spec(gravity=True)
    _carpet(spec)
    size = [thickness / 2.0, *WALL_HALF]
    pos = [WALL_X + thickness / 2.0, 0.0, WALL_Z]
    if pairs:
        spec.worldbody.add_body(name="wallb", pos=pos).add_geom(
            name="wall", type=mujoco.mjtGeom.mjGEOM_BOX, size=size, friction=PLATE_FRICTION, priority=priority,
            **BITS)
    else:
        spec.worldbody.add_geom(name="wall", type=mujoco.mjtGeom.mjGEOM_BOX, size=size, pos=pos,
                                friction=PLATE_FRICTION, priority=priority, **BITS)
    soft.add_ring(spec, "n", kind, (0.0, 0.0, soft._dims(kind).r), BITS)
    if pairs:
        _pair(spec, "wallb")
    m = spec.compile()
    d = mujoco.MjData(m)
    _settle(m, d)
    ring = soft.ring_view(m, "n")
    c, q, _, _ = ring.read(d)
    ring.write(d, c, q, [v, 0.0, 0.0], [0.0, 0.0, 0.0])
    mujoco.mj_forward(m, d)
    wall = m.geom("wall").id
    e0 = top = energy(m, d)
    deepest, vmax, finite = 0.0, 0.0, True
    for _ in range(SLAM_STEPS):
        mujoco.mj_step(m, d)
        if not finite_now(d):
            finite = False
            break
        top = max(top, energy(m, d))
        deepest = max(deepest, _deepest_on(m, d, wall))
        vmax = max(vmax, float(ring.speeds(d).max()))
    through = finite and bool(d.xpos[ring.bodies, 0].max() > WALL_X + thickness)
    return {"deepest": deepest, "through": through, "vmax": vmax, "finite": finite,
            "energy_gain": top / e0 if finite else math.inf}


def press(kind: PieceKind, force: float, thickness: float, slide: float = 0.0) -> dict:
    """The ring flat on a horizontal plate `thickness` thick (fixed in the air, its top at PLATE_TOP), a 2 in roller
    across its middle pressed down onto it with `force` N (ramped up over PRESS_RAMP_S) and dragged along x at
    `slide` m/s over PRESS_SLIDE_S: a roller load on a thin floor. Returns `squash` (m, the lowest capsule's bottom
    below the plate's top), `center_below_top` (a segment's center went below the plate's top), `vmax` (m/s) and
    `finite`."""
    spec = _spec(gravity=True)
    spec.worldbody.add_body(name="plateb", pos=[0.0, 0.0, PLATE_TOP - thickness / 2.0]).add_geom(
        name="plate", type=mujoco.mjtGeom.mjGEOM_BOX, size=[PLATE_HALF, PLATE_HALF, thickness / 2.0],
        friction=PLATE_FRICTION, **BITS)
    r = soft._dims(kind).r
    soft.add_ring(spec, "n", kind, (0.0, 0.0, PLATE_TOP + r), BITS)
    rb = spec.worldbody.add_body(name="rollerb", pos=[0.0, 0.0, PLATE_TOP + 2.0 * r + PRESS_ROLLER_R + PRESS_CLEAR])
    rb.add_joint(name="rz", type=mujoco.mjtJoint.mjJNT_SLIDE, axis=[0.0, 0.0, 1.0])
    rb.add_joint(name="rx", type=mujoco.mjtJoint.mjJNT_SLIDE, axis=[1.0, 0.0, 0.0])
    rb.add_geom(name="roller", type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[PRESS_ROLLER_R, PRESS_ROLLER_HALF, 0.0],
                quat=ROLL_Q, friction=PRESS_ROLLER_FRICTION, mass=PRESS_ROLLER_MASS, **BITS)
    m = spec.compile()
    d = mujoco.MjData(m)
    _settle(m, d, PRESS_SETTLE_STEPS)
    ring = soft.ring_view(m, "n")
    roller, vx = m.body("rollerb").id, int(m.joint("rx").dofadr[0])
    squash, vmax, finite, below = 0.0, 0.0, True, False
    for _ in range(int(round(PRESS_S / TIMESTEP))):
        d.xfrc_applied[roller, 2] = -force * min(d.time / PRESS_RAMP_S, 1.0)
        d.qvel[vx] = slide if PRESS_SLIDE_S[0] <= d.time < PRESS_SLIDE_S[1] else 0.0
        mujoco.mj_step(m, d)
        if not finite_now(d):
            finite = False
            break
        z = d.xpos[ring.bodies, 2]
        squash = max(squash, float((PLATE_TOP - (z - r)).max()))
        below = below or bool((z < PLATE_TOP).any())
        vmax = max(vmax, float(ring.speeds(d).max()))
    return {"squash": squash, "center_below_top": below, "vmax": vmax, "finite": finite}


def taco(kind: PieceKind, gap: float, lift: float) -> dict:
    """The ring flat on the carpet, folded up about its x diameter into a taco: the TACO_LIFTED segments farthest
    from the fold line are pulled up with `lift` N each (while under TACO_LIFT_BELOW) as two vertical plates (normals
    along y) close from just outside the ring to `gap` apart over TACO_CLOSE_S and hold for TACO_HOLD_S, pressing
    the halves face to face. Returns `self_overlap` (m, the deepest contact of the ring with itself), `vmax` (m/s)
    and `finite`."""
    spec = _spec(gravity=True)
    _carpet(spec)
    dims = soft._dims(kind)
    start = 2.0 * (dims.big_r + dims.r) + TACO_CLEAR
    half = TACO_PLATE_HALF[1]
    for name, sign in (("a", 1.0), ("b", -1.0)):
        spec.worldbody.add_body(name=name, mocap=True, pos=[0.0, sign * (start / 2.0 + half), TACO_PLATE_Z]).add_geom(
            name=name, type=mujoco.mjtGeom.mjGEOM_BOX, size=list(TACO_PLATE_HALF), friction=PLATE_FRICTION, **BITS)
    soft.add_ring(spec, "n", kind, (0.0, 0.0, dims.r), BITS)
    m = spec.compile()
    d = mujoco.MjData(m)
    ring = soft.ring_view(m, "n")
    mujoco.mj_forward(m, d)
    far = ring.bodies[np.argsort(-np.abs(d.xpos[ring.bodies, 1]))[:TACO_LIFTED]]
    own = ring.geoms
    overlap, vmax, finite = 0.0, 0.0, True
    for _ in range(int(round((TACO_CLOSE_S + TACO_HOLD_S) / TIMESTEP))):
        now = start + (gap - start) * min(d.time / TACO_CLOSE_S, 1.0)
        d.mocap_pos[0] = [0.0, now / 2.0 + half, TACO_PLATE_Z]
        d.mocap_pos[1] = [0.0, -(now / 2.0 + half), TACO_PLATE_Z]
        d.xfrc_applied[far, 2] = np.where(d.xpos[far, 2] < TACO_LIFT_BELOW, lift, 0.0)
        mujoco.mj_step(m, d)
        if not finite_now(d):
            finite = False
            break
        mine = np.isin(d.contact.geom, own).all(axis=1)
        if mine.any():
            overlap = max(overlap, -float(d.contact.dist[mine].min()))
        vmax = max(vmax, float(ring.speeds(d).max()))
    return {"self_overlap": overlap, "vmax": vmax, "finite": finite}


def slab(kind: PieceKind, pull: float, priority: int = 0, pairs: bool = False, v: float = 2.0,
         gap: float = 0.002) -> dict:
    """A slab (its bottom `gap` over the carpet, its top SLAB_TOP up) on a mocap body, `slabb`, moving in -x at `v`
    (m/s) toward the ring for SLAB_S. The ring is pulled toward it with `pull` N in all (a roller's drag) while its
    center is on the carpet (under SLAB_ON_CARPET) and it moves less than SLAB_SURFACE faster than the slab: an
    intake pulling the NOTE in while the robot drives at it. With `pairs` the ring meets the slab through
    soft.pair_rings. Returns `under_max` and `under_end` (the segments beneath the slab, their centers behind its
    front face by a tube radius and under its bottom plus one, the most at any time and at the end), `vmax` (m/s)
    and `finite`."""
    spec = _spec(gravity=True)
    _carpet(spec)
    half_h = (SLAB_TOP - gap) / 2.0
    spec.worldbody.add_body(name="slabb", mocap=True, pos=[SLAB_FRONT + SLAB_HALF[0], 0.0, gap + half_h]).add_geom(
        name="slab", type=mujoco.mjtGeom.mjGEOM_BOX, size=[*SLAB_HALF, half_h], friction=PLATE_FRICTION,
        priority=priority, **BITS)
    r = soft._dims(kind).r
    soft.add_ring(spec, "n", kind, (0.0, 0.0, r), BITS)
    if pairs:
        _pair(spec, "slabb")
    m = spec.compile()
    d = mujoco.MjData(m)
    _settle(m, d)
    ring = soft.ring_view(m, "n")
    under = under_max = 0
    vmax, finite = 0.0, True
    for _ in range(int(round(SLAB_S / TIMESTEP))):
        d.mocap_pos[0][0] -= v * TIMESTEP
        c, _, vc, _ = ring.read(d, spin=False)
        on = c[2] < SLAB_ON_CARPET and vc[0] + v < SLAB_SURFACE
        d.xfrc_applied[ring.bodies, 0] = pull / len(ring.bodies) if on else 0.0
        mujoco.mj_step(m, d)
        if not finite_now(d):
            finite = False
            break
        vmax = max(vmax, float(ring.speeds(d).max()))
        front = float(d.mocap_pos[0][0]) - SLAB_HALF[0]
        x, z = d.xpos[ring.bodies, 0], d.xpos[ring.bodies, 2]
        under = int(((x > front + r) & (x < front + 2.0 * SLAB_HALF[0]) & (z < gap + r)).sum())
        under_max = max(under_max, under)
    return {"under_max": under_max, "under_end": under, "vmax": vmax, "finite": finite}
