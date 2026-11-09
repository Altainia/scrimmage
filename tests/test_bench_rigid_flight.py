"""The bench takes the range's NOTE (`preset = "2024-note"`, `shape = "ring"`), flies a rigid piece in `[flight]`
(flight6 with rigid_aero's drag), and scores a shot against a goal from a range file (spec D3)."""

import math
import tomllib
from pathlib import Path

import mujoco
import numpy as np
import pytest

from scrimmage.bench import report
from scrimmage.bench.build import build_bench
from scrimmage.bench.problems import collect_problems
from scrimmage.bench.run import run_bench
from scrimmage.bench.spec import BenchError, parse_bench
from scrimmage.testrange.pieces import kind_preset
from scrimmage.testrange.spec import load_range, piece_box

RANGE_DIR = Path(__file__).resolve().parent / "data" / "range_2024"
GOAL = 'goal = { range = "2024.toml", name = "speaker_blue" }'


def _bench(speed: float = 8.05, elevation_deg: float = 49.0, flight: str | None = None,
           piece: str = 'preset = "2024-note"') -> str:
    """A NOTE thrown by hand (an inlet's velocity) straight out through an outlet: the bench measures how it leaves and
    [flight] flies it. The default flight stands the bench 2.5 m out from the blue alliance wall, facing the blue
    SPEAKER, its origin 0.5 m up."""
    e = math.radians(elevation_deg)
    if flight is None:
        flight = f'{GOAL}\nat = [2.5, 5.5446, 0.5]\nheading_deg = 180.0'
    return f'''
[bench]
name = "note lob"
author = "Sam"
idea = "lob a NOTE into the SPEAKER"
[piece]
{piece}
[[inlet]]
name = "hand"
center = [0.0, 0.0, 0.0]
size = [0.001, 0.001, 0.001]
count = 1
velocity = [{speed * math.cos(e):.4f}, 0.0, {speed * math.sin(e):.4f}]
[[outlet]]
name = "shot"
center = [0.45, 0.0, 0.0]
size = [0.1, 1.0, 3.0]
[flight]
outlet = "shot"
{flight}
[run]
duration = 0.5
'''


def _parse(text: str, base: Path = RANGE_DIR):
    return parse_bench(tomllib.loads(text), base)


def _lands(text: str, base: Path = RANGE_DIR):
    result = run_bench(_parse(text, base))
    return result, report.landings(result)


# ------------------------------------------------------------------------------------------------- the piece

def test_note_preset_is_the_ranges_note():
    bench = _parse(_bench())
    piece, note = bench.piece, load_range("2024.toml", RANGE_DIR).kinds["note"]
    assert piece.kind == "rigid" and piece.shape == "ring" and piece.ball is None
    assert piece.mass == note.mass == kind_preset("2024-note").mass
    assert piece.size == pytest.approx(piece_box(note))
    built = build_bench(bench)
    m, lay = built.model, built.layout
    body = lay.piece_bodies[0]
    assert m.body_mass[body] == pytest.approx(note.mass)
    assert sorted(m.body_inertia[body]) == pytest.approx(sorted(note.inertia[:3]), rel=1e-6)
    # the same solids: one geom per slice, the range's TUBE_SLICES, spanning the NOTE's 14 in by 2 in
    geoms = lay.piece_geom_sets[0]
    assert len(geoms) == len(note.solids) > 1
    verts = []
    for g in geoms:
        k = m.geom_dataid[g]
        rot = np.zeros(9)
        mujoco.mju_quat2Mat(rot, m.geom_quat[g])
        local = m.mesh_vert[m.mesh_vertadr[k]:m.mesh_vertadr[k] + m.mesh_vertnum[k]]
        verts.append(m.geom_pos[g] + local @ rot.reshape(3, 3).T)
    verts = np.concatenate(verts)
    assert verts.max(axis=0) - verts.min(axis=0) == pytest.approx(piece_box(note), abs=1e-6)
    # a rod through the hole touches nothing: the ring's middle is empty
    assert np.min(np.linalg.norm(verts[:, :2], axis=1)) > 0.12


def test_a_custom_ring_is_built_by_the_range_builder():
    bench = _parse(_bench(piece='shape = "ring"\nsize_in = [14.0, 10.0, 2.0]\nmass = 0.235'))
    note = kind_preset("2024-note")
    assert bench.piece.shape == "ring" and bench.piece.size == pytest.approx((0.3556, 0.254, 0.0508))
    assert bench.piece.rigid_kind.shadow_areas == pytest.approx(note.shadow_areas)
    assert bench.piece.inertia == pytest.approx(note.inertia[:3])


@pytest.mark.parametrize("piece, words", [
    ('preset = "2024-note"\nmass = 0.3', "comes from it"),
    ('preset = "2024-note"\nshape = "box"', "is a ring, not a box"),
    ('preset = "2024-note"\nkind = "squishy"', "is not one of: rigid"),
    ('shape = "ring"\nsize_in = [10.0, 14.0, 2.0]\nmass = 0.2', "inner diameter must be smaller"),
    ('preset = "2099-thing"', "known piece presets: 2025-coral"),
])
def test_piece_refusals(piece, words):
    with pytest.raises(BenchError, match=words):
        _parse(_bench(piece=piece))


# ------------------------------------------------------------------------------------------------- flight

def test_rigid_flight_reports_landings(tmp_path):
    text = _bench(flight='height = 0.5\ntarget_distance = 3.0\ntarget_height = 1.0')
    bench, problems = collect_problems(_write(tmp_path, text))
    assert bench is not None and not [p for p in problems if "balls only" in p.message], problems
    result, lands = _lands(text)
    assert len(lands) == 1
    p, land = lands[0]
    assert land.goal is None
    assert 3.0 < land.floor[0] < 6.0 and abs(land.floor[1]) < 1e-6  # a distance, straight ahead
    assert 0.8 < land.flight_time < 2.0                              # and a time
    assert land.at_goal is not None and land.at_goal[0] > 3.0
    # drag: shorter than the same throw in a vacuum, from the same exit
    e = math.radians(p.elevation_deg)
    vx, vz, z0 = p.speed * math.cos(e), p.speed * math.sin(e), p.path[-1][3] + 0.5 - 0.0254
    t = (vz + math.sqrt(vz * vz + 2 * 9.81 * z0)) / 9.81
    assert land.floor[0] < p.path[-1][1] + vx * t
    out = report.write_results(result, tmp_path / "out")
    summary = (out / "summary.txt").read_text(encoding="utf-8")
    assert "Landings (a rigid piece's flight" in summary and "balls only" not in summary
    assert "goal" not in (out / "landings.csv").read_text(encoding="utf-8").splitlines()[0].split(",")


def test_a_box_piece_flies_too():
    _, lands = _lands(_bench(piece='shape = "box"\nsize = [0.2, 0.2, 0.05]\nmass = 0.3',
                             flight='height = 0.5\ntarget_distance = 3.0\ntarget_height = 1.0'))
    assert len(lands) == 1 and lands[0][1].floor[0] > 2.0 and lands[0][1].flight_time > 0.5


def _write(tmp_path: Path, text: str) -> Path:
    f = tmp_path / "b.toml"
    f.write_text(text, encoding="utf-8")
    for src in RANGE_DIR.iterdir():
        (tmp_path / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return f


# ------------------------------------------------------------------------------------------------- the goal

def test_a_shot_into_the_speaker_scores_and_a_short_one_misses(tmp_path):
    result, lands = _lands(_bench(8.05, 49.0))
    assert [land.goal for _, land in lands] == ["scored"]
    assert report.headlines(result, lands)[0].startswith("1 of 1 left")
    assert "1 of 1 shots score in `speaker_blue`" in report.headlines(result, lands)
    out = report.write_results(result, tmp_path / "out")
    rows = (out / "landings.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0].split(",")[-1] == "goal" and rows[1].split(",")[-1] == "scored"
    _, short = _lands(_bench(6.0, 50.0))
    assert [land.goal for _, land in short] == ["missed"]
    assert short[0][1].blocked_by is None


def test_a_shot_through_an_obstacle_does_not_score(tmp_path):
    """The same shot that scores, with a practice net standing in its way: it crosses the opening, but hit the net
    first."""
    text = (RANGE_DIR / "2024.toml").read_text(encoding="utf-8")
    (tmp_path / "2024.toml").write_text(text + '''
[[obstacle]]
name = "practice_net"
pos = [1.3, 5.5446, 1.5]
size = [0.05, 2.0, 3.0]
''', encoding="utf-8")
    result, lands = _lands(_bench(8.05, 49.0), tmp_path)
    assert [(land.goal, land.blocked_by) for _, land in lands] == [("missed", "practice_net")]
    assert "0 of 1 shots score in `speaker_blue` (1 hit `practice_net` on the way)" in report.headlines(result, lands)


def test_the_sample_throws_either_side_of_the_band_hit_the_rim():
    """8.05 m/s at 49 degrees is the middle of the scoring band (8.0 to 8.1 m/s): a little faster and the NOTE's
    upper side overlaps the opening's top edge, so it doesn't all fit through."""
    _, lands = _lands(_bench(8.2, 49.0))
    assert [(land.goal, land.blocked_by) for _, land in lands] == [("missed", "speaker_blue.rim_1")]


def _straight_in(goal, u: float, v: float, extra_boxes=()):
    """A NOTE lying flat in the opening's plane, flown 10 m/s straight along the opening's normal from 0.3 m out to
    cross it at (u, v) in the opening's axes: the verdict and what stopped it."""
    from dataclasses import replace

    from scrimmage.launcher import flight6
    from scrimmage.testrange.pieces import _quat

    s = goal.goal.scoring_surface()
    q = _quat(np.column_stack([s.axes[0], s.axes[1], s.normal]))
    res = flight6.fly(s.center + 0.3 * s.normal + u * s.axes[0] + v * s.axes[1], -10.0 * s.normal, q, np.zeros(3),
                      0.235, np.eye(3) * 1e-3, lambda vel, rot, w: (np.zeros(3), np.zeros(3)),
                      goals=((goal.name, s),), floor=0.0)
    note = kind_preset("2024-note")
    lo = np.min([report._lo_hi(x)[0] for x in note.solids], axis=0)
    hi = np.max([report._lo_hi(x)[1] for x in note.solids], axis=0)
    return report._goal_verdict(res, replace(goal, boxes=goal.boxes + tuple(extra_boxes)), report._corners(lo, hi))


def test_a_flat_note_with_room_to_spare_scores():
    """The SPEAKER's opening is 41 in by 18.7 in; a NOTE's box is 14 in square: through the middle, it fits with 2.3
    in to spare top and bottom and 13.5 in each side, and still does 5 cm off center."""
    goal = _parse(_bench()).flight.goal
    assert _straight_in(goal, 0.0, 0.0) == ("scored", None)
    assert _straight_in(goal, 0.05, 0.0) == ("scored", None)


def test_a_center_just_inside_the_edge_misses_on_the_rim():
    """The center crosses 2 cm inside the opening's side, but the NOTE overlaps the side's rim bar."""
    goal = _parse(_bench()).flight.goal
    verdict, what = _straight_in(goal, 0.0, 0.50)
    assert verdict == "missed" and what.startswith("speaker_blue.rim_")
    # with no rim plate to name, the edge is named instead
    from dataclasses import replace
    bare = replace(goal, boxes=tuple(b for b in goal.boxes if ".rim_" not in b[0]))
    verdict, what = _straight_in(bare, 0.0, 0.50)
    assert verdict == "missed" and what.startswith("speaker_blue.edge_")


def test_a_corner_that_clips_a_roof_plate_is_blocked():
    """A thin roof 15 cm to the side of the NOTE's path: its center passes clear, its box's corners (8.9 cm further
    out) don't."""
    goal = _parse(_bench()).flight.goal
    s = goal.goal.scoring_surface()
    rot = np.column_stack([s.axes[0], s.axes[1], s.normal])
    roof = ("low_roof", s.center + 0.15 * s.normal + 0.65 * s.axes[1], rot, np.array([0.2, 0.5, 0.01]))
    assert _straight_in(goal, 0.0, 0.0, [roof]) == ("missed", "low_roof")
    clear = ("low_roof", roof[1] + 0.05 * s.axes[1], rot, roof[3])
    assert _straight_in(goal, 0.0, 0.0, [clear]) == ("scored", None)


def test_first_hit_is_the_earliest_box_any_point_enters():
    eye = np.eye(3)
    boxes = [("far", np.array([3.0, 0.0, 0.0]), eye, np.full(3, 0.1)),
             ("near", np.array([1.0, 0.0, 0.0]), eye, np.full(3, 0.1))]
    center = np.column_stack([np.linspace(0.0, 4.0, 41), np.zeros(41), np.zeros(41)])[:, None, :]
    assert report._first_hit(center, boxes) == (9, "near")
    assert report._first_hit(center + [0.0, 0.15, 0.0], boxes) is None
    side = np.concatenate([center + [0.0, 0.15, 0.0], center + [0.0, 0.05, 0.0]], axis=1)   # a corner closer in
    assert report._first_hit(side, boxes) == (9, "near")


def test_a_rigid_piece_takes_its_own_drag():
    flight = 'height = 0.5\ntarget_distance = 3.0\ntarget_height = 1.0'
    box = 'shape = "box"\nsize = [0.2, 0.2, 0.05]\nmass = 0.3'
    bench = _parse(_bench(piece=box + "\ndrag_coefficient = 0.0\nspin_damping = 0.002", flight=flight))
    assert (bench.piece.drag_coefficient, bench.piece.spin_damping) == (0.0, 0.002)
    note = _parse(_bench(piece='preset = "2024-note"\ndrag_coefficient = 0.8', flight=flight)).piece
    assert note.drag_coefficient == note.rigid_kind.drag_coefficient == 0.8
    # no drag flies further than the default 1.17
    far = report.landings(run_bench(bench))[0][1].floor[0]
    near = report.landings(run_bench(_parse(_bench(piece=box, flight=flight))))[0][1].floor[0]
    assert far > near + 0.2
    for piece, words in [(box + "\ndrag_coefficient = -0.5", r"\[piece\]: drag_coefficient can't be negative, not -0\.5"),
                         (box + '\nspin_damping = "x"', r"\[piece\]: spin_damping must be a number, not 'x'"),
                         ('preset = "2016-boulder"\ndrag_coefficient = 0.5', "a ball flies with the \\[air\\] drag")]:
        with pytest.raises(BenchError, match=words):
            _parse(_bench(piece=piece, flight=flight))


def test_the_uncalibrated_line_names_the_flights_drag_and_no_lift():
    flight = 'height = 0.5\ntarget_distance = 3.0\ntarget_height = 1.0'
    line = report.bench_uncalibrated(_parse(_bench(flight=flight)))
    assert "In [flight], the drag coefficient (1.17) is an estimate unless you measured it, and lift isn't modeled." \
        in line
    assert "drag coefficient" not in report.bench_uncalibrated(_parse(_bench(piece='preset = "2016-boulder"',
                                                                              flight=flight)))


@pytest.mark.parametrize("flight, words", [
    ('goal = { range = "nowhere.toml", name = "speaker_blue" }\nat = [2.5, 5.5, 0.5]\nheading_deg = 180.0',
     "goal range 'nowhere.toml' is not a file"),
    ('goal = { range = "2024.toml", name = "speaker" }\nat = [2.5, 5.5, 0.5]\nheading_deg = 180.0',
     "goal 'speaker' is not a goal in '2024.toml'; its goals: speaker_blue, amp_blue"),
    (f'{GOAL}\nheading_deg = 180.0', "at is missing"),
    (f'{GOAL}\nat = [2.5, 5.5, 0.5]', "heading_deg is missing"),
    (f'{GOAL}\nat = [2.5, 5.5, 0.5]\nheading_deg = 180.0\nheight = 0.5', "leave height out"),
    ('goal = "speaker_blue"\nat = [2.5, 5.5, 0.5]\nheading_deg = 180.0', "goal must be"),
    ('height = 0.5\ntarget_distance = 3.0\ntarget_height = 1.0\nat = [2.5, 5.5, 0.5]', "give goal ="),
])
def test_goal_refusals(flight, words):
    with pytest.raises(BenchError, match=words.replace("(", r"\(").replace(")", r"\)")):
        _parse(_bench(flight=flight))


def test_a_ball_takes_no_goal():
    with pytest.raises(BenchError, match="rigid pieces only"):
        _parse(_bench(piece='preset = "2016-boulder"'))
