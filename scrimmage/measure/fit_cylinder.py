"""The cylinder stroke fit (spec C9): a valve's `conductance`, fit so the air model's extend time matches timed
strokes.

    [stroke_setup]
    bore_in = 1.0625              # the piston's diameter, the rod's, and how far it travels
    rod_in = 0.3125
    stroke_in = 6
    load_mass_lb = 2              # optional: what the rod moves, besides itself (0 if left out)
    rod_mass_g = 100              # optional: the rod's own mass (the stock cylinder's 0.1 kg if left out)
    vertical = false              # true if the rod extends straight up, lifting its load
    working_psi = 60              # the regulator's working pressure (60 if left out)
    friction_n = 8                # the seals' friction (8 N if left out)
    fps = 240                     # optional: the camera's frame rate, if the strokes are counted in frames

    [[stroke]]                    # one table per stroke
    time_s = 0.21                 # from the valve opening to the rod at full stroke

The time is measured from the valve opening (its light, or the Pneumatic Hub channel's, coming on; or the program's
command in the robot's log) to the rod at full stroke: a slow-motion video's sound is too unreliable to time the
click by. A stroke may give `frames` (counted from the light coming on to the rod stopping) with `fps`, in
[stroke_setup] once for all strokes or in the stroke for that stroke alone, instead of `time_s`. The strokes'
times are averaged (at least 3 strokes, agreeing within 15%).

The model: the simulator's own air system (scrimmage/pneumatics/model.py), its tanks full and the regulator at
`working_psi`, with the valve switched from retract to extend at time zero and the rod starting at rest, fully in.
The rod is one body, its own mass (`rod_mass`, else ROD_MASS, the stock cylinder's) plus the load, pushed by the
air's force, held back by the seals' friction (a steady force against the motion, and holding the rod still while
the air's push is less than it), and, when vertical, by the weight of the rod and load. It is stepped at 1 ms,
between end stops at 0 and the stroke, until the rod reaches full stroke.

The search: the stroke time falls as the conductance rises (more air in, and out, each second), so the fit is a
bisection on a log scale over 0.1x to 10x DEFAULT_CONDUCTANCE. A measured time slower than the model's slowest, or
faster than its fastest, is pinned at that end.

The band: every conductance whose model time is within the timing's resolution of the measured time fits as well as
the timing can tell. A stroke's resolution is one frame when counted in frames (1 / fps), else 0.01 s; the strokes'
combine as a root mean square, and it is never less than 2% of the time.

A fit is good when the strokes' average is good (at least 3, agreeing within 15%), the model's time is within 5% of
it, the conductance is not within 5% of a search limit, and the band is within 15% of it either way. A wider band
has one of two causes, and the warning names it. The cause is found from how much of the time a 1% freer valve
takes off it, in % (its dependence on the valve: about 1 for a free rod, whose time the air's flow sets, near 0
under a heavy load). The best timing, read to 2%, gives a band of about 2% over that dependence. If that would be
within 15%, the timing is the cause: the stroke is too quick to time this closely (0.01 s is 14% of a 0.07 s
stroke), so film it and count frames. If not, no timing would do: the load sets the time, so time a lighter stroke.
A fit at the edge of its range gets no band warning: the edge is the problem.
"""

import math
from dataclasses import dataclass

from .. import i18n
from ..pneumatics.model import DEFAULT_CONDUCTANCE, AirConfig, AirSystem, CylinderSpec
from .file import MeasureError, MeasureFile
from .stats import summary
from .stats import why as summary_why
from .units import G, reading, unit_of

CONDUCTANCE_RANGE = (0.1, 10.0)  # times DEFAULT_CONDUCTANCE
ROD_MASS = 0.1  # kg: the stock cylinder's rod_mass (parts/stock/mechanisms.py)
DT = 0.001  # s: the rod's step
MAX_TIME = 20.0  # s: a model stroke not done by then never finishes
WORKING_PSI = 60.0
FRICTION_N = 8.0  # the stock cylinder's seal friction
GOOD_ERROR = 0.05  # a good fit's model time is within this fraction of the measured time
EDGE = 0.05  # a fitted conductance within this fraction of a search limit is at the edge
BAND = 0.15  # a good fit's band is within this fraction of its value either way
TIME_RESOLUTION = 0.01  # s: a stroke timed in seconds is read to no better than this
RESOLUTION_FLOOR = 0.02  # a stroke time is read to no better than this fraction of it, however it was timed
STEP = 0.01  # the step (a fraction of the log range) either side of the fit measuring how the time depends on it
BISECTIONS = 40  # steps of each bisection on the log scale: (log 100) / 2^40 is far finer than needed

_NAME = "cylinder"
# [stroke_setup]'s readings, each with its unit in its key (bore_in): name -> quantity
SETUP_READINGS = {"bore": "length", "rod": "length", "stroke": "length", "load_mass": "mass", "rod_mass": "mass",
                  "friction": "force"}
SETUP_ONLY = ("vertical", "working_psi")  # plain, once for all strokes
STROKE_READINGS = {"time": "time", "frames": "frames"}  # a stroke's: one of them, with fps for frames
STROKE_NUMBERS = ("fps",)  # plain, in [stroke_setup] once or in a stroke for that stroke alone
_LENGTHS = ("bore", "rod", "stroke")


@dataclass(frozen=True, kw_only=True)
class CylinderFit:
    conductance: float  # m^3/(s Pa): fitted
    model_time: float  # s: the model's stroke time at the fitted conductance
    measured_time: float  # s: the strokes' average
    error: float  # (model - measured) / measured: 0.03 is 3%
    warnings: list[str]  # plain sentences
    band: tuple[float, float]  # (low, high) conductance whose model time fits the strokes as well as they can tell
    band_text: str  # "valve conductance 1.6, anywhere from 1.5 to 1.7 fits your strokes (dm^3/(s bar))"
    good: bool  # the average good, error under 5%, not at the edge of its range, its band within 15% of it
    why: str  # empty when good, else why not, in a few words


@dataclass(frozen=True, kw_only=True)
class Stroke:
    """The one-body rod's setup: everything but the conductance."""
    bore: float  # m
    rod: float  # m
    stroke: float  # m
    mass: float  # kg: rod and load
    vertical: bool
    working_psi: float
    friction_n: float


def stroke_time(s: Stroke, conductance: float) -> float:
    """The model's time (s) from the valve opening to the rod at full stroke; inf if it isn't there by MAX_TIME."""
    spec = CylinderSpec(name=_NAME, bore=s.bore, rod=s.rod, stroke=s.stroke, friction=s.friction_n,
                        conductance=conductance)
    air = AirSystem(AirConfig(compressor="none", working_psi=s.working_psi), [spec])
    weight = s.mass * G if s.vertical else 0.0
    x = v = t = 0.0
    air.step(0.0, {_NAME: x}, True, True)  # fill the chambers at rest, retracted, as the robot sits before it
    air.set_valve(_NAME, "extend")
    while t < MAX_TIME:
        push = air.step(DT, {_NAME: x}, True, True)[_NAME] - weight
        if v == 0.0 and abs(push) <= s.friction_n:
            a = 0.0  # the seals hold it
        else:
            a = (push - math.copysign(s.friction_n, v if v != 0.0 else push)) / s.mass
        v_new = v + a * DT
        if v != 0.0 and v_new * v < 0.0:
            v_new = 0.0  # friction stops the rod; it doesn't push it back
        x_new = x + v_new * DT
        if x_new >= s.stroke:
            return t + DT * (s.stroke - x) / (x_new - x)  # the step's fraction that reaches the end
        if x_new <= 0.0:
            x_new, v_new = 0.0, 0.0
        x, v, t = x_new, v_new, t + DT
    return math.inf


# ------------------------------------------------------------------------------------------------ reading the file

def _plain(table: dict, key: str, where: str) -> float | None:
    if key not in table:
        return None
    v = table[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise MeasureError(i18n.t("{where}: {key} must be a number, not {value}", where=where, key=key, value=repr(v)))
    return float(v)


def _check_keys(mf: MeasureFile, setup: dict, rows: list[dict]) -> None:
    for key in setup:
        if unit_of(key, "time") is not None or unit_of(key, "frames") is not None:
            raise MeasureError(i18n.t("{path}: {key} goes in each [[stroke]], not in [stroke_setup]", path=mf.path,
                                      key=key))
        if key in (*SETUP_ONLY, *STROKE_NUMBERS) or any(unit_of(key, n) is not None for n in SETUP_READINGS):
            continue
        raise MeasureError(i18n.t("{path}: unknown key '{key}' in [stroke_setup]; it takes bore_<unit>, rod_<unit>, "
                                  "stroke_<unit>, load_mass_<unit>, rod_mass_<unit>, vertical, working_psi, "
                                  "friction_<unit>, fps", path=mf.path, key=key))
    for i, row in enumerate(rows):
        for key in row:
            if key in SETUP_ONLY or any(unit_of(key, n) is not None for n in SETUP_READINGS):
                raise MeasureError(i18n.t("{path}: {key} goes in [stroke_setup], once for all strokes, not in "
                                          "[[stroke]] trial {n}", path=mf.path, key=key, n=i + 1))
            if key not in STROKE_NUMBERS and not any(unit_of(key, n) is not None for n in STROKE_READINGS):
                raise MeasureError(i18n.t("{path}: unknown reading '{key}' in [[stroke]] trial {n}; it takes "
                                          "time_s, or frames and fps", path=mf.path, key=key, n=i + 1))


def cylinder_of(mf: MeasureFile) -> dict[str, float]:
    """The cylinder a stroke file's strokes were timed on, in a stock cylinder's units (m, N, kg): its bore, rod,
    stroke, friction and rod_mass, the defaults filled in as the fit fills them."""
    table = mf.settings.get("stroke_setup", {})
    s = _stroke(mf, table)
    rod_mass = reading(table, "mass", "rod_mass", where=i18n.t("{path}: [stroke_setup]", path=mf.path),
                       required=False)
    return {"bore": s.bore, "rod": s.rod, "stroke": s.stroke, "friction": s.friction_n,
            "rod_mass": ROD_MASS if rod_mass is None else rod_mass}


def _stroke(mf: MeasureFile, table: dict) -> Stroke:
    where = i18n.t("{path}: [stroke_setup]", path=mf.path)
    lengths = {}
    for name in _LENGTHS:
        v = reading(table, "length", name, where=where, required=False)
        if v is None:
            raise MeasureError(i18n.t("{where} has no {name}: write it as {name}_<unit> (bore_in = 1.0625)",
                                      where=where, name=name))
        if v <= 0:
            raise MeasureError(i18n.t("{where}: {name} must be above 0", where=where, name=name))
        lengths[name] = v
    if lengths["rod"] >= lengths["bore"]:
        raise MeasureError(i18n.t("{where}: the rod must be thinner than the bore (the piston's diameter)",
                                  where=where))
    load = reading(table, "mass", "load_mass", where=where, required=False) or 0.0
    if load < 0:
        raise MeasureError(i18n.t("{where}: load_mass can't be below 0", where=where))
    rod_mass = reading(table, "mass", "rod_mass", where=where, required=False)
    rod_mass = ROD_MASS if rod_mass is None else rod_mass
    if rod_mass <= 0:
        raise MeasureError(i18n.t("{where}: rod_mass must be above 0", where=where))
    friction = reading(table, "force", "friction", where=where, required=False)
    friction = FRICTION_N if friction is None else friction
    if friction < 0:
        raise MeasureError(i18n.t("{where}: friction can't be below 0", where=where))
    vertical = table.get("vertical", False)
    if not isinstance(vertical, bool):
        raise MeasureError(i18n.t("{where}: vertical must be true or false, not {value}", where=where,
                                  value=repr(vertical)))
    psi = _plain(table, "working_psi", where)
    psi = WORKING_PSI if psi is None else psi
    if psi <= 0:
        raise MeasureError(i18n.t("{where}: working_psi must be above 0, not {value}", where=where, value=f"{psi:g}"))
    try:
        AirConfig(compressor="none", working_psi=psi)
    except ValueError as e:
        raise MeasureError(i18n.t("{where}: {error}", where=where, error=e)) from None
    return Stroke(bore=lengths["bore"], rod=lengths["rod"], stroke=lengths["stroke"], mass=rod_mass + load,
                  vertical=vertical, working_psi=psi, friction_n=friction)


def trial_times(mf: MeasureFile, table: dict) -> tuple[list[float], list[float]]:
    """Each stroke's time (s) and its resolution (s)."""
    times, resolutions = [], []
    for i, row in enumerate(mf.trials["stroke"]):
        where = i18n.t("{path}: [[stroke]] trial {n}", path=mf.path, n=i + 1)
        seconds = reading(row, "time", where=where, required=False)
        frames = reading(row, "frames", where=where, required=False)
        if seconds is not None and frames is not None:
            raise MeasureError(i18n.t("{where} gives both time and frames: give one of them", where=where))
        if seconds is None and frames is None:
            raise MeasureError(i18n.t("{where} has no time: write time_s (from the valve opening to the rod at full "
                                      "stroke), or frames with fps", where=where))
        if seconds is not None:
            if seconds <= 0:
                raise MeasureError(i18n.t("{where}: time must be above 0, not {value}", where=where,
                                          value=f"{seconds:g}"))
            times.append(seconds)
            resolutions.append(TIME_RESOLUTION)
            continue
        fps = _plain(row, "fps", where)
        if fps is None:
            fps = _plain(table, "fps", i18n.t("{path}: [stroke_setup]", path=mf.path))
        if fps is None:
            raise MeasureError(i18n.t("{where} has no fps: give the camera's frame rate once in [stroke_setup] or in "
                                      "the stroke (fps = 240)", where=where))
        for name, v in (("frames", frames), ("fps", fps)):
            if v <= 0:
                raise MeasureError(i18n.t("{where}: {name} must be above 0, not {value}", where=where, name=name,
                                          value=f"{v:g}"))
        times.append(frames / fps)
        resolutions.append(1.0 / fps)
    return times, resolutions


# ------------------------------------------------------------------------------------------------------ the search

def _crossing(time_at, target: float) -> float:
    """The x in 0..1 (log conductance across its range) where the model's time falls to `target`; 0 or 1 if it
    is outside the range's times. The time falls as x rises."""
    if time_at(0.0) <= target:
        return 0.0
    if time_at(1.0) >= target:
        return 1.0
    a, b = 0.0, 1.0
    for _ in range(BISECTIONS):
        mid = 0.5 * (a + b)
        a, b = (mid, b) if time_at(mid) > target else (a, mid)
    return 0.5 * (a + b)


def _at_edge(value: float, lo: float, hi: float) -> bool:
    return value <= lo * (1.0 + EDGE) or value >= hi * (1.0 - EDGE)


def fit(mf: MeasureFile) -> CylinderFit:
    """Fit the valve's conductance to a cylinder file's timed strokes (spec C9)."""
    if mf.kind != "cylinder":
        raise MeasureError(i18n.t("{path} is a {kind} measurement: only a cylinder file has strokes to fit",
                                  path=mf.path, kind=mf.kind))
    if "stroke" not in mf.trials:
        raise MeasureError(i18n.t("{path}: a cylinder file needs its strokes, each written as a [[stroke]] table",
                                  path=mf.path))
    table = mf.settings.get("stroke_setup", {})
    _check_keys(mf, table, mf.trials["stroke"])
    setup = _stroke(mf, table)
    times, resolutions = trial_times(mf, table)
    average = summary(times)
    measured = average.mean
    warnings = list(average.warnings)

    lo, hi = (DEFAULT_CONDUCTANCE * r for r in CONDUCTANCE_RANGE)
    cache: dict[float, float] = {}

    def value(x: float) -> float:
        return lo * (hi / lo) ** x

    def time_at(x: float) -> float:
        if x not in cache:
            cache[x] = stroke_time(setup, value(x))
        return cache[x]

    if math.isinf(time_at(1.0)):
        # in psi whatever the Units setting: working_psi is the file's key, and this is also raised while a robot
        # config loads (measure/use.py), into report.json and MCP errors
        if setup.vertical:
            raise MeasureError(i18n.t("{path}: [stroke_setup]: the model's cylinder can't lift its load at {psi} psi: "
                                      "check the bore, load_mass, friction and working_psi", path=mf.path,
                                      psi=f"{setup.working_psi:g}"))
        raise MeasureError(i18n.t("{path}: [stroke_setup]: the model's cylinder can't push past its seals' friction "
                                  "at {psi} psi: check the bore, load_mass, friction and working_psi", path=mf.path,
                                  psi=f"{setup.working_psi:g}"))

    best = _crossing(time_at, measured)
    conductance, model_time = value(best), time_at(best)
    error = (model_time - measured) / measured
    resolution = max(math.sqrt(sum(r * r for r in resolutions) / len(resolutions)), RESOLUTION_FLOOR * measured)
    band = (value(_crossing(time_at, measured + resolution)), value(_crossing(time_at, measured - resolution)))

    def show(c: float) -> str:  # dm^3/(s bar), the unit valve makers print
        return f"{c * 1e8:.2f}"

    band_text = i18n.t("valve conductance {value}, anywhere from {low} to {high} fits your strokes (dm^3/(s bar))",
                       value=show(conductance), low=show(band[0]), high=show(band[1]))

    why = []
    if not average.good:
        why.append(summary_why(average))
    at_edge = _at_edge(conductance, lo, hi)
    if at_edge:
        warnings.append(i18n.t("the valve conductance, {value} dm^3/(s bar), is at the edge of what it tries: check "
                               "your strokes and the cylinder's size (it tries {low} to {high})",
                               value=show(conductance), low=show(lo), high=show(hi)))
        why.append(i18n.t("the fit is at the edge of what it tries"))
        if best >= 0.5:
            warnings.append(i18n.t("the model's cylinder can't stroke this fast: its fastest is {fastest} s, and your "
                                   "strokes average {measured} s: check that the time runs from the valve opening to "
                                   "the rod at full stroke", fastest=f"{time_at(1.0):.3f}", measured=f"{measured:.3f}"))
        else:
            warnings.append(i18n.t("the model's cylinder strokes faster than this even with the slowest valve it "
                                   "tries: its slowest is {slowest} s, and your strokes average {measured} s: check the "
                                   "load_mass and friction, and that the time stops when the rod reaches full stroke",
                                   slowest=f"{time_at(0.0):.3f}", measured=f"{measured:.3f}"))
    if not at_edge and (band[0] < (1.0 - BAND) * conductance or band[1] > (1.0 + BAND) * conductance):
        # How much of the time a 1% freer valve takes off it, in %: about 1 for a free rod (the air's flow sets
        # the time), near 0 under a heavy load. The best timing (the 2% floor) gives a band of about the floor over
        # this either way; if even that is too wide, the load is the cause, else the timing is.
        a, b = max(best - STEP, 0.0), min(best + STEP, 1.0)
        dependence = math.log(time_at(a) / time_at(b)) / math.log(value(b) / value(a))
        if dependence <= 0.0 or RESOLUTION_FLOOR / dependence > BAND:
            if setup.vertical:
                warnings.append(i18n.t("{band}: your stroke time hardly depends on the valve: the load (lifted "
                                       "straight up) sets it: time a lighter stroke", band=band_text))
            else:
                warnings.append(i18n.t("{band}: your stroke time hardly depends on the valve: the load sets it: time "
                                       "a lighter stroke", band=band_text))
            why.append(i18n.t("the stroke time hardly depends on the valve"))
        else:
            warnings.append(i18n.t("{band}: a {measured} s stroke is too quick to time this closely: film it and count "
                                   "frames at the phone's highest frame rate", band=band_text,
                                   measured=f"{measured:.2f}"))
            why.append(i18n.t("the strokes aren't timed closely enough"))
    if abs(error) >= GOOD_ERROR:
        warnings.append(i18n.t("the model's stroke time, {model} s, misses your strokes' {measured} s by {error}; a "
                               "good fit is within {good}", model=f"{model_time:.3f}", measured=f"{measured:.3f}",
                               error=f"{abs(error):.1%}", good=f"{GOOD_ERROR:.0%}"))
        why.append(i18n.t("the model misses the strokes by {error}", error=f"{abs(error):.0%}"))
    return CylinderFit(conductance=conductance, model_time=model_time, measured_time=measured, error=error,
                       warnings=warnings, band=band, band_text=band_text, good=not why, why="; ".join(why))
