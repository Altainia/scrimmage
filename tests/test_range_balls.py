"""Balls in the world: the ledger, parking, flight forces, scoring, leaving the field, reset and repeatability."""

from __future__ import annotations

import dataclasses
import math
import time

import mujoco
import numpy as np
import pytest

from range_fixtures import range_cfg, spares_world
from scrimmage.launcher.flight import fly
from scrimmage.launcher.inputs import _build_section, parse_setup
from scrimmage.launcher.model import Energy, ExitedBall
from scrimmage.protocol import HalState
from scrimmage.testrange.balls import AT_REST_SPEED, BallState
from scrimmage.testrange.build import BALL_BITS, LAUNCHED_BITS, PARK_Z, PARKED_BITS
from scrimmage.testrange.spec import BUILTIN_RANGES
from scrimmage.world import World

FUEL = _build_section("ball", {"preset": "2026-fuel"})
R = FUEL.diameter / 2.0
HUB = (4.625, 4.035)        # hub_blue's opening center (scrimmage/testrange/ranges/2026.toml)
RIM = 1.829
RIM_INRADIUS = 1.060 / 2    # the opening's flats at +-y from the center


def world(floor_balls: bool = False) -> World:
    """The kitbot on the 2026 range; with its floor balls off, they are spares the tests can place."""
    return World(range_cfg(range={"floor_balls": True}), None) if floor_balls else spares_world(range_cfg())


def n_balls(w) -> int:
    return len(w.parts.layout.ball_names)


def ball_qpos(w, i) -> np.ndarray:
    m = w.model
    a = m.jnt_qposadr[m.body_jntadr[m.body(f"ball.{i}").id]]
    return w.data.qpos[a:a + 7]


def ball_qvel(w, i) -> np.ndarray:
    m = w.model
    a = m.jnt_dofadr[m.body_jntadr[m.body(f"ball.{i}").id]]
    return w.data.qvel[a:a + 6]


def spare(w) -> int:
    """A ball out of play (a parked floor ball when floor balls are off)."""
    return int(np.flatnonzero(w.test_range.balls.state == BallState.SPARE)[0])


def run(w, seconds, each=None):
    hal = HalState()
    for _ in range(int(round(seconds / w.model.opt.timestep))):
        w.step(hal)
        if each is not None:
            each(w)


def station_pool(w) -> int:
    """The balls parked for the range's stations (the 2026 OUTPOSTS' FUEL): spare until fed."""
    return sum(len(s) for s in w.parts.layout.station_slots.values())


def ledger_sum(w) -> int:
    return sum(w.test_range.counts()["ledger"].values())


def test_ledger_sums_to_ball_count():
    w = world(floor_balls=True)
    n = n_balls(w)
    pool = station_pool(w)
    assert w.parts.layout.n_floor == 360 and n == 360 + pool
    assert w.test_range.counts()["ledger"] == {"field": 360, "robot": 0, "stuck": 0, "scored": 0, "out": 0,
                                               "spare": pool}
    w.set_robot_pose(6.6, 3.0, 0.0)
    w.set_robot_force(400.0, 0.0)
    sums = []
    run(w, 3.0, lambda w: sums.append(ledger_sum(w)))
    assert set(sums) == {n}
    assert w.robot_pose()[0] > 7.2   # the robot got into the balls


# ---------------------------------------------------------------------------------------------------- flight

LAUNCH_SPEED, LAUNCH_DEG, BACKSPIN = 12.0, 50.0, 200.0
START = np.array([1.0, 1.0])     # field (x, y) under the launcher; the shot goes +x, clear of the hubs


def _setup():
    return parse_setup({"ball": {"preset": "2026-fuel"},
                        "wheels": {"diameter": 0.1016, "width": 0.05, "preset": "30A", "positions": [0.0]},
                        "shaft": {"wheel_inertia": 0.002}, "control": {"target_rps": 60.0, "kP": 0.2},
                        "hood": {"gap": 0.12, "exit_angle_deg": LAUNCH_DEG, "friction": 0.5},
                        "launcher": {"height": 0.5, "width": 0.3},
                        "goal": {"distance": 3.0, "height": 100.0, "diameter": 1.2},   # out of reach: fly to the floor
                        "air": {"preset": "default"}})


def _shot(spin):
    return ExitedBall(index=0, status="launched", t_enter=0.0, t_exit=0.0, speed=LAUNCH_SPEED, spin=spin,
                      launch_deg=LAUNCH_DEG, yaw_deg=0.0, lane=0.0, energy=Energy(0, 0, 0, 0, 0, 0, 0))


def _fly_in_world(spin, quat=(1.0, 0.0, 0.0, 0.0)):
    """Place a ball at the launcher's exit point (flight.py's), launched along +x with backspin `spin` (rad/s, about
    -y), its body turned to `quat`; step until its center comes down to the floor height (r). The floor point
    (interpolated as flight.py does) and the apex."""
    s = _setup()
    phi = math.radians(s.hood.exit_angle_deg + 90.0)
    rc = s.wheels.diameter / 2.0 + s.hood.gap / 2.0
    p0 = np.array([START[0] + rc * math.cos(phi), START[1], s.launcher.height + rc * math.sin(phi)])
    a = math.radians(LAUNCH_DEG)
    v0 = LAUNCH_SPEED * np.array([math.cos(a), 0.0, math.sin(a)])
    w = world()
    i = spare(w)
    w.test_range.balls.place(i, p0, v0, np.array([0.0, -spin, 0.0]), BALL_BITS)
    if quat != (1.0, 0.0, 0.0, 0.0):
        # turn the body without changing its world spin: a free joint's angular qvel is in the body's frame
        rot = np.zeros(9)
        mujoco.mju_quat2Mat(rot, np.array(quat))
        ball_qpos(w, i)[3:] = quat
        ball_qvel(w, i)[3:] = rot.reshape(3, 3).T @ np.array([0.0, -spin, 0.0])
    hal = HalState()
    apex, prev = p0[2], p0.copy()
    for _ in range(5000):
        w.step(hal)
        now = ball_qpos(w, i)[:3].copy()
        apex = max(apex, now[2])
        if now[2] <= R:
            f = (prev[2] - R) / (prev[2] - now[2])
            floor = prev[:2] + f * (now[:2] - prev[:2])
            return floor - START, apex, s
        prev = now
    raise AssertionError("the ball never came down")


def _reference(spin):
    """The documented flight law (launcher/flight.py's docstring: lift 1/2 rho C_L A |v|^2 (w_hat x v_hat)),
    integrated with RK4 at 1 ms."""
    s = _setup()
    air = s.air
    area = math.pi * R * R
    k = 0.5 * air.density * area / FUEL.mass
    phi = math.radians(s.hood.exit_angle_deg + 90.0)
    rc = s.wheels.diameter / 2.0 + s.hood.gap / 2.0
    pos = np.array([rc * math.cos(phi), 0.0, s.launcher.height + rc * math.sin(phi)])
    a = math.radians(LAUNCH_DEG)
    vel = LAUNCH_SPEED * np.array([math.cos(a), 0.0, math.sin(a)])
    axis = np.array([0.0, -1.0, 0.0])

    def acc(v):
        sp = np.linalg.norm(v)
        cl = min(air.lift_slope * R * abs(spin) / sp, air.lift_max)
        return np.array([0, 0, -9.80665]) - k * air.drag_coefficient * sp * v + k * cl * sp * np.cross(axis, v)

    dt, apex = 0.001, pos[2]
    while True:
        k1 = acc(vel)
        k2 = acc(vel + dt / 2 * k1)
        k3 = acc(vel + dt / 2 * k2)
        k4 = acc(vel + dt * k3)
        new_pos = pos + dt * (vel + dt / 6 * (k1 + k2 + k3))
        new_vel = vel + dt / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
        if new_pos[2] <= R:
            f = (pos[2] - R) / (pos[2] - new_pos[2])
            return pos[:2] + f * (new_pos[:2] - pos[:2]), apex
        pos, vel = new_pos, new_vel
        apex = max(apex, pos[2])


def test_flight_matches_launcher_flight():
    floor, apex, s = _fly_in_world(BACKSPIN)
    land = fly(_shot(BACKSPIN), s, FUEL)
    assert np.hypot(*(floor - np.array(land.floor))) < 0.05
    assert apex == pytest.approx(land.apex, abs=0.02)


def test_flight_without_spin_matches_launcher_flight():
    floor, apex, s = _fly_in_world(0.0)
    land = fly(_shot(0.0), s, FUEL)
    assert np.hypot(*(floor - np.array(land.floor))) < 0.05
    assert apex == pytest.approx(land.apex, abs=0.02)


@pytest.mark.parametrize("quat", [(1.0, 0.0, 0.0, 0.0), (0.5, 0.5, -0.5, 0.5)])
def test_flight_with_backspin_matches_lift_law(quat):
    """Backspin lift by the documented law, whatever the ball's orientation (its spin is read in the world frame)."""
    floor, apex, _ = _fly_in_world(BACKSPIN, quat)
    ref_floor, ref_apex = _reference(BACKSPIN)
    plain, plain_apex, _ = _fly_in_world(0.0)
    assert apex > plain_apex + 0.2 and floor[0] > plain[0] + 0.1    # the lift carries it higher and further
    assert np.hypot(*(floor - ref_floor)) < 0.05
    assert apex == pytest.approx(ref_apex, abs=0.02)


def test_drag_only_on_balls():
    w = world()
    w.set_robot_force(50.0, 0.0)
    for k in range(5):
        i = spare(w)
        w.test_range.balls.place(i, np.array([2.0 + k * 0.5, 1.0, 1.0]), np.array([3.0, 0.0, 5.0]),
                                 np.array([0.0, -100.0, 0.0]), BALL_BITS)
    run(w, 0.1)
    assert list(w.data.xfrc_applied[w.robot_body]) == [50.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    flying = [b for b in range(n_balls(w)) if w.test_range.balls.state[b] == BallState.FIELD]
    assert len(flying) == 5
    assert all(np.any(w.data.xfrc_applied[w.model.body(f"ball.{b}").id] != 0) for b in flying)


# ---------------------------------------------------------------------------------------------------- scoring

def _drop(w, x, y, z):
    i = spare(w)
    w.test_range.balls.place(i, np.array([x, y, z]), np.zeros(3), np.zeros(3), BALL_BITS)
    return i


def test_ball_dropped_into_funnel_scores():
    w = world()
    i = _drop(w, HUB[0], HUB[1] + 0.40, RIM + 0.3)   # off center: its edge meets a funnel wall
    events = []
    hal = HalState()
    for _ in range(3000):
        w.step(hal)
        events += w.test_range.events
    c = w.test_range.counts()
    assert c["goals"]["hub_blue"] == 1 and c["goals"]["hub_red"] == 0 and c["score"] == 1
    assert events == [(i, "scored")]
    log = w.test_range.ball_log()[i]
    assert log["outcome"] == "scored" and log["goal"] == "hub_blue" and log["scored_t"] > 0
    assert log["first_hit"].startswith("hub_blue.funnel_")
    assert w.test_range.balls.state[i] == BallState.SCORED
    assert ball_qpos(w, i)[2] == PARK_Z and not ball_qvel(w, i).any()
    g = w.model.geom(f"ball.{i}").id
    assert (w.model.geom_contype[g], w.model.geom_conaffinity[g]) == (0, 0)


def test_ball_bouncing_off_rim_does_not_score():
    w = world()
    i = _drop(w, HUB[0], HUB[1] + RIM_INRADIUS + 0.04, RIM + 0.3)   # center just outside the rim edge
    run(w, 3.0)
    assert w.test_range.counts()["score"] == 0
    log = w.test_range.ball_log()[i]
    assert log["outcome"] == "on_field" and log["goal"] is None
    assert log["first_hit"].startswith("hub_blue.")
    assert log["apex_z"] == pytest.approx(RIM + 0.3)
    assert log["last_pos"][2] < 0.1 and log["at_rest"] in (True, False)


def test_ball_out_of_field():
    w = world()
    i = spare(w)
    a = math.radians(45.0)
    w.test_range.balls.place(i, np.array([15.8, 2.0, 0.3]), 8.0 * np.array([math.cos(a), 0.0, math.sin(a)]),
                             np.zeros(3), BALL_BITS)
    on_field = w.test_range.counts()["on_field"]
    events = []
    hal = HalState()
    for _ in range(3000):
        w.step(hal)
        events += w.test_range.events
    assert events == [(i, "out")]
    assert w.test_range.balls.state[i] == BallState.OUT
    assert w.test_range.counts()["on_field"] == on_field - 1
    assert w.test_range.counts()["ledger"]["out"] == 1
    log = w.test_range.ball_log()[i]
    assert log["outcome"] == "out_of_field"
    x, y, z = log["last_pos"]
    assert x > 16.54 and 1.5 < y < 2.5
    assert ball_qpos(w, i)[2] == PARK_Z


def test_out_of_field_through_the_floor():
    w = world()
    i = spare(w)
    # touching nothing, so the carpet doesn't push it back up: it falls from just above the limit
    w.test_range.balls.place(i, np.array([5.0, 2.0, -0.99]), np.array([0.0, 0.0, -5.0]), np.zeros(3), PARKED_BITS)
    run(w, 0.01)
    assert w.test_range.balls.state[i] == BallState.OUT
    assert w.test_range.ball_log()[i]["last_pos"][2] < -1.0


def test_sleeping_ball_wakes_when_pushed():
    w = world(floor_balls=True)
    m, d = w.model, w.data
    assert m.opt.enableflags & mujoco.mjtEnableBit.mjENBL_SLEEP
    run(w, 0.5)
    b = m.body("ball.0").id
    assert d.tree_awake[m.body_treeid[b]] == 0            # resting balls fall asleep
    assert d.tree_awake[m.body_treeid[w.robot_body]] == 1  # the robot never does
    p0 = ball_qpos(w, 0)[:3].copy()
    x, y = w.parts.range.floor_balls[0]
    w.set_robot_pose(x - 0.8, y, 0.0)
    w.set_robot_force(300.0, 0.0)
    run(w, 1.5)
    assert np.linalg.norm(ball_qpos(w, 0)[:3] - p0) > 0.1


def rolled(w, speed: float) -> int:
    """A ball set rolling along +x on the carpet at `speed` (its spin matching), away from everything."""
    i = spare(w)
    w.test_range.balls.place(i, np.array([8.0, 2.0, R]), np.array([speed, 0.0, 0.0]),
                             np.array([0.0, speed / R, 0.0]), BALL_BITS)
    return i


def test_rolling_ball_stops_in_predicted_time():
    """The carpet's rolling resistance C (a torque C m g r against the roll) decelerates a rolling ball at
    C g / (1 + k), k its inertia factor: from 0.5 m/s it is at rest by that time, not much before (MuJoCo's own
    rolling friction adds a little), and it stays put."""
    w = world()
    c = w.parts.range.rolling_resistance
    i = rolled(w, 0.5)
    t_stop = 0.5 / (c * 9.81 / (1.0 + FUEL.inertia_factor))
    assert 0.5 < t_stop < 2.0
    speeds = []
    run(w, 1.5 * t_stop, lambda w: speeds.append(float(np.linalg.norm(ball_qvel(w, i)[:3]))))
    dt = w.model.opt.timestep
    rest = next(k for k, v in enumerate(speeds) if v < AT_REST_SPEED) * dt
    assert 0.6 * t_stop < rest <= 1.05 * t_stop
    assert max(speeds[int(round(1.05 * t_stop / dt)):]) < AT_REST_SPEED
    assert w.test_range.ball_log()[i]["at_rest"] is True


def test_resting_ball_still_sleeps_after_rolling():
    w = world()
    i = rolled(w, 0.5)
    run(w, 3.0)
    m, d = w.model, w.data
    assert d.tree_awake[m.body_treeid[w.test_range.balls.bodies[i]]] == 0
    assert np.all(d.xfrc_applied[w.test_range.balls.bodies] == 0.0)


def test_no_rolling_resistance_keeps_rolling(tmp_path):
    """With rolling_resistance 0 only MuJoCo's own (soft) rolling friction acts: the ball is still rolling at 2 s."""
    text = BUILTIN_RANGES["2026"].read_text().replace("rolling_resistance = ", "rolling_resistance = 0.0  # x ", 1)
    (tmp_path / "r.toml").write_text(text)
    w = spares_world(range_cfg(range={"file": str(tmp_path / "r.toml")}))
    assert w.parts.range.rolling_resistance == 0.0
    i = rolled(w, 0.5)
    run(w, 2.0)
    assert np.linalg.norm(ball_qvel(w, i)[:3]) > 0.2


def test_rangeless_world_never_sleeps():
    w = World(range_cfg(range=None), None)
    assert w.test_range is None
    assert not w.model.opt.enableflags & mujoco.mjtEnableBit.mjENBL_SLEEP


# ---------------------------------------------------------------------------------------------------- reset

def test_reset_restores_balls():
    w = world(floor_balls=True)
    qpos0 = w.data.qpos.copy()
    n_floor = w.parts.layout.n_floor
    w.set_robot_pose(6.6, 3.0, 0.0)
    w.set_robot_force(400.0, 0.0)
    w.test_range.balls.place(0, np.array([HUB[0], HUB[1] + 0.4, RIM + 0.3]), np.zeros(3), np.zeros(3), BALL_BITS)
    w.test_range.balls.place(1, np.array([15.8, 2.0, 0.3]), np.array([5.7, 0.0, 5.7]), np.zeros(3), BALL_BITS)
    run(w, 2.0)
    c = w.test_range.counts()
    assert c["score"] == 1 and c["ledger"]["out"] == 1
    w.reset()
    c = w.test_range.counts()
    assert c["score"] == 0 and c["goals"] == {"hub_blue": 0, "hub_red": 0} and c["fired"] == 0
    assert c["held"] == 0 and c["on_field"] == n_floor
    assert c["ledger"] == {"field": n_floor, "robot": 0, "stuck": 0, "scored": 0, "out": 0,
                           "spare": station_pool(w)}
    for i in range(n_floor):
        np.testing.assert_array_equal(ball_qpos(w, i), qpos0[w.model.jnt_qposadr[w.model.body_jntadr[
            w.model.body(f"ball.{i}").id]]:][:7])
        g = w.model.geom(f"ball.{i}").id
        assert (w.model.geom_contype[g], w.model.geom_conaffinity[g]) == (BALL_BITS["contype"],
                                                                         BALL_BITS["conaffinity"])
    log = w.test_range.ball_log()[0]
    assert log["outcome"] == "on_field" and log["goal"] is None and log["first_hit"] is None


def test_reset_overrides_stick():
    """A plain reset() (the bridge resets the world when the robot connects) keeps the last overrides."""
    w = world(floor_balls=True)
    w.test_range.reset(floor_balls=False, start="blue_left")
    assert w.test_range.counts()["on_field"] == 0
    w.reset()
    assert w.test_range.counts()["on_field"] == 0
    x, y, _ = w.robot_pose()
    assert (x, y) == pytest.approx((3.42, 5.56), abs=0.4)
    w.test_range.clear_overrides()
    w.reset()
    assert w.test_range.counts()["on_field"] == w.parts.layout.n_floor


def _scripted_run():
    w = world(floor_balls=True)
    w.set_robot_pose(6.6, 3.0, 0.0)
    w.set_robot_force(400.0, 0.0)
    balls = w.test_range.balls
    balls.place(0, np.array([HUB[0], HUB[1] + 0.4, RIM + 0.3]), np.zeros(3), np.zeros(3), BALL_BITS)
    balls.place(1, np.array([2.0, 6.0, 0.6]), np.array([8.0, 0.0, 6.0]), np.array([0.0, -150.0, 0.0]),
                LAUNCHED_BITS)
    balls.place(2, np.array([15.8, 2.0, 0.3]), np.array([5.7, 0.0, 5.7]), np.zeros(3), BALL_BITS)
    run(w, 2.0)
    return w.test_range.ball_log(), w.data.qpos.copy()


def test_same_inputs_same_outcome():
    log1, q1 = _scripted_run()
    log2, q2 = _scripted_run()
    assert log1 == log2
    np.testing.assert_array_equal(q1, q2)
    assert [b["outcome"] for b in log1[:3]] == ["scored", "on_field", "out_of_field"]
    import json
    json.dumps(log1, allow_nan=False)   # the report's JSON: unset values are None, not NaN


def test_full_2026_layout_runs():
    w = world(floor_balls=True)
    n = n_balls(w)
    assert w.parts.layout.n_floor == 360
    w.set_robot_pose(6.6, 4.0, 0.0)
    w.set_robot_force(500.0, 0.0)
    t0 = time.perf_counter()
    sums = []
    run(w, 2.0, lambda w: sums.append(ledger_sum(w)))
    wall = time.perf_counter() - t0
    print(f"full 2026 layout: 2.0 s simulated in {wall:.2f} s wall, speed factor {2.0 / wall:.1f}x")
    assert np.all(np.isfinite(w.data.qpos)) and np.all(np.isfinite(w.data.qvel))
    assert [x.number for x in w.data.warning] == [0] * len(w.data.warning)
    assert set(sums) == {n}
    assert wall < 60.0


def test_ball_poses_are_field_balls():
    w = world(floor_balls=True)
    w.test_range.balls.park(5)
    w.test_range.balls.set_state(5, BallState.SPARE)
    poses = w.test_range.ball_poses()
    assert poses.shape == (359, 7)
    assert np.all(poses[:, 2] > 0)
