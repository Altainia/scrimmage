import math

import numpy as np
import pytest

from scrimmage.launcher.contact import (
    contact_force, normal_force, rolling_loss_torque, split_squeeze, wheel_contact_fraction, wheel_stiffness,
)
from scrimmage.launcher.inputs import parse_setup
from scrimmage.launcher.model import GRAVITY, ExitedBall, FedBall, Launcher
from scrimmage.launcher.shaft import VelocityController

POWER_CELL_D = 0.1778

BASE = {
    "ball": {"preset": "2020-power-cell"},
    "wheels": {"diameter": 0.1016, "width": 0.05, "preset": "30A", "positions": [0.0]},
    "shaft": {"wheel_inertia": 0.002},
    "control": {"target_rps": 60.0, "kP": 0.2},
    "hood": {"gap": 0.8 * POWER_CELL_D, "exit_angle_deg": 40.0, "friction": 0.5},
    "launcher": {"height": 0.5, "width": 0.3},
    "goal": {"distance": 3.0, "height": 2.64, "diameter": 1.2},
}


def setup(**sections):
    raw = {k: dict(v) for k, v in BASE.items()}
    for name, changes in sections.items():
        if name == "dt":
            raw["dt"] = changes
        else:
            raw.setdefault(name, {}).update(changes)
    return parse_setup(raw)


def fed(s, lane=0.0, entry_speed=1.0):
    return FedBall(available_at=0.0, lane=lane, entry_speed=entry_speed, ball=s.ball)


def hold(launcher, speed):
    """Hold the wheel at `speed` (rad/s): the shaft no longer responds to voltage or load."""
    launcher.shaft.speed = speed
    launcher.shaft.inertia = math.inf  # the implicit solve then holds the speed too
    launcher.shaft.step = lambda dt, voltage, load_torque: None


def shoot(s, balls=None, held=None, closed_loop=True, max_time=1.0):
    """Start the shaft at its target (or hold it), insert the balls, step until all have exited."""
    launcher = Launcher(s)
    target = 2 * math.pi * s.control.target_rps
    if held is not None:
        hold(launcher, held)
    else:
        launcher.shaft.speed = target
    ctrl = VelocityController(s.control, s.shaft)
    balls = balls if balls is not None else [fed(s)]
    out: list[ExitedBall] = []
    for b in balls:
        done = launcher.insert(b, 0.0)
        if done is not None:
            out.append(done)
    dt = launcher.default_dt()
    t = 0.0
    while len(out) < len(balls) and t < max_time:
        voltage = ctrl.voltage(launcher.shaft) if closed_loop else 0.0
        out += launcher.step(dt, voltage)
        t += dt
    assert len(out) == len(balls)
    return launcher, sorted(out, key=lambda e: e.index)


def surface_speed(s, rps=None):
    return 2 * math.pi * (rps or s.control.target_rps) * s.wheels.diameter / 2


def test_energy_balances():
    for squish in (0.05, 0.2, 0.3):
        s = setup(hood={"gap": (1 - squish) * POWER_CELL_D})
        _, (e,) = shoot(s)
        assert e.status == "launched"
        en = e.energy
        into = en.from_shaft + en.entry_kinetic
        out = en.exit_linear + en.exit_spin + en.potential + en.slip_loss + en.hysteresis_loss
        assert en.slip_loss > 0 and en.hysteresis_loss > 0
        assert en.potential > 0  # it climbs the back of the wheel, from 240 to 130 degrees
        assert out == pytest.approx(into, rel=0.01), (squish, en)


def test_no_slip_no_loss_limit():
    # A stiff ball: the squeeze must hold it on the wheel against its centripetal load. The Power Cell's own 60 N
    # lifts off the wheel at about 6.3 m/s at this squeeze (the centrifugal split), below V/2 = 9.6 m/s.
    s = setup(
        wheels={"friction": 5.0}, hood={"friction": 5.0, "gap": 0.9 * POWER_CELL_D},
        ball={"restitution": 0.99, "stiffness_n": 1000.0},
    )
    v_surface = surface_speed(s)
    _, (e,) = shoot(s, held=v_surface / (s.wheels.diameter / 2))
    assert e.status == "launched"
    assert e.speed == pytest.approx(v_surface / 2, rel=0.05)
    assert e.spin == pytest.approx(v_surface / s.hood.gap, rel=0.05)


def squish_speeds():
    speeds = {}
    for squish in (0.02, 0.10, 0.20, 0.30, 0.40, 0.60):
        s = setup(hood={"gap": (1 - squish) * POWER_CELL_D})
        _, (e,) = shoot(s)
        assert e.status == "launched", (squish, e.status)
        speeds[squish] = e.speed
    return speeds


def test_squish_trend():
    # More squeeze grips harder and holds the ball on the wheel against its centripetal load to a higher speed, but
    # costs more in the lead-in's wedge and in rolling loss: the exit speed peaks at a middle squeeze (about 0.4 D
    # here, 0.3 D within 2%; FRC practice calls about 0.28 D the sweet spot for the Power Cell).
    speeds = squish_speeds()
    peak = max(speeds.values())
    peak_at = max(speeds, key=speeds.get)
    assert peak_at in (0.20, 0.30, 0.40), speeds
    assert speeds[0.02] < peak
    assert speeds[0.60] < peak, speeds


def test_hood_angle_trend():
    # The hood pivots about the shaft with its entry fixed (at 240 degrees) and the ball travels with phi falling to
    # phi_exit = exit_angle_deg + 90. A higher exit angle therefore moves the exit end back toward the entry: less
    # wrap, so the ball is in contact for a SHORTER time, and launches steeper (ruling R8).
    low = shoot(setup(hood={"exit_angle_deg": 30.0}))[1][0]
    high = shoot(setup(hood={"exit_angle_deg": 55.0}))[1][0]
    assert low.status == high.status == "launched"
    assert high.launch_deg > low.launch_deg
    assert high.launch_deg == pytest.approx(55.0)
    assert (high.t_exit - high.t_enter) < (low.t_exit - low.t_enter)


@pytest.mark.parametrize("gap", [POWER_CELL_D, 1.2 * POWER_CELL_D])
def test_gap_at_least_diameter_is_no_contact(gap):
    s = setup(hood={"gap": gap})
    launcher, (e,) = shoot(s)
    assert e.status == "no_contact"
    for name in ("t_exit", "speed", "spin", "launch_deg"):
        assert math.isnan(getattr(e, name)), name
    assert launcher.in_contact() == 0


def test_lane_without_wheel():
    s = setup(wheels={"positions": [-0.1, 0.1], "width": 0.05})
    launcher, (e,) = shoot(s, closed_loop=False)
    assert e.status == "no_wheel"
    assert math.isnan(e.speed)
    assert e.energy.from_shaft == 0.0
    # The same steps with no ball give the same shaft speed.
    assert launcher.step(launcher.default_dt(), 0.0) == []  # reported by insert, not again by step
    alone = Launcher(s)
    alone.shaft.speed = 2 * math.pi * s.control.target_rps
    alone.step(launcher.default_dt(), 0.0)
    assert launcher.shaft.speed == alone.shaft.speed


def test_partial_wheel_launches_slower():
    # Half the patch over the wheel: half the squeeze force and grip at the wheel.
    s = setup(wheels={"positions": [0.0], "width": 0.05})
    full = shoot(s)[1][0]
    edge = shoot(s, balls=[fed(s, lane=0.025)])[1][0]
    assert edge.status == "launched"
    assert edge.energy.from_shaft < full.energy.from_shaft


def test_side_by_side_dips_more():
    s = setup(wheels={"positions": [-0.08, 0.08], "width": 0.05}, launcher={"width": 0.34})  # 2 x (0.08 + D/2) fits
    one, _ = shoot(s, balls=[fed(s, lane=-0.08)], closed_loop=False)
    two, exits = shoot(s, balls=[fed(s, lane=-0.08), fed(s, lane=0.08)], closed_loop=False)
    assert all(e.status == "launched" for e in exits)
    for launcher in (one, two):  # compare at the same time
        launcher.step(0.3 - launcher.time, 0.0)
    assert two.time == pytest.approx(one.time)
    assert two.shaft.speed < one.shaft.speed


def test_step_converges():
    base = setup()
    dt = Launcher(base).default_dt()
    coarse = shoot(setup(dt=dt))[1][0]
    fine = shoot(setup(dt=dt / 2))[1][0]
    assert fine.speed == pytest.approx(coarse.speed, rel=0.01)
    assert fine.spin == pytest.approx(coarse.spin, rel=0.01)


def test_stalled_ball_is_jammed():
    # A ball pushed in at no speed is never drawn in: the squeeze starts at zero.
    s = setup()
    _, (e,) = shoot(s, balls=[fed(s, entry_speed=0.0)], max_time=0.6)
    assert e.status == "jammed"
    assert math.isnan(e.speed)


def test_potential_is_the_climb():
    # From the entry (240 degrees) to the hood's end (130), then on round the path over the release,
    # sqrt(D delta) past it.
    s = setup()
    _, (e,) = shoot(s)
    r_c = s.wheels.diameter / 2 + s.hood.gap / 2
    release = math.sqrt(s.ball.diameter * (s.ball.diameter - s.hood.gap))
    rise = r_c * (math.sin(math.radians(130.0) - release / r_c) - math.sin(math.radians(240.0)))
    assert e.energy.potential == pytest.approx(s.ball.mass * 9.81 * rise, rel=1e-6)


def test_gravity_slows_a_slow_ball():
    # Light squeeze: the wheel barely grips, so climbing the back of the wheel costs a large share of the speed.
    s = setup(hood={"gap": 0.98 * POWER_CELL_D})
    _, (e,) = shoot(s)
    assert e.status == "launched" and e.energy.potential > 0.25 * e.energy.exit_linear


def test_inertia_factor_sets_spin():
    shell = setup(ball={"inertia_factor": 0.667})
    solid = setup(ball={"inertia_factor": 0.4})
    e_shell, e_solid = shoot(shell)[1][0], shoot(solid)[1][0]
    assert e_shell.energy.exit_spin != pytest.approx(e_solid.energy.exit_spin, rel=0.01)


def test_flare_is_an_input():
    gentle = shoot(setup(hood={"flare_deg": 20.0}))[1][0]
    steep = shoot(setup(hood={"flare_deg": 45.0}))[1][0]
    assert gentle.status == steep.status == "launched"
    assert gentle.speed != pytest.approx(steep.speed, rel=0.01)


def test_rolling_loss_is_per_distance_rolled(monkeypatch):
    """Over the constant-gap arc, hysteresis is Tabor's loss M per contact times the rate the ball rolls through the
    contacts, w + v / R_c: the contacts lie on the radius through the center, which turns at v / R_c. Rolling purely
    on the fixed hood, that is v (1 + r / R_c) / r: more than the lab spin v / r. With high grip the ball rolls at a
    steady speed there, so its squeeze split barely moves and the contacts' damping is negligible; each contact's
    load and patch come from the split (`split_squeeze`)."""
    s = setup(wheels={"friction": 5.0}, hood={"friction": 5.0})
    ball, d = s.ball, s.ball.diameter
    delta = d - s.hood.gap
    r, r_c = s.hood.gap / 2, s.wheels.diameter / 2 + s.hood.gap / 2
    cover = wheel_contact_fraction(0.0, d, delta, s.wheels)  # the 8 cm patch is wider than the 5 cm wheel
    k = wheel_stiffness(s.wheels, math.sqrt(d * delta))
    ramp = delta / math.tan(math.radians(s.hood.flare_deg))
    steps = []
    real_apply = Launcher._apply

    def apply(self, p, lin, h, d_omega):
        before = (p.s, p.v, p.w, p.hysteresis)
        out = real_apply(self, p, lin, h, d_omega)
        steps.append((h, before, (p.s, p.v, p.w, p.hysteresis), self.path))
        return out

    monkeypatch.setattr(Launcher, "_apply", apply)
    # 30 rps: the wheel grips the ball up to V/2 = 4.8 m/s within the lead-in, and it then rolls at a steady
    # speed. (Faster, the ball would still be accelerating through the arc, its centripetal load growing, so the
    # split would keep shifting the squeeze onto the hood and the hood contact's own damping would add to N_h.)
    _, (e,) = shoot(s, held=2 * math.pi * 30.0)
    assert e.status == "launched"
    measured = expected = 0.0
    for h, (s0, v0, w0, hyst0), (s1, v1, w1, hyst1), path in steps:
        if not (ramp < s0 and s1 < path):
            continue  # only the constant-gap arc
        v, w = (v0 + v1) / 2, (w0 + w1) / 2
        phi = math.radians(s.hood.entry_deg) - s0 / r_c
        sp = split_squeeze(delta, ball.mass * v0 * v0 / r_c - ball.mass * GRAVITY * math.sin(phi), ball, k, cover)
        torque = rolling_loss_torque(sp.n_w, ball, 2 * sp.x_w) + rolling_loss_torque(sp.n_h, ball, 2 * sp.x_h)
        expected += h * torque * abs(w + v / r_c)
        measured += hyst1 - hyst0
        assert w * r == pytest.approx(v, rel=0.02)  # rolling on the hood (high grip)
    assert expected > 0
    assert measured == pytest.approx(expected, rel=0.01)


def shaft_trace(s, dt=None, where=None):
    launcher = Launcher(s)
    launcher.shaft.speed = 2 * math.pi * s.control.target_rps
    ctrl = VelocityController(s.control, s.shaft)
    launcher.insert(fed(s), 0.0)
    out, speeds = [], [launcher.shaft.speed]
    while not out:
        if where is not None:
            where.append(launcher._active[0].s)  # the ball's arc length at the start of each step
        out += launcher.step(dt or launcher.default_dt(), ctrl.voltage(launcher.shaft))
        speeds.append(launcher.shaft.speed)
    return out[0], np.array(speeds)


def test_light_wheel_shaft_is_smooth():
    """A light wheel (2e-4 kg m^2, a bare 4 in. wheel is about 3e-4) with high grip, closed loop.

    A ball dips the speed and the controller recovers it, so the step-to-step change dOmega changes sign about
    once. A chattering step alternates it, which makes the second difference about twice the first; a smooth trace
    has it far smaller, except where the force itself jumps: at the squeeze profile's corners (the lead-in's end and
    the hood's end), where the squeeze rate, and so the contacts' damping, changes at once. Those steps are left
    out of the second-difference check."""
    s = setup(wheels={"friction": 5.0}, hood={"friction": 5.0}, shaft={"wheel_inertia": 2e-4})
    where = []
    e, speeds = shaft_trace(s, where=where)
    assert e.status == "launched"
    first = np.diff(speeds)
    second = np.diff(first)
    flips = int(np.sum(np.sign(first[1:]) != np.sign(first[:-1])))
    assert flips <= 2, flips
    launcher = Launcher(s)
    corners = ((s.ball.diameter - s.hood.gap) / math.tan(math.radians(s.hood.flare_deg)), launcher.path)
    ends = where + [math.inf]
    crosses = [any(a <= c < b for c in corners) for a, b in zip(ends[:-1], ends[1:])]  # step i crosses a corner
    smooth = [abs(second[i]) for i in range(len(second)) if not (crosses[i] or crosses[i + 1])]
    assert max(smooth) < 0.5 * np.max(np.abs(first))
    assert speeds.min() < speeds[0] - 2 * math.pi * 5  # it did dip (by more than 5 rps)
    half, _ = shaft_trace(setup(wheels={"friction": 5.0}, hood={"friction": 5.0}, shaft={"wheel_inertia": 2e-4},
                                dt=Launcher(s).default_dt() / 2))
    assert half.energy.from_shaft == pytest.approx(e.energy.from_shaft, rel=0.01)
    assert half.speed == pytest.approx(e.speed, rel=0.01)


def test_short_wrap_with_wide_flare_runs():
    s = setup(hood={"exit_angle_deg": 125.0})  # a 25 degree wrap, shorter than the flares' ramps
    _, (e,) = shoot(s)
    assert e.status in ("launched", "jammed")


def held_shot(s, rps, **kw):
    return shoot(s, held=2 * math.pi * rps, **kw)[1][0]


def test_exit_slip_reported():
    s = setup()
    slow, fast = held_shot(s, 25.0), held_shot(s, 90.0)
    assert slow.status == fast.status == "launched"
    assert abs(slow.exit_slip) < 0.05  # below the knee: the wheel grips the ball out
    assert fast.exit_slip > 0.3  # far above it: grip-limited, the wheel slides under the ball
    v_surface = surface_speed(s, 90.0)
    assert fast.exit_slip == pytest.approx((v_surface - fast.speed - fast.spin * s.hood.gap / 2) / v_surface)


def test_exit_speed_tracks_wheel_speed_below_knee():
    s = setup()
    a, b = held_shot(s, 20.0), held_shot(s, 30.0)
    assert b.speed / a.speed == pytest.approx(30.0 / 20.0, rel=0.05)
    c, d = held_shot(s, 90.0), held_shot(s, 120.0)
    assert d.speed == pytest.approx(c.speed, rel=0.05)  # the grip plateau is real Coulomb physics


def test_abrupt_release_beats_flared_exit():
    abrupt = held_shot(setup(), 90.0)
    flared = held_shot(setup(hood={"exit_flare_deg": 30.0}), 90.0)
    assert abrupt.status == flared.status == "launched"
    assert abrupt.speed > flared.speed


def test_split_helper_balances_the_radial_load():
    s = setup(wheels={"width": 0.08})
    ball, d = s.ball, s.ball.diameter
    delta = d - s.hood.gap
    k = wheel_stiffness(s.wheels, math.sqrt(d * delta))
    # No radial load, full coverage: the symmetric two-plate squeeze.
    sym = split_squeeze(delta, 0.0, ball, k, 1.0)
    assert sym.n_w == pytest.approx(normal_force(delta, 0.0, ball, k), rel=1e-9)
    assert sym.n_h == pytest.approx(sym.n_w, rel=1e-9)
    previous = sym.n_w
    for radial in (10.0, 30.0, 60.0):
        sp = split_squeeze(delta, radial, ball, k, 1.0)
        assert sp.x_w + sp.x_h == pytest.approx(delta, rel=1e-9)
        assert sp.n_h - sp.n_w == pytest.approx(radial, rel=1e-9)
        assert sp.n_w < previous  # the hood takes the load, the wheel loses grip
        previous = sp.n_w
    # More radial load than the whole squeeze on the hood holds: the ball lifts off the wheel.
    hood_alone = contact_force(delta, 0.0, ball)
    lifted = split_squeeze(delta, 1.01 * hood_alone, ball, k, 1.0)
    assert lifted.n_w == 0.0 and lifted.x_w == 0.0
    assert lifted.n_h == pytest.approx(hood_alone)


def record_splits(monkeypatch):
    seen = []
    real = Launcher._split

    def spy(self, p, s_arc, v):
        sp = real(self, p, s_arc, v)
        seen.append((self, p, s_arc, v, sp))
        return sp

    monkeypatch.setattr(Launcher, "_split", spy)
    return seen


def test_centrifugal_split_unloads_wheel(monkeypatch):
    seen = record_splits(monkeypatch)
    s = setup(wheels={"width": 0.08})
    e = held_shot(s, 60.0)
    assert e.status == "launched"
    m, ramp = s.ball.mass, (s.ball.diameter - s.hood.gap) / math.tan(math.radians(s.hood.flare_deg))
    arc = [(L, p, x, v, sp) for L, p, x, v, sp in seen if ramp < x < L.path]
    assert arc
    for L, p, x, v, sp in arc:
        radial = m * v * v / L.r_path - m * GRAVITY * math.sin(L._phi(x))
        assert sp.n_h - sp.n_w == pytest.approx(radial, rel=1e-6, abs=1e-9)
    slow = min(arc, key=lambda r: r[3])
    fast = max(arc, key=lambda r: r[3])
    assert fast[4].n_w < slow[4].n_w  # faster, so less of the squeeze on the wheel

    # A soft ball fed fast lifts off the wheel in the constant-gap arc; that is a state, not an error.
    seen.clear()
    soft = setup(wheels={"width": 0.08}, ball={"stiffness_n": 10.0})
    out = shoot(soft, balls=[fed(soft, entry_speed=12.0)], held=2 * math.pi * 90.0)[1][0]
    assert out.status in ("launched", "jammed")
    lifted = [sp for L, p, x, v, sp in seen if ramp < x < L.path and sp.n_w == 0.0]
    assert lifted and all(sp.n_h > 0.0 for sp in lifted)


def test_release_is_parabolic_from_the_edge():
    """Past the hood's end a sphere's surface recedes from the hood as xi^2 / D: the squeeze falls from zero slope."""
    s = setup()
    launcher = Launcher(s)
    launcher.insert(fed(s), 0.0)
    p = launcher._active[0]
    d, delta = s.ball.diameter, s.ball.diameter - s.hood.gap
    edge = launcher.path
    assert launcher._squeeze(p, edge) == (delta, 0.0)
    for xi in (1e-4, 0.01, 0.03):
        at, slope = launcher._squeeze(p, edge + xi)
        assert at == pytest.approx(delta - xi * xi / d)
        assert slope == pytest.approx(-2.0 * xi / d)
    end = edge + math.sqrt(d * delta)
    assert p.consts["end"] == pytest.approx(end)
    assert launcher._squeeze(p, end)[0] == pytest.approx(0.0, abs=1e-12)
    assert launcher._squeeze(p, end + 0.01) == (0.0, 0.0)


def release_damping(s, rps):
    """The share of the elastic energy stored at the start of the exit end (the hood's end, or the lip's start) that
    the contacts' damping dissipates over it (hysteresis less rolling), and the shot."""
    launcher = Launcher(s)
    hold(launcher, 2 * math.pi * rps)
    launcher.insert(fed(s), 0.0)
    real = launcher._apply
    book = {"lost": 0.0, "stored": None}

    def apply(p, lin, h, d_omega):
        h0, s0 = p.hysteresis, p.s
        _, slope = launcher._squeeze(p, s0)
        if slope < 0.0 and book["stored"] is None:
            book["stored"] = launcher._stored(p, s0, lin["split"])
        out = real(p, lin, h, d_omega)
        if slope < 0.0:
            book["lost"] += p.hysteresis - h0 - h * lin["roll"] * (p.w + p.v / launcher.r_path)
        return out

    launcher._apply = apply
    out = []
    while not out:
        out += launcher.step(launcher.default_dt(), 0.0)
    return book["lost"] / book["stored"], out[0]


def test_default_release_dissipates_less_than_a_flare():
    # Grip-limited (120 rps against a knee near 90), full coverage, a firm ball. The parabolic release starts
    # unloading from zero rate, so its damping takes a smaller share of the stored elastic energy than a 30 degree
    # lip's. (A share, not joules: the default hood's ball reaches its exit end faster, having had the whole arc to
    # grip, and Hunt-Crossley loses more at faster unloading. For a soft 60 N ball the shares are about equal.)
    abrupt, a = release_damping(setup(wheels={"width": 0.08}, ball={"stiffness_n": 240.0}), 120.0)
    flared, b = release_damping(
        setup(wheels={"width": 0.08}, ball={"stiffness_n": 240.0}, hood={"exit_flare_deg": 30.0}), 120.0,
    )
    assert a.status == b.status == "launched" and a.exit_slip > 0.1 and b.exit_slip > 0.1
    assert 0.0 < abrupt < flared
    assert a.speed > b.speed


def test_zero_step_is_a_no_op():
    s = setup()
    launcher = Launcher(s)
    launcher.shaft.speed = 2 * math.pi * s.control.target_rps
    launcher.insert(fed(s), 0.0)
    assert launcher.step(0.0, 12.0) == [] and launcher.time == 0.0 and launcher.in_contact() == 1
    assert launcher._active[0].s == 0.0
