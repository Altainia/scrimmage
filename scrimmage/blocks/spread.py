"""A floor spread: the same test run with the floor ball at each placement of a grid, to see which got picked up.

A piece can land awkwardly from a station, or be dropped or bumped by another robot, so how a piece lies on the floor
is its own setting. The test's `[setup] floor_spread` lists values for any of SPREAD_AXES:

    offset_in      across the robot's path, left (+) or right (-) of its center line
    distance_in    ahead of the robot's start, along its (turned) heading
    angle_deg      the approach angle: the robot's start heading turns by this, counterclockwise seen from above
    rotation_deg   the piece turned about the vertical, from the robot's heading (a ball: from the field's +x; it does
                   nothing to a ball)
    tip_deg        the piece's axis pitched up from lying, 0 to 90 (a coral standing on end at 90, a flat kind such
                   as a disc lying flat; it does nothing to a ball)

The placements are every combination of them (`placements`), at most MAX_PLACEMENTS. The robot keeps its start
position; the piece moved is `spread_ball`'s, the floor piece nearest ahead of the start (within SPREAD_CONE_DEG of the
heading and SPREAD_REACH), and an axis left out keeps that piece's own offset, distance, tip and turn (a ball's tip
and turn are 0), or 0 for the angle. A piece other than a ball is set down at its own resting height for its tip and
turn, and a placement names how it lies ("standing on end", "lying across": pieces.orientation_words) in place of
"turned 45°". "Picked up" means the piece made it in (feeds.py's outcome "in": into a hopper body).

The spread is kept as `base/spread-<NNN>/`, as a sweep is (store.py, KEEP_SPREADS): a run per placement (`001/` ...),
`table.csv` (placement, the five axes, status, picked up, outcome) and `spread.json` (spread, placements, picked,
sentence, and `cancelled` when it was stopped), both machine text (units.machine_text: American English, inches),
and `table.json`, the same table's words in every Language and Units setting for the Test tab (_write_words). A cancelled spread is kept and prunes nothing; its `picked` (and the
table's rows) cover only the placements run, so `picked` is shorter than `placements`, the two aligned by index.
"""

from __future__ import annotations

import csv
import itertools
import json
import math
import re
import shutil
import threading
from pathlib import Path

import mujoco
import numpy as np

from .. import i18n, units
from ..testrange.build import default_tip, rest_pose
from ..testrange.pieces import PieceKind, orientation_words
from . import store
from .program import SPREAD_AXES, Program, TestFileError

SPREAD_CONE_DEG = 30.0    # the spread's ball is within this of the start's heading (our call)
SPREAD_REACH = 3.0        # m: ... and no further than this from the start (our call)
IN = 0.0254              # m per inch (conditions.IN; not imported: conditions imports the palette, which imports this)
def no_ball() -> str:
    return i18n.t("the floor spread needs a ball on the floor in front of the robot's start")


def no_spread() -> str:
    return i18n.t("this test has no floor spread: set one in Setup ({setup_table} floor_spread)",
                  setup_table="[setup]")


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


def spread_kind(world, i: int) -> PieceKind | None:
    """Floor piece i's kind, None for a ball."""
    tr = world.test_range
    kind = tr.range.kinds.get(str(tr.balls.kind[i]))
    return None if kind is None or kind.form == "ball" else kind


def spread_ball(world) -> tuple[int, dict] | None:
    """The floor piece on the carpet (not one staged on an element) nearest ahead of the robot's start (within
    SPREAD_CONE_DEG of its heading and SPREAD_REACH of it) and where it lies from the start, {"offset_in" (left +), "distance_in" (ahead)}, and for a piece other than a
    ball how the field has it lying, {"tip_deg", "rotation_deg" (from the start's heading)}; None when there is
    none."""
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
    lift = tr.range.floor_heights        # a staged piece (on an element) isn't the floor's: it stays where it is
    staged = lift[:len(rel)] > 0.0 if len(lift) == len(tr.range.floor_balls) else np.zeros(len(rel), bool)
    near = [i for i in range(len(rel)) if 0.0 < dist[i] <= SPREAD_REACH and ahead[i] >= cone * dist[i]
            and not staged[i]]
    if not near:
        return None
    i = min(near, key=lambda k: dist[k])
    # not rounded: an axis left out puts the piece exactly where the field has it
    where = {"offset_in": float(side[i]) / IN, "distance_in": float(ahead[i]) / IN}
    kind = spread_kind(world, i)
    if kind is not None:
        turns = tr.range.floor_turns     # a row per floor piece, but empty in a Range made without it (its default)
        tip, turn = (float(turns[i][0]), float(turns[i][1])) if len(turns) == len(tr.range.floor_balls) \
            else (default_tip(kind), 0.0)
        where.update(tip_deg=tip, rotation_deg=(turn - heading + 180.0) % 360.0 - 180.0)
    return i, where


def rest_of(world, i: int, p: dict, heading: float) -> tuple[float, list[float]]:
    """Floor piece i set down as placement `p` has it, the robot heading `heading` (deg): its center's height and its
    quaternion (w, x, y, z). A ball rests at its radius turned by rotation_deg from the field's +x; any other piece
    at its kind's rest height, its axis tipped by tip_deg and turned by rotation_deg from the heading."""
    if spread_kind(world, i) is None:
        r = math.radians(p["rotation_deg"]) / 2.0
        return world.test_range.balls.rest_z_of(i), [math.cos(r), 0.0, 0.0, math.sin(r)]
    tr = world.test_range
    z, quat = rest_pose(tr.range, str(tr.balls.kind[i]), (p["tip_deg"], heading + p["rotation_deg"]))
    return z, quat if quat is not None else [1.0, 0.0, 0.0, 0.0]


CLEAR_IN = 1.0            # in: a placement's ball is at least this far clear of the robot (Setup's nearest_in)


def _robot_boxes(world) -> np.ndarray:
    """Every colliding robot geom's box in the robot's own frame (x ahead, y left, z up from its origin, which is on
    the floor), as rows [x0, x1, y0, y1, z0, z1] in m: the geom's bounding box, turned with it (so a little larger
    than the geom when it is turned)."""
    m = world.model
    d = mujoco.MjData(m)                # a scratch MjData at the world's pose: the world's own is left alone
    d.qpos[:] = world.data.qpos
    d.mocap_pos[:], d.mocap_quat[:] = world.data.mocap_pos, world.data.mocap_quat
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


def _shape(world, i: int, p: dict) -> tuple[float, tuple, np.ndarray, np.ndarray]:
    """Floor piece i as placement `p` has it, in the robot's frame with its center at the origin: its center's
    height, its solids' oriented boxes (centers, axes as rows, half sizes: PieceKind.turned_solids) and its spheres'
    centers ((m, 3)) and radii. A ball is one sphere."""
    kind = spread_kind(world, i)
    if kind is None:
        r = world.test_range.balls.rest_z_of(i)     # a resting ball's center height: its radius
        return r, (np.zeros((0, 3)), np.zeros((0, 3, 3)), np.zeros((0, 3))), np.zeros((1, 3)), np.array([r])
    z, quat = rest_of(world, i, p, 0.0)
    bc, ba, bh, sc, radii = kind.turned_solids(quat)
    return z, (bc, ba, bh), sc, radii


def _boxes_overlap(obbs: tuple, lo: np.ndarray, hi: np.ndarray, tol: float = 1e-3) -> bool:
    """Whether any oriented box (centers (n, 3), axes (n, 3, 3) as rows, half sizes (n, 3)) overlaps any
    axis-aligned box (corners lo, hi: (m, 3)) by more than `tol`: the separating-axis test, over the three world axes,
    the box's three and their nine cross products."""
    cb, ub, hb = obbs
    if not len(cb) or not len(lo):
        return False
    ca, ha = (lo + hi) / 2.0, (hi - lo) / 2.0
    eye = np.broadcast_to(np.eye(3), ub.shape)
    cross = np.cross(eye[:, :, None, :], ub[:, None, :, :]).reshape(-1, 9, 3)
    axes = np.concatenate([eye, ub, cross], axis=1)                                 # (n, 15, 3)
    norm = np.linalg.norm(axes, axis=2, keepdims=True)
    axes = np.where(norm > 1e-9, axes / np.maximum(norm, 1e-12), np.array([1.0, 0.0, 0.0]))    # parallel: any axis
    r_b = (np.abs(axes @ ub.transpose(0, 2, 1)) * hb[:, None, :]).sum(axis=2)       # (n, 15)
    r_a = np.abs(axes) @ ha.T                                                        # (n, 15, m)
    d = np.abs(axes @ cb[:, :, None] - axes @ ca.T)                                  # (n, 15, m)
    return bool((d < r_a + r_b[:, :, None] - tol).all(axis=1).any())


def _nearest_in(world, i: int, p: dict, boxes: np.ndarray) -> int:
    """The nearest distance ahead (in, whole) Setup offers for the piece lying as `p` has it: the robot's front,
    how far the piece's boxes and spheres reach back toward the robot, and CLEAR_IN."""
    _, (bc, ba, bh), sc, radii = _shape(world, i, p)
    reach = [bc[:, 0] - (np.abs(ba[:, :, 0]) * bh).sum(axis=1), sc[:, 0] - radii]
    back = -float(np.concatenate(reach).min())
    front = float(boxes[:, 1].max()) if len(boxes) else 0.0
    return math.ceil((front + back) / IN + CLEAR_IN)


def floor_ball(world) -> dict | None:
    """Where the spread's piece lies from the start (as spread_ball's) and the nearest distance ahead (in, whole)
    Setup offers: the robot's front, how far the piece (a ball: its radius) reaches back and CLEAR_IN; None without
    one."""
    found = spread_ball(world)
    if found is None:
        return None
    i, where = found
    (p,) = placements({}, where)
    return {**where, "nearest_in": _nearest_in(world, i, p, _robot_boxes(world))}


def inside_robot(world, spread: dict) -> str | None:
    """Why the floor spread can't run when a placement puts the piece inside the robot ("the floor spread puts the
    ball inside the robot at 12 in ahead: ..."), or None. The robot turns with the angle, so the piece sits at the
    same place in its frame whatever the angle. A piece other than a ball is inside when one of its solids' oriented
    bounding boxes, or one of its spheres, overlaps a robot geom's box."""
    found = spread_ball(world)
    if found is None:
        return None
    i = found[0]
    boxes = _robot_boxes(world)
    if not len(boxes):
        return None
    lo, hi = boxes[:, 0::2], boxes[:, 1::2]
    kind = spread_kind(world, i)
    for p in placements(spread, found[1]):
        z, (bc, ba, bh), centers, radii = _shape(world, i, p)
        at = np.array([p["distance_in"] * IN, p["offset_in"] * IN, z])
        c = centers + at
        near = np.clip(c[:, None, :], lo, hi)                       # (m, boxes, 3)
        hit = (np.linalg.norm(near - c[:, None, :], axis=2) < radii[:, None] - 1e-3).any()
        if hit or _boxes_overlap((bc + at, ba, bh), lo, hi):
            q = dict(piece=i18n.t("ball") if kind is None else kind.label, ahead=_inches(p["distance_in"]),
                     nearest=_inches(_nearest_in(world, i, p, boxes)), across=_inches(abs(p["offset_in"])))
            if p["offset_in"] == 0:
                return i18n.t("the floor spread puts the {piece} inside the robot at {ahead} ahead: start the distance "
                              "at {nearest} or more", **q)
            if p["offset_in"] > 0:
                return i18n.t("the floor spread puts the {piece} inside the robot at {ahead} ahead, {across} left: "
                              "start the distance at {nearest} or more", **q)
            return i18n.t("the floor spread puts the {piece} inside the robot at {ahead} ahead, {across} right: start "
                          "the distance at {nearest} or more", **q)
    return None


def on_partner(world, spread: dict) -> str | None:
    """Why the floor spread can't run when a placement puts the piece under the partner robot ("the floor spread's
    placement 6 in left puts the ball under the partner robot: ..."), or None (no partner, or none does). The piece is
    a circle of its kind's radius (a ball's own; any other piece's bounding sphere's) where the placement puts its
    center, against the partner's outline (frame plus bumpers), as partner.check_start judges the field's own floor
    pieces. A partner hanging from a chain is over the floor, not on it: never."""
    from ..parts.partner import _circle_overlap
    from ..testrange.elements import _rect
    info = world.parts.partner if world.parts is not None else None
    found = spread_ball(world)
    pose = start_pose(world)
    if info is None or info.hang is not None or found is None or pose is None:    # a hanging partner is off the floor
        return None
    i = found[0]
    kind = spread_kind(world, i)
    r = kind.radius if kind is not None else world.test_range.balls.rest_z_of(i)
    px, py, ph = info.start
    poly = _rect((px, py), ph, *info.outline)
    x, y, heading = pose
    names = varying(spread)
    for p in placements(spread, found[1]):
        h = math.radians(heading + p["angle_deg"])
        ahead, left = p["distance_in"] * IN, p["offset_in"] * IN
        at = np.array([x + ahead * math.cos(h) - left * math.sin(h), y + ahead * math.sin(h) + left * math.cos(h)])
        if _circle_overlap(poly, at, r) > 1e-9:
            return i18n.t("the floor spread's placement {placement} puts the {piece} under the partner robot: move "
                          "the partner farther away in Setup, or leave that placement out",
                          placement=label(p, names, kind), piece=i18n.t("ball") if kind is None else kind.label)
    return None


def placements(spread: dict, base: dict) -> list[dict]:
    """Every combination of the spread's axes, in SPREAD_AXES order with the first axis slowest. Each has all five
    axes: an axis left out is the base's (spread_ball's: the floor piece's own offset, distance, tip and turn), or 0
    (the angle; a ball's tip and turn)."""
    values = [spread[a] if a in spread else [base.get(a, 0.0) if a != "angle_deg" else 0] for a in SPREAD_AXES]
    return [dict(zip(SPREAD_AXES, combo)) for combo in itertools.product(*values)]


def varying(spread: dict) -> list[str]:
    """The axes a label names: those with more than one value, or every axis the spread sets when none has."""
    many = [a for a in SPREAD_AXES if len(spread.get(a, ())) > 1]
    return many or [a for a in SPREAD_AXES if a in spread]


def _n(x: float) -> str:
    return f"{round(float(x), 2):g}"


def _inches(x: float) -> str:
    """An `_in` axis's inches in words, in the Units setting: "6 in" or "152 mm" (machine text: the old "6 in")."""
    if units.is_machine():
        return f"{_n(x)} in"
    return units.fmt(float(x) * IN, "length", fewer=1)


ORIENTATION_AXES = ("rotation_deg", "tip_deg")


def worded(varying: list[str], kind: PieceKind | None) -> bool:
    """Whether a placement's label names how the piece lies: a piece other than a ball, its tip or turn varying."""
    return kind is not None and kind.form != "ball" and any(a in varying for a in ORIENTATION_AXES)


def label(p: dict, varying: list[str], kind: PieceKind | None = None) -> str:
    """A placement in words, only its varying axes in SPREAD_AXES order: "6 in left at 30°". For a piece other than
    a ball (`kind`), a varying tip or turn reads as how it lies, its turn from the robot's heading: "6 in left lying
    across", "standing on end"; a ball's (kind None) reads "turned 45°". Machine text (table.csv) is American
    English, as it always was; outside it the words are translated (_label_words)."""
    if not units.is_machine():
        return _label_words(p, varying, kind)
    words = []
    for a in SPREAD_AXES:
        if a not in varying:
            continue
        v = p[a]
        if a in ORIENTATION_AXES and worded(varying, kind):
            if a == next(b for b in ORIENTATION_AXES if b in varying):     # once, for both
                words.append(orientation_words(kind, p["tip_deg"], p["rotation_deg"]))
            continue
        if a == "offset_in":
            words.append("on the center line" if v == 0 else f"{_inches(abs(v))} {'left' if v > 0 else 'right'}")
        elif a == "distance_in":
            words.append(f"{_inches(v)} ahead")
        elif a == "angle_deg":
            words.append(f"at {_n(v)}°")
        elif a == "rotation_deg":
            words.append(f"turned {_n(v)}°")
        else:
            words.append(f"tipped {_n(v)}°")
    return " ".join(words)


def _label_words(p: dict, varying: list[str], kind: PieceKind | None) -> str:
    """label()'s words, translated, in the Units setting."""
    words = []
    for a in SPREAD_AXES:
        if a not in varying:
            continue
        v = p[a]
        if a in ORIENTATION_AXES and worded(varying, kind):
            if a == next(b for b in ORIENTATION_AXES if b in varying):
                words.append(orientation_words(kind, p["tip_deg"], p["rotation_deg"]))
            continue
        if a == "offset_in":
            words.append(i18n.t("on the center line") if v == 0 else
                         i18n.t("{distance} left", distance=_inches(abs(v))) if v > 0 else
                         i18n.t("{distance} right", distance=_inches(abs(v))))
        elif a == "distance_in":
            words.append(i18n.t("{distance} ahead", distance=_inches(v)))
        elif a == "angle_deg":
            words.append(i18n.t("at {angle}°", angle=_n(v)))
        elif a == "rotation_deg":
            words.append(i18n.t("turned {angle}°", angle=_n(v)))
        else:
            words.append(i18n.t("tipped {angle}°", angle=_n(v)))
    return " ".join(words)


def _differs(p: dict, a: str) -> str:
    """A tip or turn that tells two placements apart, after their label: "turned 45°", "tipped 80°"."""
    if units.is_machine():
        return f"turned {_n(p[a])}°" if a == "rotation_deg" else f"tipped {_n(p[a])}°"
    return (i18n.t("turned {angle}°", angle=_n(p[a])) if a == "rotation_deg"
            else i18n.t("tipped {angle}°", angle=_n(p[a])))


def placement_labels(ps: list[dict], varying: list[str], kind: PieceKind | None = None) -> list[str]:
    """Each placement's label. Where words for how a piece lies say the same for two placements or more ("lying
    flat" at every turn of a disc, "standing on end" at tips 80 and 90), each of those gets its tip or turn that
    differs after them in parentheses: "lying flat (turned 45°)", "standing on end (tipped 80°)"."""
    out = [label(p, varying, kind) for p in ps]
    if not worded(varying, kind):
        return out
    groups: dict[str, list[int]] = {}
    for k, lb in enumerate(out):
        groups.setdefault(lb, []).append(k)
    for lb, ks in groups.items():
        if len(ks) < 2:
            continue
        differs = [a for a in ORIENTATION_AXES if a in varying and len({ps[k][a] for k in ks}) > 1]
        for k in ks:
            extra = [_differs(ps[k], a) for a in differs]
            out[k] = (f"{lb} ({', '.join(extra)})" if units.is_machine()
                      else i18n.t("{label} ({differs})", label=lb, differs=", ".join(extra)))
    return out


def _joined(items: list[str]) -> str:
    """"a, b and c"; "a, b; c, d; and e, f" when an item has a comma of its own (not one in its parentheses:
    "lying flat (turned 0°), lying flat (turned 45°) and ...")."""
    if len(items) == 1:
        return items[0]
    semi = any("," in re.sub(r"\([^)]*\)", "", it) for it in items)
    if units.is_machine():
        return f"{'; '.join(items[:-1])}; and {items[-1]}" if semi else f"{', '.join(items[:-1])} and {items[-1]}"
    if semi:
        return i18n.t("{items}; and {last}", items="; ".join(items[:-1]), last=items[-1])
    return i18n.t("{items} and {last}", items=", ".join(items[:-1]), last=items[-1])


def sentence(picked: list[bool], labels: list[str], at: bool = True) -> str:
    """"picked up 7 of 9; missed at 6 in left at 30° and 6 in right at 30°", or "picked up 9 of 9". Labels that
    name how a piece lies (`at` false) read without the "at": "picked up 2 of 3; missed standing on end"."""
    missed = [lb for ok, lb in zip(picked, labels) if not ok]
    if units.is_machine():
        out = f"picked up {sum(picked)} of {len(picked)}"
        return out + (f"; missed {'at ' if at else ''}{_joined(missed)}" if missed else "")
    p = dict(picked=sum(picked), total=len(picked))
    if not missed:
        return i18n.t("picked up {picked} of {total}", **p)
    if at:
        return i18n.t("picked up {picked} of {total}; missed at {missed}", missed=_joined(missed), **p)
    return i18n.t("picked up {picked} of {total}; missed {missed}", missed=_joined(missed), **p)


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
        raise TestFileError(no_spread())
    world = world_for(program)
    check_problems(program, world)          # the test's problems (a spread with no ball among them), before any run
    found = spread_ball(world)
    if found is None:
        raise TestFileError(no_ball())
    inside = inside_robot(world, spread) or on_partner(world, spread)
    if inside is not None:
        raise TestFileError(inside)
    ps = placements(spread, found[1])
    names = varying(spread)
    kind = spread_kind(world, found[0])
    with units.machine_text():        # table.csv's and spread.json's words as they always were
        labels = placement_labels(ps, names, kind)
    at = not worded(names, kind)
    base = Path(base)
    tmp = store.new_sweep_folder(base, prefix="spread")

    def record(i: int, row: dict, result):
        row.update(status=result.status, outcome=result.outcome, picked=_floor_outcome(result) == "in",
                   words=result.words)
        return program, {"spread": ps[i], "index": i}

    rows = [{"placement": labels[i], **p, "status": COULDNT_RUN, "picked": False, "outcome": ""}
            for i, p in enumerate(ps)]
    # on_progress must stay numeric: anything built inside machine_text() is machine text
    with units.machine_text():
        return run_each(tmp, rows, lambda i: TestRun(world, program, placement=ps[i]).run(cancel=cancel), record,
                        lambda done, cancelled: _finish(tmp, base, spread, ps, labels, at, done, cancelled,
                                                        names, kind),
                        on_progress)


def _finish(tmp: Path, base: Path, spread: dict, ps: list, labels: list, at: bool, rows: list,
            cancelled: bool, names: list, kind) -> Path:
    """Write table.csv and spread.json and put the spread in place."""
    picked = [r["picked"] for r in rows]
    meta = {"spread": spread, "placements": ps, "picked": picked,
            "sentence": sentence(picked, labels[:len(rows)], at)}
    if cancelled:
        meta["cancelled"] = True
    try:
        _write_table(tmp / "table.csv", rows)
        (tmp / "spread.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        _write_words(tmp / "table.json", ps, names, kind, rows, picked)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return store.place_sweep(tmp, base, cancelled=cancelled, prefix="spread")


def _write_words(path: Path, ps: list, names: list, kind, rows: list, picked: list) -> None:
    """table.json: the spread's table for the Test tab, in every Language and Units setting (units.each_setting),
    since table.csv and spread.json are machine text. {"rows": [{run}], "words": {setting_key: {placements,
    outcomes, sentence}}}: run is the row's kept run ("001"), or None when it couldn't run (its outcome is None,
    table.csv saying why); a run's outcome is its own words (RunResult.words). Words, not codes, because a run's
    outcome is worded from its numbers while it runs."""
    def table_words() -> dict:
        labels = placement_labels(ps, names, kind)
        key = units.setting_key()
        return {"placements": labels[:len(rows)],
                "outcomes": [r["words"][key]["outcome"] if r.get("words") else None for r in rows],
                "sentence": sentence(picked, labels[:len(rows)], not worded(names, kind))}
    table = {"rows": [{"run": f"{i + 1:03d}" if r.get("words") else None} for i, r in enumerate(rows)],
             "words": units.each_setting(table_words)}
    path.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")


def _write_table(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["placement", *SPREAD_AXES, "status", "picked up", "outcome"])
        for r in rows:
            w.writerow([r["placement"], *(_n(r[a]) for a in SPREAD_AXES), r["status"],
                        "yes" if r["picked"] else "no", r["outcome"]])
