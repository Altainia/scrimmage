"""A face-pinch shooter for a rigid piece: a top wheel and a bottom wheel squeeze the piece's two faces and drive it
out (the 2024 NOTE's common shooter). A planar model, in the launch plane.

Seen from the side, x runs downrange along the channel from the piece's center at the entry, z across it toward the
top wheel, and y = z x x across the wheels' axles (to the left looking downrange). The piece is a rigid slab: its
thickness T is its passing width (the smallest side of its bounding box, `testrange.spec.passing_width`), its
length L the next side (the size along the path), its width W the largest (across the wheels). It has two degrees
of freedom: its center's position x along the channel (it stays on the center line: the two wheels squeeze it from
both sides, and the channel's guides take what is left) and its pitch spin w (rad/s, positive for backspin: the top
face moving backward against the center, about -y). Its pitch angle is not followed: the squeeze holds the faces
flat between the wheels, and the pass takes tens of milliseconds.

The wheels: the bottom wheel (radius R_b) has its axle at (x_w, -(R_b + g/2)), the top wheel (R_t) at
(x_w, +(R_t + g/2)), g the gap between their surfaces. Each is one shaft (`ShaftModel`, speed w_k, positive when its
surface at the gap moves downrange: the bottom wheel turns about +y, the top about -y). The squeeze across the piece is
c = T - g, shared between the two contacts.

Contacts. Each wheel touches the slab (the rectangle x +- L/2, z +- T/2) at the slab's point Q nearest its axle C:
its face while the axle is over the slab, a corner at the entry and exit. The overlap is delta = R - |C - Q| >= 0,
the normal n = (Q - C) / |Q - C| (from the wheel into the piece):
    N = max(0, k delta + c_d delta'),   delta' = -n_x v,   c_d = 2 zeta sqrt(k m)
The piece's force is N n; its x part moves the piece (the conservative wedge at the entry and exit corners), its z
part the other wheel and the guides hold. The contact's stiffness is the tread (k_tread, `launcher.contact.
wheel_stiffness` over a patch min(wheel width, W) wide) in series with the piece's own `stiffness` (N/m, at one
contact, as the bench's rigid pieces carry it): k = 1 / (1/k_tread + 1/k_piece). Squeezed by c between the wheels,
the piece carries N = k c / 2 at each. zeta is DAMPING_RATIO.

Friction (`launcher.contact.friction_force`), with t = (n_z, -n_x) the contact's tangent and r = Q - (x, 0):
    s = sigma R w - u_piece . t,   u_piece = (v - w r_z, w r_x)   (the piece's point at Q; sigma = +1 bottom, -1 top)
    F = mu N tanh(s / v_s),   on the piece F t,   load on the wheel's shaft F sigma R
At the faces this is the plain slip: bottom s = R_b w_b - (v + w T/2), top (along -x) -(R_t w_t - (v - w T/2)). So
a faster top wheel drags the top face forward, which is topspin (negative w); a faster bottom wheel gives backspin,
and equal surface speeds none. mu is the tread's friction (`[wheels.*]`'s), the same figure the bench uses against a
rigid piece.

The equations, M u' = forces, u = (v, w, w_b, w_t), M = diag(m, I_p, J_b, J_t), I_p the piece's moment of inertia
about the axis across the wheels:
    m v'     = sum N n_x + sum F t_x
    I_p w'   = sum F (t_z r_x - t_x r_z)
    J_k w_k' = a_k - b_k w_k - sum F sigma R
Each substep is semi-implicit, as disc/launcher.py's: the friction and the motor's back-EMF use their chord slope
against the new velocities, the normal forces are explicit at the substep's start, and the shafts then take their own
exact step with the friction's load. Gravity along a tilted channel is not modeled (the disc launcher's choice: at
45 deg it would take about 0.2 m/s off a 30 ms pass).

The piece leaves ("launched") once its trailing edge is past both wheels' reach. Energy, at the piece's contacts,
closes as the disc launcher's:
    from_shaft + entry_kinetic = exit_linear + exit_spin + slip_loss + damping_loss.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .. import i18n, units
from ..motors import Battery
from .contact import FRICTION_SLIP_SPEED, friction_force, wheel_stiffness
from .inputs import BatteryIn, LauncherConfigError, Shaft, Wheels
from .shaft import ShaftModel

# The substep (s), unless a setup's `dt` sets one: the shorter of DEFAULT_DT and 1 / (STEPS_PER_RADIAN omega_n),
# omega_n = sqrt(2 k / m), as the disc launcher's (tests/test_pinch_launcher.py checks that halving it changes the
# exit speed by under 1%).
DEFAULT_DT = 1e-4
STEPS_PER_RADIAN = 20.0
# The contacts' damping ratio on the piece's mass: critical, the bench's TREAD_DAMPING for a rigid piece's contacts
# (parts/assemble.py), so the analytic model and the bench damp alike. An estimate: a foam piece's real damping is
# not measured.
DAMPING_RATIO = 1.0
# A piece that has not advanced JAM_PROGRESS along the channel for JAM_TIME is "jammed" (the disc launcher's rule);
# so is one pushed back a whole length out of the entry.
JAM_TIME = 0.5
JAM_PROGRESS = 1e-3
STATUSES = ("launched", "jammed", "no_contact")

NAN = math.nan


class PinchConfigError(LauncherConfigError):
    """A pinch shooter that can't be run as given; the message names the part's or the piece's number."""


@dataclass(frozen=True)
class PinchPiece:
    """The piece as the pinch sees it: a slab `thickness` across the gap, `length` along the path and `width` across
    the wheels (m), its mass (kg), its moment of inertia about the axis across the wheels (kg m^2) and its own
    contact stiffness (N/m at one contact; None: hard)."""

    thickness: float
    length: float
    width: float
    mass: float
    pitch_inertia: float
    stiffness: float | None = None


@dataclass(frozen=True)
class PinchSetup:
    """The pinch's inputs: the piece, the two wheels (diameters, the gap between them, where their axles stand
    downrange of the piece's center at the entry, their tread's width, durometer and friction), each wheel's shaft
    (bottom first), the feed's entry speed (m/s), the battery for a stand-alone run and an optional substep."""

    piece: PinchPiece
    bottom_diameter: float
    top_diameter: float
    gap: float
    wheel_x: float
    wheel_width: float
    durometer: float
    friction: float
    shafts: tuple[Shaft, Shaft]
    entry_speed: float = 2.0
    battery: BatteryIn = BatteryIn()
    dt: float | None = None

    @property
    def compression(self) -> float:
        """The squeeze across the piece, its thickness less the gap (m)."""
        return self.piece.thickness - self.gap

    def reach(self, diameter: float) -> float:
        """How far along the channel from its axle a wheel of `diameter` reaches the squeezed piece's face (m): the
        half-length of the arc that cuts into the piece by half the compression."""
        r, half = diameter / 2.0, max(self.compression, 0.0) / 2.0
        return math.sqrt(max(r * r - (r - half) ** 2, 0.0))


def check_setup(setup: PinchSetup) -> None:
    """PinchConfigError (in words) for numbers the model can't run: every size and the mass must be positive, and
    the piece must start clear of the wheels."""
    p = setup.piece
    for name, value in (("thickness", p.thickness), ("length", p.length), ("width", p.width), ("mass", p.mass),
                        ("pitch_inertia", p.pitch_inertia), ("bottom_diameter", setup.bottom_diameter),
                        ("top_diameter", setup.top_diameter), ("gap", setup.gap), ("wheel_width", setup.wheel_width)):
        if not (math.isfinite(value) and value > 0.0):
            raise PinchConfigError(i18n.t("pinch shooter: {name} must be positive, not {value}", name=name,
                                          value=value))
    if p.stiffness is not None and not p.stiffness > 0.0:
        raise PinchConfigError(i18n.t("pinch shooter: the piece's stiffness must be positive, not {value}",
                                      value=p.stiffness))
    need = p.length / 2.0 + max(setup.reach(setup.bottom_diameter), setup.reach(setup.top_diameter))
    if setup.wheel_x < need - 1e-12:
        raise PinchConfigError(i18n.t("pinch shooter: {wheel_x_setting} puts the piece between the wheels before it "
                                      "is fed; it must be at least {need} (half the piece's length, {half}, plus how "
                                      "far the wheels reach along it)", wheel_x_setting=f"wheel_x = {setup.wheel_x:g}",
                                      need=units.fmt(need, "length"), half=units.fmt(p.length / 2.0, "length")))


@dataclass(frozen=True)
class PinchEnergy:
    """Joules, at the piece's contacts: from_shaft + entry_kinetic = exit_linear + exit_spin + slip_loss +
    damping_loss (+ the springs' stored energy, for a piece that stops in contact; exit_linear and exit_spin are NaN
    unless launched)."""

    from_shaft: float
    entry_kinetic: float
    exit_linear: float
    exit_spin: float
    slip_loss: float
    damping_loss: float


@dataclass(frozen=True)
class ExitedPiece:
    """A piece's outcome. `status` is "launched", "jammed" or "no_contact" (the gap is no narrower than the piece:
    the wheels can't grip it). The exit fields (speed, spin, exit_x, exit_slip) are NaN unless launched; t_exit is
    when it stopped otherwise."""

    index: int
    status: str
    t_enter: float
    t_exit: float
    speed: float  # m/s along the channel
    spin: float  # rad/s, positive for backspin (about the channel's -y)
    exit_x: float  # the piece's center along the channel at the exit (m)
    energy: PinchEnergy
    # The wheels' slip under the piece at its last moment in their grip, (U - v) / U with U the mean of the two
    # wheels' surface speeds (the pitch spin cancels in the mean): about 0 when the wheels grip it out, large when
    # the pass is grip-limited (more squeeze or friction helps, more wheel speed hardly does).
    exit_slip: float = NAN
    droop: tuple[float, ...] = ()  # each shaft's speed drop over the pass, (w_insert - w_min) / w_insert


def status_words(shot: ExitedPiece, setup: PinchSetup) -> str:
    """What happened to a piece that didn't leave, in words ("" for a launched one)."""
    if shot.status == "no_contact":
        return i18n.t("the wheels can't grip the piece: the gap ({gap}) is no narrower than its thickness "
                      "({thickness}); close the gap so the wheels squeeze it", gap=units.fmt(setup.gap, "length"),
                      thickness=units.fmt(setup.piece.thickness, "length"))
    if shot.status == "jammed":
        return i18n.t("the piece jammed: it stopped moving between the wheels (too tight a gap for the wheels' "
                      "grip, or the wheels too slow to pull it through)")
    return ""


@dataclass
class _Pass:
    index: int
    t_enter: float
    x: float
    v: float
    w: float
    entry_kinetic: float
    start_speeds: tuple[float, ...]
    min_speeds: list[float]
    from_shaft: float = 0.0
    slip: float = 0.0
    normal_work: float = 0.0
    touched_wheel: bool = False
    exit_slip: float = NAN
    best: float = 0.0
    t_best: float = 0.0


class PinchLauncher:
    """One piece at a time between the wheels, each wheel on its own shaft. Stepped by motor voltage per shaft
    (bottom, top)."""

    def __init__(self, setup: PinchSetup, external_bus: bool = False):
        """`external_bus`: the shafts own no battery; the world sets their bus voltage and charges their supply
        current to its battery (as the disc launcher's)."""
        check_setup(setup)
        self.setup = setup
        p = setup.piece
        self.shafts = [ShaftModel(s, None) for s in setup.shafts]
        self.battery = None
        if not external_bus:
            self.battery = Battery(nominal_voltage=setup.battery.nominal_voltage, resistance=setup.battery.resistance)
            for shaft in self.shafts:
                shaft.set_bus_voltage(setup.battery.nominal_voltage)
        self.m, self.inertia = p.mass, p.pitch_inertia
        self.half_t, self.half_l = p.thickness / 2.0, p.length / 2.0
        tread = Wheels(diameter=setup.bottom_diameter, width=setup.wheel_width, durometer=setup.durometer,
                       friction=setup.friction)
        k_tread = wheel_stiffness(tread, min(setup.wheel_width, p.width))
        self.k = 1.0 / (1.0 / k_tread + (1.0 / p.stiffness if p.stiffness is not None else 0.0))
        self.c = 2.0 * DAMPING_RATIO * math.sqrt(self.k * self.m)
        g2 = setup.gap / 2.0
        rb, rt = setup.bottom_diameter / 2.0, setup.top_diameter / 2.0
        # (axle x, axle z, radius, sigma): the bottom wheel turns about +y, the top about -y
        self.wheels = [(setup.wheel_x, -(rb + g2), rb, 1.0), (setup.wheel_x, rt + g2, rt, -1.0)]
        self.clear = setup.wheel_x + max(setup.reach(setup.bottom_diameter), setup.reach(setup.top_diameter))
        omega_n = math.sqrt(2.0 * self.k / self.m)
        self._dt = min(DEFAULT_DT, 1.0 / (STEPS_PER_RADIAN * omega_n))
        self.time = 0.0
        self._count = 0
        self._active: _Pass | None = None
        self._done: list[ExitedPiece] = []

    def default_dt(self) -> float:
        return self.setup.dt if self.setup.dt is not None else self._dt

    def in_contact(self) -> int:
        """How many pieces are in the channel (0 or 1)."""
        return 0 if self._active is None else 1

    def positions(self) -> list[tuple[int, float]]:
        """The piece in the channel as (index, how far along the channel its center has come, m)."""
        p = self._active
        return [] if p is None else [(p.index, p.x)]

    def _contacts(self, x: float) -> list[tuple[int, float, float, float, float, float]]:
        """Each wheel touching the slab centered at x: (wheel, overlap, n_x, n_z, r_x, r_z), r the contact point from
        the piece's center."""
        out = []
        for k, (cx, cz, radius, _) in enumerate(self.wheels):
            qx = min(max(cx, x - self.half_l), x + self.half_l)
            qz = min(max(cz, -self.half_t), self.half_t)
            dx, dz = qx - cx, qz - cz
            d = math.hypot(dx, dz)
            delta = radius - d
            if delta > 0.0 and d > 0.0:
                out.append((k, delta, dx / d, dz / d, qx - x, qz))
        return out

    def _stored(self, x: float) -> float:
        return sum(0.5 * self.k * c[1] ** 2 for c in self._contacts(x))

    def insert(self, t: float, entry_speed: float | None = None) -> bool:
        """Feed a piece in now, its center at the channel's origin, moving downrange at `entry_speed` (m/s; the
        setup's when None). `t` must be the launcher's clock (ValueError otherwise). False if a piece is already in
        the channel. A piece the wheels can't grip (no compression) finishes "no_contact" at once, returned by the
        next `step`. Pieces are numbered in the order they are fed, from 0."""
        if abs(t - self.time) > 1e-9 * max(1.0, abs(self.time)):
            raise ValueError(f"insert at t = {t}, but the launcher's clock is at {self.time}: feed at its time")
        t = self.time
        if self._active is not None:
            return False
        index = self._count
        self._count += 1
        v0 = self.setup.entry_speed if entry_speed is None else entry_speed
        entry_kinetic = 0.5 * self.m * v0 * v0
        speeds = tuple(s.speed for s in self.shafts)
        if self.setup.compression <= 0.0:
            energy = PinchEnergy(0.0, entry_kinetic, NAN, NAN, 0.0, 0.0)
            self._done.append(ExitedPiece(index, "no_contact", t, t, NAN, NAN, NAN, energy,
                                          droop=tuple(0.0 for _ in speeds)))
            return True
        self._active = _Pass(index, t, 0.0, v0, 0.0, entry_kinetic, speeds, list(speeds), t_best=t)
        return True

    def _substep(self, h: float, voltages: list[float]) -> None:
        """One semi-implicit step of the piece and the shafts together (see the module notes)."""
        p = self._active
        m = self.m
        vs = FRICTION_SLIP_SPEED
        mu = self.setup.friction
        u = np.array([p.v, p.w] + [s.speed for s in self.shafts])
        A = np.diag([m, self.inertia] + [s.inertia for s in self.shafts])
        rhs = A @ u
        for k, (shaft, volts) in enumerate(zip(self.shafts, voltages)):
            a, b = shaft.torque_coefficients(volts)
            A[2 + k, 2 + k] += h * b
            rhs[2 + k] += h * a
        rows = []
        for k, delta, nx, nz, rx, rz in self._contacts(p.x):
            normal = max(0.0, self.k * delta + self.c * (-nx * p.v))
            fx = normal * nx
            rhs[0] += h * fx
            tx, tz = nz, -nx
            arm = self.wheels[k][3] * self.wheels[k][2]
            row = np.zeros(4)
            row[0], row[1], row[2 + k] = -tx, tx * rz - tz * rx, arm
            s = float(row @ u)
            chord = friction_force(s, normal, mu) / s if abs(s) > 1e-6 * vs else mu * normal / vs
            A += h * chord * np.outer(row, row)
            rows.append((k, row, chord, fx))
            p.touched_wheel = True
        u_new = np.linalg.solve(A, rhs)
        mid = 0.5 * (u + u_new)
        loads = [0.0, 0.0]
        for k, row, chord, fx in rows:
            f = chord * float(row @ u_new)  # the friction the step applied
            p.slip += h * f * float(row @ mid)
            p.from_shaft += h * f * row[2 + k] * float(mid[2 + k])
            loads[k] += f * row[2 + k]
            p.normal_work += h * fx * float(mid[0])
        p.v, p.w = float(u_new[0]), float(u_new[1])
        p.x += h * p.v
        self._step_shafts(h, voltages, loads)
        if rows:
            surface = sum(radius * shaft.speed for (_, _, radius, _), shaft in zip(self.wheels, self.shafts)) / 2.0
            p.exit_slip = (surface - p.v) / surface if surface > 0.0 else NAN
        for k, shaft in enumerate(self.shafts):
            p.min_speeds[k] = min(p.min_speeds[k], shaft.speed)
        self.time += h

        t = self.time
        if p.x - self.half_l >= self.clear:
            self._finish("launched" if p.touched_wheel else "no_contact", t)
            return
        if p.x > p.best + JAM_PROGRESS:
            p.best, p.t_best = p.x, t
        if p.x < -2.0 * self.half_l or t - p.t_best >= JAM_TIME:
            self._finish("jammed", t)

    def _finish(self, status: str, t: float) -> None:
        p = self._active
        self._active = None
        launched = status == "launched"
        stored = self._stored(p.x)
        droop = tuple((w0 - lo) / w0 if w0 > 0.0 else NAN for w0, lo in zip(p.start_speeds, p.min_speeds))
        energy = PinchEnergy(
            from_shaft=p.from_shaft, entry_kinetic=p.entry_kinetic,
            exit_linear=0.5 * self.m * p.v ** 2 if launched else NAN,
            exit_spin=0.5 * self.inertia * p.w ** 2 if launched else NAN,
            slip_loss=p.slip, damping_loss=-p.normal_work - stored)
        if launched:
            out = ExitedPiece(p.index, status, p.t_enter, t, p.v, p.w, p.x, energy, p.exit_slip, droop)
        else:
            out = ExitedPiece(p.index, status, p.t_enter, t, NAN, NAN, NAN, energy, NAN, droop)
        self._done.append(out)

    def _step_shafts(self, h: float, voltages: list[float], loads: list[float]) -> None:
        for shaft, volts, load in zip(self.shafts, voltages, loads):
            shaft.step(h, volts, load)
        if self.battery is not None:
            bus = self.battery.voltage(sum(s.supply_current for s in self.shafts))
            for shaft in self.shafts:
                shaft.set_bus_voltage(bus)

    def step(self, dt: float, voltages: list[float]) -> list[ExitedPiece]:
        """Advance dt seconds with `voltages[k]` on shaft k's motor (bottom, top). Returns the pieces that finished.
        While a piece is in the channel the step is split into substeps no longer than `default_dt()`."""
        if len(voltages) != len(self.shafts):
            raise ValueError(f"{len(voltages)} voltage(s) for {len(self.shafts)} shafts; give one per shaft")
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


MAX_PASS_S = 1.0  # s: a piece still in the channel after this long is not coming out


def single_shot(setup: PinchSetup, rps: tuple[float, float], kP: float = 0.5,
                entry_speed: float | None = None) -> ExitedPiece | None:
    """One piece through the pinch with each wheel spinning at `rps` (bottom, top; rotations per second) when it
    enters, a velocity loop (kV feedforward plus kP volts per rps of error, as `launcher.shaft.VelocityController`)
    holding them while it passes: the aim's and the tests' pass. None if it is still in the channel after
    MAX_PASS_S."""
    from .inputs import Control
    from .shaft import VelocityController
    launcher = PinchLauncher(setup)
    ctrls = []
    for shaft, spec, target in zip(launcher.shafts, setup.shafts, rps):
        shaft.speed = 2.0 * math.pi * target
        ctrls.append(VelocityController(Control(target_rps=target, kP=kP), spec))
    launcher.insert(0.0, entry_speed)
    dt = launcher.default_dt()
    exits = launcher.step(0.0, [0.0, 0.0])
    while not exits and launcher.in_contact() and launcher.time < MAX_PASS_S:
        exits = launcher.step(dt, [c.voltage(s) for c, s in zip(ctrls, launcher.shafts)])
    return exits[0] if exits else None


def channel_turn(box: tuple[float, float, float]) -> np.ndarray:
    """The rotation (3x3) taking a piece's body frame to the channel's, for its bounding box sides `box` along body
    x, y and z: its thinnest side across the gap (channel z), its next along the path (x), its longest across the
    wheels (y). A permutation of the axes, turned to be a proper rotation."""
    order = sorted(range(3), key=lambda k: (box[k], k))     # thinnest, middle, longest body axes
    turn = np.zeros((3, 3))
    for channel_axis, body_axis in zip((2, 0, 1), order):
        turn[channel_axis, body_axis] = 1.0
    if np.linalg.det(turn) < 0.0:
        turn[1] = -turn[1]          # flip the across-the-wheels axis: the slab is the same either way
    return turn


def piece_of(box: tuple[float, float, float], mass: float, inertia, stiffness: float | None) -> PinchPiece:
    """The pinch's slab for a piece of bounding box `box` (sides along body x, y, z), `mass`, inertia (Ixx, Iyy, Izz,
    Ixy, Ixz, Iyz about its center of mass, body frame) and contact `stiffness`, lying face-parallel to the channel
    (channel_turn): the pitch inertia is about the channel's y."""
    ixx, iyy, izz, ixy, ixz, iyz = (float(v) for v in inertia)
    body = np.array([[ixx, ixy, ixz], [ixy, iyy, iyz], [ixz, iyz, izz]])
    turn = channel_turn(box)
    channel = turn @ body @ turn.T
    sides = sorted(box)
    return PinchPiece(thickness=sides[0], length=sides[1], width=sides[2], mass=mass,
                      pitch_inertia=float(channel[1, 1]), stiffness=stiffness)
