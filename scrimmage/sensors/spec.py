"""A robot config's `[[sensor]]` tables, checked: beam breaks, distance sensors, color sensors and cameras.

    [[sensor]]
    name = "mouth"                  # required, unique, letters, digits and _: what the Test tab and messages call it
    kind = "beam"                   # "beam", "distance" or "color"
    body = "intake.frame"           # the robot body it is mounted on, "<part instance>.<body>"
    from = [0.30, 0.17, 0.075]      # beam: its two ends, m in the body's frame (or from_in / to_in, inches)
    to = [0.30, -0.17, 0.075]
    dio = 0                         # a DigitalInput; active_low = true (the default) reads false while broken

    [[sensor]]
    name = "range"
    kind = "distance"
    body = "drive.chassis"
    at = [0.42, 0.0, 0.1]           # distance and color: where it looks from (m, or at_in) ...
    toward = [1, 0, 0]              # ... and which way (a direction in the body's frame; any length)
    max_range = 2.0                 # m (or max_range_in): 2.0 for distance, 0.10 (the most) for color
    sees = "pieces"                 # "pieces" (the default) or "anything": game pieces only, or anything but the robot
    analog = 0                      # an AnalogInput, volts_per_m (0.977) volts per meter of distance
    dio = 1                         # and/or a DigitalInput that is on while closer than threshold (m, or threshold_in;
    threshold = 0.5                 #   default half of max_range)

    [[sensor]]
    name = "eye"
    kind = "color"
    body = "intake.frame"
    at = [0.3, 0.0, 0.2]
    toward = [0, 0, -1]
    i2c = "mxp"                     # a REV Color Sensor V3 on the "onboard" or "mxp" I2C port

    [[sensor]]
    name = "front_cam"
    kind = "camera"                 # which of the range's AprilTags it sees (sensors/camera.py)
    body = "drive.chassis"
    at = [0.30, 0.0, 0.25]
    toward = [1, 0, 0.17]           # pitched up about 10 deg; not straight up or down (up is the body's z)
    hfov_deg = 70                   # its fields of view (degrees, each more than 0 and less than 180)
    vfov_deg = 50
    max_range = 5.0                 # m (or max_range_in): for 6.5 in tags; a range's apriltag_size_in scales it
    max_angle_deg = 60              # a tag seen more edge-on than this isn't detected (more than 0, at most 90)
    fps = 30                        # how often it looks, in frames per second of sim time (more than 0, at most 1000)

Each mistake is a SensorError naming the sensor and the key. The body is checked against the robot's bodies when the
world is built (sensors/runtime.py), since only the model knows them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .. import i18n

NAME_RE = re.compile(r"[A-Za-z0-9_]+$")     # as a part's name: it names the chart's sensor.<name> signal
KINDS = ("beam", "distance", "color", "camera")
SEES = ("pieces", "anything")
I2C_PORTS = ("onboard", "mxp")
I2C_PORT_NUMBERS = {"onboard": 0, "mxp": 1}     # WPILib's I2C.Port: kOnboard 0, kMXP 1
IN = 0.0254                     # m per inch
DEFAULT_MAX_RANGE = {"distance": 2.0, "color": 0.10,    # m (spec C3; a REV Color Sensor V3 sees about 10 cm)
                     "camera": 5.0}  # estimate: 6.5 in tags at 1280x800
DEFAULT_HFOV_DEG = 70           # estimate: an OV9281 with its stock lens
DEFAULT_VFOV_DEG = 50           # estimate
DEFAULT_MAX_ANGLE_DEG = 60      # choice: tags seen more edge-on than this aren't detected
DEFAULT_FPS = 30                # choice: how often a camera checks
MAX_FPS = 1000                  # choice: a camera looks at most once a physics step (1 ms)
STRAIGHT_UP_DEG = 1.0           # choice: a camera's toward this close to the body's z has no up to measure from
COLOR_MAX_RANGE = 0.10          # m: a color sensor's longest range (spec C5: nothing past 10 cm)
DEFAULT_VOLTS_PER_M = 0.977     # V/m: a MaxBotix MB1013's 5 V over 5.12 m (spec C3, an estimate)
_COMMON = ("name", "kind", "body")
_LOOK = ("at", "at_in", "toward", "max_range", "max_range_in", "sees")
KEYS = {
    "beam": _COMMON + ("from", "from_in", "to", "to_in", "dio", "active_low"),
    "distance": _COMMON + _LOOK + ("dio", "active_low", "threshold", "threshold_in", "analog", "volts_per_m"),
    "color": _COMMON + _LOOK + ("i2c",),
    "camera": _COMMON + ("at", "at_in", "toward", "hfov_deg", "vfov_deg", "max_range", "max_range_in",
                         "max_angle_deg", "fps"),
}


# A REV Color Sensor V3's SimDevice, as REVLib names it ("REV Color Sensor V3[<port>,82]", 82 its I2C address 0x52)
# and halsim_ws_client relays it: a SimDevice name with no "<type>:" prefix comes as type "SimDevice" (checked live
# with RobotPy 2026.2.2 and robotpy-rev 2026.0.4; its values are >Red, >Green, >Blue, >IR and >Proximity).
COLOR_DEVICE = "REV Color Sensor V3[{port},82]"


class SensorError(ValueError):
    """A [[sensor]] mistake; the message names the sensor and the key."""


@dataclass(frozen=True)
class SensorSpec:
    name: str
    kind: str                               # "beam", "distance", "color" or "camera"
    body: str                               # the robot body, "<instance>.<body>"
    start: tuple[float, float, float]       # m, body frame: a beam's `from`, else `at`
    direction: tuple[float, float, float]   # unit, body frame: from `from` to `to`, else `toward`
    length: float                           # m: a beam's |to - from|, else max_range
    sees: str = "pieces"                    # a beam always sees pieces
    dio: int | None = None
    analog: int | None = None
    i2c: str | None = None
    active_low: bool = True
    volts_per_m: float = DEFAULT_VOLTS_PER_M
    threshold: float | None = None          # m: a distance sensor's on distance (default length / 2); None otherwise
    hfov_deg: float | None = None           # a camera's: its fields of view (degrees), the most edge-on it detects a
    vfov_deg: float | None = None           #   tag at (degrees off the tag's normal) and its frames per second; None
    max_angle_deg: float | None = None      #   for every other kind
    fps: float | None = None


class _Table:
    def __init__(self, table: dict, where: str):
        self.t, self.where = table, where

    def err(self, msg: str) -> SensorError:
        return SensorError(f"{self.where}: {msg}")

    def has(self, key: str) -> bool:
        return key in self.t or f"{key}_in" in self.t

    def _metric(self, key: str):
        """The value of `key` or `key_in` (in meters), and the key it was given as."""
        if key in self.t and f"{key}_in" in self.t:
            raise self.err(i18n.t("has both {key} and {key}_in; give one", key=key))
        if f"{key}_in" in self.t:
            return self.t[f"{key}_in"], f"{key}_in", IN
        return self.t.get(key), key, 1.0

    def point(self, key: str, what: str) -> tuple[float, float, float]:
        if not self.has(key):
            raise self.err(i18n.t("needs {key_setting}: {what}, in meters in the body's frame (or {key}_in, inches)",
                                  key=key, what=what, key_setting="{} = [x, y, z]".format(key)))
        v, given, scale = self._metric(key)
        if not (isinstance(v, list) and len(v) == 3 and all(_finite(c) for c in v)):
            raise self.err(i18n.t("{given} must be [x, y, z], 3 numbers, not {value}", given=given, value=plain(v)))
        return tuple(float(c) * scale for c in v)

    def length(self, key: str, default: float | None) -> float | None:
        if not self.has(key):
            return default
        v, given, scale = self._metric(key)
        if not _finite(v) or v <= 0:
            raise self.err(i18n.t("{given} must be a distance more than 0, not {value}", given=given, value=plain(v)))
        return float(v) * scale

    def channel(self, key: str) -> int | None:
        v = self.t.get(key)
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise self.err(i18n.t("{key} must be a channel number (a whole number, 0 or more), not {value}",
                                  key=key, value=plain(v)))
        return v

    def choice(self, key: str, choices: tuple[str, ...], default: str | None) -> str | None:
        v = self.t.get(key, default)
        if v is not None and v not in choices:
            raise self.err(i18n.t("{key} must be {choices}, not {value}", key=key, value=plain(v),
                                  choices=i18n.t(" or ", context="list separator").join(f'"{c}"' for c in choices)))
        return v


def plain(v) -> str:
    """A config value as a student wrote it in TOML: "laser", [0, 0], true; a table as {...}."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, list):
        return "[" + ", ".join(plain(c) for c in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k} = {plain(c)}" for k, c in v.items()) + "}"
    return f"{v:g}" if isinstance(v, float) else str(v)


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _unit(v, t: _Table, key: str) -> tuple[float, float, float]:
    n = math.sqrt(sum(c * c for c in v))
    if n < 1e-9:
        if key == "toward":
            raise t.err(i18n.t("{key} must be a direction, not [0, 0, 0]", key=key))
        raise t.err(i18n.t("from and to are the same point; a beam needs two ends apart"))
    return tuple(c / n for c in v)


def _angle(t: _Table, key: str, default: float, most: float, most_too: bool) -> float:
    """A camera's angle `key` (degrees): more than 0 and less than `most` (at most `most`, with `most_too`)."""
    v = t.t.get(key, default)
    if not _finite(v) or v <= 0 or v > most or (v == most and not most_too):
        if most_too:
            raise t.err(i18n.t("{key} must be an angle more than 0 and at most {most} degrees, not {value}", key=key,
                               most=f"{most:g}", value=plain(v)))
        raise t.err(i18n.t("{key} must be an angle more than 0 and less than {most} degrees, not {value}", key=key,
                           most=f"{most:g}", value=plain(v)))
    return float(v)


def parse_sensor(table, index: int) -> SensorSpec:
    """One [[sensor]] table (`index` counting from 1, for a sensor with no name yet)."""
    if not isinstance(table, dict):
        raise SensorError(i18n.t("{sensor_table} {index} must be a table, not {value}", index=index,
                                 value=plain(table), sensor_table="[[sensor]]"))
    name = table.get("name")
    if not isinstance(name, str) or not name.strip():
        if name is None:
            raise SensorError(i18n.t("{sensor_table} {index} needs a name: {name_setting}, what the Test tab calls it",
                                     index=index, sensor_table="[[sensor]]", name_setting="name = \"<a name>\""))
        raise SensorError(i18n.t("{sensor_table} {index} needs a name: {name_setting}, what the Test tab calls it "
                                 "(not {value})", index=index, value=plain(name), sensor_table="[[sensor]]",
                                 name_setting="name = \"<a name>\""))
    if not NAME_RE.match(name):
        raise SensorError(i18n.t("{sensor_table} {index}: name must be letters, digits and _ (like a part's name), "
                                 "not {value}", index=index, value=plain(name), sensor_table="[[sensor]]"))
    t = _Table(table, f'[[sensor]] "{name}"')
    kind = table.get("kind")
    if kind is None:
        raise t.err(i18n.t("needs {kind_setting} or {last}", last=f'"{KINDS[-1]}"',
                           kind_setting="kind = " + ", ".join(f'"{k}"' for k in KINDS[:-1])))
    if kind not in KINDS:
        raise t.err(i18n.t("kind must be {kinds} or {last}, not {value}",
                           kinds=", ".join(f'"{k}"' for k in KINDS[:-1]), last=f'"{KINDS[-1]}"', value=plain(kind)))
    unknown = [k for k in table if k not in KEYS[kind]]
    if unknown:
        raise t.err(i18n.t("unknown key '{unknown}' for a {kind} sensor; its keys: {keys}",
                           unknown=unknown[0], kind=kind, keys=', '.join(KEYS[kind])))
    body = table.get("body")
    if not isinstance(body, str) or not body:
        if body is None:
            raise t.err(i18n.t("needs {body_setting}: the robot body it is mounted on",
                               body_setting="body = \"<part>.<body>\""))
        raise t.err(i18n.t("needs {body_setting}: the robot body it is mounted on (not {value})", value=plain(body),
                           body_setting="body = \"<part>.<body>\""))
    kw: dict = {"name": name, "kind": kind, "body": body}
    if kind == "beam":
        a, b = t.point("from", i18n.t("one end of the beam")), t.point("to", i18n.t("the beam's other end"))
        diff = tuple(q - p for p, q in zip(a, b))
        kw.update(start=a, direction=_unit(diff, t, "from"), length=math.sqrt(sum(c * c for c in diff)))
    else:
        at = t.point("at", i18n.t("where it looks from"))
        if "toward" not in table:
            raise t.err(i18n.t("needs {toward_setting}: the direction it looks, in the body's frame",
                               toward_setting="toward = [x, y, z]"))
        toward = table["toward"]
        if not (isinstance(toward, list) and len(toward) == 3 and all(_finite(c) for c in toward)):
            raise t.err(i18n.t("{key} must be a direction [x, y, z], 3 numbers, not {value}", key="toward",
                               value=plain(toward)))
        reach = t.length("max_range", DEFAULT_MAX_RANGE[kind])
        if kind == "color" and reach > COLOR_MAX_RANGE + 1e-12:
            raise t.err(i18n.t("max_range is {reach} m: a color sensor sees 10 cm at most ({max_range_setting})",
                               reach=f"{reach:g}", max_range_setting="max_range = 0.10"))
        direction = _unit(toward, t, "toward")
        if kind == "camera":
            if abs(direction[2]) > math.cos(math.radians(STRAIGHT_UP_DEG)):
                raise t.err(i18n.t("{key} is {value}: a camera can't look straight up or down: up is the body's z",
                                   key="toward", value=plain(toward)))
            kw.update(hfov_deg=_angle(t, "hfov_deg", DEFAULT_HFOV_DEG, 180.0, False),
                      vfov_deg=_angle(t, "vfov_deg", DEFAULT_VFOV_DEG, 180.0, False),
                      max_angle_deg=_angle(t, "max_angle_deg", DEFAULT_MAX_ANGLE_DEG, 90.0, True))
            fps = table.get("fps", DEFAULT_FPS)
            if not _finite(fps) or not 0 < fps <= MAX_FPS:
                raise t.err(i18n.t("fps must be a number more than 0 and at most {most}, not {value}", most=MAX_FPS,
                                   value=plain(fps)))
            kw["fps"] = float(fps)
        kw.update(start=at, direction=direction, length=reach)
        if kind != "camera":
            kw["sees"] = t.choice("sees", SEES, "pieces")
    kw["dio"] = t.channel("dio")
    if "active_low" in table:
        if not isinstance(table["active_low"], bool):
            raise t.err(i18n.t("active_low must be true or false, not {value}", value=plain(table['active_low'])))
        if kw["dio"] is None:
            raise t.err(i18n.t("active_low goes with dio: give {dio_setting} too, or leave active_low out",
                               dio_setting="dio = <channel>"))
        kw["active_low"] = table["active_low"]
    if kind == "distance":
        kw["analog"] = t.channel("analog")
        if "volts_per_m" in table:
            v = table["volts_per_m"]
            if not _finite(v) or v <= 0:
                raise t.err(i18n.t("volts_per_m must be a number more than 0, not {value}", value=plain(v)))
            if kw["analog"] is None:
                raise t.err(i18n.t("volts_per_m goes with analog: give {analog_setting} too, or leave volts_per_m out",
                                   analog_setting="analog = <channel>"))
            kw["volts_per_m"] = float(v)
        threshold = t.length("threshold", kw["length"] / 2.0)
        if threshold > kw["length"]:
            raise t.err(i18n.t("threshold ({threshold} m) is past max_range ({length} m); make it shorter",
                               threshold=f"{threshold:g}", length=f"{kw['length']:g}"))
        kw["threshold"] = threshold
    if kind == "color":
        kw["i2c"] = t.choice("i2c", I2C_PORTS, None)
    return SensorSpec(**kw)


def parse_sensors(tables) -> list[SensorSpec]:
    """The config's [[sensor]] list, checked."""
    if not isinstance(tables, list):
        raise SensorError(i18n.t("sensor must be {sensor_table} tables, one per sensor", sensor_table="[[sensor]]"))
    specs: list[SensorSpec] = []
    for i, table in enumerate(tables, 1):
        s = parse_sensor(table, i)
        if any(o.name == s.name for o in specs):
            raise SensorError(i18n.t("two sensors named \"{name}\"; give each {sensor_table} its own name",
                                     name=s.name, sensor_table="[[sensor]]"))
        specs.append(s)
    return specs


def color_device(i2c: str) -> tuple[str, str]:
    """The (type, device) of a REV Color Sensor V3 on I2C port `i2c` ("onboard" or "mxp") over HALSim WebSocket."""
    return "SimDevice", COLOR_DEVICE.format(port=I2C_PORT_NUMBERS[i2c])


@dataclass(frozen=True)
class SensorDevice:
    """A device a sensor gives the robot program, for World's duplicate-device check."""
    device_key: tuple[str, str]     # (type, device) over HALSim WebSocket: ("DIO", "0"), ("AI", "1"), a color sensor
    label: str                      # what the check's message calls it: [[sensor]] "<name>"


def sensor_devices(specs: list[SensorSpec]) -> list[SensorDevice]:
    """Every DIO, AI and color sensor the sensors give the robot program, in the config's order."""
    out = []
    for s in specs:
        label = f'[[sensor]] "{s.name}"'
        if s.analog is not None:
            out.append(SensorDevice(("AI", str(s.analog)), label))
        if s.dio is not None:
            out.append(SensorDevice(("DIO", str(s.dio)), label))
        if s.i2c is not None:
            out.append(SensorDevice(color_device(s.i2c), label))
    return out
