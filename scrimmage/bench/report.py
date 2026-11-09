"""A bench run's results in plain words: headlines for a room, the tables, CSVs, and with `[flight]` where the shots
land.

Headlines are three to six lines, read on a projector: what came out where and how fast, what didn't get out, the
part taking the hardest hits (when one stands out), the shaft that slows most, and always last, UNCALIBRATED with
what to measure. Names from the bench file are in backticks.

Units and Language: the headlines and summary.txt are in the Units and Language settings when they are written
(scrimmage/units.py, i18n.py; the MCP tools return the headlines in meters and American English, as before the
settings, through units.fixed()). The CSVs stay SI, their columns named by unit (speed_m_s), and the tables' column
names are the CSVs'. The UNCALIBRATED line is measure/labels.py's, as the pictures carry it, all of it in the Language.
The Bench tab doesn't read summary.txt's words: summary.json, written beside it, holds no words, only how many
lines summary.txt's title block takes, how many headline lines follow it, whether the last is the UNCALIBRATED line,
and whether the energy check failed (spec L2, L3).
"""

import csv
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np

from .. import i18n, units
from ..i18n import t
from ..launcher import flight6, rigid_aero
from ..launcher.flight import Landing, fly_state
from ..launcher.inputs import PRESETS, Air, Goal
from ..measure import labels
from ..testrange import goal_fit
from ..testrange.pieces import Solid, _round, shadow_area
from ..testrange.spec import Surface, passing_width
from .run import BenchResult, PieceOutcome

# path colors by outcome (RGB): out green, jammed red, escaped orange, timed out gray
STATUS_COLORS = {"out": (0, 158, 60), "jammed": (220, 30, 40), "escaped": (245, 140, 0),
                 "timed_out": (130, 130, 130), "stuck_at_inlet": (130, 130, 130)}
# a piece's status (a key) in words (a function: the words are in the Language setting of the moment)
def status_words() -> dict[str, str]:
    return {"out": t("left through an outlet"), "jammed": t("jammed"), "escaped": t("escaped the bench"),
            "timed_out": t("still inside at the end"), "stuck_at_inlet": t("never got in")}
LOAD_STANDS_OUT = 3.0   # a shape's peak force over this many times the other touched shapes' median is a headline
MAX_LINES = 6           # headlines, UNCALIBRATED included
JAM_NEAR = 0.05         # m: a jammed piece is "at" the nearest shape within this distance

_MEASURE = {      # N_(): said with translated()
    "squishy": i18n.N_("measure the ball's stiffness (a squeeze test) and its bounce (a drop test) first; see "
                       "docs/launcher.md, \"Measure these first\""),
    "rigid": i18n.N_("measure the tread's friction against the piece first (a sled pull: pull force / pressing "
                     "force)"),
}


def uncalibrated(result: BenchResult) -> str:
    return bench_uncalibrated(result.bench)


ESTIMATES = i18n.N_("estimates from preset numbers, not measurements")    # after measure/labels.py's mark
_MATTERS = {"squishy": labels.SQUISHY_BENCH, "rigid": labels.RIGID_BENCH}


def bench_matters(bench) -> tuple[str, ...]:
    """What a bench's results rest on: its piece's stiffness and the friction it uses."""
    return _MATTERS[bench.piece.kind]


def bench_estimates(bench) -> str:
    """A bench's estimates in a line: its pictures' and charts' UNCALIBRATED line. A bench takes no measurement file
    yet, so it names its estimates only."""
    return labels.line({}, bench_matters(bench), estimates=ESTIMATES)


def bench_uncalibrated(bench) -> str:
    """The UNCALIBRATED line for a bench: what is estimated and what to measure first, for its kind of piece."""
    line = t("{estimates}; {measure}.", estimates=bench_estimates(bench),
             measure=i18n.translated(_MEASURE[bench.piece.kind]))
    if bench.piece.kind == "rigid" and getattr(bench, "flight", None) is not None:  # a stand-in may have none
        kind = bench.piece.rigid_kind
        cd = kind.drag_coefficient if kind is not None else bench.piece.drag_coefficient
        line = t("{line} In {flight_table}, the drag coefficient ({cd}) is an estimate unless you measured it, "
                 "and lift isn't modeled.", line=line, cd=f"{cd:g}", flight_table="[flight]")
    return line


def _name(s: str) -> str:
    return f"`{s}`"


# A headline's numbers, in the Units setting, to the places they had in meters: an exit speed to 0.1 (no unit; the
# caller says it once after a ± spread), a load to 1 lbf or N, a flight distance to `places`.
def _speed(v: float) -> str:
    return f"{units.to_display(v, 'speed'):.1f}"


def _force(v: float) -> str:
    return f"{units.to_display(v, 'force'):.0f} {units.unit_name('force')}"


def _far(v: float, places: int = 2, unit: bool = True) -> str:
    text = f"{units.to_display(v, 'field'):.{places}f}"
    return f"{text} {units.unit_name('field')}" if unit else text


def _direction(elevation_deg: float, yaw: float) -> str:
    e = round(elevation_deg)
    text = t("level") if e == 0 else t("{deg}° up", deg=abs(e)) if e > 0 else t("{deg}° down", deg=abs(e))
    if abs(yaw) >= 178.0:
        return t("{way}, going backwards", way=text)
    if abs(yaw) > 2.0:
        turn = f"{abs(yaw):.0f}"
        return t("{way}, {deg}° to the left", way=text, deg=turn) if yaw > 0 else \
            t("{way}, {deg}° to the right", way=text, deg=turn)
    return text


def _out_line(outlet: str, out: list[PieceOutcome], n: int) -> str:
    speeds = np.array([p.speed for p in out])
    yaw = np.radians([p.yaw_deg for p in out])  # a circular mean: -179 and 179 average to 180, not 0
    way = _direction(float(np.mean([p.elevation_deg for p in out])),
                     math.degrees(math.atan2(np.sin(yaw).mean(), np.cos(yaw).mean())))
    at = {"out": len(out), "n": n, "outlet": _name(outlet), "speed": _speed(speeds.mean()),
          "unit": units.unit_name("speed"), "way": way}
    if len(out) > 1:
        return t("{out} of {n} left through {outlet} at {speed} {unit} ± {spread}, {way}",
                 spread=_speed(speeds.std()), **at)
    return t("{out} of {n} left through {outlet} at {speed} {unit}, {way}", **at)


def jam_place(result: BenchResult, piece: PieceOutcome) -> str | None:
    """The shape nearest where a piece stopped (within JAM_NEAR), or None."""
    m, lay = result.built.model, result.built.layout
    d = mujoco.MjData(m)
    a = lay.piece_qpos[piece.index]
    d.qpos[a:a + 3] = piece.path[-1][1:]
    d.qpos[a + 3:a + 7] = (1.0, 0.0, 0.0, 0.0)
    mujoco.mj_kinematics(m, d)
    best, name = JAM_NEAR, None
    for g in lay.piece_geom_sets[piece.index]:
        for sg, shape in lay.shape_of_geom.items():
            dist = mujoco.mj_geomDistance(m, d, g, sg, JAM_NEAR, None)
            if dist < best:
                best, name = dist, shape
    return name


def _not_out_line(result: BenchResult, n: int) -> str | None:
    rest = [p for p in result.pieces if p.status != "out"]
    if not rest:
        return None
    parts: Counter = Counter()
    words = status_words()
    for p in rest:
        if p.status == "jammed":
            at = jam_place(result, p)
            parts[t("jammed at {shape}", shape=_name(at)) if at else t("jammed inside the bench")] += 1
        else:
            parts[words[p.status]] += 1
    if len(parts) == 1:
        (what, k), = parts.items()
        return t("{k} of {n} {what}", k=k, n=n, what=what)
    return t("{rest} of {n} didn't get out: {parts}", rest=len(rest), n=n,
             parts=", ".join(t("{k} {what}", k=k, what=what) for what, k in parts.most_common()))


def _load_line(result: BenchResult) -> str | None:
    """The hardest-hit shape, when its peak is over LOAD_STANDS_OUT times the median peak of the other shapes that
    were touched (so two touched shapes are enough). One touched shape has nothing to stand out from: no line."""
    peaks = {s: ld.peak_force_n for s, ld in result.loads.items() if ld.peak_force_n > 0.0}
    if len(peaks) < 2:
        return None
    shape, peak = max(peaks.items(), key=lambda kv: kv[1])
    median = float(np.median([f for s, f in peaks.items() if s != shape]))
    if peak <= LOAD_STANDS_OUT * median:
        return None
    return t("the {shape} takes the hardest hits: up to {peak}, {times} times a typical other part's ({median})",
             shape=_name(shape), peak=_force(peak), times=f"{peak / median:.1f}", median=_force(median))


SLIPPING = 0.10  # exit slip at or above this is grip-limited (the launcher model's regime line, docs/launcher.md)


def _slip_line(result: BenchResult) -> str | None:
    """When the balls that got out were still slipping on the wheels as they left: more wheel speed won't buy much,
    more grip will. None for rigid pieces, no wheel contact, or a gripping launch."""
    slips = [p.exit_slip for p in result.pieces if p.status == "out" and p.exit_slip is not None]
    if not slips:
        return None
    s = float(np.median(slips))
    if s < SLIPPING:
        return None
    return t("the balls are still slipping on the wheels when they leave ({slip}% slip): more wheel speed won't make "
             "them much faster; more grip will (more squeeze, more or wider wheels, a longer wrap)",
             slip=f"{100 * s:.0f}")


def _shaft_line(result: BenchResult) -> str | None:
    touched = [s for s in result.shafts.values() if s.drop_pct > 0.0]
    if not touched:
        return None
    s = max(touched, key=lambda s: s.drop_pct)
    at = {"shaft": _name(s.name), "drop": f"{s.drop_pct:.0f}"}
    if s.back_before_next is False:
        line = t("the {shaft} shaft drops {drop}% and isn't back to speed when the next piece arrives (feed slower, "
                 "or add a flywheel)", **at)
    elif s.recovery_s is not None:
        line = t("the {shaft} shaft drops {drop}% and recovers in {seconds} s", seconds=f"{s.recovery_s:.2f}", **at)
    else:
        line = t("the {shaft} shaft drops {drop}% and has not recovered when the run ends", **at)
    n = len(result.shafts)
    if n == 2:
        line = t("{line} (the worse of {n} shafts)", line=line, n=n)
    elif n > 2:
        line = t("{line} (the worst of {n} shafts)", line=line, n=n)
    return line


def _time_line(result: BenchResult) -> str | None:
    out = [p.t_out - p.t_in for p in result.pieces if p.status == "out"]
    if not out:
        return None
    return t("a piece takes {seconds} s from the inlet to the outlet", seconds=f"{float(np.median(out)):.2f}")


# ----------------------------------------------------------------------------------------------------- flight

@dataclass(frozen=True)
class RigidLanding(Landing):
    """A rigid piece's landing (flight6 with rigid_aero's drag), with a `[flight] goal`: "scored" or "missed", and
    why it missed: `blocked_by`, what it hit on the way in, or `caught`, the rim plate or edge it overlapped at the
    opening (it didn't fit through)."""
    goal: str | None = None
    blocked_by: str | None = None
    caught: str | None = None


def landings(result: BenchResult) -> list[tuple[PieceOutcome, Landing]]:
    """Each piece out through `[flight] outlet`, flown from where and how it left: a ball by the launcher model's ball
    flight, a rigid piece by flight6 with rigid_aero's drag (its orientation and spin as it left). Empty with no
    [flight]."""
    fl, piece = result.bench.flight, result.bench.piece
    if fl is None:
        return []
    if piece.ball is None:
        return _rigid_landings(result)
    air = Air(**PRESETS["air"][fl.air])
    goal = Goal(distance=fl.target_distance, height=fl.target_height,
                diameter=fl.ring_diameter if fl.ring_diameter is not None else piece.ball.diameter)
    out = []
    for p in result.pieces:
        if p.status != "out" or p.outlet != fl.outlet:
            continue
        _, x, y, z = p.path[-1]
        e, yaw = math.radians(p.elevation_deg), math.radians(p.yaw_deg)
        v = p.speed * np.array([math.cos(e) * math.cos(yaw), math.cos(e) * math.sin(yaw), math.sin(e)])
        spin = np.array(p.spin, dtype=float)
        w = float(np.linalg.norm(spin))
        axis = spin / w if w > 0.0 else np.array([0.0, -1.0, 0.0])
        out.append((p, fly_state([x, y, z + fl.height], v, axis, w, piece.ball, air, goal)))
    return out


@dataclass(frozen=True)
class _Air:
    """What rigid_aero.force_fn reads from a kind."""
    shadow_areas: tuple[float, float, float]
    drag_coefficient: float
    spin_damping: float


def _model_solids(result: BenchResult) -> list[Solid]:
    """Pool piece 0's geoms as Solids in its body frame (from the compiled model, so a mesh is what collides)."""
    m, lay = result.built.model, result.built.layout
    out = []
    for g in lay.piece_geom_sets[0]:
        R = np.zeros(9)
        mujoco.mju_quat2Mat(R, m.geom_quat[g])
        R, pos, size = R.reshape(3, 3), m.geom_pos[g].copy(), m.geom_size[g]
        kind = m.geom_type[g]
        if kind == mujoco.mjtGeom.mjGEOM_SPHERE:
            out.append(Solid("sphere", (float(size[0]),), tuple(map(float, pos)), (1.0, 0.0, 0.0, 0.0)))
            continue
        if kind == mujoco.mjtGeom.mjGEOM_BOX:
            local = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]) * size[:3]
        elif kind == mujoco.mjtGeom.mjGEOM_CYLINDER:
            local = _round(float(size[0]), float(size[0]), 2.0 * float(size[1])).vertices
        else:
            k = m.geom_dataid[g]
            local = m.mesh_vert[m.mesh_vertadr[k]:m.mesh_vertadr[k] + m.mesh_vertnum[k]]
        out.append(Solid("mesh", (), (0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), vertices=pos + local @ R.T))
    return out


def _rigid_air(result: BenchResult) -> tuple[_Air, np.ndarray, float, np.ndarray]:
    """A rigid piece's air (its shadows, drag coefficient and spin damping), its body-frame inertia (3x3), its
    passing width (the smallest side of its bounding box) and that box's 8 corners in its body frame. A ring or piece
    preset is its kind's (the range's own numbers: piece_box's box); any other shape's shadows and box come from its
    solids as built, with the [piece]'s drag and damping."""
    piece = result.bench.piece
    kind = piece.rigid_kind
    if kind is not None:
        xx, yy, zz, xy, xz, yz = kind.inertia
        inertia = np.array([[xx, xy, xz], [xy, yy, yz], [xz, yz, zz]])
        return (_Air(kind.shadow_areas, kind.drag_coefficient, kind.spin_damping), inertia, passing_width(kind),
                goal_fit.kind_corners(kind))
    solids = _model_solids(result)
    pts = np.concatenate([s.vertices if s.kind == "mesh" else np.array([np.array(s.pos) - s.size[0],
                                                                         np.array(s.pos) + s.size[0]])
                          for s in solids])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    air = _Air(tuple(shadow_area(solids, k) for k in range(3)), piece.drag_coefficient, piece.spin_damping)
    return air, np.diag(piece.inertia), float((hi - lo).min()), goal_fit.box_corners(lo, hi)


def _rot_z(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _rigid_landings(result: BenchResult) -> list[tuple[PieceOutcome, Landing]]:
    """Rigid pieces through `[flight] outlet`, each flown by flight6 from its exit state: the center, the velocity,
    the orientation of its last pose and its spin (in its body frame). With a goal, the bench stands at `at` facing
    `heading_deg` on the goal's field, and testrange/goal_fit.py says whether the shot scores. Positions are
    reported in the bench's floor frame (the bench origin on the floor below it) either way."""
    fl = result.bench.flight
    air, inertia, width, corners = _rigid_air(result)
    rho = Air(**PRESETS["air"][fl.air]).density
    force = rigid_aero.force_fn(air, rho)
    g = fl.goal
    turn = _rot_z(g.heading_deg) if g else np.eye(3)
    origin = np.array([g.at[0], g.at[1], 0.0]) if g else np.zeros(3)
    half_turn = math.radians(g.heading_deg) / 2.0 if g else 0.0
    q_turn = np.array([math.cos(half_turn), 0.0, 0.0, math.sin(half_turn)])
    surface = None if g is None else g.goal.scoring_surface()
    goals = () if g is None else ((g.name, surface),)
    out = []
    for p in result.pieces:
        if p.status != "out" or p.outlet != fl.outlet:
            continue
        _, x, y, z = p.path[-1]
        e, yaw = math.radians(p.elevation_deg), math.radians(p.yaw_deg)
        v = p.speed * np.array([math.cos(e) * math.cos(yaw), math.cos(e) * math.sin(yaw), math.sin(e)])
        q = np.asarray(p.poses[-1][1][3:7], dtype=float)
        rot = flight6.matrix_from_quat(q / np.linalg.norm(q))
        omega = rot.T @ np.asarray(p.spin, dtype=float)
        w0, v0 = q_turn[0], q_turn[1:]  # the bench's turn on the field, then the piece's own: q_turn (x) q
        q_field = np.r_[w0 * q[0] - v0 @ q[1:], w0 * q[1:] + q[0] * v0 + np.cross(v0, q[1:])]
        res = flight6.fly(origin + turn @ np.array([x, y, z + fl.height]), turn @ v, q_field, omega,
                          result.bench.piece.mass, inertia, force, floor=width / 2.0, goals=goals)
        path = (res.samples[:, flight6.POS] - origin) @ turn  # the floor frame: turn.T @ (P - origin), row by row
        at_goal = goal_time = None
        if fl.target_height is not None:
            zs = path[:, 2]
            down = np.flatnonzero((zs[:-1] > fl.target_height) & (zs[1:] <= fl.target_height))
            if len(down):
                k = int(down[0])
                f = (zs[k] - fl.target_height) / (zs[k] - zs[k + 1])
                at_goal = tuple(float(c) for c in path[k, :2] + f * (path[k + 1, :2] - path[k, :2]))
                ts = res.samples[:, flight6.T]
                goal_time = float(ts[k] + f * (ts[k + 1] - ts[k]))
        scored = False
        if at_goal is not None and fl.ring_diameter is not None:
            scored = math.hypot(at_goal[0] - fl.target_distance, at_goal[1]) <= (fl.ring_diameter - width) / 2.0
        verdict = blocked = caught = None
        if g is not None:
            fit = goal_fit.fit(res, surface, g.name, g.boxes, corners)
            verdict, blocked, caught = "scored" if fit.scored else "missed", fit.hit, fit.caught
        end = path[-1]
        out.append((p, RigidLanding(scored=bool(scored), at_goal=at_goal, floor=(float(end[0]), float(end[1])),
                                    apex=float(path[:, 2].max()), flight_time=res.flight_time, goal_time=goal_time,
                                    goal=verdict, blocked_by=blocked, caught=caught)))
    return out


def _flight_line(result: BenchResult, lands: list[tuple[PieceOutcome, Landing]]) -> str | None:
    fl = result.bench.flight
    if fl is None or not lands or fl.target_height is None:
        return None
    n = len(lands)
    xs = [land.at_goal[0] for _, land in lands if land.at_goal is not None]
    if not xs:
        return t("none of the {n} shots get up to the target's height ({height})", n=n,
                 height=_far(fl.target_height, 1))
    if len(xs) > 1:
        where = t("{far} out ± {spread}", far=_far(np.mean(xs)), spread=_far(np.std(xs), unit=False))
    else:
        where = t("{far} out", far=_far(np.mean(xs)))
    at = {"height": _far(fl.target_height, 1), "distance": _far(fl.target_distance, 1), "where": where}
    if fl.ring_diameter is not None:
        k = sum(land.scored for _, land in lands)
        return t("{k} of {n} shots go in the ring {distance} away, {height} up (they come down through its height "
                 "{where})", k=k, n=n, **at)
    return t("shots come down through {height} at {where} (the target is {distance} away)", **at)


def _goal_line(result: BenchResult, lands: list[tuple[PieceOutcome, Landing]]) -> str | None:
    """With a `[flight] goal`: how many shots score in it, and what the blocked ones hit first."""
    fl = result.bench.flight
    if fl is None or fl.goal is None or not lands:
        return None
    k = sum(getattr(land, "goal", None) == "scored" for _, land in lands)
    line = t("{k} of {n} shots score in {goal}", k=k, n=len(lands), goal=_name(fl.goal.name))
    hits = Counter(land.blocked_by for _, land in lands if getattr(land, "blocked_by", None))
    if hits:
        what, b = hits.most_common(1)[0]
        line = t("{line} ({b} hit {what} on the way)", line=line, b=b, what=_name(what))
    caught = Counter(land.caught for _, land in lands if getattr(land, "caught", None))
    if caught:
        what, c = caught.most_common(1)[0]
        line = t("{line} ({c} didn't fit through the opening and caught {what})", line=line, c=c, what=_name(what))
    return line


def energy_failures(result: BenchResult) -> int:
    """How many pieces gained energy from nowhere (EnergyCheck.ok false): the simulation, not the design, is wrong."""
    return sum(not e.ok for e in result.energy)


def _energy_line(result: BenchResult) -> str | None:
    bad = energy_failures(result)
    if not bad:
        return None
    return t("the energy check failed for {bad} of {n} pieces: the simulation is wrong here, not the design; try a "
             "smaller {run_table} timestep", bad=bad, n=len(result.energy), run_table="[run]")


def headlines(result: BenchResult, lands: list[tuple[PieceOutcome, Landing]] | None = None) -> list[str]:
    """Three to six lines for a room: a failed energy check first (nothing else can be trusted), then outcomes,
    UNCALIBRATED last."""
    n = len(result.pieces)
    by_outlet: dict[str, list[PieceOutcome]] = {}
    for p in result.pieces:
        if p.status == "out":
            by_outlet.setdefault(p.outlet, []).append(p)
    lines = [_out_line(o, ps, n) for o, ps in sorted(by_outlet.items(), key=lambda kv: -len(kv[1]))]
    if not lines and result.bench.outlets:
        lines.append(t("0 of {n} got out through {outlet}", n=n, outlet=_name(result.bench.outlets[0].name)))
    slip = _slip_line(result)
    if slip:
        lines.append(slip)
    rest = _not_out_line(result, n)
    if rest:
        lines.append(rest)
    lands = landings(result) if lands is None else lands
    extra = [_goal_line(result, lands), _flight_line(result, lands), _load_line(result), _shaft_line(result),
             _time_line(result)]
    lines += [x for x in extra if x]
    energy = _energy_line(result)
    if energy:
        lines.insert(0, energy)
    return lines[:MAX_LINES - 1] + [uncalibrated(result)]


# ----------------------------------------------------------------------------------------------------- tables

def _f(v, digits: int = 3) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.{digits}f}".replace("-0." + "0" * digits, "0." + "0" * digits)  # no "-0.000"
    return str(v)


def _tables(result: BenchResult, lands) -> dict[str, tuple[list[str], list[list]]]:
    """Each table's header and rows of values, in SI (formatted by _csv_table and _shown_table)."""
    ok = {e.index: e.ok for e in result.energy}
    pieces = (["piece", "inlet", "status", "outlet", "t_in_s", "t_out_s", "time_inside_s", "speed_m_s",
               "elevation_deg", "yaw_deg", "spin_rps", "spin_x_rad_s", "spin_y_rad_s", "spin_z_rad_s", "tilt_deg",
               "exit_slip_pct", "peak_force_n", "energy_ok"], [])
    squishy = result.bench.piece.kind == "squishy"  # a ball has no up: its tilt means nothing
    for p in result.pieces:
        spin = p.spin or (None, None, None)
        rps = None if p.spin is None else float(np.linalg.norm(p.spin)) / (2 * math.pi)
        inside = None if p.t_in is None or p.t_out is None else p.t_out - p.t_in
        pieces[1].append([p.index, p.inlet, p.status, p.outlet, p.t_in, p.t_out, inside, p.speed, p.elevation_deg,
                          p.yaw_deg, rps, *spin, None if squishy else p.tilt_deg,
                          None if p.exit_slip is None else 100.0 * p.exit_slip, p.peak_force, ok.get(p.index)])
    shafts = (["shaft", "target_rps", "min_rps", "drop_pct", "recovery_s", "back_before_next", "peak_current_a",
               "energy_per_piece_j"],
              [[s.name, s.target_rps, s.min_rps, s.drop_pct, s.recovery_s, s.back_before_next, s.peak_current,
                s.energy_per_piece_j] for s in result.shafts.values()])
    loads = (["shape", "peak_force_n", "peak_impulse_ns", "at_x_m", "at_y_m", "at_z_m"],
             [[ld.shape, ld.peak_force_n, ld.peak_impulse_ns, *(ld.at or (None,) * 3)]
              for ld in sorted(result.loads.values(), key=lambda ld: -ld.peak_force_n)])
    out = {"pieces": pieces, "shafts": shafts, "loads": loads}
    fl = result.bench.flight
    if lands:
        rows = []
        for p, land in lands:
            _, x, y, z = p.path[-1]
            gx, gy = land.at_goal or (None, None)
            miss = None if land.at_goal is None else math.hypot(gx - fl.target_distance, gy)
            ring = None if fl.ring_diameter is None else land.scored
            rows.append([p.index, p.outlet, x, y, z + fl.height, p.speed, p.elevation_deg, land.apex, gx, gy, miss,
                         ring, *land.floor, land.flight_time] + ([land.goal] if fl.goal is not None else []))
        out["landings"] = (["piece", "outlet", "exit_x_m", "exit_y_m", "exit_height_m", "speed_m_s", "elevation_deg",
                            "apex_m", "target_height_x_m", "target_height_y_m", "miss_m", "in_ring", "floor_x_m",
                            "floor_y_m", "flight_time_s"] + (["goal"] if fl.goal is not None else []), rows)
    return out


# summary.txt's columns with a unit, by kind: shown in the Units setting, the unit in the column's name (speed_ft_s,
# at_x_in). The CSVs keep them in SI. A position on the bench is a length; where a shot flies, a field length.
SHOWN_KINDS = {"speed_m_s": "speed", "peak_force_n": "force", "at_x_m": "length", "at_y_m": "length",
               "at_z_m": "length", "exit_x_m": "length", "exit_y_m": "length", "exit_height_m": "length",
               "apex_m": "field", "target_height_x_m": "field", "target_height_y_m": "field", "miss_m": "field",
               "floor_x_m": "field", "floor_y_m": "field"}


def _csv_table(header: list[str], rows: list[list]) -> tuple[list[str], list[list[str]]]:
    return header, [[_f(v) for v in r] for r in rows]


def _shown_table(header: list[str], rows: list[list]) -> tuple[list[str], list[list[str]]]:
    """A table for summary.txt: SHOWN_KINDS' columns in the Units setting, renamed for their unit."""
    kinds = [SHOWN_KINDS.get(c) for c in header]

    def named(c: str, kind: str) -> str:
        stem = c[:-len("_m_s")] if c.endswith("_m_s") else c.rsplit("_", 1)[0]
        return f"{stem}_{units.unit_name(kind).replace('/', '_').lower()}"

    def shown(v, kind):
        return units.to_display(v, kind) if kind and isinstance(v, float) else v

    return ([c if k is None else named(c, k) for c, k in zip(header, kinds)],
            [[_f(shown(v, k)) for v, k in zip(r, kinds)] for r in rows])


def _text_table(header: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(header)]
    fmt = "  ".join(f"{{:>{w}}}" for w in widths)
    return "\n".join([fmt.format(*header), *(fmt.format(*r) for r in rows)])


SUMMARY_DATA = "summary.json"   # summary.txt's layout as data, for the Bench tab (it never reads their words)


def write_results(result: BenchResult, out_dir: Path) -> Path:
    """summary.txt (the headlines and tables), summary.json (SUMMARY_DATA), pieces.csv, shafts.csv, loads.csv, and
    with [flight] landings.csv."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lands = landings(result)
    tables = _tables(result, lands)
    for name, table in tables.items():
        header, rows = _csv_table(*table)
        with open(out_dir / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)
    with i18n.for_people():     # summary.txt is for people: in the Units and Language settings (an MCP run's too)
        heads = headlines(result, lands)
        (out_dir / "summary.txt").write_text(_summary(result, heads, tables), encoding="utf-8")
        title_lines = _title_lines(result.bench)
    # summary.txt's layout as data (machine JSON: no words, nothing in the Units setting): how many lines the title
    # block takes (its notes may hold blank lines), how many headline lines follow its blank line, whether the last
    # of them is the UNCALIBRATED line, and the energy check
    data = {"title_lines": title_lines, "headlines": len(heads), "uncalibrated": True,
            "energy_failed": energy_failures(result) > 0}
    (out_dir / SUMMARY_DATA).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return out_dir


def _title(b) -> list[str]:
    """summary.txt's title block: the bench's name and author, its idea, its notes (which may run over lines)."""
    text = [b.name + (f" ({b.author})" if b.author else "")]
    if b.idea:
        text.append(t('idea: "{idea}"', idea=b.idea))
    if b.notes:
        text.append(b.notes)
    return text


def _title_lines(b) -> int:
    """The lines summary.txt's title block takes as a reader splits the file (str.splitlines): the headlines start
    after them and one blank line."""
    return len(("\n".join(_title(b)) + "\n\n").splitlines()) - 1


def _summary(result: BenchResult, heads: list[str], tables) -> str:
    """summary.txt's text; `heads` is headlines()'."""
    b = result.bench
    text = _title(b)
    text += ["", *heads, *(t("warning: {warning}", warning=w) for w in b.warnings), ""]
    titles = {"pieces": t("Pieces"), "shafts": t("Shafts"), "loads": t("Loads (normal contact force on each part)"),
              "landings": t("Landings (the launcher model's ball flight, from the outlet)") if b.piece.ball is not None
              else t("Landings (a rigid piece's flight from the outlet: drag on its shadow, no lift)")}
    for name, table in tables.items():
        text += [titles[name], _text_table(*_shown_table(*table)), ""]
    bad = [e.index for e in result.energy if not e.ok]
    if bad:
        text.append(t("Energy check: pieces {pieces} gained energy from nowhere (a failure means the simulation is "
                      "wrong, not the design)", pieces=", ".join(map(str, bad))))
    else:
        text.append(t("Energy check: ok (a failure means the simulation is wrong, not the design)"))
    return "\n".join(text) + "\n"
