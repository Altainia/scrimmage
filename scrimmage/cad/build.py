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
import_id, each body's origin in the robot frame, and `attach = "<part>.<body>"` when the part hangs on a body of
another part of this import in the CAD (its root's parent, or the body it hung on when it was made a part; left out
for a part the robot itself carries). The folder's own edits (scrimmage-edits.toml, parts/overlay.py: a grip
material, a mass, ...) are carried into the new folder, so they apply to an imported part as to a plugin-made one.
A folder holding files but no marker for this import is refused. The import file records the folders each build
wrote (`written`); a recorded folder this build doesn't write is removed (while its marker and part.toml still say
it is that part from this import), so a robot can't count those bodies twice. Nothing else is ever deleted: a copy
of a part folder is safe.

A hand-edited import file whose bodies hang on each other in a ring, or with a joint axis or mount normal of no
direction, is a CadError in plain words (never a hang or a NaN in a file).
"""

from __future__ import annotations

import math
import os
import shutil
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import teamfolders
from ..i18n import t, tn
from ..bench.cad import write_obj
from ..bench.spec import BenchError
from ..parts.model import (Body, Coupling, EncoderSlot, Geom, GyroSlot, Joint, Loop, MotorSlot, Mount, Part,
                           PartError, dump_part, load_part)
from ..parts.toml_writer import _toml_value
from . import CadError
from .fit import WHEEL_CONDIM, WHEEL_FRICTION, convex_split, fit_box, fit_wheel, rot_z_to
from .frame import to_robot
from .mass import Inertial, combine, density, mesh_inertial, principal, rpy_deg
from .settings import ImportFile, _key
from .source import Source

PLACEHOLDER_KG = 0.001
SMALL_INERTIA = 0.4 * 0.05 ** 2     # x mass: a 5 cm solid sphere, as the assembler uses for a mass with no shape
MARKER = "import-origin.toml"


@dataclass
class BuiltPart:
    part: Part
    meshes: dict[str, tuple[np.ndarray, np.ndarray]]       # file name in the folder -> (v, f), body frame, meters
    origins: dict[str, list[float]]                         # body -> its origin in the robot frame
    com: list[float]                                        # the part's center of mass, robot frame
    mass: float
    attach: str | None = None                               # "<part>.<body>" it hangs on in the CAD; None: the robot


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
    tensor = mass / 12 * np.diag([b * b + c * c, a * a + c * c, a * a + b * b])
    return Inertial(mass, (lo + hi) / 2, tensor)


def _scaled(i: Inertial, k: float) -> Inertial:
    return Inertial(i.mass * k, i.com, i.tensor * k)


class _Tree:
    """The import's bodies grouped into part bodies (`head`) and parts (`part_of`)."""

    def __init__(self, imp: ImportFile, messages: list[str]):
        self.imp = imp
        b = imp.bodies
        _no_cycles(imp)
        self.roots = [n for n, s in b.items() if s.part is not None or s.parent not in b]
        for n in self.roots:
            if b[n].parent in b:
                messages.append(Note(t("{body} is its own part; attach it to {parent} in the robot", body=n,
                                       parent=b[n].parent), ("body", n)))
                gone = [w for w, has in ((t("joint"), b[n].joint), (t("motor"), b[n].motor),
                                         (t("encoder"), b[n].encoder)) if has]
                if gone:
                    messages.append(Note(tn("{body}'s {things} was dropped, because the first body of a part can't move "
                                            "on its own", "{body}'s {things} were dropped, because the first body of a "
                                            "part can't move on its own", len(gone), body=n, things=_and(gone)),
                                         ("body", n)))
        self._head: dict[str, str] = {}

    def is_root(self, n: str) -> bool:
        return n in self.roots

    def head(self, n: str) -> str:
        """The part body `n`'s pieces go in: itself if it is a part root or has a joint, else its parent's."""
        if n not in self._head:
            cur = n
            while not (self.is_root(cur) or self.imp.bodies[cur].joint is not None):
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


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else t("{names} and {last}", names=", ".join(words[:-1]), last=words[-1])


class Note(str):
    """A build message, in plain words, that knows what it is about, so the checklist doesn't read its words (spec
    L3): `about` is ("body" | "piece" | "part", name), or None for the robot frame; `error`: the part is wrong."""
    about: tuple[str, str] | None
    error: bool

    def __new__(cls, text: str, about: tuple[str, str] | None = None, error: bool = False):
        s = super().__new__(cls, text)
        s.about, s.error = about, error
        return s


def _no_cycles(imp: ImportFile) -> None:
    """A CadError when bodies hang on each other in a ring (only a hand-edited import file can say that)."""
    b = imp.bodies
    for start in b:
        path, cur = [], start
        while cur in b and cur not in path:
            path.append(cur)
            cur = b[cur].parent
        if cur in path:
            ring = path[path.index(cur):]
            raise CadError(f"{_and(ring)} hang on each other; give one of them another parent." if len(ring) > 1
                           else f"{cur} hangs on itself; give it another parent.")


def _direction(v, what: str) -> np.ndarray:
    """v as a unit vector, or a CadError saying `what` has no direction (never a NaN in a file)."""
    a = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(a)) if a.shape == (3,) and np.all(np.isfinite(a)) else 0.0
    if not n > 1e-12:
        raise CadError(f"{what} has no direction: set it again.")
    return a / n


def _point(v, what: str) -> np.ndarray:
    a = np.asarray(v, dtype=float)
    if a.shape != (3,) or not np.all(np.isfinite(a)):
        raise CadError(f"{what} isn't a point (it needs three numbers): set it again.")
    return a


def _check_geometry(imp: ImportFile) -> None:
    for n, b in imp.bodies.items():
        if b.joint is not None:
            _direction(b.joint.axis, f"{n}'s joint axis")
            _point(b.joint.anchor, f"{n}'s joint anchor")
        for m in b.mounts:
            _direction(m.normal, f"{n}'s mount {m.name}")
            _point(m.point, f"{n}'s mount {m.name}")


def _coupling_messages(imp: ImportFile, tree: "_Tree", msgs: list[str]) -> None:
    """One message per coupling that can't be built (both bodies must move on joints in the same part)."""
    def moves(x):
        return x in imp.bodies and tree.head(x) == x and not tree.is_root(x)
    for c in imp.couplings:
        a, b = c.get("a"), c.get("b")
        if not (moves(a) and moves(b) and tree.part_root(a) == tree.part_root(b)):
            msgs.append(Note(t("The coupling between {a} and {b} was left out: both need joints in the same part.",
                               a=a, b=b)))


def build_parts(imp: ImportFile, src: Source) -> BuildResult:
    """The parts `imp` makes from `src`, in memory, and messages about anything that couldn't be built as asked."""
    msgs: list[str] = []
    R, shift = to_robot(imp.frame)          # robot = R @ CAD + shift
    _check_geometry(imp)
    tree = _Tree(imp, msgs)
    _coupling_messages(imp, tree, msgs)
    by_key = {p.key: p for p in src.pieces}
    heads = [n for n in imp.bodies if tree.head(n) == n]
    origin = {h: np.zeros(3) if tree.is_root(h) else R @ np.asarray(imp.bodies[h].joint.anchor, float) + shift
              for h in heads}

    # pieces in robot coordinates, and their inertials, per import body
    verts: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {n: [] for n in imp.bodies}
    items: dict[str, list[tuple[Inertial, bool]]] = {h: [] for h in heads}       # (inertial, typed)
    for key, ps in imp.pieces.items():
        if ps.left_out or ps.body not in imp.bodies:
            continue
        p = by_key.get(key)
        if p is None:
            msgs.append(Note(t("{piece} is no longer in the CAD file: import it again to update the settings",
                               piece=key), ("piece", key)))
            continue
        verts[ps.body].append((p.v @ R.T + shift, np.asarray(p.f)))
    for name, bs in imp.bodies.items():
        own = []
        for key, ps in imp.pieces.items():
            if ps.body != name or ps.left_out or key not in by_key:
                continue
            v = by_key[key].v @ R.T + shift
            try:
                rho = density(ps.material or "aluminum", ps.infill, ps.custom_density)
            except CadError as e:
                msgs.append(Note(t("{piece}: {problem} It counts as aluminum until then.", piece=key, problem=e),
                                 ("piece", key)))
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
        result.parts[tree.part_name(root)] = _build_part(imp, tree, root, heads, origin, verts, items, R, shift, msgs)
    return result


def _build_part(imp, tree, root, heads, origin, verts, items, R, shift, msgs) -> BuiltPart:
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
    # where it hangs in the CAD: the root's own parent, else the body it hung on when it was made a part
    hang = imp.bodies[root].parent if imp.bodies[root].parent in imp.bodies else ps.attach if ps is not None else None
    attach = None
    if hang in imp.bodies and tree.part_root(hang) != root:
        attach = f"{tree.part_name(tree.part_root(hang))}.{tree.head(hang)}"
    if total:
        typed = sum(i.mass for h in order for i, ty in items[h] if ty)
        computed = sum(i.mass for h in order for i, ty in items[h] if not ty)
        if typed > total:
            msgs.append(Note(t("The weights typed in {part} add up to {typed} kg, more than its total of {total} kg: "
                               "lower a typed weight or raise the total.", part=name, typed=f"{typed:.3g}",
                               total=f"{total:.3g}"), ("part", name)))
        elif typed == total and computed > 0:
            msgs.append(Note(t("The weights typed in {part} already reach its total of {total} kg, so the rest keep "
                               "their weight from their material: lower a typed weight or raise the total.", part=name,
                               total=f"{total:.3g}"), ("part", name)))
        elif computed > 0:
            k = (total - typed) / computed
            for h in order:
                items[h] = [(i if ty else _scaled(i, k), ty) for i, ty in items[h]]
        elif typed < total:
            msgs.append(Note(t("Nothing in {part} gets its weight from a material, so it can't be scaled to its total "
                               "of {total} kg: type the weights instead.", part=name, total=f"{total:.3g}")))

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
            msgs.append(Note(t("{body} weighs nothing (its pieces have no volume or are left out), so it gets a "
                               "{kg} kg placeholder: type a weight for it.", body=h, kg=PLACEHOLDER_KG),
                             ("body", h), error=True))
            mass = PLACEHOLDER_KG
            com = meshes[f"meshes/{h}.obj"][0].mean(axis=0) + o if vs else o
            inertia, inertia_rot = [SMALL_INERTIA * mass] * 3, None
            inertials.append(Inertial(mass, com, SMALL_INERTIA * mass * np.eye(3)))

        joint = None
        parent = tree.parent_head(h)
        if parent is not None:
            j = bs.joint
            joint = Joint(h, j.kind, tuple(_floats(R @ _direction(j.axis, h))), j.range, j.damping,
                          j.frictionloss, spring=j.spring)
            motor, encoder = _slots(h, bs, msgs)
            motors += motor
            encoders += encoder
        for n in members_of:
            nb = imp.bodies[n]
            if nb.gyro:
                gyros.append(GyroSlot(f"{n}_gyro", h))
            for m in nb.mounts:
                mounts.append(Mount(m.name, h, tuple(_floats(R @ np.asarray(m.point, float) + shift - o)),
                                    rot_z_to(R @ _direction(m.normal, m.name))))
        bodies.append(Body(h, parent, float(mass), geoms, tuple(_floats(o - origin[parent])) if parent else
                           (0.0, 0.0, 0.0), joint=joint, com=tuple(_floats(np.asarray(com) - o)),
                           inertia=tuple(inertia), inertia_rot=inertia_rot))

    jointed = {b.name for b in bodies if b.joint is not None}
    couplings = []
    for c in imp.couplings:
        a, b = c.get("a"), c.get("b")
        if a in jointed and b in jointed:     # the ones left out were said once, by _coupling_messages
            couplings.append(Coupling((a, b), float(c["ratio"])))
    loops = []
    for lp in imp.loops:
        b1, b2 = lp.get("body1"), lp.get("body2")
        if b1 not in imp.bodies or b2 not in imp.bodies or tree.part_root(b1) != root:
            continue
        h1, h2 = tree.head(b1), tree.head(b2)
        if tree.part_root(b2) != root or h1 == h2:
            msgs.append(Note(t("The loop between {a} and {b} was left out: they must be in the same part and not "
                               "welded together.", a=b1, b=b2)))
            continue
        loops.append(Loop(h1, h2, tuple(_floats(R @ np.asarray(lp["point"], float) + shift - origin[h1]))))

    whole = combine(inertials)
    part = Part(name, bodies, couplings, loops, motors, encoders, gyros, mounts)
    return BuiltPart(part, meshes, {h: _floats(origin[h]) for h in order}, _floats(whole.com), float(whole.mass),
                     attach)


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
        msgs.append(Note(t("{body}'s motor was left out: a motor on a slide needs a spool diameter, so enter one.",
                           body=h), ("body", h)))
        m = None
    motors = [] if m is None else [MotorSlot(f"{h}_motor", h, m.motor, m.count, float(m.ratio / (m.spool_diameter / 2)
                                             if slide else m.ratio), m.invert, m.efficiency)]
    encoders = []
    if e is not None:
        if e.on == "joint":
            ratio = 1.0
        elif bs.motor is None or (slide and not bs.motor.spool_diameter):
            msgs.append(Note(t("{body}'s encoder is on the motor, but {body} has no motor: it reads the joint instead.",
                               body=h), ("body", h)))
            ratio = 1.0
        else:
            ratio = bs.motor.ratio / (math.pi * bs.motor.spool_diameter) if slide else bs.motor.ratio
        encoders.append(EncoderSlot(f"{h}_encoder", h, float(ratio), e.invert))
    return motors, encoders


def _bench_code(message: str) -> str | None:
    """The code of a BenchError raised without one (bench/cad.py gives its own): read from its American English."""
    if "needs coacd" in message:
        return "no_coacd"
    return "no_convex_pieces" if "no convex pieces" in message else None


def _collision(imp, n, h, vs, meshes, R, msgs) -> list[Geom]:
    """Body `n`'s collision geoms in part body `h`'s frame (vs: n's pieces in that frame)."""
    mode = imp.bodies[n].collision
    if mode == "none" or not vs:
        return []
    v, f = _join(vs)
    if mode == "wheel":
        j = imp.bodies[n].joint
        if n == h and j is not None and j.kind == "hinge":
            axis = R @ _direction(j.axis, n)
            radius, length, center = fit_wheel(v, axis)
            g = Geom("cylinder", (radius, length), pos=tuple(_floats(center)), friction=WHEEL_FRICTION,
                     condim=WHEEL_CONDIM)
            big = int(np.argmax(np.abs(axis)))
            if abs(axis[big]) > 1 - 1e-9:
                g.axis = "xyz"[big]
            else:
                g.rot = rot_z_to(axis)
            return [g]
        msgs.append(Note(t("{body} is set to touch things as a wheel, but a wheel needs a hinge (it turns): give "
                           "{body} a hinge. It touches things as a simple box until then.", body=n), ("body", n)))
    elif mode == "shaped":
        try:
            pieces = convex_split(v, f)
        except BenchError as e:
            code = e.code or _bench_code(str(e))
            if code == "no_coacd":
                text = t("{body} is concave, and touching things as shaped needs coacd to split it: pip install "
                         "scrimmage[bench]. It touches things as a simple box until then.", body=n)
            elif code == "no_convex_pieces":
                text = t("{body} couldn't be split into convex pieces to touch things as shaped: pick simple instead. "
                         "It touches things as a simple box until then.", body=n)
            else:
                text = t("{body} can't touch things as shaped because its pieces aren't closed solids: pick simple "
                         "instead. It touches things as a simple box until then.", body=n)
            msgs.append(Note(text, ("body", n)))
        else:
            out = []
            for i, (pv, pf) in enumerate(pieces):
                path = f"meshes/{n}_collide_{i:03d}.obj"
                meshes[path] = (pv, pf)
                out.append(Geom("mesh", path))
            return out
    size, center, rpy = fit_box(v)
    return [Geom("box", tuple(size), pos=tuple(center), rot=rpy)]


# ---------------------------------------------------------------- writing

def part_folder(imp: ImportFile, part: str, import_path: Path) -> Path:
    """Where part `part` is saved: its folder with {cad}/{code} expanded, else relative to the import file's folder."""
    return _resolve(_folder_text(imp, part), import_path)


def body_home(imp: ImportFile, body: str) -> tuple[str, str]:
    """(part, part body) that import body `body`'s pieces are built into: the part's name (its folder:
    part_folder) and the body of that part (itself, or the one it is welded to). Raises CadError for bodies that hang
    on each other in a ring."""
    tree = _Tree(imp, [])
    return tree.part_name(tree.part_root(body)), tree.head(body)


def _folder_text(imp: ImportFile, part: str) -> str:
    ps = imp.parts.get(part)
    return ps.folder if ps is not None else part


def _resolve(text: str, import_path: Path) -> Path:
    """A stored folder as a path (ruling R9): {cad}/{code} expanded, else relative to the import file's folder."""
    try:
        text = teamfolders.expand(text)
    except teamfolders.TeamFolderError as e:
        raise CadError(f"Can't find the folder {text}: {e}.") from None
    p = Path(text)
    return p if p.is_absolute() else Path(import_path).parent / p


def _check_target(folder: Path, imp: ImportFile) -> None:
    refuse = CadError(f"{_shown(folder)} already holds a part that didn't come from this import; pick another name")
    if folder.is_file():
        raise refuse
    if not folder.is_dir() or not any(folder.iterdir()):
        return
    marker = _read_marker(folder)
    if marker is None or marker.get("import_id") != imp.import_id:
        raise refuse


def _read_marker(folder: Path) -> dict | None:
    try:
        return tomllib.loads((folder / MARKER).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None


def _shown(folder: Path) -> str:
    return f"{folder.parent.name}/{folder.name}"


def _part_name(folder: Path) -> str | None:
    try:
        name = tomllib.loads((folder / "part.toml").read_text(encoding="utf-8")).get("part", {}).get("name")
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, AttributeError):
        return None
    return name if isinstance(name, str) else None


def _remove_stale(imp: ImportFile, import_path: Path, targets: dict[str, Path]) -> tuple[dict[str, str], list[str]]:
    """Remove the folders the last build wrote (`imp.written`) for parts this build doesn't write there (a part
    merged back into another, or saved to another folder), so a robot config can't count those bodies twice. A folder
    goes only while its marker still names this import and its part.toml still holds the part written there; a copy
    the student made, or a folder another import now owns, is never touched. Returns (the entries to keep in
    `written` because they couldn't be removed, a message per folder)."""
    keep = {f.resolve() for f in targets.values()}
    tree = _Tree(imp, [])
    kept, msgs = {}, []
    for part, text in imp.written.items():
        try:
            folder = _resolve(text, import_path)
        except CadError:
            continue                            # a team folder this computer doesn't know: leave it be
        if folder.resolve() in keep or not folder.is_dir():
            continue
        marker = _read_marker(folder)
        if marker is None or marker.get("import_id") != imp.import_id or _part_name(folder) != part:
            continue
        try:
            shutil.rmtree(folder)
        except OSError:
            kept[part] = text
            msgs.append(Note(t("Couldn't remove {folder}, an old part from this import; delete it yourself.",
                               folder=_shown(folder))))
            continue
        if part in targets:
            why = t("part {part} is now saved in {folder}", part=part, folder=_shown(targets[part]))
        else:
            now: dict[str, list[str]] = {}
            origins = marker.get("origins")
            for body in origins if isinstance(origins, dict) else []:
                if body in imp.bodies:
                    now.setdefault(tree.part_name(tree.part_root(body)), []).append(body)
            why = "; ".join(tn("{bodies} is part of {part} again", "{bodies} are part of {part} again", len(bs),
                               bodies=_and(bs), part=p) for p, bs in now.items()) \
                or t("this import no longer makes that part")
        msgs.append(Note(t("{folder} was removed: {why}", folder=_shown(folder), why=why)))
    return kept, msgs


def _marker_text(imp: ImportFile, built: BuiltPart, folder: Path, import_path: Path) -> str:
    try:
        rel = os.path.relpath(Path(import_path).resolve(), folder.resolve()).replace("\\", "/")
    except ValueError:              # Windows: another drive
        rel = Path(import_path).resolve().as_posix()
    origins = ", ".join(f"{_key(b)} = [{', '.join(repr(float(x)) for x in o)}]" for b, o in built.origins.items())
    attach = f"attach = {_toml_value(built.attach)}\n" if built.attach is not None else ""
    return (f"import = {_toml_value(rel)}\nimport_id = {_toml_value(imp.import_id)}\n"
            f"origins = {{ {origins} }}\n{attach}")


def write_parts(result: BuildResult, imp: ImportFile, import_path: Path) -> list[Path]:
    """Save each built part to its folder (see the module's notes); returns the folders. Raises CadError.

    The folders the last build wrote (`imp.written`) that this build doesn't write are removed (see `_remove_stale`),
    with a message added to `result.messages`. `imp.written` is then updated in place to what this build wrote: the
    caller saves the import file afterwards, so the next build knows exactly which folders are its own."""
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
        edits = folder / "scrimmage-edits.toml"     # the student's own edits (a grip material...): kept, unchecked
        try:                                        # here (a mistake in them shows when the part is loaded)
            if edits.is_file():
                shutil.copy2(edits, tmp / edits.name)
        except OSError as e:
            shutil.rmtree(tmp, ignore_errors=True)
            raise CadError(f"Couldn't keep your edits in {edits} ({e.strerror or e}): check the folder can be read "
                           "and try again.") from None
        _install(tmp, folder)
        out.append(folder)
    kept, msgs = _remove_stale(imp, import_path, targets)
    result.messages += msgs
    imp.written = {**kept, **{name: _folder_text(imp, name) for name in result.parts}}
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
