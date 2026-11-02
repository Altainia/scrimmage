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
from scrimmage.blocks.spread import label, placements, run_spread, sentence, spread_ball
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
    assert all(list(p) == ["offset_in", "distance_in", "angle_deg", "rotation_deg"] for p in ps)
    (only,) = placements({"distance_in": [40]}, {"offset_in": 2.5, "distance_in": 59.06})
    assert only == {"offset_in": 2.5, "distance_in": 40, "angle_deg": 0, "rotation_deg": 0}


def test_label():
    p = {"offset_in": 6, "distance_in": 40, "angle_deg": 30, "rotation_deg": 45}
    assert label(p, ["offset_in", "angle_deg"]) == "6 in left at 30°"
    assert label({**p, "offset_in": -6}, ["angle_deg", "offset_in"]) == "6 in right at 30°"     # in axis order
    assert label({**p, "offset_in": 0}, ["offset_in"]) == "on the center line"
    assert label(p, ["distance_in", "rotation_deg"]) == "40 in ahead turned 45°"


def test_sentence():
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


def test_a_placement_moves_the_ball_and_turns_the_robot(tmp_path):
    program = load(spread_test(tmp_path, "offset_in = [6]"))
    world = world_for(program)
    p = {"offset_in": 6.0, "distance_in": 40.0, "angle_deg": 30.0, "rotation_deg": 45.0}
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
    assert list(rows[0]) == ["placement", "offset_in", "distance_in", "angle_deg", "rotation_deg", "status",
                             "picked up", "outcome"]
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
