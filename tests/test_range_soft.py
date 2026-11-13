"""The world's soft NOTE (spec D8; testrange/soft.py): a ring of 16 capsules on ball joints, its numbers scaled from a
kind's `stiffness` and `restitution` (R10), a range's parked pool of rings under the floor, and the swap between a
rigid NOTE and its ring near the robot (R11-R13), drawn as one NOTE."""

from __future__ import annotations

import math
import os
import re
import sys
import time
import tomllib
from dataclasses import replace
from pathlib import Path

import mujoco
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import soft_helpers  # noqa: E402
from range_fixtures import shooter_cfg  # noqa: E402
from scrimmage.blocks.conditions import Watch  # noqa: E402
from scrimmage.blocks.program import Cond, Program, load  # noqa: E402
from scrimmage.blocks.run import TestRun, world_for  # noqa: E402
from scrimmage.blocks.run_notes import run_notes  # noqa: E402
from scrimmage.flexcontact import contact_ids  # noqa: E402
from scrimmage.movers import mover_poses, movers  # noqa: E402
from scrimmage.protocol import HalState  # noqa: E402
from scrimmage.testrange import pieces, soft  # noqa: E402
from scrimmage.testrange.build import BALL_BITS, LAUNCHED_BITS, PARK_Z, PARKED_BITS  # noqa: E402
from scrimmage.testrange.pieces import (SOFT_MIN_HOLE, SOFT_RESTITUTION_RANGE, SOFT_STIFFNESS_RANGE,  # noqa: E402
                                        kind_preset, parse_kind)
from scrimmage.testrange.spec import RangeError  # noqa: E402
from scrimmage.world import World  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "blocks" / "speaker"
ROBOT = EXAMPLE / "note-shooter-standin.toml"
NOTE = kind_preset("2024-note")
P = Path("range.toml")
RING = {"shape": "ring", "diameter_in": 14.0, "inner_diameter_in": 10.0, "length_in": 2.0, "mass": 0.235,
        "friction": 0.6, "soft": True, "restitution": 0.3}

# the spike's fitted ring's own results (docs/superpowers/specs/2026-11-11-soft-note-spike.md, params.json): the
# squeeze each fit was fitted to, and the drop's restitution it gave
FITTED = {2000.0: (12.70, 0.295), 6000.0: (38.10, 0.306)}


def _at(stiffness: float, restitution: float = 0.3):
    return replace(NOTE, stiffness=stiffness, restitution=restitution)


def _world_2024(**rng) -> World:
    cfg = tomllib.loads(ROBOT.read_text(encoding="utf-8"))
    cfg["range"] = {**cfg["range"], **rng}
    return World(cfg, EXAMPLE)


def _idle(w: World, seconds: float) -> float:
    hal = HalState()
    t0 = time.perf_counter()
    for _ in range(int(round(seconds / w.model.opt.timestep))):
        w.step(hal)
    return time.perf_counter() - t0


def _soft_off(monkeypatch) -> None:
    """The 2024-note preset with its `soft` left out: rigid, as before K5."""
    preset = {k: v for k, v in pieces.PRESETS["piece"]["2024-note"].items() if k != "soft"}
    monkeypatch.setitem(pieces.PRESETS["piece"], "2024-note", preset)


# ------------------------------------------------------------------------------------------------ the rule

def test_the_rule_scales_from_the_fit():
    """R10: the contact and joint stiffness in proportion to `stiffness`, the contact damping damping_scale(stiffness)
    x the closed form for `restitution`."""
    assert soft.damping_ratio_for(0.3) == pytest.approx(0.358, abs=0.001)
    base, triple = soft.ring_params(_at(2000.0)), soft.ring_params(_at(6000.0))
    assert base.contact_k == pytest.approx(11211.4)
    assert triple.contact_k == pytest.approx(3 * 11211.4)
    assert base.joint_k == pytest.approx(0.10276) and triple.joint_k == pytest.approx(3 * 0.10276)
    for p, s in ((base, 2000.0), (triple, 6000.0)):
        assert p.contact_b == pytest.approx(soft.damping_scale(s) * 2 * soft.damping_ratio_for(0.3)
                                            * math.sqrt(p.contact_k))
    # the soft foam's damping, within 2 %: source: K6-R6's refit raised the 2000 N/m damping scale by 2.2 % (0.8536 ->
    # 0.8722), so 66.10; K5's value was 64.45, the spike's own fit. The firm one (the spike's 113) is this ring's own
    # fit (its joint damping is R10's, not the spike's), within 10 % of the spike's
    assert base.contact_b == pytest.approx(66.10, rel=0.02) and triple.contact_b == pytest.approx(113.0, rel=0.10)
    # the contact stiffness is per unit mass in MuJoCo, so it is scaled by FIT_MASS / mass; the joints aren't
    heavy = soft.ring_params(replace(_at(2000.0), mass=2 * NOTE.mass))
    assert soft.FIT_MASS == 0.235 and heavy.contact_k == pytest.approx(11211.4 / 2)
    assert heavy.joint_k == pytest.approx(base.joint_k)
    assert heavy.contact_b == pytest.approx(soft.damping_scale(2000.0) * 2 * soft.damping_ratio_for(0.3)
                                            * math.sqrt(heavy.contact_k))


def test_a_heavier_note_squeezes_as_its_stiffness_says():
    """Task 5 review, finding 2: a NOTE of twice the mass still pushes back 12.7 N in the squeeze at 2000 N/m, and
    bounces near its restitution."""
    heavy = replace(_at(2000.0), mass=2 * NOTE.mass)
    assert soft_helpers.squeeze(heavy) == pytest.approx(12.70, rel=0.10)
    assert soft_helpers.drop(heavy)["e"] == pytest.approx(0.3, rel=0.10)


@pytest.mark.parametrize("stiffness", sorted(FITTED))
def test_the_rule_reproduces_both_fitted_foams(stiffness):
    """The squeeze (two 4 in rollers closing from 2.3 in to 1.5 in) and the 1 m drop give the spike's fitted ring's
    own results within 10 %, at its soft and its firm foam."""
    force, e = FITTED[stiffness]
    kind = _at(stiffness)
    assert soft_helpers.squeeze(kind) == pytest.approx(force, rel=0.10)
    assert soft_helpers.drop(kind)["e"] == pytest.approx(e, rel=0.10)


def test_soft_limits_are_checked():
    """R14 (Review Focus 4): a soft kind's stiffness lies in SOFT_STIFFNESS_RANGE and its restitution in
    a bounce outside SOFT_RESTITUTION_RANGE is warned about, only a ring is soft, and its hole is wide enough that its segments don't overlap."""
    assert SOFT_STIFFNESS_RANGE == (1000.0, 20000.0) and SOFT_RESTITUTION_RANGE == (0.27, 0.45)
    for bad in (50000, 500):
        with pytest.raises(RangeError) as e:
            parse_kind(P, "s", {**RING, "stiffness": bad}, ROOT)
        assert "stiffness" in str(e.value) and "1000" in str(e.value) and "20000" in str(e.value)
    # a measured bounce outside the accurate range is accepted, with a warning; inside it, none
    for outside in (0.25, 0.5):
        kind = parse_kind(P, "s", {**RING, "stiffness": 2000, "restitution": outside}, ROOT)
        assert kind.soft and kind.restitution == outside
        assert [str(w) for w in kind.warnings] == [
            f"range.toml: [piece.s]: its bounce of {outside:g} is outside 0.27 to 0.45, where the soft NOTE's drop is "
            "within 10%; the simulated bounce may be off by more"]
    assert parse_kind(P, "s", {**RING, "stiffness": 2000, "restitution": 0.3}, ROOT).warnings == ()
    assert kind_preset("2024-note").warnings == ()
    with pytest.raises(RangeError, match="ring"):
        parse_kind(P, "b", {"shape": "box", "size_in": [4, 4, 4], "mass": 0.5, "friction": 0.6, "soft": True,
                            "stiffness": 2000, "restitution": 0.3}, ROOT)
    # no hole: the existing message for a missing key
    solid = {k: v for k, v in RING.items() if k != "inner_diameter_in"}
    with pytest.raises(RangeError, match=r"needs inner_diameter: give inner_diameter in \[piece\.s\]"):
        parse_kind(P, "s", {**solid, "stiffness": 2000}, ROOT)
    # the limit is the geometry's: segments j and j + 2 clear only when 2 R sin(pi/16) > 2 r
    sn = math.sin(math.pi / 16)
    assert (1 - sn) / (1 + sn) == pytest.approx(0.674, abs=0.001) and (1 - sn) / (1 + sn) < SOFT_MIN_HOLE
    with pytest.raises(RangeError, match=r"inner_diameter must be more than 0.68 of its diameter, so its 16 segments "
                                         r"don't overlap; this one's is 0.643"):
        parse_kind(P, "s", {**RING, "stiffness": 2000, "inner_diameter_in": 9.0}, ROOT)
    assert parse_kind(P, "s", {**RING, "stiffness": 2000, "inner_diameter_in": 9.6}, ROOT).soft     # 0.686


def test_a_presets_soft_says_how_to_stay_rigid():
    """Task 5 review, finding 5: the preset sets soft = true, so an out-of-range override says so and how to keep the
    piece rigid; with soft = false the override is a rigid NOTE's, as before."""
    with pytest.raises(RangeError, match="its preset sets soft = true, and soft = false keeps it rigid"):
        parse_kind(P, "n", {"preset": "2024-note", "stiffness": 30000}, ROOT)
    rigid = parse_kind(P, "n", {"preset": "2024-note", "stiffness": 30000, "soft": False}, ROOT)
    assert not rigid.soft and rigid.stiffness == 30000
    with pytest.raises(RangeError) as e:        # the table's own soft = true: no hint
        parse_kind(P, "s", {**RING, "stiffness": 30000}, ROOT)
    assert "soft = false" not in str(e.value)


SOFT_CORNERS = [(s, e) for s in SOFT_STIFFNESS_RANGE for e in SOFT_RESTITUTION_RANGE] + [
    (math.sqrt(SOFT_STIFFNESS_RANGE[0] * SOFT_STIFFNESS_RANGE[1]), sum(SOFT_RESTITUTION_RANGE) / 2),
    (12000.0, 0.45)]  # choice: Task 4's grid: the weakest stretch between rows, -9.0 to -9.6 % from 10000 to 14000


@pytest.mark.parametrize(("stiffness", "e"), SOFT_CORNERS)
def test_the_rule_bounces_within_10_percent_across_the_range(stiffness, e):
    """At each corner of SOFT_STIFFNESS_RANGE x SOFT_RESTITUTION_RANGE, at its middle (log stiffness) and at 0.45
    and 12000 N/m (the weakest stretch between table rows), the 1 m drop's e is within 10 % of the asked one (fix round
    2: the damping scale follows the stiffness)."""
    assert soft_helpers.drop(_at(stiffness, e))["e"] == pytest.approx(e, rel=0.10)


def test_the_damping_scale_table():
    """DAMPING_SCALES: linear in log(stiffness) between its rows, held at its ends."""
    rows = soft.DAMPING_SCALES
    assert [s for s, _ in rows] == [1000.0, 2000.0, 6000.0, 20000.0]
    assert soft.damping_scale(500.0) == rows[0][1] and soft.damping_scale(50000.0) == rows[-1][1]
    mid = math.sqrt(2000.0 * 6000.0)
    assert soft.damping_scale(mid) == pytest.approx((rows[1][1] + rows[2][1]) / 2)


def test_the_shipped_damping_scales_are_the_fit():
    """tests/fit_soft_damping.py refits each row (about 11 s): within 0.5 % of the shipped table, so a change to
    the ring or its contact that moves the fit fails here until the table is refit."""
    import fit_soft_damping
    for stiffness, shipped in soft.DAMPING_SCALES:
        assert fit_soft_damping.fit(stiffness) == pytest.approx(shipped, rel=0.005), stiffness


@pytest.mark.parametrize("stiffness", SOFT_STIFFNESS_RANGE)
def test_the_ring_is_stable_at_both_ends_of_its_range(stiffness):
    """R14 (Review Focus 4), tightened by K6-R11: at each end of SOFT_STIFFNESS_RANGE the drop stays finite, its
    deepest contact is under 21 mm (K6-R1's ramp; under the 25 mm tube), its energy never rises above its start
    (K6-P4), and it is still within 1 s of its first touch."""
    r = soft_helpers.drop(_at(stiffness))
    assert r["finite"]
    # choice: measured 19.3 mm at 1000 N/m (18.8 after K6-R6's refit); was 40 mm, more than the tube
    assert r["deepest"] < 0.021, r
    # choice: K6-P4: a drop never adds energy; 0.1 % is the integrator's rounding
    assert r["energy_gain"] <= 1.001, r
    assert r["settle_s"] < 1.0, r


def test_a_rigid_pose_round_trips():
    """write_rigid then read_rigid at a turned pose, with velocity and spin, gives them back within 1e-9, and the
    momentum written is the mass times the velocity."""
    spec = mujoco.MjSpec()
    soft.add_ring(spec, "n", NOTE, (0.0, 0.0, 0.0), PARKED_BITS)
    m = spec.compile()
    d = mujoco.MjData(m)
    quat = np.array([0.8, 0.3, -0.4, 0.2]) / np.linalg.norm([0.8, 0.3, -0.4, 0.2])
    pos, vel, spin = np.array([0.3, -0.2, 1.1]), np.array([1.0, -2.0, 0.5]), np.array([3.0, 1.0, -7.0])
    soft.write_rigid(m, d, "n", pos, quat, vel, spin)
    c, q, v, w = soft.read_rigid(m, d, "n")
    assert np.abs(c - pos).max() < 1e-9
    assert min(np.abs(q - quat).max(), np.abs(q + quat).max()) < 1e-9
    assert np.abs(v - vel).max() < 1e-9 and np.abs(w - spin).max() < 1e-9
    # MuJoCo's own reading of the momentum: each segment's mass times its center's velocity
    mujoco.mj_forward(m, d)
    momentum, out = np.zeros(3), np.zeros(6)
    bodies = [m.body(f"n.s{j}").id for j in range(soft.RING_SEGMENTS)]
    for b in bodies:
        mujoco.mj_objectVelocity(m, d, mujoco.mjtObj.mjOBJ_BODY, b, out, 0)
        momentum += m.body_mass[b] * out[3:]
    assert np.abs(momentum - NOTE.mass * vel).max() < 1e-9
    assert sum(m.body_mass[b] for b in bodies) == pytest.approx(NOTE.mass)
    # ring_rest: the segments' centers about the center of mass, in the NOTE's plane (its axis z)
    rest = soft.ring_rest(m, "n")
    assert rest.shape == (soft.RING_SEGMENTS, 3)
    _, _, _, none = soft.read_rigid(m, d, "n", spin=False)
    assert none is None
    assert np.abs(rest.mean(axis=0)).max() < 1e-12 and np.abs(rest[:, 2]).max() < 1e-12


def test_the_rings_kinematics_are_mujocos():
    """Task 5 review, finding 4: on a bent, moving ring, the ring's own kinematics are MuJoCo's: each segment's center
    and velocity, and read_rigid's center, momentum and angular momentum against the subtree's."""
    spec = mujoco.MjSpec()
    soft.add_ring(spec, "n", NOTE, (0.4, -0.3, 0.8), PARKED_BITS)
    m = spec.compile()
    d = mujoco.MjData(m)
    rng = np.random.default_rng(5)
    ring = soft.ring_view(m, "n")
    q = rng.normal(size=4)
    d.qpos[3:7] = q / np.linalg.norm(q)
    for j in range(1, m.njnt):
        a = m.jnt_qposadr[j]
        q = np.r_[1.0, rng.normal(0.0, 0.15, 3)]
        d.qpos[a:a + 4] = q / np.linalg.norm(q)
    d.qvel[:] = rng.normal(size=m.nv)
    mujoco.mj_forward(m, d)
    mujoco.mj_subtreeVel(m, d)
    com, rot, v, w = ring._segments(d.qpos[ring.q], d.qvel[ring.v])
    assert np.abs(com - d.xipos[ring.bodies]).max() < 1e-12
    assert np.abs(rot.reshape(-1, 9) - d.xmat[ring.bodies]).max() < 1e-12
    out = np.zeros(6)
    for k, b in enumerate(ring.bodies):
        mujoco.mj_objectVelocity(m, d, mujoco.mjtObj.mjOBJ_BODY, int(b), out, 0)     # at the body's center of mass
        assert np.abs(out[:3] - w[k]).max() < 1e-12 and np.abs(out[3:] - v[k]).max() < 1e-12
    s0 = int(ring.bodies[0])
    c, _, vc, spin = soft.read_rigid(m, d, "n")
    assert np.abs(c - d.subtree_com[s0]).max() < 1e-12
    assert np.abs(vc - d.subtree_linvel[s0]).max() < 1e-12
    # the angular momentum about the center: the summed inertia there times the spin
    r = com - c
    frame = rot @ ring._iframe
    inertia = np.einsum("nij,nj,nkj->ik", frame, ring._idiag, frame) + sum(
        ring.m[k] * (r[k] @ r[k] * np.eye(3) - np.outer(r[k], r[k])) for k in range(len(r)))
    assert np.abs(inertia @ spin - d.subtree_angmom[s0]).max() < 1e-12


def test_the_ramp_rule():
    """K6-R1: the impedance ramps from SOFT_DMIN to dmax over the tube's radius, dmax capped so the stiffest contact's
    frequency is W_RING, and the solref scaled so that a shallow contact is K5's (-contact_k, -contact_b)."""
    for stiffness, want in ((1000.0, 0.95), (2000.0, 0.95), (6000.0, 0.763), (20000.0, 0.418)):  # source: K6-R1's table
        solref, solimp = soft.ring_contact(_at(stiffness))
        p = soft.ring_params(_at(stiffness))
        assert solimp[0] == soft.SOFT_DMIN
        assert solimp[1] == pytest.approx(want, abs=1e-3)
        assert solimp[2] == pytest.approx(0.0254)    # the 2024 NOTE's tube radius, 1 in
        assert solimp[3:] == [soft.RAMP_MID, soft.RAMP_POWER]
        assert solref[0] * (soft.SOFT_DMIN / solimp[1]) ** 2 == pytest.approx(-p.contact_k, rel=1e-12)
        assert solref[1] * (soft.SOFT_DMIN / solimp[1]) == pytest.approx(-p.contact_b, rel=1e-12)
    solref, _ = soft.ring_contact(_at(2000.0))
    p = soft.ring_params(_at(2000.0))
    # f = 0.95 / 0.2 = 4.75, f^2 = 22.5625
    assert solref == pytest.approx([-11211.4 * 22.5625, -p.contact_b * 4.75], rel=1e-6)
    # a light, stiff legal kind is already past W_RING: its dmax is clamped to SOFT_DMIN, K5's flat contact
    light = replace(NOTE, stiffness=20000.0, mass=0.05)  # choice: the review's 50 g ring at the top of the range
    p = soft.ring_params(light)
    assert soft.SOFT_DMIN * soft.W_RING / math.sqrt(p.contact_k) < soft.SOFT_DMIN
    solref, solimp = soft.ring_contact(light)
    assert solimp[1] == soft.SOFT_DMIN and solimp[1] >= solimp[0]
    assert solref == pytest.approx([-p.contact_k, -p.contact_b], rel=1e-12)


def test_the_ring_is_built_as_its_kind():
    """16 bodies, a free joint and 15 ball joints, capsules of the tube's radius, the closing connect and its
    exclude; the contact is the rule's (ring_contact: K6-R1's ramp, re-recorded per K6-R9)."""
    spec = mujoco.MjSpec()
    soft.add_ring(spec, "n", NOTE, (0.0, 0.0, 0.0), PARKED_BITS)
    m = spec.compile()
    assert m.nbody == 1 + 16 and m.njnt == 16 and m.nv == 6 + 15 * 3 and m.neq == 1 and m.nexclude == 1
    solref, solimp = soft.ring_contact(NOTE)
    r, big_r = (14.0 - 10.0) / 4 * 0.0254, (14.0 + 10.0) / 4 * 0.0254
    for j in range(16):
        g = m.geom(f"n.g{j}")
        assert g.type[0] == mujoco.mjtGeom.mjGEOM_CAPSULE and g.size[0] == pytest.approx(r)
        assert g.size[1] == pytest.approx(big_r * math.sin(math.pi / 16))
        assert list(g.solref) == pytest.approx(solref) and g.condim[0] == 3
        assert list(g.solimp) == pytest.approx(solimp)
        assert list(g.friction) == pytest.approx([NOTE.friction, 0.005, 0.0001])
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    assert np.abs(d.efc_pos[:d.ne]).max() < 1e-12      # the loop closes at rest


def test_segment_speeds_are_the_rings_own():
    """Ring.speeds is each segment's center speed from the ring's own kinematics: a ring moving as one rigid body at
    v and spinning at w has |v + w x (com_j - c)| at segment j (K6-R8)."""
    spec = mujoco.MjSpec()
    soft.add_ring(spec, "n", NOTE, (0.0, 0.0, 0.0), PARKED_BITS)
    m = spec.compile()
    d = mujoco.MjData(m)
    ring = soft.ring_view(m, "n")
    c0, q = [0.3, -0.2, 0.5], [math.cos(0.3), 0.0, math.sin(0.3), 0.0]
    soft.write_rigid(m, d, "n", c0, q, vel=[1.0, 0, 0], ang_vel=[0, 0, 10.0])
    mujoco.mj_forward(m, d)
    com, _, _, _ = ring._segments(d.qpos[ring.q], None)
    c = ring.m @ com / ring.mass
    want = np.linalg.norm(np.array([1.0, 0, 0]) + np.cross([0, 0, 10.0], com - c), axis=1)
    got = ring.speeds(d)
    assert got.shape == (16,)
    assert np.abs(got - want).max() < 1e-9


# ------------------------------------------------------------------------------------------------ the pool

def test_a_soft_kind_builds_a_parked_pool():
    """The 2024 world has soft.0 and soft.1, each 16 segments at PARK_Z with PARKED_BITS, held there."""
    w = _world_2024()
    lay = w.test_range.layout
    assert lay.soft == ["soft.0", "soft.1"] and lay.soft_kinds == ["note", "note"]
    m, d = w.model, w.data
    bodies = [m.body(f"soft.{k}.s{j}").id for k in range(2) for j in range(16)]
    assert np.allclose(d.xpos[bodies][:, 2], PARK_Z, atol=0.03)
    for b in bodies:
        g = m.body_geomadr[b]
        assert (m.geom_contype[g], m.geom_conaffinity[g]) == (PARKED_BITS["contype"], PARKED_BITS["conaffinity"])
    rows = np.concatenate([np.arange(m.nq)[soft.ring_view(m, ring).q] for ring in lay.soft])
    q0 = d.qpos[rows].copy()
    hal = HalState()
    for _ in range(300):
        w.step(hal)
    assert np.array_equal(d.qpos[rows], q0)     # written back exactly, then asleep
    mujoco.mj_forward(m, d)
    assert np.allclose(d.xpos[bodies][:, 2], PARK_Z, atol=0.03)


def test_no_soft_kind_no_pool(monkeypatch):
    """The 2026 world builds no ring, and never imports soft.py: its nbody and nq are a build's where importing it
    fails."""
    w = World(shooter_cfg(range={"floor_balls": True}), None)
    assert w.test_range.layout.soft == [] and w.test_range.parked_rings is None
    monkeypatch.setitem(sys.modules, "scrimmage.testrange.soft", None)     # importing it raises ImportError
    w2 = World(shooter_cfg(range={"floor_balls": True}), None)
    assert (w2.model.nbody, w2.model.nq) == (w.model.nbody, w.model.nq)


def test_the_parked_pool_adds_only_its_rings(monkeypatch):
    """F13, the cost through structure: the pool adds its 2 x 16 bodies and 2 x 51 dofs; idle, the rings fall asleep,
    so the world solves the same constraints as without them and has no more contacts."""
    with_pool = _world_2024()
    _soft_off(monkeypatch)
    without = _world_2024()
    assert without.test_range.layout.soft == []
    assert with_pool.model.nbody - without.model.nbody == 2 * 16
    assert with_pool.model.nv - without.model.nv == 2 * 51
    _idle(with_pool, 2.0)
    _idle(without, 2.0)
    trees = with_pool.test_range.parked_rings.trees
    assert (with_pool.data.tree_asleep[trees] >= 0).all()
    assert with_pool.data.nefc == without.data.nefc and with_pool.data.ncon == without.data.ncon
    # and every NOTE is exactly where it would be without the pool
    assert np.array_equal(with_pool.test_range.balls._qpos, without.test_range.balls._qpos)


@pytest.mark.skipif(bool(os.environ.get("CI")), reason="timing is only meaningful on a quiet machine")
def test_the_parked_pool_costs_little(monkeypatch):
    """The 2024 world idles 2 s with its pool and without it (the preset's `soft` left out): the least of 3 runs
    each, at most 1.3x (the spike: a parked ring adds about 0.02 s per simulated second). Skipped on CI: timing never
    gates CI (F13)."""
    with_pool = _world_2024()
    _soft_off(monkeypatch)
    without = _world_2024()
    best = {name: min(_idle(w, 2.0) for _ in range(3)) for name, w in (("pool", with_pool), ("none", without))}
    assert best["pool"] / best["none"] <= 1.3, best


def test_rings_arent_movable_objects_or_contact_names():
    """No parked ring reaches a student: not movable, and not a contact check's target (Task 5 review, finding 6)."""
    w = _world_2024()
    assert not [n for n in w.movable_objects() if n.startswith("soft.")]
    names = w.contact_names()
    assert not [n for n in names if n.startswith("soft.")] and "ball.0" in names



# ------------------------------------------------------------------------------------------------ the swap (D8, R11-R13)

RANGE_2024 = ROOT / "scrimmage" / "testrange" / "ranges" / "2024.toml"
WING_NOTE = (2.8956, 4.1056)        # F6: blue_wing_note's NOTE, 1.3 m ahead of the start
VOLTS = 12.5                        # the example's nominal battery: a PWM's speed is its share of it


def wing_cfg(tmp_path: Path, notes=(WING_NOTE,), preload: int = 0, timestep: float | None = None,
             sensors: list | None = None) -> dict:
    """The speaker example's robot at blue_wing_note on the 2024 range written to tmp_path with `notes` its only floor
    NOTEs, facing them; optionally at another [model] timestep, with [[sensor]]s. Without stage_blue_leg1: since Task 9
    it is FIRST's CAD leg, an upright post from the carpet 0.15 m past the wing NOTE, where the robot pushes it (the
    old estimated leg leaned from 0.15 m up, so the NOTE went under it). These tests are about the swap, not the
    STAGE."""
    text = RANGE_2024.read_text(encoding="utf-8")
    text = re.sub(r'\[\[floor_balls\]\]\nkind = "note"\nat = \[\[.*\n', "", text)
    assert "[[floor_balls]]" not in text
    text, n = re.subn(r'\[\[obstacle\]\]\nname = "stage_blue_leg1".*?\n\n', "", text, flags=re.S)
    assert n == 1
    text += '\n[[floor_balls]]\nkind = "note"\nat = [' + ", ".join(f"[{x}, {y}]" for x, y in notes) + "]\n"
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "range.toml").write_text(text, encoding="utf-8")
    cfg = tomllib.loads(ROBOT.read_text(encoding="utf-8"))
    cfg["range"] = {"file": "range.toml", "start": "blue_wing_note", "preload": preload, "floor_balls": True}
    if timestep is not None:
        cfg["model"]["timestep"] = timestep
    if sensors:
        cfg["sensor"] = sensors
    return cfg


def wing_world(tmp_path: Path, notes=(WING_NOTE,), preload: int = 0, **kw) -> World:
    """wing_cfg's world."""
    return World(wing_cfg(tmp_path, notes, preload, **kw), tmp_path)


def drive(hal: HalState, volts: float) -> None:
    """Both sides of the differential at `volts`, forward (its right side is mirrored)."""
    for port, sign in (("0", 1.0), ("1", -1.0)):
        hal.devices[("PWM", port)] = {"<init": True, "<speed": sign * volts / VOLTS}


def _state(w: World, slot: int) -> dict:
    """The slot's stand-in (center, momentum, its rigid shape's clearance) and its ring's read (center, momentum),
    None without one."""
    pool, balls = w.test_range.soft, w.test_range.balls
    ring = pool.in_use().get(slot)
    out = {"com": balls._qpos[slot, :3].copy(), "p": NOTE.mass * balls._qvel[slot, :3].copy(),
           "clearance": balls.clearance(slot), "ring": None}
    if ring is not None:
        c, _, v, _ = soft.read_rigid(w.model, w.data, ring, spin=False)
        out["ring"] = (c, soft.ring_view(w.model, ring).mass * v)
    return out


def watch_swaps(w: World, slot: int) -> list[dict]:
    """Each step's row from here on: its time, the slot's distance to the robot and that less its closing speed times
    the timestep (the predicted distance a swap in reads), its state just before the pool's update and just after, and
    in_use after (the pool's update, wrapped)."""
    pool, rows = w.test_range.soft, []
    inner = pool.update

    def update():
        dist, closing = pool.distances(np.array([slot]), closing=True)
        row = {"t": float(w.data.time), "dist": float(dist[0]),
               "pred": float(dist[0] - max(0.0, float(closing[0])) * w.model.opt.timestep), "before": _state(w, slot)}
        inner()
        row["after"], row["in_use"] = _state(w, slot), dict(pool.in_use())
        rows.append(row)
    pool.update = update
    return rows


def swap_run(w: World, press_s: float = 0.6, stop_s: float = 0.0, after_s: float = 0.5, on_step=None):
    """Drive at 2 V until the NOTE turns soft and on for `press_s`, stop for `stop_s`, then back away at 4 V until it is
    rigid again and `after_s` more. (slot, the rows (watch_swaps), the swap in's row, the swap back's row)."""
    hal = HalState()
    slot = int(np.flatnonzero(w.test_range.balls.field)[0])
    rows = watch_swaps(w, slot)
    drive(hal, 2.0)
    t_in = t_back = None
    while w.time < 8.0:
        w.step(hal)
        if on_step is not None:
            on_step(w)
        in_use = rows[-1]["in_use"]
        if t_in is None and in_use:
            t_in = w.time
        if t_in is not None and t_back is None:
            if w.time >= t_in + press_s + stop_s:
                drive(hal, -4.0)
            elif w.time >= t_in + press_s:
                drive(hal, 0.0)
            if not in_use:
                t_back = w.time
        if t_back is not None and w.time >= t_back + after_s - 1e-9:
            break
    assert t_in is not None and t_back is not None, "the NOTE never turned soft and back"
    assert w.test_range.soft.resets == 0        # the guard never fired (K6-R7)
    first = next(k for k, r in enumerate(rows) if r["in_use"])
    back = next(k for k in range(first, len(rows)) if not rows[k]["in_use"])
    return slot, rows, first, back


def test_a_note_near_the_robot_turns_soft_and_back(tmp_path):
    """D8, R12, R13: at the first step where the NOTE's center is predicted within SOFT_REACH of a robot geom's box
    after the next step (its distance less its closing speed times the timestep) it turns soft, its ring keeping its
    momentum (within 1e-6 kg m/s; the spike's swap: 3e-9) and its center (1e-6 m). Backing
    away at 4 V, it is rigid again SOFT_LINGER after its center is past SOFT_REACH from every robot geom: in play, on
    BALL_BITS, its momentum kept."""
    w = wing_world(tmp_path)
    slot, rows, first, back = swap_run(w)
    near = next(k for k, r in enumerate(rows) if r["pred"] <= soft.SOFT_REACH)
    assert near == first and rows[first]["in_use"] == {slot: "soft.0"}
    assert rows[first]["dist"] - rows[first]["pred"] < 0.001        # at 2 V the prediction moves it by a step
    assert rows[first]["dist"] > soft.SOFT_REACH - 0.01         # it turned soft on its way in, not deep inside
    before, (c, p) = rows[first]["before"], rows[first]["after"]["ring"]
    assert np.abs(p - before["p"]).max() < 1e-6 and np.abs(c - before["com"]).max() < 1e-6
    # rigid again SOFT_LINGER after the last step within reach
    last_near = max(k for k in range(back) if rows[k]["dist"] <= soft.SOFT_REACH)
    assert rows[back]["t"] - rows[last_near]["t"] == pytest.approx(soft.SOFT_LINGER + 0.001, abs=1e-9)
    assert all(rows[k]["in_use"] for k in range(first, back))
    (c, p), after = rows[back]["before"]["ring"], rows[back]["after"]
    assert np.abs(after["p"] - p).max() < 1e-6
    balls, m = w.test_range.balls, w.model
    assert balls.state[slot] == "field" and not balls.soft[slot] and w.test_range.soft.in_use() == {}
    g, b = balls.geoms[slot], balls.bodies[slot]
    assert (m.geom_contype[g], m.geom_conaffinity[g]) == (BALL_BITS["contype"], BALL_BITS["conaffinity"])
    assert (m.body_contype[b], m.body_conaffinity[b]) == (BALL_BITS["contype"], BALL_BITS["conaffinity"])
    assert w.test_range.soft.swaps == 1


def _subtree(m, d, body: int):
    """MuJoCo's own (center of mass, linear momentum, angular momentum about it) of `body`'s subtree."""
    mujoco.mj_forward(m, d)
    mujoco.mj_subtreeVel(m, d)
    return (d.subtree_com[body].copy(), m.body_subtreemass[body] * d.subtree_linvel[body],
            d.subtree_angmom[body].copy())


def test_a_swap_keeps_the_angular_momentum(tmp_path):
    """Task 5 review M9: the ring's inertia is a few percent below the rigid NOTE's, so the swap keeps the angular
    momentum (the spin rate moves by that much). MuJoCo's own subtree center, momentum and angular momentum are the
    same, within 1e-9, for the rigid NOTE, its ring after the swap in, and the rigid NOTE after the swap back."""
    w = wing_world(tmp_path)
    pool, balls, m, d = w.test_range.soft, w.test_range.balls, w.model, w.data
    slot = int(np.flatnonzero(balls.field)[0])
    ring = soft.ring_view(m, "soft.0")
    ratio = np.linalg.eigvalsh(ring.rest_inertia) / np.linalg.eigvalsh(pool._inertia[slot])
    assert ((ratio > 0.94) & (ratio < 0.97)).all(), ratio      # source: Task 5 review M9, 4 to 4.5 % below
    quat = np.array([0.9, 0.2, -0.3, 0.1]) / np.linalg.norm([0.9, 0.2, -0.3, 0.1])
    balls.place(slot, (3.0, 4.0, 1.0), (1.0, 0.5, -0.2), (2.0, -1.0, 9.0), BALL_BITS, quat=quat)
    stand_in, s0 = int(balls.bodies[slot]), int(ring.bodies[0])
    rigid = _subtree(m, d, stand_in)
    pool._swap_in(slot, 0)
    soft_ = _subtree(m, d, s0)
    pool._swap_back(0)
    again = _subtree(m, d, stand_in)
    for got in (soft_, again):
        for a, b in zip(rigid, got):
            assert np.abs(a - b).max() < 1e-9, (rigid, got)
    assert np.abs(rigid[2]).max() > 0.01            # a real spin


def test_the_soft_note_is_the_same_piece(tmp_path):
    """R11: while soft, the NOTE keeps its name and the counts; each of its ring's capsules is its piece in
    ball_of_geom; its ball log's last position follows the ring's center; and a Test-tab touch on it by the chassis
    holds while only the ring touches the robot."""
    w = wing_world(tmp_path)
    rt = w.test_range
    balls = rt.balls
    slot = int(np.flatnonzero(balls.field)[0])
    name, counts = balls.piece_name(slot), rt.counts()
    watch = Watch(w, Program(path=tmp_path / "t.test.toml", robot=ROBOT))
    touch = Cond("touch", {"robot": "drive", "field": f"ball.{slot}"})
    stand_in = set(balls._slot_geoms[slot].tolist())
    seen = {"soft": 0, "touch": 0}

    def on_step(w):
        watch.update()
        ring = rt.soft.in_use().get(slot)
        if ring is None:
            return
        seen["soft"] += 1
        assert balls.piece_name(slot) == name and rt.counts() == counts
        assert (balls.ball_of_geom(soft.ring_view(w.model, ring).geoms) == slot).all()
        c, _, _, _ = soft.read_rigid(w.model, w.data, ring, spin=False)
        assert np.abs(np.array(rt.ball_log()[slot]["last_pos"]) - c).max() < 1e-9
        ids = contact_ids(w.model, w.data)
        assert not stand_in & set(ids.ravel().tolist())         # the stand-in touches nothing
        if watch.holds(touch):
            seen["touch"] += 1
    swap_run(w, on_step=on_step)
    assert seen["soft"] > 500 and seen["touch"] > 100, seen


def test_a_scenario_contact_check_sees_the_soft_note(tmp_path):
    """Final review I1: driving into the wing NOTE, which turns soft before the robot reaches it, a scenario's
    must_touch on its `ball.<i>` passes and never_touch on it fails, and the first touch is while it is soft (only its
    ring touches the robot then). robot_contacts names the ring's capsules by their NOTE, never `soft.*`."""
    from types import SimpleNamespace

    from scrimmage.checks import ContactCheck

    w = wing_world(tmp_path)
    balls = w.test_range.balls
    slot = int(np.flatnonzero(balls.field)[0])
    target = mujoco.mj_id2name(w.model, mujoco.mjtObj.mjOBJ_BODY, int(balls.bodies[slot]))
    must, never = (ContactCheck(target, flag, w, "[[check]]") for flag in (True, False))
    session = SimpleNamespace(world=w)
    for c in (must, never):
        c.begin(session)
    seen = {"soft_at_first": None, "soft_names": set()}

    def on_step(w):
        for c in (must, never):
            c.tick(session, w.time)
        names = w.robot_contacts()
        seen["soft_names"] |= {n for n in names if n.startswith("soft.")}
        if seen["soft_at_first"] is None and must.first_t is not None:
            seen["soft_at_first"] = slot in w.test_range.soft.in_use()
    swap_run(w, on_step=on_step)
    assert must.end(session).passed and not never.end(session).passed
    assert seen["soft_at_first"] is True and not seen["soft_names"], seen


def test_a_held_or_launched_note_stays_rigid(tmp_path):
    """R12, a stated deviation from spec D8, which says a soft NOTE "is swapped back when it has been more than
    SOFT_REACH from the robot for 0.25 s (choice), or when the shooter launches it". Here a NOTE in the robot (the ball
    path's) or one just launched and still clearing its robot (on LAUNCHED_BITS) never turns soft, so the shooter
    never has a soft NOTE to launch. Its cost: no NOTE squashes in the world while the robot holds or shoots it (the
    pinch shooter's exit is launcher/pinch.py's, from `stiffness`, as before), and a weak shot that falls back onto
    its robot after LAUNCH_CLEAR_S may turn soft then. The example's subwoofer shot is unchanged and swaps nothing."""
    program = load(EXAMPLE / "subwoofer.test.toml")
    world = world_for(program)
    r = TestRun(world, program).run()
    assert r.status == "finished", r.outcome
    assert r.soft_resets == 0
    assert [c["passed"] for c in r.checks] == [True]
    log = world.test_range.ball_log()[0]
    assert log["goal"] == "speaker_blue" and log["first_hit"] is None
    assert log["hood_deg"] == pytest.approx(58.0, abs=0.2)
    assert log["shooter_rps"] == pytest.approx(21.7, rel=0.03)
    assert world.test_range.soft.swaps == 0
    # a NOTE on LAUNCHED_BITS 0.1 m from the robot stays rigid
    w = wing_world(tmp_path)
    pool, balls = w.test_range.soft, w.test_range.balls
    slot = int(np.flatnonzero(balls.field)[0])
    x0 = w.robot_pose()[0]
    xs = np.linspace(x0 + 1.0, x0, 201)      # from 1 m ahead, in
    for x in xs:
        balls.put(slot, (float(x), WING_NOTE[1], balls.rest_z_of(slot)), BALL_BITS)
        if pool.distances(np.array([slot]))[0] <= 0.1:
            break
    assert 0.09 <= pool.distances(np.array([slot]))[0] <= 0.1
    pos = balls.position(slot).copy()
    balls.place(slot, pos, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), LAUNCHED_BITS, full_bits_after=10.0)
    hal = HalState()
    for _ in range(200):
        w.step(hal)
        assert np.isfinite(balls._full_bits_at[slot]) and pool.distances(np.array([slot]))[0] < soft.SOFT_REACH
    assert pool.swaps == 0 and pool.in_use() == {} and pool.resets == 0


def test_the_swap_back_on_the_carpet_doesnt_hop(tmp_path):
    """R13: the robot presses the NOTE along the carpet with its bumper, stops, then backs away. At the swap back the
    stand-in's lowest point is within 1 mm of the carpet or above it; 50 ms later it moves up or down at under
    0.05 m/s, and over 0.5 s it moves under 2 cm."""
    w = wing_world(tmp_path)
    slot, rows, first, back = swap_run(w, press_s=0.6, stop_s=0.3, after_s=0.55)
    at = rows[back]["after"]
    assert at["clearance"] >= -0.001, at
    later = rows[back + 50]["before"]
    assert abs(later["p"][2] / NOTE.mass) < 0.05, later
    assert np.linalg.norm(rows[back + 500]["before"]["com"] - at["com"]) < 0.02
    # this robot's bumper squashes the ring by under a millimeter; a ring squashed as the spike's drop squashes it
    # (its center 14 mm low) is set on the carpet the same way
    w = wing_world(tmp_path)
    pool, balls, d = w.test_range.soft, w.test_range.balls, w.data
    slot = int(np.flatnonzero(balls.field)[0])
    pool._swap_in(slot, 0)
    c, q, v, spin = soft.read_rigid(w.model, d, "soft.0")
    soft.write_rigid(w.model, d, "soft.0", c - [0.0, 0.0, 0.014], q, v + [0.0, 0.0, -0.3], spin)
    pool._swap_back(0)
    assert balls.clearance(slot) == pytest.approx(0.0, abs=1e-12) and balls._qvel[slot, 2] == 0.0
    start, hal = balls.position(slot).copy(), HalState()
    for k in range(500):
        w.step(hal)
        if k == 49:
            assert abs(balls.velocity(slot)[2]) < 0.05
    assert np.linalg.norm(balls.position(slot) - start) < 0.02


def test_a_reset_mid_swap_parks_every_ring(tmp_path):
    """World.reset() with a ring in use parks every ring (at its built place under the floor, PARKED_BITS), and
    in_use() is empty; two 2 s runs from the reset give the same balls within 1e-12."""
    w = wing_world(tmp_path)
    rt, m, d = w.test_range, w.model, w.data
    hal = HalState()
    drive(hal, 2.0)
    while not rt.soft.in_use():
        w.step(hal)
    for _ in range(200):
        w.step(hal)
    assert rt.soft.in_use()
    w.reset()

    def parked():
        assert rt.soft.in_use() == {} and not rt.balls.soft.any()
        for name in rt.layout.soft:
            ring = soft.ring_view(m, name)
            assert np.array_equal(d.qpos[ring.q], ring.qpos0) and not d.qvel[ring.v].any()
            assert np.allclose(d.xpos[ring.bodies][:, 2], PARK_Z, atol=0.03)
            assert not m.geom_contype[ring.geoms].any() and not m.geom_conaffinity[ring.geoms].any()
            assert not m.body_contype[ring.bodies].any() and not m.body_conaffinity[ring.bodies].any()
            assert (rt.balls.ball_of_geom(ring.geoms) == -1).all()
    parked()
    runs = []
    for _ in range(2):
        drive(hal, 2.0)
        for _ in range(2000):
            w.step(hal)
        assert rt.soft.swaps == 1 and rt.soft.resets == 0
        runs.append((rt.balls._qpos.copy(), d.qpos.copy()))
        w.reset()
        parked()
    assert np.abs(runs[0][0] - runs[1][0]).max() <= 1e-12
    assert np.abs(runs[0][1] - runs[1][1]).max() <= 1e-12


def test_a_full_pool_leaves_the_third_rigid_and_notes_it_once(tmp_path):
    """Three NOTEs 0.4 m apart across the robot's path: two turn soft and the third stays rigid; the full pool's note
    is in soft_notes() and the Test tab's run notes once, and gone after a reset."""
    x, y = WING_NOTE
    w = wing_world(tmp_path, notes=((x, y - 0.4), (x, y), (x, y + 0.4)))
    rt = w.test_range
    pool, balls = rt.soft, rt.balls
    slots = np.flatnonzero(balls.field)
    assert len(slots) == 3
    hal = HalState()
    drive(hal, 2.0)
    full_seen = False
    for _ in range(1800):
        w.step(hal)
        near = pool.distances(slots) <= soft.SOFT_REACH
        assert len(pool.in_use()) <= 2
        if near.all() and len(pool.in_use()) == 2:
            full_seen = True
    assert full_seen and pool.swaps >= 2 and pool.resets == 0
    n = soft.POOL_SIZE
    full = (f"a NOTE came near the robot while the soft pool's {n} NOTE was in use; it stayed rigid" if n == 1 else
            f"a NOTE came near the robot while all {n} soft NOTEs were in use; it stayed rigid")
    notes = rt.soft_notes()
    assert notes.count(full) == 1, notes
    assert sum("UNCALIBRATED" in n for n in notes) == 1
    program = Program(path=tmp_path / "t.test.toml", robot=ROBOT)
    assert run_notes(w, program).count(full) == 1
    w.reset()
    assert rt.soft_notes() == [] and run_notes(w, program).count(full) == 0


def test_one_note_is_drawn(tmp_path):
    """R11: while soft, the live view's poses put the stand-in at PARK_Z, the ring's s0 where the ring is, and the
    free ring at PARK_Z."""
    w = wing_world(tmp_path)
    rt = w.test_range
    hal = HalState()
    drive(hal, 2.0)
    while not rt.soft.in_use():
        w.step(hal)
    for _ in range(100):
        w.step(hal)
    slot = next(iter(rt.soft.in_use()))
    ms = movers(w)
    poses = mover_poses(w, ms)
    row = {mv.id: i for i, mv in enumerate(ms) if mv.kind == "body"}
    stand_in = int(rt.balls.bodies[slot])
    assert rt.soft.drawn_parked() == {stand_in}
    assert poses[row[stand_in], 2] == np.float32(PARK_Z)
    assert np.allclose(poses[row[stand_in], :2], w.data.xpos[stand_in, :2])
    s0 = soft.ring_view(w.model, "soft.0").bodies[0]
    assert np.allclose(poses[row[s0], :3], w.data.xpos[s0], atol=1e-6) and poses[row[s0], 2] > 0.0
    free = soft.ring_view(w.model, "soft.1").bodies
    assert np.allclose(poses[[row[b] for b in free], 2], PARK_Z, atol=0.03)


# ------------------------------------------------------------------------------------------------ the review's fixes

def _soft_now(w: World, extra: int = 50) -> int:
    """Drive at 2 V until the NOTE turns soft, then `extra` steps more; its slot."""
    hal = HalState()
    drive(hal, 2.0)
    while not w.test_range.soft.in_use():
        w.step(hal)
    for _ in range(extra):
        w.step(hal)
    return next(iter(w.test_range.soft.in_use()))


def _scene_geoms(m, d) -> set[int]:
    scn = mujoco.MjvScene(m, 20000)
    mujoco.mjv_updateScene(m, d, mujoco.MjvOption(), None, mujoco.MjvCamera(), mujoco.mjtCatBit.mjCAT_ALL, scn)
    return {int(scn.geoms[i].objid) for i in range(scn.ngeom) if scn.geoms[i].objtype == mujoco.mjtObj.mjOBJ_GEOM}


def test_a_picture_draws_one_note(tmp_path):
    """Review finding 1: MuJoCo's own scene (screenshots, contact sheets, scenario pictures) has the ring while the
    NOTE is soft and not its rigid stand-in, and the stand-in again (at its own alpha) once it is rigid."""
    w = wing_world(tmp_path)
    m, d, rt = w.model, w.data, w.test_range
    slot = int(np.flatnonzero(rt.balls.field)[0])
    stand_in = set(rt.balls._slot_geoms[slot].tolist())
    alpha = m.geom_rgba[list(stand_in), 3].copy()
    assert stand_in <= _scene_geoms(m, d)
    _soft_now(w)
    seen = _scene_geoms(m, d)
    assert not stand_in & seen and set(soft.ring_view(m, "soft.0").geoms.tolist()) <= seen
    w.reset()
    assert stand_in <= _scene_geoms(m, d) and np.array_equal(m.geom_rgba[list(stand_in), 3], alpha)


def test_a_sensor_reads_a_soft_note(tmp_path):
    """Review finding 2: a pieces-only distance sensor aimed along the robot's axis at the soft NOTE reads it: its
    ray stops on one of the ring's capsules (the stand-in is hidden from rays), read as the NOTE, at the ring's near
    edge."""
    probe = wing_world(tmp_path / "probe")
    chassis = probe.model.body("drive.chassis").id
    z = 0.0254 - float(probe.data.xpos[chassis][2])     # the NOTE's tube's height, in the chassis frame
    w = wing_world(tmp_path / "w", sensors=[{"name": "front", "kind": "distance", "body": "drive.chassis",
                                            "at": [0.0, 0.0, z], "toward": [1.0, 0.0, 0.0], "max_range": 2.0,
                                            "analog": 0}])
    slot = _soft_now(w, extra=20)
    reading = w.sensors.reading("front")
    assert reading.distance < 2.0
    ring = soft.ring_view(w.model, w.test_range.soft.in_use()[slot])
    assert int(w.sensors._gid[0]) in set(ring.geoms.tolist())
    c, _, _, _ = soft.read_rigid(w.model, w.data, ring.name, spin=False)
    rot = w.data.xmat[chassis].reshape(3, 3)
    start = w.data.xpos[chassis] + rot @ np.array([0.0, 0.0, z])
    outer = 14.0 / 2 * 0.0254
    assert abs(start[0] + reading.distance - (c[0] - outer)) < 0.01


def test_a_fast_note_turns_soft_before_it_reaches_the_robot(tmp_path, monkeypatch):
    """Review finding 4, the owner's request: a NOTE turns soft when it is predicted within SOFT_REACH after the next
    step, so at a coarse timestep its ring is never created inside the robot. A range refuses a timestep above
    build.MAX_TIMESTEP (0.001 s) today, so this patches the limit to 0.005 s (a NOTE fired at 20 m/s then moves 0.1 m
    a step, more than the 0.07 m between SOFT_REACH and touching the bumper). Ten starts 0.01 m apart cover a step's
    phases; at each, no stand-in geom touches the robot before the swap, the ring touches it at most 1 mm deep in the
    step after it, and at least one swap came from the prediction (its center still past SOFT_REACH)."""
    from scrimmage.testrange import build
    monkeypatch.setattr(build, "MAX_TIMESTEP", 0.005)
    predicted = 0
    for k, d0 in enumerate(np.arange(0.60, 0.50, -0.01)):
        w = wing_world(tmp_path / str(k), timestep=0.005)
        rt, m, d = w.test_range, w.model, w.data
        pool, balls = rt.soft, rt.balls
        slot = int(np.flatnonzero(balls.field)[0])
        x0, y = w.robot_pose()[0], WING_NOTE[1]
        z = balls.rest_z_of(slot) + 0.005
        for x in np.linspace(x0 + 2.0, x0, 4001):
            balls.put(slot, (float(x), y, z), BALL_BITS, quat=balls.rest_quat(slot))
            mujoco.mj_forward(m, d)
            if pool.distances(np.array([slot]))[0] <= d0:
                break
        balls.place(slot, balls.position(slot).copy(), (-20.0, 0.0, 0.0), (0.0, 0.0, 0.0), BALL_BITS,
                    quat=balls.rest_quat(slot))
        stand_in, robot = set(balls._slot_geoms[slot].tolist()), set(pool._robot_geoms.tolist())
        hal = HalState()
        for _ in range(40):
            w.step(hal)
            pairs = contact_ids(m, d).tolist()
            assert not any((a in stand_in and b in robot) or (b in stand_in and a in robot) for a, b in pairs), d0
            if pool.in_use():
                break
        assert pool.in_use(), d0
        if pool.distances(np.array([slot]))[0] > soft.SOFT_REACH:
            predicted += 1
        w.step(hal)
        assert pool.resets == 0, d0
        ring = set(soft.ring_view(m, "soft.0").geoms.tolist())
        for i, (a, b) in enumerate(contact_ids(m, d).tolist()):
            if (a in ring and b in robot) or (b in ring and a in robot):
                assert d.contact.dist[i] >= -0.001, (d0, float(d.contact.dist[i]))
    assert predicted >= 1


def test_a_fed_note_turns_soft_at_a_waiting_robot(tmp_path):
    """Review finding 7: a NOTE the blue SOURCE feeds to a robot waiting 0.9 m out from it, facing it, turns soft
    within 1 s, and the ledger's counts don't change while it is soft."""
    w = wing_world(tmp_path)
    rt = w.test_range
    w.set_robot_pose(15.5023 - 0.5 * 0.9, 0.79 + 0.866 * 0.9, -60.0)
    mujoco.mj_forward(w.model, w.data)
    rt.feeder.start("source_blue")
    hal, counts = HalState(), None
    while w.time < 1.0 and not rt.soft.in_use():
        w.step(hal)
    assert rt.soft.in_use(), "the fed NOTE never turned soft"
    slot = next(iter(rt.soft.in_use()))
    assert slot in [f.ball for f in rt.feeder.fed]
    counts = rt.counts()
    for _ in range(200):
        w.step(hal)
        if slot in rt.soft.in_use():
            assert rt.counts() == counts
    assert rt.soft.resets == 0


def test_a_swap_back_waits_until_the_note_is_clear_of_a_wall(tmp_path):
    """Review finding 9: a ring squashed 8 mm into the blue alliance wall stays soft past SOFT_LINGER, since its rigid
    NOTE would be inside the wall; once it is clear, it swaps back, its rigid shape at most OVERLAP_TOL into
    anything."""
    w = wing_world(tmp_path)
    rt, m, d = w.test_range, w.model, w.data
    pool, balls = rt.soft, rt.balls
    slot = int(np.flatnonzero(balls.field)[0])
    balls.put(slot, (0.5, 2.5, balls.rest_z_of(slot)), BALL_BITS, quat=balls.rest_quat(slot))
    mujoco.mj_forward(m, d)
    pool._swap_in(slot, 0)
    _, q, _, _ = soft.read_rigid(m, d, "soft.0")
    outer = 14.0 / 2 * 0.0254
    z = balls.rest_z_of(slot)
    soft.write_rigid(m, d, "soft.0", (outer - 0.008, 2.5, z), q, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    pool.shadow()
    pool._far[slot] = -1.0                  # out of reach long since
    pool.update()
    assert pool.in_use() == {slot: "soft.0"}
    wall = m.geom("wall_blue").id
    assert pool.overlap(slot, 0, balls.position(slot), np.eye(3)) == pytest.approx(0.008, abs=0.002)
    soft.write_rigid(m, d, "soft.0", (outer + 0.01, 2.5, z), q, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    pool.shadow()
    pool.update()
    assert pool.in_use() == {}
    mujoco.mj_forward(m, d)
    fromto = np.zeros(6)
    for g in balls._slot_geoms[slot].tolist():
        assert mujoco.mj_geomDistance(m, d, g, wall, 0.05, fromto) >= -soft.OVERLAP_TOL


def test_a_scenario_report_notes_the_soft_note(tmp_path):
    """Review finding 8: a scenario on the wing range with a NOTE already within reach of the robot: report.json's
    notes have the soft NOTE's UNCALIBRATED note once."""
    import asyncio
    import json

    from scrimmage.scenario import load_scenario_text
    from scrimmage.scenario_runner import run_scenario
    from test_range_checks import write_config, write_scenario

    probe = wing_world(tmp_path / "probe")
    pool, balls = probe.test_range.soft, probe.test_range.balls
    slot = int(np.flatnonzero(balls.field)[0])
    x0, y = probe.robot_pose()[0], WING_NOTE[1]
    for x in np.linspace(x0 + 1.0, x0, 1001):
        balls.put(slot, (float(x), y, balls.rest_z_of(slot)), BALL_BITS, quat=balls.rest_quat(slot))
        mujoco.mj_forward(probe.model, probe.data)
        if pool.distances(np.array([slot]))[0] <= 0.22:
            break
    cfg = write_config(tmp_path, wing_cfg(tmp_path, notes=((round(float(x), 4), y),)))
    scn = write_scenario(tmp_path, cfg, '''
[[step]]
name = "wait"
mode = "teleop"
duration = 0.3
''')
    report = asyncio.run(run_scenario(load_scenario_text(scn.read_text(), scn), tmp_path / "out"))
    assert report.error is None, report.summary()
    notes = json.loads((tmp_path / "out" / "report.json").read_text())["notes"]
    assert sum("UNCALIBRATED" in n and "soft NOTE" in n for n in notes) == 1, notes
    assert not [n for n in notes if "blew up" in n or "unstable" in n], notes     # the guard never fired (K6-R7)


@pytest.mark.skipif(os.environ.get("SCRIMMAGE_TIMING") != "1", reason="timed locally only: set SCRIMMAGE_TIMING=1")
def test_what_a_soft_note_costs(tmp_path):
    """Review finding 3: wall seconds per simulated second on the wing world with two NOTEs, idle and pushed at 2 V,
    with 0, 1 and 2 soft (the least of 3 runs of 1 s), then what K6-R2's pairs cost (_what_pairs_cost). Printed, not
    a gate (F13): the numbers in spec D8's cost and docs/test-range.md (rerun for K6 on 2026-11-13: 0.36, 0.77 and
    1.13 s per simulated second pushed with 0, 1 and 2 soft, the same within 2 % as the K5 code on the same computer
    that day; the pairs 0.006 ms a step). Opt-in, as test_bench_reference.py's test_turnaround is (final review
    Minor 7): run it with SCRIMMAGE_TIMING=1 and -s to record the numbers."""
    x, y = WING_NOTE
    out = {}
    for volts, label in ((0.0, "idle"), (2.0, "pushed")):
        for n in (0, 1, 2):
            w = wing_world(tmp_path / f"{label}{n}", notes=((x, y - 0.2), (x, y + 0.2)))
            pool, balls = w.test_range.soft, w.test_range.balls
            for k, s in enumerate(np.flatnonzero(balls.field).tolist()):
                want, yy = (0.21, (y - 0.2, y + 0.2)[k]) if k < n else (0.9, y + 2.0)
                for xx in np.linspace(w.robot_pose()[0] + 1.2, w.robot_pose()[0], 1201):
                    balls.put(s, (float(xx), yy, balls.rest_z_of(s)), BALL_BITS, quat=balls.rest_quat(s))
                    mujoco.mj_forward(w.model, w.data)
                    if pool.distances(np.array([s]))[0] <= want:
                        break
            hal = HalState()
            for _ in range(300):
                w.step(hal)
            drive(hal, volts)
            best = math.inf
            for _ in range(3):
                t0 = time.perf_counter()
                for _ in range(1000):
                    w.step(hal)
                best = min(best, time.perf_counter() - t0)
            out[f"{label} {n} soft"] = best
    print({k: round(v, 3) for k, v in out.items()})
    print(_what_pairs_cost())


PAIR_RUN_S = 2.0  # choice: the example's drive: the NOTE is soft in the rollers for the second half of it
INTAKE_TEST = Path(__file__).resolve().parent.parent / "examples" / "blocks" / "note-intake" / "intake.test.toml"


def _what_pairs_cost() -> dict:
    """K6-R2's pairs on examples/blocks/note-intake (256 of them: 8 priority geoms x 2 rings x 16 capsules): wall
    seconds per simulated second built with and without them, with the robot standing (both rings parked, every pair
    still checked) and driving at its NOTE with the rollers on (a ring in use for about the second half), and the
    pairs' cost per step (ms). The least of 3 runs of PAIR_RUN_S, each from the start."""
    from scrimmage.blocks.program import load

    out = {}
    for pairs in (True, False):
        with pytest.MonkeyPatch.context() as mp:
            if not pairs:
                mp.setattr(soft, "pair_rings", lambda spec, rings, bodies: 0)
            w = world_for(load(INTAKE_TEST))
        assert (w.model.npair > 0) == pairs
        for label, volts in (("standing", 0.0), ("driving in", 3.1)):
            best, steps = math.inf, 0
            for _ in range(3):
                w.reset()
                hal = HalState()
                drive(hal, volts)
                hal.devices[("PWM", "2")] = {"<init": True, "<speed": (8.0 if volts else 0.0) / VOLTS}
                t0, s0, n = time.perf_counter(), w.data.time, 0
                while w.data.time - s0 < PAIR_RUN_S - 1e-9:
                    w.step(hal)
                    n += 1
                best, steps = min(best, (time.perf_counter() - t0) / (w.data.time - s0)), n / PAIR_RUN_S
            out[f"{label}, {'pairs' if pairs else 'no pairs'}"] = round(best, 3)
            out[f"{label}, steps per s"] = steps
    for label in ("standing", "driving in"):
        out[f"{label}, the pairs' ms per step"] = round(
            1000 * (out[f"{label}, pairs"] - out[f"{label}, no pairs"]) / out[f"{label}, steps per s"], 4)
    return out
