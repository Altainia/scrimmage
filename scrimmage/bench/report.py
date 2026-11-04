"""A bench run's results in plain words: headlines for a room, the tables, CSVs, and with `[flight]` where the shots
land.

Headlines are three to six lines, read on a projector: what came out where and how fast, what didn't get out, the
part taking the hardest hits (when one stands out), the shaft that slows most, and always last, UNCALIBRATED with
what to measure. Names from the bench file are in backticks.
"""

import csv
import math
from collections import Counter
from pathlib import Path

import mujoco
import numpy as np

from ..launcher.flight import Landing, fly_state
from ..launcher.inputs import PRESETS, Air, Goal
from ..measure import labels
from .run import BenchResult, PieceOutcome

# path colors by outcome (RGB): out green, jammed red, escaped orange, timed out gray
STATUS_COLORS = {"out": (0, 158, 60), "jammed": (220, 30, 40), "escaped": (245, 140, 0),
                 "timed_out": (130, 130, 130), "stuck_at_inlet": (130, 130, 130)}
STATUS_WORDS = {"out": "left through an outlet", "jammed": "jammed", "escaped": "escaped the bench",
                "timed_out": "still inside at the end", "stuck_at_inlet": "never got in"}
LOAD_STANDS_OUT = 3.0   # a shape's peak force over this many times the other touched shapes' median is a headline
MAX_LINES = 6           # headlines, UNCALIBRATED included
JAM_NEAR = 0.05         # m: a jammed piece is "at" the nearest shape within this distance

_MEASURE = {
    "squishy": "measure the ball's stiffness (a squeeze test) and its bounce (a drop test) first; see "
               "docs/launcher.md, \"Measure these first\"",
    "rigid": "measure the tread's friction against the piece first (a sled pull: pull force / pressing force)",
}


def uncalibrated(result: BenchResult) -> str:
    return bench_uncalibrated(result.bench)


ESTIMATES = "UNCALIBRATED: estimates from preset numbers, not measurements"
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
    return f"{bench_estimates(bench)}; {_MEASURE[bench.piece.kind]}."


def _name(s: str) -> str:
    return f"`{s}`"


def _direction(elevation_deg: float, yaw: float) -> str:
    e = round(elevation_deg)
    text = "level" if e == 0 else f"{abs(e)}° {'up' if e > 0 else 'down'}"
    if abs(yaw) >= 178.0:
        text += ", going backwards"
    elif abs(yaw) > 2.0:
        text += f", {abs(yaw):.0f}° to the {'left' if yaw > 0 else 'right'}"
    return text


def _out_line(outlet: str, out: list[PieceOutcome], n: int) -> str:
    speeds = np.array([p.speed for p in out])
    spread = f" ± {speeds.std():.1f}" if len(out) > 1 else ""
    yaw = np.radians([p.yaw_deg for p in out])  # a circular mean: -179 and 179 average to 180, not 0
    way = _direction(float(np.mean([p.elevation_deg for p in out])),
                     math.degrees(math.atan2(np.sin(yaw).mean(), np.cos(yaw).mean())))
    return f"{len(out)} of {n} left through {_name(outlet)} at {speeds.mean():.1f} m/s{spread}, {way}"


def jam_place(result: BenchResult, piece: PieceOutcome) -> str | None:
    """The shape nearest where a piece stopped (within JAM_NEAR), or None."""
    m, lay = result.built.model, result.built.layout
    d = mujoco.MjData(m)
    a = lay.piece_qpos[piece.index]
    d.qpos[a:a + 3] = piece.path[-1][1:]
    d.qpos[a + 3:a + 7] = (1.0, 0.0, 0.0, 0.0)
    mujoco.mj_kinematics(m, d)
    g = lay.piece_geoms[piece.index]
    best, name = JAM_NEAR, None
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
    for p in rest:
        if p.status == "jammed":
            at = jam_place(result, p)
            parts[f"jammed at {_name(at)}" if at else "jammed inside the bench"] += 1
        else:
            parts[STATUS_WORDS[p.status]] += 1
    if len(parts) == 1:
        (what, k), = parts.items()
        return f"{k} of {n} {what}"
    return f"{len(rest)} of {n} didn't get out: " + ", ".join(f"{k} {what}" for what, k in parts.most_common())


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
    return (f"the {_name(shape)} takes the hardest hits: up to {peak:.0f} N, {peak / median:.1f} times a typical "
            f"other part's ({median:.0f} N)")


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
    return (f"the balls are still slipping on the wheels when they leave ({100 * s:.0f}% slip): more wheel speed won't "
            f"make them much faster; more grip will (more squeeze, more or wider wheels, a longer wrap)")


def _shaft_line(result: BenchResult) -> str | None:
    touched = [s for s in result.shafts.values() if s.drop_pct > 0.0]
    if not touched:
        return None
    s = max(touched, key=lambda s: s.drop_pct)
    if s.back_before_next is False:
        back = "isn't back to speed when the next piece arrives (feed slower, or add a flywheel)"
    elif s.recovery_s is not None:
        back = f"recovers in {s.recovery_s:.2f} s"
    else:
        back = "has not recovered when the run ends"
    line = f"the {_name(s.name)} shaft drops {s.drop_pct:.0f}% and {back}"
    n = len(result.shafts)
    if n > 1:
        line += f" (the {'worse' if n == 2 else 'worst'} of {n} shafts)"
    return line


def _time_line(result: BenchResult) -> str | None:
    out = [p.t_out - p.t_in for p in result.pieces if p.status == "out"]
    if not out:
        return None
    return f"a piece takes {float(np.median(out)):.2f} s from the inlet to the outlet"


# ----------------------------------------------------------------------------------------------------- flight

def landings(result: BenchResult) -> list[tuple[PieceOutcome, Landing]]:
    """Each piece out through `[flight] outlet`, flown by the launcher model's ball flight from where and how it left.
    Balls only (squishy pieces): empty for a rigid piece or with no [flight]."""
    fl, piece = result.bench.flight, result.bench.piece
    if fl is None or piece.ball is None:
        return []
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


def _flight_line(result: BenchResult, lands: list[tuple[PieceOutcome, Landing]]) -> str | None:
    fl = result.bench.flight
    if fl is None or not lands:
        return None
    n = len(lands)
    xs = [land.at_goal[0] for _, land in lands if land.at_goal is not None]
    if not xs:
        return f"none of the {n} shots get up to the target's height ({fl.target_height:.1f} m)"
    where = f"{np.mean(xs):.2f} m out" + (f" ± {np.std(xs):.2f}" if len(xs) > 1 else "")
    if fl.ring_diameter is not None:
        k = sum(land.scored for _, land in lands)
        return (f"{k} of {n} shots go in the ring {fl.target_distance:.1f} m away, {fl.target_height:.1f} m up "
                f"(they come down through its height {where})")
    return (f"shots come down through {fl.target_height:.1f} m at {where} (the target is {fl.target_distance:.1f} m "
            "away)")


def energy_failures(result: BenchResult) -> int:
    """How many pieces gained energy from nowhere (EnergyCheck.ok false): the simulation, not the design, is wrong."""
    return sum(not e.ok for e in result.energy)


def _energy_line(result: BenchResult) -> str | None:
    bad = energy_failures(result)
    if not bad:
        return None
    return (f"the energy check failed for {bad} of {len(result.energy)} pieces: the simulation is wrong here, not the "
            "design; try a smaller [run] timestep")


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
        lines.append(f"0 of {n} got out through {_name(result.bench.outlets[0].name)}")
    slip = _slip_line(result)
    if slip:
        lines.append(slip)
    rest = _not_out_line(result, n)
    if rest:
        lines.append(rest)
    lands = landings(result) if lands is None else lands
    extra = [_flight_line(result, lands), _load_line(result), _shaft_line(result), _time_line(result)]
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


def _tables(result: BenchResult, lands) -> dict[str, tuple[list[str], list[list[str]]]]:
    ok = {e.index: e.ok for e in result.energy}
    pieces = (["piece", "inlet", "status", "outlet", "t_in_s", "t_out_s", "time_inside_s", "speed_m_s",
               "elevation_deg", "yaw_deg", "spin_rps", "spin_x_rad_s", "spin_y_rad_s", "spin_z_rad_s", "tilt_deg",
               "exit_slip_pct", "peak_force_n", "energy_ok"], [])
    squishy = result.bench.piece.kind == "squishy"  # a ball has no up: its tilt means nothing
    for p in result.pieces:
        spin = p.spin or (None, None, None)
        rps = None if p.spin is None else float(np.linalg.norm(p.spin)) / (2 * math.pi)
        inside = None if p.t_in is None or p.t_out is None else p.t_out - p.t_in
        pieces[1].append([_f(v) for v in (p.index, p.inlet, p.status, p.outlet, p.t_in, p.t_out, inside, p.speed,
                                          p.elevation_deg, p.yaw_deg, rps, *spin, None if squishy else p.tilt_deg,
                                          None if p.exit_slip is None else 100.0 * p.exit_slip,
                                          p.peak_force, ok.get(p.index))])
    shafts = (["shaft", "target_rps", "min_rps", "drop_pct", "recovery_s", "back_before_next", "peak_current_a",
               "energy_per_piece_j"],
              [[_f(v) for v in (s.name, s.target_rps, s.min_rps, s.drop_pct, s.recovery_s, s.back_before_next,
                                s.peak_current, s.energy_per_piece_j)] for s in result.shafts.values()])
    loads = (["shape", "peak_force_n", "peak_impulse_ns", "at_x_m", "at_y_m", "at_z_m"],
             [[_f(v) for v in (ld.shape, ld.peak_force_n, ld.peak_impulse_ns, *(ld.at or (None,) * 3))]
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
            rows.append([_f(v) for v in (p.index, p.outlet, x, y, z + fl.height, p.speed, p.elevation_deg, land.apex,
                                         gx, gy, miss, ring, *land.floor, land.flight_time)])
        out["landings"] = (["piece", "outlet", "exit_x_m", "exit_y_m", "exit_height_m", "speed_m_s", "elevation_deg",
                            "apex_m", "target_height_x_m", "target_height_y_m", "miss_m", "in_ring", "floor_x_m",
                            "floor_y_m", "flight_time_s"], rows)
    return out


def _text_table(header: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(header)]
    fmt = "  ".join(f"{{:>{w}}}" for w in widths)
    return "\n".join([fmt.format(*header), *(fmt.format(*r) for r in rows)])


def write_results(result: BenchResult, out_dir: Path) -> Path:
    """summary.txt (the headlines and tables), pieces.csv, shafts.csv, loads.csv, and with [flight] landings.csv."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lands = landings(result)
    tables = _tables(result, lands)
    for name, (header, rows) in tables.items():
        with open(out_dir / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)
    b = result.bench
    text = [b.name + (f" ({b.author})" if b.author else "")]
    if b.idea:
        text.append(f'idea: "{b.idea}"')
    if b.notes:
        text.append(b.notes)
    text += ["", *headlines(result, lands), *(f"warning: {w}" for w in b.warnings), ""]
    titles = {"pieces": "Pieces", "shafts": "Shafts", "loads": "Loads (normal contact force on each part)",
              "landings": "Landings (the launcher model's ball flight, from the outlet)"}
    for name, (header, rows) in tables.items():
        text += [titles[name], _text_table(header, rows), ""]
    if b.flight is not None and b.piece.ball is None:
        text += ["[flight] flies balls only; a rigid piece's flight is not modeled yet, so there are no landings.",
                 ""]
    bad = [e.index for e in result.energy if not e.ok]
    text.append(f"Energy check: {'pieces ' + ', '.join(map(str, bad)) + ' gained energy from nowhere' if bad else 'ok'}"
                " (a failure means the simulation is wrong, not the design)")
    (out_dir / "summary.txt").write_text("\n".join(text) + "\n", encoding="utf-8")
    return out_dir
