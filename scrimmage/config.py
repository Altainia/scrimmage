"""Loading a robot config TOML: the world, device bindings, and how to run the robot program."""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from . import java_tools
from .driverstation import MatchSettings
from .i18n import N_, t, translated
from .teamfolders import TeamFolderError, expand
from .lockstep import DEFAULT_STEP

if TYPE_CHECKING:
    from .world import World

DEFAULT_ODOMETRY = "/SmartDashboard/Field/Robot"  # WPILib Field2d robot pose: double[3] x, y, heading deg


class ConfigError(ValueError):
    """A mistake in a robot config. `fields`: the Robot tab's (part, field) pairs it is about (see PartError), kept
    from the PartError it came from."""

    def __init__(self, message: str = "", *, fields=()):
        super().__init__(message)
        self.fields = tuple(fields)


@dataclass
class RobotProgram:
    command: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)
    start_timeout: float = 30.0
    sim_mode: str = "phoenix"   # "fast" adds SCRIMMAGE_MODE=fast to env; it changes no bindings (see set_sim_mode)


SIM_MODES = ("fast", "phoenix")
SIM_MODE_ENV = "SCRIMMAGE_MODE"


def set_sim_mode(config: Config, mode: str | None) -> None:
    """Set the robot program's sim mode ("fast" or "phoenix"; None keeps the config's). Only the robot's environment
    changes: "fast" sets SCRIMMAGE_MODE=fast, which fast-mode robot code reads through Scrimmage.fastMode(), and
    "phoenix" removes it. The bindings stay what the config says, so a config that binds Sim* devices expects
    robot code that checks Scrimmage.fastMode()."""
    if mode is None:
        return
    if mode not in SIM_MODES:
        raise ConfigError(t("sim_mode must be one of {modes}, not {mode}", modes=", ".join(SIM_MODES),
                            mode=repr(mode)))
    rp = config.robot_program
    if rp is None:
        return
    rp.sim_mode = mode
    if mode == "fast":
        rp.env[SIM_MODE_ENV] = "fast"
    else:
        rp.env.pop(SIM_MODE_ENV, None)


def sim_mode_unused(config: "Config", mode: str | None, *, launching: bool) -> str | None:
    """Why a sim mode asked for (`mode`, or the config's sim_mode = "fast") does nothing here, or None. The mode
    only sets the environment of a robot program scrimmage launches."""
    rp = config.robot_program
    if rp is None:
        if mode is None:
            return None
        return (f"sim mode {mode!r} has no effect: the config has no [robot_program], so scrimmage doesn't launch "
                "the robot program and can't set SCRIMMAGE_MODE; set it in the robot program's environment yourself")
    asked = mode if mode is not None else ("fast" if rp.sim_mode == "fast" else None)
    if launching or asked is None:
        return None
    return (f"sim mode {asked!r} has no effect: scrimmage doesn't launch the robot program here, so it can't set "
            "SCRIMMAGE_MODE; set it in the robot program's environment yourself")


@dataclass
class Config:
    path: Path
    raw: dict
    step: float = DEFAULT_STEP
    robot_program: RobotProgram | None = None
    odometry_topic: str | None = DEFAULT_ODOMETRY
    nt_port: int = 5810
    step_timeout: float = 10.0
    nt: bool = True           # [telemetry] nt = false: the robot program serves no NetworkTables, so don't wait for it
    match: MatchSettings = field(default_factory=MatchSettings)   # what a session sends as the Driver Station
    real_ds: bool = False     # [driver_station] real = true: the FRC Driver Station app drives the robot by default

    @property
    def dir(self) -> Path:
        return self.path.parent


def _table(raw: dict, name: str) -> dict:
    v = raw.get(name, {})
    if not isinstance(v, dict):
        raise ConfigError(t("[{section}] must be a table, not {value}", section=name, value=repr(v)))
    return v


def _number(table: dict, key: str, default: float, section: str) -> float:
    v = table.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(t("[{section}] {key} must be a number of seconds, not {value}", section=section, key=key,
                            value=repr(v)))
    return float(v)


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise ConfigError(t("config file not found: {path}", path=path)) from None
    except OSError as e:
        raise ConfigError(t("can't read config file {path}: {problem}", path=path, problem=e.strerror or e)) from None
    except UnicodeDecodeError as e:
        raise ConfigError(t("{path} isn't a text file: {problem}", path=path, problem=e)) from None
    return load_config_text(text, path)


def load_config_text(text: str, path: str | Path) -> Config:
    """The config parsed from TOML text; ``path`` (need not exist) is where it lives, for relative paths."""
    path = Path(path).resolve()
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None

    step = _number(_table(raw, "lockstep"), "step", DEFAULT_STEP, "lockstep")

    program = None
    rp = _table(raw, "robot_program")
    if rp:
        cmd = rp.get("command")
        if not (isinstance(cmd, list) and cmd and all(isinstance(c, str) for c in cmd)):
            raise ConfigError(t("{robot_program_table} command must be a list of strings, e.g. [\"{python_token}\", "
                                "\"{m}\", \"{robotpy}\", \"{sim}\", \"{nogui}\", \"{ws_client}\"]",
                                robot_program_table="[robot_program]", python_token="{python}", m="-m",
                                robotpy="robotpy", sim="sim", nogui="--nogui", ws_client="--ws-client"))
        env = rp.get("env", {})
        if not isinstance(env, dict):
            raise ConfigError(t("{robot_program_table} env must be a table of {name_setting}",
                                robot_program_table="[robot_program]", name_setting="NAME = value"))
        sim_mode = rp.get("sim_mode", "phoenix")
        if sim_mode not in SIM_MODES:
            raise ConfigError(t("{robot_program_table} sim_mode must be one of {modes}, not {mode}",
                                modes=", ".join(SIM_MODES), mode=repr(sim_mode),
                                robot_program_table="[robot_program]"))
        gradle = Path(cmd[0]).name in ("gradlew", "gradlew.bat")
        try:
            command = [sys.executable if c == "{python}"
                       else expand(c.replace("{scrimmage_java}", str(java_tools.JAVA_DIR))) for c in cmd]
            cwd = (path.parent / expand(str(rp.get("cwd", ".")))).resolve()
        except TeamFolderError as e:
            raise ConfigError(str(e)) from None
        program = RobotProgram(command, cwd,
                               {str(k): str(v) for k, v in env.items()},
                               _number(rp, "start_timeout", 180.0 if gradle else 30.0, "robot_program"), sim_mode)
        if sim_mode == "fast":
            program.env[SIM_MODE_ENV] = "fast"

    tel = _table(raw, "telemetry")
    odometry = tel.get("odometry", DEFAULT_ODOMETRY)
    if odometry is not None and not isinstance(odometry, str):
        raise ConfigError(t("{telemetry_table} odometry must be a NetworkTables topic name, not {value}",
                            value=repr(odometry), telemetry_table="[telemetry]"))
    nt_port = tel.get("nt_port", 5810)
    if isinstance(nt_port, bool) or not isinstance(nt_port, int):
        raise ConfigError(t("{telemetry_table} nt_port must be a port number, not {value}", value=repr(nt_port),
                            telemetry_table="[telemetry]"))

    nt = tel.get("nt", True)
    if not isinstance(nt, bool):
        raise ConfigError(t("{telemetry_table} nt must be true or false (false: the robot program serves no "
                            "NetworkTables), not {value}", value=repr(nt), telemetry_table="[telemetry]"))

    try:
        match = MatchSettings().merged(_table(raw, "match"), "[match]")
    except ValueError as e:
        raise ConfigError(str(e), fields=getattr(e, "fields", ())) from None

    ds = _table(raw, "driver_station")
    for key in ds:
        if key != "real":
            raise ConfigError(t("{driver_station_table} has no key {key}; the only key is real", key=repr(key),
                                driver_station_table="[driver_station]"))
    real_ds = ds.get("real", False)
    if not isinstance(real_ds, bool):
        raise ConfigError(t("{driver_station_table} real must be true or false (true: the FRC Driver Station app "
                            "drives the robot), not {value}", value=repr(real_ds),
                            driver_station_table="[driver_station]"))

    sess = _table(raw, "session")
    return Config(path, raw, step, program, odometry or None, nt_port,
                  _number(sess, "step_timeout", 10.0, "session"), nt, match, real_ds)


# Keys a binding entry reads without a default, and the entries that read them (for a KeyError's message).
_KEY_USERS = {      # (N_() words, said with translated() and the tables a student types in them)
    "joint": (N_("a {tables} entry"), "[[motor]]/[[encoder]]/[[analog]]/[[digital]]"),
    "device": (N_("a {tables} entry"), "[[gyro]]"), "body": (N_("a {tables} entry"), "[[gyro]]"),
    "port": (N_("an {tables} entry"), "[[analog]]"), "channel": (N_("a {tables} entry"), "[[digital]]"),
    **{k: (N_("a {tables} entry's custom {motor_setting} table"), "[[motor]]")
       for k in ("stall_torque", "stall_current", "free_current", "free_rpm")},
}


def build_world(config: Config) -> World:
    """The config's World, with a mistake in the config's world sections reported as a ConfigError."""
    from .world import World
    try:
        return World(config.raw, config.dir)
    except KeyError as e:
        key = e.args[0] if e.args else "?"
        users = _KEY_USERS.get(key)
        if users is None:
            raise ConfigError(t("{path}: missing key {key} in the config", path=config.path, key=repr(key))) from None
        raise ConfigError(t("{path}: missing key {key} in {users}", path=config.path, key=repr(key),
                            users=translated(users[0], tables=users[1], motor_setting="motor = { ... }"))) from None
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{config.path}: {e}", fields=getattr(e, "fields", ())) from None
