"""Scenario files: a sequence of steps (DS mode, duration, joysticks, game data) with checks and screenshots."""

from __future__ import annotations

import copy
import dataclasses
import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import mujoco

from .checks import CHECK_KINDS, ScenarioError, bad, build_check
from .driverstation import MODES, MatchSettings, check_game_data
from .i18n import t, translated
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


# A ScenarioError's `field` (see checks.ScenarioError): `at` below is the field id's prefix for the table being
# read, "scenario/", "step0/", "step0/shot1/" or "step0/sheet/"; a key's field is the prefix and the key.

def _keys(d: dict, allowed: set[str], where: str, at: str) -> None:
    for k in d:
        if k not in allowed:
            raise bad(where, t("unknown key {key}; allowed: {allowed}", key=repr(k),
                               allowed=", ".join(sorted(allowed))),
                      field=f"{at}{k}")


def _step_where(where: str, i: int, name: str | None = None) -> str:
    """Where a step's messages say they are: "x.toml, step 2", or "x.toml, step 2 (shoot)" once it has a name."""
    if name is None:
        return t("{file}, step {n}", file=where, n=i)
    return t("{file}, step {n} ({name})", file=where, n=i, name=name)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _ports(v, where: str, what: str, at: str) -> dict[int, list]:
    field = f"{at}{what}"
    if not isinstance(v, dict):
        raise bad(where, t("{what} must be a table like {what} = {{ 0 = [...] }}", what=what), field=field)
    out = {}
    for k, vals in v.items():
        if not str(k).isdigit() or not 0 <= int(k) <= 5 or not isinstance(vals, list):
            raise bad(where, t("{what} keys are joystick ports 0-5 with a list of values", what=what), field=field)
        for i, x in enumerate(vals):
            said = {"what": what, "port": k, "index": i, "value": repr(x)}
            if what == "axes" and not (_is_num(x) and -1 <= x <= 1):
                raise bad(where, t("{what} port {port}, index {index} must be a number between -1 and 1, not {value}",
                                   **said), field=field)
            if what == "buttons" and not isinstance(x, bool):
                raise bad(where, t("{what} port {port}, index {index} must be true or false, not {value}", **said),
                          field=field)
            if what == "povs" and not (isinstance(x, int) and not isinstance(x, bool) and (x == -1 or 0 <= x <= 359)):
                raise bad(where, t("{what} port {port}, index {index} must be a whole number: -1 or 0-359 degrees, not "
                                   "{value}", **said), field=field)
        out[int(k)] = vals
    return out


def _int(d: dict, key: str, default: int, where: str, at: str) -> int:
    v = d.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        raise bad(where, t("{key} must be a positive whole number", key=key), field=f"{at}{key}")
    return v


def _size(width: int, height: int, where: str, at: str) -> None:
    for name, v in (("width", width), ("height", height)):
        try:
            check_size(name, v)
        except ValueError as e:
            raise bad(where, str(e), field=f"{at}{name}") from None


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path).resolve()
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise ScenarioError(t("scenario file not found: {path}", path=path)) from None
    except OSError as e:
        raise ScenarioError(t("can't read scenario file {path}: {problem}", path=path,
                              problem=e.strerror or e)) from None
    except UnicodeDecodeError as e:
        raise ScenarioError(t("{path} isn't a text file: {problem}", path=path, problem=e)) from None
    return load_scenario_text(text, path)


def load_scenario_text(text: str, path: str | Path) -> Scenario:
    """The scenario parsed from TOML text; ``path`` (need not exist) is where it lives, for the relative config."""
    path = Path(path).resolve()
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ScenarioError(f"{path}: {e}") from None
    return load_scenario_raw(raw, path)


def load_scenario_raw(raw: dict, path: str | Path, *, first_step: int = 1) -> Scenario:
    """The scenario from already-parsed TOML (as load_scenario_text, without the parsing). `first_step`: the number
    its first [[step]] has in messages and fields (the Scenario tab loads each step of a file on its own)."""
    path = Path(path).resolve()
    where = path.name
    _keys(raw, TOP_KEYS, where, "scenario/")
    if not isinstance(raw.get("config"), str):
        raise bad(where, t("config = \"<robot config .toml>\" is required"), field="scenario/config")
    try:
        config_ref = expand(raw["config"])
    except TeamFolderError as e:
        raise ScenarioError(str(e)) from None
    try:
        speed = parse_speed(str(raw.get("speed", "max")))
    except ValueError as e:
        raise bad(where, str(e), field="scenario/speed") from None
    req = raw.get("require_lockstep", True)
    if not isinstance(req, bool):
        raise bad(where, t("require_lockstep must be true or false"), field="scenario/require_lockstep")
    steps_raw = raw.get("step", [])
    if not isinstance(steps_raw, list) or not steps_raw:
        raise bad(where, t("needs at least one [[step]]"))
    steps = []
    for i, s in enumerate(steps_raw, first_step):
        sw, at_step = _step_where(where, i), f"step{i - 1}/"
        if not isinstance(s, dict):
            raise bad(sw, t("each step must be a [[step]] table, not {value}", value=repr(s)))
        _keys(s, STEP_KEYS, sw, at_step)
        name = str(s.get("name", f"step {i}"))     # data: report.json's step name
        sw = _step_where(where, i, name) if "name" in s else sw
        if s.get("mode") not in MODES:
            raise bad(sw, t("mode must be one of {modes}, not {value}", modes=", ".join(MODES),
                            value=repr(s.get("mode"))), field=f"{at_step}mode")
        dur = s.get("duration")
        if not _is_num(dur) or dur <= 0:
            raise bad(sw, t("duration must be a positive number of seconds, not {value}", value=repr(dur)),
                      field=f"{at_step}duration")
        game_data = None
        if "game_data" in s:
            try:
                game_data = check_game_data(s["game_data"], f"{sw}:")
            except ValueError as e:
                raise ScenarioError(str(e), field=f"{at_step}game_data", detail=getattr(e, "detail", None)) from None
        track = None
        if "inputs" in s:
            inputs = f"{at_step}inputs"
            if not isinstance(s["inputs"], str):
                raise bad(sw, t("inputs must be the path of a .csv file"), field=inputs)
            if any(k in s for k in ("axes", "buttons", "povs")):
                raise bad(sw, t("inputs can't be combined with axes, buttons or povs in the same step"), field=inputs)
            try:
                track = load_track(path.parent / s["inputs"])
            except TrackError as e:
                raise bad(sw, t("inputs: {problem}", problem=e), field=inputs) from None
            if track.samples and track.samples[-1].t > dur + 1e-9:
                raise bad(sw, t("inputs: {file}: the last sample (t = {last}) is after the step's {duration} s",
                                file=track.path.name, last=f"{track.samples[-1].t:g}", duration=f"{dur:g}"),
                          field=inputs)
        checks = s.get("check", [])
        if isinstance(checks, dict):
            raise bad(sw, t("use [[step.check]] (double brackets), one per check"), field=f"{at_step}check")
        if not isinstance(checks, list):
            raise bad(sw, t("checks are [[step.check]] tables"), field=f"{at_step}check")
        for j, c in enumerate(checks, 1):
            if not isinstance(c, dict):
                raise bad(sw, t("check {n} must be a table, not {value}", n=j, value=repr(c)),
                          field=f"{at_step}check{j - 1}/check")
        for j, c in enumerate(checks, 1):  # structure only here; names are checked against the world in validate()
            kinds = [k for k in c]
            if len(kinds) != 1:
                if kinds:
                    detail = t("each [[step.check]] needs exactly one of {kinds}; got {got}",
                               kinds=", ".join(CHECK_KINDS), got=", ".join(kinds))
                else:
                    detail = t("each [[step.check]] needs exactly one of {kinds}; got nothing",
                               kinds=", ".join(CHECK_KINDS))
                raise bad(sw, detail, field=f"{at_step}check{j - 1}/check")
            if "by_time" in c:
                _by_time(c["by_time"], dur, t("{step}, check {n}, by_time", step=sw, n=j),
                         f"{at_step}check{j - 1}/by_time")
        shots = []
        shots_raw = s.get("screenshot", [])
        if isinstance(shots_raw, dict):
            raise bad(sw, t("use [[step.screenshot]] (double brackets), one per screenshot"), field=f"{at_step}shot")
        if not isinstance(shots_raw, list):
            raise bad(sw, t("screenshots are [[step.screenshot]] tables"), field=f"{at_step}shot")
        for j, sh in enumerate(shots_raw, 1):
            shw, at_shot = t("{step}, screenshot {n}", step=sw, n=j), f"{at_step}shot{j - 1}/"
            if not isinstance(sh, dict):
                raise ScenarioError(t("{where} must be a table, not {value}", where=shw, value=repr(sh)),
                                    field=f"{at_shot}view",
                                    detail=t("must be a table, not {value}", value=repr(sh)))
            _keys(sh, SHOT_KEYS, shw, at_shot)
            at = sh.get("at", "end")
            if at != "end" and (not _is_num(at) or at < 0):
                raise bad(shw, t("at must be \"end\" or seconds into the step"), field=f"{at_shot}at")
            if at != "end" and at > dur:
                raise bad(shw, t("at = {at} is beyond the step's {duration} s", at=at, duration=f"{dur:g}"),
                          field=f"{at_shot}at")
            w_, h_ = _int(sh, "width", 960, shw, at_shot), _int(sh, "height", 600, shw, at_shot)
            _size(w_, h_, shw, at_shot)
            shots.append(Shot(str(sh.get("view", "follow")), at, w_, h_, sh.get("camera")))
        sheet = None
        if "contact_sheet" in s:
            cs = s["contact_sheet"]
            csw, at_sheet = f"{sw}, contact_sheet", f"{at_step}sheet/"
            if not isinstance(cs, dict):
                raise ScenarioError(t("{where} must be a table like [step.contact_sheet], not {value}", where=csw,
                                      value=repr(cs)), field=f"{at_sheet}view",
                                    detail=t("must be a table like [step.contact_sheet], not {value}", value=repr(cs)))
            _keys(cs, SHEET_KEYS, csw, at_sheet)
            sheet = SheetSpec(_int(cs, "frames", 8, csw, at_sheet), str(cs.get("view", "follow")),
                              _int(cs, "width", 480, csw, at_sheet), _int(cs, "height", 300, csw, at_sheet))
            _size(sheet.width, sheet.height, csw, at_sheet)
            if sheet.frames > 16:
                raise bad(csw, t("frames must be 1-16 (contact_sheet frames)"), field=f"{at_sheet}frames")
            try:
                check_sheet_size(sheet.frames, sheet.width, sheet.height)
            except ValueError as e:
                raise bad(csw, str(e), field=f"{at_sheet}frames") from None
        steps.append(Step(name, s["mode"], float(dur), _ports(s.get("axes", {}), sw, "axes", at_step),
                          _ports(s.get("buttons", {}), sw, "buttons", at_step),
                          _ports(s.get("povs", {}), sw, "povs", at_step), checks, shots, sheet, track, game_data))
    return Scenario(path, (path.parent / config_ref).resolve(), str(raw.get("description", "")), speed, req, steps,
                    _range(raw, path, where), _robot_env(raw, where), _match(raw, where))


def _by_time(spec, duration: float, where: str, field: str) -> None:
    """A by_time check's t: present, a number, and within the step (the rest is checked against the world)."""
    if not isinstance(spec, dict):
        raise bad(where, t("expected a table like by_time = {{ signal = \"score\", min = 8, t = 15 }}"), field=field)
    if "t" not in spec:
        raise bad(where, t("by_time needs t, the seconds into the step it must hold by"), field=field)
    when = spec["t"]
    if not _is_num(when) or when < 0:
        raise bad(where, t("t must be 0 or more seconds into the step, not {value}", value=repr(when)),
                  field=f"{field}.t")
    if when > duration + 1e-9:
        raise bad(where, t("t = {t} is beyond the step's {duration} s", t=f"{when:g}", duration=f"{duration:g}"),
                  field=f"{field}.t")


def _range(raw: dict, path: Path, where: str) -> dict | None:
    """The scenario's [range]: its keys checked for type here (start and preload against the robot when it runs);
    a file other than a built-in range's name is relative to the scenario and made absolute."""
    sec = raw.get("range")
    if sec is None:
        return None
    field = "scenario/range"
    if not isinstance(sec, dict):
        raise bad(where, t("range must be a [range] table with {keys}", keys=", ".join(RANGE_KEYS)), field=field)
    for k in sec:
        if k not in RANGE_KEYS:
            raise bad(where, t("range: {key} isn't a [range] key; use {keys}", key=repr(k), keys=", ".join(RANGE_KEYS)),
                      field=field)
    out = dict(sec)
    if "file" in sec:
        from .testrange.spec import BUILTIN_RANGES
        if not isinstance(sec["file"], str) or not sec["file"]:
            raise bad(where, t("range: file must be a built-in range's name ({ranges}) or a path relative to the "
                               "scenario, not {value}", ranges=", ".join(BUILTIN_RANGES), value=repr(sec["file"])),
                      field=field)
        if sec["file"] not in BUILTIN_RANGES:
            out["file"] = str((path.parent / sec["file"]).resolve())
    if "start" in sec and not isinstance(sec["start"], str):
        raise bad(where, t("range: start must be the name of one of the range's starts, not {value}",
                           value=repr(sec["start"])), field=field)
    p = sec.get("preload", 0)
    if isinstance(p, bool) or not isinstance(p, int) or p < 0:
        raise bad(where, t("range: preload must be a whole number of balls (0 or more), not {value}", value=repr(p)),
                  field=field)
    if not isinstance(sec.get("floor_balls", True), bool):
        raise bad(where, t("range: floor_balls must be true or false, not {value}", value=repr(sec["floor_balls"])),
                  field=field)
    return out


def _robot_env(raw: dict, where: str) -> dict[str, str]:
    env = raw.get("robot_env", {})
    if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
        raise bad(where, t("robot_env must be a table of NAME = \"text\", e.g. robot_env = {{ SCRIMMAGE_AUTO = "
                           "\"shoot\" }}"), field="scenario/robot_env")
    return {str(k): v for k, v in env.items()}


def _match(raw: dict, where: str) -> dict:
    """The scenario's [match] as written, once MatchSettings takes it."""
    table = raw.get("match", {})
    try:
        MatchSettings().merged(table, f"{where}: [match]")
    except ValueError as e:
        key = getattr(e, "key", None)
        raise ScenarioError(str(e), field=f"scenario/match.{key}" if key else "scenario/match",
                            detail=getattr(e, "detail", None)) from None
    return dict(table)


def match_for(scenario: Scenario, config) -> MatchSettings:
    """The match settings a run sends: the scenario's [match] keys over the config's (M4)."""
    return config.match.merged(scenario.match, f"{scenario.path.name}: [match]")


def range_file(ref: str) -> Path:
    """A [range] file as a path: a built-in range's own file, else `ref` (already absolute in a Scenario)."""
    from .testrange.spec import BUILTIN_RANGES
    return Path(BUILTIN_RANGES.get(ref) or ref).resolve()


def no_range_message(config_path: Path) -> str:
    return t("the scenario's [range] needs a robot on a test range, but {config} has no [range]: add [range] to the "
             "config (see docs/test-range.md)", config=config_path)


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
        raise bad(where, t("range: {problem}", problem=e), field="scenario/range") from None


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
            raise ScenarioError(no_range_message(config.path), field="scenario/range",
                                detail=no_range_message(config.path))
        raw["range"] = {**raw["range"], **scenario.range}
        if "start" in scenario.range and isinstance(raw.get("robot"), dict):
            raw["robot"].pop("start_pose", None)
        _check_range(config, raw, where)
    rp = config.robot_program
    if scenario.robot_env:
        if rp is None:
            raise bad(where, t("robot_env needs a robot program to give it to, but {config} has no [robot_program] "
                               "(scrimmage doesn't launch the robot program)", config=config.path),
                      field="scenario/robot_env")
        rp = dataclasses.replace(rp, env={**rp.env, **scenario.robot_env})
    return dataclasses.replace(config, raw=raw, robot_program=rp)


def validate(scenario: Scenario, world, *, first_step: int = 1) -> None:
    """Check every name in the scenario against the world before anything runs."""
    for i, step in enumerate(scenario.steps, first_step):
        where = _step_where(scenario.path.name, i, step.name)
        for c in step.checks:
            build_check(c, world, where)
        for j, shot in enumerate(step.screenshots, 1):
            shw = t("{step}, screenshot {n}", step=where, n=j)
            _check_view(shot.view, world, shw)
            try:
                cam = check_camera(shot.camera)
            except ValueError as e:
                raise bad(shw, str(e), key="camera") from None
            if shot.view == "free" and "lookat" not in cam:
                raise bad(shw, t("the free view needs camera = {{ lookat = [x, y, z] }} (and optionally distance, "
                                 "azimuth, elevation)"), key="camera")
        if step.contact_sheet is not None:
            csw = f"{where}, contact_sheet"
            if step.contact_sheet.view == "free":
                raise bad(csw, t("contact_sheet supports follow, top and camera:<name>, not 'free'"), key="view")
            _check_view(step.contact_sheet.view, world, csw)


def _check_view(view: str, world, where: str) -> None:
    if view in ("follow", "top", "free"):
        return
    if not view.startswith("camera:"):
        raise bad(where, t("{help}, not {view}", help=translated(VIEWS_HELP), view=repr(view)), key="view")
    name = view.split(":", 1)[1]
    m = world.model
    names = [mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_CAMERA, i) for i in range(m.ncam)]
    if name not in names:
        have = ", ".join(n for n in names if n)
        if have:
            raise bad(where, t("no camera {name}; the model's cameras: {cameras}", name=repr(name), cameras=have),
                      key="view")
        raise bad(where, t("no camera {name}; the model has no cameras", name=repr(name)), key="view")
