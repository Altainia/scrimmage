"""What a robot offers the block editor, and the problems a test's blocks have with it (scrimmage/blocks/palette.py)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from range_fixtures import range_cfg
from scrimmage.assembler.templates import new_config
from scrimmage.blocks.palette import palette, problems
from scrimmage.blocks.program import loads
from scrimmage.world import World

HEAD = '[test]\nrobot = "r.toml"\n'
PATH = Path("/tmp/x.test.toml")
CLIMBER = {"name": "climber", "stock": "climber", "attach": "drive.top", "ids": {"extend": "PWM:2"}}


def standin(*parts) -> World:
    cfg = tomllib.loads(new_config("standin"))
    cfg["part"] += [dict(p) for p in parts]
    return World(cfg, None)


@pytest.fixture(scope="module")
def climber_world():
    return standin(CLIMBER)


def test_palette_of_the_standin_with_a_climber(climber_world):
    p = palette(climber_world, [])
    assert p["motors"] == [{"name": "climber.stage1", "unit": "in", "kind": "slide"},
                           {"name": "dt.left_2", "unit": "deg", "kind": "wheel"},
                           {"name": "dt.right_2", "unit": "deg", "kind": "wheel"}]
    assert p["drive"] is True
    joints = {j["name"]: j for j in p["joints"]}
    assert joints["climber.tip"] == {"name": "climber.tip", "unit": "in", "kind": "slide"}
    assert joints["climber.latch0"]["kind"] == "hinge" and joints["dt.left_1"]["kind"] == "wheel"
    assert [j["name"] for j in p["joints"]] == sorted(joints)
    assert p["parts"] == ["climber", "drive", "dt"]
    assert "drive.chassis" in p["bodies"] and "climber.tip" in p["bodies"] and p["bodies"] == sorted(p["bodies"])
    assert p["hooks"] == ["climber.hook0"]
    assert p["bars"] == p["elements"] == p["pieces"] == p["levels"] == []
    assert p["signals"] == ["tilt", "climber.stage1", "dt.left_2", "dt.right_2", "climber.stage1.current",
                            "dt.left_2.current", "dt.right_2.current", "hook_load"]


def test_palette_of_a_robot_without_a_drivetrain_or_hooks(tmp_path):
    w = World({"part": [{"name": "drive", "stock": "chassis"}]}, tmp_path)
    p = palette(w, [])
    assert p["motors"] == [] and p["drive"] is False and p["hooks"] == []
    assert p["signals"] == ["tilt"]


def test_palette_of_the_ladder():
    w = World(range_cfg(range={"file": "ladder", "start": "below"}), None)
    p = palette(w, ["drive.chassis"])
    assert p["bars"] == ["rung1", "rung2", "rung3", "rung4"]
    assert p["elements"] == ["rail_left", "rail_right", "rung1", "rung2", "rung3", "rung4"]
    assert p["hooks"] == ["drive.chassis"] and p["signals"][-1] == "hook_load"
    assert p["levels"] == []


def test_palette_levels_on_a_range_that_scores_climbs():
    w = World(range_cfg(range={"file": "2022-hangar", "start": None}), None)
    assert palette(w, [])["levels"] == list(w.parts.range.climb.levels)
    assert palette(w, [])["levels"]


RENAMED = HEAD + '''
[[block]]
do = "spin"
motor = "climber.stage1"
volts = 6.0

[[block]]
do = "wait"
seconds = 1.0

[[block]]
do = "repeat"
times = 2

  [[block.body]]
  do = "spin"
  motor = "climber.crank"
  volts = 6.0

  [[block.body]]
  do = "stop"
  motor = "all"
'''


def test_a_renamed_joint_is_one_problem_at_its_place(climber_world):
    """Review Focus 1: a test written before the robot changed."""
    assert problems(loads(RENAMED, PATH), climber_world) == [
        {"place": [2, 0], "message": 'no joint "climber.crank" on this robot any more'}]


def test_a_program_that_fits_has_no_problems(climber_world):
    text = RENAMED.replace("climber.crank", "climber.stage1") + '''
[[block]]
do = "move"
joint = "climber.stage1"
to = 10.0
unit = "in"

[[block]]
do = "drive"
seconds = 1.0

[[block]]
do = "expect"
that = { tilt = { below = 20.0 } }
throughout = true
'''
    assert problems(loads(text, PATH), climber_world) == []


def _one(text, world):
    return [p["message"] for p in problems(loads(HEAD + text, PATH), world)]


def test_problem_messages(climber_world, tmp_path):
    w = climber_world
    assert _one('[[block]]\ndo = "spin"\nmotor = "climber.tip"\nvolts = 3.0\n', w) == [
        '"climber.tip" has no motor to move it']
    assert _one('[[block]]\ndo = "move"\njoint = "climber.stage1"\nto = 3.0\nunit = "deg"\n', w) == [
        '"climber.stage1" slides, so give it in inches or meters, not degrees']
    assert _one('[[block]]\ndo = "wait_until"\nuntil = { joint = { name = "climber.latch0", above = 1.0, '
                'unit = "in" } }\n', w) == ['"climber.latch0" turns, so give it in degrees']
    assert _one('[[block]]\ndo = "wait_until"\nuntil = { hanging_from = "rung5" }\n', w) == [
        'no bar "rung5" on this field']
    assert _one('[[block]]\ndo = "expect"\nthat = { level = "HIGH" }\n', w) == [
        "levels need a field that scores climbs"]
    assert _one('[[block]]\ndo = "wait_until"\nuntil = { touch = { robot = "arm", field = "nowhere" } }\n', w) == [
        'no part or body "arm" on this robot any more', 'nothing called "nowhere" on this field']
    assert _one('[[block]]\ndo = "wait_until"\nuntil = { piece_in = { body = "intake.hopper" } }\n', w) == [
        'no body "intake.hopper" on this robot any more', "this field has no game pieces"]
    bare = World({"part": [{"name": "drive", "stock": "chassis"}]}, tmp_path)
    assert _one('[[block]]\ndo = "drive"\ndistance = 1.0\n', bare) == [
        "drive needs a drivetrain; this robot has none"]
    assert _one('[[block]]\ndo = "wait_until"\nuntil = { touch = { robot = "hook", field = "next bar" } }\n',
                bare) == ["this robot has no hooks; say which bodies are hooks in the test's setup",
                          '"next bar" needs a field with bars']


def test_a_hook_that_is_no_body_is_a_setup_problem(climber_world):
    text = HEAD.replace('robot = "r.toml"\n', 'robot = "r.toml"\nhooks = ["arm.hook_A"]\n')
    assert problems(loads(text, PATH), climber_world) == [
        {"place": [], "message": 'no body "arm.hook_A" on this robot any more (the test lists it as a hook)'}]
