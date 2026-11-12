"""The world's soft NOTE (spec D8, first half; testrange/soft.py): a ring of 16 capsules on ball joints, its numbers
scaled from a kind's `stiffness` and `restitution` (R10), and a range's parked pool of rings under the floor."""

from __future__ import annotations

import math
import os
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
from scrimmage.protocol import HalState  # noqa: E402
from scrimmage.testrange import pieces, soft  # noqa: E402
from scrimmage.testrange.build import PARK_Z, PARKED_BITS  # noqa: E402
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
    """R10: the contact and joint stiffness in proportion to `stiffness`, the contact damping DAMPING_SCALE x the
    closed form for `restitution`."""
    assert soft.damping_ratio_for(0.3) == pytest.approx(0.358, abs=0.001)
    base, triple = soft.ring_params(_at(2000.0)), soft.ring_params(_at(6000.0))
    assert base.contact_k == pytest.approx(11211.4)
    assert triple.contact_k == pytest.approx(3 * 11211.4)
    assert base.joint_k == pytest.approx(0.10276) and triple.joint_k == pytest.approx(3 * 0.10276)
    for p in (base, triple):
        assert p.contact_b == pytest.approx(0.85 * 2 * soft.damping_ratio_for(0.3) * math.sqrt(p.contact_k))
    # the spike's own fitted damping (64.5 soft, 113 firm) within 2 %
    assert base.contact_b == pytest.approx(64.45, rel=0.02) and triple.contact_b == pytest.approx(113.0, rel=0.02)
    # the contact stiffness is per unit mass in MuJoCo, so it is scaled by FIT_MASS / mass; the joints aren't
    heavy = soft.ring_params(replace(_at(2000.0), mass=2 * NOTE.mass))
    assert soft.FIT_MASS == 0.235 and heavy.contact_k == pytest.approx(11211.4 / 2)
    assert heavy.joint_k == pytest.approx(base.joint_k)
    assert heavy.contact_b == pytest.approx(0.85 * 2 * soft.damping_ratio_for(0.3) * math.sqrt(heavy.contact_k))


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
    SOFT_RESTITUTION_RANGE, only a ring is soft, and its hole is wide enough that its segments don't overlap."""
    assert SOFT_STIFFNESS_RANGE == (1000.0, 20000.0) and SOFT_RESTITUTION_RANGE == (0.25, 0.55)
    for bad in (50000, 500):
        with pytest.raises(RangeError) as e:
            parse_kind(P, "s", {**RING, "stiffness": bad}, ROOT)
        assert "stiffness" in str(e.value) and "1000" in str(e.value) and "20000" in str(e.value)
    for bad in (0.2, 0.6):
        with pytest.raises(RangeError, match="restitution must be between 0.25 and 0.55, not"):
            parse_kind(P, "s", {**RING, "stiffness": 2000, "restitution": bad}, ROOT)
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


@pytest.mark.parametrize("e", SOFT_RESTITUTION_RANGE)
def test_the_rule_bounces_within_10_percent_at_both_ends(e):
    """M11: at each end of SOFT_RESTITUTION_RANGE, at the preset's 2000 N/m, the drop's e is within 10 % of the
    asked one (measured: 0.25 gives 0.265, 0.55 gives 0.496)."""
    assert soft_helpers.drop(_at(2000.0, e))["e"] == pytest.approx(e, rel=0.10)


@pytest.mark.parametrize("stiffness", SOFT_STIFFNESS_RANGE)
def test_the_ring_is_stable_at_both_ends_of_its_range(stiffness):
    """R14 (Review Focus 4): at each end of SOFT_STIFFNESS_RANGE the drop stays finite, its deepest contact is under
    40 mm, and it is still within 1 s of its first touch."""
    r = soft_helpers.drop(_at(stiffness))
    assert r["finite"]
    assert r["deepest"] < 0.040, r
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


def test_the_ring_is_built_as_its_kind():
    """16 bodies, a free joint and 15 ball joints, capsules of the tube's radius, the closing connect and its
    exclude; the contact is the rule's."""
    spec = mujoco.MjSpec()
    soft.add_ring(spec, "n", NOTE, (0.0, 0.0, 0.0), PARKED_BITS)
    m = spec.compile()
    assert m.nbody == 1 + 16 and m.njnt == 16 and m.nv == 6 + 15 * 3 and m.neq == 1 and m.nexclude == 1
    p = soft.ring_params(NOTE)
    r, big_r = (14.0 - 10.0) / 4 * 0.0254, (14.0 + 10.0) / 4 * 0.0254
    for j in range(16):
        g = m.geom(f"n.g{j}")
        assert g.type[0] == mujoco.mjtGeom.mjGEOM_CAPSULE and g.size[0] == pytest.approx(r)
        assert g.size[1] == pytest.approx(big_r * math.sin(math.pi / 16))
        assert list(g.solref) == pytest.approx([-p.contact_k, -p.contact_b]) and g.condim[0] == 6
        assert list(g.solimp) == pytest.approx(soft.SOFT_SOLIMP)
        assert list(g.friction) == pytest.approx([NOTE.friction, 0.005, 0.0001])
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    assert np.abs(d.efc_pos[:d.ne]).max() < 1e-12      # the loop closes at rest


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
