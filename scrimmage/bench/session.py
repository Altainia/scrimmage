"""A bench session: a bench file's parent folder, with a small `.scrimmage-bench/` state folder beside the files.

The CLI, the MCP tools and the Bench tab (three processes) share it: a run log they all append to, the list of keys
"talked about" for each file, and a lock so only one bench runs at a time. Nothing here is needed to run a bench.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal

from .spec import _SECTIONS, _SINGLES

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

STATE_DIR = ".scrimmage-bench"
_held = threading.local()  # folders this thread holds run_lock for
FOCUS_MAX = 12


def state_dir(folder: Path) -> Path:
    d = Path(folder) / STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


# ----------------------------------------------------------------------------------------------------- OS locks

def _try_lock(f) -> bool:
    """Take an exclusive lock on the open file without blocking; the OS drops it when the process dies."""
    try:
        if fcntl is not None:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return False
    return True


def _unlock(f) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        else:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


@contextlib.contextmanager
def _short_lock(folder: Path, name: str) -> Iterator[None]:
    """A brief lock around a read-modify-write of one state file."""
    with open(state_dir(folder) / name, "a+b") as f:
        while not _try_lock(f):
            time.sleep(0.01)
        try:
            yield
        finally:
            _unlock(f)


# ----------------------------------------------------------------------------------------------------- the run log

def _log_lines(folder: Path) -> list[dict]:
    p = Path(folder) / STATE_DIR / "log.jsonl"
    if not p.exists():
        return []
    events = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue  # a torn line
        if isinstance(e, dict) and isinstance(e.get("seq"), int):
            events.append(e)
    return events


def log_event(folder: Path, kind: Literal["run", "sweep", "compare", "gallery"], files: list[Path], out: Path,
              source: Literal["cli", "agent", "page"], ok: bool, error: str | None = None) -> None:
    """Append one event to the run log. Call it after the results folder is complete."""
    with _short_lock(folder, "log.lock"):
        events = _log_lines(folder)
        seq = events[-1]["seq"] + 1 if events else 0
        event = {"seq": seq, "t": time.time(), "kind": kind, "files": [str(Path(f).resolve()) for f in files],
                 "out": str(Path(out).resolve()), "source": source, "ok": ok, "error": error}
        p = state_dir(folder) / "log.jsonl"
        text = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        prefix = "\n" if text and not text.endswith("\n") else ""  # a torn last line must not swallow this one
        with open(p, "a", encoding="utf-8") as f:
            f.write(prefix + json.dumps(event) + "\n")


def read_events(folder: Path, since: int = 0) -> list[dict]:
    """The events with seq >= since, oldest first."""
    return [e for e in _log_lines(folder) if e["seq"] >= since]


# ----------------------------------------------------------------------------------------------------- talked about

def _focus_all(folder: Path) -> dict:
    p = Path(folder) / STATE_DIR / "focus.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def bump(folder: Path, file: Path, keys: list[str]) -> None:
    """Move `keys` to the front of the file's talked-about list (at most FOCUS_MAX kept)."""
    with _short_lock(folder, "focus.lock"):
        data = _focus_all(folder)
        old = [k for k in data.get(Path(file).name, []) if k not in keys]
        data[Path(file).name] = list(dict.fromkeys([*keys, *old]))[:FOCUS_MAX]
        tmp = state_dir(folder) / "focus.json.tmp"
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, tmp.with_name("focus.json"))


def focus(folder: Path, file: Path) -> list[str]:
    """The keys talked about for this file, newest first."""
    keys = _focus_all(folder).get(Path(file).name, [])
    return [k for k in keys if isinstance(k, str)][:FOCUS_MAX]


def _entries(raw: dict, section: str) -> dict[str, dict]:
    """{entry key: table}: `bar.k` for a list entry, `shaft.<name>` for a shaft, `run` for a single table."""
    v = raw.get(section)
    if section == "shaft":
        return {f"shaft.{n}": t for n, t in (v or {}).items() if isinstance(t, dict)}
    if section in _SINGLES or section == "bench":
        return {section: v} if isinstance(v, dict) else {}
    return {f"{section}.{e.get('name')}": e for e in (v or []) if isinstance(e, dict)}


def changed_keys(old_raw: dict, new_raw: dict) -> list[str]:
    """Dotted keys whose values differ. An added or removed list entry or shaft reports its entry key plus every key
    inside it except `name`; a changed entry only the keys that changed."""
    out: list[str] = []
    for section in _SECTIONS:
        old, new = _entries(old_raw, section), _entries(new_raw, section)
        single = section in _SINGLES or section == "bench"
        for ekey in dict.fromkeys([*old, *new]):
            a, b = old.get(ekey), new.get(ekey)
            if a is None or b is None:
                if not single:
                    out.append(ekey)
                out += [f"{ekey}.{k}" for k in (a if b is None else b) if k != "name"]
                continue
            out += [f"{ekey}.{k}" for k in dict.fromkeys([*a, *b]) if k != "name" and a.get(k) != b.get(k)]
    return out


# ----------------------------------------------------------------------------------------------------- the run lock

def _holder_path(folder: Path) -> Path:
    return Path(folder) / STATE_DIR / "holder.json"


def _read_holder(folder: Path) -> dict | None:
    try:
        h = json.loads(_holder_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return h if isinstance(h, dict) else None


@contextlib.contextmanager
def run_lock(folder: Path, holder: dict, wait: Callable[[dict], None] | None = None) -> Iterator[None]:
    """Hold the folder's run lock (one bench at a time across processes). Blocks until free; calls `wait(other)` once
    if it has to wait. `holder` is {source, file}; pid and since are added."""
    key = str(Path(folder).resolve())
    mine = _held.__dict__.setdefault("folders", set())
    if key in mine:
        raise RuntimeError(f"run_lock is already held by this thread for {key}")
    with open(state_dir(folder) / "run.lock", "a+b") as f:
        told = False
        while not _try_lock(f):
            if not told and wait is not None:
                told = True
                wait(_read_holder(folder) or {})
            time.sleep(0.05)
        mine.add(key)
        try:
            _holder_path(folder).write_text(
                json.dumps({**holder, "pid": os.getpid(), "since": time.time()}), encoding="utf-8")
            yield
        finally:
            mine.discard(key)
            with contextlib.suppress(OSError):
                _holder_path(folder).unlink()
            _unlock(f)


def lock_holder(folder: Path) -> dict | None:
    """Who holds the run lock now, or None. A stale holder.json from a dead process reads as None."""
    if not (Path(folder) / STATE_DIR).is_dir():
        return None
    with open(Path(folder) / STATE_DIR / "run.lock", "a+b") as f:
        if _try_lock(f):
            _unlock(f)
            return None
    return _read_holder(folder)


@contextlib.contextmanager
def recorded(folder: Path, kind: Literal["run", "sweep", "compare", "gallery"], files: list[Path], out: Path,
             source: Literal["cli", "agent", "page"], wait: Callable[[dict], None] | None = None) -> Iterator[None]:
    """Run one bench job under the run lock, then log it (after the body, so the results are complete). A failure is
    logged with its message and re-raised."""
    def log(ok: bool, error: str | None = None) -> None:
        try:
            log_event(folder, kind, files, out, source, ok, error)
        except OSError as e:  # never hide the bench's own outcome behind a logging failure
            print(f"scrimmage: warning: could not write the session log: {e}", file=sys.stderr)

    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(
                run_lock(folder, {"source": source, "file": Path(files[0]).name if files else ""}, wait))
        except OSError as e:
            print(f"scrimmage: warning: {folder} is not writable ({e}); this run is not shared with the Bench tab",
                  file=sys.stderr)
            yield
            return
        try:
            yield
        except Exception as e:
            log(False, str(e))
            raise
        log(True)
