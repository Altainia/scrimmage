"""The App's documents (AttachMixin): which robot config the open scenario or test points at, attaching it as
the robot document or saying why there is none, validating both documents (in the Language setting), picking
up outside edits, and the version check and long poll every editing route uses.
A mixin of server.py's App: it has no __init__ (App.__init__ sets every attribute it uses) and never imports server.py.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import mujoco

from .. import i18n, teamfolders
from . import scenario_validate, validate
from .doc import ConfigDoc, FileChanged
from .server_common import POLL_SECONDS, ApiError, _plain, _fe, unset_folder, _hand_written


class AttachMixin:
    """The robot and scenario documents: attaching, validating, syncing with the files, and waiting for a change."""

    # -- documents --------------------------------------------------------------------------
    @property
    def robot_version(self) -> int:
        return self.robot.version if self.robot is not None else self._robot_ver

    @staticmethod
    def _config_named(d, scenario_path: Path) -> Path | None:
        """The robot config a parsed scenario `d` names (resolved against its folder), or None (d is None for a
        TOML error, or it has no string `config`)."""
        c = _plain(d.get("config")) if d is not None else None
        if not isinstance(c, str):
            return None
        try:
            c = teamfolders.expand(c)
        except teamfolders.TeamFolderError:
            return None   # _point_robot says which folder to set
        return (scenario_path.parent / c).resolve()

    def _scenario_config(self, sc: ConfigDoc | None) -> Path | None:
        """The robot config the scenario document `sc` names, or None (no scenario, a TOML error, no `config`)."""
        return self._config_named(sc.doc, sc.path) if sc is not None else None

    def _check_live_robot(self, target: Path | None) -> None:
        """While a drive session is live (starting, running or paused), the robot document must stay its config:
        409 when `target` (the robot config a scenario change would point at) is another one, or isn't known
        (None) or readable. Called with self._lock held."""
        st = self.live.status()
        if st["state"] not in ("starting", "running", "paused"):
            return
        live = Path(st["config"]).resolve()
        if target is not None and target.is_file() and os.access(target, os.R_OK):
            if target.resolve() == live:
                return
            try:
                if os.path.samefile(target, live):
                    return
            except OSError:
                pass
        raise ApiError(409, i18n.t("a drive session is live"))

    def _detach(self, words, target: Path | None, initial: bool) -> bool:
        """No robot document, for the reason `words()` says (a function, so a Language change words it again:
        _reword_missing); True when that changed."""
        self._reword_missing()
        reason = words()
        if self.robot is None and self.robot_missing == reason:
            self._missing_words = words
            return False
        if not initial:
            self._robot_ver = self.robot_version + 1
        self.robot, self.robot_missing, self._robot_target = None, reason, target
        self._missing_words, self._missing_lang = words, i18n.current_language()
        return not initial

    def _reword_missing(self) -> None:
        """Why there is no robot document, again in the Language setting when it changed since. Lock held."""
        if self.robot is None and self._missing_words is not None and self._missing_lang != i18n.current_language():
            self.robot_missing, self._missing_lang = self._missing_words(), i18n.current_language()

    def _point_robot(self, initial: bool = False) -> bool:
        """Make the robot document the scenario's config; True when it was replaced, detached or reattached."""
        sc = self.scenario
        target = self._scenario_config(sc)
        if target is None:
            def reason() -> str:
                if sc.doc is None:
                    return i18n.t("the scenario {name} has a TOML error, so its robot config isn't known; fix the "
                                  "scenario in a text editor", name=sc.path.name)
                unset = unset_folder(_plain(sc.doc.get("config")))
                return unset[1] if unset else i18n.t("this scenario names no robot config ({setting})",
                                                     setting='config = "<robot config .toml>"')
            return self._detach(reason, None, initial)
        return self._attach_robot(target, initial)

    def _point_test_robot(self, initial: bool = False) -> bool:
        """Make the robot document the test's robot; True when it was replaced, detached or reattached."""
        return self._attach_robot(self.test.robot_path, initial)

    def _attach_robot(self, target: Path, initial: bool) -> bool:
        """Make the robot config `target` the robot document (a scenario's or a test's); True when it was replaced,
        detached or reattached."""
        if self.robot is not None and self.robot.path == target:
            if target.exists():
                return False
            return self._detach(lambda: i18n.t("the robot config {path} was deleted", path=self._display(target)),
                                target, initial)
        doc, reason = None, None
        try:
            doc = ConfigDoc(target)
        except FileNotFoundError:
            def reason() -> str:
                return i18n.t("the robot config {path} doesn't exist", path=self._display(target))
        except OSError as e:
            why = e.strerror or e

            def reason() -> str:
                return i18n.t("can't read the robot config {path}: {why}", path=self._display(target), why=why)
        if doc is None:
            if self.robot is None and self._robot_target == target and self.robot_missing is not None:
                return False   # still missing: keep saying why (e.g. "was deleted")
            return self._detach(reason, target, initial)
        doc.version = 0 if initial else self.robot_version + 1
        self.robot, self.robot_missing, self._robot_target = doc, None, target
        return True

    def _revalidate(self) -> None:
        """Validate the robot as it is now; a hand-written or invalid config becomes read-only."""
        self._view = None
        self._scenario_view = None
        self._signals = None
        self._parts_seen = self._parts_look()
        self._robot_lang = i18n.current_language()
        if self.robot is None:
            self.result = validate.Result(False, [validate.FieldError("status", self.robot_missing)], [], None)
        else:
            self.result = validate.check(self.robot.text, self.robot.path)
            if self.robot.read_only_reason is None:
                unset = unset_folder(self.robot.doc.unwrap()) if self.robot.doc is not None else None
                if not self.result.ok and unset is not None:
                    self.robot.read_only_reason = unset[1]
                elif not self.result.ok:
                    errors = "; ".join(e.message for e in self.result.errors)
                    d = self.robot.doc
                    if self.scenario is None and d is not None and "part" not in d and "model" not in d:
                        self.robot.read_only_reason = i18n.t(
                            "this config has errors; fix them in a text editor: {errors}; to start a new one, use "
                            "scrimmage edit FILE --new swerve|differential or --new-scenario --robot CONFIG",
                            errors=errors)
                    else:
                        self.robot.read_only_reason = i18n.t("this config has errors; fix them in a text editor: {errors}",
                                                             errors=errors)
                elif self.result.world.parts is None:
                    self.robot.read_only_reason = _hand_written()
        if self.test is not None:
            self.test.refresh(self._robot_world(), self._robot_problem())
        self._changed.notify_all()

    def _robot_problem(self) -> str | None:
        """Why the robot document has no World, in a sentence (None when it has one)."""
        if self._robot_world() is not None:
            return None
        if self.robot is None:
            return self._rel(self.robot_missing)
        return self._rel(i18n.t("{name} has errors: {errors}", name=self.robot.path.name,
                                errors="; ".join(e.message for e in self.result.errors)))

    def _scenario_signature(self):
        r = self.scenario_result
        return None if r is None else (r.ok, self.scenario_errors, _fe(r.warnings), self.scenario_robot_problem,
                                       self.scenario_read_only_reason)

    def _rel(self, text: str | None) -> str | None:
        """Paths under the working directory shown relative to it."""
        return text.replace(str(self.cwd) + os.sep, "") if text else text

    def _set_scenario_result(self, res: scenario_validate.Result, bump: bool = False) -> None:
        before = self._scenario_signature()
        self.scenario_result = res
        problem = res.robot_problem
        if self.robot is None and self.robot_missing is not None and (problem or self.scenario.doc is None):
            problem = self.robot_missing   # our own words: "was deleted", "has a TOML error", ...
        problem = self._rel(problem)
        self.scenario_robot_problem = problem
        self._scenario_words()
        if bump and self._scenario_signature() != before:
            self.scenario.version += 1   # the page's next state shows the new errors / read-only state
        self._changed.notify_all()

    def _scenario_words(self) -> None:
        """The scenario's errors and read-only reason in the Language setting: the robot's problem in our words (L3:
        scenario_validate marks its "fix the robot first" error with a code, not by its text)."""
        res, problem = self.scenario_result, self.scenario_robot_problem
        first = i18n.t("fix the robot first: {why}", why=problem)
        self.scenario_errors = [{"field": e.field, "message": first if e.code == scenario_validate.ROBOT_FIRST else
                                 e.message, **({"detail": e.detail} if e.detail is not None else {})}
                                for e in res.errors]    # detail: without its where, for beside its field
        self.scenario_read_only_reason = self.scenario.read_only_reason or (first if res.robot_problem else None)
        self._scenario_lang = i18n.current_language()

    def _refusal(self, res: scenario_validate.Result) -> list[dict] | None:
        """Why a candidate scenario can't be written, or None. A valid file only takes a valid candidate; an invalid
        one (its robot fine) takes a candidate with no more errors, so fixing one error may reveal the next."""
        cur = self.scenario_result
        if res.ok:
            return None
        if res.robot_problem is None and not cur.ok and cur.robot_problem is None \
                and len(res.errors) <= len(cur.errors):
            return None
        return _fe(res.errors)

    def _revalidate_scenario(self, bump: bool = False) -> None:
        if self.scenario is not None:
            self._set_scenario_result(scenario_validate.check(self.scenario.text, self.scenario.path), bump)

    def _sync(self) -> bool:
        """Pick up outside edits of either file; True when something changed."""
        s_changed = self.scenario is not None and self.scenario.reload_if_changed()
        r_changed = self.scenario is not None and self._point_robot()
        if self.test is not None:
            self.test.reload_if_changed()
            r_changed = self._point_test_robot() or r_changed
        if self.robot is not None and self.robot.reload_if_changed():
            r_changed = True
        if self.robot is not None and self._parts_look() != self._parts_seen:
            # a part folder changed (Import again rewrites the folder, not the robot config): build the robot again,
            # past MuJoCo's cache, which keeps a mesh by its file name and time to the second
            mujoco.mj_clearCache(mujoco.mj_getCache())
            if not r_changed:
                self.robot.version += 1
            r_changed = True
        if r_changed:
            self._revalidate()
        if s_changed:
            self._revalidate_scenario()
        elif r_changed:
            self._revalidate_scenario(bump=True)
        return bool(s_changed or r_changed)

    def _check_fresh(self, body: dict, current) -> None:
        """`current` is a callable giving the document's version after a sync."""
        v = body.get("version")
        if not isinstance(v, int) or isinstance(v, bool):
            raise ApiError(400, i18n.t("version (the integer from the state) is required"))
        before = current()
        if self._sync() and current() != before:
            raise ApiError(409, FileChanged().message)
        if v != current():
            raise ApiError(409, i18n.t("the file changed (another tab?); the page has reloaded it"))

    def _display(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.cwd))
        except ValueError:
            return str(path)

    def _wait(self, since: int | None, current, make) -> dict:
        end = time.monotonic() + POLL_SECONDS
        with self._lock:
            while True:
                self._sync()
                if since is None or current() != since or time.monotonic() >= end:
                    return make()
                self._changed.wait(1.0)

    def _robot_world(self):
        """The robot's world when its config loads (hand-written robots included), else None."""
        return self.result.world if self.result is not None and self.result.ok else None
