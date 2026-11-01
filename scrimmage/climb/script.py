"""A climb script: the steps `climb run` plays (docs/climbing.md, "Scripts").

A script is TOML: an optional `goal` (a level of the range's `[climb] levels`), optional `[limits]` and a list of
`[[step]]`s. Each step, in order:
- sets drives, `drive = { joint = "<joint>", to = <position> }` (a position loop) or `{ joint, voltage = <V> }`, or a
  list of them; a drive holds until a later step drives the same joint;
- optionally sets the latch release motors, `release = <output>` (-1 to 1, every release motor) or
  `release = { joint = "<latch joint>", output = <output> }`; it holds until changed too;
- then waits for one condition, `wait = { ... }`:
  - `time = <s>`;
  - `joint_at = { joint, value, tol }` (tol in the joint's units, default JOINT_TOL);
  - `swing_phase = "forward_zero" | "backward_zero" | "forward_peak" | "backward_peak"`: the swing crossing straight
    under the bar heading forward (the robot's +x) or backward, or reaching its forward or backward extreme;
  - `hook_contact = "<bar>"`: a hook touching that bar (a bar or switch element of the range), or
    `hook_contact = { bar = "<bar>", hook = "<part>" }`: one of that climber's hooks (`<part>`, or one hook,
    `<part>.hook<i>`) touching it;
  - `level = "<level>"`: climb.level reaching that level (or a higher one) and holding it for 0.1 s
    (run.LEVEL_STABLE_S: a hook bouncing on a bar flickers it);
  with no wait the step ends at once;
- `timeout` (s, default DEFAULT_TIMEOUT): a wait that hasn't happened by then ends the run, "timed out at step N".

Joints are named as World.joint_states() names them (`<part>.<joint>`, e.g. `mid.stage1`, `high.pivot`). Units are
the joint's: m for a slide, rad for a hinge, unless the drive or wait gives `to_deg` / `value_deg` / `tol_deg`.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TIMEOUT = 10.0       # s: a step's wait gives up after this long unless the step sets `timeout`
JOINT_TOL = 0.005            # m or rad: joint_at's default tolerance (5 mm, 0.3 deg)
SWING_PHASES = ("forward_zero", "backward_zero", "forward_peak", "backward_peak")
WAIT_KINDS = ("time", "joint_at", "swing_phase", "hook_contact", "level")
# The 2022 robot limits, the defaults for [limits] (manual section 7.1).
G107_EXTENSION = 16 * 0.0254  # m: source: G107, at most 16 in beyond the frame perimeter
G106_HEIGHT = 66 * 0.0254     # m: source: G106, at most 5 ft 6 in tall while in the HANGAR ZONE
# A robot geom other than a hook pressing on a bar this hard has hit it (a tube or the frame swinging into the next
# rung). Estimate: about a third of a 60 kg robot's weight; a brush while reaching past a bar is a few tens of N.
HIT_FORCE = 200.0             # N


class ScriptError(ValueError):
    pass


@dataclass(frozen=True)
class Drive:
    joint: str
    to: float | None = None          # a position loop's target (m or rad)
    voltage: float | None = None     # or a constant voltage
    max_voltage: float | None = None  # the position loop's output cap (V); None: the bus


@dataclass(frozen=True)
class Wait:
    kind: str                        # one of WAIT_KINDS
    time: float = 0.0
    joint: str = ""
    value: float = 0.0
    tol: float = JOINT_TOL
    name: str = ""                   # swing phase, bar or level
    hook: str = ""                   # hook_contact: the climber (or hook) that must touch, "" for any

    def describe(self) -> str:
        if self.kind == "time":
            return f"time {self.time:g} s"
        if self.kind == "joint_at":
            return f"{self.joint} at {self.value:g} +- {self.tol:g}"
        if self.kind == "hook_contact" and self.hook:
            return f"hook contact {self.hook} on {self.name}"
        return f"{self.kind.replace('_', ' ')} {self.name}"


@dataclass(frozen=True)
class Step:
    index: int                       # 1-based
    name: str
    drives: tuple[Drive, ...] = ()
    release: tuple[tuple[str | None, float], ...] = ()     # (latch joint or None for every one, output)
    wait: Wait | None = None
    timeout: float = DEFAULT_TIMEOUT


@dataclass(frozen=True)
class Limits:
    extension: float = G107_EXTENSION    # m beyond the frame perimeter
    height: float = G106_HEIGHT          # m tall
    hit_force: float = HIT_FORCE         # N


@dataclass(frozen=True)
class Script:
    steps: tuple[Step, ...]
    goal: str | None = None
    limits: Limits = field(default_factory=Limits)
    path: str = ""


def _num(v, where: str, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ScriptError(f"{where}: {what} must be a number, not {v!r}")
    return float(v)


def _keys(t: dict, allowed: tuple, where: str) -> None:
    extra = [k for k in t if k not in allowed]
    if extra:
        raise ScriptError(f"{where}: unknown key {extra[0]!r}; allowed: {', '.join(allowed)}")


def _angle(t: dict, key: str, where: str) -> float | None:
    """t[key], or t[key_deg] in radians; None if neither."""
    if key in t and f"{key}_deg" in t:
        raise ScriptError(f"{where}: give {key} or {key}_deg, not both")
    if f"{key}_deg" in t:
        return math.radians(_num(t[f"{key}_deg"], where, f"{key}_deg"))
    if key in t:
        return _num(t[key], where, key)
    return None


def _joint(t: dict, where: str) -> str:
    j = t.get("joint")
    if not isinstance(j, str) or not j:
        raise ScriptError(f"{where}: needs joint = \"<part>.<joint>\" (a name from the robot's joints)")
    return j


def _drive(t, where: str) -> Drive:
    if not isinstance(t, dict):
        raise ScriptError(f"{where}: a drive is a table, {{ joint = \"...\", to = <position> }} or "
                          f"{{ joint = \"...\", voltage = <V> }}")
    _keys(t, ("joint", "to", "to_deg", "voltage", "max_voltage"), where)
    joint = _joint(t, where)
    to = _angle(t, "to", where)
    volts = _num(t["voltage"], where, "voltage") if "voltage" in t else None
    if (to is None) == (volts is None):
        raise ScriptError(f"{where}: give the drive one of to (a position), to_deg or voltage")
    cap = None
    if "max_voltage" in t:
        if to is None:
            raise ScriptError(f"{where}: max_voltage caps a position loop (to): remove it from a voltage drive")
        cap = _num(t["max_voltage"], where, "max_voltage")
        if cap <= 0.0:
            raise ScriptError(f"{where}: max_voltage must be above 0 V")
    return Drive(joint, to, volts, cap)


def _release(v, where: str) -> tuple[tuple[str | None, float], ...]:
    def output(x) -> float:
        out = _num(x, where, "the release output")
        if not -1.0 <= out <= 1.0:
            raise ScriptError(f"{where}: the release output must be -1 to 1, not {out:g}")
        return out
    if isinstance(v, dict):
        _keys(v, ("joint", "output"), where)
        if "output" not in v:
            raise ScriptError(f"{where}: release = {{ joint = \"<latch joint>\", output = <-1 to 1> }}")
        return ((_joint(v, where), output(v["output"])),)
    return ((None, output(v)),)


def _wait(t, where: str) -> Wait:
    if not isinstance(t, dict) or len(t) != 1 or next(iter(t)) not in WAIT_KINDS:
        raise ScriptError(f"{where}: wait is one of {{ time = <s> }}, {{ joint_at = {{ joint, value, tol }} }}, "
                          f"{{ swing_phase = \"{SWING_PHASES[0]}\" }}, {{ hook_contact = \"<bar>\" }} or "
                          f"{{ level = \"<level>\" }}")
    kind, v = next(iter(t.items()))
    if kind == "time":
        s = _num(v, where, "wait time")
        if s < 0.0:
            raise ScriptError(f"{where}: wait time must be 0 s or more")
        return Wait("time", time=s)
    if kind == "joint_at":
        if not isinstance(v, dict):
            raise ScriptError(f"{where}: joint_at = {{ joint = \"...\", value = <position>, tol = <tolerance> }}")
        _keys(v, ("joint", "value", "value_deg", "tol", "tol_deg"), where)
        value = _angle(v, "value", where)
        if value is None:
            raise ScriptError(f"{where}: joint_at needs value (or value_deg)")
        tol = _angle(v, "tol", where)
        tol = JOINT_TOL if tol is None else tol
        if tol <= 0.0:
            raise ScriptError(f"{where}: joint_at's tol must be above 0")
        return Wait("joint_at", joint=_joint(v, where), value=value, tol=tol)
    if kind == "hook_contact" and isinstance(v, dict):
        _keys(v, ("bar", "hook"), where)
        bar, hook = v.get("bar"), v.get("hook", "")
        if not isinstance(bar, str) or not bar or not isinstance(hook, str):
            raise ScriptError(f"{where}: hook_contact = {{ bar = \"<bar>\", hook = \"<part>\" }}")
        return Wait(kind, name=bar, hook=hook)
    if not isinstance(v, str) or not v:
        raise ScriptError(f"{where}: {kind} names a {'phase' if kind == 'swing_phase' else kind.split('_')[-1]}")
    if kind == "swing_phase" and v not in SWING_PHASES:
        raise ScriptError(f"{where}: swing_phase {v!r} isn't one of {', '.join(SWING_PHASES)}")
    return Wait(kind, name=v)


def _step(i: int, t, where: str) -> Step:
    if not isinstance(t, dict):
        raise ScriptError(f"{where}: a step is a table ([[step]])")
    _keys(t, ("name", "drive", "release", "wait", "timeout"), where)
    name = t.get("name", f"step {i}")
    if not isinstance(name, str):
        raise ScriptError(f"{where}: name must be a string")
    raw = t.get("drive", [])
    drives = tuple(_drive(d, where) for d in (raw if isinstance(raw, list) else [raw]))
    joints = [d.joint for d in drives]
    if len(set(joints)) != len(joints):
        raise ScriptError(f"{where}: drives the same joint twice")
    release = _release(t["release"], where) if "release" in t else ()
    wait = _wait(t["wait"], where) if "wait" in t else None
    timeout = DEFAULT_TIMEOUT
    if "timeout" in t:
        timeout = _num(t["timeout"], where, "timeout")
        if timeout <= 0.0:
            raise ScriptError(f"{where}: timeout must be above 0 s")
    if wait is not None and wait.kind == "time" and wait.time > timeout:
        raise ScriptError(f"{where}: waits {wait.time:g} s but times out after {timeout:g} s: raise timeout")
    return Step(i, name, drives, release, wait, timeout)


def parse_script(raw: dict, path: str = "<script>") -> Script:
    _keys(raw, ("goal", "limits", "step"), path)
    goal = raw.get("goal")
    if goal is not None and (not isinstance(goal, str) or not goal):
        raise ScriptError(f"{path}: goal names a level of the range's [climb] levels, e.g. goal = \"HIGH\"")
    lim = raw.get("limits", {})
    if not isinstance(lim, dict):
        raise ScriptError(f"{path}: [limits] is a table")
    _keys(lim, ("extension_in", "height_in", "hit_force"), f"{path} [limits]")
    limits = Limits(
        extension=_num(lim.get("extension_in", G107_EXTENSION / 0.0254), path, "extension_in") * 0.0254,
        height=_num(lim.get("height_in", G106_HEIGHT / 0.0254), path, "height_in") * 0.0254,
        hit_force=_num(lim.get("hit_force", HIT_FORCE), path, "hit_force"))
    for key, value in (("extension_in", limits.extension), ("height_in", limits.height),
                       ("hit_force", limits.hit_force)):
        if value <= 0:
            raise ScriptError(f"{path} [limits]: {key} must be above 0")
    steps = raw.get("step")
    if not isinstance(steps, list) or not steps:
        raise ScriptError(f"{path}: a climb script needs at least one [[step]]")
    return Script(tuple(_step(i, s, f"{path} step {i}") for i, s in enumerate(steps, start=1)), goal, limits, path)


def load_script(path) -> Script:
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ScriptError(f"{path}: not valid TOML: {e}") from None
    return parse_script(raw, str(path))
