"""The soft-note spike's squeeze and drop scenes for one soft ring alone (testrange/soft.py), on the world's options
(implicitfast, an elliptic cone, impratio 10, Newton, 1 ms). The scenes' numbers are launcher/soft_scenes.py's."""

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


def squeeze(kind: PieceKind) -> float:
    """The ring flat, no gravity, between two mocap rollers on a line across its middle, closing from
    SQUEEZE_START_GAP to SQUEEZE_GAP and held: the top roller's normal force (N), averaged over the last
    SQUEEZE_READ_S."""
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
        gap = sc.SQUEEZE_START_GAP + (sc.SQUEEZE_GAP - sc.SQUEEZE_START_GAP) * min(t / sc.SQUEEZE_CLOSE_S, 1.0)
        d.mocap_pos[0] = [0.0, 0.0, gap / 2.0 + r]
        d.mocap_pos[1] = [0.0, 0.0, -(gap / 2.0 + r)]
        mujoco.mj_step(m, d)
        if t > total - sc.SQUEEZE_READ_S:
            ids = contact_ids(m, d)
            f = 0.0
            for i in np.flatnonzero((ids == top).any(axis=1)):
                mujoco.mj_contactForce(m, d, int(i), f6)
                f += f6[0]
            forces.append(f)
    return float(np.mean(forces))


def drop(kind: PieceKind) -> dict:
    """The ring flat, its bottom DROP_HEIGHT above a carpet plane: its restitution e = sqrt(rebound apex above rest
    / DROP_HEIGHT), whether it stayed finite, the deepest contact (m), the time from its first touch until it last
    moved faster than STILL (s), and its center's height at the end (m)."""
    spec = _spec(gravity=True)
    spec.worldbody.add_geom(name="carpet", type=mujoco.mjtGeom.mjGEOM_PLANE, size=[20.0, 20.0, 0.1],
                            friction=list(sc.CARPET_FRICTION), **BITS)
    dims = soft._dims(kind)
    soft.add_ring(spec, "n", kind, (0.0, 0.0, sc.DROP_HEIGHT + dims.r), BITS)
    m = spec.compile()
    d = mujoco.MjData(m)
    apex, phase, touched, deepest, finite = -1.0, "fall", None, 0.0, True
    moving = 0.0        # the last time it moved faster than STILL
    for _ in range(int(round(sc.DROP_TIME_S / TIMESTEP))):
        mujoco.mj_step(m, d)
        if not (np.isfinite(d.qpos).all() and np.isfinite(d.qvel).all()):
            finite = False
            break
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
            "rest_z": float(rest)}
