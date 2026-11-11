"""A `kind = "mesh"` field element: a static shape from a CAD file (K4 D3).

`file` is a STEP, an STL or OBJ file (or a folder of them), relative to the range file. `select` lists the pieces to
use, as globs on a piece's name or its key (`scrimmage cad list` shows both); left out, every piece is used. `units`
(only when the file can't say: an STL or OBJ) and `scale` read the file's numbers as the `cad` piece kind's do. `pos`
puts the file's origin on the field, after the file is turned by `rpy_deg` (roll x, pitch y, yaw z, fixed axes, as a
plate's); a file drawn Y-up needs `rpy_deg = [90, 0, 0]`. `ground = true` makes the shape floor for the terrain
signals, as a box's does.

The selected pieces are read once and cached (`MESH_CACHE`, keyed by the file's bytes, `select`, `units` and `scale`),
then each piece is split into convex parts on its own (`convex_split`), in the file's axes, so moving the element
(`pos`, `rpy_deg`) re-reads nothing. A piece that isn't a closed solid (a sheet body, a flat piece) can't be split:
it is left out, and one warning counts and names them. This module imports no CAD reader until a range has a
mesh element.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import i18n
from ..cad.fit import CONVEX_SPLIT_VERSION, SHAPED_CACHE
from .spec import RangeError, _Reader

MAX_MESH_TRIANGLES = 200_000    # choice: a few STAGE-sized assemblies; the 2024 field's STAGE is well under it, and a
                                # whole field (millions) would take minutes to split and a model of that many geoms
FIELD_MARGIN = 1.0      # choice: m a selection may be larger than the field before it's a units mistake (the 2024
                        # CAD's guardrail and carpet pieces are up to 0.1 m longer than the field)
OUTSIDE_WARN = 0.5      # choice: m an element may reach outside the field before range check warns (a wrong pos or
                        # rpy_deg puts a STAGE through the guardrail)
MESH_CACHE = SHAPED_CACHE / "mesh-elements"
CACHE_VERSION = 2       # in the cache key: raise it when what is stored changes (2: notes by kind)


@dataclass(frozen=True, eq=False)
class Mesh:
    """A static mesh element. `parts` are convex parts (vertices, faces) in m, in the file's axes after `units` and
    `scale`; the body is welded at `pos` turned by `mesh_rotation`. `lo` and `hi` are the parts' bounding box in the
    field, `center` its plan center (the footprint is the box). `pieces` are the keys selected."""
    name: str
    file: Path
    select: tuple[str, ...]
    units: str | None
    scale: float
    pos: tuple[float, float, float]
    rpy_deg: tuple[float, float, float]
    friction: float
    ground: bool
    parts: tuple[tuple[np.ndarray, np.ndarray], ...]
    lo: np.ndarray
    hi: np.ndarray
    center: tuple[float, float]
    triangles: int
    pieces: tuple[str, ...]
    yaw_deg: float = 0.0
    warnings: tuple[str, ...] = ()


def mesh_rotation(e: Mesh) -> np.ndarray:
    """The turn `rpy_deg` makes: roll about x, then pitch about y, then yaw about z (fixed axes), as a plate's."""
    from .pieces import _rpy
    return _rpy(e.rpy_deg)


def placed(v: np.ndarray, pos, rpy_deg) -> np.ndarray:
    """Vertices in the file's axes (m) as they sit on the field."""
    from .pieces import _rpy
    return np.asarray(v, dtype=float) @ _rpy(rpy_deg).T + np.asarray(pos, dtype=float)


def _digest(path: Path, key: str) -> str:
    """The cache key: the file's bytes (a folder's mesh files, by name and bytes) and the settings."""
    from ..cad.source import MESH_SUFFIXES
    h = hashlib.sha256()
    for p in sorted(q for q in path.iterdir() if q.suffix.lower() in MESH_SUFFIXES) if path.is_dir() else [path]:
        if path.is_dir():
            h.update(p.name.encode() + b"\0")
        h.update(p.read_bytes())
    h.update(key.encode())
    return h.hexdigest()


def _write(path: Path, meta: dict, arrays: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}")
    try:
        with open(tmp, "wb") as fh:
            np.savez(fh, meta=np.array(json.dumps(meta)), **arrays)
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)        # a cache that can't be written is only a slower next time


def _read(path: Path, count_key: str):
    """(meta, arrays) of a cache file, or None (a miss) when it is missing, cut short, damaged, or doesn't hold what
    its meta says: `count_key` is "keys" (a selection) or "count" (convex parts), the list or number of v/f pairs."""
    try:
        with np.load(path, allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
            arrays = {k: z[k] for k in z.files if k != "meta"}
        n = meta[count_key]
        n = len(n) if isinstance(n, list) else int(n)
        if not all(f"v{i}" in arrays and f"f{i}" in arrays for i in range(n)):
            return None
        return meta, arrays
    except (OSError, ValueError, KeyError, TypeError, EOFError, zipfile.BadZipFile):
        return None


def _cap(items, r: _Reader) -> int:
    """The selection's triangles; an error that names the biggest pieces when it is over MAX_MESH_TRIANGLES."""
    triangles = sum(len(f) for _, _, f in items)
    if triangles > MAX_MESH_TRIANGLES:
        big = sorted(items, key=lambda it: -len(it[2]))[:5]
        raise r.err(i18n.t("selects {triangles} triangles, more than the {most} one element may have; select fewer "
                           "pieces (a narrower select, or several elements). Largest: {largest}",
                           triangles=triangles, most=MAX_MESH_TRIANGLES,
                           largest=", ".join(i18n.t("{key} ({n} triangles)", key=k, n=len(f)) for k, _, f in big)))
    return triangles


def _load(path: Path, select: tuple[str, ...], units: str | None, scale: float, r: _Reader):
    """(the selection as (key, v, f) in m, the file's axes; the kinds of cad_factor's notes, which cad_note words),
    and the cache key."""
    if not path.exists():
        raise r.err(i18n.t("file {file} not found; give a path relative to the range file", file=str(path)))
    digest = _digest(path, json.dumps([list(select), units, scale, CACHE_VERSION]))
    cached = _read(MESH_CACHE / f"{digest}.npz", "keys")
    if cached is not None:
        meta, arrays = cached
        return [(k, arrays[f"v{i}"], arrays[f"f{i}"]) for i, k in enumerate(meta["keys"])], meta["notes"], digest
    from ..cad import CadError
    from ..cad.listing import close_names, matches, rows
    from ..cad.source import load_source
    from .pieces import cad_factor
    factor, load_units, notes = cad_factor(r, path)
    try:
        src = load_source(path, load_units)
    except CadError as e:
        raise r.err(str(e)) from None
    chosen = src.pieces
    if select:
        chosen = []
        for glob in select:
            found = [p for p in src.pieces if matches(p, glob)]
            if not found:
                close = close_names(rows(src), glob)
                raise r.err(i18n.t("select {glob} matches no piece in {file}; close names: {names} ({command} shows "
                                   "them all)", glob=repr(glob), file=path.name, command="scrimmage cad list",
                                   names=", ".join(close) if close else i18n.t("none")))
            chosen += [p for p in found if not any(p is c for c in chosen)]
    items = [(p.key, np.ascontiguousarray(p.v * factor), np.asarray(p.f, dtype=np.int32)) for p in chosen]
    _cap(items, r)              # before the write: an over-cap selection isn't cached
    arrays = {}
    for i, (_, v, f) in enumerate(items):
        arrays[f"v{i}"], arrays[f"f{i}"] = v, f
    _write(MESH_CACHE / f"{digest}.npz", {"keys": [k for k, _, _ in items], "notes": notes}, arrays)
    return items, notes, digest


def selection(path: Path, select: tuple[str, ...], units: str | None, scale: float,
              r: _Reader) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """The selected pieces as (key, vertices, faces) in m, in the file's axes: read from the cache when this file and
    these settings were read before (then no CAD reader is imported)."""
    return _load(Path(path), tuple(select), units, scale, r)[0]


def _split(items, digest: str, r: _Reader):
    """(the convex parts, the keys of the pieces left out), per piece; cached beside the selection."""
    name = f"{digest}.s{CONVEX_SPLIT_VERSION}.parts.npz"     # the split's identity is in the name
    cached = _read(MESH_CACHE / name, "count")
    if cached is not None:
        meta, arrays = cached
        return [(arrays[f"v{i}"], arrays[f"f{i}"]) for i in range(meta["count"])], meta["left_out"]
    from ..bench.cad import check_closed, weld
    from ..bench.spec import BenchError
    from ..cad.fit import convex_split
    parts, left_out = [], []
    for key, v, f in items:
        try:
            check_closed(*weld(v, f), Path(key))
            parts += convex_split(v, f)
        except BenchError as e:
            if e.code:              # coacd missing, or found nothing: not this piece's shape
                raise r.err(str(e)) from None
            left_out.append(key)
    arrays = {}
    for i, (v, f) in enumerate(parts):
        arrays[f"v{i}"], arrays[f"f{i}"] = np.asarray(v, dtype=float), np.asarray(f, dtype=np.int32)
    _write(MESH_CACHE / name, {"count": len(parts), "left_out": left_out}, arrays)
    return parts, left_out


def _text_list(r: _Reader, key: str) -> tuple[str, ...]:
    if not r.has(key):
        return ()
    v = r.t[key]
    if not isinstance(v, list) or not v or any(not isinstance(x, str) for x in v):
        raise r.err(i18n.t("{key} must be a list of text, not {value}", key=key, value=repr(v)))
    return tuple(v)


def parse_mesh(r: _Reader, name: str, base_dir: Path, size: tuple[float, float]) -> Mesh:
    """A mesh element from its table, with every mistake a RangeError naming the file and the element."""
    from ..checks import worded
    from .elements import DEFAULT_FRICTION, _bool
    from .pieces import cad_note

    file = Path(r.text("file"))
    path = file if file.is_absolute() else Path(base_dir) / file
    select = _text_list(r, "select")
    units = r.text("units") if r.has("units") else None
    scale = r.num("scale", 1.0, positive=True)
    pos = r.nums("pos", 3, (0.0, 0.0, 0.0))
    rpy_deg = r.nums("rpy_deg", 3, (0.0, 0.0, 0.0))
    friction = r.num("friction", DEFAULT_FRICTION, positive=True)
    ground = _bool(r, "ground", False)
    items, notes, digest = _load(path, select, units, scale, r)
    triangles = _cap(items, r)
    every = np.concatenate([placed(v, pos, rpy_deg) for _, v, _ in items])
    low, high = every.min(axis=0), every.max(axis=0)
    across = high - low
    if across[0] > size[0] + FIELD_MARGIN or across[1] > size[1] + FIELD_MARGIN:
        raise r.err(i18n.t("selects pieces {x} by {y} m across, larger than the {length} by {width} m field; select "
                           "fewer pieces, or check units (m, mm or in) and scale", x=f"{across[0]:.4g}",
                           y=f"{across[1]:.4g}", length=f"{size[0]:.4g}", width=f"{size[1]:.4g}"))
    parts, left_out = _split(items, digest, r)
    if not parts:
        raise r.err(i18n.t("has no closed solid among its {count} selected pieces; export closed solids, not "
                           "surfaces", count=len(items)))
    warnings = [cad_note(kind, r, path) for kind in notes]      # worded again in each Language
    reach = max(-low[0], -low[1], high[0] - size[0], high[1] - size[1])
    if reach > OUTSIDE_WARN:
        warnings.append(worded(lambda: i18n.t(
            "{path}: {where}: reaches {reach} m outside the {length} by {width} m field (x {x0} to {x1} m, y {y0} to "
            "{y1} m); check {pos_key} and {rpy_key}", path=r.path, where=r.where, reach=f"{reach:.2g}",
            length=f"{size[0]:.4g}", width=f"{size[1]:.4g}", x0=f"{low[0]:.3g}", x1=f"{high[0]:.3g}",
            y0=f"{low[1]:.3g}", y1=f"{high[1]:.3g}", pos_key="pos", rpy_key="rpy_deg")))
    if left_out:
        warnings.append(worded(lambda: i18n.t(
            "{path}: {where}: left out {count} of {total} selected pieces, which can't be made into a solid (not "
            "closed, or flat): {names}", path=r.path, where=r.where, count=len(left_out), total=len(items),
            names=", ".join(left_out[:5])) if len(left_out) <= 5 else i18n.t(
            "{path}: {where}: left out {count} of {total} selected pieces, which can't be made into a solid (not "
            "closed, or flat): {names}, and {more} more", path=r.path, where=r.where, count=len(left_out),
            total=len(items), names=", ".join(left_out[:5]), more=len(left_out) - 5)))
    placed_parts = [placed(v, pos, rpy_deg) for v, _ in parts]
    lo = np.min([p.min(axis=0) for p in placed_parts], axis=0)
    hi = np.max([p.max(axis=0) for p in placed_parts], axis=0)
    return Mesh(name=name, file=path, select=select, units=units, scale=scale, pos=pos, rpy_deg=rpy_deg,
                friction=friction, ground=ground, parts=tuple((np.asarray(v), np.asarray(f)) for v, f in parts),
                lo=lo, hi=hi, center=(float((lo[0] + hi[0]) / 2.0), float((lo[1] + hi[1]) / 2.0)),
                triangles=triangles, pieces=tuple(k for k, _, _ in items), warnings=tuple(warnings))
