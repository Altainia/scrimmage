"""The scenario run manager behind the editor's Run and Stop buttons: one run at a time, on a worker thread."""

from __future__ import annotations

import asyncio
import copy
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

import tomlkit

from .. import i18n, robot_process
from ..scenario import ScenarioError, load_scenario_text, runs_folder
from ..scenario_runner import STOPPED, UNSET, run_scenario
from .simlock import SimBusy, SimLock

KEEP_RUNS = 10
CONSOLE_LINES = 200


class RunBusy(Exception):
    """A run is already in progress."""


class RunManager:
    def __init__(self, on_change: Callable[[], None], runs_dir: Path | str = "runs", simlock: SimLock | None = None,
                 watch: Callable[[object, float], None] | None = None):
        self._on_change = on_change
        self._simlock = simlock if simlock is not None else SimLock()
        self._watch = watch     # watch(session, t) after every robot step (the live view)
        self._runs_dir = Path(runs_dir)  # the CLI's default base folder (relative to the working directory)
        self._lock = threading.Lock()
        self._version = 0
        self._runs: list[dict] = []          # newest first
        self._dirs: dict[str, Path] = {}
        self._thread: threading.Thread | None = None
        self._cancel: threading.Event | None = None
        self._seq = 0
        self._running = False   # cleared with the run's final state, under the lock

    # -- public ---------------------------------------------------------------------------------------------

    def start(self, scenario_path: Path | str, text: str, speed=UNSET) -> str:
        """Snapshot `text` into a new run folder and run it; raises RunBusy (also when a drive session holds the
        simulation), or ScenarioError when the text can't run (nothing is left behind then). `speed` overrides the
        scenario's (None = as fast as possible); leave it out to use the scenario's own."""
        scenario_path = Path(scenario_path).resolve()
        with self._lock:
            if self._running:
                raise RunBusy(i18n.t("a run is already in progress"))
            try:
                self._simlock.acquire("run")
            except SimBusy as e:
                raise RunBusy(str(e)) from None
        try:
            return self._start(scenario_path, text, speed)
        except BaseException:
            with self._lock:
                if not self._running:
                    self._simlock.release("run")
            raise

    def _start(self, scenario_path: Path, text: str, speed) -> str:
        try:
            doc = tomlkit.parse(text)
            if not isinstance(doc.get("config"), str):
                raise ScenarioError(i18n.t("{name}: {config_setting} is required", name=scenario_path.name,
                                           config_setting="config = \"<robot config .toml>\""))
            doc["config"] = str((scenario_path.parent / doc["config"]).resolve())
            snapshot_text = tomlkit.dumps(doc)
            scenario = load_scenario_text(snapshot_text, scenario_path)
        except ScenarioError:
            raise
        except Exception as e:
            raise ScenarioError(f"{scenario_path.name}: {e}") from None
        with self._lock:
            self._seq += 1
            run_id = f"{scenario_path.stem}-{datetime.now():%Y%m%d-%H%M%S}"
            if run_id in self._dirs or (self._runs_dir / run_id).exists():
                run_id = f"{run_id}-{self._seq}"
            out_dir = (self._runs_dir / run_id).resolve()
            try:
                runs_folder(self._runs_dir)
                out_dir.mkdir(parents=True)
            except OSError as e:
                raise ScenarioError(i18n.t("can't make the run folder {folder}: {why}", folder=out_dir,
                                           why=e.strerror or e)) from None
            try:
                snapshot = out_dir / "scenario.toml"
                snapshot.write_text(snapshot_text)
            except OSError as e:
                shutil.rmtree(out_dir, ignore_errors=True)
                raise ScenarioError(i18n.t("can't write the run folder {folder}: {why}", folder=out_dir,
                                           why=e.strerror or e)) from None
            # `scenario` is the snapshot's text (config made absolute); its path stays the student's file, so the
            # report is titled with their scenario's name rather than "scenario.toml"
            self._dirs[run_id] = out_dir
            status = {"id": run_id, "state": "running", "step": None, "step_name": None, "t": 0.0,
                      "total_t": sum(s.duration for s in scenario.steps), "console": [], "report": None,
                      "out_dir": str(out_dir)}
            self._runs.insert(0, status)
            for old in self._runs[KEEP_RUNS:]:
                self._dirs.pop(old["id"], None)
            del self._runs[KEEP_RUNS:]
            self._cancel = threading.Event()
            self._running = True
            self._thread = threading.Thread(target=self._work, name=f"run-{run_id}", daemon=True,
                                            args=(run_id, scenario, out_dir, speed, self._cancel))
            self._thread.start()
            self._bump()
        self._on_change()
        return run_id

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def stop(self) -> None:
        with self._lock:
            cancel = self._cancel
        if cancel is not None:
            cancel.set()

    def join(self, timeout: float | None = None) -> bool:
        """Waits for the worker; True if it has finished."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def kill_robots(self) -> None:
        """Kill, by their run tokens, the robot programs the worker thread started and hasn't stopped: for a worker
        that didn't finish after Stop (Linux only; elsewhere a no-op)."""
        with self._lock:
            thread = self._thread
        if thread is not None and thread.ident is not None:
            for token in robot_process.tokens_started_by(thread.ident):
                robot_process.kill_by_token(token, timeout=2.0)

    def runs(self, since: int = 0) -> dict:
        with self._lock:
            return {"version": self._version, "runs": copy.deepcopy(self._runs)}

    def file(self, run_id: str, name: str) -> Path | None:
        with self._lock:
            out_dir = self._dirs.get(run_id)
        if out_dir is None or not name or "\x00" in name:
            return None
        p = (out_dir / name).resolve()
        if out_dir not in p.parents or not p.is_file():
            return None
        return p

    # -- worker ---------------------------------------------------------------------------------------------

    def _bump(self) -> None:  # lock held
        self._version += 1

    def _update(self, run_id: str, fn: Callable[[dict], None]) -> None:
        with self._lock:
            for r in self._runs:
                if r["id"] == run_id:
                    fn(r)
                    break
            self._bump()
        self._on_change()

    def _work(self, run_id: str, scenario, out_dir: Path, speed, cancel: threading.Event) -> None:
        """The run, as MCP's run_scenario runs it: its machine text (report.json) in American English, inside
        i18n.machine(); report.md and the page's words (to_dict(shown=True)) in the Language setting (spec L2)."""
        with i18n.machine():
            self._work_guarded(run_id, scenario, out_dir, speed, cancel)

    def _work_guarded(self, run_id: str, scenario, out_dir: Path, speed, cancel: threading.Event) -> None:
        def progress(e: dict) -> None:
            kind = e.get("type")
            if kind == "step_start":
                def f(r): r.update(step=e["index"], step_name=e["name"])
            elif kind == "tick":
                def f(r): r["t"] = e["t"]
            elif kind == "console":
                def f(r):
                    r["console"].append(e["line"])
                    del r["console"][:-CONSOLE_LINES]
            else:
                return  # "done" is settled below, with the report
            self._update(run_id, f)

        kw = {} if speed is UNSET else {"speed": speed}
        report, error = None, None
        try:
            report = asyncio.run(run_scenario(scenario, out_dir, progress=progress, cancel=cancel, watch=self._watch,
                                              **kw))
        except BaseException as e:  # noqa: BLE001 - the worker must always settle the run
            error = str(e) or type(e).__name__

        def finish(r: dict) -> None:
            try:
                if report is not None:
                    with i18n.for_people():
                        r["report"] = report.to_dict(shown=True)   # the checks in the Units and Language settings
                    if report.error is not None:
                        # "stopped" only when the stop is why it ended; any other error shows as itself
                        r["state"] = "stopped" if report.error == STOPPED else "error"
                    else:
                        r["state"] = "passed" if report.passed else "failed"
                    return
                r["console"].append(f"error: {error}")
            except Exception as e:  # noqa: BLE001 - a run must never stay "running"
                r["report"] = None
                r["console"].append(f"error: couldn't read the report: {type(e).__name__}: {e}")
            r["state"] = "error"

        with self._lock:
            for r in self._runs:
                if r["id"] == run_id:
                    finish(r)
                    break
            self._simlock.release("run")
            self._running = False
            self._bump()
        self._on_change()
