"""Fingerprints of CAD pieces and matching them across re-imports.

A fingerprint is `{"volume", "area", "extents", "center"}` in SI units as plain floats (6 significant digits), so it
can be stored in the import file. `match` pairs the pieces stored in an import file with the pieces of a re-read
source so per-piece settings follow the pieces even when the CAD file renamed or moved them.
"""

from dataclasses import dataclass, field

import numpy as np

from .mass import mesh_inertial, principal
from .source import Piece

VOLUME_TOL = 0.02       # relative
EXTENT_TOL = 0.02       # relative
CENTER_TOL = 0.005      # meters


def _r(x) -> float:
    return float(f"{float(x):.6g}")


def fingerprint(p: Piece) -> dict:
    v, f = np.asarray(p.v, dtype=float), np.asarray(p.f, dtype=int)
    inertial = mesh_inertial(v, f, 1.0)
    if inertial.mass > 0:
        _, r = principal(inertial.tensor)
    else:
        # Open or empty mesh: the vertex covariance's eigenvectors stand in for the inertia axes.
        cov = np.cov((v - v.mean(axis=0)).T) if len(v) > 1 else np.zeros((3, 3))
        _, r = np.linalg.eigh(np.nan_to_num(cov))
    proj = v @ r
    extents = np.sort(proj.max(axis=0) - proj.min(axis=0)) if len(v) else np.zeros(3)
    if len(f):
        a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
        area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum()
    else:
        area = 0.0
    return {"volume": _r(inertial.mass), "area": _r(area), "extents": [_r(e) for e in extents],
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
