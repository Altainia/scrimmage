"""Live drive sessions: one robot program driven from the browser (inputs, DS mode, pause / step / speed, pokes,
recording), on a worker thread with its own asyncio loop, publishing frames and state for a live view."""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import logging
import math
import struct
import threading
import time
from pathlib import Path
from typing import Callable

from .. import i18n, robot_process
from ..bridge import MISSING_AFTER
from ..checks import consume_peaks, resolve_signal
from ..devicecheck import check_devices
from ..driverstation import MATCH_KEYS, REAL_DS_DRIVING, REAL_DS_WAITING, STATIONS, MatchSettings, check_game_data
from ..session import REAL_DS_INPUTS, Session
from ..tracks import PortInputs
from .recorder import DriveRecorder, RecordingError, RecordingExists
from .simlock import SimLock

log = logging.getLogger("scrimmage")

FRAME_HEAD = struct.Struct("<IBdH")   # seq, source (0 = live), t, number of movers; then n x 7 float32
SOURCE_LIVE = 0
CONSOLE_LINES = 200
SLICE = 0.1          # sim seconds per advance while running (commands cut it short)
MODES = ("auto", "teleop", "test")
MAX_AXES, MAX_BUTTONS, MAX_POVS = 12, 32, 12   # what a WPILib joystick can carry
MAX_SIGNALS = 4
MAX_STEPS = 500
SETTLED_AFTER = MISSING_AFTER + 1.0   # robot seconds: every device the code makes at start-up is in by then
DEVICES_WAIT = 25.0                  # s a devices long-poll waits for a change
SIMPLE = ("enable", "disable", "pause", "resume", "reset", "record_start", "record_stop")
ENDED = ("stopped", "error")
# what a session the FRC Driver Station app drives refuses (D6, D7): the DS's own controls, and what needs the clock held
REAL_DS_NO_PAUSE = f"this session can't be paused or stepped: {REAL_DS_DRIVING}, in real time"
REAL_DS_NO_RECORD = f"this session can't be recorded: {REAL_DS_DRIVING}"
REAL_DS_REFUSES = {"mode": REAL_DS_INPUTS, "enable": REAL_DS_INPUTS, "disable": REAL_DS_INPUTS,
                   "match": REAL_DS_INPUTS, "pause": REAL_DS_NO_PAUSE, "resume": REAL_DS_NO_PAUSE,
                   "step": REAL_DS_NO_PAUSE, "record_start": REAL_DS_NO_RECORD}


class LiveError(ValueError):
    """A request the live session can't take; the message says why."""


class AlreadyLive(LiveError):
    """Start was asked for while a drive session is live."""


class RecordingTaken(LiveError):
    """Save as scenario named a file (or tracks folder) that is already there: the page asks before overwriting it."""


class UnsavedRecording(LiveError):
    """Start was asked for while the last session's recording hasn't been saved."""


def _unsaved() -> str:
    return i18n.t("the last drive's recording hasn't been saved: save it, or start again to discard it")


class _Stopped(Exception):
    """Stop was asked for during the robot program's start-up."""


def _number(name: str, v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise LiveError(f"{name} must be a number, not {v!r}")
    return float(v)


def _neutral(pi: PortInputs) -> PortInputs:
    return PortInputs([0.0] * len(pi.axes), [False] * len(pi.buttons), [-1] * len(pi.povs))


def _port_inputs(port: int, data) -> PortInputs:
    if not isinstance(data, dict):
        raise LiveError(f"port {port}: inputs must be an object with axes, buttons and povs")
    unknown = set(data) - {"axes", "buttons", "povs"}
    if unknown:
        raise LiveError(f"port {port}: unknown key {sorted(unknown)[0]!r}; use axes, buttons, povs")

    def items(key: str, most: int) -> list:
        v = data.get(key, [])
        if not isinstance(v, list) or len(v) > most:
            raise LiveError(f"port {port}: {key} must be a list of at most {most} values")
        return v

    axes = []
    for v in items("axes", MAX_AXES):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not (math.isfinite(v) and -1 <= v <= 1):
            raise LiveError(f"port {port}: axes must be numbers between -1 and 1, not {v!r}")
        axes.append(round(float(v), 4))
    buttons = []
    for v in items("buttons", MAX_BUTTONS):
        if not (isinstance(v, bool) or (isinstance(v, int) and v in (0, 1))):
            raise LiveError(f"port {port}: buttons must be true/false or 0/1, not {v!r}")
        buttons.append(bool(v))
    povs = []
    for v in items("povs", MAX_POVS):
        if isinstance(v, bool) or not isinstance(v, int) or not (v == -1 or 0 <= v <= 359):
            raise LiveError(f"port {port}: povs must be whole numbers: -1 or 0-359 degrees, not {v!r}")
        povs.append(v)
    return PortInputs(axes, buttons, povs)


class LiveManager:
    def __init__(self, simlock: SimLock, publish: Callable[[str, object], None], *, frame_period: float = 1 / 30,
                 state_period: float = 0.1, input_timeout: float = 0.5):
        self._simlock, self._publish_cb = simlock, publish
        self._frame_period, self._state_period, self._input_timeout = frame_period, state_period, input_timeout
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._live = False                 # from start() until the worker has settled (under the lock)
        self._closed = False               # shutdown(): no session may start any more
        self._stopping = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._commands: collections.deque[dict] = collections.deque()
        # status (written under the lock)
        self._state, self._error, self._config_path = "idle", None, None
        self._step: float | None = None
        self._t, self._mode, self._enabled, self._paused = 0.0, "teleop", False, False
        self._speed: float | None = 1.0
        self._speed_locked: str | None = None
        # the FRC Driver Station app drives (Session.real_ds): None from start() until the session has read its config
        # (start()'s real_ds, or the config's [driver_station] real); the last session's stays after it ends
        self._real_ds: bool | None = False
        self._ds_report: dict | None = None   # the real DS's latest real_ds_report (the session's state() match)
        self._recorder: DriveRecorder | None = None
        self._start_pose: tuple[float, float, float] | None = None
        # the match settings (MATCH_KEYS) and the game data box, from the session's start (None until then); the box
        # reaches the robot while it is enabled in Teleop (M5)
        self._match: dict | None = None
        # the robot's game data when the last session ended (restart() keeps it with _match: the same match)
        self._robot_game_data = ""
        # the device check of the latest session (written on the loop, under the lock; the rows stay after it ends)
        self._devices_changed = threading.Condition(self._lock)
        self._dev_version, self._dev_rows, self._dev_t, self._dev_settled = 0, [], 0.0, False
        # restart(): None, "restarting", "restarted" or "failed" (with why), until the next start() of its own
        self._restart: str | None = None
        self._restart_problem: str | None = None
        # the page's inputs (written by input() / client_gone(), applied on the loop)
        self._inputs: dict[int, PortInputs] = {}
        self._input_wall: dict[int, float] = {}
        self._fresh: set[int] = set()
        self._reset_loop_state()

    def _reset_loop_state(self) -> None:
        """What only the session's loop touches."""
        self._session: Session | None = None
        self._applied: dict[int, PortInputs] = {}   # what the robot has: the same values the recorder samples
        self._signals = []
        self._rev_noticed = False      # the REV-unavailable error went out for the current session
        self._push_until: float | None = None
        self._steps_left = 0
        self._console: collections.deque[str] = collections.deque(maxlen=CONSOLE_LINES)
        self._console_next = 0
        self._readouts: dict | None = None
        self._frame_seq = 0
        self._last_frame = self._last_state = -math.inf
        self._frame_t: float | None = None
        self._dirty = True   # a poke moved something since the last frame

    # -- public ---------------------------------------------------------------------------------------------

    def start(self, config_path: Path | str, speed: float | None = 1.0, discard_recording: bool = False,
              *, restarting: bool = False, real_ds: bool | None = None) -> None:
        """Start a live session on `config_path`'s robot program; raises SimBusy when a scenario run holds the
        simulation, LiveError when a session is already live (or `speed` is bad), and UnsavedRecording (a LiveError)
        when the last session left a recording that was never saved, unless `discard_recording`. `real_ds`: the FRC
        Driver Station app drives the robot (None: the config's [driver_station] real); fixed for the session."""
        if speed is not None:
            speed = _number("speed", speed)
            if speed <= 0:
                raise LiveError(f"speed must be positive, not {speed:g}")
        config_path = Path(config_path).resolve()
        with self._lock:
            if self._closed:
                raise LiveError("the editor is shutting down")
            if self._live:
                raise AlreadyLive("a drive session is already live")
            self._simlock.acquire("live")   # SimBusy first: no point asking about the recording then
            rec = self._recorder
            if not discard_recording and rec is not None and not rec.empty and not rec.saved:
                self._simlock.release("live")
                raise UnsavedRecording(_unsaved())
            self._live = True
            self._stopping = threading.Event()
            self._commands.clear()
            self._inputs, self._input_wall, self._fresh = {}, {}, set()
            self._state, self._error, self._config_path, self._step = "starting", None, config_path, None
            self._t, self._mode, self._enabled, self._paused = 0.0, "teleop", False, False
            self._speed, self._speed_locked = speed, None
            self._real_ds, self._ds_report = (None if real_ds is None else bool(real_ds)), None
            # restart() keeps the match settings, the box and the robot's game data: a restart is the same match,
            # as restart_robot keeps the game data
            kept = (dict(self._match), self._robot_game_data) if restarting and self._match is not None else None
            self._recorder, self._start_pose, self._match = None, None, None
            self._robot_game_data = ""
            self._dev_rows, self._dev_t, self._dev_settled = [], 0.0, False
            self._restart, self._restart_problem = ("restarted" if restarting else None), None
            self._devices_bump()
            self._reset_loop_state()
            self._thread = threading.Thread(target=self._work, name="live", daemon=True,
                                            args=(config_path, speed, self._stopping, kept, self._real_ds))
            self._thread.start()
        self._publish_state(force=True)

    def stop(self) -> None:
        """End the session (also interrupts the robot program's start-up)."""
        with self._lock:
            stopping = self._stopping
        stopping.set()
        self._wake_loop()

    def shutdown(self) -> None:
        """End the session for good: a start() racing the editor's shutdown is refused rather than starting a robot
        program after it."""
        with self._lock:
            self._closed = True
        self.stop()

    def join(self, timeout: float | None = None) -> bool:
        """Waits for the worker; True if it has finished."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def kill_robots(self) -> None:
        """Kill, by their run tokens, the robot programs the worker started and hasn't stopped (Linux only)."""
        with self._lock:
            thread = self._thread
        if thread is not None and thread.ident is not None:
            for token in robot_process.tokens_started_by(thread.ident):
                robot_process.kill_by_token(token, timeout=2.0)

    def status(self) -> dict:
        with self._lock:
            rec = self._recorder
            out = {"state": self._state, "error": self._error,
                   "config": str(self._config_path) if self._config_path is not None else None,
                   "t": round(self._t, 4), "mode": self._effective_mode(),
                   "chosen_mode": self._mode, "enabled": self._enabled,
                   "match": dict(self._match) if self._match is not None else None, "stations": list(STATIONS),
                   "paused": self._paused, "speed": self._speed, "speed_locked": self._speed_locked,
                   "recording": rec is not None and rec.recording, "pokes": rec.pokes if rec is not None else 0,
                   "can_save": rec is not None and not rec.empty and self._state not in ("idle", "starting"),
                   "real_ds": self._real_ds is True}
            if self._real_ds:   # what the real DS is doing, per the robot program's own reports
                report = self._ds_report
                mode = report["mode"] if report is not None else "waiting"
                out |= {"mode": mode, "enabled": mode in MODES, "match": dict(report) if report is not None else None,
                        "driver_station": "connected" if report is not None and report["connected"]
                        else REAL_DS_WAITING}
            return out

    def devices(self, since: int | None = None, timeout: float = DEVICES_WAIT) -> dict:
        """The device check of the session (devicecheck.check_devices as dicts), updated while it runs; the last
        session's rows stay after it ends. With `since`, waits up to `timeout` for a version other than it."""
        with self._lock:
            if since is not None:
                self._devices_changed.wait_for(lambda: self._dev_version != since, timeout)
            return {"version": self._dev_version, "live": self._live, "robot_time": round(self._dev_t, 3),
                    "settled": self._dev_settled, "rows": list(self._dev_rows), "restart": self._restart,
                    "restart_problem": self._restart_problem}

    def restart(self) -> None:
        """Stop the live session and start it again on the same config and speed (it reads the file again), on a
        thread of its own; the device check follows it, and devices()' restart says how it went ("restarting",
        then "restarted" once the new session has started, or "failed" with restart_problem). LiveError when no
        session is live, UnsavedRecording (before anything stops) when its recording hasn't been saved."""
        with self._lock:
            if not self._live or self._closed:
                raise LiveError(i18n.t("no drive session is live"))
            rec = self._recorder
            if rec is not None and not rec.empty and not rec.saved:
                raise UnsavedRecording(_unsaved())
            path, speed, real_ds = self._config_path, self._speed, self._real_ds
            self._restart, self._restart_problem = "restarting", None
            self._devices_bump()

        def failed(why: str) -> None:
            with self._lock:
                self._restart, self._restart_problem = "failed", why
                self._devices_bump()

        def again() -> None:
            self.stop()
            if not self.join(30):
                log.warning("live: the session didn't stop in 30 s; not restarting it")
                failed("the robot didn't stop within 30 s, so it wasn't started again")
                return
            try:
                self.start(path, speed, restarting=True, real_ds=real_ds)
            except Exception as e:  # noqa: BLE001 - a scenario run took the simulation, the editor is closing...
                log.warning("live: restarting the session failed: %s", e)
                failed(str(e) or type(e).__name__)
        threading.Thread(target=again, name="live-restart", daemon=True).start()

    def command(self, cmd: dict) -> None:
        """Validate `cmd` now (LiveError when it's bad) and queue it for the session's loop."""
        cmd = self._validate(cmd)
        with self._lock:
            if not self._live:
                raise LiveError(i18n.t("no drive session is live"))
            if self._real_ds and cmd["cmd"] in REAL_DS_REFUSES:
                raise LiveError(REAL_DS_REFUSES[cmd["cmd"]])
            self._commands.append(cmd)
        self._wake_loop()

    def input(self, ports: dict) -> None:
        """The page's joystick inputs, ``{"<port>": {"axes": [...], "buttons": [...], "povs": [...]}}``; latest
        wins per port. Axes are rounded to 4 decimals, so the robot and a recording get identical values."""
        if not isinstance(ports, dict):
            raise LiveError("ports must be an object of port number to inputs")
        parsed = {}
        for key, data in ports.items():
            port = key if isinstance(key, int) and not isinstance(key, bool) else (
                int(key) if isinstance(key, str) and key.isdigit() else None)
            if port is None or not 0 <= port <= 5:
                raise LiveError(f"joystick ports are 0-5, not {key!r}")
            parsed[port] = _port_inputs(port, data)
        now = time.monotonic()
        with self._lock:
            if self._live and self._real_ds:
                raise LiveError(REAL_DS_INPUTS)
            for port, pi in parsed.items():
                self._inputs[port] = pi
                self._input_wall[port] = now
                self._fresh.add(port)

    def client_gone(self) -> None:
        """The last control page went away: zero every port, disable the robot and pause (the session stays)."""
        with self._lock:
            for port, pi in self._inputs.items():
                self._inputs[port] = _neutral(pi)
                self._fresh.add(port)
            if not self._live or self._real_ds:   # the real DS drives on, as it does with no page open
                return
            self._commands.extend([{"cmd": "disable"}, {"cmd": "pause"}])
        self._wake_loop()

    def save_recording(self, path: Path | str, overwrite: bool) -> dict:
        """Write the recording as a scenario (stopping the recording first while live)."""
        with self._lock:
            rec = self._recorder
            if rec is None or self._state in ("idle", "starting"):
                raise LiveError(i18n.t("nothing has been recorded"))
            rec.stop(self._t)
            if rec.empty:
                raise LiveError(i18n.t("nothing has been recorded"))
            try:
                return rec.save(Path(path), self._config_path, self._start_pose, overwrite)
            except RecordingExists as e:
                raise RecordingTaken(str(e)) from None      # the page asks before overwriting (save/exists)
            except RecordingError as e:
                raise LiveError(str(e)) from None
            except OSError as e:
                raise LiveError(i18n.t("can't write {path}: {why}", path=path, why=e.strerror or e)) from None

    # -- validation -----------------------------------------------------------------------------------------

    def _validate(self, cmd) -> dict:
        if not isinstance(cmd, dict):
            raise LiveError("a command must be an object with a cmd")
        name = cmd.get("cmd")
        if name in SIMPLE:
            return {"cmd": name}
        if name == "mode":
            if cmd.get("mode") not in MODES:
                raise LiveError(f"mode must be one of {', '.join(MODES)}, not {cmd.get('mode')!r}")
            return {"cmd": name, "mode": cmd["mode"]}
        if name == "step":
            n = cmd.get("n")
            if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_STEPS:
                raise LiveError(f"n must be a whole number from 1 to {MAX_STEPS}, not {n!r}")
            return {"cmd": name, "n": n}
        if name == "speed":
            if cmd.get("speed") == "max":
                return {"cmd": name, "speed": None}
            speed = _number("speed", cmd.get("speed"))
            if speed <= 0:
                raise LiveError(f"speed must be positive, not {speed:g}")
            with self._lock:
                step = self._step
            if step is not None and step / speed >= 1.0:
                raise LiveError(f"speed {speed:g} with {step:g} s steps: each step would take {step / speed:g} s "
                                "of wall time or more, and the robot's 2 s watchdog would release the clock")
            return {"cmd": name, "speed": speed}
        if name == "set_pose":
            return {"cmd": name, **{k: _number(k, cmd.get(k)) for k in ("x", "y", "heading_deg")}}
        if name == "push":
            out = {"cmd": name, **{k: _number(k, cmd.get(k)) for k in ("force_n", "direction_deg", "duration_s")}}
            if out["duration_s"] <= 0:
                raise LiveError(f"duration_s must be positive, not {out['duration_s']:g}")
            return out
        if name == "move_body":
            body = cmd.get("name")
            if not isinstance(body, str) or not body:
                raise LiveError(f"name must be the name of a movable body, not {body!r}")
            return {"cmd": name, "name": body, **{k: _number(k, cmd.get(k)) for k in ("x", "y", "yaw_deg")}}
        if name == "signals":
            names = cmd.get("names")
            if (not isinstance(names, list) or len(names) > MAX_SIGNALS
                    or not all(isinstance(n, str) for n in names)):
                raise LiveError(f"names must be a list of at most {MAX_SIGNALS} signal names")
            return {"cmd": name, "names": list(names)}
        if name == "match":
            out = {k: v for k, v in cmd.items() if k != "cmd"}
            unknown = set(out) - {*MATCH_KEYS, "game_data"}
            if unknown:
                raise LiveError(f"match has no key {sorted(unknown)[0]!r}; the keys are {', '.join(MATCH_KEYS)}, "
                                "game_data")
            try:
                MatchSettings().merged({k: v for k, v in out.items() if k != "game_data"}, "match:")
                if "game_data" in out:
                    check_game_data(out["game_data"], "match:")
            except ValueError as e:
                raise LiveError(str(e)) from None
            return {"cmd": name, **out}
        raise LiveError(f"unknown command {name!r}")

    # -- worker ---------------------------------------------------------------------------------------------

    def _work(self, config_path: Path, speed, stopping: threading.Event, kept: tuple | None = None,
              real_ds: bool | None = False) -> None:
        error = None
        try:
            asyncio.run(self._main(config_path, speed, stopping, kept, real_ds))
        except _Stopped:
            pass
        except BaseException as e:  # noqa: BLE001 - the worker must always settle the session
            error = str(e) or type(e).__name__
        rec_t = self._t
        with self._lock:
            if self._recorder is not None:
                self._recorder.stop(rec_t)
            self._state, self._error = ("error", error) if error is not None else ("stopped", None)
            self._paused = False
            self._loop = self._wake = None
            self._simlock.release("live")
            self._live = False
            self._devices_bump()
        self._publish_state(force=True)

    async def _main(self, config_path: Path, speed, stopping: threading.Event, kept: tuple | None,
                    real_ds: bool | None) -> None:
        with self._lock:
            self._loop, self._wake = asyncio.get_running_loop(), asyncio.Event()
        session = Session(config_path, speed=speed, real_ds=real_ds)
        session.set_speed(speed)   # the --speed rule for this config's step
        with self._lock:
            self._real_ds = session.real_ds
            self._step = session.config.step
            self._recorder = DriveRecorder(session.config.step, dataclasses.asdict(session.config.match))
        try:
            if kept is not None:
                await self._until_stopped(session.start(
                    match=MatchSettings(**{k: kept[0][k] for k in MATCH_KEYS}), game_data=kept[1]), stopping)
            else:
                await self._until_stopped(session.start(), stopping)
            self._session = session
            self._rev_noticed = False
            if not session.real_ds:
                session.set_mode("disabled")
                with self._lock:
                    self._match = {**dataclasses.asdict(session.bridge.ds.match),
                                   "game_data": kept[0]["game_data"] if kept is not None else ""}
            self._set_state("running")
            await self._run(session, stopping)
        finally:
            self._session = None   # stopping: state messages keep the last readouts
            if session.bridge is not None:
                with self._lock:
                    self._robot_game_data = session.bridge.ds.game_data
            self._update_console(session)
            await asyncio.shield(session.stop())
            self._update_console(session)

    async def _until_stopped(self, aw, stopping: threading.Event):
        """Awaits `aw` (the robot start-up) as a task that Stop interrupts: the task is cancelled (its own clean-up
        stops the robot program) and _Stopped is raised."""
        task = asyncio.ensure_future(aw)

        async def watch() -> None:
            while not stopping.is_set():
                await asyncio.sleep(0.05)
        watcher = asyncio.ensure_future(watch())
        try:
            await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            watcher.cancel()
            if not task.done():
                task.cancel()
                await asyncio.wait({task})
        if task.cancelled():
            raise _Stopped
        return task.result()

    async def _run(self, session: Session, stopping: threading.Event) -> None:
        step = session.config.step
        while True:
            self._wake.clear()
            self._apply_commands(session)
            if stopping.is_set():
                return
            if self._steps_left > 0:
                await session.advance(self._steps_left * step, on_step=self._on_step, stop=self._pending)
            elif not self._paused:
                await session.advance(SLICE, on_step=self._on_step, stop=self._pending)
            else:
                self._publish(session)
                try:
                    await asyncio.wait_for(self._wake.wait(), self._state_period)
                except TimeoutError:
                    pass

    def _pending(self) -> bool:
        return bool(self._commands) or self._stopping.is_set()

    def _on_step(self, t: float) -> None:
        session = self._session
        if self._steps_left > 0:
            self._steps_left -= 1
        if self._push_until is not None and t >= self._push_until - 1e-9:
            session.world.set_robot_force(0.0, 0.0)
            self._push_until = None
        self._sync(session, t)
        self._publish(session)

    def _sync(self, session: Session, t: float) -> None:
        """Zero ports gone quiet, give the robot the inputs that changed, and record the effective state at t."""
        now = time.monotonic()
        with self._lock:
            self._t = t
            for port, pi in self._inputs.items():
                if now - self._input_wall.get(port, now) >= self._input_timeout:
                    zero = _neutral(pi)
                    if pi != zero:
                        self._inputs[port] = zero
                        self._fresh.add(port)
            changed = {p: self._inputs[p] for p in self._fresh} if not session.real_ds else {}
            self._fresh.clear()
        for port, pi in sorted(changed.items()):
            session.set_joystick(port, axes=pi.axes, buttons=pi.buttons, povs=pi.povs)
            self._applied[port] = pi
        with self._lock:
            if self._recorder is not None and self._recorder.recording:
                self._recorder.sample(t, self._effective_mode(), self._applied, session.bridge.ds.game_data)

    def _effective_mode(self) -> str:  # lock held
        return self._mode if self._enabled else "disabled"

    def _apply_commands(self, session: Session) -> None:
        applied = False
        while True:
            with self._lock:
                if not self._commands:
                    break
                cmd = self._commands.popleft()
            applied = True
            try:
                self._apply(session, cmd)
            except Exception as e:  # noqa: BLE001 - a bad poke must not end the session
                self._notice("error", str(e) or type(e).__name__)
        if applied:
            self._sync(session, session.t)
            self._set_state("paused" if self._paused else "running")

    def _apply(self, session: Session, cmd: dict) -> None:
        name = cmd["cmd"]
        if session.real_ds and name in REAL_DS_REFUSES:   # queued (by client_gone, say) before the choice was known
            return
        if name in ("mode", "enable", "disable"):
            with self._lock:
                if name == "mode":
                    self._mode = cmd["mode"]
                else:
                    self._enabled = name == "enable"
                mode = self._effective_mode()
            session.set_mode(mode)
            self._send_game_data(session)
        elif name == "match":
            settings = {k: v for k, v in cmd.items() if k in MATCH_KEYS}
            if settings:
                session.set_match(**settings)
            with self._lock:
                self._match.update(dataclasses.asdict(session.bridge.ds.match))
                if "game_data" in cmd:
                    self._match["game_data"] = cmd["game_data"]
            self._send_game_data(session)
        elif name in ("pause", "resume"):
            with self._lock:
                self._paused = name == "pause"
            self._steps_left = 0
        elif name == "step":
            with self._lock:
                self._paused = True
            self._steps_left = cmd["n"]
        elif name == "speed":
            session.set_speed(cmd["speed"])
            with self._lock:
                self._speed = session.speed
        elif name == "set_pose":
            session.set_pose(cmd["x"], cmd["y"], cmd["heading_deg"])
            self._poked()
        elif name == "push":
            a = math.radians(cmd["direction_deg"])
            session.world.set_robot_force(cmd["force_n"] * math.cos(a), cmd["force_n"] * math.sin(a))
            self._push_until = session.t + cmd["duration_s"]
            self._poked()
        elif name == "move_body":
            session.move_body(cmd["name"], cmd["x"], cmd["y"], yaw_deg=cmd["yaw_deg"])
            self._poked()
        elif name == "reset":
            # at rest at its start pose: a push still running ends here (else it shoves the robot off again)
            session.world.set_robot_force(0.0, 0.0)
            self._push_until = None
            session.reset_robot()
            self._poked()
        elif name == "record_start":
            self._sync(session, session.t)
            pose = session.state()["pose"]
            with self._lock:
                self._start_pose = (pose["x"], pose["y"], pose["heading_deg"])
                self._recorder.start(session.t, self._effective_mode(), self._applied, session.bridge.ds.game_data,
                                     {k: self._match[k] for k in MATCH_KEYS})
        elif name == "record_stop":
            with self._lock:
                self._recorder.stop(session.t)
        elif name == "signals":
            signals, bad = [], []
            for n in cmd["names"]:
                try:
                    signals.append(resolve_signal(n, session.world))
                except ValueError as e:
                    bad.append(str(e))
            self._signals = signals
            for msg in bad:
                self._notice("error", msg)

    def _send_game_data(self, session: Session) -> None:
        """The game data box to the robot while it is enabled in Teleop, as the real DS sends it (M5); sent only when
        it differs, so a box left empty keeps the default frames."""
        with self._lock:
            box = self._match["game_data"] if self._effective_mode() == "teleop" else None
        if box is not None and box != session.bridge.ds.game_data:
            session.set_match(game_data=box)

    def _poked(self) -> None:
        self._dirty = True
        with self._lock:
            if self._recorder is not None:
                self._recorder.poke()

    # -- publishing -----------------------------------------------------------------------------------------

    def _wake_loop(self) -> None:
        with self._lock:
            loop, wake = self._loop, self._wake
        if loop is not None and wake is not None:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:
                pass  # the loop has just closed

    def _send(self, kind: str, msg) -> None:
        try:
            self._publish_cb(kind, msg)
        except Exception:  # noqa: BLE001 - a broken listener must not end the session
            log.exception("live: publishing %s failed", kind)

    def _check_rev(self, session) -> None:
        """The agent can't simulate the config's REV SPARKs (REVLib too old): one error notice per session."""
        if self._rev_noticed:
            return
        problem = session.rev_problem()
        if problem:
            self._rev_noticed = True
            self._notice("error", problem)

    def _notice(self, kind: str, text: str) -> None:
        self._send("notice", {"kind": kind, "text": text})

    def _set_state(self, state: str) -> None:
        with self._lock:
            if self._state == state:
                return
            self._state = state
        self._publish_state(force=True)

    def _publish(self, session: Session) -> None:
        """A frame and a state message, each when its period has passed."""
        now = time.monotonic()
        t = session.t
        if now - self._last_frame >= self._frame_period and (self._dirty or t != self._frame_t):
            self._last_frame, self._frame_t, self._dirty = now, t, False
            poses = session.mover_poses()
            self._frame_seq = (self._frame_seq + 1) & 0xFFFFFFFF
            self._send("frame", FRAME_HEAD.pack(self._frame_seq, SOURCE_LIVE, t, len(poses))
                       + poses.astype("<f4").tobytes())
        self._publish_state()

    def _devices_bump(self) -> None:  # lock held
        self._dev_version += 1
        self._devices_changed.notify_all()

    def _update_devices(self, session: Session) -> None:
        """The device check now (on the loop: the bridge's device list is the loop's); a new version when the rows
        (or settled) change, not for the clock alone."""
        b = session.bridge
        rows = [r.as_dict() for r in check_devices(session.world, b.hal, session.t)] if b is not None else []
        settled = session.t >= SETTLED_AFTER - 1e-6
        with self._lock:
            self._dev_t = session.t
            if rows != self._dev_rows or settled != self._dev_settled:
                self._dev_rows, self._dev_settled = rows, settled
                self._devices_bump()

    def _publish_state(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_state < self._state_period:
            return
        self._last_state = now
        session = self._session
        signals = {}
        if session is not None:   # set only while the session runs
            self._readouts = session.state()
            self._update_console(session)
            self._check_rev(session)
            self._update_devices(session)
            b = session.bridge
            with self._lock:
                self._speed_locked = b.realtime_reason if b.frees_clock else None
                if session.real_ds:
                    self._ds_report = dict(self._readouts["match"])
            for sig in self._signals:
                try:
                    signals[sig.name] = sig.read(session)
                except Exception:  # noqa: BLE001 - a signal that can't be read shows as empty
                    signals[sig.name] = None
            consume_peaks(session.world)     # the plot's next point holds the peaks since this one
        self._send("state", {"source": "live", **self.status(), "readouts": self._readouts, "signals": signals,
                             "console": list(self._console), "console_seq": self._console_next})

    def _update_console(self, session: Session) -> None:
        robot = session.robot
        if robot is None:
            return
        for line in robot.since(self._console_next):
            self._console.append(line.text)
            self._console_next = line.index + 1
