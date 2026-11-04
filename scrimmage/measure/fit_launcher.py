"""The launcher shot fit (spec C8): a ball's `stiffness_n` and a wheel's `friction`, fit so the launcher model's exit
speeds match filmed shots.

    [shot_setup]
    setup = "shooter.toml"        # the launcher setup the shots were taken on, relative to this file
    marks_in = 12                 # the tape marks' spacing, and the camera's frame rate, once for all shots
    fps = 240

    [[shot]]                      # one table per shot
    wheel_rps = 50                # the wheel speed it was shot at, held steady (wheel rotations per second)
    hood_deg = 45                 # optional: the hood's exit angle, else the setup's
    frames = 10.5                 # frames counted while the ball crosses from one mark to the next

A shot's exit speed is the mark spacing times fps over the frames counted. `marks_<unit>`, `fps` and `hood_deg` may
be given once in [shot_setup] or in a shot for that shot alone.

The model is the launcher's own: one ball fed at the setup's entry speed into the feed lane nearest the middle, with
the wheel spinning at the shot's speed when it enters and the setup's own velocity loop holding it as the ball
passes (the same single-ball pass as the aiming table's, scrimmage/testrange/aim.py, with the setup's gains). A
`scrimmage launcher run` burst feeds a ball once the wheel is within the feed tolerance of its target, so its first
ball can meet a wheel up to 5% slow; a filmed shot's wheel is held at its speed, so the fit starts the ball there.
The setup's [spread] is not used: the model is run once per wheel speed and hood angle, without random variation.

The search: stiffness from 0.25x to 4x the setup's (on a log scale) and friction from 0.2 to 2.0, a 9 x 9 grid,
then a Nelder-Mead refinement from the grid's best point. It minimizes the root mean square of each shot's
relative error, (model - measured) / measured; a ball the model doesn't launch counts as an error of 1. Shots at
wheel speeds less than 10% apart fit the stiffness alone, friction kept at the setup's: one speed can't tell a
softer ball from a slipperier wheel.

A fit is good with an RMS error under 5% and no fitted number within 5% of its search limit (a fit pinned at a
limit means the shots ask for more than it tries: often a miscounted frame or a wrong setup).
"""

import dataclasses
import math
from dataclasses import dataclass
from pathlib import Path

from ..launcher.inputs import LauncherConfigError, Setup, load_setup
from ..launcher.model import FedBall, Launcher
from ..launcher.shaft import VelocityController
from .file import MeasureError, MeasureFile
from .units import reading, unit_of

STIFFNESS_RANGE = (0.25, 4.0)  # times the setup's stiffness_n
FRICTION_RANGE = (0.2, 2.0)
GRID = 9  # points per searched number
GOOD_RMS = 0.05  # a good fit's RMS relative error is under this
EDGE = 0.05  # a fitted number within this fraction of a search limit is at the edge
SECOND_SPEED = 1.10  # the fastest shot's wheel speed must be this many times the slowest's to fit friction
TOLERANCE = 1e-3  # the refinement stops when its points are this close (in grid units, 0 to 1 across the range)
MAX_STEPS = 200  # the refinement's limit on steps (each runs the model at one to three points)
MAX_PASS_S = 1.0  # s: a ball still in the launcher after this long is not coming out

_NUMBERS = ("wheel_rps", "hood_deg", "fps")  # plain numbers, their unit in their name
_SETUP_NUMBERS = ("hood_deg", "fps")


@dataclass(frozen=True, kw_only=True)
class LauncherFit:
    stiffness_n: float  # the ball's, N
    friction: float  # the wheel's
    rms_error: float  # root mean square relative error of the shots' exit speeds: 0.03 is 3%
    per_shot: list[tuple[float, float]]  # (measured, model) exit speed per shot in file order, m/s; model NaN if no launch
    fitted: tuple[str, ...]  # ("stiffness_n", "friction"), or ("stiffness_n",) from one wheel speed
    warnings: list[str]  # plain sentences
    good: bool  # RMS under 5% and no fitted number at the edge of its range
    why: str  # empty when good, else why not, in a few words


@dataclass(frozen=True)
class _Shot:
    wheel_rps: float
    hood_deg: float
    speed: float  # measured, m/s


def exit_speed(setup: Setup, wheel_rps: float, hood_deg: float | None = None) -> float:
    """The model's exit speed (m/s) for one ball through `setup` with the wheel at `wheel_rps` (and the hood at
    `hood_deg`, else the setup's); NaN if the ball isn't launched."""
    if hood_deg is not None:
        setup = dataclasses.replace(setup, hood=dataclasses.replace(setup.hood, exit_angle_deg=hood_deg))
    launcher = Launcher(setup)
    launcher.shaft.speed = 2.0 * math.pi * wheel_rps
    ctrl = VelocityController(dataclasses.replace(setup.control, target_rps=wheel_rps), setup.shaft)
    fed = FedBall(0.0, min(setup.feed.lanes, key=abs), setup.feed.entry_speed, setup.ball)
    shot = launcher.insert(fed, 0.0)
    dt = launcher.default_dt()
    exits = [] if shot is None else [shot]
    while not exits and launcher.in_contact() and launcher.time < MAX_PASS_S:
        exits = launcher.step(dt, ctrl.voltage(launcher.shaft))
    return exits[0].speed if exits and exits[0].status == "launched" else math.nan


# ------------------------------------------------------------------------------------------------ reading the file

def _number(table: dict, key: str, where: str) -> float | None:
    if key not in table:
        return None
    v = table[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise MeasureError(f"{where}: {key} must be a number, not {v!r}")
    return float(v)


def _is_reading(key: str) -> bool:
    return unit_of(key, "marks") is not None or unit_of(key, "frames") is not None


def _check_keys(mf: MeasureFile, setup: dict, rows: list[dict]) -> None:
    for key in setup:
        if key == "wheel_rps" or unit_of(key, "frames") is not None:
            raise MeasureError(f"{mf.path}: {key} goes in each [[shot]], not in [shot_setup]")
        if key != "setup" and key not in _SETUP_NUMBERS and unit_of(key, "marks") is None:
            raise MeasureError(f"{mf.path}: unknown key '{key}' in [shot_setup]; it takes setup, marks_<unit>, "
                               "fps, hood_deg")
    for i, row in enumerate(rows):
        for key in row:
            if key == "setup":
                raise MeasureError(f"{mf.path}: setup goes in [shot_setup], once for all shots, not in [[shot]] "
                                   f"trial {i + 1}")
            if key not in _NUMBERS and not _is_reading(key):
                raise MeasureError(f"{mf.path}: unknown reading '{key}' in [[shot]] trial {i + 1}; it takes "
                                   "wheel_rps, hood_deg, marks_<unit>, frames, fps")


def _load_setup(mf: MeasureFile, table: dict) -> Setup:
    if "setup" not in table:
        raise MeasureError(f"{mf.path}: [shot_setup] is missing required key 'setup': the launcher setup file the "
                           "shots were taken on")
    name = table["setup"]
    if not isinstance(name, str):
        raise MeasureError(f"{mf.path}: [shot_setup] setup must be text, not {name!r}")
    path = mf.path.parent / name
    if not path.is_file():
        raise MeasureError(f"{mf.path}: [shot_setup] setup = \"{name}\": no such file (looked in {path.resolve()})")
    try:
        return load_setup(path)[0]
    except (LauncherConfigError, OSError) as e:
        raise MeasureError(f"{mf.path}: [shot_setup] setup = \"{name}\": {e}") from None


def _shots(mf: MeasureFile, setup: Setup, table: dict) -> list[_Shot]:
    rows = mf.trials["shot"]
    here = f"{mf.path}: [shot_setup]"
    shots = []
    for i, row in enumerate(rows):
        where = f"{mf.path}: [[shot]] trial {i + 1}"

        def either(read):  # the shot's own reading, else [shot_setup]'s
            v = read(row, where)
            return read(table, here) if v is None else v

        rps = _number(row, "wheel_rps", where)
        if rps is None:
            raise MeasureError(f"{where} has no wheel_rps: the wheel speed it was shot at (rotations per second)")
        frames = reading(row, "frames", where=where)
        marks = either(lambda t, w: reading(t, "length", "marks", where=w, required=False))
        if marks is None:
            raise MeasureError(f"{where} has no marks: give the tape marks' spacing once in [shot_setup] or in "
                               "the shot, as marks_<unit>")
        fps = either(lambda t, w: _number(t, "fps", w))
        if fps is None:
            raise MeasureError(f"{where} has no fps: give the camera's frame rate once in [shot_setup] or in the "
                               "shot (fps = 240)")
        for name, v in (("wheel_rps", rps), ("frames", frames), ("marks", marks), ("fps", fps)):
            if v <= 0:
                raise MeasureError(f"{where}: {name} must be above 0, not {v:g}")
        hood = either(lambda t, w: _number(t, "hood_deg", w))
        if hood is None:
            hood = setup.hood.exit_angle_deg
        else:
            try:
                Launcher(dataclasses.replace(setup, hood=dataclasses.replace(setup.hood, exit_angle_deg=hood)))
            except LauncherConfigError as e:
                raise MeasureError(f"{where}: hood_deg {hood:g} doesn't fit the setup's hood: {e}") from None
        shots.append(_Shot(wheel_rps=rps, hood_deg=hood, speed=marks * fps / frames))
    return shots


# ------------------------------------------------------------------------------------------------------ the search

def _minimize(cost, x0: tuple[float, ...], step: float) -> tuple[float, ...]:
    """Nelder-Mead from x0 over the unit box (points outside are moved onto its edge), first steps `step` long."""
    n = len(x0)

    def clamp(x):
        return tuple(min(1.0, max(0.0, c)) for c in x)

    simplex = [clamp(x0)]
    for i in range(n):
        x = list(x0)
        x[i] += step if x[i] + step <= 1.0 else -step
        simplex.append(clamp(x))
    values = [cost(x) for x in simplex]
    for _ in range(MAX_STEPS):
        order = sorted(range(n + 1), key=lambda j: values[j])
        simplex, values = [simplex[j] for j in order], [values[j] for j in order]
        if max(abs(a - b) for x in simplex[1:] for a, b in zip(x, simplex[0])) < TOLERANCE:
            break
        worst = simplex[-1]
        center = [sum(x[i] for x in simplex[:-1]) / n for i in range(n)]

        def toward(t):
            return clamp([c + t * (c - w) for c, w in zip(center, worst)])

        r = toward(1.0)
        fr = cost(r)
        if fr < values[0]:
            e = toward(2.0)
            fe = cost(e)
            simplex[-1], values[-1] = (e, fe) if fe < fr else (r, fr)
        elif fr < values[-2]:
            simplex[-1], values[-1] = r, fr
        else:
            c = toward(0.5) if fr < values[-1] else toward(-0.5)
            fc = cost(c)
            if fc < min(fr, values[-1]):
                simplex[-1], values[-1] = c, fc
            else:  # shrink toward the best
                best = simplex[0]
                simplex = [best] + [clamp([b + 0.5 * (a - b) for a, b in zip(x, best)]) for x in simplex[1:]]
                values = [values[0]] + [cost(x) for x in simplex[1:]]
    return simplex[min(range(n + 1), key=lambda j: values[j])]


def _rms(shots: list[_Shot], model: list[float]) -> float:
    errors = [1.0 if math.isnan(m) else (m - s.speed) / s.speed for s, m in zip(shots, model)]
    return math.sqrt(sum(e * e for e in errors) / len(errors))


def _at_edge(value: float, lo: float, hi: float) -> bool:
    return value <= lo * (1.0 + EDGE) or value >= hi * (1.0 - EDGE)


def _speeds(shots: list[_Shot]) -> str:
    lo, hi = min(s.wheel_rps for s in shots), max(s.wheel_rps for s in shots)
    return f"{lo:g} rps" if lo == hi else f"{lo:g} to {hi:g} rps"


def fit(mf: MeasureFile) -> LauncherFit:
    """Fit the ball's stiffness_n and the wheel's friction to a launcher file's shots (spec C8)."""
    if mf.kind != "launcher":
        raise MeasureError(f"{mf.path} is a {mf.kind} measurement: only a launcher file has shots to fit")
    if "shot" not in mf.trials:
        raise MeasureError(f"{mf.path}: a launcher file needs its shots, each written as a [[shot]] table")
    table = mf.settings.get("shot_setup", {})
    _check_keys(mf, table, mf.trials["shot"])
    setup = _load_setup(mf, table)
    shots = _shots(mf, setup, table)

    conditions = sorted({(s.wheel_rps, s.hood_deg) for s in shots})
    k0, f0 = setup.ball.stiffness_n, setup.wheels.friction
    k_lo, k_hi = k0 * STIFFNESS_RANGE[0], k0 * STIFFNESS_RANGE[1]
    f_lo, f_hi = FRICTION_RANGE
    cache: dict[tuple[float, float], list[float]] = {}

    def model(k: float, f: float) -> list[float]:
        """Each shot's model exit speed at stiffness k and friction f."""
        if (k, f) not in cache:
            trial = dataclasses.replace(setup, ball=dataclasses.replace(setup.ball, stiffness_n=k),
                                        wheels=dataclasses.replace(setup.wheels, friction=f))
            by_condition = {c: exit_speed(trial, *c) for c in conditions}
            cache[(k, f)] = [by_condition[(s.wheel_rps, s.hood_deg)] for s in shots]
        return cache[(k, f)]

    both = max(s.wheel_rps for s in shots) >= SECOND_SPEED * min(s.wheel_rps for s in shots)

    def values(x: tuple[float, ...]) -> tuple[float, float]:
        k = k_lo * (k_hi / k_lo) ** x[0]
        return k, (f_lo + (f_hi - f_lo) * x[1] if both else f0)

    def cost(x: tuple[float, ...]) -> float:
        return _rms(shots, model(*values(x)))

    ticks = [i / (GRID - 1) for i in range(GRID)]
    grid = [(u, v) for u in ticks for v in ticks] if both else [(u,) for u in ticks]
    start = min(grid, key=cost)
    k, f = values(_minimize(cost, start, 1.0 / (GRID - 1)))
    speeds = model(k, f)
    rms = _rms(shots, speeds)

    warnings, why = [], []
    if not both:
        warnings.append(f"the shots are all at about one wheel speed ({_speeds(shots)}), so only the ball's "
                        f"stiffness is fit and the wheel friction stays at the setup's {f0:g}: shoot at a second "
                        "wheel speed to fit friction too")
    edges = [f"the ball's stiffness_n, {k:.0f} N, is at the edge of what it tries: check your shots (it tries "
             f"{k_lo:.0f} to {k_hi:.0f} N)"] if _at_edge(k, k_lo, k_hi) else []
    if both and _at_edge(f, f_lo, f_hi):
        edges.append(f"the wheel friction, {f:.2f}, is at the edge of what it tries: check your shots (it tries "
                     f"{f_lo:g} to {f_hi:g})")
    if edges:
        warnings += edges
        why.append("the fit is at the edge of what it tries")
    if rms >= GOOD_RMS:
        warnings.append(f"the model's exit speeds miss your shots by {rms:.1%} (root mean square); a good fit is "
                        f"under {GOOD_RMS:.0%}: check your shots and the launcher setup")
        why.append(f"the model misses the shots by {rms:.0%}")
    missed = [i + 1 for i, m in enumerate(speeds) if math.isnan(m)]
    if missed:
        warnings.append(f"the model's ball doesn't leave the launcher for shot{'s' if len(missed) > 1 else ''} "
                        f"{', '.join(map(str, missed))}: check the setup's hood gap and wheel")
    return LauncherFit(stiffness_n=k, friction=f, rms_error=rms,
                       per_shot=[(s.speed, m) for s, m in zip(shots, speeds)],
                       fitted=("stiffness_n", "friction") if both else ("stiffness_n",),
                       warnings=warnings, good=not why, why="; ".join(why))
