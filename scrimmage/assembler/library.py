"""The part library for the GUI: stock parts with parameter metadata, and part folders.

A part folder the Part tab built from a CAD import has an `import-origin.toml` marker (scrimmage/cad/build.py):
`cad_marker` reads it, and the folder's entry carries it as `cad`, so the robot can place the part where the CAD has it
and attach it to what it hangs on in the CAD. Each stock part names its `root_body` (the chassis's is where an import
part's own root goes).
"""

from __future__ import annotations

import math
import os
import tomllib
from pathlib import Path

from ..cad.build import MARKER
from ..parts.model import PartError, load_part
from ..parts.stock import REGISTRY, _load_all, build_stock

_GROUPS = {"chassis": "Chassis", "mechanisms": "Mechanisms", "swerve": "Drivetrain", "differential": "Drivetrain"}


def _group(stock) -> str:
    return _GROUPS.get(stock.build.__module__.rsplit(".", 1)[-1], "Mechanisms")


def _param(p) -> dict:
    default = list(p.default) if p.kind == "pair" else p.default
    return {"name": p.name, "kind": p.kind, "unit": p.unit, "default": default, "min": p.min,
            "max": None if callable(p.max) else p.max, "max_depends": callable(p.max),
            "choices": list(p.choices) if p.choices is not None else None, "doc": p.doc, "positive": p.positive}


_KINDS: dict[str, dict[str, str]] = {}


def _slot_kinds(s) -> dict[str, str]:
    """{slot: kind} in the stock part's declared slot order, from the part built with default parameters."""
    if s.name not in _KINDS:
        kinds = build_stock(s.name, {}, s.name).slot_kinds()
        _KINDS[s.name] = {n: kinds[n] for n in s.slots if n in kinds} | kinds
    return _KINDS[s.name]


_ROOTS: dict[str, str] = {}


def root_body(name: str) -> str:
    """The stock part's root body (built with default parameters): "chassis" for the chassis."""
    if name not in _ROOTS:
        _ROOTS[name] = build_stock(name, {}, name).bodies[0].name
    return _ROOTS[name]


def stock() -> list[dict]:
    _load_all()
    return [{"name": s.name, "group": _group(s), "doc": s.doc, "note": s.note,
             "params": [_param(p) for p in s.params], "slots": _slot_kinds(s), "mounts": list(s.mounts),
             "root_body": root_body(s.name)}
            for s in sorted(REGISTRY.values(), key=lambda s: s.name)]


def _rel(folder: Path, base: Path | None) -> str:
    if base is not None:
        try:
            return os.path.relpath(folder, base)
        except ValueError:
            pass
    return str(folder.resolve())


def cad_marker(folder: Path) -> dict | None:
    """{import_id, part, origins: {body: [x, y, z]}, attach} from a part folder's import-origin.toml: each body's
    origin in the robot frame (m), and attach "<part>.<body>" (the body of another part from the same import it hangs on
    in the CAD; None when the robot carries it). part is the folder's part name (its part.toml's; None when unreadable).
    None when the folder has no marker or it can't be read (a hand-edited one with a bad origin or attach too)."""
    try:
        raw = tomllib.loads((Path(folder) / MARKER).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    iid, origins = raw.get("import_id"), raw.get("origins")
    if not isinstance(iid, str) or not isinstance(origins, dict):
        return None
    attach = raw.get("attach")
    if attach is not None and not (isinstance(attach, str) and attach.count(".") == 1):
        return None
    out = {}
    for body, o in origins.items():
        if not (isinstance(o, list) and len(o) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                            and math.isfinite(x) for x in o)):
            return None
        out[body] = [float(x) for x in o]
    try:
        part = tomllib.loads((Path(folder) / "part.toml").read_text(encoding="utf-8")).get("part", {}).get("name")
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, AttributeError):
        part = None
    return {"import_id": iid, "part": part if isinstance(part, str) else None, "origins": out, "attach": attach}


def _cad_entry(folder: Path) -> dict | None:
    m = cad_marker(folder)
    return None if m is None else {"import_id": m["import_id"], "part": m["part"], "attach": m["attach"]}


def folders(dirs: list[Path], config_dir: Path | None = None) -> list[dict]:
    """Each subfolder of `dirs` holding a part.toml (or the dir itself), once each even when dirs overlap;
    `config_dir` makes paths relative. A folder that can't be read is skipped."""
    out = []
    seen: set[Path] = set()
    for d in dirs:
        d = Path(d)
        try:
            if not d.is_dir():
                continue
            cands = [d] if (d / "part.toml").is_file() else sorted(p for p in d.iterdir() if (p / "part.toml").is_file())
        except OSError:
            continue
        for c in cands:
            key = c.resolve()
            if key in seen:
                continue
            seen.add(key)
            item = {"name": c.name, "path": _rel(c, config_dir),
                    "slots": {}, "mounts": [], "error": None, "cad": _cad_entry(c)}
            try:
                part = load_part(c)
                item.update(name=part.name, slots=part.slot_kinds(), mounts=[m.name for m in part.mounts])
            except PartError as e:
                item["error"] = str(e)
            out.append(item)
    return out
