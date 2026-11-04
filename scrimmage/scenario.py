"""Scenario files: a sequence of steps (DS mode, duration, joysticks, game data) with checks and screenshots."""

from __future__ import annotations

import copy
import dataclasses
import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import mujoco

from .checks import CHECK_KINDS, ScenarioError, build_check
from .driverstation import MODES, MatchSettings, check_game_data
from .lockstep import parse_speed
from .render import VIEWS_HELP, check_camera, check_sheet_size, check_size
from .teamfolders import TeamFolderError, expand
from .tracks import Track, TrackError, load_track

TOP_KEYS = {"config", "description", "speed", "require_lockstep", "range", "robot_env", "match", "step"}
RANGE_KEYS = ("file", "start", "preload", "floor_balls")   # a scenario's [range]: the config's, for this run
STEP_KEYS = {"name", "mode", "duration", "game_data", "axes", "buttons", "povs", "check", "screenshot",
             "contact_sheet", "inputs"}
SHOT_KEYS = {"view", "at", "width", "height", "camera"}
SHEET_KEYS = {"frames", "view", "width", "height"}

__all__ = ["ScenarioError", "Shot", "SheetSpec", "Step", "Scenario", "load_scenario", "validate", "config_for",
           "match_for", "range_file"]


@dataclass
class Shot:
    view: str = "follow"
    at: float | str = "end"
    width: int = 960
    height: int = 600
    camera: dict | None = None


@dataclass
class SheetSpec:
    frames: int = 8
    view: str = "follow"
    width: int = 480
    height: int = 300


@dataclass
class Step:
    name: str
    mode: str
    duration: float
    axes: dict[int, list[float]] = field(default_factory=dict)
    buttons: dict[int, list[bool]] = field(default_factory=dict)
    povs: dict[int, list[int]] = field(default_factory=dict)
    checks: list[dict] = field(default_factory=list)
    screenshots: list[Shot] = field(default_factory=list)
    contact_sheet: SheetSpec | None = None
    inputs: Track | None = None
    game_data: str | None = None    # sent when the step starts; None leaves the robot's as it was


@dataclass
class Scenario:
    path: Path
    config_path: Path
    description: str
    speed: float | None
    require_lockstep: bool
    steps: list[Step]
    range: dict | None = None       # [range] keys over the config's for this run (file made absolute unless built in)
    robot_env: dict[str, str] = field(default_factory=dict)   # merged into the robot program's environment
    match: dict = field(default_factory=dict)   # [match] keys over the config's for this run (checked at load)


def _keys(d: dict, allowed: set[str], where: str) -> None:
    for k in d:
        if k not in allowed:
            raise ScenarioError(f"{where}: unknown key {k!r}; allowed: {', '.join(sorted(allowed))}")


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _ports(v, where: str, what: str) -> dict[int, list]:
    if not isinstance(v, dict):
        raise ScenarioError(f"{where}: {what} must be a table like {what} = {{ 0 = [...] }}")
    out = {}
    for k, vals in v.items():
        if not str(k).isdigit() or not 0 <= int(k) <= 5 or not isinstance(vals, list):
            raise ScenarioError(f"{where}: {what} keys are joystick ports 0-5 with a list of values")
        for i, x in enumerate(vals):
            at = f"{where}: {what} port {k}, index {i}"
            if what == "axes" and not (_is_num(x) and -1 <= x <= 1):
                raise ScenarioError(f"{at} must be a number between -1 and 1, not {x!r}")
            if what == "buttons" and not isinstance(x, bool):
                raise ScenarioError(f"{at} must be true or false, not {x!r}")
            if what == "povs" and not (isinstance(x, int) and not isinstance(x, bool) and (x == -1 or 0 <= x <= 359)):
                raise ScenarioError(f"{at} must be a whole number: -1 or 0-359 degrees, not {x!r}")
        out[int(k)] = vals
    return out


def _int(d: dict, key: str, default: int, where: str) -> int:
    v = d.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        raise ScenarioError(f"{where}: {key} must be a positive whole number")
    return v


def _size(width: int, height: int, where: str) -> None:
    for name, v in (("width", width), ("height", height)):
        try:
            check_size(name, v)
        except ValueError as e:
            raise ScenarioError(f"{where}: {e}") from None


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path).resolve()
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise ScenarioError(f"scenario file not found: {path}") from None
    except OSError as e:
        raise ScenarioError(f"can't read scenario file {path}: {e.strerror or e}") from None
    except UnicodeDecodeError as e:
        raise ScenarioError(f"{path} isn't a text file: {e}") from None
    return load_scenario_text(text, path)


def load_scenario_text(text: str, path: str | Path) -> Scenario:
    """The scenario parsed from TOML text; ``path`` (need not exist) is where it lives, for the relative config."""
    path = Path(path).resolve()
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ScenarioError(f"{path}: {e}") from None
    return load_scenario_raw(raw, path)


def load_scenario_raw(raw: dict, path: str | Path) -> Scenario:
    """The scenario from already-parsed TOML (as load_scenario_text, without the parsing)."""
    path = Path(path).resolve()
    where = path.name
    _keys(raw, TOP_KEYS, where)
    if not isinstance(raw.get("config"), str):
        raise ScenarioError(f"{where}: config = \"<robot config .toml>\" is required")
    try:
        config_ref = expand(raw["config"])
    except TeamFolderError as e:
        raise ScenarioError(str(e)) from None
    try:
        speed = parse_speed(str(raw.get("speed", "max")))
    except ValueError as e:
        raise ScenarioError(f"{where}: {e}") from None
    req = raw.get("require_lockstep", True)
    if not isinstance(req, bool):
        raise ScenarioError(f"{where}: require_lockstep must be true or false")
    steps_raw = raw.get("step", [])
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ScenarioError(f"{where}: needs at least one [[step]]")
    steps = []
    for i, s in enumerate(steps_raw, 1):
        sw = f"{where}, step {i}"
        if not isinstance(s, dict):
            raise ScenarioError(f"{sw}: each step must be a [[step]] table, not {s!r}")
        _keys(s, STEP_KEYS, sw)
        name = str(s.get("name", f"step {i}"))
        sw = f"{where}, step {i} ({name})" if "name" in s else sw
        if s.get("mode") not in MODES:
            raise ScenarioError(f"{sw}: mode must be one of {', '.join(MODES)}, not {s.get('mode')!r}")
        dur = s.get("duration")
        if not _is_num(dur) or dur <= 0:
            raise ScenarioError(f"{sw}: duration must be a positive number of seconds, not {dur!r}")
        game_data = None
        if "game_data" in s:
            try:
                game_data = check_game_data(s["game_data"], f"{sw}:")
            except ValueError as e:
                raise ScenarioError(str(e)) from None
        track = None
        if "inputs" in s:
            if not isinstance(s["inputs"], str):
                raise ScenarioError(f"{sw}: inputs must be the path of a .csv file")
            if any(k in s for k in ("axes", "buttons", "povs")):
                raise ScenarioError(f"{sw}: inputs can't be combined with axes, buttons or povs in the same step")
            try:
                track = load_track(path.parent / s["inputs"])
            except TrackError as e:
                raise ScenarioError(f"{sw}: inputs: {e}") from None
            if track.samples and track.samples[-1].t > dur + 1e-9:
                raise ScenarioError(f"{sw}: inputs: {track.path.name}: the last sample "
                                    f"(t = {track.samples[-1].t:g}) is after the step's {dur:g} s")
        checks = s.get("check", [])
        if isinstance(checks, dict):
            raise ScenarioError(f"{sw}: use [[step.check]] (double brackets), one per check")
        if not isinstance(checks, list):
            raise ScenarioError(f"{sw}: checks are [[step.check]] tables")
        for j, c in enumerate(checks, 1):
            if not isinstance(c, dict):
                raise ScenarioError(f"{sw}: check {j} must be a table, not {c!r}")
        for j, c in enumerate(checks, 1):  # structure only here; names are checked against the world in validate()
            kinds = [k for k in c]
            if len(kinds) != 1:
                raise ScenarioError(f"{sw}: each [[step.check]] needs exactly one of {', '.join(CHECK_KINDS)}; "
                                    f"got {', '.join(kinds) or 'nothing'}")
            if "by_time" in c:
                _by_time(c["by_time"], dur, f"{sw}, check {j}, by_time")
        shots = []
        shots_raw = s.get("screenshot", [])
        if isinstance(shots_raw, dict):
            raise ScenarioError(f"{sw}: use [[step.screenshot]] (double brackets), one per screenshot")
        if not isinstance(shots_raw, list):
            raise ScenarioError(f"{sw}: screenshots are [[step.screenshot]] tables")
        for j, sh in enumerate(shots_raw, 1):
            shw = f"{sw}, screenshot {j}"
            if not isinstance(sh, dict):
                raise ScenarioError(f"{shw} must be a table, not {sh!r}")
            _keys(sh, SHOT_KEYS, shw)
            at = sh.get("at", "end")
            if at != "end" and (not _is_num(at) or at < 0):
                raise ScenarioError(f"{shw}: at must be \"end\" or seconds into the step")
            if at != "end" and at > dur:
                raise ScenarioError(f"{shw}: at = {at} is beyond the step's {dur:g} s")
            w_, h_ = _int(sh, "width", 960, shw), _int(sh, "height", 600, shw)
            _size(w_, h_, shw)
            shots.append(Shot(str(sh.get("view", "follow")), at, w_, h_, sh.get("camera")))
        sheet = None
        if "contact_sheet" in s:
            cs = s["contact_sheet"]
            csw = f"{sw}, contact_sheet"
            if not isinstance(cs, dict):
                raise ScenarioError(f"{csw} must be a table like [step.contact_sheet], not {cs!r}")
            _keys(cs, SHEET_KEYS, csw)
            sheet = SheetSpec(_int(cs, "frames", 8, csw), str(cs.get("view", "follow")),
                              _int(cs, "width", 480, csw), _int(cs, "height", 300, csw))
            _size(sheet.width, sheet.height, csw)
            if sheet.frames > 16:
                raise ScenarioError(f"{csw}: frames must be 1-16 (contact_sheet frames)")
            try:
                check_sheet_size(sheet.frames, sheet.width, sheet.height)
            except ValueError as e:
                raise ScenarioError(f"{csw}: {e}") from None
        steps.append(Step(name, s["mode"], float(dur), _ports(s.get("axes", {}), sw, "axes"),
                          _ports(s.get("buttons", {}), sw, "buttons"), _ports(s.get("povs", {}), sw, "povs"),
                          checks, shots, sheet, track, game_data))
    return Scenario(path, (path.parent / config_ref).resolve(), str(raw.get("description", "")), speed, req, steps,
                    _range(raw, path, where), _robot_env(raw, where), _match(raw, where))


def _by_time(spec, duration: float, where: str) -> None:
    """A by_time check's t: present, a number, and within the step (the rest is checked against the world)."""
    if not isinstance(spec, dict):
        raise ScenarioError(f"{where}: expected a table like by_time = {{ signal = \"score\", min = 8, t = 15 }}")
    if "t" not in spec:
        raise ScenarioError(f"{where}: by_time needs t, the seconds into the step it must hold by")
    t = spec["t"]
    if not _is_num(t) or t < 0:
        raise ScenarioError(f"{where}: t must be 0 or more seconds into the step, not {t!r}")
    if t > duration + 1e-9:
        raise ScenarioError(f"{where}: t = {t:g} is beyond the step's {duration:g} s")


def _range(raw: dict, path: Path, where: str) -> dict | None:
    """The scenario's [range]: its keys checked for type here (start and preload against the robot when it runs);
    a file other than a built-in range's name is relative to the scenario and made absolute."""
    sec = raw.get("range")
    if sec is None:
        return None
    if not isinstance(sec, dict):
        raise ScenarioError(f"{where}: range must be a [range] table with {', '.join(RANGE_KEYS)}")
    for k in sec:
        if k not in RANGE_KEYS:
            raise ScenarioError(f"{where}: range: {k!r} isn't a [range] key; use {', '.join(RANGE_KEYS)}")
    out = dict(sec)
    if "file" in sec:
        from .testrange.spec import BUILTIN_RANGES
        if not isinstance(sec["file"], str) or not sec["file"]:
            raise ScenarioError(f"{where}: range: file must be a built-in range's name ({', '.join(BUILTIN_RANGES)}) "
                                f"or a path relative to the scenario, not {sec['file']!r}")
        if sec["file"] not in BUILTIN_RANGES:
            out["file"] = str((path.parent / sec["file"]).resolve())
    if "start" in sec and not isinstance(sec["start"], str):
        raise ScenarioError(f"{where}: range: start must be the name of one of the range's starts, not {sec['start']!r}")
    p = sec.get("preload", 0)
    if isinstance(p, bool) or not isinstance(p, int) or p < 0:
        raise ScenarioError(f"{where}: range: preload must be a whole number of balls (0 or more), not {p!r}")
    if not isinstance(sec.get("floor_balls", True), bool):
        raise ScenarioError(f"{where}: range: floor_balls must be true or false, not {sec['floor_balls']!r}")
    return out


def _robot_env(raw: dict, where: str) -> dict[str, str]:
    env = raw.get("robot_env", {})
    if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
        raise ScenarioError(f"{where}: robot_env must be a table of NAME = \"text\", e.g. "
                            f"robot_env = {{ SCRIMMAGE_AUTO = \"shoot\" }}")
    return {str(k): v for k, v in env.items()}


def _match(raw: dict, where: str) -> dict:
    """The scenario's [match] as written, once MatchSettings takes it."""
    table = raw.get("match", {})
    try:
        MatchSettings().merged(table, f"{where}: [match]")
    except ValueError as e:
        raise ScenarioError(str(e)) from None
    return dict(table)


def match_for(scenario: Scenario, config) -> MatchSettings:
    """The match settings a run sends: the scenario's [match] keys over the config's (M4)."""
    return config.match.merged(scenario.match, f"{scenario.path.name}: [match]")


def range_file(ref: str) -> Path:
    """A [range] file as a path: a built-in range's own file, else `ref` (already absolute in a Scenario)."""
    from .testrange.spec import BUILTIN_RANGES
    return Path(BUILTIN_RANGES.get(ref) or ref).resolve()


def no_range_message(config_path: Path) -> str:
    return (f"the scenario's [range] needs a robot on a test range, but {config_path} has no [range]: add [range] to "
            "the config (see docs/test-range.md)")


def _check_range(config, raw: dict, where: str) -> None:
    """The scenario's [range] values (in `raw`) against the range and the robot, when the config's own [range]
    reads: a bad start or preload is then the scenario's mistake."""
    from .parts.model import PartError
    from .testrange.build import parse_range_section
    try:
        parse_range_section(config.raw, config.dir)
    except (PartError, ValueError):
        return      # the config's own [range] is broken: building its world says so
    try:
        parse_range_section(raw, config.dir)
    except (PartError, ValueError) as e:
        raise ScenarioError(f"{where}: range: {e}") from None


def config_for(scenario: Scenario, config):
    """The config a scenario runs on its own session: the scenario's [range] keys over the config's (its start
    replacing any [robot] start_pose) and its robot_env over the robot program's environment. `config` itself is
    left as it was. Its [match] goes over the config's too, so the robot hears the station from its first frame. A
    [range] on a config without one, a [range] value the config's range or robot can't take (its start, preload),
    and a robot_env with no robot program to give it to are ScenarioErrors naming the scenario; a mistake in the
    config's own [range] is left for building the world to report as the config's."""
    if scenario.match:
        config = dataclasses.replace(config, match=match_for(scenario, config))
    if not scenario.range and not scenario.robot_env:
        return config
    where = scenario.path.name
    raw = copy.deepcopy(config.raw)
    if scenario.range:
        if "range" not in raw:
            raise ScenarioError(no_range_message(config.path))
        raw["range"] = {**raw["range"], **scenario.range}
        if "start" in scenario.range and isinstance(raw.get("robot"), dict):
            raw["robot"].pop("start_pose", None)
        _check_range(config, raw, where)
    rp = config.robot_program
    if scenario.robot_env:
        if rp is None:
            raise ScenarioError(f"{where}: robot_env needs a robot program to give it to, but {config.path} has no "
                                "[robot_program] (scrimmage doesn't launch the robot program)")
        rp = dataclasses.replace(rp, env={**rp.env, **scenario.robot_env})
    return dataclasses.replace(config, raw=raw, robot_program=rp)


def validate(scenario: Scenario, world) -> None:
    """Check every name in the scenario against the world before anything runs."""
    for i, step in enumerate(scenario.steps, 1):
        where = f"{scenario.path.name}, step {i} ({step.name})"
        for c in step.checks:
            build_check(c, world, where)
        for j, shot in enumerate(step.screenshots, 1):
            shw = f"{where}, screenshot {j}"
            _check_view(shot.view, world, shw)
            try:
                cam = check_camera(shot.camera)
            except ValueError as e:
                raise ScenarioError(f"{shw}: {e}") from None
            if shot.view == "free" and "lookat" not in cam:
                raise ScenarioError(f"{shw}: the free view needs camera = {{ lookat = [x, y, z] }} "
                                    f"(and optionally distance, azimuth, elevation)")
        if step.contact_sheet is not None:
            csw = f"{where}, contact_sheet"
            if step.contact_sheet.view == "free":
                raise ScenarioError(f"{csw}: contact_sheet supports follow, top and camera:<name>, not 'free'")
            _check_view(step.contact_sheet.view, world, csw)


def _check_view(view: str, world, where: str) -> None:
    if view in ("follow", "top", "free"):
        return
    if not view.startswith("camera:"):
        raise ScenarioError(f"{where}: {VIEWS_HELP}, not {view!r}")
    name = view.split(":", 1)[1]
    m = world.model
    names = [mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_CAMERA, i) for i in range(m.ncam)]
    if name not in names:
        have = ", ".join(n for n in names if n)
        raise ScenarioError(f"{where}: no camera {name!r}; " + (f"the model's cameras: {have}" if have
                                                                else "the model has no cameras"))
