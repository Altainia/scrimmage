"""Measuring a robot model for inspection (design Call I4): its weight, frame perimeter, height, reach beyond the frame
and the smallest circle holding it seen from above. Every geom of the robot counts (the bodies under the robot body's
root), colliding or not: an elevator's stages are built collide = false, and the rules count them.

- mass: the robot's subtree mass (the root's), everything the model weighs.
- perimeter: 2 x (length + width) of the frame outline (terrain.climb.frame_outline over every geom): the largest box
  on the robot body by top-view area (the stock chassis's frame plate, so bumpers are out, as R104 measures), or,
  without a box (`frame_plate` False), the outline of all the body's geoms.
- height: the highest corner of the geoms' bounding boxes (MuJoCo's geom_aabb, turned with the geom) above the floor,
  z = 0. Bumpers count (answer 7).
- reach: the largest horizontal distance, in the robot body's own x-y plane, from the frame outline to a corner of a
  geom not on the robot body itself (so its frame, bumpers and heading marker never count), the climb runs'
  extension over every geom.
- diameter: the smallest circle holding every geom's box corners projected on the floor, bumpers included (2013).

All of it at the world's current state: `measure(world)` right after building is the start configuration (answer 4).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import mujoco
import numpy as np

from ..terrain.climb import frame_outline


@dataclass(frozen=True)
class Measures:
    mass: float          # kg
    perimeter: float     # m
    frame_plate: bool    # the frame outline is a box geom's (else the outline of all the robot body's geoms)
    height: float        # m
    reach: float         # m
    diameter: float      # m


class Extent:
    """The robot's height and reach at the world's current state. The geoms, their boxes' corners and the frame
    outline are found once here, so read() (called every 20 ms of a scenario) only turns the corners."""

    def __init__(self, world):
        m = world.model
        self.model, self.data, self.body = m, world.data, world.robot_body
        root = m.body_rootid[self.body]
        mine = [g for g in range(m.ngeom) if m.body_rootid[m.geom_bodyid[g]] == root]
        reach = [g for g in mine if m.geom_bodyid[g] != self.body]
        self._geoms = np.array(reach + [g for g in mine if m.geom_bodyid[g] == self.body], dtype=np.int64)
        self._reach = len(reach)        # the first _reach of _geoms count for the reach
        signs = np.array(list(np.ndindex(2, 2, 2)), dtype=float) * 2.0 - 1.0          # (8, 3)
        self._corners = m.geom_aabb[self._geoms, None, :3] + signs * m.geom_aabb[self._geoms, None, 3:]  # (n, 8, 3)
        x0, x1, y0, y1 = frame_outline(m, self.body, colliding_only=False)
        self.outline = (x0, x1, y0, y1)
        self._lo, self._hi = np.array([x0, y0]), np.array([x1, y1])

    def points(self) -> np.ndarray:
        """The corners of every robot geom's bounding box, in the world: (geoms, 8, 3)."""
        d = self.data
        rot = d.geom_xmat[self._geoms].reshape(-1, 3, 3)
        return self._corners @ rot.transpose(0, 2, 1) + d.geom_xpos[self._geoms][:, None, :]

    def read(self) -> tuple[float, float]:
        """(height, reach) in meters."""
        if not len(self._geoms):
            return 0.0, 0.0
        d = self.data
        pts = self.points()
        height = float(pts[..., 2].max())        # the floor is z = 0
        if not self._reach:
            return height, 0.0
        rc = d.xmat[self.body].reshape(3, 3)
        local = ((pts[:self._reach].reshape(-1, 3) - d.xpos[self.body]) @ rc[:, :2]).reshape(-1, 8, 2)  # body x, y
        out = np.maximum(np.maximum(local.max(axis=1) - self._hi, self._lo - local.min(axis=1)), 0.0)
        return height, float(np.sqrt((out * out).sum(axis=1)).max())


def measure(world) -> Measures:
    """The robot's measures at the world's current state."""
    m = world.model
    ext = Extent(world)
    x0, x1, y0, y1 = ext.outline
    height, reach = ext.read()
    pts = ext.points()
    return Measures(mass=float(m.body_subtreemass[m.body_rootid[world.robot_body]]),
                    perimeter=2.0 * ((x1 - x0) + (y1 - y0)),
                    frame_plate=_has_box(m, world.robot_body),
                    height=height, reach=reach,
                    diameter=smallest_circle(pts[..., :2].reshape(-1, 2)) if len(pts) else 0.0)


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
