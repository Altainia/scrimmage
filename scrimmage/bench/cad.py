"""CAD on the bench: a mesh file (STL or OBJ) for a part that doesn't move, or a part folder from the FreeCAD exporter.

Meshes. A mesh collides as convex pieces (MuJoCo collides a mesh as its convex hull). A mesh is concave when its hull's
volume exceeds its own by more than CONCAVE (2%); a concave mesh is split with `coacd` (the optional extra,
`pip install scrimmage[bench]`), and the pieces are cached beside the mesh as `<mesh>.convex/<key>/piece_NNN.obj`,
keyed by the file's bytes, its units and the split's settings, so a rerun reads them back. Pieces are written in the
mesh's own units. When the mesh's folder can't be written the cache goes under the temp folder (TEMP_CACHE), with a
warning. Without coacd a concave mesh is an error: it never runs with its hollows filled in by a hull.

Part folders (`parts.model.load_part`). Every body is welded to the bench at the part's pose, except a body whose
joint carries a motor slot the bench file gives a shaft (`slots = { <slot> = "<shaft>" }`): it turns with that
shaft, a hinge at the joint, positive about the joint's axis (flipped by the slot's `invert`). A joint left without a
shaft is welded, with a warning. Only colliding geoms go on the bench (a look-only mesh would hide the view); a
colliding mesh is split like a mesh file, in meters. A colliding cylinder on a turning body, on its axis, is a wheel:
it takes the [[cad]] `tread` (friction, and against a squishy ball the tread's softness), as the exporter's wheel
cylinders are. The shaft's inertia is the bench's [shaft] table's; the part's masses and its own couplings, loops
and motor settings are not used.
"""

import hashlib
import math
import os
import shutil
import struct
import tempfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import mujoco
import numpy as np

from ..parts.assemble import AXIS_TURN, TREAD_STIFFNESS
from ..parts.model import Body, Part, PartError, load_part, quat_from_rpy
from .spec import UNITS, Bench, BenchError, Cad

CONCAVE = 0.02  # a mesh whose hull is more than 2% bigger than itself is concave
TEMP_CACHE = Path(tempfile.gettempdir()) / "scrimmage-convex"
# coacd's settings: a concavity threshold of 0.02 (of the mesh's size) cuts a 1/8 in. hood of 0.19 m radius into
# pieces about 7 degrees apart, inside faces within 0.4 mm of the curve; fewer search iterations than coacd's
# defaults keep a hood's split under a second. A fixed seed keeps the split the same every time.
SPLIT = dict(threshold=0.02, max_convex_hull=-1, preprocess_mode="auto", resolution=1000, mcts_nodes=10,
             mcts_iterations=60, mcts_max_depth=3, merge=True, seed=0)
SPLIT_VERSION = 1  # bump when the split's output changes for the same settings
WELD = 1e-6  # vertices this close (of the mesh's size) are one
WHEEL_ON_AXIS = 1e-3  # m: a cylinder's center this close to its shaft's axis (and parallel) is a wheel


# ------------------------------------------------------------------------------------------------------- meshes

def read_mesh(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """A mesh file's vertices (n x 3, in the file's units) and triangles (m x 3 indices). STL (binary or text) or
    OBJ (polygons are fanned into triangles). STL's repeated corners are merged."""
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise BenchError(f"mesh file not found: {path}") from None
    except OSError as e:
        raise BenchError(f"can't read mesh {path}: {e}") from None
    if suffix == ".stl":
        tris = _stl(data, path)
        v, inv = np.unique(tris.reshape(-1, 3), axis=0, return_inverse=True)
        f = inv.reshape(-1, 3)
    elif suffix == ".obj":
        v, f = _obj(data, path)
    else:
        raise BenchError(f"mesh {path} is not an STL or OBJ file (export it from CAD as .stl or .obj)")
    if not len(f):
        raise BenchError(f"mesh {path} has no triangles")
    return v, f


def _stl(data: bytes, path: Path) -> np.ndarray:
    if len(data) >= 84:
        (n,) = struct.unpack_from("<I", data, 80)
        if len(data) == 84 + 50 * n:
            rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]),
                                count=n, offset=84)
            return rec["v"].astype(float)
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        raise BenchError(f"mesh {path} is neither a binary nor a text STL") from None
    pts = [tuple(float(x) for x in ln.split()[1:4]) for ln in text.splitlines() if ln.strip().startswith("vertex")]
    if len(pts) % 3:
        raise BenchError(f"mesh {path}: a text STL's vertices must come in threes")
    return np.array(pts, dtype=float).reshape(-1, 3, 3)


def _obj(data: bytes, path: Path) -> tuple[np.ndarray, np.ndarray]:
    v, f = [], []
    for i, ln in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
        parts = ln.split()
        if not parts:
            continue
        try:
            if parts[0] == "v":
                v.append(tuple(float(x) for x in parts[1:4]))
            elif parts[0] == "f":
                idx = [int(p.split("/")[0]) for p in parts[1:]]
                idx = [k - 1 if k > 0 else len(v) + k for k in idx]
                f += [(idx[0], idx[j], idx[j + 1]) for j in range(1, len(idx) - 1)]
        except (ValueError, IndexError):
            raise BenchError(f"mesh {path} line {i}: can't read {ln.strip()!r}") from None
    v, f = np.array(v, dtype=float).reshape(-1, 3), np.array(f, dtype=int).reshape(-1, 3)
    if len(f) and (f.min() < 0 or f.max() >= len(v)):
        raise BenchError(f"mesh {path}: a face names a vertex that isn't there")
    return v, f


def weld(v: np.ndarray, f: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Merge vertices within WELD of the mesh's size of each other (STL repeats every corner per face, and CAD
    rounds them), and drop the faces that collapse."""
    span = float(np.linalg.norm(v.max(axis=0) - v.min(axis=0))) if len(v) else 0.0
    tol = max(span * WELD, 1e-12)
    _, first, inv = np.unique(np.round((v - v.min(axis=0)) / tol).astype(np.int64), axis=0, return_index=True,
                              return_inverse=True)
    inv = inv.reshape(-1)
    f = inv[f]
    f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
    return v[first], f


def check_closed(v: np.ndarray, f: np.ndarray, path: Path) -> None:
    """A closed, consistently wound surface: every edge is used by exactly two faces, in opposite directions. Only
    then does the mesh hold a volume (an open or mixed-up one's 'volume' depends on where it sits), so only then can
    it be told convex or concave. Raises BenchError."""
    d = np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    und = np.sort(d, axis=1)
    edges, uses = np.unique(und, axis=0, return_counts=True)
    open_edges = int(np.sum(uses != 2))
    directed, dcount = np.unique(d, axis=0, return_counts=True)
    twice = {tuple(e) for e, n in zip(directed, dcount) if n > 1}  # one edge run the same way by two faces
    wound = len({tuple(sorted(e)) for e in twice})
    if open_edges or wound:
        what = ", ".join(x for x in (f"{open_edges} open edges" if open_edges else "",
                                     f"{wound} edges inconsistently wound" if wound else "") if x)
        raise BenchError(f"mesh {path} isn't a closed solid ({what}): export it as a closed solid (a part's body, "
                         "not a surface or a few faces; in Inventor, Save Copy As STL from the part, not a sketch "
                         "or surface)")


def write_obj(path: Path, v: np.ndarray, f: np.ndarray) -> None:
    lines = [f"v {x:.9g} {y:.9g} {z:.9g}" for x, y, z in v] + [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in f]
    Path(path).write_text("\n".join(lines) + "\n")


def volume(v: np.ndarray, f: np.ndarray) -> float:
    """The volume a closed triangle mesh holds (the divergence theorem; either winding)."""
    return abs(float(np.einsum("ij,ij->i", v[f[:, 0]], np.cross(v[f[:, 1]], v[f[:, 2]])).sum()) / 6.0)


def hull_volume(v: np.ndarray, f: np.ndarray, path: Path) -> float:
    """The mesh's convex hull's volume, from MuJoCo's own hull (the shape it would collide as)."""
    spec = mujoco.MjSpec()
    mesh = spec.add_mesh(name="m", uservert=v.ravel().tolist(), userface=f.ravel().tolist())
    mesh.inertia = mujoco.mjtMeshInertia.mjMESH_INERTIA_CONVEX
    spec.worldbody.add_body(name="b").add_geom(type=mujoco.mjtGeom.mjGEOM_MESH, meshname="m", density=1.0)
    try:
        return float(spec.compile().body_mass[1])
    except ValueError as e:
        raise BenchError(f"mesh {path} is flat or broken (MuJoCo: {e}); export the part as a closed solid") from None


def cache_key(mesh_path: Path, units: str) -> str:
    h = hashlib.sha256(Path(mesh_path).read_bytes())
    h.update(f"\0{units}\0{SPLIT_VERSION}\0{sorted(SPLIT.items())}".encode())
    return h.hexdigest()[:16]


def _split(v: np.ndarray, f: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """coacd's convex pieces. Raises ImportError when coacd isn't installed."""
    import coacd  # the optional extra: pip install scrimmage[bench]

    coacd.set_log_level("error")
    out = [(np.asarray(pv, float), np.asarray(pf, int)) for pv, pf in coacd.run_coacd(coacd.Mesh(v, f), **SPLIT)]
    return [(pv, pf) for pv, pf in out if len(pv) >= 4 and len(pf) >= 4]  # a sliver with no volume adds nothing


def _cached(folder: Path) -> list[Path] | None:
    return (sorted(folder.glob("piece_*.obj")) or None) if folder.is_dir() else None


def _write_pieces(folder: Path, pieces) -> list[Path]:
    """Write to a fresh folder beside `folder`, then rename it into place, so a cache folder is always complete."""
    folder.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{folder.name}-", dir=folder.parent))
    try:
        for i, (pv, pf) in enumerate(pieces):
            write_obj(tmp / f"piece_{i:03d}.obj", pv, pf)
        try:
            os.replace(tmp, folder)
        except OSError:
            if _cached(folder) is None:  # not another run that got there first
                raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return _cached(folder)


def convex_pieces(mesh_path: Path, units: str) -> list[Path]:
    """The mesh's convex pieces, as mesh files in the mesh's own units: [mesh_path] itself when it is convex,
    else coacd's split, cached (see the module's notes). `units` keys the cache: it is "m", "mm" or "in"."""
    mesh_path = Path(mesh_path)
    if units not in UNITS:
        raise BenchError(f"units must be one of {', '.join(UNITS)}, not {units!r}")
    v, f = weld(*read_mesh(mesh_path))
    check_closed(v, f, mesh_path)
    hull, own = hull_volume(v, f, mesh_path), volume(v, f)
    if hull <= (1.0 + CONCAVE) * own:
        return [mesh_path]
    key = cache_key(mesh_path, units)
    home = mesh_path.parent / f"{mesh_path.name}.convex" / key
    for folder in (home, TEMP_CACHE / key):
        found = _cached(folder)
        if found:
            return found
    try:
        pieces = _split(v, f)
    except ImportError:
        bigger = f"{100 * (hull / own - 1):.0f}%" if own > 0 else "far"
        raise BenchError(f"mesh {mesh_path} is concave (its hull is {bigger} bigger than it), so it has to be split "
                         "into convex pieces, which needs coacd: pip install scrimmage[bench]. (Run as it is, its "
                         "hollows would be filled in.)") from None
    if not pieces:
        raise BenchError(f"mesh {mesh_path}: coacd found no convex pieces in it")
    try:
        return _write_pieces(home, pieces)
    except OSError as e:
        warnings.warn(f"can't write the convex split of {mesh_path} to {home.parent} ({e}); caching it in "
                      f"{TEMP_CACHE} instead", stacklevel=2)
        return _write_pieces(TEMP_CACHE / key, pieces)


# --------------------------------------------------------------------------------------------------- placement

def _qmul(p, q) -> np.ndarray:
    out = np.zeros(4)
    mujoco.mju_mulQuat(out, np.asarray(p, float), np.asarray(q, float))
    return out


def _rot(q, v) -> np.ndarray:
    out = np.zeros(3)
    mujoco.mju_rotVecQuat(out, np.asarray(v, float), np.asarray(q, float))
    return out


def _conj(q) -> np.ndarray:
    return np.array([q[0], -q[1], -q[2], -q[3]], dtype=float)


@dataclass
class CadWheel:
    """A part's wheel cylinder on a bench shaft."""
    diameter: float
    width: float
    tread: str
    shaft: str


@dataclass
class _Placed:
    body: Body
    pos: np.ndarray   # world
    quat: np.ndarray  # world
    shaft: str | None  # the bench shaft that carries it, None when welded to the bench


@dataclass
class Placement:
    """Where a [[cad]] entry's bodies sit, and the shaft lines its turning joints set."""
    cad: Cad
    part: Part | None = None
    bodies: list[_Placed] = field(default_factory=list)
    anchors: list[tuple[str, str, np.ndarray, np.ndarray]] = field(default_factory=list)  # shaft, label, origin, axis


def place(c: Cad) -> Placement:
    """Load the [[cad]] entry's part (if any) and place its bodies; warns about each joint it welds."""
    out = Placement(c)
    if c.part is None:
        return out
    try:
        part = out.part = load_part(c.part)
    except PartError as e:
        raise BenchError(f"[[cad]] '{c.name}': can't load its part folder {c.part}: {e}") from None
    motor = {s.slot: s for s in part.motors}
    for slot in c.slots:
        if slot not in motor:
            raise BenchError(f"[[cad]] '{c.name}' slots: the part has no motor slot {slot!r}; its motor slots: "
                             f"{', '.join(motor) or '(none)'}")
    driven = {motor[s].joint: (motor[s], shaft) for s, shaft in c.slots.items()}
    unused = {s.joint: s.slot for s in part.motors if s.slot not in c.slots}
    root_q = np.array(quat_from_rpy(c.rot_deg))
    root_p = np.array(c.center, dtype=float)
    placed: dict[str, _Placed] = {}
    for b in part.bodies:
        if b.parent is None:
            pp, pq, carrier = root_p, root_q, None
        else:
            par = placed[b.parent]
            pp, pq, carrier = par.pos, par.quat, par.shaft
        q = _qmul(pq, quat_from_rpy(b.rot))
        p = pp + _rot(pq, b.pos)
        j = b.joint
        if j is not None and j.name in driven:
            slot, shaft = driven[j.name]
            if carrier is not None:
                raise BenchError(f"[[cad]] '{c.name}': joint '{j.name}' (slot '{slot.slot}') rides on a body that "
                                 f"turns with shaft '{carrier}'; a bench shaft turns about a fixed line, so give only "
                                 "the outer joint a shaft")
            if j.kind != "hinge":
                raise BenchError(f"[[cad]] '{c.name}': slot '{slot.slot}' drives joint '{j.name}', a slide; bench "
                                 "shafts turn, so give slots only to hinge joints")
            axis = _rot(q, j.axis) * (-1.0 if slot.invert else 1.0)
            out.anchors.append((shaft, f"[[cad]] '{c.name}' joint '{j.name}'", p, axis / np.linalg.norm(axis)))
            carrier = shaft
        elif j is not None:
            why = (f"its motor slot '{unused[j.name]}' isn't given a shaft in slots" if j.name in unused
                   else "it has no motor slot")
            warnings.warn(f"[[cad]] '{c.name}': joint '{j.name}' is welded (it won't move on the bench): {why}",
                          stacklevel=2)
        placed[b.name] = _Placed(b, p, q, carrier)
    out.bodies = list(placed.values())
    if part.couplings or part.loops:
        warnings.warn(f"[[cad]] '{c.name}': the part's couplings and loops aren't used on the bench (its joints are "
                      "welded or turned by bench shafts; couple shafts with coupled_to)", stacklevel=2)
    return out


def _geom_kw(g, base_dir: Path) -> list[tuple[dict, Path | None]]:
    """MuJoCo geom keywords for a part geom (box, cylinder, capsule, sphere), or one per convex piece of a mesh,
    with each piece's file; positions and turns are the caller's."""
    T = mujoco.mjtGeom
    if g.shape == "box":
        return [({"type": T.mjGEOM_BOX, "size": [s / 2 for s in g.size]}, None)]
    if g.shape in ("cylinder", "capsule"):
        t = T.mjGEOM_CYLINDER if g.shape == "cylinder" else T.mjGEOM_CAPSULE
        return [({"type": t, "size": [g.size[0], g.size[1] / 2, 0.0]}, None)]
    if g.shape == "sphere":
        return [({"type": T.mjGEOM_SPHERE, "size": [g.size[0], 0.0, 0.0]}, None)]
    return [({"type": T.mjGEOM_MESH}, p) for p in convex_pieces(base_dir / g.size, "m")]


def _add_mesh(spec: mujoco.MjSpec, name: str, file: Path, scale: float) -> str:
    mesh = spec.add_mesh(name=name, file=str(file), scale=[scale] * 3)
    mesh.inertia = mujoco.mjtMeshInertia.mjMESH_INERTIA_SHELL  # a shape's mass is never used; a thin piece is fine
    return name


def add_cad(spec: mujoco.MjSpec, pl: Placement, bench: Bench, shapes) -> tuple[dict[str, CadWheel], dict[str, str]]:
    """Add a placed [[cad]] entry's shapes: on the world, or on their shaft's body (built already, at
    `shaft:<name>`). `shapes` is the builder's shape registry (`add`). Returns the part's wheels and every other
    shape's material, by shape name."""
    c, mats = pl.cad, bench.materials
    mu = mats[c.material].rigid_friction
    wheels: dict[str, CadWheel] = {}
    material: dict[str, str] = {}
    if c.mesh is not None:
        try:
            files = convex_pieces(c.mesh, c.units)
        except BenchError as e:
            raise BenchError(f"[[cad]] '{c.name}': {e}") from None
        q = quat_from_rpy(c.rot_deg)
        for i, f in enumerate(files):
            mesh = _add_mesh(spec, f"cad:{c.name}:{i}", f, UNITS[c.units])
            shapes.add(spec.worldbody, f"cad:{c.name}:{i}", c.name, mujoco.mjtGeom.mjGEOM_MESH, (), c.center, q, mu,
                       meshname=mesh)
        material[c.name] = c.material
        return wheels, material

    for pb in pl.bodies:
        b = pb.body
        hits = [g for g in b.geoms if g.collide]
        if not hits:
            continue
        if pb.shaft is None:
            body, bpos, bquat = spec.worldbody, np.zeros(3), np.array([1.0, 0.0, 0.0, 0.0])
        else:
            body = spec.body(f"shaft:{pb.shaft}")
            bpos, bquat = np.array(body.pos, float), np.array(body.quat, float)
            bquat /= np.linalg.norm(bquat)
        shape = f"{c.name}.{b.name}"
        for k, g in enumerate(hits):
            gq = _qmul(pb.quat, quat_from_rpy(g.rot))
            if g.shape == "cylinder":
                gq = _qmul(gq, quat_from_rpy(AXIS_TURN[g.axis]))
            gp = pb.pos + _rot(pb.quat, g.pos)
            # in the carrying body's frame
            lp, lq = _rot(_conj(bquat), gp - bpos), _qmul(_conj(bquat), gq)
            wheel = False
            if pb.shaft is not None and g.shape == "cylinder":
                z = _rot(lq, (0.0, 0.0, 1.0))
                wheel = abs(z[2]) > 1 - 1e-6 and math.hypot(lp[0], lp[1]) < WHEEL_ON_AXIS
            name = shape if len(hits) == 1 or not wheel else f"{shape}.{k}"
            try:
                kws = _geom_kw(g, pl.part.base_dir)
            except BenchError as e:
                raise BenchError(f"[[cad]] '{c.name}' body '{b.name}': {e}") from None
            for i, (kw, file) in enumerate(kws):
                gname = f"cad:{c.name}:{b.name}:{k}:{i}"
                if file is not None:
                    kw["meshname"] = _add_mesh(spec, gname, file, 1.0)
                gtype, size = kw.pop("type"), kw.pop("size", ())
                if wheel:
                    shapes.add(body, gname, name, gtype, size, lp, lq, mats[c.tread].rigid_friction,
                               k_shape=TREAD_STIFFNESS, **kw)
                    wheels[name] = CadWheel(diameter=2 * g.size[0], width=g.size[1], tread=c.tread, shaft=pb.shaft)
                else:
                    shapes.add(body, gname, name, gtype, size, lp, lq, mu, **kw)
                    material[name] = c.material
    return wheels, material
