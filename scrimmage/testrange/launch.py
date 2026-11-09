"""Leaving the launcher: where a launched ball enters the world, how fast and spinning which way, and what its log
records about the shot.

The launcher model (sub-project 8) works in the launcher's frame: x forward, z up, the shaft along y, the origin on
the shaft. The ball's center runs round the shaft at r_path (the wheel's radius plus half the hood's gap) and leaves
at the path angle phi_exit = launch_deg + 90 deg (measured from +x toward +z), moving at `speed` along the launch
direction, launch_deg above x and turned yaw_deg about z, with backspin `spin` about -y turned by the yaw: the same
geometry as launcher/flight.py, but at the hood's angle at the moment of the exit (`ExitedBall.launch_deg`), since an
articulated hood moves. The launcher part's base body is that frame in the world, so its pose places the exit, and
the robot's motion there (the base's velocity plus omega x the lever arm) and its angular velocity are added.

**A disc** (`exit_state_disc`) leaves the disc shooter's channel (disc/launcher.py) in the channel's frame: x
downrange from the disc's center at the entry, y left, z normal to the channel plane (the disc's axis). The
disc_shooter part's `channel` body is that frame in the world, tilted by its tilt joint, so its pose places the exit
at (exit_x, exit_y, 0), the disc's center where it left (exit_x is about the channel length plus the disc's radius;
the standalone run, disc/run.py, starts the disc at the channel's end instead). The velocity and orientation are
disc/run.py's `channel_exit` for that frame: speed at the exit yaw in the channel plane, the disc lying in it, its
axis the channel's z, spinning about it at `spin` (positive counter-clockwise seen from above the channel). The
robot's motion at the exit point and its angular velocity are added, as for a ball, and the position is moved on to
t_now the same way.

**A rigid piece** (`exit_state_piece`, the 2024 NOTE from a pinch shooter) leaves the pinch (launcher/pinch.py) in
the channel's frame too: x downrange from the piece's center at the entry, z toward the top wheel, y along the
wheels' axles. The pinch_shooter part's `channel` body is that frame in the world, tilted by its tilt joint, so its
pose places the exit at (exit_x, 0, 0), the piece's center where it left. It leaves along the channel's x at `speed`,
face-parallel to the channel (turned by the shooter's `turn`, pinch.channel_turn: its thinnest side across the gap,
its faces in the channel's x-y plane, as it lay between the wheels), with its pitch spin `spin` as backspin about
the channel's -y. The robot's motion is added as for a ball.

**Clearing the robot.** A launched ball gets LAUNCHED_BITS (it touches the field and the balls, not the robot) for
LAUNCH_CLEAR_S of flight, then BALL_BITS. That is a choice: the launcher's own geometry doesn't collide, but the
robot's (the chassis, the bumpers) does, and a launcher mounted low can have its exit point inside the bumper volume.
A ball born inside a collision box would be thrown out by the penetration recovery (or stick); ignoring the robot
until it is clear lets it fly as in free air. 0.15 s is about 1 m at a slow 7 m/s shot, past any robot's frame
perimeter (1.2 m diagonal at most), and short enough that a ball falling back onto its robot is still stopped by it.
"""

from __future__ import annotations

import math

import numpy as np

from ..disc.launcher import ExitedDisc
from ..disc.run import channel_exit
from ..launcher.model import ExitedBall
from ..launcher.pinch import ExitedPiece
from ..disc.flight import quat_from_matrix

LAUNCH_CLEAR_S = 0.15   # s of flight a launched ball ignores the robot for (LAUNCHED_BITS), then BALL_BITS


def exit_state(shot: ExitedBall, r_path: float, base_pos: np.ndarray, base_mat: np.ndarray, body_vel: np.ndarray,
               t_now: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A launched ball's position, velocity and angular velocity in the world at time t_now (the launcher's clock).

    base_pos and base_mat (3x3) are the launcher's frame in the world (its base body); body_vel (6,) is that body's
    angular then linear velocity (world axes) at base_pos. The exit point is (r_path cos phi, lane, r_path sin phi)
    with phi = launch_deg + 90 deg; the velocity is the launch velocity plus the robot's at that point,
    v + omega x (p - base); the spin is the backspin about -y turned by the yaw, plus the robot's omega. The position
    is then moved on at that velocity by t_now - shot.t_exit: the exit happened inside a world step, at the launcher
    model's own time (RangeRuntime passes the step's start, so t_now - t_exit is at most 0 and mj_step carries the
    ball on to where it is at the step's end)."""
    phi = math.radians(shot.launch_deg + 90.0)
    launch, yaw = math.radians(shot.launch_deg), math.radians(shot.yaw_deg)
    local = np.array([r_path * math.cos(phi), shot.lane, r_path * math.sin(phi)])
    lever = base_mat @ local
    pos = base_pos + lever
    omega, v_base = body_vel[:3], body_vel[3:]
    direction = np.array([math.cos(launch) * math.cos(yaw), math.cos(launch) * math.sin(yaw), math.sin(launch)])
    vel = shot.speed * (base_mat @ direction) + v_base + np.cross(omega, lever)
    axis = np.array([math.sin(yaw), -math.cos(yaw), 0.0])     # backspin: -y turned by the yaw (as flight.py)
    ang_vel = shot.spin * (base_mat @ axis) + omega
    return pos + vel * (t_now - shot.t_exit), vel, ang_vel


def exit_state_disc(shot: ExitedDisc, chan_pos: np.ndarray, chan_mat: np.ndarray, body_vel: np.ndarray,
                    t_now: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """A launched disc's position, velocity, orientation (w, x, y, z) and angular velocity (world) at time t_now (the
    launcher's clock): see the module docstring. chan_pos and chan_mat (3x3) are the channel's frame in the world;
    body_vel (6,) is the channel body's angular then linear velocity (world axes) at chan_pos."""
    lever = chan_mat @ np.array([shot.exit_xy[0], shot.exit_xy[1], 0.0])
    pos = chan_pos + lever
    omega, v_base = body_vel[:3], body_vel[3:]
    along, quat = channel_exit(shot, chan_mat)
    vel = along + v_base + np.cross(omega, lever)
    ang_vel = shot.spin * chan_mat[:, 2] + omega
    return pos + vel * (t_now - shot.t_exit), vel, quat, ang_vel


def exit_state_piece(shot: ExitedPiece, chan_pos: np.ndarray, chan_mat: np.ndarray, body_vel: np.ndarray,
                     t_now: float, turn) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """A launched rigid piece's position, velocity, orientation (w, x, y, z) and angular velocity (world) at time
    t_now (the launcher's clock): see the module docstring. chan_pos and chan_mat (3x3) are the channel's frame in the
    world, body_vel (6,) the channel body's angular then linear velocity (world axes) at chan_pos, and `turn` (3x3)
    the piece's body frame in the channel's."""
    lever = chan_mat @ np.array([shot.exit_x, 0.0, 0.0])
    pos = chan_pos + lever
    omega, v_base = body_vel[:3], body_vel[3:]
    vel = shot.speed * chan_mat[:, 0] + v_base + np.cross(omega, lever)
    quat = quat_from_matrix(chan_mat @ np.asarray(turn, dtype=float))
    ang_vel = -shot.spin * chan_mat[:, 1] + omega       # backspin: about the channel's -y
    return pos + vel * (t_now - shot.t_exit), vel, quat, ang_vel
