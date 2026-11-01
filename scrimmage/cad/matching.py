"""Fingerprints of CAD pieces and matching them across re-imports.

A fingerprint is `{"volume", "area", "extents", "center"}` in SI units as plain floats (6 significant digits), so it
can be stored in the import file. `match` pairs the pieces stored in an import file with the pieces of a re-read
source so per-piece settings follow the pieces even when the CAD file renamed or moved them.
"""

from dataclasses import dataclass, field

import numpy as np

from .mass import mesh_inertial
from .source import Piece

VOLUME_TOL = 0.02       # relative
EXTENT_TOL = 0.02       # relative
CENTER_TOL = 0.005      # meters


def _r(x) -> float:
    return float(f"{float(x):.6g}")


def _frame(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two unit vectors perpendicular to `axis` and to each other."""
    helper = np.array([1.0, 0, 0]) if abs(axis[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(axis, helper)
    u /= np.linalg.norm(u)
    return u, np.cross(axis, u)


def _extreme_points(v: np.ndarray, count: int = 256) -> np.ndarray:
    """The vertices that stick out furthest in `count` evenly spread directions: a cheap stand-in for the convex
    hull, so the box search costs the same for a 20-vertex bolt and a 20,000-vertex bracket (side error < 1%)."""
    if len(v) <= count // 4:
        return v
    k = np.arange(count) + 0.5
    z = 1 - 2 * k / count
    t = np.pi * (1 + 5 ** 0.5) * k                          # golden-angle spiral over the sphere
    r = np.sqrt(1 - z * z)
    dirs = np.stack([r * np.cos(t), r * np.sin(t), z])
    return v[np.unique(np.argmax(v @ dirs, axis=0))]


def _extents(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    """Sorted side lengths of a tight oriented bounding box, found by trying several candidate axes (the inertia
    axes and the normals of the biggest faces) and turning the box about each in half-degree steps, keeping the
    smallest volume. Unlike plain inertia axes this does not wobble when a piece with equal principal moments
    (square tube, round stock, cube) is turned."""
    if len(v) < 2:
        return np.zeros(3)
    centered = v - v.mean(axis=0)
    pts = _extreme_points(v)
    pts = pts - pts.mean(axis=0)
    cov = np.nan_to_num(np.cov(centered.T))     # all vertices: the axes should not depend on the subset
    axes = list(np.linalg.eigh(cov)[1].T)
    if len(f):
        a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
        n = np.cross(b - a, c - a)
        area = np.linalg.norm(n, axis=1)
        for i in np.argsort(-area)[:24]:
            if area[i] > 0:
                d = n[i] / area[i]
                if all(abs(d @ o) < 0.999 for o in axes):       # skip near-parallel repeats
                    axes.append(d)
    def box(axis, angles):
        """(volume, extents) of the smallest box about `axis` over `angles`."""
        u, w = _frame(axis)
        along = pts @ axis
        pu, pw = pts @ u, pts @ w
        c, s = np.cos(angles)[:, None], np.sin(angles)[:, None]
        x, y = pu * c + pw * s, -pu * s + pw * c                    # (angles, points)
        ex, ey = x.max(axis=1) - x.min(axis=1), y.max(axis=1) - y.min(axis=1)
        k = int(np.argmin(ex * ey))
        ez = along.max() - along.min()
        return ex[k] * ey[k] * ez, (ex[k], ey[k], ez), angles[k]

    coarse = np.radians(np.arange(0, 90, 3.0))
    fits = [box(axis, coarse) for axis in axes]
    i = min(range(len(axes)), key=lambda j: fits[j][0])
    fine = fits[i][2] + np.radians(np.arange(-3, 3.01, 0.25))       # then refine the best axis only
    best_ext = box(axes[i], fine)[1]
    return np.sort(np.array(best_ext))


def fingerprint(p: Piece) -> dict:
    v, f = np.asarray(p.v, dtype=float), np.asarray(p.f, dtype=int)
    inertial = mesh_inertial(v, f, 1.0)
    extents = _extents(v, f)
    if len(f):
        a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
        area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum()
    else:
        area = 0.0
    return {"volume": _r(abs(inertial.mass)), "area": _r(area), "extents": [_r(e) for e in extents],
            "center": [_r(c) for c in inertial.com]}


@dataclass
class Match:
    pairs: dict[str, str] = field(default_factory=dict)         # old key -> new key
    guessed: set[str] = field(default_factory=set)              # old keys paired by shape alone
    missing: list[str] = field(default_factory=list)            # old keys with no new piece
    added: list[str] = field(default_factory=list)              # new keys no old piece claimed


def _tail(key: str) -> str:
    return key.rsplit("/", 1)[-1].split("#")[0]


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol * max(abs(a), abs(b))


def _same_shape(a: dict, b: dict) -> bool:
    return _close(a["volume"], b["volume"], VOLUME_TOL) and all(
        _close(x, y, EXTENT_TOL) for x, y in zip(a["extents"], b["extents"]))


def _dist(a: dict, b: dict) -> float:
    return float(np.linalg.norm(np.subtract(a["center"], b["center"])))


def match(old: list[dict], new: list[Piece]) -> Match:
    """Pair stored records `{"key", "fp"}` with `new` pieces: the exact key; then the same name with the same shape
    within 5 mm; then the same shape alone (guessed). A new piece is used once; ties go by file order."""
    fps = {p.key: fingerprint(p) for p in new}
    m = Match()
    taken: set[str] = set()
    left = list(old)

    def nearest(rec, ok):
        best = None
        for p in new:                       # file order, so a tie keeps the earlier piece
            if p.key in taken or not _same_shape(rec["fp"], fps[p.key]) or not ok(p):
                continue
            d = _dist(rec["fp"], fps[p.key])
            if best is None or d < best[0]:
                best = (d, p.key)
        return best

    by_key = {p.key for p in new}
    rest = []
    for rec in left:
        if rec["key"] in by_key and rec["key"] not in taken:
            m.pairs[rec["key"]] = rec["key"]
            taken.add(rec["key"])
        else:
            rest.append(rec)
    for stage in (2, 3):
        still = []
        for rec in rest:
            hit = nearest(rec, (lambda p, rec=rec: _tail(p.key) == _tail(rec["key"]))
                          if stage == 2 else (lambda p: True))
            if stage == 2 and hit and hit[0] > CENTER_TOL:
                hit = None
            if hit:
                m.pairs[rec["key"]] = hit[1]
                taken.add(hit[1])
                if stage == 3:
                    m.guessed.add(rec["key"])
            else:
                still.append(rec)
        rest = still
    m.missing = [r["key"] for r in rest]
    m.added = [p.key for p in new if p.key not in taken]
    return m
