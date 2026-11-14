"""Measuring a robot for inspection (scrimmage/inspection/measure.py): its weight, frame perimeter, height, reach
beyond the frame and the smallest circle holding it, over every geom of the robot, colliding or not."""
from __future__ import annotations

import math
from pathlib import Path

import mujoco
import numpy as np
import pytest

from scrimmage.config import build_world, load_config
from scrimmage.inspection.measure import Extent, measure, smallest_circle
from scrimmage.world import World

ROOT = Path(__file__).resolve().parent.parent


def world(name: str):
    return build_world(load_config(ROOT / "configs" / name))


def test_the_stock_kitbot():   # configs/kitbot.toml: chassis 0.66 x 0.6, bumpers 0.08
    w = world("kitbot.toml")
    m = measure(w)
    assert m.perimeter == pytest.approx(2 * (0.66 + 0.6), abs=1e-6) and m.frame_plate
    root = w.model.body_rootid[w.robot_body]
    assert m.mass == pytest.approx(float(w.model.body_subtreemass[root]))
    assert m.reach == pytest.approx(0, abs=0.01)
    assert 0.6 + 2 * 0.08 <= m.diameter <= math.hypot(0.66 + 0.16, 0.6 + 0.16) + 1e-6
    assert 0.02 + 0.12 <= m.height <= 0.16       # at least the bumpers' top (clearance + their height); the wheels' boxes


def collides(model, g: int) -> bool:
    return bool(model.geom_contype[g] or model.geom_conaffinity[g])


def test_a_non_colliding_elevator_counts_for_height():
    w = world("elevator_arm.toml")
    m = w.model
    elevator = [g for g in range(m.ngeom) if m.body(m.geom_bodyid[g]).name.startswith("elevator.")]
    assert elevator and not any(collides(m, g) for g in elevator)
    chassis_top = max(float(w.data.geom_xpos[g][2] + m.geom_size[g][2])
                      for g in range(m.ngeom) if m.geom_bodyid[g] == w.robot_body)
    top = max(float(w.data.geom_xpos[g][2] + m.geom_size[g][2]) for g in elevator)   # upright boxes
    h = measure(w).height
    assert h > chassis_top + 0.3
    assert h == pytest.approx(top, abs=1e-6)
    assert Extent(w).read()[0] == pytest.approx(h)


def test_an_arm_reaching_out_reads_its_reach():
    """The arm's box (0.5 x 0.04 x 0.04 from its pivot) at angle a: its farthest corner is the pivot's x plus
    0.5 cos a + 0.02 sin a, in the chassis's frame. Set a so that's 0.3 m past the frame's front (x = 0.33)."""
    w = world("elevator_arm.toml")
    m, d = w.model, w.data
    arm = m.body("arm.arm").id
    rc = d.xmat[w.robot_body].reshape(3, 3)
    pivot_x = float((d.xpos[arm] - d.xpos[w.robot_body]) @ rc[:, 0])
    want = 0.33 + 0.3 - pivot_x
    a = next(x for x in np.linspace(0, math.pi / 2, 200001) if 0.5 * math.cos(x) + 0.02 * math.sin(x) <= want)
    d.qpos[m.jnt_qposadr[m.joint("arm.pivot").id]] = a
    mujoco.mj_forward(m, d)
    assert measure(w).reach == pytest.approx(0.3, abs=1e-4)
    assert Extent(w).read()[1] == pytest.approx(0.3, abs=1e-4)


ROBOT_XML = """<mujoco>
  <worldbody>
    <body name="chassis" pos="1 2 0.2"><freejoint/>
      <geom type="cylinder" size="0.3 0.1" mass="40"/>
      <geom type="sphere" size="0.05" pos="0.25 0.2 0.1" contype="0" conaffinity="0" mass="1"/>
      <body name="mast" pos="0 0 0.1">
        <geom type="capsule" fromto="0 0 0 0 0 0.5" size="0.02" mass="2"/>
      </body>
    </body>
  </worldbody>
</mujoco>"""


def test_a_robot_without_a_frame_plate_uses_all_its_geoms(tmp_path):
    (tmp_path / "robot.xml").write_text(ROBOT_XML, encoding="utf-8")
    w = World({"model": {"mjcf": "robot.xml"}}, tmp_path)
    m = measure(w)
    assert not m.frame_plate
    # the cylinder spans x, y in [-0.3, 0.3]; the non-colliding sphere reaches x 0.3, y 0.25
    assert m.perimeter == pytest.approx(2 * (0.6 + 0.6), abs=1e-6)
    assert m.mass == pytest.approx(43.0)
    assert m.height == pytest.approx(0.2 + 0.1 + 0.5 + 0.02, abs=1e-6)
    assert m.reach == pytest.approx(0, abs=1e-9)
    assert m.diameter == pytest.approx(0.6 * math.sqrt(2), abs=1e-6)   # the cylinder's bounding square


def test_smallest_circle():
    assert smallest_circle(np.array([[0, 0], [1, 0], [0, 1], [1, 1]], dtype=float)) == pytest.approx(math.sqrt(2))
    assert smallest_circle(np.array([[0, 0], [1, 1], [3, 3], [2, 2]], dtype=float)) == pytest.approx(3 * math.sqrt(2))
    assert smallest_circle(np.array([[2.0, 5.0]])) == 0.0
    assert smallest_circle(np.array([[0, 0], [4, 0], [2, 1]], dtype=float)) == pytest.approx(4.0)   # obtuse: the span
    pts = np.random.default_rng(1).normal(size=(3000, 2))
    dia = smallest_circle(pts)
    # at least the set's diameter (its farthest pair), at most 2/sqrt(3) times it (Jung's theorem)
    full = max(np.linalg.norm(pts - p, axis=1).max() for p in pts)
    assert full - 1e-9 <= dia <= full * 2 / math.sqrt(3) + 1e-9


def test_smallest_circle_holds_every_point():
    from scrimmage.inspection.measure import _enclosing
    pts = np.random.default_rng(2).uniform(-1, 1, size=(2000, 2))
    c, r = _enclosing(pts)
    assert np.linalg.norm(pts - c, axis=1).max() <= r + 1e-9
    # and it's the smallest: some 2 or 3 points sit on it
    on = np.sum(np.abs(np.linalg.norm(pts - c, axis=1) - r) < 1e-9)
    assert on >= 2
