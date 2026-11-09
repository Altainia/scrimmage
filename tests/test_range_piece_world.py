"""Every piece kind in the world: rigid pieces (a hollow coral, as slices) as free bodies beside balls, in the one
ledger; a goal counts the kind it names (by default the first ball or disc kind), the shooter and its ball path take
only their own kind."""

from __future__ import annotations

import math

import numpy as np
import pytest

from range_fixtures import range_cfg, shooter_cfg
from scrimmage.assembler.view import model_view
from scrimmage.protocol import HalState
from scrimmage.testrange.balls import AT_REST_SPEED, BallState
from scrimmage.testrange.build import BALL_BITS
from scrimmage.world import World

IN = 0.0254
CORAL_L = 11.875 * IN
CORAL_R = 4.5 * IN / 2

CORAL = '''
[range]
name = "coral"

[piece.coral]
preset = "2025-coral"

[[floor_balls]]
at = [[12.0, 2.0]]
tip_deg = {tip}
rotation_deg = {turn}
'''
MIXED = '''
[range]
name = "mixed"
ball = "{ball}"

[piece.coral]
preset = "2025-coral"

[[floor_balls]]
at = [[12.0, 6.0]]

[[floor_balls]]
at = [[{coral_x}, {coral_y}]]
kind = "coral"
tip_deg = 0
rotation_deg = 90

[[goal]]
name = "score"
pos = [12.0, 2.0]
rim_height = 1.0
opening = {{ diameter = 0.5 }}
'''


def _file(tmp_path, text: str) -> str:
    path = tmp_path / "field.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def coral_world(tmp_path, tip: float, turn: float = 0.0) -> World:
    return World(range_cfg(range={"file": _file(tmp_path, CORAL.format(tip=tip, turn=turn)), "floor_balls": True}),
                 None)


def mixed_world(tmp_path, ball="2025-algae", coral=(12.0, 4.0), **cfg) -> World:
    text = MIXED.format(ball=ball, coral_x=coral[0], coral_y=coral[1])
    make = cfg.pop("make", range_cfg)
    return World(make(range={"file": _file(tmp_path, text), "floor_balls": True, **cfg.pop("range", {})}, **cfg),
                 None)


def step(w, seconds: float, hal: HalState | None = None) -> None:
    hal = hal or HalState()
    for _ in range(int(round(seconds / w.model.opt.timestep))):
        w.step(hal)


def axis_tilt_deg(w, i: int) -> float:
    """The angle of piece i's long axis above the carpet."""
    b = w.test_range.balls
    kind = b.kinds[b.kind[i]]
    a = w.data.xmat[b.bodies[i]].reshape(3, 3) @ np.array(kind.axis)
    return math.degrees(math.asin(min(1.0, abs(float(a[2])))))


def test_a_coral_stands_on_its_end_and_stays(tmp_path):
    w = coral_world(tmp_path, 90)
    step(w, 2.0)
    z = float(w.test_range.balls.position(0)[2])
    assert z == pytest.approx(CORAL_L / 2, abs=0.003) and axis_tilt_deg(w, 0) > 88


def test_a_coral_on_its_side_rolls_when_pushed_and_stops(tmp_path):
    w = coral_world(tmp_path, 0)                # its axis along x
    b = w.test_range.balls
    step(w, 0.5)
    start = b.position(0).copy()
    assert start[2] == pytest.approx(CORAL_R, abs=0.003)
    w.data.qvel[b._v[0, 1]] = 0.5               # across its axis
    step(w, 6.0)
    moved = float(np.linalg.norm(b.position(0)[:2] - start[:2]))
    assert moved > 0.05 and float(np.linalg.norm(b.velocity(0))) < AT_REST_SPEED
    assert axis_tilt_deg(w, 0) < 2


def test_a_leaning_coral_settles_lying_or_standing(tmp_path):
    w = coral_world(tmp_path, 40)
    step(w, 3.0)
    tilt = axis_tilt_deg(w, 0)
    assert tilt < 5 or tilt > 85


def test_a_mixed_field_builds_every_kind(tmp_path):
    w = mixed_world(tmp_path)
    b = w.test_range.balls
    assert w.parts.layout.kinds[:2] == ["2025-algae", "coral"] and w.test_range.ball_poses().shape[0] == 1
    assert b.ball_form.tolist()[:2] == [True, False] and b.rolls.tolist()[:2] == [True, True]
    assert b.radius[0] == pytest.approx(0.4064 / 2) and b.radius[1] > CORAL_L / 2
    assert w.model.body_geomnum[b.bodies[1]] == 24     # the coral's slices
    assert b.counted_by["score"].tolist()[:2] == [True, False]
    step(w, 0.5)
    assert b.position(0)[2] == pytest.approx(0.4064 / 2, abs=0.003)
    assert b.position(1)[2] == pytest.approx(CORAL_R, abs=0.003)
    assert axis_tilt_deg(w, 1) < 2


def test_a_piece_is_named_by_its_kind(tmp_path):
    w = mixed_world(tmp_path)
    b = w.test_range.balls
    assert [b.piece_name(i) for i in (0, 1)] == ["algae 1", "coral 1"]
    b.place(0, (12.0, 4.0, 0.6), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), BALL_BITS)     # dropped onto the coral
    step(w, 0.5)
    assert w.test_range.ball_log()[0]["first_hit"] == "coral 1"
    legacy = World(range_cfg(range={"floor_balls": True}), None).test_range.balls
    assert legacy.piece_name(3) == "ball 3"


def test_a_coral_in_a_goal_opening_doesnt_score(tmp_path):
    w = mixed_world(tmp_path, coral=(12.0, 2.0))      # the coral under the goal's opening, to be dropped through it
    b = w.test_range.balls
    coral = b.kinds["coral"]
    b.place(1, (12.0, 2.0, 1.6), (0.0, 0.0, -1.0), (0.0, 0.0, 0.0), BALL_BITS, quat=coral.orient(90.0, 0.0))
    step(w, 1.5)
    assert b.position(1)[2] < 0.5 and b.state[1] == BallState.FIELD     # it went through the opening
    assert w.test_range.counts()["score"] == 0
    b.place(0, (12.0, 2.0, 1.6), (0.0, 0.0, -1.0), (0.0, 0.0, 0.0), BALL_BITS)      # the algae does score
    step(w, 1.5)
    assert w.test_range.counts()["score"] == 1


def test_a_shooter_on_a_coral_only_field_is_refused(tmp_path):
    with pytest.raises(ValueError, match="needs a ball or disc on the field; this field's pieces are: coral"):
        World(shooter_cfg(range={"file": _file(tmp_path, CORAL.format(tip=0, turn=0))}), None)


def test_the_intake_ball_path_ignores_a_coral(tmp_path):
    # the shooter fixture's intake zone is 0.45 m ahead of the robot's center: the coral lies across it
    w = mixed_world(tmp_path, ball="2026-fuel", coral=(3.45, 4.0), make=shooter_cfg,
                    robot={"start_pose": [3.0, 4.0, 0.0]})
    hal = HalState()
    hal.devices[("PWM", "7")] = {"<init": True, "<speed": 1.0}
    step(w, 1.0, hal)
    assert w.test_range.ball_path.held() == 0 and w.test_range.balls.state[1] == BallState.FIELD


def test_a_coral_only_field_with_a_station_builds(tmp_path):
    text = CORAL.format(tip=0, turn=0) + '''
[[station]]
name = "feeder"
release = [1.0, 4.0, 0.8]
heading_deg = 0.0
chute = { slope_deg = 30.0, length = 0.6 }
'''
    w = World(range_cfg(range={"file": _file(tmp_path, text), "floor_balls": True}), None)
    step(w, 0.1)
    assert w.test_range.counts()["on_field"] >= 1


def test_a_coral_is_drawn_in_the_view(tmp_path):
    w = coral_world(tmp_path, 90)
    v = model_view(w, include_world=True)
    mover = next(k for k, mv in enumerate(v["movers"]) if mv["name"] == "ball.0")
    slices = [g for g in v["world_geoms"] if g["mover"] == mover]
    assert len(slices) == 24 and all(g["type"] == "mesh" and v["meshes"][g["mesh"]]["vert"] for g in slices)
