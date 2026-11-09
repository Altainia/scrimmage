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
Integrated by launcher/flight6.py (the integrator a rigid piece flies with too): RK4 at a fixed step dt, with q
renormalized after each step. Without aerodynamics the flight is an exact parabola (RK4 integrates a constant
acceleration exactly) and H = R(q) I w is constant.

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

from ..launcher import flight6
from ..launcher.flight6 import DT, GRAVITY, MAX_TIME, OMEGA, POS, QUAT, VEL, T, matrix_from_quat  # noqa: F401
from ..testrange.spec import Surface
from . import aero as aero_module
from .inputs import AeroSet, Disc

# DT (the RK4 step), MAX_TIME (the cap), GRAVITY and the columns of DiscFlight.samples (T, POS, VEL, QUAT, OMEGA)
# are flight6's.


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


def fly(state: DiscState, disc: Disc, aero: AeroSet, rho: float, goals: tuple[tuple[str, Surface], ...] = (),
        dt: float = DT, max_time: float = MAX_TIME) -> DiscFlight:
    """Fly a disc from `state` until it lands or `max_time` passes. `rho` is the air density (kg/m^3); `goals` are
    (name, Surface) pairs, and the first one the center crosses is the flight's `crossing`. The integrator is
    launcher/flight6.py's, with Hummel's aerodynamics (aero.forces) as its force function."""

    def hummel(vel: np.ndarray, rot: np.ndarray, w: np.ndarray):
        return aero_module.forces(disc, aero, rho, vel, rot, w)

    out = flight6.fly(state.pos, state.vel, state.quat, state.omega_body, disc.mass,
                      np.array([disc.ixx, disc.ixx, disc.izz]), hummel, dt=dt, floor=disc.height / 2.0, goals=goals,
                      max_time=max_time)
    return DiscFlight(samples=out.samples, landing=out.landing, crossing=out.crossing, apex=out.apex,
                      flight_time=out.flight_time)
