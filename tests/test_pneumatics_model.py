"""The pneumatic model: compressor curve, cylinder forces, air use, the pressure switch and the regulator."""
import math

import pytest

from scrimmage.pneumatics.model import (ATM, COMPRESSORS, DEFAULT_CONDUCTANCE, PSI, AirConfig, AirSystem,
                                        CylinderSpec)

BORE, ROD, STROKE = 0.0269875, 0.0079375, 0.1524     # 1-1/16 in, 5/16 in, 6 in
A_CAP = math.pi * BORE ** 2 / 4
A_ANN = math.pi * (BORE ** 2 - ROD ** 2) / 4


def cyl(name="c"):
    return CylinderSpec(name, BORE, ROD, STROKE, 8.0, DEFAULT_CONDUCTANCE)


def system(**config):
    config.setdefault("compressor", "none")
    return AirSystem(AirConfig(**config), [cyl()])


def vented(air, valve):
    """Step once on "retract" so the cap side is at atmosphere, then switch to `valve`."""
    air.set_valve("c", "retract")
    air.step(0.001, {"c": 0.0}, True, True)
    air.set_valve("c", valve)


def run(air, seconds, x, dt=0.001, enabled=True, closed_loop=True):
    f = {}
    for _ in range(round(seconds / dt)):
        f = air.step(dt, {"c": x}, enabled, closed_loop)
    return f["c"]


def test_compressor_curve():
    c = COMPRESSORS["VIAIR 90C"]
    assert c.flow_cfm(0) == 0.88
    assert c.amps(100) == 11.0
    assert c.flow_cfm(120) == pytest.approx(0.088, abs=1e-9)
    assert c.flow_cfm(400) == 0.0


def test_stall_force_is_pressure_times_area():
    air = system()
    vented(air, "extend")
    assert run(air, 1.0, 0.0) == pytest.approx(60 * PSI * A_CAP, rel=0.01)


def test_retract_force_uses_the_annulus():
    air = system()
    vented(air, "extend")
    run(air, 0.5, STROKE)               # extended and full on the cap side
    air.set_valve("c", "retract")
    assert run(air, 1.0, STROKE) == pytest.approx(-60 * PSI * A_ANN, rel=0.01)


@pytest.mark.parametrize("dt", [0.001, 0.01])
def test_no_overshoot(dt):
    air = system()
    vented(air, "extend")
    assert air.chamber_psi("c")[0] == pytest.approx(0.0, abs=1e-6)
    peak = 0.0
    for _ in range(round(1.0 / dt)):
        air.step(dt, {"c": 0.0}, True, True)
        peak = max(peak, air.chamber_psi("c")[0])
        assert air.chamber_psi("c")[0] <= 60.0 + 1e-6
    assert peak == pytest.approx(60.0, abs=1e-6)       # it filled, and stopped at the regulator


def test_never_above_the_tanks():
    # Tanks below the working pressure, a big step: the chamber may not end up above the tanks.
    air = AirSystem(AirConfig(tanks=1, tank_ml=100.0, compressor="none", start_psi=20.0), [cyl()])
    air.set_valve("c", "extend")
    for _ in range(10):
        air.step(0.1, {"c": STROKE}, True, True)
        assert air.chamber_psi("c")[0] <= air.stored_psi + 1e-6


def test_air_used_per_stroke():
    air = system()
    vented(air, "extend")
    before = air.stored_psi
    for i in range(500):
        air.step(0.001, {"c": STROKE * (i + 1) / 500}, True, True)
    run(air, 1.0, STROKE)
    used = (before - air.stored_psi) * PSI * (2 * 574e-6) / ATM          # m^3 of free air
    # The extended cap side holds its volume at 60 psi gauge, i.e. (60 psi + atmosphere) absolute, of which the
    # dead volume was already there as atmosphere (the cap side starts vented).
    absolute = (60 * PSI + ATM) / ATM
    expected = (A_CAP * STROKE + 1e-6) * absolute - 1e-6
    assert used == pytest.approx(expected, rel=0.05)


def test_switch_hysteresis():
    air = system(compressor="VIAIR 90C", start_psi=100.0)
    assert air.switch_calls_for_air
    air.step(0.001, {"c": 0.0}, True, True)
    assert air.compressor_on and air.compressor_amps > 0
    while air.compressor_on:                     # a few minutes at most; the 90C is slow
        air.step(0.1, {"c": 0.0}, True, True)
    assert air.stored_psi == pytest.approx(115.0, abs=0.5)
    assert not air.switch_calls_for_air
    air.step(0.1, {"c": 0.0}, True, True)
    assert not air.compressor_on and air.compressor_amps == 0.0
    # Stays off until the tanks fall below 95: drain them directly.
    for i in range(100000):
        air._tank_mass *= 0.999
        air.step(0.001, {"c": 0.0}, True, True)
        if air.stored_psi >= 95.0:
            assert not air.switch_calls_for_air
        else:
            break
    assert air.switch_calls_for_air and air.compressor_on


def test_disabled_never_runs():
    air = system(compressor="VIAIR 90C", start_psi=100.0)
    for enabled, closed_loop in ((False, True), (True, False)):
        air.step(0.1, {"c": 0.0}, enabled, closed_loop)
        assert air.switch_calls_for_air and not air.compressor_on


def test_regulator_follows_low_tanks():
    air = system(start_psi=40.0)
    assert air.working_psi == pytest.approx(40.0)


def test_double_hold_keeps_state():
    air = system()
    vented(air, "extend")
    run(air, 0.5, 0.0)
    air.set_valve("c", "hold")
    run(air, 0.5, 0.0)
    assert air.chamber_psi("c")[0] == pytest.approx(60.0, abs=1e-6)


def test_config_refuses_over_the_rules():
    for bad in ({"working_psi": 61.0}, {"start_psi": 121.0}, {"switch_on_psi": 115.0}, {"tanks": 0},
                {"switch_off_psi": 121.0}):
        with pytest.raises(ValueError):
            AirConfig(**bad)


def test_set_valve_refuses_unknowns():
    air = system()
    with pytest.raises(ValueError):
        air.set_valve("nope", "extend")
    with pytest.raises(ValueError):
        air.set_valve("c", "sideways")
