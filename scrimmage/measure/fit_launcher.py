"""The launcher shot fit (spec C8): a ball's `stiffness_n` or a wheel's `friction`, fit so the launcher model's exit
speeds match filmed shots.

    [shot_setup]
    setup = "shooter.toml"        # the launcher setup the shots were taken on, relative to this file
    ball = "fuel.measure.toml"    # optional: the ball's squeeze test, so the shots fit the wheel's friction
    marks_in = 12                 # the tape marks' spacing, and the camera's frame rate, once for all shots
    fps = 240

    [[shot]]                      # one table per shot
    wheel_rps = 50                # the wheel speed it was shot at, held steady (wheel rotations per second)
    hood_deg = 45                 # optional: the hood's exit angle, else the setup's
    frames = 10.5                 # frames counted while the ball crosses from one mark to the next

A shot's exit speed is the mark spacing times fps over the frames counted. `marks_<unit>`, `fps` and `hood_deg` may
be given once in [shot_setup] or in a shot for that shot alone.

One number is fit, never both: in the model a stiffer ball and a grippier wheel speed a ball up in the same way at
every wheel speed, so shots alone can't tell them apart. With the ball's squeeze measured (a ball file with a good
squeeze test, named by `ball` here or by the setup's own `[ball] measured`), stiffness is fixed at it and the wheel
friction is fit. Otherwise the ball's stiffness is fit, with the wheel friction left at the setup's.

The setup is read as written, without its own measurements applied (only its `[ball] measured` ball file is read,
for the squeeze): a setup may name this very file, and applying it here would go round in a circle.

The model is the launcher's own: one ball fed at the setup's entry speed into the feed lane nearest the middle, with
the wheel spinning at the shot's speed when it enters and the setup's own velocity loop holding it as the ball
passes (the same single-ball pass as the aiming table's, scrimmage/testrange/aim.py, with the setup's gains). A
`scrimmage launcher run` burst feeds a ball once the wheel is within the feed tolerance of its target, so its first
ball can meet a wheel up to 5% slow; a filmed shot's wheel is held at its speed, so the fit starts the ball there.
The setup's [spread] is not used: the model is run once per wheel speed and hood angle, without random variation.

The search: stiffness from 0.25x to 4x the setup's (on a log scale), or friction from 0.2 to 2.0, scanned at 9
points, then a golden-section search between the best point's neighbors. It minimizes the root mean square of each
shot's relative error, (model - measured) / measured; a ball the model doesn't launch counts as an error of 1.

A fit is good with an RMS error under 5% and the fitted number not within 5% of its search limit (a fit pinned at a
limit means the shots ask for more than it tries: often a miscounted frame or a wrong setup). Shots all at one wheel
speed still fit, with a warning: shots at a second speed check that the model follows the launcher's speed.
"""

import dataclasses
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..launcher.inputs import LauncherConfigError, Setup, load_setup
from ..launcher.model import FedBall, Launcher
from ..launcher.shaft import VelocityController
from .file import MeasureError, MeasureFile, load
from .stats import why as summary_why
from .tests_direct import values
from .units import reading, unit_of

STIFFNESS_RANGE = (0.25, 4.0)  # times the setup's stiffness_n
FRICTION_RANGE = (0.2, 2.0)
SCAN = 9  # points scanned across the range before the golden-section search
GOOD_RMS = 0.05  # a good fit's RMS relative error is under this
EDGE = 0.05  # a fitted number within this fraction of a search limit is at the edge
SECOND_SPEED = 1.10  # shots at wheel speeds less than this ratio apart count as one speed
TOLERANCE = 1e-3  # the search stops when its bracket is this narrow (as a fraction of the range)
MAX_PASS_S = 1.0  # s: a ball still in the launcher after this long is not coming out
SQUEEZE_SENTENCE = "Measure the ball's squeeze (the squeeze test) to fit wheel friction too."

_NUMBERS = ("wheel_rps", "hood_deg", "fps")  # plain numbers, their unit in their name
_SETUP_NUMBERS = ("hood_deg", "fps")
_SETUP_TEXT = ("setup", "ball")
_GOLDEN = (math.sqrt(5.0) - 1.0) / 2.0


@dataclass(frozen=True, kw_only=True)
class LauncherFit:
    stiffness_n: float  # the ball's, N: fitted, or the squeeze test's
    friction: float  # the wheel's: fitted, or the setup's
    rms_error: float  # root mean square relative error of the shots' exit speeds: 0.03 is 3%
    per_shot: list[tuple[float, float]]  # (measured, model) exit speed per shot in file order, m/s; model NaN if no launch
    fitted: tuple[str, ...]  # ("friction",) with the squeeze measured, else ("stiffness_n",)
    warnings: list[str]  # plain sentences
    good: bool  # RMS under 5% and the fitted number not at the edge of its range
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
        if key not in _SETUP_TEXT and key not in _SETUP_NUMBERS and unit_of(key, "marks") is None:
            raise MeasureError(f"{mf.path}: unknown key '{key}' in [shot_setup]; it takes setup, ball, "
                               "marks_<unit>, fps, hood_deg")
    for i, row in enumerate(rows):
        for key in row:
            if key in _SETUP_TEXT:
                raise MeasureError(f"{mf.path}: {key} goes in [shot_setup], once for all shots, not in [[shot]] "
                                   f"trial {i + 1}")
            if key not in _NUMBERS and not _is_reading(key):
                raise MeasureError(f"{mf.path}: unknown reading '{key}' in [[shot]] trial {i + 1}; it takes "
                                   "wheel_rps, hood_deg, marks_<unit>, frames, fps")


def _text(mf: MeasureFile, table: dict, key: str) -> str | None:
    if key not in table:
        return None
    v = table[key]
    if not isinstance(v, str):
        raise MeasureError(f"{mf.path}: [shot_setup] {key} must be text, not {v!r}")
    return v


def _load_setup(mf: MeasureFile, table: dict) -> tuple[Setup, Path, str | None]:
    """The shots' setup as written (its own measurements not applied), its path, and its `[ball] measured` if any."""
    name = _text(mf, table, "setup")
    if name is None:
        raise MeasureError(f"{mf.path}: [shot_setup] is missing required key 'setup': the launcher setup file the "
                           "shots were taken on")
    path = mf.path.parent / name
    where = f"{mf.path}: [shot_setup] setup = \"{name}\""
    if not path.is_file():
        raise MeasureError(f"{where}: no such file (looked in {path.resolve()})")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as e:
        raise MeasureError(f"{where}: {e}") from None
    measured_ball = None
    for section in ("ball", "wheels"):
        t = raw.get(section)
        if isinstance(t, dict) and "measured" in t:
            t = dict(t)
            m = t.pop("measured")
            raw = {**raw, section: t}
            if section == "ball" and isinstance(m, str):
                measured_ball = m
    try:
        return load_setup(raw)[0], path, measured_ball
    except LauncherConfigError as e:
        raise MeasureError(f"{where}: {e}") from None


def _squeeze(path: Path, where: str, *, required: bool) -> tuple[float | None, str]:
    """A ball file's squeeze-test stiffness (N), or None with a warning (or an error, when `required`) saying why."""
    if not path.is_file():
        raise MeasureError(f"{where}: no such file (looked in {path.resolve()})")
    ball = load(path)
    problem = None
    if ball.kind != "ball":
        problem = f"is a {ball.kind} measurement; the fit needs a ball measurement with a squeeze test"
    elif "stiffness_n" not in (vals := values(ball)):
        problem = "has no [[squeeze]] trials, so it doesn't measure the ball's stiffness"
    elif not vals["stiffness_n"][0].good:
        problem = f"has a squeeze that isn't good enough to use ({summary_why(vals['stiffness_n'][0])})"
    if problem is None:
        return vals["stiffness_n"][0].mean, ""
    if required:
        raise MeasureError(f"{where} {problem}")
    return None, f"{where} {problem}, so the ball's stiffness is fit instead"


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

def _search(cost) -> float:
    """The x in 0..1 with the lowest cost: a scan, then a golden-section search between the best point's neighbors."""
    ticks = [i / (SCAN - 1) for i in range(SCAN)]
    seen = {x: cost(x) for x in ticks}
    best = min(ticks, key=seen.get)
    i = ticks.index(best)
    a, b = ticks[max(i - 1, 0)], ticks[min(i + 1, SCAN - 1)]
    c, d = b - _GOLDEN * (b - a), a + _GOLDEN * (b - a)
    fc, fd = cost(c), cost(d)
    seen |= {c: fc, d: fd}
    while b - a > TOLERANCE:
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - _GOLDEN * (b - a)
            fc = seen[c] = cost(c)
        else:
            a, c, fc = c, d, fd
            d = a + _GOLDEN * (b - a)
            fd = seen[d] = cost(d)
    return min(seen, key=seen.get)


def _rms(shots: list[_Shot], model: list[float]) -> float:
    errors = [1.0 if math.isnan(m) else (m - s.speed) / s.speed for s, m in zip(shots, model)]
    return math.sqrt(sum(e * e for e in errors) / len(errors))


def _at_edge(value: float, lo: float, hi: float) -> bool:
    return value <= lo * (1.0 + EDGE) or value >= hi * (1.0 - EDGE)


def fit(mf: MeasureFile) -> LauncherFit:
    """Fit the wheel's friction (the ball's squeeze measured) or else the ball's stiffness_n to a launcher file's
    shots (spec C8)."""
    if mf.kind != "launcher":
        raise MeasureError(f"{mf.path} is a {mf.kind} measurement: only a launcher file has shots to fit")
    if "shot" not in mf.trials:
        raise MeasureError(f"{mf.path}: a launcher file needs its shots, each written as a [[shot]] table")
    table = mf.settings.get("shot_setup", {})
    _check_keys(mf, table, mf.trials["shot"])
    setup, setup_path, setup_ball = _load_setup(mf, table)
    shots = _shots(mf, setup, table)
    warnings: list[str] = []

    squeezed = None
    ball_name = _text(mf, table, "ball")
    if ball_name is not None:
        squeezed, _ = _squeeze(mf.path.parent / ball_name, f"{mf.path}: [shot_setup] ball = \"{ball_name}\"",
                               required=True)
    elif setup_ball is not None:
        # Only a ball file there is a squeeze; a launcher file there (perhaps this one) is passed over, and a
        # missing one is the setup's own error, given when the setup is used.
        path = setup_path.parent / setup_ball
        if path.is_file() and load(path).kind == "ball":
            squeezed, note = _squeeze(path, f"{setup_path}: [ball] measured = \"{setup_ball}\"", required=False)
            if note:
                warnings.append(note)

    conditions = sorted({(s.wheel_rps, s.hood_deg) for s in shots})
    cache: dict[tuple[float, float], list[float]] = {}

    def model(k: float, f: float) -> list[float]:
        """Each shot's model exit speed at stiffness k and friction f."""
        if (k, f) not in cache:
            trial = dataclasses.replace(setup, ball=dataclasses.replace(setup.ball, stiffness_n=k),
                                        wheels=dataclasses.replace(setup.wheels, friction=f))
            by_condition = {c: exit_speed(trial, *c) for c in conditions}
            cache[(k, f)] = [by_condition[(s.wheel_rps, s.hood_deg)] for s in shots]
        return cache[(k, f)]

    if squeezed is not None:
        lo, hi = FRICTION_RANGE
        k = squeezed
        f = lo + (hi - lo) * _search(lambda x: _rms(shots, model(k, lo + (hi - lo) * x)))
        fitted, value, edge = ("friction",), f, f"the wheel friction, {f:.2f}, is at the edge of what it tries"
        span = f"{lo:g} to {hi:g}"
    else:
        lo, hi = (setup.ball.stiffness_n * r for r in STIFFNESS_RANGE)
        f = setup.wheels.friction
        k = lo * (hi / lo) ** _search(lambda x: _rms(shots, model(lo * (hi / lo) ** x, f)))
        fitted, value, edge = ("stiffness_n",), k, f"the ball's stiffness_n, {k:.0f} N, is at the edge of what it tries"
        span = f"{lo:.0f} to {hi:.0f} N"
        warnings.append(SQUEEZE_SENTENCE)
    speeds = model(k, f)
    rms = _rms(shots, speeds)

    why = []
    slow, fast = min(s.wheel_rps for s in shots), max(s.wheel_rps for s in shots)
    if fast < SECOND_SPEED * slow:
        at = f"{slow:g} rps" if slow == fast else f"{slow:g} to {fast:g} rps"
        warnings.append(f"the shots are all at one wheel speed ({at}): shots at two or more wheel speeds check the fit")
    if _at_edge(value, lo, hi):
        warnings.append(f"{edge}: check your shots (it tries {span})")
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
                       per_shot=[(s.speed, m) for s, m in zip(shots, speeds)], fitted=fitted,
                       warnings=warnings, good=not why, why="; ".join(why))
