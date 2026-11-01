"""Ball flight from the launcher's exit to the goal or the floor, and whether it scores.

Robot frame at launch: x forward, y left, z up, origin on the floor below the shaft. The robot is taken as frozen
in place at its launch position: only its velocity is added to the ball's. The goal center is (distance, 0, height).

Forces: gravity, drag F_D = -1/2 rho C_d A |v| v, and Magnus lift F_L = 1/2 rho C_L A |v|^2 (w_hat x v_hat) with
C_L = min(lift_slope * S, lift_max) and S = r |w| / |v|. Backspin has w_hat = -y (rotated by the yaw about z), which
lifts a ball moving along +x.
"""

import math
from dataclasses import dataclass

import numpy as np

from .inputs import Air, Ball, Goal, Setup
from .model import GRAVITY, ExitedBall

DT = 0.001  # RK4 step (s); the plan fixes it at 1 ms
MAX_TIME = 10.0  # s; a cap so a ball can never fly forever


@dataclass(frozen=True)
class Landing:
    scored: bool
    at_goal: tuple[float, float] | None  # (x, y) where the center crosses the goal height descending
    floor: tuple[float, float]  # (x, y) where it reaches the floor
    apex: float  # highest center height (m)
    flight_time: float  # s from the exit to the floor
    goal_time: float | None = None  # s from the exit to the goal-height crossing (None with at_goal)


def fly(shot: ExitedBall, setup: Setup, ball: Ball) -> Landing:
    if shot.status != "launched":
        raise ValueError(f"cannot fly a ball whose status is {shot.status!r}")
    phi = math.radians(setup.hood.exit_angle_deg + 90.0)
    rc = setup.wheels.diameter / 2.0 + setup.hood.gap / 2.0
    pos = np.array([rc * math.cos(phi), shot.lane, setup.launcher.height + rc * math.sin(phi)])

    launch, yaw = math.radians(shot.launch_deg), math.radians(shot.yaw_deg)
    vel = shot.speed * np.array([math.cos(launch) * math.cos(yaw), math.cos(launch) * math.sin(yaw), math.sin(launch)])
    vel[0] += setup.robot.velocity[0]
    vel[1] += setup.robot.velocity[1]

    # Spin axis for backspin: -y rotated by the yaw about z.
    axis = np.array([math.sin(yaw), -math.cos(yaw), 0.0])
    return fly_state(pos, vel, axis, shot.spin, ball, setup.air, setup.goal)


def fly_state(pos, vel, spin_axis, spin: float, ball: Ball, air: Air, goal: Goal) -> Landing:
    """Fly a ball from center `pos` (m, floor frame: z up, the floor at z = 0) with velocity `vel` (m/s), spinning at
    `spin` rad/s about the unit `spin_axis` (a negative spin turns the other way), to the goal-height crossing and
    the floor. The spin magnitude stays constant in flight: a modeling choice (no spin decay), since the inputs have
    no figure for the air's torque on the ball."""
    r = ball.diameter / 2.0
    area = math.pi * r * r
    half_rho_a = 0.5 * air.density * area
    pos, vel = np.array(pos, dtype=float), np.array(vel, dtype=float)
    axis = np.array(spin_axis, dtype=float)
    w = abs(spin)
    axis = axis if spin >= 0 else -axis

    def accel(v: np.ndarray) -> np.ndarray:
        speed = float(np.linalg.norm(v))
        a = np.array([0.0, 0.0, -GRAVITY])
        if speed < 1e-9:
            return a
        a -= half_rho_a * air.drag_coefficient * speed * v / ball.mass
        cl = min(air.lift_slope * r * w / speed, air.lift_max)
        a += half_rho_a * cl * speed * np.cross(axis, v) / ball.mass   # 1/2 rho A C_L |v|^2 (w_hat x v_hat)
        return a

    t = 0.0
    apex = pos[2]
    at_goal = None
    goal_time = None
    while True:
        k1v = accel(vel)
        k1p = vel
        k2v = accel(vel + 0.5 * DT * k1v)
        k2p = vel + 0.5 * DT * k1v
        k3v = accel(vel + 0.5 * DT * k2v)
        k3p = vel + 0.5 * DT * k2v
        k4v = accel(vel + DT * k3v)
        k4p = vel + DT * k3v
        new_pos = pos + DT / 6.0 * (k1p + 2 * k2p + 2 * k3p + k4p)
        new_vel = vel + DT / 6.0 * (k1v + 2 * k2v + 2 * k3v + k4v)

        if at_goal is None and pos[2] > goal.height >= new_pos[2]:
            f = (pos[2] - goal.height) / (pos[2] - new_pos[2])
            at_goal = (float(pos[0] + f * (new_pos[0] - pos[0])), float(pos[1] + f * (new_pos[1] - pos[1])))
            goal_time = t + f * DT
        if new_pos[2] <= r or t + DT >= MAX_TIME:
            f = (pos[2] - r) / (pos[2] - new_pos[2]) if new_pos[2] < pos[2] else 1.0
            f = min(max(f, 0.0), 1.0)
            floor = (float(pos[0] + f * (new_pos[0] - pos[0])), float(pos[1] + f * (new_pos[1] - pos[1])))
            t += f * DT
            break
        pos, vel, t = new_pos, new_vel, t + DT
        apex = max(apex, pos[2])

    scored = False
    if at_goal is not None:
        reach = (goal.diameter - ball.diameter) / 2.0
        scored = math.hypot(at_goal[0] - goal.distance, at_goal[1]) <= reach
    return Landing(scored=scored, at_goal=at_goal, floor=floor, apex=float(apex), flight_time=t,
                   goal_time=goal_time)
