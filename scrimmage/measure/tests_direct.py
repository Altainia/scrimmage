"""The direct tests: each trial's readings give a value by a formula a student can work by hand.

- **size** (a ball): `mass`, and `diameter` from a tape around the ball (circumference / pi) or read directly.
- **squeeze** (a ball): `stiffness_n`, the force to squeeze the ball by 25% of its diameter between flat boards.
  The ideal trial squeezes it to 75% of its diameter; the reading is the scale's force minus the top board's weight
  (`board_<unit>`). A trial squeezed to another height (`squeezed_to_<unit>`, the gap between the boards) is
  converted to 25% with the ball's `stiffness_exponent` (force grows as squeeze^exponent; 1.5 unless the base
  preset says otherwise): F25 = F * (0.25 / squeeze)^exponent. That needs the ball's diameter: the file's own size
  trials, else its base preset's.
- **drop** (a ball): `restitution = sqrt(bounce / drop)`, both heights to the bottom of the ball. The model's foam
  is tuned for an 18 in drop on a hard floor, so another height, or a carpet floor (`floor = "..."`), is warned.
- **sled** (a friction): `friction = pull / sled weight`, the spring scale read at a steady slide. The setup names
  the two materials: `sliding` (on the sled's bottom) and `on` (what it slides on).

A test's settings (the board, the squeeze height, the drop height, the sled's weight) may be given once in its
`[<test>_setup]` table, or in a trial for that trial alone. Every value is averaged by stats.summary.
"""

import dataclasses
import math

from ..launcher.inputs import PRESETS
from .file import MeasureError, MeasureFile
from .stats import Summary, summary
from .units import UNITS, reading, unit_of

DROP_HEIGHT = 18 * 0.0254  # the drop the model's foam is tuned for (m)
DROP_TOLERANCE = 0.05  # a drop height further than this fraction from 18 in is warned
IDEAL_SQUEEZE = 0.25  # the fraction of the diameter stiffness_n is defined at

# Each test's readings: name -> quantity. Trial readings are per trial; settings may also go in [<test>_setup].
_TRIAL = {
    "size": {"mass": "mass", "circumference": "length", "diameter": "length"},
    "squeeze": {"force": "force"},
    "drop": {"bounce": "length"},
    "sled": {"pull": "force"},
}
_SETTING = {
    "size": {},
    "squeeze": {"board": "force", "squeezed_to": "length"},
    "drop": {"drop": "length"},
    "sled": {"sled": "force"},
}
_SETUP_TEXT = {"size": (), "squeeze": (), "drop": ("floor",), "sled": ("sliding", "on")}


def _keys(names: dict[str, str]) -> str:
    return ", ".join(f"{n}_<unit>" if n not in UNITS[q] else n for n, q in names.items())


def _check_keys(mf: MeasureFile, label: str, table: dict, names: dict[str, str], text: tuple[str, ...] = ()):
    for key in table:
        if key in text or any(unit_of(key, n) is not None for n in names):
            continue
        valid = _keys(names) + "".join(f", {t}" for t in text)
        raise MeasureError(f"{mf.path}: unknown reading '{key}' in {label}; it takes {valid}")


class _Test:
    """One test's trials and setup, read with errors that name the file, the table and the trial."""

    def __init__(self, mf: MeasureFile, test: str):
        self.mf, self.test = mf, test
        self.rows = mf.trials[test]
        self.setup = mf.settings.get(f"{test}_setup", {})
        _check_keys(mf, f"[{test}_setup]", self.setup, _SETTING[test], _SETUP_TEXT[test])
        for i, row in enumerate(self.rows):
            _check_keys(mf, self.label(i), row, _TRIAL[test] | _SETTING[test])

    def label(self, i: int) -> str:
        return f"[[{self.test}]] trial {i + 1}"

    def where(self, i: int) -> str:
        return f"{self.mf.path}: {self.label(i)}"

    def err(self, i: int, msg: str) -> MeasureError:
        return MeasureError(f"{self.where(i)} {msg}")

    def trial(self, i: int, name: str, *, required: bool = True) -> float | None:
        v = reading(self.rows[i], _TRIAL[self.test][name], name, where=self.where(i), required=required)
        if v is not None and v < 0:
            raise self.err(i, f"has a negative {name} reading")
        return v

    def setting(self, i: int, name: str, *, required: bool = True) -> float | None:
        """A setting from trial i, else from [<test>_setup]."""
        quantity = _SETTING[self.test][name]
        v = reading(self.rows[i], quantity, name, where=self.where(i), required=False)
        if v is None:
            v = reading(self.setup, quantity, name, where=f"{self.mf.path}: [{self.test}_setup]", required=False)
        if v is None and required:
            raise self.err(i, f"has no {name}: give it once in [{self.test}_setup] or in the trial, as "
                              f"{name}_<unit>")
        if v is not None and v < 0:
            raise self.err(i, f"has a negative {name} reading")
        return v

    def text(self, key: str, *, required: bool = True) -> str | None:
        if key not in self.setup:
            if required:
                raise MeasureError(f"{self.mf.path}: [{self.test}_setup] is missing required key '{key}'")
            return None
        v = self.setup[key]
        if not isinstance(v, str):
            raise MeasureError(f"{self.mf.path}: [{self.test}_setup] {key} must be text, not {v!r}")
        return v


def _with(s: Summary, warnings: list[str]) -> Summary:
    return dataclasses.replace(s, warnings=s.warnings + warnings) if warnings else s


def _size(t: _Test) -> dict:
    mass, diameter = [], []
    for i, row in enumerate(t.rows):
        m = t.trial(i, "mass", required=False)
        c = t.trial(i, "circumference", required=False)
        d = t.trial(i, "diameter", required=False)
        if c is not None and d is not None:
            given = [k for k in row if unit_of(k, "circumference") is not None or unit_of(k, "diameter") is not None]
            raise t.err(i, f"gives {' and '.join(given)}: give a circumference or a diameter, not both")
        if m is None and c is None and d is None:
            raise t.err(i, f"has no reading: write {_keys(_TRIAL['size'])}")
        if m is not None:
            mass.append(m)
        if c is not None or d is not None:
            diameter.append(c / math.pi if c is not None else d)
    out = {}
    if mass:
        out["mass"] = (summary(mass), mass)
    if diameter:
        out["diameter"] = (summary(diameter), diameter)
    return out


def _preset(mf: MeasureFile) -> dict:
    return PRESETS["ball"].get(mf.base, {}) if mf.base else {}


def _squeeze(t: _Test, diameter: float | None) -> dict:
    exponent = _preset(t.mf).get("stiffness_exponent", 1.5)
    values = []
    for i in range(len(t.rows)):
        force = t.trial(i, "force") - (t.setting(i, "board", required=False) or 0.0)
        if force <= 0:
            raise t.err(i, "reads less than the top board weighs: the force is the scale's reading minus the board")
        height = t.setting(i, "squeezed_to", required=False)
        if height is not None:
            if diameter is None:
                raise t.err(i, "is squeezed to a height, which needs the ball's diameter: add [[size]] trials or "
                               "a base preset in [measure]")
            squeeze = 1.0 - height / diameter
            if not 0 < squeeze < 1:
                raise t.err(i, f"is squeezed to {height / 0.0254:.3g} in, which isn't less than the ball's "
                               f"diameter ({diameter / 0.0254:.3g} in)")
            force *= (IDEAL_SQUEEZE / squeeze) ** exponent
        values.append(force)
    return {"stiffness_n": (summary(values), values)}


def _drop(t: _Test) -> dict:
    values, heights = [], []
    for i in range(len(t.rows)):
        drop, bounce = t.setting(i, "drop"), t.trial(i, "bounce")
        if drop <= 0:
            raise t.err(i, "has a drop height of 0")
        if bounce > drop:
            raise t.err(i, "bounces higher than the drop: measure both to the bottom of the ball")
        values.append(math.sqrt(bounce / drop))
        heights.append(drop)
    warnings = []
    off = sorted({round(h / 0.0254, 1) for h in heights if abs(h / DROP_HEIGHT - 1) > DROP_TOLERANCE})
    if off:
        warnings.append(f"the ball was dropped from {' and '.join(f'{h:g}' for h in off)} in: the model's foam is "
                        "tuned for an 18 in drop, so drop it from 18 in if you can")
    floor = t.text("floor", required=False)
    if floor is None:
        warnings.append("say what floor it bounced on (floor = \"concrete\" in [drop_setup]): the model expects a "
                        "hard floor")
    elif "carpet" in floor.lower():
        warnings.append("a ball bounces lower on carpet than on the hard floor the model expects: drop it on "
                        "concrete, tile or wood")
    return {"restitution": (_with(summary(values), warnings), values)}


def _sled(t: _Test) -> dict:
    t.text("sliding")
    t.text("on")
    values = []
    for i in range(len(t.rows)):
        weight = t.setting(i, "sled")
        if weight <= 0:
            raise t.err(i, "has a sled weight of 0")
        values.append(t.trial(i, "pull") / weight)
    return {"friction": (summary(values), values)}


def values(mf: MeasureFile) -> dict[str, tuple[Summary, list[float]]]:
    """Each measured field (mass, diameter, stiffness_n, restitution, friction) with its summary and per-trial values,
    in SI. A file with only fitted tests (shot, stroke) has none."""
    out: dict[str, tuple[Summary, list[float]]] = {}
    if "size" in mf.trials:
        out |= _size(_Test(mf, "size"))
    if "squeeze" in mf.trials:
        diameter = out["diameter"][0].mean if "diameter" in out else _preset(mf).get("diameter")
        out |= _squeeze(_Test(mf, "squeeze"), diameter)
    if "drop" in mf.trials:
        out |= _drop(_Test(mf, "drop"))
    if "sled" in mf.trials:
        out |= _sled(_Test(mf, "sled"))
    return out
