"""The ball intake (examples/blocks/intake): a hand-written part on the stand-in robot, a roller and a bin, tested with
blocks on a range with one ball on the carpet. It is the second worked example, there to show the Test tab isn't shaped
around climbing: the roller's contact alone pulls the ball over the bin's lip (the robot has no [shooter], so nothing
moves a ball but physics), and with the roller off the robot just pushes the ball ahead of it."""

from __future__ import annotations

import csv
import json
import tomllib
from pathlib import Path

import numpy as np
import pytest

from scrimmage.__main__ import main
from scrimmage.blocks import store
from scrimmage.blocks.palette import problems
from scrimmage.blocks.program import load
from scrimmage.blocks.run import TestRun, world_for
from scrimmage.blocks.spread import run_spread
from scrimmage.blocks.sweep import run_sweep
from scrimmage.testrange.spec import BUILTIN_RANGES, load_range

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "blocks" / "intake"
BALL_R = 0.075        # m: the 2026 FUEL's radius
FLOOR_TOP = 0.04      # m: the bin's floor top above the carpet (parts/intake/part.toml)
LIP_X = 0.21          # m: the back of the bin's lip, forward of the bumper's face (parts/intake/part.toml)
WALL_Y = 0.18         # m: the inside of the bin's side walls, either side of the center line


@pytest.fixture(scope="module")
def intake():
    program = load(EXAMPLE / "intake.test.toml")
    world = world_for(program)
    run = TestRun(world, program)
    result = run.run()
    return {"program": program, "world": world, "result": result}


def _ball_in_bin(world) -> np.ndarray:
    """The (only) ball's center in the intake's frame: x forward of the bumper's face, y left, z up from the carpet."""
    m, d = world.model, world.data
    (name,) = world.parts.layout.ball_names
    ball, frame = m.body(name).id, m.body("intake.frame").id
    return d.xmat[frame].reshape(3, 3).T @ (d.xpos[ball] - d.xpos[frame])


def test_the_range_has_one_ball_in_front_of_the_start():
    assert BUILTIN_RANGES["intake"].is_file()
    rng = load_range("intake", EXAMPLE)
    (start,) = rng.starts.values()
    (ball,) = rng.floor_balls
    assert start[2] == 0.0 and ball[1] == start[1] and ball[0] > start[0] + 1.0   # straight ahead, facing it


def test_the_robot_moves_balls_by_physics_only(intake):
    raw = tomllib.loads((EXAMPLE / "intake-standin.toml").read_text(encoding="utf-8"))
    assert "shooter" not in raw                          # no intake zone that takes balls out of the world
    assert raw["range"] == {"file": "intake", "start": "facing_ball"}
    assert problems(intake["program"], intake["world"]) == []


def test_intake_takes_a_ball(intake):
    r = intake["result"]
    assert r.status == "finished", r.outcome
    assert r.outcome == "1 of 1 made it in (100%)"      # the hopper names a body, so the floor ball is counted
    (check,) = r.checks
    assert check["passed"] and check["text"] == "a ball reaches intake.hopper", check
    x, y, z = _ball_in_bin(intake["world"])
    # behind the lip, between the walls, resting on the bin's floor (a second after the robot stopped)
    assert BALL_R - 0.01 < x < LIP_X and abs(y) < WALL_Y, (x, y, z)
    assert z == pytest.approx(FLOOR_TOP + BALL_R, abs=0.01), (x, y, z)
    assert max(r.signals["intake.roller"]) > 0.0         # the roller turned the way that pulls a ball in


def test_the_run_is_kept_for_the_replay(intake, tmp_path):
    r = intake["result"]
    folder = store.save_run(r, intake["program"], tmp_path / "intake.test-runs")
    run = store.load_run(tmp_path / "intake.test-runs", folder.name)
    assert run["status"] == "finished" and run["view"]["geoms"]
    assert "intake.roller" in r.movers and run["signals"]["intake.roller"]


def test_a_roller_volts_sweep_shows_the_roller_does_it(intake, tmp_path):
    """Try values… on the spin block: off, the robot pushes the ball away and the wait times out; at 6 V it's taken."""
    out = run_sweep(intake["program"], {"block": [0], "key": "volts"}, [0.0, 6.0], tmp_path / "runs")
    with open(out / "table.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [float(row["value"]) for row in rows] == [0.0, 6.0]
    assert [row["status"] for row in rows] == ["timed out", "finished"], rows
    assert rows[0]["outcome"].startswith("waiting for a ball to reach intake.hopper timed out at "), rows[0]


def test_scrimmage_test_run_on_the_example(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    with pytest.raises(SystemExit) as e:
        main(["test", "run", str(EXAMPLE / "intake.test.toml"), "--out", str(tmp_path / "runs")])
    out = capsys.readouterr().out
    assert e.value.code == 0, out
    assert out.startswith("1 of 1 made it in (100%)") and "passed: a ball reaches intake.hopper" in out
    assert json.loads((tmp_path / "runs" / "001" / "result.json").read_text(encoding="utf-8"))["status"] == "finished"


def test_the_intake_spread_picks_up_the_center_ball(tmp_path):
    """spread.test.toml: the ball 6 in right, on the center line and 6 in left. The center one is picked up; 6 in off
    center the ball's side meets the front of a bin wall and it wedges there (the README says why)."""
    folder = run_spread(load(EXAMPLE / "spread.test.toml"), tmp_path / "runs")
    with open(folder / "table.csv", newline="", encoding="utf-8") as f:
        rows = {row["placement"]: row for row in csv.DictReader(f)}
    assert list(rows) == ["6 in right", "on the center line", "6 in left"]
    assert rows["on the center line"]["picked up"] == "yes", rows
    # only the center ball: a ball wedged on the bin outside its box is stuck, never "held" (ruling P26)
    assert json.loads((folder / "spread.json").read_text(encoding="utf-8"))["picked"] == [False, True, False]
