"""A rigid body's flight under gravity and a force function: 6-DOF RK4, from launch to a floor, a stop or a time cap.

One integrator for every piece that flies with an orientation: the disc (disc/flight.py, with Hummel's
aerodynamics) and a rigid piece such as the 2024 NOTE (launcher/rigid_aero.py's drag). The bench's `[flight]` and
`launcher aim` fly rigid pieces through it; the world flies them in MuJoCo with the same force function.

World frame: z up, gravity -GRAVITY z_hat. Body frame: the piece's own, origin at its center of mass. The orientation
is the unit quaternion q = (w, x, y, z) taking body to world, R(q) its matrix. The state is (pos, vel, q, w), w the
angular velocity in the body frame. With F (world) and tau (body) from force_fn(vel, R, w):
    pos' = vel
    vel' = F / m - g z_hat
    q'   = 1/2 q (x) (0, w)                       the quaternion product
    I w' = tau - w x (I w)                         Euler's equations, with the gyroscopic term
I is the body-frame inertia: three numbers for a diagonal tensor (worked out per axis, as the disc always was) or a
3x3 tensor. RK4 at a fixed step dt, q renormalized after each step. Without forces the flight is an exact parabola
(RK4 integrates a constant acceleration exactly) and H = R(q) I w is constant.

Each step, in this order:
- goals: (name, Surface) pairs; the first one the center crosses (Surface.crossed, from the normal's side) is the
  flight's `crossing`, interpolated linearly in the step. The flight carries on through it.
- floor: when the center falls to `floor` the flight lands there, the step interpolated (cubic Hermite for position,
  linear for the rest). None: no floor.
- until(pos, prev_pos): True stops the flight at the step's end (an aim's stop at a crossing, a bench's at a wall).
- max_time: a cap, so nothing flies forever.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

GRAVITY = 9.81  # m/s^2: the MuJoCo world's and the ball launcher's (launcher/model.py)
DT = 1e-3  # s: the RK4 step
MAX_TIME = 10.0  # s: a cap so a piece can never fly forever

# Columns of Flight6Result.samples.
T, POS, VEL, QUAT, OMEGA = 0, slice(1, 4), slice(4, 7), slice(7, 11), slice(11, 14)

ForceFn = Callable[[np.ndarray, np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]


@dataclass(frozen=True)
class Flight6Result:
    samples: np.ndarray  # (n, 14) one row per step from launch: t, pos(3), vel(3), quat(4), omega_body(3)
    landing: np.ndarray | None  # (3,) where the center reached the floor; None if it didn't
    crossing: tuple[str, float, np.ndarray] | None  # (goal name, t, center) at the first goal crossing
    stopped: str  # "floor", "until" or "max_time": what ended the flight
    apex: float  # highest center height (m)
    flight_time: float  # s from launch to the end

    @property
    def pos(self) -> np.ndarray:
        return self.samples[-1, POS]

    @property
    def vel(self) -> np.ndarray:
        return self.samples[-1, VEL]

    @property
    def quat(self) -> np.ndarray:
        return self.samples[-1, QUAT]

    @property
    def omega(self) -> np.ndarray:
        """The end's angular velocity, body frame."""
        return self.samples[-1, OMEGA]


def matrix_from_quat(q: np.ndarray) -> np.ndarray:
    """The rotation matrix of the unit quaternion (w, x, y, z)."""
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def _quat_rate(q: np.ndarray, w: np.ndarray) -> np.ndarray:
    """1/2 q (x) (0, w), w in the body frame."""
    qw, qx, qy, qz = q
    wx, wy, wz = w
    return 0.5 * np.array([
        -qx * wx - qy * wy - qz * wz,
        qw * wx + qy * wz - qz * wy,
        qw * wy - qx * wz + qz * wx,
        qw * wz + qx * wy - qy * wx,
    ])


def _hermite(y0: np.ndarray, y1: np.ndarray, h: float, f: float) -> np.ndarray:
    """Position at fraction f of a step: the cubic through both ends' positions and velocities."""
    f2, f3 = f * f, f * f * f
    return ((2 * f3 - 3 * f2 + 1) * y0[0:3] + (f3 - 2 * f2 + f) * h * y0[3:6] + (-2 * f3 + 3 * f2) * y1[0:3]
            + (f3 - f2) * h * y1[3:6])


def _landing_fraction(y0: np.ndarray, y1: np.ndarray, h: float, floor: float) -> float:
    """The fraction of the step at which the center's height falls to `floor` (bisection on the step's cubic)."""
    if y0[2] <= floor:
        return 0.0
    lo, hi = 0.0, 1.0
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        if _hermite(y0, y1, h, mid)[2] > floor:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def fly(pos, vel, quat, omega, mass: float, inertia, force_fn: ForceFn, *, dt: float = DT,
        until: Callable[[np.ndarray, np.ndarray], bool] | None = None, floor: float | None = None,
        goals: tuple = (), max_time: float = MAX_TIME) -> Flight6Result:
    """Fly a rigid body from (pos, vel, quat, omega) until it lands on `floor`, `until(pos, prev_pos)` says stop, or
    `max_time` passes (see the module docstring). `omega` is in the body frame; `inertia` is the body-frame inertia,
    (3,) diagonal or (3, 3); `force_fn(vel, R, omega_body)` gives (world force, body torque)."""
    if not dt > 0.0:
        raise ValueError(f"dt must be positive, got {dt}")
    inertia = np.asarray(inertia, dtype=float)
    diagonal = inertia.shape == (3,)
    inverse = None if diagonal else np.linalg.inv(inertia)
    gravity = np.array([0.0, 0.0, -GRAVITY])

    def deriv(y: np.ndarray) -> np.ndarray:
        q = y[6:10] / np.linalg.norm(y[6:10])
        w = y[10:13]
        force, torque = force_fn(y[3:6], matrix_from_quat(q), w)
        out = np.empty(13)
        out[0:3] = y[3:6]
        out[3:6] = force / mass + gravity
        out[6:10] = _quat_rate(q, w)
        if diagonal:
            out[10:13] = (torque - np.cross(w, inertia * w)) / inertia
        else:
            out[10:13] = inverse @ (torque - np.cross(w, inertia @ w))
        return out

    q0 = np.asarray(quat, dtype=float)
    y = np.concatenate([np.asarray(pos, dtype=float), np.asarray(vel, dtype=float), q0 / np.linalg.norm(q0),
                        np.asarray(omega, dtype=float)]).astype(float)
    rows = [np.concatenate([[0.0], y])]
    t = 0.0
    landing = None
    crossing = None
    stopped = "max_time"
    steps = int(math.ceil(max_time / dt - 1e-9))
    for n in range(1, steps + 1):
        t_next = min(n * dt, max_time)  # from the step count, so long flights don't accumulate rounding in t
        h = t_next - t
        k1 = deriv(y)
        k2 = deriv(y + 0.5 * h * k1)
        k3 = deriv(y + 0.5 * h * k2)
        k4 = deriv(y + h * k3)
        new = y + h / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)
        new[6:10] /= np.linalg.norm(new[6:10])

        prev_pos, pos_now = y[0:3], new[0:3]
        if crossing is None:
            for name, surface in goals:
                if surface.crossed(prev_pos, pos_now)[0]:
                    d0 = float((prev_pos - surface.center) @ surface.normal)
                    d1 = float((pos_now - surface.center) @ surface.normal)
                    f = d0 / (d0 - d1)
                    crossing = (name, t + f * h, prev_pos + f * (pos_now - prev_pos))
                    break
        if floor is not None and pos_now[2] <= floor:
            f = _landing_fraction(y, new, h, floor)
            at = y + f * (new - y)
            at[0:3] = _hermite(y, new, h, f)
            at[6:10] /= np.linalg.norm(at[6:10])
            landing = at[0:3].copy()
            t += f * h
            rows.append(np.concatenate([[t], at]))
            stopped = "floor"
            break
        y, t = new, t_next
        rows.append(np.concatenate([[t], y]))
        if until is not None and until(pos_now, prev_pos):
            stopped = "until"
            break
    samples = np.array(rows)
    return Flight6Result(samples=samples, landing=landing, crossing=crossing, stopped=stopped,
                         apex=float(np.max(samples[:, 3])), flight_time=t)
