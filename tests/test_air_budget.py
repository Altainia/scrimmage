"""scrimmage air: the air budget."""

import math

import pytest

from scrimmage.__main__ import main
from scrimmage.pneumatics.budget import budget, format_budget
from scrimmage.pneumatics.model import ATM, CFM, COMPRESSORS, PSI, AirConfig, CylinderSpec

CYL = CylinderSpec("cyl", bore=0.0254, rod=0.0079375, stroke=0.1524, friction=0.0, conductance=8e-9)


def expected_cycle_l(c: CylinderSpec, psi: float) -> float:
    a_cap = math.pi * c.bore ** 2 / 4
    a_rod = a_cap - math.pi * c.rod ** 2 / 4
    swept = (a_cap + a_rod) * c.stroke * (psi * PSI + ATM) / ATM
    dead = 2 * c.dead_volume * psi * PSI / ATM
    return (swept + dead) * 1000


def test_budget_one_default_cylinder():
    cfg = AirConfig()
    b = budget(cfg, [CYL])
    want = expected_cycle_l(CYL, cfg.working_psi)
    assert b.per_cycle_l["cyl"] == pytest.approx(want, rel=1e-9)
    assert b.stored_l == pytest.approx(2 * 0.574 * 55 * PSI / ATM, rel=1e-9)
    assert b.cycles_no_compressor == pytest.approx(b.stored_l / want)
    mid = COMPRESSORS["VIAIR 90C"].flow_cfm(105.0)
    assert b.compressor_l_per_match == pytest.approx(mid * CFM * 150 * 1000)
    assert b.cycles_per_match == pytest.approx((b.stored_l + b.compressor_l_per_match) / want)


def test_budget_without_compressor():
    b = budget(AirConfig(compressor="none"), [CYL])
    assert b.compressor_l_per_match == 0.0
    assert b.cycles_per_match == b.cycles_no_compressor


def write_robot(tmp_path, with_cylinder=True):
    parts = '[[part]]\nname = "drive"\nstock = "chassis"\n'
    if with_cylinder:
        parts += '[[part]]\nname = "cyl"\nstock = "cylinder"\nattach = "drive.top"\n'
    f = tmp_path / "robot.toml"
    f.write_text(parts)
    return f


def test_air_cli(tmp_path, capsys):
    main(["air", str(write_robot(tmp_path))])
    out = capsys.readouterr().out
    assert "cycles" in out.lower() and "UNCALIBRATED" in out and "psi" in out


def test_air_cli_no_cylinders(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main(["air", str(write_robot(tmp_path, False))])
    assert e.value.code == 1
    assert "has no pneumatic cylinders" in capsys.readouterr().err


def test_format_budget_ends_with_note():
    assert format_budget(budget(AirConfig(), [CYL])).endswith("see docs/pneumatics.md.")


def test_cycles_are_whole_cycles_rounded_down():
    """4.8 cycles is 4 that reach the working pressure, not 5: the budget never promises a cycle the air can't pay."""
    from scrimmage.pneumatics.budget import full_cycles
    assert [full_cycles(x) for x in (4.8, 4.0, 3.9999999999, 0.4, math.inf)] == ["4", "4", "4", "0", "unlimited"]
    cfg = AirConfig()
    b = budget(cfg, [CYL])
    out = format_budget(b)
    assert f"Cycles from the tanks alone: {math.floor(b.cycles_no_compressor)}\n" in out
    assert f"Cycles per match, tanks and compressor: {math.floor(b.cycles_per_match)}\n" in out
