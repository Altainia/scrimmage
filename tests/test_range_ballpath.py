"""The ball path inside the robot: lanes, the hopper, the feeder stages, the intake zone and the beam breaks.

The fixture robot (range_fixtures.shooter_cfg) has two lanes at -+0.08 m, a hopper floor stage (0.40 m, PWM 5) then a
feeder stage (0.25 m, PWM 6), both rollers 0.05 m across, and an intake roller (PWM 7) with its zone in front of the
bumper. The shooter (PWM 2) is never run here: a ball fed to the stopped launcher jams there."""

from __future__ import annotations

import math

import numpy as np
import pytest

from range_fixtures import shooter_cfg
from scrimmage.protocol import HalState
from scrimmage.testrange.balls import BallState
from scrimmage.world import World

FLOOR_L, FEEDER_L = 0.40, 0.25
R_ROLLER = 0.025
D = 0.1501                                  # the 2026 range ball's diameter
FORWARD = {"p0": 0.5, "p1": -0.5}          # the kitbot's right side is inverted


def pwm_hal(**pwm) -> HalState:
    hal = HalState()
    for port, speed in pwm.items():
        hal.devices[("PWM", port.removeprefix("p"))] = {"<init": True, "<speed": speed}
    return hal


def world(preload=0, **shooter) -> World:
    return World(shooter_cfg(range={"preload": preload}, shooter=shooter), None)


def run(w, seconds, hal, each=None):
    for _ in range(int(round(seconds / w.model.opt.timestep))):
        w.step(hal)
        if each is not None and each(w) is False:
            return


def dof(w, part) -> int:
    return int(w.model.jnt_dofadr[w.model.joint(f"{part}.spin").id])


def surface(w, part) -> float:
    return float(w.data.qvel[dof(w, part)]) * R_ROLLER


def path(w):
    return w.test_range.ball_path


def check_ledger(w):
    """Every ball in one state, the robot's balls each in exactly one place in the lanes."""
    rt = w.test_range
    c = rt.counts()
    assert sum(c["ledger"].values()) == rt.balls.n
    ids = [i for lane in path(w).lane_balls() for i in lane]
    assert len(ids) == len(set(ids))
    assert len(ids) == path(w).held() == c["held"]
    assert all(rt.balls.state[i] == BallState.ROBOT for i in ids)


# ------------------------------------------------------------------------------------------------ lanes

def test_preload_fills_emptiest_lane():
    w = world(preload=5, hopper={"capacity": 6})
    lanes = path(w).lanes_state()
    assert [len(lane) for lane in lanes] == [3, 2]
    assert all(where == "hopper" for lane in lanes for where, _ in lane)
    n_floor = w.parts.layout.n_floor
    assert path(w).lane_balls() == [[n_floor, n_floor + 2, n_floor + 4], [n_floor + 1, n_floor + 3]]
    assert [w.test_range.balls.log[n_floor + j].lane for j in range(5)] == [0, 1, 0, 1, 0]
    assert path(w).held() == 5 and w.test_range.counts()["held"] == 5


def test_lanes_too_close_rejected():
    with pytest.raises(ValueError, match=r"\[shooter\] lanes -0\.05 and 0\.05.*0\.1.*diameter"):
        world(lanes=[-0.05, 0.05])


# ------------------------------------------------------------------------------------------------ stages

def test_stage_moves_at_surface_speed():
    """Lane 0 holds two balls: the floor stage draws the first from the hopper, the second once the first is a
    diameter along, and both then move at the roller's surface speed, keeping their spacing."""
    w = world(preload=3)
    hal = pwm_hal(p5=0.5)
    run(w, 0.25, hal)              # the roller up to speed, both of lane 0's balls on the floor stage
    (a, sa), (b, sb) = path(w).lanes_state()[0]
    assert a == b == "floor" and sa - sb >= D - 1e-12
    gap = sa - sb
    s0, t0, traveled = sa, w.time, 0.0
    for _ in range(100):
        u = surface(w, "hopper_floor")
        w.step(hal)
        traveled += u * w.model.opt.timestep
    (_, s1), (_, s1b) = path(w).lanes_state()[0]
    omega = float(w.data.qvel[dof(w, "hopper_floor")])
    assert omega > 20.0
    assert s1 - s0 == pytest.approx(omega * R_ROLLER * (w.time - t0), rel=0.01)
    assert s1 - s0 == pytest.approx(traveled, rel=1e-9)
    assert s1 - s1b == pytest.approx(gap, abs=1e-12)


def test_stopped_stage_holds_balls():
    w = world(preload=2)
    run(w, 0.5, pwm_hal())
    assert path(w).lanes_state() == [[("hopper", 0.0)], [("hopper", 0.0)]]     # the floor never ran: none drawn
    run(w, 0.15, pwm_hal(p5=0.5))
    run(w, 0.2, pwm_hal())                         # the roller stops (PWM brake)
    assert abs(surface(w, "hopper_floor")) < 1e-9
    before = path(w).lanes_state()
    assert [lane[0][0] for lane in before] == ["floor", "floor"] and before[0][0][1] > 0.02
    run(w, 0.5, pwm_hal())
    assert path(w).lanes_state() == before


def test_chained_stages_hand_off():
    """A ball goes hopper -> floor -> feeder (entering it at 0) -> launcher; with the feeder stopped it waits at the
    feeder's entry and the ball behind waits a diameter back on the floor."""
    w = world(preload=3)
    run(w, 1.0, pwm_hal(p5=0.5))
    lane0 = path(w).lanes_state()[0]
    assert lane0[0] == ("feeder", 0.0)
    assert lane0[1][0] == "floor" and lane0[1][1] == pytest.approx(FLOOR_L - D, abs=1e-12)
    seen = []
    first_on_feeder = []

    def watch(w):
        lane = path(w).lanes_state()[0]
        where = lane[0][0] if lane else None
        if where == "feeder" and len(lane) > 1 and lane[1][0] == "feeder" and not first_on_feeder:
            first_on_feeder.append(lane[1][1])
        if not seen or seen[-1] != where:
            seen.append(where)
        check_ledger(w)

    run(w, 1.0, pwm_hal(p5=0.5, p6=0.5), watch)
    assert seen[:2] == ["feeder", "launcher"]
    assert first_on_feeder and first_on_feeder[0] < D      # the second ball came onto the feeder near its start


def test_ball_enters_launcher_with_stage_speed(monkeypatch):
    w = world(preload=2)
    launcher = w.test_range.shooter.launcher
    fed = []
    real = launcher.insert

    def insert(ball, t):
        fed.append((ball, t, surface(w, "feeder"), launcher.time, w.time))
        return real(ball, t)

    monkeypatch.setattr(launcher, "insert", insert)
    run(w, 2.0, pwm_hal(p5=0.5, p6=0.4), lambda w: len(fed) < 2)
    assert len(fed) == 2
    lanes = sorted(f[0].lane for f in fed)
    assert lanes == [-0.08, 0.08]
    for ball, t, u, now, _ in fed:
        assert u > 0.5
        assert ball.entry_speed == u
        assert ball.available_at == t == now
        assert ball.ball == w.test_range.range.ball
    assert path(w).lanes_state()[0][0][0] == "launcher"
    ids = {i for lane in path(w).lane_balls() for i in lane}
    assert ids == {w.parts.layout.n_floor, w.parts.layout.n_floor + 1}


def test_reverse_returns_balls():
    """Review Focus 4: lane 0 holds three balls, lane 1 two. First a ball waits at the stage boundary (the feeder
    stopped) with the next ball right behind it: the feeder alone reversing can't push it back onto the floor (the
    floor's end is taken), so it stays; both reversing send it back to the floor's end, and every ball back into the
    hopper in its order. Then at full speed a ball waits at the feeder's end for the launcher's entry (the
    stopped launcher holds the ball before it a while, then jams it back out) and both stages reverse: it goes back.
    The ledger sums to the ball count every step and no ball is in two places."""
    w = world(preload=5, hopper={"capacity": 5})
    n = w.parts.layout.n_floor
    assert path(w).lane_balls() == [[n, n + 2, n + 4], [n + 1, n + 3]]
    seen = []

    def each(w):
        check_ledger(w)
        seen.append(path(w).lanes_state()[0])

    run(w, 1.0, pwm_hal(p5=0.5), each)
    at_boundary = path(w).lanes_state()
    assert at_boundary[0][:2] == [("feeder", 0.0), ("floor", pytest.approx(FLOOR_L - D, abs=1e-12))]
    run(w, 0.3, pwm_hal(p6=-0.5), each)
    assert path(w).lanes_state() == at_boundary        # the floor's end is blocked by the ball right behind: it stays
    seen.clear()
    run(w, 2.0, pwm_hal(p5=-0.5, p6=-0.5), each)
    assert any(lane[0] == ("floor", FLOOR_L) for lane in seen)        # back to the floor's end
    assert path(w).lane_balls() == [[n, n + 2, n + 4], [n + 1, n + 3]]
    assert all(where == "hopper" for lane in path(w).lanes_state() for where, _ in lane)

    def waiting(w):
        check_ledger(w)
        lane = path(w).lanes_state()[0]
        return not (len(lane) >= 2 and lane[0][0] == "launcher" and lane[1] == ("feeder", FEEDER_L))

    run(w, 2.0, pwm_hal(p5=1.0, p6=1.0), waiting)
    assert not waiting(w), path(w).lanes_state()
    # the balls in the launcher never come back out; every other ball goes back in its order
    inside = [[i for i, (where, _) in zip(ids, lane) if where == "launcher"]
              for ids, lane in zip(path(w).lane_balls(), path(w).lanes_state())]
    assert inside[0] == [n]
    seen.clear()
    run(w, 2.0, pwm_hal(p5=-1.0, p6=-1.0), each)
    assert any(lane[0][0] == "feeder" and lane[0][1] < FEEDER_L - 0.05 for lane in seen if lane)
    lanes = path(w).lanes_state()
    assert all(where == "hopper" for lane in lanes for where, _ in lane)
    assert path(w).lane_balls() == [[n + 2, n + 4], [i for i in (n + 1, n + 3) if i not in inside[1]]]
    for i in inside[0] + inside[1]:
        assert w.test_range.balls.state[i] == BallState.STUCK
        assert w.test_range.balls.log[i].launcher_status == "jammed"


def test_reversed_ball_rests_on_the_stopped_stage_behind():
    """A ball at the boundary, the floor stopped and the feeder reversing: it goes to the floor's end and stays there
    (it isn't handed forward again, though the feeder keeps reversing), and a beam break at the boundary is steady."""
    w = world(preload=2, beam_break=[{"at": "stage:floor", "fraction": 1.0, "lane": 0, "dio": 0}])
    run(w, 1.0, pwm_hal(p5=0.5))
    assert path(w).lanes_state() == [[("feeder", 0.0)], [("feeder", 0.0)]]
    hal = pwm_hal(p6=-0.5)
    states, beam = [], []

    def each(w):
        states.append(path(w).lanes_state())
        beam.append(dio_values(w, hal)["0"])

    run(w, 0.5, hal, each)
    first = next(k for k, st in enumerate(states) if st[0][0] == ("floor", FLOOR_L))
    assert all(st == [[("floor", FLOOR_L)], [("floor", FLOOR_L)]] for st in states[first:])
    assert first < 10 and len(set(beam[first:])) == 1 and beam[-1] is False     # occupied (active-low) throughout


def spacing(w) -> float:
    """The smallest distance between neighbors on the path, over every lane."""
    pb = path(w)
    gaps = [pb._start[a[1]] + a[2] - pb._start[b[1]] - b[2] for lane in pb.path for a, b in zip(lane, lane[1:])]
    return min(gaps, default=np.inf)


@pytest.mark.parametrize("phases", [
    [(3.0, {"p5": 1.0, "p6": 1.0})],
    [(1.0, {"p5": 1.0}), (1.0, {"p5": 1.0, "p6": -1.0}), (1.0, {"p5": -1.0, "p6": 1.0}),
     (1.0, {"p5": 0.3, "p6": 1.0}), (1.0, {"p5": -0.5, "p6": -1.0})],
], ids=["full speed", "mixed directions"])
def test_balls_never_closer_than_a_diameter(phases):
    w = world(preload=5, hopper={"capacity": 5})
    closest = []
    for seconds, pwm in phases:
        run(w, seconds, pwm_hal(**pwm), lambda w: closest.append(spacing(w)))
    assert min(closest) >= D - 1e-9
    assert min(closest) < D + 1e-6           # balls did queue up against each other


# ------------------------------------------------------------------------------------------------ the intake

def zone_front(w):
    """A point on the floor just in front of the bumper, inside the intake zone (the robot's frame -> the world)."""
    x, y, yaw = w.robot_pose()
    ahead = 0.41 + 0.075 + 0.01
    body = w.data.xpos[w.model.body("drive.chassis").id]
    return np.array([body[0] + ahead * math.cos(yaw), body[1] + ahead * math.sin(yaw), 0.075])


def test_intake_needs_motor_inward():
    w = world()
    balls = w.test_range.balls
    i = w.parts.layout.n_floor
    from scrimmage.testrange.build import BALL_BITS
    balls.put(i, zone_front(w), BALL_BITS)
    for hal in (pwm_hal(), pwm_hal(p7=-0.5)):
        run(w, 0.5, hal)
        assert balls.state[i] == BallState.FIELD and path(w).held() == 0
    run(w, 0.5, pwm_hal(p7=0.5))
    assert balls.state[i] == BallState.ROBOT and path(w).held() == 1
    assert path(w).lanes_state() == [[("hopper", 0.0)], []]
    assert balls.positions()[i][2] < -10.0                 # parked


def test_intake_rate_and_capacity():
    """Review Focus 3: the robot drives into a pile of 12 balls with room for 5. It takes exactly 5, no faster than 8
    per second; the other 7 stay on the field, pushed by the bumper."""
    w = World(shooter_cfg(range={"floor_balls": True}, shooter={"hopper": {"capacity": 5}}), None)
    balls = w.test_range.balls
    pile = list(range(12))
    for i in range(12, w.parts.layout.n_floor):
        balls.set_state(i, BallState.SPARE)
        balls.park(i)
    front = zone_front(w)
    from scrimmage.testrange.build import BALL_BITS
    for k, i in enumerate(pile):
        row, col = divmod(k, 4)
        balls.put(i, front + [0.3 + 0.16 * row, -0.24 + 0.16 * col, 0.0], BALL_BITS)
    start = balls.positions()[pile].copy()
    picks: list[float] = []

    def each(w):
        held = path(w).held()
        assert held <= 5
        if held > len(picks):
            picks.append(w.time)
        check_ledger(w)

    run(w, 2.5, pwm_hal(p7=0.5, **FORWARD), each)
    assert path(w).held() == 5 == len(picks)
    assert np.all(np.diff(picks) >= 1.0 / 8.0 - 1e-9)
    left = [i for i in pile if balls.state[i] == BallState.FIELD]
    assert len(left) == 7
    moved = np.linalg.norm(balls.positions()[left] - start[[pile.index(i) for i in left]], axis=1)
    assert np.all(moved > 0.05), moved
    assert np.all(balls.positions()[left][:, 2] > 0.0)


# ------------------------------------------------------------------------------------------------ beam breaks

BEAMS = [{"at": "hopper", "dio": 0},
         {"at": "stage:feeder", "fraction": 0.5, "dio": 1},
         {"at": "launcher", "dio": 2},
         {"at": "stage:floor", "fraction": 0.5, "lane": 1, "dio": 3, "active_low": False},
         {"at": "hopper", "lane": 0, "dio": 4, "active_low": False}]


def dio_values(w, hal) -> dict:
    return {dev: v["<>value"] for typ, dev, v in w.sensor_updates(hal) if typ == "DIO"}


def test_beam_breaks():
    """Each beam break toggles as a ball passes it (active-low reads False while a ball is there)."""
    w = world(preload=3, beam_break=BEAMS)
    hal = pwm_hal(p5=0.5, p6=0.5)
    assert sorted(path(w).device_keys()) == [("DIO", str(d)) for d in range(5)]
    history = {str(d): [] for d in range(5)}

    def each(w):
        for dev, v in dio_values(w, hal).items():
            if not history[dev] or history[dev][-1] != v:
                history[dev].append(v)

    each(w)
    run(w, 1.5, hal, each)
    assert history["0"] == [False, True]                   # a ball in the hopper, then none
    assert history["1"] == [True, False, True, False, True]          # lane 0's two balls past the middle
    assert history["2"][:2] == [True, False]               # lane 0's first ball into the launcher
    assert history["3"] == [False, True, False]            # lane 1's ball past the floor's middle (active high)
    assert history["4"] == [True, False]                   # lane 0's hopper: its second ball drawn


def test_beam_break_dio_conflict_rejected():
    cfg = shooter_cfg(shooter={"beam_break": [{"at": "launcher", "dio": 7}]},
                      digital=[{"channel": 7, "joint": "launcher.hood"}])
    with pytest.raises(ValueError, match=r"DIO 7 is bound by digital #1 and \[\[shooter.beam_break\]\] at launcher"):
        World(cfg, None)
    with pytest.raises(ValueError, match=r"DIO 2 is bound by .*hopper.* and .*launcher"):
        world(beam_break=[{"at": "hopper", "dio": 2}, {"at": "launcher", "dio": 2}])


# ------------------------------------------------------------------------------------------------ config

@pytest.mark.parametrize("shooter, match", [
    ({"stage": []}, r"\[shooter\] stage: at least one"),
    ({"stage": [{"name": "floor", "part": "launcher", "path_length": 0.4}]}, r"stage 'floor'.*'launcher'.*roller"),
    ({"stage": [{"name": "floor", "part": "hopper_floor", "path_length": 0.0}]}, r"path_length"),
    ({"stage": [{"name": "a", "part": "hopper_floor", "path_length": 0.4},
                {"name": "a", "part": "feeder", "path_length": 0.4}]}, r"two stages named 'a'"),
    ({"stage": [{"name": "a", "part": "hopper_floor", "path_length": 0.4, "speed": 2}]}, r"unknown key 'speed'"),
    ({"intake": {"part": "intake", "at": "drive.nowhere", "pos": [0, 0, 0], "size": [0.1, 0.1, 0.1], "rate": 8.0}},
     r"intake.*drive.nowhere"),
    ({"intake": {"part": "intake", "at": "drive.center", "pos": [0, 0, 0], "size": [0.1, 0.1, 0.1], "rate": 0}},
     r"intake.*rate"),
    ({"beam_break": [{"at": "stage:nope", "fraction": 0.5, "dio": 1}]}, r"beam_break.*'stage:nope'.*floor, feeder"),
    ({"beam_break": [{"at": "stage:floor", "dio": 1}]}, r"beam_break.*fraction"),
    ({"beam_break": [{"at": "hopper", "fraction": 0.5, "dio": 1}]}, r"beam_break.*fraction.*stage"),
    ({"beam_break": [{"at": "hopper", "lane": 2, "dio": 1}]}, r"beam_break.*lane 2"),
])
def test_ball_path_config_errors(shooter, match):
    with pytest.raises(ValueError, match=match):
        world(**shooter)


def test_intake_is_optional():
    w = world(intake=None)
    run(w, 0.05, pwm_hal(p7=1.0))
    assert path(w).held() == 0


def test_reset_robot_stops_the_shooter_and_keeps_its_balls():
    """reset_robot puts every mechanism at rest, the shooter shaft (run by the launcher model) too; a ball in the
    launcher model goes back to the front of its lane's hopper, so the robot still holds every ball it held."""
    w = world(preload=4)
    rt = w.test_range
    shaft = rt.shooter.launcher.shaft
    # stopped shooter: the fed balls jam in the launcher
    run(w, 2.0, pwm_hal(p5=0.5, p6=0.4), lambda w: "launcher" not in [p for lane in path(w).lanes_state()
                                                                       for p, _ in lane])
    in_launcher = [i for i, _ in path(w).in_launcher.values()]
    assert in_launcher
    shaft.speed = 300.0         # as if spun up (spinning it up for real would launch the jammed balls)
    w.step(pwm_hal())
    assert w.data.qvel[dof(w, "launcher")] > 250.0
    held = rt.counts()["held"]
    w.reset_robot()
    shaft = rt.shooter.launcher.shaft               # a fresh launcher model
    assert shaft.speed == 0.0 and w.data.qvel[dof(w, "launcher")] == 0.0
    assert path(w).in_launcher == {} and rt.shooter.launcher.positions() == []
    assert rt.counts()["held"] == held == 4
    for i in in_launcher:
        lane = rt.balls.log[i].lane
        assert i in path(w).hopper[lane]
    check_ledger(w)
    w.step(pwm_hal())
    assert shaft.speed == 0.0                       # stays at rest
    check_ledger(w)
