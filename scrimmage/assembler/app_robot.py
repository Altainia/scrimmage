"""The App's Robot tab (RobotMixin): the robot document's state (its parts, CAD placement, mass and center of
gravity), its 3D view and part library, and its edits, undo and redo.
A mixin of server.py's App: it has no __init__ (App.__init__ sets every attribute it uses) and never imports server.py.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import mujoco
import tomlkit

from .. import i18n, teamfolders
from ..parts.assemble import PLACEMENT_KEYS
from . import edits, library, validate
from .doc import FileChanged
from .edits import EditError
from .live import LiveError, UnsavedRecording
from .server_common import (_FOLDER_PLACEHOLDER, ApiError, _plain, _nothing_to, _cant, _chassis_root_body,
                            _stock_defaults, _fe, _hand_written)
from .view import model_view


class RobotMixin:
    """The Robot tab: the robot document's state, view, library, edits and history."""

    # -- robot ------------------------------------------------------------------------------
    def _robot_words(self) -> None:
        """When the Language setting changed since the robot was validated: its errors, warnings and read-only
        reason again, in its words (the file is the same), and the reason no robot is attached (scenario mode:
        _reword_missing). Lock held."""
        if self._robot_lang == i18n.current_language() or (self.robot is None and self.robot_missing is None):
            return
        self._reword_missing()
        d = self.robot
        if d is not None:
            if d.doc is not None:
                d.read_only_reason = None   # only _revalidate words it while the file parses
            else:
                d.reword()
        self._revalidate()

    def state(self) -> dict:
        self._robot_words()
        d = self.robot
        parts = []
        other = ""
        if d is not None and d.doc is not None:
            for p in edits.parts(d.doc):
                parts.append({"name": _plain(p.get("name")), "stock": _plain(p.get("stock")),
                              "path": _plain(p.get("path")), "attach": _plain(p.get("attach")),
                              "pos": _plain(p.get("pos", [0.0, 0.0, 0.0])), "rot": _plain(p.get("rot", [0.0, 0.0, 0.0])),
                              "params": {k: _plain(v) for k, v in p.items() if k not in PLACEMENT_KEYS},
                              "ids": _plain(p.get("ids", {})),
                              "cad_pos": self._cad_pos(d.doc, _plain(p.get("path")), _plain(p.get("attach"))),
                              "cad": self._cad_info(d.doc, _plain(p.get("path")))})
            rest = tomlkit.parse(d.text)
            rest.pop("part", None)
            other = tomlkit.dumps(rest)
        elif d is not None:
            other = d.text
        reason = d.read_only_reason if d is not None else self.robot_missing
        out = {"version": self.robot_version, "path": str(d.path) if d is not None else None,
               "display_path": self._display(d.path) if d is not None else "",
               "read_only": reason is not None, "read_only_reason": reason, "parts": parts,
               "errors": _fe(self.result.errors), "warnings": _fe(self.result.warnings),
               "other_sections": other, "can_undo": d is not None and d.can_undo,
               "can_redo": d is not None and d.can_redo,
               "scenario_open": self.scenario is not None,
               "mass_kg": None, "bodies": None, "joints": None, "part_bodies": None, "com": None,
               "range": self._range_file(), "start": self._range_start()}
        w = self.result.world if self.result.ok else None
        if w is not None and w.parts is not None:
            m = w.model
            root = int(m.body_rootid[w.robot_body])
            out["mass_kg"] = float(m.body_subtreemass[w.robot_body])
            out["part_bodies"] = {i.name: [b.name for b in i.part.bodies] for i in w.parts.instances}
            out["bodies"] = sum(1 for b in range(1, m.nbody) if int(m.body_rootid[b]) == root)
            out["joints"] = sum(1 for j in range(m.njnt) if int(m.body_rootid[m.jnt_bodyid[j]]) == root
                                and int(m.jnt_type[j]) != int(mujoco.mjtJoint.mjJNT_FREE))
            out["com"] = self._robot_com(w)
        return out

    def _range_file(self) -> str | None:
        """The robot document's [range] file as written (a built-in range's name or a path); None: the plain field."""
        raw = self.robot.doc.unwrap() if self.robot is not None and self.robot.doc is not None else {}
        sec = raw.get("range") if isinstance(raw.get("range"), dict) else None
        return sec.get("file") if sec is not None and isinstance(sec.get("file"), str) else None

    def _range_start(self) -> str | None:
        """The robot document's [range] start (a named start of its field) as written; None when it names none."""
        raw = self.robot.doc.unwrap() if self.robot is not None and self.robot.doc is not None else {}
        sec = raw.get("range") if isinstance(raw.get("range"), dict) else None
        return sec.get("start") if sec is not None and isinstance(sec.get("start"), str) else None

    def _robot_com(self, w) -> dict:
        """The robot's center of gravity at its start pose: subtree_com of the robot body in that body's frame (m;
        for a chassis root, its origin is on the floor at the frame center). Kept until the world changes."""
        if self._com is None or self._com[0] is not w:
            m = w.model
            d = mujoco.MjData(m)
            mujoco.mj_kinematics(m, d)
            mujoco.mj_comPos(m, d)
            b = w.robot_body
            x, y, z = d.xmat[b].reshape(3, 3).T @ (d.subtree_com[b] - d.xpos[b])
            self._com = (w, {"height": float(z), "forward": float(x), "left": float(y)})
        return self._com[1]

    def _part_folder(self, path) -> Path | None:
        """The folder a [[part]] `path` names (relative to the robot config, or {cad}/...), or None."""
        if not isinstance(path, str) or not path.strip():
            return None
        try:
            p = Path(teamfolders.expand(path)).expanduser()
        except teamfolders.TeamFolderError:
            return None
        return p if p.is_absolute() else self._base_dir() / p

    def _part_files(self, path) -> list[Path]:
        """The files a [[part]] `path` reads: its part.toml, the plugin overlay beside it and the meshes it names (not
        the folder around them, which may be the robot's own, with its tests and their runs)."""
        folder = self._part_folder(path)
        if folder is None:
            return []
        toml = folder if folder.suffix == ".toml" else folder / "part.toml"
        files = [toml, toml.parent / "scrimmage-edits.toml"]
        try:
            st = toml.stat()
        except OSError:
            return files
        key = (str(toml), st.st_mtime_ns, st.st_size, st.st_ino)
        meshes = self._part_meshes.get(key)
        if meshes is None:
            try:
                raw = tomllib.loads(toml.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
                raw = {}
            bodies = raw.get("body") if isinstance(raw.get("body"), list) else []
            meshes = sorted({g["mesh"] for b in bodies if isinstance(b, dict)
                             for g in (b.get("geom") if isinstance(b.get("geom"), list) else [])
                             if isinstance(g, dict) and isinstance(g.get("mesh"), str)})
            if len(self._part_meshes) > 64:
                self._part_meshes.clear()
            self._part_meshes[key] = meshes
        return files + [toml.parent / m for m in meshes]

    def _parts_look(self) -> tuple | None:
        """Each file the robot document's parts read, with its time, size and file id, to see Import again."""
        d = self.robot
        if d is None or d.doc is None:
            return None
        out = []
        for p in edits.parts(d.doc):
            for f in self._part_files(p.get("path")):
                try:
                    st = f.stat()
                    out.append((str(f), st.st_mtime_ns, st.st_size, st.st_ino))
                except OSError:
                    out.append((str(f), None))      # not there (an overlay a part doesn't have), or being replaced
        return tuple(out)

    def _cad_pos(self, doc, path, attach) -> list[float] | None:
        """Where the part folder `path` attached to `attach` sits where the CAD has it (edits.cad_offset), or None:
        not from a CAD import, a target that isn't a body (a mount: known while the robot loads), or one with no CAD
        placement."""
        folder = self._part_folder(path)
        marker = library.cad_marker(folder) if folder is not None else None
        if marker is None or not isinstance(attach, str) or "." not in attach:
            return None
        inst, body = attach.split(".", 1)
        target = next((p for p in edits.parts(doc) if p.get("name") == inst), None)
        if target is None:
            return None
        w = self._robot_world()
        if w is not None and w.parts is not None and any(
                i.name == inst and any(mt.name == body for mt in i.part.mounts) for i in w.parts.instances):
            return None     # a mount wins over a body of the same name
        tfolder = self._part_folder(_plain(target.get("path")))
        return edits.cad_offset(marker, body, library.cad_marker(tfolder) if tfolder is not None else None,
                                chassis_root=_plain(target.get("stock")) == "chassis" and body == _chassis_root_body())

    def _cad_info(self, doc, path) -> dict | None:
        """{import_id, part, hangs_on} for a part folder from a CAD import, else None. hangs_on: what the CAD hangs it
        on, as an attach target: "<instance>.<body>" when a part from the same import with that name is in the robot,
        else the marker's "<part>.<body>"; None when the robot carries it."""
        folder = self._part_folder(path)
        marker = library.cad_marker(folder) if folder is not None else None
        if marker is None:
            return None
        hangs_on = marker["attach"]
        if hangs_on is not None:
            part, body = hangs_on.split(".")
            for p in edits.parts(doc):
                f = self._part_folder(_plain(p.get("path")))
                m = library.cad_marker(f) if f is not None else None
                if m is not None and m["import_id"] == marker["import_id"] and m["part"] == part:
                    hangs_on = f"{_plain(p.get('name'))}.{body}"
                    break
        return {"import_id": marker["import_id"], "part": marker["part"], "hangs_on": hangs_on}

    def wait_state(self, since: int | None) -> dict:
        return self._wait(since, lambda: self.robot_version, self.state)

    def view(self) -> dict:
        with self._lock:
            w = self._robot_world()
            if self.robot is None or self.robot.read_only_reason is not None or w is None or w.parts is None:
                return {}
            if self._view is None:
                self._view = model_view(w)
            return self._view

    def _base_dir(self) -> Path:
        return (self.robot or self.scenario or self.test).path.parent

    def library(self) -> dict:
        with self._lock:
            base = self._base_dir()
            dirs = [base, base / "parts", *self.extra_folders]   # parts/ is where the FreeCAD exporter puts them
            return {"stock": library.stock(), "folders": library.folders(dirs, base)}

    def add_folder(self, path) -> dict:
        if not isinstance(path, str) or not path.strip():
            raise ApiError(400, i18n.t("path must be a folder path"))
        p = Path(path).expanduser()
        with self._lock:
            if not p.is_absolute():
                p = self._base_dir() / p
            p = p.resolve()
            if not p.is_dir():
                raise ApiError(400, i18n.t("{path} is not a folder", path=p))
            if p not in self.extra_folders:
                self.extra_folders.append(p)
        return {"ok": True}

    def _require_robot_writable(self) -> None:
        self._robot_words()
        if self.robot is None:
            raise ApiError(409, self.robot_missing)
        if self.robot.read_only_reason is not None:
            raise ApiError(409, self.robot.read_only_reason)

    def _stock_of(self, name) -> str | None:
        for p in edits.parts(self.robot.doc):
            if p.get("name") == name:
                return _plain(p.get("stock"))
        return None

    def _apply(self, op: str, b: dict):
        name = b.get("name")
        if op == "add":
            path = b.get("path")
            pos = b.get("pos")
            if pos is None:     # a part from a CAD import goes where the CAD has it
                pos = self._cad_pos(self.robot.doc, path, b.get("attach"))
            if isinstance(path, str) and path.strip() and not _FOLDER_PLACEHOLDER.search(path):
                portable = self._portable((self._base_dir() / Path(path).expanduser()).resolve(), self._base_dir())
                path = portable if portable.startswith("{") else path
            return lambda doc: edits.add_part(doc, name=name, stock=b.get("stock"), path=path,
                                              attach=b.get("attach"), pos=pos, rot=b.get("rot"),
                                              params=b.get("params"), ids=b.get("ids"),
                                              defaults=_stock_defaults(b.get("stock")))
        if op == "remove":
            return lambda doc: edits.remove_part(doc, name)
        if op == "rename":
            return lambda doc: edits.rename_part(doc, name, b.get("new_name"))
        if op == "attach":
            return lambda doc: edits.set_attach(doc, name, b.get("attach"), b.get("pos"), b.get("rot"))
        if op == "params":
            defaults = _stock_defaults(self._stock_of(name))
            return lambda doc: edits.set_params(doc, name, b.get("params") or {}, defaults)
        if op == "ids":
            return lambda doc: edits.set_ids(doc, name, b.get("ids") or {})
        if op == "start":
            keys = ("range", "pose", "named", "keep_pose", "floor_balls")
            return lambda doc: edits.set_start(doc, {k: b[k] for k in keys if k in b}, self.robot.path.parent)
        if op == "battery":
            return lambda doc: edits.set_battery(doc, {k: b[k] for k in ("nominal_voltage", "resistance") if k in b})
        if op == "match_devices":
            return lambda doc: edits.match_devices(doc, b.get("matches"))
        if op == "motor":
            world = self.result.world if self.result is not None and self.result.ok else None
            motor_slots = {m.name for m in world.motors} if world is not None else None
            return lambda doc: edits.set_motor(doc, b.get("slot"), motor_slots=motor_slots,
                                               **{k: b[k] for k in ("neutral", "invert") if k in b})
        raise ApiError(400, i18n.t("unknown op {op} (add, remove, rename, attach, params, ids, start, battery, match_devices, motor)",
                                   op=repr(op)))

    def edit(self, body: dict) -> tuple[int, dict]:
        with self._lock:
            self._check_fresh(body, lambda: self.robot_version)
            self._require_robot_writable()
            status, out = self._commit(self._apply(body.get("op"), body))
            if body.get("op") == "match_devices" and out.get("ok"):
                out["restarting"], problem = self._restart_live()
                if problem is not None:
                    out["restart_problem"] = problem
            return status, out

    def _restart_live(self) -> tuple[bool, str | None]:
        """Ask the live session on the robot document to restart after its IDs changed: (restarting, why not, when
        it couldn't: an unsaved recording). The restart runs on its own thread; /api/robot/devices' restart says
        how it went. Called with self._lock held."""
        st = self.live.status()
        if st["state"] not in ("starting", "running", "paused") or st["config"] is None \
                or Path(st["config"]) != self.robot.path.resolve():
            return False, None
        try:
            self.live.restart()
        except UnsavedRecording as e:
            return False, f"the running robot wasn't restarted with the new IDs: {e}"
        except LiveError:
            return False, None    # it ended meanwhile
        return True, None

    def _commit(self, fn) -> tuple[int, dict]:
        """Apply the edit `fn` to the robot document and write it when the result validates (one undo step).
        Called with self._lock held, the document writable."""
        try:
            candidate = self.robot.apply(fn)
        except EditError as e:
            return 200, {"ok": False, "errors": [{"field": e.field, "message": e.message}]}
        except (TypeError, ValueError, AttributeError) as e:   # malformed JSON values from a client
            return 200, {"ok": False, "errors": [{"field": "status", "message": f"bad edit: {e}"}]}
        res = validate.check(candidate, self.robot.path)
        if not res.ok:
            return 200, {"ok": False, "errors": _fe(res.errors)}
        if res.world.parts is None:
            return 200, {"ok": False, "errors": [{"field": "status", "message": _hand_written()}]}
        try:
            self.robot.commit(candidate)
        except FileChanged as e:
            self._sync()
            raise ApiError(409, e.message) from None
        except EditError as e:
            return 200, {"ok": False, "errors": [{"field": e.field, "message": e.message}]}
        except OSError as e:
            return 200, {"ok": False, "errors": [{"field": "status", "message": i18n.t("can't write the file: {why}",
                                                                                       why=e)}]}
        self._view = self._scenario_view = self._signals = None
        self.result = res
        self._robot_lang = i18n.current_language()
        self._parts_seen = self._parts_look()
        if self.test is not None:      # its palette and problems: an edit can change the field or the parts
            self.test.refresh(self._robot_world(), self._robot_problem())
        self._revalidate_scenario(bump=True)
        self._changed.notify_all()
        return 200, {"ok": True, "version": self.robot.version}

    def history(self, which: str, body: dict) -> tuple[int, dict]:
        with self._lock:
            self._check_fresh(body, lambda: self.robot_version)
            self._require_robot_writable()
            target = self.robot.peek_undo() if which == "undo" else self.robot.peek_redo()
            if target is None:
                return 200, {"ok": False, "errors": [{"field": "status", "message": _nothing_to(which)}]}
            res = validate.check(target, self.robot.path)   # the world may have changed (a deleted part folder)
            if not res.ok or res.world.parts is None:
                errs = _fe(res.errors) if not res.ok else [{"field": "status", "message": _hand_written()}]
                return 200, {"ok": False, "errors": [{"field": e["field"], "message": _cant(which, e["message"])}
                                                      for e in errs]}
            try:
                self.robot.undo() if which == "undo" else self.robot.redo()
            except FileChanged as e:
                self._sync()
                raise ApiError(409, e.message) from None
            except OSError as e:
                raise ApiError(500, i18n.t("can't write the file: {why}", why=e)) from None
            self._revalidate()
            self._revalidate_scenario(bump=True)
            return 200, {"ok": True, "version": self.robot.version}
