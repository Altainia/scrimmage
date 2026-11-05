"""The shared unit table and the Python formatter (scrimmage/units.py); the page's units.js is tested in
tests/test_display_units_browser.py against the same table."""
import math

import pytest

from scrimmage import units
from scrimmage.units import KINDS, TABLE, fmt, from_display, table_json, to_display, unit_name

# (kind, SI value, imperial text, metric text): the spec's examples first
CASES = [
    ("length", 0.1016, "4 in", "101.6 mm"),
    ("length", 0.00635, "0.25 in", "6.4 mm"),
    ("field", 3.81, "12.5 ft", "3.81 m"),
    ("speed", 3.048, "10 ft/s", "3.05 m/s"),
    ("mass", 0.45359237 * 120, "120 lb", "54.431 kg"),
    ("force", 4.4482216152605 * 5, "5 lbf", "22.2 N"),
    ("torque", 1.0, "8.9 in·lbf", "1 N·m"),
    ("pressure", 60 * 6894.757293168361, "60 psi", "413.7 kPa"),
    ("density", 2700.0, "0.0975 lb/in³", "2.7 g/cm³"),
    ("spring", 175.12683524647639, "1 lbf/in", "175 N/m"),
    ("angle", 90.0, "90°", "90°"),
    ("time", 1.5, "1.5 s", "1.5 s"),
    ("current", 40.0, "40 A", "40 A"),
    ("voltage", 12.0, "12 V", "12 V"),
]


def test_every_kind_has_a_unit_in_both_systems():
    assert set(TABLE) == {"imperial", "metric"}
    for system in TABLE:
        assert set(TABLE[system]) == set(KINDS)
    assert {c[0] for c in CASES} == set(KINDS)


@pytest.mark.parametrize("kind,si,imperial,metric", CASES)
def test_fmt_both_systems(kind, si, imperial, metric):
    assert fmt(si, kind, "imperial") == imperial
    assert fmt(si, kind, "metric") == metric


def test_fmt_uses_the_setting(units):
    assert fmt(0.1016, "length") == "4 in"          # the default, imperial
    units("metric")
    assert fmt(0.1016, "length") == "101.6 mm"
    assert unit_name("field") == "m"


def test_fmt_trims_zeros_and_rounds_within_the_decimals():
    assert fmt(0.0254 * 2.5, "length", "imperial") == "2.5 in"
    assert fmt(0.0254 * 0.1234567, "length", "imperial") == "0.123 in"
    assert fmt(0.0, "length", "metric") == "0 mm"
    assert fmt(-0.00001, "length", "metric") == "0 mm"       # never "-0"
    assert fmt(-0.1016, "length", "imperial") == "-4 in"
    assert fmt(1e6, "field", "metric") == "1000000 m"          # huge values are written out, not 1e+06
    assert fmt(1e6, "field", "imperial") == "3280839.9 ft"
    # past the default 28-digit Decimal context: still written out, not an InvalidOperation
    assert fmt(1e25, "field", "metric") == "10000000000000000000000000 m"
    assert fmt(-1e300, "length", "metric") == "-1" + "0" * 303 + " mm"
    # a tie (as written) rounds away from zero, in units.js too, so the page and the server agree
    assert fmt(0.125, "time", "metric") == "0.13 s"
    assert fmt(-0.125, "time", "metric") == "-0.13 s"


def test_fmt_of_nothing_or_not_a_number():
    assert fmt(None, "length") == ""
    assert fmt(float("nan"), "length") == "nan"


def test_round_trips():
    for system in TABLE:
        for kind in KINDS:
            unit = TABLE[system][kind]
            for shown in (0.0, 1.0, 4.0, 0.25, 12.5, -3.5, 101.6, 1234.5):
                si = from_display(shown, kind, system)
                assert to_display(si, kind, system) == pytest.approx(shown, rel=1e-12, abs=1e-12)
                # typed in the shown decimals, it reads back as typed
                typed = round(shown, unit.decimals)
                text = f"{typed:.{unit.decimals}f}".rstrip("0").rstrip(".") if unit.decimals else f"{typed:.0f}"
                assert fmt(from_display(typed, kind, system), kind, system).replace(" ", "").removesuffix(
                    unit.name.replace(" ", "")) == text, (system, kind, shown)


def test_from_display_writes_clean_numbers():
    # what a typed value saves as: no float noise in the file (12.5 ft is 3.81 m, not 3.8100000000000005)
    assert from_display(12.5, "field", "imperial") == 3.81
    assert from_display(4, "length", "imperial") == 0.1016
    assert from_display(101.6, "length", "metric") == 0.1016
    assert from_display(60, "pressure", "imperial") == pytest.approx(413685.43759)


def test_unit_names():
    assert [unit_name(k, "imperial") for k in ("length", "field", "speed", "mass", "force", "torque", "pressure",
                                               "density", "spring")] == \
        ["in", "ft", "ft/s", "lb", "lbf", "in·lbf", "psi", "lb/in³", "lbf/in"]
    assert [unit_name(k, "metric") for k in ("length", "field", "speed", "mass", "force", "torque", "pressure",
                                             "density", "spring")] == \
        ["mm", "m", "m/s", "kg", "N", "N·m", "kPa", "g/cm³", "N/m"]


def test_unknown_kind_or_system():
    with pytest.raises(ValueError, match="unknown kind 'furlong'"):
        fmt(1.0, "furlong", "imperial")
    with pytest.raises(ValueError, match="imperial or metric"):
        fmt(1.0, "length", "cubits")


def test_factors_match_the_measure_files():
    from scrimmage.measure.units import UNITS
    assert 1 / TABLE["imperial"]["length"].factor_from_si == pytest.approx(UNITS["length"]["in"], rel=1e-15)
    assert 1 / TABLE["imperial"]["mass"].factor_from_si == pytest.approx(UNITS["mass"]["lb"], rel=1e-15)
    assert 1 / TABLE["imperial"]["force"].factor_from_si == pytest.approx(UNITS["force"]["lb"], rel=1e-15)
    for quantity, by_unit in units.KEY_UNITS.items():
        measured = UNITS.get("weight" if quantity == "force" else quantity)   # a force can be read in g or oz
        for unit, si in by_unit.items():
            if measured is not None:
                assert si == pytest.approx(measured[unit], rel=1e-15), (quantity, unit)


def test_table_json_is_what_the_page_needs():
    t = table_json()
    assert t["systems"] == ["imperial", "metric"] and t["kinds"] == list(KINDS)
    assert t["units"]["imperial"]["length"] == {"name": "in", "factor": 1 / 0.0254, "decimals": 3}
    assert "mm" in t["aliases"]["mm"] and '"' in t["aliases"]["in"]
    assert t["keys"]["length"]["in"] == 0.0254
    assert all(math.isfinite(u["factor"]) for s in t["units"].values() for u in s.values())
