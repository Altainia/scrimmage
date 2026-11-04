"""Readings and their units: every reading's key ends in the unit it was read in (`force_lb`, `drop_mm`).

A reading is found by its name and the kind of quantity it is: `reading(row, "length", "bounce")` finds the one key
`bounce_<unit>` in the row and returns it in SI (meters, kilograms, newtons, seconds). A scale's reading in pounds or
kilograms is a force when the quantity is a force (one pound-force, one kilogram under standard gravity). A weight
(a tare, a sled) is a force read on a bathroom or kitchen scale: it takes the force units and the mass units g and
oz too, each turned into newtons with standard gravity. Frame counts take the bare key `frames` (or
`<name>_frames`). A reading with no unit, an unknown unit, or the same reading
given in two units is an error that lists the units allowed.
"""

import math

from .file import MeasureError

G = 9.80665  # standard gravity (m/s^2): a scale reading in a mass unit is that mass's weight

# The SI value of one of each unit, by quantity.
UNITS: dict[str, dict[str, float]] = {
    "force": {"n": 1.0, "lb": 4.4482216152605, "kg": G},
    "weight": {"n": 1.0, "lb": 4.4482216152605, "kg": G, "g": 0.001 * G, "oz": 0.028349523125 * G},
    "mass": {"g": 0.001, "kg": 1.0, "oz": 0.028349523125, "lb": 0.45359237},
    "length": {"in": 0.0254, "mm": 0.001, "cm": 0.01, "m": 1.0},
    "time": {"s": 1.0},
    "frames": {"frames": 1.0},
}


def _choices(name: str, quantity: str) -> str:
    keys = [name if unit == name else f"{name}_{unit}" for unit in UNITS[quantity]]
    return keys[0] if len(keys) == 1 else f"{', '.join(keys[:-1])} or {keys[-1]}"


def unit_of(key: str, name: str) -> str | None:
    """The unit part of `key` if it is a reading called `name` (`force_lb` -> "lb"; a bare `name` -> ""), else None."""
    if key == name:
        return ""
    if key.startswith(name + "_") and "_" not in key[len(name) + 1:]:
        return key[len(name) + 1:]
    return None


def reading(row: dict, quantity: str, name: str | None = None, *, where: str = "this trial",
            required: bool = True) -> float | None:
    """The reading `name` (default: the quantity's own name) of `row`, in SI. Left out, it is an error, or None when
    not `required`. `where` begins the error sentences ("fuel.measure.toml: [[squeeze]] trial 2")."""
    if quantity not in UNITS:
        raise ValueError(f"unknown quantity {quantity!r}; one of {', '.join(UNITS)}")
    name = name or quantity
    units = UNITS[quantity]
    found = [(k, u) for k in row if (u := unit_of(k, name)) is not None]
    for key, unit in found:
        if unit == "" and name not in units:
            raise MeasureError(f"{where}: {key} needs its unit in its key: write {_choices(name, quantity)}")
        if unit and unit not in units and unit.lower() in units:
            raise MeasureError(f"{where}: {key}: units are lowercase: write {name}_{unit.lower()}")
        if unit and unit not in units:
            raise MeasureError(f"{where}: {key}: '{unit}' isn't a unit of {quantity}; "
                               f"write {_choices(name, quantity)}")
    if len(found) > 1:
        raise MeasureError(f"{where} gives {' and '.join(k for k, _ in found)}: give {name} once, in one unit")
    if not found:
        if not required:
            return None
        raise MeasureError(f"{where} has no {name} reading: write {_choices(name, quantity)}")
    key, unit = found[0]
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MeasureError(f"{where}: {key} must be a number, not {value!r}")
    return float(value) * units[unit or name]
