"""The disc in the launcher: lying flat in the shooter's channel, squeezed between spinning wheels and a fence.

A planar model. Seen from above, x runs downrange along the channel from its entry and y across it, to the left
looking downrange (z up). The disc is a rigid circle of radius r with three degrees of freedom: its center (x, y)
and its spin Omega (rad/s, positive counter-clockwise from above). Each wheel group is one shaft (`ShaftModel`)
with speed w_g (rad/s, positive when its wheels drive the disc downrange). Its wheels are circles of radius R_w at
fixed centers; a right-hand wheel turns clockwise from above (w_z = -w_g), a left-hand one counter-clockwise.

The layouts (`Layout.kind`), with c the compression:
- "linear": wheels on one side, a straight fence along the other at y = +-r over 0 <= x <= L. A wheel's
  center is R_w + r - c from the center line, so the disc against the fence is squeezed by c beside each wheel.
- "arc": one wheel and a fence concentric with it, radius R_f = R_w + D - c, wrapping `arc_deg` (at most 180) round
  it and ending beside the wheel (x = the wheel's x, y = +-r), where the disc leaves along +x. Its entry end opens
  by c over a lead-in (the radius falls linearly from R_f + c to R_f over an arc as long as `wheel_reach`), so the
  disc enters unsqueezed, as it does in a linear channel.
- "pinch": wheels on both sides and no fence; each wheel's center is R_w + r - c/2 from the center line.
Guide walls, the channel's side plates, stand at y = +-r wherever there is no fence (the wheel side of a linear
channel, both sides of a pinch, and the arc's straight lead-out from its exit end to x = L). The disc fits between
them unsqueezed; they catch its recoil as a wheel lets go and so set its exit direction (docs/disc.md, Sources, the
exit guide). They are rigid, as slippery as the rails (`rail_friction`) and critically damped (`WALL_DAMPING_RATIO`), and run on
to x = L + r, where the disc's trailing rim passes L and it leaves: an exit guide that holds the disc until it is
clear, so the recoil dies in the guide and the disc leaves along the channel.
The disc enters at x = 0, y = 0 (an arc: at the fence's entry end, touching the wheel) moving downrange at the feed's
`entry_speed`, and leaves when its trailing rim passes x = L (`channel_length`).

Contacts. Each wheel, the fence and each wall is a contact with overlap delta(x, y) >= 0 (the depth the rim would
cut into it) and normal n, from the contact into the disc:
    N = max(0, k delta + c_d delta'),   c_d = 2 zeta sqrt(k m),   force on the disc N n,   delta' = -v . n
The rim's stiffness k_rim (`Disc.rim_stiffness`) is diametral, measured by squeezing the disc between two plates:
two contacts in series, so each side of the disc has 2 k_rim. A wheel contact has the tread (k_wheel, from
`launcher.contact.wheel_stiffness` over a patch min(tread width, disc height) wide, or `Layout.wheel_stiffness`) in
series with its side: k = 1 / (1/k_wheel + 1/(2 k_rim)). The fence and walls are rigid: k = 2 k_rim. So squeezed
between a wheel and the fence, the disc carries N = k_series c with k_series = 1 / (1/k_wheel + 1/k_rim), as two
springs in series (docs/disc.md, Sources). zeta is `Disc.rim_damping_ratio`. The arc's centripetal load comes out of the
geometry: the fence holds the disc on its curve, so as it speeds up it presses the fence harder and the wheel less
(N_fence - N_wheel = m v^2 / rho), and the wheel's grip falls with speed. On the lead-in, where the fence's radius
R(psi) changes, delta = rho + r - R(psi) and the force is -N grad delta (a conservative wedge, |grad delta| ~ 1).

Friction (`launcher.contact.friction_force`), with t = z x n the contact's tangent:
    s = u_surface . t - (v . t - r Omega)     (slip: the other surface's speed past the disc's rim point)
    F = mu N tanh(s / v_s),   force on the disc F t,   torque on the disc -r F,   torque on the wheel's shaft load
    F sigma R_w (sigma = -1 right, +1 left; u_surface . t = sigma R_w w_g, 0 for the fence)
mu is the wheels' friction, the fence's, or the walls'. A right-hand wheel drives the disc's right rim forward, so
it spins counter-clockwise (positive); rolling on a fixed fence, v = r Omega and the advance ratio r Omega / V is 1.

Guide rails (the plates above and below) carry the disc's weight and resist its sliding: F_rail = -mu_rail m g
tanh(|v| / v_s) v / |v|. Their drag on the spin is neglected (at most about mu_rail m g r, 0.06 N m, against the
wheels' r F of a few N m).

The equations, M u' = forces, u = (vx, vy, Omega, w_0 ... w_G-1), M = diag(m, m, I_zz, J_g):
    m v'    = sum N n + sum F t + F_rail
    I_zz W' = -r sum F
    J_g w_g' = a_g - b_g w_g - sum F sigma R_w (over group g's wheels; a - b w is `ShaftModel.torque_coefficients`)
Each substep is semi-implicit (linearly implicit in the velocities, the positions following them): friction, the
rail and the motor's back-EMF use their chord slope F(s) / s against the new velocities, as `launcher.model` does
(the friction is a stiff damper near zero slip); the normal forces are explicit at the substep's start. The shafts
then take their own exact step with the friction's load, as in `launcher.model`.

Energy, at the disc's contacts: the wheels' work on the disc F sigma R_w w is `from_shaft`; friction dissipates
F s (`slip_loss`); the rails F_rail . v (`rail_loss`); the normal forces' net work over the pass, once the springs
have unloaded, is the damping (`damping_loss`, with the springs' integration error). With velocities at each
substep's midpoint these close exactly:
    from_shaft + entry_kinetic = exit_linear + exit_spin + slip_loss + damping_loss + rail_loss.
"""

import math
from dataclasses import dataclass

import numpy as np

from ..launcher.contact import FRICTION_SLIP_SPEED, friction_force, wheel_stiffness
from ..launcher.shaft import ShaftModel
from ..motors import Battery
from .inputs import DiscSetup, wheel_reach

# The substep (s), unless a setup's `dt` sets one: the shorter of DEFAULT_DT and 1 / (STEPS_PER_RADIAN omega_n),
# omega_n = sqrt(2 k / m), k the stiffer of a wheel's and the fence's contacts (the disc squeezed between two). The
# contacts give 1e-4 for the presets; tests/test_disc_launcher.py checks that halving it changes the exit speed and
# spin by under 1% (0.07% measured).
DEFAULT_DT = 1e-4
STEPS_PER_RADIAN = 20.0

# The guide walls' damping ratio on the disc's mass: critical, so the walls do not ring. Applies to the walls only
# (the fence and the wheels keep Disc.rim_damping_ratio). A modeling choice: the exit guide as a real rail.
WALL_DAMPING_RATIO = 1.0

# A disc that has not advanced JAM_PROGRESS along the channel for JAM_TIME is "jammed" (the ball launcher's 0.5 s: a
# shot takes tens of milliseconds). So is one pushed back out of the entry.
JAM_TIME = 0.5
JAM_PROGRESS = 1e-3

# Standard gravity (m/s^2), rounded as in launcher/contact.py.
GRAVITY = 9.81

NAN = math.nan


@dataclass(frozen=True)
class DiscEnergy:
    """Joules, at the disc's contacts:
    from_shaft + entry_kinetic = exit_linear + exit_spin + slip_loss + damping_loss + rail_loss (+ the springs'
    stored energy, for a disc that stops in contact; exit_linear and exit_spin are NaN unless launched)."""

    from_shaft: float
    entry_kinetic: float
    exit_linear: float
    exit_spin: float
    slip_loss: float
    damping_loss: float
    rail_loss: float


@dataclass(frozen=True)
class ExitedDisc:
    """A disc's outcome. `status` is "launched", "jammed", "no_contact" (no compression: nothing touches it) or
    "no_wheel" (it went through without a wheel touching it). The exit fields (t_exit, speed, spin, yaw_deg,
    exit_xy) are NaN unless launched; t_exit is when it stopped otherwise."""

    index: int
    status: str
    t_enter: float
    t_exit: float
    speed: float  # m/s
    spin: float  # rad/s, positive counter-clockwise seen from above with the channel horizontal
    yaw_deg: float  # the velocity's direction, from downrange toward the left
    exit_xy: tuple[float, float]  # the center at the exit (m)
    energy: DiscEnergy
    slip_energy: float  # energy.slip_loss
    # Each group's wheel speed drop over the pass, (w_insert - w_min) / w_insert (NaN for a stopped wheel).
    droop: tuple[float, ...] = ()


@dataclass
class _Contact:
    """One contact on the disc at a moment: overlap, its gradient g (the normal is n = -g / |g|), the friction
    coefficient, stiffness and damping, and the wheel group (None for the fence) with sigma R_w."""

    delta: float
    g: tuple[float, float]
    mu: float
    k: float
    c: float
    group: int | None = None
    arm: float = 0.0  # sigma R_w: the wheel's surface speed along t per unit shaft speed


@dataclass
class _Pass:
    index: int
    t_enter: float
    x: float
    y: float
    vx: float
    vy: float
    w: float
    entry_kinetic: float
    start_speeds: tuple[float, ...]
    min_speeds: list[float]
    from_shaft: float = 0.0
    slip: float = 0.0
    normal_work: float = 0.0
    rail: float = 0.0
    touched_wheel: bool = False
    best: float = 0.0
    t_best: float = 0.0


class DiscLauncher:
    """One disc at a time in the channel, driven by its wheel groups' shafts. Stepped by motor voltage per group."""

    def __init__(self, setup: DiscSetup, external_bus: bool = False):
        """`external_bus`: the shafts own no battery; the world sets their bus voltage (`ShaftModel.set_bus_voltage`)
        and charges their supply current to the shared battery, as `launcher.model.Launcher`'s."""
        self.setup = setup
        lay, disc, wheels = setup.layout, setup.disc, setup.wheels
        self.layout = lay
        # The shafts own no battery. Stand-alone, they share the launcher's one: every step the groups' summed supply
        # current sets one bus voltage for all of them (one step's lag, as `ShaftModel` with its own battery). With
        # `external_bus` the world does the same with its battery.
        self.shafts = [ShaftModel(s, None) for s in setup.shafts]
        self.battery = None
        if not external_bus:
            self.battery = Battery(nominal_voltage=setup.battery.nominal_voltage,
                                   resistance=setup.battery.resistance)
            for shaft in self.shafts:
                shaft.set_bus_voltage(setup.battery.nominal_voltage)
        self.r = disc.radius
        self.m = disc.mass
        self.izz = disc.izz
        self.r_w = wheels.diameter / 2.0
        c = lay.compression
        k_rim_side = 2.0 * disc.rim_stiffness
        k_tread = lay.wheel_stiffness
        if k_tread is None:
            k_tread = wheel_stiffness(wheels, min(wheels.width, disc.height))
        self.k_wheel = 1.0 / (1.0 / k_tread + 1.0 / k_rim_side)
        self.k_fence = k_rim_side
        zeta = disc.rim_damping_ratio
        self.c_wheel = 2.0 * zeta * math.sqrt(self.k_wheel * self.m)
        self.c_fence = 2.0 * zeta * math.sqrt(self.k_fence * self.m)
        # The guide walls are an exit guide, a real rail: critically damped (WALL_DAMPING_RATIO), so the recoil of
        # the squeeze as the last wheel lets go dies in the guide instead of ringing between walls and fence.
        self.c_wall = 2.0 * WALL_DAMPING_RATIO * math.sqrt(self.k_fence * self.m)
        # Wheel centers, and sigma (the wheel's turning sense: -1 clockwise, right; +1 left).
        offset = self.r_w + self.r - (c / 2.0 if lay.kind == "pinch" else c)
        self.wheels = [(w.x, offset if w.side == "left" else -offset, 1.0 if w.side == "left" else -1.0, w.group)
                       for w in lay.wheels]
        side = 1.0 if lay.wheels[0].side == "left" else -1.0  # where the wheels are (linear, arc)
        # Straight surfaces (y, x_from, x_to, mu): the linear fence opposite the wheels, and the guide walls, the
        # channel's side plates at y = +-r where there is no fence (the wheels reach through them by the squeeze).
        L, r, mu_g = lay.channel_length, self.r, lay.rail_friction
        if lay.kind == "linear":
            self.straight = [(-side * r, 0.0, L, lay.fence_friction, self.c_fence),
                             (side * r, 0.0, L + r, mu_g, self.c_wall)]
        elif lay.kind == "pinch":
            self.straight = [(r, 0.0, L + r, mu_g, self.c_wall), (-r, 0.0, L + r, mu_g, self.c_wall)]
        else:  # the arc's lead-out, from its exit end to the channel's
            self.straight = [(r, self.wheels[0][0], L + r, mu_g, self.c_wall),
                             (-r, self.wheels[0][0], L + r, mu_g, self.c_wall)]
        if lay.kind == "arc":
            xw, yw, sigma, _ = self.wheels[0]
            self.arc_center = (xw, yw)
            self.sigma = sigma  # +1: the disc goes counter-clockwise round the wheel (a left-hand wheel)
            self.wrap = math.radians(lay.arc_deg)
            self.phi_exit = -sigma * math.pi / 2.0  # the disc's angle about the wheel center where the fence ends
            self.rho_entry = self.r_w + self.r  # the disc touching the wheel, unsqueezed, at the entry
            self.lead = max(wheel_reach(lay, disc, wheels) / self.rho_entry, 1e-9)  # the lead-in (rad)
        omega_n = math.sqrt(2.0 * max(self.k_wheel, self.k_fence if lay.kind != "pinch" else 0.0) / self.m)
        self._dt = min(DEFAULT_DT, 1.0 / (STEPS_PER_RADIAN * omega_n))
        self.time = 0.0
        self._count = 0
        self._active: _Pass | None = None
        self._done: list[ExitedDisc] = []

    def default_dt(self) -> float:
        return self.setup.dt if self.setup.dt is not None else self._dt

    def in_contact(self) -> int:
        """How many discs are in the channel (0 or 1)."""
        return 0 if self._active is None else 1

    # --- geometry ----------------------------------------------------------------------------------------------------

    def _arc_psi(self, x: float, y: float) -> tuple[float, float, float, float]:
        """For the arc: the disc center's distance rho from the wheel center, its unit direction e, and psi, how far
        round the fence (rad) from the entry end (wrap at the exit end; outside [0, wrap] off the fence)."""
        cx, cy = self.arc_center
        dx, dy = x - cx, y - cy
        rho = math.hypot(dx, dy)
        d = self.sigma * (math.atan2(dy, dx) - self.phi_exit)
        low = self.wrap + math.pi / 4.0
        d = (d + low) % (2.0 * math.pi) - low
        return rho, dx / rho, dy / rho, self.wrap + d

    def _fence_radius(self, psi: float) -> tuple[float, float]:
        """The arc fence's radius at psi and its slope d R / d psi: R_f, opened by c over the entry lead-in."""
        c = self.layout.compression
        if psi < self.lead:
            return self.layout.fence_radius + c * (1.0 - psi / self.lead), -c / self.lead
        return self.layout.fence_radius, 0.0

    def _arc_point(self, psi: float) -> tuple[float, float]:
        cx, cy = self.arc_center
        phi = self.phi_exit - self.sigma * (self.wrap - psi)
        radius, _ = self._fence_radius(psi)
        return cx + radius * math.cos(phi), cy + radius * math.sin(phi)

    def _point_contact(self, x, y, px, py, radius) -> tuple[float, tuple[float, float]] | None:
        """Overlap and gradient of the disc against a circle of `radius` (0 for a point) centered at (px, py)."""
        dx, dy = x - px, y - py
        d = math.hypot(dx, dy)
        delta = radius + self.r - d
        if delta <= 0.0 or d == 0.0:
            return None
        return delta, (-dx / d, -dy / d)

    def _arc_fence(self, x: float, y: float) -> tuple[float, tuple[float, float]] | None:
        rho, ex, ey, psi = self._arc_psi(x, y)
        if psi < 0.0 or psi > self.wrap:
            px, py = self._arc_point(0.0 if psi < 0.0 else self.wrap)
            return self._point_contact(x, y, px, py, 0.0)
        radius, slope = self._fence_radius(psi)
        delta = rho + self.r - radius
        if delta <= 0.0:
            return None
        # delta = rho + r - R(psi): grad delta = e - R'(psi) grad psi, grad psi = sigma e_phi / rho, e_phi = z x e.
        q = slope * self.sigma / rho
        return delta, (ex + q * ey, ey - q * ex)

    def _contacts(self, x: float, y: float) -> list[_Contact]:
        out = []
        mu_w = self.setup.wheels.friction
        for wx, wy, sigma, group in self.wheels:
            hit = self._point_contact(x, y, wx, wy, self.r_w)
            if hit is not None:
                out.append(_Contact(hit[0], hit[1], mu_w, self.k_wheel, self.c_wheel, group, sigma * self.r_w))
        for wy, x0, x1, mu, damping in self.straight:
            hit = self._point_contact(x, y, min(max(x, x0), x1), wy, 0.0)
            if hit is not None:
                out.append(_Contact(hit[0], hit[1], mu, self.k_fence, damping))
        if self.layout.kind == "arc":
            hit = self._arc_fence(x, y)
            if hit is not None:
                out.append(_Contact(hit[0], hit[1], self.layout.fence_friction, self.k_fence, self.c_fence))
        return out

    def _stored(self, x: float, y: float) -> float:
        return sum(0.5 * c.k * c.delta**2 for c in self._contacts(x, y))

    def _progress(self, p: _Pass) -> float:
        """How far along the channel (m) the disc's center has come."""
        if self.layout.kind != "arc":
            return p.x
        _, _, _, psi = self._arc_psi(p.x, p.y)
        if psi <= self.wrap:
            return psi * self.rho_entry
        return self.wrap * self.rho_entry + (p.x - self.arc_center[0])

    # --- feeding and stepping ----------------------------------------------------------------------------------------

    def positions(self) -> list[tuple[int, float]]:
        """The disc in the channel as (index, how far along the channel its center has come, m); empty when the
        channel is clear."""
        p = self._active
        return [] if p is None else [(p.index, self._progress(p))]

    def insert(self, t: float, entry_speed: float | None = None) -> bool:
        """Feed a disc into the channel's entry now, moving downrange at `entry_speed` (m/s; the setup's
        [feed] entry_speed when None, as the standalone run feeds; the world passes its stage's surface speed). `t`
        must be the launcher's current time (`self.time`, the clock t_exit is read from; ValueError otherwise).
        False if a disc is already in the channel (nothing is fed). A disc that nothing can touch (no compression)
        finishes "no_contact" at once; it is returned by the next `step`. Discs are numbered (`ExitedDisc.index`)
        in the order they are fed, from 0."""
        if abs(t - self.time) > 1e-9 * max(1.0, abs(self.time)):
            raise ValueError(f"insert at t = {t}, but the launcher's clock is at {self.time}: feed at its time")
        t = self.time
        if self._active is not None:
            return False
        index = self._count
        self._count += 1
        lay = self.layout
        v0 = self.setup.feed.entry_speed if entry_speed is None else entry_speed
        entry_kinetic = 0.5 * self.m * v0 * v0
        speeds = tuple(s.speed for s in self.shafts)
        if lay.compression <= 0.0:
            energy = DiscEnergy(0.0, entry_kinetic, NAN, NAN, 0.0, 0.0, 0.0)
            self._done.append(ExitedDisc(index, "no_contact", t, t, NAN, NAN, NAN, (NAN, NAN), energy, 0.0,
                                         tuple(0.0 for _ in speeds)))
            return True
        if lay.kind == "arc":
            cx, cy = self.arc_center
            phi = self.phi_exit - self.sigma * self.wrap
            x, y = cx + self.rho_entry * math.cos(phi), cy + self.rho_entry * math.sin(phi)
            # Along the path: sigma e_phi, e_phi = (-sin phi, cos phi).
            vx, vy = -self.sigma * v0 * math.sin(phi), self.sigma * v0 * math.cos(phi)
        else:
            x, y, vx, vy = 0.0, 0.0, v0, 0.0
        p = _Pass(index, t, x, y, vx, vy, 0.0, entry_kinetic, speeds, list(speeds), t_best=t)
        p.best = self._progress(p)
        self._active = p
        return True

    def _substep(self, h: float, voltages: list[float]) -> None:
        """One semi-implicit step of the disc and the shafts together (see the module notes)."""
        p = self._active
        G = len(self.shafts)
        n = 3 + G
        m, izz, r = self.m, self.izz, self.r
        vs = FRICTION_SLIP_SPEED
        u = np.array([p.vx, p.vy, p.w] + [s.speed for s in self.shafts])
        A = np.diag([m, m, izz] + [s.inertia for s in self.shafts])
        rhs = A @ u
        # The motors: a - b w, b implicit.
        for g, (shaft, volts) in enumerate(zip(self.shafts, voltages)):
            a, b = shaft.torque_coefficients(volts)
            A[3 + g, 3 + g] += h * b
            rhs[3 + g] += h * a
        # The normal forces, explicit: N = k delta + c delta', delta' = g . v; force -N g.
        contacts = self._contacts(p.x, p.y)
        normals = []
        rows = []
        for ct in contacts:
            gx, gy = ct.g
            rate = gx * p.vx + gy * p.vy
            normal = max(0.0, ct.k * ct.delta + ct.c * rate)
            fx, fy = -normal * gx, -normal * gy
            normals.append((fx, fy))
            rhs[0] += h * fx
            rhs[1] += h * fy
            # Friction: load N |g| along n = -g / |g|; t = z x n; s = J . u with J = (-t, r, arm at the group).
            norm = math.hypot(gx, gy)
            tx, ty = gy / norm, -gx / norm
            row = np.zeros(n)
            row[0], row[1], row[2] = -tx, -ty, r
            if ct.group is not None:
                row[3 + ct.group] = ct.arm
                p.touched_wheel = True
            s = float(row @ u)
            load = normal * norm
            chord = friction_force(s, load, ct.mu) / s if abs(s) > 1e-6 * vs else ct.mu * load / vs
            A += h * chord * np.outer(row, row)
            rows.append((row, chord, ct))
        # The rails: mu_rail m g against the velocity, chord-implicit.
        speed = math.hypot(p.vx, p.vy)
        weight = m * GRAVITY
        mu_r = self.layout.rail_friction
        rail = friction_force(speed, weight, mu_r) / speed if speed > 1e-6 * vs else mu_r * weight / vs
        A[0, 0] += h * rail
        A[1, 1] += h * rail
        u_new = np.linalg.solve(A, rhs)
        mid = 0.5 * (u + u_new)

        loads = [0.0] * G
        for (row, chord, ct), (fx, fy) in zip(rows, normals):
            f = chord * float(row @ u_new)  # the friction the step applied
            p.slip += h * f * float(row @ mid)
            if ct.group is not None:
                p.from_shaft += h * f * ct.arm * float(mid[3 + ct.group])
                loads[ct.group] += f * ct.arm
            p.normal_work += h * (fx * float(mid[0]) + fy * float(mid[1]))
        p.rail += h * rail * float(u_new[0] * mid[0] + u_new[1] * mid[1])
        p.vx, p.vy, p.w = float(u_new[0]), float(u_new[1]), float(u_new[2])
        p.x += h * p.vx
        p.y += h * p.vy
        self._step_shafts(h, voltages, loads)
        for g, shaft in enumerate(self.shafts):
            p.min_speeds[g] = min(p.min_speeds[g], shaft.speed)
        self.time += h

        t = self.time
        if p.x - r >= self.layout.channel_length:
            self._finish("launched" if p.touched_wheel else "no_wheel", t)
            return
        progress = self._progress(p)
        if progress > p.best + JAM_PROGRESS:
            p.best, p.t_best = progress, t
        if progress < -r or t - p.t_best >= JAM_TIME:
            self._finish("jammed", t)

    def _finish(self, status: str, t: float) -> None:
        p = self._active
        self._active = None
        launched = status == "launched"
        stored = self._stored(p.x, p.y)
        droop = tuple((w0 - lo) / w0 if w0 > 0.0 else NAN for w0, lo in zip(p.start_speeds, p.min_speeds))
        energy = DiscEnergy(
            from_shaft=p.from_shaft, entry_kinetic=p.entry_kinetic,
            exit_linear=0.5 * self.m * (p.vx**2 + p.vy**2) if launched else NAN,
            exit_spin=0.5 * self.izz * p.w**2 if launched else NAN,
            slip_loss=p.slip, damping_loss=-p.normal_work - stored, rail_loss=float(p.rail),
        )
        if launched:
            speed = math.hypot(p.vx, p.vy)
            out = ExitedDisc(p.index, status, p.t_enter, t, speed, p.w, math.degrees(math.atan2(p.vy, p.vx)),
                             (p.x, p.y), energy, p.slip, droop)
        else:
            out = ExitedDisc(p.index, status, p.t_enter, t, NAN, NAN, NAN, (NAN, NAN), energy, p.slip, droop)
        self._done.append(out)

    def _step_shafts(self, h: float, voltages: list[float], loads: list[float]) -> None:
        """Step every shaft with its load; then, stand-alone, the shared battery sets their next bus voltage from
        their summed supply current."""
        for shaft, volts, load in zip(self.shafts, voltages, loads):
            shaft.step(h, volts, load)
        if self.battery is not None:
            bus = self.battery.voltage(sum(s.supply_current for s in self.shafts))
            for shaft in self.shafts:
                shaft.set_bus_voltage(bus)

    def step(self, dt: float, voltages: list[float]) -> list[ExitedDisc]:
        """Advance dt seconds with `voltages[g]` on wheel group g's motor. Returns the discs that finished.

        While a disc is in the channel the step is split into substeps no longer than `default_dt()`. A step of no
        time (dt <= 0) changes nothing."""
        if len(voltages) != len(self.shafts):
            raise ValueError(f"{len(voltages)} voltage(s) for {len(self.shafts)} wheel group(s); give one per group")
        idle = [0.0] * len(self.shafts)
        if dt > 0.0:
            if self._active is None:
                self._step_shafts(dt, voltages, idle)
                self.time += dt
            else:
                steps = max(1, math.ceil(dt / self.default_dt() - 1e-9))
                h = dt / steps
                for _ in range(steps):
                    if self._active is not None:
                        self._substep(h, voltages)
                    else:
                        self._step_shafts(h, voltages, idle)
                        self.time += h
        done, self._done = self._done, []
        return done
