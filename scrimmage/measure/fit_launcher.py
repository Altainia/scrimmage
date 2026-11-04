"""The launcher shot fit (spec C8): a ball's `stiffness_n` or a wheel's `friction`, fit so the launcher model's
horizontal exit speeds match filmed shots.

    [shot_setup]
    setup = "shooter.toml"        # the launcher setup the shots were taken on, relative to this file
    ball = "fuel.measure.toml"    # optional: the ball's squeeze test, so the shots fit the wheel's friction
    marks_in = 24                 # the tape marks' spacing, and the camera's frame rate, once for all shots
    fps = 960

    [[shot]]                      # one table per shot
    wheel_rps = 50                # the wheel speed it was shot at, held steady (wheel rotations per second)
    hood_deg = 45                 # optional: the hood's exit angle, else the setup's
    frames = 64.5                 # frames counted while the ball crosses from one mark to the next

The marks are vertical lines taped a measured horizontal distance apart, on a board just behind the ball's path, so
a shot gives the ball's horizontal speed: the mark spacing times fps over the frames counted. The model's is its
exit speed times the cosine of its launch angle (the ball's own, which may differ from the hood's setting); the
fit compares the two. `marks_<unit>`, `fps` and `hood_deg` may be given once in [shot_setup] or in a shot for that
shot alone.

Film one ball with the wheel already steady, and write the wheel speed you measured (a tachometer or the robot's
log), not the setpoint. Tape the marks on a board within a few inches behind the ball's path, the first one close to
the exit, and film square-on from at least 10 times as far in front of the path as the board is behind it (so the
ball, nearer the camera than the marks, isn't read early or late against them). Space the marks so the ball takes
20 or more frames between them: 24 in at 960 fps; at 240 fps a mark spacing that far gives about 16 frames, so
the marks go further apart or the frame rate up.

One number is fit, never both: in the model a stiffer ball and a grippier wheel speed a ball up in the same way at
every wheel speed, so shots alone can't tell them apart. With the ball's squeeze measured (a ball file with a good
squeeze test, named by `ball` here or by the setup's own `[ball] measured`), stiffness is fixed at it and the wheel
friction is fit. Otherwise the ball's stiffness is fit, with the wheel friction at the setup's: its `[wheels]
measured` friction file's (a sled test) when it names a good one, else the setup's number.

The setup is read as written, without its own measurements applied, except the two files above for the numbers that
aren't fit: a setup may name this very file, and applying it here would go round in a circle, so a launcher file
named there is passed over.

The model is the launcher's own: one ball fed at the setup's entry speed into the feed lane nearest the middle, with
the wheel spinning at the shot's speed when it enters and the setup's own velocity loop holding it as the ball
passes (scrimmage/launcher/single.py, the aiming table's pass, here with the setup's own gains). A
`scrimmage launcher run` burst feeds a ball once the wheel is within the feed tolerance of its target, so its first
ball can meet a wheel up to 5% slow; a filmed shot's wheel is held at its speed, so the fit starts the ball there.
The setup's [spread] is not used: the model is run once per wheel speed and hood angle, without random variation.

The search: stiffness from 0.25x to 4x the setup's (on a log scale), or friction from 0.2 to 2.0, scanned at 9
points, then a golden-section search between the best point's neighbors. It minimizes the root mean square of each
shot's relative error, (model - measured) / measured; a ball the model doesn't launch counts as an error of 1.

The band: every value whose RMS error is within the shots' resolution of the best one's fits the shots as well as
the numbers can tell. A shot's resolution is one frame in the frames counted, but never better than 2% (a speed read
off video is no better than that), and the shots' resolutions combine as a root mean square. The band is found from
the scan's points, then by bisection out to where the error rises past it.

A fit is good with an RMS error under 5%, the fitted number not within 5% of its search limit (a fit pinned at a
limit means the shots ask for more than it tries: often a miscounted frame or a wrong setup), and its band within
15% of it either way (a wider band means the shots hardly depend on that number at their wheel speeds). Shots all at
one wheel speed still fit, with a warning: shots at a second speed check that the model follows the launcher's speed.
"""

import dataclasses
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..launcher.inputs import LauncherConfigError, Setup, load_setup
from ..launcher.model import Launcher
from ..launcher.single import single_pass
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
RESOLUTION = 0.02  # a shot's speed is read to no better than this fraction, however many frames it spans
BAND = 0.15  # a good fit's band is within this fraction of its value either way
BISECTIONS = 8  # steps finding each end of the band between two scan points
GOOD_FRAMES = 20  # a shot should span this many frames or more between the marks (the forms say so)
SQUEEZE_SENTENCE = "Measure the ball's squeeze (the squeeze test) to fit wheel friction too."

SHOT_NUMBERS = ("wheel_rps", "hood_deg", "fps")  # plain numbers, their unit in their name
SHOT_READINGS = {"marks": "length", "frames": "frames"}  # readings with their unit in their key: name -> quantity
SETUP_NUMBERS = ("hood_deg", "fps")  # what [shot_setup] takes: these, its text, and marks_<unit>
SETUP_TEXT = ("setup", "ball")
_GOLDEN = (math.sqrt(5.0) - 1.0) / 2.0


@dataclass(frozen=True, kw_only=True)
class LauncherFit:
    stiffness_n: float  # the ball's, N: fitted, or the squeeze test's
    friction: float  # the wheel's: fitted, or the setup's
    rms_error: float  # root mean square relative error of the shots' horizontal speeds: 0.03 is 3%
    per_shot: list[tuple[float, float]]  # (measured, model) horizontal speed per shot, in file order, m/s; model NaN
    #   if the model ball isn't launched
    fitted: tuple[str, ...]  # ("friction",) with the squeeze measured, else ("stiffness_n",)
    warnings: list[str]  # plain sentences
    band: tuple[float, float]  # (low, high): the fitted number's values that fit the shots as well as they can tell
    band_text: str  # "stiffness 118 N, anywhere from 113 to 127 N fits your shots"
    good: bool  # RMS under 5%, the fitted number not at the edge of its range, its band within 15% of it
    why: str  # empty when good, else why not, in a few words


@dataclass(frozen=True)
class _Shot:
    wheel_rps: float
    hood_deg: float
    frames: float
    speed: float  # measured, m/s


def horizontal_speed(setup: Setup, wheel_rps: float, hood_deg: float | None = None) -> float:
    """The model's horizontal exit speed (m/s), speed x cos(launch angle), for one ball through `setup` with the wheel
    at `wheel_rps` (and the hood at `hood_deg`, else the setup's); NaN if the ball isn't launched."""
    if hood_deg is not None:
        setup = dataclasses.replace(setup, hood=dataclasses.replace(setup.hood, exit_angle_deg=hood_deg))
    shot = single_pass(setup, wheel_rps, min(setup.feed.lanes, key=abs))
    if shot is None or shot.status != "launched":
        return math.nan
    return shot.speed * math.cos(math.radians(shot.launch_deg))


# ------------------------------------------------------------------------------------------------ reading the file

def _number(table: dict, key: str, where: str) -> float | None:
    if key not in table:
        return None
    v = table[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise MeasureError(f"{where}: {key} must be a number, not {v!r}")
    return float(v)


def _is_reading(key: str) -> bool:
    return any(unit_of(key, name) is not None for name in SHOT_READINGS)


def _check_keys(mf: MeasureFile, setup: dict, rows: list[dict]) -> None:
    for key in setup:
        if key == "wheel_rps" or unit_of(key, "frames") is not None:
            raise MeasureError(f"{mf.path}: {key} goes in each [[shot]], not in [shot_setup]")
        if key not in SETUP_TEXT and key not in SETUP_NUMBERS and unit_of(key, "marks") is None:
            raise MeasureError(f"{mf.path}: unknown key '{key}' in [shot_setup]; it takes setup, ball, "
                               "marks_<unit>, fps, hood_deg")
    for i, row in enumerate(rows):
        for key in row:
            if key in SETUP_TEXT:
                raise MeasureError(f"{mf.path}: {key} goes in [shot_setup], once for all shots, not in [[shot]] "
                                   f"trial {i + 1}")
            if key not in SHOT_NUMBERS and not _is_reading(key):
                raise MeasureError(f"{mf.path}: unknown reading '{key}' in [[shot]] trial {i + 1}; it takes "
                                   "wheel_rps, hood_deg, marks_<unit>, frames, fps")


def _text(mf: MeasureFile, table: dict, key: str) -> str | None:
    if key not in table:
        return None
    v = table[key]
    if not isinstance(v, str):
        raise MeasureError(f"{mf.path}: [shot_setup] {key} must be text, not {v!r}")
    return v


def _load_setup(mf: MeasureFile, table: dict) -> tuple[Setup, Path, dict[str, str]]:
    """The shots' setup as written (its own measurements not applied), its path, and the measurement files its
    sections name (`{"ball": "fuel.measure.toml"}`)."""
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
    measured = {}
    for section in ("ball", "wheels"):
        t = raw.get(section)
        if isinstance(t, dict) and "measured" in t:
            t = dict(t)
            m = t.pop("measured")
            raw = {**raw, section: t}
            if isinstance(m, str):
                measured[section] = m
    try:
        return load_setup(raw)[0], path, measured
    except LauncherConfigError as e:
        raise MeasureError(f"{where}: {e}") from None


def _setup_file(setup_path: Path, section: str, name: str) -> tuple[MeasureFile | None, str]:
    """The measurement file a setup's `[section] measured` names, read once, and the words that place it. None when
    it is missing (the setup's own error, given when the setup is used) or a launcher file (perhaps this one)."""
    where = f"{setup_path}: [{section}] measured = \"{name}\""
    path = setup_path.parent / name
    if not path.is_file():
        return None, where
    try:
        found = load(path)
    except MeasureError as e:
        raise MeasureError(f"{where}: {e}") from None
    return (None if found.kind == "launcher" else found), where


def _squeeze(ball: MeasureFile, where: str, *, required: bool) -> tuple[float | None, str]:
    """A ball file's squeeze-test stiffness (N), or None with a warning (or an error, when `required`) saying why."""
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


def _sled(found: MeasureFile, where: str, setup_friction: float) -> tuple[float | None, str]:
    """A friction file's sled-test friction, or None with a warning saying why it isn't used."""
    vals = values(found)
    if "friction" not in vals:
        return None, f"{where} has no [[sled]] trials, so the setup's wheel friction {setup_friction:g} is used"
    s = vals["friction"][0]
    if not s.good:
        return None, (f"{where} has a sled pull that isn't good enough to use ({summary_why(s)}), so the setup's "
                      f"wheel friction {setup_friction:g} is used")
    return s.mean, ""


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
        shots.append(_Shot(wheel_rps=rps, hood_deg=hood, frames=frames, speed=marks * fps / frames))
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


def _band(cost, best: float, limit: float) -> tuple[float, float]:
    """The x in 0..1 either side of `best` whose cost stays at or under `limit`: the scan's points out to the first
    above it, then bisection between that point and the last under it (or the search limit if none rises above)."""
    ticks = [i / (SCAN - 1) for i in range(SCAN)]
    ends = []
    for side in (-1, 1):
        beyond = sorted((t for t in ticks if (t - best) * side > 0), key=lambda t: abs(t - best))
        inside, outside = best, None
        for t in beyond:
            if cost(t) > limit:
                outside = t
                break
            inside = t
        if outside is None:
            ends.append(0.0 if side < 0 else 1.0)
            continue
        for _ in range(BISECTIONS):
            mid = 0.5 * (inside + outside)
            inside, outside = (mid, outside) if cost(mid) <= limit else (inside, mid)
        ends.append(inside)
    return ends[0], ends[1]


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
    setup, setup_path, measured = _load_setup(mf, table)
    shots = _shots(mf, setup, table)
    warnings: list[str] = []

    squeezed = None
    ball_name = _text(mf, table, "ball")
    if ball_name is not None:
        where = f"{mf.path}: [shot_setup] ball = \"{ball_name}\""
        path = mf.path.parent / ball_name
        if not path.is_file():
            raise MeasureError(f"{where}: no such file (looked in {path.resolve()})")
        try:
            ball = load(path)
        except MeasureError as e:
            raise MeasureError(f"{where}: {e}") from None
        squeezed, _ = _squeeze(ball, where, required=True)
    elif "ball" in measured:
        found, where = _setup_file(setup_path, "ball", measured["ball"])
        if found is not None and found.kind == "ball":
            squeezed, note = _squeeze(found, where, required=False)
            if note:
                warnings.append(note)
    friction = setup.wheels.friction
    if squeezed is None and "wheels" in measured:
        found, where = _setup_file(setup_path, "wheels", measured["wheels"])
        if found is not None and found.kind == "friction":
            sled, note = _sled(found, where, friction)
            friction = friction if sled is None else sled
            if note:
                warnings.append(note)

    conditions = sorted({(s.wheel_rps, s.hood_deg) for s in shots})
    cache: dict[tuple[float, float], list[float]] = {}

    def model(k: float, f: float) -> list[float]:
        """Each shot's model horizontal exit speed at stiffness k and friction f."""
        if (k, f) not in cache:
            trial = dataclasses.replace(setup, ball=dataclasses.replace(setup.ball, stiffness_n=k),
                                        wheels=dataclasses.replace(setup.wheels, friction=f))
            by_condition = {c: horizontal_speed(trial, *c) for c in conditions}
            cache[(k, f)] = [by_condition[(s.wheel_rps, s.hood_deg)] for s in shots]
        return cache[(k, f)]

    if squeezed is not None:
        lo, hi = FRICTION_RANGE
        k = squeezed

        def value(x: float) -> float:
            return lo + (hi - lo) * x

        def speeds_at(x: float) -> list[float]:
            return model(k, value(x))
        name, unit, digits = "wheel friction", "", 2
    else:
        lo, hi = (setup.ball.stiffness_n * r for r in STIFFNESS_RANGE)
        f = friction

        def value(x: float) -> float:
            return lo * (hi / lo) ** x

        def speeds_at(x: float) -> list[float]:
            return model(value(x), f)
        name, unit, digits = "ball's stiffness", " N", 0
        warnings.append(SQUEEZE_SENTENCE)

    def cost(x: float) -> float:
        return _rms(shots, speeds_at(x))

    best = _search(cost)
    rms = cost(best)
    resolution = math.sqrt(sum(max(1.0 / s.frames, RESOLUTION) ** 2 for s in shots) / len(shots))
    x_lo, x_hi = _band(cost, best, rms + resolution)
    fitted_value, band = value(best), (value(x_lo), value(x_hi))
    if squeezed is not None:
        f, fitted = fitted_value, ("friction",)
    else:
        k, fitted = fitted_value, ("stiffness_n",)
    speeds = model(k, f)

    def show(v: float) -> str:
        return f"{v:.{digits}f}"

    plain = "stiffness" if squeezed is None else "wheel friction"
    band_text = (f"{plain} {show(fitted_value)}{unit}, anywhere from {show(band[0])} to {show(band[1])}{unit} fits "
                 "your shots")

    why = []
    slow, fast = min(s.wheel_rps for s in shots), max(s.wheel_rps for s in shots)
    if fast < SECOND_SPEED * slow:
        at = f"{slow:g} rps" if slow == fast else f"{slow:g} to {fast:g} rps"
        warnings.append(f"the shots are all at one wheel speed ({at}): shots at two or more wheel speeds check the fit")
    if _at_edge(fitted_value, lo, hi):
        warnings.append(f"the {name}, {show(fitted_value)}{unit}, is at the edge of what it tries: check your shots "
                        f"(it tries {show(lo)} to {show(hi)}{unit})")
        why.append("the fit is at the edge of what it tries")
    if band[0] < (1.0 - BAND) * fitted_value or band[1] > (1.0 + BAND) * fitted_value:
        warnings.append(f"{band_text}: your shots hardly depend on the {name} at these wheel speeds: shoot at a "
                        "higher wheel speed (where the ball's grip, not the wheel's speed, sets the exit speed)")
        why.append(f"the shots hardly depend on the {name}")
    if rms >= GOOD_RMS:
        warnings.append(f"the model's horizontal speeds miss your shots by {rms:.1%} (root mean square); a good fit is "
                        f"under {GOOD_RMS:.0%}: check your shots and the launcher setup")
        why.append(f"the model misses the shots by {rms:.0%}")
        if all(math.isnan(m) or m < s.speed for s, m in zip(shots, speeds)):
            warnings.append("the model's ball can't go this fast at these wheel speeds: check the wheel speed you "
                            "wrote and the mark spacing")
    missed = [i + 1 for i, m in enumerate(speeds) if math.isnan(m)]
    if missed:
        warnings.append(f"the model's ball doesn't leave the launcher for shot{'s' if len(missed) > 1 else ''} "
                        f"{', '.join(map(str, missed))}: check the setup's hood gap and wheel")
    return LauncherFit(stiffness_n=k, friction=f, rms_error=rms,
                       per_shot=[(s.speed, m) for s, m in zip(shots, speeds)], fitted=fitted,
                       warnings=warnings, band=band, band_text=band_text, good=not why, why="; ".join(why))
