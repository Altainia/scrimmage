"""A measurement file, `<name>.measure.toml`: what was measured, by whom and when, and every trial's raw readings.

    [measure]
    kind = "ball"                 # ball, friction, launcher or cylinder
    about = "a FUEL ball from the practice bin"
    by = "J. Smith"
    date = 2026-11-10             # a TOML date, or the same as text
    tools = "bathroom scale, two boards"
    base = "2026-fuel"            # the preset it measured (a ball's must be a launcher ball preset)

    [squeeze_setup]               # a test's settings, read once for all its trials
    tare_lb = 1.2

    [[squeeze]]                   # one table per trial, readings in the units they were read in
    force_lb = 9.8

A kind has its own tests: a ball has size, squeeze and drop; a friction has sled; a launcher has shot; a cylinder
has stroke. Each test may have a `[<test>_setup]` table. Only `kind` is required: who measured it and when are
asked for (the labels name them), but a file without them still reads, so trials can be typed in first. The file keeps the readings as written; the averages and
fits are worked out from them each time it is read (stats.py, tests_direct.py and the fits), so they never go stale.
"""

import datetime
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .. import i18n
from ..launcher.inputs import PRESETS
from .stats import why


class MeasureError(ValueError):
    """A measurement file problem; the message names the file, the table and the key. `key`: the robot file's key it
    is about when that isn't the one naming the file (a value given and also measured), for the Robot tab's field."""

    def __init__(self, message: str = "", *, key: str | None = None):
        super().__init__(message)
        self.key = key


KIND_TESTS: dict[str, tuple[str, ...]] = {
    "ball": ("size", "squeeze", "drop"),
    "friction": ("sled",),
    "launcher": ("shot",),
    "cylinder": ("stroke",),
}
TEST_KIND = {test: kind for kind, tests in KIND_TESTS.items() for test in tests}

_HEADER = ("kind", "about", "by", "date", "tools", "base")


@dataclass(frozen=True, kw_only=True)
class MeasureFile:
    kind: str
    about: str
    by: str  # "" when not given
    date: str  # ISO, 2026-11-10; "" when not given
    tools: str
    base: str | None  # the ball's or wheel's preset that was measured
    trials: dict[str, list[dict]]  # test name -> one table of raw readings per trial, in file order
    settings: dict[str, dict]  # "<test>_setup" -> its table
    path: Path


@dataclass(frozen=True)
class Provenance:
    """Where a measured number came from, carried with it to the labels."""
    file: Path
    by: str  # "" when not given
    date: str  # "" when not given
    trials: int
    good: bool
    why: str = ""  # empty when good, else why not: "2 trials: take at least 3", "trials disagree by 22%"


def provenance(mf: MeasureFile, summary) -> Provenance:
    """The provenance of a number averaged from `mf`'s trials (`summary` is a stats.Summary)."""
    return Provenance(file=mf.path, by=mf.by, date=mf.date, trials=summary.n, good=summary.good, why=why(summary))


def _listing(names) -> str:
    return ", ".join(names)


def _text(path: Path, table: dict, key: str, *, required: bool) -> str:
    if key not in table:
        if required:
            raise MeasureError(i18n.t("{path}: {measure_table} is missing required key '{key}'", path=path, key=key,
                                      measure_table="[measure]"))
        return ""
    v = table[key]
    if not isinstance(v, str):
        raise MeasureError(i18n.t("{path}: {measure_table} {key} must be text, not {value}", path=path, key=key,
                                  value=repr(v), measure_table="[measure]"))
    return v


def _date(path: Path, table: dict) -> str:
    """The date as ISO text; "" when it isn't given (left out, or left blank)."""
    v = table.get("date", "")
    if v == "":
        return ""
    if isinstance(v, datetime.date) and not isinstance(v, datetime.datetime):
        return v.isoformat()
    if isinstance(v, str):
        try:
            return datetime.date.fromisoformat(v).isoformat()
        except ValueError:
            pass
    raise MeasureError(i18n.t("{path}: {measure_table} date must be a date like 2026-11-10, not {value}", path=path,
                              value=repr(v), measure_table="[measure]"))


def load(path) -> MeasureFile:
    """Read and check a measurement file's shape; the readings themselves are checked when they are used."""
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MeasureError(i18n.t("{path}: no such file", path=path)) from None
    except UnicodeDecodeError:
        raise MeasureError(i18n.t("{path} isn't a text file: a measurement file is TOML text (UTF-8)",
                                  path=path)) from None
    except tomllib.TOMLDecodeError as e:
        raise MeasureError(i18n.t("{path} isn't valid TOML: {error}", path=path, error=e)) from None
    return parse(raw, path)


def parse(raw: dict, path) -> MeasureFile:
    """Check a measurement file's shape from its parsed TOML (`raw`), read from (or to be written to) `path`; the
    Measure tab checks the trials typed in before they are saved this way."""
    path = Path(path)
    head = raw.get("measure")
    if not isinstance(head, dict):
        raise MeasureError(i18n.t("{path}: needs a {measure_table} table saying what was measured (kind, by, date)",
                                  path=path, measure_table="[measure]"))
    unknown = [k for k in head if k not in _HEADER]
    if unknown:
        raise MeasureError(i18n.t("{path}: unknown key '{key}' in {measure_table}; valid keys: {keys}", path=path,
                                  key=unknown[0], keys=_listing(_HEADER), measure_table="[measure]"))
    kind = _text(path, head, "kind", required=True)
    if kind not in KIND_TESTS:
        raise MeasureError(i18n.t("{path}: {measure_table} kind must be one of {kinds}, not {value}", path=path,
                                  kinds=_listing(KIND_TESTS), value=repr(kind), measure_table="[measure]"))
    base = _text(path, head, "base", required=False) or None
    if kind == "ball" and base is not None:
        if base not in PRESETS["ball"]:
            raise MeasureError(i18n.t("{path}: {measure_table} base '{base}' isn't a ball preset; one of {presets}",
                                      path=path, base=base, presets=_listing(PRESETS["ball"]),
                                      measure_table="[measure]"))

    tests = KIND_TESTS[kind]
    trials: dict[str, list[dict]] = {}
    settings: dict[str, dict] = {}
    for key, value in raw.items():
        if key == "measure":
            continue
        if key in tests:
            if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
                raise MeasureError(i18n.t("{path}: {key} must be trials written as [[{key}]] tables", path=path,
                                          key=key))
            if not value:
                raise MeasureError(i18n.t("{path}: {key} has no trials: write each trial as a [[{key}]] table, or "
                                          "leave the test out", path=path, key=key))
            trials[key] = value
        elif key.endswith("_setup") and key[:-len("_setup")] in tests:
            if not isinstance(value, dict):
                raise MeasureError(i18n.t("{path}: {key} must be a [{key}] table", path=path, key=key))
            settings[key] = value
        elif key in TEST_KIND or (key.endswith("_setup") and key[:-len("_setup")] in TEST_KIND):
            test = key.removesuffix("_setup")
            if key.endswith("_setup"):
                raise MeasureError(i18n.t("{path}: [{key}] is for the {test} test, a {other} test; a {kind} file has "
                                          "{tests}", path=path, key=key, test=test, other=TEST_KIND[test], kind=kind,
                                          tests=_listing(tests)))
            raise MeasureError(i18n.t("{path}: [[{key}]] is a {other} test; a {kind} file has {tests}", path=path,
                                      key=key, other=TEST_KIND[test], kind=kind, tests=_listing(tests)))
        else:
            valid = ["measure", *tests, *(f"{t}_setup" for t in tests)]
            raise MeasureError(i18n.t("{path}: unknown table '{key}'; a {kind} file has {tables}", path=path, key=key,
                                      kind=kind, tables=_listing(valid)))

    return MeasureFile(kind=kind, about=_text(path, head, "about", required=False),
                       by=_text(path, head, "by", required=False), date=_date(path, head),
                       tools=_text(path, head, "tools", required=False), base=base, trials=trials,
                       settings=settings, path=path)
