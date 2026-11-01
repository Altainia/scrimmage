"""Loading a robot config TOML: the world, device bindings, and how to run the robot program."""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from . import java_tools
from .teamfolders import TeamFolderError, expand
from .lockstep import DEFAULT_STEP

if TYPE_CHECKING:
    from .world import World

DEFAULT_ODOMETRY = "/SmartDashboard/Field/Robot"  # WPILib Field2d robot pose: double[3] x, y, heading deg


class ConfigError(ValueError):
    pass


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
        raise ConfigError(f"sim_mode must be one of {', '.join(SIM_MODES)}, not {mode!r}")
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

    @property
    def dir(self) -> Path:
        return self.path.parent


def _table(raw: dict, name: str) -> dict:
    v = raw.get(name, {})
    if not isinstance(v, dict):
        raise ConfigError(f"[{name}] must be a table, not {v!r}")
    return v


def _number(table: dict, key: str, default: float, section: str) -> float:
    v = table.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(f"[{section}] {key} must be a number of seconds, not {v!r}")
    return float(v)


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except OSError as e:
        raise ConfigError(f"can't read config file {path}: {e.strerror or e}") from None
    except UnicodeDecodeError as e:
        raise ConfigError(f"{path} isn't a text file: {e}") from None
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
            raise ConfigError('[robot_program] command must be a list of strings, '
                              'e.g. ["{python}", "-m", "robotpy", "sim", "--nogui", "--ws-client"]')
        env = rp.get("env", {})
        if not isinstance(env, dict):
            raise ConfigError("[robot_program] env must be a table of NAME = value")
        sim_mode = rp.get("sim_mode", "phoenix")
        if sim_mode not in SIM_MODES:
            raise ConfigError(f"[robot_program] sim_mode must be one of {', '.join(SIM_MODES)}, not {sim_mode!r}")
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
        raise ConfigError(f"[telemetry] odometry must be a NetworkTables topic name, not {odometry!r}")
    nt_port = tel.get("nt_port", 5810)
    if isinstance(nt_port, bool) or not isinstance(nt_port, int):
        raise ConfigError(f"[telemetry] nt_port must be a port number, not {nt_port!r}")

    nt = tel.get("nt", True)
    if not isinstance(nt, bool):
        raise ConfigError(f"[telemetry] nt must be true or false (false: the robot program serves no NetworkTables), "
                          f"not {nt!r}")

    sess = _table(raw, "session")
    return Config(path, raw, step, program, odometry or None, nt_port,
                  _number(sess, "step_timeout", 10.0, "session"), nt)


# Keys a binding entry reads without a default, and the entries that read them (for a KeyError's message).
_KEY_USERS = {
    "joint": "a [[motor]]/[[encoder]]/[[analog]]/[[digital]] entry",
    "device": "a [[gyro]] entry", "body": "a [[gyro]] entry",
    "port": "an [[analog]] entry", "channel": "a [[digital]] entry",
    **{k: "a [[motor]] entry's custom motor = { ... } table"
       for k in ("stall_torque", "stall_current", "free_current", "free_rpm")},
}


def build_world(config: Config) -> World:
    """The config's World, with a mistake in the config's world sections reported as a ConfigError."""
    from .world import World
    try:
        return World(config.raw, config.dir)
    except KeyError as e:
        key = e.args[0] if e.args else "?"
        raise ConfigError(f"{config.path}: missing key {key!r} in {_KEY_USERS.get(key, 'the config')}") from None
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{config.path}: {e}") from None
