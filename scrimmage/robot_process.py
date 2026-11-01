"""Runs the robot program under test: launch it, capture its console, stop it."""

from __future__ import annotations

import asyncio
import collections
import ctypes
import functools
import os
import signal
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

AGENT_PREFIX = "scrimmage agent: "  # the Java agent's console lines: loaded / disabled / ignored N ...
HELPER_PREFIX = "scrimmage rev helper: "  # the RobotPy REV helper's lines: the same words after the prefix
REV_UNAVAILABLE = "REV sim unavailable:"     # ... REV sim unavailable: <reason> (REVLib too old or missing)
MAX_LINE = 8192  # characters kept per console line; longer lines are cut, the rest discarded
CONSOLE_POLL = 0.02  # s between reads of a Java robot's console file
TRUNCATED = " [line truncated]"

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
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"  # a zombie has exited
    except (OSError, IndexError):
        return False


def kill_by_token(token: str, timeout: float = 10.0) -> None:
    """Kill every process whose environment carries SCRIMMAGE_RUN_TOKEN=<token> (Linux only; elsewhere a no-op).
    Catches robot JVMs spawned by a Gradle daemon, which are outside the robot program's process group.
    SIGTERM first, then SIGKILL for whatever is left after `timeout` s."""
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
        self.agent_status = self.agent_summary = self.rev_state = None
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
                raise RuntimeError(f"can't write the robot log {self.log_path}: {e.strerror}") from None
        self._write_log(f"--- robot program started: {' '.join(self.command)} ---")
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.command, cwd=self.cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,  # its own process group, so stop() takes its children too
                preexec_fn=(functools.partial(_die_with_parent, os.getpid())
                            if sys.platform.startswith("linux") else None))
        except OSError as e:
            raise RuntimeError(f"couldn't start the robot program ({' '.join(self.command)} in {self.cwd}): "
                               f"{e.strerror or e}") from None
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
                os.killpg(proc.pid, signal.SIGKILL)
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
