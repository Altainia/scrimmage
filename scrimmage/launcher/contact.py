"""Contact laws for a ball squeezed between the shooter wheel and the hood.

All units are SI: meters, newtons, seconds. `delta` is the ball's total squeeze across its diameter (ball diameter
minus the gap at that contact), the same squeeze a ball gets between two flat plates, which is how
`Ball.stiffness_n` is defined.

The energy lost in one load-unload cycle is a fraction 1 - e^2 of the stored energy, where e is the ball's
`restitution`. Both loss terms come from it:
  - `normal_force` damps the squeeze (Hunt-Crossley), so a drop on a hard floor at RESTITUTION_SPEED bounces
    back at e of the speed;
  - `rolling_loss_torque` is the hysteresis of rolling (Tabor), each part of the ball losing the same 1 - e^2 of
    its strain energy as it rolls into and out of the contact patch.
"""

import math
from dataclasses import dataclass
from functools import lru_cache

from .inputs import Ball, LauncherConfigError, Wheels

# The impact speed at which `Ball.restitution` is defined: a drop from 0.46 m (18 in.), sqrt(2 * 9.81 * 0.46) =
# 3.0 m/s. A documented modeling choice: Hunt-Crossley damping makes the bounce depend on the impact speed, so the
# restitution input needs one; measure it with a drop from this height.
RESTITUTION_SPEED = 3.0

# Friction is regularized near zero slip by tanh(slip / FRICTION_SLIP_SPEED): full Coulomb friction (96% of it)
# from 2 slip speeds up. A documented modeling choice: small next to the ball's 1 to 30 m/s speeds through the
# launcher, but large enough that sticking contact is a stiff damper, not a discontinuity the integrator can't step.
FRICTION_SLIP_SPEED = 0.01

# Poisson's ratio of the wheel rubber: rubber is nearly incompressible, nu = 0.5 (Gent, "Engineering with Rubber",
# 3rd ed. 2012, ch. 1).
RUBBER_POISSON = 0.5


def _elastic(x: float, ball: Ball) -> float:
    """The ball's power-law spring force at squeeze x (x >= 0)."""
    return ball.stiffness_n * (x / (0.25 * ball.diameter)) ** ball.stiffness_exponent


@lru_cache(maxsize=256)
def _damping(restitution: float) -> float:
    """The Hunt-Crossley damping constant alpha (s/m) that gives a bounce of `restitution` at RESTITUTION_SPEED.

    The law is F = K delta^n (1 + alpha delta'), with delta the two-contact squeeze across the diameter (as
    between two plates, or the wheel and the hood). A drop on a hard floor loads only one contact: with the
    ball deforming alike on both sides, indenting the floor by x squeezes it as a two-plate squeeze of
    delta = 2x does, so F = K (2x)^n (1 + 2 alpha x'). A drop at v0 is therefore a squeeze at delta' = 2 v0.

    For a mass m hitting a rigid plate, m v dv/dx = -K' x^n (1 + alpha' v) (alpha' = 2 alpha) separates in v and
    x, so the work integral of K' x^n over the squeeze cancels between the approach (v0 to 0) and the rebound
    (0 to -e v0):

        u - ln(1 + u) = -e u - ln(1 - e u),   u = alpha' v0 = 2 alpha v0.

    This is exact for any K, n and m (Carvalho & Martins, "Exact restitution and generalizations for the
    Hunt-Crossley contact model", Mech. Mach. Theory 139, 2019), and is solved here by bisection, so the bounce
    is reproduced over the whole range 0 < e <= 1. The closed-form approximations are not: at e = 0.3 the
    original Hunt & Crossley (1975) alpha' = 3(1 - e)/(2 v0) and Lankarani & Nikravesh (1990)
    alpha' = 3(1 - e^2)/(4 v0) bounce 0.28 and 0.38 too high, and Flores et al. (2011) alpha' = 8(1 - e)/(5 e v0)
    0.04 too low.

    Accuracy limits: the bounce is exact only at RESTITUTION_SPEED. At other impact speeds the Hunt-Crossley form
    makes e fall with speed (about 1 - e ~ alpha' v for small alpha' v), which real foam does more weakly; the
    bounce also assumes the plate is rigid and the ball's mass is the only one moving.
    """
    if not 0.0 < restitution <= 1.0:
        raise LauncherConfigError(f"[ball] restitution must be above 0 and at most 1, not {restitution!r}")
    e = restitution
    if e == 1.0:
        return 0.0

    def gap(u: float) -> float:  # > 0 below the root, < 0 above it
        return u - math.log1p(u) + e * u + math.log1p(-e * u)

    lo, hi = 0.0, 1.0 / e  # the rebound term needs e u < 1, and gap -> -inf as e u -> 1
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if mid in (lo, hi):
            break
        if gap(mid) > 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi) / (2.0 * RESTITUTION_SPEED)  # the drop squeezes delta at twice its speed


def _ball_squeeze(delta: float, ball: Ball, k: float) -> float:
    """Split a total squeeze `delta` between the ball (power law) and the wheel (spring k) in series.

    Solves g(x) = x + F(x)/k - delta = 0 for the ball's share x in [0, delta]. g is increasing, and convex for
    n >= 1, so Newton from x = delta (where g >= 0) falls monotonically to the root; a bisection guard keeps it in
    the bracket for any exponent. A handful of steps reach full precision.
    """
    lo, hi = 0.0, delta
    x = delta
    for _ in range(60):
        f = _elastic(x, ball)
        g = x + f / k - delta
        if g > 0.0:
            hi = x
        else:
            lo = x
        slope = 1.0 + (ball.stiffness_exponent * f / x / k if x > 0.0 else 0.0)
        step = x - g / slope
        x_new = step if lo < step < hi else 0.5 * (lo + hi)
        if abs(x_new - x) <= 1e-14 * delta:
            return x_new
        x = x_new
    return x


def normal_force(delta: float, delta_rate: float, ball: Ball, wheel_stiffness: float | None) -> float:
    """The normal force (N, never negative) squeezing the ball at total squeeze `delta`, rising at `delta_rate`.

    - Spring: a power law, F = stiffness_n * (d_ball / (0.25 D))^exponent, d_ball being the ball's own squeeze.
    - Wheel: with `wheel_stiffness` (N/m, from `wheel_stiffness`), the wheel's rubber is a linear spring in series
      with the ball, so the two share the squeeze: delta = d_ball + F / k. Without it, the wheel is rigid.
    - Damping: Hunt-Crossley, F = F_spring (1 + alpha d_ball'), alpha from `restitution` (see `_damping`). The
      wheel spring is taken as undamped, and the split is the static one, so d_ball' = delta_rate * d d_ball /
      d delta = delta_rate / (1 + F_spring' / k).
    - A separating contact can lower the force to zero, never below it (the ball can't pull on the wheel).
    """
    if delta <= 0.0:
        return 0.0
    if wheel_stiffness is None:
        x, rate = delta, delta_rate
    elif wheel_stiffness <= 0.0:
        return 0.0
    else:
        x = _ball_squeeze(delta, ball, wheel_stiffness)
        if x <= 0.0:
            return 0.0
        spring_slope = ball.stiffness_exponent * _elastic(x, ball) / x
        rate = delta_rate / (1.0 + spring_slope / wheel_stiffness)
    force = _elastic(x, ball) * (1.0 + _damping(ball.restitution) * rate)
    return max(force, 0.0)


def contact_force(x: float, x_rate: float, ball: Ball, wheel_stiffness: float | None = None) -> float:
    """The normal force (N, never negative) at ONE contact, the ball and the surface approaching by `x` at `x_rate`.

    A two-plate squeeze delta is two such contacts in series, each taking delta / 2 at the same force, so one
    contact's law is N = K (2x)^n (1 + alpha (2x)'), the law `_damping` is calibrated on (a drop squeezes one
    contact). With `wheel_stiffness` k (the wheel rubber under this contact, `wheel_stiffness`) the approach is
    the ball's indentation plus the rubber's N / k; in two-plate terms that is `normal_force` with 2x against k / 2.
    """
    return normal_force(2.0 * x, 2.0 * x_rate, ball, None if wheel_stiffness is None else 0.5 * wheel_stiffness)


def contact_energy(x: float, ball: Ball) -> float:
    """The elastic energy (J) stored in one rigid-surface contact indented by x: the integral of K (2x)^n dx."""
    if x <= 0.0:
        return 0.0
    q = 0.25 * ball.diameter
    n = ball.stiffness_exponent
    return 0.5 * ball.stiffness_n * q / (n + 1.0) * (2.0 * x / q) ** (n + 1.0)


@dataclass(frozen=True)
class Split:
    """How a squeeze is shared between the wheel and hood contacts (elastic, no damping).

    x_w + x_h = delta: the approach at each contact (x_w includes the wheel rubber's share, `ball_w` is the ball's
    own indentation there). n_w, n_h: the normal forces (n_w scaled by the wheel coverage). k_w, k_h: their slopes
    dN / dx. `lifted` is "wheel" when the ball has lifted off the wheel (x_w = 0), "hood" off the hood, else ""."""

    x_w: float
    x_h: float
    ball_w: float
    n_w: float
    n_h: float
    k_w: float
    k_h: float
    lifted: str = ""
    ball_share: float = 1.0  # d ball_w / d x_w: the ball's part of a change in the wheel side's approach

    def energy(self, ball: Ball, wheel_stiffness: float | None, coverage: float) -> float:
        """The elastic energy (J) stored in both contacts."""
        rubber = 0.0
        if wheel_stiffness is not None and wheel_stiffness > 0.0 and coverage > 0.0:
            per = self.n_w / coverage
            rubber = coverage * per * per / (2.0 * wheel_stiffness)
        return contact_energy(self.x_h, ball) + coverage * contact_energy(self.ball_w, ball) + rubber

    def damped(self, rate_w: float, rate_h: float, ball: Ball) -> tuple[float, float]:
        """The wheel and hood normal forces with each contact's own Hunt-Crossley damping, the contacts' approaches
        changing at rate_w and rate_h (m/s). Per contact N = N_el (1 + alpha (2 x_ball)'), as in `contact_force`;
        on the wheel side only the ball's share of the approach is damped (the rubber is taken as undamped, as in
        `normal_force`): x_ball' = `ball_share` x_w'. Never negative."""
        alpha = _damping(ball.restitution)
        n_w = max(self.n_w * (1.0 + alpha * 2.0 * rate_w * self.ball_share), 0.0)
        n_h = max(self.n_h * (1.0 + alpha * 2.0 * rate_h), 0.0)
        return n_w, n_h


def split_squeeze(
    delta: float, radial: float, ball: Ball, wheel_stiffness: float | None, coverage: float,
    guess: float | None = None,
) -> Split:
    """Share a total squeeze `delta` between the wheel and hood contacts when the hood must push `radial` newtons
    more than the wheel (the ball's centripetal load less gravity's outward part, model.py).

    The ball is two contacts in series with a fixed total approach, x_w + x_h = delta. The hood (rigid, the full
    patch) follows `contact_force`; the wheel side is the same law on the fraction `coverage` of the patch over
    wheel material, with the wheel rubber in series. Solves N_h(x_h) - N_w(delta - x_h) = radial for x_h.

    - radial = 0 and full coverage give the symmetric two-plate squeeze (`normal_force` at delta).
    - If the whole squeeze on the hood, N_h(delta), can't carry `radial`, the ball has lifted off the wheel:
      x_w = 0, N_w = 0, and the hood holds what it can at x_h = delta (`lifted` = "wheel"). The model keeps the
      ball's center on its path circle; the unbalanced rest would move it outward, deeper into a concentric hood.
    - If `radial` is negative and more than the wheel can carry at the whole squeeze, it lifts off the hood.

    Solved for the hood's share: the wheel side's approach for a given force is closed-form (the power law
    inverted, plus the rubber's F / k), so g(x_h) = x_h + x_w(N_h(x_h) - radial) - delta is increasing and a
    bracketed Newton finds its root; `guess` (the last step's x_h) starts it.
    """
    if delta <= 0.0:
        return Split(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    k_n, n, q = ball.stiffness_n, ball.stiffness_exponent, 0.25 * ball.diameter
    f = coverage
    soft = 1.0 / wheel_stiffness if wheel_stiffness else 0.0  # the rubber's compliance (m/N); 0 if rigid

    def hood(x: float) -> float:
        return k_n * (2.0 * x / q) ** n if x > 0.0 else 0.0

    def wheel_side(force: float) -> tuple[float, float]:
        """The wheel side's approach and the ball's share of it, carrying `force` (coverage included)."""
        if force <= 0.0 or f <= 0.0:
            return 0.0, 0.0
        per = force / f
        b = 0.5 * q * (per / k_n) ** (1.0 / n)
        return b + per * soft, b

    def share(force: float, b: float) -> float:
        """d b / d x_w: the ball's compliance b / (n F) over its sum with the rubber's."""
        if force <= 0.0 or f <= 0.0 or b <= 0.0:
            return 1.0
        c = b / (n * force / f)
        return c / (c + soft)

    def wheel_slope(force: float, b: float) -> float:
        """dN_w / dx_w: the ball's per-contact slope n F / b in series with the rubber, times coverage."""
        if force <= 0.0 or f <= 0.0 or b <= 0.0:
            return 0.0
        per = force / f
        return f / (b / (n * per) + soft)

    n_full = hood(delta)
    if n_full <= radial:
        return Split(0.0, delta, 0.0, 0.0, n_full, 0.0, n * n_full / delta, "wheel")
    if radial < 0.0 and f > 0.0:
        x_w, _ = wheel_side(-radial)
        if x_w >= delta:  # off the hood: the whole squeeze on the wheel side
            per = contact_force(delta, 0.0, ball, wheel_stiffness)
            force = f * per
            b = 0.5 * q * (per / k_n) ** (1.0 / n) if per > 0.0 else 0.0
            return Split(delta, 0.0, b, force, 0.0, wheel_slope(force, b), 0.0, "hood", share(force, b))

    # Newton on g(x) = x + x_w(N_h(x) - radial) - delta, bracketed. hood, wheel_side and wheel_slope are written
    # out inline (the same arithmetic, in the same order): this loop runs for every ball in the launcher on every
    # substep, and the calls were most of its cost.
    half_q, inv_n, on_wheel, tol = 0.5 * q, 1.0 / n, f > 0.0, 1e-13 * delta
    lo, hi = 0.0, delta
    x = 0.5 * delta if guess is None or not 0.0 < guess < delta else guess
    for _ in range(100):
        nh = k_n * (2.0 * x / q) ** n if x > 0.0 else 0.0
        nw = nh - radial
        if nw <= 0.0 or not on_wheel:
            x_w = b = 0.0
        else:
            per = nw / f
            b = half_q * (per / k_n) ** inv_n
            x_w = b + per * soft
        val = x + x_w - delta
        if nw < 0.0:
            nw = 0.0
        if val > 0.0:
            hi = x
        else:
            lo = x
        k_h = n * nh / x if x > 0.0 else 0.0
        # dN_w / dx_w (wheel_slope); g'(x) = 1 + dx_w/dN_w * dN_h/dx_h, dx_w/dN_w = 1 / k_w (infinite at N_w = 0,
        # so bisect there)
        k_w = 0.0 if nw <= 0.0 or not on_wheel or b <= 0.0 else f / (b / (n * per) + soft)
        step = x - val / (1.0 + k_h / k_w) if k_w > 0.0 else 0.5 * (lo + hi)
        if k_w > 0.0 and abs(step - x) <= tol:
            # Converged. Tested before the bracket: a step this small can land on the bracket's end (x itself, just
            # moved there), and bisecting from there on took up to 45 passes to shrink the bracket to `tol`.
            x = step
            break
        x_new = step if lo < step < hi else 0.5 * (lo + hi)
        if abs(x_new - x) <= tol or hi - lo <= tol:
            x = x_new
            break
        x = x_new
    nh = hood(x)
    nw = max(nh - radial, 0.0)
    _, b = wheel_side(nh - radial)
    x_w = delta - x
    return Split(x_w, x, b, nw, nh, wheel_slope(nw, b), n * nh / x if x > 0.0 else 0.0, "", share(nw, b))


def shore_a_modulus(durometer: float) -> float:
    """Young's modulus (Pa) of rubber of the given Shore A hardness.

    Gent's relation (A. N. Gent, "On the relation between indentation hardness and Young's modulus",
    Trans. Inst. Rubber Ind. 34, 1958, 46-57): E [MPa] = 0.0981 (56 + 7.62336 S) / (0.137505 (254 - 2.54 S)).
    About 1.1 MPa at 30A and 3.6 MPa at 60A; it is for 0 < S < 100 and is least accurate near either end.
    """
    if not 0.0 < durometer < 100.0:
        raise LauncherConfigError(f"[wheels] durometer must be between 0 and 100 (Shore A), not {durometer!r}")
    return 1e6 * 0.0981 * (56.0 + 7.62336 * durometer) / (0.137505 * (254.0 - 2.54 * durometer))


def wheel_stiffness(wheels: Wheels, contact_width: float) -> float:
    """The wheel rubber's normal stiffness (N/m) under a contact patch `contact_width` across.

    The patch is treated as a rigid flat circular punch of diameter w on an elastic half-space (Boussinesq;
    Johnson, "Contact Mechanics", 1985, ch. 3): k = 2 a E / (1 - nu^2) = E w / (1 - nu^2), with E from the
    durometer (Gent 1958, `shore_a_modulus`) and nu = RUBBER_POISSON. This ignores the hub under a thin tread
    (which would stiffen it) and the wheel's curvature. Rubber is far stiffer than a foam ball, so the wheel
    takes a small share of the squeeze unless the ball is hard (an inflated ball) or the rubber very soft.
    """
    if contact_width <= 0.0:
        return 0.0
    return shore_a_modulus(wheels.durometer) * contact_width / (1.0 - RUBBER_POISSON**2)


def friction_force(slip: float, normal: float, mu: float) -> float:
    """Coulomb friction (N), mu * normal * tanh(slip / FRICTION_SLIP_SPEED), signed like `slip` (m/s).

    `slip` is the speed of the other surface relative to the ball's surface at the contact, so the force on the
    ball is along it and drives the slip toward zero. No normal force, no friction.
    """
    if normal <= 0.0:
        return 0.0
    return mu * normal * math.tanh(slip / FRICTION_SLIP_SPEED)


def wheel_contact_fraction(lane_y: float, ball_d: float, delta: float, wheels: Wheels) -> float:
    """The fraction (0 to 1) of the ball's contact patch that lies over wheel material.

    The patch is about sqrt(D delta) across: with the squeeze shared between the wheel and hood contacts, each is
    a Hertz contact of depth delta/2 and radius a = sqrt((D/2)(delta/2)), so 2a = sqrt(D delta). It is centered at
    `lane_y` along the shaft. Wheels are the bands `positions` +- width/2 (a roller is one band of `width`
    centered at 0); bands that overlap are counted once. With no squeeze, it is 1 if the point under the ball's
    center is on a wheel, else 0.
    """
    if wheels.roller:
        bands = [(-0.5 * wheels.width, 0.5 * wheels.width)]
    else:
        bands = sorted((p - 0.5 * wheels.width, p + 0.5 * wheels.width) for p in wheels.positions)
    patch = math.sqrt(ball_d * delta) if delta > 0.0 else 0.0
    if patch == 0.0:
        return 1.0 if any(lo <= lane_y <= hi for lo, hi in bands) else 0.0
    left, right = lane_y - 0.5 * patch, lane_y + 0.5 * patch
    if any(lo <= left and right <= hi for lo, hi in bands):
        return 1.0  # wholly on one band (exact, free of rounding)
    covered = 0.0
    reach = left  # the right end of what has been counted
    for lo, hi in bands:
        lo, hi = max(lo, reach), min(hi, right)
        if hi > lo:
            covered += hi - lo
            reach = hi
    return min(covered / patch, 1.0)


def rolling_loss_torque(normal: float, ball: Ball, delta: float) -> float:
    """The torque (N*m, >= 0) resisting the ball rolling at one contact, from hysteresis in the ball.

    Rolling, each part of the ball is squeezed as it enters the contact patch and released as it leaves; it
    gives back less than it took. Tabor ("The mechanism of rolling friction. II. The elastic range", Proc. R. Soc.
    A 229, 1955; Johnson, "Contact Mechanics", 1985, ch. 8) integrates the strain energy fed into a Hertz contact
    per distance rolled and loses a fraction alpha of it, so the resisting force is F = (3/16) alpha (a / R) N,
    a torque M = F R = (3/16) alpha a N, with a the contact radius.

    alpha is the loss of one load-unload cycle, the same loss that sets the bounce: an impact keeps e^2 of its
    energy, so alpha = 1 - e^2. And a = sqrt(D delta) / 2, the patch of `wheel_contact_fraction`.

    Limits: Tabor's alpha is the loss of a simple squeeze, while rolling also shears the material, and he found
    rolling losses of the same order as, though not equal to, the rebound loss; the loss here does not depend on
    the rolling speed, while the normal damping (Hunt-Crossley) does. Both use the same 1 - e^2 loss fraction; only
    the normal damping depends on speed.
    """
    if normal <= 0.0 or delta <= 0.0:
        return 0.0
    alpha = 1.0 - ball.restitution**2
    a = 0.5 * math.sqrt(ball.diameter * delta)
    return 3.0 / 16.0 * alpha * a * normal
