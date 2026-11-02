"""Reading a CAD source: a STEP assembly, or STL/OBJ meshes, as named pieces in meters (CAD coordinates).

A STEP file goes through cascadio (OpenCASCADE) to a GLB in a temp folder, which keeps each part's name and the
nesting of sub-assemblies; the result is one `Piece` per mesh with its world transform applied. STL/OBJ files are
one piece each, scaled from `units` to meters. Nothing is rotated: pieces stay in the CAD file's own axes (the
robot frame is chosen later). `cascadio` and `trimesh` are imported inside the functions that need them, only here,
so the rest of `scrimmage.cad` loads without the `cad` extra; a missing one raises `CadError(MISSING_EXTRA)`.
"""

import json
import shutil
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import MISSING_EXTRA, CadError

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


def guess_units(extent_max: float) -> str:
    """The likely units of a mesh whose largest bounding-box side (all pieces together) is `extent_max` in file units."""
    if extent_max < 5:
        return "m"
    return "in" if extent_max <= 60 else "mm"


def load_source(path: "Path | list[Path]", units: str | None = None) -> Source:
    paths = [Path(p) for p in path] if isinstance(path, (list, tuple)) else [Path(path)]
    if not paths:
        raise CadError("No CAD file was given: choose a STEP, STL or OBJ file.")
    if len(paths) == 1 and paths[0].is_dir():
        found = sorted(p for p in paths[0].iterdir() if p.suffix.lower() in MESH_SUFFIXES)
        if not found:
            raise CadError(f"The folder {paths[0].name} has no STL or OBJ files: pick a folder that does.")
        return _load_meshes(found, units, paths[0])
    kinds = {p.suffix.lower() in STEP_SUFFIXES for p in paths}
    if kinds == {True}:
        if len(paths) > 1:
            raise CadError("Open one STEP file at a time: pick a single .step file.")
        return _load_step(paths[0])
    for p in paths:
        if p.suffix.lower() not in MESH_SUFFIXES:
            raise CadError(f"{p.name} is not a STEP, STL or OBJ file: export it from CAD as one of those.")
    if len(paths) > 1 and True in kinds:
        raise CadError("Don't mix a STEP file with mesh files: open them separately.")
    return _load_meshes(paths, units, paths[0] if len(paths) == 1 else paths[0].parent)


def _load_meshes(files: list[Path], units: str | None, where: Path) -> Source:
    from ..bench.cad import read_mesh
    from ..bench.spec import BenchError
    if units is not None and units not in UNITS:
        raise CadError(f"Unknown units '{units}': pick m, mm or in.")
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
        raise CadError(MISSING_EXTRA) from None
    unreadable = CadError(f"Could not read {path.name} as a STEP file: re-export it from CAD and try again.")
    with tempfile.TemporaryDirectory(prefix="scrimmage-cad-") as tmp:
        # cascadio takes C strings; copying to a plain name sidesteps spaces and non-ASCII characters in the path
        src, glb = Path(tmp) / "in.step", Path(tmp) / "out.glb"
        try:
            shutil.copyfile(path, src)
        except OSError as e:
            raise CadError(f"Could not open {path.name}: {e.strerror or e}. Check the file and try again.") from None
        try:
            cascadio.step_to_glb(str(src), str(glb), tol_linear=TOL_LINEAR, tol_angular=TOL_ANGULAR)
            raw = glb.read_bytes()
            scene = trimesh.load(str(glb), force="scene")
            doc = _glb_json(raw)
        except Exception:
            raise unreadable from None
    meshes = _meshes(scene, doc)
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
        if "mesh" in node:
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
        raise CadError(f"{path.name} has no solid parts in it: check the STEP file has bodies, not just sketches.")
    return Source("step", path, "m", pieces, groups)


def _glb_json(raw: bytes) -> dict:
    """The JSON chunk of a GLB. trimesh renames repeated node names (bolt, bolt_1...), so the original names and
    nesting are read from here."""
    length = struct.unpack_from("<I", raw, 12)[0]
    return json.loads(raw[20:20 + length])


def _meshes(scene, doc: dict):
    """Per glTF mesh index: (vertices float64, faces int32) with duplicate vertices merged (so solids are closed).
    trimesh keeps the geometry in mesh order, which is checked by count; None if it doesn't match."""
    geoms = list(scene.geometry.values())
    if len(geoms) != len(doc.get("meshes", [])):
        return None
    out = []
    for g in geoms:
        g = g.copy()
        g.merge_vertices()
        out.append((np.asarray(g.vertices, dtype=np.float64), np.asarray(g.faces, dtype=np.int32)))
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
