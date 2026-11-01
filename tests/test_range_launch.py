"""Leaving the launcher: a launched ball's exit state in the world (the robot's motion added), its collision bits while
it clears the robot, and the per-ball log of every fired, stuck and missed ball.

The world tests use range_fixtures.shooter_cfg's kitbot: the launcher (PWM 2) on its top mount, a hopper floor stage
(PWM 5) and a feeder stage (PWM 6), on the 2026 range with its floor balls off. A fixed hood keeps the exit angle
where the test puts it."""

from __future__ import annotations

import math

import numpy as np
import pytest

from range_fixtures import shooter_cfg
from scrimmage.launcher.model import Energy, ExitedBall
from scrimmage.protocol import HalState
from scrimmage.testrange.balls import AT_REST_SPEED, BallState
from scrimmage.testrange.build import BALL_BITS, LAUNCHED_BITS
from scrimmage.testrange.launch import LAUNCH_CLEAR_S, exit_state
from scrimmage.world import World

R_PATH = 0.11
HUB = (4.625, 4.035)        # the blue hub's opening center (2026.toml)
RIM = 1.829


def shot(launch_deg=50.0, yaw_deg=0.0, lane=0.08, speed=12.0, spin=150.0, t_exit=1.0) -> ExitedBall:
    return ExitedBall(index=0, status="launched", t_enter=t_exit - 0.02, t_exit=t_exit, speed=speed, spin=spin,
                      launch_deg=launch_deg, yaw_deg=yaw_deg, lane=lane, energy=Energy(*([0.0] * 7)), exit_slip=0.1)


def rot_z(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


BASE = np.array([1.0, 2.0, 0.5])


def local_exit(launch_deg=50.0, lane=0.08) -> np.ndarray:
    phi = math.radians(launch_deg + 90.0)
    return np.array([R_PATH * math.cos(phi), lane, R_PATH * math.sin(phi)])


def launch_dir(launch_deg=50.0, yaw_deg=0.0) -> np.ndarray:
    a, y = math.radians(launch_deg), math.radians(yaw_deg)
    return np.array([math.cos(a) * math.cos(y), math.cos(a) * math.sin(y), math.sin(a)])


# ------------------------------------------------------------------------------------------------ the exit state

def test_exit_point_and_velocity_at_rest():
    s = shot()
    pos, vel, _ = exit_state(s, R_PATH, BASE, rot_z(90.0), np.zeros(6), s.t_exit)
    np.testing.assert_allclose(pos, BASE + rot_z(90.0) @ local_exit(), rtol=0, atol=1e-9)
    np.testing.assert_allclose(vel, rot_z(90.0) @ (12.0 * launch_dir()), rtol=0, atol=1e-9)
    np.testing.assert_allclose(vel, [-0.0, 12.0 * math.cos(math.radians(50.0)), 12.0 * math.sin(math.radians(50.0))],
                               rtol=0, atol=1e-9)
    # the state at a later time: moved on at that velocity
    later, vel2, _ = exit_state(s, R_PATH, BASE, rot_z(90.0), np.zeros(6), s.t_exit + 0.002)
    np.testing.assert_allclose(later, pos + 0.002 * vel, rtol=0, atol=1e-12)
    np.testing.assert_allclose(vel2, vel, rtol=0, atol=0)


def test_robot_velocity_added():
    s = shot()
    body = np.array([0.0, 0.0, 0.0, 2.0, 0.0, 0.0])
    _, vel, _ = exit_state(s, R_PATH, BASE, np.eye(3), body, s.t_exit)
    np.testing.assert_allclose(vel, 12.0 * launch_dir() + [2.0, 0.0, 0.0], rtol=0, atol=1e-9)


def test_robot_rotation_added():
    s = shot(yaw_deg=8.0)
    body = np.array([0.0, 0.0, 3.0, 0.0, 0.0, 0.0])
    pos, vel, _ = exit_state(s, R_PATH, BASE, rot_z(30.0), body, s.t_exit)
    r = pos - BASE
    np.testing.assert_allclose(r, rot_z(30.0) @ local_exit(), rtol=0, atol=1e-12)
    expected = rot_z(30.0) @ (12.0 * launch_dir(yaw_deg=8.0)) + np.cross([0.0, 0.0, 3.0], r)
    np.testing.assert_allclose(vel, expected, rtol=0, atol=1e-9)


def test_backspin_axis_and_robot_omega():
    s = shot(yaw_deg=10.0, spin=200.0)
    body = np.array([0.0, 0.0, 3.0, 0.0, 0.0, 0.0])
    _, vel, w = exit_state(s, R_PATH, BASE, rot_z(90.0), body, s.t_exit)
    y = math.radians(10.0)
    axis = rot_z(90.0) @ np.array([math.sin(y), -math.cos(y), 0.0])     # flight.py's backspin axis, turned
    np.testing.assert_allclose(w, 200.0 * axis + [0.0, 0.0, 3.0], rtol=0, atol=1e-9)
    # backspin lifts: w x v points up for a ball moving forward
    assert np.cross(w - [0.0, 0.0, 3.0], vel)[2] > 0.0
    _, _, w_top = exit_state(shot(spin=-50.0), R_PATH, BASE, np.eye(3), np.zeros(6), 1.0)
    np.testing.assert_allclose(w_top, [0.0, 50.0, 0.0], rtol=0, atol=1e-12)    # topspin: the other way


# ------------------------------------------------------------------------------------------------ in the world

def pwm_hal(**pwm) -> HalState:
    hal = HalState()
    for port, speed in pwm.items():
        hal.devices[("PWM", port.removeprefix("p"))] = {"<init": True, "<speed": speed}
    return hal


def fixed_hood(angle: float, **launcher) -> dict:
    return {"hood": "fixed", "hood_angle": angle, "ids": {"shooter": "PWM:2"}, **launcher}


def check_ledger(w):
    rt = w.test_range
    c = rt.counts()
    assert sum(c["ledger"].values()) == rt.balls.n
    assert rt.ball_path.held() == c["held"]
    ids = [i for lane in rt.ball_path.lane_balls() for i in lane]
    assert len(ids) == len(set(ids)) == c["held"]
    assert all(rt.balls.state[i] == BallState.ROBOT for i in ids)


def shoot(w, spin_up: float, seconds: float, shooter: float, feed: float = 1.0, each=None) -> None:
    """Spin the shooter up for spin_up seconds, then feed for `seconds` more, calling each(w) after every step."""
    dt = w.model.opt.timestep
    for hal, t in ((pwm_hal(p2=shooter), spin_up), (pwm_hal(p2=shooter, p5=feed, p6=feed), seconds)):
        for _ in range(int(round(t / dt))):
            w.step(hal)
            if each is not None:
                each(w)


def robot_world(hood: float, preload: int, x: float = HUB[0] - 3.0, **launcher) -> World:
    w = World(shooter_cfg(launcher=fixed_hood(hood, **launcher), range={"preload": preload}), None)
    w.set_robot_pose(x, HUB[1], 0.0)
    return w


def no_warnings(w) -> bool:
    return all(int(wn.number) == 0 for wn in w.data.warning)


def test_exit_inside_bumper_flies_out():
    """Review Focus 1: the launcher sits low at the front, so its exit point (0.1108 m from the shaft at 140 deg round
    it, for the 50 deg hood) lies inside the front bumper's collision box (x 0.37..0.45 ahead of the chassis center,
    z 0.02..0.14). The ball ignores the robot while it clears it, and leaves at the speed it would have in free air."""
    phi = math.radians(140.0)
    ex, ez = 0.41, 0.115                                   # the exit point: inside the front bumper box
    # the base's offset from the chassis's top mount (0.12 m above the floor at the chassis center)
    pos = [ex - R_PATH_FIXTURE * math.cos(phi), 0.0, ez - R_PATH_FIXTURE * math.sin(phi) - 0.12]
    w = robot_world(50.0, 1, pos=pos)
    rt = w.test_range
    fired = []
    track = {}

    def each(w):
        if rt.balls.fired and not fired:
            i = next(k for k, log in enumerate(rt.balls.log) if log.launcher_status == "launched")
            fired.append((i, w.time))
            p = rt.balls.positions()[i]
            local = p - w.data.xpos[w.robot_body]
            assert abs(local[0] - 0.41) < 0.05 and p[2] < 0.15      # it starts inside the bumper
            assert rt.balls.model.geom_conaffinity[rt.balls.geoms[i]] == LAUNCHED_BITS["conaffinity"]
        if fired:
            i, t0 = fired[0]
            for dt_mark in (0.1, LAUNCH_CLEAR_S + 0.01):
                if dt_mark not in track and w.time >= t0 + dt_mark - 1e-9:
                    track[dt_mark] = (w.data.qvel[rt.balls._v[i, :3]].copy(), rt.balls.positions()[i].copy())

    shoot(w, 1.0, 2.0, shooter=0.6, each=each)
    assert fired, "no ball launched"
    i, _ = fired[0]
    log = rt.balls.log[i]
    v0 = launched_velocity(w, log)
    v_ref = free_flight(v0, 0.1, rt.balls)
    v, _ = track[0.1]
    assert np.linalg.norm(v) == pytest.approx(np.linalg.norm(v_ref), rel=0.05)
    np.testing.assert_allclose(v, v_ref, atol=0.05 * np.linalg.norm(v_ref))
    assert rt.balls.model.geom_conaffinity[rt.balls.geoms[i]] == BALL_BITS["conaffinity"] \
        or rt.balls.state[i] != BallState.FIELD           # it touches the robot again once clear
    assert no_warnings(w)


R_PATH_FIXTURE = 0.1016 / 2.0 + 0.12 / 2.0     # the stock launcher's wheel radius + half its hood gap


def launched_velocity(w, log) -> np.ndarray:
    """The launch velocity in the world for a robot at rest facing +x (heading 0)."""
    a = math.radians(log.hood_deg)
    return log.exit_speed * np.array([math.cos(a), 0.0, math.sin(a)])


def free_flight(v0: np.ndarray, seconds: float, balls) -> np.ndarray:
    """The velocity after `seconds` under gravity and drag alone (the lift is small over 0.1 s): RK4, 0.1 ms steps."""
    air, r = balls.air, balls.r
    m = balls.range.ball.mass
    k = 0.5 * air.density * math.pi * r * r * air.drag_coefficient / m

    def acc(v):
        return np.array([0.0, 0.0, -9.81]) - k * np.linalg.norm(v) * v

    v, h = v0.astype(float), 1e-4
    for _ in range(int(round(seconds / h))):
        k1 = acc(v)
        k2 = acc(v + h / 2 * k1)
        k3 = acc(v + h / 2 * k2)
        k4 = acc(v + h * k3)
        v = v + h / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
    return v


def test_stuck_ball_logged():
    """A hood gap at least the ball's diameter (0.1501) can't squeeze it: the launcher turns it away at the entry."""
    w = robot_world(50.0, 1, hood_gap=0.16)
    shoot(w, 0.2, 1.5, shooter=0.5, each=check_ledger)
    rt = w.test_range
    i = rt.layout.n_floor
    log = rt.ball_log()[i]
    assert log["launcher_status"] == "no_contact"
    assert log["outcome"] == "stuck" and rt.balls.state[i] == BallState.STUCK
    assert log["lane"] == 0 and log["fired_t"] is None and log["exit_speed"] is None
    assert rt.counts()["ledger"]["stuck"] == 1 and rt.counts()["fired"] == 0


# ------------------------------------------------------------------------------------------------ shooting at the hub

# Found by a manual search with this file's robot_world/shoot (the robot's center 3.0 m short of the blue hub's
# center, facing it; the launcher 0.1 m behind the center, its shaft 0.47 m up; 4 preloaded balls each run). The
# fixture's 0.12 m hood gap (0.03 m of squeeze) is grip-limited at about 6.3 m/s of exit speed, which reaches the
# hub's body or funnel and never scores from 3 m (hoods 40..70 deg in 5 deg steps, outputs 0.30..0.90 in 0.05 steps:
# 0 of 4 everywhere). With SCORE_GAP (0.05 m of squeeze) the exit speed tops out near 7.2 m/s and, scored of 4:
#   hood \ output  0.5  0.6  0.7  0.8  0.9
#   45 deg          0    0    2    4    4
#   50 deg          0    2    4    4    4
#   55 deg          0    2    4    4    4
#   60 deg          0    2    4    4    4
#   65 deg          0    0    0    0    0
# SCORE_HOOD / SCORE_OUTPUT is the middle of the band that scored all four; SHORT_OUTPUT falls well short of the hub.
SCORE_GAP = 0.10
SCORE_HOOD, SCORE_OUTPUT = 55.0, 0.8
SHORT_OUTPUT = 0.25


def score_run():
    w = robot_world(SCORE_HOOD, 4, hood_gap=SCORE_GAP)
    shoot(w, 1.5, 4.0, shooter=SCORE_OUTPUT, each=check_ledger)
    return w


@pytest.fixture(scope="module")
def scored():
    return score_run()


def test_shoot_and_score_end_to_end(scored):
    """Hood SCORE_HOOD, shooter output SCORE_OUTPUT from 3 m (see the search above): of the 4 preloads at least one
    scores, the ledger sums every step (check_ledger, also that the ball path's held() agrees with counts() after
    every launch), and a second identical run logs the same."""
    w = scored
    c = w.test_range.counts()
    assert c["fired"] == 4 and c["held"] == 0
    assert c["goals"]["hub_blue"] >= 1 and c["score"] == c["goals"]["hub_blue"]
    assert sum(c["ledger"].values()) == w.test_range.balls.n
    assert score_run().test_range.ball_log() == w.test_range.ball_log()


FIRING = ("fired_t", "robot_pose", "robot_velocity", "hood_deg", "shooter_rps", "exit_speed", "spin_rpm", "exit_slip",
          "lane", "launcher_status")


def finite(v) -> bool:
    if isinstance(v, tuple):
        return all(finite(x) for x in v)
    return v is not None and (isinstance(v, str) or math.isfinite(v))


def test_fired_ball_log_complete(scored):
    rt = scored.test_range
    logs = [log for log in rt.ball_log() if log["outcome"] == "scored"]
    assert logs
    log = logs[0]
    assert all(finite(log[k]) for k in FIRING), {k: log[k] for k in FIRING}
    assert log["launcher_status"] == "launched" and log["goal"] == "hub_blue" and log["scored_t"] > log["fired_t"]
    x, y, heading = log["robot_pose"]
    assert (x, y, heading) == pytest.approx((HUB[0] - 3.0, HUB[1], 0.0), abs=0.01)
    assert np.allclose(log["robot_velocity"], 0.0, atol=0.01)
    assert log["hood_deg"] == SCORE_HOOD and log["shooter_rps"] > 10.0 and log["exit_speed"] > 3.0
    assert log["spin_rpm"] > 0.0 and log["lane"] in (0, 1)
    assert log["nearest_goal"] == "hub_blue" and log["rim_height"] == pytest.approx(RIM)


def missed_run():
    """One ball shot well short of the hub, then 5 s more."""
    w = robot_world(SCORE_HOOD, 1, hood_gap=SCORE_GAP)
    shoot(w, 1.0, 0.6, shooter=SHORT_OUTPUT, each=check_ledger)
    fired = w.test_range.balls.log[w.test_range.layout.n_floor].fired_t
    assert math.isfinite(fired)
    shoot(w, 0.0, fired + 5.0 - w.time, shooter=SHORT_OUTPUT, each=check_ledger)
    return w


def test_missed_ball_says_why():
    w = missed_run()
    rt = w.test_range
    i = rt.layout.n_floor
    log = rt.ball_log()[i]
    assert log["launcher_status"] == "launched" and log["outcome"] == "on_field"
    assert log["nearest_goal"] == "hub_blue" and log["apex_z"] < log["rim_height"]
    assert log["first_hit"] == "carpet"
    assert log["last_pos"][0] < HUB[0] - 1.0 and log["last_pos"][2] == pytest.approx(rt.balls.r, abs=0.005)
    speed = float(np.linalg.norm(w.data.qvel[rt.balls._v[i, :3]]))
    assert log["at_rest"] == (speed < AT_REST_SPEED)


def test_missed_ball_at_rest_after_5_s():
    """The carpet's rolling resistance stops the missed ball within 5 s of the shot (it lands, bounces and rolls)."""
    w = missed_run()
    assert w.test_range.ball_log()[w.test_range.layout.n_floor]["at_rest"] is True
