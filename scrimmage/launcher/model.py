"""The ball in the launcher: squeezed between the spinning shooter wheel and the fixed concentric hood.

Geometry (see the plan's convention): the launcher's plane is the robot's x-z plane, the shaft along y. A ball's
place on its path is the angle phi of its center around the shaft, from +x toward +z. It enters at
`hood.entry_deg` and travels with phi decreasing, up the back of the wheel, to phi_exit = exit_angle_deg + 90.
Its center rides at R_c = R_wheel + gap / 2, so its contacts are gap / 2 from its center: the wheel's surface on
the inside, the hood on the outside. The squeeze is delta = D - gap.

Each ball has two degrees of freedom, its speed v along the path (s = arc length traveled, ds/dt = v) and its spin
omega (rad/s, positive for backspin: the side against the wheel moves forward faster than the center). With
r = gap / 2, V the wheel's surface speed and the ball's moment of inertia I:

    m dv/dt = F_w + F_h + W + S + G - M_roll / R_c
    I dw/dt = r F_w - r F_h - M_roll
    F_w = mu_w N_w tanh(s_w / v_s),  s_w = V - (v + w r)       (wheel friction; slip of wheel over ball)
    F_h = mu_h N_h tanh(s_h / v_s),  s_h = 0 - (v - w r)       (hood friction; the hood is fixed)
    M_roll = (M(N_w, x_w) + M(N_h, x_h)) tanh(u r / v_s), u = w + v / R_c   (rolling hysteresis, Task 2, per contact)

The squeeze split (ruling R18). The ball is two contacts in series, wheel side and hood side, sharing the squeeze:
x_w + x_h = delta(s), x being each contact's approach. Per contact N = K (2x)^n (the law `contact.py`'s damping is
calibrated on, `contact_force`); the wheel side has the wheel rubber in series and only the covered fraction f of
the patch (N_w = f N(x_w)). The hood must also hold the ball on its curved path, so

    N_h(x_h) - N_w(x_w) = C,   C = m v^2 / R_c - m g sin(phi)                (`split_squeeze`)

As the ball speeds up the squeeze shifts onto the hood and off the wheel: grip falls with speed. If even the whole
squeeze on the hood can't carry C, the ball has lifted off the wheel: N_w = 0, no wheel friction, N_h = N(delta)
(a state, not an error). Each contact is damped by its own approach rate (Hunt-Crossley, as `contact_force`):
x_w' = X_d delta' v + X_c C', x_h' = delta' v - x_w', with X_d = k_h / (k_w + k_h) and X_c = -1 / (k_w + k_h) from
the contacts' slopes k = dN/dx. The ball's path stays the circle R_c (the plan's convention); its center's small
radial shift is not followed, but the work the contacts do on it is charged to the path:

    W = -delta'(s) (N_h (1 - X_d) + N_w X_d)          (the flares' wedge: the squeeze's work as the profile changes)
    S = (N_h - N_w) X_c C' / v                        (the radial shift: the centripetal load moving the squeeze)

so W v + S v = -(N_h x_h' + N_w x_w'), the power the contacts put into the ball's deformation. Elastic, it is the
stored energy's rate, and the ball gets it back as the squeeze is released; damped, the difference is the loss.
C' = 2 m v dv/dt / R_c + m g cos(phi) v / R_c: its dv/dt part makes S an added mass (m_eff - m), and the normal
forces' dependence on v and dv/dt enters the friction, wedge and rolling rows of the implicit step.

The wheel's reaction to F_w, a torque F_w R_wheel, loads the shaft (`ShaftModel.step`).

Energy, at the ball/wheel interface: the work the wheel's friction force does, F_w V, is `from_shaft`; friction
dissipates F_w s_w + F_h s_h (`slip_loss`); rolling hysteresis M_roll u and the contacts' damping (the squeeze's
work, -(W + S) v, less the elastic energy stored in the two contacts) are `hysteresis_loss`; gravity's is
`potential`. The elastic energy stored in the lead-in is returned in the release, less the damping.

The squeeze profile delta(s) (rulings R6, R17). The entry is a lead-in, the hood's entry end flared at
`hood.flare_deg`; the exit is either
- (`hood.exit_flare_deg` unset, the default and most FRC hoods) the hood ending at the arc's end, s = L_arc
  (ruling R20). Past the edge the hood no longer presses the ball; only the part of the ball still over the hood
  can. A sphere's surface a distance xi along the path past the edge lies xi^2 / D (to first order, the sagitta of
  its diameter-D section) further from the hood than its lowest point, so the squeeze the hood still imposes
  falls parabolically from zero slope:
      delta(xi) = delta_end - xi^2 / D,   0 <= xi <= sqrt(D delta_end),   then 0,
  with delta_end the squeeze at the edge. The unloading rate grows from zero, not stepping to a fixed fraction of
  v. The ball is followed round the path circle over the release, so its pass ends at s = L_arc + sqrt(D delta_end),
  sqrt(D delta_end) / R_c further round than the hood's end. How much of the stored elastic energy the release
  returns, which Hunt-Crossley damping sets here at unloading rates beyond the drop it is calibrated on, is the
  model's main remaining uncertainty. It is bounded by an undamped release, which returns all of it: a few % of
  the exit speed, up to about 10% for a stiff ball. Or
- a flared lip at `hood.exit_flare_deg`, releasing the squeeze as the entry flare loads it and ending at L_arc.
Either way the ball leaves along the tangent at the hood's end: once the hood no longer holds it on the curve it
travels straight on, so its direction is `exit_angle_deg` (launch_deg); the release adds its length to the distance
traveled, its time to t_exit and its climb to `potential`, but not to the direction.

Gravity (ruling R7) acts along the path and in the normal balance. With the direction of travel
t = (sin phi, -cos phi) and the outward radius e_r = (cos phi, sin phi), it adds G = m g cos(phi) to m dv/dt
(negative while the ball climbs the back of the wheel) and -m g sin(phi) to N_h (below the shaft it presses the ball
onto the hood). Its work is `potential`, the rise in potential energy m g R_c (sin phi - sin phi_entry). The ball
climbs about 0.2 m up the back of a 4 in. wheel; that costs about 2% of the exit speed at squeezes of 0.1 to 0.4 D
and a quarter of it at 0.02 D, where the grip is weak and the ball slow.
"""

import math
from dataclasses import dataclass, field

from .. import i18n
from .contact import (
    FRICTION_SLIP_SPEED, Split, _damping, rolling_loss_torque, split_squeeze, wheel_contact_fraction, wheel_stiffness,
)
from .inputs import Ball, LauncherConfigError, Setup
from .shaft import ShaftModel

# Default integration step (s). Chosen so halving it changes the exit speed by well under 1% for the presets
# (tests/test_launcher_model.py::test_step_converges); a setup's `dt` overrides it.
DEFAULT_DT = 1e-4

# A ball still in contact this long after it entered is reported "jammed". A documented choice: a pass takes
# tens of milliseconds (60-160 ms for a Power Cell on a 4 in. wheel at 60 rps), so 0.5 s means it has stalled.
# A ball pushed back out of the entry by the flare (the wheel too slow or too slippery to draw it in) is "jammed"
# too: it never got through.
JAM_TIME = 0.5

# The lead-in (ruling R6) is the hood's flared entry end, `hood.flare_deg` (an input): a constant-gap hood would snap
# the ball into full compression at the entry. Along a straight flare the squeeze changes linearly with the path, at
# d delta / ds = tan(flare), so the ramp is delta / tan(flare) long: a fixed hood shape squeezes a ball over a longer
# stretch the more it squeezes it. tan(flare) is the flare's nip slope: wheel and hood friction must beat it to draw
# the ball in (as in a rolling mill's nip). The exit (ruling R17) is the hood's abrupt end, or a flared lip at
# `hood.exit_flare_deg` (see the module notes). The lead-in, and a flared lip, lie inside the wrap, which stays the
# hood's contact arc; on a wrap shorter than the ramps the squeeze peaks below delta where they meet.

# Standard gravity (m/s^2), rounded as in contact.py (CGPM 1901: 9.80665).
GRAVITY = 9.81


@dataclass(frozen=True)
class FedBall:
    """A ball offered to the launcher: when it can be fed, where across the wheels, how fast it arrives."""

    available_at: float
    lane: float  # position along the shaft (m)
    entry_speed: float  # speed along the path at the entry (m/s)
    ball: Ball
    yaw_deg: float = 0.0


@dataclass(frozen=True)
class Energy:
    """Joules, at the ball/wheel interface:
    from_shaft + entry_kinetic = exit_linear + exit_spin + potential + slip_loss + hysteresis_loss."""

    from_shaft: float
    entry_kinetic: float
    exit_linear: float
    exit_spin: float
    potential: float  # rise in potential energy from the entry (positive when the ball rises)
    slip_loss: float
    hysteresis_loss: float


@dataclass(frozen=True)
class ExitedBall:
    """A ball's outcome. `status` is "launched", "no_contact", "no_wheel" or "jammed" from the Launcher (the run
    adds "unfed" for balls never fed). Exit fields (t_exit, speed, spin, launch_deg) are NaN unless launched."""

    index: int
    status: str
    t_enter: float
    t_exit: float
    speed: float  # m/s along the path at the exit
    spin: float  # rad/s, positive for backspin
    launch_deg: float  # above horizontal, forward
    yaw_deg: float
    lane: float
    energy: Energy
    # The wheel's slip under the ball at the exit, (V - v - w r) / V with V the wheel's surface speed: about 0 when
    # the wheel grips the ball out (exit speed follows wheel speed), large when the pass is grip-limited (more wheel
    # speed buys little; more squeeze, coverage, wrap or wheel friction does). NaN unless launched.
    exit_slip: float = math.nan


NAN = math.nan


@dataclass
class _Pass:
    """A ball in contact. s is the arc length its center has traveled from the entry."""

    index: int
    fed: FedBall
    t_enter: float
    s: float
    v: float
    w: float
    from_shaft: float = 0.0
    slip: float = 0.0
    hysteresis: float = 0.0
    entry_kinetic: float = 0.0
    surface: float = 0.0  # the wheel's surface speed at the end of the last step (m/s)
    split: tuple | None = None  # (s, v, Split) cache: the split at the ball's current state
    consts: dict = field(default_factory=dict)
    wheel: dict = field(default_factory=dict)  # `Launcher._wheel` by squeeze, for the few squeezes a substep visits


def _chord(x: float, scale: float) -> float:
    """tanh(x / scale) / x, the chord slope of the regularized Coulomb law (1 / scale at x = 0)."""
    if abs(x) < 1e-6 * scale:
        return 1.0 / scale
    return math.tanh(x / scale) / x


class Launcher:
    """Balls rolling between the shooter wheel and the hood, all loading one shaft. Stepped by motor voltage."""

    def __init__(self, setup: Setup, external_bus: bool = False):
        """`external_bus`: the shaft owns no battery; the world sets its bus voltage (`ShaftModel.set_bus_voltage`)
        and charges its supply current to the shared battery."""
        self.setup = setup
        self.shaft = ShaftModel(setup.shaft, None if external_bus else setup.battery)
        self.r_wheel = setup.wheels.diameter / 2.0
        self.gap = setup.hood.gap
        self.r_path = self.r_wheel + self.gap / 2.0
        self.exit_angle_deg = setup.hood.exit_angle_deg  # the hood's angle now (`set_hood_angle`)
        self.path = self._path_for(self.exit_angle_deg)  # arc length of the hood, entry to its exit end (m)
        self._nip = math.tan(math.radians(setup.hood.flare_deg))  # d delta / ds along the entry flare
        exit_flare = setup.hood.exit_flare_deg
        self._exit_nip = None if exit_flare is None else math.tan(math.radians(exit_flare))
        self._phi_entry = math.radians(setup.hood.entry_deg)
        self.time = 0.0
        self._count = 0
        self._active: list[_Pass] = []
        self._done: list[ExitedBall] = []

    def default_dt(self) -> float:
        return self.setup.dt if self.setup.dt is not None else DEFAULT_DT

    def in_contact(self) -> int:
        return len(self._active)

    def positions(self) -> list[tuple[int, float, float]]:
        """(index, lane, s) for each ball in contact, s the arc length (m) its center has traveled from the entry."""
        return [(p.index, p.fed.lane, p.s) for p in self._active]

    def _path_for(self, exit_angle_deg: float) -> float:
        """The hood's arc length for an exit angle: the entry is fixed, the exit end turns with the angle."""
        wrap = self.setup.hood.entry_deg - (exit_angle_deg + 90.0)
        if wrap <= 0.0:
            raise LauncherConfigError(
                i18n.t("hood angle {exit_angle_deg}: the wrap, entry_deg {entry_deg} less {exit_plus_90} "
                       "(exit_angle_deg + 90), is {wrap}; it must be above 0", exit_angle_deg=exit_angle_deg,
                       entry_deg=self.setup.hood.entry_deg, exit_plus_90=exit_angle_deg + 90.0, wrap=wrap)
            )
        return self.r_path * math.radians(wrap)

    def set_hood_angle(self, exit_angle_deg: float) -> None:
        """Turn the hood about the shaft to a new exit angle (degrees above horizontal), between steps.

        The entry end stays where it is; the exit end, and so the arc length and every ball's end point, moves with
        the angle. A ball already past the new end finishes "launched" in the next substep, at the new angle. The
        hood's work on a ball as it moves (the change in the squeeze's stored elastic energy at the ball, as its
        squeeze profile shifts under it) is booked as hysteresis, so its energy books still close.
        Raises LauncherConfigError if the angle leaves no wrap."""
        if exit_angle_deg == self.exit_angle_deg:
            return
        path = self._path_for(exit_angle_deg)
        before = [self._stored(p, p.s, self._split(p, p.s, p.v)) for p in self._active]
        self.exit_angle_deg = exit_angle_deg
        self.path = path
        for p, stored in zip(self._active, before):
            self._set_end(p)
            # The cached split is for the old profile: keep it only as the solver's starting guess (a NaN key never
            # matches).
            p.split = (NAN, NAN, p.split[2])
            p.hysteresis += stored - self._stored(p, p.s, self._split(p, p.s, p.v))

    def insert(self, fed: FedBall, t: float) -> ExitedBall | None:
        """Start a ball at the entry at time t (the launcher's current time).

        A ball that cannot touch the wheel (no squeeze, or no wheel under its lane) finishes at once: its
        `ExitedBall` is returned here, not by a later `step`. Otherwise returns None."""
        index = self._count
        self._count += 1
        ball = fed.ball
        m = ball.mass
        entry_kinetic = 0.5 * m * fed.entry_speed**2
        delta = ball.diameter - self.gap
        status = None
        if delta <= 0.0:
            status = "no_contact"
        elif wheel_contact_fraction(fed.lane, ball.diameter, delta, self.setup.wheels) <= 0.0:
            status = "no_wheel"
        if status is not None:
            energy = Energy(0.0, entry_kinetic, NAN, NAN, 0.0, 0.0, 0.0)
            return ExitedBall(index, status, t, NAN, NAN, NAN, NAN, fed.yaw_deg, fed.lane, energy)
        p = _Pass(index, fed, t, 0.0, fed.entry_speed, 0.0, entry_kinetic=entry_kinetic)
        p.consts = {"delta": delta, "m": m, "I": ball.inertia_factor * m * (ball.diameter / 2.0) ** 2}
        self._set_end(p)
        self._active.append(p)
        return None

    def _set_end(self, p: _Pass) -> None:
        """Where the ball's pass ends, for the hood's current arc length."""
        c = p.consts
        c["end"] = self.path
        if self._exit_nip is None:
            # The abrupt release (see the module notes): past the hood's end the squeeze falls as
            # delta_end - xi^2 / D, reaching zero at xi = sqrt(D delta_end).
            at_end = min(self._nip * self.path, c["delta"])
            release = math.sqrt(p.fed.ball.diameter * at_end)
            c.update(at_end=at_end, release=release, end=self.path + release)

    def _phi(self, s: float) -> float:
        """The angle (rad) of the ball's center around the shaft at arc length s."""
        return self._phi_entry - s / self.r_path

    # --- the squeeze profile -------------------------------------------------------------------------------------

    def _squeeze(self, p: _Pass, s: float) -> tuple[float, float]:
        """delta and d delta / ds at arc length s: rising at tan(flare) from the entry, the constant gap's full
        squeeze, then the exit: falling at tan(exit flare) to the hood's end, or (no exit flare) full to the hood's
        end and falling parabolically, delta_end - xi^2 / D with xi = s - L_arc, to zero past it."""
        c = p.consts
        full, nip = c["delta"], self._nip
        into = nip * s
        if self._exit_nip is None:
            if s <= self.path:
                return (full, 0.0) if into >= full else (max(into, 0.0), nip)
            xi = s - self.path
            if xi >= c["release"]:
                return 0.0, 0.0
            d = p.fed.ball.diameter
            return max(c["at_end"] - xi * xi / d, 0.0), -2.0 * xi / d
        left = self._exit_nip * (self.path - s)
        if into >= full and left >= full:
            return full, 0.0
        if into <= left:
            return max(into, 0.0), nip
        return max(left, 0.0), -self._exit_nip

    def _wheel(self, p: _Pass, delta: float) -> tuple[float, float | None]:
        """The wheel coverage f and the wheel rubber's stiffness under the patch at squeeze delta.

        Partial coverage: only the fraction f of the contact patch over wheel material is pressed by the wheel. That
        part is the same contact as a fully covered one with f of the area, so its force is f times the full
        contact's, with the wheel rubber's stiffness per patch width unchanged (the punch stiffness of
        `wheel_stiffness` is proportional to the width). Both use the patch of the total squeeze, sqrt(D delta).
        """
        if delta <= 0.0:
            return 0.0, None
        got = p.wheel.get(delta)
        if got is None:
            if len(p.wheel) >= 4:
                p.wheel.clear()
            ball = p.fed.ball
            f = wheel_contact_fraction(p.fed.lane, ball.diameter, delta, self.setup.wheels)
            got = p.wheel[delta] = f, wheel_stiffness(self.setup.wheels, math.sqrt(ball.diameter * delta))
        return got

    def _radial(self, p: _Pass, s: float, v: float) -> float:
        """How much harder the hood pushes on the ball than the wheel: m v^2 / R_c - m g sin(phi)."""
        m = p.consts["m"]
        return m * v * v / self.r_path - m * GRAVITY * math.sin(self._phi(s))

    def _split(self, p: _Pass, s: float, v: float) -> Split:
        """The squeeze's elastic split between the wheel and hood contacts at arc length s and speed v."""
        if p.split is not None and p.split[0] == s and p.split[1] == v:
            return p.split[2]
        delta, _ = self._squeeze(p, s)
        f, k = self._wheel(p, delta)
        guess = p.split[2].x_h if p.split is not None else None
        sp = split_squeeze(delta, self._radial(p, s, v), p.fed.ball, k, f, guess)
        p.split = (s, v, sp)
        return sp

    def _stored(self, p: _Pass, s: float, sp: Split) -> float:
        """The elastic energy (J) in the ball's contacts."""
        delta, _ = self._squeeze(p, s)
        f, k = self._wheel(p, delta)
        return sp.energy(p.fed.ball, k, f)

    # --- stepping ------------------------------------------------------------------------------------------------

    def _linearize(self, p: _Pass, h: float, wheel_speed: float) -> dict:
        """One ball's forces at its current state, and the linearly implicit step's matrix for (dv, dw).

        The stiff parts are treated implicitly: friction against slip (and so against the wheel's speed), rolling
        loss against the rolling rate, and the squeeze damping against v (N is affine in the squeeze rate, so its
        slope is (N - N_elastic) / rate). Friction and rolling use their chord slope F(x) / x, not the tangent: in
        a step the slip can cross the narrow tanh band, where the tangent slope is about zero in saturation and the
        step would be explicit there and overshoot to the other side (a two-step chatter of the slip, and of a light
        wheel's speed). Through the chord each acts as a damper of coefficient F / x, which an implicit step drives
        toward zero slip without crossing it. Any bounded slope keeps the step first-order consistent (a W-method).

        The squeeze split makes both normal forces depend on v (through the centripetal load): dN/dv enters the
        friction and wedge rows, and the radial shift's angular-momentum term enters as the added mass m_eff - m.
        """
        c = p.consts
        m, inertia = c["m"], c["I"]
        ball = p.fed.ball
        r = self.gap / 2.0
        rc = self.r_path
        vs = FRICTION_SLIP_SPEED
        mu_w, mu_h = self.setup.wheels.friction, self.setup.hood.friction
        surface = wheel_speed * self.r_wheel
        v, w = p.v, p.w

        delta, slope = self._squeeze(p, p.s)
        sp = self._split(p, p.s, v)
        phi = self._phi(p.s)
        # The split's sensitivities: d x_w = X_d d delta + X_c d radial (from N_h(delta - x_w) - N_w(x_w) = radial).
        stiff = sp.k_w + sp.k_h
        if sp.lifted == "wheel" or delta <= 0.0:
            x_d, x_c = 0.0, 0.0
        elif sp.lifted == "hood":
            x_d, x_c = 1.0, 0.0
        elif stiff > 0.0:
            x_d, x_c = sp.k_h / stiff, -1.0 / stiff
        else:
            x_d, x_c = 0.5, 0.0
        dr_dv = 2.0 * m * v / rc  # d radial / dv
        dr_rest = m * GRAVITY * math.cos(phi) * v / rc  # d radial / dt from gravity's turning, phi' = -v / R_c
        # Each contact's approach rate, for its own damping: x_w' = X_d delta' v + X_c (dr_rest + dr_dv dv/dt),
        # x_h' = delta' v - x_w'. The forces are taken at dv/dt = 0 here; their dv/dt part (N_w falls and N_h rises
        # as the ball speeds up) is treated implicitly below, through dn_*_da.
        rate_w = x_d * slope * v + x_c * dr_rest
        rate_h = slope * v - rate_w
        n_w, n_h = sp.damped(rate_w, rate_h, ball)
        n_w_el, n_h_el = sp.n_w, sp.n_h
        alpha2 = 2.0 * _damping(ball.restitution)
        dnw_da = n_w_el * alpha2 * sp.ball_share * x_c * dr_dv if n_w > 0.0 else 0.0  # d N_w / d(dv/dt)
        dnh_da = -n_h_el * alpha2 * x_c * dr_dv if n_h > 0.0 else 0.0
        dnw_dv = sp.k_w * x_c * dr_dv  # the elastic forces' dependence on v, through the radial load
        dnh_dv = -sp.k_h * x_c * dr_dv
        # The squeeze's force along the path is the contacts' power on the ball's deformation over v:
        #   -(N_h x_h' + N_w x_w') / v = -delta' (N_h (1 - X_d) + N_w X_d) + (N_h - N_w) X_c radial' / v,
        # the first part the flares' wedge, the second the radial shift (the ball's center moving out as the
        # centripetal load grows takes that energy from its speed). radial' = dr_rest + dr_dv dv/dt, so the shift's
        # dv/dt part is an added mass. It vanishes with the forces, as when a fast release outruns the ball.
        lean = n_h * (1.0 - x_d) + n_w * x_d
        lean_el = n_h_el * (1.0 - x_d) + n_w_el * x_d
        wedge = -slope * lean
        dlean_dv = (1.0 - x_d) * dnh_dv + x_d * dnw_dv + ((lean - lean_el) / v if v != 0.0 and lean > 0.0 else 0.0)
        c_wedge = slope * dlean_dv  # -d wedge / dv
        held = n_h - n_w  # the radial load the contacts actually carry
        shift = held * x_c * m * GRAVITY * math.cos(phi) / rc
        # m_eff >= m whenever the hood carries the load (held >= 0). It drops below m only when held < 0 (the wheel
        # pushing harder than the hood: gravity above the shaft at low speed, or the hood side unloading fast enough
        # for its damping to zero it), by at most about 2 x / (n R_c) (contact approach x). That is far from 1 at
        # ordinary squeezes. The floor at m / 2 is a numerical guard against a non-positive mass in such an extreme
        # squeeze, not physics; it never binds in the test suite or in the report's setups (min m_eff / m = 1.0).
        m_eff = max(m - held * x_c * 2.0 * m / rc, 0.5 * m)
        grav = m * GRAVITY * math.cos(phi)
        slip_w, slip_h = surface - v - w * r, w * r - v
        t_w, t_h = math.tanh(slip_w / vs), math.tanh(slip_h / vs)
        f_w = mu_w * n_w * t_w
        k_w = mu_w * n_w * _chord(slip_w, vs)
        # d f / d(dv) through the normal force: its v part, and its dv/dt part (dv/dt = dv / h over the step).
        q_w = mu_w * t_w * (dnw_dv + dnw_da / h)
        f_h = mu_h * n_h * t_h
        k_h = mu_h * n_h * _chord(slip_h, vs)
        q_h = mu_h * t_h * (dnh_dv + dnh_da / h)
        c_wedge += slope * ((1.0 - x_d) * dnh_da + x_d * dnw_da) / h
        # Rolling: the contacts sit on the line from the shaft through the center, which turns at -v / R_c, so the
        # ball's material rolls through them at u = w + v / R_c relative to that line (pure rolling on the fixed hood
        # gives u r = v (1 + r / R_c), the distance rolled along the hood's concave face). The loss is M u; its
        # generalized forces are -M on the spin and -M / R_c on the path. Each contact's patch is its own.
        m_w = rolling_loss_torque(n_w, ball, 2.0 * sp.x_w)
        m_h = rolling_loss_torque(n_h, ball, 2.0 * sp.x_h)
        m_roll = m_w + m_h
        u = w + v / rc
        t_r = math.tanh(u * r / vs)
        roll = m_roll * t_r
        k_r = m_roll * r * _chord(u * r, vs)  # roll / u
        # d roll / d(dv) through the normal forces' dv/dt part (M is proportional to N at each contact).
        q_r = t_r * ((m_w / n_w * dnw_da if n_w > 0.0 else 0.0) + (m_h / n_h * dnh_da if n_h > 0.0 else 0.0)) / h

        g_v = (f_w + f_h + wedge + grav + shift - roll / rc) / m_eff
        g_w = (r * f_w - r * f_h - roll) / inertia
        # (1 - h J) for (dv, dw), and the column for the wheel's speed: d g / d Omega = k_w R_wheel (1/m_eff, r/I).
        a11 = 1.0 + h * (k_w - q_w + k_h - q_h + c_wedge + (k_r / rc + q_r) / rc) / m_eff
        a12 = h * ((k_w - k_h) * r + k_r / rc) / m_eff
        a21 = h * ((k_w - q_w - k_h + q_h) * r + k_r / rc + q_r) / inertia
        a22 = 1.0 + h * ((k_w + k_h) * r * r + k_r) / inertia
        det = a11 * a22 - a12 * a21
        # Solve for dv, dw given dOmega: (dv, dw) = y - z dOmega.
        y_v = h * (g_v * a22 - g_w * a12) / det
        y_w = h * (g_w * a11 - g_v * a21) / det
        b_v = h * k_w * self.r_wheel / m_eff
        b_w = h * k_w * self.r_wheel * r / inertia
        z_v = -(b_v * a22 - b_w * a12) / det
        z_w = -(b_w * a11 - b_v * a21) / det
        return dict(
            surface=surface, split=sp, f_w=f_w, k_w=k_w, q_w=q_w, f_h=f_h, k_h=k_h, q_h=q_h, wedge=wedge,
            c_wedge=c_wedge, shift=shift, m_eff=m_eff, roll=roll, k_r=k_r, q_r=q_r,
            y_v=y_v, y_w=y_w, z_v=z_v, z_w=z_w,
        )

    def _apply(self, p: _Pass, lin: dict, h: float, d_omega: float) -> tuple[float, float]:
        """Take the step with the wheel's speed changing by d_omega over it. Returns the wheel-side friction force
        the step applied and the new arc length."""
        r, rc = self.gap / 2.0, self.r_path
        m = p.consts["m"]
        dv = lin["y_v"] - lin["z_v"] * d_omega
        dw = lin["y_w"] - lin["z_w"] * d_omega
        d_surface = d_omega * self.r_wheel
        # The forces the step actually applied (each linearized at the new state), so the energy books close:
        # m dv = h (f_w_e + f_h_e + wedge_e + grav + shift_e - roll_e / R_c).
        k_w, k_h, k_r = lin["k_w"], lin["k_h"], lin["k_r"]
        f_w_e = lin["f_w"] + k_w * (d_surface - dv - r * dw) + lin["q_w"] * dv
        f_h_e = lin["f_h"] + k_h * (r * dw - dv) + lin["q_h"] * dv
        wedge_e = lin["wedge"] - lin["c_wedge"] * dv
        shift_e = lin["shift"] - (lin["m_eff"] - m) * dv / h
        roll_e = lin["roll"] + k_r * (dw + dv / rc) + lin["q_r"] * dv
        v_mid, w_mid = p.v + 0.5 * dv, p.w + 0.5 * dw
        surface_mid = lin["surface"] + 0.5 * d_surface
        s_old = p.s
        s_new = p.s + h * v_mid

        p.from_shaft += h * f_w_e * surface_mid
        p.slip += h * (f_w_e * (surface_mid - v_mid - w_mid * r) + f_h_e * (w_mid * r - v_mid))
        stored_old = self._stored(p, s_old, lin["split"])
        p.v, p.w, p.s = p.v + dv, p.w + dw, s_new
        stored = self._stored(p, s_new, self._split(p, s_new, p.v)) - stored_old
        # Hysteresis: rolling, and the squeeze work (the wedge and the radial shift) less the elastic energy stored;
        # what is left is the contacts' damping.
        p.hysteresis += h * roll_e * (w_mid + v_mid / rc) + (-h * (wedge_e + shift_e) * v_mid - stored)
        p.surface = lin["surface"] + d_surface
        return f_w_e, s_new

    def _advance(self, p: _Pass, h: float, wheel_speed: float) -> tuple[float, float]:
        """Advance one ball alone, the wheel's speed held over the step."""
        return self._apply(p, self._linearize(p, h, wheel_speed), h, 0.0)

    def _finish(self, p: _Pass, status: str, t_exit: float) -> ExitedBall:
        c = p.consts
        launched = status == "launched"
        exit_linear = 0.5 * c["m"] * p.v**2 if launched else NAN
        exit_spin = 0.5 * c["I"] * p.w**2 if launched else NAN
        potential = c["m"] * GRAVITY * self.r_path * (math.sin(self._phi(p.s)) - math.sin(self._phi_entry))
        energy = Energy(p.from_shaft, p.entry_kinetic, exit_linear, exit_spin, potential, p.slip, p.hysteresis)
        if not launched:
            return ExitedBall(p.index, status, p.t_enter, NAN, NAN, NAN, NAN, p.fed.yaw_deg, p.fed.lane, energy)
        # The velocity leaves tangent to the path at phi_exit, pointing the hood's exit angle at that moment above
        # horizontal.
        surface = p.surface
        slip = (surface - p.v - p.w * self.gap / 2.0) / surface if surface > 0.0 else NAN
        return ExitedBall(
            p.index, status, p.t_enter, t_exit, p.v, p.w, self.exit_angle_deg, p.fed.yaw_deg, p.fed.lane, energy,
            exit_slip=slip,
        )

    def _substep(self, h: float, voltage: float) -> None:
        """One step of the balls and the shaft together, linearly implicit in all of them.

        The wheel's friction on a ball depends steeply on the wheel's speed, and the reaction loads the shaft, so a
        shaft stepped explicitly against that load chatters when the wheel is light. Here the shaft's speed is in
        the same implicit solve: J dOmega = h (a - b Omega_new - R_wheel sum F_w,new), with each ball's (dv, dw) =
        y - z dOmega from its own 2x2 block (a Schur complement on the shared shaft). ShaftModel then takes the step
        with the load those forces apply, which gives the same speed to O(h^2) with its exact motor update."""
        shaft = self.shaft
        omega = shaft.speed
        # A ball the hood was turned to end behind (`set_hood_angle`) has already left: it finishes now, unmoved.
        for p in [p for p in self._active if p.s >= p.consts["end"]]:
            self._done.append(self._finish(p, "launched", self.time))
            self._active.remove(p)
        lins = [self._linearize(p, h, omega) for p in self._active]
        a, b = shaft.torque_coefficients(voltage)
        rw = self.r_wheel
        lhs = shaft.inertia + h * b
        rhs = h * (a - b * omega)
        for lin in lins:
            # The wheel force at the step's end: f_w + k_w (R dOmega - dv - r dw) + q_w dv, (dv, dw) = y - z dOmega.
            k_w, q_w = lin["k_w"], lin["q_w"]
            lhs += h * rw * (k_w * (rw + lin["z_v"] + (self.gap / 2.0) * lin["z_w"]) - q_w * lin["z_v"])
            rhs += h * rw * (-lin["f_w"] + k_w * (lin["y_v"] + (self.gap / 2.0) * lin["y_w"]) - q_w * lin["y_v"])
        d_omega = rhs / lhs if math.isfinite(lhs) else 0.0  # an infinite inertia holds the speed

        load = 0.0
        still: list[_Pass] = []
        for p, lin in zip(self._active, lins):
            saved = (p.s, p.v, p.w, p.from_shaft, p.slip, p.hysteresis, p.surface)
            s_old = p.s
            end = p.consts["end"]
            force, s_new = self._apply(p, lin, h, d_omega)
            if s_new >= end and s_new > s_old:
                # Land exactly on the exit: redo the step for the fraction of h that reaches it (alone, the wheel's
                # speed held: the release has nearly ended the squeeze there, so the ball barely loads the wheel).
                frac = 1.0
                for _ in range(3):  # the distance is nearly linear in the fraction, so a few passes converge
                    frac = min(1.0, frac * (end - s_old) / (s_new - s_old))
                    p.s, p.v, p.w, p.from_shaft, p.slip, p.hysteresis, p.surface = saved
                    force, s_new = self._advance(p, frac * h, omega)
                    if s_new <= s_old or abs(s_new - end) <= 1e-12:
                        break
                p.s = end
                load += force * rw * frac
                self._done.append(self._finish(p, "launched", self.time + frac * h))
                continue
            load += force * rw
            t_now = self.time + h
            if s_new < 0.0:
                # Pushed back out of the entry: it never got through.
                self._done.append(self._finish(p, "jammed", t_now))
            elif t_now - p.t_enter >= JAM_TIME:
                self._done.append(self._finish(p, "jammed", t_now))
            else:
                still.append(p)
        self._active = still
        shaft.step(h, voltage, load)
        self.time += h

    def step(self, dt: float, voltage: float) -> list[ExitedBall]:
        """Advance dt seconds with `voltage` on the shooter motor. Returns the balls that finished in this step.

        While balls are in contact the step is split into substeps no longer than `default_dt()`. A step of no time
        (dt <= 0) changes nothing."""
        if dt <= 0.0:
            return []
        if not self._active:
            self.shaft.step(dt, voltage, 0.0)
            self.time += dt
        else:
            n = max(1, math.ceil(dt / self.default_dt() - 1e-9))
            h = dt / n
            for _ in range(n):
                if self._active:
                    self._substep(h, voltage)
                else:
                    self.shaft.step(h, voltage, 0.0)
                    self.time += h
        done, self._done = self._done, []
        return done
