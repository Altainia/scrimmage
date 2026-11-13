"""The soft NOTE's acceptance suite (K6-R11): it can't be pushed through a thin part, through itself, or under a slab
by any load an FRC mechanism makes, and its squeeze keeps its calibration. Each scene is one ring alone on the world's
options (tests/soft_helpers.py), at both ends of the soft range and the preset.

Written before the fixes: FIXED_BY names the K6 task that makes each case that fails today pass (Task 3, the
contact ramp K6-R1, has landed; Task 5, the priority-part pairs K6-R2). Those cases are strict xfails, so a fix that
lands shows up as an XPASS until its entry is removed."""

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

import soft_helpers as sh  # noqa: E402
from scrimmage.flexcontact import id_name  # noqa: E402
from scrimmage.parts import partner as partner_mod  # noqa: E402
from scrimmage.parts.assemble import assemble  # noqa: E402
from scrimmage.testrange import soft  # noqa: E402
from scrimmage.testrange.pieces import kind_preset  # noqa: E402
from scrimmage.world import World  # noqa: E402

NOTE = kind_preset("2024-note")
IN = 0.0254
STIFFNESSES = (1000.0, 2000.0, 20000.0)  # source: K6-R11: both ends of SOFT_STIFFNESS_RANGE and a firm foam
VMAX = 20.0             # choice: under the guard's 25 m/s; K6-P5
THIN = (0.003175, 0.006)  # source: K6 spec: a 1/8 in polycarbonate plate and a 6 mm one
WALLS = (*THIN, 0.05)   # source: K6-R11: the thin plates and a 5 cm box
FORCES = (25, 50, 100, 200)  # source: K6-R11: roller loads (N)
SLIDE = 3.4             # source: k6 scenes.press: a roller's surface speed (m/s)
SPEEDS = (2, 4, 6, 8)   # source: K6-R11: hits up to 8 m/s, a launched NOTE's worst case near a robot
TACO_GAPS = (0.12, 0.10, 0.08, 0.06)  # source: K6-R11: plates closing to 120 mm down to 60 mm
LIFTS = (0.3, 1.0)      # source: K6-R11: the taco's lift on each far segment (N)
PULLS = (0, 5, 10, 20)  # source: K6-R11: a roller's drag on the NOTE (N)
# choice: 21 mm, ruled in Task 3 (K6-R11's drop bound; K6-R11 had 20 mm here). 200 N dragged at 1.0 r measures 18.3 mm at 2000 N/m and 20.3 mm at
# 1000 N/m. The spec's 0.9 r fallback sends the NOTE through both plates, and 0.95 r sits next to that cliff. 20.3 mm
# still leaves 4.7 mm before the 25 mm tube radius, and nothing goes through.
SQUASH_MAX = 0.021
DEPTH_MAX = 0.024       # choice: 3 mm short of the 1/8 in flip at r + T/2; measured 23.1 mm at 1000 N/m, 8 m/s
ENERGY_SLACK = 1.001    # choice: K6-P4; a hit never adds energy, within the integrator's rounding
# choice: Task 3's ruling, a documented limit (docs/test-range.md, The soft NOTE). At 20000 N/m an 8 m/s slide gains
# 6.8 % on the first step after the launch, from the carpet, not the wall. MuJoCo's soft friction lifts a contact
# sliding fast: its friction rows aim at -B v with B = contact_b / dmin (1290 /s at 20000 N/m), stiffened by impratio
# 10. A rigid box launched at 8 m/s with RIGID_SOLREF hops 219 mm too. Cutting the damping 10 % clears the gain but
# moves the drop's e to 0.335, outside its 10 %.
LAUNCH_GAIN_SLACK = 1.08
FAST_STIFF = {(8, 20000.0)}  # source: Task 3: the only (speed, stiffness) slams that gain energy (none up to 7 m/s)
SELF_MAX = 0.020        # choice: K6-R11; measured 17.3 mm with K6-R1
UNDER_MAX = 2           # choice: K6-R11; measured 2 segments, briefly, at 20 N with pairs
SQUEEZE_GAPS = (1.5, 1.0, 0.6, 0.4)  # source: K6-R1's table: 25 %, 50 %, 70 % and 80 % squeezes (in)
SQUEEZE_STIFFNESSES = (1000.0, 2000.0, 6000.0, 20000.0)  # source: K6-R1's table, the range and the spike's 6000
SQUEEZE_DEPTH = 0.25 * IN  # source: soft_scenes.SQUEEZE_GAP: each side of the 2 in NOTE squeezed 0.25 in at 1.5 in
CALIBRATION_TOL = 0.02  # choice: K6-R11
# today's failing cases and the K6 task that fixes each (the value is the xfail's reason). Task 3's ramp (K6-R1, with
# its two rulings, SQUASH_MAX and LAUNCH_GAIN_SLACK) and Task 5's pairs (K6-R2) passed every case.
FIXED_BY: dict[str, str] = {}


def _at(stiffness: float):
    return replace(NOTE, stiffness=stiffness)


def _cases(rows):
    """pytest params from (id, values) rows, each a strict xfail if FIXED_BY names it."""
    return [pytest.param(*values, id=cid,
                         marks=pytest.mark.xfail(strict=True, reason=FIXED_BY[cid]) if cid in FIXED_BY else ())
            for cid, values in rows]


def _sane(r: dict) -> None:
    assert r["finite"], r
    assert r["vmax"] < VMAX, r


PRESS = _cases((f"{t * 1000:g}mm-{f}N-{'dragged' if slide else 'held'}-{s:g}", (t, f, slide, s))
               for t in THIN for f in FORCES for slide in (0.0, SLIDE) for s in STIFFNESSES)


@pytest.mark.parametrize("thickness, force, slide, stiffness", PRESS)
def test_a_thin_plate_holds_a_roller_load(thickness, force, slide, stiffness):
    """A 2 in roller pressing the NOTE onto a 1/8 in or 6 mm plate, held or dragged at a roller's surface speed: no
    segment's center gets below the plate's top, and the squash stays under SQUASH_MAX."""
    r = sh.press(_at(stiffness), force, thickness, slide)
    _sane(r)
    assert not r["center_below_top"], r
    assert r["squash"] < SQUASH_MAX, r


SLAM = _cases((f"{t * 1000:g}mm-{v}mps-prio{p}-{s:g}", (t, v, p, s))
              for t in WALLS for v in SPEEDS for p in (0, 1) for s in STIFFNESSES)


@pytest.mark.parametrize("thickness, v, priority, stiffness", SLAM)
def test_a_hard_hit_never_goes_through(thickness, v, priority, stiffness):
    """The NOTE sliding flat into a 1/8 in, 6 mm or 5 cm wall at up to 8 m/s, a priority-0 wall or a priority-1 one
    met through pairs: it never goes through, its depth stays under DEPTH_MAX, and the hit adds no energy. A firm NOTE
    launched at 8 m/s may gain up to LAUNCH_GAIN_SLACK from the carpet (a documented limit)."""
    r = sh.slam(_at(stiffness), v, thickness, priority=priority, pairs=priority == 1)
    _sane(r)
    assert not r["through"], r
    assert r["deepest"] < DEPTH_MAX, r
    slack = LAUNCH_GAIN_SLACK if (v, stiffness) in FAST_STIFF else ENERGY_SLACK
    assert r["energy_gain"] <= slack, r


TACO = _cases((f"{g * 1000:g}mm-{lift:g}N-{s:g}", (g, lift, s))
              for g in TACO_GAPS for lift in LIFTS for s in STIFFNESSES)


@pytest.mark.parametrize("gap, lift, stiffness", TACO)
def test_a_folded_ring_doesnt_pass_through_itself(gap, lift, stiffness):
    """The ring folded into a taco and pressed face to face between plates closing to `gap`: it keeps colliding with
    itself (K6-R3), overlapping under SELF_MAX."""
    r = sh.taco(_at(stiffness), gap, lift)
    _sane(r)
    assert r["self_overlap"] < SELF_MAX, r


SLAB = _cases((f"prio{p}-{pull}N-{s:g}", (p, pull, s))
              for p in (0, 1) for pull in PULLS for s in STIFFNESSES)


@pytest.mark.parametrize("priority, pull, stiffness", SLAB)
def test_a_2mm_slab_doesnt_run_the_note_over(priority, pull, stiffness):
    """A slab 2 mm over the carpet driving at the NOTE at 2 m/s while a roller pulls the NOTE toward it: at most
    UNDER_MAX segments are ever under the slab, and none at the end."""
    r = sh.slab(_at(stiffness), pull, priority=priority, pairs=priority == 1)
    _sane(r)
    assert r["under_max"] <= UNDER_MAX, r
    assert r["under_end"] == 0, r


def test_a_note_beside_the_slab_isnt_under_it():
    """Regression (Task 5): at 15 N and 2.0 m/s the NOTE rides up the slab's front and drops an edge onto the carpet
    past the slab's side. Counted without the slab's width, 2 segments were "under" it and 1 at the end; none is."""
    stiffness = 2000.0  # choice: the preset, where the near cases ended under without the width bound
    pull = 15           # choice: Task 5's near case that failed without the bound (2 under, 1 at the end)
    r = sh.slab(_at(stiffness), pull, priority=1, pairs=True)
    _sane(r)
    assert (r["under_max"], r["under_end"]) == (0, 0), r


SQUEEZE = _cases((f"{s:g}", (s,)) for s in SQUEEZE_STIFFNESSES)


@pytest.mark.parametrize("stiffness", SQUEEZE)
def test_the_squeeze_stiffens_and_keeps_its_calibration(stiffness):
    """Two rollers squeezing the NOTE to 1.5, 1.0, 0.6 and 0.4 in: every force is finite and each deeper squeeze
    pushes back harder, and the 25 % point is still `stiffness` times the depth per side."""
    forces = [sh.squeeze(_at(stiffness), gap * IN) for gap in SQUEEZE_GAPS]
    assert all(math.isfinite(f) for f in forces), forces
    assert forces[0] == pytest.approx(stiffness * SQUEEZE_DEPTH, rel=CALIBRATION_TOL), forces
    assert all(b > a for a, b in zip(forces, forces[1:])), forces
    if stiffness == 2000.0:
        # K6-R1's measured squeeze at the preset (choice: pins the rule)
        assert forces[1] == pytest.approx(31.5, rel=0.10), forces
        assert forces[2] == pytest.approx(133.5, rel=0.10), forces
        assert forces[3] == pytest.approx(494.3, rel=0.10), forces


# ------------------------------------------------------------------------------------------- K6-R2's pairs

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "blocks"
SPEAKER = EXAMPLES / "speaker" / "note-shooter-standin.toml"
INTAKE = EXAMPLES / "note-intake" / "note-intake-standin.toml"
INTAKE_FRICTION = 0.4   # source: examples/blocks/note-intake/parts/intake/part.toml: the bin's priority geoms
PARTNER_AT = "\n[partner]\nstart = [4, 4, 0]\n"  # choice: tests/test_partner.py's start, clear of the robot
BUILD_COST_MAX = 1.2    # choice: K6-R11 and K6-P6: pairs make a build at most 20 % slower


def _build(config: Path, extra: str = "", pairs: bool = True):
    cfg = tomllib.loads(config.read_text() + extra)
    if pairs:
        return assemble(cfg, config.parent)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(soft, "pair_rings", lambda spec, rings, bodies: 0)
        return assemble(cfg, config.parent)


def _priority_counts(m: mujoco.MjModel, roots: list[int]) -> tuple[list[int], list[int]]:
    """The compiled model's priority bodies under `roots` (body ids) and their colliding geoms."""
    colliding = (m.geom_contype != 0) | (m.geom_conaffinity != 0)
    under = np.isin(m.body_rootid[m.geom_bodyid], roots)
    bodies = sorted({int(b) for b in m.geom_bodyid[colliding & under & (m.geom_priority > 0)]})
    geoms = [int(g) for g in np.flatnonzero(colliding & np.isin(m.geom_bodyid, bodies))]
    return bodies, geoms


@pytest.mark.parametrize("config", [SPEAKER, INTAKE], ids=["speaker", "intake"])
def test_the_pairs_cover_every_priority_body(config):
    """Every colliding geom of every robot body with a priority geom meets every ring capsule through a pair with the
    capsule's own solref, and each such body is excluded from each ring segment (K6-R2)."""
    built = _build(config)
    m = built.model
    bodies, geoms = _priority_counts(m, [int(m.body(built.robot_body).id)])
    n_r = soft.RING_SEGMENTS * len(built.layout.soft)
    assert m.npair == n_r * len(geoms)
    assert m.nexclude - _build(config, pairs=False).model.nexclude == n_r * len(bodies)
    rings = set(np.flatnonzero(np.isin(m.geom_bodyid, [m.body(f"{r}.s{j}").id for r in built.layout.soft
                                                      for j in range(soft.RING_SEGMENTS)])).tolist())
    for p in range(m.npair):
        a, b = int(m.pair_geom1[p]), int(m.pair_geom2[p])    # MuJoCo may put either side first
        assert (a in rings) != (b in rings)
        cap = a if a in rings else b
        np.testing.assert_array_equal(m.pair_solref[p], m.geom_solref[cap])
        np.testing.assert_array_equal(m.pair_solimp[p], m.geom_solimp[cap])
        # MuJoCo's own rule for the rest: the higher priority's friction and condim, the larger of each at equal
        # priority; the larger margin and gap
        part = b if cap == a else a
        if m.geom_priority[part] != m.geom_priority[cap]:
            win = part if m.geom_priority[part] > m.geom_priority[cap] else cap
            mu, condim = m.geom_friction[win], m.geom_condim[win]
        else:
            mu = np.maximum(m.geom_friction[part], m.geom_friction[cap])
            condim = max(m.geom_condim[part], m.geom_condim[cap])
        np.testing.assert_allclose(m.pair_friction[p], [mu[0], mu[0], mu[1], mu[2], mu[2]])
        assert m.pair_dim[p] == condim
        assert m.pair_margin[p] == max(m.geom_margin[part], m.geom_margin[cap])
        assert m.pair_gap[p] == max(m.geom_gap[part], m.geom_gap[cap])
    if config == INTAKE:
        assert m.npair > 0
        np.testing.assert_allclose(m.pair_friction[:, 0], INTAKE_FRICTION)


def _priority_partner(monkeypatch) -> None:
    """The partner's chassis with its first colliding geom at priority 1."""
    real = partner_mod._parts

    def parts(p):
        chassis, drive = real(p)
        body = chassis.part.bodies[0]
        i = next(i for i, g in enumerate(body.geoms) if g.collide)
        geoms = [replace(g, priority=1) if k == i else g for k, g in enumerate(body.geoms)]
        part = replace(chassis.part, bodies=[replace(body, geoms=geoms), *chassis.part.bodies[1:]])
        return replace(chassis, part=part), drive
    monkeypatch.setattr(partner_mod, "_parts", parts)


def test_a_partners_priority_geoms_are_paired(monkeypatch):
    """The partner robot, added after the range, is paired too (K6-R2)."""
    _priority_partner(monkeypatch)
    built = _build(INTAKE, PARTNER_AT)
    m = built.model
    partner = int(m.body(built.partner.body).id)
    mine = [g for g in range(m.ngeom) if m.body_rootid[m.geom_bodyid[g]] == partner and m.geom_priority[g] > 0]
    assert len(mine) == 1
    assert mine[0] in set(m.pair_geom1.tolist()) | set(m.pair_geom2.tolist())


def test_no_priority_geom_no_pairs():
    """A robot with no priority geom gets no pairs, and its model is the one built without pair_rings."""
    m, m0 = _build(SPEAKER).model, _build(SPEAKER, pairs=False).model
    assert m.npair == 0
    assert (m.nbody, m.ngeom, m.nexclude) == (m0.nbody, m0.ngeom, m0.nexclude)


def test_a_paired_geom_keeps_its_contact_name():
    """A geom named only for its pairs (K6-P11) still reads as its body in contact reports."""
    m = _build(INTAKE).model
    renamed = [g for g in range(m.ngeom) if soft.PAIR_SUFFIX in (mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g)
                                                                    or "")]
    assert renamed
    for g in renamed:
        assert id_name(m, g) == m.body(int(m.geom_bodyid[g])).name


@pytest.mark.skipif(os.environ.get("SCRIMMAGE_TIMING") != "1", reason="timed locally only: set SCRIMMAGE_TIMING=1")
def test_pairs_cost_little_to_build():
    """K6-R11: the intake's build with its pairs is under BUILD_COST_MAX times the build without."""
    def best(pairs: bool) -> float:
        times = []
        for _ in range(3):
            t0 = time.perf_counter()
            _build(INTAKE, pairs=pairs)
            times.append(time.perf_counter() - t0)
        return min(times)
    without, with_pairs = best(False), best(True)
    print(f"intake build: {without:.3f} s without pairs, {with_pairs:.3f} s with")
    assert with_pairs < BUILD_COST_MAX * without


def test_a_paired_partner_geom_keeps_its_contact_name(monkeypatch):
    """A partner geom named only for its pairs (K6-P11) reaches contact checks and their reports as its body:
    World.contact_names() and World.robot_contacts() never show the #soft-pair- suffix."""
    _priority_partner(monkeypatch)
    w = World(tomllib.loads(INTAKE.read_text() + PARTNER_AT), INTAKE.parent)
    m = w.model
    partner = int(m.body(w.parts.partner.body).id)
    renamed = [g for g in range(m.ngeom) if m.body_rootid[m.geom_bodyid[g]] == partner
               and soft.PAIR_SUFFIX in (mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g) or "")]
    assert renamed      # every colliding geom of the priority body, all unnamed in the stock chassis
    names = w.contact_names()
    assert not [n for n in names if soft.PAIR_SUFFIX in n]
    assert m.body(int(m.geom_bodyid[renamed[0]])).name in names
    # the robot touching that geom: one contact, robot geom 0's body against the renamed geom
    robot_geom = next(g for g in range(m.ngeom) if m.body_rootid[m.geom_bodyid[g]] == m.body_rootid[w.robot_body])
    import scrimmage.flexcontact as fc
    monkeypatch.setattr(fc, "contact_ids", lambda model, data: np.array([[robot_geom, renamed[0]]]))
    touching = w.robot_contacts()
    assert touching and not [n for n in touching if soft.PAIR_SUFFIX in n], touching
