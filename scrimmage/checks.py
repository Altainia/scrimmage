"""Signals a scenario can check, and the checks themselves. Checks read the session duck-typed (world,
bridge.hal, robot, nt_value), so they can be unit-tested without a robot program.

Units: a check whose message has a length (pose, odometry_error) words it twice, through `said()`: `message` as it
always was (meters), for report.json and the MCP result, which don't change with the Units setting, and `shown()` in
the setting, for report.md, the terminal and the Scenario tab. A signal's own value is shown as the signal gives it."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, fields
from dataclasses import field as dc_field
from typing import Any, Callable

from . import units

# Python tracebacks and Java stack traces in the robot console
CRASH_PATTERN = re.compile(r"^Traceback \(most recent call last\)|^Exception in thread|^\s+at [\w.$<>]+\(.*\)\s*$",
                           re.MULTILINE)
SIGNALS_HELP = ("battery, speed, motor.<name>.current|command, joint.<name>.position|velocity, "
                "robot.ground_gap|wheels_down|air_time|pitch|roll|tilt|z|stranded|tipped, "
                "climb.hanging|level|points|swing|hook_load|extension|height, climb.hook.<hook>.load, "
                "hal:<type>/<device>/<key>, nt:<topic>, and on a test range score, score.<goal>, fired, held, on_field, "
                "element.<platform>.angle, element.<hinged or sliding element>, and with a [partner] partner.height|"
                "tilt|moved")
RANGE_SIGNALS = ("score", "fired", "held", "on_field")   # and score.<goal>: read from the range's counts()


class ScenarioError(ValueError):
    pass


def _plain(v):
    """A signal value as plain Python: numpy scalars (MuJoCo state, the battery model) become int / float, numpy
    arrays (NT double[] and the like) and sequences holding numpy scalars become lists."""
    if v is None or type(v) in (bool, str, int, float):   # not isinstance: np.float64 subclasses float
        return v
    tolist = getattr(v, "tolist", None)
    if callable(tolist):          # numpy arrays and scalars: tolist() gives plain Python all the way down
        try:
            return tolist()
        except (TypeError, ValueError):
            return v
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def _show(v) -> str:
    """A signal value or bound for a message: the shortest text that reads back as the same number (so a failing
    4.9999996 never shows as its bound 5), without a trailing ".0" (lists element-wise); text and booleans as
    their repr."""
    if isinstance(v, int) and not isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        r = repr(float(v))   # float(): np.float64's own repr is "np.float64(...)"
        return r[:-2] if r.endswith(".0") else r
    if isinstance(v, list):
        return "[" + ", ".join(_show(x) for x in v) + "]"
    return repr(v)


@dataclass
class Signal:
    name: str
    read: Callable[[Any], Any]
    uses_nt: bool = False

    def __post_init__(self) -> None:
        raw = self.read
        self.read = lambda s: _plain(raw(s))


def _range_signal(name: str, world) -> Signal:
    rng = getattr(world, "test_range", None)
    if rng is None:
        raise ScenarioError(f"{name} needs a robot on a test range: add [range] to the config (see docs/test-range.md)")
    if name.startswith("score."):
        goal = name[len("score."):]
        goals = [g.name for g in rng.range.goals]
        if goal not in goals:
            raise ScenarioError(f"unknown signal {name!r}: range {rng.range.name!r} has the goals "
                                f"{', '.join(goals) or 'none'}")
        return Signal(name, lambda s: s.world.test_range.counts()["goals"][goal])
    return Signal(name, lambda s: s.world.test_range.counts()[name])


def _robot_signal(name: str, world) -> Signal:
    """robot.<field>: the terrain signals (scrimmage/terrain/monitor.py), peak-held between ticks."""
    from .terrain.monitor import ROBOT_SIGNALS, WHEEL_SIGNALS
    field = name[len("robot."):]
    if field not in ROBOT_SIGNALS:
        raise ScenarioError(f"unknown signal {name!r}: the robot signals are "
                            f"{', '.join('robot.' + f for f in ROBOT_SIGNALS)}")
    make = getattr(world, "terrain_monitor", None)
    if make is None:
        raise ScenarioError(f"{name} needs a simulated robot")
    if field in WHEEL_SIGNALS and not (world.parts is not None and world.parts.tread_geoms):
        raise ScenarioError(f"{name} reads the drive wheels, but this robot has no tread wheels (a hand-written "
                            "[model] mjcf robot, or parts without tread = true geoms): build it from [[part]] with a "
                            "stock drivetrain, or use robot.tilt, pitch, roll, z, air_time or tipped")
    make()
    return Signal(name, lambda s: s.world.terrain.sample()[name])


def _element_signal(name: str, world) -> Signal:
    """element.<platform>.angle, a platform's tilt (deg); element.<name>, a hinged element's angle (deg) or a sliding
    one's travel (in), 0 at rest."""
    from .testrange.elements import Platform, moving_units, moving_value
    rng = getattr(world, "test_range", None)
    elements = list(rng.range.elements) if rng is not None else []
    units = moving_units(elements)
    rest = name[len("element."):]
    if rest in units:
        qadr, unit = world.parts.layout.moving[rest], units[rest]
        return Signal(name, lambda s: moving_value(float(s.world.data.qpos[qadr]), unit))
    elem, _, field = rest.rpartition(".")
    platforms = [e.name for e in elements if isinstance(e, Platform)]
    known = f"platforms: {', '.join(platforms) or 'none'}; moving elements: {', '.join(units) or 'none'}"
    if field != "angle" or not elem:
        raise ScenarioError(f"unknown signal {name!r}: use element.<platform>.angle or element.<moving element>; "
                            f"{known}")
    if elem not in platforms:
        kinds = {e.name: type(e).__name__.lower() for e in elements}
        why = (f"{elem!r} is a {kinds[elem]} element: read it as element.{elem}" if elem in units else
               f"element {elem!r} is a {kinds[elem]}, not a platform: only a platform tilts" if elem in kinds else
               f"no platform {elem!r}")
        raise ScenarioError(f"signal {name!r}: {why}; {known}")
    world.terrain_monitor()
    return Signal(name, lambda s: s.world.terrain.platform_angle(elem))


def _partner_signal(name: str, world) -> Signal:
    """partner.height (in), partner.tilt (deg) and partner.moved (in): the [partner] robot's (parts/partner.py)."""
    from .parts.partner import SIGNALS
    if name not in SIGNALS:
        raise ScenarioError(f"unknown signal {name!r}: the partner signals are {', '.join(SIGNALS)}")
    if getattr(world, "partner", None) is None:
        raise ScenarioError(f"{name} needs a partner robot: add [partner] to the config (see README.md)")
    return Signal(name, lambda s: s.world.partner.read(name))


def _climb_signal(name: str, world) -> Signal:
    """climb.<field> and climb.hook.<hook>.load: the climb signals (scrimmage/terrain/climb.py), peak-held between
    ticks."""
    from .terrain.climb import CLIMB_SIGNALS, EXTENT_SIGNALS, robot_hooks
    make = getattr(world, "climb_monitor", None)
    field = name[len("climb."):]
    if field.startswith("hook.") and field.endswith(".load"):
        if make is None:
            raise ScenarioError(f"{name} needs a simulated robot")
        hook = field[len("hook."):-len(".load")]
        hooks = list(robot_hooks(world.model, world.robot_body))
        if hook not in hooks:
            raise ScenarioError(f"unknown signal {name!r}: the robot's hooks are {', '.join(hooks) or 'none'} "
                                "(a stock climber's, <part>.hook<i>), each read as climb.hook.<hook>.load")
    elif field not in CLIMB_SIGNALS:
        raise ScenarioError(f"unknown signal {name!r}: the climb signals are "
                            f"{', '.join('climb.' + f for f in CLIMB_SIGNALS)} and climb.hook.<hook>.load")
    elif make is None:
        raise ScenarioError(f"{name} needs a simulated robot")
    mon = make()
    if field in EXTENT_SIGNALS:
        mon.track_extent()
    return Signal(name, lambda s: s.world.climb.sample()[name])


def resolve_signal(name: str, world) -> Signal:
    if name in RANGE_SIGNALS or name.startswith("score."):
        return _range_signal(name, world)
    if name.startswith("robot."):
        return _robot_signal(name, world)
    if name.startswith("element."):
        return _element_signal(name, world)
    if name.startswith("climb."):
        return _climb_signal(name, world)
    if name.startswith("partner."):
        return _partner_signal(name, world)
    if name == "battery":
        return Signal(name, lambda s: s.world.vbatt)
    if name == "speed":
        return Signal(name, lambda s: s.world.robot_speed())
    if name.startswith("motor."):
        # Part-built robots have dotted names ("dt.left"), so the field is the last segment.
        motor, _, field = name[len("motor."):].rpartition(".")
        motors = [m.name for m in world.motors]
        if field not in ("current", "command") or motor not in motors:
            raise ScenarioError(f"unknown signal {name!r}: use motor.<name>.current or .command; "
                                f"motors: {', '.join(motors) or 'none'}")
        i = motors.index(motor)
        return Signal(name, lambda s: getattr(s.world.motors[i], field))
    if name.startswith("joint."):
        joint, _, field = name[len("joint."):].rpartition(".")
        joints = sorted(world.joint_states())
        if field not in ("position", "velocity") or joint not in joints:
            raise ScenarioError(f"unknown signal {name!r}: use joint.<name>.position or .velocity; "
                                f"joints: {', '.join(joints) or 'none'}")
        j, k = joint, 0 if field == "position" else 1
        return Signal(name, lambda s: s.world.joint_states()[j][k])
    if name.startswith("hal:"):
        typ, _, rest = name[4:].partition("/")
        dev, _, key = rest.rpartition("/")
        if not (typ and key):
            raise ScenarioError(f"unknown signal {name!r}: use hal:<type>/<device>/<key>, e.g. hal:PWM/0/<speed")
        if key[0] not in "<>":
            raise ScenarioError(f"signal {name!r}: the key needs its direction prefix, e.g. hal:PWM/0/<speed "
                                f"(< for robot output, > for input)")
        return Signal(name, lambda s: s.bridge.hal.get(typ, dev, key))
    if name.startswith("nt:") and len(name) > 3:
        topic = name[3:]
        if not topic.startswith("/"):
            raise ScenarioError(f"signal {name!r}: NetworkTables topics start with /, e.g. nt:/SmartDashboard/x")
        return Signal(name, lambda s: s.nt_value(topic), uses_nt=True)
    raise ScenarioError(f"unknown signal {name!r}; signals are {SIGNALS_HELP}")


@dataclass
class CheckResult:
    check: str
    passed: bool
    message: str
    measured: Any = None
    expected: Any = None
    first_failure_t: float | None = None
    words: Callable[[], str] | None = dc_field(default=None, repr=False, compare=False)   # see said()

    def shown(self) -> str:
        """The message in the Units setting (report.md, the terminal, the page); `message` is the machine one."""
        return self.words() if self.words is not None else self.message

    def to_dict(self, shown: bool = False) -> dict:
        d = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "words"}
        if shown:
            d["message"] = self.shown()
        return d


def said(check: str, passed: bool, message: str, words: Callable[[], str], *rest) -> CheckResult:
    """A CheckResult whose message has units: `message` the machine text, as it was before the Units setting (report.json
    and MCP results don't change with it), and `words` to build it in the Units setting when it is shown."""
    return CheckResult(check, passed, message, *rest, words=words)


def _far(v: float) -> str:
    """A shown pose or odometry distance: a place finer than the table's, so a fail never reads as its bound."""
    return units.fmt(v, "field", fewer=-1)


class Check:
    label = ""
    uses_nt = False
    ticks = False  # wants tick() calls during the step
    nt_period: float | None = None  # NT checks: the runner samples them every nt_period s, at hold points

    def begin(self, session) -> None:
        pass

    def tick(self, session, t: float) -> None:
        pass

    def end(self, session) -> CheckResult:
        raise NotImplementedError


def _num(spec: dict, key: str, where: str) -> float | None:
    v = spec.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ScenarioError(f"{where}: {key} must be a number, not {v!r}")
    if not math.isfinite(v):
        raise ScenarioError(f"{where}: {key} must be a finite number, not {v!r}")
    return float(v)


def _tol(spec: dict, key: str, where: str) -> float | None:
    v = _num(spec, key, where)
    if v is not None and v < 0:
        raise ScenarioError(f"{where}: {key} can't be negative")
    return v


def _only(spec: dict, allowed: set[str], where: str) -> None:
    extra = set(spec) - allowed
    if extra:
        raise ScenarioError(f"{where}: unknown key {sorted(extra)[0]!r}; allowed: {', '.join(sorted(allowed))}")


class PoseCheck(Check):
    def __init__(self, spec: dict, where: str):
        _only(spec, {"x", "y", "heading", "tol_xy", "tol_heading"}, where)
        self.x, self.y, self.h = _num(spec, "x", where), _num(spec, "y", where), _num(spec, "heading", where)
        if self.x is None and self.y is None and self.h is None:
            raise ScenarioError(f"{where}: pose needs at least one of x, y, heading")
        tol_xy, tol_h = _tol(spec, "tol_xy", where), _tol(spec, "tol_heading", where)
        self.tol_xy = 0.1 if tol_xy is None else tol_xy
        self.tol_h = 5.0 if tol_h is None else tol_h
        self.label = "pose"

    def end(self, session) -> CheckResult:
        x, y, yaw = session.world.robot_pose()
        hdg = math.degrees(yaw)
        dx = (x - self.x) if self.x is not None else 0.0
        dy = (y - self.y) if self.y is not None else 0.0
        dist = math.hypot(dx, dy)
        dh = abs((hdg - self.h + 180) % 360 - 180) if self.h is not None else 0.0
        ok = dist <= self.tol_xy and dh <= self.tol_h
        measured = {"x": round(x, 3), "y": round(y, 3), "heading": round(hdg, 1)}
        expected = {k: v for k, v in (("x", self.x), ("y", self.y), ("heading", self.h)) if v is not None}
        where = f"(x={x:.2f}, y={y:.2f}, heading={hdg:.1f}°)"
        msg = (f"pose within {dist:.2f} m and {dh:.1f}° of target {where}" if ok else
               f"pose: {dist:.2f} m from target, heading off by {dh:.1f}° {where}; "
               f"allowed {self.tol_xy:g} m, {self.tol_h:g}°")

        def words() -> str:
            where = f"(x={_far(x)}, y={_far(y)}, heading={hdg:.1f}°)"
            return (f"pose within {_far(dist)} and {dh:.1f}° of target {where}" if ok else
                    f"pose: {_far(dist)} from target, heading off by {dh:.1f}° {where}; "
                    f"allowed {_far(self.tol_xy)}, {self.tol_h:g}°")
        return said(self.label, ok, msg, words, measured, expected)


class SignalCheck(Check):
    """at_end or always: a signal compared with min / max / equals / approx+tol."""

    def __init__(self, kind: str, spec: dict, world, where: str):
        _only(spec, {"signal", "min", "max", "equals", "approx", "tol"}, where)
        if "signal" not in spec:
            raise ScenarioError(f"{where}: {kind} needs signal = \"...\"")
        self.kind = kind
        self.signal = resolve_signal(str(spec["signal"]), world)
        self.min, self.max = _num(spec, "min", where), _num(spec, "max", where)
        self.equals = spec.get("equals")
        self.approx, self.tol = _num(spec, "approx", where), _tol(spec, "tol", where)
        if self.equals is not None:
            ok_type = isinstance(self.equals, (bool, str)) or (
                isinstance(self.equals, (int, float)) and math.isfinite(self.equals))
            if not ok_type:
                raise ScenarioError(f"{where}: equals must be a bool, a finite number or a string, "
                                    f"not {self.equals!r}")
            if self.min is not None or self.max is not None or self.approx is not None:
                raise ScenarioError(f"{where}: equals can't be combined with min, max or approx")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ScenarioError(f"{where}: min ({self.min:g}) is greater than max ({self.max:g})")
        if self.approx is not None and self.tol is None:
            raise ScenarioError(f"{where}: approx needs tol")
        if self.tol is not None and self.approx is None:
            raise ScenarioError(f"{where}: tol needs approx")
        if kind == "always" and self.approx is not None:
            raise ScenarioError(f"{where}: always checks take min, max or equals")
        if self.min is None and self.max is None and self.equals is None and self.approx is None:
            raise ScenarioError(f"{where}: {kind} needs min, max, equals, or approx with tol")
        self.uses_nt = self.signal.uses_nt
        self.ticks = kind == "always"
        self.label = f"{kind} {self.signal.name} {self._expected()}"
        self._reset()

    def _reset(self) -> None:
        self.first_fail: float | None = None
        self.first_num_fail: float | None = None
        self.worst_t: float | None = None
        self.n_missing = 0
        self.first_missing: float | None = None
        self.worst: Any = None
        self.worst_excess: float | None = None
        self.extreme: Any = None
        self.extreme_margin: float | None = None
        self.last: Any = None
        self.seen = False

    def _expected(self) -> str:
        parts = []
        if self.min is not None:
            parts.append(f">= {_show(self.min)}")
        if self.max is not None:
            parts.append(f"<= {_show(self.max)}")
        if self.equals is not None:
            parts.append(f"== {self.equals!r}")
        if self.approx is not None:
            parts.append(f"≈ {_show(self.approx)} ± {_show(self.tol)}")
        return " and ".join(parts)

    @staticmethod
    def _as_float(v) -> float | None:
        if isinstance(v, bool) or v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None

    def _ok(self, v) -> bool:
        if v is None:
            return False
        if self.equals is not None:
            if isinstance(self.equals, bool) or isinstance(v, bool):
                return type(v) is type(self.equals) and v == self.equals
            return v == self.equals
        f = self._as_float(v)
        if f is None:
            return False
        if self.min is not None and f < self.min:
            return False
        if self.max is not None and f > self.max:
            return False
        if self.approx is not None and abs(f - self.approx) > self.tol:
            return False
        return True

    def _excess(self, v) -> float:
        """How far out of range v is (inf when it can't be compared)."""
        f = self._as_float(v)
        if f is None or self.equals is not None:
            return math.inf
        ex = [e for e in ((self.min - f) if self.min is not None else None,
                          (f - self.max) if self.max is not None else None) if e is not None]
        return max(ex) if ex else math.inf

    def _margin(self, f: float) -> float:
        m = [e for e in ((f - self.min) if self.min is not None else None,
                         (self.max - f) if self.max is not None else None) if e is not None]
        return min(m)

    def begin(self, session) -> None:
        self._reset()

    def tick(self, session, t: float) -> None:
        v = self.signal.read(session)
        self.seen = True
        self.last = v
        if v is None:
            self.n_missing += 1
            if self.first_missing is None:
                self.first_missing = round(t, 4)
            if self.first_fail is None:
                self.first_fail = self.first_missing
        elif not self._ok(v):
            if self.first_fail is None:
                self.first_fail = round(t, 4)
            if self.first_num_fail is None:
                self.first_num_fail = round(t, 4)
            ex = self._excess(v)
            if self.worst_excess is None or ex > self.worst_excess:
                self.worst, self.worst_excess, self.worst_t = v, ex, round(t, 4)
        elif self.equals is None:
            f = float(v)
            m = self._margin(f)
            if self.extreme_margin is None or m < self.extreme_margin:
                self.extreme, self.extreme_margin = f, m

    def _missing(self, session, at_end: bool) -> str:
        name = self.signal.name
        if name.startswith("hal:"):
            seen = ", ".join(f"{t}/{d}" for t, d in sorted(session.bridge.hal.devices)) or "none"
            return f"no HAL value {name}; devices seen: {seen}"
        return f"{name} has no value" + (" at the end of the step" if at_end else "")

    def _sampled(self) -> str:
        if self.uses_nt and self.nt_period is not None:
            return f" (sampled at {self.nt_period:g} s hold points)"
        return ""

    def end(self, session) -> CheckResult:
        if self.kind == "always":
            if not self.seen:
                self.tick(session, getattr(session, "t", 0.0))
            ok = self.first_fail is None
            if ok:
                measured = self.last if self.equals is not None else self.extreme
                return CheckResult(self.label, True, f"{self.signal.name} stayed {self._expected()}{self._sampled()}",
                                   measured, self._expected())
            if self.worst_excess is None:
                msg = f"{self._missing(session, False)} (first at t={self.first_missing}); expected {self._expected()}"
            else:
                msg = (f"{self.signal.name} was {_show(self.worst)} at t={self.worst_t} (first out of range at "
                       f"t={self.first_num_fail}); expected {self._expected()}")
                if self.n_missing:
                    msg += f"; no value at {self.n_missing} ticks (first at t={self.first_missing})"
            msg += self._sampled()
            return CheckResult(self.label, False, msg, self.worst, self._expected(), self.first_fail)
        v = self.signal.read(session)
        if v is None:
            return CheckResult(self.label, False, self._missing(session, True), None, self._expected())
        ok = self._ok(v)
        return CheckResult(self.label, ok, f"{self.signal.name} = {_show(v)} at the end; expected {self._expected()}",
                           v, self._expected())


class OdometryErrorCheck(Check):
    def __init__(self, spec: dict, where: str):
        _only(spec, {"max", "when"}, where)
        self.max = _num(spec, "max", where)
        if self.max is None:
            raise ScenarioError(f"{where}: odometry_error needs max")
        if self.max <= 0:
            raise ScenarioError(f"{where}: odometry_error max must be greater than 0")
        self.when = spec.get("when", "end")
        if self.when not in ("end", "always"):
            raise ScenarioError(f"{where}: odometry_error when must be \"end\" or \"always\"")
        self.uses_nt = True
        self.ticks = self.when == "always"
        self.label = f"odometry_error {self.when} <= {self.max:g} m"
        self.worst, self.first_fail, self.missing = 0.0, None, False

    def _err(self, session) -> float | None:
        link = session.bridge.nt_link
        odo = link.odometry() if link is not None else None
        if odo is None:
            return None
        x, y, _ = session.world.robot_pose()
        return math.hypot(x - odo[0], y - odo[1])

    def begin(self, session) -> None:
        self.worst, self.first_fail, self.missing = 0.0, None, False

    def tick(self, session, t: float) -> None:
        e = self._err(session)
        if e is None:
            self.missing = True
            return
        self.worst = max(self.worst, e)
        if e > self.max and self.first_fail is None:
            self.first_fail = round(t, 4)

    def end(self, session) -> CheckResult:
        if self.when == "end":
            e = self._err(session)
            if e is None:
                return CheckResult(self.label, False, "no odometry on NetworkTables ([telemetry] odometry)", None, self.max)
            return said(self.label, e <= self.max,
                        f"odometry {e:.3f} m from ground truth at the end; allowed {self.max:g} m",
                        lambda: f"odometry {_far(e)} from ground truth at the end; allowed {_far(self.max)}",
                        round(e, 4), self.max)
        if self.missing and self.worst == 0.0:
            return CheckResult(self.label, False, "no odometry on NetworkTables ([telemetry] odometry)", None, self.max)
        ok = self.first_fail is None
        worst = self.worst
        return said(self.label, ok, f"odometry error peaked at {worst:.3f} m; allowed {self.max:g} m",
                    lambda: f"odometry error peaked at {_far(worst)}; allowed {_far(self.max)}",
                    round(worst, 4), self.max, self.first_fail)


class ContactCheck(Check):
    def __init__(self, target: Any, must: bool, world, where: str):
        if not isinstance(target, str) or target not in world.contact_names():
            raise ScenarioError(f"{where}: no geom or body {target!r} to touch; names: {', '.join(world.contact_names())}")
        self.target, self.must = target, must
        self.ticks = True
        self.label = f"{'must_touch' if must else 'never_touch'} {target}"
        self.first_t: float | None = None

    def begin(self, session) -> None:
        self.first_t = None

    def tick(self, session, t: float) -> None:
        if self.first_t is None and self.target in session.world.robot_contacts():
            self.first_t = round(t, 4)

    def end(self, session) -> CheckResult:
        touched = self.first_t is not None
        ok = touched if self.must else not touched
        msg = (f"touched {self.target} at t={self.first_t}" if touched else f"never touched {self.target}")
        return CheckResult(self.label, ok, msg, touched, self.must, None if self.must else self.first_t)


class ConsoleCheck(Check):
    def __init__(self, spec: dict, where: str):
        _only(spec, {"contains", "not_contains"}, where)
        try:
            self.contains = re.compile(spec["contains"]) if "contains" in spec else None
            self.not_contains = re.compile(spec["not_contains"]) if "not_contains" in spec else None
        except re.error as e:
            raise ScenarioError(f"{where}: bad regex: {e}") from None
        if self.contains is None and self.not_contains is None:
            raise ScenarioError(f"{where}: console needs contains or not_contains")
        self.label = "console " + " ".join(f"{k} /{v}/" for k, v in spec.items())
        self.ticks = True
        self._reset(0)

    def _reset(self, first: int) -> None:
        self.first = first
        self.count = 0
        self.matched = False
        self.bad: str | None = None

    def begin(self, session) -> None:
        self._reset(session.robot.next_index if session.robot is not None else 0)

    def _scan(self, session) -> None:
        robot = session.robot
        if robot is None or robot.next_index <= self.first:
            return
        for line in robot.since(self.first):
            self.count += 1
            self.first = line.index + 1
            if self.contains is not None and not self.matched and self.contains.search(line.text):
                self.matched = True
            if self.not_contains is not None and self.bad is None and self.not_contains.search(line.text):
                self.bad = line.text

    def tick(self, session, t: float) -> None:
        self._scan(session)

    def end(self, session) -> CheckResult:
        self._scan(session)
        if self.contains is not None and not self.matched:
            return CheckResult(self.label, False, f"no console line matched /{self.contains.pattern}/ "
                               f"({self.count} lines in the step)", None, self.contains.pattern)
        if self.bad is not None:
            return CheckResult(self.label, False,
                               f"console line matched /{self.not_contains.pattern}/: {self.bad}", self.bad, None)
        return CheckResult(self.label, True, "console as expected")


class ByTimeCheck(Check):
    """by_time: an at_end condition that must hold at some tick up to `t` seconds into the step (the first such
    tick passes it); otherwise it fails with the value at t."""

    def __init__(self, spec: dict, world, where: str):
        _only(spec, {"signal", "min", "max", "equals", "approx", "tol", "t"}, where)
        self.t = _num(spec, "t", where)
        if self.t is None:
            raise ScenarioError(f"{where}: by_time needs t, the seconds into the step it must hold by")
        if self.t < 0:
            raise ScenarioError(f"{where}: t must be 0 or more seconds into the step, not {_show(self.t)}")
        self.cond = SignalCheck("at_end", {k: v for k, v in spec.items() if k != "t"}, world, where)
        self.signal = self.cond.signal
        self.uses_nt = self.signal.uses_nt
        self.ticks = True
        self.label = f"by_time {self.signal.name} {self.cond._expected()} by t={_show(self.t)}"
        self._reset(0.0)

    def _reset(self, start: float) -> None:
        self.start = start
        self.reached: tuple[float, Any] | None = None    # (step time, value) of the first tick that met it
        self.last: tuple[float, Any] | None = None       # the last tick up to t

    def begin(self, session) -> None:
        self._reset(session.t)
        self.tick(session, session.t)

    def tick(self, session, t: float) -> None:
        st = t - self.start
        if self.reached is not None or st > self.t + 1e-9:
            return
        v = self.signal.read(session)
        self.last = (round(st, 4), v)
        if self.cond._ok(v):
            self.reached = self.last

    def end(self, session) -> CheckResult:
        name, by = self.signal.name, _show(self.t)
        expected = f"{self.cond._expected()} by t={by}"
        if self.reached is not None:
            st, v = self.reached
            return CheckResult(self.label, True, f"{name} reached {_show(v)} at t={_show(st)} (by {by})", v, expected)
        fail_t = round(self.start + self.t, 4)
        if self.last is None or self.last[1] is None:
            at = f" at t={_show(self.last[0])}" if self.last is not None else ""
            return CheckResult(self.label, False, f"{name} has no value{at}; expected {expected}", None, expected,
                               fail_t)
        st, v = self.last
        return CheckResult(self.label, False, f"{name} was {_show(v)} at t={_show(st)}; expected {expected}", v,
                           expected, fail_t)


def tick_checks(checks, session, t: float) -> None:
    """Tick every check at t, then start the terrain and climb signals' next peak-hold: every check this tick reads
    the same hold, the largest values since the last tick."""
    for c in checks:
        c.tick(session, t)
    consume_peaks(session.world)


def consume_peaks(world) -> None:
    """Start the next peak-hold of the world's terrain and climb signals (those it has)."""
    for monitor in (getattr(world, "terrain", None), getattr(world, "climb", None)):
        if monitor is not None:
            monitor.consume()


CHECK_KINDS = ("pose", "odometry_error", "at_end", "always", "by_time", "never_touch", "must_touch", "console")


def build_check(spec: dict, world, where: str) -> Check:
    keys = [k for k in spec if k in CHECK_KINDS]
    if len(keys) != 1 or len(spec) != 1:
        raise ScenarioError(f"{where}: each [[step.check]] needs exactly one of {', '.join(CHECK_KINDS)}; "
                            f"got {', '.join(spec) or 'nothing'}")
    kind, val = keys[0], spec[keys[0]]
    w = f"{where}, {kind}"
    if kind in ("never_touch", "must_touch"):
        return ContactCheck(val, kind == "must_touch", world, w)
    if not isinstance(val, dict):
        raise ScenarioError(f"{w}: expected a table like {kind} = {{ ... }}")
    if kind == "pose":
        return PoseCheck(val, w)
    if kind == "odometry_error":
        return OdometryErrorCheck(val, w)
    if kind == "console":
        return ConsoleCheck(val, w)
    if kind == "by_time":
        return ByTimeCheck(val, world, w)
    return SignalCheck(kind, val, world, w)
