"""The Bench tab's page jobs (`BenchJobs`): the runs, sweeps, compares and galleries the page asks for, queued on one
worker thread per App. Each job holds the App's SimLock and the session folder's run lock and is logged in the
session log like a CLI or agent run, so the open idea session (bench_session.py) picks up its result. server.py
imports BenchJobs from here.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .. import i18n
from ..bench import run as bench_run
from ..bench import session
from ..bench.fields import key_kind
from ..bench.spec import BenchError, _check_path
from .bench_session import POLL_SECONDS, BenchSession, SessionError, _Idea, _refused
from .simlock import SimBusy, SimLock


KEEP_JOBS = 10


class _Stopped(Exception):
    """The editor is closing: a queued job doesn't start."""


def _plain(e: BaseException) -> str:
    return str(e) or type(e).__name__


class BenchJobs:
    """The Bench tab's runs, sweeps, compares and galleries: one worker thread per App, jobs in order. Each job holds
    the App's SimLock and the session folder's run lock (`session.recorded`, source "page", so it is logged like a CLI
    or agent run and reaches the ideas' `latest` or the session's `result`); while another process holds the run lock
    the job stays queued."""

    def __init__(self, simlock: SimLock, current: Callable[[], BenchSession | None]):
        self._simlock = simlock
        self._current = current          # the App's open session (its log is re-read when a job finishes)
        self._cond = threading.Condition()
        self.version = 0
        self._jobs: list[dict] = []      # newest first, the last KEEP_JOBS
        self._queue: list[tuple[dict, Path, list[Path], Path, Callable[[], str | None]]] = []
        self._seq = 0
        self._busy = False               # a job is queued or running
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- the page's requests -----------------------------------------------------------------------------
    @staticmethod
    def _runnable(s: BenchSession, file) -> tuple[_Idea, list[dict]]:
        """The idea and its errors as a refusal's errors ([] when it can run)."""
        idea = s._need(file)
        if idea.lang != i18n.current_language():
            idea.reword()
        errs = [{"field": p["key"] or "status", "message": p["message"]} for p in idea.problems
                if p["level"] == "error"]
        if not errs and idea.doc.read_only_reason is not None:
            errs = [{"field": "status", "message": idea.doc.read_only_reason}]
        return idea, errs

    def submit(self, s: BenchSession, kind: str, body: dict) -> tuple[int, dict]:
        """Queue a page job on session `s`: {ok: true, id}, or 200 {ok: false, errors} when it can't run."""
        with s._lock:
            s._check_open()
            folder = s.folder
            if kind == "run":
                idea, errs = self._runnable(s, body.get("file"))
                if errs:
                    return 200, {"ok": False, "errors": errs}
                path = idea.doc.path
                files, out = [path], folder / f"{path.stem}-results"
                work = lambda: _run_job(path, out)   # noqa: E731
            elif kind == "sweep":
                idea, errs = self._runnable(s, body.get("file"))
                key, values = body.get("key"), body.get("values")
                if not isinstance(key, str) or not key:
                    errs.append({"field": "key",
                                 "message": i18n.t("key must be a dotted key, e.g. wheel.top.diameter")})
                else:
                    try:
                        _check_path(s._merged(idea, idea.raw), key)
                    except BenchError as e:
                        errs.append({"field": "key", "message": str(e)})
                if not isinstance(values, list) or not values:
                    errs.append({"field": "values", "message": i18n.t("values must be a non-empty list")})
                if errs:
                    return 200, {"ok": False, "errors": errs}
                path = idea.doc.path
                files, out = [path], folder / f"{path.stem}-sweep"
                work = lambda: _sweep_job(path, {key: list(values)}, out)   # noqa: E731
            elif kind == "compare":
                names = body.get("files")
                if not isinstance(names, list) or not all(isinstance(n, str) for n in names) \
                        or len(set(names)) < 2:
                    return _refused("files", i18n.t("compare needs two or more different ideas"))
                errs, files = [], []
                for name in dict.fromkeys(names):
                    idea, bad = self._runnable(s, name)
                    errs += [{**e, "message": i18n.t("{file}: {why}", file=idea.file, why=e["message"])} for e in bad]
                    files.append(idea.doc.path)
                if errs:
                    return 200, {"ok": False, "errors": errs}
                out = folder / "compare-results"
                work = lambda: _compare_job(files, out)   # noqa: E731
            elif kind == "gallery":
                files, out = [folder], folder / "gallery.html"
                work = lambda: _gallery_job(folder, out)   # noqa: E731
            else:
                raise SessionError(404, f"no bench job {kind!r}")
        with self._cond:
            if s._closed:   # an open replaced the session after the checks above (see gate())
                raise SessionError(409, i18n.t("the idea session was closed"))
            if self._stop.is_set():
                raise SessionError(409, i18n.t("the editor is closing"))
            self._seq += 1
            job = {"id": f"{kind}-{self._seq}", "kind": kind, "files": [f.name for f in files] if kind != "gallery"
                   else [], "state": "queued", "error": None, "out": str(out)}
            self._jobs.insert(0, job)
            del self._jobs[KEEP_JOBS:]
            self._queue.append((job, folder, files, out, work))
            self._busy = True
            self._bump()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, daemon=True, name="bench-jobs")
                self._thread.start()
        return 200, {"ok": True, "id": job["id"]}

    @contextlib.contextmanager
    def gate(self):
        """Hold off submits while an open replaces the session: 409 when a job is queued or running, else the body
        runs with no job able to queue (a submit then finds its session closed)."""
        with self._cond:
            if self._busy:
                raise SessionError(409, "a bench run is in progress; wait for it to finish")
            yield

    @property
    def active(self) -> bool:
        """A job of this App is queued or running."""
        with self._cond:
            return self._busy

    def jobs(self) -> dict:
        with self._cond:
            return {"version": self.version, "jobs": [dict(j) for j in self._jobs]}

    def wait_jobs(self, since: int | None) -> dict:
        end = time.monotonic() + POLL_SECONDS
        with self._cond:
            while since is not None and self.version == since and not self._stop.is_set() \
                    and time.monotonic() < end:
                self._cond.wait(1.0)
            return self.jobs()

    def clear(self) -> None:
        """Forget the finished jobs (another session was opened); the version keeps counting up."""
        with self._cond:
            self._jobs = [j for j in self._jobs if j["state"] in ("queued", "running")]
            self._bump()

    def stop(self, timeout: float = 30.0) -> None:
        """Stop the worker: queued jobs don't start; a running one is waited for up to `timeout` seconds. Never call
        it holding a lock the worker may need. The worker resets itself when it exits (also after a timed-out
        stop), so the next submit starts a new one."""
        with self._cond:
            thread = self._thread
            if thread is None:
                return
            self._stop.set()
            self._cond.notify_all()
        if thread is not threading.current_thread():
            thread.join(timeout)

    def _exit(self) -> None:
        """The worker is leaving on a stop (self._cond held): drop the queue and let a new worker start."""
        for job, *_ in self._queue:
            job.update(state="failed", error="the editor closed before it ran")
        self._queue.clear()
        if self._thread is threading.current_thread():
            self._thread = None
            self._stop.clear()
        self._busy = False
        self._bump()

    # -- the worker ------------------------------------------------------------------------------------
    def _bump(self) -> None:   # self._cond held
        self.version += 1
        self._cond.notify_all()

    def _set(self, job: dict, finished: bool = False, **kw) -> None:
        with self._cond:
            job.update(kw)
            if finished:   # the final state and "not busy" together: open isn't refused once the page sees it
                self._busy = bool(self._queue)
            self._bump()

    def _wait(self, ready: Callable[[], bool]) -> None:
        while not ready():
            if self._stop.wait(0.1):
                raise _Stopped()

    def _take_simlock(self) -> bool:
        try:
            self._simlock.acquire("bench")
        except SimBusy:   # a drive session or a scenario run (not in bench mode, but never two at once)
            return False
        return True

    def _work(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._stop.is_set():
                    self._cond.wait()
                if self._stop.is_set() or not self._queue:
                    self._exit()
                    return
                job, folder, files, out, work = self._queue.pop(0)
            try:
                self._execute(job, folder, files, out, work)
                self._sync(folder)   # latest shows the run before the job shows as done
                self._set(job, finished=True, state="done", error=None)
            except _Stopped:
                self._set(job, finished=True, state="failed", error="the editor closed before it ran")
            except BaseException as e:  # noqa: BLE001 - a job must never stay "queued" or "running"
                self._sync(folder)
                self._set(job, finished=True, state="failed", error=_plain(e))
            finally:
                with self._cond:   # a safety net: the job is settled and busy matches the queue
                    if job["state"] in ("queued", "running"):
                        job.update(state="failed", error="the job stopped unexpectedly")
                    self._busy = bool(self._queue)
                    self._bump()

    def _execute(self, job: dict, folder: Path, files: list[Path], out: Path, work) -> None:
        def free() -> bool:   # another process (the agent, the CLI) holds the folder's run lock: stay queued
            try:
                return session.lock_holder(folder) is None
            except OSError:
                return True   # an unwritable folder: recorded() runs it unshared
        self._wait(self._take_simlock)
        try:
            self._wait(free)
            with session.recorded(folder, job["kind"], files, out, "page"):
                self._set(job, state="running")
                work()
        finally:
            self._simlock.release("bench")

    def _sync(self, folder: Path) -> None:
        """Re-read the open session's log now, so a finished job's `latest` or `result` is in the state as the job
        ends."""
        s = self._current()
        if s is not None and s.folder == folder:
            try:
                s.sync()
            except Exception:
                logging.getLogger("scrimmage.assembler").exception("bench session sync after a job")


def _run_job(path: Path, out: Path) -> None:
    """`bench run`: the results and pictures in `out`. A failed energy check is in the results (summary.txt and the
    first headline), so `latest.energy_failed` reads it the same for every source; the job is done."""
    from ..bench.pictures import PICTURE_TRACE_HZ, write_pictures
    from ..bench.report import write_results
    from ..bench.spec import load_bench
    from ..render import RenderUnavailable
    result = bench_run.run_bench(load_bench(path), trace_hz=PICTURE_TRACE_HZ)
    out = write_results(result, out)
    try:
        write_pictures(result, out)
    except RenderUnavailable as e:
        logging.getLogger("scrimmage.assembler").warning("no pictures: %s", e)


def _sweep_job(path: Path, sweep: dict, out: Path) -> None:
    """`bench sweep` with the page's one key and values in place of the file's [sweep]: results.csv and a chart."""
    from ..bench import compare
    from ..launcher import report
    from ..bench.report import bench_estimates
    bench, benches = compare.sweep_benches(path, sweep)
    rows = compare.rows([(c, bench_run.run_bench(b)) for c, b in benches])
    out.mkdir(parents=True, exist_ok=True)
    report.write_csv(rows, out / "results.csv", compare.BENCH_METRICS)
    report.write_charts(rows, compare.default_charts(list(sweep)), out, None, compare.BENCH_METRICS,
                        compare.CHART_KINDS, footer=bench_estimates(bench), shown=compare.SHOWN, swept=key_kind)


def _compare_job(files: list[Path], out: Path) -> None:
    """`bench compare`: compare.csv and compare.png, the piece and feed of the first idea."""
    from PIL import Image

    from ..bench import compare, gallery
    from ..bench.pictures import PICTURE_TRACE_HZ
    from ..launcher import report
    results = compare.compare_benches(files, trace_hz=PICTURE_TRACE_HZ)
    rows = compare.rows([({"idea": n}, r) for n, r in zip(compare.idea_labels(results), results)])
    out.mkdir(parents=True, exist_ok=True)
    report.write_csv(rows, out / "compare.csv", compare.BENCH_METRICS)
    Image.fromarray(gallery.compare_picture(results)).save(out / "compare.png")


def _gallery_job(folder: Path, out: Path) -> None:
    """`bench gallery`: every bench file in the folder run once, one HTML page (a file that didn't run is listed with
    its problem)."""
    from ..bench import gallery
    gallery.write_gallery(folder, out, gallery.run_folder(folder))
