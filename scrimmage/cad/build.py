"""Building part folders from an import: `build_parts(imp, src)` makes the parts in memory, `write_parts` saves them.

Bodies. A body welded to its parent (no joint) is merged into it: one part body per part root or jointed body, named
after it, holding the pieces of the bodies welded to it (their visual mesh, mass and collision). Each body frame is
aligned with the robot frame (`rot` zero). A part root's origin is the robot origin (the floor at the frame's
center); a jointed body's origin is its joint anchor, mapped by `to_robot`. So a part attached to the stock chassis's
root body at zero offset sits where the CAD has it. A body under a part root belongs to that part, not to the part
its parent is in; a part root that still names a parent drops its joint, and the robot config attaches it instead.

Mass. Each piece left in gives a mass from its material's density, or a typed weight (which keeps the piece's center
of mass and scales its inertia). A typed body weight scales that body's own pieces together. A part's total weight
then scales every computed (not typed) mass so the part weighs that much. A part body that still weighs nothing gets
a 0.001 kg placeholder and a message. `com`, `inertia` (principal moments) and `inertia_rot` are always written, so
the meshes only draw and collide.

Geoms. One visual mesh per part body, `meshes/<body>.obj`, `collide = false`, in the body frame. Collision is set per
import body over its own pieces: `wheel` (a cylinder about the body's hinge, friction 1.2, condim 4), `shaped` (the
mesh split into convex pieces by `bench.cad.convex_pieces`; without coacd, a message and a box), `simple` (a box
fitted to the pieces, `fit.fit_box`), or `none`.

Slots: `<body>_motor`, `<body>_encoder`, `<body>_gyro`. Mounts, couplings and loops map one to one, with points in
their part body's frame. Problems that don't stop the build come back as plain-words messages.

Folders. Each part goes to its `folder` (`{cad}/...` expanded, else relative to the import file's folder), written
to `<folder>.tmp` and swapped in, with an `import-origin.toml` marker: the import file's relative path, the
import_id, and each body's origin in the robot frame. A folder holding files but no marker for this import is
refused.
"""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import tempfile
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import teamfolders
from ..bench.cad import convex_pieces, read_mesh, write_obj
from ..bench.spec import BenchError
from ..parts.model import (Body, Coupling, EncoderSlot, Geom, GyroSlot, Joint, Loop, MotorSlot, Mount, Part,
                           PartError, dump_part, load_part)
from ..parts.toml_writer import _toml_value
from . import CadError
from .fit import fit_box, fit_wheel, rot_z_to
from .frame import to_robot
from .mass import Inertial, combine, density, mesh_inertial, principal, rpy_deg
from .settings import ImportFile, _key
from .source import Source

PLACEHOLDER_KG = 0.001
WHEEL_FRICTION = 1.2
WHEEL_CONDIM = 4
SMALL_INERTIA = 0.4 * 0.05 ** 2     # x mass: a 5 cm solid sphere, as the assembler uses for a mass with no shape
MARKER = "import-origin.toml"
SHAPED_CACHE = Path(tempfile.gettempdir()) / "scrimmage-cad-shaped"


@dataclass
class BuiltPart:
    part: Part
    meshes: dict[str, tuple[np.ndarray, np.ndarray]]       # file name in the folder -> (v, f), body frame, meters
    origins: dict[str, list[float]]                         # body -> its origin in the robot frame
    com: list[float]                                        # the part's center of mass, robot frame
    mass: float


@dataclass
class BuildResult:
    parts: dict[str, BuiltPart] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)


def _floats(x) -> list[float]:
    return [float(v) for v in x]


def _box_inertial(v: np.ndarray, mass: float, at=None) -> Inertial:
    """A solid box's inertial over v's bounding box (for a weight typed on something with no volume)."""
    if len(v) == 0:
        return Inertial(mass, np.zeros(3) if at is None else np.asarray(at, float), SMALL_INERTIA * mass * np.eye(3))
    lo, hi = v.min(axis=0), v.max(axis=0)
    a, b, c = np.maximum(hi - lo, 0.01)
    t = mass / 12 * np.diag([b * b + c * c, a * a + c * c, a * a + b * b])
    return Inertial(mass, (lo + hi) / 2, t)


def _scaled(i: Inertial, k: float) -> Inertial:
    return Inertial(i.mass * k, i.com, i.tensor * k)


class _Tree:
    """The import's bodies grouped into part bodies (`head`) and parts (`part_of`)."""

    def __init__(self, imp: ImportFile, messages: list[str]):
        self.imp = imp
        b = imp.bodies
        self.roots = [n for n, s in b.items() if s.part is not None or s.parent not in b]
        for n in self.roots:
            if b[n].parent in b:
                messages.append(f"{n} is its own part; attach it to {b[n].parent} in the robot")
        self._head: dict[str, str] = {}

    def is_root(self, n: str) -> bool:
        return n in self.roots

    def head(self, n: str) -> str:
        """The part body `n`'s pieces go in: itself if it is a part root or has a joint, else its parent's."""
        if n not in self._head:
            seen, cur = [], n
            while not (self.is_root(cur) or self.imp.bodies[cur].joint is not None):
                if cur in seen:
                    raise CadError(f"{cur} hangs on itself through other bodies: pick a different parent for it.")
                seen.append(cur)
                cur = self.imp.bodies[cur].parent
            self._head[n] = cur
        return self._head[n]

    def parent_head(self, h: str) -> str | None:
        return None if self.is_root(h) else self.head(self.imp.bodies[h].parent)

    def part_root(self, n: str) -> str:
        h = self.head(n)
        while not self.is_root(h):
            h = self.parent_head(h)
        return h

    def part_name(self, root: str) -> str:
        return self.imp.bodies[root].part or root


def build_parts(imp: ImportFile, src: Source) -> BuildResult:
    """The parts `imp` makes from `src`, in memory, and messages about anything that couldn't be built as asked."""
    msgs: list[str] = []
    R, t = to_robot(imp.frame)
    tree = _Tree(imp, msgs)
    by_key = {p.key: p for p in src.pieces}
    heads = [n for n in imp.bodies if tree.head(n) == n]
    origin = {h: np.zeros(3) if tree.is_root(h) else R @ np.asarray(imp.bodies[h].joint.anchor, float) + t
              for h in heads}

    # pieces in robot coordinates, and their inertials, per import body
    verts: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {n: [] for n in imp.bodies}
    items: dict[str, list[tuple[Inertial, bool]]] = {h: [] for h in heads}       # (inertial, typed)
    for key, ps in imp.pieces.items():
        if ps.left_out or ps.body not in imp.bodies:
            continue
        p = by_key.get(key)
        if p is None:
            msgs.append(f"{key} is no longer in the CAD file: import it again to update the settings")
            continue
        verts[ps.body].append((p.v @ R.T + t, np.asarray(p.f)))
    for name, bs in imp.bodies.items():
        own = []
        for key, ps in imp.pieces.items():
            if ps.body != name or ps.left_out or key not in by_key:
                continue
            v = by_key[key].v @ R.T + t
            try:
                rho = density(ps.material, ps.infill, ps.custom_density)
            except CadError as e:
                msgs.append(f"{key}: {e} It counts as aluminum until then.")
                rho = density("aluminum")
            i = mesh_inertial(v, by_key[key].f, rho)
            if ps.mass:
                i = _scaled(i, ps.mass / i.mass) if i.mass > 0 else _box_inertial(v, ps.mass)
            own.append((i, bool(ps.mass)))
        if bs.mass:
            body = combine([i for i, _ in own])
            allv = np.concatenate([v for v, _ in verts[name]]) if verts[name] else np.zeros((0, 3))
            own = [(_scaled(body, bs.mass / body.mass) if body.mass > 0 else _box_inertial(allv, bs.mass,
                                                                                          origin[tree.head(name)]),
                    True)]
        items[tree.head(name)] += own

    result = BuildResult(messages=msgs)
    for root in tree.roots:
        result.parts[tree.part_name(root)] = _build_part(imp, tree, root, heads, origin, verts, items, R, t, msgs)
    return result


def _build_part(imp, tree, root, heads, origin, verts, items, R, t, msgs) -> BuiltPart:
    name = tree.part_name(root)
    members = [h for h in heads if tree.part_root(h) == root]
    order: list[str] = []

    def visit(h):
        order.append(h)
        for c in members:
            if c not in order and tree.parent_head(c) == h:
                visit(c)
    visit(root)

    # the total weight scales the computed masses
    ps = imp.parts.get(name)
    total = ps.total_mass if ps is not None else None
    if total:
        typed = sum(i.mass for h in order for i, ty in items[h] if ty)
        computed = sum(i.mass for h in order for i, ty in items[h] if not ty)
        if typed > total:
            msgs.append(f"The weights typed in {name} add up to {typed:.3g} kg, more than its total of {total:.3g} "
                        "kg: lower a typed weight or raise the total.")
        elif computed > 0:
            k = (total - typed) / computed
            for h in order:
                items[h] = [(i if ty else _scaled(i, k), ty) for i, ty in items[h]]
        elif typed < total:
            msgs.append(f"Nothing in {name} gets its weight from a material, so it can't be scaled to its total of "
                        f"{total:.3g} kg: type the weights instead.")

    bodies, meshes, inertials = [], {}, []
    motors, encoders, gyros, mounts = [], [], [], []
    for h in order:
        o = origin[h]
        bs = imp.bodies[h]
        members_of = [n for n in imp.bodies if tree.head(n) == h]
        geoms: list[Geom] = []
        vs = [(v - o, f) for n in members_of for v, f in verts[n]]
        if vs:
            meshes[f"meshes/{h}.obj"] = _join(vs)
            geoms.append(Geom("mesh", f"meshes/{h}.obj", collide=False))
        for n in members_of:
            geoms += _collision(imp, n, h, [(v - o, f) for v, f in verts[n]], meshes, R, msgs)

        inertial = combine([i for i, _ in items[h]])
        if inertial.mass > 0:
            moments, axes = principal(inertial.tensor)
            mass, com = inertial.mass, inertial.com
            inertia = _floats(np.maximum(moments, 1e-12))
            inertia_rot = tuple(float(x) for x in rpy_deg(axes))
            inertials.append(inertial)
        else:
            msgs.append(f"{h} weighs nothing (its pieces have no volume or are left out), so it gets a "
                        f"{PLACEHOLDER_KG} kg placeholder: type a weight for it.")
            mass = PLACEHOLDER_KG
            com = meshes[f"meshes/{h}.obj"][0].mean(axis=0) + o if vs else o
            inertia, inertia_rot = [SMALL_INERTIA * mass] * 3, None
            inertials.append(Inertial(mass, com, SMALL_INERTIA * mass * np.eye(3)))

        joint = None
        parent = tree.parent_head(h)
        if parent is not None:
            j = bs.joint
            joint = Joint(h, j.kind, tuple(_floats(_unit(R @ np.asarray(j.axis, float)))), j.range, j.damping,
                          j.frictionloss, spring=j.spring)
            motor, encoder = _slots(h, bs, msgs)
            motors += motor
            encoders += encoder
        for n in members_of:
            nb = imp.bodies[n]
            if nb.gyro:
                gyros.append(GyroSlot(f"{n}_gyro", h))
            for m in nb.mounts:
                mounts.append(Mount(m.name, h, tuple(_floats(R @ np.asarray(m.point, float) + t - o)),
                                    rot_z_to(R @ np.asarray(m.normal, float))))
        bodies.append(Body(h, parent, float(mass), geoms, tuple(_floats(o - origin[parent])) if parent else
                           (0.0, 0.0, 0.0), joint=joint, com=tuple(_floats(np.asarray(com) - o)),
                           inertia=tuple(inertia), inertia_rot=inertia_rot))

    jointed = {b.name for b in bodies if b.joint is not None}
    couplings = []
    for c in imp.couplings:
        a, b = c.get("a"), c.get("b")
        if not any(x in imp.bodies and tree.part_root(x) == root for x in (a, b)):
            continue                            # another part's
        if a in jointed and b in jointed:
            couplings.append(Coupling((a, b), float(c["ratio"])))
        elif a not in jointed or tree.part_root(a) == root:     # said once, by the part that holds `a` (or `b`)
            msgs.append(f"The coupling between {a} and {b} was left out: both need joints in the same part.")
    loops = []
    for lp in imp.loops:
        b1, b2 = lp.get("body1"), lp.get("body2")
        if b1 not in imp.bodies or b2 not in imp.bodies or tree.part_root(b1) != root:
            continue
        h1, h2 = tree.head(b1), tree.head(b2)
        if tree.part_root(b2) != root or h1 == h2:
            msgs.append(f"The loop between {b1} and {b2} was left out: they must be in the same part and not "
                        "welded together.")
            continue
        loops.append(Loop(h1, h2, tuple(_floats(R @ np.asarray(lp["point"], float) + t - origin[h1]))))

    whole = combine(inertials)
    part = Part(name, bodies, couplings, loops, motors, encoders, gyros, mounts)
    return BuiltPart(part, meshes, {h: _floats(origin[h]) for h in order}, _floats(whole.com), float(whole.mass))


def _unit(a: np.ndarray) -> np.ndarray:
    return a / np.linalg.norm(a)


def _join(vs: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    out_v, out_f, n = [], [], 0
    for v, f in vs:
        out_v.append(v)
        out_f.append(np.asarray(f) + n)
        n += len(v)
    return np.concatenate(out_v), np.concatenate(out_f).astype(np.int64)


def _slots(h, bs, msgs) -> tuple[list[MotorSlot], list[EncoderSlot]]:
    j, m, e = bs.joint, bs.motor, bs.encoder
    slide = j.kind == "slide"
    if slide and m is not None and not m.spool_diameter:
        msgs.append(f"{h}'s motor was left out: a motor on a slide needs a spool diameter, so enter one.")
        m = None
    motors = [] if m is None else [MotorSlot(f"{h}_motor", h, m.motor, m.count, float(m.ratio / (m.spool_diameter / 2)
                                             if slide else m.ratio), m.invert, m.efficiency)]
    encoders = []
    if e is not None:
        if e.on == "joint":
            ratio = 1.0
        elif bs.motor is None or (slide and not bs.motor.spool_diameter):
            msgs.append(f"{h}'s encoder is on the motor, but {h} has no motor: it reads the joint instead.")
            ratio = 1.0
        else:
            ratio = bs.motor.ratio / (math.pi * bs.motor.spool_diameter) if slide else bs.motor.ratio
        encoders.append(EncoderSlot(f"{h}_encoder", h, float(ratio), e.invert))
    return motors, encoders


def _collision(imp, n, h, vs, meshes, R, msgs) -> list[Geom]:
    """Body `n`'s collision geoms in part body `h`'s frame (vs: n's pieces in that frame)."""
    mode = imp.bodies[n].collision
    if mode == "none" or not vs:
        return []
    v, f = _join(vs)
    if mode == "wheel":
        j = imp.bodies[n].joint
        if n == h and j is not None and j.kind == "hinge":
            axis = _unit(R @ np.asarray(j.axis, float))
            radius, length, center = fit_wheel(v, axis)
            g = Geom("cylinder", (radius, length), pos=tuple(_floats(center)), friction=WHEEL_FRICTION,
                     condim=WHEEL_CONDIM)
            big = int(np.argmax(np.abs(axis)))
            if abs(axis[big]) > 1 - 1e-9:
                g.axis = "xyz"[big]
            else:
                g.rot = rot_z_to(axis)
            return [g]
        msgs.append(f"{n} is set to touch things as a wheel, but a wheel needs a hinge (it turns): give {n} a hinge. "
                    "It touches things as a simple box until then.")
    elif mode == "shaped":
        try:
            pieces = _convex(v, f)
        except BenchError as e:
            if "coacd" in str(e):
                msgs.append(f"{n} is concave, and touching things as shaped needs coacd to split it: pip install "
                            "scrimmage[bench]. It touches things as a simple box until then.")
            else:
                msgs.append(f"{n} can't touch things as shaped because its pieces aren't closed solids: "
                            "pick simple instead. It touches things as a simple box until then.")
        else:
            out = []
            for i, (pv, pf) in enumerate(pieces):
                path = f"meshes/{n}_collide_{i:03d}.obj"
                meshes[path] = (pv, pf)
                out.append(Geom("mesh", path))
            return out
    size, center, rpy = fit_box(v)
    return [Geom("box", tuple(size), pos=tuple(center), rot=rpy)]


def _convex(v: np.ndarray, f: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """The mesh's convex pieces (meters). The mesh is written to a cache file named by its content, so
    convex_pieces's own cache (beside it) saves re-splitting the same body on every rebuild."""
    lines = [f"v {x:.9g} {y:.9g} {z:.9g}" for x, y, z in v] + [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in f]
    text = "\n".join(lines) + "\n"
    SHAPED_CACHE.mkdir(parents=True, exist_ok=True)
    path = SHAPED_CACHE / f"{hashlib.sha256(text.encode()).hexdigest()[:16]}.obj"
    if not path.is_file():
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}")
        tmp.write_text(text)
        os.replace(tmp, path)
    return [read_mesh(p) for p in convex_pieces(path, "m")]


# ---------------------------------------------------------------- writing

def part_folder(imp: ImportFile, part: str, import_path: Path) -> Path:
    """Where part `part` is saved: its folder with {cad}/{code} expanded, else relative to the import file's folder."""
    ps = imp.parts.get(part)
    text = ps.folder if ps is not None else part
    try:
        text = teamfolders.expand(text)
    except teamfolders.TeamFolderError as e:
        raise CadError(f"Can't save {part}: {e}.") from None
    p = Path(text)
    return p if p.is_absolute() else Path(import_path).parent / p


def _check_target(folder: Path, imp: ImportFile) -> None:
    shown = f"{folder.parent.name}/{folder.name}"
    refuse = CadError(f"{shown} already holds a part that didn't come from this import; pick another name")
    if folder.is_file():
        raise refuse
    if not folder.is_dir() or not any(folder.iterdir()):
        return
    try:
        marker = tomllib.loads((folder / MARKER).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise refuse from None
    if marker.get("import_id") != imp.import_id:
        raise refuse


def _marker_text(imp: ImportFile, built: BuiltPart, folder: Path, import_path: Path) -> str:
    try:
        rel = os.path.relpath(Path(import_path).resolve(), folder.resolve()).replace("\\", "/")
    except ValueError:              # Windows: another drive
        rel = Path(import_path).resolve().as_posix()
    origins = ", ".join(f"{_key(b)} = [{', '.join(repr(float(x)) for x in o)}]" for b, o in built.origins.items())
    return (f"import = {_toml_value(rel)}\nimport_id = {_toml_value(imp.import_id)}\n"
            f"origins = {{ {origins} }}\n")


def write_parts(result: BuildResult, imp: ImportFile, import_path: Path) -> list[Path]:
    """Save each built part to its folder (see the module's notes); returns the folders. Raises CadError."""
    targets = {name: part_folder(imp, name, import_path) for name in result.parts}
    for folder in targets.values():
        _check_target(folder, imp)
    out = []
    for name, built in result.parts.items():
        folder = targets[name]
        tmp = folder.with_name(folder.name + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            (tmp / "meshes").mkdir(parents=True)
            for path, (v, f) in built.meshes.items():
                write_obj(tmp / path, v, f)
            dump_part(built.part, tmp / "part.toml")
            (tmp / MARKER).write_text(_marker_text(imp, built, folder, import_path), encoding="utf-8")
            load_part(tmp)                      # never swap in a folder that doesn't load
        except PartError as e:
            shutil.rmtree(tmp, ignore_errors=True)
            raise CadError(f"The part {name} came out wrong ({e}); this is a bug in Scrimmage: please report it.") \
                from None
        except OSError as e:
            shutil.rmtree(tmp, ignore_errors=True)
            raise CadError(f"Couldn't write the part folder {folder} ({e.strerror or e}): check the folder can be "
                           "written and try again.") from None
        _install(tmp, folder)
        out.append(folder)
    return out


def _install(tmp: Path, folder: Path) -> None:
    """Rename the finished `tmp` folder to `folder`, replacing the old one (as the FreeCAD add-on's export does). A
    rename the OS refuses (on Windows: a folder open in Explorer or an editor) is a CadError; the old folder stays."""
    old = None
    try:
        if folder.exists():
            old = folder.with_name(f".{folder.name}.old-{uuid.uuid4().hex[:8]}")
            os.rename(folder, old)
        try:
            os.rename(tmp, folder)
        except BaseException:
            if old is not None:
                os.rename(old, folder)
            raise
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise CadError(f"Couldn't replace the part folder {folder} ({e.strerror or e}): close anything using it "
                       "(Explorer, an editor) and try again.") from None
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)
