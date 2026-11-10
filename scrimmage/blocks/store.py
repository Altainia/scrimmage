"""Kept runs of a block test, each a self-contained replay: `<test stem>-runs/` next to the test file.

    <test stem>-runs/
      001/ ... 010/          the newest KEEP_RUNS runs (folder names count up and are never reused); a run the student
                             stopped doesn't count (`_prune_runs`)
        result.json          status, outcome, t_end, checks, hung, started (ISO time), test (the test file's TOML
                             as it ran), sweep (None, or what was swept and the value), feeds (one row per fed
                             piece), counts (fed, in, stuck, missed; None without stations), seed (the stations'
                             seed; None without stations), marks (moments the replay marks: {t, kind, text,
                             ball}, e.g. a slip out of a grabber, ball the piece's index), notes (what the numbers
                             rest on: the grips' UNCALIBRATED frictions and the pneumatic estimates,
                             scrimmage/blocks/run_notes.py's run_notes),
                             seated (each rod with a piece on it at the end: its depth in words), goals (the
                             range's goal counts; None without a [shooter]), air (the air's start, end and lowest
                             psi, the compressor's seconds and when the tanks fell below the working pressure;
                             None without cylinders: RunResult.air), clearance (each element the test measures:
                             its smallest clearance in inches and when, both None when never within 0.5 m; None
                             when it measures none: RunResult.clearance) and hopper (whether the test names where a
                             piece should end up); for a match (`match = true`) only: match (the phases' run times,
                             whether LEAVE was earned and the climb level held at the end), points (auto, teleop,
                             endgame, total and the items: RunResult.points) and rules (the breaches; [] for none);
                             and, for a run made inside machine text (a sweep's, a spread's,
                             MCP test_run's: its words above are machine text), words: the outcome, the checks'
                             words, the marks' text, seated and notes in every Language and Units setting
                             (RunResult.words), which the Test tab shows in the viewer's
        signals.csv          t and each signal, SIGNAL_HZ (scrimmage/blocks/run.py)
        view.json            model_view(world, include_world=True) at the run's start, meshes included
        motion.bin           the movers' poses at MOTION_HZ (below)
      sweep-001/ ...         the newest KEEP_SWEEPS sweeps (scrimmage/blocks/sweep.py)
        001/ ...             one run per value, as above
        table.csv, sweep.json
        table.json           the table for the Test tab, in the viewer's setting (sweep.py's _write_words)
      spread-001/ ...        the newest KEEP_SPREADS floor spreads (scrimmage/blocks/spread.py), kept as sweeps are
        001/ ...             one run per placement, as above
        table.csv, spread.json
        table.json           the table for the Test tab, as a sweep's (spread.py's _write_words)

A run keeps the geometry it was run with (view.json), so its replay still draws the old robot after the part is
imported again; no MuJoCo model is kept. Every folder is written as a hidden temp folder, then renamed into place.

motion.bin: a header struct "<4sHII" (b"SCRM", version 1, frames, movers), then per frame "<d" t and movers x 7
float32 (x, y, z, qw, qx, qy, qz) in the order of `scrimmage.movers.movers`, which view.json's geoms refer to.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import shutil
import struct
import tempfile
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from .. import i18n, units
from ..cad.settings import replace_file
from .program import Program, dumps

KEEP_RUNS = 10
KEEP_SWEEPS = 3
KEEP_SPREADS = 3
STALE_S = 3600.0          # a temp file or folder this old was left by a process that was killed
MOTION_MAGIC = b"SCRM"
MOTION_VERSION = 1
_HEADER = struct.Struct("<4sHII")
_RUN_RE = re.compile(r"\d{3,}")
_SWEEP_RE = re.compile(r"sweep-(\d{3,})")
_SPREAD_RE = re.compile(r"spread-(\d{3,})")
_ID_RE = re.compile(r"((sweep|spread)-\d{3,}/)?\d{3,}")
_GROUPS = {"sweep": (_SWEEP_RE, KEEP_SWEEPS), "spread": (_SPREAD_RE, KEEP_SPREADS)}   # prefix -> folders, how many kept


def runs_dir(test_path) -> Path:
    """`<test stem>-runs/` next to the test file (`climb.test.toml` -> `climb-runs/`)."""
    p = Path(test_path)
    stem = p.name[:-len(".test.toml")] if p.name.endswith(".test.toml") else p.stem
    return p.parent / f"{stem}-runs"


def _numbered(base: Path, pattern: re.Pattern) -> list[tuple[int, Path]]:
    if not base.is_dir():
        return []
    out = []
    for p in base.iterdir():
        m = pattern.fullmatch(p.name)
        if m and p.is_dir():
            out.append((int(m.group(1) if m.groups() else m.group(0)), p))
    return sorted(out)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _when(started) -> float:
    """An ISO time (with its UTC offset) as a timestamp, for sorting; 0 for none or a bad one."""
    try:
        t = datetime.fromisoformat(started)
    except (TypeError, ValueError):
        return 0.0
    if t.tzinfo is None:
        t = t.astimezone()
    return t.timestamp()


def _id_key(run_id: str) -> tuple[int, int]:
    """("004" -> (0, 4), "sweep-002/003" or "spread-002/003" -> (2, 3)): the newer sweep and the later run sort
    higher."""
    head, _, tail = run_id.rpartition("/")
    return (int(head.rpartition("-")[2]) if head else 0, int(tail))


def clean_stale(folder: Path, patterns: tuple[str, ...], age_s: float = STALE_S) -> None:
    """Remove temp files and folders matching `patterns` in folder, older than age_s: what a process killed while
    writing a run or a sweep left behind."""
    if not folder.is_dir():
        return
    now = time.time()
    for pattern in patterns:
        for p in folder.glob(pattern):
            try:
                if now - p.stat().st_mtime < age_s:
                    continue
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink()
            except OSError:
                pass        # held or gone: the next clean tries again


TEMP_RUNS = (".run-*", ".sweep-*", ".spread-*")


def _plain(v):
    if isinstance(v, np.generic):
        return v.item()
    if isinstance(v, np.ndarray):
        return v.tolist()
    raise TypeError(i18n.t("can't write {value} to JSON", value=repr(v)))


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, default=_plain, ensure_ascii=False), encoding="utf-8")


def _place(tmp: Path, base: Path, pattern: re.Pattern, name) -> Path:
    """Rename the finished temp folder to the next free number in base; returns where it went."""
    for _ in range(100):
        nums = _numbered(base, pattern)
        dst = base / name((nums[-1][0] if nums else 0) + 1)
        if dst.exists():
            continue
        try:
            replace_file(tmp, dst)
            return dst
        except FileExistsError:         # another writer took that number meanwhile
            continue
        except OSError:
            if dst.exists():
                continue
            raise
    raise OSError(i18n.t("couldn't find a free folder name in {base}", base=base))


def _status(folder: Path) -> str | None:
    try:
        return json.loads((folder / "result.json").read_text(encoding="utf-8")).get("status")
    except (OSError, ValueError, AttributeError):
        return None


def _prune_runs(base: Path) -> None:
    """Leave the newest KEEP_RUNS runs that weren't stopped, and the stopped ones newer than the oldest of those (the
    newest KEEP_RUNS of them): pressing Stop doesn't push a finished run out."""
    runs = _numbered(base, _RUN_RE)
    stopped = {k for k, p in runs if _status(p) == "cancelled"}
    done = [k for k, _ in runs if k not in stopped]
    keep = set(done[-KEEP_RUNS:])
    oldest = done[-KEEP_RUNS] if len(done) >= KEEP_RUNS else -1
    keep |= set([k for k, _ in runs if k in stopped and k > oldest][-KEEP_RUNS:])
    for k, p in runs:
        if k not in keep:
            shutil.rmtree(p, ignore_errors=True)


def write_motion(path: Path, motion: list, n_movers: int) -> None:
    with open(path, "wb") as f:
        f.write(_HEADER.pack(MOTION_MAGIC, MOTION_VERSION, len(motion), n_movers))
        for t, poses in motion:
            f.write(struct.pack("<d", float(t)))
            f.write(np.ascontiguousarray(poses, dtype="<f4").reshape(n_movers, 7).tobytes())


def read_motion(path) -> tuple[int, np.ndarray, np.ndarray]:
    """(movers, times (frames,), poses (frames, movers, 7) float32)."""
    raw = Path(path).read_bytes()
    if len(raw) < _HEADER.size:
        raise ValueError(i18n.t("{path} isn't a replay file", path=path))
    magic, version, frames, n = _HEADER.unpack_from(raw)
    if magic != MOTION_MAGIC:
        raise ValueError(i18n.t("{path} isn't a replay file", path=path))
    if version != MOTION_VERSION:
        raise ValueError(i18n.t("{path} is a replay file from a newer scrimmage (version {version})",
                                path=path, version=version))
    frame = np.dtype([("t", "<f8"), ("poses", "<f4", (n, 7))])
    if len(raw) != _HEADER.size + frames * frame.itemsize:
        raise ValueError(i18n.t("{path} is cut short", path=path))
    data = np.frombuffer(raw, frame, count=frames, offset=_HEADER.size)
    return n, data["t"].copy(), data["poses"].astype(np.float32)


def _write_signals(path: Path, signals: dict) -> None:
    names = list(signals)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(names)
        for row in zip(*(signals[n] for n in names)):
            w.writerow([repr(float(v)) for v in row])


def _read_signals(path: Path) -> dict[str, list[float | None]]:
    """Each signal's samples; an empty sample (NaN: a seated depth while no piece is on the rod) is None, as JSON
    carries it (JSON has no NaN, and the browser's JSON.parse refuses one)."""
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"))))
    if not rows:
        return {}
    names, body = rows[0], rows[1:]
    return {n: [_sample(r[i]) for r in body] for i, n in enumerate(names)}


def _sample(text: str) -> float | None:
    v = float(text)
    return None if math.isnan(v) else v


def _seed(folder: Path):
    try:
        return json.loads((folder / "result.json").read_text(encoding="utf-8")).get("seed")
    except (OSError, ValueError, AttributeError):
        return None


def save_run(result, program: Program, base, sweep: dict | None = None) -> Path:
    """Keep a run in a new folder `base/<NNN>/`; returns it. A plain run (no `sweep`) leaves the newest KEEP_RUNS
    plain runs in base, not counting stopped ones (`_prune_runs`); a sweep's runs are pruned with their sweep.

    A plain run whose stations' seed differs from the newest kept plain run's (when that one had a seed) says so: its
    outcome, `result.outcome` too, gains "; a new shuffle (seed N)", so a comparison of two runs is seen to be fair or
    not."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    clean_stale(base, TEMP_RUNS)
    seed = getattr(result, "seed", None)
    if sweep is None and seed is not None:
        runs = _numbered(base, _RUN_RE)
        before = _seed(runs[-1][1]) if runs else None
        if before is not None and before != seed:
            result.outcome = f"{result.outcome}; a new shuffle (seed {seed})"
            for key, words in (getattr(result, "words", None) or {}).items():
                lang, _, system = key.partition("|")
                with units.shown_in(system, lang):
                    words["outcome"] = i18n.t("{outcome}; a new shuffle (seed {seed})", outcome=words["outcome"],
                                              seed=seed)
    tmp = Path(tempfile.mkdtemp(dir=base, prefix=".run-"))
    try:
        _write_json(tmp / "result.json", {
            "status": result.status, "outcome": result.outcome, "t_end": result.t_end, "checks": result.checks,
            "hung": result.hung, "started": getattr(result, "started", "") or _now(),
            "test": dumps(program), "sweep": sweep, "feeds": getattr(result, "feeds", []),
            "counts": getattr(result, "counts", None), "seed": seed, "marks": getattr(result, "marks", []),
            "notes": getattr(result, "notes", []), "seated": getattr(result, "seated", {}),
            "goals": getattr(result, "goals", None), "air": getattr(result, "air", None),
            "clearance": getattr(result, "clearance", None), "hopper": bool(program.hopper),
            "pieces": getattr(result, "pieces", []),
            **({"match": result.match, "points": result.points, "rules": result.rules}
               if getattr(result, "match", None) is not None else {}),
            **({"words": result.words} if getattr(result, "words", None) else {})})
        _write_signals(tmp / "signals.csv", result.signals)
        _write_json(tmp / "view.json", result.view)
        write_motion(tmp / "motion.bin", result.motion, len(result.movers))
        folder = _place(tmp, base, _RUN_RE, lambda k: f"{k:03d}")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if sweep is None:
        _prune_runs(base)
    return folder


def _entry(base: Path, folder: Path) -> dict | None:
    try:
        r = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None                     # a folder being deleted, or not a run
    out = {"id": folder.relative_to(base).as_posix(), "status": r.get("status"), "outcome": r.get("outcome"),
           "started": r.get("started"), "sweep": r.get("sweep"), "cancelled": r.get("status") == "cancelled"}
    words = r.get("words")
    if isinstance(words, dict):         # a run made in machine text: its outcome in every setting
        out["words"] = {k: {"outcome": w.get("outcome")} for k, w in words.items() if isinstance(w, dict)}
    return out


def list_runs(base) -> list[dict]:
    """The kept runs, plain and in sweeps and spreads, newest first: id ("004", "sweep-002/003", "spread-001/002"),
    status, outcome, started, sweep, cancelled (the student stopped it), and for a run made in machine text its
    outcome in every setting (words: {setting_key: {outcome}})."""
    base = Path(base)
    folders = [p for _, p in _numbered(base, _RUN_RE)]
    for _, s in _numbered(base, _SWEEP_RE) + _numbered(base, _SPREAD_RE):
        folders += [p for _, p in _numbered(s, _RUN_RE)]
    out = [e for e in (_entry(base, p) for p in folders) if e is not None]
    out.sort(key=lambda e: (_when(e["started"]), _id_key(e["id"])), reverse=True)
    return out


def run_folder(base, run_id: str) -> Path:
    """The folder of the kept run `run_id` ("004", "sweep-002/003", "spread-001/002"); FileNotFoundError for an id
    that isn't one."""
    base = Path(base)
    if not isinstance(run_id, str) or not _ID_RE.fullmatch(run_id):
        raise FileNotFoundError(i18n.t("no run {run_id}", run_id=repr(run_id)))
    folder = base / run_id
    if not (folder / "result.json").is_file():
        raise FileNotFoundError(i18n.t("no run {run_id} in {base}", run_id=repr(run_id), base=base))
    return folder


def load_result(base, run_id: str) -> dict:
    """A kept run without its replay: result.json's fields plus id and signals (t and each signal)."""
    folder = run_folder(base, run_id)
    out = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    out["id"] = run_id
    out["signals"] = _read_signals(folder / "signals.csv")
    return out


def load_run(base, run_id: str) -> dict:
    """A kept run: result.json's fields plus signals (t and each signal), view and motion (the motion.bin path)."""
    out = load_result(base, run_id)
    folder = Path(base) / run_id
    out["view"] = json.loads((folder / "view.json").read_text(encoding="utf-8"))
    out["motion"] = folder / "motion.bin"
    return out


def _table(folder: Path, n: int) -> dict | None:
    """A sweep's or spread's table.json (its table in every Language and Units setting: sweep.py's and spread.py's
    _write_words), or None for an older folder without one, or one that doesn't match table.csv's rows: the Test
    tab then shows table.csv's machine text as it is."""
    try:
        table = json.loads((folder / "table.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(table, dict) or not isinstance(table.get("rows"), list) or len(table["rows"]) != n \
            or not isinstance(table.get("words"), dict):
        return None
    return table


def list_sweeps(base) -> list[dict]:
    """The kept sweeps, newest first: id ("sweep-002"), target, values, cancelled, problems, rows (table.csv's,
    each a dict of its columns as text) and table (table.json's, or None: _table). A sweep whose files can't be read
    (being deleted) is left out."""
    out = []
    for _, folder in reversed(_numbered(Path(base), _SWEEP_RE)):
        try:
            meta = json.loads((folder / "sweep.json").read_text(encoding="utf-8"))
            with open(folder / "table.csv", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except (OSError, ValueError):
            continue
        out.append({"id": folder.name, "target": meta.get("target"), "values": meta.get("values"),
                    "cancelled": bool(meta.get("cancelled")), "problems": meta.get("problems", []), "rows": rows,
                    "table": _table(folder, len(rows))})
    return out


def list_spreads(base) -> list[dict]:
    """The kept floor spreads, newest first: id ("spread-002"), spread, placements, picked, sentence, cancelled,
    rows (table.csv's, each a dict of its columns as text) and table (table.json's, or None: _table). A spread whose
    files can't be read is left out."""
    out = []
    for _, folder in reversed(_numbered(Path(base), _SPREAD_RE)):
        try:
            meta = json.loads((folder / "spread.json").read_text(encoding="utf-8"))
            with open(folder / "table.csv", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except (OSError, ValueError):
            continue
        out.append({"id": folder.name, "spread": meta.get("spread"), "placements": meta.get("placements"),
                    "picked": meta.get("picked"), "sentence": meta.get("sentence"),
                    "cancelled": bool(meta.get("cancelled")), "rows": rows, "table": _table(folder, len(rows))})
    return out


def new_sweep_folder(base: Path, prefix: str = "sweep") -> Path:
    """A hidden temp folder in base to build a sweep (or a spread: prefix "spread") in; `place_sweep` renames it
    into place."""
    base.mkdir(parents=True, exist_ok=True)
    clean_stale(base, TEMP_RUNS)
    return Path(tempfile.mkdtemp(dir=base, prefix=f".{prefix}-"))


def _cancelled(folder: Path, prefix: str) -> bool:
    try:
        return bool(json.loads((folder / f"{prefix}.json").read_text(encoding="utf-8")).get("cancelled"))
    except (OSError, ValueError):
        return False


def place_sweep(tmp: Path, base: Path, cancelled: bool = False, prefix: str = "sweep") -> Path:
    """Rename a finished sweep (or spread: prefix "spread") into place. A complete one leaves the newest KEEP_SWEEPS
    (KEEP_SPREADS) complete ones (and the cancelled ones newer than the oldest of those); a cancelled one doesn't
    count and prunes nothing."""
    pattern, keep = _GROUPS[prefix]
    try:
        folder = _place(tmp, base, pattern, lambda k: f"{prefix}-{k:03d}")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if not cancelled:
        sweeps = _numbered(base, pattern)
        complete = [k for k, p in sweeps if not _cancelled(p, prefix)]
        if len(complete) > keep:
            oldest_kept = complete[-keep]
            for k, p in sweeps:
                if k < oldest_kept:
                    shutil.rmtree(p, ignore_errors=True)
    return folder
