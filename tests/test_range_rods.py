"""Rods: a straight round or square rod at any angle, fixed to the field (a peg, a pole, a coral branch)."""

from __future__ import annotations

import math

import mujoco
import numpy as np
import pytest

from range_fixtures import range_cfg
from scrimmage.protocol import HalState
from scrimmage.testrange.balls import BallState
from scrimmage.testrange.build import BALL_BITS
from scrimmage.testrange.elements import IN, Rod, footprint
from scrimmage.testrange.spec import RangeError, load_range
from scrimmage.world import World

HEAD = '''
[range]
name = "test"
ball = "2026-fuel"
'''
PEG = '''
[[element]]
name = "branch"
kind = "rod"
base_in = [60.0, 100.0, 0.0]
tip_in = [60.0, 100.0, 12.0]
diameter_in = 1.0
'''
CORAL_FIELD = '''
[range]
name = "rods"

[piece.coral]
preset = "2025-coral"

[[floor_balls]]
at = [[2.0, 2.0]]
tip_deg = 90

[[element]]
name = "post"
kind = "box"
center = [12.0, 4.0]
size = [0.2, 0.2, 0.3]

[[element]]
name = "branch"
kind = "rod"
base = [12.0, 4.0, 0.3]
tip_in = [{tx}, {ty}, {tz}]
diameter_in = 1.0
{extra}
'''
CORAL_L = 11.875 * IN


def load_text(tmp_path, text):
    (tmp_path / "r.toml").write_text(HEAD + text)
    return load_range("r.toml", tmp_path)


def rod_world(tmp_path, extra="", tip=(12.0 / IN, 4.0 / IN, 0.3 / IN + 12.0)) -> World:
    path = tmp_path / "field.toml"
    path.write_text(CORAL_FIELD.format(tx=tip[0], ty=tip[1], tz=tip[2], extra=extra), encoding="utf-8")
    return World(range_cfg(range={"file": str(path), "floor_balls": True}), None)


def step(w, seconds: float) -> None:
    for _ in range(int(round(seconds / w.model.opt.timestep))):
        w.step(HalState())


def test_a_rod_parses_in_inches(tmp_path):
    rng = load_text(tmp_path, PEG)
    e = rng.elements[0]
    assert isinstance(e, Rod) and e.section == "round"
    assert e.length() == pytest.approx(12 * IN) and e.axis() == pytest.approx([0, 0, 1])
    assert e.center == pytest.approx((60 * IN, 100 * IN)) and e.diameter == pytest.approx(IN)
    assert footprint(e).shape == (4, 2)


def test_a_slanted_rod_has_a_derived_center_and_yaw(tmp_path):
    e = load_text(tmp_path, '''
[[element]]
name = "branch"
kind = "rod"
base = [1.0, 1.0, 0.5]
tip = [1.2, 1.2, 0.9]
diameter = 0.02
''').elements[0]
    assert e.center == pytest.approx((1.1, 1.1)) and e.yaw_deg == pytest.approx(45.0)
    assert e.length() == pytest.approx(math.sqrt(0.2 ** 2 + 0.2 ** 2 + 0.4 ** 2))


def test_a_rod_is_one_capsule_reaching_its_tip(tmp_path):
    w = rod_world(tmp_path)
    m = w.model
    g = m.geom("element.branch.rod")
    assert g.type[0] == mujoco.mjtGeom.mjGEOM_CAPSULE
    axis = w.data.geom_xmat[g.id].reshape(3, 3)[:, 2]
    top = w.data.geom_xpos[g.id] + axis * (g.size[1] + g.size[0])
    assert top[2] == pytest.approx(0.3 + 12 * IN, abs=1e-6)
    assert top[:2] == pytest.approx([12.0, 4.0], abs=1e-6)


def test_a_slanted_rod_reaches_its_tip(tmp_path):
    w = rod_world(tmp_path, tip=(12.0 / IN + 4, 4.0 / IN + 3, 0.3 / IN + 10))
    g = w.model.geom("element.branch.rod")
    axis = w.data.geom_xmat[g.id].reshape(3, 3)[:, 2]
    end = w.data.geom_xpos[g.id] + axis * (g.size[1] + g.size[0])
    assert end == pytest.approx([12.0 + 4 * IN, 4.0 + 3 * IN, 0.3 + 10 * IN], abs=1e-6)


def test_a_square_rod_is_a_box(tmp_path):
    w = rod_world(tmp_path, extra='section = "square"')
    g = w.model.geom("element.branch.rod")
    assert g.type[0] == mujoco.mjtGeom.mjGEOM_BOX
    assert list(g.size) == pytest.approx([IN / 2, IN / 2, 6 * IN])


def test_bad_rods_say_what(tmp_path):
    same = PEG.replace("12.0]", "0.0]")
    with pytest.raises(RangeError, match="element 'branch': base and tip are the same point; a rod needs two ends"):
        load_text(tmp_path, same)
    stub = '''
[[element]]
name = "branch"
kind = "rod"
base = [1.0, 1.0, 0.0]
tip = [1.0, 1.0, 0.02]
diameter = 0.05
'''
    with pytest.raises(RangeError, match="element 'branch': diameter 0.05 m is longer than the rod"):
        load_text(tmp_path, stub)
    with pytest.raises(RangeError, match="unknown key 'center' in element 'branch'"):
        load_text(tmp_path, PEG + "center = [1.0, 1.0]\n")
    with pytest.raises(RangeError, match="section must be"):
        load_text(tmp_path, PEG + 'section = "hex"\n')


def test_a_start_or_floor_piece_inside_a_standing_rod_is_refused(tmp_path):
    text = PEG + '''
[[floor_balls]]
at = [[1.524, 2.54]]
'''
    with pytest.raises(RangeError, match="lies on element 'branch'"):
        load_text(tmp_path, text)


def test_a_release_inside_a_rod_is_refused(tmp_path):
    text = PEG + '''
[[station]]
name = "feeder"
release = [1.524, 2.54, 0.15]
heading_deg = 0.0
'''
    with pytest.raises(RangeError, match="inside element 'branch'"):
        load_text(tmp_path, text)


def seated(w, offset: float):
    b = w.test_range.balls
    coral = b.kinds["coral"]
    b.place(0, (12.0 + offset, 4.0, 0.3 + 12 * IN + 0.02 + CORAL_L / 2), (0.0, 0.0, -0.5), (0.0, 0.0, 0.0),
            BALL_BITS, quat=coral.orient(90.0, 0.0))
    step(w, 2.0)
    return b.position(0)


def test_a_coral_dropped_over_a_rod_slides_down_it(tmp_path):
    w = rod_world(tmp_path)
    p = seated(w, 0.0)
    assert p[2] - CORAL_L / 2 == pytest.approx(0.3, abs=0.01) and abs(p[0] - 12.0) < 0.02


def test_a_coral_dropped_off_center_doesnt_pass_through(tmp_path):
    w = rod_world(tmp_path)
    p = seated(w, 4 * IN)
    assert w.test_range.balls.state[0] == BallState.FIELD
    assert abs(p[0] - 12.0) > 0.04 and p[2] - CORAL_L / 2 < 0.3 + 0.05
