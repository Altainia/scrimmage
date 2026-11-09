"""Opt-in collisions for stock mechanisms: `collide = true` turns a part's geoms (all but the drawing-only ones) into
robot geoms that touch the field, game pieces and a partner robot, never the robot's own parts."""

from __future__ import annotations

import copy

import mujoco
import numpy as np
import pytest

from range_fixtures import KITBOT, drive, range_cfg, shooter_cfg, spares_world
from scrimmage.parts.assemble import NO_BITS, ROBOT_BITS, assemble
from scrimmage.testrange.build import BALL_BITS
from scrimmage.world import World

MECHANISMS = ("elevator", "arm", "flywheel", "roller", "launcher", "disc_shooter", "pinch_shooter", "turret",
              "cylinder")


def _bits(m, body: str) -> list[tuple[int, int]]:
    b = m.body(body).id
    return [(int(m.geom_contype[g]), int(m.geom_conaffinity[g])) for g in range(m.ngeom) if m.geom_bodyid[g] == b]


def _part(stock: str, **params):
    cfg = {"part": [{"name": "drive", "stock": "chassis"}, {"name": "arm", "stock": "arm", "attach": "drive.top"},
                    {"name": "p", "stock": stock, "attach": "drive.top", **params}]}
    return assemble(cfg, ".").model


def _bodies(m, part: str = "p") -> list[str]:
    return [m.body(b).name for b in range(m.nbody) if m.body(b).name.startswith(f"{part}.")]


ROBOT = (ROBOT_BITS["contype"], ROBOT_BITS["conaffinity"])
NONE = (NO_BITS["contype"], NO_BITS["conaffinity"])


@pytest.mark.parametrize("stock", MECHANISMS)
def test_collide_default_off(stock):
    m = _part(stock)
    assert all(bits == NONE for b in _bodies(m) for bits in _bits(m, b))
    assert _part(stock, collide=False).nbody == m.nbody


def test_collide_on_elevator():
    m = _part("elevator", stages=2, collide=True)
    for body in ("p.base", "p.stage1", "p.carriage"):
        assert _bits(m, body) == [ROBOT]


@pytest.mark.parametrize("stock, params, colliding, drawn", [
    ("arm", {}, {"p.base": 1, "p.arm": 1}, ()),
    ("turret", {}, {"p.base": 1, "p.platform": 1}, ()),
    ("flywheel", {}, {"p.base": 1, "p.wheel": 1}, ()),
    ("roller", {}, {"p.base": 1, "p.wheel": 1}, ()),
    # the side plates and the wheel collide; the hood's drawn arc (its own body, or the fixed hood's on the base)
    # doesn't
    ("launcher", {}, {"p.base": 2, "p.wheel": 1}, ("p.hood",)),
    ("launcher", {"hood": "fixed"}, {"p.base": 2, "p.wheel": 1}, ("p.base",)),
    # the frame, the fence, the spinning wheel and the 3 still ones drawn on the channel collide; the two guide
    # plates above and below the disc don't
    ("disc_shooter", {}, {"p.base": 1, "p.channel": 4, "p.wheel0": 1}, ("p.channel",)),
    # the frame, the side plates and both wheels collide; nothing is drawn only
    ("pinch_shooter", {}, {"p.base": 1, "p.channel": 2, "p.bottom_wheel": 1, "p.top_wheel": 1}, ()),
    ("cylinder", {}, {"p.barrel": 1, "p.rod": 1}, ()),
    # a push cylinder's barrel is drawn only
    ("cylinder", {"push": "arm.arm", "push_pos": [0.2, 0.0, 0.0]}, {}, ("p.barrel",)),
])
def test_collide_on_every_geom_but_the_drawn_ones(stock, params, colliding, drawn):
    m = _part(stock, collide=True, **params)
    for body in _bodies(m):
        bits = _bits(m, body)
        assert bits.count(ROBOT) == colliding.get(body, 0), body
        assert bits.count(ROBOT) + bits.count(NONE) == len(bits), body
        assert (NONE in bits) == (body in drawn), body


START = (2.5, 4.0)


def _box_world(tmp_path, collide: bool):
    """The kitbot with an elevator (PWM 4) at START, and a box hanging over the carriage's front edge 0.5 m above
    its top: the carriage reaches 0.04 m ahead of the base and the box starts 0.03 m ahead, so only the carriage
    can reach it."""
    (tmp_path / "box.toml").write_text(f"""
[range]
name = "a box"
ball = "2026-fuel"

[start.here]
pose = [{START[0]}, {START[1]}, 0.0]

[[element]]
name = "lid"
kind = "box"
center = [{START[0] + 0.03 + 0.15}, {START[1]}]
size = [0.3, 0.4, 0.1]
base_height = 0.72
""", encoding="utf-8")
    elevator = {"name": "elevator", "stock": "elevator", "attach": "drive.top", "ids": {"lift": "PWM:4"},
                "collide": collide}
    cfg = range_cfg(part=copy.deepcopy(KITBOT) + [elevator], range={"file": "box.toml", "start": "here"})
    return World(cfg, tmp_path)


def test_carriage_raised_into_a_box_stops_at_it(tmp_path):
    for collide in (False, True):
        w = _box_world(tmp_path, collide)
        m, d = w.model, w.data
        mujoco.mj_forward(m, d)
        top0 = d.xpos[m.body("elevator.carriage").id][2] + 0.1
        assert abs(d.xpos[m.body("elevator.base").id][0] - START[0]) < 1e-6
        q = m.joint("elevator.carriage").qposadr[0]
        drive(w, 2.0, p4=0.5)
        lift = float(d.qpos[q])
        if collide:
            assert 0.72 - top0 - lift == pytest.approx(0.0, abs=0.02)    # the carriage's top at the box's bottom
        else:
            assert lift > 0.95                                            # through it, to the top stop


def test_collide_on_roller_moves_a_ball():
    """A ball set on top of a roller ahead of the bumper: spinning, the roller throws it off the way its top moves.
    Without collide the ball falls straight through it."""
    moved = {}
    for collide, spin in ((False, 1.0), (True, 1.0), (True, -1.0)):
        roller = {"name": "roller", "stock": "roller", "attach": "drive.center", "pos": [0.6, 0.0, 0.3],
                  "gearing": 10.0, "ids": {"spin": "PWM:7"}, "collide": collide}
        w = spares_world(range_cfg(part=copy.deepcopy(KITBOT) + [roller]))
        m, d = w.model, w.data
        mujoco.mj_forward(m, d)
        axle = d.xpos[m.body("roller.wheel").id].copy()
        balls = w.test_range.balls
        balls.put(0, axle + [0.0, 0.0, 0.025 + balls.r - 0.002], BALL_BITS)      # resting on the roller's top
        drive(w, 0.5, p7=spin)      # positive about +y: the roller's top moves forward (+x)
        moved[collide, spin] = float(balls.position(0)[0] - axle[0])
    assert abs(moved[False, 1.0]) < 0.005
    assert moved[True, 1.0] > 0.3 and moved[True, -1.0] < -0.3


def test_collide_shooter_intake_errors():
    """Review Focus 5, as ruled: only the [shooter]'s intake roller is refused, since it would push the floor balls
    its zone collects."""
    with pytest.raises(ValueError) as e:
        World(_with(shooter_cfg(), "intake"), None)
    assert str(e.value) == ("intake has collide = true but is the [shooter]'s intake; the ball path moves those "
                            "balls, so leave collide off")


def test_colliding_launcher_and_stage_still_shoot():
    """A colliding launcher (a turret launcher under a trench) and a colliding stage build, and a preloaded ball
    still runs the ball path and launches: the balls they hold are parked, and a launched ball clears the robot."""
    cfg = _with(shooter_cfg(launcher={"collide": True, "hood": "fixed", "hood_angle": 50.0,
                                      "ids": {"shooter": "PWM:2"}},
                            range={"preload": 2}), "feeder")
    w = World(cfg, None)
    m = w.model
    assert ROBOT in _bits(m, "launcher.wheel") and ROBOT in _bits(m, "feeder.wheel")
    w.set_robot_pose(1.6, 4.035, 0.0)
    drive(w, 1.0, p2=0.6)                       # spin up
    drive(w, 2.0, p2=0.6, p5=1.0, p6=1.0)       # feed
    rt = w.test_range
    assert rt.balls.fired
    assert any(log.launcher_status == "launched" for log in rt.balls.log)


def test_collide_on_a_part_off_the_ball_path_is_fine():
    cfg = shooter_cfg()
    cfg["part"].append({"name": "lift", "stock": "elevator", "attach": "drive.top", "pos": [0.2, 0.0, 0.0],
                        "collide": True})
    World(cfg, None)


def _with(cfg: dict, part: str) -> dict:
    next(p for p in cfg["part"] if p["name"] == part)["collide"] = True
    return cfg

