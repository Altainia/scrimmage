"""The Bench tab's idea session: a folder of bench files, kept in step with the agent and the CLI.

The ideas are the folder's `*.toml` files that classify as "bench" (a top-level [bench] table), each a `ConfigDoc`
(tomlkit edits that keep comments and order, undo and redo, atomic writes). A background poll every 0.25 s reloads
ideas changed outside, picks up new and deleted ones, and reads the session log (`bench.session`) for runs made by
anyone (an idea's newest run is its own result; the newest sweep or compare is the session's), the talked-about
lists and the run lock's holder. Every change bumps one version, which the page long-polls.

Writing rule (as the scenario's): a candidate is written if it has no more errors than the file now has, so a valid
file only takes a valid edit and fixing one error at a time works. An edit is refused, and says so, when its idea
changed since the version the page sent: an outside write is never overwritten by a stale page edit.

Dotted keys (`wheel.top.diameter`) address fields in problems, focus and edits, always as the meters key even when
the file writes `diameter_in`. `file` is an idea's file name inside the folder.
"""

from __future__ import annotations

import contextlib
import csv
import logging
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ..bench import run as bench_run
from ..bench import session
from ..bench.build import build_bench
from ..bench.compare import BENCH_METRICS
from ..bench.fields import fields_payload
from ..bench.pictures import PICTURE_UNCALIBRATED
from ..bench.problems import collect_problems
from ..bench.report import bench_uncalibrated
from ..bench.spec import (_LISTS, _SECTIONS, _SINGLES, BenchError, _check_path, _read_toml,
                          _resolve_includes)
from . import bench_edits
from .bench_view import bench_view
from .doc import ConfigDoc, FileChanged
from .edits import EditError
from .simlock import SimBusy, SimLock

POLL_PERIOD = 0.25
POLL_SECONDS = 25.0
EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "bench"
_ENTRY_SECTIONS = (*_LISTS, "shaft", *_SINGLES)   # what an include can supply
RESULT_SUFFIXES = (".txt", ".csv", ".png", ".gif")   # what a bench writes in its results folder
TABLE_METRICS = ("out", "jammed", "exit_speed", "elevation_deg", "spin_rps", "speed_drop_pct", "peak_load_n")
TABLE_ROWS = 40   # a sweep's table on the stage shows its first rows; results.csv has them all


class SessionError(Exception):
    """A request the session refuses: `status` is the HTTP status (400, 404, 409)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def examples() -> list[str]:
    """The stems of the shipped example benches (`examples/bench/*.toml`) a new idea can start from."""
    try:
        return sorted(p.stem for p in EXAMPLES.glob("*.toml") if p.is_file())
    except OSError:
        return []


def _meters(key: str | None) -> str | None:
    """The meters form of a dotted key: `wheel.top.diameter_in` -> `wheel.top.diameter`. Only a length field loses
    `_in`; an entry named `feed_in` keeps its name. A `sweep.` key is the swept key's."""
    if key is None or not key.endswith("_in"):
        return key
    parts = key.split(".")
    if parts[0] == "sweep" and len(parts) > 1:
        return "sweep." + _meters(".".join(parts[1:]))
    section = parts[0]
    want = 3 if section in _LISTS or section == "shaft" else 2
    if section in _SECTIONS and len(parts) == want and parts[-1][:-3] in _SECTIONS[section][1]:
        return key[:-3]
    return key


def _sig(p: Path):
    try:
        st = p.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _is_bench(p: Path) -> bool:
    from .server import classify   # server imports this module
    return classify(p) == "bench"


def _refused(field: str, message: str) -> tuple[int, dict]:
    return 200, {"ok": False, "errors": [{"field": field, "message": message}]}


def _summary(out: Path | None) -> tuple[list[str], str | None]:
    """(headlines, UNCALIBRATED line) from a results folder's summary.txt: the lines after the title block, through
    the UNCALIBRATED line `bench run` ends them with. ([], None) when there is none."""
    try:
        lines = (out / "summary.txt").read_text(encoding="utf-8").splitlines() if out is not None else []
    except (OSError, UnicodeDecodeError):
        return [], None
    if "" not in lines:
        return [], None
    heads = []
    for line in lines[lines.index("") + 1:]:
        heads.append(line)
        if line.startswith("UNCALIBRATED"):
            return heads, line
        if not line:
            break
    return [], None


def _result_files(kind: str, out: Path | None) -> tuple[list[str], Path | None]:
    """(picture paths, CSV) a sweep or compare wrote in `out`: a compare's compare.png and compare.csv; a sweep's
    results.csv and the charts written with it (line-*.png no older than results.csv: a folder shared with runs or
    older sweeps keeps their pictures, which aren't this sweep's)."""
    if out is None:
        return [], None
    if kind == "compare":
        pic, table = out / "compare.png", out / "compare.csv"
        return ([str(pic)] if pic.is_file() else []), (table if table.is_file() else None)
    table = out / "results.csv"
    since = _sig(table)
    if since is None:
        return [], None
    try:
        charts = sorted((p for p in out.glob("line-*.png") if p.is_file() and (_sig(p) or (0,))[0] >= since[0]),
                        key=lambda p: (not p.name.startswith("line-exit_speed-"), p.name))   # exit speed first: on stage
    except OSError:
        charts = []
    return [str(p) for p in charts], table


def _table(path: Path | None) -> dict | None:
    """{columns, rows} from a sweep's or compare's CSV: the swept keys (or `idea`), then TABLE_METRICS; at most
    TABLE_ROWS rows. None without a readable one."""
    if path is None:
        return None
    try:
        with open(path, newline="", encoding="utf-8") as f:
            lines = list(csv.reader(f))
    except (OSError, UnicodeDecodeError, csv.Error):
        return None
    if not lines:
        return None
    keep = [i for i, c in enumerate(lines[0]) if c not in BENCH_METRICS or c in TABLE_METRICS]
    pick = lambda row: [row[i] if i < len(row) else "" for i in keep]   # noqa: E731
    return {"columns": pick(lines[0]), "rows": [pick(r) for r in lines[1:TABLE_ROWS + 1]]}


def _energy_failed(out: Path | None) -> bool:
    """Whether the results folder's summary.txt says the energy check failed (its "Energy check:" line, written by
    report.write_results for every run, whoever ran it). False without one."""
    try:
        lines = (out / "summary.txt").read_text(encoding="utf-8").splitlines() if out is not None else []
    except (OSError, UnicodeDecodeError):
        return False
    return any(line.startswith("Energy check:") and not line.startswith("Energy check: ok") for line in lines)


class _Idea:
    def __init__(self, path: Path):
        self.doc = ConfigDoc(path)
        self.raw: dict = {}            # the last text that parsed, as plain values (a half-written file keeps it)
        self.bench = None              # the parsed bench while it has no errors
        self.problems: list[dict] = []
        self.changed_at = 0            # the session version of its last content change
        self.by: str | None = None     # who made it: "page" or "outside"
        self.touched = 0.0             # when it was last edited or run, by anyone
        self.view: dict | None = None
        self.unreadable = False        # the last read failed (permissions): read-only until it reads again
        self.check()

    @property
    def file(self) -> str:
        return self.doc.path.name

    @property
    def errors(self) -> int:
        return sum(p["level"] == "error" for p in self.problems)

    def check(self, found=None) -> None:
        """Re-read the problems (or take `found`, collect_problems' answer for the text now in the document)."""
        if self.doc.doc is not None:
            self.raw = self.doc.doc.unwrap()
        bench, problems = found or collect_problems(self.doc.path, self.doc.text)
        self.bench = bench if self.doc.doc is not None else None
        self.problems = [{"level": p.level, "key": _meters(p.key), "message": p.message} for p in problems]
        self.view = None


class BenchSession:
    def __init__(self, folder: Path, on_change: Callable[[], None], stage: str | None = None, version: int = 0,
                 cwd: Path | None = None):
        self.folder = Path(folder).resolve()
        self._on_change = on_change
        self._cwd = (cwd or Path.cwd()).resolve()
        self.version = version
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self.ideas: dict[str, _Idea] = {}
        self._others: dict[str, object] = {}       # the folder's other .toml files (includes): name -> signature
        self._next_seq = 0
        self._log_sig = None
        self._latest: dict[str, dict] = {}         # file -> the newest run event naming it (its own result)
        self._last_t: dict[str, float] = {}        # file -> when the newest event naming it was logged
        self._result: dict | None = None           # the newest sweep or compare event (the session's result)
        self._result_on_stage = False              # nothing moved the stage since that result
        self._outs: set[Path] = set()              # every results folder the log names (served by file())
        self._focus_sig = _sig(self.folder / session.STATE_DIR / "focus.json")
        self.lock_holder = self._holder()
        self.stage: str | None = None
        self._closed = False
        with self._lock:
            self._scan(initial=True)
            self._read_log(initial=True)
            for idea in self.ideas.values():
                idea.touched = max((_sig(idea.doc.path) or (0,))[0] / 1e9, self._last_t.get(idea.file, 0))
            self.stage = stage if stage in self.ideas else self._most_recent()
            t = self._result.get("t") if self._result is not None else None
            # after a restart the result is on stage when nothing (a run, an edit) came after it
            self._result_on_stage = isinstance(t, (int, float)) and all(i.touched <= t for i in self.ideas.values())
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll, daemon=True, name="bench-session-poll")
        self._thread.start()

    # -- keeping in step with the folder --------------------------------------------------------------
    def _bump(self) -> None:
        self.version += 1
        self._cond.notify_all()

    def _touch(self, idea: _Idea) -> None:
        idea.touched = time.time()
        self.stage = idea.file
        self._result_on_stage = False   # an edit or a run of an idea: the stage shows that idea's own result

    def _most_recent(self) -> str | None:
        return max(self.ideas.values(), key=lambda i: i.touched).file if self.ideas else None

    def _holder(self) -> dict | None:
        try:
            return session.lock_holder(self.folder)
        except OSError:
            return None

    def _bump_focus(self, idea: _Idea, keys: list[str]) -> None:
        keys = list(dict.fromkeys(_meters(k) for k in keys))
        if keys:
            try:
                session.bump(self.folder, idea.doc.path, keys)
            except OSError:   # a read-only folder: the list just isn't kept
                pass

    def _changed(self, idea: _Idea, old_raw: dict, by: str) -> None:
        """After `idea`'s text changed (and was checked): talked-about keys, the stage and the version."""
        if idea.doc.doc is not None:   # a half-written file isn't a list of changes
            self._bump_focus(idea, session.changed_keys(old_raw, idea.raw))
        idea.by = by
        self._touch(idea)
        self._bump()
        idea.changed_at = self.version

    def _reload(self, idea: _Idea) -> bool:
        """Pick up an outside write of this idea; True when there was one. A file that can't be read (permissions)
        makes the idea read-only with the reason until it reads again."""
        old = idea.raw
        try:
            changed = idea.doc.reload_if_changed()
            if idea.unreadable and not changed:   # readable again, unchanged: back to what its text says
                idea.doc._load()
        except OSError as e:   # FileNotFoundError is handled inside (a delete is the scan's)
            reason = f"can't read {idea.file}: {e.strerror or e}"
            if idea.doc.read_only_reason != reason:
                idea.doc.read_only_reason = reason
                idea.unreadable = True
                self._bump()
            return False
        if idea.unreadable:
            idea.unreadable = False
            if not changed:
                idea.check()
                self._bump()
                return False
        if not changed:
            return False
        idea.check()
        self._changed(idea, old, "outside")
        return True

    def _drop(self, name: str) -> None:
        del self.ideas[name]
        if self.stage == name:
            self.stage = self._most_recent()
        self._bump()

    def _scan(self, initial: bool = False) -> None:
        try:
            paths = sorted(p for p in self.folder.glob("*.toml") if not p.name.startswith(".") and p.is_file())
        except OSError:
            paths = []
        present = {p.name for p in paths}
        for name in [n for n in self.ideas if n not in present]:
            self._drop(name)
        includes_changed = any(n not in present for n in self._others)
        self._others = {n: s for n, s in self._others.items() if n in present}
        for p in paths:
            if p.name in self.ideas:
                self._reload(self.ideas[p.name])
                continue
            sig = _sig(p)
            if p.name in self._others and self._others[p.name] == sig:
                continue
            includes_changed = includes_changed or not initial   # a new or changed file an idea may include
            self._others[p.name] = sig
            if not _is_bench(p):
                continue
            try:
                idea = _Idea(p)
            except OSError:
                continue
            del self._others[p.name]
            self.ideas[p.name] = idea
            if not initial:
                self._changed(idea, idea.raw, "outside")   # a new idea: nothing in it is "changed" yet
        if includes_changed:   # an included file changed: the ideas' problems may have
            for idea in self.ideas.values():
                before = idea.problems
                idea.check()
                if idea.problems != before:
                    self._bump()

    def _read_log(self, initial: bool = False) -> None:
        sig = _sig(self.folder / session.STATE_DIR / "log.jsonl")
        if sig == self._log_sig:
            return
        if sig is None or (self._log_sig is not None and sig[1] < self._log_sig[1]):   # a new log: start over
            self._next_seq, self._latest, self._last_t, self._outs = 0, {}, {}, set()
            self._result, self._result_on_stage = None, False
        self._log_sig = sig
        try:
            events = session.read_events(self.folder, self._next_seq)
        except OSError:
            return
        for e in events:
            self._next_seq = e["seq"] + 1
            out = self._out_root(e.get("out"))
            if out is not None:
                self._outs.add(out)
            kind = e.get("kind")
            for f in e.get("files") or []:
                p = Path(f) if isinstance(f, str) else None
                if p is None or p.parent != self.folder:
                    continue
                if isinstance(e.get("t"), (int, float)):
                    self._last_t[p.name] = e["t"]
                if kind == "run":
                    self._latest[p.name] = e
                if not initial and kind in ("run", "sweep") and p.name in self.ideas:
                    self._touch(self.ideas[p.name])
            if kind in ("sweep", "compare"):   # the session's result, shown on stage until the next edit or run
                self._result, self._result_on_stage = e, True
        if events and not initial:
            self._bump()

    def _out_root(self, out) -> Path | None:
        """A logged results folder (or gallery file) /file may serve from; None for one too broad to trust: the home
        folder, the filesystem root or an ancestor of the session folder."""
        if not isinstance(out, str) or not out:
            return None
        p = Path(out).resolve()
        if p == Path(p.anchor) or p == Path.home().resolve() or self.folder.is_relative_to(p):
            return None
        return p

    def sync(self) -> None:
        """Pick up everything that changed outside since the last call."""
        with self._lock:
            if self._closed:
                return
            before = self.version
            self._scan()
            self._read_log()
            fsig = _sig(self.folder / session.STATE_DIR / "focus.json")
            if fsig != self._focus_sig:   # e.g. the agent's bench_focus
                self._focus_sig = fsig
                self._bump()
            holder = self._holder()
            if holder != self.lock_holder:
                self.lock_holder = holder
                self._bump()
            changed = self.version != before
        if changed:
            self._on_change()

    def _poll(self) -> None:
        while not self._stop.wait(POLL_PERIOD):
            try:
                self.sync()
            except Exception:   # never let one bad read stop the session following the folder
                logging.getLogger("scrimmage.assembler").exception("bench session poll")

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            self._closed = True
            self._cond.notify_all()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    # -- state ------------------------------------------------------------------------------------------
    def _display(self, p: Path) -> str:
        try:
            return str(p.relative_to(self._cwd))
        except ValueError:
            return str(p)

    def _latest_of(self, idea: _Idea) -> dict | None:
        e = self._latest.get(idea.file)
        if e is None:
            return None
        out = Path(e["out"]) if isinstance(e.get("out"), str) else None
        heads, unc = _summary(out)
        if unc is None:   # never a result without its UNCALIBRATED line
            unc = bench_uncalibrated(idea.bench) if idea.bench is not None else PICTURE_UNCALIBRATED

        def pic(name):
            return str(out / name) if out is not None and (out / name).is_file() else None
        return {"kind": e.get("kind"), "ok": e.get("ok"), "error": e.get("error"), "t": e.get("t"),
                "source": e.get("source"), "out": e.get("out"), "headlines": heads, "uncalibrated": unc,
                "energy_failed": _energy_failed(out),
                "pictures": {"path_side": pic("path_side.png"), "pass_gif": pic("pass.gif")}}

    def _result_state(self) -> dict | None:
        """The session's result: the newest sweep or compare, null when the log has none."""
        e = self._result
        if e is None:
            return None
        files = [Path(f).name for f in e.get("files") or [] if isinstance(f, str) and Path(f).parent == self.folder]
        out = Path(e["out"]) if isinstance(e.get("out"), str) else None
        kind = e.get("kind")
        pictures, table = _result_files(kind, out) if e.get("ok") else ([], None)
        first = self.ideas.get(files[0]) if files else None
        unc = bench_uncalibrated(first.bench) if first is not None and first.bench is not None \
            else PICTURE_UNCALIBRATED   # never a result without its UNCALIBRATED line
        return {"kind": kind, "files": files, "ok": e.get("ok"), "error": e.get("error"), "t": e.get("t"),
                "source": e.get("source"), "out": e.get("out"), "uncalibrated": unc, "pictures": pictures,
                "table": _table(table), "on_stage": self._result_on_stage}

    def _idea_state(self, idea: _Idea) -> dict:
        b = idea.raw.get("bench") if isinstance(idea.raw.get("bench"), dict) else {}
        try:
            focus = [_meters(k) for k in session.focus(self.folder, idea.doc.path)]
        except OSError:
            focus = []
        return {"file": idea.file, "name": b.get("name") or idea.doc.path.stem, "author": b.get("author"),
                "idea": b.get("idea"), "read_only_reason": idea.doc.read_only_reason, "problems": idea.problems,
                "focus": list(dict.fromkeys(focus)), "can_undo": idea.doc.can_undo, "can_redo": idea.doc.can_redo,
                "latest": self._latest_of(idea)}

    def state(self) -> dict:
        with self._lock:
            ideas = sorted(self.ideas.values(), key=lambda i: -i.touched)
            return {"version": self.version, "folder": str(self.folder), "display_folder": self._display(self.folder),
                    "stage": self.stage, "lock": self.lock_holder, "ideas": [self._idea_state(i) for i in ideas],
                    "result": self._result_state()}

    def wait_state(self, since: int | None) -> dict:
        end = time.monotonic() + POLL_SECONDS
        with self._cond:
            while since is not None and self.version == since and not self._closed and time.monotonic() < end:
                self._cond.wait(1.0)
            return self.state()

    @staticmethod
    def fields() -> dict:
        return fields_payload()

    def _check_open(self) -> None:
        """409 for a request that got this session before an open replaced it (lock held)."""
        if self._closed:
            raise SessionError(409, "the idea session was closed")

    def _need(self, file) -> _Idea:
        self._check_open()
        idea = self.ideas.get(file) if isinstance(file, str) else None
        if idea is None:
            raise SessionError(404, f"no idea {file!r} in this session")
        return idea

    def view(self, file) -> dict:
        """bench_view of the idea, or {} while it has errors."""
        with self._lock:
            idea = self._need(file)
            if idea.bench is None:
                return {}
            if idea.view is None:
                idea.view = bench_view(build_bench(idea.bench))
            return idea.view

    def _sources(self, raw: dict, base: Path, stack: tuple[Path, ...]) -> dict[str, Path]:
        """{entry key: the file it comes from} for what `raw`'s includes supply (an include's own entries win over
        what it includes, as the loader merges them)."""
        out: dict[str, Path] = {}
        incs = raw.get("include", [])
        for inc in incs if isinstance(incs, list) else []:
            if not isinstance(inc, str):
                continue
            p = (base / inc).resolve()
            if p in stack:
                continue
            try:
                sub = _read_toml(p)
            except BenchError:
                continue
            here = {k: p for s in _ENTRY_SECTIONS for k in session._entries(sub, s)}
            for k, v in self._sources(sub, p.parent, stack + (p,)).items():
                here.setdefault(k, v)
            for k, v in here.items():
                out.setdefault(k, v)
        return out

    def _merged(self, idea: _Idea, own: dict) -> dict:
        try:
            return _resolve_includes(own, self.folder, (idea.doc.path,))
        except BenchError:
            return own

    def idea(self, file) -> dict:
        """{own: the file's tables, included: {entry key: source file}, merged: the bench after includes}."""
        with self._lock:
            idea = self._need(file)
            own = idea.doc.doc.unwrap() if idea.doc.doc is not None else {}
            mine = {k for s in _ENTRY_SECTIONS for k in session._entries(own, s)}
            included = {}
            for k, p in self._sources(own, self.folder, (idea.doc.path,)).items():
                if k not in mine:
                    included[k] = p.relative_to(self.folder).as_posix() if p.is_relative_to(self.folder) else str(p)
            return {"own": own, "included": included, "merged": self._merged(idea, own)}

    # -- edits -------------------------------------------------------------------------------------------
    @staticmethod
    def _version(body: dict) -> int:
        v = body.get("version")
        if not isinstance(v, int) or isinstance(v, bool):
            raise SessionError(400, "version (the integer from the state) is required")
        return v

    def _fresh(self, idea: _Idea, version: int, what: str) -> tuple[int, dict] | None:
        """None when the page saw the idea as it is now; else the refusal saying the edit wasn't saved."""
        if not idea.doc.path.exists():   # deleted outside: never write it back
            self._drop(idea.file)
            raise SessionError(404, f"the idea {idea.file} was deleted")
        self._reload(idea)
        if idea.changed_at <= version:
            return None
        who = "the agent changed this idea" if idea.by == "outside" else "this idea changed (on another page?)"
        code, body = _refused("status", f"{who}; {what} wasn't saved")
        return code, {**body, "version": self.version}

    def _op(self, op, b: dict, idea: _Idea):
        e = bench_edits
        section, name = b.get("section"), b.get("name")
        values = b.get("values")
        if b.get("op") == "set" and values is not None:   # several keys in one edit: one write, one undo step
            if not isinstance(values, dict) or not values:
                raise SessionError(400, "values must be a table of dotted keys and their values")

            def set_all(d):
                for k, v in values.items():
                    e.set_value(d, k, v)
            set_op = (b.get("field") or next(iter(values)), set_all)
        else:
            set_op = (b.get("field") or b.get("key"), lambda d: e.set_value(d, b.get("key"), b.get("value")))
        ops = {
            "set": set_op,
            "add": (section, lambda d: e.add_entry(d, section, name, b.get("fields"))),
            "remove": (f"{section}.{name}", lambda d: e.remove_entry(d, section, name)),
            "rename": (f"{section}.{name}", lambda d: e.rename_entry(d, section, name, b.get("new_name"))),
            "override": (f"{section}.{name}" if name is not None else section,
                         lambda d: e.override_included(d, self._merged(idea, idea.raw), section, name)),
        }
        if op not in ops:
            raise SessionError(400, f"unknown op {op!r} ({', '.join(ops)})")
        field, fn = ops[op]
        return str(field), fn

    def _write(self, idea: _Idea, text: str, do: Callable[[], object], what: str, prefix: str = "",
               rename: Callable[[str], str] = lambda f: f):
        """Check `text` (what `do` writes) under the writing rule, then write it. `rename` maps an error's field to
        the one the page shows it under."""
        found = collect_problems(idea.doc.path, text)
        errs = [p for p in found[1] if p.level == "error"]
        if len(errs) > idea.errors:
            return 200, {"ok": False, "errors": [{"field": rename(_meters(p.key) or "status"), "message": prefix + p.message}
                                                 for p in errs]}
        old = idea.raw
        try:
            do()
        except FileChanged:   # written outside between the check and the write
            return self._fresh(idea, -1, what)
        except EditError as e:
            return _refused(e.field, e.message)
        except OSError as e:
            return _refused("status", f"can't write the file: {e}")
        idea.check(found)
        self._changed(idea, old, "page")
        return 200, {"ok": True, "version": self.version}

    def edit(self, body: dict) -> tuple[int, dict]:
        with self._lock:
            idea = self._need(body.get("file"))
            field, fn = self._op(body.get("op"), body, idea)
            what = f"your edit to {field}"
            stale = self._fresh(idea, self._version(body), what)
            if stale is not None:
                return stale
            if idea.doc.read_only_reason is not None:
                raise SessionError(409, idea.doc.read_only_reason)
            keys = list(body["values"]) if body.get("op") == "set" and isinstance(body.get("values"), dict) \
                else [body.get("key")]
            # a group set's errors on its own keys are shown under the field the page sent (the row it used)
            own = {_meters(k) for k in keys if isinstance(k, str)}
            group = body.get("field") if body.get("op") == "set" and isinstance(body.get("field"), str) else None

            def rename(f):
                return group if group and (f in own or f == "status") else f
            for key in keys:
                if body.get("op") == "set" and isinstance(key, str) and key.endswith(".name") and key != "bench.name":
                    return _refused(group or key, "a name is changed with rename, which also renames what refers to it")
            try:
                candidate = idea.doc.apply(fn)
            except EditError as e:
                return _refused(rename(_meters(e.field) or e.field), e.message)
            except (TypeError, ValueError, AttributeError, KeyError, IndexError) as e:   # malformed client values
                return _refused(group or "status", f"bad edit: {e}")
            return self._write(idea, candidate, lambda: idea.doc.commit(candidate), what, rename=rename)

    def history(self, which: str, body: dict) -> tuple[int, dict]:
        with self._lock:
            idea = self._need(body.get("file"))
            stale = self._fresh(idea, self._version(body), f"your {which}")
            if stale is not None:
                return stale
            if idea.doc.read_only_reason is not None:
                raise SessionError(409, idea.doc.read_only_reason)
            target = idea.doc.peek_undo() if which == "undo" else idea.doc.peek_redo()
            if target is None:
                return _refused("status", f"nothing to {which}")
            do = idea.doc.undo if which == "undo" else idea.doc.redo
            return self._write(idea, target, do, f"your {which}", prefix=f"can't {which}: ")

    def new_idea(self, body: dict) -> tuple[int, dict]:
        """A new bench file named from `name` (made unique), from the blank bench or an example; it goes on stage."""
        name, author, text = body.get("name"), body.get("author") or "", body.get("idea") or ""
        if not isinstance(name, str) or not name.strip():
            return _refused("name", "give the idea a name")
        if not isinstance(author, str) or not isinstance(text, str):
            return _refused("status", "author and idea must be text")
        example = body.get("example") or None
        if example is not None and example not in examples():
            return _refused("example", f"no example {example!r}; examples: {', '.join(examples())}")
        try:
            content = bench_edits.new_bench_text(name.strip(), author.strip(), text.strip(),
                                                 EXAMPLES / f"{example}.toml" if example else None)
        except OSError as e:
            return _refused("example", f"can't read the example: {e}")
        stem = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "idea"
        with self._lock:   # the poll doesn't see the file before it is an idea of ours
            self._check_open()
            n = 1
            while True:
                path = self.folder / (stem + (f"_{n}" if n > 1 else "") + ".toml")
                try:
                    with open(path, "x", encoding="utf-8") as f:
                        f.write(content)
                    break
                except FileExistsError:
                    n += 1
                except OSError as e:
                    return _refused("status", f"can't write {path.name}: {e}")
            idea = _Idea(path)
            self.ideas[path.name] = idea
            self._changed(idea, idea.raw, "page")
            return 200, {"ok": True, "file": path.name}

    def file(self, path) -> Path:
        """A file under the session folder, or one the bench writes in a results folder the log names (summary.txt,
        the CSVs, the pictures; a gallery's HTML file). Symlinks are followed before the check."""
        if not isinstance(path, str) or not path or "\x00" in path:
            raise SessionError(404, "no such file")
        p = Path(path).expanduser()
        p = (p if p.is_absolute() else self.folder / p).resolve()
        with self._lock:
            self._check_open()
            outs = list(self._outs)
        if p.is_file() and (p.is_relative_to(self.folder) or any(
                (p.parent == r and p.suffix.lower() in RESULT_SUFFIXES) or (p == r and p.suffix.lower() == ".html")
                for r in outs)):
            return p
        raise SessionError(404, "no such file in this session")


# ----------------------------------------------------------------------------------------------------- page jobs

KEEP_JOBS = 10


class _Stopped(Exception):
    """The editor is closing: a queued job doesn't start."""


def _plain(e: BaseException) -> str:
    return str(e) or type(e).__name__


class BenchJobs:
    """The Bench tab's runs, sweeps, compares and galleries: one worker thread per App, jobs in order. Each job holds
    the App's SimLock and the session folder's run lock (`session.recorded`, source "page", so it is logged like a CLI
    or agent run and reaches the ideas' `latest` or the session's `result`); while another process holds the run lock
    the job stays queued."""

    def __init__(self, simlock: SimLock, current: Callable[[], BenchSession | None]):
        self._simlock = simlock
        self._current = current          # the App's open session (its log is re-read when a job finishes)
        self._cond = threading.Condition()
        self.version = 0
        self._jobs: list[dict] = []      # newest first, the last KEEP_JOBS
        self._queue: list[tuple[dict, Path, list[Path], Path, Callable[[], str | None]]] = []
        self._seq = 0
        self._busy = False               # a job is queued or running
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- the page's requests -----------------------------------------------------------------------------
    @staticmethod
    def _runnable(s: BenchSession, file) -> tuple[_Idea, list[dict]]:
        """The idea and its errors as a refusal's errors ([] when it can run)."""
        idea = s._need(file)
        errs = [{"field": p["key"] or "status", "message": p["message"]} for p in idea.problems
                if p["level"] == "error"]
        if not errs and idea.doc.read_only_reason is not None:
            errs = [{"field": "status", "message": idea.doc.read_only_reason}]
        return idea, errs

    def submit(self, s: BenchSession, kind: str, body: dict) -> tuple[int, dict]:
        """Queue a page job on session `s`: {ok: true, id}, or 200 {ok: false, errors} when it can't run."""
        with s._lock:
            s._check_open()
            folder = s.folder
            if kind == "run":
                idea, errs = self._runnable(s, body.get("file"))
                if errs:
                    return 200, {"ok": False, "errors": errs}
                path = idea.doc.path
                files, out = [path], folder / f"{path.stem}-results"
                work = lambda: _run_job(path, out)   # noqa: E731
            elif kind == "sweep":
                idea, errs = self._runnable(s, body.get("file"))
                key, values = body.get("key"), body.get("values")
                if not isinstance(key, str) or not key:
                    errs.append({"field": "key", "message": "key must be a dotted key, e.g. wheel.top.diameter"})
                else:
                    try:
                        _check_path(s._merged(idea, idea.raw), key)
                    except BenchError as e:
                        errs.append({"field": "key", "message": str(e)})
                if not isinstance(values, list) or not values:
                    errs.append({"field": "values", "message": "values must be a non-empty list"})
                if errs:
                    return 200, {"ok": False, "errors": errs}
                path = idea.doc.path
                files, out = [path], folder / f"{path.stem}-sweep"
                work = lambda: _sweep_job(path, {key: list(values)}, out)   # noqa: E731
            elif kind == "compare":
                names = body.get("files")
                if not isinstance(names, list) or not all(isinstance(n, str) for n in names) \
                        or len(set(names)) < 2:
                    return _refused("files", "compare needs two or more different ideas")
                errs, files = [], []
                for name in dict.fromkeys(names):
                    idea, bad = self._runnable(s, name)
                    errs += [{**e, "message": f"{idea.file}: {e['message']}"} for e in bad]
                    files.append(idea.doc.path)
                if errs:
                    return 200, {"ok": False, "errors": errs}
                out = folder / "compare-results"
                work = lambda: _compare_job(files, out)   # noqa: E731
            elif kind == "gallery":
                files, out = [folder], folder / "gallery.html"
                work = lambda: _gallery_job(folder, out)   # noqa: E731
            else:
                raise SessionError(404, f"no bench job {kind!r}")
        with self._cond:
            if s._closed:   # an open replaced the session after the checks above (see gate())
                raise SessionError(409, "the idea session was closed")
            if self._stop.is_set():
                raise SessionError(409, "the editor is closing")
            self._seq += 1
            job = {"id": f"{kind}-{self._seq}", "kind": kind, "files": [f.name for f in files] if kind != "gallery"
                   else [], "state": "queued", "error": None, "out": str(out)}
            self._jobs.insert(0, job)
            del self._jobs[KEEP_JOBS:]
            self._queue.append((job, folder, files, out, work))
            self._busy = True
            self._bump()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, daemon=True, name="bench-jobs")
                self._thread.start()
        return 200, {"ok": True, "id": job["id"]}

    @contextlib.contextmanager
    def gate(self):
        """Hold off submits while an open replaces the session: 409 when a job is queued or running, else the body
        runs with no job able to queue (a submit then finds its session closed)."""
        with self._cond:
            if self._busy:
                raise SessionError(409, "a bench run is in progress; wait for it to finish")
            yield

    @property
    def active(self) -> bool:
        """A job of this App is queued or running."""
        with self._cond:
            return self._busy

    def jobs(self) -> dict:
        with self._cond:
            return {"version": self.version, "jobs": [dict(j) for j in self._jobs]}

    def wait_jobs(self, since: int | None) -> dict:
        end = time.monotonic() + POLL_SECONDS
        with self._cond:
            while since is not None and self.version == since and not self._stop.is_set() \
                    and time.monotonic() < end:
                self._cond.wait(1.0)
            return self.jobs()

    def clear(self) -> None:
        """Forget the finished jobs (another session was opened); the version keeps counting up."""
        with self._cond:
            self._jobs = [j for j in self._jobs if j["state"] in ("queued", "running")]
            self._bump()

    def stop(self, timeout: float = 30.0) -> None:
        """Stop the worker: queued jobs don't start; a running one is waited for up to `timeout` seconds. Never call
        it holding a lock the worker may need. The worker resets itself when it exits (also after a timed-out
        stop), so the next submit starts a new one."""
        with self._cond:
            thread = self._thread
            if thread is None:
                return
            self._stop.set()
            self._cond.notify_all()
        if thread is not threading.current_thread():
            thread.join(timeout)

    def _exit(self) -> None:
        """The worker is leaving on a stop (self._cond held): drop the queue and let a new worker start."""
        for job, *_ in self._queue:
            job.update(state="failed", error="the editor closed before it ran")
        self._queue.clear()
        if self._thread is threading.current_thread():
            self._thread = None
            self._stop.clear()
        self._busy = False
        self._bump()

    # -- the worker ------------------------------------------------------------------------------------
    def _bump(self) -> None:   # self._cond held
        self.version += 1
        self._cond.notify_all()

    def _set(self, job: dict, finished: bool = False, **kw) -> None:
        with self._cond:
            job.update(kw)
            if finished:   # the final state and "not busy" together: open isn't refused once the page sees it
                self._busy = bool(self._queue)
            self._bump()

    def _wait(self, ready: Callable[[], bool]) -> None:
        while not ready():
            if self._stop.wait(0.1):
                raise _Stopped()

    def _take_simlock(self) -> bool:
        try:
            self._simlock.acquire("bench")
        except SimBusy:   # a drive session or a scenario run (not in bench mode, but never two at once)
            return False
        return True

    def _work(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._stop.is_set():
                    self._cond.wait()
                if self._stop.is_set() or not self._queue:
                    self._exit()
                    return
                job, folder, files, out, work = self._queue.pop(0)
            try:
                self._execute(job, folder, files, out, work)
                self._sync(folder)   # latest shows the run before the job shows as done
                self._set(job, finished=True, state="done", error=None)
            except _Stopped:
                self._set(job, finished=True, state="failed", error="the editor closed before it ran")
            except BaseException as e:  # noqa: BLE001 - a job must never stay "queued" or "running"
                self._sync(folder)
                self._set(job, finished=True, state="failed", error=_plain(e))
            finally:
                with self._cond:   # a safety net: the job is settled and busy matches the queue
                    if job["state"] in ("queued", "running"):
                        job.update(state="failed", error="the job stopped unexpectedly")
                    self._busy = bool(self._queue)
                    self._bump()

    def _execute(self, job: dict, folder: Path, files: list[Path], out: Path, work) -> None:
        def free() -> bool:   # another process (the agent, the CLI) holds the folder's run lock: stay queued
            try:
                return session.lock_holder(folder) is None
            except OSError:
                return True   # an unwritable folder: recorded() runs it unshared
        self._wait(self._take_simlock)
        try:
            self._wait(free)
            with session.recorded(folder, job["kind"], files, out, "page"):
                self._set(job, state="running")
                work()
        finally:
            self._simlock.release("bench")

    def _sync(self, folder: Path) -> None:
        """Re-read the open session's log now, so a finished job's `latest` or `result` is in the state as the job
        ends."""
        s = self._current()
        if s is not None and s.folder == folder:
            try:
                s.sync()
            except Exception:
                logging.getLogger("scrimmage.assembler").exception("bench session sync after a job")


def _run_job(path: Path, out: Path) -> None:
    """`bench run`: the results and pictures in `out`. A failed energy check is in the results (summary.txt and the
    first headline), so `latest.energy_failed` reads it the same for every source; the job is done."""
    from ..bench.pictures import PICTURE_TRACE_HZ, write_pictures
    from ..bench.report import write_results
    from ..bench.spec import load_bench
    from ..render import RenderUnavailable
    result = bench_run.run_bench(load_bench(path), trace_hz=PICTURE_TRACE_HZ)
    out = write_results(result, out)
    try:
        write_pictures(result, out)
    except RenderUnavailable as e:
        logging.getLogger("scrimmage.assembler").warning("no pictures: %s", e)


def _sweep_job(path: Path, sweep: dict, out: Path) -> None:
    """`bench sweep` with the page's one key and values in place of the file's [sweep]: results.csv and a chart."""
    from ..bench import compare
    from ..launcher import report
    _, benches = compare.sweep_benches(path, sweep)
    rows = compare.rows([(c, bench_run.run_bench(b)) for c, b in benches])
    out.mkdir(parents=True, exist_ok=True)
    report.write_csv(rows, out / "results.csv", compare.BENCH_METRICS)
    report.write_charts(rows, compare.default_charts(list(sweep)), out, None, compare.BENCH_METRICS,
                        compare.CHART_KINDS)


def _compare_job(files: list[Path], out: Path) -> None:
    """`bench compare`: compare.csv and compare.png, the piece and feed of the first idea."""
    from PIL import Image

    from ..bench import compare, gallery
    from ..bench.pictures import PICTURE_TRACE_HZ
    from ..launcher import report
    results = compare.compare_benches(files, trace_hz=PICTURE_TRACE_HZ)
    rows = compare.rows([({"idea": n}, r) for n, r in zip(compare.idea_labels(results), results)])
    out.mkdir(parents=True, exist_ok=True)
    report.write_csv(rows, out / "compare.csv", compare.BENCH_METRICS)
    Image.fromarray(gallery.compare_picture(results)).save(out / "compare.png")


def _gallery_job(folder: Path, out: Path) -> None:
    """`bench gallery`: every bench file in the folder run once, one HTML page (a file that didn't run is listed with
    its problem)."""
    from ..bench import gallery
    gallery.write_gallery(folder, out, gallery.run_folder(folder))
