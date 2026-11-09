"""Air drag on a rigid piece (a NOTE, a coral...): the drag of its shadow, facing the air as it does now.

The air sees the piece's projected area along its velocity through the air. With no wind that is its world velocity
v. The area comes from the piece's shadows along its three body axes, (A_x, A_y, A_z) (PieceKind.shadow_areas: the
union of its solids' projections onto the plane across each axis, rasterized once when the kind is built), so with
v_hat = R^T v / |v| in the body frame
    A(v_hat) = A_x |v_hat_x| + A_y |v_hat_y| + A_z |v_hat_z|
That is exact for a box seen from any direction and for any piece seen along an axis; between the axes it is the
sum of the axis shadows, each seen at its angle. A ring's face shows its annulus, its hole open: the 2024 NOTE (14 in
across, 10 in hole, 2 in thick) shows 0.049 m^2 face on and 0.018 m^2 edge on. The force is drag only, against the
velocity:
    F = -1/2 rho Cd A(v_hat) |v| v
There is no lift (no source gives a ring's lift coefficients), so a flat, backspun piece that really glides comes up
short here, and no torque from the air's center of pressure: a tumbling piece turns under its own gyroscopics. An
optional spin damping slows the spin, tau = -c w (N m s/rad); its default 0 keeps the spin, as a ball's is kept.

Estimates, until someone measures (results resting on them are uncalibrated):
- Cd is the kind's `drag_coefficient`, by default DRAG_COEFFICIENT, Hoerner's broadside flat plate (the value
  disc/presets.toml cites as flat_plate_cd), used at every angle, on the shadow's area: air through a ring's hole
  is taken to cost nothing.

Used by the world (testrange/balls.py, each step on a flying rigid piece) and by flight6 (force_fn), so the world,
the bench's `[flight]` and `launcher aim` fly a rigid piece with the same drag.
"""

from __future__ import annotations

import math

import numpy as np

# estimate: a flat disc broadside to the flow, Hoerner "Fluid-Dynamic Drag" (1965) ch. 3 (disc/presets.toml's
# flat_plate_cd), used for a whole piece at every angle
DRAG_COEFFICIENT = 1.17
SPIN_DAMPING = 0.0  # estimate: none (N m s/rad): a flying piece keeps its spin, as a ball's is kept
MIN_SPEED = 1e-9  # m/s: below this the air's direction is undefined and the drag is zero


def box_areas(half_extents) -> tuple[float, float, float]:
    """A box's shadows along each body axis: (4 b c, 4 a c, 4 a b) for half sizes (a, b, c)."""
    a, b, c = (float(x) for x in half_extents)
    return 4.0 * b * c, 4.0 * a * c, 4.0 * a * b


def projected_area(areas, v_body) -> float:
    """The area seen along `v_body` (any length, in the body frame) of a piece whose shadows along body x, y and z
    are `areas`: A_x |v_hat_x| + A_y |v_hat_y| + A_z |v_hat_z|. Zero for a zero vector."""
    x, y, z = (float(c) for c in v_body)
    n = math.sqrt(x * x + y * y + z * z)
    if not n > 0.0:
        return 0.0
    ax, ay, az = areas
    return (ax * abs(x) + ay * abs(y) + az * abs(z)) / n


def forces(areas, cd: float, spin_damping: float, rho: float, vel_world, rot_world,
           omega_world) -> tuple[np.ndarray, np.ndarray]:
    """The air's (force, torque) on a rigid piece, both in the world frame: drag -1/2 rho Cd A(v_hat) |v| v, A the
    projected area along the velocity of a piece whose shadows along body x, y and z are `areas` (see the module
    docstring), and the spin damping -c w. `rot_world` is the body-to-world rotation (3x3) and `omega_world` the
    angular velocity in the world frame."""
    v = np.asarray(vel_world, dtype=float)
    w = np.asarray(omega_world, dtype=float)
    torque = -float(spin_damping) * w
    speed = math.sqrt(float(v @ v))
    if not speed > MIN_SPEED:
        return np.zeros(3), torque
    area = projected_area(areas, np.asarray(rot_world, dtype=float).T @ v)
    return -0.5 * float(rho) * float(cd) * area * speed * v, torque


def areas(kind) -> tuple[float, float, float]:
    """A rigid kind's shadows along its body x, y and z (m^2), worked out once per kind (PieceKind.shadow_areas)."""
    return kind.shadow_areas


def force_fn(kind, rho: float):
    """flight6's force function for a rigid kind in air of density `rho`: (vel, R, omega_body) -> (world force, body
    torque), with the kind's drag_coefficient and spin_damping."""
    h, cd, c = areas(kind), kind.drag_coefficient, kind.spin_damping

    def fn(vel: np.ndarray, rot: np.ndarray, w: np.ndarray):
        force, torque = forces(h, cd, c, rho, vel, rot, rot @ w)
        return force, rot.T @ torque

    return fn
