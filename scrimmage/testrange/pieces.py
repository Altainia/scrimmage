"""Piece kinds: what a game piece is, for a range's `[piece.<name>]` tables and the `[piece.*]` presets.

A kind is a ball (a launcher ball preset), a disc (a disc preset and its aero set) or a rigid piece built from simple
shapes or from CAD:

    [piece.coral]
    shape = "tube"                # sphere, box, cylinder, tube, ring, cone, compound or cad
    diameter_in = 4.5             # every length has an inch form, <key>_in (give one of the two)
    inner_diameter_in = 4.0       # makes a tube or ring hollow; left out, it is solid
    length_in = 11.875
    mass = 0.7                    # kg, required: CAD files carry no mass
    material = "pvc"              # a [surface.*] preset, or friction = <number> (my own number)

`tube` and `ring` take `diameter`, `length` and an optional `inner_diameter` (a ring is a short tube); `cylinder` is
a solid tube; `cone` takes `diameter` (its base), `tip_diameter` (default 0, a point) and `length`; `box` takes
`size = [x, y, z]`; `sphere` takes `diameter`. `compound` lists `[[piece.<name>.shapes]]`, each one of those shapes
with `pos` (or `pos_in`) and `rpy_deg` (roll about x, then pitch about y, then yaw about z, fixed axes). `cad` takes a
STEP, STL or OBJ file (`cad`, relative to the range file) and its `units` (m, mm or in), read by scrimmage/cad. A
compound or CAD piece may declare `axis = "x" | "y" | "z"` (default z) and a `hole = { diameter, length, center }`
(along the axis; length defaults to the piece's own length along it, center to its bounding box's center, in the
file's coordinates). `inertia = [Ixx, Iyy, Izz]` replaces the inertia the shapes give at a uniform density.

A kind's frame has its origin at the center of mass; its `inertia` is the full tensor about it (Ixx, Iyy, Izz, Ixy,
Ixz, Iyz, MuJoCo's `fullinertia` order) and its long `axis` a unit vector in it: a tube's, ring's, cylinder's or
cone's own axis (z, a cone's from base to tip), a box's longest edge, or a compound's or CAD's choice. A kind is
flat when it is shorter along its axis than across it (a gear, a hatch panel, a note).

Solids are what MuJoCo collides: spheres, boxes and convex meshes. A hollow tube or ring is TUBE_SLICES convex
slices, each the hull of its outer arc and its two inner corners, so a rod really passes through the hole (whose
effective radius is the inner radius times cos(pi / TUBE_SLICES), the slices' inner chords). A solid round is a
ROUND_SIDES-gon prism or frustum. A concave CAD mesh is split by `scrimmage.cad.fit.convex_split` (coacd); without
coacd it is the bench's plain error.

Orientation is tip and turn: `orient(tip_deg, turn_deg)` takes the axis to +x by the shortest turn, pitches it up by
tip (0 lying on its side, 90 standing on end), then yaws it by turn about vertical. A rigid piece rests at its lowest
point plus FLOOR_DROP, so it settles onto the carpet.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import Path

import numpy as np

from ..cad.mass import Inertial, combine, mesh_inertial
from ..disc.inputs import PRESETS as DISC_PRESETS
from ..disc.inputs import AeroSet, Disc, DiscConfigError, aero_set, disc_preset
from ..launcher import inputs as launcher_inputs
from ..launcher.inputs import PRESETS, Ball, LauncherConfigError, _build_section
from .elements import IN, _length
from .spec import RangeError, _Reader

TUBE_SLICES = 24  # a hollow tube or ring is this many convex slices around its axis
ROUND_SIDES = 32  # a solid round (cylinder, solid tube, cone, disc) is this regular polygon's prism or frustum
FLOOR_DROP = 0.005  # m: a rigid piece is set down this far above its resting height, so it settles
MAX_EXTENT = 3.0  # m: a piece bigger than this is refused as a units mistake


def _meters(m: float) -> str:
    """A units mistake's size in plain digits: "301", "1234" (whole meters above 100), "3.5"; never "1.23e+03"."""
    return f"{m:.0f}" if m > 100 else f"{m:.3g}"
SHAPES = ("sphere", "box", "cylinder", "tube", "ring", "cone", "compound", "cad")
FORMS = ("ball", "disc", "rigid")
PRESETS_PATH = Path(launcher_inputs.__file__).with_name("presets.toml")

_ARC_STEPS = 4  # chords along each slice's outer arc: the outside is a TUBE_SLICES * _ARC_STEPS-gon
_SPHERE_RINGS = 8  # a sphere's triangle mesh (solid_mesh): latitude rings, and twice as many meridians
_STAND_DEG = 80.0  # a tip at or above this is standing (flat kinds: lying flat)
_LIE_DEG = 10.0  # a tip at or below this is lying (flat kinds: standing on edge)

_KIND = ("preset", "ball", "disc", "aero", "shape", "diameter", "inner_diameter", "tip_diameter", "length",
         "size", "mass", "material", "friction", "axis", "inertia", "shapes", "cad", "units", "hole")
_LENGTHS = ("diameter", "inner_diameter", "tip_diameter", "length", "size")
_KIND_ALL = _KIND + tuple(k + "_in" for k in _LENGTHS) + ("label",)
# The keys each shape takes beyond shape, mass, material, friction, inertia and label (lengths also as <key>_in).
_SHAPE_KEYS = {
    "sphere": ("diameter",),
    "box": ("size",),
    "cylinder": ("diameter", "length"),
    "tube": ("diameter", "inner_diameter", "length"),
    "ring": ("diameter", "inner_diameter", "length"),
    "cone": ("diameter", "tip_diameter", "length"),
    "compound": ("shapes", "axis", "hole"),
    "cad": ("cad", "units", "axis", "hole"),
}
_SIMPLE = SHAPES[:6]  # the shapes a compound's entries may be
_ENTRY = ("shape", "pos", "pos_in", "rpy_deg") + _LENGTHS + tuple(k + "_in" for k in _LENGTHS)
_HOLE = ("diameter", "diameter_in", "length", "length_in", "center", "center_in")
_AXES = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}
_UNITS = ("m", "mm", "in")
_IDENTITY = (1.0, 0.0, 0.0, 0.0)


class _KindReader(_Reader):
    """A `_Reader` whose messages read "<file>: [piece.<name>]: <what>"."""

    def err(self, msg: str) -> RangeError:
        return RangeError(f"{self.path}: {self.where}: {msg}")


# ------------------------------------------------------------------------------------------------------ turning

def _matrix(q) -> np.ndarray:
    """The rotation matrix of a w x y z quaternion."""
    w, x, y, z = (float(c) for c in q)
    n = w * w + x * x + y * y + z * z
    s = 2.0 / n if n > 0 else 0.0
    return np.array([
        [1 - s * (y * y + z * z), s * (x * y - w * z), s * (x * z + w * y)],
        [s * (x * y + w * z), 1 - s * (x * x + z * z), s * (y * z - w * x)],
        [s * (x * z - w * y), s * (y * z + w * x), 1 - s * (x * x + y * y)],
    ])


def _quat(m: np.ndarray) -> np.ndarray:
    """The w x y z quaternion (w >= 0) of a rotation matrix."""
    t = np.trace(m)
    if t > 0:
        s = 2.0 * math.sqrt(1.0 + t)
        q = [s / 4, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s]
    else:
        i = int(np.argmax(np.diag(m)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = 2.0 * math.sqrt(max(1.0 + m[i, i] - m[j, j] - m[k, k], 0.0))
        q = [0.0] * 4
        q[0] = (m[k, j] - m[j, k]) / s
        q[1 + i] = s / 4
        q[1 + j] = (m[j, i] + m[i, j]) / s
        q[1 + k] = (m[k, i] + m[i, k]) / s
    q = np.array(q, dtype=float)
    q /= np.linalg.norm(q)
    return -q if q[0] < 0 else q


def rotate(q, v) -> np.ndarray:
    """Vector `v` turned by the w x y z quaternion `q`."""
    return _matrix(q) @ np.asarray(v, dtype=float)


def _axis_angle(axis, angle: float) -> np.ndarray:
    k = np.asarray(axis, dtype=float)
    k = k / np.linalg.norm(k)
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(angle) * kx + (1 - math.cos(angle)) * kx @ kx


def _rpy(rpy_deg) -> np.ndarray:
    """Roll about x, then pitch about y, then yaw about z (fixed axes), as a plate's `rpy_deg`."""
    r, p, y = (math.radians(a) for a in rpy_deg)
    return _axis_angle((0, 0, 1), y) @ _axis_angle((0, 1, 0), p) @ _axis_angle((1, 0, 0), r)


def _to_x(axis) -> np.ndarray:
    """The shortest turn taking unit vector `axis` to +x."""
    a = np.asarray(axis, dtype=float)
    c = float(np.clip(a[0], -1.0, 1.0))
    if c > 1.0 - 1e-12:
        return np.eye(3)
    if c < -1.0 + 1e-12:
        return _axis_angle((0, 0, 1), math.pi)
    return _axis_angle(np.cross(a, (1.0, 0.0, 0.0)), math.acos(c))


# ------------------------------------------------------------------------------------------------------- solids

@dataclass(frozen=True)
class Solid:
    """One collision shape of a kind, in the kind's frame. `kind` is "sphere" (size = (radius,)), "box" (size = half
    sizes, as MuJoCo's) or "mesh" (a convex mesh: `vertices` and `faces` already in the kind's frame, pos at the
    origin, quat the identity)."""

    kind: str
    size: tuple[float, ...]
    pos: tuple[float, float, float]
    quat: tuple[float, float, float, float]
    vertices: np.ndarray | None = field(default=None, compare=False, repr=False)
    faces: np.ndarray | None = field(default=None, compare=False, repr=False)

    def moved(self, rot: np.ndarray, shift) -> Solid:
        """This solid turned by `rot` and then shifted by `shift`."""
        shift = np.asarray(shift, dtype=float)
        if self.kind == "mesh":
            v = self.vertices @ rot.T + shift
            return replace(self, vertices=v)
        pos = tuple(float(c) for c in rot @ np.asarray(self.pos) + shift)
        quat = _IDENTITY if self.kind == "sphere" else tuple(float(c) for c in _quat(rot @ _matrix(self.quat)))
        return replace(self, pos=pos, quat=quat)


_BOX_FACES = np.array([(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
                       (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)])


def _box_corners(s: Solid) -> np.ndarray:
    h = np.asarray(s.size, dtype=float)
    local = np.array([(x, y, z) for x in (-h[0], h[0]) for y in (-h[1], h[1]) for z in (-h[2], h[2])])
    return local @ _matrix(s.quat).T + np.asarray(s.pos)


def _sphere_mesh(center, r: float) -> tuple[np.ndarray, np.ndarray]:
    rings, sides = _SPHERE_RINGS, 2 * _SPHERE_RINGS
    v = [(0.0, 0.0, -r)]
    for i in range(1, rings):
        lat = -math.pi / 2 + math.pi * i / rings
        lon = 2 * math.pi * np.arange(sides) / sides
        v += [(r * math.cos(lat) * math.cos(a), r * math.cos(lat) * math.sin(a), r * math.sin(lat)) for a in lon]
    v.append((0.0, 0.0, r))
    top, f = len(v) - 1, []
    for j in range(sides):
        f.append((0, 1 + (j + 1) % sides, 1 + j))
        f.append((top, top - sides + j, top - sides + (j + 1) % sides))
    for i in range(rings - 2):
        a, b = 1 + i * sides, 1 + (i + 1) * sides
        for j in range(sides):
            k = (j + 1) % sides
            f += [(a + j, a + k, b + k), (a + j, b + k, b + j)]
    return np.array(v) + np.asarray(center, dtype=float), np.array(f)


def solid_mesh(s: Solid) -> tuple[np.ndarray, np.ndarray]:
    """A closed triangle mesh of the solid (vertices, faces), in the kind's frame."""
    if s.kind == "mesh":
        return s.vertices, s.faces
    if s.kind == "box":
        return _box_corners(s), _BOX_FACES
    return _sphere_mesh(s.pos, s.size[0])


def _rings(bottom: np.ndarray, top: np.ndarray, z0: float, z1: float) -> Solid:
    """The convex solid between polygon `bottom` at z0 and `top` at z1 (each (n, 2), counterclockwise, the same n,
    or `top` a single point: a pyramid)."""
    n = len(bottom)
    v = [(x, y, z0) for x, y in bottom] + [(x, y, z1) for x, y in top]
    f = [(0, i + 1, i) for i in range(1, n - 1)]  # the bottom cap, facing down
    if len(top) == 1:
        f += [(i, (i + 1) % n, n) for i in range(n)]
    else:
        f += [(n, n + i, n + i + 1) for i in range(1, n - 1)]
        for i in range(n):
            j = (i + 1) % n
            f += [(i, j, n + j), (i, n + j, n + i)]
    return Solid("mesh", (), (0.0, 0.0, 0.0), _IDENTITY, np.array(v, dtype=float), np.array(f))


def _circle(r: float, sides: int = ROUND_SIDES) -> np.ndarray:
    a = 2 * math.pi * np.arange(sides) / sides
    return np.column_stack([r * np.cos(a), r * np.sin(a)])


def _round(r0: float, r1: float, length: float) -> Solid:
    """A solid round along z, centered: a prism (r0 == r1), a frustum, or a cone (r1 == 0)."""
    top = np.zeros((1, 2)) if r1 == 0 else _circle(r1)
    return _rings(_circle(r0), top, -length / 2, length / 2)


def _slices(outer: float, inner: float, length: float) -> list[Solid]:
    """A hollow tube along z, centered, as TUBE_SLICES convex slices."""
    out = []
    for k in range(TUBE_SLICES):
        a0, a1 = 2 * math.pi * k / TUBE_SLICES, 2 * math.pi * (k + 1) / TUBE_SLICES
        arc = [a0 + (a1 - a0) * j / _ARC_STEPS for j in range(_ARC_STEPS + 1)]
        poly = [(outer * math.cos(a), outer * math.sin(a)) for a in arc]
        poly += [(inner * math.cos(a1), inner * math.sin(a1)), (inner * math.cos(a0), inner * math.sin(a0))]
        poly = np.array(poly)
        out.append(_rings(poly, poly, -length / 2, length / 2))
    return out


def _inertial(s: Solid) -> Inertial:
    """The solid's volume (as mass, at density 1), center and inertia tensor."""
    if s.kind == "sphere":
        r = s.size[0]
        vol = 4.0 / 3.0 * math.pi * r ** 3
        return Inertial(vol, np.asarray(s.pos, dtype=float), 0.4 * vol * r * r * np.eye(3))
    return mesh_inertial(*solid_mesh(s), 1.0)


# --------------------------------------------------------------------------------------------------- the kind

@dataclass(frozen=True)
class Hole:
    """A hollow piece's hole: its `diameter`, its `length` along the kind's axis and its `center` in the kind's
    frame."""

    diameter: float
    length: float
    center: tuple[float, float, float]


@dataclass(frozen=True, eq=False)
class PieceKind:
    name: str
    form: str  # "ball", "disc" or "rigid" (FORMS)
    preset: str | None  # the piece, ball or disc preset it came from
    ball: Ball | None
    disc: Disc | None
    aero: AeroSet | None
    solids: tuple[Solid, ...]
    mass: float
    inertia: tuple[float, ...]  # Ixx, Iyy, Izz, Ixy, Ixz, Iyz about the center of mass
    friction: float  # the surface's sliding friction
    axis: tuple[float, float, float]
    hole: Hole | None
    extent: float  # the largest bounding-box side (a ball's diameter)
    radius: float  # the bounding sphere's, about the center of mass (a ball's or disc's own radius)
    flat: bool
    label: str = ""  # the plain name a student reads ("coral"); left empty, display_name(name)
    shape: str = ""  # "ball", "disc" or one of SHAPES

    def __post_init__(self):
        if not self.label:
            object.__setattr__(self, "label", display_name(self.name))

    @cached_property
    def _points(self) -> np.ndarray:
        """Every box corner and mesh vertex, in the kind's frame."""
        pts = [_box_corners(s) if s.kind == "box" else s.vertices for s in self.solids if s.kind != "sphere"]
        return np.concatenate(pts) if pts else np.zeros((0, 3))

    @cached_property
    def _spheres(self) -> tuple[np.ndarray, np.ndarray]:
        sph = [s for s in self.solids if s.kind == "sphere"]
        return (np.array([s.pos for s in sph], dtype=float).reshape(-1, 3),
                np.array([s.size[0] for s in sph], dtype=float))

    def lowest(self, quat) -> float:
        """How far below the center the lowest point lies when the kind is turned by `quat` (w x y z)."""
        m = _matrix(quat)
        centers, radii = self._spheres
        z = np.concatenate([(self._points @ m.T)[:, 2], (centers @ m.T)[:, 2] - radii])
        return float(-z.min())

    def rest_height(self, quat) -> float:
        """The center's height when set down on the carpet turned by `quat`: a ball's radius; a disc's as today;
        a rigid piece's lowest point plus FLOOR_DROP, so it settles."""
        if self.form == "ball":
            return self.radius
        if self.form == "disc":
            return self.lowest(quat)
        return self.lowest(quat) + FLOOR_DROP

    def orient(self, tip_deg: float, turn_deg: float) -> np.ndarray:
        """The quaternion (w x y z) that lays the axis along +x, pitches it up by `tip_deg` and turns it by
        `turn_deg` about vertical."""
        m = (_axis_angle((0, 0, 1), math.radians(turn_deg)) @ _axis_angle((0, 1, 0), -math.radians(tip_deg))
             @ _to_x(self.axis))
        return _quat(m)

    @cached_property
    def _boxes(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Each box and mesh solid's oriented bounding box, in the kind's frame: centers (n, 3), axes (n, 3, 3, each
        row a unit axis) and half sizes (n, 3). A mesh's box lies along its vertices' principal axes, so a tube's
        slice is boxed closely whichever way round the tube it lies."""
        centers, axes, halves = [], [], []
        for s in self.solids:
            if s.kind == "box":
                centers.append(np.asarray(s.pos, dtype=float))
                axes.append(_matrix(s.quat).T)
                halves.append(np.asarray(s.size, dtype=float))
            elif s.kind == "mesh":
                v = np.asarray(s.vertices, dtype=float)
                rows = np.linalg.eigh(np.cov((v - v.mean(axis=0)).T))[1].T     # principal axes, as rows
                local = v @ rows.T
                lo, hi = local.min(axis=0), local.max(axis=0)
                centers.append(((lo + hi) / 2.0) @ rows)
                axes.append(rows)
                halves.append((hi - lo) / 2.0)
        return (np.array(centers, dtype=float).reshape(-1, 3), np.array(axes, dtype=float).reshape(-1, 3, 3),
                np.array(halves, dtype=float).reshape(-1, 3))

    def turned_solids(self, quat) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """The kind turned by `quat` (w x y z), about its center: its solids' oriented boxes (centers (n, 3), axes
        (n, 3, 3) as rows, half sizes (n, 3)) and its spheres' centers ((m, 3)) and radii."""
        m = _matrix(quat)
        centers, axes, halves = self._boxes
        sc, radii = self._spheres
        return centers @ m.T, axes @ m.T, halves.copy(), sc @ m.T, radii.copy()

    def reach(self, quat) -> float:
        """The largest horizontal distance of a point of the kind from its center, turned by `quat`."""
        m = _matrix(quat)
        centers, radii = self._spheres
        r = np.concatenate([np.linalg.norm((self._points @ m.T)[:, :2], axis=1),
                            np.linalg.norm((centers @ m.T)[:, :2], axis=1) + radii])
        return float(r.max())

    def effective_hole_radius(self) -> float | None:
        """The radius a rod fits through: a sliced tube's inner chords sit at cos(pi / TUBE_SLICES) of its inner
        radius; a declared hole is its own radius. None for a piece without a hole."""
        if self.hole is None:
            return None
        r = self.hole.diameter / 2
        return r * math.cos(math.pi / TUBE_SLICES) if self.shape in ("tube", "ring") else r


def display_name(name: str) -> str:
    """The plain name a student reads for a kind or preset: "2025-coral" is "coral", "2010-soccer-ball" "soccer
    ball"."""
    return re.sub(r"^\d{4}-", "", name).replace("-", " ").replace("_", " ")


def _finish(r: _Reader, name: str, shape: str, solids: list[Solid], mass: float, friction: float, axis,
            hole_at: tuple | None, inertia: tuple | None, own: Inertial | None = None,
            label: str = "") -> PieceKind:
    """The kind from its solids in the shape's own frame: moved so the center of mass is the origin, inertia at a
    uniform density (from `own`, the volume of a CAD file's own meshes, when given), the sizes and the units check.
    `hole_at` is (diameter, length or None, center or None) in the shape's frame."""
    parts = combine([_inertial(s) for s in solids]) if own is None or own.mass <= 0 else own
    if not parts.mass > 0:
        raise r.err("has no volume: its shapes are flat or broken")
    com = np.asarray(parts.com, dtype=float)
    tensor = parts.tensor * (mass / parts.mass)
    lo = np.min([_lo_hi(s)[0] for s in solids], axis=0)
    hi = np.max([_lo_hi(s)[1] for s in solids], axis=0)
    sides = hi - lo
    extent = float(sides.max())
    if extent > MAX_EXTENT:
        raise r.err(f"the piece is {_meters(extent)} m across; check units (m, mm or in)")
    moved = tuple(s.moved(np.eye(3), -com) for s in solids)
    i = int(np.argmax(axis))
    hole = None
    if hole_at is not None:
        diameter, length, center = hole_at
        center = (lo + hi) / 2 if center is None else np.asarray(center, dtype=float)
        hole = Hole(diameter, float(sides[i]) if length is None else length, tuple(float(c) for c in center - com))
    full = inertia + (0.0, 0.0, 0.0) if inertia is not None else (
        tensor[0, 0], tensor[1, 1], tensor[2, 2], tensor[0, 1], tensor[0, 2], tensor[1, 2])
    kind = PieceKind(name=name, form="rigid", preset=None, ball=None, disc=None, aero=None, solids=moved,
                     mass=mass, inertia=tuple(float(x) for x in full), friction=friction,
                     axis=tuple(float(c) for c in axis),
                     hole=hole, extent=extent, radius=0.0, flat=bool(sides[i] < np.delete(sides, i).max()),
                     label=label, shape=shape)
    centers, radii = kind._spheres
    radius = max([float(np.linalg.norm(kind._points, axis=1).max()) if len(kind._points) else 0.0]
                 + list(np.linalg.norm(centers, axis=1) + radii))
    return replace(kind, radius=radius)


def _lo_hi(s: Solid) -> tuple[np.ndarray, np.ndarray]:
    if s.kind == "sphere":
        c = np.asarray(s.pos, dtype=float)
        return c - s.size[0], c + s.size[0]
    pts = _box_corners(s) if s.kind == "box" else s.vertices
    return pts.min(axis=0), pts.max(axis=0)


# ---------------------------------------------------------------------------------------------------- reading

def _vec(r: _Reader, key: str, default: tuple) -> tuple:
    if r.has(key) and r.has(key + "_in"):
        raise r.err(f"has both {key} and {key}_in; give one")
    if r.has(key + "_in"):
        return tuple(x * IN for x in r.nums(key + "_in", 3))
    return r.nums(key, 3, default)


def _has(r: _Reader, key: str) -> bool:
    return r.has(key) or r.has(key + "_in")


def _simple(r: _Reader, shape: str) -> tuple[list[Solid], tuple | None, tuple | None]:
    """A simple shape's solids in its own frame (centered on its middle, its axis z), its hole (diameter, length,
    center) when hollow, and its own long axis."""
    if shape == "sphere":
        return [Solid("sphere", (_length(r, "diameter") / 2,), (0.0, 0.0, 0.0), _IDENTITY)], None, (0, 0, 1)
    if shape == "box":
        if r.has("size") == r.has("size_in"):
            raise r.err("needs one of size = [x, y, z] (m) or size_in = [x, y, z] (in)")
        size = r.nums("size", 3, positive=True) if r.has("size") else tuple(
            v * IN for v in r.nums("size_in", 3, positive=True))
        axis = [0, 0, 0]
        axis[int(np.argmax(size))] = 1
        return [Solid("box", tuple(s / 2 for s in size), (0.0, 0.0, 0.0), _IDENTITY)], None, tuple(axis)
    d, length = _length(r, "diameter"), _length(r, "length")
    if shape == "cone":
        tip = _length(r, "tip_diameter", 0.0, positive=False)
        if not tip < d:
            raise r.err(f"tip_diameter {tip:.4g} m must be smaller than diameter {d:.4g} m")
        return [_round(d / 2, tip / 2, length)], None, (0, 0, 1)
    if shape in ("tube", "ring") and _has(r, "inner_diameter"):
        inner = _length(r, "inner_diameter")
        if not inner < d:
            raise r.err(f"inner_diameter {inner:.4g} m must be smaller than diameter {d:.4g} m")
        return _slices(d / 2, inner / 2, length), (inner, length, (0.0, 0.0, 0.0)), (0, 0, 1)
    return [_round(d / 2, d / 2, length)], None, (0, 0, 1)


_SHAPED = {k for v in _SHAPE_KEYS.values() for k in v}


def _check_shape_keys(r: _Reader, shape: str) -> None:
    """Refuse a key another shape takes (a box's size on a tube)."""
    for key in r.t:
        base = key[:-3] if key.endswith("_in") else key
        if base in _SHAPED and base not in _SHAPE_KEYS[shape]:
            raise r.err(f"{key} isn't used by a {shape}; a {shape} takes {', '.join(_SHAPE_KEYS[shape])}")


def _shape_name(r: _Reader, choices: tuple[str, ...]) -> str:
    shape = r.text("shape")
    if shape not in choices:
        raise r.err(f"shape must be one of {', '.join(choices)}, not {shape!r}")
    return shape


def _axis(r: _Reader) -> tuple:
    if not r.has("axis"):
        return (0, 0, 1)
    a = r.raw("axis")
    if not isinstance(a, str) or a not in _AXES:
        raise r.err(f"axis must be \"x\", \"y\" or \"z\", not {a!r}")
    return _AXES[a]


def _hole(r: _Reader, path: Path, name: str) -> tuple | None:
    if not r.has("hole"):
        return None
    h = _KindReader(path, f"[piece.{name}] hole", r.raw("hole"), _HOLE)
    length = _length(h, "length") if _has(h, "length") else None
    center = _vec(h, "center", None) if _has(h, "center") else None
    return _length(h, "diameter"), length, center


def _compound(r: _Reader, path: Path, name: str) -> list[Solid]:
    raw = r.raw("shapes")
    if not isinstance(raw, list) or not raw:
        raise r.err("shapes must list at least one shape ([[piece.<name>.shapes]] tables)")
    out = []
    for i, t in enumerate(raw):
        e = _KindReader(path, f"[piece.{name}] shapes #{i + 1}", t, _ENTRY)
        shape = _shape_name(e, _SIMPLE)
        _check_shape_keys(e, shape)
        solids, _, _ = _simple(e, shape)
        rot, pos = _rpy(e.nums("rpy_deg", 3, (0.0, 0.0, 0.0))), _vec(e, "pos", (0.0, 0.0, 0.0))
        out += [s.moved(rot, pos) for s in solids]
    return out


def _cad(r: _Reader, base_dir: Path) -> tuple[list[Solid], Inertial]:
    from ..bench.spec import BenchError
    from ..cad import CadError
    from ..cad.fit import convex_split
    from ..cad.source import load_source

    units = None
    if r.has("units"):
        units = r.text("units")
        if units not in _UNITS:
            raise r.err(f"units must be one of {', '.join(_UNITS)}, not {units!r}")
    file = Path(r.text("cad"))
    try:
        src = load_source(file if file.is_absolute() else Path(base_dir) / file, units)
    except CadError as e:
        raise r.err(str(e)) from None
    lo = np.min([p.v.min(axis=0) for p in src.pieces], axis=0)
    hi = np.max([p.v.max(axis=0) for p in src.pieces], axis=0)
    if float((hi - lo).max()) > MAX_EXTENT:  # before the split, which a units mistake would make slow
        raise r.err(f"the piece is {_meters(float((hi - lo).max()))} m across; check units (m, mm or in)")
    solids = []
    for p in src.pieces:
        try:
            pieces = convex_split(p.v, p.f)
        except BenchError as e:
            raise r.err(str(e)) from None
        solids += [Solid("mesh", (), (0.0, 0.0, 0.0), _IDENTITY, np.asarray(v, dtype=float), np.asarray(f))
                   for v, f in pieces]
    return solids, combine([mesh_inertial(p.v, p.f, 1.0) for p in src.pieces])


def parse_kind(path, name: str, table: dict, base_dir: Path) -> PieceKind:
    """A `[piece.<name>]` table: a preset (`preset`, `ball` or `disc`) or a shape. Raises RangeError."""
    path = Path(path)
    r = _KindReader(path, f"[piece.{name}]", table, _KIND_ALL)
    label = r.text("label") if r.has("label") else ""
    given = [k for k in ("preset", "ball", "disc", "shape") if r.has(k)]
    if not given:
        raise r.err("needs a shape (one of " + ", ".join(SHAPES) + "), or preset = \"<piece preset>\", ball = "
                    "\"<ball preset>\" or disc = \"<disc preset>\"")
    if len(given) > 1:
        raise r.err(f"has both {given[0]} and {given[1]}; a kind is a preset or a shape: give one")
    source = given[0]
    if r.has("aero") and source != "disc":
        raise r.err("aero is a disc's aerodynamics; only a disc kind takes it")
    if source != "shape":
        extra = [k for k in r.t if k not in (source, "aero", "label")]
        if extra:
            raise r.err(f"{extra[0]} doesn't go with {source} = {r.raw(source)!r}: a preset kind is its preset")
        try:
            if source == "ball":
                kind = ball_kind(name, r.text("ball"))
            elif source == "disc":
                kind = disc_kind(name, r.text("disc"), r.text("aero") if r.has("aero") else _default_aero())
            else:
                kind = replace(kind_preset(r.text("preset")), name=name, label="")
        except RangeError as e:
            raise r.err(str(e)) from None
        return replace(kind, label=label) if label else kind
    shape = _shape_name(r, SHAPES)
    _check_shape_keys(r, shape)
    mass = r.num("mass", positive=True)
    if r.has("material") and r.has("friction"):
        raise r.err("has both material and friction; give one: material = \"<surface>\", or friction = <number> "
                    "for your own measured number")
    if r.has("material"):
        try:
            friction = surface_friction(r.text("material"))
        except RangeError as e:
            raise r.err(str(e)) from None
    elif r.has("friction"):
        friction = r.num("friction", positive=True)
    else:
        raise r.err("needs a surface: material = \"<surface preset>\", or friction = <number> for your own number")
    inertia = r.nums("inertia", 3, positive=True) if r.has("inertia") else None
    own = None
    if shape == "compound":
        solids, hole, axis = _compound(r, path, name), _hole(r, path, name), _axis(r)
    elif shape == "cad":
        axis, hole = _axis(r), _hole(r, path, name)
        solids, own = _cad(r, base_dir)
    else:
        solids, hole, axis = _simple(r, shape)
    return _finish(r, name, shape, solids, mass, friction, axis, hole, inertia, own, label)


def _default_aero() -> str:
    from ..disc.inputs import DEFAULT_AERO
    return DEFAULT_AERO


# ----------------------------------------------------------------------------------------------------- presets

def _surface_friction_of_balls() -> float:
    from .build import BALL_FRICTION  # build.py pulls in mujoco
    return float(BALL_FRICTION[0])


def ball_kind(name: str, preset: str) -> PieceKind:
    """A kind that is a launcher ball preset."""
    if preset not in PRESETS["ball"]:
        raise RangeError(f"unknown ball preset {preset!r}; known ball presets: {', '.join(PRESETS['ball'])}")
    try:
        ball = _build_section("ball", {"preset": preset})
    except LauncherConfigError as e:  # pragma: no cover - the shipped presets are complete
        raise RangeError(str(e)) from e
    return ball_kind_of(name, preset, ball)


def ball_kind_of(name: str, preset: str | None, ball: Ball) -> PieceKind:
    """A kind that is the launcher ball `ball` (from `preset`)."""
    r = ball.diameter / 2
    i = ball.inertia_factor * ball.mass * r * r
    return PieceKind(name=name, form="ball", preset=preset, ball=ball, disc=None, aero=None,
                     solids=(Solid("sphere", (r,), (0.0, 0.0, 0.0), _IDENTITY),), mass=ball.mass,
                     inertia=(i, i, i, 0.0, 0.0, 0.0), friction=_surface_friction_of_balls(), axis=(0.0, 0.0, 1.0),
                     hole=None, extent=ball.diameter, radius=r, flat=False, shape="ball")


def disc_kind(name: str, preset: str, aero: str) -> PieceKind:
    """A kind that is a disc preset, flying with aero set `aero`."""
    if preset not in DISC_PRESETS.get("disc", {}):
        raise RangeError(f"unknown disc preset {preset!r}; known disc presets: "
                         f"{', '.join(DISC_PRESETS.get('disc', {}))}")
    if aero not in DISC_PRESETS.get("aero", {}):
        raise RangeError(f"unknown aero set {aero!r}; known aero sets: {', '.join(DISC_PRESETS.get('aero', {}))}")
    try:
        disc, air = disc_preset(preset), aero_set(aero)
    except DiscConfigError as e:  # pragma: no cover - the shipped presets are complete
        raise RangeError(str(e)) from e
    return PieceKind(name=name, form="disc", preset=preset, ball=None, disc=disc, aero=air,
                     solids=(_round(disc.radius, disc.radius, disc.height),), mass=disc.mass,
                     inertia=(disc.ixx, disc.ixx, disc.izz, 0.0, 0.0, 0.0), friction=_surface_friction_of_balls(),
                     axis=(0.0, 0.0, 1.0), hole=None, extent=max(disc.diameter, disc.height), radius=disc.radius,
                     flat=disc.height < disc.diameter, shape="disc")


def kind_preset(name: str) -> PieceKind:
    """A `[piece.*]` preset (launcher/presets.toml) as a kind named after it."""
    known = PRESETS.get("piece", {})
    if name not in known:
        raise RangeError(f"unknown piece preset {name!r}; known piece presets: {', '.join(known) or 'none yet'}")
    return replace(parse_kind(PRESETS_PATH, name, known[name], PRESETS_PATH.parent), preset=name)


def surface_friction(name: str) -> float:
    """A `[surface.*]` preset's sliding friction (launcher/presets.toml)."""
    known = PRESETS.get("surface", {})
    if name not in known:
        raise RangeError(f"material {name!r} isn't a surface preset; known surfaces: {', '.join(known) or 'none yet'}")
    return float(known[name]["friction"])


# ------------------------------------------------------------------------------------------------------- words

def _turn_words(turn: int) -> str:
    if turn == 0:
        return "lying in line"
    if abs(turn) == 90:
        return "lying across"
    if turn == 180:
        return "lying in line, turned around"
    return f"lying at {turn}°"


def _edge_words(turn: int) -> str:
    if turn == 0:
        return "standing on edge"
    return "standing on edge, turned around" if turn == 180 else f"standing on edge, turned {turn}°"


def orientation_words(kind: PieceKind, tip_deg: float, turn_deg: float) -> str:
    """How a piece lies, in plain words: "standing on end", "lying across", "leaning 40°, lying in line"... A flat
    kind (a gear, a note) is "lying flat" at tip 90 and "standing on edge" at tip 0. The turn is about vertical
    from the robot's path (or the field's +x)."""
    turn = round((turn_deg + 180.0) % 360.0 - 180.0)
    turn = 180 if turn == -180 else turn
    if tip_deg >= _STAND_DEG:
        return "lying flat" if kind.flat else "standing on end"
    lying = _edge_words(turn) if kind.flat else _turn_words(turn)
    return lying if tip_deg <= _LIE_DEG else f"leaning {round(tip_deg)}°, {lying}"
