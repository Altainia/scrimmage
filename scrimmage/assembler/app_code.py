"""The App's robot code panel (CodeMixin): the [robot_program] folder and its readiness checks, where the robot
starts, the battery, whether the Java code is built, pointing the robot at its code, and the fixes (a Gradle
build, ...) run one at a time on their own worker thread.
A mixin of server.py's App: it has no __init__ (App.__init__ sets every attribute it uses) and never imports server.py.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import threading
import time
from pathlib import Path

from .. import i18n, robotcode, teamfolders
from ..testrange.spec import BUILTIN_RANGES, RangeError, load_range
from . import edits
from .server_common import POLL_SECONDS, ApiError, _sentence, _is_num, _unpicked, _not_built, start_sentence, _info_json


FIXES = ("build_agent", "gradle_build", "install_lockstep")
FIX_LINES = 1000          # a fix's output lines kept for the page (the last ones)
BATTERY_DEFAULTS = {"nominal_voltage": 12.0, "resistance": 0.02}   # world.py's, when [battery] leaves one out


class CodeMixin:
    """The Robot tab's robot code panel: the code folder, start, battery, build state and fixes."""

    # -- robot code, where it starts, battery (the Robot tab's robot code panel) --------------------------------
    def _portable(self, target: Path, base: Path) -> str:
        """How a robot file (or scenario) in `base` names `target`: `{cad}/...` or `{code}/...` when a team folder
        holds `target` but not `base` (so the file works on every computer), else relative to `base` ("/"-separated;
        absolute on another drive)."""
        target, base = Path(target).resolve(), Path(base).resolve()
        named = teamfolders.relativize(target)
        if named.startswith("{"):
            root = teamfolders.folders()[named[1:named.index("}")]]
            if not base.is_relative_to(root.resolve()):
                return named
        try:
            return Path(os.path.relpath(target, base)).as_posix()
        except ValueError:   # another drive (Windows)
            return target.as_posix()

    def _program_folder(self, sentence: bool = False) -> tuple[str | None, Path | None, str | None]:
        """(the [robot_program] cwd as written, the folder it names, why it can't be used) for the robot document;
        all None without a [robot_program]. With `sentence`, why is a finished sentence (the robot code panel shows
        it as it is). Called with self._lock held."""
        raw = self.robot.doc.unwrap() if self.robot is not None and self.robot.doc is not None else {}
        rp = raw.get("robot_program")
        if not isinstance(rp, dict):
            return None, None, None
        cwd = rp.get("cwd", ".")
        if not isinstance(cwd, str):
            return None, None, (i18n.t("{robot_program_table} cwd must be a folder path, not {cwd}.", cwd=repr(cwd),
                                       robot_program_table="[robot_program]") if sentence
                                else i18n.t("{robot_program_table} cwd must be a folder path, not {cwd}",
                                            cwd=repr(cwd), robot_program_table="[robot_program]"))
        try:
            folder = (self.robot.path.parent / teamfolders.expand(cwd)).resolve()
        except teamfolders.TeamFolderError as e:
            why = str(e)    # teamfolders' words (the loader's too), not translated: made a sentence here
            return cwd, None, (why[:1].upper() + why[1:] + ("" if why.endswith(".") else ".")) if sentence else why
        if not folder.is_dir():
            if sentence:
                return cwd, folder, i18n.t("The robot code folder {path} isn't there (was it moved?): pick it again.",
                                           path=self._display(folder))
            return cwd, folder, i18n.t("the robot code folder {path} isn't there (was it moved?): pick it again",
                                       path=self._display(folder))
        return cwd, folder, None

    def _named_starts(self, ref: str, base: Path) -> dict | None:
        """The named starts of range `ref` (a built-in name or a path relative to `base`), None when it doesn't
        load. Cached by the file's modification time."""
        try:
            f = BUILTIN_RANGES.get(ref) or base / teamfolders.expand(ref)
            key = (str(f), f.stat().st_mtime_ns)
            if key not in self._ranges:
                rng = load_range(ref, base)
                self._ranges[key] = {k: [float(a) for a in v] for k, v in rng.starts.items()}
            return self._ranges[key]
        except (RangeError, OSError, ValueError):
            return None

    def _start(self) -> dict:
        """Where the robot document's robot starts and on which field. Called with self._lock held."""
        raw = self.robot.doc.unwrap() if self.robot is not None and self.robot.doc is not None else {}
        robot = raw.get("robot") if isinstance(raw.get("robot"), dict) else {}
        sec = raw.get("range") if isinstance(raw.get("range"), dict) else None
        rng = sec.get("file") if sec is not None and isinstance(sec.get("file"), str) else None
        named = sec.get("start") if sec is not None and isinstance(sec.get("start"), str) else None
        starts = (self._named_starts(rng, self.robot.path.parent) or {}) if rng is not None else {}
        written = robot.get("start_pose")
        pose = None
        if named is not None:
            pose = starts.get(named)
        elif isinstance(written, list) and len(written) == 3 and all(_is_num(a) for a in written):
            pose = [float(a) for a in written]
        picked = written is not None or named is not None
        if pose is not None:
            sentence = start_sentence(pose, edits.field_size(raw, self.robot.path.parent)[0])
        else:
            sentence = (i18n.t("Starts at {name}", name=named) if named is not None else i18n.t("Not picked yet") if not picked
                        else i18n.t("Starts as written"))
        floor = sec.get("floor_balls", True) if sec is not None else True
        return {"picked": picked, "pose": pose, "range": rng, "named": named, "named_starts": list(starts),
                "floor_balls": floor if isinstance(floor, bool) else True, "fields": list(edits.FIELDS),
                "range_read_only": rng is not None and rng not in edits.FIELDS, "sentence": sentence}

    def _battery(self) -> dict:
        raw = self.robot.doc.unwrap() if self.robot is not None and self.robot.doc is not None else {}
        b = raw.get("battery") if isinstance(raw.get("battery"), dict) else {}
        return {"nominal_voltage": b.get("nominal_voltage"), "resistance": b.get("resistance"),
                "defaults": dict(BATTERY_DEFAULTS)}

    def _start_problem(self, for_run: bool = False) -> str | None:
        """_unpicked() while the robot document (a [[part]] robot) has neither [robot] start_pose nor [range] start
        (for a run, nor does the scenario's [range] give a start), else None. A hand-written model starts where its
        MJCF puts it. Only the page asks: the CLI and the loader keep their default. Called with self._lock held."""
        if self.robot is None or self.robot.doc is None:
            return None
        raw = self.robot.doc.unwrap()
        if "part" not in raw or self._start()["picked"]:
            return None
        if for_run and self.scenario is not None and self.scenario.doc is not None:
            sec = self.scenario.doc.unwrap().get("range")
            if isinstance(sec, dict) and sec.get("start") is not None:
                return None
        return _unpicked()

    def code_state(self) -> dict:
        with self._lock:
            self._sync()
            cwd, folder, problem = self._program_folder()
            out = {"folder": str(folder) if folder is not None else None,
                   "display_folder": self._display(folder) if folder is not None else None,
                   "cwd": cwd, "problem": problem, "problem_text": self._program_folder(sentence=True)[2], "info": None,
                   "checks": [], "start": self._start(), "battery": self._battery(), "build_failed": False}
        if folder is not None and problem is None:   # outside the lock: the checks run programs
            info = robotcode.detect(folder)
            if info.kind is None:
                out["problem"] = info.summary
                out["problem_text"] = i18n.t("Nothing here scrimmage can start: no build.gradle or robot.py.")
            else:
                out["info"] = _info_json(info)
                failed = self._failed_build(folder)
                out["build_failed"] = failed is not None
                out["checks"] = [failed if c.id == "gradle_built" and failed is not None else dataclasses.asdict(c)
                                 for c in robotcode.checklist(info)]
        return out

    def _failed_build(self, folder: Path) -> dict | None:
        """The failed gradle_built check of this folder's last Build now, while nothing has built there since (a
        build in VS Code counts), else None."""
        with self._fix_cond:
            got = self._build_failed.get(str(folder))
        if got is None:
            return None
        built = robotcode.built_at(folder)
        if built is not None and built >= got[0]:
            return None
        return dataclasses.asdict(got[2]()) if got[2] is not None else got[1]   # in the Language now

    def _not_built(self) -> str | None:
        """Why the robot document's Java code can't start yet (the page's starts only): it has never built on this
        computer, its last Build now failed, or a build is running. None for RobotPy, a hand-written program or a
        folder that can't be used (the start says why). Called with self._lock held."""
        _, folder, problem = self._program_folder()
        if folder is None or problem is not None or not robotcode.is_java(folder):
            return None
        with self._fix_cond:
            building = self._fix["running"] and self._fix["fix"] == "gradle_build"
        if building:
            return _not_built("running")
        if self._failed_build(folder) is not None:
            return _not_built("failed")
        if robotcode.built_at(folder) is None:
            return _not_built("never")
        return None

    def code_set(self, body: dict) -> tuple[int, dict]:
        """Point the robot at its code: detect the project in `folder` and write [robot_program] (one undo step). A
        refusal's first error carries problem_text: the refusal as a finished sentence, which the page shows as it
        is."""
        try:
            status, out = self._code_set(body)
        except ApiError as e:
            e.detail = {**getattr(e, "detail", {}), "problem_text": _sentence(e.message)}
            raise
        errors = out.get("errors") or []
        if not out.get("ok") and errors:
            errors[0] = {**errors[0], "problem_text": _sentence("; ".join(e["message"] for e in errors))}
        return status, out

    def _code_set(self, body: dict) -> tuple[int, dict]:
        p = self._user_path(body.get("folder"), "a folder path")
        if not p.is_dir():
            raise ApiError(400, i18n.t("{path} is not a folder", path=self._display(p)))
        info = robotcode.detect(p)
        if info.kind is None:
            raise ApiError(400, info.summary)
        with self._lock:
            if "version" in body:
                self._check_fresh(body, lambda: self.robot_version)
            else:
                self._sync()
            self._require_robot_writable()
            table = {**robotcode.program_table(info), "cwd": self._portable(info.folder, self.robot.path.parent)}
            status, out = self._commit(lambda doc: edits.set_program(doc, table))
        if out.get("ok"):
            out["info"] = _info_json(info)
        return status, out

    def _fix_json(self) -> dict:
        """The fix's state; a failed one's result in the Language now (its lines are the program's own). Fix lock
        held."""
        out = {**self._fix, "lines": list(self._fix["lines"])}
        if self._fix_again is not None and not out["running"]:
            out["result"] = dataclasses.asdict(self._fix_again())
        return out

    def fix_state(self) -> dict:
        with self._fix_cond:
            return self._fix_json()

    def wait_fix(self, since: int | None) -> dict:
        end = time.monotonic() + POLL_SECONDS
        with self._fix_cond:
            while since is not None and self._fix["version"] == since and time.monotonic() < end:
                self._fix_cond.wait(1.0)
            return self._fix_json()

    def code_fix(self, body: dict) -> dict:
        """Run one readiness fix (robotcode.run_fix) on the fix worker: one at a time."""
        fix = body.get("fix")
        if fix not in FIXES:
            raise ApiError(400, i18n.t("fix must be one of {choices}", choices=', '.join(FIXES)))
        with self._lock:
            _, folder, problem = self._program_folder()
        if folder is None or problem is not None:
            raise ApiError(400, problem or i18n.t("pick the robot code's folder first"))
        info = robotcode.detect(folder)
        if info.kind is None:
            raise ApiError(400, info.summary)
        with self._fix_cond:
            if self._fix["running"]:
                raise ApiError(409, i18n.t("a fix is already running; wait for it to finish"))
            cancel = self._fix_cancel = threading.Event()
            f = self._fix
            f.update(id=f["id"] + 1, fix=fix, running=True, lines=[], result=None, version=f["version"] + 1)
            self._fix_again = None
            fix_id = f["id"]
            self._fix_cond.notify_all()

            def out(line: str) -> None:
                with self._fix_cond:
                    if self._fix["id"] == fix_id:
                        self._fix["lines"].append(line)
                        del self._fix["lines"][:-FIX_LINES]
                        self._fix["version"] += 1
                        self._fix_cond.notify_all()

            def work() -> None:
                try:
                    result = robotcode.run_fix(fix, info, out, cancel=cancel)
                except Exception as e:   # a bug-shaped failure still ends the fix with a message
                    logging.getLogger("scrimmage.assembler").exception("fix %s failed", fix)
                    result = robotcode.Check(fix, False, f"This didn't work: {type(e).__name__}: {e}", fix=fix)
                with self._fix_cond:
                    if fix == "gradle_build":
                        if result.ok:
                            self._build_failed.pop(str(info.folder), None)
                        else:
                            self._build_failed[str(info.folder)] = (time.time(), dataclasses.asdict(result),
                                                                    getattr(result, "again", None))
                    self._fix_again = getattr(result, "again", None)
                    self._fix.update(running=False, result=dataclasses.asdict(result),
                                     version=self._fix["version"] + 1)
                    self._fix_cond.notify_all()
            self._fix_thread = threading.Thread(target=work, daemon=True, name="robot-code-fix")
            self._fix_thread.start()
        return {"ok": True, "id": fix_id}
