"""Aiming a turret at a goal under the Test tab (sub-project 23, goal 3): configs/fastmode_range.toml's robot (as
tests/test_blocks_shooting.py has it, its robot program dropped) with its launcher moved onto a stock turret."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from scrimmage.blocks.aim import aim_error, aim_target, turrets
from scrimmage.blocks.conditions import describe
from scrimmage.blocks.palette import palette, problems
from scrimmage.blocks.program import Cond, TestFileError, loads
from scrimmage.blocks.run import TestRun, world_for

from test_blocks_shooting import _robot

GOAL = "hub_blue"       # the hub blue_center faces

LAUNCHER = 'name = "launcher"\nstock = "launcher"\nattach = "drive.top"\npos = [-0.15, 0.0, 0.40]\n'


def _turret_robot(tmp: Path, *, turret_range=None, launcher_rot=None, bound=True) -> None:
    """The shooting robot with a turret on drive.top at the launcher's old pos, the launcher on turret.top."""
    path = _robot(tmp)
    text = path.read_text(encoding="utf-8")
    assert LAUNCHER in text
    turret = ('name = "turret"\nstock = "turret"\nattach = "drive.top"\npos = [-0.15, 0.0, 0.40]\n'
              + (f"range = {list(turret_range)}\n" if turret_range else "")
              + ('ids = { turn = "SimMotor:turret" }\n' if bound else "") + '\n[[part]]\n')
    launcher = 'name = "launcher"\nstock = "launcher"\nattach = "turret.top"\n' \
               + (f"rot = {list(launcher_rot)}\n" if launcher_rot else "")
    path.write_text(text.replace(LAUNCHER, turret + launcher), encoding="utf-8")


def _program(tmp: Path, blocks: str, **robot):
    _turret_robot(tmp, **robot)
    return loads('[test]\nrobot = "shooter.toml"\n' + blocks, tmp / "aim.test.toml")


def _aim(turret: str = "turret", goal: str = GOAL) -> str:
    return f'[[block]]\ndo = "aim"\nturret = "{turret}"\nat = "{goal}"\n'


def _expect(within: float | None = None, turret: str = "turret", goal: str = GOAL) -> str:
    w = f", within_deg = {within}" if within is not None else ""
    return f'[[block]]\ndo = "expect"\nthat = {{ aimed = {{ turret = "{turret}", at = "{goal}"{w} }} }}\n'


WAIT = '[[block]]\ndo = "wait"\nseconds = 1.0\n'


def _turn(deg: float) -> str:
    return f'[[block]]\ndo = "turn"\ndegrees = {deg}\n'


def test_aim_holds_while_turning(tmp_path):
    p = _program(tmp_path, _aim() + WAIT + _expect(3) + _turn(90) + _expect(5))
    world = world_for(p)
    r = TestRun(world, p).run()
    assert r.status == "finished", r.outcome
    assert r.passed, r.checks
    assert [c["passed"] for c in r.checks] == [True, True]
    assert r.checks[0]["line"].startswith("passed: the turret is aimed at hub_blue within 3° at ")
    assert len(r.signals["aim_error"]) == len(r.signals["t"])
    assert abs(r.signals["aim_error"][-1]) < 5
    q = world.data.qpos[world.model.joint("turret.turn").qposadr[0]]
    # the robot turned left 90 deg, so the turret turned right about as far (less the launcher's offset from the
    # robot's center, which sees the hub from a new place after the turn)
    assert -100 < math.degrees(q) < -60


def test_aim_out_of_range(tmp_path):
    # turned right 120 deg the hub is 120 deg to the left; the turret stops at 30
    p = _program(tmp_path, _turn(-120) + _aim() + WAIT + _expect(), turret_range=(-30, 30))
    world = world_for(p)
    r = TestRun(world, p).run()
    assert r.status == "check failed"
    assert r.checks[-1]["failure"] == "hub_blue is outside the turret's range (it stops at 30°)"
    target, ok = aim_target(world, "turret", GOAL)
    assert not ok and target == pytest.approx(math.radians(30))
    q = world.data.qpos[world.model.joint("turret.turn").qposadr[0]]
    assert math.degrees(q) == pytest.approx(30, abs=2)


def test_aim_names_checked(tmp_path):
    p = _program(tmp_path, _aim(turret="drive") + _expect(turret="drive"))
    world = world_for(p)
    assert palette(world, [])["turrets"] == ["turret"]
    assert turrets(world) == ["turret"]
    bad = "drive isn't a turret; the robot's turrets: turret"
    assert problems(p, world) == [{"place": [0], "message": bad}, {"place": [1], "message": bad}]
    q = _program(tmp_path, _aim(goal="nowhere") + _expect(goal="nowhere"))
    nowhere = "no goal 'nowhere' on this field; its goals: hub_blue, hub_red"
    assert problems(q, world) == [{"place": [0], "message": nowhere}, {"place": [1], "message": nowhere}]
    assert problems(_program(tmp_path, _aim() + _expect()), world) == []


def test_aim_needs_a_turret_motor(tmp_path):
    p = _program(tmp_path, _aim() + _expect(), bound=False)
    assert problems(p, world_for(p)) == [{"place": [0], "message": '"turret.turn" has no motor to move it'}]


def test_aim_error_uses_launcher(tmp_path):
    # the launcher faces the platform's +y: pointing it at the hub (straight ahead at the start) turns the platform
    # 90 deg right
    p = _program(tmp_path, _aim() + WAIT + _expect(2), launcher_rot=(0, 0, 90))
    world = world_for(p)
    r = TestRun(world, p).run()
    assert r.passed, r.outcome
    q = world.data.qpos[world.model.joint("turret.turn").qposadr[0]]
    assert math.degrees(q) == pytest.approx(-90, abs=3)
    assert abs(math.degrees(aim_error(world, "turret", GOAL))) < 2
    m, d = world.model, world.data
    platform_x = d.xmat[m.body("turret.platform").id].reshape(3, 3)[:2, 0]
    base = d.xpos[m.body("launcher.base").id][:2]
    to_goal = (1.0, 0.0)
    for g in world.parts.range.goals:
        if g.name == GOAL:
            to_goal = (g.pos[0] - base[0], g.pos[1] - base[1])
    # the platform's own x axis points well away from the goal
    off = math.degrees(abs(math.atan2(platform_x[0] * to_goal[1] - platform_x[1] * to_goal[0],
                                      platform_x[0] * to_goal[0] + platform_x[1] * to_goal[1])))
    assert off > 60


def test_aim_file_shape(tmp_path):
    head = '[test]\nrobot = "r.toml"\n'
    p = loads(head + _aim() + _expect() + _expect(5), tmp_path / "x.test.toml")
    assert p.blocks[0].do == "aim" and p.blocks[0].args == {"turret": "turret", "at": GOAL}
    assert p.blocks[1].args["that"] == Cond("aimed", {"turret": "turret", "at": GOAL, "within_deg": 2.0})
    assert p.blocks[2].args["that"].args["within_deg"] == 5.0
    for bad in ('{ turret = "turret" }', '{ at = "hub_blue" }', '{ turret = "turret", at = "hub_blue", '
                'within_deg = -1 }', '{ turret = "turret", at = "hub_blue", lane = 2 }'):
        with pytest.raises(TestFileError):
            loads(head + f'[[block]]\ndo = "expect"\nthat = {{ aimed = {bad} }}\n', tmp_path / "x.test.toml")
    with pytest.raises(TestFileError):
        loads(head + '[[block]]\ndo = "aim"\nturret = "turret"\n', tmp_path / "x.test.toml")


def test_aimed_words():
    assert describe(Cond("aimed", {"turret": "turret", "at": "blue_hub", "within_deg": 2.0})) == \
        "the turret is aimed at blue_hub within 2°"


def test_a_later_move_ends_the_aim(tmp_path):
    move = '[[block]]\ndo = "move"\njoint = "turret.turn"\nto = 0.0\nunit = "deg"\n'
    p = _program(tmp_path, _aim() + WAIT + move + _turn(90) + WAIT + _expect(5))
    world = world_for(p)
    r = TestRun(world, p).run()
    assert r.status == "check failed"
    assert "expected the turret aimed at hub_blue within 5°, but it was " in r.checks[-1]["failure"]
    q = world.data.qpos[world.model.joint("turret.turn").qposadr[0]]
    assert math.degrees(q) == pytest.approx(0, abs=2)       # the move's target holds, not the aim's
