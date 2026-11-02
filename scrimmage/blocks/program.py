"""The test file: a student's blocks as TOML (`<name>.test.toml`).

    [test]
    robot = "climber_box.toml"          # required: a robot config, relative to this file
    hooks = ["climber.hook_A"]          # optional: robot bodies that count as hooks
    hopper = ["feeder.hopper"]          # optional: robot bodies that hold balls (counted by in_hopper)
    time_limit = 60.0                   # optional, s
    chart = ["tilt", "climber.crank"]   # optional: signals shown by default

    [[block]]
    do = "spin"
    motor = "climber.crank"
    volts = 6.0

An optional `[setup]` table sets the field up for the test: ball, seed, station (a human player's settings by name)
and floor_spread (several placements of the robot on the floor).

A `repeat` block holds its blocks as `[[block.body]]` tables (`[[block.body.body]]` one level in). Only the file's
shape is checked here (kinds, types, ranges); names are checked against the robot elsewhere.
"""

from __future__ import annotations

import json
import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..climb.run import SETTLE_S
from ..launcher.inputs import PRESETS
from ..testrange.station import OVERRIDE_KEYS

TIME_LIMIT = 60.0        # s: the whole run ends here
MOVE_TIMEOUT = 5.0       # s: a move gives up after this long
WAIT_TIMEOUT = 10.0      # s: a wait until gives up after this long
DRIVE_TIMEOUT = 10.0     # s: a drive for a distance gives up after this long
DRIVE_VOLTS = 4.0        # V: drive's default
REPEAT_MAX = 20          # a repeat until stops after this many rounds
MAX_VOLTS = 12.0
MAX_TIMES = 100
MAX_DEPTH = 4            # repeats inside repeats, counting the top level
MAX_PLACEMENTS = 25      # the floor spread's placements: the product of its axes' lengths
MAX_SEED = 2147483647
MAX_HOPPER = 1000        # balls: in_hopper's count
MAX_TURN = 720.0         # degrees

UNITS = ("deg", "in", "m")
DO_KINDS = ("spin", "move", "drive", "stop", "wait", "wait_until", "repeat", "expect",
            "start_feeding", "stop_feeding", "feed_one", "turn")
COND_KINDS = ("joint", "touch", "tilt", "piece_in", "hanging_from", "level", "in_hopper", "station_empty",
              "made_it_in")
DRIVE_DIRECTIONS = ("forward", "back", "left", "right")
SPREAD_AXES = ("offset_in", "distance_in", "angle_deg", "rotation_deg")
SETUP_KEYS = ("ball", "seed", "station", "floor_spread")
_STATION_LISTS = ("spread_position", "spread_rotation_deg")


class TestFileError(ValueError):
    __test__ = False     # not a pytest class


@dataclass(frozen=True)
class Cond:
    kind: str
    args: dict


@dataclass(frozen=True)
class Block:
    do: str
    args: dict = field(default_factory=dict)
    body: list = field(default_factory=list)


@dataclass
class Program:
    path: Path
    robot: Path
    hooks: list = field(default_factory=list)
    time_limit: float = TIME_LIMIT
    chart: list | None = None
    blocks: list = field(default_factory=list)
    setup: dict = field(default_factory=dict)
    hopper: list = field(default_factory=list)


# ---- reading --------------------------------------------------------------------------------------------------------

def _place(trail: tuple) -> str:
    """trail: 1-based block numbers, outermost first."""
    s = f"block {trail[-1]}"
    for n in reversed(trail[:-1]):
        s += f" inside block {n}"
    return s + (" (repeat)" if len(trail) > 1 else "")


def _num(v, where: str, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise TestFileError(f"{where}: {what} must be a number, not {v!r}")
    return float(v)


def _time(v, where: str, what: str) -> float:
    x = _num(v, where, what)
    if x < 0:
        raise TestFileError(f"{where}: {what} can't be negative")
    return x


def _volts(v, where: str, what: str = "volts") -> float:
    x = _num(v, where, what)
    if abs(x) > MAX_VOLTS:
        raise TestFileError(f"{where}: {what} must be between -12 and 12")
    return x


def _text(v, where: str, what: str) -> str:
    if not isinstance(v, str) or not v:
        raise TestFileError(f"{where}: {what} must be a name in quotes")
    return v


def _whole(v, where: str, what: str, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= hi:
        raise TestFileError(f"{where}: {what} must be a whole number from 1 to {hi}")
    return v


def _unit(v, where: str) -> str:
    if v is None:
        raise TestFileError(f'{where}: say the unit ("deg", "in" or "m")')
    if v not in UNITS:
        raise TestFileError(f'{where}: the unit must be "deg", "in" or "m", not {v!r}')
    return v


def _keys(t: dict, allowed: tuple, where: str) -> None:
    extra = [k for k in t if k not in allowed]
    if extra:
        raise TestFileError(f"{where}: {extra[0]!r} isn't something this kind of block uses; "
                            f"it uses {', '.join(allowed)}")


def _one_of(t: dict, names: tuple, where: str) -> str:
    have = [n for n in names if n in t]
    if len(have) != 1:
        raise TestFileError(f"{where}: give exactly one of {' or '.join(names)}")
    return have[0]


def _cond(v, where: str) -> Cond:
    if not isinstance(v, dict) or not v:
        raise TestFileError(f"{where}: a condition is a table like {{ tilt = {{ above = 10 }} }}")
    if len(v) != 1:
        raise TestFileError(f"{where}: one condition per block (this has {', '.join(map(str, v))})")
    kind, t = next(iter(v.items()))
    if kind not in COND_KINDS:
        raise TestFileError(f"{where}: {kind!r} isn't a condition; use {', '.join(COND_KINDS)}")
    if kind in ("hanging_from", "level", "station_empty"):
        key = {"hanging_from": "bar", "level": "level", "station_empty": "station"}[kind]
        return Cond(kind, {key: _text(t, where, kind)})
    if not isinstance(t, dict):
        raise TestFileError(f"{where}: {kind} needs a table with its settings")
    if kind == "joint":
        _keys(t, ("name", "above", "below", "unit"), where)
        side = _one_of(t, ("above", "below"), where)
        return Cond(kind, {"name": _text(t.get("name"), where, "name"), side: _num(t[side], where, side),
                           "unit": _unit(t.get("unit"), where)})
    if kind == "in_hopper":
        _keys(t, ("at_least", "at_most"), where)
        side = _one_of(t, ("at_least", "at_most"), where)
        n = t[side]
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= MAX_HOPPER:
            raise TestFileError(f"{where}: {side} must be a whole number from 0 to {MAX_HOPPER}")
        return Cond(kind, {side: n})
    if kind == "made_it_in":
        _keys(t, ("at_least",), where)
        if "at_least" not in t:
            raise TestFileError(f"{where}: made_it_in needs at_least, a percent")
        pct = _num(t["at_least"], where, "at_least")
        if not 0 <= pct <= 100:
            raise TestFileError(f"{where}: at_least is a percent, from 0 to 100")
        return Cond(kind, {"at_least": pct})
    if kind == "tilt":
        _keys(t, ("above", "below"), where)
        side = _one_of(t, ("above", "below"), where)
        return Cond(kind, {side: _num(t[side], where, side)})
    if kind == "touch":
        _keys(t, ("robot", "field"), where)
        return Cond(kind, {"robot": _text(t.get("robot"), where, "robot"),
                           "field": _text(t.get("field"), where, "field")})
    _keys(t, ("body", "piece"), where)
    return Cond(kind, {"body": _text(t.get("body"), where, "body"),
                       "piece": _text(t.get("piece", "any"), where, "piece")})


def _block(t, trail: tuple) -> Block:
    where = _place(trail)
    if not isinstance(t, dict):
        raise TestFileError(f"{where}: a block is a table with do = \"...\"")
    do = t.get("do")
    if do is None:
        raise TestFileError(f"{where}: says nothing about what to do (do = \"spin\", ...)")
    if do not in DO_KINDS:
        kinds = ", ".join(DO_KINDS)
        raise TestFileError(f'{where}: "{do}" isn\'t a block; use {kinds}')
    if len(trail) > MAX_DEPTH:
        raise TestFileError(f"{where}: blocks go at most {MAX_DEPTH} deep inside each other")
    t = {k: v for k, v in t.items() if k != "do"}
    a: dict = {}
    body: list = []
    if do == "spin":
        _keys(t, ("motor", "volts"), where)
        a = {"motor": _text(t.get("motor"), where, "motor"), "volts": _volts(t.get("volts"), where)}
    elif do == "move":
        _keys(t, ("joint", "to", "unit", "timeout"), where)
        a = {"joint": _text(t.get("joint"), where, "joint"), "to": _num(t.get("to"), where, "to"),
             "unit": _unit(t.get("unit"), where)}
        if "timeout" in t:
            a["timeout"] = _time(t["timeout"], where, "timeout")
    elif do == "drive":
        _keys(t, ("distance", "seconds", "direction", "volts", "timeout"), where)
        k = _one_of(t, ("distance", "seconds"), where)
        a = {k: _time(t[k], where, k) if k == "seconds" else _num(t[k], where, k)}
        if "direction" in t:
            if t["direction"] not in DRIVE_DIRECTIONS:
                raise TestFileError(f"{where}: direction must be one of {', '.join(DRIVE_DIRECTIONS)}, "
                                    f"not {t['direction']!r}")
            if t["direction"] == "back" and k == "distance" and a[k] < 0:
                raise TestFileError(f"{where}: drive back with a minus distance goes forward; "
                                    "use one or the other")
            a["direction"] = t["direction"]
        if "volts" in t:
            a["volts"] = _volts(t["volts"], where)
        if "timeout" in t:
            if k == "seconds":
                raise TestFileError(f"{where}: timeout goes with distance, not seconds")
            a["timeout"] = _time(t["timeout"], where, "timeout")
    elif do == "stop":
        _keys(t, ("motor",), where)
        a = {"motor": _text(t.get("motor"), where, "motor")}
    elif do == "wait":
        _keys(t, ("seconds",), where)
        a = {"seconds": _time(t.get("seconds"), where, "seconds")}
    elif do == "wait_until":
        _keys(t, ("until", "timeout"), where)
        a = {"until": _cond(t.get("until"), where)}
        if "timeout" in t:
            a["timeout"] = _time(t["timeout"], where, "timeout")
    elif do in ("start_feeding", "stop_feeding", "feed_one"):
        _keys(t, ("station", "timeout") if do == "feed_one" else ("station",), where)
        a = {"station": _text(t.get("station"), where, "station")}
        if "timeout" in t:
            a["timeout"] = _time(t["timeout"], where, "timeout")
    elif do == "turn":
        _keys(t, ("degrees", "volts", "timeout"), where)
        deg = _num(t.get("degrees"), where, "degrees")
        if deg == 0:
            raise TestFileError(f"{where}: degrees can't be 0")
        if abs(deg) > MAX_TURN:
            raise TestFileError(f"{where}: degrees must be between -720 and 720")
        a = {"degrees": deg}
        if "volts" in t:
            a["volts"] = _volts(t["volts"], where)
        if "timeout" in t:
            a["timeout"] = _time(t["timeout"], where, "timeout")
    elif do == "repeat":
        _keys(t, ("times", "until", "max", "body"), where)
        k = _one_of(t, ("times", "until"), where)
        if k == "times":
            if "max" in t:
                raise TestFileError(f"{where}: max goes with until, not times")
            a = {"times": _whole(t["times"], where, "times", MAX_TIMES)}
        else:
            a = {"until": _cond(t["until"], where)}
            if "max" in t:
                a["max"] = _whole(t["max"], where, "max", MAX_TIMES)
        raw = t.get("body", [])
        if not isinstance(raw, list):
            raise TestFileError(f"{where}: body is a list of blocks ([[block.body]])")
        body = [_block(b, trail + (i,)) for i, b in enumerate(raw, 1)]
    else:   # expect
        _keys(t, ("that", "throughout"), where)
        a = {"that": _cond(t.get("that"), where)}
        if "throughout" in t:
            if not isinstance(t["throughout"], bool):
                raise TestFileError(f"{where}: throughout must be true or false")
            a["throughout"] = t["throughout"]
    return Block(do, a, body)


def _strings(v, where: str, what: str) -> list:
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise TestFileError(f"{where}: {what} must be a list of names in quotes")
    return list(v)


def _setup(raw: dict) -> dict:
    """The `[setup]` table, checked: ball, seed, station settings and the floor spread. What the field has (which
    stations, whether it has a range at all) is checked when the world is built."""
    where = "[setup]"
    extra = [k for k in raw if k not in SETUP_KEYS]
    if extra:
        raise TestFileError(f'{where}: "{extra[0]}" isn\'t a setting; settings are {", ".join(SETUP_KEYS)}')
    out: dict = {}
    if "ball" in raw:
        ball = raw["ball"]
        if not isinstance(ball, str) or ball not in PRESETS["ball"]:
            raise TestFileError(f'{where}: ball {ball!r} isn\'t a ball; balls: {", ".join(PRESETS["ball"])}')
        out["ball"] = ball
    if "seed" in raw:
        seed = raw["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= MAX_SEED:
            raise TestFileError(f"{where}: seed must be a whole number from 0 to {MAX_SEED}, not {seed!r}")
        out["seed"] = seed
    if "station" in raw:
        stations = raw["station"]
        if not isinstance(stations, dict):
            raise TestFileError(f"{where}: station is a table of station names, like station = {{ feeder = "
                                "{ interval = 1.0 } }")
        out["station"] = {name: _station_setup(name, t) for name, t in stations.items()}
    if "floor_spread" in raw:
        spread = raw["floor_spread"]
        if not isinstance(spread, dict):
            raise TestFileError(f"{where}: floor_spread is a table like floor_spread = {{ offset_in = [-6, 0, 6] }}")
        out["floor_spread"] = _floor_spread(spread)
        n = placement_count(out)
        if n > MAX_PLACEMENTS:
            raise TestFileError(f"{where}: the floor spread tries {n} placements; at most {MAX_PLACEMENTS}")
    return out


def _station_setup(name: str, t) -> dict:
    where = f"[setup] station {name!r}"
    if not isinstance(t, dict):
        raise TestFileError(f"{where}: needs a table of settings")
    _keys(t, OVERRIDE_KEYS, where)
    out: dict = {}
    for key, v in t.items():
        if key in _STATION_LISTS:
            if not isinstance(v, list) or len(v) != 3:
                raise TestFileError(f"{where}: {key} must be 3 numbers")
            out[key] = [_time(x, where, key) for x in v]
        elif key == "stock":
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise TestFileError(f"{where}: stock must be a whole number, 0 (no limit) or more")
            out[key] = v
        else:
            x = _time(v, where, key)
            if key == "interval" and x <= 0:
                raise TestFileError(f"{where}: interval must be more than 0")
            out[key] = x
    return out


def _floor_spread(spread: dict) -> dict:
    where = "[setup] floor_spread"
    _keys(spread, SPREAD_AXES, where)
    out: dict = {}
    for axis, v in spread.items():
        if not isinstance(v, list) or not v:
            raise TestFileError(f"{where}: {axis} must be a list of numbers, like [-6, 0, 6]")
        out[axis] = [_num(x, where, axis) for x in v]
        if axis == "distance_in" and min(out[axis]) <= 0:
            raise TestFileError(f"{where}: distance_in must be more than 0")
    return out


def placement_count(setup: dict) -> int:
    """How many placements the setup's floor spread tries (1 without one): the product of its axes' lengths."""
    n = 1
    for v in setup.get("floor_spread", {}).values():
        n *= len(v)
    return n


def _program(data: dict, path: Path, head: str = "[test]") -> Program:
    test = data.get("test")
    if not isinstance(test, dict) or "robot" not in test:
        raise TestFileError(f'{head} needs robot = "<robot file>.toml", which says which robot to test'
                            if head == "[test]" else "the test needs a robot file, which says which robot to test")
    _keys(test, ("robot", "hooks", "hopper", "time_limit", "chart"), head)
    robot = test["robot"]
    if not isinstance(robot, str) or not robot:
        raise TestFileError(f'{head} robot must be the robot file\'s name in quotes, like "my_robot.toml"')
    hooks = _strings(test.get("hooks", []), head, "hooks")
    hopper = _strings(test.get("hopper", []), head, "hopper")
    chart = _strings(test["chart"], head, "chart") if "chart" in test else None
    limit = _time(test.get("time_limit", TIME_LIMIT), head, "time_limit")
    if limit <= SETTLE_S:
        raise TestFileError(f"{head}: the time limit must be longer than {SETTLE_S:g} s "
                            "(the robot settles that long before the first block)")
    raw = data.get("block", [])
    if not isinstance(raw, list):
        raise TestFileError("blocks are written as [[block]] tables")
    setup = data.get("setup", {})
    if not isinstance(setup, dict):
        raise TestFileError("[setup] must be a table")
    return Program(Path(path), Path(robot), hooks, limit, chart,
                   [_block(b, (i,)) for i, b in enumerate(raw, 1)], _setup(setup), hopper)


def loads(text: str, path: Path) -> Program:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise TestFileError(f"{Path(path).name} isn't valid TOML: {e}") from None
    return _program(data, Path(path))


def load(path) -> Program:
    path = Path(path)
    return loads(path.read_text(encoding="utf-8"), path)


# ---- the browser's JSON shape ---------------------------------------------------------------------------------------

def _cond_json(c: Cond):
    if c.kind in ("hanging_from", "level", "station_empty"):
        return {c.kind: next(iter(c.args.values()))}
    return {c.kind: dict(c.args)}


def block_json(b: Block) -> dict:
    """One block as the test's JSON has it (to_json's "blocks" entries)."""
    d: dict = {"do": b.do}
    for k, v in b.args.items():
        d[k] = _cond_json(v) if isinstance(v, Cond) else v
    if b.do == "repeat":
        d["body"] = [block_json(x) for x in b.body]
    return d


def to_json(p: Program) -> dict:
    return {"robot": p.robot.as_posix(), "hooks": list(p.hooks), "hopper": list(p.hopper),
            "time_limit": p.time_limit,
            "chart": None if p.chart is None else list(p.chart), "setup": dict(p.setup),
            "blocks": [block_json(b) for b in p.blocks]}


def from_json(d: dict, path: Path) -> Program:
    if not isinstance(d, dict):
        raise TestFileError("the test is a table")
    extra = [k for k in d if k not in ("robot", "hooks", "hopper", "time_limit", "chart", "setup", "blocks")]
    if extra:
        raise TestFileError(f"the test has something it doesn't use: {extra[0]!r}")
    test = {k: d[k] for k in ("robot", "hooks", "hopper", "time_limit", "chart") if d.get(k) is not None}
    data = {"test": test, "block": d.get("blocks", []), "setup": d.get("setup") or {}}
    return _program(data, Path(path), "the test")


# ---- writing --------------------------------------------------------------------------------------------------------

def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, Cond):
        return _value(_cond_json(v))
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(x)}" for k, x in v.items()) + " }" if v else "{}"
    raise TypeError(f"can't write {v!r} to TOML")


def _string(v: str) -> str:
    # json escapes quotes, backslashes and C0 controls; TOML also forbids DEL
    return json.dumps(v, ensure_ascii=False).replace("\x7f", "\\u007f")


def _key(k: str) -> str:
    return k if k.replace("_", "").replace("-", "").isalnum() else _string(k)


def _write_block(b: Block, header: str, out: list) -> None:
    out += ["", f"[[{header}]]", f"do = {_value(b.do)}"]
    out += [f"{_key(k)} = {_value(v)}" for k, v in b.args.items()]
    for x in b.body:
        _write_block(x, header + ".body", out)


def dumps(p: Program) -> str:
    out = ["[test]", f"robot = {_value(p.robot.as_posix())}"]
    if p.hooks:
        out.append(f"hooks = {_value(p.hooks)}")
    if p.hopper:
        out.append(f"hopper = {_value(p.hopper)}")
    if p.time_limit != TIME_LIMIT:
        out.append(f"time_limit = {_value(p.time_limit)}")
    if p.chart is not None:
        out.append(f"chart = {_value(p.chart)}")
    if p.setup:
        out += ["", "[setup]"] + [f"{_key(k)} = {_value(v)}" for k, v in p.setup.items()]
    for b in p.blocks:
        _write_block(b, "block", out)
    return "\n".join(out) + "\n"
