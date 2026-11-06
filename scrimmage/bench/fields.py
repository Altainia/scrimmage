"""Plain-word help, units and choices for every bench key, read from `fields.toml`.

The Bench tab's forms show these beside each field. `FIELDS` is keyed `"<section>.<key>"`, as the parser's
`spec._SECTIONS` names them; a list or shaft section's key applies to every entry. `_in` inch variants are not
separate fields: `length` says a key has one.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..launcher.inputs import PRESETS
from ..motors import MOTORS
from .spec import _SECTIONS, MATERIALS, UNITS

CONTROLS = ("speed", "voltage", "free")
_FIXED = ("pieces", "materials", "treads", "motors", "controls", "units")  # choices that do not depend on the idea


@dataclass(frozen=True)
class Field:
    section: str
    key: str
    unit: str | None
    help: str
    choices: str | None  # a kind for `choices()`, or None
    length: bool  # also readable as `<key>_in`
    default: object | None


def _load() -> dict[str, Field]:
    raw = tomllib.loads(Path(__file__).with_name("fields.toml").read_text(encoding="utf-8"))
    out = {}
    for section, (keys, lengths) in _SECTIONS.items():
        for key in keys:
            v = raw[section][key]
            out[f"{section}.{key}"] = Field(section=section, key=key, unit=v.get("unit"), help=v["help"],
                                            choices=v.get("choices"), length=key in lengths, default=v.get("default"))
    return out


FIELDS = _load()

# A key's units kind (units.py) by the unit it gives, as the Bench tab's kindOf (static/bench.js): a length is
# "length" (in or mm), a flight's target "field" (ft or m); angles, times, rps and the rest read as the file has them.
KINDS = {"m": "length", "m/s": "speed", "kg": "mass", "N": "force", "N/m": "spring"}
FIELD_KEYS = ("flight.target_distance", "flight.target_height")
IN = 0.0254


def key_kind(dotted: str) -> tuple[str, float, str] | None:
    """A swept key's ("wheel.top.diameter", or its inch form "wheel.top.diameter_in") units kind, SI per one of its
    values and the key without `_in`, for showing its values in the Units setting (a sweep's chart and table); None
    for one shown as the file has it."""
    parts = str(dotted).split(".")
    section, k = parts[0], parts[-1]
    inches = k.endswith("_in")
    base_k = k[:-3] if inches else k
    f = FIELDS.get(f"{section}.{base_k}")
    if f is None or f.choices or (inches and not f.length):
        return None
    kind = "field" if f.unit == "m" and f"{section}.{base_k}" in FIELD_KEYS else KINDS.get(f.unit or "")
    if kind is None:
        return None
    return kind, IN if inches else 1.0, ".".join([*parts[:-1], base_k])


def choices(kind: str, raw: dict) -> list[str]:
    """The valid words for a `choices` kind; "shafts" and "outlets" are read from the idea's raw dict."""
    if kind == "pieces":
        return list(PRESETS["ball"])
    if kind == "materials":
        return list(MATERIALS)
    if kind == "treads":
        return list(PRESETS["wheels"])
    if kind == "motors":
        return list(MOTORS)
    if kind == "shafts":
        return list(raw.get("shaft", {}))
    if kind == "outlets":
        return [o["name"] for o in raw.get("outlet", []) if isinstance(o, dict) and "name" in o]
    if kind == "controls":
        return list(CONTROLS)
    if kind == "units":
        return list(UNITS)
    raise ValueError(f"unknown choices kind {kind!r}")


def fields_payload() -> dict:
    """What the page needs: every field's help, and the choice lists that do not depend on the idea."""
    return {"fields": {d: {"unit": f.unit, "help": f.help, "choices": f.choices, "length": f.length,
                           "default": f.default} for d, f in FIELDS.items()},
            "choices": {k: choices(k, {}) for k in _FIXED}}
