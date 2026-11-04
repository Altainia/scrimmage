"""A robot config's `[[sensor]]` tables, checked: beam breaks, distance sensors and color sensors.

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

Each mistake is a SensorError naming the sensor and the key. The body is checked against the robot's bodies when the
world is built (sensors/runtime.py), since only the model knows them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

NAME_RE = re.compile(r"[A-Za-z0-9_]+$")     # as a part's name: it names the chart's sensor.<name> signal
KINDS = ("beam", "distance", "color")
SEES = ("pieces", "anything")
I2C_PORTS = ("onboard", "mxp")
I2C_PORT_NUMBERS = {"onboard": 0, "mxp": 1}     # WPILib's I2C.Port: kOnboard 0, kMXP 1
IN = 0.0254                     # m per inch
DEFAULT_MAX_RANGE = {"distance": 2.0, "color": 0.10}    # m (spec C3; a REV Color Sensor V3 sees about 10 cm)
COLOR_MAX_RANGE = 0.10          # m: a color sensor's longest range (spec C5: nothing past 10 cm)
DEFAULT_VOLTS_PER_M = 0.977     # V/m: a MaxBotix MB1013's 5 V over 5.12 m (spec C3, an estimate)
_COMMON = ("name", "kind", "body")
_LOOK = ("at", "at_in", "toward", "max_range", "max_range_in", "sees")
KEYS = {
    "beam": _COMMON + ("from", "from_in", "to", "to_in", "dio", "active_low"),
    "distance": _COMMON + _LOOK + ("dio", "active_low", "threshold", "threshold_in", "analog", "volts_per_m"),
    "color": _COMMON + _LOOK + ("i2c",),
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
    kind: str                               # "beam", "distance" or "color"
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
            raise self.err(f"has both {key} and {key}_in; give one")
        if f"{key}_in" in self.t:
            return self.t[f"{key}_in"], f"{key}_in", IN
        return self.t.get(key), key, 1.0

    def point(self, key: str, what: str) -> tuple[float, float, float]:
        if not self.has(key):
            raise self.err(f"needs {key} = [x, y, z]: {what}, in meters in the body's frame (or {key}_in, inches)")
        v, given, scale = self._metric(key)
        if not (isinstance(v, list) and len(v) == 3 and all(_finite(c) for c in v)):
            raise self.err(f"{given} must be [x, y, z], 3 numbers, not {plain(v)}")
        return tuple(float(c) * scale for c in v)

    def length(self, key: str, default: float | None) -> float | None:
        if not self.has(key):
            return default
        v, given, scale = self._metric(key)
        if not _finite(v) or v <= 0:
            raise self.err(f"{given} must be a distance more than 0, not {plain(v)}")
        return float(v) * scale

    def channel(self, key: str) -> int | None:
        v = self.t.get(key)
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise self.err(f"{key} must be a channel number (a whole number, 0 or more), not {plain(v)}")
        return v

    def choice(self, key: str, choices: tuple[str, ...], default: str | None) -> str | None:
        v = self.t.get(key, default)
        if v is not None and v not in choices:
            raise self.err(f"{key} must be " + " or ".join(f'"{c}"' for c in choices) + f", not {plain(v)}")
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
        raise t.err(f"{key} must be a direction, not [0, 0, 0]" if key == "toward" else
                    "from and to are the same point; a beam needs two ends apart")
    return tuple(c / n for c in v)


def parse_sensor(table, index: int) -> SensorSpec:
    """One [[sensor]] table (`index` counting from 1, for a sensor with no name yet)."""
    if not isinstance(table, dict):
        raise SensorError(f"[[sensor]] {index} must be a table, not {plain(table)}")
    name = table.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SensorError(f"[[sensor]] {index} needs a name: name = \"<a name>\", what the Test tab calls it"
                          + ("" if name is None else f" (not {plain(name)})"))
    if not NAME_RE.match(name):
        raise SensorError(f"[[sensor]] {index}: name must be letters, digits and _ (like a part's name), "
                          f"not {plain(name)}")
    t = _Table(table, f'[[sensor]] "{name}"')
    kind = table.get("kind")
    if kind is None:
        raise t.err('needs kind = "beam", "distance" or "color"')
    if kind not in KINDS:
        raise t.err("kind must be " + ", ".join(f'"{k}"' for k in KINDS[:-1]) + f' or "{KINDS[-1]}", not {plain(kind)}')
    unknown = [k for k in table if k not in KEYS[kind]]
    if unknown:
        raise t.err(f"unknown key '{unknown[0]}' for a {kind} sensor; its keys: {', '.join(KEYS[kind])}")
    body = table.get("body")
    if not isinstance(body, str) or not body:
        raise t.err('needs body = "<part>.<body>": the robot body it is mounted on'
                    + ("" if body is None else f" (not {plain(body)})"))
    kw: dict = {"name": name, "kind": kind, "body": body}
    if kind == "beam":
        a, b = t.point("from", "one end of the beam"), t.point("to", "the beam's other end")
        diff = tuple(q - p for p, q in zip(a, b))
        kw.update(start=a, direction=_unit(diff, t, "from"), length=math.sqrt(sum(c * c for c in diff)))
    else:
        at = t.point("at", "where it looks from")
        if "toward" not in table:
            raise t.err("needs toward = [x, y, z]: the direction it looks, in the body's frame")
        toward = table["toward"]
        if not (isinstance(toward, list) and len(toward) == 3 and all(_finite(c) for c in toward)):
            raise t.err(f"toward must be a direction [x, y, z], 3 numbers, not {plain(toward)}")
        reach = t.length("max_range", DEFAULT_MAX_RANGE[kind])
        if kind == "color" and reach > COLOR_MAX_RANGE + 1e-12:
            raise t.err(f"max_range is {reach:g} m: a color sensor sees 10 cm at most (max_range = 0.10)")
        kw.update(start=at, direction=_unit(toward, t, "toward"), length=reach, sees=t.choice("sees", SEES, "pieces"))
    kw["dio"] = t.channel("dio")
    if "active_low" in table:
        if not isinstance(table["active_low"], bool):
            raise t.err(f"active_low must be true or false, not {plain(table['active_low'])}")
        if kw["dio"] is None:
            raise t.err("active_low goes with dio: give dio = <channel> too, or leave active_low out")
        kw["active_low"] = table["active_low"]
    if kind == "distance":
        kw["analog"] = t.channel("analog")
        if "volts_per_m" in table:
            v = table["volts_per_m"]
            if not _finite(v) or v <= 0:
                raise t.err(f"volts_per_m must be a number more than 0, not {plain(v)}")
            if kw["analog"] is None:
                raise t.err("volts_per_m goes with analog: give analog = <channel> too, or leave volts_per_m out")
            kw["volts_per_m"] = float(v)
        threshold = t.length("threshold", kw["length"] / 2.0)
        if threshold > kw["length"]:
            raise t.err(f"threshold ({threshold:g} m) is past max_range ({kw['length']:g} m); make it shorter")
        kw["threshold"] = threshold
    if kind == "color":
        kw["i2c"] = t.choice("i2c", I2C_PORTS, None)
    return SensorSpec(**kw)


def parse_sensors(tables) -> list[SensorSpec]:
    """The config's [[sensor]] list, checked."""
    if not isinstance(tables, list):
        raise SensorError("sensor must be [[sensor]] tables, one per sensor")
    specs: list[SensorSpec] = []
    for i, table in enumerate(tables, 1):
        s = parse_sensor(table, i)
        if any(o.name == s.name for o in specs):
            raise SensorError(f'two sensors named "{s.name}"; give each [[sensor]] its own name')
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
