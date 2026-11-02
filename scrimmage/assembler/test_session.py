"""The Test tab's server side: a block test (`<name>.test.toml`, scrimmage/blocks/) behind /api/test/*.

A test opens in robot mode: the robot document is the test's `robot`, and the App keeps it in step with the file
(an assembler edit, an outside edit, Import again). The session holds the test's Program, the palette and problems of
the robot as it is now (`refresh`, called by the App whenever it validates the robot), and one run or sweep at a time
on a worker thread ("test-run", "test-sweep"), which holds the SimLock as "test" while it runs.

Every change bumps one `version`, which the page long-polls. A save is refused (409) when the test changed after the
version the page sent (`_changed_at`): a run's progress doesn't make the page's version stale. An outside edit of the
test file is read on the App's next sync; one that can't be read keeps the last good test and says why (`file_error`)
until it is fixed.

Runs are kept where `scrimmage test run` keeps them (scrimmage/blocks/store.py: `<test stem>-runs/` next to the test).
"""

from __future__ import annotations

import logging
import tempfile
import threading
import time
from pathlib import Path

from ..blocks import store
from ..blocks.palette import palette, problems
from ..blocks.program import TestFileError, dumps, from_json, load, to_json
from ..blocks.run import TestRun, world_for
from ..blocks.sweep import MAX_SWEEP, _kind as sweep_kind, run_sweep
from ..cad.settings import replace_file
from .bench_session import SessionError
from .simlock import SimBusy, SimLock

log = logging.getLogger("scrimmage.assembler")
STALE = "the test changed (another tab, or an outside edit); the page has reloaded it"
BUSY = "a test run is in progress; wait for it to finish or stop it"
FIX_FIRST = "Fix the problems first: the test can't run on this robot yet."
PROGRESS_PERIOD = 0.25    # s of wall time between progress updates while a run goes
POLL_SECONDS = 25.0


def _sig(path: Path) -> bytes | None:
    """The file's bytes (a test file is small; a time stamp can miss a quick second edit on a coarse clock)."""
    try:
        return path.read_bytes()
    except OSError:
        return None


def _plain_error(e: BaseException) -> str:
    return f"the run stopped with an error ({type(e).__name__}: {e})"


class TestSession:
    __test__ = False     # not a pytest class

    def __init__(self, path: Path, simlock: SimLock, version: int = 0):
        """TestFileError when the file can't be read as a test (OSError when it can't be read at all)."""
        self.path = Path(path).resolve()
        self.simlock = simlock
        self.program = load(self.path)
        self._sig = _sig(self.path)
        self.version = version
        self._changed_at = version
        self._cond = threading.Condition()
        self.palette: dict | None = None
        self.problems: list[dict] = []
        self.file_error: str | None = None   # the test file as it is on disk can't be read (an outside edit)
        self.run_error: str | None = None    # why the last run or sweep couldn't run
        self.running: dict | None = None     # {"kind": "run" | "sweep", "progress": 0..1}
        self.last: dict | None = None        # the last run or sweep this session made: {"kind", "id"}
        self._world = None
        self._robot_problem: str | None = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False

    # -- what it is -------------------------------------------------------------------------
    @property
    def robot_path(self) -> Path:
        """The robot config the test names (relative to the test file's folder)."""
        return (self.path.parent / self.program.robot).resolve()

    @property
    def runs_dir(self) -> Path:
        return store.runs_dir(self.path)

    def _bump(self, changed: bool = False) -> None:
        self.version += 1
        if changed:
            self._changed_at = self.version
        self._cond.notify_all()

    # -- the robot and the file -------------------------------------------------------------
    def _names(self) -> tuple[dict | None, list[dict]]:
        if self._world is None:
            return None, [{"place": [], "message": self._robot_problem or "the robot isn't loaded"}]
        try:
            return palette(self._world, list(self.program.hooks)), problems(self.program, self._world)
        except Exception as e:   # a robot the palette can't read must not take the Test tab down
            log.exception("test palette")
            return None, [{"place": [], "message": f"can't read the robot's motors and joints ({e})"}]

    def _recompute(self) -> None:
        pal, probs = self._names()
        if (pal, probs) != (self.palette, self.problems):
            self.palette, self.problems = pal, probs
            self._bump()

    def refresh(self, world, robot_problem: str | None) -> None:
        """The robot as it is now: its World (None when it doesn't load, `robot_problem` saying why)."""
        with self._cond:
            self._world, self._robot_problem = world, robot_problem
            self._recompute()

    def reload_if_changed(self) -> bool:
        """Read an outside edit of the test file; True when the test (and so maybe its robot) changed."""
        sig = _sig(self.path)
        with self._cond:
            if sig == self._sig or sig is None:
                return False
            self._sig = sig
            try:
                new = load(self.path)
            except (TestFileError, OSError, UnicodeDecodeError) as e:
                msg = str(e) if isinstance(e, TestFileError) else f"can't read {self.path.name}: {e}"
                if msg != self.file_error:
                    self.file_error = msg
                    self._bump(changed=True)
                return False
            self.program, self.file_error = new, None
            self._bump(changed=True)
            self._recompute()
            return True

    def save(self, body: dict) -> tuple[int, dict]:
        """Replace the test with body["test"] (the state's `test` shape) and write the file."""
        v = body.get("version")
        if not isinstance(v, int) or isinstance(v, bool):
            raise SessionError(400, "version (the integer from the state) is required")
        with self._cond:
            if self._closed:
                raise SessionError(409, "the test was closed")
            if v < self._changed_at:
                return 409, {"ok": False, "error": STALE, "state": self.state()}
            try:
                new = from_json(body.get("test"), self.path)
            except TestFileError as e:
                raise SessionError(400, str(e)) from None
            text = dumps(new)
            tmp = None
            try:
                with tempfile.NamedTemporaryFile("w", dir=self.path.parent, prefix=f".{self.path.name}.",
                                                 suffix=".tmp", delete=False, encoding="utf-8", newline="\n") as f:
                    tmp = Path(f.name)
                    f.write(text)
                replace_file(tmp, self.path)
            except OSError as e:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
                raise SessionError(400, f"can't write {self.path.name}: {e.strerror or e}") from None
            self._sig = _sig(self.path)
            self.program, self.file_error = new, None
            self._bump(changed=True)
            self._recompute()
            return 200, {"ok": True, "version": self.version}

    # -- runs -------------------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        with self._cond:
            return self.running is not None

    def start(self, kind: str, body: dict | None = None) -> tuple[int, dict]:
        """Start a run ("run") or a sweep ("sweep", body {target, values}) on the worker thread."""
        body = body or {}
        target = values = None
        if kind == "sweep":
            target, values = body.get("target"), body.get("values")
            try:
                sweep_kind(target)
            except ValueError as e:
                raise SessionError(400, str(e)) from None
            if not isinstance(values, list) or not values or len(values) > MAX_SWEEP or not all(
                    isinstance(x, (int, float)) and not isinstance(x, bool) for x in values):
                raise SessionError(400, f"values must be a list of 1 to {MAX_SWEEP} numbers")
        with self._cond:
            if self._closed:
                raise SessionError(409, "the test was closed")
            if self.running is not None:
                raise SessionError(409, BUSY)
            if self.file_error is not None:
                raise SessionError(409, f"fix the test file first: {self.file_error}")
            if self.problems:
                return 409, {"ok": False, "error": FIX_FIRST, "problems": self.problems}
            try:
                self.simlock.acquire("test")
            except SimBusy as e:
                raise SessionError(409, e.wait) from None
            program = self.program
            self._cancel = cancel = threading.Event()
            self.running = {"kind": kind, "progress": 0.0}
            self.run_error = None
            self._bump()
            if kind == "run":
                work, args = self._run, (program, cancel)
            else:
                work, args = self._sweep, (program, target, values, cancel)
            self._thread = threading.Thread(target=work, args=args, daemon=True, name=f"test-{kind}")
            try:
                self._thread.start()
            except BaseException:
                self.simlock.release("test")
                self.running = None
                self._bump()
                raise
        return 200, {"ok": True}

    def cancel(self) -> dict:
        self._cancel.set()
        return {"ok": True}

    def _progress(self, p: float, last: list) -> None:
        now = time.monotonic()
        if now - last[0] < PROGRESS_PERIOD:
            return
        last[0] = now
        with self._cond:
            if self.running is not None:
                self.running = {**self.running, "progress": max(0.0, min(1.0, p))}
                self._bump()

    def _finish(self, last: dict | None, error: str | None) -> None:
        self.simlock.release("test")    # first: a page that sees the run finished may start a drive session
        with self._cond:
            self.running = None
            if last is not None:
                self.last = last
            self.run_error = error
            self._bump()

    def _run(self, program, cancel: threading.Event) -> None:
        last = error = None
        clock = [time.monotonic()]
        try:
            limit = program.time_limit
            result = TestRun(world_for(program), program).run(
                on_sample=lambda t, row: self._progress(t / limit, clock), cancel=cancel)
            folder = store.save_run(result, program, self.runs_dir)
            last = {"kind": "run", "id": folder.name}
        except (ValueError, OSError) as e:      # TestFileError, ConfigError and PartError are ValueErrors
            error = str(e)
        except Exception as e:
            log.exception("test run")
            error = _plain_error(e)
        finally:
            self._finish(last, error)

    def _sweep(self, program, target: dict, values: list, cancel: threading.Event) -> None:
        last = error = None
        clock = [time.monotonic()]
        try:
            folder = run_sweep(program, target, values, self.runs_dir, cancel=cancel,
                               on_progress=lambda k, n, row: self._progress(k / n, clock))
            last = {"kind": "sweep", "id": folder.name}
        except (ValueError, OSError) as e:
            error = str(e)
        except Exception as e:
            log.exception("test sweep")
            error = _plain_error(e)
        finally:
            self._finish(last, error)

    # -- reading ----------------------------------------------------------------------------
    def state(self) -> dict:
        with self._cond:
            out = {"version": self.version, "path": str(self.path), "test": to_json(self.program),
                   "palette": self.palette, "problems": self.problems, "running": self.running,
                   "file_error": self.file_error, "run_error": self.run_error, "last": self.last}
        out["runs"] = store.list_runs(self.runs_dir)
        out["sweeps"] = store.list_sweeps(self.runs_dir)
        return out

    def wait(self, since: int | None, end: float) -> bool:
        """Wait (at most a second, and not past `end`) for the version to move from `since`; True when it has, or
        there is nothing to wait for."""
        with self._cond:
            if since is None or self.version != since or self._closed or time.monotonic() >= end:
                return True
            self._cond.wait(min(1.0, max(0.0, end - time.monotonic())))
            return self.version != since or self._closed

    def run_file(self, run_id: str, what: str) -> Path:
        """A kept run's view.json ("view") or motion.bin ("motion"); 404 for a run that isn't kept."""
        try:
            folder = store.run_folder(self.runs_dir, run_id)
        except FileNotFoundError:
            raise SessionError(404, f"no run {run_id} is kept for this test") from None
        return folder / ("view.json" if what == "view" else "motion.bin")

    def run_result(self, run_id: str) -> dict:
        try:
            return store.load_result(self.runs_dir, run_id)
        except FileNotFoundError:
            raise SessionError(404, f"no run {run_id} is kept for this test") from None

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cancel.set()
            self._cond.notify_all()
            t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=30)
