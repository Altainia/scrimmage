"""The direct tests: each trial's readings give a value by a formula a student can work by hand.

- **size** (a ball): `mass`, and `diameter` from a tape around the ball (circumference / pi) or read directly.
- **squeeze** (a ball): `stiffness_n`, the force to squeeze the ball by 25% of its diameter between flat boards.
  The ideal trial squeezes it to 75% of its diameter. The top board's weight presses on the ball too, so it is part
  of the force: the bottom board and the ball go on the scale, the scale is zeroed, then the top board goes on and
  is pressed, and the reading is the force. A scale that can't be zeroed gives `tare_<unit>`, its reading with only
  the bottom board and the ball on it, which is subtracted. A trial squeezed to another height
  (`squeezed_to_<unit>`, the gap between the boards) is converted to 25% with the ball's `stiffness_exponent`
  (force grows as squeeze^exponent; 1.5 unless the base preset says otherwise): F25 = F * (0.25 / squeeze)^exponent.
  That needs the ball's diameter: the file's own size trials when they are good, else its base preset's.
- **drop** (a ball): `restitution = sqrt(bounce / drop)`, both heights to the bottom of the ball. The model's foam
  is tuned for an 18 in drop on a hard floor, so another height, or a soft floor (`floor = "..."`: carpet, a rug,
  turf, a mat, foam tiles), is warned.
- **sled** (a friction): `friction = pull / sled weight`, the spring scale read at a steady slide; the weight may be
  read on a kitchen scale in g or oz (units.py). The setup names the two materials: `sliding` (on the sled's
  bottom) and `on` (what it slides on).

A test's settings (the tare, the squeeze height, the drop height, the sled's weight) may be given once in its
`[<test>_setup]` table, or in a trial for that trial alone. Every value is averaged by stats.summary.
"""

import dataclasses
import math
import re

from .. import i18n
from ..launcher.inputs import PRESETS
from .file import MeasureError, MeasureFile
from .stats import Summary, summary, why
from .units import UNITS, reading, unit_of

DROP_HEIGHT = 18 * 0.0254  # the drop the model's foam is tuned for (m)
DROP_TOLERANCE = 0.05  # a drop height further than this fraction from 18 in is warned
IDEAL_SQUEEZE = 0.25  # the fraction of the diameter stiffness_n is defined at

# Each test's readings: name -> quantity. Trial readings are per trial; settings may also go in [<test>_setup].
TRIAL = {
    "size": {"mass": "mass", "circumference": "length", "diameter": "length"},
    "squeeze": {"force": "force"},
    "drop": {"bounce": "length"},
    "sled": {"pull": "force"},
}
SETTING = {
    "size": {},
    "squeeze": {"tare": "weight", "squeezed_to": "length"},
    "drop": {"drop": "length"},
    "sled": {"sled": "weight"},
}
SETUP_TEXT = {"size": (), "squeeze": (), "drop": ("floor",), "sled": ("sliding", "on")}
_SOFT_FLOOR = re.compile(r"\b(carpet|rug|turf|mat|foam tile)s?\b", re.IGNORECASE)


def _keys(names: dict[str, str]) -> str:
    return ", ".join(f"{n}_<unit>" if n not in UNITS[q] else n for n, q in names.items())


def _check_keys(mf: MeasureFile, label: str, table: dict, names: dict[str, str], text: tuple[str, ...] = (),
                setup_text: tuple[str, ...] = (), setup: str = ""):
    for key in table:
        if key in text or any(unit_of(key, n) is not None for n in names):
            continue
        if key in setup_text:
            raise MeasureError(i18n.t("{path}: {key} goes in [{setup}], once for all trials, not in {table}",
                                      path=mf.path, key=key, setup=setup, table=label))
        valid = _keys(names) + "".join(f", {t}" for t in text)
        raise MeasureError(i18n.t("{path}: unknown reading '{key}' in {table}; it takes {valid}", path=mf.path,
                                  key=key, table=label, valid=valid))


class _Test:
    """One test's trials and setup, read with errors that name the file, the table and the trial."""

    def __init__(self, mf: MeasureFile, test: str):
        self.mf, self.test = mf, test
        self.rows = mf.trials[test]
        self.setup = mf.settings.get(f"{test}_setup", {})
        _check_keys(mf, f"[{test}_setup]", self.setup, SETTING[test], SETUP_TEXT[test])
        for i, row in enumerate(self.rows):
            _check_keys(mf, self.label(i), row, TRIAL[test] | SETTING[test], setup_text=SETUP_TEXT[test],
                        setup=f"{test}_setup")

    def label(self, i: int) -> str:
        return i18n.t("[[{test}]] trial {n}", test=self.test, n=i + 1)

    def where(self, i: int) -> str:
        return f"{self.mf.path}: {self.label(i)}"

    def err(self, message: str) -> MeasureError:
        """`message`: the whole sentence, beginning with its trial's where()."""
        return MeasureError(message)

    def trial(self, i: int, name: str, *, required: bool = True) -> float | None:
        v = reading(self.rows[i], TRIAL[self.test][name], name, where=self.where(i), required=required)
        if v is not None and v < 0:
            raise self.err(i18n.t("{where} has a negative {name} reading", where=self.where(i), name=name))
        return v

    def setting(self, i: int, name: str, *, required: bool = True) -> float | None:
        """A setting from trial i, else from [<test>_setup]."""
        quantity = SETTING[self.test][name]
        v = reading(self.rows[i], quantity, name, where=self.where(i), required=False)
        if v is None:
            v = reading(self.setup, quantity, name, where=f"{self.mf.path}: [{self.test}_setup]", required=False)
        if v is None and required:
            raise self.err(i18n.t("{where} has no {name}: give it once in {test_setup_table} or in the trial, as "
                                  "{name}_<unit>", where=self.where(i), name=name,
                                  test_setup_table="[{}_setup]".format(self.test)))
        if v is not None and v < 0:
            raise self.err(i18n.t("{where} has a negative {name} reading", where=self.where(i), name=name))
        return v

    def text(self, key: str, *, required: bool = True) -> str | None:
        if key not in self.setup:
            if required:
                raise MeasureError(i18n.t("{path}: {test_setup_table} is missing required key '{key}'",
                                          path=self.mf.path, key=key,
                                          test_setup_table="[{}_setup]".format(self.test)))
            return None
        v = self.setup[key]
        if not isinstance(v, str):
            raise MeasureError(i18n.t("{path}: {test_setup_table} {key} must be text, not {value}", path=self.mf.path,
                                      key=key, value=repr(v), test_setup_table="[{}_setup]".format(self.test)))
        return v


def _with(s: Summary, warnings: list[str]) -> Summary:
    return dataclasses.replace(s, warnings=s.warnings + warnings) if warnings else s


def _size(t: _Test) -> dict:
    mass, diameter = [], []
    mass_trials, diameter_trials = [], []   # the trials each value came from: a trial may only weigh
    for i, row in enumerate(t.rows):
        m = t.trial(i, "mass", required=False)
        c = t.trial(i, "circumference", required=False)
        d = t.trial(i, "diameter", required=False)
        if c is not None and d is not None:
            given = [k for k in row if unit_of(k, "circumference") is not None or unit_of(k, "diameter") is not None]
            raise t.err(i18n.t("{where} gives {keys}: give a circumference or a diameter, not both",
                               where=t.where(i), keys=" and ".join(given)))
        if m is None and c is None and d is None:
            raise t.err(i18n.t("{where} has no reading: write {keys}", where=t.where(i), keys=_keys(TRIAL["size"])))
        if m is not None:
            mass.append(m)
            mass_trials.append(i)
        if c is not None or d is not None:
            diameter.append(c / math.pi if c is not None else d)
            diameter_trials.append(i)
    out = {}
    if mass:
        out["mass"] = (summary(mass, mass_trials), mass)
    if diameter:
        out["diameter"] = (summary(diameter, diameter_trials), diameter)
    return out


def _preset(mf: MeasureFile) -> dict:
    return PRESETS["ball"].get(mf.base, {}) if mf.base else {}


def _diameter(mf: MeasureFile, size: tuple[Summary, list[float]] | None) -> tuple[float | None, list[str]]:
    """The ball's diameter for squeeze heights, and a warning when the measured one isn't good enough to use."""
    preset = _preset(mf).get("diameter")
    if size is None:
        return preset, []
    s = size[0]
    if s.good:
        return s.mean, []
    if preset is not None:
        return preset, [i18n.t("the squeeze heights use the {preset} preset's diameter, because the {size_table} "
                               "diameter isn't good enough ({why})", preset=mf.base, why=why(s),
                               size_table="[[size]]")]
    return s.mean, [i18n.t("the squeeze heights use the {size_table} diameter, which isn't good enough ({why}): "
                           "measure it again, or give a base preset in {measure_table}", why=why(s),
                           size_table="[[size]]", measure_table="[measure]")]


def _squeeze(t: _Test, size: tuple[Summary, list[float]] | None) -> dict:
    exponent = _preset(t.mf).get("stiffness_exponent", 1.5)
    diameter, diameter_warnings = _diameter(t.mf, size)
    values, heights_used = [], False
    for i in range(len(t.rows)):
        force = t.trial(i, "force") - (t.setting(i, "tare", required=False) or 0.0)
        if force <= 0:
            raise t.err(i18n.t("{where} reads no more than the tare: zero the scale with the bottom board and the "
                               "ball on it, then put the top board on and press", where=t.where(i)))
        height = t.setting(i, "squeezed_to", required=False)
        if height is not None:
            if height == 0:
                raise t.err(i18n.t("{where} is squeezed to 0: squeezed_to is the gap between the boards when you "
                                   "read the scale, about 75% of the ball's diameter", where=t.where(i)))
            if diameter is None:
                raise t.err(i18n.t("{where} is squeezed to a height, which needs the ball's diameter: add "
                                   "{size_table} trials or a base preset in {measure_table}", where=t.where(i),
                                   size_table="[[size]]", measure_table="[measure]"))
            if height >= diameter:
                raise t.err(i18n.t("{where} is squeezed to {height} in, which isn't less than the ball's diameter "
                                   "({diameter} in): measure the gap between the boards", where=t.where(i),
                                   height=f"{height / 0.0254:.3g}", diameter=f"{diameter / 0.0254:.3g}"))
            force *= (IDEAL_SQUEEZE / (1.0 - height / diameter)) ** exponent
            heights_used = True
        values.append(force)
    return {"stiffness_n": (_with(summary(values), diameter_warnings if heights_used else []), values)}


def _drop(t: _Test) -> dict:
    values, heights = [], []
    for i in range(len(t.rows)):
        drop, bounce = t.setting(i, "drop"), t.trial(i, "bounce")
        if drop <= 0:
            raise t.err(i18n.t("{where} has a drop height of 0: measure from the floor to the bottom of the ball "
                               "before you let go ({drop_in_setting})", where=t.where(i),
                               drop_in_setting="drop_in = 18"))
        if bounce > drop:
            raise t.err(i18n.t("{where} bounces higher than the drop: measure both to the bottom of the ball",
                               where=t.where(i)))
        values.append(math.sqrt(bounce / drop))
        heights.append(drop)
    warnings = []
    off = sorted({round(h / 0.0254, 1) for h in heights if abs(h / DROP_HEIGHT - 1) > DROP_TOLERANCE})
    if off:
        warnings.append(i18n.t("the ball was dropped from {heights} in: the model's foam is tuned for an 18 in drop, "
                               "so drop it from 18 in if you can", heights=" and ".join(f"{h:g}" for h in off)))
    floor = t.text("floor", required=False)
    if floor is None:
        warnings.append(i18n.t("say what floor it bounced on ({floor_setting} in {drop_setup_table}): the model "
                               "expects a hard floor", floor_setting="floor = \"concrete\"",
                               drop_setup_table="[drop_setup]"))
    elif _SOFT_FLOOR.search(floor):
        warnings.append(i18n.t("a ball bounces lower on a soft floor ({floor}) than on the hard floor the model "
                               "expects: drop it on concrete, tile or wood", floor=floor))
    return {"restitution": (_with(summary(values), warnings), values)}


def _sled(t: _Test) -> dict:
    t.text("sliding")
    t.text("on")
    values = []
    for i in range(len(t.rows)):
        weight = t.setting(i, "sled")
        if weight <= 0:
            raise t.err(i18n.t("{where} has a sled weight of 0: weigh the sled with its load on a scale "
                               "({sled_lb_setting})", where=t.where(i), sled_lb_setting="sled_lb = 5"))
        values.append(t.trial(i, "pull") / weight)
    return {"friction": (summary(values), values)}


def values(mf: MeasureFile) -> dict[str, tuple[Summary, list[float]]]:
    """Each measured field (mass, diameter, stiffness_n, restitution, friction) with its summary and per-trial values,
    in SI. A file with only fitted tests (shot, stroke) has none."""
    out: dict[str, tuple[Summary, list[float]]] = {}
    if "size" in mf.trials:
        out |= _size(_Test(mf, "size"))
    if "squeeze" in mf.trials:
        out |= _squeeze(_Test(mf, "squeeze"), out.get("diameter"))
    if "drop" in mf.trials:
        out |= _drop(_Test(mf, "drop"))
    if "sled" in mf.trials:
        out |= _sled(_Test(mf, "sled"))
    return out
