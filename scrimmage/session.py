"""Session: one simulation of one robot program, driven step by step.

The MCP server, the scenario runner and Python users all work through this class. A session holds the
robot program paused between calls (lockstep, with keep-alives), so callers can take as long as they like.
"""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import functools
import itertools
import logging
import math
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import mujoco

from .bindings import binds_ph, binds_rev
from .bridge import Bridge, robot_language
from .config import Config, ConfigError, RobotProgram, build_world, load_config, set_sim_mode, sim_mode_unused
from .devicecheck import check_devices, device_notes
from .driverstation import REAL_DS_WAITING, MatchSettings, check_game_data, real_ds_report
from .i18n import N_, t, translated
from .lockstep import Pacer
from .movers import Mover, mover_poses, movers
from .render import Renderer, check_sheet_size, check_size, RenderUnavailable, contact_sheet, to_png
from .robot_process import RobotProcess
from .robotcode import with_ds_socket
from .world import World

log = logging.getLogger("scrimmage")

ATTACH_TIMEOUT = 300.0   # wall seconds to wait for a robot program the user starts themselves
REALTIME_POLL = 0.02     # wall seconds between on_step calls when the robot program runs in real time
CONSOLE_LIMIT = 200      # most console lines returned by one advance
NT_CONNECT_TIMEOUT = 5.0 # wall s to wait for NetworkTables once the robot is ready ($SCRIMMAGE_NT_CONNECT_TIMEOUT)


# N_(): said with translated()
PHOENIX_UNPAUSABLE = N_("Phoenix 6 devices keep running between calls: this session can't pause them")
REAL_DS_UNPAUSABLE = N_("the real Driver Station keeps the robot running between calls: this session can't pause it")
DS_SOCKET_ENV = "SCRIMMAGE_DS_SOCKET"   # set to 1 for a real-DS launch: the Gradle init script then keeps the DS socket
DS_UDP_PORT, DS_TCP_PORT = 1110, 1740   # the DS protocol's fixed ports, where the robot listens for the real DS
REAL_DS_INPUTS = N_("this session can't change the Driver Station's inputs: the FRC Driver Station is driving")
DS_LISTEN_TIMEOUT = 30.0                # wall s an attached robot program has to listen on UDP 1110 (no start_timeout)
NOT_LISTENING = N_("the robot program isn't listening for the Driver Station (UDP 1110): RobotPy needs --ds-socket; "
                    "Java needs wpi.sim.addDriverstation()")
# scrimmage-lockstep: 0.2.0 REV helper, 0.3.0 clock at 0, 0.4.0 seed, 0.5.0 sim_values, 0.6.0 clock at 1 us (AdvantageKit)
LOCKSTEP_VERSION = (0, 6, 0)
PH_LOCKSTEP_VERSION = (0, 7, 0)   # 0.7.0 relays the REV Pneumatic Hub; needed only by robots with valves on one


def lockstep_install(python: str | None = None) -> str:
    """The command that installs (or updates) the lockstep extension, typed from any folder: `python -m pip install`
    with the full path of the checkout's lockstep/ folder (where scrimmage was installed from), so it works in a
    team's robot folder too and goes into the Python that needs it (`python`: the robot's; else scrimmage's own).
    Without the checkout (an installed wheel), the README's `pip install ./lockstep`."""
    import shlex
    import subprocess
    folder = Path(__file__).resolve().parent.parent / "lockstep"
    if not (folder / "pyproject.toml").is_file():
        return "pip install ./lockstep"
    if sys.platform == "win32":   # cmd.exe doesn't take shlex's single quotes
        return subprocess.list2cmdline([python or sys.executable, "-m", "pip", "install", str(folder)])
    return f"{shlex.quote(python or sys.executable)} -m pip install {shlex.quote(str(folder))}"


def lockstep_version_problem(config: Config) -> str | None:
    """Why a RobotPy robot run with `--scrimmage-lockstep` (or binding REV SPARKs, whose helper the package carries), or a
    Java robot while scrimmage-lockstep is installed (the Gradle init script then loads it), can't be simulated:
    scrimmage-lockstep is missing or older than this scrimmage needs. A RobotPy or Java robot with valves on a REV
    Pneumatic Hub needs PH_LOCKSTEP_VERSION, installed even for Java (only the extension relays a hub). None for any
    other config."""
    from importlib import metadata
    rp = config.robot_program
    language = robot_language(rp.command) if rp is not None else None
    need_version, ph = LOCKSTEP_VERSION, binds_ph(config.raw)
    if ph and language in ("java", "python"):
        why, need_version = "ph", PH_LOCKSTEP_VERSION
    elif language == "java":
        why = "java"     # without the extension the init script runs it in real time: nothing to check
    elif language != "python":
        return None
    elif binds_rev(config.raw):
        why = "rev"
    elif "--scrimmage-lockstep" in rp.command:
        why = "lockstep"
    else:
        return None
    said = {"config": config.path, "need": ".".join(map(str, need_version)),
            "install": lockstep_install(rp.command[0] if language == "python" and rp.command else None)}
    try:
        have = metadata.version("scrimmage-lockstep")
    except metadata.PackageNotFoundError:
        if language == "java" and not ph:
            return None
        if why == "ph":
            return t("{config} binds valves on a REV Pneumatic Hub, which needs scrimmage-lockstep {need} or later, but "
                     "it isn't installed: `{install}`", **said)
        if why == "java":
            return t("{config} runs a Java robot, which needs scrimmage-lockstep {need} or later, but it isn't "
                     "installed: `{install}`", **said)
        if why == "rev":
            return t("{config} binds REV SPARKs on a RobotPy robot, which needs scrimmage-lockstep {need} or later, but "
                     "it isn't installed: `{install}`", **said)
        return t("{config} runs a RobotPy robot with --scrimmage-lockstep, which needs scrimmage-lockstep {need} or "
                 "later, but it isn't installed: `{install}`", **said)
    nums = []
    for part in have.split(".")[:3]:
        digits = "".join(itertools.takewhile(str.isdigit, part))
        nums.append(int(digits) if digits else 0)
    if tuple(nums + [0] * (3 - len(nums))) < need_version:
        said["have"] = have
        if why == "ph":
            return t("{config} binds valves on a REV Pneumatic Hub, which needs scrimmage-lockstep {need} or later "
                     "(installed: {have}): reinstall it with `{install}`", **said)
        if why == "java":
            return t("{config} runs a Java robot, which needs scrimmage-lockstep {need} or later (installed: {have}): "
                     "reinstall it with `{install}`", **said)
        if why == "rev":
            return t("{config} binds REV SPARKs on a RobotPy robot, which needs scrimmage-lockstep {need} or later "
                     "(installed: {have}): reinstall it with `{install}`", **said)
        return t("{config} runs a RobotPy robot with --scrimmage-lockstep, which needs scrimmage-lockstep {need} or "
                 "later (installed: {have}): reinstall it with `{install}`", **said)
    return None


# A real-time advance of s seconds gives up after ADVANCE_FACTOR s + ADVANCE_EXTRA seconds of wall time, plus the time
# physics skipped ahead, and never after more than ADVANCE_MAX_FACTOR s + ADVANCE_EXTRA (physics at a third of real time)
ADVANCE_FACTOR, ADVANCE_MAX_FACTOR, ADVANCE_EXTRA = 1.5, 3.0, 5.0


class SessionError(RuntimeError):
    """A problem a caller (often an agent) can act on: the message plus the robot console's tail."""

    def __init__(self, message: str, *, t: float | None = None, console: list[str] | None = None):
        super().__init__(message)
        self.t = t
        self.console = console or []

    def to_dict(self) -> dict:
        return {"error": str(self), "t": self.t, "console_tail": self.console}


def _finite(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SessionError(t("{name} must be a finite number, not {value}", name=name, value=repr(value)))
    return float(value)


def _listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _port_free(kind: int, port: int) -> bool:
    """Can this computer's DS port be bound now (nothing listens on it)? TCP binds as libuv does (SO_REUSEADDR), so
    a closed connection's leftovers don't count."""
    with socket.socket(socket.AF_INET, kind) as sock:
        if kind == socket.SOCK_STREAM and sys.platform != "win32":   # on Windows it lets a bind succeed over a listener
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def ds_ports_problem() -> str | None:
    """Why a real-DS robot program can't listen for the Driver Station here: one of its ports is taken."""
    for kind, name, port in ((socket.SOCK_DGRAM, "UDP", DS_UDP_PORT), (socket.SOCK_STREAM, "TCP", DS_TCP_PORT)):
        if not _port_free(kind, port):
            return t("{protocol} port {port} is already in use, so the robot program can't listen for the Driver "
                     "Station: stop the other real Driver Station session or robot program (only one can listen per "
                     "computer)", protocol=name, port=port)
    return None


def real_ds_program(rp: RobotProgram) -> RobotProgram:
    """The robot program, told to listen for the real DS: RobotPy through --ds-socket, Java through the init script."""
    return dataclasses.replace(rp, command=with_ds_socket(rp.command), env={**rp.env, DS_SOCKET_ENV: "1"})


def ds_socket_problem() -> str | None:
    """NOT_LISTENING if nothing listens for the Driver Station on UDP 1110. Check it once the robot program has
    started: binding the port while it starts could take it from the robot."""
    return translated(NOT_LISTENING) if _port_free(socket.SOCK_DGRAM, DS_UDP_PORT) else None


def connect_timeout_hint(command: list[str], cwd) -> str:
    """What to add to "the robot program didn't connect" when the command is Gradle's wrapper."""
    if Path(command[0]).name not in ("gradlew", "gradlew.bat"):
        return ""
    hint = "; " + t("first Gradle runs download dependencies: run `{gradlew_build}` once in {cwd}, or raise "
                    "{robot_program_table} start_timeout", cwd=cwd, gradlew_build="./gradlew build",
                    robot_program_table="[robot_program]")
    if not any("scrimmage.init.gradle" in c for c in command):
        from . import java_tools
        hint += "; " + t("Java projects run through `{i_java_scrimmage}` (in the command, as `{i_scrimmage_java}`)",
                         i_java_scrimmage="-I {}/scrimmage.init.gradle".format(java_tools.JAVA_DIR),
                         i_scrimmage_java="-I {scrimmage_java}/scrimmage.init.gradle")
    return hint


class Session:
    def __init__(self, config: Config | str | Path, *, attach: bool = False, viewer: bool = False,
                 speed: float | None = None, nt: bool = True, port: int | None = None,
                 log_path: Path | None = None, robot_log: Path | None = None, picture_sizes=(),
                 sim_mode: str | None = None, real_ds: bool | None = None):
        """`picture_sizes`: (width, height) pairs pictures will be taken at; their renderers are built during start(),
        before the robot program runs (building one holds Python's GIL, which would stall a held robot's keep-alives
        for seconds with slow software GL). `real_ds`: the FRC Driver Station app drives the robot instead of
        scrimmage (None: the config's [driver_station] real)."""
        self.config = config if isinstance(config, Config) else load_config(config)
        if sim_mode is not None and self.config is config and config.robot_program is not None:
            # the caller's Config stays as it was: set_sim_mode changes the robot program's mode and environment
            rp = config.robot_program
            self.config = dataclasses.replace(config, robot_program=dataclasses.replace(rp, env=dict(rp.env)))
        set_sim_mode(self.config, sim_mode)    # "fast" | "phoenix" | None (keep the config's)
        if (unused := sim_mode_unused(self.config, sim_mode, launching=not attach)) is not None:
            log.warning(unused)
        if not attach and self.config.robot_program is None:
            raise SessionError(t("{config} has no {robot_program_table} section: add one (see configs/kitbot.toml), "
                                 "or attach to a robot program you start yourself. A match without robot code is a "
                                 "Test-tab test with {test_table} {match_setting}", config=self.config.path,
                                 robot_program_table="[robot_program]", test_table="[test]",
                                 match_setting="match = true"))
        self.real_ds = self.config.real_ds if real_ds is None else bool(real_ds)
        rp = self.config.robot_program
        if self.real_ds and rp is not None and not attach:
            rp = real_ds_program(rp)
            self.config = dataclasses.replace(self.config, robot_program=rp)
        self._ds_notes: list[str] = []          # the real-DS listening check's note
        self._ds_watch: asyncio.Task | None = None
        if rp is not None and not attach:
            from . import java_tools
            missing = java_tools.agent_missing_message(self.config.path, rp.command,
                                                       rev=binds_rev(self.config.raw))
            if missing:
                raise SessionError(missing)
        self.attach, self.viewer, self.speed = attach, viewer, speed
        self.nt = nt and self.config.nt     # a config with [telemetry] nt = false has no NetworkTables to wait for
        # simulated and wall seconds spent advancing in real time with Phoenix 6 devices (see phoenix_speed)
        self._phoenix_sim = self._phoenix_wall = 0.0
        self._unpausable_said = False  # the first advance with Phoenix 6 devices warns that they keep running
        self.port = port if port is not None else (3300 if attach else 0)
        self.host = "localhost" if attach else "127.0.0.1"  # a launched robot is told 127.0.0.1
        self.log_path, self.robot_log = log_path, robot_log
        self.picture_sizes = [tuple(sz) for sz in picture_sizes]
        self.world: World | None = None
        self.bridge: Bridge | None = None
        self.robot: RobotProcess | None = None
        self._task: asyncio.Task | None = None
        self._renderer: Renderer | None = None
        self._render_pool: ThreadPoolExecutor | None = None  # one thread owns the GL context and does every draw
        self._movers: list[Mover] | None = None
        self._lock = asyncio.Lock()  # one caller drives the simulation at a time

    # ------------------------------------------------------------------ properties
    @property
    def t(self) -> float:
        return self.world.time if self.world is not None else 0.0

    @property
    def lockstep(self) -> bool:
        return self.bridge is not None and self.bridge.mode == "lockstep"

    # ------------------------------------------------------------------ lifecycle
    async def start(self, *, match: MatchSettings | None = None, game_data: str = "") -> dict:
        """`match` and `game_data`: what the Driver Station sends from the robot's first frame (default: the config's
        [match] and no game data); the Drive tab's restart passes the match it had."""
        cfg = self.config
        if self._task is not None:
            raise SessionError(t("this session has already been started"))
        if not self.attach and (problem := lockstep_version_problem(cfg)):
            raise SessionError(problem)
        if self.real_ds and not self.attach and (problem := ds_ports_problem()):
            raise SessionError(problem)
        if not self.attach and self.nt and _listening("127.0.0.1", cfg.nt_port):
            # The robot program we launch would serve NT there too, and each would read the other's values.
            raise SessionError(t("NetworkTables port {port} is already in use (another robot program running?); stop "
                                 "it, or set {telemetry_table} nt_port", port=cfg.nt_port,
                                 telemetry_table="[telemetry]"))
        try:
            self.world = build_world(cfg)
        except ConfigError as e:
            raise SessionError(str(e)) from None
        try:
            self.bridge = Bridge(self.world, self.host, self.port, held=True, viewer=self.viewer, nt=self.nt,
                                 log_path=self.log_path, step=cfg.step, speed=None,
                                 odometry_topic=cfg.odometry_topic, nt_port=cfg.nt_port,
                                 sim_mode=cfg.robot_program.sim_mode if cfg.robot_program is not None else None,
                                 real_ds=self.real_ds)
        except ValueError as e:
            raise SessionError(f"{cfg.path}: {e}") from None
        self.bridge.ds.active = not self.real_ds   # the real DS's own packets reach the robot program
        self.bridge.ds.match = match if match is not None else cfg.match
        if game_data:
            self.bridge.ds.set_game_data(game_data)
        self.bridge.agent_state = self.agent_state
        self.bridge.rev_state = self.rev_state
        self.bridge.grapple_state = self.grapple_state
        self.bridge.robot_language = self.robot_language()
        self._task = asyncio.create_task(self.bridge.run())
        try:
            listening = asyncio.ensure_future(self.bridge.listening.wait())
            try:
                done, _ = await asyncio.wait({self._task, listening}, timeout=10,
                                             return_when=asyncio.FIRST_COMPLETED)
            finally:
                listening.cancel()
            if self._task in done or not self.bridge.listening.is_set():
                if self._task.done() and not self._task.cancelled() and self._task.exception() is not None:
                    raise SessionError(t("couldn't start the HALSim server on port {port}: {problem}",
                                         port=self.port, problem=self._task.exception()))
                if self._task.done():
                    raise SessionError(t("couldn't start the HALSim server on port {port}", port=self.port))
                raise SessionError(t("the HALSim server didn't start listening on port {port} within 10 s",
                                     port=self.port))
            if self.picture_sizes:
                await asyncio.get_running_loop().run_in_executor(self._pool(), contextvars.copy_context().run,
                                                           self._prepare_pictures)
            if not self.attach:
                await self._launch()
            await self._wait_ready()
            if self.attach:
                rp = cfg.robot_program
                self._watch_ds_socket(rp.start_timeout if rp is not None else DS_LISTEN_TIMEOUT)
            await self._wait_nt()
        except BaseException:
            await asyncio.shield(self.stop())  # the SessionError already carries the console tail
            raise
        return {**self.status(), "console": self._lines(0)}

    async def _launch(self) -> None:
        rp = self.config.robot_program
        if self.robot is None:
            self.robot = RobotProcess(rp.command, rp.cwd, rp.env, clock=lambda: self.t, log_path=self.robot_log)
        try:
            await self.robot.start(self.bridge.bound_port)
        except RuntimeError as e:
            raise SessionError(str(e)) from None
        self._watch_ds_socket(rp.start_timeout)

    def _watch_ds_socket(self, timeout: float) -> None:
        """Real DS: once `timeout` wall seconds have passed, check that the robot program listens on UDP 1110 (the
        port can't be bound), and say what it needs if not. Checked once, late: binding it while the robot program
        starts could take it from the robot."""
        if not self.real_ds:
            return
        if self._ds_watch is not None:
            self._ds_watch.cancel()
        self._ds_notes.clear()

        async def watch():
            await asyncio.sleep(timeout)
            if (note := ds_socket_problem()) is not None:
                log.warning("%s", note)
                self._ds_notes.append(note)
        self._ds_watch = asyncio.create_task(watch())

    async def _wait_ready(self) -> None:
        b = self.bridge
        timeout = ATTACH_TIMEOUT if self.attach else self.config.robot_program.start_timeout
        deadline = time.monotonic() + timeout
        while b.mode is None:
            if self.robot is not None and not self.robot.running:
                await self.robot.wait_exited(2.0)  # let the console reader catch the last lines
                raise self._error(t("the robot program exited (code {code}) before it connected",
                                    code=self.robot.returncode))
            left = deadline - time.monotonic()
            if left <= 0:
                if self.attach:
                    raise self._error(t("no robot program connected to port {port} within {seconds} s",
                                        port=b.bound_port, seconds=f"{timeout:g}"))
                msg = t("the robot program didn't connect within {seconds} s (command: {command} in {cwd})",
                        seconds=f"{timeout:g}", command=" ".join(self.config.robot_program.command),
                        cwd=self.config.robot_program.cwd)
                msg += connect_timeout_hint(self.config.robot_program.command, self.config.robot_program.cwd)
                raise self._error(msg)
            b.mode_changed.clear()
            if b.mode is not None:
                break
            try:
                await asyncio.wait_for(b.mode_changed.wait(), min(left, 0.2))
            except TimeoutError:
                pass

    async def _wait_nt(self) -> None:
        """Give NetworkTables time to connect, so the first NT reads aren't empty. Not an error if it doesn't:
        some robot programs don't serve NetworkTables, and scenarios that don't read it must still run."""
        link = self.bridge.nt_link
        if link is None:
            return
        try:
            timeout = float(os.environ.get("SCRIMMAGE_NT_CONNECT_TIMEOUT", NT_CONNECT_TIMEOUT))
        except ValueError:
            timeout = NT_CONNECT_TIMEOUT
        deadline = time.monotonic() + timeout
        while not link.connected:
            if self.robot is not None and not self.robot.running:
                return  # the next call reports the exit
            if time.monotonic() >= deadline:
                log.warning("NetworkTables didn't connect to the robot program (port %d) within %g s; "
                            "NT reads will be empty", self.config.nt_port, timeout)
                return
            await asyncio.sleep(0.05)

    async def stop(self) -> None:
        if self._ds_watch is not None:
            self._ds_watch.cancel()
        if self.robot is not None:
            await self.robot.stop()
            self.robot.close()
        if self.bridge is not None:
            self.bridge.done.set()
        if self._task is not None:
            task, self._task = self._task, None
            try:
                await asyncio.wait_for(task, 5)
            except asyncio.CancelledError:
                cur = asyncio.current_task()
                if cur is not None and cur.cancelling():
                    raise  # stop() itself was cancelled
            except Exception:
                pass
        if self._render_pool is not None:
            pool, self._render_pool = self._render_pool, None
            renderer, self._renderer = self._renderer, None
            if renderer is not None:
                await asyncio.get_running_loop().run_in_executor(pool, renderer.close)
            pool.shutdown(wait=False)

    async def restart_robot(self, env: dict | None = None) -> dict:
        """Restart the robot program; `env` (NAME -> text) is added to the config's environment for this launch
        (a later restart without it goes back to the config's)."""
        async with self._lock:
            return await self._restart_robot(env)

    async def _restart_robot(self, env: dict | None = None) -> dict:
        b = self._require_started()
        if self.robot is None and not self.attach:
            raise SessionError(t("the session has no robot program to restart"))
        if not self.real_ds:   # the real DS keeps its own match data; scrimmage's DS is inactive
            game_data, sent = b.ds.game_data, b.ds.sends_match_data
            b.ds.clear()
            # the field's data stays for the match, so the new program hears it on its first frame (M2); an attached
            # program isn't restarted and keeps what its HAL last heard, so it is sent the reset's game data even
            # when that is empty (a scenario clears it before this reset)
            if game_data or (self.attach and sent):
                b.ds.set_game_data(game_data)
        if self.attach:
            if env:
                raise SessionError(t("attach mode: scrimmage doesn't launch the robot program, so it can't set its "
                                     "environment; set the variables yourself"))
            b._restart_clock()
            return {**self.status(), "note": t("attach mode: the world was reset; restart the robot program yourself")}
        await self.robot.stop()
        self.robot.env = {**self.config.robot_program.env, **{str(k): str(v) for k, v in (env or {}).items()}}
        deadline = time.monotonic() + 5
        while b.ws is not None and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        if b.ws is not None:
            raise SessionError(t("the old robot program didn't disconnect within 5 s"))
        first = self.robot.next_index
        try:
            await self._launch()
            await self._wait_ready()
            await self._wait_nt()
        except BaseException:
            await asyncio.shield(self.robot.stop())
            raise
        return {**self.status(), "console": self._lines(first)}

    # ------------------------------------------------------------------ time
    async def advance(self, seconds: float, on_step: Callable[[float], None] | None = None,
                      stop: Callable[[], bool] | None = None) -> dict:
        """Run the simulation forward by `seconds` of simulated time, then hold it again.
        on_step(t) is called after every step (every lockstep step, or every 20 ms in real time).
        stop() is checked after every step; when it returns true the advance ends early."""
        async with self._lock:
            return await (self._advance(seconds, on_step) if stop is None else self._advance(seconds, on_step, stop))

    async def _advance(self, seconds: float, on_step: Callable[[float], None] | None,
                       stop: Callable[[], bool] | None = None) -> dict:
        if not (isinstance(seconds, (int, float)) and math.isfinite(seconds) and seconds > 0):
            raise SessionError(t("seconds must be a positive number, not {value}", value=repr(seconds)))
        self._require_started()
        self._check_alive()
        b, w = self.bridge, self.world
        first = self.robot.next_index if self.robot is not None else 0
        driver = b.driver
        target = w.time + seconds - 1e-9
        realtime = not (b.mode == "lockstep" and driver is not None)
        if not realtime:
            n = max(1, round(seconds / self.config.step))
            pacer = Pacer(self.speed)
            pacer.start(w.time)
            for _ in range(n):
                r = await driver.step_once(timeout=self.config.step_timeout)
                if r == "released":   # Phoenix 6 devices appeared: the rest runs in real time
                    realtime = True
                    break
                if r == "timeout":
                    raise self._error(t("the robot program stopped responding at t={t} (no step acknowledgement for "
                                        "{seconds} s)", t=f"{w.time:.3f}", seconds=f"{self.config.step_timeout:g}"))
                if r == "done" or b.done.is_set():
                    raise self._error(t("the session is stopping (at t={t})", t=f"{w.time:.3f}"))
                if r != "ok":
                    await self._settle()
                    raise self._error(t("the robot program disconnected at t={t}", t=f"{w.time:.3f}"))
                if on_step is not None:
                    on_step(w.time)
                if stop is not None and stop():
                    break
                delay = pacer.delay(w.time)
                if delay > 0:
                    await asyncio.sleep(delay)
        if realtime:
            started, sim0, skipped0 = time.monotonic(), w.time, b.physics_skipped
            # Behind real time, physics skips ahead (the bridge) and never makes that time up, so the time it skipped
            # is credited: a slow machine's run finishes (and its report says physics fell behind) instead of being
            # cut off. ADVANCE_MAX_FACTOR bounds it all the same, so a run that stops making progress still ends.
            budget, hard = seconds * ADVANCE_FACTOR + ADVANCE_EXTRA, seconds * ADVANCE_MAX_FACTOR + ADVANCE_EXTRA
            while w.time < target:
                if stop is not None and stop():
                    break
                if b.ws is None:
                    await self._settle()
                    raise self._error(t("the robot program disconnected at t={t}", t=f"{w.time:.3f}"))
                spent = time.monotonic() - started
                skipped = max(b.physics_skipped - skipped0, 0.0)   # a reset mid-advance zeroes the count
                if spent > min(budget + skipped, hard):
                    raise self._error(t("physics reached t={t} after {spent} s of wall time (target t={target}; it "
                                        "skipped {skipped} s ahead)", t=f"{w.time:.3f}", spent=f"{spent:.1f}",
                                        target=f"{target + 1e-9:.3f}", skipped=f"{skipped:.2f}"))
                await asyncio.sleep(REALTIME_POLL)
                if on_step is not None:
                    on_step(w.time)
            if b.phoenix:   # how well physics kept pace with the wall clock (see phoenix_speed)
                self._phoenix_sim += w.time - sim0
                self._phoenix_wall += time.monotonic() - started
        result = {**self.state(), "console": self._lines(first)}
        warnings = []
        if b.lockstep_released_at is not None:
            warnings.append(self.release_message())
        if self._unpausable() and not self._unpausable_said:
            self._unpausable_said = True
            warnings.append(self._unpausable_message())
        if warnings:
            result["warning"] = "; ".join(warnings)
        return result

    def _unpausable(self) -> bool:
        """A held session that keeps running between calls: its Phoenix 6 devices or the real DS follow the wall
        clock."""
        b = self.bridge
        return b is not None and b.held and b.frees_clock

    def _unpausable_message(self) -> str:
        return translated(PHOENIX_UNPAUSABLE if self.bridge.phoenix else REAL_DS_UNPAUSABLE)

    def release_message(self) -> str:
        return t("the robot program's clock ran free at t={t} (scrimmage stalled for more than 2 s); results aren't "
                 "repeatable", t=f"{self.bridge.lockstep_released_at:.3f}")

    # ------------------------------------------------------------------ driver inputs
    def _not_real_ds(self) -> None:
        if self.real_ds:
            raise SessionError(translated(REAL_DS_INPUTS))

    def set_mode(self, mode: str) -> dict:
        self._require_started()
        self._not_real_ds()
        try:
            self.bridge.ds.set_mode(mode)
        except ValueError as e:
            raise SessionError(str(e)) from None
        return self.state()

    def set_joystick(self, port: int, axes=None, buttons=None, povs=None) -> dict:
        self._require_started()
        self._not_real_ds()
        try:
            self.bridge.ds.set_joystick(port, axes, buttons, povs)
        except ValueError as e:
            raise SessionError(str(e)) from None
        return self.state()

    def set_match(self, station=None, fms=None, timer=None, game_data=None, auto_seconds=None,
                  teleop_seconds=None) -> dict:
        """Change any of the match settings and the game data (None keeps each); sent on the next DS frame."""
        ds = self._require_started().ds
        self._not_real_ds()
        changes = {k: v for k, v in (("station", station), ("fms", fms), ("timer", timer),
                                     ("auto_seconds", auto_seconds), ("teleop_seconds", teleop_seconds))
                   if v is not None}
        try:
            match = ds.match.merged(changes, "set_match:")      # check everything before changing anything
            if game_data is not None:
                check_game_data(game_data, "set_match:")
            ds.use_match(match)
            if game_data is not None:
                ds.set_game_data(game_data)
        except ValueError as e:
            raise SessionError(str(e)) from None
        return self.state()

    # ------------------------------------------------------------------ pokes (physics only; robot code isn't told)
    def set_pose(self, x: float, y: float, heading_deg: float) -> dict:
        """Teleport the robot. Physics only: robot code isn't told directly, but its gyro sees the heading jump."""
        self._require_started()
        x, y, heading_deg = _finite("x", x), _finite("y", y), _finite("heading_deg", heading_deg)
        try:
            self.world.set_robot_pose(x, y, heading_deg)
        except ValueError as e:
            raise SessionError(str(e)) from None
        return self.state()

    async def push(self, force_n: float, direction_deg: float, duration_s: float) -> dict:
        """Push the robot with a horizontal force for duration_s of simulated time (this advances time)."""
        self._require_started()
        force_n, direction_deg = _finite("force_n", force_n), _finite("direction_deg", direction_deg)
        duration_s = _finite("duration_s", duration_s)
        if duration_s <= 0:
            raise SessionError(t("duration_s must be a positive number, not {value}", value=repr(duration_s)))
        a = math.radians(direction_deg)
        self.world.set_robot_force(force_n * math.cos(a), force_n * math.sin(a))
        try:
            return await self.advance(duration_s)
        finally:
            self.world.set_robot_force(0.0, 0.0)

    def move_body(self, name: str, x: float, y: float, z: float | None = None, yaw_deg: float = 0.0) -> dict:
        self._require_started()
        x, y, yaw_deg = _finite("x", x), _finite("y", y), _finite("yaw_deg", yaw_deg)
        w = self.world
        if z is None:
            z = w.object_z(name)
            if z is None:
                z = 0.0  # not movable: move_object raises the "no movable object" error below
        else:
            z = _finite("z", z)
        try:
            w.move_object(name, x, y, z, yaw_deg)
        except ValueError as e:
            raise SessionError(str(e)) from None
        return self.state()

    # ------------------------------------------------------------------ reads
    def reset_robot(self) -> dict:
        """Put the robot and its mechanisms back at the start pose (field objects stay). Physics only."""
        self._require_started()
        self.world.reset_robot()
        return self.state()

    # ------------------------------------------------------------------ live view helpers
    def movers(self) -> list[Mover]:
        self._require_started()
        if self._movers is None:
            self._movers = movers(self.world)
        return self._movers

    def mover_poses(self):
        return mover_poses(self.world, self.movers())

    @property
    def phoenix_speed(self) -> float | None:
        """How well physics kept pace with the wall clock while Phoenix 6 devices ran it in real time: simulated
        seconds per wall second over those advances (physics skips ahead when it falls behind, so below 1 it lagged);
        None until at least 0.5 s has been simulated that way."""
        if self._phoenix_sim < 0.5 or self._phoenix_wall <= 0:
            return None
        return self._phoenix_sim / self._phoenix_wall

    def set_speed(self, speed: float | None) -> None:
        """Change the pacing (positive finite, or None for as fast as possible); same rule as --speed."""
        if speed is not None:
            speed = _finite("speed", speed)
            if speed <= 0:
                raise SessionError(t("speed must be positive, not {speed}", speed=f"{speed:g}"))
            step = self.config.step
            if step / speed >= 1.0:
                raise SessionError(t("--speed {speed} with {step} s steps: each step would take {wall} s of wall time "
                                     "or more, and the robot's 2 s watchdog would release the clock",
                                     speed=f"{speed:g}", step=f"{step:g}", wall=f"{step / speed:g}"))
        self.speed = speed
        if self.bridge is not None:
            self.bridge.speed = speed

    def state(self) -> dict:
        w = self.world
        self._require_started()
        x, y, yaw = w.robot_pose()
        if self.real_ds:    # what the real DS is doing, per the robot program's own reports
            # match.silent says the DS went quiet; driver_station keeps its two values (agents compare them)
            report = real_ds_report(self.bridge.hal, self.bridge.ds_quiet())
            ds = {"mode": report["mode"], "match": report, "real_ds": True,
                  "driver_station": "connected" if report["connected"] else REAL_DS_WAITING}
        else:
            ds = {"mode": self.bridge.ds.mode,
                  "match": {**dataclasses.asdict(self.bridge.ds.match), "game_data": self.bridge.ds.game_data,
                            "match_time": self.bridge.ds.sent_match_time},
                  "real_ds": False}
        return {
            "t": round(w.time, 4),
            **ds,
            "lockstep": self.lockstep,
            "lockstep_released_at": self._released_at(),
            "pose": {"x": round(x, 4), "y": round(y, 4), "heading_deg": round(math.degrees(yaw), 2)},
            "speed": round(w.robot_speed(), 3),
            "battery": round(w.vbatt, 3),
            "motors": {m.name: {"command": round(m.command, 4), "current": round(m.current, 2)} for m in w.motors},
            "joints": {n: {"position": round(p, 5), "velocity": round(v, 4)} for n, (p, v) in w.joint_states().items()},
            **self._phoenix_status(),
            **({"range": {k: v for k, v in w.test_range.counts().items() if k != "ledger"}}
               if w.test_range is not None else {}),
            **({"pneumatics": w.pneumatics.state()} if w.pneumatics is not None else {}),
            **({"partner": w.partner.state()} if getattr(w, "partner", None) is not None else {}),
            **({"sensors": w.sensors.state()} if getattr(w, "sensors", None) is not None else {}),
        }

    def hal(self, device_filter: str = "") -> dict:
        self._require_started()
        f = device_filter.lower()
        return {f"{typ}/{dev}": dict(vals) for (typ, dev), vals in sorted(self.bridge.hal.devices.items())
                if f in f"{typ}/{dev}".lower()}

    def console(self, since: int = 0) -> dict:
        self._require_started()
        nxt = self.robot.next_index if self.robot is not None else 0
        return {"t": round(self.t, 4), "lines": self._lines(since), "next": nxt}

    def status(self) -> dict:
        b = self.bridge
        if b is not None:
            b.settle_foreign_writes()  # judge writes from the last robot loop too
        if self.attach:
            robot = "attached"
        elif self.robot is None:
            robot = None
        else:
            robot = {"running": self.robot.running, "returncode": self.robot.returncode,
                     "command": self.robot.command}
        return {"t": round(self.t, 4), "lockstep": self.lockstep, "lockstep_released_at": self._released_at(),
                "real_ds": self.real_ds,
                "connected": b is not None and b.ws is not None,
                "port": b.bound_port if b is not None else None, "robot": robot,
                "devices": sorted(f"{t}/{d}" for t, d in b.hal.devices) if b is not None else [],
                "warnings": [*self.world.warnings, *self.phoenix_warnings(), *self._ds_notes],
                "rev_state": self.rev_state(),
                **self._device_status(), **self._phoenix_status()}

    def _device_status(self) -> dict:
        """Expected devices beside the ones the robot code made: the rows, and plain lines about the problems."""
        if self.bridge is None:
            return {"device_check": [], "device_notes": []}
        rows = check_devices(self.world, self.bridge.hal, self.t)
        return {"device_check": [r.as_dict() for r in rows], "device_notes": device_notes(rows)}

    def _phoenix_status(self) -> dict:
        b = self.bridge
        return {"realtime_reason": b.realtime_reason if b is not None else None,
                "unpausable": self._unpausable(),
                "foreign_writes": [dict(f) for f in b.foreign_writes.values()] if b is not None else [],
                "agent_summary": self.agent_summary()}

    def phoenix_warnings(self) -> list[str]:
        """What to say about this session's Phoenix 6 devices: they can't be paused, and they don't belong in fast
        mode."""
        b = self.bridge
        out = [self._unpausable_message()] if self._unpausable() else []
        if b is not None and (msg := b.fast_phoenix_message) is not None:
            out.append(msg)
        return out

    def agent_summary(self) -> str | None:
        """The last summary of the robot-side sim writes the Java agent or RobotPy REV helper ignored, from the
        console."""
        return self.robot.agent_summary if self.robot is not None else None

    def agent_state(self) -> str | None:
        """The Java agent or RobotPy REV helper per the console: "loaded", "disabled", "missing" (no agent line),
        or None (attached: the console isn't visible)."""
        return self.robot.agent_state() if self.robot is not None else None

    def rev_state(self) -> str | None:
        """"unavailable: <reason>" once the Java agent or RobotPy REV helper says it can't simulate REV SPARKs."""
        return self.robot.rev_state if self.robot is not None else None

    def grapple_state(self) -> str | None:
        """"unavailable: <reason>" once the Grapple LaserCAN helper (Java agent or RobotPy lockstep) says it can't feed
        LaserCANs."""
        return self.robot.grapple_state if self.robot is not None else None

    def rev_problem(self) -> str | None:
        """The error to show when the config binds REV SPARKs but the agent can't simulate them (REVLib too old)."""
        state = self.rev_state()
        if not state or not state.startswith("unavailable") or not binds_rev(self.config.raw):
            return None
        from .bridge import rev_unavailable_message
        return rev_unavailable_message(state, self.robot_language())

    def robot_language(self) -> str | None:
        """"python" / "java" per the robot program command in the config, None when unknown or absent."""
        rp = self.config.robot_program
        return robot_language(rp.command) if rp is not None else None

    def _released_at(self) -> float | None:
        t = self.bridge.lockstep_released_at if self.bridge is not None else None
        return None if t is None else round(t, 4)

    # ------------------------------------------------------------------ pictures
    def _render_job(self, view: str, width: int, height: int, paths: bool, camera: dict | None):
        """Validate and snapshot here (the simulation's thread); return the draw to run on the render thread. Drawing
        from a copy of the state lets the event loop keep the robot's keep-alives going (a held robot program frees
        its clock after 2 s without them) and, in real time, keeps physics running while a picture is drawn."""
        try:
            width, height = check_size("width", width), check_size("height", height)
        except ValueError as e:
            raise SessionError(str(e)) from None
        self._require_started()
        p = None
        if paths:
            p = self.bridge.recorder.paths()
            x, y, _ = self.world.robot_pose()
            p["truth"] = p["truth"] + [(x, y)]
        m = self.world.model
        snap = mujoco.MjData(m)
        mujoco.mj_copyData(snap, m, self.world.data)

        def draw():
            if self._renderer is None:
                self._renderer = Renderer(self.world)
            try:
                return self._renderer.render(view, width=width, height=height, paths=p, camera=camera, data=snap)
            except (RenderUnavailable, ValueError) as e:
                raise SessionError(str(e)) from None
        return functools.partial(contextvars.copy_context().run, draw)   # the caller's language guard (MCP's) too

    def _prepare_pictures(self) -> None:   # on the render thread
        if self._renderer is None:
            self._renderer = Renderer(self.world)
        self._renderer.prepare(self.picture_sizes)

    def _pool(self) -> ThreadPoolExecutor:
        if self._render_pool is None:
            self._render_pool = ThreadPoolExecutor(1, thread_name_prefix="scrimmage-render")
        return self._render_pool

    def render(self, view: str = "follow", *, width: int = 960, height: int = 600, paths: bool = True,
               camera: dict | None = None):
        """The picture now, as an RGB array (blocks the caller while it's drawn; see render_async)."""
        return self._pool().submit(self._render_job(view, width, height, paths, camera)).result()

    async def render_async(self, view: str = "follow", *, width: int = 960, height: int = 600, paths: bool = True,
                           camera: dict | None = None):
        """render() drawn on the render thread while the event loop keeps running."""
        job = self._render_job(view, width, height, paths, camera)
        return await asyncio.get_running_loop().run_in_executor(self._pool(), job)

    def screenshot(self, view: str = "follow", *, width: int = 960, height: int = 600, paths: bool = True,
                   camera: dict | None = None) -> bytes:
        return to_png(self.render(view, width=width, height=height, paths=paths, camera=camera))

    async def screenshot_png(self, view: str = "follow", *, width: int = 960, height: int = 600, paths: bool = True,
                             camera: dict | None = None) -> bytes:
        """screenshot() drawn and encoded off the event loop, so a large picture (or slow software GL) doesn't stall
        the keep-alives that hold the robot paused."""
        img = await self.render_async(view, width=width, height=height, paths=paths, camera=camera)
        return await asyncio.to_thread(to_png, img)

    async def contact_sheet(self, seconds: float, frames: int = 8, view: str = "follow", *, width: int = 480,
                            height: int = 300, paths: bool = True, camera: dict | None = None) -> bytes:
        """Advance `seconds`, capturing `frames` pictures at equal steps, and tile them into one image."""
        self._require_started()
        seconds = _finite("seconds", seconds)
        if seconds <= 0:
            raise SessionError(t("seconds must be a positive number, not {value}", value=repr(seconds)))
        if isinstance(frames, bool) or not isinstance(frames, int) or not 1 <= frames <= 16:
            raise SessionError(t("frames must be a whole number from 1 to 16, not {value}", value=repr(frames)))
        try:
            check_size("width", width), check_size("height", height)
            check_sheet_size(frames, width, height)
        except ValueError as e:
            raise SessionError(str(e)) from None
        await self.render_async(view, width=width, height=height, paths=paths, camera=camera)  # fail before advancing
        shots = []
        for _ in range(frames):
            await self.advance(seconds / frames)
            shots.append((self.t, await self.render_async(view, width=width, height=height, paths=paths,
                                                          camera=camera)))
        return await asyncio.to_thread(lambda: to_png(contact_sheet(shots)))

    # ------------------------------------------------------------------ NetworkTables
    def _nt(self):
        self._require_started()
        link = self.bridge.nt_link
        if link is None:
            raise SessionError(t("NetworkTables isn't available in this session (install {nt_extra}, and don't start "
                                 "the session with {nt_off})", nt_extra="scrimmage[nt]", nt_off="nt=False"))
        return link

    def nt_list(self, prefix: str = "/") -> dict:
        self._require_started()
        return {"t": round(self.t, 4), "topics": self._nt().topics(prefix)}

    def nt_get(self, topics: list[str]) -> dict:
        self._require_started()
        link = self._nt()
        values, missing = {}, []
        for name in topics:
            try:
                values[name] = link.get(name)
            except KeyError:
                missing.append(name)
        return {"t": round(self.t, 4), "values": values, "missing": missing}

    def nt_value(self, topic: str):
        self._require_started()
        try:
            return self._nt().get(topic)
        except KeyError:
            return None

    # ------------------------------------------------------------------ helpers
    def _lines(self, first: int) -> list[dict]:
        if self.robot is None:
            return []
        return [{"t": round(line.t, 3), "text": line.text} for line in self.robot.since(first)][-CONSOLE_LIMIT:]

    def _error(self, message: str) -> SessionError:
        if self.robot is not None:  # init-script warnings explain many start failures; the tail can scroll them away
            warns = list(dict.fromkeys(l.text for l in self.robot.lines if l.text.startswith("scrimmage: WARNING")))
            if warns:
                message += "\n" + "\n".join(warns)
        return SessionError(message, t=round(self.t, 4), console=self.robot.tail(30) if self.robot is not None else [])

    def _require_started(self) -> Bridge:
        if self._task is None or self.bridge is None or self.world is None:
            raise SessionError(t("the session isn't started"))
        return self.bridge

    async def _settle(self) -> None:
        """After a lost connection: let a dead robot program's last console lines arrive, then raise if it died."""
        if self.robot is not None and self.robot.proc is not None:
            await self.robot.wait_exited(2.0)
        self._check_alive()

    def _check_alive(self) -> None:
        if self.robot is not None and not self.robot.running:
            raise self._error(t("the robot program exited (code {code}) at t={t}; call restart_robot to launch it "
                                "again", code=self.robot.returncode, t=f"{self.t:.3f}"))
        if self.bridge is None or self.bridge.ws is None or self.bridge.mode is None:
            raise self._error(t("no robot program is connected"))
