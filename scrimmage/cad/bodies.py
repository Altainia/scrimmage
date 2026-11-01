"""The default split of a CAD source into bodies (pieces that move together), with loose hardware merged in.

A STEP source gives one body per child of its single top-level assembly (a loose piece or a sub-assembly), or one
per top-level assembly when there are several; a loose top-level piece is its own body. A mesh source gives one body
per file. Hardware (bolts, screws, washers... by name or by tiny size) never gets a body of its own: a hardware
piece standing alone joins the body whose pieces' bounding box contains its center, else the nearest one. Hardware
already inside a sub-assembly stays there. No piece is ever left out.
"""

import re

import numpy as np

from .names import sanitize, unique
from .source import Piece, Source

_HARDWARE_NAME = re.compile(r"(?i)\b(bolt|screw|nut|washer|rivet|standoff|spacer|shcs|bhcs|fhcs|nylock|pem|insert)\b")
_MCMASTER = re.compile(r"^\d{4,5}[A-Z]\d{1,4}")
SMALL_FRACTION = 0.01       # a piece this small next to the whole assembly is hardware


def _bounds(p: Piece) -> tuple[np.ndarray, np.ndarray]:
    return p.v.min(axis=0), p.v.max(axis=0)


def assembly_diagonal(pieces: list[Piece]) -> float:
    """Diagonal of the bounding box of all `pieces` together."""
    if not pieces:
        return 0.0
    lo = np.min([p.v.min(axis=0) for p in pieces], axis=0)
    hi = np.max([p.v.max(axis=0) for p in pieces], axis=0)
    return float(np.linalg.norm(hi - lo))


def is_hardware(p: Piece, assembly_diag: float) -> bool:
    """True if `p`'s name says hardware (or is a McMaster part number), or its largest side is under 1% of
    `assembly_diag`."""
    # underscores count as word breaks (\b alone would miss Hex_Nut and bolt_2)
    if _HARDWARE_NAME.search(p.name.replace("_", " ")) or _MCMASTER.match(p.name):
        return True
    lo, hi = _bounds(p)
    return float((hi - lo).max()) < SMALL_FRACTION * assembly_diag


def _group_name(key: str) -> str:
    return re.sub(r"#\d+$", "", key.rsplit("/", 1)[-1])


def default_bodies(src: Source) -> dict[str, list[str]]:
    """Body name -> piece keys, in source order."""
    # first pass: raw bodies, keyed by the group key (or the piece key for a loose piece)
    raw: dict[str, list[Piece]] = {}
    label: dict[str, str] = {}
    tops = {p.group[0] for p in src.pieces if p.group}
    for p in src.pieces:
        if src.kind == "mesh" or not p.group:
            k, name = p.key, p.name
        elif len(tops) == 1:
            if len(p.group) == 1:
                k, name = p.key, p.name
            else:
                k = p.group[1]
                name = _group_name(k)
        else:
            k = p.group[0]
            name = _group_name(k)
        raw.setdefault(k, []).append(p)
        label[k] = name
    diag = assembly_diagonal(src.pieces)
    hw = {p.key for p in src.pieces if is_hardware(p, diag)}
    anchors = {k: ps for k, ps in raw.items() if any(p.key not in hw for p in ps)}
    if not anchors:             # all hardware: nothing to merge into, so keep the split as it is
        anchors = raw
    placed = {k: [] for k in anchors}
    loose = []
    for k, ps in raw.items():
        if k in anchors:
            placed[k] = list(ps)
        else:
            loose.extend(ps)
    for p in loose:
        placed[_nearest(p, anchors)].append(p)
    order = {p.key: i for i, p in enumerate(src.pieces)}
    used: set[str] = set()
    out = {}
    for k, ps in placed.items():
        out[unique(sanitize(label[k]), used)] = [p.key for p in sorted(ps, key=lambda p: order[p.key])]
    return out


def _nearest(p: Piece, bodies: dict[str, list[Piece]]) -> str:
    """The body (key) whose pieces' bounding box holds `p`'s center (the smallest such box), else the body with
    the closest piece center."""
    lo, hi = _bounds(p)
    c = (lo + hi) / 2
    best, best_vol, near, near_d = None, np.inf, None, np.inf
    for k, ps in bodies.items():
        blo = np.min([q.v.min(axis=0) for q in ps], axis=0)
        bhi = np.max([q.v.max(axis=0) for q in ps], axis=0)
        if np.all(c >= blo) and np.all(c <= bhi):
            vol = float(np.prod(bhi - blo))
            if vol < best_vol:
                best, best_vol = k, vol
        for q in ps:
            qlo, qhi = _bounds(q)
            d = float(np.linalg.norm((qlo + qhi) / 2 - c))
            if d < near_d:
                near, near_d = k, d
    return best if best is not None else near
