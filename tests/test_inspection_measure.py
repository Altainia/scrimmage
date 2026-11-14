"""Measuring a robot for inspection (scrimmage/inspection/measure.py): its weight, frame perimeter, height, reach
beyond the frame and the smallest circle holding it, over every geom of the robot, colliding or not."""
from __future__ import annotations

import math
from pathlib import Path

import mujoco
import numpy as np
import pytest

from scrimmage.config import build_world, load_config
from scrimmage.terrain.climb import extent
from scrimmage.inspection.measure import Extent, measure, shape_points, smallest_circle
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
    assert m.reach < 0.02       # the wheels' rims stick out 6 mm past the frame's ends
    assert m.reach_geom.startswith("dt.") and "tread" in m.reach_geom
    assert 0.6 + 2 * 0.08 <= m.diameter <= math.hypot(0.66 + 0.16, 0.6 + 0.16) + 1e-6
    assert 0.02 + 0.12 <= m.height <= 0.16       # at least the bumpers' top (clearance + their height); the wheels' boxes


def test_the_stock_swerve():   # configs/swerve.toml: a 0.7 m square frame plate, four MK4i modules
    w = world("swerve.toml")
    m = measure(w)
    assert m.perimeter == pytest.approx(4 * 0.7, abs=1e-6) and m.frame_plate
    assert m.reach < 0.01       # a module's azimuth rim sticks out 2 mm past the frame
    # the tallest part is the 0.05 x 0.02 x 0.005 plate centered 0.145 m up, on the chassis: its top is 0.15 m, and
    # the wheels (r 0.051, centered 0.051 up) stand on the floor
    assert m.height == pytest.approx(0.15, abs=1e-6)


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
    lowest = float(Extent(w).points()[:, 2].min())     # the wheels' bottoms, standing level on the floor
    assert h > chassis_top + 0.3
    assert h == pytest.approx(top - lowest, abs=1e-6) and abs(lowest) < 0.005
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
    lo, hi = 0.0, math.pi / 2        # 0.5 cos a + 0.02 sin a falls over this range (a bisection for it)
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if 0.5 * math.cos(mid) + 0.02 * math.sin(mid) > want else (lo, mid)
    a = hi
    d.qpos[m.jnt_qposadr[m.joint("arm.pivot").id]] = a
    mujoco.mj_forward(m, d)
    got = measure(w)
    assert got.reach == pytest.approx(0.3, abs=1e-4) and got.reach_geom == "arm.arm"
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
    # in the robot's own frame: from the cylinder's bottom (z 0.1) to the capsule's tip (0.2 + 0.1 + 0.5 + 0.02)
    assert m.height == pytest.approx(0.1 + 0.5 + 0.02 + 0.1, abs=1e-6)
    assert m.reach == pytest.approx(0, abs=1e-9) and m.reach_geom == ""
    # the cylinder (r 0.3) and the sphere (r 0.05, its center 0.32 off the axis): 0.3 + 0.32 + 0.05 across, less a
    # little for the rings' points sitting inside the true circles
    assert 0.67 - 0.003 <= m.diameter <= 0.67 + 1e-9


def test_round_parts_are_measured_by_their_shape():
    """configs/maxswerve.toml: each module's azimuth cylinder (r 0.06) is centered 0.0191 m inside the frame's
    corner in x and y, so its square bounding box would read hypot(0.0409, 0.0409) = 0.058 m past the corner; its
    rim reaches 0.0409 m past one edge. What sets the reach is the module's base plate, a 0.1 m square box centered
    there: its corner is hypot(0.0309, 0.0309) = 0.0437 m past the frame's corner."""
    w = world("maxswerve.toml")
    got = measure(w)
    assert got.reach == pytest.approx(math.hypot(0.05 - 0.0191, 0.05 - 0.0191), abs=1e-3)
    assert got.reach_geom.endswith(".base")
    m = w.model
    azimuth = [g for g in range(m.ngeom) if m.body(m.geom_bodyid[g]).name.endswith(".azimuth")]
    d, ext = w.data, Extent(w)
    pts = np.concatenate([shape_points(m, g) @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g] for g in azimuth])
    _, reach, _ = extent(pts, len(pts), d.xpos[w.robot_body], d.xmat[w.robot_body].reshape(3, 3), ext._lo, ext._hi)
    assert reach == pytest.approx(0.06 - 0.0191, abs=1e-3)      # the rim, not its box's corner (0.058)


MESH_XML = """<mujoco>
  <asset><mesh name="wedge" vertex="0.3 -0.1 0  0.5 -0.1 0  0.3 0.1 0  0.3 -0.1 0.4"/></asset>
  <worldbody>
    <body name="chassis" pos="0 0 0.1"><freejoint/>
      <geom type="box" size="0.3 0.3 0.05" mass="40"/>
      <body name="arm" pos="0.1 0 0.05" euler="0 0 90">
        <geom name="wedge" type="mesh" mesh="wedge" mass="1"/>
      </body>
    </body>
  </worldbody>
</mujoco>"""


def test_a_mesh_is_measured_by_its_vertices(tmp_path):
    """A wedge mesh on a body turned 90 deg about z, at x 0.1: its vertices go to x 0.1 + 0.1 = 0.2 (inside the
    frame), y 0.3 to 0.5 (0.2 past the frame's side at y 0.3) and z 0.15 + 0.4: 0.5 above the box's bottom (z 0.05)."""
    (tmp_path / "robot.xml").write_text(MESH_XML, encoding="utf-8")
    w = World({"model": {"mjcf": "robot.xml"}}, tmp_path)
    got = measure(w)
    assert got.reach == pytest.approx(0.2, abs=1e-6) and got.reach_geom == "wedge"
    assert got.height == pytest.approx(0.1 + 0.4, abs=1e-6)


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


def _brute_force_diameter(pts: np.ndarray) -> float:
    """The smallest circle holding every point, from all the circles through 2 points (as a diameter) and 3 points
    (circumscribed), keeping those that hold every point."""
    best = math.inf
    n = len(pts)
    candidates = []
    for i in range(n):
        for j in range(i + 1, n):
            candidates.append(((pts[i] + pts[j]) / 2, float(np.linalg.norm(pts[i] - pts[j])) / 2))
            for k in range(j + 1, n):
                a, b, c = pts[i], pts[j], pts[k]
                d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
                if abs(d) < 1e-12:
                    continue
                ux = ((a @ a) * (b[1] - c[1]) + (b @ b) * (c[1] - a[1]) + (c @ c) * (a[1] - b[1])) / d
                uy = ((a @ a) * (c[0] - b[0]) + (b @ b) * (a[0] - c[0]) + (c @ c) * (b[0] - a[0])) / d
                centre = np.array([ux, uy])
                candidates.append((centre, float(np.linalg.norm(a - centre))))
    for centre, r in candidates:
        if r < best and np.linalg.norm(pts - centre, axis=1).max() <= r + 1e-9:
            best = r
    return 2 * best


@pytest.mark.parametrize("seed", [3, 4, 5, 6, 7])
def test_smallest_circle_matches_a_brute_force(seed):
    pts = np.random.default_rng(seed).normal(size=(30, 2))
    assert smallest_circle(pts) == pytest.approx(_brute_force_diameter(pts), abs=1e-9)


def test_smallest_circle_holds_every_point():
    from scrimmage.inspection.measure import _enclosing
    pts = np.random.default_rng(2).uniform(-1, 1, size=(2000, 2))
    c, r = _enclosing(pts)
    assert np.linalg.norm(pts - c, axis=1).max() <= r + 1e-9
    # and it's the smallest: some 2 or 3 points sit on it
    on = np.sum(np.abs(np.linalg.norm(pts - c, axis=1) - r) < 1e-9)
    assert on >= 2
