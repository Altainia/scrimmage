"""Fitting an axis from a clicked triangle (numpy only, CAD coordinates in and out).

`round` finds a hinge axis from a hole or round face: grow a region across edges whose normals differ by under 20
degrees (a curved face's facets differ by a few degrees; a cylinder's cap is 90), take the direction every normal is
perpendicular to (the smallest eigenvector of the area-weighted sum of n nT), and put the point on the fitted circle's
center at the region's mid-height. `flat` finds a slide direction: grow coplanar triangles (2 degrees) and return the
area-weighted normal and the region's centroid. Meshes have shared vertex indices, so triangles meet where they share
two indices. Bad clicks raise `CadError` in plain words; a NaN axis is never returned.
"""

from collections import defaultdict, deque

import numpy as np

from . import CadError
from .source import Piece

ROUND_ANGLE_DEG = 20.0
FLAT_ANGLE_DEG = 2.0
CYLINDER_RATIO = 0.05       # smallest/largest eigenvalue above this is not a cylinder
NOT_ROUND = "that face isn't round; click inside a hole or on a round face"
NOT_A_FACE = "that spot has no surface to measure; click a different part of the piece"


def _grow(p: Piece, start: int, normals: np.ndarray, valid: np.ndarray, angle_deg: float) -> list[int]:
    """Triangles reachable from `start` across shared edges whose normals differ by less than `angle_deg`."""
    edges = defaultdict(list)
    for t, tri in enumerate(p.f):
        if valid[t]:
            for a, b in ((0, 1), (1, 2), (2, 0)):
                edges[frozenset((int(tri[a]), int(tri[b])))].append(t)
    cos_limit = np.cos(np.radians(angle_deg))
    seen = {start}
    queue = deque([start])
    while queue:
        t = queue.popleft()
        tri = p.f[t]
        for a, b in ((0, 1), (1, 2), (2, 0)):
            for u in edges[frozenset((int(tri[a]), int(tri[b])))]:
                if u not in seen and float(normals[t] @ normals[u]) > cos_limit:
                    seen.add(u)
                    queue.append(u)
    return sorted(seen)


def fit_axis(p: Piece, triangle: int, kind: str) -> tuple[np.ndarray, np.ndarray]:
    """(point, unit direction) in CAD coordinates for the face under triangle `triangle`; kind "round" or "flat"."""
    if not 0 <= triangle < len(p.f):
        raise CadError("That click is outside the piece: click on the piece itself.")
    tv = p.v[p.f]
    cross = np.cross(tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0])
    length = np.linalg.norm(cross, axis=1)
    valid = length > 1e-18
    if not valid[triangle]:
        raise CadError(NOT_A_FACE)
    area = length / 2
    normals = np.zeros_like(cross)
    normals[valid] = cross[valid] / length[valid, None]

    if kind == "flat":
        region = _grow(p, triangle, normals, valid, FLAT_ANGLE_DEG)
        d = (normals[region] * area[region, None]).sum(axis=0)
        d /= np.linalg.norm(d)
        centroid = (tv[region].mean(axis=1) * area[region, None]).sum(axis=0) / area[region].sum()
        return centroid, d

    region = _grow(p, triangle, normals, valid, ROUND_ANGLE_DEG)
    n = normals[region]
    w = area[region]
    vals, vecs = np.linalg.eigh((n.T * w) @ n)        # ascending
    if vals[2] <= 0 or vals[0] > CYLINDER_RATIO * vals[2] or vals[1] <= CYLINDER_RATIO * vals[2]:
        # a flat face (one big eigenvalue) and a saddle or sphere (three) are not cylinders either
        raise CadError(NOT_ROUND)
    d = vecs[:, 0]
    d = d if d[np.argmax(np.abs(d))] > 0 else -d       # the same click always gives the same sign
    pts = p.v[np.unique(p.f[region])]
    u = np.cross(d, [1.0, 0, 0] if abs(d[0]) < 0.9 else [0, 1.0, 0])
    u /= np.linalg.norm(u)
    v = np.cross(d, u)
    x, y = pts @ u, pts @ v
    sol, *_ = np.linalg.lstsq(np.stack([2 * x, 2 * y, np.ones_like(x)], 1), x * x + y * y, rcond=None)
    along = pts @ d
    point = sol[0] * u + sol[1] * v + d * (along.min() + along.max()) / 2
    if not (np.all(np.isfinite(point)) and np.all(np.isfinite(d))):
        raise CadError(NOT_ROUND)
    return point, d
