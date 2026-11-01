"""Field elements in the MuJoCo model: bumps and ramps as static boxes, platforms on their double hinge."""

from __future__ import annotations

import math

import mujoco
import numpy as np
import pytest

from range_fixtures import range_cfg
from scrimmage.assembler.view import model_view
from scrimmage.movers import movers
from scrimmage.parts.field import FIELD_BITS
from scrimmage.testrange.build_elements import ELEMENT_GROUP, add_element, platform_tilt
from scrimmage.testrange.elements import IN, Bump, Platform
from scrimmage.testrange.spec import BUILTIN_RANGES, load_range
from scrimmage.world import World

GROUND = np.array([1 if g == ELEMENT_GROUP else 0 for g in range(6)], dtype=np.uint8)
NEW_RANGES = ("2026-bump", "2023-charge-station", "terrain-library")
LEVEL_DEG = 2.5      # 2023 manual 5.4: LEVEL is within about 2.5 deg of the carpet


def ground_z(m, d, x, y, top=2.0) -> float:
    """The height of the ground (carpet or element) under (x, y), by a ray down in ELEMENT_GROUP."""
    gid = np.array([-1], dtype=np.int32)
    dist = mujoco.mj_ray(m, d, np.array([x, y, top]), np.array([0.0, 0.0, -1.0]), GROUND, 1, -1, gid)
    assert dist >= 0.0, f"no ground under ({x}, {y})"
    return top - dist


def charge_station() -> Platform:
    rng = load_range("2023-charge-station", ".")
    return next(e for e in rng.elements if isinstance(e, Platform))


def platform_world(p: Platform, box_x: float | None = None):
    """A carpet, the platform, and (box_x given) a 60 kg box resting on the deck at box_x along its tilt, in the
    platform's frame."""
    spec = mujoco.MjSpec()
    spec.option.timestep = 0.001
    spec.worldbody.add_geom(name="carpet", type=mujoco.mjtGeom.mjGEOM_PLANE, size=[10, 10, 0.1], group=ELEMENT_GROUP,
                            **FIELD_BITS)
    add_element(spec, p)
    if box_x is not None:
        c, s = math.cos(math.radians(p.yaw_deg)), math.sin(math.radians(p.yaw_deg))
        b = spec.worldbody.add_body(name="box", pos=[p.center[0] + c * box_x, p.center[1] + s * box_x,
                                                     p.height + 0.16])
        b.add_freejoint()
        b.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, size=[0.15, 0.15, 0.15], mass=60.0, contype=2, conaffinity=1,
                   friction=[1.0, 0.005, 0.0001])
    m = spec.compile()
    return m, mujoco.MjData(m)


def tilt_deg(m, d, name) -> float:
    return math.degrees(platform_tilt(m, d, name))


def test_bump_profile_matches_measurement():
    w = World(range_cfg(range={"file": "2026-bump", "start": "approach"}), None)
    m, d = w.model, w.data
    bump = next(e for e in w.parts.range.elements if isinstance(e, Bump))
    cx, cy = bump.center
    assert ground_z(m, d, cx, cy) == pytest.approx(6.51 * IN, abs=1e-3)
    half_run = bump.depth / 4.0          # halfway up the near ramp, rays 1 cm either side
    za, zb = ground_z(m, d, cx - half_run - 0.01, cy), ground_z(m, d, cx - half_run + 0.01, cy)
    assert math.degrees(math.atan2(zb - za, 0.02)) == pytest.approx(15.0, abs=0.2)
    za, zb = ground_z(m, d, cx + half_run - 0.01, cy), ground_z(m, d, cx + half_run + 0.01, cy)
    assert math.degrees(math.atan2(za - zb, 0.02)) == pytest.approx(15.0, abs=0.2)
    toe = bump.depth / 2.0
    assert ground_z(m, d, cx - toe + 0.002, cy) == pytest.approx(0.61 * IN, abs=1.5e-3)    # the toe lip
    assert ground_z(m, d, cx - toe - 0.02, cy) == pytest.approx(0.0, abs=1e-6)
    assert ground_z(m, d, cx + toe + 0.02, cy) == pytest.approx(0.0, abs=1e-6)
    assert ground_z(m, d, cx, cy + bump.width / 2.0 + 0.02) == pytest.approx(0.0, abs=1e-6)
    body = m.body("element.bump")
    assert int(m.body_weldid[body.id]) == 0                       # welded to the world
    for g in range(m.ngeom):
        if int(m.geom_bodyid[g]) == body.id:
            assert (int(m.geom_contype[g]), int(m.geom_conaffinity[g]), int(m.geom_group[g])) == (
                FIELD_BITS["contype"], FIELD_BITS["conaffinity"], ELEMENT_GROUP)
    assert int(m.geom_group[m.geom("carpet").id]) == ELEMENT_GROUP


def test_ray_in_group_2_ignores_balls_and_robot():
    w = World(range_cfg(range={"file": "2026-bump", "start": "approach", "floor_balls": True}), None)
    m, d = w.model, w.data
    x, y = w.parts.range.floor_balls[0]
    gid = np.array([-1], dtype=np.int32)
    everything = mujoco.mj_ray(m, d, np.array([x, y, 1.0]), np.array([0.0, 0.0, -1.0]), None, 1, -1, gid)
    assert m.geom(int(gid[0])).name.startswith("ball.") and everything < 1.0 - 0.1
    assert ground_z(m, d, x, y, top=1.0) == pytest.approx(0.0, abs=1e-6)
    rx, ry, _ = w.parts.range.starts["approach"]
    mujoco.mj_ray(m, d, np.array([rx, ry, 1.0]), np.array([0.0, 0.0, -1.0]), None, 1, -1, gid)
    assert int(m.body_rootid[m.geom_bodyid[int(gid[0])]]) == int(m.body_rootid[w.robot_body])
    assert ground_z(m, d, rx, ry, top=1.0) == pytest.approx(0.0, abs=1e-6)
    robot = {g for g in range(m.ngeom) if int(m.body_rootid[m.geom_bodyid[g]]) == int(m.body_rootid[w.robot_body])}
    assert not any(int(m.geom_group[g]) == ELEMENT_GROUP for g in robot)


def test_platform_tilts_to_its_stop_under_a_weight_on_one_end():
    p = charge_station()
    m, d = platform_world(p, box_x=0.45)     # the deck is 48 in (1.22 m) along its tilt: 0.45 m is near its end
    stop = p.max_tilt_deg
    seen = []
    for _ in range(2000):
        mujoco.mj_step(m, d)
        seen.append(tilt_deg(m, d, p.name))
    assert seen[-1] == pytest.approx(stop, abs=0.5)
    assert max(seen) < stop + 1.0                                   # a stiff stop
    assert all(abs(t - stop) < 0.5 for t in seen[-500:])            # settled, not bouncing


def test_platform_holds_level_until_the_load_beats_the_double_hinge():
    """The double hinge's dead band: an off-center load tips the deck only when its torque about the near hinge axis
    beats the deck's own weight about it, about m g (spacing / 2) = 63 N m."""
    p = charge_station()
    holding = 63.0 / (60.0 * 9.81) + p.hinge_spacing / 2.0      # box offset that balances it (about 0.17 m)
    m, d = platform_world(p, box_x=holding - 0.06)
    for _ in range(1500):
        mujoco.mj_step(m, d)
    assert abs(tilt_deg(m, d, p.name)) < LEVEL_DEG
    m, d = platform_world(p, box_x=holding + 0.06)
    for _ in range(1500):
        mujoco.mj_step(m, d)
    assert tilt_deg(m, d, p.name) > p.max_tilt_deg - 0.5


@pytest.mark.parametrize("start", [10.0, -10.0])
def test_platform_settles_level_unloaded(start):
    p = charge_station()
    m, d = platform_world(p)
    mujoco.mj_forward(m, d)
    assert tilt_deg(m, d, p.name) == pytest.approx(0.0, abs=1e-9)   # qpos0 is level
    adr = m.jnt_qposadr[m.joint(f"element.{p.name}.tilt_plus" if start > 0 else f"element.{p.name}.tilt_minus").id]
    d.qpos[adr] = math.radians(start)
    mujoco.mj_forward(m, d)
    assert tilt_deg(m, d, p.name) == pytest.approx(start)
    for _ in range(3000):
        mujoco.mj_step(m, d)
    assert abs(tilt_deg(m, d, p.name)) < LEVEL_DEG


def test_reset_levels_the_platform_reset_robot_does_not():
    w = World(range_cfg(range={"file": "2023-charge-station", "start": "approach"}), None)
    name = "charge_station"
    (a, b) = w.parts.layout.platforms[name]
    assert (a, b) == tuple(int(w.model.jnt_qposadr[w.model.joint(f"element.{name}.tilt_{s}").id])
                           for s in ("plus", "minus"))
    w.data.qpos[a] = math.radians(12.0)
    mujoco.mj_forward(w.model, w.data)
    w.reset_robot()
    assert tilt_deg(w.model, w.data, name) == pytest.approx(12.0)
    w.reset()
    assert tilt_deg(w.model, w.data, name) == pytest.approx(0.0, abs=1e-9)
    assert not w.data.qvel[w.model.jnt_dofadr[w.model.joint(f"element.{name}.tilt_plus").id]]


@pytest.mark.parametrize("name", NEW_RANGES)
def test_builtin_element_ranges_load_and_compile(name):
    assert name in BUILTIN_RANGES
    rng = load_range(name, ".")
    assert rng.elements
    start = "approach" if "approach" in rng.starts else None
    w = World(range_cfg(range={"file": name, "start": start, "floor_balls": True}), None)
    for e in rng.elements:
        assert w.model.body(f"element.{e.name}") is not None
    assert w.model.opt.timestep == pytest.approx(0.001)
    for _ in range(200):          # the robot sits still; nothing on the field flies apart
        mujoco.mj_step(w.model, w.data)
    assert np.isfinite(w.data.qpos).all()
    for e in rng.elements:
        if isinstance(e, Platform):
            assert abs(tilt_deg(w.model, w.data, e.name)) < LEVEL_DEG


def test_bump_range_start_faces_the_bump_and_balls_lie_past_it():
    rng = load_range("2026-bump", ".")
    (bump,) = rng.elements
    assert bump.name == "bump" and bump.center == pytest.approx((4.625, 2.513))
    x, y, heading = rng.starts["approach"]
    toe = bump.center[0] - bump.depth / 2.0
    assert heading == 0.0 and y == pytest.approx(bump.center[1]) and toe - x == pytest.approx(1.5, abs=0.01)
    far = bump.center[0] + bump.depth / 2.0
    assert len(rng.floor_balls) == 6
    assert all(0.3 <= bx - far <= 0.6 for bx, _ in rng.floor_balls)
    assert np.ptp(rng.floor_balls[:, 1]) > 0.6             # a row across the robot's path


def test_live_view_draws_a_platform_as_a_mover():
    w = World(range_cfg(range={"file": "2023-charge-station", "start": "approach"}), None)
    names = [mv.name for mv in movers(w)]
    assert "element.charge_station" in names
    v = model_view(w, include_world=True)
    i = [mv["name"] for mv in v["movers"]].index("element.charge_station")
    deck = [g for g in v["world_geoms"] if g["name"].startswith("element.charge_station.")]
    assert deck and all(g["type"] == "box" for g in deck)
    assert any(g["mover"] == i for g in deck)
