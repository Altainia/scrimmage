import pytest

from scrimmage.lockstep import Pacer, parse_speed, physics_steps_per_step


def test_parse_speed():
    assert parse_speed("max") is None
    assert parse_speed(" MAX ") is None
    assert parse_speed("1") == 1.0
    assert parse_speed("2.5") == 2.5
    for bad in ["0", "-1", "fast", "nan", "inf", ""]:
        with pytest.raises(ValueError):
            parse_speed(bad)


def test_physics_steps_per_step():
    assert physics_steps_per_step(0.005, 0.001) == 5
    assert physics_steps_per_step(0.02, 0.002) == 10
    assert physics_steps_per_step(0.001, 0.001) == 1
    for step in [0.0025, 0.0005, 0.0]:
        with pytest.raises(ValueError, match="whole multiple"):
            physics_steps_per_step(step, 0.001)


def test_pacer_holds_sim_time_to_wall_time_times_speed():
    wall = [100.0]
    p = Pacer(2.0, clock=lambda: wall[0])
    p.start(10.0)
    assert p.delay(10.0) == 0.0
    assert p.delay(11.0) == pytest.approx(0.5)     # 1 s of sim at 2x = 0.5 s of wall
    wall[0] = 100.5
    assert p.delay(11.0) == 0.0
    wall[0] = 101.0
    assert p.delay(11.0) == 0.0                    # behind: never negative


def test_pacer_max_never_waits():
    p = Pacer(None, clock=lambda: 0.0)
    p.start(0.0)
    assert p.delay(1000.0) == 0.0


def test_pacer_lag_is_how_far_wall_time_is_behind():
    wall = [100.0]
    p = Pacer(2.0, clock=lambda: wall[0])
    p.start(10.0)
    assert p.lag(11.0) == pytest.approx(-0.5)      # ahead: 0.5 s of wall still to go
    wall[0] = 100.5
    assert p.lag(11.0) == pytest.approx(0.0)
    wall[0] = 102.0
    assert p.lag(11.0) == pytest.approx(1.5)       # behind
