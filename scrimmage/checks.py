"""Signals a scenario can check, and the checks themselves. Checks read the session duck-typed (world,
bridge.hal, robot, nt_value), so they can be unit-tested without a robot program.

Units and Language: every check result words its message twice, through `said()`: `message` as it always was
(American English, meters), for report.json and the MCP result, which don't change with the settings, and `shown()`
in the Units and Language settings, for report.md and the Scenario tab (the terminal's is American English). A
signal's own value is shown as the signal gives it. ScenarioError's messages (a scenario file's problems) stay
American English: the Scenario tab places them on their fields by their words, as the CLI prints them."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, fields
from dataclasses import field as dc_field
from typing import Any, Callable

from . import i18n, units
from .i18n import t, tn

# Python tracebacks and Java stack traces in the robot console
CRASH_PATTERN = re.compile(r"^Traceback \(most recent call last\)|^Exception in thread|^\s+at [\w.$<>]+\(.*\)\s*$",
                           re.MULTILINE)
SIGNALS_HELP = ("battery, speed, motor.<name>.current|command, joint.<name>.position|velocity, "
                "robot.ground_gap|wheels_down|air_time|pitch|roll|tilt|z|stranded|tipped, "
                "climb.hanging|level|points|swing|hook_load|extension|height, climb.hook.<hook>.load, "
                "hal:<type>/<device>/<key>, nt:<topic>, and on a test range score, score.<goal>, fired, held, on_field, "
                "with a [game] points, points.auto|teleop|endgame, element.<platform>.angle, element.<hinged or sliding element>, with a [partner] partner.height|"
                "tilt|moved, and with [[sensor]]s sensor.<name>, sensor.<name>.distance|color, "
                "sensor.<name>.sees.<color>")
RANGE_SIGNALS = ("score", "fired", "held", "on_field")   # and score.<goal>: read from the range's counts()


class ScenarioError(ValueError):
    """A mistake in a scenario file. Its message is "<where>: <detail>" (the file, step, check or screenshot, then
    the problem) and stays what the CLI and MCP print. For the Scenario tab (spec L3: placed by these, not by its
    words, so the words can be translated): `field`, the tab's field id it is about ("step0/mode",
    "step1/check0/pose.tol_xy", "scenario/match.station"), set where the loader knows it; `key`, the key inside a
    check or screenshot a world check names ("signal", "min", "view"), which the tab adds to the item's field;
    `detail`, the message without its where, shown beside the field."""

    def __init__(self, message: str = "", *, field: str | None = None, key: str | None = None,
                 detail: str | None = None):
        super().__init__(message)
        self.field, self.key, self.detail = field, key, detail


def bad(where: str, detail: str, *, field: str | None = None, key: str | None = None) -> ScenarioError:
    """A ScenarioError at `where`: "<where>: <detail>", knowing its field (or key) and its detail."""
    return ScenarioError(f"{where}: {detail}", field=field, key=key, detail=detail)


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
        raise ScenarioError(t("{signal} needs a robot on a test range: add {range_table} to the config (see "
                              "docs/test-range.md)", signal=name, range_table="[range]"))
    if name.startswith("score."):
        goal = name[len("score."):]
        goals = [g.name for g in rng.range.goals]
        if goal not in goals:
            if goals:
                raise ScenarioError(t("unknown signal {signal}: range {range} has the goals {goals}",
                                      signal=repr(name), range=repr(rng.range.name), goals=", ".join(goals)))
            raise ScenarioError(t("unknown signal {signal}: range {range} has the goals none", signal=repr(name),
                                  range=repr(rng.range.name)))
        return Signal(name, lambda s: s.world.test_range.counts()["goals"][goal])
    return Signal(name, lambda s: s.world.test_range.counts()[name])


POINTS_FIELDS = ("auto", "teleop", "endgame")


def _points_signal(name: str, world) -> Signal:
    """points, points.auto, points.teleop, points.endgame: the points so far, as the Test tab's points condition reads
    them (scrimmage/scenario_points.py: the scenario's steps are the phases)."""
    rng = getattr(world, "test_range", None)
    if rng is None:
        raise ScenarioError(t("{signal} needs a robot on a test range: add {range_table} to the config (see "
                              "docs/test-range.md)", signal=name, range_table="[range]"))
    field = name[len("points"):].lstrip(".") if name == "points" or name.startswith("points.") else None
    if field not in ("", *POINTS_FIELDS):
        raise ScenarioError(t("unknown signal {signal}: the points signals are {signals}", signal=repr(name),
                              signals=", ".join(("points", *("points." + f for f in POINTS_FIELDS)))))
    if rng.range.game is None:
        raise ScenarioError(t("{signal} needs a {game_table} table in the range file: it says what the pieces are "
                              "worth (see docs/test-range.md)", signal=name, game_table="[game]"))
    which = field or "total"

    def read(s) -> float:
        m = getattr(s.world, "scenario_match", None)
        return 0.0 if m is None else float(getattr(m.points(float(s.world.time)), which))
    return Signal(name, read)


def _robot_signal(name: str, world) -> Signal:
    """robot.<field>: the terrain signals (scrimmage/terrain/monitor.py), peak-held between ticks."""
    from .terrain.monitor import ROBOT_SIGNALS, WHEEL_SIGNALS
    field = name[len("robot."):]
    if field not in ROBOT_SIGNALS:
        raise ScenarioError(t("unknown signal {signal}: the robot signals are {signals}", signal=repr(name),
                              signals=", ".join("robot." + f for f in ROBOT_SIGNALS)))
    make = getattr(world, "terrain_monitor", None)
    if make is None:
        raise ScenarioError(t("{signal} needs a simulated robot", signal=name))
    if field in WHEEL_SIGNALS and not (world.parts is not None and world.parts.tread_geoms):
        raise ScenarioError(t("{signal} reads the drive wheels, but this robot has no tread wheels (a hand-written "
                              "{model_table} mjcf robot, or parts without {tread_setting} geoms): build it from "
                              "{part_table} with a stock drivetrain, or use robot.tilt, pitch, roll, z, air_time or "
                              "tipped", signal=name, model_table="[model]", tread_setting="tread = true",
                              part_table="[[part]]"))
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
    known = t("platforms: {platforms}; moving elements: {moving}", platforms=", ".join(platforms) or t("none"),
              moving=", ".join(units) or t("none"))
    if field != "angle" or not elem:
        raise ScenarioError(t("unknown signal {signal}: use element.<platform>.angle or element.<moving element>; "
                              "{known}", signal=repr(name), known=known))
    if elem not in platforms:
        kinds = {e.name: type(e).__name__.lower() for e in elements}   # the element's kind is its file key
        said = {"signal": repr(name), "element": repr(elem), "name": elem, "kind": kinds.get(elem), "known": known}
        if elem in units:
            message = t("signal {signal}: {element} is a {kind} element: read it as element.{name}; {known}", **said)
        elif elem in kinds:
            message = t("signal {signal}: element {element} is a {kind}, not a platform: only a platform tilts; "
                        "{known}", **said)
        else:
            message = t("signal {signal}: no platform {element}; {known}", **said)
        raise ScenarioError(message)
    world.terrain_monitor()
    return Signal(name, lambda s: s.world.terrain.platform_angle(elem))


def _partner_signal(name: str, world) -> Signal:
    """partner.height (in), partner.tilt (deg) and partner.moved (in): the [partner] robot's (parts/partner.py)."""
    from .parts.partner import SIGNALS
    if name not in SIGNALS:
        raise ScenarioError(t("unknown signal {signal}: the partner signals are {signals}", signal=repr(name),
                              signals=", ".join(SIGNALS)))
    if getattr(world, "partner", None) is None:
        raise ScenarioError(t("{signal} needs a partner robot: add {partner_table} to the config (see README.md)",
                              signal=name, partner_table="[partner]"))
    return Signal(name, lambda s: s.world.partner.read(name))


def _climb_signal(name: str, world) -> Signal:
    """climb.<field> and climb.hook.<hook>.load: the climb signals (scrimmage/terrain/climb.py), peak-held between
    ticks."""
    from .terrain.climb import CLIMB_SIGNALS, EXTENT_SIGNALS, robot_hooks
    make = getattr(world, "climb_monitor", None)
    field = name[len("climb."):]
    if field.startswith("hook.") and field.endswith(".load"):
        if make is None:
            raise ScenarioError(t("{signal} needs a simulated robot", signal=name))
        hook = field[len("hook."):-len(".load")]
        hooks = list(robot_hooks(world.model, world.robot_body))
        if hook not in hooks:
            raise ScenarioError(t("unknown signal {signal}: the robot's hooks are {hooks} (a stock climber's, "
                                  "<part>.hook<i>), each read as climb.hook.<hook>.load", signal=repr(name),
                                  hooks=", ".join(hooks) or t("none")))
    elif field not in CLIMB_SIGNALS:
        raise ScenarioError(t("unknown signal {signal}: the climb signals are {signals} and climb.hook.<hook>.load",
                              signal=repr(name), signals=", ".join("climb." + f for f in CLIMB_SIGNALS)))
    elif make is None:
        raise ScenarioError(t("{signal} needs a simulated robot", signal=name))
    mon = make()
    if field in EXTENT_SIGNALS:
        mon.track_extent()
    return Signal(name, lambda s: s.world.climb.sample()[name])


def _sensor_signal(name: str, world) -> Signal:
    """sensor.<name>: true while the [[sensor]] is on (a beam broken, a distance closer than its threshold, a color
    sensor seeing a piece), as the Test tab's sensor condition reads it; sensor.<name>.distance (m: a distance sensor's,
    max_range with nothing in range; a color sensor's to the piece it sees; no value otherwise); sensor.<name>.color
    (the named color a color sensor sees, no value while it sees none); sensor.<name>.sees.<color> (true while it is on
    and sees that named color)."""
    from .sensors.colors import NAMED_COLORS
    runtime = getattr(world, "sensors", None)
    if runtime is None:
        raise ScenarioError(t("{signal} needs a sensor: add a {sensor_table} to the config (see docs/sensors.md)",
                              signal=name, sensor_table="[[sensor]]"))
    kinds = {sp.name: sp.kind for sp in runtime.specs}
    sensor, _, field = name[len("sensor."):].partition(".")     # a sensor's name has no dot (sensors/spec.py)
    if sensor not in kinds or field not in ("", "distance", "color") and not field.startswith("sees."):
        raise ScenarioError(t("unknown signal {signal}: use one of {sensor_signals}; the robot's sensors: {sensors}",
                              signal=repr(name), sensors=", ".join(kinds),
                              sensor_signals="sensor.<name>, sensor.<name>.distance, sensor.<name>.color, "
                                             "sensor.<name>.sees.<color>"))
    if field == "":
        return Signal(name, lambda s: s.world.sensors.reading(sensor).on)
    if field == "distance":
        if kinds[sensor] == "beam":
            raise ScenarioError(t("signal {signal}: {sensor} is a beam break, which reads no distance; read it as "
                                  "{on_signal}", signal=repr(name), sensor=sensor, on_signal=f"sensor.{sensor}"))
        return Signal(name, lambda s: s.world.sensors.reading(sensor).distance)
    if kinds[sensor] != "color":
        raise ScenarioError(t("signal {signal}: {sensor} isn't a color sensor, so it sees no color", signal=repr(name),
                              sensor=sensor))
    if field == "color":
        return Signal(name, lambda s: s.world.sensors.reading(sensor).color_name)
    color = field[len("sees."):]
    if color not in NAMED_COLORS:
        raise ScenarioError(t("signal {signal}: {color} isn't a named color; the colors: {colors}", signal=repr(name),
                              color=repr(color), colors=", ".join(NAMED_COLORS)))

    def sees(s) -> bool:
        r = s.world.sensors.reading(sensor)
        return bool(r.on and r.color_name == color)
    return Signal(name, sees)


def resolve_signal(name: str, world) -> Signal:
    if name.startswith("sensor."):
        return _sensor_signal(name, world)
    if name in RANGE_SIGNALS or name.startswith("score."):
        return _range_signal(name, world)
    if name == "points" or name.startswith("points."):
        return _points_signal(name, world)
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
            raise ScenarioError(t("unknown signal {signal}: use motor.<name>.current or .command; motors: {motors}",
                                  signal=repr(name), motors=", ".join(motors) or t("none")))
        i = motors.index(motor)
        return Signal(name, lambda s: getattr(s.world.motors[i], field))
    if name.startswith("joint."):
        joint, _, field = name[len("joint."):].rpartition(".")
        joints = sorted(world.joint_states())
        if field not in ("position", "velocity") or joint not in joints:
            raise ScenarioError(t("unknown signal {signal}: use joint.<name>.position or .velocity; joints: {joints}",
                                  signal=repr(name), joints=", ".join(joints) or t("none")))
        j, k = joint, 0 if field == "position" else 1
        return Signal(name, lambda s: s.world.joint_states()[j][k])
    if name.startswith("hal:"):
        typ, _, rest = name[4:].partition("/")
        dev, _, key = rest.rpartition("/")
        if not (typ and key):
            raise ScenarioError(t("unknown signal {signal}: use hal:<type>/<device>/<key>, e.g. hal:PWM/0/<speed",
                                  signal=repr(name)))
        if key[0] not in "<>":
            raise ScenarioError(t("signal {signal}: the key needs its direction prefix, e.g. hal:PWM/0/<speed (< for "
                                  "robot output, > for input)", signal=repr(name)))
        return Signal(name, lambda s: s.bridge.hal.get(typ, dev, key))
    if name.startswith("nt:") and len(name) > 3:
        topic = name[3:]
        if not topic.startswith("/"):
            raise ScenarioError(t("signal {signal}: NetworkTables topics start with /, e.g. nt:/SmartDashboard/x",
                                  signal=repr(name)))
        return Signal(name, lambda s: s.nt_value(topic), uses_nt=True)
    raise ScenarioError(t("unknown signal {signal}; signals are {signals}", signal=repr(name), signals=SIGNALS_HELP))


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
        """The message in the Units and Language settings (report.md, the page); `message` is the machine one."""
        return self.words() if self.words is not None else self.message

    def to_dict(self, shown: bool = False) -> dict:
        d = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "words"}
        if shown:
            d["message"] = self.shown()
        return d


def said(check: str, passed: bool, message: str, words: Callable[[], str], *rest) -> CheckResult:
    """A CheckResult worded twice: `message` the machine text, as it was before the Units and Language settings
    (report.json and MCP results don't change with them), and `words` to build it in the settings when it is shown."""
    return CheckResult(check, passed, message, *rest, words=words)


class Said(str):
    """Text worded twice, as a CheckResult's message is (said()): the str itself is the machine text (report.json, MCP
    results), and `words()` builds it in the Units and Language settings when it is shown (shown())."""

    def __new__(cls, text: str, words: Callable[[], str]):
        s = super().__new__(cls, text)
        s.words = words
        return s

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __reduce__(self):
        """Pickled (sent to another process), it is its machine text: its words are a closure."""
        return str, (str(self),)


def worded(words: Callable[[], str]) -> Said:
    """One builder for both texts of a unit-free Said: the machine text is words() in the source's English
    (i18n.fixed()), the shown text words() in the Language setting when it is shown."""
    with i18n.fixed():
        return Said(words(), words)


def shown(text):
    """`text` as it is shown: a Said's words, else the text itself (None stays None)."""
    return text.words() if isinstance(text, Said) else text


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
        raise bad(where, t("{key} must be a number, not {value}", key=key, value=repr(v)), key=key)
    if not math.isfinite(v):
        raise bad(where, t("{key} must be a finite number, not {value}", key=key, value=repr(v)), key=key)
    return float(v)


def _tol(spec: dict, key: str, where: str) -> float | None:
    v = _num(spec, key, where)
    if v is not None and v < 0:
        raise bad(where, t("{key} can't be negative", key=key), key=key)
    return v


def _only(spec: dict, allowed: set[str], where: str) -> None:
    extra = set(spec) - allowed
    if extra:
        first = sorted(extra)[0]
        raise bad(where, t("unknown key {key}; allowed: {allowed}", key=repr(first),
                           allowed=", ".join(sorted(allowed))),
                  key=first)


class PoseCheck(Check):
    def __init__(self, spec: dict, where: str):
        _only(spec, {"x", "y", "heading", "tol_xy", "tol_heading"}, where)
        self.x, self.y, self.h = _num(spec, "x", where), _num(spec, "y", where), _num(spec, "heading", where)
        if self.x is None and self.y is None and self.h is None:
            raise bad(where, t("pose needs at least one of x, y, heading"))
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
            at = {"x": _far(x), "y": _far(y), "heading": f"{hdg:.1f}", "dist": _far(dist), "off": f"{dh:.1f}"}
            if ok:
                return t("pose within {dist} and {off}° of target (x={x}, y={y}, heading={heading}°)", **at)
            return t("pose: {dist} from target, heading off by {off}° (x={x}, y={y}, heading={heading}°); "
                     "allowed {tol_xy}, {tol_heading}°", tol_xy=_far(self.tol_xy), tol_heading=f"{self.tol_h:g}", **at)
        return said(self.label, ok, msg, words, measured, expected)


class SignalCheck(Check):
    """at_end or always: a signal compared with min / max / equals / approx+tol."""

    def __init__(self, kind: str, spec: dict, world, where: str):
        _only(spec, {"signal", "min", "max", "equals", "approx", "tol"}, where)
        if "signal" not in spec:
            raise bad(where, t("{kind} needs {signal_setting}", kind=kind, signal_setting="signal = \"...\""))
        self.kind = kind
        try:
            self.signal = resolve_signal(str(spec["signal"]), world)
        except ScenarioError as e:
            e.key = e.key or "signal"       # the Scenario tab shows it on the signal's box
            e.detail = e.detail or str(e)   # it says no where: all of it goes beside the box
            raise
        self.min, self.max = _num(spec, "min", where), _num(spec, "max", where)
        self.equals = spec.get("equals")
        self.approx, self.tol = _num(spec, "approx", where), _tol(spec, "tol", where)
        if self.equals is not None:
            ok_type = isinstance(self.equals, (bool, str)) or (
                isinstance(self.equals, (int, float)) and math.isfinite(self.equals))
            if not ok_type:
                raise bad(where, t("equals must be a bool, a finite number or a string, not {value}",
                                   value=repr(self.equals)), key="equals")
            if self.min is not None or self.max is not None or self.approx is not None:
                raise bad(where, t("equals can't be combined with min, max or approx"), key="equals")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise bad(where, t("min ({min}) is greater than max ({max})", min=f"{self.min:g}", max=f"{self.max:g}"),
                      key="min")
        if self.approx is not None and self.tol is None:
            raise bad(where, t("approx needs tol"), key="approx")
        if self.tol is not None and self.approx is None:
            raise bad(where, t("tol needs approx"), key="tol")
        if kind == "always" and self.approx is not None:
            raise bad(where, t("always checks take min, max or equals"))
        if self.min is None and self.max is None and self.equals is None and self.approx is None:
            raise bad(where, t("{kind} needs min, max, equals, or approx with tol", kind=kind))
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
        return " and ".join(self._expected_parts())

    def _expected_parts(self) -> list[str]:
        parts = []
        if self.min is not None:
            parts.append(f">= {_show(self.min)}")
        if self.max is not None:
            parts.append(f"<= {_show(self.max)}")
        if self.equals is not None:
            parts.append(f"== {self.equals!r}")
        if self.approx is not None:
            parts.append(f"≈ {_show(self.approx)} ± {_show(self.tol)}")
        return parts

    def _expected_words(self) -> str:
        """_expected() in the Language setting, when it is called: its parts joined by the translator's "and"."""
        parts = self._expected_parts() or [""]
        words = parts[0]
        for p in parts[1:]:
            words = t("{names} and {last}", names=words, last=p)
        return words

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

    def _missing(self, session, at_end: bool) -> Said:
        """Why the signal has no value: machine text and words from one builder (worded())."""
        name = self.signal.name
        if name.startswith("hal:"):
            seen = ", ".join(f"{typ}/{d}" for typ, d in sorted(session.bridge.hal.devices))
            return worded(lambda: t("no HAL value {name}; devices seen: {seen}", name=name, seen=seen or t("none")))
        if at_end:
            return worded(lambda: t("{name} has no value at the end of the step", name=name))
        return worded(lambda: t("{name} has no value", name=name))

    def _sampled(self) -> str:
        """The machine text's hold-point suffix (the words' sentences carry it whole: _period())."""
        period = self._period()
        return f" (sampled at {period} s hold points)" if period is not None else ""

    def _period(self) -> str | None:
        """The hold-point period the words say the signal was sampled at, or None (each sentence has its own form)."""
        return f"{self.nt_period:g}" if self.uses_nt and self.nt_period is not None else None

    def end(self, session) -> CheckResult:
        if self.kind == "always":
            if not self.seen:
                self.tick(session, getattr(session, "t", 0.0))
            ok = self.first_fail is None
            name, expected, period = self.signal.name, self._expected_words, self._period()
            if ok:
                measured = self.last if self.equals is not None else self.extreme
                return said(self.label, True, f"{self.signal.name} stayed {self._expected()}{self._sampled()}",
                            lambda: t("{name} stayed {expected}", name=name, expected=expected()) if period is None
                            else t("{name} stayed {expected} (sampled at {period} s hold points)", name=name,
                                   expected=expected(), period=period),
                            measured, self._expected())
            if self.worst_excess is None:
                msg = f"{self._missing(session, False)} (first at t={self.first_missing}); expected {self._expected()}"
                first = self.first_missing
                seen = (", ".join(f"{typ}/{d}" for typ, d in sorted(session.bridge.hal.devices))
                        if name.startswith("hal:") else None)

                def words() -> str:   # a whole sentence for each: a HAL value or not, sampled or not
                    if seen is not None:
                        p = dict(name=name, seen=seen or t("none"), t=first, expected=expected())
                        if period is None:
                            return t("no HAL value {name}; devices seen: {seen} (first at t={t}); expected {expected}",
                                     **p)
                        return t("no HAL value {name}; devices seen: {seen} (first at t={t}); expected {expected} "
                                 "(sampled at {period} s hold points)", period=period, **p)
                    if period is None:
                        return t("{name} has no value (first at t={t}); expected {expected}", name=name, t=first,
                                 expected=expected())
                    return t("{name} has no value (first at t={t}); expected {expected} (sampled at {period} s hold "
                             "points)", name=name, t=first, expected=expected(), period=period)
            else:
                msg = (f"{self.signal.name} was {_show(self.worst)} at t={self.worst_t} (first out of range at "
                       f"t={self.first_num_fail}); expected {self._expected()}")
                if self.n_missing:
                    msg += f"; no value at {self.n_missing} ticks (first at t={self.first_missing})"
                worst, worst_t, first_num, n_missing, first_missing = (
                    _show(self.worst), self.worst_t, self.first_num_fail, self.n_missing, self.first_missing)

                def words() -> str:   # a whole sentence for each: with no-value ticks or not, sampled or not
                    if n_missing and period is not None:
                        return tn("{name} was {value} at t={t} (first out of range at t={first}); expected "
                                  "{expected}; no value at {count} tick (first at t={first_missing}) (sampled at "
                                  "{period} s hold points)",
                                  "{name} was {value} at t={t} (first out of range at t={first}); expected "
                                  "{expected}; no value at {count} ticks (first at t={first_missing}) (sampled at "
                                  "{period} s hold points)", n_missing, name=name, value=worst, t=worst_t,
                                  first=first_num, expected=expected(), first_missing=first_missing, period=period)
                    if n_missing:
                        return tn("{name} was {value} at t={t} (first out of range at t={first}); expected "
                                  "{expected}; no value at {count} tick (first at t={first_missing})",
                                  "{name} was {value} at t={t} (first out of range at t={first}); expected "
                                  "{expected}; no value at {count} ticks (first at t={first_missing})", n_missing,
                                  name=name, value=worst, t=worst_t, first=first_num, expected=expected(),
                                  first_missing=first_missing)
                    if period is not None:
                        return t("{name} was {value} at t={t} (first out of range at t={first}); expected "
                                 "{expected} (sampled at {period} s hold points)", name=name, value=worst, t=worst_t,
                                 first=first_num, expected=expected(), period=period)
                    return t("{name} was {value} at t={t} (first out of range at t={first}); expected {expected}",
                             name=name, value=worst, t=worst_t, first=first_num, expected=expected())
            msg += self._sampled()
            return said(self.label, False, msg, words, self.worst, self._expected(), self.first_fail)
        v = self.signal.read(session)
        expected = self._expected_words
        if v is None:
            missing = self._missing(session, True)
            return said(self.label, False, missing, missing.words, None, self._expected())
        ok = self._ok(v)
        name, value = self.signal.name, _show(v)
        return said(self.label, ok, f"{self.signal.name} = {_show(v)} at the end; expected {self._expected()}",
                    lambda: t("{name} = {value} at the end; expected {expected}", name=name, value=value,
                              expected=expected()),
                    v, self._expected())


class OdometryErrorCheck(Check):
    def __init__(self, spec: dict, where: str):
        _only(spec, {"max", "when"}, where)
        self.max = _num(spec, "max", where)
        if self.max is None:
            raise bad(where, t("odometry_error needs max"))
        if self.max <= 0:
            raise bad(where, t("odometry_error max must be greater than 0"))
        self.when = spec.get("when", "end")
        if self.when not in ("end", "always"):
            raise bad(where, t("odometry_error when must be \"{end}\" or \"{always}\"", end="end", always="always"))
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
                return said(self.label, False, "no odometry on NetworkTables ([telemetry] odometry)", _no_odometry,
                            None, self.max)
            return said(self.label, e <= self.max,
                        f"odometry {e:.3f} m from ground truth at the end; allowed {self.max:g} m",
                        lambda: t("odometry {error} from ground truth at the end; allowed {max}", error=_far(e),
                                  max=_far(self.max)),
                        round(e, 4), self.max)
        if self.missing and self.worst == 0.0:
            return said(self.label, False, "no odometry on NetworkTables ([telemetry] odometry)", _no_odometry, None,
                        self.max)
        ok = self.first_fail is None
        worst = self.worst
        return said(self.label, ok, f"odometry error peaked at {worst:.3f} m; allowed {self.max:g} m",
                    lambda: t("odometry error peaked at {error}; allowed {max}", error=_far(worst),
                              max=_far(self.max)),
                    round(worst, 4), self.max, self.first_fail)


def _no_odometry() -> str:
    return t("no odometry on NetworkTables ({telemetry_table} odometry)", telemetry_table="[telemetry]")


class ContactCheck(Check):
    def __init__(self, target: Any, must: bool, world, where: str):
        if not isinstance(target, str) or target not in world.contact_names():
            raise bad(where, t("no geom or body {target} to touch; names: {names}", target=repr(target),
                               names=", ".join(world.contact_names())))
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
        target, first = self.target, self.first_t

        def words() -> str:
            if touched:
                return t("touched {target} at t={t}", target=target, t=first)
            return t("never touched {target}", target=target)
        return said(self.label, ok, msg, words, touched, self.must, None if self.must else self.first_t)


class ConsoleCheck(Check):
    def __init__(self, spec: dict, where: str):
        _only(spec, {"contains", "not_contains"}, where)
        try:
            self.contains = re.compile(spec["contains"]) if "contains" in spec else None
            self.not_contains = re.compile(spec["not_contains"]) if "not_contains" in spec else None
        except re.error as e:
            raise bad(where, t("bad regex: {problem}", problem=e)) from None
        if self.contains is None and self.not_contains is None:
            raise bad(where, t("console needs contains or not_contains"))
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
            pattern, count = self.contains.pattern, self.count
            return said(self.label, False, f"no console line matched /{self.contains.pattern}/ "
                        f"({self.count} lines in the step)",
                        lambda: tn("no console line matched /{pattern}/ ({count} line in the step)",
                                   "no console line matched /{pattern}/ ({count} lines in the step)", count,
                                   pattern=pattern),
                        None, self.contains.pattern)
        if self.bad is not None:
            pattern, bad = self.not_contains.pattern, self.bad
            return said(self.label, False, f"console line matched /{self.not_contains.pattern}/: {self.bad}",
                        lambda: t("console line matched /{pattern}/: {line}", pattern=pattern, line=bad),
                        self.bad, None)
        return said(self.label, True, "console as expected", lambda: t("console as expected"))


class ByTimeCheck(Check):
    """by_time: an at_end condition that must hold at some tick up to `t` seconds into the step (the first such
    tick passes it); otherwise it fails with the value at t."""

    def __init__(self, spec: dict, world, where: str):
        _only(spec, {"signal", "min", "max", "equals", "approx", "tol", "t"}, where)
        self.t = _num(spec, "t", where)
        if self.t is None:
            raise bad(where, t("by_time needs t, the seconds into the step it must hold by"))
        if self.t < 0:
            raise bad(where, t("t must be 0 or more seconds into the step, not {value}", value=_show(self.t)), key="t")
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
            return said(self.label, True, f"{name} reached {_show(v)} at t={_show(st)} (by {by})",
                        lambda: t("{name} reached {value} at t={t} (by {by})", name=name, value=_show(v), t=_show(st),
                                  by=by),
                        v, expected)
        fail_t = round(self.start + self.t, 4)
        if self.last is None or self.last[1] is None:
            at = f" at t={_show(self.last[0])}" if self.last is not None else ""
            last = self.last

            def words() -> str:
                if last is None:
                    return t("{name} has no value; expected {expected}", name=name, expected=expected)
                return t("{name} has no value at t={t}; expected {expected}", name=name, t=_show(last[0]),
                         expected=expected)
            return said(self.label, False, f"{name} has no value{at}; expected {expected}", words, None, expected,
                        fail_t)
        st, v = self.last
        return said(self.label, False, f"{name} was {_show(v)} at t={_show(st)}; expected {expected}",
                    lambda: t("{name} was {value} at t={t}; expected {expected}", name=name, value=_show(v),
                              t=_show(st), expected=expected),
                    v, expected, fail_t)


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


CHECK_KINDS = ("pose", "odometry_error", "at_end", "always", "by_time", "never_touch", "must_touch", "console",
               "sensor")
SENSOR_WHEN = ("end", "always", "by")


def sensor_check(spec: dict, world, where: str) -> Check:
    """sensor = {name, is = "on" or "off", sees, when, t}: the Test tab's sensor condition as a step check. The sensor
    (with `sees`, a color sensor seeing that named color) is on, or off: at the end of the step (when = "end", the
    default), on every tick of it ("always"), or at some tick up to `t` s into it ("by"). It is the at_end, always or
    by_time check of the signal sensor.<name> (sensor.<name>.sees.<color> with sees) equal to true for on, false for
    off, so it reports as they do."""
    _only(spec, {"name", "is", "sees", "when", "t"}, where)
    name = spec.get("name")
    if not isinstance(name, str) or not name:
        raise bad(where, t("{sensor_check} needs {name_setting}, the {sensor_table}'s name", sensor_check="sensor",
                           name_setting="name = \"...\"", sensor_table="[[sensor]]"), key="name")
    state = spec.get("is", "on")
    if state not in ("on", "off"):
        raise bad(where, t("{is_setting} must be {on} or {off}, not {value}", is_setting="is", on='"on"', off='"off"',
                           value=repr(state)), key="is")
    when = spec.get("when", "end")
    if when not in SENSOR_WHEN:
        raise bad(where, t("{when_setting} must be one of {choices}, not {value}", when_setting="when",
                           choices=", ".join(f'"{w}"' for w in SENSOR_WHEN), value=repr(when)), key="when")
    if ("t" in spec) != (when == "by"):
        raise bad(where, t("{t_setting} goes with {by_setting} (the seconds into the step it must hold by)",
                           t_setting="t", by_setting="when = \"by\""), key="t")
    sees = spec.get("sees")
    if sees is not None and not isinstance(sees, str):
        raise bad(where, t("{sees_setting} must be a named color, not {value}", sees_setting="sees",
                           value=repr(sees)), key="sees")
    cond = {"signal": f"sensor.{name}" if sees is None else f"sensor.{name}.sees.{sees}", "equals": state == "on"}
    if when == "by":
        return ByTimeCheck({**cond, "t": spec["t"]}, world, where)
    return SignalCheck("at_end" if when == "end" else "always", cond, world, where)


def build_check(spec: dict, world, where: str) -> Check:
    keys = [k for k in spec if k in CHECK_KINDS]
    if len(keys) != 1 or len(spec) != 1:
        if spec:
            raise bad(where, t("each {step_check_table} needs exactly one of {kinds}; got {got}",
                               kinds=", ".join(CHECK_KINDS), got=", ".join(spec), step_check_table="[[step.check]]"))
        raise bad(where, t("each {step_check_table} needs exactly one of {kinds}; got nothing",
                           kinds=", ".join(CHECK_KINDS), step_check_table="[[step.check]]"))
    kind, val = keys[0], spec[keys[0]]
    w = f"{where}, {kind}"
    if kind in ("never_touch", "must_touch"):
        return ContactCheck(val, kind == "must_touch", world, w)
    if not isinstance(val, dict):
        raise bad(w, t("expected a table like {kind_setting}", kind_setting="{} = {{ ... }}".format(kind)))
    if kind == "pose":
        return PoseCheck(val, w)
    if kind == "odometry_error":
        return OdometryErrorCheck(val, w)
    if kind == "console":
        return ConsoleCheck(val, w)
    if kind == "by_time":
        return ByTimeCheck(val, world, w)
    if kind == "sensor":
        return sensor_check(val, world, w)
    return SignalCheck(kind, val, world, w)
