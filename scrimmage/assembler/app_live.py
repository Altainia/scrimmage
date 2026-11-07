"""The App's simulations (LiveMixin): scenario runs (started, stopped, watched for the Drive view, and their
files), and the Drive tab's live session (its stream messages, start and stop, the device check and saving
a recording).
A mixin of server.py's App: it has no __init__ (App.__init__ sets every attribute it uses) and never imports server.py.
"""

from __future__ import annotations

import math
import time
from pathlib import Path

from .. import i18n
from ..config import ConfigError, load_config_text
from ..lockstep import parse_speed
from ..scenario import ScenarioError
from ..scenario_runner import UNSET
from .live import FRAME_HEAD, AlreadyLive, LiveError, RecordingTaken, UnsavedRecording
from .recorder import RecordingError, RecordingExists
from .runs import RunBusy
from .server_common import POLL_SECONDS, FRAME_PERIOD, STATE_PERIOD, ApiError, _no_program, _unpicked
from .simlock import SimBusy


SOURCE_RUN = 1


class LiveMixin:
    """Scenario runs and the Drive tab's live session."""

    # -- runs -------------------------------------------------------------------------------
    def _runs_changed(self) -> None:
        with self._runs_cond:
            self._runs_cond.notify_all()

    def run(self, body: dict) -> tuple[int, dict]:
        with self._lock:
            sc = self._need_scenario()
            self._check_fresh(body, lambda: sc.version)
            res = self.scenario_result
            if not res.ok:
                errs = self.scenario_errors or [{"field": "status", "message": self.scenario_read_only_reason
                                            or "the scenario has errors"}]
                return 200, {"ok": False, "errors": errs}
            not_built = self._not_built()
            if not_built is not None:
                return 200, {"ok": False, "errors": [{"field": "start/not_built", "message": not_built}]}
            if self._start_problem(for_run=True) is not None:
                return 200, {"ok": False, "errors": [{"field": "start/unpicked", "message": _unpicked()}]}
            speed = body.get("speed")
            if speed is None:
                speed = UNSET
            else:
                try:
                    if isinstance(speed, bool) or not isinstance(speed, (str, int, float)):
                        raise ValueError(f'speed must be a positive number or "max", not {speed!r}')
                    speed = parse_speed(str(speed))
                except ValueError as e:
                    return 200, {"ok": False, "errors": [{"field": "status", "message": str(e)}]}
            path, text = sc.path, sc.text   # the run's snapshot: later edits don't change it
        try:
            run_id = self.runs.start(path, text, speed)
        except RunBusy as e:
            raise ApiError(409, str(e)) from None
        except ScenarioError as e:
            return 200, {"ok": False, "errors": [{"field": "status", "message": str(e)}]}
        return 200, {"ok": True, "id": run_id}

    def stop_run(self) -> dict:
        with self._lock:
            self._need_scenario()
        self.runs.stop()
        return {"ok": True}

    def wait_runs(self, since: int | None) -> dict:
        with self._lock:
            self._need_scenario()
        end = time.monotonic() + POLL_SECONDS
        with self._runs_cond:
            while True:
                r = self.runs.runs()
                if since is None or r["version"] != since or time.monotonic() >= end:
                    return r
                self._runs_cond.wait(1.0)

    def run_file(self, rest: str) -> Path:
        with self._lock:
            self._need_scenario()
        run_id, _, name = rest.partition("/")
        p = self.runs.file(run_id, name) if run_id and name else None
        if p is None:
            raise ApiError(404, i18n.t("no such run file"))
        return p

    # -- live -------------------------------------------------------------------------------
    def _live_message(self, msg: dict) -> None:
        """A message from an authenticated page on the stream (on the hub's loop)."""
        try:
            if msg.get("type") == "input":
                self.live.input(msg.get("ports"))
            elif msg.get("type") == "cmd":
                self.live.command({k: v for k, v in msg.items() if k != "type"})
        except LiveError as e:
            self.hub.publish("notice", {"kind": "error", "text": str(e)})

    def _live_client_gone(self) -> None:
        self.live.client_gone()

    def _watch_run(self, session, t: float) -> None:
        """After every robot step of a scenario run (its worker thread): frames and state for the Drive view."""
        now = time.monotonic()
        if now - self._watch_frame >= FRAME_PERIOD:
            self._watch_frame = now
            poses = session.mover_poses()
            self._watch_seq = (self._watch_seq + 1) & 0xFFFFFFFF
            self.hub.publish("frame", FRAME_HEAD.pack(self._watch_seq, SOURCE_RUN, t, len(poses))
                             + poses.astype("<f4").tobytes())
        if now - self._watch_state >= STATE_PERIOD:
            self._watch_state = now
            runs = self.runs.runs()["runs"]
            step_name = runs[0]["step_name"] if runs else None
            st = session.state()
            self.hub.publish("state", {"source": "run", "t": round(t, 4), "step_name": step_name,
                                       "mode": st["mode"], "range": st.get("range")})   # None: no test range

    def live_info(self) -> dict:
        _, cfg, problem = self._drivable()
        with self._lock:
            picked = self._start_problem() is None
        return {"port": self.hub.port, "status": self.live.status(), "run_active": self.runs.running,
                "can_start": problem is None, "start_problem": problem, "start_picked": picked,
                "real_ds_default": cfg is not None and cfg.real_ds}

    def _drivable(self):
        """(config path, config, None) when the robot document can be driven, else (None, None, why not)."""
        with self._lock:
            self._sync()
            self._robot_words()
            if self.robot is None:
                return None, None, self.robot_missing
            if not self.result.ok:
                return None, None, self.robot.read_only_reason or "; ".join(e.message for e in self.result.errors)
            path, text = self.robot.path, self.robot.text
        try:
            cfg = load_config_text(text, path)
        except ConfigError as e:
            return None, None, str(e)
        if cfg.robot_program is None:
            return None, None, _no_program()
        return path, cfg, None

    def live_start(self, body: dict) -> tuple[int, dict]:
        def refuse(message: str) -> tuple[int, dict]:
            return 200, {"ok": False, "errors": [{"field": "status", "message": message}]}

        discard = body.get("discard_recording", False)
        if not isinstance(discard, bool):
            raise ApiError(400, i18n.t("discard_recording must be true or false"))
        real_ds = body.get("real_ds")
        if real_ds is not None and not isinstance(real_ds, bool):
            raise ApiError(400, i18n.t("real_ds must be true, false or null"))
        # under the lock from the robot check to the start: no scenario open or edit switches the robot document
        # in between (LiveManager never calls back into the app, so holding it here can't deadlock)
        with self._lock:
            path, cfg, problem = self._drivable()
            if problem is not None:
                return refuse(problem)
            not_built = self._not_built()
            if not_built is not None:
                return 200, {"ok": False, "errors": [{"field": "start/not_built", "message": not_built}]}
            if self._start_problem() is not None:
                return 200, {"ok": False, "errors": [{"field": "start/unpicked", "message": _unpicked()}]}
            speed = body.get("speed", 1.0)
            if speed == "max":
                speed = None
            elif isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) \
                    or speed <= 0:
                return refuse(i18n.t("speed must be a positive number or \"{max}\", not {speed}", speed=repr(speed),
                                     max="max"))
            elif cfg.step / speed >= 1.0:
                return refuse(i18n.t("speed {speed} with {step} s steps: each step would take {wall} s of wall time or more, "
                                     "and the robot's 2 s watchdog would release the clock", speed=f"{speed:g}",
                                     step=f"{cfg.step:g}", wall=f"{cfg.step / speed:g}"))
            try:
                self.live.start(path, speed, discard_recording=discard, real_ds=real_ds)
            except SimBusy as e:
                raise ApiError(409, str(e), field=f"busy/{e.holder}") from None     # busy/run: a scenario run
            except UnsavedRecording as e:
                return 200, {"ok": False, "errors": [{"field": "live/unsaved", "message": str(e)}]}
            except AlreadyLive as e:
                raise ApiError(409, str(e), field="live/already") from None
            except LiveError as e:
                raise ApiError(409, str(e)) from None   # shutting down (the speed was checked)
        return 200, {"ok": True}

    def robot_check(self, body: dict) -> tuple[int, dict]:
        """Start and check devices: Drive's start (refused the same ways), disabled until someone enables it in
        Drive; /api/robot/devices then fills in."""
        return self.live_start({k: body[k] for k in ("speed", "discard_recording", "real_ds") if k in body})

    def live_stop(self) -> dict:
        self.live.stop()
        return {"ok": True}

    def save_recording(self, body: dict) -> tuple[int, dict]:
        path = body.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ApiError(400, i18n.t("path must be a file path"))
        overwrite = body.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise ApiError(400, i18n.t("overwrite must be true or false"))
        p = Path(path).expanduser()
        p = (p if p.is_absolute() else self.cwd / p).resolve()
        config = self.live.status()["config"]
        if config is None:
            with self._lock:
                config = str(self.robot.path) if self.robot is not None else None
        allowed = [self.cwd] + ([Path(config).resolve().parent] if config else [])
        if not any(p.is_relative_to(base) and p != base for base in allowed):
            return 200, {"ok": False, "errors": [{"field": "status", "message": i18n.t(
                         "{path} is outside the folder scrimmage edit was started in and the robot config's folder; "
                         "save the recording in one of them", path=p)}]}
        try:
            res = self.live.save_recording(p, overwrite)
        except (LiveError, RecordingError) as e:
            field = "save/exists" if isinstance(e, (RecordingExists, RecordingTaken)) else "status"
            return 200, {"ok": False, "errors": [{"field": field, "message": str(e)}]}
        return 200, {"ok": True, "path": res["path"], "display_path": self._display(Path(res["path"])),
                     "steps": res["steps"], "pokes": res["pokes"]}
