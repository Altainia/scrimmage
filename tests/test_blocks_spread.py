"""Floor spreads (scrimmage/blocks/spread.py): the intake example's test run with its floor ball at a grid of
placements, a run kept for each, and a sentence saying which got picked up."""

from __future__ import annotations

import csv
import json
import math
import shutil
import threading
from pathlib import Path

import pytest

from scrimmage.__main__ import main
from scrimmage.blocks import store
from scrimmage.blocks.program import load
from scrimmage.blocks.run import TestRun, world_for
from scrimmage.blocks.spread import label, placement_labels, placements, run_spread, sentence, spread_ball
from scrimmage.blocks.store import KEEP_SPREADS

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "blocks" / "intake"
IN = 0.0254


def spread_test(folder: Path, spread: str = "") -> Path:
    """A copy of the intake example in `folder` with `hopper = ["intake.hopper"]` and `spread` as its floor spread
    (e.g. "offset_in = [0, 30]"); the test file's path."""
    shutil.copytree(EXAMPLE, folder / "intake", ignore=shutil.ignore_patterns("*-runs"))
    path = folder / "intake" / "intake.test.toml"
    text = path.read_text(encoding="utf-8").replace("[test]\n", '[test]\nhopper = ["intake.hopper"]\n', 1)
    if spread:
        text += f"\n[setup.floor_spread]\n{spread}\n"
    path.write_text(text, encoding="utf-8")
    return path


def read_table(folder: Path) -> list[dict]:
    with open(folder / "table.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_placements_grid_order_and_defaults():
    ps = placements({"offset_in": [-6, 6], "angle_deg": [0, 30]}, {"offset_in": 0.0, "distance_in": 59.06})
    assert [(p["offset_in"], p["angle_deg"]) for p in ps] == [(-6, 0), (-6, 30), (6, 0), (6, 30)]
    assert all(p["distance_in"] == 59.06 and p["rotation_deg"] == 0 for p in ps)
    assert all(list(p) == ["offset_in", "distance_in", "angle_deg", "rotation_deg", "tip_deg"] for p in ps)
    (only,) = placements({"distance_in": [40]}, {"offset_in": 2.5, "distance_in": 59.06})
    assert only == {"offset_in": 2.5, "distance_in": 40, "angle_deg": 0, "rotation_deg": 0, "tip_deg": 0}


def test_tip_is_a_spread_axis_and_old_spreads_keep_their_order():
    base = {"offset_in": 0.0, "distance_in": 59.06}
    ps = placements({"offset_in": [-6, 6], "rotation_deg": [0, 90]}, base)
    assert all(p["tip_deg"] == 0 for p in ps)
    assert [(p["offset_in"], p["rotation_deg"]) for p in ps] == [(-6, 0), (-6, 90), (6, 0), (6, 90)]
    ps = placements({"tip_deg": [0, 90], "rotation_deg": [0, 45]}, base)
    assert [(p["rotation_deg"], p["tip_deg"]) for p in ps] == [(0, 0), (0, 90), (45, 0), (45, 90)]   # tip last


def test_a_floor_pieces_own_tip_and_turn_are_the_spreads_defaults():
    """A coral the field has lying across stays lying across in a spread that names neither axis."""
    (only,) = placements({"offset_in": [3]}, {"offset_in": 0.0, "distance_in": 40.0, "tip_deg": 0.0,
                                                "rotation_deg": 90.0})
    assert only["tip_deg"] == 0 and only["rotation_deg"] == 90
    (only,) = placements({"tip_deg": [90]}, {"offset_in": 0.0, "distance_in": 40.0, "tip_deg": 0.0,
                                               "rotation_deg": 90.0})
    assert only["tip_deg"] == 90 and only["rotation_deg"] == 90


def coral():
    from scrimmage.testrange.pieces import parse_kind
    return parse_kind(Path("coral.toml"), "coral", {"preset": "2025-coral"}, Path("."))


def test_labels_for_a_coral():
    c = coral()
    p = {"offset_in": 6, "distance_in": 40, "angle_deg": 0, "rotation_deg": 0, "tip_deg": 0}
    assert label({**p, "rotation_deg": 90, "tip_deg": 0}, ["rotation_deg", "tip_deg"], c) == "lying across"
    assert label({**p, "tip_deg": 90}, ["tip_deg"], c) == "standing on end"
    assert label({**p, "rotation_deg": 45}, ["offset_in", "rotation_deg"], c) == "6 in left lying at 45°"
    assert label({**p, "rotation_deg": 45}, ["rotation_deg"], None) == "turned 45°"   # a ball: unchanged
    assert label({**p, "offset_in": -6}, ["offset_in"], c) == "6 in right"            # no orientation varying


def test_a_flat_kinds_turns_read_differently():
    """A note lying flat reads "lying flat" at every turn: a spread over its turn adds the turn, so every placement
    reads differently, and the sentence still parses."""
    from scrimmage.testrange.pieces import parse_kind
    note = parse_kind(Path("note.toml"), "note", {"preset": "2024-note"}, Path("."))
    assert note.flat
    ps = placements({"rotation_deg": [0, 45, 90]}, {"offset_in": 0.0, "distance_in": 40.0, "tip_deg": 90.0})
    words = placement_labels(ps, ["rotation_deg"], note)
    assert words == ["lying flat (turned 0°)", "lying flat (turned 45°)", "lying flat (turned 90°)"]
    assert sentence([False] * 3, words, at=False) == \
        "picked up 0 of 3; missed lying flat (turned 0°), lying flat (turned 45°) and lying flat (turned 90°)"
    ps = placements({"offset_in": [-6, 6], "tip_deg": [80, 90]}, {"offset_in": 0.0, "distance_in": 40.0})
    assert placement_labels(ps, ["offset_in", "tip_deg"], coral()) == [
        "6 in right standing on end (tipped 80°)", "6 in right standing on end (tipped 90°)",
        "6 in left standing on end (tipped 80°)", "6 in left standing on end (tipped 90°)"]
    # both differ: the parentheses keep their comma, and the sentence still joins with plain commas
    ps = placements({"tip_deg": [80, 90], "rotation_deg": [0, 90]}, {"offset_in": 0.0, "distance_in": 40.0})
    words = placement_labels(ps, ["tip_deg", "rotation_deg"], coral())
    assert words == ["standing on end (turned 0°, tipped 80°)", "standing on end (turned 0°, tipped 90°)",
                     "standing on end (turned 90°, tipped 80°)", "standing on end (turned 90°, tipped 90°)"]
    assert sentence([False] * 4, words, at=False).count(";") == 1          # "picked up 0 of 4; missed ..." only
    ps = placements({"rotation_deg": [0, 45]}, {"offset_in": 0.0, "distance_in": 40.0})
    assert placement_labels(ps, ["rotation_deg"], None) == ["turned 0°", "turned 45°"]     # a ball: unchanged
    ps = placements({"rotation_deg": [0, 90]}, {"offset_in": 0.0, "distance_in": 40.0, "tip_deg": 0.0})
    assert placement_labels(ps, ["rotation_deg"], coral()) == ["lying in line", "lying across"]   # distinct: as is


def test_a_coral_sentence_drops_the_at():
    labels = ["lying in line", "lying at 45°", "standing on end"]
    assert sentence([True, True, False], labels, at=False) == "picked up 2 of 3; missed standing on end"
    assert sentence([True, False], ["a", "b"]) == "picked up 1 of 2; missed at b"         # a ball's: unchanged


def test_label():
    p = {"offset_in": 6, "distance_in": 40, "angle_deg": 30, "rotation_deg": 45}
    assert label(p, ["offset_in", "angle_deg"]) == "6 in left at 30°"
    assert label({**p, "offset_in": -6}, ["angle_deg", "offset_in"]) == "6 in right at 30°"     # in axis order
    assert label({**p, "offset_in": 0}, ["offset_in"]) == "on the center line"
    assert label(p, ["distance_in", "rotation_deg"]) == "40 in ahead turned 45°"


def test_sentence():  # a ball's
    assert sentence([True] * 9, ["x"] * 9) == "picked up 9 of 9"
    picked = [True, True, False, True, True, True, True, False, True]
    labels = [f"p{i}" for i in range(9)]
    labels[2], labels[7] = "6 in left at 30°", "6 in right at 30°"
    assert sentence(picked, labels) == "picked up 7 of 9; missed at 6 in left at 30° and 6 in right at 30°"
    assert sentence([False, False, False], ["a", "b", "c"]) == "picked up 0 of 3; missed at a, b and c"


def test_the_spread_ball_is_the_intake_fields_ball():
    world = world_for(load(EXAMPLE / "intake.test.toml"))
    i, where = spread_ball(world)
    assert i == 0 and where == {"offset_in": 0.0, "distance_in": pytest.approx(1.5 / IN)}    # 1.5 m straight ahead


def test_the_robot_boxes_leave_the_worlds_own_data_alone():
    """floor_ball measures the robot on a scratch MjData, as drive.drivetrain() does: the session world's own data
    (its positions, which a Watch may be reading) is not recomputed."""
    from scrimmage.blocks.spread import floor_ball
    world = world_for(load(EXAMPLE / "intake.test.toml"))
    world.data.xpos[:] = 0.0                # stale on purpose: mj_kinematics on the world's data would fill it in
    assert floor_ball(world)["nearest_in"] > 0
    assert not world.data.xpos.any()


def test_a_placement_moves_the_ball_and_turns_the_robot(tmp_path):
    program = load(spread_test(tmp_path, "offset_in = [6]"))
    world = world_for(program)
    p = {"offset_in": 6.0, "distance_in": 40.0, "angle_deg": 30.0, "rotation_deg": 45.0, "tip_deg": 0.0}
    r = TestRun(world, program, placement=p).run()
    poses = r.motion[0][1]                                  # the first sample, after the settle
    ball = poses[r.movers.index("ball.0")]
    h = math.radians(30.0)
    x = 2.0 + 40 * IN * math.cos(h) - 6 * IN * math.sin(h)
    y = 4.0 + 40 * IN * math.sin(h) + 6 * IN * math.cos(h)
    assert ball[:2] == pytest.approx([x, y], abs=0.005)
    assert ball[3:] == pytest.approx([math.cos(math.radians(22.5)), 0, 0, math.sin(math.radians(22.5))], abs=1e-3)
    qw, qz = poses[r.movers.index("drive.chassis")][[3, 6]]
    assert math.degrees(2 * math.atan2(qz, qw)) == pytest.approx(30.0, abs=0.5)
    (floor,) = r.feeds
    assert floor["source"] == "floor" and floor["ball"] == 0


def test_run_spread_keeps_a_spread_with_a_row_per_placement(tmp_path):
    p = load(spread_test(tmp_path, "offset_in = [-3, 0, 3]"))
    base = tmp_path / "runs"
    seen = []
    folder = run_spread(p, base, on_progress=lambda k, n, row: seen.append((k, n)))
    rows = read_table(folder)
    meta = json.loads((folder / "spread.json").read_text(encoding="utf-8"))
    assert len(rows) == 3 and meta["sentence"].startswith("picked up ")
    assert seen == [(1, 3), (2, 3), (3, 3)]
    assert folder.name.startswith("spread-") and store.list_spreads(base)[0]["id"] == folder.name
    assert list(rows[0]) == ["placement", "offset_in", "distance_in", "angle_deg", "rotation_deg", "tip_deg",
                             "status", "picked up", "outcome"]
    assert [r["placement"] for r in rows] == ["3 in right", "on the center line", "3 in left"]
    assert rows[1]["picked up"] == "yes" and rows[1]["distance_in"] == "59.06", rows[1]     # the table rounds
    assert meta["picked"][1] is True and meta["spread"] == {"offset_in": [-3.0, 0.0, 3.0]}
    assert [q["offset_in"] for q in meta["placements"]] == [-3.0, 0.0, 3.0]
    for k in (1, 2, 3):
        run = store.load_result(base, f"{folder.name}/{k:03d}")
        assert run["feeds"][0]["source"] == "floor"
    assert {e["id"] for e in store.list_runs(base)} == {f"{folder.name}/{k:03d}" for k in (1, 2, 3)}


def test_a_far_off_center_ball_is_missed_and_named(tmp_path):
    folder = run_spread(load(spread_test(tmp_path, "offset_in = [0, 30]")), tmp_path / "runs")
    sentence = json.loads((folder / "spread.json").read_text(encoding="utf-8"))["sentence"]
    assert sentence == "picked up 1 of 2; missed at 30 in left", sentence
    assert [r["picked up"] for r in read_table(folder)] == ["yes", "no"]


def test_only_three_spreads_are_kept(tmp_path):
    p = load(spread_test(tmp_path, "offset_in = [0]"))
    base = tmp_path / "runs"
    for _ in range(KEEP_SPREADS + 1):
        run_spread(p, base)
    assert sorted(x.name for x in base.iterdir()) == [f"spread-{i:03d}" for i in range(2, KEEP_SPREADS + 2)]
    stop = threading.Event()
    stop.set()
    out = run_spread(p, base, cancel=stop)                  # a stopped spread is kept and prunes nothing
    meta = json.loads((out / "spread.json").read_text(encoding="utf-8"))
    assert meta["cancelled"] is True and read_table(out)[0]["status"] == "cancelled"
    assert sorted(x.name for x in base.iterdir()) == [f"spread-{i:03d}" for i in range(2, KEEP_SPREADS + 3)]
    assert store.list_spreads(base)[0]["cancelled"] is True


def test_a_spread_needs_a_floor_spread_and_a_ball(tmp_path):
    with pytest.raises(ValueError, match="no floor spread"):
        run_spread(load(spread_test(tmp_path)), tmp_path / "runs")
    path = tmp_path / "intake" / "intake.test.toml"
    path.write_text(path.read_text(encoding="utf-8") + "\n[setup.floor_spread]\noffset_in = [0]\n", encoding="utf-8")
    robot = tmp_path / "intake" / "intake-standin.toml"
    robot.write_text(robot.read_text(encoding="utf-8").replace('[range]\nfile = "intake"\nstart = "facing_ball"',
                                                               '[range]\nfile = "station"\nstart = "at_station"'),
                     encoding="utf-8")
    with pytest.raises(ValueError, match="needs a ball on the floor in front of the robot's start"):
        run_spread(load(path), tmp_path / "runs")


def test_cli_spread_exit_codes(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))

    def cli(*args) -> int:
        with pytest.raises(SystemExit) as e:
            main(["test", "spread", *map(str, args)])
        return e.value.code

    good = spread_test(tmp_path / "a", "offset_in = [0]")
    assert cli(good, "--out", tmp_path / "out") == 0
    out = capsys.readouterr().out
    assert out.startswith("picked up 1 of 1\n") and "  on the center line: picked up (" in out, out
    assert f"kept in {tmp_path / 'out' / 'spread-001'}" in out
    bad = spread_test(tmp_path / "b", "offset_in = [30]")
    assert cli(bad) == 1
    out = capsys.readouterr().out
    assert out.startswith("picked up 0 of 1; missed at 30 in left") and "  30 in left: missed (waiting for" in out, out
    assert (bad.parent / "intake-runs" / "spread-001" / "table.csv").is_file()
    assert cli(tmp_path / "nope.test.toml") == 2
    assert "test file not found" in capsys.readouterr().err


def test_the_intake_picks_up_a_ball_within_half_an_inch_of_its_place(tmp_path):
    """The example's margin: a 3 x 3 grid, +-0.5 in across and along the path around the field's ball, all picked
    up (at 8 V on the roller a ball could rattle back over the lip, so the run ended "bounced out" at scattered
    placements; examples/blocks/intake/README.md)."""
    d = 1.5 / IN
    p = load(spread_test(tmp_path, f"offset_in = [-0.5, 0, 0.5]\ndistance_in = [{d - 0.5}, {d}, {d + 0.5}]"))
    folder = run_spread(p, tmp_path / "runs")
    rows = read_table(folder)
    assert len(rows) == 9 and all(r["picked up"] == "yes" for r in rows), rows
    assert json.loads((folder / "spread.json").read_text(encoding="utf-8"))["sentence"] == "picked up 9 of 9"


CORAL_RANGE = """
[range]
name = "A coral to pick up"
ball = "2026-fuel"

[piece.coral]
preset = "2025-coral"

[[floor_balls]]
at = [[3.5, 4.0]]
kind = "coral"
tip_deg = 0
rotation_deg = 90

[start.facing_ball]
pose = [2.0, 4.0, 0.0]
"""


def coral_spread_test(folder: Path, spread: str = "") -> Path:
    """spread_test's intake on a field with a coral lying across 1.5 m in front of the start, in place of the ball."""
    path = spread_test(folder, spread)
    (path.parent / "coral.toml").write_text(CORAL_RANGE, encoding="utf-8")
    robot = path.parent / "intake-standin.toml"
    robot.write_text(robot.read_text(encoding="utf-8").replace('file = "intake"', 'file = "coral.toml"'),
                     encoding="utf-8")
    return path


def test_the_spread_piece_is_a_coral_with_its_own_tip_and_turn(tmp_path):
    world = world_for(load(coral_spread_test(tmp_path, "tip_deg = [90]")))
    i, where = spread_ball(world)
    assert i == 0 and where == {"offset_in": 0.0, "distance_in": pytest.approx(1.5 / IN), "tip_deg": 0.0,
                                "rotation_deg": 90.0}


def test_a_placement_stands_a_coral_on_end(tmp_path):
    program = load(coral_spread_test(tmp_path, "tip_deg = [90]"))
    world = world_for(program)
    kind = world.test_range.range.kinds["coral"]
    p = {"offset_in": 0.0, "distance_in": 40.0, "angle_deg": 0.0, "rotation_deg": 0.0, "tip_deg": 90.0}
    r = TestRun(world, program, placement=p).run()
    pose = r.motion[0][1][r.movers.index("ball.0")]          # the first sample, after the settle
    assert pose[:2] == pytest.approx([2.0 + 40 * IN, 4.0], abs=0.005)
    from scrimmage.testrange.pieces import rotate
    assert abs(rotate(pose[3:], kind.axis)[2]) == pytest.approx(1.0, abs=1e-3)       # its axis vertical
    assert pose[2] == pytest.approx(kind.rest_height(kind.orient(90.0, 0.0)), abs=0.006)


def test_a_coral_lying_in_line_is_turned_with_the_robot(tmp_path):
    program = load(coral_spread_test(tmp_path, "angle_deg = [30]"))
    world = world_for(program)
    kind = world.test_range.range.kinds["coral"]
    p = {"offset_in": 0.0, "distance_in": 40.0, "angle_deg": 30.0, "rotation_deg": 0.0, "tip_deg": 0.0}
    r = TestRun(world, program, placement=p).run()
    pose = r.motion[0][1][r.movers.index("ball.0")]
    from scrimmage.testrange.pieces import rotate
    axis = rotate(pose[3:], kind.axis)
    assert abs(axis[2]) < 1e-2 and abs(math.degrees(math.atan2(axis[1], axis[0])) % 180 - 30) < 1.0


def test_a_coral_lying_in_line_reaches_back_into_the_robot(tmp_path):
    """30 in ahead, a coral lying across clears the intake's roller and hopper; lying in line, its end reaches back
    into the hopper's floor, and the nearest distance offered counts its half length (0.15 m), not its radius."""
    from scrimmage.blocks.spread import floor_ball, inside_robot
    world = world_for(load(coral_spread_test(tmp_path, "tip_deg = [90]")))
    assert floor_ball(world)["nearest_in"] == 36                                # lying across, as the field has it
    assert inside_robot(world, {"distance_in": [30], "rotation_deg": [90]}) is None
    why = inside_robot(world, {"distance_in": [30], "rotation_deg": [0]})
    assert why == "the floor spread puts the coral inside the robot at 30 in ahead: start the distance at 39 in or more"


def test_a_coral_through_the_hopper_floor_is_refused(tmp_path):
    """23 in ahead lying in line, the coral's ends clear every robot geom but its middle passes through the
    hopper's floor: refused, whatever its offset across the hopper."""
    from scrimmage.blocks.spread import inside_robot
    world = world_for(load(coral_spread_test(tmp_path, "tip_deg = [90]")))
    for off in (-9.5, 0, 9.5):
        why = inside_robot(world, {"distance_in": [23], "offset_in": [off], "rotation_deg": [0]})
        assert why is not None and why.startswith("the floor spread puts the coral inside the robot at 23 in"), why


def test_a_coral_spread_names_its_misses_by_how_they_lay(tmp_path):
    folder = run_spread(load(coral_spread_test(tmp_path, "offset_in = [40]\ntip_deg = [0, 90]")), tmp_path / "runs")
    meta = json.loads((folder / "spread.json").read_text(encoding="utf-8"))
    assert meta["sentence"] == "picked up 0 of 2; missed lying across and standing on end", meta["sentence"]
    assert [r["tip_deg"] for r in read_table(folder)] == ["0", "90"]
    for k in (1, 2):                    # each missed lying on the floor, as it lay: its lowest point on the carpet
        assert store.load_result(tmp_path / "runs", f"{folder.name}/{k:03d}")["feeds"][0]["why"] == "on the floor"
