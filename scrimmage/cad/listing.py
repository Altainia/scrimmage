"""`scrimmage cad list`: the pieces of a CAD file as rows (name, bounding-box center and size, triangles), grouped by
name, filtered by a name glob or a distance from a point, and as a table or JSON. Positions are in the file's own
axes, in meters; `load_source` is the only reader. Task 3's mesh element uses `matches` for its `select`."""

import difflib
import fnmatch
import math
from dataclasses import dataclass

import numpy as np

from .. import i18n
from .source import STEP_UNITS, UNITS, Source, step_length_unit

Vec = tuple[float, float, float]


@dataclass(frozen=True)
class Row:
    key: str
    name: str
    count: int
    center_m: Vec
    size_m: Vec
    triangles: int


def file_units(src: Source) -> str:
    """The unit the file's own numbers are in: a STEP's (meters when it can't say), or a mesh source's `units`."""
    if src.kind == "step":
        return step_length_unit(src.path) or "m"
    return src.units


def matches(piece_or_row, glob: str) -> bool:
    """True when `glob` (case-sensitive, `*?[]`) fits the piece's name or its key."""
    return fnmatch.fnmatchcase(piece_or_row.name, glob) or fnmatch.fnmatchcase(piece_or_row.key, glob)


def rows(src: Source) -> list[Row]:
    out = []
    for p in src.pieces:
        lo, hi = p.v.min(axis=0), p.v.max(axis=0)
        out.append(Row(p.key, p.name, 1, tuple(float(x) for x in (lo + hi) / 2),
                       tuple(float(x) for x in hi - lo), int(len(p.f))))
    return out


def grouped(rows: list[Row]) -> list[Row]:
    """One row per name, in first-seen order: the count, the combined bounding box, the summed triangles."""
    boxes: dict[str, list] = {}
    for r in rows:
        lo = np.array(r.center_m) - np.array(r.size_m) / 2
        hi = np.array(r.center_m) + np.array(r.size_m) / 2
        if r.name not in boxes:
            boxes[r.name] = [r, lo, hi, 1, r.triangles]
        else:
            b = boxes[r.name]
            b[1], b[2], b[3], b[4] = np.minimum(b[1], lo), np.maximum(b[2], hi), b[3] + 1, b[4] + r.triangles
    return [Row(first.key, first.name, n, tuple(float(x) for x in (lo + hi) / 2), tuple(float(x) for x in hi - lo), tri)
            for first, lo, hi, n, tri in boxes.values()]


def near(rows: list[Row], point: Vec, within: float) -> list[Row]:
    """The rows whose center is within `within` meters of `point`."""
    return [r for r in rows if math.dist(r.center_m, point) <= within]


def sort_rows(rows: list[Row], order: str) -> list[Row]:
    """`order` is "size" (largest bounding-box volume first, ties in file order), "name", or "file" (as read)."""
    if order == "size":
        return sorted(rows, key=lambda r: -(r.size_m[0] * r.size_m[1] * r.size_m[2]))
    if order == "name":
        return sorted(rows, key=lambda r: r.name)
    return list(rows)


def close_names(rows: list[Row], glob: str) -> list[str]:
    """Up to 5 piece names near a glob that matched nothing."""
    bare = glob.translate({ord(c): None for c in "*?[]"})
    return difflib.get_close_matches(bare, list(dict.fromkeys(r.name for r in rows)), n=5)


def _num(x: float) -> str:
    return f"{x:.6g}" if abs(x) >= 1e-9 else "0"


def _xyz(v: Vec, scale: float) -> str:
    return " x ".join(_num(x / scale) for x in v)


def table_lines(rows: list[Row], units: str, guessed: bool = False) -> list[str]:
    """A header, then a line per row. A row with a count covers all its pieces together. `guessed` says the file's
    unit was guessed from its size (an STL or OBJ doesn't carry one)."""
    scale = STEP_UNITS[units]
    shown = i18n.t("{units} (guessed)", units=units) if guessed else units
    lines = [i18n.t("{pieces} pieces in {lines} lines. Center and size in {units}, then in m (the file's own axes).",
                    pieces=sum(r.count for r in rows), lines=len(rows), units=shown)]
    for r in rows:
        name = r.name if r.count == 1 else f"{r.name} {i18n.t('×{count}', count=r.count)}"
        lines.append(i18n.t("{name}  center {center} | {center_m}  size {size} | {size_m}  triangles {triangles}",
                            name=name, center=_xyz(r.center_m, scale), center_m=_xyz(r.center_m, 1.0),
                            size=_xyz(r.size_m, scale), size_m=_xyz(r.size_m, 1.0), triangles=r.triangles))
    return lines


def _r(x: float) -> float:
    return float(f"{x:.7g}")      # 7 digits, a float32 mesh's precision: 0.15000000596 reads 0.15


def to_json(rows: list[Row], units: str, file: str = "") -> dict:
    scale = STEP_UNITS[units]
    return {"file": file, "units": units, "pieces": [
        {"key": r.key, "name": r.name, "center_m": [_r(x) for x in r.center_m],
         "size_m": [_r(x) for x in r.size_m], "center_file": [_r(x / scale) for x in r.center_m],
         "size_file": [_r(x / scale) for x in r.size_m],
         "triangles": r.triangles} for r in rows]}
