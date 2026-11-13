"""The soft NOTE's acceptance suite (K6-R11): it can't be pushed through a thin part, through itself, or under a slab
by any load an FRC mechanism makes, and its squeeze keeps its calibration. Each scene is one ring alone on the world's
options (tests/soft_helpers.py), at both ends of the soft range and the preset.

Written before the fixes: FIXED_BY names the K6 task that makes each case that fails today pass (Task 3, the
contact ramp K6-R1, has landed; Task 5, the priority-part pairs K6-R2). Those cases are strict xfails, so a fix that
lands shows up as an XPASS until its entry is removed."""

from __future__ import annotations

import math
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import soft_helpers as sh  # noqa: E402
from scrimmage.testrange.pieces import kind_preset  # noqa: E402

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
# choice: K6-R11's 21 mm, ruled in Task 3. 200 N dragged at 1.0 r measures 18.3 mm at 2000 N/m and 20.3 mm at
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
# today's failing cases and the K6 task that fixes each (the value is the xfail's reason). Task 5 (K6-R2's pairs) owns
# every priority-1 case: the scene calls soft.pair_rings, which Task 5 adds. Task 3's ramp (K6-R1) passed every other
# case, with Task 3's two rulings (SQUASH_MAX and LAUNCH_GAIN_SLACK).
TASK5_GAIN = ("K6 Task 5: soft.pair_rings (AttributeError until then); once pairs exist, LAUNCH_GAIN_SLACK (8 %) "
              "applies here as at priority 0; Task 5 removes this entry")
TASK5_SLAB = ("K6 Task 5: 1 segment still under at the end with the spike's patch; if still red after pair_rings, "
              "Task 5 strengthens the fix, don't loosen under_end")
TASK5 = "K6 Task 5"
FIXED_BY: dict[str, str] = {
    **{f"{t * 1000:g}mm-{v}mps-prio1-{s:g}": TASK5 for t in WALLS for v in SPEEDS for s in STIFFNESSES},
    **{f"prio1-{pull}N-{s:g}": TASK5 for pull in PULLS for s in STIFFNESSES},
    **{f"{t * 1000:g}mm-8mps-prio1-20000": TASK5_GAIN for t in WALLS},
    "prio1-20N-2000": TASK5_SLAB,
}


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
        assert forces[2] == pytest.approx(133.5, rel=0.10), forces
        assert forces[3] == pytest.approx(494.3, rel=0.10), forces
