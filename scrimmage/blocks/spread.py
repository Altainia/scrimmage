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

import mujoco
import numpy as np

from . import store
from .program import SPREAD_AXES, Program, TestFileError

SPREAD_CONE_DEG = 30.0    # the spread's ball is within this of the start's heading (our call)
SPREAD_REACH = 3.0        # m: ... and no further than this from the start (our call)
IN = 0.0254              # m per inch (conditions.IN; not imported: conditions imports the palette, which imports this)
NO_BALL = "the floor spread needs a ball on the floor in front of the robot's start"
NO_SPREAD = "this test has no floor spread: set one in Setup ([setup] floor_spread)"
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


CLEAR_IN = 1.0            # in: a placement's ball is at least this far clear of the robot (Setup's nearest_in)


def _robot_boxes(world) -> np.ndarray:
    """Every colliding robot geom's box in the robot's own frame (x ahead, y left, z up from its origin, which is on
    the floor), as rows [x0, x1, y0, y1, z0, z1] in m: the geom's bounding box, turned with it (so a little larger
    than the geom when it is turned)."""
    m = world.model
    d = mujoco.MjData(m)                # a scratch MjData at the world's pose: the world's own is left alone
    d.qpos[:] = world.data.qpos
    mujoco.mj_kinematics(m, d)
    root = m.body_rootid[world.robot_body]
    rpos, rmat = d.xpos[world.robot_body], d.xmat[world.robot_body].reshape(3, 3)
    signs = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], float)
    out = []
    for g in range(m.ngeom):
        if m.body_rootid[m.geom_bodyid[g]] != root or not (m.geom_contype[g] or m.geom_conaffinity[g]):
            continue
        corners = signs * m.geom_aabb[g][3:] + m.geom_aabb[g][:3]
        local = (corners @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g] - rpos) @ rmat
        out.append([local[:, 0].min(), local[:, 0].max(), local[:, 1].min(), local[:, 1].max(),
                    local[:, 2].min(), local[:, 2].max()])
    return np.array(out, float).reshape(-1, 6)


def floor_ball(world) -> dict | None:
    """Where the spread's ball lies from the start ({offset_in, distance_in}, as spread_ball's) and the nearest
    distance ahead (in, whole) Setup offers: the robot's front, the ball's radius and CLEAR_IN; None without one."""
    found = spread_ball(world)
    if found is None:
        return None
    r = world.test_range.balls.rest_z                 # a resting ball's center height: its radius
    boxes = _robot_boxes(world)
    front = float(boxes[:, 1].max()) if len(boxes) else 0.0
    return {**found[1], "nearest_in": math.ceil((front + r) / IN + CLEAR_IN)}


def inside_robot(world, spread: dict) -> str | None:
    """Why the floor spread can't run when a placement puts the ball inside the robot ("the floor spread puts the
    ball inside the robot at 12 in ahead: ..."), or None. The robot turns with the angle, so the ball sits at the
    same place in its frame whatever the angle."""
    found = spread_ball(world)
    if found is None:
        return None
    r = world.test_range.balls.rest_z
    boxes = _robot_boxes(world)
    for p in placements(spread, found[1]):
        c = np.array([p["distance_in"] * IN, p["offset_in"] * IN, r])
        near = np.clip(c, boxes[:, 0::2], boxes[:, 1::2])
        if len(boxes) and (np.linalg.norm(near - c, axis=1) < r - 1e-3).any():
            nearest = floor_ball(world)["nearest_in"]
            side = "" if p["offset_in"] == 0 else \
                f", {_n(abs(p['offset_in']))} in {'left' if p['offset_in'] > 0 else 'right'}"
            return (f"the floor spread puts the ball inside the robot at {_n(p['distance_in'])} in ahead{side}: "
                    f"start the distance at {nearest} in or more")
    return None


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
    from .sweep import run_each
    spread = program.setup.get("floor_spread")
    if not spread:
        raise TestFileError(NO_SPREAD)
    world = world_for(program)
    check_problems(program, world)          # the test's problems (a spread with no ball among them), before any run
    found = spread_ball(world)
    if found is None:
        raise TestFileError(NO_BALL)
    inside = inside_robot(world, spread)
    if inside is not None:
        raise TestFileError(inside)
    ps = placements(spread, found[1])
    names = varying(spread)
    labels = [label(p, names) for p in ps]
    base = Path(base)
    tmp = store.new_sweep_folder(base, prefix="spread")

    def record(i: int, row: dict, result):
        row.update(status=result.status, outcome=result.outcome, picked=_floor_outcome(result) == "in")
        return program, {"spread": ps[i], "index": i}

    rows = [{"placement": labels[i], **p, "status": COULDNT_RUN, "picked": False, "outcome": ""}
            for i, p in enumerate(ps)]
    return run_each(tmp, rows, lambda i: TestRun(world, program, placement=ps[i]).run(cancel=cancel), record,
                    lambda done, cancelled: _finish(tmp, base, spread, ps, labels, done, cancelled), on_progress)


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
