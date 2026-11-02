"""A floor spread: the same test run with the floor ball at each placement of a grid, to see which got picked up.

A piece can land awkwardly from a station, or be dropped or bumped by another robot, so how a piece lies on the floor
is its own setting. The test's `[setup] floor_spread` lists values for any of SPREAD_AXES:

    offset_in      across the robot's path, left (+) or right (-) of its center line
    distance_in    ahead of the robot's start, along its (turned) heading
    angle_deg      the approach angle: the robot's start heading turns by this, counterclockwise seen from above
    rotation_deg   the ball turned about the vertical (it does nothing to a ball; the format carries it for pieces)

The placements are every combination of them (`placements`), at most MAX_PLACEMENTS. The robot keeps its start
position; the ball moved is `spread_ball`'s, the floor ball nearest ahead of the start (within SPREAD_CONE_DEG of the
heading and SPREAD_REACH), and an axis left out keeps that ball's own offset or distance, or 0 for the angle and
rotation. "Picked up" means the ball made it in (feeds.py's outcome "in": into a hopper body).

The spread is kept as `base/spread-<NNN>/`, as a sweep is (store.py, KEEP_SPREADS): a run per placement (`001/` ...),
`table.csv` (placement, the four axes, status, picked up, outcome) and `spread.json` (spread, placements, picked,
sentence, and `cancelled` when it was stopped). A cancelled spread is kept and prunes nothing; its `picked` (and the
table's rows) cover only the placements run, so `picked` is shorter than `placements`, the two aligned by index.
"""

from __future__ import annotations

import csv
import itertools
import json
import math
import shutil
import threading
from pathlib import Path

import numpy as np

from ..cad.settings import replace_file
from . import store
from .program import SPREAD_AXES, Program, TestFileError

SPREAD_CONE_DEG = 30.0    # the spread's ball is within this of the start's heading (our call)
SPREAD_REACH = 3.0        # m: ... and no further than this from the start (our call)
IN = 0.0254              # m per inch (conditions.IN; not imported: conditions imports the palette, which imports this)
NO_BALL = "the floor spread needs a ball on the floor in front of the robot's start"
COULDNT_RUN = "couldn't run"


def start_pose(world) -> tuple[float, float, float] | None:
    """(x, y, heading_deg) the robot starts at on World.reset: the field's start, or the config's start_pose."""
    tr = getattr(world, "test_range", None)
    if tr is not None:
        start = tr.range.starts.get(tr.settings().get("start"))
        if start is not None:
            return tuple(float(v) for v in start)
    pose = getattr(world, "start_pose", None)
    return tuple(float(v) for v in pose) if pose is not None else None


def spread_ball(world) -> tuple[int, dict] | None:
    """The floor ball nearest ahead of the robot's start (within SPREAD_CONE_DEG of its heading and SPREAD_REACH of
    it) and where it lies from the start, {"offset_in" (left +), "distance_in" (ahead)}; None when there is none."""
    tr = getattr(world, "test_range", None)
    pose = start_pose(world)
    if tr is None or pose is None or not tr.layout.n_floor:
        return None
    x, y, heading = pose
    h = math.radians(heading)
    rel = np.asarray(tr.range.floor_balls, float)[:tr.layout.n_floor] - np.array([x, y])
    ahead = rel @ np.array([math.cos(h), math.sin(h)])
    side = rel @ np.array([-math.sin(h), math.cos(h)])
    dist = np.hypot(ahead, side)
    cone = math.cos(math.radians(SPREAD_CONE_DEG))
    near = [i for i in range(len(rel)) if 0.0 < dist[i] <= SPREAD_REACH and ahead[i] >= cone * dist[i]]
    if not near:
        return None
    i = min(near, key=lambda k: dist[k])
    # not rounded: an axis left out puts the ball exactly where the field has it
    return i, {"offset_in": float(side[i]) / IN, "distance_in": float(ahead[i]) / IN}


def placements(spread: dict, base: dict) -> list[dict]:
    """Every combination of the spread's axes, in SPREAD_AXES order with the first axis slowest. Each has all four
    axes: an axis left out is the base's offset or distance, or 0 for the angle and rotation."""
    values = [spread[a] if a in spread else [base.get(a, 0.0) if a in ("offset_in", "distance_in") else 0]
              for a in SPREAD_AXES]
    return [dict(zip(SPREAD_AXES, combo)) for combo in itertools.product(*values)]


def varying(spread: dict) -> list[str]:
    """The axes a label names: those with more than one value, or every axis the spread sets when none has."""
    many = [a for a in SPREAD_AXES if len(spread.get(a, ())) > 1]
    return many or [a for a in SPREAD_AXES if a in spread]


def _n(x: float) -> str:
    return f"{round(float(x), 2):g}"


def label(p: dict, varying: list[str]) -> str:
    """A placement in words, only its varying axes in SPREAD_AXES order: "6 in left at 30°"."""
    words = []
    for a in SPREAD_AXES:
        if a not in varying:
            continue
        v = p[a]
        if a == "offset_in":
            words.append("on the center line" if v == 0 else f"{_n(abs(v))} in {'left' if v > 0 else 'right'}")
        elif a == "distance_in":
            words.append(f"{_n(v)} in ahead")
        elif a == "angle_deg":
            words.append(f"at {_n(v)}°")
        else:
            words.append(f"turned {_n(v)}°")
    return " ".join(words)


def _joined(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def sentence(picked: list[bool], labels: list[str]) -> str:
    """"picked up 7 of 9; missed at 6 in left at 30° and 6 in right at 30°", or "picked up 9 of 9"."""
    out = f"picked up {sum(picked)} of {len(picked)}"
    missed = [lb for ok, lb in zip(picked, labels) if not ok]
    return out + (f"; missed at {_joined(missed)}" if missed else "")


def _floor_outcome(result) -> str:
    """The floor ball's outcome in a run ("" when it wasn't tracked)."""
    return next((f["outcome"] for f in result.feeds if f["source"] == "floor"), "")


# ---- the spread -----------------------------------------------------------------------------------------------------

def run_spread(program: Program, base, on_progress=None, cancel: threading.Event | None = None) -> Path:
    """Run the test once per placement of its floor spread, on one world; returns the spread's folder.
    `on_progress(k, n, row)` is called after each placement (k from 1); setting `cancel` ends the run under way and
    the spread."""
    from .run import TestRun, check_problems, world_for
    from .sweep import _plain_error
    spread = program.setup.get("floor_spread")
    if not spread:
        raise TestFileError("this test has no floor spread: set one in Setup ([setup] floor_spread)")
    world = world_for(program)
    check_problems(program, world)          # the test's problems (a spread with no ball among them), before any run
    found = spread_ball(world)
    if found is None:
        raise TestFileError(NO_BALL)
    ps = placements(spread, found[1])
    names = varying(spread)
    labels = [label(p, names) for p in ps]
    base = Path(base)
    tmp = store.new_sweep_folder(base, prefix="spread")
    rows: list[dict] = []
    cancelled = False
    try:
        for i, p in enumerate(ps):
            row = {"placement": labels[i], **p, "status": COULDNT_RUN, "picked": False, "outcome": ""}
            result = None
            try:
                result = TestRun(world, program, placement=p).run(cancel=cancel)
            except (ValueError, OSError) as e:
                row["outcome"] = str(e)
            except Exception as e:              # anything else: this placement's row, not the end of the spread
                row["outcome"] = _plain_error(e)
            if result is not None:
                row.update(status=result.status, outcome=result.outcome, picked=_floor_outcome(result) == "in")
                folder = store.save_run(result, program, tmp, sweep={"spread": p, "index": i})
                if folder.name != f"{i + 1:03d}":         # a placement before it couldn't run: keep k = its row
                    replace_file(folder, tmp / f"{i + 1:03d}")
            rows.append(row)
            if on_progress is not None:
                on_progress(i + 1, len(ps), row)
            if result is not None and result.status == "cancelled":
                cancelled = True
                break
    except BaseException:
        cancelled = True                        # interrupted: keep the runs done so far, then stop
        _finish(tmp, base, spread, ps, labels, rows, cancelled)
        raise
    return _finish(tmp, base, spread, ps, labels, rows, cancelled)


def _finish(tmp: Path, base: Path, spread: dict, ps: list, labels: list, rows: list, cancelled: bool) -> Path:
    """Write table.csv and spread.json and put the spread in place."""
    picked = [r["picked"] for r in rows]
    meta = {"spread": spread, "placements": ps, "picked": picked, "sentence": sentence(picked, labels[:len(rows)])}
    if cancelled:
        meta["cancelled"] = True
    try:
        _write_table(tmp / "table.csv", rows)
        (tmp / "spread.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return store.place_sweep(tmp, base, cancelled=cancelled, prefix="spread")


def _write_table(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["placement", *SPREAD_AXES, "status", "picked up", "outcome"])
        for r in rows:
            w.writerow([r["placement"], *(_n(r[a]) for a in SPREAD_AXES), r["status"],
                        "yes" if r["picked"] else "no", r["outcome"]])
