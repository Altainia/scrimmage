"""Disc aerodynamics: the force and moment on a spinning disc, after Hummel (2003). The single source of disc
aerodynamics, used by the standalone flight model and the MuJoCo world alike.

Source: S. A. Hummel, "Frisbee Flight Simulation and Throw Biomechanics", MS thesis, UC Davis, 2003 (HUM03), with the
corrections of Hubbard and Hummel's addendum. The forms below are copied from the thesis's own MATLAB code,
simulate_flight.m (App. A, pp. 81-87) and discfltEOM.m (App. B, pp. 88-90), because the long-flight damping
coefficients were identified with that code. Page numbers are the thesis's printed ones.

Frame F (HUM03 §3.3.1 and discfltEOM.m p. 89). Body z is the disc's axis, pointing out of the top. Hummel's c3 points
out of the underside (his inertial z is down), so c3 = -z_body.
    x_F = the velocity's projection onto the disc plane, unit (Hummel's uvp)
    alpha = atan(v.c3 / |v_p|): the angle of the velocity to the disc plane, positive when the air strikes the
            underside (it gives positive lift)
    Hummel's ulat = c3 x x_F = -y_F, where y_F = z_body x x_F (points left). Positive C_M about ulat raises the
            leading edge (nose up).
    r = omega.c3 (Hummel's spin, positive clockwise seen from above), p = omega.x_F, q = omega.ulat.

With q_inf A = 1/2 rho v^2 A, A = pi d^2 / 4 and the moment arm d (the full diameter):
    lift  L = C_L q_inf A along ulift = -(v_hat x ulat): perpendicular to v, in the plane of v and the axis (p. 90)
    drag  D = C_D q_inf A along -v_hat (p. 90)
    roll  R = q_inf A d (C_Rr + C_Rp p) about x_F, potts-crowther form (App. A p. 86; App. B p. 89 prints it with
          a stray `CRr*`, a typo)
          R = q_inf A d (sqrt(d/g) C_Rr r + C_Rp p) about x_F, linear form, g = 9.7935 (pp. 81, 89)
    pitch M = q_inf A d (C_M + C_Mq q) about ulat (p. 90)
    spin-down N = C_Nr r about c3, with no q_inf A d factor, as in the code (p. 90; eq. 3.8, §3.3.1, has one)
The linear form's coefficients (p. 89): C_L = C_L0 + C_La a, C_D = C_D0 + C_Da (a - a0)^2 with a0 = -C_L0/C_La,
C_M = C_M0 + C_Ma a. The potts-crowther form interpolates Potts and Crowther's (2002) wind-tunnel tables
(embedded on pp. 82-83) with MATLAB's interp1/interp2 'spline', a not-a-knot cubic spline (pp. 85-86, 89); C_Rr is
looked up against alpha and the advance ratio AdvR = d r / (2 v) (p. 89).

Choices beyond the thesis, all estimates (the tables stop at -10 and 30 deg; the linear fits were identified at
small alpha, so they use the same range):
- Beyond the core range alpha reaches +-90 deg (the disc broadside to the flow), where x_F flips as the velocity
  crosses the axis. So C_L, C_M and C_Rr must vanish there, and C_D must be finite.
- C_L and C_M: the core curve's tangent at its edge, blended into a flat plate by w = cos^2(pi/2 (a - edge) /
  (+-pi/2 - edge)), which is 1 at the edge (with zero slope) and 0 at +-90 deg:
      C = C_fp + (C_edge + C'_edge (a - edge) - C_fp) w,    C_L,fp = 1/2 C_D90 sin 2a,    C_M,fp = 0.
  For the P&C table the lift so made rises to a stall at about 45 deg (C_L about 2.0) and falls toward the flat
  plate's; the pitching moment stays below 0.15 in size. Continuous with a continuous slope.
- C_D: rises monotonically from the core edge to the flat-plate value C_D90 (1.17 for a broadside disc) at +-90:
      C_D = C_D90 - (C_D90 - C_D,edge) u^n,   u = (+-pi/2 - a)/(+-pi/2 - edge),
  with n chosen to match the edge slope (at least 1). Continuous; continuous slope when n >= 1 is not clamped.
- C_Rr: the edge value times w, fading to zero at +-90 deg. The advance ratio is clamped to the table's 0..2.
  Inside the table, between its 15 and 30 deg knots (the only wide gap), C_Rr is linear in alpha rather than the
  spline: the spline overshoots there to +0.2, an artifact of the 1-deg data before the gap (see _CRR_GAP_LO).
  The same between the AdvR knots 1.04 and 2, where the spline overshoots by a third. Elsewhere in the table
  (alpha -5 to 15 deg, AdvR 0 to 1.04, which covers Hummel's flights) the values are his code's.
- Spin direction. The P&C C_Rr table is for one spin sense (AdvR >= 0; Hummel's discs spin clockwise from above).
  The opposite spin mirrors the spin-induced part about the zero-spin row:
      C_Rr(a, -AdvR) = C_Rr(a, 0) - (C_Rr(a, AdvR) - C_Rr(a, 0)).
  The zero-spin row (at most 0.003 in size) is the wind-tunnel model's own asymmetry and is kept as measured.
- Below V_MIN the aerodynamics are zero (the frame is undefined at zero speed). The forces there are below 1e-5 N,
  but Hummel's spin-down N = C_Nr r does not scale with speed, so it switches off abruptly at V_MIN.
- The Robins-Magnus side force is neglected, as Hummel did (HUM03 §2.2.6).
"""

import math

import numpy as np

from .inputs import AeroSet, Disc

RHO = 1.225  # air density (kg/m^3): International Standard Atmosphere at sea level
G_HUMMEL = 9.7935  # the g of the roll term's sqrt(d/g) scaling, as in the code the coefficients came from (p. 81)
V_MIN = 0.01  # m/s: below this speed the aerodynamics are zero
EDGE_ON = 1e-9  # |v_p| / |v| below which x_F is undefined (velocity along the axis); body x stands in for it
ADV_MAX = 2.0  # the C_Rr table's largest advance ratio

HALF_PI = math.pi / 2
CORE_LO = math.radians(-10.0)  # the linear sets' core range: the P&C tables' alpha range
CORE_HI = math.radians(30.0)


def spline_slopes(x) -> np.ndarray:
    """The matrix S with slopes = S @ y for the not-a-knot cubic spline through (x, y), MATLAB's 'spline'.

    The interior rows make the second derivative continuous; the end rows make the third derivative continuous at
    the second and second-last knots (the not-a-knot condition, as in scipy's CubicSpline). Needs 4 or more knots.
    """
    x = np.asarray(x, dtype=float)
    n = len(x)
    h = np.diff(x)
    a = np.zeros((n, n))
    b = np.zeros((n, n - 1))  # right-hand side = b @ delta, delta the chord slopes
    for i in range(1, n - 1):
        a[i, i - 1], a[i, i], a[i, i + 1] = h[i], 2 * (h[i - 1] + h[i]), h[i - 1]
        b[i, i - 1], b[i, i] = 3 * h[i], 3 * h[i - 1]
    d = x[2] - x[0]
    a[0, 0], a[0, 1] = h[1], d
    b[0, 0], b[0, 1] = (h[0] + 2 * d) * h[1] / d, h[0] ** 2 / d
    d = x[-1] - x[-3]
    a[-1, -1], a[-1, -2] = h[-2], d
    b[-1, -2], b[-1, -1] = h[-1] ** 2 / d, (2 * d + h[-1]) * h[-2] / d
    chord = np.zeros((n - 1, n))
    chord[np.arange(n - 1), np.arange(n - 1)] = -1 / h
    chord[np.arange(n - 1), np.arange(1, n)] = 1 / h
    return np.linalg.solve(a, b @ chord)


def _hermite(x: np.ndarray, y: np.ndarray, m: np.ndarray, at: float):
    """The cubic through knots x with values y and slopes m (rows), evaluated at `at`; returns (value, slope)."""
    i = int(np.clip(np.searchsorted(x, at) - 1, 0, len(x) - 2))
    h = x[i + 1] - x[i]
    t = (at - x[i]) / h
    t2, t3 = t * t, t * t * t
    value = ((2 * t3 - 3 * t2 + 1) * y[i] + (t3 - 2 * t2 + t) * h * m[i]
             + (-2 * t3 + 3 * t2) * y[i + 1] + (t3 - t2) * h * m[i + 1])
    slope = ((6 * t2 - 6 * t) * (y[i] - y[i + 1]) / h + (3 * t2 - 4 * t + 1) * m[i] + (3 * t2 - 2 * t) * m[i + 1])
    return value, slope


class _Spline:
    def __init__(self, x, y):
        self.x = np.asarray(x, dtype=float)
        self.y = np.asarray(y, dtype=float)
        self.m = spline_slopes(self.x) @ self.y
        self.lo, self.hi = float(self.x[0]), float(self.x[-1])

    def __call__(self, at: float):
        return _hermite(self.x, self.y, self.m, at)


# Potts and Crowther (2002) tables as embedded in HUM03 simulate_flight.m. The radian columns are the code's own
# (rounded) values, so the knots sit where Hummel's did.
# CL_data and CD_data, p. 82: [rad, CL or CD, deg]; deg = -10, -3, 0, 5, 10, 15, 20, 25, 30.
_PC_ALPHA = [-0.1745, -0.05236, 0.0, 0.08727, 0.17453, 0.26180, 0.34907, 0.43633, 0.52360]
_PC_CL = [-0.2250, 0.0, 0.150, 0.4500, 0.7250, 0.9750, 1.2000, 1.4500, 1.6750]
_PC_CD_ALPHA = [-0.1745, -0.05236, 0.0, 0.08727, 0.1745, 0.26180, 0.3491, 0.4363, 0.5236]
_PC_CD = [0.1500, 0.0800, 0.1000, 0.1500, 0.2600, 0.3900, 0.5700, 0.7500, 0.9200]
# CM_data, pp. 82-83: deg = -10, -5, -3, 0, 3, 6, 9, 12, 15, 20, 23, 26, 30.
_PC_CM_ALPHA = [-0.174532925, -0.087266463, -0.052359878, 0.0, 0.052359878, 0.104719755, 0.157079633, 0.20943951,
                0.261799388, 0.34906585, 0.401425728, 0.453785606, 0.523598776]
_PC_CM = [-0.0380, -0.0220, -0.0140, -0.0060, -0.0060, -0.0020, 0.0000, 0.0100, 0.0220, 0.0440, 0.0600, 0.0840,
          0.1100]
# CRr_rad, CRr_AdvR and CRr_data, p. 83 (checked against the page image): deg = -5 .. 15 in 1 deg steps, then 30.
_PC_CRR_ALPHA = [-0.0873, -0.0698, -0.0524, -0.0349, -0.0175, 0.0000, 0.0175, 0.0349, 0.0524, 0.0698, 0.0873,
                 0.1047, 0.1222, 0.1396, 0.1571, 0.1745, 0.1920, 0.2094, 0.2269, 0.2443, 0.2618, 0.5236]
_PC_CRR_ADVR = [2.0, 1.04, 0.69, 0.35, 0.17, 0.0]  # one row of CRr_data each, in the code's order
_PC_CRR = [
    [-0.0172, -0.0192, -0.018, -0.0192, -0.018, -0.0172, -0.0172, -0.0168, -0.0188, -0.0164, -0.0136, -0.01, -0.0104,
     -0.0108, -0.0084, -0.008, -0.008, -0.006, -0.0048, -0.0064, -0.008, -0.003],
    [-0.0112, -0.0132, -0.012, -0.0132, -0.012, -0.0112, -0.0112, -0.0108, -0.0128, -0.0104, -0.0096, -0.0068, -0.0072,
     -0.0076, -0.0052, -0.0048, -0.0048, -0.0028, -0.0032, -0.0048, -0.0064, -0.003],
    [-0.0056, -0.0064, -0.0064, -0.0068, -0.0064, -0.0064, -0.0052, -0.0064, -0.0028, -0.0028, -0.004, -0.002, -0.004,
     -0.002, -0.0016, 0.0, 0.0, 0.0, 0.0, -0.002, -0.0048, -0.003],
    [-0.0012, -0.0016, -0.0004, -0.0028, -0.0016, -0.0016, -0.0004, 0.0004, 0.0004, 0.0008, 0.0004, 0.0008, 0.0012,
     0.0008, 0.002, 0.0028, 0.0032, 0.0024, 0.0028, 0.0004, -0.0012, -0.003],
    [-0.0012, -0.0012, -0.0016, -0.0016, -0.0012, -0.0004, 0.0004, 0.0008, 0.0008, 0.0016, 0.0004, 0.002, 0.0004,
     0.0016, 0.002, 0.002, 0.002, 0.0012, 0.0012, 0.0, -0.0012, -0.003],
    [-0.0012, -0.0012, -0.0004, -0.0008, -0.0008, -0.0008, 0.0004, 0.0004, 0.0004, 0.0008, 0.0004, 0.0008, -0.0004,
     0.0, 0.0, 0.0004, 0.0, 0.0, 0.0004, -0.002, -0.0012, -0.003],
]

_CL = _Spline(_PC_ALPHA, _PC_CL)
_CD = _Spline(_PC_CD_ALPHA, _PC_CD)
_CM = _Spline(_PC_CM_ALPHA, _PC_CM)

# interp2 'spline' is the tensor-product spline: along alpha for every AdvR row, then along AdvR. Rows sorted by
# ascending AdvR.
_CRR_ORDER = np.argsort(_PC_CRR_ADVR)
_CRR_ADVR = np.asarray(_PC_CRR_ADVR)[_CRR_ORDER]
_CRR_ROWS = _Spline(_PC_CRR_ALPHA, np.asarray(_PC_CRR)[_CRR_ORDER].T)  # y is (alpha knots, AdvR rows)
_CRR_ADVR_SLOPES = spline_slopes(_CRR_ADVR)
# The table jumps from 15 to 30 deg after 1-deg steps, and the spline through its noisy 1-deg data swings to
# C_Rr = +0.2 in that gap (ten times anything measured; Hummel's flights stayed below 15 deg so his code never met
# it). Across the gap C_Rr is interpolated linearly between the 15 and 30 deg knots instead.
_CRR_GAP_LO = _PC_CRR_ALPHA[-2]
# Likewise the AdvR knots jump from 1.04 to 2, and the spline there overshoots the table by a third (C_Rr -0.026 at
# AdvR 1.5 against -0.0188 at 2): linear across that gap too. Below AdvR 1.04 and 15 deg the spline is Hummel's.
_CRR_ADVR_GAP_LO = float(_CRR_ADVR[-2])


def _crr_rows(alpha: float) -> np.ndarray:
    """The C_Rr table's AdvR rows at `alpha`: Hummel's spline up to 15 deg, linear across the 15-30 deg gap."""
    if alpha <= _CRR_GAP_LO:
        return _CRR_ROWS(alpha)[0]
    t = (alpha - _CRR_GAP_LO) / (_CRR_ROWS.hi - _CRR_GAP_LO)
    return (1 - t) * _CRR_ROWS.y[-2] + t * _CRR_ROWS.y[-1]


def _crr_table(alpha: float, adv: float) -> float:
    rows = _crr_rows(alpha)
    if adv > _CRR_ADVR_GAP_LO:  # linear across the 1.04-2 gap, as across the alpha gap
        t = (adv - _CRR_ADVR_GAP_LO) / (_CRR_ADVR[-1] - _CRR_ADVR_GAP_LO)
        return float((1 - t) * rows[-2] + t * rows[-1])
    value, _ = _hermite(_CRR_ADVR, rows, _CRR_ADVR_SLOPES @ rows, adv)
    return float(value)


def _weight(alpha: float, edge: float, end: float) -> float:
    """cos^2 fade: 1 (with zero slope) at the core edge, 0 at +-90 deg."""
    return math.cos(HALF_PI * (alpha - edge) / (end - edge)) ** 2


def _side(alpha: float, lo: float, hi: float):
    """The core edge and the +-90 deg end on alpha's side of the core range."""
    return (hi, HALF_PI) if alpha > hi else (lo, -HALF_PI)


def _to_flat_plate(alpha, lo, hi, curve, flat: float) -> float:
    """Tangent continuation of `curve` beyond its core range, blended into the flat-plate value `flat`."""
    edge, end = _side(alpha, lo, hi)
    value, slope = curve(edge)
    w = _weight(alpha, edge, end)
    return flat + (value + slope * (alpha - edge) - flat) * w


def _drag_to_flat_plate(alpha, lo, hi, curve, cd90: float) -> float:
    """C_D rising monotonically from the core edge to cd90 at +-90 deg, matching the edge slope where it can."""
    edge, end = _side(alpha, lo, hi)
    value, slope = curve(edge)
    rise = cd90 - value
    if rise <= 1e-9:  # the core edge is at or above the flat plate (or there is no drag at all): fade across
        flat = cd90 * math.sin(alpha) ** 2
        return flat + (value - flat) * _weight(alpha, edge, end)
    span = abs(end - edge)
    outward = slope if end > 0 else -slope
    n = max(outward * span / rise, 1.0)
    u = (end - alpha) / (end - edge)
    return cd90 - rise * u**n


def _linear_curves(aero: AeroSet):
    """The linear form's C_L, C_D, C_M (HUM03 p. 89) as (value, slope) functions of alpha."""
    a0 = -aero.cl0 / aero.cla if aero.cla != 0.0 else 0.0
    cl = lambda a: (aero.cl0 + aero.cla * a, aero.cla)  # noqa: E731
    cd = lambda a: (aero.cd0 + aero.cda * (a - a0) ** 2, 2 * aero.cda * (a - a0))  # noqa: E731
    cm = lambda a: (aero.cm0 + aero.cma * a, aero.cma)  # noqa: E731
    return cl, cd, cm


def coefficients(aero: AeroSet, alpha: float, adv_ratio: float) -> dict:
    """C_L, C_D, C_M and C_Rr at angle of attack `alpha` (rad) and advance ratio `adv_ratio` = d r / (2 v).

    `adv_ratio` is signed: positive for Hummel's spin sense (clockwise seen from above, omega_z < 0 in the body
    frame). C_Rr is in the set's own form: dimensionless for potts-crowther, the sqrt(d/g) coefficient for linear sets
    (forces() applies the form). See the module docstring for the extension beyond the core alpha range.
    """
    alpha = min(max(alpha, -HALF_PI), HALF_PI)
    cd90 = aero.flat_plate_cd
    cl_fp = 0.5 * cd90 * math.sin(2 * alpha)
    if aero.form == "potts-crowther":
        curves = ((_CL, _CL.lo, _CL.hi), (_CD, _CD.lo, _CD.hi), (_CM, _CM.lo, _CM.hi))
    else:
        cl, cd, cm = _linear_curves(aero)
        curves = ((cl, CORE_LO, CORE_HI), (cd, CORE_LO, CORE_HI), (cm, CORE_LO, CORE_HI))
    (cl, cl_lo, cl_hi), (cd, cd_lo, cd_hi), (cm, cm_lo, cm_hi) = curves

    def inside(lo, hi):
        return lo <= alpha <= hi

    c_l = cl(alpha)[0] if inside(cl_lo, cl_hi) else _to_flat_plate(alpha, cl_lo, cl_hi, cl, cl_fp)
    c_d = cd(alpha)[0] if inside(cd_lo, cd_hi) else _drag_to_flat_plate(alpha, cd_lo, cd_hi, cd, cd90)
    c_m = cm(alpha)[0] if inside(cm_lo, cm_hi) else _to_flat_plate(alpha, cm_lo, cm_hi, cm, 0.0)

    if aero.form == "potts-crowther":
        lo, hi = _CRR_ROWS.lo, _CRR_ROWS.hi
        at = alpha if inside(lo, hi) else _side(alpha, lo, hi)[0]
        base = _crr_table(at, 0.0)
        spun = _crr_table(at, min(abs(adv_ratio), ADV_MAX))
        c_rr = base + math.copysign(1.0, adv_ratio) * (spun - base)
    else:
        lo, hi = CORE_LO, CORE_HI
        c_rr = aero.crr
    if not inside(lo, hi):
        c_rr *= _weight(alpha, *_side(alpha, lo, hi))
    return {"CL": float(c_l), "CD": float(c_d), "CM": float(c_m), "CRr": float(c_rr)}


def forces(disc: Disc, aero: AeroSet, rho: float, vel: np.ndarray, rot: np.ndarray,
           omega_body: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The aerodynamic force (world frame, at the center of mass) and torque (body frame) on a disc.

    `vel` is the center of mass's velocity relative to the air (world frame), `rot` the body-to-world rotation matrix
    (body z the disc's axis, out of the top) and `omega_body` the angular velocity in the body frame (rad/s).
    """
    vel = np.asarray(vel, dtype=float)
    rot = np.asarray(rot, dtype=float)
    speed = float(np.linalg.norm(vel))
    if speed < V_MIN:
        return np.zeros(3), np.zeros(3)
    c3 = -rot[:, 2]  # Hummel's c3, out of the underside
    omega = rot @ np.asarray(omega_body, dtype=float)
    v_c3 = float(vel @ c3)
    v_p = vel - v_c3 * c3
    v_p_mag = float(np.linalg.norm(v_p))
    alpha = math.atan2(v_c3, v_p_mag)  # = atan(vc3 / |vp|) (p. 89), safe edge-on
    u_vp = v_p / v_p_mag if v_p_mag > EDGE_ON * speed else rot[:, 0]  # C_L, C_M, C_Rr are zero edge-on
    u_lat = np.cross(c3, u_vp)
    u_vel = vel / speed
    q_a = 0.5 * rho * speed * speed * disc.area  # Hummel's Adp (p. 89)
    spin = float(omega @ c3)  # Hummel's omegaspin
    c = coefficients(aero, alpha, disc.radius * spin / speed)  # AdvR = d omegaspin / 2 / vmag (p. 89)

    u_lift = -np.cross(u_vel, u_lat)  # p. 90
    force = q_a * (c["CL"] * u_lift - c["CD"] * u_vel)

    d = disc.diameter
    roll_from_spin = c["CRr"] if aero.form == "potts-crowther" else math.sqrt(d / G_HUMMEL) * c["CRr"] * spin
    m_roll = q_a * d * (roll_from_spin + aero.crp * float(omega @ u_vp))  # Mvp (pp. 86, 89)
    m_pitch = q_a * d * (c["CM"] + aero.cmq * float(omega @ u_lat))  # Mlat (p. 90)
    m_spin = aero.cnr * spin  # Mspin (p. 90)
    torque = m_roll * u_vp + m_pitch * u_lat + m_spin * c3
    return force, rot.T @ torque
