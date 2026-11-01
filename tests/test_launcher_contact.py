import math

import pytest

from scrimmage.launcher import contact
from scrimmage.launcher.contact import (
    friction_force, normal_force, rolling_loss_torque, wheel_contact_fraction, wheel_stiffness,
)
from scrimmage.launcher.inputs import PRESETS, Ball, Wheels

POWER_CELL = Ball(**PRESETS["ball"]["2020-power-cell"])


def wheels(preset="30A", **kw):
    args = dict(diameter=0.1016, width=0.05, **PRESETS["wheels"][preset])
    args.update(kw)
    return Wheels(**args)


def test_force_at_reference_compression():
    b = POWER_CELL
    assert normal_force(0.25 * b.diameter, 0.0, b, None) == pytest.approx(b.stiffness_n, rel=1e-9)
    # power law: half the compression gives 0.5^1.5 of the force
    assert normal_force(0.125 * b.diameter, 0.0, b, None) == pytest.approx(b.stiffness_n * 0.5**1.5, rel=1e-9)
    assert normal_force(0.0, 1.0, b, None) == 0.0
    assert normal_force(-0.01, -1.0, b, None) == 0.0


def _drop(ball: Ball, v0: float) -> float:
    """Integrate a ball hitting a rigid plate at v0 (RK4, fine step); return the rebound speed.

    A drop loads one contact: indenting the plate by x squeezes the ball as a two-plate squeeze of 2x would, so
    the law is applied at delta = 2x, rate 2x'."""
    m = ball.mass
    x, v = 0.0, v0  # x: indentation into the plate, v: its rate
    # contact-time scale of an undamped power-law impact
    n = ball.stiffness_exponent
    k = ball.stiffness_n / (0.25 * ball.diameter) ** n
    t_scale = (m * v0**2 * (n + 1) / (2 * k)) ** (1 / (n + 1)) / v0
    dt = t_scale / 2000

    def acc(x, v):
        return -normal_force(2 * x, 2 * v, ball, None) / m

    for _ in range(100000):
        k1x, k1v = v, acc(x, v)
        k2x, k2v = v + dt / 2 * k1v, acc(x + dt / 2 * k1x, v + dt / 2 * k1v)
        k3x, k3v = v + dt / 2 * k2v, acc(x + dt / 2 * k2x, v + dt / 2 * k2v)
        k4x, k4v = v + dt * k3v, acc(x + dt * k3x, v + dt * k3v)
        x += dt / 6 * (k1x + 2 * k2x + 2 * k3x + k4x)
        v += dt / 6 * (k1v + 2 * k2v + 2 * k3v + k4v)
        if x <= 0.0:
            return -v
    raise AssertionError("ball never left the plate")


@pytest.mark.parametrize("e", [0.3, 0.5, 0.8])
def test_drop_reproduces_restitution(e):
    ball = Ball(diameter=0.1778, mass=0.1417, stiffness_n=60.0, restitution=e)
    assert _drop(ball, 3.0) == pytest.approx(e * 3.0, abs=0.03)


def test_no_tension():
    b = POWER_CELL
    for delta in (1e-6, 0.001, 0.01, 0.04, 0.08):
        for rate in (-0.1, -1.0, -5.0, -50.0, -1e6):
            assert normal_force(delta, rate, b, None) >= 0.0
            assert normal_force(delta, rate, b, 1e5) >= 0.0
    # separating faster than the damping can bear: zero, not a pull
    assert normal_force(0.01, -1e6, b, None) == 0.0
    # approaching adds damping, separating removes it
    assert normal_force(0.02, 1.0, b, None) > normal_force(0.02, 0.0, b, None) > normal_force(0.02, -1.0, b, None)


def test_wheel_compliance_softens():
    b = POWER_CELL
    delta = 0.25 * b.diameter
    width = math.sqrt(b.diameter * delta)
    soft = normal_force(delta, 0.0, b, wheel_stiffness(wheels("30A"), width))
    hard = normal_force(delta, 0.0, b, wheel_stiffness(wheels("60A"), width))
    rigid = normal_force(delta, 0.0, b, None)
    assert soft < hard < rigid
    # a very soft wheel takes most of the squeeze
    assert normal_force(delta, 0.0, b, 100.0) < 0.1 * rigid
    # harder rubber is stiffer, and a wider patch is stiffer
    assert wheel_stiffness(wheels("30A"), width) < wheel_stiffness(wheels("60A"), width)
    assert wheel_stiffness(wheels("30A"), width) < wheel_stiffness(wheels("30A"), 2 * width)


def test_series_split_is_consistent():
    """The ball's force equals the wheel spring's force: F = k * (delta - delta_ball)."""
    b = POWER_CELL
    k = 2000.0
    delta = 0.03
    f = normal_force(delta, 0.0, b, k)
    d_ball = 0.25 * b.diameter * (f / b.stiffness_n) ** (1 / b.stiffness_exponent)
    assert f == pytest.approx(k * (delta - d_ball), rel=1e-6)


def test_friction_saturates_and_is_odd():
    n, mu = 50.0, 0.8
    assert friction_force(1.0, n, mu) == pytest.approx(mu * n, rel=1e-6)
    assert friction_force(-1.0, n, mu) == pytest.approx(-mu * n, rel=1e-6)
    assert friction_force(0.0, n, mu) == 0.0
    for s in (0.001, 0.005, 0.02, 0.3):
        assert friction_force(-s, n, mu) == -friction_force(s, n, mu)
        assert 0.0 < friction_force(s, n, mu) <= mu * n
    assert friction_force(0.005, n, mu) < 0.5 * mu * n  # well below full friction inside the slip speed
    # regularized over 0.01 m/s: tanh(1) of full friction at that slip
    assert friction_force(0.01, n, mu) == pytest.approx(mu * n * math.tanh(1.0))
    assert friction_force(1.0, 0.0, mu) == 0.0
    assert friction_force(1.0, -5.0, mu) == 0.0  # no normal force, no friction


def test_contact_fraction():
    d, delta = 0.1778, 0.02
    patch = math.sqrt(d * delta)  # about 0.06 m
    wide = wheels(width=0.1, positions=(0.0,))
    assert wheel_contact_fraction(0.0, d, delta, wide) == 1.0
    two = wheels(width=0.04, positions=(-0.1, 0.1))
    assert wheel_contact_fraction(0.0, d, delta, two) == 0.0  # in the gap between wheels
    edge = wheel_contact_fraction(0.12, d, delta, wheels(width=0.04, positions=(0.1,)))  # wheel edge at 0.12
    assert 0.0 < edge < 1.0
    assert edge == pytest.approx(0.5)
    roller = wheels(width=0.4, roller=True, positions=(0.15,))  # positions are ignored for a roller
    for y in (-0.15, -0.05, 0.0, 0.1, 0.16):
        assert wheel_contact_fraction(y, d, delta, roller) == 1.0
    # overlapping wheels are not counted twice
    stacked = wheels(width=0.04, positions=(0.0, 0.01))
    assert wheel_contact_fraction(0.005, d, delta, stacked) == pytest.approx(0.05 / patch)
    # no squish: the point under the ball decides
    assert wheel_contact_fraction(0.0, d, 0.0, wide) == 1.0
    assert wheel_contact_fraction(0.0, d, 0.0, two) == 0.0


def test_rolling_loss_from_restitution():
    b = POWER_CELL
    delta = 0.02
    n = normal_force(delta, 0.0, b, None)
    a = math.sqrt(b.diameter * delta) / 2
    assert rolling_loss_torque(n, b, delta) == pytest.approx(3 / 16 * (1 - b.restitution**2) * a * n)
    lossless = Ball(diameter=b.diameter, mass=b.mass, stiffness_n=b.stiffness_n, restitution=1.0)
    assert rolling_loss_torque(n, lossless, delta) == 0.0
    deader = Ball(diameter=b.diameter, mass=b.mass, stiffness_n=b.stiffness_n, restitution=0.3)
    assert rolling_loss_torque(n, deader, delta) > rolling_loss_torque(n, b, delta)
    assert rolling_loss_torque(0.0, b, delta) == 0.0
    assert rolling_loss_torque(n, b, 0.0) == 0.0


def test_damping_constant_is_exact():
    """The Hunt-Crossley energy integral is met exactly by the damping constant."""
    for e in (0.2, 0.5, 0.9):
        u = contact._damping(e) * 2 * contact.RESTITUTION_SPEED  # the drop squeezes delta at twice its speed
        w = e * u
        assert u - math.log1p(u) == pytest.approx(-w - math.log1p(-w), rel=1e-9)
    assert contact._damping(1.0) == 0.0
