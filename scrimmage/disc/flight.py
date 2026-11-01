"""Disc flight: a spinning disc as a rigid body under gravity and Hummel's aerodynamics, from launch to the floor.

World frame: x forward (downrange), y left, z up, origin on the floor. Body frame: z the disc's axis, out of the top;
x and y in the disc plane. The orientation is the unit quaternion q = (w, x, y, z) taking body to world, R(q) its
matrix. Inertia is diag(Ixx, Ixx, Izz) in the body frame, the disc being axisymmetric.

State (pos, vel, q, w), w the angular velocity in the body frame. With F and tau_b from aero.forces (world force,
body torque) and g = GRAVITY:
    pos' = vel
    vel' = F / m - g z_hat
    q'   = 1/2 q (x) (0, w)                                    the quaternion product
    I w' = tau_b - w x (I w)                                    Euler's equations, with the gyroscopic term
which for diag(Ixx, Ixx, Izz) is
    Ixx wx' = tau_x - (Izz - Ixx) wy wz,   Ixx wy' = tau_y + (Izz - Ixx) wx wz,   Izz wz' = tau_z.
Integrated by RK4 at a fixed step dt, with q renormalized after each step. Without aerodynamics the flight is an
exact parabola (RK4 integrates a constant acceleration exactly) and H = R(q) I w is constant.

The disc lands when its center falls to half its height, where it would sit lying flat (the step is interpolated,
cubic Hermite for position). A goal is (name, Surface): the center crossing the surface's polygon from the normal's side scores, at the
first such crossing, interpolated linearly in the step. The flight carries on through a goal to the floor, as the
ball's does (launcher/flight.py): contact with the goal is the world's job.

Gravity is GRAVITY, the value of the MuJoCo world and the ball launcher. Hummel's code used g = 9.7935 (HUM03 p. 81);
over his 1.46 s f2302 flight the difference moves the disc 2 cm.

Hummel's own frame (HUM03 App. A/B, discfltEOM.m pp. 88-90) is x forward, y right, z down, with the non-spinning
frame C from N by phi about n1 then theta about the new 2-axis, and the spin angle gamma about c3:
    T_c_N = [[ct, st sf, -st cf], [0, cf, sf], [st, -ct sf, ct cf]]   (rows: c1, c2, c3 in N)
    omega in C = (phidot ct, thetadot, phidot st + gammadot)
state_from_hummel converts his state: our world is his with y and z negated (a half turn about x), and our body axes
are his d1, -d2, -d3 (d the spinning frame, d3 = c3), so our body z is out of the top.
"""

import math
from dataclasses import dataclass

import numpy as np

from ..testrange.spec import Surface
from . import aero as aero_module
from .inputs import AeroSet, Disc

GRAVITY = 9.81  # m/s^2: the MuJoCo world's and the ball launcher's (launcher/model.py)
DT = 1e-3  # s: the RK4 step
MAX_TIME = 10.0  # s: a cap so a disc can never fly forever

# Columns of DiscFlight.samples.
T, POS, VEL, QUAT, OMEGA = 0, slice(1, 4), slice(4, 7), slice(7, 11), slice(11, 14)


@dataclass(frozen=True)
class DiscState:
    pos: np.ndarray  # (3,) center of mass, world (m)
    vel: np.ndarray  # (3,) world (m/s)
    quat: np.ndarray  # (4,) body to world, (w, x, y, z)
    omega_body: np.ndarray  # (3,) angular velocity, body frame (rad/s); +z is counter-clockwise seen from above


@dataclass(frozen=True)
class DiscFlight:
    samples: np.ndarray  # (n, 14) one row per step from launch: t, pos(3), vel(3), quat(4), omega_body(3)
    landing: np.ndarray | None  # (3,) where the center reached the floor; None if max_time came first
    crossing: tuple[str, float, np.ndarray] | None  # (goal name, t, center) at the first goal crossing
    apex: float  # highest center height (m)
    flight_time: float  # s from launch to landing (or max_time)


def matrix_from_quat(q: np.ndarray) -> np.ndarray:
    """The rotation matrix of the unit quaternion (w, x, y, z)."""
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def quat_from_matrix(rot: np.ndarray) -> np.ndarray:
    """The unit quaternion (w, x, y, z), w >= 0, of a rotation matrix (Shepperd's method: the largest diagonal)."""
    m = np.asarray(rot, dtype=float)
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        s = 2 * math.sqrt(1 + trace)
        q = np.array([s / 4, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s])
    else:
        i = int(np.argmax(np.diag(m)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = 2 * math.sqrt(1 + m[i, i] - m[j, j] - m[k, k])
        q = np.zeros(4)
        q[0] = (m[k, j] - m[j, k]) / s
        q[1 + i] = s / 4
        q[1 + j] = (m[j, i] + m[i, j]) / s
        q[1 + k] = (m[k, i] + m[i, k]) / s
    q /= np.linalg.norm(q)
    return q if q[0] >= 0 else -q


def state_from_hummel(x, y, z, vx, vy, vz, phi, theta, gamma, phidot, thetadot, gammadot) -> DiscState:
    """A DiscState from Hummel's state (HUM03 Table 3-3; simulate_flight.m's x0, p. 84), in his frame and angles
    (see the module docstring)."""
    st, ct, sf, cf = math.sin(theta), math.cos(theta), math.sin(phi), math.cos(phi)
    t_cn = np.array([[ct, st * sf, -st * cf], [0.0, cf, sf], [st, -ct * sf, ct * cf]])  # p. 88
    omega_c = np.array([phidot * ct, thetadot, phidot * st + gammadot])  # p. 89
    sg, cg = math.sin(gamma), math.cos(gamma)
    spin = np.array([[cg, sg, 0.0], [-sg, cg, 0.0], [0.0, 0.0, 1.0]])  # d = spin @ c (T_d_N, p. 88)
    t_dn = spin @ t_cn  # rows: d1, d2, d3 in N
    omega_d = spin @ omega_c
    flip = np.array([1.0, -1.0, -1.0])  # his N to our world, and his d to our body: y and z negated
    rot = (t_dn.T * flip[None, :]) * flip[:, None]  # columns: our body axes in our world
    return DiscState(pos=np.array([x, y, z]) * flip, vel=np.array([vx, vy, vz]) * flip,
                     quat=quat_from_matrix(rot), omega_body=omega_d * flip)


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


def fly(state: DiscState, disc: Disc, aero: AeroSet, rho: float, goals: tuple[tuple[str, Surface], ...] = (),
        dt: float = DT, max_time: float = MAX_TIME) -> DiscFlight:
    """Fly a disc from `state` until it lands or `max_time` passes. `rho` is the air density (kg/m^3); `goals` are
    (name, Surface) pairs, and the first one the center crosses is the flight's `crossing`."""
    if not dt > 0.0:
        raise ValueError(f"dt must be positive, got {dt}")
    inertia = np.array([disc.ixx, disc.ixx, disc.izz])
    gravity = np.array([0.0, 0.0, -GRAVITY])
    floor = disc.height / 2.0

    def deriv(y: np.ndarray) -> np.ndarray:
        q = y[6:10] / np.linalg.norm(y[6:10])
        w = y[10:13]
        force, torque = aero_module.forces(disc, aero, rho, y[3:6], matrix_from_quat(q), w)
        out = np.empty(13)
        out[0:3] = y[3:6]
        out[3:6] = force / disc.mass + gravity
        out[6:10] = _quat_rate(q, w)
        out[10:13] = (torque - np.cross(w, inertia * w)) / inertia
        return out

    y = np.concatenate([state.pos, state.vel, np.asarray(state.quat, dtype=float) / np.linalg.norm(state.quat),
                        state.omega_body]).astype(float)
    rows = [np.concatenate([[0.0], y])]
    t = 0.0
    landing = None
    crossing = None
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

        prev_pos, pos = y[0:3], new[0:3]
        if crossing is None:
            for name, surface in goals:
                if surface.crossed(prev_pos, pos)[0]:
                    d0 = float((prev_pos - surface.center) @ surface.normal)
                    d1 = float((pos - surface.center) @ surface.normal)
                    f = d0 / (d0 - d1)
                    crossing = (name, t + f * h, prev_pos + f * (pos - prev_pos))
                    break
        if pos[2] <= floor:
            f = _landing_fraction(y, new, h, floor)
            at = y + f * (new - y)
            at[0:3] = _hermite(y, new, h, f)
            at[6:10] /= np.linalg.norm(at[6:10])
            landing = at[0:3].copy()
            t += f * h
            rows.append(np.concatenate([[t], at]))
            break
        y, t = new, t_next
        rows.append(np.concatenate([[t], y]))
    samples = np.array(rows)
    return DiscFlight(samples=samples, landing=landing, crossing=crossing, apex=float(np.max(samples[:, 3])),
                      flight_time=t)
