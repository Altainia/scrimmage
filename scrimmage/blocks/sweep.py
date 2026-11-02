"""A sweep: the same test run at up to MAX_SWEEP values of one setting, a run kept for each and a table of them.

The setting (a target) is one of

    {"block": [place...], "key": "volts" | "seconds" | "to" | "distance"}   a block's number (place: 0-based, outermost
                                                                           first, as a check's place)
    {"robot": "mass" | "com_forward" | "com_left" | "com_up"}              the stand-in chassis' mass / com_offset
    {"mount": "<part>", "axis": "x" | "y" | "z" | "yaw"}                   a [[part]]'s pos (m) / rot yaw (deg)

A block value changes the test; a robot or mount value changes a copy of the robot config, written as a hidden
temp file next to it (so its meshes and part folders still resolve) and deleted after that run (tried again while
another program holds it; one still there is named in sweep.json's `problems`). Temp copies and folders over an hour
old, left by a process that was killed, are removed when a sweep starts. A value that can't build or run ("mass must
be greater than 0"), or whose run fails in any other way, is a row with that message, not the end of the sweep.

The sweep is kept as `base/sweep-<NNN>/`: a run per value (`001/` ...), `table.csv` (value, status, outcome, t_end,
and passed / failed for each check) and `sweep.json` (target, values, and `cancelled` / `problems` when there are
any). Run `<k>/` is row k's (none for a value that couldn't run). The newest KEEP_SWEEPS complete sweeps are kept; a
cancelled sweep (stopped, or interrupted, with the runs done so far) doesn't count and prunes nothing.
"""

from __future__ import annotations

import csv
import json
import math
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

import tomlkit

from ..cad.settings import REPLACE_WAITS, replace_file
from . import store
from .program import Program, TestFileError, from_json, to_json
from .run import TestRun, _where

MAX_SWEEP = 12
BLOCK_KEYS = ("volts", "seconds", "to", "distance")
ROBOT_KEYS = {"mass": None, "com_forward": 0, "com_left": 1, "com_up": 2}
AXES = {"x": ("pos", 0), "y": ("pos", 1), "z": ("pos", 2), "yaw": ("rot", 2)}
COULDNT_RUN = "couldn't run"


# ---- the target -----------------------------------------------------------------------------------------------------

def _kind(target) -> str:
    if isinstance(target, dict):
        if set(target) == {"block", "key"} and target["key"] in BLOCK_KEYS and isinstance(target["block"], list) \
                and target["block"] and all(isinstance(i, int) and not isinstance(i, bool) for i in target["block"]):
            return "block"
        if set(target) == {"robot"} and target["robot"] in ROBOT_KEYS:
            return "robot"
        if set(target) == {"mount", "axis"} and isinstance(target["mount"], str) and target["axis"] in AXES:
            return "mount"
    raise ValueError(f"can't sweep {target!r}: sweep a block's volts, seconds, to or distance, the robot's mass or "
                     "center of mass, or where a part is mounted")


def _block_json(blocks: list, place: list) -> dict:
    """The block at `place` in to_json's blocks (raises TestFileError naming the place when it isn't there)."""
    b = None
    for depth, i in enumerate(place):
        where = _where(tuple(place[:depth + 1]))
        if i < 0 or i >= len(blocks):
            raise TestFileError(f"there's no {where.split(' inside ')[0]}" +
                                (f" inside {_where(tuple(place[:depth]))}" if depth else ""))
        b = blocks[i]
        if depth < len(place) - 1:
            if b["do"] != "repeat":
                raise TestFileError(f"{where} isn't a repeat, so it has no blocks inside")
            blocks = b["body"]
    return b


def _edit_program(program: Program, place: list, key: str, value: float) -> Program:
    d = to_json(program)
    b = _block_json(d["blocks"], place)
    if key not in b:
        raise TestFileError(f"{_where(tuple(place))} has no {key}")
    b[key] = value
    return from_json(d, program.path)


def _set(table, key: str, i: int | None, value: float) -> None:
    if i is None:
        table[key] = value
        return
    if key not in table:
        arr = tomlkit.array()
        arr.extend([0.0, 0.0, 0.0])
        table[key] = arr
    table[key][i] = value


def _edit_config(text: str, target: dict, value: float) -> str:
    """The robot config's text with the target set to value, comments kept."""
    doc = tomlkit.parse(text)
    parts = list(doc.get("part", []))
    if "robot" in target:
        chassis = next((p for p in parts if p.get("stock") == "chassis"), None)
        if chassis is None:
            what = "change the mass of" if target["robot"] == "mass" else "move the center of gravity of"
            raise ValueError(f"this robot has no stand-in chassis (stock = \"chassis\") to {what}")
        i = ROBOT_KEYS[target["robot"]]
        _set(chassis, "mass" if i is None else "com_offset", i, value)
    else:
        part = next((p for p in parts if p.get("name") == target["mount"]), None)
        if part is None:
            raise ValueError(f"the robot has no part named {target['mount']!r}")
        key, i = AXES[target["axis"]]
        _set(part, key, i, value)
    return tomlkit.dumps(doc)


# ---- one value ------------------------------------------------------------------------------------------------------

def _remove(path: Path) -> bool:
    """Delete a file, tried again for about a second while it's held (on Windows a virus scanner or the search
    indexer briefly holds a file just written); False when it's still there after that."""
    for wait in (*REPLACE_WAITS, None):
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if wait is None:
                return False
            time.sleep(wait)
        except OSError:
            return False
    return False


def _temp_config(config: Path, text: str) -> Path:
    fd, name = tempfile.mkstemp(dir=config.parent, prefix=f".{config.stem}.sweep-", suffix=".toml")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return Path(name)


def _world(config: Path, tmp: Path):
    """The World of the temp copy `tmp` of the config; an error names the config, not the temp copy."""
    from ..config import build_world, load_config
    try:
        return build_world(load_config(tmp))
    except (ValueError, OSError) as e:
        msg = str(e)
        for s in (str(tmp.resolve()), str(tmp), tmp.name):
            msg = msg.replace(s, config.name)
        raise ValueError(msg) from None


def _plain_error(e: BaseException) -> str:
    """An unexpected error's message for a row."""
    text = str(e).strip()
    return f"something went wrong ({type(e).__name__}{': ' + text if text else ''})"


def _check_column(c: dict) -> str:
    return f"{_where(tuple(c['place']))}: {c['text']}"


# ---- the sweep ------------------------------------------------------------------------------------------------------

def run_sweep(program: Program, target: dict, values: list[float], base, on_progress=None,
              cancel: threading.Event | None = None) -> Path:
    """Run the test at each value; returns the sweep's folder. `on_progress(k, n, row)` is called after each value
    (k from 1); setting `cancel` ends the run under way and the sweep."""
    from .run import world_for
    kind = _kind(target)
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("a sweep needs at least one value")
    if len(values) > MAX_SWEEP:
        raise ValueError(f"a sweep tries at most {MAX_SWEEP} values")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values):
        raise ValueError("a sweep's values must be numbers")
    values = [float(v) for v in values]
    config = Path(program.path).parent / program.robot
    text = None
    if kind == "block":
        b = _block_json(to_json(program)["blocks"], target["block"])     # the target is there (else it's refused)
        if target["key"] not in b:
            raise TestFileError(f"{_where(tuple(target['block']))} has no {target['key']}")
    else:
        text = config.read_text(encoding="utf-8")
        _edit_config(text, target, values[0])                          # the part or chassis is there

    base = Path(base)
    if kind != "block":
        store.clean_stale(config.parent, (f".{config.stem}.sweep-*.toml",))
    tmp = store.new_sweep_folder(base)
    rows: list[dict] = []
    left: list[str] = []            # temp configs that couldn't be removed
    cancelled = False
    try:
        for i, v in enumerate(values):
            row = {"value": v, "status": COULDNT_RUN, "outcome": "", "t_end": None, "checks": {}}
            result = temp = None
            try:
                if kind == "block":
                    p = _edit_program(program, target["block"], target["key"], v)
                    world = world_for(p)
                else:
                    p = program
                    temp = _temp_config(config, _edit_config(text, target, v))
                    world = _world(config, temp)
                result = TestRun(world, p).run(cancel=cancel)
            except (ValueError, OSError) as e:      # TestFileError, ConfigError and PartError are ValueErrors
                row["outcome"] = str(e)
            except Exception as e:                  # anything else: this value's row, not the end of the sweep
                row["outcome"] = _plain_error(e)
            finally:
                if temp is not None and not _remove(temp):
                    left.append(temp.name)
            if result is not None:
                row.update(status=result.status, outcome=result.outcome, t_end=result.t_end,
                           checks={_check_column(c): "passed" if c["passed"] else "failed" for c in result.checks})
                folder = store.save_run(result, p, tmp, sweep={"target": target, "value": v, "index": i})
                if folder.name != f"{i + 1:03d}":             # a value before it couldn't run: keep k = its row
                    replace_file(folder, tmp / f"{i + 1:03d}")
            rows.append(row)
            if on_progress is not None:
                on_progress(i + 1, len(values), row)
            if result is not None and result.status == "cancelled":
                cancelled = True
                break
    except BaseException:
        cancelled = True                            # interrupted: keep the runs done so far, then stop
        _finish(tmp, base, target, values, rows, left, cancelled)
        raise
    return _finish(tmp, base, target, values, rows, left, cancelled)


def _finish(tmp: Path, base: Path, target: dict, values: list, rows: list, left: list, cancelled: bool) -> Path:
    """Write table.csv and sweep.json and put the sweep in place."""
    meta = {"target": target, "values": values}
    if cancelled:
        meta["cancelled"] = True
    if left:
        meta["problems"] = [f"couldn't delete the temp copy of the robot file, {n}; it's held by another program. "
                            "Delete it by hand." for n in left]
    try:
        _write_table(tmp / "table.csv", rows)
        (tmp / "sweep.json").write_text(json.dumps(meta), encoding="utf-8")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return store.place_sweep(tmp, base, cancelled=cancelled)


def _write_table(path: Path, rows: list[dict]) -> None:
    cols: list[str] = []
    for r in rows:
        cols += [c for c in r["checks"] if c not in cols]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["value", "status", "outcome", "t_end", *cols])
        for r in rows:
            t = "" if r["t_end"] is None else f"{r['t_end']:.3f}"
            w.writerow([f"{r['value']:g}", r["status"], r["outcome"], t, *(r["checks"].get(c, "") for c in cols)])
