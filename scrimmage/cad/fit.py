"""Collision shapes fitted to a body's vertices (numpy only, in the body's frame, meters).

`fit_wheel` is the cylinder about a joint axis through the body's origin (a body's origin is its joint anchor): its
radius is the largest distance of any vertex from the axis, its length the extent along the axis. `fit_box` is an
oriented bounding box: the box along the vertices' principal axes, or the axis-aligned box when that is smaller (a
cube's principal axes are arbitrary). `rot_z_to` is the shortest-arc turn of +z onto a direction, as roll, pitch,
yaw degrees. The plugin overlay (`scrimmage/parts/overlay.py`) uses this module too, so it must stay import-light:
numpy and `scrimmage.cad.mass` only, never trimesh.
"""

import hashlib
import os
import tempfile
import uuid
from pathlib import Path

import numpy as np

from .mass import principal, rpy_deg

WHEEL_FRICTION = 1.2
WHEEL_CONDIM = 4
SHAPED_CACHE = Path(tempfile.gettempdir()) / "scrimmage-cad-shaped"
MIN_SIZE = 1e-4     # m: a box side, cylinder radius or length is at least this (part.toml needs sizes above 0)


def _unit(a) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    return a / np.linalg.norm(a)


def fit_wheel(v_body: np.ndarray, axis: np.ndarray) -> tuple[float, float, np.ndarray]:
    """(radius, length, center) of the cylinder about `axis` (through the origin) that holds every vertex."""
    a = _unit(axis)
    v = np.asarray(v_body, dtype=float)
    s = v @ a
    radial = np.linalg.norm(v - np.outer(s, a), axis=1)
    lo, hi = float(s.min()), float(s.max())
    return max(float(radial.max()), MIN_SIZE), max(hi - lo, MIN_SIZE), a * (lo + hi) / 2


def _box(v: np.ndarray, r: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    p = v @ r                                   # coordinates along r's columns
    lo, hi = p.min(axis=0), p.max(axis=0)
    return hi - lo, r @ ((lo + hi) / 2)


def fit_box(v_body: np.ndarray) -> tuple[list[float], list[float], tuple[float, float, float]]:
    """(full sizes along the box's own x, y, z; center; rot as roll, pitch, yaw degrees) of an oriented box
    holding every vertex."""
    v = np.asarray(v_body, dtype=float)
    centered = v - v.mean(axis=0)
    _, r = principal(centered.T @ centered)     # proper rotation, columns the principal axes
    size, center = _box(v, r)
    size0, center0 = _box(v, np.eye(3))
    if np.prod(np.maximum(size0, MIN_SIZE)) <= np.prod(np.maximum(size, MIN_SIZE)) * (1 + 1e-9):
        size, center, rpy = size0, center0, (0.0, 0.0, 0.0)
    else:
        rpy = rpy_deg(r)
    return [max(float(x), MIN_SIZE) for x in size], [float(x) for x in center], tuple(float(x) for x in rpy)


def rot_z_to(n: np.ndarray) -> tuple[float, float, float]:
    """Roll, pitch, yaw degrees of the shortest-arc rotation taking +z to direction `n`."""
    n = _unit(n)
    z = np.array([0.0, 0.0, 1.0])
    c = float(n @ z)
    if c > 1 - 1e-12:
        return (0.0, 0.0, 0.0)
    if c < -1 + 1e-12:
        return (180.0, 0.0, 0.0)                # half a turn about x
    k = np.cross(z, n)
    s = float(np.linalg.norm(k))
    k /= s
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    r = np.eye(3) + s * kx + (1 - c) * kx @ kx
    return tuple(float(x) for x in rpy_deg(r))


def convex_split(v: np.ndarray, f: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    """The mesh's convex pieces (meters), by `bench.cad.convex_pieces` (imported here, not at the top: it pulls in
    mujoco). Raises BenchError. The mesh is written to a cache file named by its content, so convex_pieces's own
    cache (beside it) saves re-splitting the same body again."""
    from ..bench.cad import convex_pieces, read_mesh
    lines = [f"v {x:.9g} {y:.9g} {z:.9g}" for x, y, z in v] + [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in f]
    text = "\n".join(lines) + "\n"
    SHAPED_CACHE.mkdir(parents=True, exist_ok=True)
    path = SHAPED_CACHE / f"{hashlib.sha256(text.encode()).hexdigest()[:16]}.obj"
    if not path.is_file():
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}")
        tmp.write_text(text)
        os.replace(tmp, path)
    return [read_mesh(p) for p in convex_pieces(path, "m")]
