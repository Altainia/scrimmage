"""Reading a CAD source: a STEP assembly, or STL/OBJ meshes, as named pieces in meters (CAD coordinates).

A STEP file goes through cascadio (OpenCASCADE) to a GLB in a temp folder, which keeps each part's name and the
nesting of sub-assemblies; the result is one `Piece` per mesh with its world transform applied. STL/OBJ files are
one piece each, scaled from `units` to meters. Nothing is rotated: pieces stay in the CAD file's own axes (the
robot frame is chosen later). `cascadio` and `trimesh` are imported inside the functions that need them, only here,
so the rest of `scrimmage.cad` loads without the `cad` extra; a missing one raises `CadError(missing_extra())`.
"""

import json
import re
import shutil
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import i18n
from . import CadError, missing_extra

UNITS = {"m": 1.0, "mm": 0.001, "in": 0.0254}
TOL_LINEAR = 0.2        # tessellation, in the STEP file's own units
TOL_ANGULAR = 0.5       # radians
MESH_SUFFIXES = (".stl", ".obj")
STEP_SUFFIXES = (".step", ".stp")


@dataclass
class Piece:
    key: str                    # "Climber/hook_assy/hook"; repeated paths get "#2", "#3" in file order
    name: str                   # the piece's own name, as the CAD file spells it
    group: tuple[str, ...]      # the keys of the assemblies it sits in, top first
    v: np.ndarray               # (n, 3) float64, meters, CAD coordinates
    f: np.ndarray               # (m, 3) int32


@dataclass
class Source:
    kind: str                   # "step" or "mesh"
    path: Path
    units: str                  # "m" for STEP (cascadio already gives meters); the scale used for meshes
    pieces: list[Piece] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)     # every assembly key, in file order


STEP_UNITS = {"m": 1.0, "mm": 0.001, "cm": 0.01, "in": 0.0254, "ft": 0.3048}   # a STEP's length units, in m
_ENTITY = re.compile(rb"#(\d+)\s*=\s*(.*?);\s*(?=#\d+\s*=|ENDSEC)", re.S)
_CONTEXT = re.compile(rb"GLOBAL_UNIT_ASSIGNED_CONTEXT\s*\(\s*\(([^)]*)\)", re.S)
_LENGTH = (("in", re.compile(rb"CONVERSION_BASED_UNIT\s*\(\s*'INCH'", re.I)),
           ("ft", re.compile(rb"CONVERSION_BASED_UNIT\s*\(\s*'FOOT'", re.I)),
           ("mm", re.compile(rb"SI_UNIT\s*\(\s*\.MILLI\.\s*,\s*\.METRE\.\s*\)")),
           ("cm", re.compile(rb"SI_UNIT\s*\(\s*\.CENTI\.\s*,\s*\.METRE\.\s*\)")),
           ("m", re.compile(rb"SI_UNIT\s*\(\s*\$\s*,\s*\.METRE\.\s*\)")))


def step_length_unit(path: Path) -> str | None:
    """The length unit a STEP file's products say their numbers are in (a STEP_UNITS key): the LENGTH_UNIT entity each
    GLOBAL_UNIT_ASSIGNED_CONTEXT names, so a unit elsewhere in the file (a tolerance's) isn't taken for it. None when
    there is no such context, their units disagree (sub-assemblies in different units), or the file can't be read.
    cascadio reads it and gives meters, so a STEP's own unit is always applied; a range piece's `units` reads the
    numbers in another one (testrange/pieces.py)."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    entities = {int(m.group(1)): m.group(2) for m in _ENTITY.finditer(raw)}
    found = set()
    for ctx in _CONTEXT.finditer(raw):
        for ref in re.findall(rb"#(\d+)", ctx.group(1)):
            body = entities.get(int(ref), b"")
            if b"LENGTH_UNIT" not in body:
                continue
            unit = next((u for u, pattern in _LENGTH if pattern.search(body)), None)
            if unit is None:
                return None             # a length unit it doesn't know
            found.add(unit)
    return found.pop() if len(found) == 1 else None


def guess_units(extent_max: float) -> str:
    """The likely units of a mesh whose largest bounding-box side (all pieces together) is `extent_max` in file units."""
    if extent_max < 5:
        return "m"
    return "in" if extent_max <= 60 else "mm"


def load_source(path: "Path | list[Path]", units: str | None = None) -> Source:
    paths = [Path(p) for p in path] if isinstance(path, (list, tuple)) else [Path(path)]
    if not paths:
        raise CadError(i18n.t("No CAD file was given: choose a STEP, STL or OBJ file."))
    if len(paths) == 1 and paths[0].is_dir():
        found = sorted(p for p in paths[0].iterdir() if p.suffix.lower() in MESH_SUFFIXES)
        if not found:
            raise CadError(i18n.t("The folder {name} has no STL or OBJ files: pick a folder that does.",
                                  name=paths[0].name))
        return _load_meshes(found, units, paths[0])
    kinds = {p.suffix.lower() in STEP_SUFFIXES for p in paths}
    if kinds == {True}:
        if len(paths) > 1:
            raise CadError(i18n.t("Open one STEP file at a time: pick a single .step file."))
        return _load_step(paths[0])
    for p in paths:
        if p.suffix.lower() not in MESH_SUFFIXES:
            raise CadError(i18n.t("{name} is not a STEP, STL or OBJ file: export it from CAD as one of those.",
                                  name=p.name))
    if len(paths) > 1 and True in kinds:
        raise CadError(i18n.t("Don't mix a STEP file with mesh files: open them separately."))
    return _load_meshes(paths, units, paths[0] if len(paths) == 1 else paths[0].parent)


def _load_meshes(files: list[Path], units: str | None, where: Path) -> Source:
    from ..bench.cad import read_mesh
    from ..bench.spec import BenchError
    if units is not None and units not in UNITS:
        raise CadError(i18n.t("Unknown units '{units}': pick m, mm or in.", units=units))
    raw = []
    for p in files:
        try:
            raw.append((p, *read_mesh(p)))
        except BenchError as e:
            raise CadError(str(e)) from None
    lo = np.min([v.min(axis=0) for _, v, _ in raw], axis=0)
    hi = np.max([v.max(axis=0) for _, v, _ in raw], axis=0)
    units = units or guess_units(float((hi - lo).max()))
    scale = UNITS[units]
    pieces, seen = [], {}
    for p, v, f in raw:
        key = _numbered(p.stem, seen)
        pieces.append(Piece(key, p.stem, (), np.ascontiguousarray(v * scale, dtype=np.float64), f.astype(np.int32)))
    return Source("mesh", where, units, pieces, [])


def _numbered(key: str, seen: dict[str, int]) -> str:
    """`key`, or `key#2`, `key#3`... on its repeats (in the order they come)."""
    seen[key] = seen.get(key, 0) + 1
    return key if seen[key] == 1 else f"{key}#{seen[key]}"


def _load_step(path: Path) -> Source:
    try:
        import cascadio
        import trimesh
    except ImportError:
        raise CadError(missing_extra()) from None
    unreadable = CadError(i18n.t("Could not read {name} as a STEP file: re-export it from CAD and try again.",
                                 name=path.name))
    with tempfile.TemporaryDirectory(prefix="scrimmage-cad-") as tmp:
        # cascadio takes C strings; copying to a plain name sidesteps spaces and non-ASCII characters in the path
        src, glb = Path(tmp) / "in.step", Path(tmp) / "out.glb"
        try:
            shutil.copyfile(path, src)
        except OSError as e:
            raise CadError(i18n.t("Could not open {name}: {error}. Check the file and try again.",
                                  name=path.name, error=e.strerror or e)) from None
        try:
            cascadio.step_to_glb(str(src), str(glb), tol_linear=TOL_LINEAR, tol_angular=TOL_ANGULAR)
            raw = glb.read_bytes()
            doc = _glb_json(raw)
        except Exception:
            raise unreadable from None
    meshes = _meshes(raw, doc, trimesh)
    if meshes is None:
        raise unreadable
    pieces: list[Piece] = []
    groups: list[str] = []
    nodes = doc.get("nodes", [])
    roots = (doc.get("scenes") or [{"nodes": range(len(nodes))}])[doc.get("scene", 0)].get("nodes", [])

    def walk(idx: int, parent_key: str, parent_group: tuple[str, ...], parent_world: np.ndarray, seen: dict):
        node = nodes[idx]
        name = str(node.get("name") or f"node{idx}")
        key = _numbered(f"{parent_key}/{name}" if parent_key else name, seen)
        world = parent_world @ _local(node)
        if "mesh" in node and len(meshes[node["mesh"]][1]):     # a mesh of only points or lines has no surface
            v, f = meshes[node["mesh"]]
            pieces.append(Piece(key, name, parent_group, v @ world[:3, :3].T + world[:3, 3], f))
        if node.get("children"):
            groups.append(key)
            below: dict[str, int] = {}
            for c in node["children"]:
                walk(c, key, parent_group + (key,), world, below)

    top: dict[str, int] = {}
    for r in roots:
        walk(r, "", (), np.eye(4), top)
    if not pieces:
        raise CadError(i18n.t("{name} has no solid parts in it: check the STEP file has bodies, not just sketches.",
                              name=path.name))
    return Source("step", path, "m", pieces, groups)


def _glb_json(raw: bytes) -> dict:
    """The JSON chunk of a GLB. trimesh renames repeated node names (bolt, bolt_1...), so the original names and
    nesting are read from here."""
    length = struct.unpack_from("<I", raw, 12)[0]
    return json.loads(raw[20:20 + length])


class _Broken(Exception):
    """A GLB that isn't the plain triangles an OpenCASCADE export writes (caught in _meshes: never shown)."""


_COMPONENTS = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_WIDTH = {"SCALAR": 1, "VEC3": 3}


def _glb_bin(raw: bytes) -> bytes:
    """The binary chunk of a GLB (b"" when it has none)."""
    length = struct.unpack_from("<I", raw, 12)[0]
    at = 20 + length
    if len(raw) < at + 8:
        return b""
    size, kind = struct.unpack_from("<II", raw, at)
    return raw[at + 8:at + 8 + size] if kind == 0x004E4942 else b""


def _accessor(doc: dict, blob: bytes, index: int) -> np.ndarray:
    """One glTF accessor as an (count, width) array, reading its buffer view's stride. Raises on anything a plain
    OpenCASCADE export doesn't write (sparse accessors, other buffers, unknown types)."""
    acc = doc["accessors"][index]
    if "sparse" in acc or acc["type"] not in _WIDTH or acc["componentType"] not in _COMPONENTS:
        raise _Broken
    view = doc["bufferViews"][acc["bufferView"]]
    if view.get("buffer", 0) != 0:
        raise _Broken
    dtype = np.dtype(_COMPONENTS[acc["componentType"]])
    width, count = _WIDTH[acc["type"]], int(acc["count"])
    start = int(view.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
    stride = int(view.get("byteStride") or dtype.itemsize * width)
    end = start + (count - 1) * stride + dtype.itemsize * width if count else start
    if end > int(view.get("byteOffset", 0)) + int(view["byteLength"]) or end > len(blob):
        raise _Broken
    out = np.ndarray((count, width), dtype=dtype, buffer=blob, offset=start, strides=(stride, dtype.itemsize))
    return np.array(out)


def _meshes(raw: bytes, doc: dict, trimesh):
    """Per glTF mesh index: (vertices float64, faces int32), every triangle primitive of the mesh together, with
    duplicate vertices merged (so solids are closed). Read from the GLB by index: OpenCASCADE writes a part with
    several colors as one mesh of several primitives, so meshes and geometries needn't match one to one (the 2024
    STAGE STEP, TE-24200: 81 meshes, 84 primitives). Points and lines are left out (no surface). None (the "re-export
    it" message) for what it can't read right rather than read it wrong or drop it: an index past its vertices, an
    accessor past its buffer, triangle strips or fans, quantized or normalized positions."""
    blob = _glb_bin(raw)
    out = []
    try:
        for mesh in doc.get("meshes", []):
            vs, fs, n = [], [], 0
            for prim in mesh.get("primitives", []):
                mode = prim.get("mode", 4)
                if mode in (0, 1, 2, 3):            # points and lines: no surface
                    continue
                if mode != 4:                       # strips and fans (5, 6) aren't decoded: say so, never drop them
                    raise _Broken
                pos = doc["accessors"][prim["attributes"]["POSITION"]]
                if pos.get("componentType") != 5126 or pos.get("normalized"):   # quantized positions aren't meters
                    raise _Broken
                v = _accessor(doc, blob, prim["attributes"]["POSITION"]).astype(np.float64)
                f = (_accessor(doc, blob, prim["indices"]).astype(np.int64).reshape(-1, 3) if "indices" in prim
                     else np.arange(len(v) - len(v) % 3, dtype=np.int64).reshape(-1, 3))
                if len(f) and (f.min() < 0 or f.max() >= len(v)):
                    return None
                vs.append(v)
                fs.append(f + n)
                n += len(v)
            v = np.concatenate(vs) if vs else np.zeros((0, 3))
            f = np.concatenate(fs) if fs else np.zeros((0, 3), dtype=np.int64)
            g = trimesh.Trimesh(v, f, process=False)
            g.merge_vertices()
            out.append((np.asarray(g.vertices, dtype=np.float64), np.asarray(g.faces, dtype=np.int32)))
    except (KeyError, IndexError, TypeError, ValueError, _Broken):
        return None
    return out


def _local(node: dict) -> np.ndarray:
    """A glTF node's local 4x4 transform (a column-major `matrix`, or translation/rotation/scale)."""
    if "matrix" in node:
        return np.array(node["matrix"], dtype=np.float64).reshape(4, 4).T
    m = np.eye(4)
    x, y, z, w = node.get("rotation", (0, 0, 0, 1))
    m[:3, :3] = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                 [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                 [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    m[:3, :3] *= np.array(node.get("scale", (1, 1, 1)))
    m[:3, 3] = node.get("translation", (0, 0, 0))
    return m
