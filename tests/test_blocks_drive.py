"""Driving and turning a swerve robot and a tank drive with blocks (scrimmage/blocks/drive.py, run.py)."""

from __future__ import annotations

import math
import re
import tomllib

import pytest

from scrimmage.assembler.templates import new_config
from scrimmage.blocks.drive import drivetrain
from scrimmage.blocks.palette import palette, problems
from scrimmage.blocks.program import loads
from scrimmage.blocks.run import TestRun
from scrimmage.hal_drive import driven_joints
from scrimmage.world import World

START = [3.0, 3.0, 30.0]     # on the plain field, turned so "forward" isn't the field's +x


def world_of(template: str, folder, start=START) -> World:
    cfg = tomllib.loads(new_config(template))
    cfg["robot"]["start_pose"] = start
    return World(cfg, folder)


def run(world, blocks: str):
    program = loads(f'[test]\nrobot = "robot.toml"\n{blocks}', "t.test.toml")
    return TestRun(world, program).run()


def moved(world, r):
    """(forward, left, yaw in degrees) of the robot from the run's start to its end, in its start frame."""
    i = r.movers.index(world.model.body(world.robot_body).name)
    start, end = r.motion[0][1][i], r.motion[-1][1][i]
    h0 = 2 * math.atan2(float(start[6]), float(start[3]))
    h1 = 2 * math.atan2(float(end[6]), float(end[3]))
    dx, dy = end[:2] - start[:2]
    c, s = math.cos(h0), math.sin(h0)
    yaw = math.degrees((h1 - h0 + math.pi) % (2 * math.pi) - math.pi)
    return c * dx + s * dy, -s * dx + c * dy, yaw


@pytest.fixture(scope="module")
def swerve_world(tmp_path_factory):
    return world_of("swerve", tmp_path_factory.mktemp("swerve"))


@pytest.fixture(scope="module")
def standin_world(tmp_path_factory):
    return world_of("standin", tmp_path_factory.mktemp("standin"))


def test_a_swerve_robot_is_swerve_and_a_standin_is_tank(swerve_world, standin_world):
    dt = drivetrain(swerve_world, driven_joints(swerve_world))
    assert dt.kind == "swerve" and len(dt.modules) == 4
    assert sorted(dt.wheels) == ["bl.drive", "br.drive", "fl.drive", "fr.drive"]
    assert {mod.steer for mod in dt.modules} == {"bl.steer", "br.steer", "fl.steer", "fr.steer"}
    fl = next(mod for mod in dt.modules if mod.steer == "fl.steer")
    assert fl.wheel == "fl.drive" and fl.pos[0] > 0.1 and fl.pos[1] > 0.1
    assert palette(swerve_world, [])["drive_kind"] == "swerve" and palette(swerve_world, [])["drive"] is True
    tank = drivetrain(standin_world, driven_joints(standin_world))
    assert tank.kind == "tank" and tank.modules == [] and sorted(tank.wheels) == ["dt.left_2", "dt.right_2"]
    assert palette(standin_world, [])["drive_kind"] == "tank"


def test_a_robot_without_wheel_motors_has_no_drivetrain(tmp_path):
    w = World({"part": [{"name": "drive", "stock": "chassis"}]}, tmp_path)
    assert drivetrain(w, driven_joints(w)) is None


def test_steer_targets_point_the_modules(swerve_world):
    dt = drivetrain(swerve_world, driven_joints(swerve_world))
    assert all(abs(q) < 1e-6 for q in dt.steer_targets("forward").values())
    left = dt.steer_targets("left")
    assert all(abs(abs(q) - math.pi / 2) < 1e-6 for q in left.values())
    turn = dt.steer_targets(None, turn=1.0)
    assert all(abs(abs(q) - math.pi / 4) < 1e-6 for q in turn.values())    # square modules: 45° each


def test_swerve_drive_left_goes_left(swerve_world):
    r = run(swerve_world, '[[block]]\ndo = "drive"\ndirection = "left"\ndistance = 0.5\n')
    assert r.status == "finished", r.outcome
    dx, dy, dyaw = moved(swerve_world, r)
    assert dy == pytest.approx(0.5, abs=0.08) and abs(dx) < 0.1 and abs(dyaw) < 5


def test_swerve_drive_back_goes_back(swerve_world):
    r = run(swerve_world, '[[block]]\ndo = "drive"\ndirection = "back"\ndistance = 0.5\n')
    assert r.status == "finished", r.outcome
    dx, dy, dyaw = moved(swerve_world, r)
    assert dx < -0.25 and abs(dy) < 0.1


def test_swerve_turn_90_turns_left(swerve_world):
    r = run(swerve_world, '[[block]]\ndo = "turn"\ndegrees = 90\n')
    assert r.status == "finished", r.outcome
    _, _, dyaw = moved(swerve_world, r)
    assert dyaw == pytest.approx(90, abs=10)


def test_tank_turn_right(standin_world):
    r = run(standin_world, '[[block]]\ndo = "turn"\ndegrees = -90\n')
    assert r.status == "finished", r.outcome
    _, _, dyaw = moved(standin_world, r)
    assert dyaw == pytest.approx(-90, abs=10)


def test_tank_drive_back(standin_world):
    r = run(standin_world, '[[block]]\ndo = "drive"\ndirection = "back"\ndistance = 0.4\n')
    assert r.status == "finished", r.outcome
    dx, _, _ = moved(standin_world, r)
    assert dx < -0.3


def test_a_turn_that_cant_finish_says_how_far_it_got(tmp_path):
    """In the plain field's corner, its walls 2 cm off two sides, the square robot can't turn: its corners hit them."""
    w = world_of("swerve", tmp_path, start=[0.45, 0.45, 0.0])
    r = run(w, '[[block]]\ndo = "turn"\ndegrees = 90\ntimeout = 1\n')
    assert r.status == "timed out" and "didn't turn" in r.outcome and "turned" in r.outcome, r.outcome
    assert re.fullmatch(r"turn \(block 1\) didn't turn 90° in 1 s \(turned -?\d+°\)", r.outcome), r.outcome


def test_a_drive_that_cant_finish_names_its_direction(swerve_world):
    r = run(swerve_world, '[[block]]\ndo = "drive"\ndirection = "left"\ndistance = 5.0\ntimeout = 1\n')
    assert r.status == "timed out", r.outcome
    assert r.outcome.startswith("drive left (block 1) didn't reach 5 m in 1 s (went "), r.outcome


def test_a_swerve_drive_is_no_problem_but_a_tank_drive_left_is(swerve_world, standin_world):
    left = loads('[test]\nrobot = "r.toml"\n[[block]]\ndo = "drive"\ndirection = "left"\ndistance = 1\n',
                 "t.test.toml")
    assert problems(left, swerve_world) == []
    assert [p["message"] for p in problems(left, standin_world)] == [
        "a tank drive can't drive sideways; turn, then drive forward"]


def test_a_swerve_drive_for_seconds_counts_from_when_the_wheels_start(swerve_world):
    """Driving left first turns the modules 90° (wheels at 0 V), so the block ends that much later than driving
    forward, and goes about as far."""
    block = '[[block]]\ndo = "drive"\ndirection = "{}"\nseconds = 0.5\n'
    fwd, left = run(swerve_world, block.format("forward")), run(swerve_world, block.format("left"))
    assert fwd.status == left.status == "finished", (fwd.outcome, left.outcome)
    assert left.t_end - fwd.t_end > 0.03
    assert moved(swerve_world, left)[1] == pytest.approx(moved(swerve_world, fwd)[0], rel=0.15)


def test_wheels_that_start_before_the_modules_are_pointed_are_signed_every_step(swerve_world, monkeypatch):
    """Past STEER_TIMEOUT the wheels start though a module is still turning; each step until it's pointed, its wheel's
    sign follows where it rolls then (one signing as the wheels start would keep a wrong sign)."""
    import scrimmage.blocks.run as R
    from scrimmage.blocks.drive import Drivetrain
    calls = []
    signed = Drivetrain.wheel_volts
    monkeypatch.setattr(R, "STEER_TIMEOUT", 0.0)
    monkeypatch.setattr(Drivetrain, "steered", lambda self, data: False)      # never pointed: signed every step
    monkeypatch.setattr(Drivetrain, "wheel_volts", lambda self, *a: calls.append(1) or signed(self, *a))
    r = run(swerve_world, '[[block]]\ndo = "drive"\ndirection = "left"\nseconds = 0.2\n')
    assert r.status == "finished", r.outcome
    assert len(calls) > 20                  # 0.2 s of steps, not once
    monkeypatch.undo()
    calls.clear()
    monkeypatch.setattr(Drivetrain, "wheel_volts", lambda self, *a: calls.append(1) or signed(self, *a))
    assert run(swerve_world, '[[block]]\ndo = "drive"\ndirection = "forward"\nseconds = 0.2\n').status == "finished"
    assert len(calls) == 1                  # pointed already: signed once


def test_the_drivetrain_rest_pose_is_worked_out_once_per_world(tmp_path, monkeypatch):
    import sys
    import scrimmage.blocks.drive as D
    w = world_of("swerve", tmp_path)
    made = []                               # the scratch MjDatas drive.py makes
    scratch = D.mujoco.MjData

    def counted(m):
        if sys._getframe(1).f_globals["__name__"] == D.__name__:
            made.append(1)
        return scratch(m)

    monkeypatch.setattr(D.mujoco, "MjData", counted)
    palette(w, [])
    run(w, '[[block]]\ndo = "drive"\nseconds = 0.1\n')        # its problems, its palette and its drivetrain
    assert drivetrain(w, driven_joints(w)).kind == "swerve"
    assert len(made) == 1
