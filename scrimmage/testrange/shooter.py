"""The shooter on the test range: a stock `launcher` part run by sub-project 8's launcher model (or a stock
`disc_shooter` part run by the disc launcher, `kind = "disc"`, in shooter_disc.py, or a stock `pinch_shooter` part run
by the pinch model for a flat rigid piece, `kind = "piece"`, in shooter_piece.py). The parsing helpers shared by all
three live here.

The config's [shooter] names the launcher part and gives the launcher model's own inputs (the wheels' tread, the
hood's friction and flares, the air), inline or from a sub-project 8 setup file. Everything the part or the range
already fixes comes from them, never from [shooter]: the ball is the range's, and the wheel diameter, the hood's gap,
entry and exit angle, the shaft (inertia, motors, gearing) and the launcher's width and height are the part's. A setup
file's values for these must agree with the part's, except its [launcher] height, which is ignored (the height is the
part's mount), and an articulated hood's exit_angle_deg, which only has to lie within hood_range.

At neutral the shooter follows its motors' neutral mode, like every other motor: brake shorts the winding (0 V, the
shaft braking on its back-EMF through the motor's resistance), coast (and an unbound shooter) is an open circuit.

At run time the launcher model owns the shooter shaft. Its motor bindings are external: they read the robot
program's command, the model steps the shaft at their mean voltage on the world's bus voltage, and its currents go
back to the bindings (for logs and the robot's sensors) and its supply current onto the shared battery, once. After
each physics step the spin joint is set to the shaft's angle and speed, so the rotor sensor, the fast-mode encoder
and the view see the model's shaft. An articulated hood is an ordinary joint driven by its motor; each step the model
takes its exit angle from it.
"""

from __future__ import annotations

import dataclasses
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np

from .. import i18n
from ..launcher.inputs import LauncherConfigError, Setup, _build_section, apply_measured, check_lane, parse_setup
from ..launcher.model import ExitedBall, FedBall, Launcher
from ..motors import get_motor
from ..parts.model import quat_from_rpy

SHOOTER_KINDS = ("ball", "disc", "piece")
SHOOTER_KEYS = ("kind", "part", "setup", "lanes", "wheels", "hood", "air", "hopper", "stage", "intake",
                "beam_break")
INLINE_SECTIONS = ("wheels", "hood", "air")            # sub-project 8 sections, inline; they override the setup file's
HOPPER_KEYS = ("capacity",)
STAGE_KEYS = ("name", "part", "path_length")
INTAKE_KEYS = ("part", "at", "pos", "size", "rate", "min_surface_speed")
BEAM_KEYS = ("at", "fraction", "lane", "dio", "active_low")
STAGE_SOURCES = ("stock roller", "stock flywheel")
DEFAULT_MIN_SURFACE_SPEED = 0.5    # m/s inward at the intake's surface before it collects a ball
HOOD_TOLERANCE_DEG = 1e-6           # the launcher model's hood follows the joint once it has moved this far
# A setup file's sections that a shooter in the world doesn't use: the robot program controls the shaft, the range
# has the goals, the robot moves itself, and the hopper and stages feed the balls.
IGNORED_SECTIONS = ("control", "goal", "robot", "feed", "spread", "sweep", "chart")
# parse_setup needs a [control] and a [goal]; in the world neither is used (the robot program drives the shaft and the
# range's goals are real), so these stand in.
CONTROL_PLACEHOLDER = {"target_rps": 0.0, "kP": 0.0}
GOAL_PLACEHOLDER = {"distance": 1.0, "height": 0.0, "diameter": 1.0}
# The setup keys the launcher part gives, and its parameter for each (the hood's exit angle and the launcher's height
# are handled on their own: the hood joint or hood_angle, and the part's mount).
PART_KEYS = {("wheels", "diameter"): "wheel_diameter", ("hood", "gap"): "hood_gap",
             ("hood", "entry_deg"): "hood_entry_deg",
             ("shaft", "wheel_inertia"): "wheel_moi", ("shaft", "added_inertia"): "added_moi",
             ("shaft", "motor"): "motor", ("shaft", "motor_count"): "count", ("shaft", "gearing"): "gearing",
             ("launcher", "width"): "width"}


@dataclass(frozen=True)
class Stage:
    """A driven feeder stage: a stock roller or flywheel whose surface carries the balls `path_length` along."""
    name: str
    part: str                           # the stock part: its spin joint is `<part>.spin`
    path_length: float                  # m along the ball path
    diameter: float                     # the part's roller or wheel diameter (m)


@dataclass(frozen=True)
class Intake:
    """The intake zone: a box riding on a robot body, collecting while the intake roller spins inward (positive)."""
    part: str                           # the stock roller
    diameter: float
    body: str                           # the robot body the zone rides on (prefixed)
    center: tuple[float, float, float]  # the zone's center in that body's frame
    axes: tuple[tuple[float, ...], ...]     # the zone's x, y, z axes in that body's frame (a 3x3 matrix's columns)
    size: tuple[float, float, float]    # full depth, width, height (m)
    rate: float                         # balls per second at most
    min_surface_speed: float            # m/s inward before it collects


@dataclass(frozen=True)
class BeamBreak:
    """A beam break on the ball path, read by the robot as a DIO like a limit switch."""
    at: str                             # "hopper", "stage:<name>" or "launcher"
    fraction: float | None              # a stage's: where along it
    lane: int | None                    # one lane (an index into lanes), or None for any
    dio: int
    active_low: bool

    @property
    def device_key(self) -> tuple[str, str]:
        return ("DIO", str(self.dio))

    @property
    def label(self) -> str:
        return f"[[shooter.beam_break]] at {self.at}"


@dataclass(frozen=True)
class ShooterConfig:
    part: str                           # the stock launcher instance the shooter runs in
    setup: Setup                        # the launcher model's inputs: the part's values, the range's ball
    lanes: tuple[float, ...]            # ball positions across the wheels (m, along the shaft)
    hood: str                           # "articulated" or "fixed"
    hood_range: tuple[float, float]     # exit angles (deg) at the articulated hood's stops: joint 0 is the low one
    hood_angle: float                   # the fixed hood's exit angle (deg)
    capacity: int                       # balls the robot holds ([shooter.hopper] capacity)
    stages: tuple[Stage, ...] = ()      # the feeder stages in order, the hopper's side first
    intake: Intake | None = None
    beam_breaks: tuple[BeamBreak, ...] = ()
    kind: str = "ball"


def _err(msg: str) -> ValueError:
    return ValueError(f"[shooter] {msg}")


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return a == b


def _mount_height(built, part: str, body: str = "base") -> float:
    """The launcher's shaft height above the floor in the start pose: its base body's origin (or `body`'s)."""
    data = mujoco.MjData(built.model)
    mujoco.mj_kinematics(built.model, data)
    return max(0.0, float(data.xpos[built.model.body(f"{part}.{body}").id][2]))


def _setup_file(path: Path) -> dict:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise _err(i18n.t("setup: can't read {path}: {error}", path=path, error=e.strerror)) from None
    except tomllib.TOMLDecodeError as e:
        raise _err(f"setup {path}: {e}") from None
    return {k: v for k, v in raw.items() if k not in IGNORED_SECTIONS}


def _check_file(raw: dict, path: Path, name: str, given: dict, rng, art: bool, p: dict) -> None:
    """A setup file's values for what the part or the range gives must agree with them. Its [launcher] height is
    ignored: the launcher's height is where the part is mounted (a standalone setup file always has one). `raw` has
    its measurement files applied (parse_shooter)."""
    where = f"setup {path}"
    if "ball" in raw:
        try:
            ball = _build_section("ball", raw["ball"])
        except LauncherConfigError as e:
            raise _err(f"{where}: {e}") from None
        if ball != rng.ball:
            raise _err(i18n.t("{where}: its {ball_table} ({ball}) is not the range's ball {ball_preset}: the shooter "
                              "shoots the range's balls; remove {ball_table} from the setup file or make it the "
                              "range's", where=where, ball=raw['ball'], ball_preset=repr(rng.ball_preset),
                              ball_table="[ball]"))
    for (section, key), value in given.items():
        table = raw.get(section)
        if not isinstance(table, dict) or key not in table:
            continue
        mine = table[key]
        if (section, key) == ("launcher", "height"):
            continue
        if (section, key) == ("hood", "exit_angle_deg") and art:
            lo, hi = p["hood_range"]
            if not (isinstance(mine, (int, float)) and lo - 1e-9 <= mine <= hi + 1e-9):
                raise _err(i18n.t("{where}: {hood_table} {exit_angle_deg_setting} is outside the launcher part "
                                  "{name}'s {hood_range_setting} (its hood joint sets the exit angle)", where=where,
                                  name=repr(name), hood_table="[hood]",
                                  exit_angle_deg_setting="exit_angle_deg = {}".format(mine),
                                  hood_range_setting="hood_range = {}".format(list(p['hood_range']))))
            continue
        if not _same(mine, value):
            theirs = {("hood", "exit_angle_deg"): "hood_angle"}.get((section, key), PART_KEYS.get((section, key)))
            raise _err(i18n.t("{where}: [{section}] {key} = {mine} but the launcher part {name} has {theirs} = "
                              "{value}; make them agree or leave the key out of the setup file", where=where,
                              section=section, key=key, mine=mine, name=repr(name), theirs=theirs, value=value))


def parse_shooter(cfg: dict, built, cfg_dir: Path | None) -> ShooterConfig:
    """The config's [shooter], checked against the robot's parts and the range: every mistake a ValueError naming
    the [shooter] key."""
    sec = cfg.get("shooter")
    if not isinstance(sec, dict):
        raise _err(i18n.t("must be a table"))
    if built is None or built.range is None:
        raise _err(i18n.t("needs a {range_table}: the shooter shoots the range's balls, so add a {range_table} (see "
                          "the range files) or remove {shooter_table}", range_table="[range]",
                          shooter_table="[shooter]"))
    rng = built.range
    kind = sec.get("kind", "ball")
    if kind not in SHOOTER_KINDS:
        raise _err(i18n.t("kind must be \"{ball}\" (a stock launcher part), \"{disc}\" (a stock disc_shooter "
                          "part) or \"{piece}\" (a stock pinch_shooter part), not {kind}", kind=repr(kind),
                          ball="ball", disc="disc", piece="piece"))
    if kind == "piece":
        from .shooter_piece import _parse_piece
        return _parse_piece(sec, built)
    if rng.ball is None and rng.disc is None:
        raise ValueError(i18n.t("the robot's {shooter_table} needs a ball or disc on the field; this field's pieces "
                                "are: {pieces}. A rigid piece is shot with {kind_setting} in a stock pinch_shooter "
                                "part", pieces=', '.join(k.label for k in rng.kinds.values()),
                                shooter_table="[shooter]", kind_setting="kind = \"piece\""))
    if kind == "ball" and rng.disc is not None:
        raise _err(i18n.t("{ball_setting} (the default: a stock launcher part) shoots balls, but the range {name} "
                          "plays discs ({disc_preset}); set {disc_setting} and run it in a stock disc_shooter part",
                          name=repr(rng.name), disc_preset=repr(rng.disc_preset), ball_setting="kind = \"ball\"",
                          disc_setting="kind = \"disc\""))
    if kind == "disc" and rng.disc is None:
        raise _err(i18n.t("{kind_setting} (a stock disc_shooter part) shoots discs, but the range {name} plays balls "
                          "({ball_preset}); leave kind out for a stock launcher part, or use a disc range (such as "
                          "\"{disc_range}\")", name=repr(rng.name), ball_preset=repr(rng.ball_preset),
                          kind_setting="kind = \"disc\"", disc_range="2013"))
    if kind == "disc":
        from .shooter_disc import _parse_disc
        return _parse_disc(sec, built, cfg_dir)
    for k in sec:
        if k == "ball":
            raise _err(i18n.t("ball comes from the range ({ball_preset}); leave it out",
                              ball_preset=repr(rng.ball_preset)))
        if k in ("shaft", "launcher"):
            raise _err(i18n.t("{key} comes from the launcher part (its parameters wheel_moi, added_moi, motor, "
                              "count, gearing, width, and its mount); leave it out", key=k))
        if k not in SHOOTER_KEYS:
            raise _err(i18n.t("unknown key {key}; allowed: {keys}",
                              key=repr(k), keys=', '.join(SHOOTER_KEYS)))

    name = sec.get("part")
    by_name = {i.name: i for i in built.instances}
    if not isinstance(name, str) or name not in by_name:
        raise _err(i18n.t("part {name}: no such part; parts: {parts}", name=repr(name), parts=', '.join(by_name)))
    inst = by_name[name]
    if inst.source != "stock launcher":
        raise _err(i18n.t("part {name} is a {source} part; the shooter runs in a stock launcher part",
                          name=repr(name), source=inst.source))
    p = inst.stock_params
    art = p["hood"] == "articulated"
    entry = p["hood_entry_deg"]
    top = p["hood_range"][1] if art else p["hood_angle"]
    if entry - (top + 90.0) <= 0.0:
        if art:
            raise _err(i18n.t("part {name}: hood_range's high stop {top} leaves the hood no wrap: hood_entry_deg "
                              "{entry} less {least} (the exit angle + 90) is {wrap}; it must be above 0",
                              name=repr(name), top=top, entry=entry, least=top + 90.0, wrap=entry - (top + 90.0)))
        raise _err(i18n.t("part {name}: hood_angle {top} leaves the hood no wrap: hood_entry_deg {entry} less "
                          "{least} (the exit angle + 90) is {wrap}; it must be above 0",
                          name=repr(name), top=top, entry=entry, least=top + 90.0, wrap=entry - (top + 90.0)))

    given = {(s, k): p[param] for (s, k), param in PART_KEYS.items()}
    given[("hood", "exit_angle_deg")] = p["hood_range"][0] if art else p["hood_angle"]
    given[("launcher", "height")] = _mount_height(built, name)

    for section in INLINE_SECTIONS:
        table = sec.get(section, {})
        if not isinstance(table, dict):
            raise _err(i18n.t("{section} must be a table, not {table}", section=section, table=repr(table)))
        for k in table:
            if (section, k) == ("hood", "exit_angle_deg"):
                if art:
                    raise ValueError(i18n.t("{shooter_hood_table} exit_angle_deg comes from the launcher part "
                                            "{name}'s hood joint ({hood_range_setting}); leave it out",
                                            name=repr(name), shooter_hood_table="[shooter.hood]",
                                            hood_range_setting="hood_range = {}".format(list(p["hood_range"]))))
                raise ValueError(i18n.t("{shooter_hood_table} exit_angle_deg comes from the launcher part {name} "
                                        "({hood_angle_setting}); leave it out", name=repr(name),
                                        shooter_hood_table="[shooter.hood]",
                                        hood_angle_setting="hood_angle = {}".format(p["hood_angle"])))
            if (section, k) in PART_KEYS:
                raise ValueError(i18n.t("{shooter_section_table} {key} comes from the launcher part {name} ({param} "
                                        "= {value}); leave it out", key=k, name=repr(name),
                                        param=PART_KEYS[(section, k)], value=p[PART_KEYS[(section, k)]],
                                        shooter_section_table="[shooter.{}]".format(section)))

    raw: dict = {}
    path = None
    measured: dict = {}  # the setup file's measured numbers' provenance
    if "setup" in sec:
        ref = sec["setup"]
        if not isinstance(ref, str):
            raise _err(i18n.t("setup must be a path to a launcher setup file, not {ref}", ref=repr(ref)))
        path = Path(cfg_dir if cfg_dir is not None else ".") / ref
        raw = _setup_file(path)
        try:  # its [ball] and [wheels] measured files, found from it, read together (a launcher file may be both's)
            raw, measured = apply_measured(raw, path)
        except LauncherConfigError as e:
            raise _err(str(e)) from None
        _check_file(raw, path, name, given, rng, art, p)
    for section in INLINE_SECTIONS:
        if section in sec:
            base = raw.get(section, {})
            base = base if isinstance(base, dict) else {}
            inline = dict(sec[section])
            if isinstance(inline.get("measured"), str):  # found from the robot config, the file that names it
                inline["measured"] = str(Path(cfg_dir if cfg_dir is not None else ".") / inline["measured"])
                # it replaces what the setup file's own measurement gave
                base = {k: v for k, v in base.items() if f"{section}.{k}" not in measured}
                measured = {k: v for k, v in measured.items() if not k.startswith(f"{section}.")}
            measured = {k: v for k, v in measured.items()      # an inline number replaces a measured one
                        if not (k.startswith(f"{section}.") and k.split(".", 1)[1] in inline)}
            raw[section] = {**base, **inline}

    lanes = sec.get("lanes", [0.0])
    if not (isinstance(lanes, list) and lanes
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in lanes)):
        raise _err(i18n.t("lanes must be a list of positions across the wheels (m), not {lanes}", lanes=repr(lanes)))
    lanes = tuple(float(x) for x in lanes)
    for lane in lanes:
        try:
            check_lane(lane, rng.ball.diameter, p["width"], "[shooter] lanes: lane")
        except LauncherConfigError as e:
            raise ValueError(str(e)) from None
    by_pos = sorted(lanes)
    for a, b in zip(by_pos, by_pos[1:]):
        if b - a < rng.ball.diameter - 1e-12:
            raise _err(i18n.t("lanes {a} and {b} are {gap} m apart, closer than the ball's diameter {diameter}: "
                              "balls side by side would overlap",
                              a=f"{a:g}", b=f"{b:g}", gap=f"{b - a:.4g}", diameter=rng.ball.diameter))

    capacity = _capacity(sec, 0)
    stages = _stages(sec.get("stage"), by_name, name)
    intake = _intake(sec.get("intake"), by_name)
    beams = _beam_breaks(sec.get("beam_break", []), stages, len(lanes))

    for (section, key), value in given.items():
        raw.setdefault(section, {})
        raw[section] = {**raw[section], key: value}
    raw["ball"] = dataclasses.asdict(rng.ball)
    raw["control"] = dict(CONTROL_PLACEHOLDER)
    raw["goal"] = dict(GOAL_PLACEHOLDER)
    raw["feed"] = {"lanes": list(lanes)}
    try:
        setup = parse_setup(raw, path)
    except LauncherConfigError as e:
        raise _err(i18n.t("(the launcher model's inputs): {error}", error=e)) from None
    ball_kind = next(k for k in rng.kinds.values() if k.form == "ball")
    ball = {f"ball.{k}": v for k, v in ball_kind.provenance.items()}  # the range's ball: the shooter shoots it
    wheels = {k: v for k, v in measured.items() if not k.startswith("ball.")}
    if ball or wheels:
        setup = dataclasses.replace(setup, provenance={**ball, **wheels, **setup.provenance})
    return ShooterConfig(part=name, setup=setup, lanes=lanes, hood=p["hood"], hood_range=p["hood_range"],
                         hood_angle=p["hood_angle"], capacity=capacity, stages=stages, intake=intake,
                         beam_breaks=beams)


def _capacity(sec: dict, default: int) -> int:
    """[shooter.hopper] capacity: the pieces the whole robot holds, `default` when it is left out."""
    hopper = sec.get("hopper", {})
    if not isinstance(hopper, dict):
        raise _err(i18n.t("hopper must be a table, not {hopper}", hopper=repr(hopper)))
    for k in hopper:
        if k not in HOPPER_KEYS:
            raise _err(i18n.t("hopper: unknown key {key}; allowed: {keys}",
                              key=repr(k), keys=', '.join(HOPPER_KEYS)))
    capacity = hopper.get("capacity", default)
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 0:
        raise _err(i18n.t("hopper capacity must be a whole number of balls, not {capacity}", capacity=repr(capacity)))
    return capacity


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _keys(table, allowed: tuple, where: str) -> None:
    if not isinstance(table, dict):
        raise _err(i18n.t("{where} must be a table, not {table}", where=where, table=repr(table)))
    for k in table:
        if k not in allowed:
            raise _err(i18n.t("{where}: unknown key {key}; allowed: {allowed}",
                              where=where, key=repr(k), allowed=', '.join(allowed)))


def _spinner(part, by_name: dict, where: str, sources: tuple, launcher: str):
    """The stock roller (or flywheel) instance named `part`."""
    if not isinstance(part, str) or part not in by_name:
        raise _err(i18n.t("{where}: part {part}: no such part; parts: {parts}",
                          where=where, part=repr(part), parts=', '.join(by_name)))
    inst = by_name[part]
    if inst.source not in sources or part == launcher:
        kinds = " or ".join(x.removeprefix("stock ") for x in sources)
        raise _err(i18n.t("{where}: part {part} is a {source} part; it must be a stock {kinds}",
                          where=where, part=repr(part), source=inst.source, kinds=kinds))
    return inst


def _stages(table, by_name: dict, launcher: str) -> tuple[Stage, ...]:
    """[[shooter.stage]]: at least one, in order from the hopper's side."""
    if not isinstance(table, list) or not table:
        raise _err(i18n.t("stage: at least one {shooter_stage_table} is needed (name, part, path_length): the stages "
                          "carry the balls from the hopper to the launcher", shooter_stage_table="[[shooter.stage]]"))
    stages: list[Stage] = []
    for k, st in enumerate(table, 1):
        _keys(st, STAGE_KEYS, f"stage #{k}")
        name = st.get("name")
        if not isinstance(name, str) or not name or name in ("hopper", "launcher"):
            raise _err(i18n.t("stage #{key}: name must be a name other than hopper or launcher, not {name}",
                              key=k, name=repr(name)))
        if any(x.name == name for x in stages):
            raise _err(i18n.t("two stages named {name}: give each its own name", name=repr(name)))
        where = f"stage {name!r}"
        inst = _spinner(st.get("part"), by_name, where, STAGE_SOURCES, launcher)
        length = st.get("path_length")
        if not _number(length) or length <= 0.0:
            raise _err(i18n.t("{where}: path_length must be a length (m) above 0, not {length}",
                              where=where, length=repr(length)))
        stages.append(Stage(name=name, part=inst.name, path_length=float(length),
                            diameter=float(inst.stock_params["diameter"])))
    return tuple(stages)


def _mount_frame(at, by_name: dict, where: str) -> tuple[str, np.ndarray, np.ndarray]:
    """A robot mount (or body) "<part>.<mount>": its body's prefixed name, and its position and rotation matrix in
    that body's frame."""
    tname, _, local = at.partition(".") if isinstance(at, str) else ("", "", "")
    inst = by_name.get(tname)
    if inst is not None:
        for m in inst.part.mounts:
            if m.name == local:
                mat = np.zeros(9)
                mujoco.mju_quat2Mat(mat, np.asarray(quat_from_rpy(m.rot), float))
                return f"{tname}.{m.body}", np.array(m.pos, float), mat.reshape(3, 3)
        if any(b.name == local for b in inst.part.bodies):
            return f"{tname}.{local}", np.zeros(3), np.eye(3)
    mounts = [f"{i.name}.{m.name}" for i in by_name.values() for m in i.part.mounts]
    raise _err(i18n.t("{where}: at {at} is not a robot mount; mounts: {mounts}",
                      where=where, at=repr(at), mounts=', '.join(mounts)))


def _intake(table, by_name: dict) -> Intake | None:
    """[shooter.intake], or None without one (a robot that only shoots its preloads)."""
    if table is None:
        return None
    _keys(table, INTAKE_KEYS, "intake")
    inst = _spinner(table.get("part"), by_name, "intake", ("stock roller",), "")
    if inst.stock_params.get("collide"):
        # its roller would push the floor balls the zone collects; the launcher and the stages may collide (the
        # balls they hold are parked and touch nothing, and a launched ball clears the robot)
        raise ValueError(i18n.t("{name} has {collide_setting} but is the {shooter_table}'s intake; the ball path "
                                "moves those balls, so leave collide off", name=inst.name,
                                collide_setting="collide = true", shooter_table="[shooter]"))
    body, mpos, mrot = _mount_frame(table.get("at"), by_name, "intake")
    pos, size = table.get("pos"), table.get("size")
    if not (isinstance(pos, list) and len(pos) == 3 and all(_number(v) for v in pos)):
        raise _err(i18n.t("intake: pos must be [x, y, z] (m, the zone's center in the mount's frame), not {pos}",
                          pos=repr(pos)))
    if not (isinstance(size, list) and len(size) == 3 and all(_number(v) and v > 0.0 for v in size)):
        raise _err(i18n.t("intake: size must be [depth, width, height] (m, each above 0), not {size}",
                          size=repr(size)))
    rate = table.get("rate")
    if not _number(rate) or rate <= 0.0:
        raise _err(i18n.t("intake: rate must be balls per second above 0, not {rate}", rate=repr(rate)))
    speed = table.get("min_surface_speed", DEFAULT_MIN_SURFACE_SPEED)
    if not _number(speed) or speed < 0.0:
        raise _err(i18n.t("intake: min_surface_speed must be a speed (m/s) of 0 or more, not {speed}",
                          speed=repr(speed)))
    center = mpos + mrot @ np.asarray(pos, float)
    return Intake(part=inst.name, diameter=float(inst.stock_params["diameter"]), body=body,
                  center=tuple(float(v) for v in center), axes=tuple(tuple(float(v) for v in row) for row in mrot),
                  size=tuple(float(v) for v in size), rate=float(rate), min_surface_speed=float(speed))


def _beam_breaks(table, stages: tuple[Stage, ...], n_lanes: int) -> tuple[BeamBreak, ...]:
    """[[shooter.beam_break]] (two on one DIO, or one on a [[digital]]'s channel, World rejects)."""
    if not isinstance(table, list):
        raise _err(i18n.t("beam_break must be a list of {shooter_beam_break_table} tables, not {table}",
                          table=repr(table), shooter_beam_break_table="[[shooter.beam_break]]"))
    names = [st.name for st in stages]
    out = []
    for k, bb in enumerate(table, 1):
        where = f"beam_break #{k}"
        _keys(bb, BEAM_KEYS, where)
        at = bb.get("at")
        stage = at.removeprefix("stage:") if isinstance(at, str) and at.startswith("stage:") else None
        if not (at in ("hopper", "launcher") or stage in names):
            raise _err(i18n.t("{where}: at {at} must be \"{hopper}\", \"{launcher}\" or \"{stage_name}\" of a stage; "
                              "stages: {names}", where=where, at=repr(at), names=', '.join(names), hopper="hopper",
                              launcher="launcher", stage_name="stage:<name>"))
        fraction = bb.get("fraction")
        if stage is None and fraction is not None:
            raise _err(i18n.t("{where}: fraction is only for a beam break on a stage (where along it), not at {at}",
                              where=where, at=repr(at)))
        if stage is not None and not (_number(fraction) and 0.0 <= fraction <= 1.0):
            raise _err(i18n.t("{where}: at {at} needs fraction, where along the stage (0 to 1), not {fraction}",
                              where=where, at=repr(at), fraction=repr(fraction)))
        lane = bb.get("lane")
        if lane is not None and (isinstance(lane, bool) or not isinstance(lane, int) or not 0 <= lane < n_lanes):
            raise _err(i18n.t("{where}: lane {lane} must be an index into lanes (0 to {last}), or left out for any "
                              "lane", where=where, lane=repr(lane), last=n_lanes - 1))
        dio = bb.get("dio")
        if isinstance(dio, bool) or not isinstance(dio, int) or dio < 0:
            raise _err(i18n.t("{where}: dio must be a DIO channel (0 or more), not {dio}", where=where,
                              dio=repr(dio)))
        low = bb.get("active_low", True)
        if not isinstance(low, bool):
            raise _err(i18n.t("{where}: active_low must be true or false, not {low}", where=where, low=repr(low)))
        out.append(BeamBreak(at=at, fraction=None if fraction is None else float(fraction), lane=lane, dio=dio,
                             active_low=low))
    return tuple(out)


# ------------------------------------------------------------------------------------------------ a shaft's motors
# The shooter's shafts (the ball launcher's one, the disc launcher's one per wheel group) are run by their models with
# external motor bindings; these are each shaft's share of the world's battery.

def _mean_voltage(emf: float, bindings) -> float:
    """The mean voltage on a shaft's motors. A motor coasting at neutral (an open circuit) is taken at the shaft's
    back-EMF `emf`, which drives no current; so is an unbound shaft."""
    if not bindings:
        return emf
    return sum(emf if mb.open_circuit else mb.voltage for mb in bindings) / len(bindings)


def _share_currents(shaft, bindings, volts: float, vbatt: float) -> None:
    """The shaft model's currents back to its bindings, after its step at `volts` on bus `vbatt`."""
    # the shaft model's supply current takes |duty| (sub-project 8's standalone model, kept for its goldens), so a
    # reversed voltage braking the shaft would read as regeneration: the bindings take the signed duty, as the
    # bus solve does
    v = max(-vbatt, min(vbatt, volts))
    supply = shaft.stator_current * v / vbatt if vbatt > 0.0 else 0.0
    n = len(bindings)
    for mb in bindings:
        mb.set_shaft_currents(shaft.stator_current / n, supply / n)


def _supply_and_slope(shaft, bindings, bus: float) -> tuple[float, float]:
    """A shaft's motors' supply current on bus voltage `bus` and its derivative (Shooter.supply_and_slope)."""
    motor = shaft.motor
    emf = shaft.speed * shaft.gearing / motor.Kv
    driven = [mb for mb in bindings if not mb.open_circuit]
    if not bindings:                # unbound: taken at the back-EMF, no current
        return 0.0, 0.0
    if bus <= 0.0:
        # the limit as the bus falls to 0: the request is clamped to it, full duty; coasting draws nothing
        request = sum(mb._duty if mb._duty is not None else mb._volts for mb in driven)
        if not request:
            return 0.0, 0.0
        return (0.0 - emf) / motor.R * math.copysign(1.0, request), 1.0 / motor.R
    n = len(bindings)
    mean = (sum(mb.volts_at(bus) for mb in driven) + emf * (len(bindings) - len(driven))) / n
    dmean = sum(mb._duty if mb._duty is not None else (0.0 if abs(mb._volts) < bus else
                math.copysign(1.0, mb._volts)) for mb in driven) / n
    if abs(mean) >= bus:
        v, dv = math.copysign(bus, mean), math.copysign(1.0, mean)
    else:
        v, dv = mean, dmean
    supply = (v - emf) * v / (motor.R * bus)       # shaft.motor.current at the shaft's speed, times v / bus
    slope = ((2.0 * v - emf) * dv * bus - (v - emf) * v) / (motor.R * bus * bus)
    return supply, slope


class Shooter:
    """The launcher model running the launcher part's shooter shaft, on the world's battery."""

    def __init__(self, world, config: ShooterConfig):
        self.world = world
        self.config = config
        m = world.model
        spin = m.joint(f"{config.part}.spin").id
        self._qadr, self._dadr = int(m.jnt_qposadr[spin]), int(m.jnt_dofadr[spin])
        self._q0 = float(m.qpos0[self._qadr])
        self.bindings = [mb for mb in world.motors if mb.dadr == self._dadr]   # the shooter slot's motors
        for mb in self.bindings:
            mb.external = True
        self._hood_qadr = (int(m.jnt_qposadr[m.joint(f"{config.part}.hood").id])
                           if config.hood == "articulated" else None)
        shaft = config.setup.shaft
        self._emf = shaft.gearing / get_motor(shaft.motor, shaft.motor_count).Kv   # back-EMF (V) per rad/s of wheel
        self.reset()

    def reset(self) -> None:
        """A fresh launcher model: its clock at 0, the shaft at rest, no ball in it."""
        self.launcher = Launcher(self.config.setup, external_bus=True)
        self.exited: list[ExitedBall] = []
        self._set_hood()

    def hood_deg(self) -> float:
        """The hood's exit angle now (deg above horizontal): hood_range[0] + the hood joint's angle, or the fixed
        hood's hood_angle."""
        if self._hood_qadr is None:
            return self.config.hood_angle
        return self.config.hood_range[0] + math.degrees(float(self.world.data.qpos[self._hood_qadr]))

    def angle_deg(self) -> float:
        """The launch angle the model shoots at now, for the shot's log: the hood's exit angle."""
        return float(self.launcher.exit_angle_deg)

    def rps(self) -> float:
        """The shooter wheel's speed (rotations per second)."""
        return self.launcher.shaft.speed / (2.0 * math.pi)

    def _set_hood(self) -> None:
        lo, hi = self.config.hood_range
        # the joint's soft limits can overshoot its stops a little; the model's hood stays between them
        angle = self.hood_deg() if self._hood_qadr is None else min(hi, max(lo, self.hood_deg()))
        # a hood at rest still jiggles by ~1e-9 deg with the robot's motion; each change rebuilds the hood's path
        if abs(angle - self.launcher.exit_angle_deg) >= HOOD_TOLERANCE_DEG:
            self.launcher.set_hood_angle(angle)

    def _voltage(self) -> float:
        """The mean voltage on the shooter's motors (see _mean_voltage)."""
        return _mean_voltage(self.launcher.shaft.speed * self._emf, self.bindings)

    def step(self, dt: float, vbatt: float) -> list[ExitedBall]:
        """Advance the launcher model dt seconds on bus voltage vbatt; the balls that left it this step."""
        self._set_hood()
        shaft = self.launcher.shaft
        shaft.set_bus_voltage(vbatt)
        volts = self._voltage()
        self.exited = self.launcher.step(dt, volts)
        _share_currents(shaft, self.bindings, volts, vbatt)
        return self.exited

    def supply_at(self, bus: float) -> float:
        """The supply current (A) the shooter's motors would draw on bus voltage `bus` at the shaft's present speed:
        the motor current at their mean voltage (clamped to the bus) times the signed duty, like
        MotorBinding.supply_at. Negative while the shaft regenerates (World clamps the total); positive while a
        reversed voltage brakes a spinning shaft (plugging draws from the battery)."""
        return self.supply_and_slope(bus)[0]

    def supply_and_slope(self, bus: float) -> tuple[float, float]:
        """supply_at(bus) and its derivative with respect to the bus voltage (A/V), for World's Newton solve."""
        return _supply_and_slope(self.launcher.shaft, self.bindings, bus)

    def supply_current(self) -> float:
        """The shooter motors' supply current (A) for the battery."""
        return sum(mb.supply_current for mb in self.bindings)

    def mirror(self) -> None:
        """The spin joint takes the model's shaft angle and speed (after mj_step)."""
        shaft = self.launcher.shaft
        self.world.data.qpos[self._qadr] = self._q0 + shaft.angle
        self.world.data.qvel[self._dadr] = shaft.speed

    # ---- the ball path's hand-off (ballpath.BallPath)

    def positions(self) -> list[tuple[int, float, float]]:
        """The balls in the launcher model: (index, lane, s from its entry)."""
        return self.launcher.positions()

    def busy_lanes(self, d: float) -> set[float]:
        """The lanes (positions across the wheels) with a ball still within `d` of the entry."""
        return {lane for _, lane, s in self.launcher.positions() if s < d}

    def feed(self, lane: float, entry_speed: float, t: float) -> tuple[int | None, ExitedBall | None]:
        """Feed a ball in at `lane` at the launcher's time t: (its launcher index, None), or (None, the ExitedBall)
        for one the launcher model turned away at once (no_contact, no_wheel)."""
        shot = self.launcher.insert(FedBall(available_at=t, lane=lane, entry_speed=entry_speed,
                                            ball=self.config.setup.ball), t)
        if shot is not None:
            return None, shot
        return self.launcher.positions()[-1][0], None
