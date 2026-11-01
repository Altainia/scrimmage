import dataclasses
import math

import pytest

from scrimmage.launcher.flight import Landing, fly
from scrimmage.launcher.inputs import parse_setup
from scrimmage.launcher.model import GRAVITY, Energy, ExitedBall

POWER_CELL_D = 0.1778

BASE = {
    "ball": {"preset": "2020-power-cell"},
    "wheels": {"diameter": 0.1016, "width": 0.05, "preset": "30A", "positions": [0.0]},
    "shaft": {"wheel_inertia": 0.002},
    "control": {"target_rps": 60.0, "kP": 0.2},
    "hood": {"gap": 0.8 * POWER_CELL_D, "exit_angle_deg": 40.0, "friction": 0.5},
    "launcher": {"height": 0.5, "width": 0.3},
    "goal": {"distance": 3.0, "height": 2.64, "diameter": 1.2},
}


def setup(air=None, robot=None, **goal):
    s = parse_setup({k: dict(v) for k, v in BASE.items()})
    if air is not None:
        s = dataclasses.replace(s, air=dataclasses.replace(s.air, **air))
    if robot is not None:
        s = dataclasses.replace(s, robot=dataclasses.replace(s.robot, velocity=robot))
    if goal:
        s = dataclasses.replace(s, goal=dataclasses.replace(s.goal, **goal))
    return s


VACUUM = {"drag_coefficient": 0.0, "lift_slope": 0.0, "lift_max": 0.0}
ZERO_ENERGY = Energy(0, 0, 0, 0, 0, 0, 0)


def shot(speed=8.0, spin=0.0, launch_deg=60.0, yaw_deg=0.0, lane=0.0, status="launched"):
    return ExitedBall(index=0, status=status, t_enter=0.0, t_exit=0.1, speed=speed, spin=spin,
                      launch_deg=launch_deg, yaw_deg=yaw_deg, lane=lane, energy=ZERO_ENERGY)


def exit_point(s, lane=0.0):
    phi = math.radians(s.hood.exit_angle_deg + 90.0)
    rc = s.wheels.diameter / 2 + s.hood.gap / 2
    return rc * math.cos(phi), lane, s.launcher.height + rc * math.sin(phi)


def test_vacuum_is_parabola():
    s = setup(air=VACUUM, height=100.0)  # goal out of reach: look at the floor point
    sh = shot()
    x0, _, z0 = exit_point(s)
    vx, vz = sh.speed * math.cos(math.radians(60)), sh.speed * math.sin(math.radians(60))
    r = s.ball.diameter / 2
    apex = z0 + vz**2 / (2 * GRAVITY)
    t = (vz + math.sqrt(vz**2 + 2 * GRAVITY * (z0 - r))) / GRAVITY
    land = fly(sh, s, s.ball)
    assert land.apex == pytest.approx(apex, abs=1e-3)
    assert land.floor[0] == pytest.approx(x0 + vx * t, abs=1e-3)
    assert land.floor[1] == pytest.approx(0.0, abs=1e-6)
    assert land.flight_time == pytest.approx(t, abs=2e-3)
    assert not land.scored and land.at_goal is None


def test_drag_shortens():
    vac = fly(shot(), setup(air=VACUUM, height=100.0), setup().ball)
    drag = fly(shot(), setup(air={**VACUUM, "drag_coefficient": 0.47}, height=100.0), setup().ball)
    assert drag.floor[0] < vac.floor[0] and drag.apex < vac.apex


def test_backspin_lengthens():
    air = {"drag_coefficient": 0.47, "lift_slope": 1.0, "lift_max": 0.3}
    s = setup(air=air, height=100.0)
    # A flat shot: on a steep one the lift's backward component outweighs its upward one.
    plain = fly(shot(launch_deg=25.0), s, s.ball)
    back = fly(shot(launch_deg=25.0, spin=300.0), s, s.ball)
    top = fly(shot(launch_deg=25.0, spin=-300.0), s, s.ball)
    assert back.floor[0] > plain.floor[0] > top.floor[0]
    assert back.floor[1] == pytest.approx(0.0, abs=1e-6)


def test_robot_velocity_adds():
    base = setup(air=VACUUM, height=100.0)
    moving = setup(air=VACUUM, robot=(2.0, 0.0), height=100.0)
    side = setup(air=VACUUM, robot=(0.0, 1.5), height=100.0)
    a, b, c = (fly(shot(), s, s.ball) for s in (base, moving, side))
    assert b.floor[0] > a.floor[0] + 1.0
    assert c.floor[0] == pytest.approx(a.floor[0], abs=1e-3)
    assert c.floor[1] == pytest.approx(1.5 * c.flight_time, abs=1e-3)


def test_short_shot_not_scored():
    s = setup(air=VACUUM)
    land = fly(shot(speed=3.0), s, s.ball)
    assert land.apex < s.goal.height
    assert not land.scored and land.at_goal is None
    assert land.floor[0] > 0


def test_backwards_robot_motion():
    s = setup(robot=(-15.0, 0.0))
    land = fly(shot(), s, s.ball)
    assert not land.scored and land.at_goal is None
    assert land.floor[0] < 0


def test_center_hit_scores():
    s = setup(air=VACUUM)
    x0, _, z0 = exit_point(s)
    angle = math.radians(70)

    def descending_x(v):  # closed form: x where the vacuum path comes down through the goal height
        vz = v * math.sin(angle)
        t = (vz + math.sqrt(vz**2 - 2 * GRAVITY * (s.goal.height - z0))) / GRAVITY
        return x0 + v * math.cos(angle) * t

    lo, hi = 7.0, 14.0  # descending_x rises with v over this range
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if descending_x(mid) < s.goal.distance else (lo, mid)
    land = fly(shot(speed=lo, launch_deg=70.0), s, s.ball)
    assert land.scored
    assert land.at_goal[0] == pytest.approx(s.goal.distance, abs=2e-3)
    assert land.at_goal[1] == pytest.approx(0.0, abs=1e-9)


def test_outside_radius_misses():
    s = setup(air=VACUUM)
    reach = (s.goal.diameter - s.ball.diameter) / 2
    # Same descending crossing, steered sideways by the yaw: inside the radius scores, outside does not.
    inside = fly(shot(speed=10.5, launch_deg=70.0, yaw_deg=0.0), s, s.ball)
    assert inside.at_goal is not None
    off = fly(shot(speed=10.5, launch_deg=70.0, yaw_deg=40.0), s, s.ball)
    assert off.at_goal is not None and math.hypot(off.at_goal[0] - 3.0, off.at_goal[1]) > reach
    assert not off.scored


def test_not_launched_raises():
    s = setup()
    with pytest.raises(ValueError):
        fly(shot(status="no_contact", speed=math.nan), s, s.ball)
