"""The stock part library: Python builders with typed, documented parameters.

A builder takes validated parameters and returns part.toml-shaped data; build_stock() validates both."""

from __future__ import annotations

import importlib
import math
from dataclasses import dataclass
from typing import Any, Callable

from ..assemble import TREAD_DAMPING, TREAD_STIFFNESS
from ..model import Part, PartError, part_from_dict

KINDS = ("float", "int", "str", "bool", "ratio", "pair", "vec3", "text")


@dataclass(frozen=True)
class Param:
    """One parameter of a stock part.

    kind: float | int | str | bool | ratio (a named choice or a number > 0) | pair ([min, max], min < max) | vec3 ([x, y, z], three finite numbers)
    | text (any string, empty allowed; the builder checks what it means).
    min: inclusive lower bound. max: inclusive upper bound, or a callable(checked) -> float for a limit that
    depends on other parameters. The callable receives a dict of the parameters declared before this one, already
    checked and converted (not the later ones), so declare dependent parameters after the ones they depend on.
    """
    name: str
    unit: str
    default: Any
    doc: str
    kind: str = "float"
    min: float | None = None
    max: float | Callable[[dict], float] | None = None
    choices: tuple | None = None
    positive: bool = False

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"{self.name}: unknown kind {self.kind!r}; kinds: {', '.join(KINDS)}")
        if self.kind in ("str", "ratio") and not self.choices:
            raise ValueError(f"{self.name}: kind {self.kind!r} needs choices")
        if self.choices is not None:
            object.__setattr__(self, "choices", tuple(self.choices))


@dataclass(frozen=True)
class Stock:
    name: str
    doc: str
    params: tuple[Param, ...]
    build: Callable[[dict], dict]
    slots: tuple[str, ...]
    mounts: tuple[str, ...]
    note: str = ""


# A drivetrain's wheels: their geoms say tread = true, and the assembler reads these two (parts/assemble.py).
TREAD_PARAMS = (
    Param("tread_stiffness", "N/m", TREAD_STIFFNESS, "each wheel's tread stiffness (estimate: TPU or Colson-like "
          "tread on a 4 in wheel, about 1e5 to 3e5)", positive=True),
    Param("tread_damping", "", TREAD_DAMPING, "the tread contact's damping ratio (1 is critical: no bounce)",
          positive=True),
)

REGISTRY: dict[str, Stock] = {}


def register(stock: Stock) -> Stock:
    REGISTRY[stock.name] = stock
    return stock


def _finite(x) -> bool:
    """math.isfinite, but False for an int too large for a float (which isfinite would raise on)."""
    try:
        return math.isfinite(x)
    except OverflowError:
        return False


def _check(p: Param, v: Any, where: str, params: dict) -> Any:
    """Validate one value. `params` holds the already-checked parameters declared before p (used by callable max)."""
    unit = f" ({p.unit})" if p.unit else ""

    def bad(msg):
        return PartError(f"{where}: {p.name} {msg}")

    if p.kind in ("float", "int"):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (p.kind == "int" and not isinstance(v, int)):
            raise bad(f"must be {'a whole number' if p.kind == 'int' else 'a number'}{unit}, not {v!r}")
        if not _finite(v):
            raise bad(f"must be a finite number{unit}, not {v!r}")
        if p.positive and not v > 0:
            raise bad(f"must be greater than 0, got {v}")
        if p.min is not None and v < p.min:
            raise bad(f"must be at least {p.min}, got {v}")
        hi = p.max(params) if callable(p.max) else p.max
        if hi is not None and v > hi:
            raise bad(f"must be at most {round(hi, 6)}, got {v}")
        return float(v) if p.kind == "float" else v
    if p.kind == "bool":
        if not isinstance(v, bool):
            raise bad(f"must be true or false, not {v!r}")
        return v
    if p.kind == "str":
        if not isinstance(v, str) or v not in p.choices:
            raise bad(f"must be one of {', '.join(map(str, p.choices))}, not {v!r}")
        return v
    if p.kind == "text":
        if not isinstance(v, str):
            raise bad(f"must be text in quotes, not {v!r}")
        return v
    if p.kind == "ratio":
        if isinstance(v, str) and v in p.choices:
            return v
        if not isinstance(v, bool) and isinstance(v, (int, float)) and _finite(v) and v > 0:
            return float(v)
        raise bad(f"must be one of {', '.join(map(str, p.choices))} or a number greater than 0, not {v!r}")
    if p.kind == "pair":
        ok = (isinstance(v, (list, tuple)) and len(v) == 2
              and all(isinstance(x, (int, float)) and not isinstance(x, bool) and _finite(x) for x in v)
              and v[0] < v[1])
        if not ok:
            raise bad(f"must be [min, max] with min < max{unit}, not {v!r}")
        return (float(v[0]), float(v[1]))
    if p.kind == "vec3":
        ok = (isinstance(v, (list, tuple)) and len(v) == 3
              and all(isinstance(x, (int, float)) and not isinstance(x, bool) and _finite(x) for x in v))
        if not ok:
            raise bad(f"must be [x, y, z], three numbers{unit}, not {v!r}")
        return [float(x) for x in v]
    raise AssertionError(p.kind)


FAMILIES = ("chassis", "mechanisms", "swerve", "differential", "climber")


def _load_all() -> None:
    """Import every stock family module (importing registers its parts). Families added by later tasks may
    not exist yet."""
    for mod in FAMILIES:
        try:
            importlib.import_module(f"{__name__}.{mod}")
        except ModuleNotFoundError as e:
            if e.name != f"{__name__}.{mod}":
                raise


def stock_params(name: str, given: dict, where: str) -> dict:
    """The stock part's parameters, checked and converted, with the defaults filled in."""
    _load_all()
    stock = REGISTRY.get(name)
    if stock is None:
        raise PartError(f"{where}: unknown stock part {name!r}; stock parts: {', '.join(sorted(REGISTRY))}")
    names = [p.name for p in stock.params]
    for k in given:
        if k not in names:
            raise PartError(f"{where}: unknown parameter {k!r} for {name}; parameters: {', '.join(names)}")
    checked: dict = {}
    for p in stock.params:   # each parameter sees only the checked parameters declared before it
        checked[p.name] = _check(p, given.get(p.name, p.default), where, checked)
    return checked


def build_stock(name: str, given: dict, where: str) -> Part:
    checked = stock_params(name, given, where)
    return part_from_dict(REGISTRY[name].build(checked), f"{where} (stock {name})", None)


def _constraints(p: Param) -> str:
    parts = []
    if p.kind == "int":
        parts.append("a whole number")
    if p.kind == "ratio":
        parts.append(f"one of {', '.join(map(str, p.choices))} or a number greater than 0")
    elif p.kind == "str":
        parts.append(f"one of {', '.join(map(str, p.choices))}")
    if p.kind == "pair":
        parts.append("[min, max] with min < max")
    if p.kind == "vec3":
        parts.append("[x, y, z]")
    if p.positive:
        parts.append("greater than 0")
    if p.min is not None:
        parts.append(f"at least {p.min}")
    if callable(p.max):
        parts.append("limited by other parameters")
    elif p.max is not None:
        parts.append(f"at most {p.max}")
    return ", ".join(parts)


def describe(name: str | None) -> str:
    _load_all()
    if name is None:
        return "\n".join(f"{s.name:20} {s.doc}" for s in sorted(REGISTRY.values(), key=lambda s: s.name))
    s = REGISTRY.get(name)
    if s is None:
        raise PartError(f"unknown stock part {name!r}; stock parts: {', '.join(sorted(REGISTRY))}")
    lines = [f"{s.name}: {s.doc}", ""]
    if s.note:
        lines += [s.note, ""]
    lines.append("parameters:")
    for p in s.params:
        value = repr(list(p.default) if p.kind in ("pair", "vec3") else p.default)
        if p.unit:
            value += f" {p.unit}"
        constraints = _constraints(p)
        if constraints:
            value += f" ({constraints})"
        lines.append(f"  {p.name} = {value}: {p.doc}")
    lines += ["", f"slots: {', '.join(s.slots) or 'none'}", f"mounts: {', '.join(s.mounts) or 'none'}"]
    return "\n".join(lines)
