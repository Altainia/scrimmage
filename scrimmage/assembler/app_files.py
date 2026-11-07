"""The App's files and settings (FilesMixin): the start page's state, the Units, Language and Theme settings,
the team folders, Browse, and opening or making a robot config, scenario, measurement file, session or CAD
import (a dropped CAD file too).
A mixin of server.py's App: it has no __init__ (App.__init__ sets every attribute it uses) and never imports server.py.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import i18n, settings, teamfolders, units
from ..blocks.program import TestFileError
from ..cad import CadError
from ..cad.names import sanitize
from ..measure.use import SUFFIX
from . import bench_session, measure_session, part_session, recent, templates
from .bench_session import SessionError
from .server_common import ApiError, unset_folder, classify, remember_opened


ROBOT_TEMPLATES = ("swerve", "swerve-rev", "differential")


class FilesMixin:
    """The start page and its dialogs: what is open, the settings and team folders, Browse, Open and New."""

    def app_state(self) -> dict:
        with self._lock:
            mode = self.mode
            p = self.bench.folder if mode == "bench" else self.part.path if mode == "part" \
                else self.measure.path if mode == "measure" \
                else (self.test or self.scenario or self.robot).path if mode == "robot" else None
            unset = self._needs_folder()
            back = self.back if mode == "part" else None
            test = str(self.test.path) if self.test is not None else None
        return {"mode": mode, "test": test, "path": str(p) if p is not None else None,
                "display_path": self._display(p) if p is not None else "", "recent": recent.recent(),
                "back": {"path": str(back), "display_path": self._display(back)} if back is not None else None,
                "examples": bench_session.examples(), "needs_folder": unset[0] if unset else None,
                "measure_kinds": measure_session.kinds()}

    def _needs_folder(self) -> tuple[str, str] | None:
        """The team folder the open robot file (or the scenario's `config`) names that this computer hasn't set."""
        for d in (self.robot, self.scenario):
            if d is not None and d.doc is not None:
                unset = unset_folder(d.doc.unwrap())
                if unset is not None:
                    return unset
        return None

    def settings_state(self, have: str | None = None) -> dict:
        """`have`: the catalog_version the page already has; the catalog is left out while it is still that one."""
        language = settings.current_language()
        version = i18n.catalog_version(language)
        out = {"units": settings.current_units(), "table": units.table_json(), "language": language,
               "languages": i18n.languages(), "catalog_version": version, "theme": settings.load().theme,
               "warning": settings.problem_words()}
        if have != version:
            out["catalog"] = i18n.catalog_json(language)
        return out

    def set_settings(self, body: dict) -> dict:
        """Save the Units, Language or Theme setting (or several). Under `scrimmage --units` or `--language` the menu still
        switches: this run follows it too."""
        changes = {}
        try:
            if "units" in body:
                changes["units"] = settings.check_units(body["units"])
            if "language" in body:
                changes["language"] = settings.check_language(body["language"])
            if "theme" in body:
                changes["theme"] = settings.check_theme(body["theme"])
            if not changes:
                raise ValueError("send units, language or theme to change")
            settings.save(**changes)
        except ValueError as e:
            raise ApiError(400, str(e)) from None
        except OSError as e:
            raise ApiError(500, i18n.t("can't save {path}: {why}", path=settings.settings_path(),
                                       why=e.strerror or e)) from None
        if "units" in changes and settings.overridden():
            settings.set_override(changes["units"])
        if "language" in changes and settings.language_overridden():
            settings.set_language_override(changes["language"])
        return self.settings_state()

    def get_folders(self) -> dict:
        known = teamfolders.folders()
        return {**{k: str(v) if v is not None else None for k, v in known.items()},
                "from_env": {k: bool(os.environ.get(f"SCRIMMAGE_{k.upper()}")) for k in teamfolders.FOLDERS}}

    def set_folder(self, body: dict) -> dict:
        """Remember where a team folder is on this computer; what is open is read again with it."""
        name, path = body.get("name"), body.get("path")
        if name not in teamfolders.FOLDERS:
            raise ApiError(400, i18n.t("name must be one of {choices}", choices=', '.join(teamfolders.FOLDERS)))
        p = None
        if path is not None:
            p = self._user_path(path, "a folder path")
            if not p.is_dir():
                raise ApiError(400, i18n.t("{path} is not a folder", path=self._display(p)))
        try:
            teamfolders.set_folder(name, p)
        except OSError as e:
            raise ApiError(500, i18n.t("can't save the team folders: {why}", why=e.strerror or e)) from None
        with self._lock:
            if self.mode == "robot":
                if self.robot is not None and self.robot.doc is not None:
                    self.robot.read_only_reason = None   # recomputed below: it may have been the unset folder
                if not (self.scenario is not None and self._point_robot()):
                    if self.robot is not None:
                        self.robot.version += 1
                    else:
                        self._robot_ver += 1
                self._revalidate()
                self._revalidate_scenario(bump=True)
        return {"ok": True}

    def browse(self, folder) -> dict:
        d = self._user_path(folder, "a folder path") if folder else self.cwd
        if not d.is_dir():
            raise ApiError(400, i18n.t("{path} is not a folder", path=self._display(d)))
        try:
            items = sorted(d.iterdir(), key=lambda p: p.name.lower())
        except OSError as e:
            raise ApiError(400, i18n.t("can't read {path}: {why}", path=self._display(d), why=e.strerror or e)) from None
        folders, files = [], []
        for p in items:
            if p.name.startswith("."):
                continue
            if p.is_dir():
                folders.append({"name": p.name, "path": str(p), "kind": "folder"})
            elif p.is_file() and p.suffix.lower() == ".toml":
                files.append({"name": p.name, "path": str(p), "kind": classify(p)})
            elif p.is_file() and p.suffix.lower() in part_session.CAD_SUFFIXES:
                files.append({"name": p.name, "path": str(p), "kind": "cad"})
        return {"dir": str(d), "parent": str(d.parent) if d.parent != d else None, "entries": folders + files}

    def open_path(self, path, kind: str | None = None, parts_dir: Path | None = None) -> dict:
        """Open a robot config, scenario, bench file (its folder as the session, that file on stage), folder, import
        file, CAD file or part folder (see _load for kind and parts_dir)."""
        p = self._user_path(path, "a file or folder path")
        if not p.exists():
            raise ApiError(400, i18n.t("{path} doesn't exist", path=self._display(p)))
        if p.is_file() and p.suffix.lower() not in (".toml", *part_session.CAD_SUFFIXES):
            raise ApiError(400, i18n.t("{path} is not a robot config, scenario, bench file, CAD file or folder",
                                       path=self._display(p)))
        with self._lock:
            self._check_can_open()
            try:
                with self.bench_jobs.gate():   # no job can queue on the old session while it is replaced
                    self._load(p, kind=kind, parts_dir=parts_dir)
            except OSError as e:
                raise ApiError(400, i18n.t("can't read {path}: {why}", path=self._display(p), why=e.strerror or e)) from None
            except (CadError, TestFileError) as e:
                raise ApiError(400, str(e)) from None
        self.bench_jobs.stop()    # idle (refused above while a job is active); outside the lock: it joins
        self.bench_jobs.clear()
        remember_opened(p)
        self.remember_part()
        return {"ok": True}

    def new_import(self, body: dict) -> dict:
        """New part from CAD: open a Browse-picked source in place (its import file in parts/ next to it, or next
        to the robot config `robot`)."""
        p = self._user_path(body.get("path"), "the CAD file to import")
        if not (p.is_file() and p.suffix.lower() in part_session.CAD_SUFFIXES) and not (
                p.is_dir() and any(f.suffix.lower() in part_session.CAD_SUFFIXES for f in p.iterdir())):
            raise ApiError(400, i18n.t("{path} is not a STEP, STL or OBJ file (or a folder of STL/OBJ files)",
                                       path=self._display(p)))
        parts_dir = None
        if body.get("robot") is not None:
            robot = self._user_path(body.get("robot"), "the robot config the part is for")
            if not robot.is_file() or classify(robot) != "robot":
                raise ApiError(400, i18n.t("{path} is not a robot config", path=self._display(robot)))
            parts_dir = robot.parent / "parts"
        return self.open_path(str(p), kind="import", parts_dir=parts_dir)

    def upload(self, stream, length: int, filename: str | None, files: list | None = None,
               again: bool = False) -> tuple[int, dict]:
        """A dropped CAD file: into the open import's parts/<name>.source/ (re-imported from there), or, with no
        part open, a new import in parts/ (next to the robot config in robot mode, else the working directory).
        `files` ([(name, size)], more than one) is several STL or OBJ files sent one after another: they go in
        .source/meshes/ and are read together, as a folder of them. `again` is Import again on a copied source:
        the settings are kept as for an ordinary re-import."""
        if files is not None and len(files) == 1:
            filename, files = files[0][0], None

        def save(folder: Path) -> Path:
            if files is None:
                return part_session.save_upload(stream, length, filename, folder)
            return part_session.save_uploads(stream, length, files, folder)
        with self._lock:
            part, base = self.part, (self._base_dir() if self.mode == "robot" else self.cwd)
        if part is not None:
            return part.replace_source(save(part.source_folder()), again)
        with self._lock:
            self._check_can_open()
        if files is None:
            name = sanitize(Path(part_session.upload_name(filename)).stem)
        else:
            names = [part_session.upload_name(n) for n, _ in files]
            part_session.upload_set(names)
            name = part_session.upload_stem(names)
        imp = part_session.free_import(base / "parts", name)
        dest = save(imp.with_name(imp.name[:-len(part_session.SUFFIX)] + ".source"))
        with self._lock:
            self._check_can_open()
            with self.bench_jobs.gate():
                self._load(imp, kind="import", source=dest)
        self.bench_jobs.stop()
        self.bench_jobs.clear()
        self.remember_part()
        return 200, self.part.state()

    def new(self, body: dict) -> dict:
        """Write a new robot config (from a template), scenario (for a robot config), measurement file (a kind's
        starter) or session folder, then open it. A path that exists is refused; a robot or scenario path without
        .toml gets it, and a measurement file's name without .measure.toml gets that."""
        kind = body.get("kind")
        if kind == "import":
            return self.new_import(body)
        if kind not in ("robot", "scenario", "session", "measure"):
            raise ApiError(400, i18n.t("kind must be robot, scenario, session, measure or import"))
        p = self._user_path(body.get("path"), "a file or folder path")
        if kind == "measure" and not p.name.lower().endswith(SUFFIX):
            p = p.with_name(p.name.removesuffix(".toml") + SUFFIX)
        elif kind not in ("session", "measure") and p.suffix.lower() != ".toml":
            p = p.with_name(p.name + ".toml")
        if p.exists():
            raise ApiError(400, i18n.t("{path} already exists; choose another name", path=self._display(p)))
        self._check_can_open()
        text = None
        if kind == "robot":
            if body.get("template") not in ROBOT_TEMPLATES:
                raise ApiError(400, i18n.t("template must be one of {choices}", choices=', '.join(ROBOT_TEMPLATES)))
            text = templates.new_config(body["template"])
        elif kind == "scenario":
            robot = self._user_path(body.get("robot"), "the robot config the scenario runs")
            if not robot.is_file() or classify(robot) != "robot":
                raise ApiError(400, i18n.t("{path} is not a robot config", path=self._display(robot)))
            text = templates.new_scenario(self._portable(robot, p.parent))
        elif kind == "measure":
            try:
                text = measure_session.new_text(body.get("measure"))
            except SessionError as e:
                raise ApiError(400, e.message) from None
        try:
            if text is None:
                p.mkdir(parents=True)
            else:
                with open(p, "x", encoding="utf-8") as f:
                    f.write(text)
        except FileExistsError:
            raise ApiError(400, i18n.t("{path} already exists; choose another name", path=self._display(p))) from None
        except OSError as e:
            raise ApiError(400, i18n.t("can't write {path}: {why}", path=self._display(p), why=e.strerror or e)) from None
        return self.open_path(str(p))
