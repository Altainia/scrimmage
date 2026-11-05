"""The Measure tab's side of the editor: one measurement file (`<name>.measure.toml`, scrimmage/measure/file.py).

The page edits the file as JSON (`file` below) and the server does the rest with the measure package's own code, so
the tab says what `scrimmage measure` says:
  - each test's readings and words are forms.TEST's, the list the printed forms and the starter files use;
  - the summary (average, lowest, highest, spread, warnings, the trials that look off) is tests_direct's and
    stats', from the trials as typed (not yet saved): `check`;
  - Fit runs fit_launcher's or fit_cylinder's fit on the trials as typed: `fit`;
  - Save writes the file with tomlkit, changing only what changed, so its comments and layout stay: `save`.

The file as JSON:
    {measure: {about, by, date, tools, base},       base "" when there is none (a ball's only)
     tests: {<test>: {setup: PART, trials: [PART + {origin}]}}}   every test of the file's kind
    PART: {cells: {<reading name>: {unit, value}}, extra: {<key>: value}}
A cell is one of the test's readings (forms.TEST[test].setup or .trial) and the unit its key ends in ("lb" for
force_lb; "" for a plain number or text such as fps or floor; "frames" for frames). A key the tab has no box for
(a setup value given in one trial, a reading in a unit the file doesn't read) is kept in `extra`, written back as
it was. A trial's `origin` is its index in the file the page last saw (null for a new trial): Save keeps a trial's
comments with it when another is removed.
"""

from __future__ import annotations

import dataclasses
import datetime
import functools
import math
import threading
import time
import tomllib
from pathlib import Path

import tomlkit

from .. import i18n
from ..i18n import translated
from ..launcher.inputs import PRESETS
from ..measure import cli, fit_cylinder, fit_launcher, forms, tests_direct
from ..measure.file import KIND_TESTS, MeasureError, MeasureFile, parse
from ..measure.stats import summary, why
from ..measure.units import UNITS, unit_of
from . import tomlutil
from .bench_session import SessionError
from .test_session import replace_text

def _stale() -> str:
    return i18n.t("the measurement file changed outside this page (another tab, or an outside edit)")
HEADER = ("about", "by", "date", "tools", "base")
OPTIONAL = ("about", "by", "date", "tools")   # [measure]'s text a blank box doesn't add (base: removed when blank)
FITS = ("shot", "stroke")


# -------------------------------------------------------------------------------------------------- the readings

def _type(r: forms.Reading) -> str:
    if r.quantity is not None:
        return "reading"
    return "text" if isinstance(r.example, str) else "bool" if isinstance(r.example, bool) else "number"


def reading_json(r: forms.Reading) -> dict:
    """A reading as the page draws it: its box, and the units its key may end in (the unit picker). `unit` is the
    form's; the page picks a new column's by its quantity and the Units setting (measure.js defaultUnit)."""
    units = list(UNITS[r.quantity]) if r.quantity else []
    return {"name": r.name, "what": translated(r.what), "optional": r.optional, "type": _type(r),
            "quantity": r.quantity,
            "units": [{"unit": u, "label": forms.UNIT_WORDS.get(u, u)} for u in units],
            "unit": (r.unit or units[0]) if units else ""}


def _fields(test: str) -> list[dict]:
    """The values a test's trials give, each with its column's name: what the summary averages."""
    t = forms.TEST[test]
    fields = ("time",) if test == "stroke" else tuple(cli.GIVES.get(test, ()))
    return [{"field": f, "label": translated(label)} for f, label in zip(fields, t.summaries)]


def test_json(test: str) -> dict:
    """A test as the page shows it: forms.TEST's words (in the Language setting), picture and readings, the values
    its trials give, and whether it is fit (shots, strokes) rather than averaged."""
    return _test_json(test, i18n.current_language())


@functools.cache
def _test_json(test: str, lang: str) -> dict:
    """test_json() in `lang` (cached by language: the page only reads it)."""
    t = forms.TEST[test]
    return {"name": test, "title": translated(t.title), "gives": translated(t.gives),
            "need": [translated(x) for x in t.need], "steps": [translated(x) for x in t.steps],
            "picture": forms.picture(test), "setup": [reading_json(r) for r in t.setup],
            "trial": [reading_json(r) for r in t.trial], "fields": _fields(test), "value": translated(t.value),
            "formula": translated(t.formula), "note": translated(t.note) if t.note else "", "fit": test in FITS}


def _exact(r: forms.Reading, key: str) -> str | None:
    """The unit of `key` when it is reading `r` in a unit the file reads ("" for a plain one), else None."""
    if r.quantity is None:
        return "" if key == r.name else None
    return next((u for u in UNITS[r.quantity] if forms.key(r, u) == key), None)


def _loose(r: forms.Reading, key: str) -> str | None:
    """The unit of `key` when it is reading `r` in a unit the file doesn't read (force_kgf), else None."""
    if r.quantity is None or r.name in UNITS[r.quantity]:
        return None
    unit = unit_of(key, r.name)
    return unit or None


def split(table: dict, readings) -> dict:
    """A table of the file as {cells, extra} (the module docstring)."""
    cells, extra = {}, {}
    rest = dict(table)
    for match in (_exact, _loose):
        for key in list(rest):
            r = next((r for r in readings if r.name not in cells and match(r, key) is not None), None)
            if r is not None:
                cells[r.name] = {"unit": match(r, key), "value": _plain(rest.pop(key))}
    for key, value in rest.items():
        extra[key] = _plain(value)
    return {"cells": cells, "extra": extra}


def _plain(v):
    if isinstance(v, datetime.date):
        return v.isoformat()
    return v


def wire(raw: dict, kind: str) -> dict:
    """The file (parsed TOML) as the page's JSON."""
    head = raw.get("measure", {})
    measure = {k: _plain(head.get(k, "")) for k in HEADER}
    tests = {}
    for test in KIND_TESTS[kind]:
        t = forms.TEST[test]
        rows = raw.get(test, [])
        tests[test] = {"setup": split(raw.get(f"{test}_setup", {}), t.setup),
                       "trials": [{"origin": i, **split(row, t.trial)} for i, row in enumerate(rows)]}
    return {"measure": measure, "tests": tests}


def _value(v, where: str):
    if isinstance(v, bool) or isinstance(v, str):
        return v
    if isinstance(v, (int, float)) and math.isfinite(v):
        return v
    raise SessionError(400, f"{where} must be a number, text, or true or false, not {v!r}")


def _join(part, readings, where: str) -> dict:
    if not isinstance(part, dict):
        raise SessionError(400, f"{where} must be an object {{cells, extra}}")
    cells, extra = part.get("cells", {}), part.get("extra", {})
    if not isinstance(cells, dict) or not isinstance(extra, dict):
        raise SessionError(400, f"{where}: cells and extra must be objects")
    by_name = {r.name: r for r in readings}
    out = {}
    for name, cell in cells.items():
        r = by_name.get(name)
        if r is None:
            raise SessionError(400, f"{where}: no reading '{name}'; it has {', '.join(by_name) or 'none'}")
        if not isinstance(cell, dict) or not isinstance(cell.get("unit", ""), str):
            raise SessionError(400, f"{where}: {name} must be {{unit, value}}")
        value = cell.get("value")
        if value is None or value == "":
            continue
        unit = cell.get("unit") or ""
        if r.quantity is None or not unit or unit in UNITS[r.quantity]:
            key = forms.key(r, unit)   # no unit: the reading's own (its starter's)
        else:
            key = f"{r.name}_{unit}"   # a unit the file doesn't read, kept as the file had it: the summary says so
        out[key] = _value(value, f"{where}: {name}")
    for key, value in extra.items():
        if key not in out:
            out[key] = _value(value, f"{where}: {key}")
    return out


def unwire(body, kind: str) -> tuple[dict, dict[str, list]]:
    """The page's JSON as parsed TOML, and each test's trial origins."""
    if not isinstance(body, dict) or not isinstance(body.get("measure"), dict) or \
            not isinstance(body.get("tests"), dict):
        raise SessionError(400, "file must be {measure, tests}")
    head = body["measure"]
    raw: dict = {"measure": {"kind": kind}}
    for key in HEADER:
        v = head.get(key, "")
        if not isinstance(v, str):
            raise SessionError(400, f"[measure] {key} must be text")
        if v or key != "base":
            raw["measure"][key] = v
    origins: dict[str, list] = {}
    for test, part in body["tests"].items():
        if test not in KIND_TESTS[kind]:
            raise SessionError(400, f"a {kind} file has no {test} test; it has {', '.join(KIND_TESTS[kind])}")
        if not isinstance(part, dict) or not isinstance(part.get("trials", []), list):
            raise SessionError(400, f"{test} must be {{setup, trials: [...]}}")
        t = forms.TEST[test]
        setup = _join(part.get("setup", {"cells": {}}), t.setup, f"[{test}_setup]")
        if setup:
            raw[f"{test}_setup"] = setup
        rows, seen = [], []
        for i, row in enumerate(part.get("trials", [])):
            rows.append(_join(row, t.trial, f"[[{test}]] trial {i + 1}"))
            origin = row.get("origin")
            if origin is not None:
                if isinstance(origin, bool) or not isinstance(origin, int) or (seen and origin <= seen[-1]):
                    raise SessionError(400, f"[[{test}]] trial {i + 1}: origin must be a later trial's index")
                seen.append(origin)
            origins.setdefault(test, []).append(origin)
        if rows:
            raw[test] = rows
    return raw, origins


# ----------------------------------------------------------------------------------------------------- summaries

def _without_path(e: Exception, path: Path) -> str:
    return str(e).replace(f"{path}: ", "").replace(f"{path} ", "this file ")


def _field_json(field: str, s, values: list[float]) -> dict:
    """A field's summary for the page; its rows are the trials that gave it a value (s.trials)."""
    show = cli.FIELD[field][1]
    trials = s.trials
    return {"field": field, "what": cli.FIELD[field][0], "mean": s.mean, "low": s.low, "high": s.high,
            "spread": s.spread, "n": s.n, "good": s.good, "why": why(s), "warnings": list(s.warnings),
            "outliers": [trials[i] for i in s.outliers],
            "shown": {"mean": show(s.mean), "low": show(s.low), "high": show(s.high)},
            "rows": [{"trial": t, "value": v, "shown": show(v), "outlier": i in s.outliers}
                     for i, (t, v) in enumerate(zip(trials, values))]}


def _direct(mf: MeasureFile, test: str) -> list[dict]:
    trials = {test: mf.trials[test]}
    if test == "squeeze" and "size" in mf.trials:   # squeeze heights need the diameter: the size test's, if it reads
        try:
            tests_direct.values(dataclasses.replace(mf, trials={"size": mf.trials["size"]}))
            trials = {"size": mf.trials["size"], **trials}
        except MeasureError:
            pass
    measured = tests_direct.values(dataclasses.replace(mf, trials=trials))
    return [_field_json(field, *measured[field]) for field in cli.GIVES[test] if field in measured]


def summarize(mf: MeasureFile) -> dict:
    """Each test of the file's kind: {error, fields}; fields as _field_json, none for a test without trials or one
    that is fit, not averaged (shots)."""
    out = {}
    for test in KIND_TESTS[mf.kind]:
        fields, error = [], None
        try:
            if test in mf.trials and test == "stroke":
                times, _ = fit_cylinder.trial_times(mf, mf.settings.get("stroke_setup", {}))
                fields = [_field_json("time", summary(times), times)]
            elif test in mf.trials and test != "shot":
                fields = _direct(mf, test)
        except MeasureError as e:
            error = _without_path(e, mf.path)
        out[test] = {"error": error, "fields": fields}
    return out


def fit_json(mf: MeasureFile, test: str) -> dict:
    """A launcher's or a cylinder's fit, with the numbers `scrimmage measure` prints."""
    if test == "shot":
        r = fit_launcher.fit(mf)
        what, held = cli.shot_words(r)
        return {"test": test, "what": what, "held": held,
                "value": r.friction if "friction" in r.fitted else r.stiffness_n, "band": list(r.band),
                "band_text": r.band_text, "rms_error": r.rms_error, "good": r.good, "why": r.why,
                "warnings": list(r.warnings),
                "rows": [{"measured": m, "model": None if math.isnan(x) else x,
                          "off": None if math.isnan(x) else (x - m) / m} for m, x in r.per_shot]}
    if test == "stroke":
        r = fit_cylinder.fit(mf)
        return {"test": test, "conductance": r.conductance, "band": list(r.band), "band_text": r.band_text,
                "model_time": r.model_time, "measured_time": r.measured_time, "off": r.error, "good": r.good,
                "why": r.why, "warnings": list(r.warnings)}
    raise MeasureError(f"the {test} test is averaged, not fit: only shots and strokes are fit")


# ------------------------------------------------------------------------------------------------------- writing

def _reading_of(key: str, readings) -> str | None:
    return next((r.name for r in readings if _exact(r, key) is not None or _loose(r, key) is not None), None)


def _same(old, new) -> bool:
    old = _plain(old.unwrap() if hasattr(old, "unwrap") else old)
    return type(old) is type(new) and old == new or (
        isinstance(old, (int, float)) and isinstance(new, (int, float)) and not isinstance(old, bool)
        and not isinstance(new, bool) and old == new)


def _sync(table, want: dict, readings) -> None:
    """Make a tomlkit table hold `want`: an unchanged value stays as written, a reading in a new unit takes its old
    key's place, and the comments stay."""
    removed = [k for k in table if k not in want]
    for key, value in want.items():
        if key in table:
            if not _same(table[key], value):
                tomlutil.set_value(table, key, _item(key, value))
            continue
        name = _reading_of(key, readings)
        old = next((k for k in removed if name is not None and _reading_of(k, readings) == name), None)
        item = tomlkit.item(_item(key, value))
        if old is not None:   # the same reading in another unit: its comment stays with it
            was = table.item(old).trivia
            item.trivia.comment_ws, item.trivia.comment = was.comment_ws, was.comment
        tomlutil.insert(table, key, item, after=(old,) if old else ())
    for key in removed:
        del table[key]


def _item(key: str, value):
    if key == "date" and isinstance(value, str):
        try:
            return tomlkit.item(datetime.date.fromisoformat(value))
        except ValueError:
            pass
    return value


def _new_table(values: dict):
    t = tomlkit.table()
    for k, v in values.items():
        t[k] = _item(k, v)
    return t


def apply(doc, raw: dict, origins: dict[str, list], kind: str) -> None:
    """Make the tomlkit document `doc` (the file as it is) hold `raw` (unwire's)."""
    head = doc["measure"]   # a line left blank isn't added; one the file has (a starter's) is kept, blank
    _sync(head, {"kind": head["kind"], **{k: v for k, v in raw["measure"].items()
                                          if k != "kind" and not (v == "" and k in OPTIONAL and k not in head)}}, ())
    for test in KIND_TESTS[kind]:
        t = forms.TEST[test]
        name = f"{test}_setup"
        want = raw.get(name, {})
        if name in doc:
            _sync(doc[name], want, t.setup)
        elif want:
            tomlutil.insert_table(doc, name, _new_table(want), before=(test,))
        rows, came_from = raw.get(test, []), origins.get(test, [])
        if test in doc:   # the trials taken out go first, each with the comments above it
            for i in reversed(range(len(doc[test]))):
                if i not in came_from:
                    tomlutil.remove_aot_item(doc, test, i)
        j = 0   # the file's trials left are the page's trials with an origin, in order
        for row, origin in zip(rows, came_from):
            if origin is not None and test in doc and j < len(doc[test]):
                _sync(doc[test][j], row, t.trial)
            else:
                tomlutil.insert_aot_item(doc, test, _new_table(row), index=j if test in doc else None)
            j += 1


# ------------------------------------------------------------------------------------------------------- session

def _sig(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


class MeasureSession:
    def __init__(self, path: Path, version: int = 0):
        """OSError when the file can't be read at all."""
        self.path = Path(path).resolve()
        self.version = version
        self._changed_at = version
        self._cond = threading.Condition()
        self._closed = False
        self._sig = self.path.read_bytes()
        self._read()

    def _read(self) -> None:
        """The file as it is now: its kind, its JSON and its summary, or why it can't be edited here."""
        self.kind, self.raw, self.file_error, self.summary = None, None, None, {}
        try:
            raw = tomllib.loads(self._sig.decode("utf-8"))
        except UnicodeDecodeError:
            self.file_error = f"{self.path.name} isn't a text file: a measurement file is TOML text (UTF-8)"
            return
        except tomllib.TOMLDecodeError as e:
            self.file_error = f"{self.path.name} isn't valid TOML: {e}"
            return
        head = raw.get("measure")
        if isinstance(head, dict) and head.get("kind") in KIND_TESTS:
            self.kind = head["kind"]
        try:
            mf = parse(raw, self.path)
        except MeasureError as e:
            self.file_error = _without_path(e, self.path)
            return
        self.raw = raw
        self.summary = summarize(mf)

    def _bump(self, changed: bool = False) -> None:
        self.version += 1
        if changed:
            self._changed_at = self.version
        self._cond.notify_all()

    def reload_if_changed(self) -> bool:
        """Read an outside edit of the file; True when it changed."""
        sig = _sig(self.path)
        with self._cond:
            if sig is None or sig == self._sig:
                return False
            self._sig = sig
            self._read()
            self._bump(changed=True)
            return True

    def state(self) -> dict:
        with self._cond:
            return self._state()

    def _state(self) -> dict:
        return {"version": self.version, "path": str(self.path), "name": self.path.name, "kind": self.kind,
                "tests": [test_json(t) for t in KIND_TESTS.get(self.kind, ())],
                "bases": list(PRESETS["ball"]) if self.kind == "ball" else [],
                "file": wire(self.raw, self.kind) if self.raw is not None else None,
                "file_error": self.file_error, "summary": self.summary}

    def _typed(self, body: dict) -> tuple[MeasureFile, dict, dict]:
        """The file as typed in the page (body["file"]): checked, not written."""
        if self.kind is None or self.raw is None:
            raise SessionError(409, i18n.t("fix the file first: {why}", why=self.file_error))
        raw, origins = unwire(body.get("file"), self.kind)
        try:
            return parse(raw, self.path), raw, origins
        except MeasureError as e:
            raise SessionError(400, _without_path(e, self.path)) from None

    def check(self, body: dict) -> dict:
        """The summary of the trials as typed: {tests: summarize's}."""
        with self._cond:
            mf, _, _ = self._typed(body)
        return {"tests": summarize(mf)}

    def fit(self, body: dict) -> dict:
        """Fit the trials as typed (body {file, test}); 400 with why it can't."""
        with self._cond:
            mf, _, _ = self._typed(body)
        try:
            return fit_json(mf, body.get("test"))
        except MeasureError as e:
            raise SessionError(400, _without_path(e, self.path)) from None

    def save(self, body: dict) -> tuple[int, dict]:
        """Write body["file"] over the file; 409 {error, state} when the file changed after body["version"]."""
        v = body.get("version")
        if not isinstance(v, int) or isinstance(v, bool):
            raise SessionError(400, "version (the integer from the state) is required")
        with self._cond:
            if self._closed:
                raise SessionError(409, i18n.t("the measurement file was closed"))
            if self.kind is None or self.raw is None:
                raise SessionError(409, i18n.t("fix the file first: {why}", why=self.file_error))
            if v < self._changed_at or _sig(self.path) != self._sig:
                stale = _stale()
                return 409, {"ok": False, "error": stale, "errors": [{"field": "status", "message": stale}],
                             "state": self._state()}
            _, raw, origins = self._typed(body)
            doc = tomlkit.parse(self._sig.decode("utf-8"))
            apply(doc, raw, origins, self.kind)
            text = tomlkit.dumps(doc)
            try:
                replace_text(self.path, text)
            except OSError as e:
                raise SessionError(400, i18n.t("can't write {name}: {why}", name=self.path.name,
                                               why=e.strerror or e)) from None
            self._sig = _sig(self.path)
            self._read()
            self._bump(changed=True)
            return 200, {"ok": True, "version": self.version}

    def wait(self, since: int | None, end: float) -> bool:
        """Wait (at most a second, and not past `end`) for the version to move from `since`."""
        with self._cond:
            if since is None or self.version != since or self._closed or time.monotonic() >= end:
                return True
            self._cond.wait(min(1.0, max(0.0, end - time.monotonic())))
            return self.version != since or self._closed

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()


def new_text(kind: str) -> str:
    """A starter file for a kind: its first test's (forms.starter_toml)."""
    if not isinstance(kind, str) or kind not in KIND_TESTS:
        raise SessionError(400, f"measure must be {_kinds()}")
    return forms.starter_toml(KIND_TESTS[kind][0])


def kinds() -> list[dict]:
    """What a measurement file can measure, for the start page: each kind and its tests' titles."""
    return [{"kind": k, "tests": [forms.TEST[t].title for t in tests]} for k, tests in KIND_TESTS.items()]


def _kinds() -> str:
    kinds = list(KIND_TESTS)
    return f"{', '.join(kinds[:-1])} or {kinds[-1]}"
