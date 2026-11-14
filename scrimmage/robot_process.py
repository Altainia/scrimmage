"""Runs the robot program under test: launch it, capture its console, stop it."""

from __future__ import annotations

import asyncio
import collections
import ctypes
import functools
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import i18n

AGENT_PREFIX = "scrimmage agent: "  # the Java agent's console lines: loaded / disabled / ignored N ...
HELPER_PREFIX = "scrimmage rev helper: "  # the RobotPy REV helper's lines: the same words after the prefix
REV_UNAVAILABLE = "REV sim unavailable:"     # ... REV sim unavailable: <reason> (REVLib too old or missing)
GRAPPLE_PREFIX = "scrimmage grapple helper: "   # the LaserCAN helper's lines (Java agent and RobotPy lockstep alike)
GRAPPLE_UNAVAILABLE = "unavailable:"            # ... unavailable: <reason>
MAX_LINE = 8192  # characters kept per console line; longer lines are cut, the rest discarded
CONSOLE_POLL = 0.02  # s between reads of a Java robot's console file
TRUNCATED = " [line truncated]"
RUN_TOKEN_ARG = "-Dscrimmage.runToken="   # the Gradle init script puts the run token on the robot JVM's command line

log = logging.getLogger("scrimmage")

_libc = None
if sys.platform.startswith("linux"):
    try:
        _libc = ctypes.CDLL(None, use_errno=True)
    except OSError:
        _libc = None


def _die_with_parent(parent: int) -> None:
    """Runs in the child before exec (Linux). SIGKILL the robot program when scrimmage dies, even from
    kill -9, so it's never orphaned. The getppid check covers a parent that died before prctl ran."""
    if _libc is not None:
        _libc.prctl(1, signal.SIGKILL)  # 1 = PR_SET_PDEATHSIG
    if os.getppid() != parent:
        os._exit(1)


def _token_pids(token: str) -> list[int]:
    needle = f"SCRIMMAGE_RUN_TOKEN={token}".encode()
    me = os.getpid()
    pids = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return pids
    for name in entries:
        if not name.isdigit() or int(name) == me:
            continue
        try:
            with open(f"/proc/{name}/environ", "rb") as f:
                env = f.read()
        except OSError:
            continue  # gone, or not ours to read
        if needle in env.split(b"\0"):
            pids.append(int(name))
    return pids


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"  # a zombie has exited
    except (OSError, IndexError):
        return False


def kill_tree(pid: int) -> None:
    """Kill a robot program and the processes it started: SIGKILL its process group (it was started in a session of
    its own); on Windows, which has neither, taskkill /T (then the process alone if that fails). Raises
    ProcessLookupError or PermissionError as os.killpg does; on Windows it never raises."""
    if sys.platform != "win32":
        os.killpg(pid, signal.SIGKILL)
        return
    try:
        r = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=10)
        if r.returncode == 0:
            return
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        os.kill(pid, signal.SIGTERM)   # Windows: TerminateProcess
    except OSError:
        pass


def _token_pids_windows(token: str) -> list[int] | None:
    """Windows: the processes whose command line carries RUN_TOKEN_ARG<token> (it can't read another process's
    environment), from a CIM query through PowerShell (on every Windows 10 and 11); None when the query fails."""
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and "
              f"$_.CommandLine.Contains('{RUN_TOKEN_ARG}{token}') }} | ForEach-Object {{ $_.ProcessId }}")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [int(line) for line in r.stdout.split() if line.isdigit()]


def kill_by_token(token: str, timeout: float = 10.0) -> None:
    """Kill every process whose environment carries SCRIMMAGE_RUN_TOKEN=<token> (on Windows, whose command line
    carries RUN_TOKEN_ARG<token>; elsewhere a no-op). Catches robot JVMs spawned by a Gradle daemon, which are outside
    the robot program's process group. SIGTERM first, then SIGKILL for whatever is left after `timeout` s; on Windows
    taskkill /F /T at once."""
    if sys.platform == "win32":
        pids = _token_pids_windows(token)
        if pids is None:
            log.warning("%s", i18n.t("scrimmage couldn't look for robot programs left running ({query} failed), so one "
                                     "may still be running: its command line has {arg}", query="Get-CimInstance",
                                     arg=f"{RUN_TOKEN_ARG}{token}"))
        for pid in pids or []:
            kill_tree(pid)
        return
    if not sys.platform.startswith("linux"):
        return
    pids = _token_pids(token)
    for sig, wait in ((signal.SIGTERM, timeout), (signal.SIGKILL, 2.0)):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        deadline = time.monotonic() + wait
        while any(_alive(p) for p in pids) and time.monotonic() < deadline:
            time.sleep(0.05)
        pids = [p for p in pids if _alive(p)]
        if not pids:
            return


# Windows: scrimmage's job object. Every robot program is assigned to it right after it starts; its handle is held
# (never closed) for this process's life, so when scrimmage exits for any reason, even killed, Windows closes it and
# KILL_ON_JOB_CLOSE ends every process in it: the robot program and its children (Linux: _die_with_parent).
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9    # JOBOBJECTINFOCLASS JobObjectExtendedLimitInformation
PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001   # the rights AssignProcessToJobObject needs


class _BasicLimits(ctypes.Structure):     # JOBOBJECT_BASIC_LIMIT_INFORMATION
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]


class _JobLimits(ctypes.Structure):       # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),   # IO_COUNTERS
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


_job = None   # the job object's handle once made
_job_lock = threading.Lock()


def _kernel32():
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.argtypes, k32.CreateJobObjectW.restype = [wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.OpenProcess.argtypes, k32.OpenProcess.restype = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.CloseHandle.argtypes, k32.CloseHandle.restype = [wintypes.HANDLE], wintypes.BOOL
    return k32


def _last_error() -> int:
    return ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0


def _robot_job(k32):
    """The job object, made on first use: KILL_ON_JOB_CLOSE, not inheritable (so only this process holds it)."""
    global _job
    with _job_lock:
        if _job is None:
            job = k32.CreateJobObjectW(None, None)
            if not job:
                raise OSError(_last_error(), "CreateJobObjectW")
            limits = _JobLimits()
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits),
                                               ctypes.sizeof(limits)):
                err = _last_error()
                k32.CloseHandle(job)
                raise OSError(err, "SetInformationJobObject")
            _job = job
        return _job


def assign_to_job(pid: int, k32=None) -> bool:
    """Windows: put the robot program `pid`, just started, in scrimmage's job object (nested jobs are allowed since
    Windows 8). A process it started before this escapes the job: RobotPy runs in one process, and a Gradle robot JVM
    starts well after (kill_by_token finds one from a Gradle daemon). When it can't, one log line and False: stop()
    and kill_by_token still work."""
    try:
        k32 = k32 or _kernel32()
        job = _robot_job(k32)
        proc = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not proc:
            raise OSError(_last_error(), "OpenProcess")
        try:
            if not k32.AssignProcessToJobObject(job, proc):
                raise OSError(_last_error(), "AssignProcessToJobObject")
        finally:
            k32.CloseHandle(proc)
    except OSError as e:   # strerror: the kernel32 call that failed; errno: its GetLastError() code
        log.warning("%s", i18n.t("couldn't tie the robot program to scrimmage ({call} failed, error {code}), so if "
                                 "scrimmage is killed the robot program may keep running", call=e.strerror,
                                 code=e.errno))
        return False
    return True


# The run token of every robot program that may still be running -> the thread that started it, so a caller
# whose worker thread is stuck can still kill that thread's robot programs (tokens_started_by + kill_by_token).
_live_tokens: dict[str, int] = {}
_live_lock = threading.Lock()


def tokens_started_by(thread_ident: int) -> list[str]:
    """The run tokens of the robot programs started from that thread and not yet stopped."""
    with _live_lock:
        return [t for t, ident in _live_tokens.items() if ident == thread_ident]


def _forget_token(token: str | None) -> None:
    with _live_lock:
        _live_tokens.pop(token, None)


def _scrimmage_paths() -> dict[str, str]:
    """Where the lockstep extension and the Java agent are, for the Gradle init script (only those that exist).
    Also JAVA_HOME (the WPILib JDK) so ./gradlew works without the user exporting it."""
    out = {}
    try:
        import scrimmage_lockstep
        so = Path(scrimmage_lockstep.library_path())
        if so.exists():
            out["SCRIMMAGE_LOCKSTEP_SO"] = str(so)
    except ImportError:
        pass
    from . import java_tools
    jar = java_tools.agent_jar_path()
    if jar.exists():
        out["SCRIMMAGE_AGENT_JAR"] = str(jar)
    try:
        out["JAVA_HOME"] = str(java_tools.find_jdk())  # find_jdk prefers a valid inherited JAVA_HOME
    except java_tools.JavaToolError:
        pass
    return out


def windows_command(command: list[str], cwd: str | Path) -> list[str]:
    """Windows: a config's `./gradlew` (the shell script, which Windows can't run) as the project's gradlew.bat, by its
    full path (Windows doesn't look for a program in the working folder). Any other command, or no gradlew.bat, as is."""
    if command and Path(command[0]).name == "gradlew":
        bat = (Path(cwd) / command[0]).with_name("gradlew.bat")
        if bat.is_file():
            return [str(bat), *command[1:]]
    return list(command)


@dataclass
class ConsoleLine:
    index: int   # counts every line ever printed, so callers can ask for "lines since N"
    t: float     # simulated time when the line arrived
    text: str


def seed_preload(command: list[str]) -> str | None:
    """The scrimmage-lockstep library that pins std::random_device (so REV's C++ sensor noise repeats), to preload
    into a RobotPy robot program run with --scrimmage-lockstep on Linux; None otherwise or when it isn't installed."""
    if not sys.platform.startswith("linux") or "--scrimmage-lockstep" not in command:
        return None
    from .bridge import robot_language
    if robot_language(command) != "python":
        return None
    try:
        import scrimmage_lockstep
        path = scrimmage_lockstep.seed_library_path()
    except (ImportError, AttributeError):  # not installed, or older than 0.4.0 (the version check reports that)
        return None
    return str(path) if path.exists() else None


class _Lines:
    """Splits a byte stream into console lines for `add`; a runaway line keeps its start up to MAX_LINE and the rest,
    up to its newline, is dropped."""

    def __init__(self, add: Callable[[bytes], None]):
        self.add = add
        self.buf = b""
        self.skip_to_newline = False

    def feed(self, chunk: bytes) -> None:
        buf = self.buf + chunk
        if self.skip_to_newline:
            cut = buf.find(b"\n")
            if cut < 0:
                self.buf = b""
                return
            buf = buf[cut + 1:]
            self.skip_to_newline = False
        *lines, buf = buf.split(b"\n")
        for raw in lines:
            self.add(raw)
        if len(buf) > MAX_LINE:
            self.add(buf)
            buf = b""
            self.skip_to_newline = True
        self.buf = buf

    def end(self) -> None:
        if self.buf:
            self.add(self.buf)
            self.buf = b""


class RobotProcess:
    def __init__(self, command: list[str], cwd: str | Path, env: dict[str, str] | None = None, *,
                 clock: Callable[[], float] = lambda: 0.0, log_path: Path | None = None,
                 echo: bool = False, keep: int = 5000):
        self.command = list(command)
        self.cwd = Path(cwd)
        self.env = dict(env or {})
        self.clock = clock
        self.log_path = log_path
        self.echo = echo
        self.lines: collections.deque[ConsoleLine] = collections.deque(maxlen=keep)
        # The next three come from the Java agent's lines or the RobotPy REV helper's (this run):
        self.agent_status: str | None = None   # "loaded" or "disabled" once the agent / helper says so
        self.agent_summary: str | None = None  # its latest "... ignored N robot-side sim writes ..." line
        self.rev_state: str | None = None      # "unavailable: <reason>" once it says REV can't be simulated
        self.grapple_state: str | None = None  # "unavailable: <reason>" once the Grapple LaserCAN helper says so
        self.next_index = 0
        self.proc: asyncio.subprocess.Process | None = None  # None again once stop() has reaped it
        self._last_returncode: int | None = None
        self._reader: asyncio.Task | None = None
        self._logf = None
        self._token: str | None = None
        self._console_file: Path | None = None  # a Java robot's console, written by the Gradle init script

    def agent_state(self) -> str:
        """The Java agent or RobotPy REV helper per the console: "loaded", "disabled" or "missing" (no line)."""
        return self.agent_status or "missing"

    async def start(self, port: int) -> None:
        if self.running:
            raise RuntimeError("the robot program is already running")
        self._last_returncode = None
        self.agent_status = self.agent_summary = self.rev_state = self.grapple_state = None
        env = {**os.environ, **self.env}
        env.setdefault("PYTHONUNBUFFERED", "1")
        env["HALSIMWS_HOST"] = "127.0.0.1"  # the bridge binds 127.0.0.1; "localhost" may also resolve to ::1
        env["HALSIMWS_PORT"] = str(port)
        _forget_token(self._token)
        self._token = uuid.uuid4().hex
        env["SCRIMMAGE_RUN_TOKEN"] = self._token
        with _live_lock:
            _live_tokens[self._token] = threading.get_ident()
        for key, value in _scrimmage_paths().items():
            if key == "JAVA_HOME" and "JAVA_HOME" not in self.env:
                env[key] = value  # replaces an inherited JAVA_HOME that isn't a JDK; a valid one is returned as is
            else:
                env.setdefault(key, value)
        if (seed := seed_preload(self.command)) is not None:
            env["LD_PRELOAD"] = ":".join(p for p in (seed, env.get("LD_PRELOAD")) if p)
        self._remove_console_file()
        from .bridge import robot_language
        if robot_language(self.command) == "java":
            fd, name = tempfile.mkstemp(prefix="scrimmage-console-", suffix=".log")
            os.close(fd)
            self._console_file = Path(name)
            env["SCRIMMAGE_CONSOLE_FILE"] = name
        if self.log_path is not None and self._logf is None:
            try:
                self._logf = open(self.log_path, "a", encoding="utf-8", errors="replace")
            except OSError as e:
                raise RuntimeError(i18n.t("can't write the robot log {path}: {error}", path=self.log_path,
                                          error=e.strerror)) from None
        self._write_log(f"--- robot program started: {' '.join(self.command)} ---")
        command = windows_command(self.command, self.cwd) if sys.platform == "win32" else self.command
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *command, cwd=self.cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,  # its own process group, so stop() takes its children too
                preexec_fn=(functools.partial(_die_with_parent, os.getpid())
                            if sys.platform.startswith("linux") else None))
        except OSError as e:
            raise RuntimeError(i18n.t("couldn't start the robot program ({command} in {folder}): {error}",
                                      command=" ".join(self.command), folder=self.cwd,
                                      error=e.strerror or e)) from None
        if sys.platform == "win32":   # dies with scrimmage (asyncio can't start it suspended: assigned right after)
            assign_to_job(self.proc.pid)
        self._reader = asyncio.create_task(self._capture(self.proc, self._console_file))

    async def _capture(self, proc: asyncio.subprocess.Process, console_file: Path | None) -> None:
        """Its output, and a Java robot's console file (until the output ends, then what's left in the file)."""
        if console_file is None:
            await self._read(proc)
            return
        ended = asyncio.Event()

        async def read() -> None:
            try:
                await self._read(proc)
            finally:
                ended.set()
        await asyncio.gather(read(), self._tail(console_file, ended))

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        lines = _Lines(self._add)
        while chunk := await proc.stdout.read(65536):
            lines.feed(chunk)
        lines.end()

    async def _tail(self, path: Path, ended: asyncio.Event) -> None:
        lines = _Lines(self._add)
        try:
            f = open(path, "rb")
        except OSError:
            return
        with f:
            while True:
                done = ended.is_set()   # checked before the read, so the last read follows the end
                while chunk := f.read(65536):
                    lines.feed(chunk)
                if done:
                    break
                try:
                    await asyncio.wait_for(ended.wait(), CONSOLE_POLL)
                except TimeoutError:
                    pass
        lines.end()

    def _add(self, raw: bytes) -> None:
        text = raw.decode(errors="replace").rstrip("\r")
        if len(text) > MAX_LINE:
            text = text[:MAX_LINE] + TRUNCATED
        line = ConsoleLine(self.next_index, self.clock(), text)
        self._note_agent(text.strip())
        self.next_index += 1
        self.lines.append(line)
        self._write_log(f"[{line.t:9.3f}] {text}")
        if self.echo:
            try:
                print(text, flush=True)
            except OSError:
                pass

    def _note_agent(self, text: str) -> None:
        if text.startswith(GRAPPLE_PREFIX):
            rest = text[len(GRAPPLE_PREFIX):]
            if rest.startswith(GRAPPLE_UNAVAILABLE):
                self.grapple_state = "unavailable: " + rest[len(GRAPPLE_UNAVAILABLE):].strip()
            return
        prefix = next((p for p in (AGENT_PREFIX, HELPER_PREFIX) if text.startswith(p)), None)
        if prefix is None:
            return
        rest = text[len(prefix):]
        if rest.startswith(REV_UNAVAILABLE):
            self.rev_state = "unavailable: " + rest[len(REV_UNAVAILABLE):].strip()
            return
        word = rest.split(" ", 1)[0]
        if word == "disabled":
            self.agent_status = "disabled"
        elif word in ("loaded", "ignored"):
            self.agent_status = "loaded"
            if word == "ignored":
                self.agent_summary = text

    def _write_log(self, text: str) -> None:
        if self._logf is None:
            return
        try:
            self._logf.write(text + "\n")
            self._logf.flush()
        except OSError:
            # A full or broken disk must not stop the console capture: drop the log and carry on.
            try:
                self._logf.close()
            except OSError:
                pass
            self._logf = None

    @property
    def returncode(self) -> int | None:
        return self._last_returncode if self.proc is None else self.proc.returncode

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def since(self, index: int) -> list[ConsoleLine]:
        return [line for line in self.lines if line.index >= index]

    def tail(self, n: int = 50) -> list[str]:
        return [line.text for line in list(self.lines)[-n:]]

    async def wait_exited(self, timeout: float) -> bool:
        """Wait up to `timeout` s for the process to exit and the console reader to reach EOF (so its last
        lines are captured). Returns whether the process exited."""
        proc = self.proc
        if proc is None:
            return True
        waiters = {asyncio.ensure_future(proc.wait())}
        if self._reader is not None:
            waiters.add(self._reader)
        try:
            _, pending = await asyncio.wait(waiters, timeout=timeout)
        finally:
            for w in waiters:
                if w is not self._reader and not w.done():
                    w.cancel()
        return proc.returncode is not None

    async def stop(self) -> None:
        proc = self.proc
        if proc is not None:
            # Always signal the group, even if the leader already exited: its children may still run.
            # SIGKILL: a robot paused by lockstep ignores SIGINT/SIGTERM.
            try:
                if sys.platform == "win32":   # taskkill: a program to wait for
                    await asyncio.get_running_loop().run_in_executor(None, kill_tree, proc.pid)
                else:
                    kill_tree(proc.pid)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                pass
            if self._token is not None:  # a child that escaped the process group (e.g. via a Gradle daemon)
                await asyncio.get_running_loop().run_in_executor(None, kill_by_token, self._token)
            if proc.returncode is not None:  # reaped: its pid may be reused, so a second stop() mustn't signal it
                self._last_returncode = proc.returncode
                self.proc = None
                _forget_token(self._token)
                self._token = None
        elif self._token is not None:   # start() didn't finish: a process may exist that we hold no handle to
            await asyncio.get_running_loop().run_in_executor(None, kill_by_token, self._token)
            _forget_token(self._token)
            self._token = None
        reader = self._reader
        if reader is not None:
            # asyncio.wait rather than wait_for: wait_for would re-raise the reader's own exception here.
            done, _ = await asyncio.wait({reader}, timeout=2)
            if reader in done:
                if not reader.cancelled():
                    reader.exception()  # consume it so it isn't logged as never retrieved
            else:
                reader.cancel()
            self._reader = None
        self._remove_console_file()

    def _remove_console_file(self) -> None:
        if self._console_file is not None:
            try:
                self._console_file.unlink()
            except OSError:
                pass
            self._console_file = None

    def close(self) -> None:
        self._remove_console_file()
        if self._logf is not None:
            try:
                self._logf.close()
            except OSError:
                pass
            self._logf = None
