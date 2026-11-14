"""Measuring a robot model for inspection (design Call I4): its weight, frame perimeter, height, reach beyond the frame
and the smallest circle holding it seen from above. Every geom of the robot counts (the bodies under the robot body's
root), colliding or not: an elevator's stages are built collide = false, and the rules count them.

- mass: the robot's subtree mass (the root's), everything the model weighs.
- perimeter: 2 x (length + width) of the frame outline (terrain.climb.frame_outline over every geom): the largest box
  on the robot body by top-view area (the stock chassis's frame plate, so bumpers are out, as R104 measures), or,
  without a box (`frame_plate` False), the outline of all the body's geoms.
- The geoms are measured by points on their own shapes (shape_points: a box's corners, a round geom's rings, a mesh's
  vertices), not their bounding boxes: a round part's square box would overstate its reach (a swerve module's
  azimuth cylinder, 4 cm past the frame corner by its box, under 2 cm by its shape). A ring of RING points sits
  inside the true circle by at most r (1 - cos(pi / RING)), 2 % of the radius.
- height: the robot's height in its own frame, from its lowest point to its highest along the chassis body's up
  (as terrain.climb.ClimbMonitor.own_height, over these shape points): what it would measure resting level on a flat
  floor, as every season's match height rule measures it (2020 G17, 2024 G413-A, 2026 R107: "as if on a flat
  floor"). Standing level on the floor it is the highest point above z = 0; lifted onto a bump, tilted or hanging, it
  doesn't change. Bumpers count (answer 7).
- reach: the largest horizontal distance, in the robot body's own x-y plane, from the frame outline to a point of a
  geom not on the robot body itself (so its frame, bumpers and heading marker never count), the climb runs'
  extension (terrain.climb.extent) over every geom's shape. `reach_geom` names the geom setting it.
- diameter: the smallest circle holding every point projected on the floor, bumpers included (2013).

All of it at the world's current state: `measure(world)` right after building is the start configuration (answer 4).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import mujoco
import numpy as np

from ..terrain.climb import extent, frame_outline


@dataclass(frozen=True)
class Measures:
    mass: float          # kg
    perimeter: float     # m
    frame_plate: bool    # the frame outline is a box geom's (else the outline of all the robot body's geoms)
    height: float        # m
    reach: float         # m
    diameter: float      # m
    reach_geom: str = ""  # the geom setting the reach (its name, or its body's); "" when none sticks out


RING = 16      # points on each ring of a round geom's surface


def _ring(r1: float, r2: float, z: float) -> np.ndarray:
    """RING points on an ellipse of half-axes r1 (x), r2 (y) at height z: (RING, 3)."""
    a = np.arange(RING) * (2 * math.pi / RING)
    return np.stack([r1 * np.cos(a), r2 * np.sin(a), np.full(RING, z)], axis=1)


def shape_points(model: mujoco.MjModel, g: int) -> np.ndarray:
    """Points on geom g's own shape, in its frame (n, 3): a box's 8 corners; a sphere's or ellipsoid's three
    orthogonal rings and poles; a cylinder's two end rims; a capsule's two rims, its caps' rings at 45 deg and its
    tips; a mesh's vertices. Anything else (a plane, height field or SDF) is its bounding box's corners."""
    T = mujoco.mjtGeom
    t, size = int(model.geom_type[g]), model.geom_size[g]     # an int: a numpy int isn't `in` a tuple of mjtGeom
    signs = np.array(list(np.ndindex(2, 2, 2)), dtype=float) * 2.0 - 1.0          # (8, 3)
    if t == T.mjGEOM_BOX:
        return signs * size
    if t in (int(T.mjGEOM_SPHERE), int(T.mjGEOM_ELLIPSOID)):
        rx, ry, rz = (size[0],) * 3 if t == T.mjGEOM_SPHERE else size
        xy = _ring(rx, ry, 0.0)
        xz = _ring(rx, rz, 0.0)[:, [0, 2, 1]]
        yz = _ring(ry, rz, 0.0)[:, [2, 0, 1]]
        return np.concatenate([xy, xz, yz, [[0, 0, rz], [0, 0, -rz]]])
    if t == T.mjGEOM_CYLINDER:
        r, h = size[0], size[1]
        return np.concatenate([_ring(r, r, h), _ring(r, r, -h)])
    if t == T.mjGEOM_CAPSULE:
        r, h = size[0], size[1]
        c = r * math.sqrt(0.5)
        return np.concatenate([_ring(r, r, h), _ring(r, r, -h), _ring(c, c, h + c), _ring(c, c, -h - c),
                               [[0, 0, h + r], [0, 0, -h - r]]])
    if t == T.mjGEOM_MESH and model.geom_dataid[g] >= 0:
        k = model.geom_dataid[g]
        start, n = model.mesh_vertadr[k], model.mesh_vertnum[k]
        return np.array(model.mesh_vert[start:start + n], dtype=float)      # already in the geom's frame
    aabb = model.geom_aabb[g]
    return aabb[:3] + signs * aabb[3:]


class Extent:
    """The robot's height and reach at the world's current state, over points on every robot geom's own shape
    (shape_points). The geoms, their points in their own frames and the frame outline are found once here, so
    read() (called every 20 ms of a scenario) only turns the points."""

    def __init__(self, world):
        m = world.model
        self.model, self.data, self.body = m, world.data, world.robot_body
        root = m.body_rootid[self.body]
        mine = [g for g in range(m.ngeom) if m.body_rootid[m.geom_bodyid[g]] == root]
        reach = [g for g in mine if m.geom_bodyid[g] != self.body]
        geoms = reach + [g for g in mine if m.geom_bodyid[g] == self.body]
        shapes = [shape_points(m, g) for g in geoms]
        counts = [len(p) for p in shapes]
        self._geoms = np.array(geoms, dtype=np.int64)
        self._local = np.concatenate(shapes) if shapes else np.zeros((0, 3))           # (n, 3)
        self._of = np.repeat(self._geoms, counts)                                      # each point's geom
        self._reach = sum(counts[:len(reach)])        # the first _reach points count for the reach
        x0, x1, y0, y1 = frame_outline(m, self.body, colliding_only=False)
        self.outline = (x0, x1, y0, y1)
        self._lo, self._hi = np.array([x0, y0]), np.array([x1, y1])

    def points(self) -> np.ndarray:
        """Every point on the robot's geoms, in the world: (n, 3)."""
        d = self.data
        rot = d.geom_xmat[self._of].reshape(-1, 3, 3)
        return np.einsum("nij,nj->ni", rot, self._local) + d.geom_xpos[self._of]

    def read_where(self) -> tuple[float, float, int]:
        """(height in the robot's own frame, reach, the geom setting the reach: -1 when none sticks out)."""
        if not len(self._local):
            return 0.0, 0.0, -1
        d = self.data
        pts, rot = self.points(), d.xmat[self.body].reshape(3, 3)
        _, reach, k = extent(pts, self._reach, d.xpos[self.body], rot, self._lo, self._hi)
        up = pts @ rot[:, 2]                     # along the chassis's up: the height in the robot's own frame
        return float(up.max() - up.min()), reach, (int(self._of[k]) if k >= 0 else -1)

    def read(self) -> tuple[float, float]:
        """(height, reach) in meters."""
        height, reach, _ = self.read_where()
        return height, reach


def geom_label(model: mujoco.MjModel, g: int) -> str:
    """A geom's name, or with none its body's."""
    return model.geom(g).name or model.body(model.geom_bodyid[g]).name


def measure(world) -> Measures:
    """The robot's measures at the world's current state."""
    m = world.model
    ext = Extent(world)
    x0, x1, y0, y1 = ext.outline
    height, reach, where = ext.read_where()
    pts = ext.points()
    return Measures(mass=float(m.body_subtreemass[m.body_rootid[world.robot_body]]),
                    perimeter=2.0 * ((x1 - x0) + (y1 - y0)),
                    frame_plate=_has_box(m, world.robot_body),
                    height=height, reach=reach,
                    diameter=smallest_circle(pts[:, :2]) if len(pts) else 0.0,
                    reach_geom=geom_label(m, where) if where >= 0 else "")


def _has_box(model, body: int) -> bool:
    return any(model.geom_bodyid[g] == body and model.geom_type[g] == mujoco.mjtGeom.mjGEOM_BOX
               for g in range(model.ngeom))


def smallest_circle(points: np.ndarray) -> float:
    """The diameter of the smallest circle holding every point (n, 2)."""
    return 2.0 * _enclosing(points)[1]


_EPS = 1e-12


def _enclosing(points: np.ndarray) -> tuple[np.ndarray, float]:
    """(center, radius) of the smallest enclosing circle: Welzl's algorithm in its iterative form (shuffled, then
    each point outside the circle so far is on the boundary of the circle of the points before it, expected linear
    time, no recursion)."""
    pts = np.unique(np.asarray(points, dtype=float).reshape(-1, 2), axis=0)
    if not len(pts):
        return np.zeros(2), 0.0
    pts = pts[np.random.default_rng(0).permutation(len(pts))]
    p = [(float(x), float(y)) for x, y in pts]
    tol = 1e-12 * (1.0 + float(np.abs(pts).max()))

    def outside(c, q):
        return math.hypot(q[0] - c[0], q[1] - c[1]) > c[2] + tol

    c = (p[0][0], p[0][1], 0.0)
    for i in range(1, len(p)):
        if not outside(c, p[i]):
            continue
        c = (p[i][0], p[i][1], 0.0)
        for j in range(i):
            if not outside(c, p[j]):
                continue
            c = _two(p[i], p[j])
            for k in range(j):
                if outside(c, p[k]):
                    c = _three(p[i], p[j], p[k])
    return np.array(c[:2]), c[2]


def _two(a, b):
    cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    return (cx, cy, math.hypot(a[0] - cx, a[1] - cy))


def _three(a, b, c):
    """The circle through three points; collinear (or nearly), the circle on the two farthest apart."""
    bx, by, cx, cy = b[0] - a[0], b[1] - a[1], c[0] - a[0], c[1] - a[1]
    d = 2.0 * (bx * cy - by * cx)
    if abs(d) <= _EPS * max(1.0, bx * bx + by * by, cx * cx + cy * cy):
        return max((_two(a, b), _two(a, c), _two(b, c)), key=lambda s: s[2])
    b2, c2 = bx * bx + by * by, cx * cx + cy * cy
    ux, uy = (cy * b2 - by * c2) / d, (bx * c2 - cx * b2) / d
    return (a[0] + ux, a[1] + uy, math.hypot(ux, uy))
