import math
import time

import numpy as np

import pytest

from scrimmage.launcher import run as run_module
from scrimmage.launcher.inputs import LauncherConfigError, parse_setup
from scrimmage.launcher.model import FedBall
from scrimmage.launcher.run import METRIC_KEYS, run_setup

POWER_CELL_D = 0.1778

BASE = {
    "ball": {"preset": "2020-power-cell"},
    "wheels": {"diameter": 0.1016, "width": 0.05, "preset": "30A", "positions": [0.0]},
    "shaft": {"wheel_inertia": 0.002},
    "control": {"target_rps": 60.0, "kP": 0.2},
    "hood": {"gap": 0.8 * POWER_CELL_D, "exit_angle_deg": 40.0, "friction": 0.5},
    "launcher": {"height": 0.5, "width": 0.3},
    "goal": {"distance": 3.0, "height": 2.64, "diameter": 1.2},
    "feed": {"count": 5, "rate": 5.0, "rule": "schedule"},
}


def setup(**sections):
    raw = {k: dict(v) for k, v in BASE.items()}
    for name, changes in sections.items():
        raw.setdefault(name, {}).update(changes)
    return parse_setup(raw)


def test_metric_keys_in_order():
    r = run_setup(setup(feed={"count": 2}))
    assert tuple(r.metrics) == METRIC_KEYS
    assert r.metrics["fired"] == 2 and r.trace and len(r.trace) <= 2000


def test_same_seed_same_result():
    a = run_setup(setup(feed={"count": 3}, spread={"seed": 7}))
    b = run_setup(setup(feed={"count": 3}, spread={"seed": 7}))
    assert a.metrics == b.metrics and a.trace == b.trace


def test_different_seed_differs():
    a = run_setup(setup(feed={"count": 3}, spread={"seed": 7}))
    b = run_setup(setup(feed={"count": 3}, spread={"seed": 8}))
    assert a.metrics["exit_speed"] != b.metrics["exit_speed"]


def test_heavier_flywheel_tradeoff():
    light = run_setup(setup()).metrics
    heavy = run_setup(setup(shaft={"added_inertia": 0.004})).metrics
    assert heavy["speed_drop_pct"] < light["speed_drop_pct"]
    assert heavy["recovery_s"] > light["recovery_s"]


def test_wait_for_speed_slower_than_schedule():
    kw = dict(feed={"count": 5, "rate": 20.0, "entry_speed": 5.0}, shaft={"wheel_inertia": 0.001})
    sched = run_setup(setup(**{**kw, "feed": {**kw["feed"], "rule": "schedule"}})).metrics
    wait = run_setup(setup(**{**kw, "feed": {**kw["feed"], "rule": "wait_for_speed"}})).metrics
    assert wait["burst_s"] > sched["burst_s"]


def test_unrecoverable_burst_terminates():
    s = setup(
        shaft={"motor": "NEO550", "gearing": 4.0, "added_inertia": 0.05},
        feed={"rule": "wait_for_speed", "timeout": 2.0},
    )
    t0 = time.perf_counter()
    r = run_setup(s)
    assert time.perf_counter() - t0 < 30
    assert r.metrics["unfed"] > 0
    assert any(shot.status == "unfed" for shot, _ in r.shots)


def test_side_by_side_lanes():
    s = setup(wheels={"positions": [-0.08, 0.08]}, launcher={"width": 0.34},  # 2 x (0.08 + D/2) = 0.338
              feed={"count": 4, "rate": 2.0, "lanes": [-0.08, 0.08]})
    r = run_setup(s)
    enters = sorted(shot.t_enter for shot, _ in r.shots)
    assert enters[0] == enters[1] and enters[2] == enters[3] and enters[2] > enters[1]


def test_hopper_feed_is_used():
    s = setup()
    feed = [FedBall(available_at=0.1 * i, lane=0.0, entry_speed=1.0, ball=s.ball) for i in range(2)]
    r = run_setup(s, feed)
    assert r.metrics["fired"] == 2


def test_bad_feed_rule_rejected():
    with pytest.raises(LauncherConfigError, match="rule"):
        setup(feed={"rule": "whenever"})
    with pytest.raises(LauncherConfigError, match="count"):
        setup(feed={"count": 0})
    with pytest.raises(LauncherConfigError, match="rate"):
        setup(feed={"rate": 0.0})


def test_shooter_speed_spread_does_not_starve_wait_for_speed():
    s = setup(feed={"rule": "wait_for_speed"}, spread={"shooter_speed": 0.05})
    m = run_setup(s).metrics
    assert m["unfed"] == 0 and m["fired"] == 5


def test_one_ball_per_lane_at_a_time():
    s = setup(feed={"count": 3, "rate": 100.0, "entry_speed": 1.0}, shaft={"wheel_inertia": 0.05},
              spread={"feed_time": 0.0, "lane": 0.0, "diameter": 0.0, "entry_speed": 0.0})
    r = run_setup(s)
    enters = sorted(shot.t_enter for shot, _ in r.shots)
    gap = s.ball.diameter / 1.0
    assert enters[1] - enters[0] >= gap - 1e-6 and enters[2] - enters[1] >= gap - 1e-6


def test_dip_metrics_match_the_trace(monkeypatch):
    monkeypatch.setattr(run_module, "MAX_TRACE", 10**9)  # keep the whole trace
    s = setup(feed={"count": 1})
    r = run_setup(s)
    shot, _ = r.shots[0]
    t = np.array([p[0] for p in r.trace])
    w = np.array([p[1] for p in r.trace])  # rps
    target = s.control.target_rps
    i = int(np.argmin(abs(t - shot.t_enter)))
    stop = int(np.searchsorted(t, shot.t_exit, side="left"))
    m = i + int(np.argmin(w[i:stop + 1]))
    assert r.metrics["speed_drop_pct"] == pytest.approx(100.0 * (w[i] - w[m]) / target)
    assert r.metrics["min_speed_pct"] == pytest.approx(100.0 * w[m] / target)
    back = np.nonzero(w[m:] >= (1.0 - s.results.recovery_tolerance) * target)[0][0]
    assert r.metrics["recovery_s"] == pytest.approx(t[m + back] - t[m])
    assert r.metrics["speed_drop_pct"] > 0.5  # a real dip, so the check is not vacuous


def test_exit_slip_metric_is_the_mean_exit_slip():
    keys = list(METRIC_KEYS)
    assert keys.index("exit_slip_pct") == keys.index("spin_rpm") + 1
    r = run_setup(setup(feed={"count": 2}))
    slips = [shot.exit_slip for shot, _ in r.shots if shot.status == "launched"]
    assert slips and all(-0.05 <= x <= 1.0 for x in slips)
    assert r.metrics["exit_slip_pct"] == pytest.approx(100.0 * float(np.mean(slips)))



@pytest.mark.parametrize("changes, status", [
    ({"hood": {"gap": 1.1 * POWER_CELL_D}}, "no_contact"),  # gap >= D: no squeeze
    ({"launcher": {"width": 0.6}, "feed": {"lanes": [0.2]}}, "no_wheel"),  # the one wheel is at 0, not under 0.2
], ids=["no_contact", "no_wheel"])
def test_balls_that_never_touch_are_all_reported(changes, status):
    # Such a ball finishes the moment it is inserted; the last one must not be lost when the feed loop ends.
    feed = {"count": 3, **changes.pop("feed", {})}
    r = run_setup(setup(**changes, feed=feed))
    assert [shot.status for shot, _ in r.shots] == [status] * 3
    assert r.metrics["fired"] == 3


def test_landing_spread_uses_floor_points_when_some_balls_miss_the_goal_height(monkeypatch):
    # Mixing goal-height crossings with floor points would measure the distance between the two sets, not the
    # spread: with any ball short of the goal's height, every ball's floor point is used.
    from scrimmage.launcher.flight import Landing
    floors = iter([(4.0, 0.1), (4.2, -0.1), (4.4, 0.3)])
    at_goal = iter([(3.0, 0.0), None, (3.1, 0.0)])

    def fake_fly(shot, setup_, ball):
        return Landing(scored=False, at_goal=next(at_goal), floor=next(floors), apex=3.0, flight_time=1.0)

    monkeypatch.setattr(run_module, "fly", fake_fly)
    m = run_setup(setup(feed={"count": 3})).metrics
    assert m["launched"] == 3
    assert m["spread_downrange_m"] == pytest.approx(float(np.std([4.0, 4.2, 4.4])))
    assert m["spread_lateral_m"] == pytest.approx(float(np.std([0.1, -0.1, 0.3])))


def test_landing_spread_uses_goal_height_crossings_when_all_reach_it(monkeypatch):
    from scrimmage.launcher.flight import Landing
    floors = iter([(4.0, 0.1), (4.2, -0.1), (4.4, 0.3)])
    at_goal = iter([(3.0, 0.0), (3.2, 0.05), (3.1, 0.0)])

    def fake_fly(shot, setup_, ball):
        return Landing(scored=True, at_goal=next(at_goal), floor=next(floors), apex=3.0, flight_time=1.0)

    monkeypatch.setattr(run_module, "fly", fake_fly)
    m = run_setup(setup(feed={"count": 3})).metrics
    assert m["spread_downrange_m"] == pytest.approx(float(np.std([3.0, 3.2, 3.1])))


def test_hopper_feed_lane_outside_the_width_rejected():
    s = setup()
    feed = [FedBall(available_at=0.0, lane=0.1, entry_speed=1.0, ball=s.ball)]
    with pytest.raises(LauncherConfigError, match="lane"):
        run_setup(s, feed)


def test_empty_hopper_feed_gives_an_empty_result():
    r = run_setup(setup(), [])
    assert r.shots == [] and r.trace
    assert tuple(r.metrics) == METRIC_KEYS
    for k in ("fired", "launched", "jammed", "unfed", "scored"):
        assert r.metrics[k] == 0, k
    assert math.isnan(r.metrics["exit_speed"]) and math.isnan(r.metrics["spread_downrange_m"])
