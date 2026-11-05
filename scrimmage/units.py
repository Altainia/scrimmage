"""Display units: the one table of what each kind of quantity is shown in, per unit system, and its formatter.

Values are SI (meters, kilograms, newtons, pascals, kg/m^3, N/m, m/s), except angles, which are degrees, as the
files keep them. Each display or input site names its kind (`length` for anything on a robot or mechanism, `field`
for poses, field picks and drive distances; see docs/superpowers/specs/2026-11-04-display-units-design.md, U1):

    fmt(0.1016, "length")           -> "4 in" (imperial) or "101.6 mm" (metric)
    from_display(12.5, "field")     -> 3.81 (what a typed 12.5 ft saves as)

The page's formatter (static/units.js) uses this same table, sent by table_json() in GET /api/settings, so the two
can't drift apart. Angle, time, current and voltage read the same in both systems. Machine-readable output (MCP
results, report.json, CSVs, files) stays SI and never comes through here.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import NamedTuple

from .settings import SYSTEMS, current_units

KINDS = ("length", "field", "speed", "mass", "force", "torque", "pressure", "density", "spring", "angle", "time",
         "current", "voltage")

# the SI value of one of each (the same factors as measure/units.py)
IN, FT, LB, LBF = 0.0254, 0.3048, 0.45359237, 4.4482216152605
G = 9.80665


class Unit(NamedTuple):
    name: str
    factor_from_si: float   # shown = SI value * factor_from_si
    decimals: int           # the most decimals shown (trailing zeros are trimmed)


TABLE: dict[str, dict[str, Unit]] = {
    "imperial": {
        "length": Unit("in", 1 / IN, 3),
        "field": Unit("ft", 1 / FT, 2),
        "speed": Unit("ft/s", 1 / FT, 2),
        "mass": Unit("lb", 1 / LB, 2),
        "force": Unit("lbf", 1 / LBF, 2),
        "torque": Unit("in·lbf", 1 / (LBF * IN), 1),
        "pressure": Unit("psi", IN * IN / LBF, 1),
        "density": Unit("lb/in³", IN ** 3 / LB, 4),
        "spring": Unit("lbf/in", IN / LBF, 2),
    },
    "metric": {
        "length": Unit("mm", 1000.0, 1),
        "field": Unit("m", 1.0, 3),
        "speed": Unit("m/s", 1.0, 2),
        "mass": Unit("kg", 1.0, 3),
        "force": Unit("N", 1.0, 1),
        "torque": Unit("N·m", 1.0, 2),
        "pressure": Unit("kPa", 0.001, 1),
        "density": Unit("g/cm³", 0.001, 2),
        "spring": Unit("N/m", 1.0, 0),
    },
}
for _system in SYSTEMS:   # the same in both systems
    TABLE[_system].update(angle=Unit("°", 1.0, 1), time=Unit("s", 1.0, 2), current=Unit("A", 1.0, 1),
                          voltage=Unit("V", 1.0, 2))

# What a typed value may end with for each unit, besides its name (compared ignoring case and spaces; see units.js
# parse). A unit of the other system is refused with a plain message, never converted.
ALIASES: dict[str, list[str]] = {
    "in": ["in", "inch", "inches", '"'], "mm": ["mm"], "cm": ["cm"], "ft": ["ft", "foot", "feet", "'"], "m": ["m"],
    "ft/s": ["ft/s", "fps"], "m/s": ["m/s"], "lb": ["lb", "lbs"], "kg": ["kg"], "lbf": ["lbf", "lb", "lbs"],
    "N": ["N"], "in·lbf": ["in·lbf", "in-lbf", "inlbf", "in*lbf", "in·lb", "in-lb", "inlb", "lbf·in", "lbf-in"],
    "N·m": ["N·m", "Nm", "N-m", "N*m"], "psi": ["psi"], "kPa": ["kPa"],
    "lb/in³": ["lb/in³", "lb/in^3", "lb/in3"], "g/cm³": ["g/cm³", "g/cm^3", "g/cm3"],
    "lbf/in": ["lbf/in", "lb/in"], "N/m": ["N/m"], "°": ["°", "deg", "degrees"], "s": ["s", "sec"],
    "A": ["A"], "V": ["V"],
}

# The length units a length or field box takes, by system: the SI value of one. A box takes any of its system's
# ("6 in" in a tolerance shown in ft, "2 cm" in one shown in mm); the other system's are refused (units.js parse).
LENGTHS: dict[str, dict[str, float]] = {"imperial": {"in": IN, "ft": FT}, "metric": {"mm": 0.001, "cm": 0.01, "m": 1.0}}

# The units a file's key can name (`diameter_in`, a measure file's `force_lb`), by quantity: the SI value (degrees
# for an angle) of one. The page writes such a key in its own unit whatever the setting (U3; units.js toKey).
KEY_UNITS: dict[str, dict[str, float]] = {
    "length": {"in": IN, "mm": 0.001, "cm": 0.01, "m": 1.0},
    "mass": {"g": 0.001, "kg": 1.0, "oz": 0.028349523125, "lb": LB},
    "force": {"n": 1.0, "lb": LBF, "kg": G, "g": 0.001 * G, "oz": 0.028349523125 * G},
    "angle": {"deg": 1.0},
    "time": {"s": 1.0},
}


# Machine-readable text (a sweep's or spread's table.csv, MCP results) is written in one fixed system, whatever the
# setting: Imperial, the Test tab's words before the setting existed (inches, psi). `with fixed(MACHINE):` makes it
# the default for the code inside (this thread or task only), so the words built there needn't be passed a system.
MACHINE = "imperial"
_fixed: ContextVar[str | None] = ContextVar("units_fixed", default=None)


@contextmanager
def fixed(system: str):
    """Format in `system` by default inside the `with`, instead of the Units setting."""
    if system not in TABLE:
        raise ValueError(f"unknown unit system {system!r}; imperial or metric")
    token = _fixed.set(system)
    try:
        yield
    finally:
        _fixed.reset(token)


def _unit(kind: str, system: str | None) -> Unit:
    system = (_fixed.get() or current_units()) if system is None else system
    if system not in TABLE:
        raise ValueError(f"unknown unit system {system!r}; imperial or metric")
    if kind not in TABLE[system]:
        raise ValueError(f"unknown kind {kind!r}; one of {', '.join(KINDS)}")
    return TABLE[system][kind]


def _clean(x: float) -> float:
    """`x` to 15 significant digits: no float noise from a conversion (3.8100000000000005 -> 3.81)."""
    return float(f"{x:.15g}") if math.isfinite(x) else x


def unit_name(kind: str, system: str | None = None) -> str:
    return _unit(kind, system).name


def to_display(value_si: float, kind: str, system: str | None = None) -> float:
    return _clean(value_si * _unit(kind, system).factor_from_si)


def from_display(value: float, kind: str, system: str | None = None) -> float:
    """A value shown or typed in the system's unit, in SI."""
    return _clean(value / _unit(kind, system).factor_from_si)


def number(shown: float, decimals: int) -> str:
    """`shown` to `decimals` places, trailing zeros trimmed, never "-0". It rounds the number as written (its
    shortest form: 6.35, not the binary 6.34999...), a tie away from zero, as units.js does, so the page and the
    server write the same text: 0.25 in is 6.4 mm."""
    d = Decimal(repr(abs(shown)))
    with localcontext() as ctx:   # enough digits for any finite float (the default 28 can't quantize 1e25)
        ctx.prec = max(28, d.adjusted() + decimals + 2)
        q = d.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    text = f"{q:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text if text == "0" or shown >= 0 else "-" + text


def fmt(value_si: float | None, kind: str, system: str | None = None, fewer: int = 0) -> str:
    """`value_si` in the system's unit (default: the setting), with its unit name: "4 in", "101.6 mm", "90°".
    `fewer` shows that many fewer decimals than the table's (never below none), for a reading rather than a
    setting: a seated depth's "1.5 in" or "38 mm"."""
    unit = _unit(kind, system)
    if value_si is None:
        return ""
    if not math.isfinite(value_si):
        return str(value_si)
    text = number(to_display(value_si, kind, system), max(0, unit.decimals - fewer))
    return f"{text}{unit.name}" if unit.name == "°" else f"{text} {unit.name}"


def table_json() -> dict:
    """The table for the page (static/units.js)."""
    return {
        "systems": list(SYSTEMS),
        "kinds": list(KINDS),
        "units": {s: {k: {"name": u.name, "factor": u.factor_from_si, "decimals": u.decimals}
                      for k, u in TABLE[s].items()} for s in SYSTEMS},
        "aliases": ALIASES,
        "lengths": LENGTHS,
        "keys": KEY_UNITS,
    }
