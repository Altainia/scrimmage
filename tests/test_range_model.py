"""The test range in the MuJoCo model: goals as static plates, balls as free bodies, and the [range] config section.

tests/data/rangeless_golden.npz holds nbody, ngeom and the final qpos of two rangeless robots driven for 500 steps (see
_rangeless_run), written by running this module:
    .venv/bin/python tests/test_range_model.py
It was first written by the code before the range existed (the range changes nothing for them). It has been rewritten
since only when the battery model changed: the bus voltage solved within the step (the old lagged one had left the
swerve run oscillating, at 0 V), then each motor's current taken at its start-of-step speed, then the solve by Newton's
method (it lands nearer the root than the bracketed solve's 0.1 mV stop: the kitbot's duty-cycle drive moved by 1e-7 m;
the swerve's 6 V request is below the bus, so its bus doesn't touch the physics), and when the stock wheels became
tread wheels (sub-project 10: the swerve's scrubbing turn ends at heading -19.7 deg, not -18.1; the kitbot is MJCF).
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import mujoco
import numpy as np
import pytest

from range_fixtures import range_cfg
from scrimmage.config import build_world, load_config
from scrimmage.launcher.inputs import _build_section
from scrimmage.parts.model import PartError
from scrimmage.protocol import HalState
from scrimmage.testrange.spec import Goal, Opening, Plate, load_range
from scrimmage.world import World

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "data" / "rangeless_golden.npz"
RANGELESS = {"swerve": "configs/swerve.toml", "kitbot": "tests/fake_robot.toml"}


def _rangeless_run(config: str) -> World:
    """500 steps of a rangeless robot with a fixed HAL command: swerve.toml binds only TalonFX CAN ids, so its four
    drive motors get 6 V through CTRE's HAL key (half of 12 V); the kitbot's two drive PWMs run at 0.5."""
    w = build_world(load_config(ROOT / config))
    hal = HalState()
    hal.devices[("DriverStation", "")] = {">enabled": True}
    if config.endswith("swerve.toml"):
        for i in (1, 3, 5, 7):
            hal.devices[("CANMotor", f"Talon FX (v6)[{i}]")] = {"<motorVoltage": 6.0}
    else:
        for port in ("0", "1"):
            hal.devices[("PWM", port)] = {"<init": True, "<speed": 0.5}
    for _ in range(500):
        w.step(hal)
    return w


def write_golden() -> None:
    arrays = {}
    for key, config in RANGELESS.items():
        w = _rangeless_run(config)
        arrays[f"{key}_nbody"] = np.array(w.model.nbody)
        arrays[f"{key}_ngeom"] = np.array(w.model.ngeom)
        arrays[f"{key}_qpos"] = w.data.qpos.copy()
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    np.savez(GOLDEN, **arrays)


@pytest.mark.parametrize("key", sorted(RANGELESS))
def test_rangeless_world_unchanged(key):
    golden = np.load(GOLDEN)
    w = _rangeless_run(RANGELESS[key])
    assert w.model.nbody == int(golden[f"{key}_nbody"])
    assert w.model.ngeom == int(golden[f"{key}_ngeom"])
    # rtol for MuJoCo across Python versions and machines (CI); the robot moved, so a change would show
    np.testing.assert_allclose(w.data.qpos, golden[f"{key}_qpos"], rtol=1e-12, atol=1e-12)
    assert getattr(w, "test_range", None) is None


# ---------------------------------------------------------------------------------------------------- the model

def geom_names(w) -> list[str]:
    m = w.model
    return [mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g) or "" for g in range(m.ngeom)]


def test_range_turns_off_obstacle():
    w = World(range_cfg(), None)
    assert "obstacle" not in geom_names(w)
    assert "carpet" in geom_names(w) and "wall_blue" in geom_names(w)


def test_obstacle_true_with_range_rejected():
    with pytest.raises(PartError, match="the range's field elements replace the center obstacle"):
        World(range_cfg(field={"obstacle": True}), None)
    assert "obstacle" not in geom_names(World(range_cfg(field={"obstacle": False}), None))


def test_hub_plates_are_static_field_geoms():
    from scrimmage.movers import movers
    from scrimmage.parts.field import FIELD_BITS
    from scrimmage.parts.assemble import ROBOT_BITS
    w = World(range_cfg(), None)
    m = w.model
    assert w.parts.layout.goal_bodies == ["hub_blue", "hub_red"]
    mover_ids = {mv.id for mv in movers(w) if mv.kind == "body"}
    for goal in ("hub_blue", "hub_red"):
        b = m.body(goal).id
        assert int(m.body_weldid[b]) == 0 and b not in mover_ids       # welded to the world
        geoms = [g for g in range(m.ngeom) if m.geom_bodyid[g] == b]
        assert len(geoms) == 6 + 4 + 1                                 # funnel walls, body walls, the net
        for g in geoms:
            assert (int(m.geom_contype[g]), int(m.geom_conaffinity[g])) == (FIELD_BITS["contype"], FIELD_BITS["conaffinity"])
            # touches the robot: (ct1 & ca2) | (ct2 & ca1)
            assert (m.geom_contype[g] & ROBOT_BITS["conaffinity"]) | (ROBOT_BITS["contype"] & m.geom_conaffinity[g])
            assert min(m.geom_size[g]) == pytest.approx(0.01)          # 0.02 m thick
    assert not [n for n in w.movable_objects() if n.startswith("hub_") or n.startswith("ball.")]


def test_hub_funnel_walls_meet_the_rim_and_throat():
    """Each funnel wall's inner face holds its rim side's midpoint and its throat side's midpoint."""
    w = World(range_cfg(), None)
    goal = w.parts.range.goals[0]
    m, d = w.model, w.data
    rim = goal.rim_vertices()
    throat = goal.scoring_surface()
    tv = throat.center + throat.polygon @ throat.axes
    for k in range(6):
        g = m.geom(f"hub_blue.funnel_{k}").id
        rot = d.geom_xmat[g].reshape(3, 3)
        inner = rot[:, 2]                                   # the box's thin axis, pointing into the funnel
        face = d.geom_xpos[g] + inner * m.geom_size[g][2]
        for pts in (rim, tv):
            mid = (pts[k] + pts[(k + 1) % 6]) / 2
            assert abs((mid - face) @ inner) < 1e-9
        assert inner[2] > 0 and 2 * m.geom_size[g][0] == pytest.approx(np.linalg.norm(rim[1] - rim[0]))


def ball_ids(w) -> list[int]:
    m = w.model
    return [b for b in range(m.nbody) if (mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, b) or "").startswith("ball.")]


def test_ball_bodies_fixed_count():
    """With floor balls, one body per floor ball, on the carpet; with them off, none (parked, 360 balls would still
    cost each physics step about as much as the robot), so a reset can't put them out."""
    from scrimmage.testrange.build import BALL_BITS
    on = World(range_cfg(range={"floor_balls": True}), None)
    off = World(range_cfg(), None)
    n_floor = len(on.parts.range.floor_balls)
    pool = sum(s.pool() for s in on.parts.range.stations)              # the OUTPOSTS' FUEL, parked after them
    assert n_floor == 360 and on.parts.layout.n_floor == n_floor
    names = [mujoco.mj_id2name(on.model, mujoco.mjtObj.mjOBJ_BODY, b) for b in ball_ids(on)]
    assert names == [f"ball.{i}" for i in range(n_floor + 0 + pool)]   # capacity 0: no [shooter]
    assert on.parts.layout.ball_names == names
    assert len(ball_ids(off)) == pool and off.parts.layout.ball_names == [f"ball.{i}" for i in range(pool)]
    assert off.parts.layout.n_floor == 0
    with pytest.raises(ValueError, match="built with \\[range\\] floor_balls = false"):
        off.test_range.reset(floor_balls=True)
    r = on.parts.range.ball.diameter / 2
    for i, b in enumerate(ball_ids(on)[:n_floor]):
        g = on.model.body_geomadr[b]
        assert on.model.body_geomnum[b] == 1 and on.model.geom_size[g][0] == pytest.approx(r)
        assert on.model.body_mass[b] == pytest.approx(on.parts.range.ball.mass)
        assert (on.model.geom_contype[g], on.model.geom_conaffinity[g]) == (BALL_BITS["contype"], BALL_BITS["conaffinity"])
        assert on.data.xpos[b] == pytest.approx([*on.parts.range.floor_balls[i], r])


def test_hopper_slots_are_parked_after_the_floor_balls():
    from scrimmage.parts.field import add_field
    from scrimmage.testrange.build import PARK_Z, PARKED_BITS, add_range
    from scrimmage.testrange.spec import load_range
    rng = load_range("2026", ROOT)
    spec = mujoco.MjSpec()
    spec.option.timestep = 0.001                                        # MjSpec's default 2 ms is too long for a range
    add_field(spec, {}, obstacle_default=False)
    layout = add_range(spec, rng, 5)
    m = spec.compile()
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    pool = sum(s.pool() for s in rng.stations)                          # the OUTPOSTS' FUEL, after the hopper's
    assert layout.n_floor == 360 and layout.ball_names == [f"ball.{i}" for i in range(365 + pool)]
    assert layout.robot_slots == range(360, 365)
    slots = [m.body(f"ball.{i}").id for i in range(360, 365)]
    xy = {tuple(np.round(d.xpos[b][:2], 6)) for b in slots}
    assert len(xy) == 5                                                 # each slot parks under its own (x, y)
    for b in slots:
        g = m.body_geomadr[b]
        assert d.xpos[b][2] == pytest.approx(PARK_Z)
        assert (m.geom_contype[g], m.geom_conaffinity[g]) == (PARKED_BITS["contype"], PARKED_BITS["conaffinity"])


# ---------------------------------------------------------------------------------------------------- config

def robot_qpos(w) -> np.ndarray:
    return w.data.qpos[w.free_qadr:w.free_qadr + 7].copy()


def test_named_start_sets_pose():
    named = World(range_cfg(range={"start": "blue_left"}), None)
    posed = World(range_cfg(robot={"start_pose": [3.42, 5.56, 0.0]}), None)
    np.testing.assert_allclose(robot_qpos(named), robot_qpos(posed), atol=1e-12)
    x, y, _ = named.robot_pose()
    assert (x, y) == pytest.approx((3.42, 5.56), abs=0.05)


def test_start_and_start_pose_rejected():
    with pytest.raises(PartError, match=r"\[range\] start.*\[robot\] start_pose"):
        World(range_cfg(range={"start": "blue_left"}, robot={"start_pose": [2.0, 4.0, 0.0]}), None)


def test_unknown_start_lists_names():
    with pytest.raises(PartError, match="blue_middle") as e:
        World(range_cfg(range={"start": "blue_middle"}), None)
    assert all(n in str(e.value) for n in ("blue_left", "blue_center", "blue_right"))


def test_range_with_mjcf_rejected():
    cfg = {"model": {"mjcf": "models/kitbot.xml"}, "robot": {"body": "chassis"}, "range": {"file": "2026"}}
    with pytest.raises(PartError, match=r"a range needs a robot built from \[\[part\]\]"):
        World(cfg, ROOT / "tests")


def test_preload_without_shooter_rejected():
    with pytest.raises(PartError, match=r"preload.*\[shooter\]"):
        World(range_cfg(range={"preload": 8}), None)
    World(range_cfg(range={"preload": 0}), None)


def test_bad_range_keys_rejected():
    with pytest.raises(PartError, match="unknown key 'flor_balls'"):
        World(range_cfg(range={"flor_balls": True}), None)
    with pytest.raises(PartError, match="floor_balls must be true or false"):
        World(range_cfg(range={"floor_balls": 1}), None)
    with pytest.raises(PartError, match="preload must be a whole number"):
        World(range_cfg(range={"preload": -1}), None)
    with pytest.raises(PartError, match="built-in ranges: 2026"):
        World(range_cfg(range={"file": "nowhere.toml"}), ROOT)


# ---------------------------------------------------------------------------------------------------- contact

def drop_world(ball, *, goal=None, at=(8.27, 4.035), height=1.0):
    """The generated field (no obstacle) with one ball, its bottom `height` above the carpet (or above a horizontal
    plate's top face, for `goal`); the assembler's solver options."""
    from scrimmage.parts.field import add_field
    from scrimmage.testrange.build import add_range
    from scrimmage.testrange.spec import load_range
    rng = load_range("2026", ROOT)
    rng = dataclasses.replace(rng, ball=ball, goals=() if goal is None else (goal,), floor_balls=np.array([at]))
    spec = mujoco.MjSpec()
    spec.compiler.degree = False
    spec.option.timestep = 0.001
    spec.option.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST
    spec.option.cone = mujoco.mjtCone.mjCONE_ELLIPTIC
    spec.option.impratio = 10
    add_field(spec, {}, obstacle_default=False)
    add_range(spec, rng, 0)
    m = spec.compile()
    d = mujoco.MjData(m)
    return m, d


def bounce(m, d, floor_z: float, height: float) -> tuple[float, float]:
    """Drop the ball from `height` (its bottom above floor_z): (impact speed, rebound speed)."""
    b = m.body("ball.0").id
    qa, da = m.jnt_qposadr[m.body_jntadr[b]], m.jnt_dofadr[m.body_jntadr[b]]
    r = m.geom_size[m.body_geomadr[b]][0]
    d.qpos[qa + 2] = floor_z + r + height
    mujoco.mj_forward(m, d)
    v_in, v_out, touched = 0.0, 0.0, False
    for _ in range(int(1.5 / m.opt.timestep)):
        mujoco.mj_step(m, d)
        vz = float(d.qvel[da + 2])
        if d.ncon == 0 and not touched:
            v_in = max(v_in, -vz)
        else:
            touched = True
            v_out = max(v_out, vz)
            if vz < 0 and v_out > 0 and d.ncon == 0:
                break
    return v_in, v_out


@pytest.mark.parametrize("e", [0.3, 0.6, 0.8])
def test_drop_reproduces_restitution(e):
    from scrimmage.testrange.spec import load_range
    ball = dataclasses.replace(load_range("2026", ROOT).ball, restitution=e)
    m, d = drop_world(ball)
    v_in, v_out = bounce(m, d, 0.0, 1.0)
    assert v_in == pytest.approx(math.sqrt(2 * 9.81 * 1.0), rel=0.02)
    assert v_out == pytest.approx(e * v_in, abs=0.05 * v_in)


@pytest.mark.parametrize("e", [0.3, 0.6, 0.8])
def test_drop_on_hub_plate(e):
    from scrimmage.testrange.spec import Goal, Opening, Plate, load_range
    ball = dataclasses.replace(load_range("2026", ROOT).ball, restitution=e)
    # a hoop at 1.0 m with a horizontal deck 1 m in front of it, its top face at 0.5 m (pitch 90: normal along z)
    goal = Goal(name="test", pos=(8.0, 4.0), heading_deg=0.0, rim_height=1.0, tilt_deg=0.0,
                opening=Opening(sides=6, across_flats=0.4), funnel=None, body=None,
                plates=(Plate(name="deck", pos=(1.0, 0.0, -0.51), size=(0.6, 0.6), rpy_deg=(0.0, 90.0, 0.0)),))
    m, d = drop_world(ball, goal=goal, at=(9.0, 4.0))
    assert m.geom("test.deck").id >= 0 and m.geom("test.rim_0").id >= 0
    v_in, v_out = bounce(m, d, 0.5, 1.0)
    assert v_in == pytest.approx(math.sqrt(2 * 9.81 * 1.0), rel=0.02)
    assert v_out == pytest.approx(e * v_in, abs=0.05 * v_in)
    b = m.body("ball.0").id
    assert d.xpos[b][2] > 0.5                                           # it bounced off the deck, not the carpet


def plate_hit(ball, v: float) -> tuple[float, float]:
    """A normal hit at v (m/s) on a free 2 cm plate, no gravity: (the ball's x past the plate's middle, rebound speed
    along the normal); the plate's middle is at x = 9.0."""
    goal = Goal(name="test", pos=(8.0, 4.0), heading_deg=0.0, rim_height=1.0, tilt_deg=0.0,
                opening=Opening(sides=6, across_flats=0.4), funnel=None, body=None,
                plates=(Plate(name="wall", pos=(1.0, 0.0, 0.0), size=(1.0, 1.0)),))
    m, d = drop_world(ball, goal=goal, at=(8.7, 4.0))
    m.opt.gravity[:] = 0
    b = m.body("ball.0").id
    qa, da = m.jnt_qposadr[m.body_jntadr[b]], m.jnt_dofadr[m.body_jntadr[b]]
    d.qpos[qa + 2] = 1.0
    d.qvel[da] = v
    touched = False
    for _ in range(int(0.3 / m.opt.timestep)):      # until the ball leaves the plate (or 0.3 s)
        mujoco.mj_step(m, d)
        touched = touched or d.ncon > 0
        if touched and d.ncon == 0:
            break
    assert np.all(np.isfinite(d.qpos))
    return float(d.qpos[qa] - 9.0), float(-d.qvel[da])


@pytest.mark.parametrize("v", [30.0, 40.0, 50.0])
@pytest.mark.parametrize("preset", ["2020-power-cell", "2022-cargo", "2026-fuel", "2016-boulder", "2017-fuel", "2019-cargo",
                                    "2025-algae", "2014-ball", "2010-soccer-ball", "2012-basketball"])
def test_fast_hit_on_plate_bounces_back(preset, v):
    """At the default 1 ms step a fast ball doesn't pass through a 2 cm plate, and bounces back at no more than
    0.08 of its speed above e. That margin is the step's: a ball arriving at 40 m/s is already up to 4 cm into the
    plate at the first step that sees the contact (build.py's module docstring), e.g. 0.67 for e = 0.6 at 40 m/s."""
    ball = _build_section("ball", {"preset": preset})
    past, rebound = plate_hit(ball, v)
    assert past < 0                                                     # still on its own side
    assert 0 < rebound <= (ball.restitution + 0.08) * v


@pytest.mark.parametrize("bad", ["fast", -0.001, 0.0, True, float("nan")])
def test_bad_timestep_named(bad):
    """[model] timestep is checked before the range (or the parts robot) uses it: an error naming the key, not a bare
    float() error or a range contact built for a negative step."""
    with pytest.raises(PartError, match=r"\[model\] timestep must be a positive number of seconds"):
        World(range_cfg(model={"timestep": bad}), None)
    with pytest.raises(PartError, match=r"\[model\] timestep must be a positive number of seconds"):
        World({"part": [{"name": "base", "stock": "chassis"}], "model": {"timestep": bad}}, None)


def test_range_timestep_too_long_rejected():
    with pytest.raises(PartError, match=r"timestep of at most 0.001 s, not 0.002"):
        World(range_cfg(model={"timestep": 0.002}), None)
    World(range_cfg(model={"timestep": 0.0005}), None)


def test_bouncy_ball_rejected():
    from scrimmage.testrange.build import MAX_RESTITUTION, ball_contact, damping_ratio
    ball = load_range("2026", ROOT).ball
    for e in (0.97, 1.0):
        with pytest.raises(ValueError, match=r"restitution .* at most 0.95"):
            ball_contact(dataclasses.replace(ball, restitution=e), 0.001)
    ball_contact(dataclasses.replace(ball, restitution=MAX_RESTITUTION), 0.001)
    for e in (0.0, 1.0):
        with pytest.raises(ValueError, match="above 0 and below 1"):
            damping_ratio(e)


def test_resting_ball_stays_put():
    w = World(range_cfg(range={"floor_balls": True}), None)
    b = w.model.body("ball.0").id
    start = w.data.xpos[b].copy()
    hal = HalState()
    for _ in range(2000):
        w.step(hal)
    assert np.linalg.norm(w.data.xpos[b] - start) < 1e-3


if __name__ == "__main__":
    write_golden()
