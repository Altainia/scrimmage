"""Shooting under the Test tab (sub-project 23, goal 2): configs/fastmode_range.toml's robot, its robot program
dropped, spins its launcher and stages from the blocks; the `scored` condition reads the range's goal counts."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scrimmage.blocks.conditions import describe
from scrimmage.blocks.palette import palette, problems
from scrimmage.blocks.program import Cond, TestFileError, load, loads
from scrimmage.blocks.run import TestRun, goal_lines, world_for

ROOT = Path(__file__).resolve().parent.parent
STATION = ROOT / "examples" / "blocks" / "station" / "feed.test.toml"

# The launcher's hood joint reads 0 at its 40 deg stop; 40 is its other stop, an 80 deg exit. Found by a quick sweep
# (0 to 40 in steps of 5, then 36 to 40 by 1): from blue_center, 1.07 m short of hub_blue, only 35 and up score, and 40
# scores all 8 preloaded balls (the first two at 2.2 s on the run's clock).
HOOD_DEG = 40.0

# The robot's launcher motor is the joint `launcher.spin` (its slot is `shooter`). The shots fly about 1.4 s at this
# steep angle, so the check comes 2 s after the stages start.
SHOOT = f"""
[[block]]
do = "move"
joint = "launcher.hood"
to = {HOOD_DEG}
unit = "deg"

[[block]]
do = "spin"
motor = "launcher.spin"
volts = 11.0

[[block]]
do = "spin"
motor = "hopper_floor.spin"
volts = 8.0

[[block]]
do = "spin"
motor = "feeder.spin"
volts = 8.0

[[block]]
do = "wait"
seconds = 2.0

[[block]]
do = "expect"
that = {{ scored = {{ goal = "any", at_least = 1 }} }}
"""


def _robot(tmp: Path) -> Path:
    """configs/fastmode_range.toml with its [robot_program] dropped and [range] start = "blue_center" (as it has):
    its fast-mode `SimMotor:` ids bind with no robot program, so they stay."""
    text = (ROOT / "configs" / "fastmode_range.toml").read_text(encoding="utf-8")
    text, n = re.subn(r"\n\[robot_program\]\n.*?(?=\n# ---- Drive)", "\n", text, flags=re.S)
    assert n == 1
    assert '\nstart = "blue_center"\n' in text
    (tmp / "shooter.toml").write_text(text, encoding="utf-8")
    return tmp / "shooter.toml"


def _program(tmp: Path, blocks: str):
    _robot(tmp)
    return loads('[test]\nrobot = "shooter.toml"\n' + blocks, tmp / "shoot.test.toml")


@pytest.fixture(scope="module")
def shot(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("shooting")
    program = _program(tmp, SHOOT)
    world = world_for(program)
    return {"program": program, "world": world, "result": TestRun(world, program).run()}


def test_spinning_the_launcher_scores(shot):
    r = shot["result"]
    assert r.status == "finished", r.outcome
    assert r.passed, r.checks
    assert r.checks[-1]["line"].startswith("passed: 1 or more balls are scored in any goal at ")
    assert set(r.goals) == {"hub_blue", "hub_red"}
    assert sum(r.goals.values()) >= 1
    assert r.goals == shot["world"].test_range.counts()["goals"]


def test_the_palette_lists_the_goals(shot):
    assert palette(shot["world"], [])["goals"] == ["hub_blue", "hub_red"]


def test_scored_names_a_goal(shot):
    p = _program(Path(shot["program"].path).parent,
                 '[[block]]\ndo = "expect"\nthat = { scored = { goal = "nowhere", at_least = 1 } }\n')
    assert problems(p, shot["world"]) == [
        {"place": [0], "message": "no goal 'nowhere' on this field; its goals: hub_blue, hub_red"}]
    named = _program(Path(shot["program"].path).parent,
                     '[[block]]\ndo = "expect"\nthat = { scored = { goal = "hub_red", at_least = 1 } }\n')
    assert problems(named, shot["world"]) == []


def test_scored_file_shape(tmp_path):
    head = '[test]\nrobot = "r.toml"\n[[block]]\ndo = "expect"\n'
    p = loads(head + 'that = { scored = { goal = "hub_blue", at_least = 5 } }\n', tmp_path / "x.test.toml")
    assert p.blocks[0].args["that"] == Cond("scored", {"goal": "hub_blue", "at_least": 5})
    for bad in ('{ goal = "hub_blue", at_least = 0 }', '{ goal = "hub_blue", at_least = 1.5 }',
                '{ goal = "hub_blue" }', '{ at_least = 1 }', '{ goal = "hub_blue", at_least = 1, lane = 2 }'):
        with pytest.raises(TestFileError):
            loads(head + f"that = {{ scored = {bad} }}\n", tmp_path / "x.test.toml")


def test_scored_words():
    assert describe(Cond("scored", {"goal": "hub_blue", "at_least": 5})) == "5 or more balls are scored in hub_blue"
    assert describe(Cond("scored", {"goal": "any", "at_least": 1})) == "1 or more balls are scored in any goal"


def test_no_shooter_no_goals():
    program = load(STATION)
    r = TestRun(world_for(program), program).run()
    assert r.goals is None


def test_the_run_lines_say_the_goals(shot):
    r = shot["result"]
    assert goal_lines(r) == [f"scored {n} in {g}" for g, n in r.goals.items() if n]
    assert f"scored {r.goals['hub_blue']} in hub_blue" in goal_lines(r)


def test_a_failed_scored_check_says_the_count(shot):
    p = _program(Path(shot["program"].path).parent,
                 '[[block]]\ndo = "expect"\nthat = { scored = { goal = "hub_red", at_least = 2 } }\n')
    r = TestRun(shot["world"], p).run()
    assert r.status == "check failed"
    assert "expected 2 or more balls scored in hub_red, but 0 were" in r.outcome
