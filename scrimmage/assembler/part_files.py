"""Where the Part tab's files go: the import file a CAD source gets (`<name>.import.toml` in parts/, never one
already taken), how the import file stores the source's path, and the CAD files sent to the page (one STEP, STL or
OBJ file, or several meshes read as one folder) copied into the import's `<name>.source/` folder. None of it needs a
session; part_session re-exports every name here.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from urllib.parse import unquote

from .. import i18n, teamfolders
from ..cad import CadError
from ..cad.build import _resolve
from ..cad.names import sanitize
from ..cad.settings import read_import, replace_file
from ..cad.source import MESH_SUFFIXES, STEP_SUFFIXES
from .bench_session import SessionError

CAD_SUFFIXES = STEP_SUFFIXES + MESH_SUFFIXES
MAX_UPLOAD = 512 * 1024 * 1024
SUFFIX = ".import.toml"


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


def _taken(path: Path) -> bool:
    """Whether parts/<name>.import.toml can't be a new import: it, its <name>.source/ or its default part folder
    parts/<name>/ (say one FreeCAD made) is already there."""
    stem = path.name[:-len(SUFFIX)]
    return path.exists() or path.with_name(stem + ".source").exists() or path.with_name(stem).exists()


def free_import(parts_dir: Path, name: str) -> Path:
    """parts_dir/<name>.import.toml, or <name>_2... when that is taken."""
    path, n = Path(parts_dir) / f"{name}{SUFFIX}", 2
    while _taken(path):
        path, n = Path(parts_dir) / f"{name}_{n}{SUFFIX}", n + 1
    return path


def import_for_source(source: Path, parts_dir: Path) -> Path:
    """The import file for a CAD source: an existing parts_dir/<stem>.import.toml (or _2, ...) that names it, else
    the first free one (see free_import)."""
    source = Path(source).resolve()
    name = sanitize(source.stem if source.is_file() else source.name)
    path, n = Path(parts_dir) / f"{name}{SUFFIX}", 2
    while _taken(path):
        try:
            if path.exists() and _resolve(read_import(path).source, path).resolve() == source:
                return path
        except CadError:
            pass
        path, n = Path(parts_dir) / f"{name}_{n}{SUFFIX}", n + 1
    return path


def is_part_target(path: Path) -> bool:
    """A CAD file, or a folder holding a part.toml (classify's "import" besides the import file itself)."""
    p = Path(path)
    return (p.is_dir() and (p / "part.toml").is_file()) or (p.is_file() and p.suffix.lower() in CAD_SUFFIXES)



MESH_FOLDER = "meshes"   # several mesh files sent at once go in parts/<name>.source/meshes/, read as one folder


def upload_name(filename: str | None) -> str:
    """The plain name of an uploaded file (only the last part of `filename`, which may be %-encoded); 400 for no
    name or one that isn't a CAD file."""
    name = unquote(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name.startswith(".") or ":" in name or any(ord(ch) < 32 for ch in name):
        raise SessionError(400, i18n.t("the file needs a name (header X-Filename)"))
    if Path(name).suffix.lower() not in CAD_SUFFIXES:
        raise SessionError(400, i18n.t("{name} is not a STEP, STL or OBJ file: export it from CAD as one of those",
                                       name=name))
    return name


def upload_set(names: list[str]) -> None:
    """Several files sent at once must be STL or OBJ files, read together like a folder of them: 400 in one
    sentence for STEP files among them, or two with the same name."""
    steps = [n for n in names if Path(n).suffix.lower() in STEP_SUFFIXES]
    if len(steps) == len(names) and len(names) > 1:
        raise SessionError(400, i18n.t("Use one STEP file at a time: a STEP file already holds the whole assembly."))
    if steps:
        raise SessionError(400, i18n.t("Use a STEP file on its own, or only STL and OBJ files, not both at once."))
    seen = set()
    for n in names:
        if n.lower() in seen:
            raise SessionError(400, i18n.t("Two of the files are called {n}: give each piece its own file name.", n=n))
        seen.add(n.lower())


def _copy_in(stream, length: int, dest) -> None:
    with open(dest, "wb") as f:
        left = length
        while left > 0:
            chunk = stream.read(min(left, 1 << 20))
            if not chunk:
                raise SessionError(400, i18n.t("the upload stopped before the whole file arrived"))
            f.write(chunk)
            left -= len(chunk)


def save_upload(stream, length: int, filename: str | None, folder: Path) -> Path:
    """Copy an uploaded file (`length` bytes of `stream`) into `folder` under its own name (upload_name); 400 for
    a name that isn't a CAD file, 413 for a body over MAX_UPLOAD."""
    name = upload_name(filename)
    if length > MAX_UPLOAD:
        raise SessionError(413, i18n.t("that file is too large to upload (over 512 MB)"))
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    if dest.parent != folder:
        raise SessionError(400, i18n.t("the file needs a plain name"))
    tmp = folder / f".upload-{uuid.uuid4().hex[:8]}.tmp"
    try:
        _copy_in(stream, length, tmp)
        replace_file(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def save_uploads(stream, length: int, files: list[tuple[str, int]], folder: Path) -> Path:
    """Several mesh files sent one after another in `stream` (`files` is each one's (name, size), `length` their
    sum) into `folder`/meshes/, replacing what was there, so the set is read as one folder of STL files. 400 as
    for upload_name and upload_set, or when the sizes don't add up; 413 over MAX_UPLOAD."""
    names = [upload_name(n) for n, _ in files]
    upload_set(names)
    sizes = [n for _, n in files]
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in sizes) or sum(sizes) != length:
        raise SessionError(400, i18n.t("the files' sizes don't add up to what was sent"))
    if length > MAX_UPLOAD:
        raise SessionError(413, i18n.t("those files are too large to upload (over 512 MB)"))
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    dest, tag = folder / MESH_FOLDER, uuid.uuid4().hex[:8]
    tmp, old = folder / f".upload-{tag}", folder / f".old-{tag}"
    tmp.mkdir()
    try:
        for name, size in zip(names, sizes):
            _copy_in(stream, size, tmp / name)
        if dest.exists():
            replace_file(dest, old)
        try:
            replace_file(tmp, dest)
        except OSError:
            if old.exists():
                replace_file(old, dest)
            raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
    return dest


def upload_stem(names: list[str]) -> str:
    """A name for a new import made from several files: what their names start with (arm_a.stl and arm_b.stl make
    "arm"), else "stl_parts"."""
    stems = [Path(n).stem for n in names]
    common = os.path.commonprefix(stems).rstrip("_- .0123456789")
    return sanitize(common) if len(common) >= 3 else "stl_parts"
