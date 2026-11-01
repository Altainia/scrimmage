"""The Part tab's session: one CAD import (`<name>.import.toml`), or one plugin-made part folder, kept in step with
the files on disk.

Import mode (`PartSession`). The import file holds the student's choices (scrimmage/cad/settings.py); the CAD source
it names (`{cad}/...` expanded, else relative to the import file's folder) is read once into memory and again only
on Import again or a dropped file. Every edit is saved at once and goes on a 50-step undo history. One build worker
thread per session ("part-build") then runs `build_parts` and `write_parts` on a copy of the latest settings; edits
made while it builds win (a build made stale before it is written is thrown away and the newest settings built).
`write_parts` records the folders it wrote in the copy's `written`; the worker puts that record on the session's
latest settings (never a stale copy: an edit's undo keeps the newest record too) and saves the import file under the
session lock, noting the file's signature so the save isn't taken for an outside edit.

A poll thread ("part-poll") every 0.25 s picks up outside edits of the import file (they go on the undo history
and rebuild); a file that stops reading as an import file makes the session read-only, saying why, until it reads
again. A source that can't be read (the `cad` extra missing, the file moved) makes it read-only too, with the
CadError's plain message, until Import again or a dropped file reads one.

Plugin mode (`plugin_session.PluginSession`, on the same `_Session` base): a part folder without an
`import-origin.toml` marker (the FreeCAD add-on's).

Every change bumps one `version`, which the page long-polls. An edit is refused (409) when the settings changed
after the version the page sent (`_changed_at`): a build finishing doesn't make the page's version stale.
Lock order: the App's lock, then a session's; a session never takes the App's lock.
"""

from __future__ import annotations

import base64
import copy
import dataclasses
import logging
import os
import threading
import time
import tomllib
import uuid
from pathlib import Path
from urllib.parse import unquote

import numpy as np

from .. import teamfolders
from ..cad import CadError
from ..cad.axis import fit_axis
from ..cad.bodies import assembly_diagonal, is_hardware
from ..cad.build import MARKER, BuildResult, _resolve, build_parts, write_parts
from ..cad.checklist import checklist
from ..cad.edits import EditError, apply_edit
from ..cad.frame import to_robot
from ..cad.mass import MATERIALS, density, mesh_inertial
from ..cad.matching import match
from ..cad.names import sanitize
from ..cad.settings import new_import, read_import, reimport, write_import
from ..cad.source import MESH_SUFFIXES, STEP_SUFFIXES, load_source
from ..motors import MOTORS
from .bench_session import SessionError

POLL_PERIOD = 0.25
POLL_SECONDS = 25.0
HISTORY = 50
CAD_SUFFIXES = STEP_SUFFIXES + MESH_SUFFIXES
MAX_UPLOAD = 512 * 1024 * 1024
NEED_STEP = "per-piece materials need the STEP: import it instead"
PIECE_OPS = ("set_material", "move_pieces", "new_body", "leave_out")
SUFFIX = ".import.toml"
STALE = "the import changed (another tab, or an outside edit); the page has reloaded it"
log = logging.getLogger("scrimmage.assembler")


def materials() -> dict:
    """GET /api/part/materials: the materials (key -> label, density, printed) and the motor names."""
    return {"materials": {k: dataclasses.asdict(m) for k, m in MATERIALS.items()}, "motors": sorted(MOTORS)}


def _sig(*paths: Path):
    out = []
    for p in paths:
        try:
            st = p.stat()
            out.append((st.st_mtime_ns, st.st_size, st.st_ino))
        except OSError:
            out.append(None)
    return tuple(out)


def _floats(x) -> list[float]:
    return [float(v) for v in x]


def _mesh(v, f) -> dict:
    vert = np.ascontiguousarray(v, dtype="<f4")
    face = np.ascontiguousarray(f, dtype="<i4")
    return {"vert": base64.b64encode(vert.tobytes()).decode(), "face": base64.b64encode(face.tobytes()).decode()}


def _refused(e: EditError) -> tuple[int, dict]:
    return 400, {"ok": False, "field": e.field, "message": e.message, "error": e.message,
                 "errors": [{"field": e.field, "message": e.message}]}


# ---------------------------------------------------------------- where things go

def source_text(source: Path, import_path: Path) -> str:
    """A source path as the import file stores it: `{cad}/...` under a team folder, else relative to the import
    file's folder ("/"-separated), else absolute (another drive)."""
    p = Path(source).resolve()
    rel = teamfolders.relativize(p)
    if rel.startswith("{"):
        return rel
    try:
        return Path(os.path.relpath(p, Path(import_path).parent.resolve())).as_posix()
    except ValueError:
        return p.as_posix()


def free_import(parts_dir: Path, name: str) -> Path:
    """parts_dir/<name>.import.toml, or <name>_2... when that is taken."""
    path, n = Path(parts_dir) / f"{name}{SUFFIX}", 2
    while path.exists() or path.with_name(path.name[:-len(SUFFIX)] + ".source").exists():
        path, n = Path(parts_dir) / f"{name}_{n}{SUFFIX}", n + 1
    return path


def import_for_source(source: Path, parts_dir: Path) -> Path:
    """The import file for a CAD source: an existing parts_dir/<stem>.import.toml (or _2, ...) that names it, else
    the first free one."""
    source = Path(source).resolve()
    name = sanitize(source.stem if source.is_file() else source.name)
    path, n = Path(parts_dir) / f"{name}{SUFFIX}", 2
    while path.exists():
        try:
            if _resolve(read_import(path).source, path).resolve() == source:
                return path
        except CadError:
            pass
        path, n = Path(parts_dir) / f"{name}_{n}{SUFFIX}", n + 1
    return path


def is_part_target(path: Path) -> bool:
    """A CAD file, or a folder holding a part.toml (classify's "import" besides the import file itself)."""
    p = Path(path)
    return (p.is_dir() and (p / "part.toml").is_file()) or (p.is_file() and p.suffix.lower() in CAD_SUFFIXES)


def open_part(path: Path, version: int, parts_dir: Path | None = None,
              source: Path | None = None) -> "_Session":
    """The session for an import file, a CAD source (a file or a folder of meshes: its import file in `parts_dir`,
    default the parts/ folder next to it, is created when there is none) or a part folder; with `source`, `path` is
    the import file to make from it. CadError when an import-made folder's import file is gone."""
    path = Path(path).resolve()
    if source is not None:
        return PartSession(path, version, source=source)
    if path.is_dir() and (path / "part.toml").is_file():
        if not (path / MARKER).is_file():
            from .plugin_session import PluginSession
            return PluginSession(path, version)
        try:
            rel = tomllib.loads((path / MARKER).read_text(encoding="utf-8"))["import"]
            imp = (path / rel).resolve()
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError):
            raise CadError(f"{path.name}'s {MARKER} can't be read: open the CAD file again to remake it.") from None
        if not imp.is_file():
            raise CadError(f"{path.name} was made from {rel}, which is gone: open the CAD file again to remake it.")
        return PartSession(imp, version)
    if path.is_file() and path.name.lower().endswith(".toml"):
        return PartSession(path, version)
    return PartSession(import_for_source(path, parts_dir or path.parent / "parts"), version, source=path)


def save_upload(stream, length: int, filename: str | None, folder: Path) -> Path:
    """Copy an uploaded file (`length` bytes of `stream`) into `folder` under its own name (only the last part of
    `filename`, which may be %-encoded); 400 for a name that isn't a CAD file or a body over MAX_UPLOAD."""
    name = unquote(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name.startswith(".") or ":" in name or any(ord(ch) < 32 for ch in name):
        raise SessionError(400, "the file needs a name (header X-Filename)")
    if Path(name).suffix.lower() not in CAD_SUFFIXES:
        raise SessionError(400, f"{name} is not a STEP, STL or OBJ file: export it from CAD as one of those")
    if length > MAX_UPLOAD:
        raise SessionError(413, "that file is too large to upload (over 512 MB)")
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    if dest.parent != folder:
        raise SessionError(400, "the file needs a plain name")
    tmp = folder / f".upload-{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "wb") as f:
            left = length
            while left > 0:
                chunk = stream.read(min(left, 1 << 20))
                if not chunk:
                    raise SessionError(400, "the upload stopped before the whole file arrived")
                f.write(chunk)
                left -= len(chunk)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


# ---------------------------------------------------------------- what both modes share

class _Session:
    mode = ""

    def __init__(self, version: int):
        self.version = version
        self._changed_at = version            # the version at which the settings last changed
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._undo: list = []
        self._redo: list = []
        self._messages: list[str] = []
        self._closed = False
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def _start(self, *targets) -> None:
        for name, fn in targets:
            t = threading.Thread(target=fn, daemon=True, name=name)
            self._threads.append(t)
            t.start()

    def _bump(self, changed: bool = False) -> None:
        self.version += 1
        if changed:
            self._changed_at = self.version
        self._cond.notify_all()

    def _push(self, old) -> None:
        self._undo = [*self._undo, old][-HISTORY:]
        self._redo = []

    def _check_open(self) -> None:
        if self._closed:
            raise SessionError(409, "the part was closed")

    def _fresh(self, body: dict) -> None:
        v = body.get("version")
        if not isinstance(v, int) or isinstance(v, bool):
            raise SessionError(400, "version (the integer from the state) is required")
        if v < self._changed_at:
            raise SessionError(409, STALE)

    def _poll(self) -> None:
        while not self._stop.wait(POLL_PERIOD):
            try:
                with self._lock:
                    if not self._closed:
                        self._sync()
            except Exception:   # never let one bad read stop the session following the files
                log.exception("part session poll")

    def wait_state(self, since: int | None) -> dict:
        end = time.monotonic() + POLL_SECONDS
        with self._cond:
            while since is not None and self.version == since and not self._closed and time.monotonic() < end:
                self._cond.wait(1.0)
            return self.state()

    def close(self) -> None:
        self._stop.set()
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(timeout=30)

    def axis(self, body: dict) -> dict:
        raise SessionError(400, "a part from the CAD plugin has no CAD file to measure: import the STEP instead")

    def reimport(self, body: dict) -> tuple[int, dict]:
        raise SessionError(400, "a part from the CAD plugin has no CAD file to read again: import the STEP instead")

    def replace_source(self, path: Path) -> tuple[int, dict]:
        raise SessionError(400, "a part from the CAD plugin has no CAD file to replace: import the STEP instead")

    def source_folder(self) -> Path:
        raise SessionError(400, "a part from the CAD plugin has no CAD file to replace: import the STEP instead")


# ---------------------------------------------------------------- import mode

class PartSession(_Session):
    mode = "import"

    def __init__(self, import_path: Path, version: int = 0, source: Path | None = None):
        """Open `import_path`; when it doesn't exist yet, it is made from `source` (a CAD file or mesh folder) once
        that reads (so a missing `cad` extra leaves nothing behind)."""
        super().__init__(version)
        self.path = Path(import_path).resolve()
        self.imp = None
        self.src = None
        self._pending = Path(source).resolve() if source is not None else None   # the source of an import to make
        self._file_problem: str | None = None
        self._src_problem: str | None = None
        self._sig = None
        self._info: dict = {}                   # piece key -> (volume, com in CAD coordinates, hardware)
        self._src_n = 0                         # counts source reads (the view's cache key)
        self._view_key = self._view = None
        self._guessed: set[str] = set()
        self._result: BuildResult | None = None
        self._build = {"state": "idle", "message": None}
        self._gen = self._built_gen = 0
        with self._lock:
            if self.path.exists():
                self._sync()
            else:
                self._make()
        self._start(("part-poll", self._poll), ("part-build", self._work))

    @property
    def read_only_reason(self) -> str | None:
        return self._file_problem or self._src_problem

    def _stem(self) -> str:
        n = self.path.name
        return n[:-len(SUFFIX)] if n.endswith(SUFFIX) else sanitize(self.path.stem)

    # -- the source ---------------------------------------------------------------------------------
    def _source_path(self, imp=None) -> Path | None:
        """Where the settings' source is (R9: {cad}/{code} expanded, else beside the import file); the source to
        make an import from while there are no settings yet. None (with _src_problem) for an unknown team folder."""
        imp = imp or self.imp
        if imp is None:
            return self._pending
        try:
            return _resolve(imp.source, self.path)
        except CadError as e:
            self._src_problem = str(e)
            return None

    def _read(self, path: Path | None, units: str | None):
        """The source read from `path`, or None with _src_problem saying why (and the build marked failed)."""
        if path is None:
            return None
        try:
            src = load_source(path, units)
        except CadError as e:
            self._src_problem = str(e)
            self._build = {"state": "failed", "message": str(e)}
            return None
        if not src.pieces:
            self._src_problem = f"{path.name} has no pieces to import: export the parts, not an empty file."
            return None
        return src

    def _try_read(self, imp, units: str | None):
        """The source `imp` names, read with `units`; 400 with the reason when it can't be (nothing changes)."""
        keep = self._src_problem, self._build
        path = self._source_path(imp)
        src = self._read(path, units) if path is not None else None
        problem = self._src_problem
        self._src_problem, self._build = keep
        if src is None:
            raise SessionError(400, problem or "the CAD file can't be read")
        return src

    def _use(self, src) -> None:
        """Make `src` (just read) the source: its pieces' volumes, centers and hardware guesses are kept."""
        self.src, self._src_problem, self._src_n = src, None, self._src_n + 1
        diag = assembly_diagonal(src.pieces)
        self._info = {}
        for p in src.pieces:
            i = mesh_inertial(p.v, p.f, 1.0)
            self._info[p.key] = (float(i.mass), np.asarray(i.com, float), is_hardware(p, diag))

    def _make(self) -> None:
        """A first import of the pending source (nothing is written while it can't be read)."""
        src = self._read(self._pending, None)
        if src is None:
            self._bump()
            return
        self._use(src)
        self._commit(None, new_import(src, self._stem(), Path(source_text(self._pending, self.path)),
                                      self.path.parent), [])

    # -- keeping in step with the import file ---------------------------------------------------------
    def _save(self) -> None:
        try:
            write_import(self.imp, self.path)
        except OSError as e:
            raise SessionError(500, f"can't save {self.path.name}: {e.strerror or e}") from None
        self._sig = _sig(self.path)

    def _commit(self, old, new, msgs: list[str], push: bool = True) -> None:
        """Make `new` the settings: saved, on the history (when `push`), rebuilt."""
        if push and old is not None:
            self._push(old)
        self.imp, self._messages = new, msgs
        self._save()
        self._schedule()
        self._bump(changed=True)

    def _schedule(self) -> None:
        self._gen += 1
        self._cond.notify_all()

    def _sync(self) -> None:
        """Pick up an outside edit of the import file (lock held)."""
        sig = _sig(self.path)
        if sig == self._sig or (sig == (None,) and self.imp is None):   # (not made yet: the source didn't read)
            return
        self._sig = sig
        if sig == (None,):
            self._file_problem = (f"{self.path.name} was deleted; put it back, or open the CAD file again to make "
                                  "a new one")
            self._bump()
            return
        try:
            new = read_import(self.path)
        except CadError as e:
            self._file_problem = f"{e} Fix it in a text editor."
            self._bump()
            return
        self._file_problem = None
        old = self.imp
        if old is not None:
            new.written = {**old.written, **new.written}   # never forget a folder a build wrote
            self._push(old)
        self.imp, self._messages = new, []
        if old is None or (old.source, old.units) != (new.source, new.units) or self.src is None:
            path = self._source_path()
            src = self._read(path, new.units) if path is not None else None
            if src is not None:
                self._use(src)
        self._schedule()
        self._bump(changed=True)

    # -- the build worker -----------------------------------------------------------------------------
    def _work(self) -> None:
        while True:
            with self._cond:
                while not self._stop.is_set() and (self._gen == self._built_gen or self.imp is None
                                                   or self.src is None or self.read_only_reason is not None):
                    self._cond.wait()
                if self._stop.is_set():
                    return
                gen, imp, src = self._gen, copy.deepcopy(self.imp), self.src
                if self._build["state"] != "building":
                    self._build = {"state": "building", "message": None}
                    self._bump()
            result, err = None, None
            try:
                result = build_parts(imp, src)
                with self._lock:
                    if gen != self._gen or self._stop.is_set():
                        continue   # an edit came in: build the newest settings instead
                write_parts(result, imp, self.path)
            except CadError as e:
                err = str(e)
            except Exception as e:   # a bug: say so, keep the session
                log.exception("part build")
                err = f"The build failed ({type(e).__name__}: {e}); this is a bug in Scrimmage: please report it."
            with self._cond:
                self._built_gen = gen
                if err is not None:
                    self._result, self._build = None, {"state": "failed", "message": err}
                else:
                    self._result = result
                    self._sync()   # an outside edit first: the record goes on the newest settings
                    if self.imp is not None and self._file_problem is None and self.imp.written != imp.written:
                        self.imp.written = dict(imp.written)
                        try:
                            self._save()
                        except SessionError as e:
                            err = e.message
                    self._build = {"state": "failed" if err else "building" if self._gen != gen else "idle",
                                   "message": err}
                self._bump()

    # -- state ----------------------------------------------------------------------------------------
    def _piece_mass(self, ps, volume) -> float | None:
        if ps.mass:
            return float(ps.mass)
        if volume is None:
            return None
        try:
            return volume * density(ps.material or "aluminum", ps.infill, ps.custom_density)
        except CadError:
            return volume * density("aluminum")

    def state(self) -> dict:
        with self._lock:
            imp, src = self.imp, self.src
            out = {"version": self.version, "mode": "import", "path": str(self.path),
                   "name": imp.name if imp else self._stem(), "source": imp.source if imp else None,
                   "units": imp.units if imp else None,
                   "frame": dataclasses.asdict(imp.frame) if imp else None, "pieces": [], "bodies": [], "parts": [],
                   "couplings": [], "loops": [], "checklist": [], "messages": list(self._messages),
                   "build": dict(self._build),
                   "can_undo": bool(self._undo), "can_redo": bool(self._redo),
                   "read_only_reason": self.read_only_reason}
            if imp is None:
                return out
            R, t = to_robot(imp.frame)
            names = {p.key: (p.name, list(p.group)) for p in src.pieces} if src else {}
            sums: dict[str, list] = {n: [] for n in imp.bodies}
            for key, ps in imp.pieces.items():
                vol, com, hw = self._info.get(key, (None, None, False))
                mass = self._piece_mass(ps, vol)
                name, group = names.get(key, (key.rsplit("/", 1)[-1], []))
                out["pieces"].append({"key": key, "name": name, "group": group, "body": ps.body,
                                      "material": ps.material, "infill": ps.infill,
                                      "custom_density": ps.custom_density, "mass": mass, "typed_mass": ps.mass,
                                      "left_out": ps.left_out, "volume": vol, "hardware": hw})
                if not ps.left_out and ps.body in sums and mass is not None and com is not None:
                    sums[ps.body].append((mass, R @ com + t))
            out["couplings"] = copy.deepcopy(imp.couplings)
            out["loops"] = copy.deepcopy(imp.loops)
            for name, bs in imp.bodies.items():
                got = sums[name]
                total = sum(m for m, _ in got)
                com = _floats(sum(m * c for m, c in got) / total) if total > 0 else None
                d = {k: dataclasses.asdict(v) if dataclasses.is_dataclass(v) else v
                     for k, v in ((f.name, getattr(bs, f.name)) for f in dataclasses.fields(bs))}
                d["mounts"] = [dataclasses.asdict(m) for m in bs.mounts]
                d["typed_mass"] = d.pop("mass")
                out["bodies"].append({"name": name, **d, "mass": float(bs.mass) if bs.mass else total, "com": com})
            built = self._result.parts if self._result is not None else {}
            for name, ps in imp.parts.items():
                b = built.get(name)
                out["parts"].append({"name": name, "folder": ps.folder, "total_mass": ps.total_mass,
                                     "mass": float(b.mass) if b else None, "com": _floats(b.com) if b else None})
            if src is not None:
                out["checklist"] = [dataclasses.asdict(i) for i in
                                    checklist(imp, src, self._result or BuildResult(), self._guessed,
                                              {k: v[0] for k, v in self._info.items()})]
            return out

    def view(self) -> dict:
        """Every piece's mesh in robot coordinates (left-out pieces too); cached per source read and frame."""
        with self._lock:
            if self.imp is None or self.src is None:
                return {"pieces": []}
            key = (self._src_n, dataclasses.astuple(self.imp.frame))
            if key != self._view_key:
                R, t = to_robot(self.imp.frame)
                self._view = {"pieces": [{"key": p.key, **_mesh(p.v @ R.T + t, p.f)} for p in self.src.pieces]}
                self._view_key = key
            return self._view

    # -- requests -------------------------------------------------------------------------------------
    def _writable(self, body: dict) -> None:
        self._check_open()
        self._sync()
        self._fresh(body)
        if self.read_only_reason is not None:
            raise SessionError(409, self.read_only_reason)

    def edit(self, body: dict) -> tuple[int, dict]:
        op, args = body.get("op"), body.get("args", {})
        if not isinstance(op, str):
            raise SessionError(400, "op (the edit's name) is required")
        if not isinstance(args, dict):
            raise SessionError(400, "args must be a JSON object")
        with self._lock:
            self._writable(body)
            try:
                new, msgs = apply_edit(self.imp, op, args)
            except EditError as e:
                return _refused(e)
            if new.units != self.imp.units:   # mesh files are scaled as they are read
                self._use(self._try_read(new, new.units))
            self._commit(self.imp, new, msgs)
            return 200, self.state()

    def history(self, which: str, body: dict) -> tuple[int, dict]:
        with self._lock:
            self._writable(body)
            stack, other = (self._undo, self._redo) if which == "undo" else (self._redo, self._undo)
            if not stack:
                return 200, {**self.state(), "ok": False}
            new = copy.deepcopy(stack[-1])
            new.written = dict(self.imp.written)   # the build's record is never undone
            if (new.source, new.units) != (self.imp.source, self.imp.units):
                self._use(self._try_read(new, new.units))
            stack.pop()
            other.append(self.imp)
            self.imp, self._messages = new, []
            self._save()
            self._schedule()
            self._bump(changed=True)
            return 200, self.state()

    def axis(self, body: dict) -> dict:
        with self._lock:
            self._check_open()
            pieces = {p.key: p for p in self.src.pieces} if self.src is not None else {}
            p, tri, kind = pieces.get(body.get("piece")), body.get("triangle"), body.get("kind")
            if p is None:
                raise SessionError(400, "there is no such piece: click on a piece")
            if isinstance(tri, bool) or not isinstance(tri, int):
                raise SessionError(400, "triangle must be a whole number (the triangle clicked)")
            if kind not in ("round", "flat"):
                raise SessionError(400, "kind is round or flat")
            try:
                point, d = fit_axis(p, tri, kind)
            except CadError as e:
                raise SessionError(400, str(e)) from None
            return {"point": _floats(point), "axis": _floats(d)}

    def reimport(self, body: dict, path: Path | None = None) -> tuple[int, dict]:
        """Read the source again (or `path`, a new one, in its place) and reconcile the settings with it."""
        with self._lock:
            self._check_open()
            self._sync()
            if self._file_problem is not None:
                raise SessionError(409, self._file_problem)
            if "version" in body:
                self._fresh(body)
            if path is None:
                path = self._source_path()
            units = self.imp.units if self.imp is not None and path == self._source_path() else None
        if path is None:
            raise SessionError(400, self._src_problem or "this import names no CAD file")
        try:
            src = load_source(path, units)   # outside the lock: a big STEP takes a while
        except CadError as e:
            with self._lock:
                self._src_problem, self._build = str(e), {"state": "failed", "message": str(e)}
                self._bump()
            raise SessionError(400, str(e)) from None
        with self._lock:
            self._check_open()
            self._use(src)
            if self.imp is None:
                self._pending = Path(path).resolve()
                self._commit(None, new_import(src, self._stem(), Path(source_text(path, self.path)),
                                              self.path.parent), [])
                return 200, self.state()
            base = copy.deepcopy(self.imp)
            base.source, base.units = source_text(path, self.path), src.units
            new, msgs = reimport(base, src)
            # the checklist asks to check the pieces matched by shape alone (by their new keys)
            m = match([{"key": k, "fp": p.fp} for k, p in base.pieces.items()], src.pieces)
            self._guessed = {m.pairs[k] for k in m.guessed}
            self._commit(self.imp, new, msgs)
            return 200, self.state()

    def replace_source(self, path: Path) -> tuple[int, dict]:
        return self.reimport({}, path)

    def source_folder(self) -> Path:
        """Where a dropped file goes: parts/<import name>.source/ next to the import file."""
        name = sanitize(self.imp.name) if self.imp is not None else self._stem()   # a hand-edited name stays inside
        return self.path.parent / f"{name}.source"
