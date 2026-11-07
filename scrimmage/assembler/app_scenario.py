"""The App's Scenario tab (ScenarioMixin): the scenario document's state, its input tracks, edits, undo and
redo, the robot's signals and world view (Drive uses them too), the robot configs to pick from, and opening
another scenario.
A mixin of server.py's App: it has no __init__ (App.__init__ sets every attribute it uses) and never imports server.py.
"""

from __future__ import annotations

import os
from pathlib import Path

import tomlkit
from tomlkit.exceptions import TOMLKitError

from .. import i18n
from ..driverstation import MATCH_KEYS, STATIONS
from ..tracks import TrackError, load_track
from . import scenario_edits, scenario_validate
from .doc import ConfigDoc, FileChanged
from .edits import EditError
from .server_common import ApiError, _nothing_to, _cant, _fe, _is_num, classify
from .signals import signals
from .view import model_view


class ScenarioMixin:
    """The Scenario tab: the scenario document's state, edits, history, signals, views and opening one."""

    # -- scenario ---------------------------------------------------------------------------
    def _need_scenario(self) -> ConfigDoc:
        if self.scenario is None:
            raise ApiError(404, i18n.t("no scenario is open (scrimmage edit was given a robot config)"))
        return self.scenario

    def scenario_state(self) -> dict:
        sc = self._need_scenario()
        res = self.scenario_result
        if self._scenario_lang != i18n.current_language():
            # the Language setting changed since: the errors again, in its words (the file is the same), the reason
            # no robot is attached too
            self._reword_missing()
            self._set_scenario_result(scenario_validate.check(sc.text, sc.path))
            res = self.scenario_result
        raw = sc.doc.unwrap() if sc.doc is not None else {}
        top = {k: raw.get(k) for k in ("config", "description", "speed", "require_lockstep")}
        top["match"] = raw["match"] if isinstance(raw.get("match"), dict) else {}
        steps = []
        t = 0.0
        for s in raw.get("step", []) if isinstance(raw.get("step"), list) else []:
            if not isinstance(s, dict):
                continue
            checks = []
            for c in s.get("check", []) if isinstance(s.get("check"), list) else []:
                if isinstance(c, dict) and len(c) == 1:
                    (kind, spec), = c.items()
                    checks.append({"kind": kind, "spec": spec})
                else:
                    checks.append({"kind": None, "spec": c})
            shots = s.get("screenshot", [])
            sheet = s.get("contact_sheet")
            steps.append({"name": s.get("name"), "mode": s.get("mode"), "duration": s.get("duration"),
                          "game_data": s.get("game_data"), "start_t": t,
                          "inputs": s.get("inputs") if isinstance(s.get("inputs"), str) else None,
                          **{k: s.get(k) if isinstance(s.get(k), dict) else {} for k in ("axes", "buttons", "povs")},
                          "checks": checks,
                          "shots": [x for x in shots if isinstance(x, dict)] if isinstance(shots, list) else [],
                          "sheet": sheet if isinstance(sheet, dict) else None})
            d = s.get("duration")
            t += d if _is_num(d) and d > 0 else 0.0
        ro = self.scenario_read_only_reason
        return {"version": sc.version, "path": str(sc.path), "display_path": self._display(sc.path),
                "read_only": ro is not None, "read_only_reason": ro, "robot_problem": self.scenario_robot_problem,
                "top": top, "steps": steps, "errors": self.scenario_errors, "warnings": _fe(res.warnings),
                "can_undo": ro is None and sc.can_undo, "can_redo": ro is None and sc.can_redo,
                "stations": list(STATIONS), "match_keys": list(MATCH_KEYS)}

    def scenario_track(self, step) -> dict:
        """The input track of step `step` for the Scenario tab's plot: {path, duration, t, series}; 404 when the
        step has no `inputs` or the file doesn't load."""
        with self._lock:
            sc = self._need_scenario()
            raw = sc.doc.unwrap() if sc.doc is not None else {}
            steps = raw.get("step") if isinstance(raw.get("step"), list) else []
            try:
                i = int(step)
            except (TypeError, ValueError):
                raise ApiError(400, i18n.t("step must be an integer")) from None
            s = steps[i] if 0 <= i < len(steps) and isinstance(steps[i], dict) else None
            if s is None or not isinstance(s.get("inputs"), str):
                raise ApiError(404, i18n.t("step {step} has no input track", step=i))
            try:
                track = load_track(sc.path.parent / s["inputs"])
            except TrackError as e:
                raise ApiError(404, str(e)) from None
            dur = s.get("duration")
        series: dict[str, list] = {}
        widths: dict[int, tuple[int, int, int]] = {}
        for smp in track.samples:
            for p, pi in smp.ports.items():
                widths[p] = (len(pi.axes), len(pi.buttons), len(pi.povs))
        for p, (na, nb, nv) in sorted(widths.items()):
            for k in range(na):
                series[f"p{p}.axis{k}"] = [smp.ports[p].axes[k] for smp in track.samples]
            for k in range(nb):
                series[f"p{p}.button{k + 1}"] = [int(smp.ports[p].buttons[k]) for smp in track.samples]
            for k in range(nv):
                series[f"p{p}.pov{k}"] = [smp.ports[p].povs[k] for smp in track.samples]
        return {"path": s["inputs"], "duration": dur, "t": [smp.t for smp in track.samples], "series": series}

    def wait_scenario_state(self, since: int | None) -> dict:
        with self._lock:
            self._need_scenario()
        # another file opened meanwhile: the version changes (or there is no scenario: 404)
        return self._wait(since, lambda: self.scenario.version if self.scenario is not None else None,
                          self.scenario_state)

    def _require_scenario_writable(self) -> None:
        if self.scenario_read_only_reason is not None:
            raise ApiError(409, self.scenario_read_only_reason)

    @staticmethod
    def _scenario_op(op: str, b: dict, scenario_path: Path, made: list):
        e = scenario_edits
        ops = {
            "top": lambda d: e.set_top(d, b.get("key"), b.get("value")),
            "add_step": lambda d: e.add_step(d, b.get("after"), b.get("name"), b.get("mode"), b.get("duration")),
            "duplicate_step": lambda d: e.duplicate_step(d, b.get("step")),
            "delete_step": lambda d: e.delete_step(d, b.get("step")),
            "move_step": lambda d: e.move_step(d, b.get("step"), b.get("to")),
            "set_step": lambda d: e.set_step(d, b.get("step"), b.get("fields") or {}),
            "match": lambda d: e.set_match(d, b.get("key"), b.get("value")),
            "game_data": lambda d: e.set_step_game_data(d, b.get("step"), b.get("value")),
            "inputs_file": lambda d: e.set_inputs_file(d, b.get("step"), b.get("path")),
            "trim_track": lambda d: made.append(e.trim_track(d, b.get("step"), b.get("start"), b.get("end"), scenario_path)),
            "inputs": lambda d: e.set_inputs(d, b.get("step"), b.get("kind"), b.get("port"), b.get("values")),
            "add_check": lambda d: e.add_check(d, b.get("step"), b.get("kind"), b.get("spec")),
            "set_check": lambda d: e.set_check(d, b.get("step"), b.get("check"), b.get("spec")),
            "delete_check": lambda d: e.delete_check(d, b.get("step"), b.get("check")),
            "add_shot": lambda d: e.add_shot(d, b.get("step"), b.get("spec") or {}),
            "set_shot": lambda d: e.set_shot(d, b.get("step"), b.get("shot"), b.get("spec")),
            "delete_shot": lambda d: e.delete_shot(d, b.get("step"), b.get("shot")),
            "sheet": lambda d: e.set_sheet(d, b.get("step"), b.get("spec")),
        }
        if op not in ops:
            raise ApiError(400, i18n.t("unknown op {op} ({choices})", op=repr(op), choices=', '.join(ops)))
        return ops[op]

    def _check_config_change(self, sc: ConfigDoc, text: str) -> None:
        """409 when writing `text` would point the robot document away from a live session's config."""
        try:
            d = tomlkit.parse(text)
        except TOMLKitError:   # not TOML: its config isn't known
            d = None
        target = self._config_named(d, sc.path)
        if target != self._scenario_config(sc):
            self._check_live_robot(target)

    def _scenario_changed(self, res: scenario_validate.Result) -> None:
        """After the scenario file was written: follow its config, then take `res` as its validation."""
        if self._point_robot():
            self._revalidate()
        self._set_scenario_result(res)

    def scenario_edit(self, body: dict) -> tuple[int, dict]:
        with self._lock:
            sc = self._need_scenario()
            self._check_fresh(body, lambda: sc.version)
            self._require_scenario_writable()
            made: list[Path] = []   # new track files this edit wrote; removed again unless the edit is committed
            fn = self._scenario_op(body.get("op"), body, sc.path, made)
            committed = False
            try:
                try:
                    candidate = sc.apply(fn)
                except EditError as e:
                    return 200, {"ok": False, "errors": [{"field": e.field, "message": e.message}]}
                except (TypeError, ValueError, AttributeError, KeyError, IndexError) as e:   # malformed client values
                    return 200, {"ok": False, "errors": [{"field": "status", "message": f"bad edit: {e}"}]}
                res = scenario_validate.check(candidate, sc.path)
                refused = self._refusal(res)
                if refused is not None:
                    return 200, {"ok": False, "errors": refused}
                self._check_config_change(sc, candidate)
                try:
                    sc.commit(candidate)
                except FileChanged as e:
                    self._sync()
                    raise ApiError(409, e.message) from None
                except EditError as e:
                    return 200, {"ok": False, "errors": [{"field": e.field, "message": e.message}]}
                except OSError as e:
                    return 200, {"ok": False, "errors": [{"field": "status", "message": i18n.t("can't write the file: {why}",
                                                                                               why=e)}]}
                committed = True
            finally:
                if not committed:
                    for f in made:
                        f.unlink(missing_ok=True)
            self._scenario_changed(res)
            return 200, {"ok": True, "version": sc.version}

    def scenario_history(self, which: str, body: dict) -> tuple[int, dict]:
        with self._lock:
            sc = self._need_scenario()
            self._check_fresh(body, lambda: sc.version)
            self._require_scenario_writable()
            target = sc.peek_undo() if which == "undo" else sc.peek_redo()
            if target is None:
                return 200, {"ok": False, "errors": [{"field": "status", "message": _nothing_to(which)}]}
            res = scenario_validate.check(target, sc.path)   # the robot may have changed since
            refused = self._refusal(res)
            if refused is not None:
                return 200, {"ok": False, "errors": [{"field": e["field"], "message": _cant(which, e["message"])}
                                                      for e in refused]}
            self._check_config_change(sc, target)
            try:
                sc.undo() if which == "undo" else sc.redo()
            except FileChanged as e:
                self._sync()
                raise ApiError(409, e.message) from None
            except OSError as e:
                raise ApiError(500, i18n.t("can't write the file: {why}", why=e)) from None
            self._scenario_changed(res)
            return 200, {"ok": True, "version": sc.version}

    def robot_signals(self) -> dict:
        """signals(world) of the robot document, or {} when it doesn't load."""
        with self._lock:
            w = self._robot_world()
            if w is None:
                return {}
            if self._signals is None:
                self._signals = signals(w)
            return self._signals

    def scenario_signals(self) -> dict:
        with self._lock:
            self._need_scenario()
            return self.robot_signals()

    def scenario_configs(self) -> dict:
        with self._lock:
            sc = self._need_scenario()
            base = sc.path.parent.resolve()
        out, seen = [], set()
        for folder in (self.cwd / "configs", base):
            try:
                files = sorted(p for p in folder.glob("*.toml") if p.is_file())
            except OSError:
                continue
            for p in files:
                p = p.resolve()
                if p in seen or p == sc.path.resolve() or classify(p) != "robot":
                    continue
                seen.add(p)
                try:
                    out.append(Path(os.path.relpath(p, base)).as_posix())
                except ValueError:   # another drive (Windows)
                    out.append(p.as_posix())
        return {"configs": out}

    def world_view(self) -> dict:
        """model_view(world, include_world=True) of the robot document, or {} when it doesn't load."""
        with self._lock:
            w = self._robot_world()
            if w is None:
                return {}
            if self._scenario_view is None:
                self._scenario_view = model_view(w, include_world=True)
            return self._scenario_view

    def scenario_view(self) -> dict:
        with self._lock:
            self._need_scenario()
            return self.world_view()

    def open_scenario(self, path) -> dict:
        """Make `path` the scenario document, as if it had been given to `scrimmage edit`; the robot document
        follows its config."""
        if not isinstance(path, str) or not path.strip():
            raise ApiError(400, i18n.t("path must be a scenario file path"))
        p = Path(path).expanduser()
        p = (p if p.is_absolute() else self.cwd / p).resolve()
        if not p.is_file() or classify(p) != "scenario":
            raise ApiError(400, i18n.t("{path} is not a scenario file", path=self._display(p)))
        if self.runs.running:
            raise ApiError(409, i18n.t("a scenario run is in progress"))
        with self._lock:
            if self.test is not None and self.test.busy:
                raise ApiError(409, i18n.t("a test run is in progress"))
            try:
                doc = ConfigDoc(p)
            except OSError as e:
                raise ApiError(400, i18n.t("can't read {path}: {why}", path=self._display(p), why=e.strerror or e)) from None
            self._check_live_robot(self._scenario_config(doc))   # a live session keeps its robot document
            if self.scenario is not None:
                doc.version = self.scenario.version + 1   # the page's long poll sees the new document
            self.scenario = doc
            if self.test is not None:   # the scenario takes the test's place
                self.test.close()
                self.test = None
            if not self._point_robot() and self.robot is not None:
                self.robot.version += 1   # the same robot: its state now says scenario_open
            self._revalidate()
            self._revalidate_scenario()
        return {"ok": True}
