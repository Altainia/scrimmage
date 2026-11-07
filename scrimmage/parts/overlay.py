"""The plugin overlay: `scrimmage-edits.toml` beside a plugin-made part.toml (the FreeCAD add-on writes part.toml
again on every export; the overlay holds the student's edits and survives that).

    total_mass = 12.0                  # scales the bodies whose mass isn't set below, so the part weighs this
    [body.<name>]
    mass = 2.5                         # kg; the body's inertia scales with it (center of mass kept)
    collision = "wheel"                # none | wheel | shaped | simple, as in the CAD import
    grip = "nitrile"                   # the body's grip material, or a measured friction (parts/grip.py)
    [body.<name>.joint]                # range, spring, damping, frictionloss, limit_solref, as in part.toml
    [[motor]] [[encoder]] [[gyro]] [[mount]]     # added to the part, same shape as in part.toml

Edits naming a body that's gone come back as warnings, not errors. This module needs only the standard library and
numpy (through scrimmage.cad.fit); `load_part` imports it only when the file exists.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import numpy as np

from .. import i18n
from ..cad.fit import WHEEL_CONDIM, WHEEL_FRICTION, convex_split, fit_box, fit_wheel, rot_z_to
from .model import Part, PartError, part_from_dict, part_to_dict, quat_from_rpy

OVERLAY = "scrimmage-edits.toml"
COLLISION = ("none", "wheel", "shaped", "simple")
BODY_KEYS = ("mass", "collision", "joint", "grip")
JOINT_KEYS = ("range", "spring", "damping", "frictionloss", "limit_solref")
TOP_KEYS = ("total_mass", "body", "motor", "encoder", "gyro", "mount")


def _err(msg: str) -> PartError:
    return PartError(f"{OVERLAY}: {msg}")


def _check_keys(table, allowed, where: str) -> None:
    if not isinstance(table, dict):
        raise _err(i18n.t("{where} must be a table", where=where))
    for k in table:
        if k not in allowed:
            raise _err(i18n.t("unknown key {key} in {where}; allowed: {allowed}",
                              key=repr(k), where=where, allowed=', '.join(allowed)))


def _number(x, what: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not np.isfinite(x) or x <= 0:
        raise _err(i18n.t("{what} must be a number above 0, not {value}", what=what, value=repr(x)))
    return float(x)


def _rot(rpy) -> np.ndarray:
    w, x, y, z = quat_from_rpy(rpy)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def _mesh(b, base_dir: Path | None) -> tuple[np.ndarray, np.ndarray] | None:
    """The vertices and triangles of all the body's mesh geoms, in the body's frame, or None."""
    from ..bench.cad import read_mesh          # not at the top: it pulls in mujoco
    if base_dir is None:
        return None
    vs, fs, n = [], [], 0
    for g in b.geoms:
        if g.shape != "mesh":
            continue
        v, f = read_mesh(Path(base_dir) / g.size)
        vs.append(v @ _rot(g.rot).T + np.array(g.pos))
        fs.append(f + n)
        n += len(v)
    return (np.vstack(vs), np.vstack(fs)) if vs else None


def _collision(b: dict, orig, mode: str, base_dir, warn: list[str]) -> None:
    """Replace body `b`'s (a part.toml dict) colliding geoms by `mode`'s, fitted to its mesh geoms."""
    keep = [g for g in b["geom"] if not g.get("collide", True)]
    if mode == "none":
        b["geom"] = keep
        return
    try:
        m = _mesh(orig, base_dir)
    except Exception as e:                      # BenchError: a missing or unreadable mesh
        warn.append(i18n.t("{overlay}: body {name}: collision {mode} needs its mesh, which can't be read "
                           "({error}); its collision was left as it was",
                           overlay=OVERLAY, name=orig.name, mode=repr(mode), error=e))
        return
    if m is None:
        warn.append(i18n.t("{overlay}: body {name} has no mesh to fit collision {mode} to; its collision was left "
                           "as it was", overlay=OVERLAY, name=orig.name, mode=repr(mode)))
        return
    v, f = m
    geoms: list[dict] = []
    if mode == "wheel":
        j = orig.joint
        if j is not None and j.kind == "hinge":
            axis = np.array(j.axis, dtype=float)
            radius, length, center = fit_wheel(v, axis)
            g: dict = {"cylinder": [radius, length], "friction": WHEEL_FRICTION, "condim": WHEEL_CONDIM}
            if np.any(center):
                g["pos"] = [float(x) for x in center]
            big = int(np.argmax(np.abs(axis)))
            if abs(axis[big]) > 1 - 1e-9:
                if big != 2:
                    g["axis"] = "xyz"[big]
            else:
                g["rot"] = list(rot_z_to(axis))
            geoms = [g]
        else:
            warn.append(i18n.t("{overlay}: body {name} is set to touch things as a wheel, but a wheel needs a "
                               "hinge (it turns); it touches things as a simple box instead",
                               overlay=OVERLAY, name=orig.name))
    elif mode == "shaped":
        geoms = _shaped(orig.name, v, f, base_dir, warn)
    if not geoms:
        size, center, rpy = fit_box(v)
        g = {"box": size}
        if any(center):
            g["pos"] = center
        if any(rpy):
            g["rot"] = list(rpy)
        geoms = [g]
    b["geom"] = keep + geoms


def _shaped(name: str, v: np.ndarray, f: np.ndarray, base_dir, warn: list[str]) -> list[dict]:
    """Convex-piece geoms for the mesh. The pieces live in <part folder>/meshes/_overlay/<hash>_NNN.obj (found there
    again on the next load, so coacd doesn't re-run); a folder that can't be written falls back to the temp cache."""
    from ..bench.cad import write_obj
    tag = hashlib.sha256(np.ascontiguousarray(v).tobytes() + np.ascontiguousarray(f).tobytes()).hexdigest()[:16]
    rel = Path("meshes") / "_overlay"
    if base_dir is not None:
        have = sorted((Path(base_dir) / rel).glob(f"{tag}_*.obj"))
        if have:
            return [{"mesh": (rel / p.name).as_posix()} for p in have]
    try:
        pieces = convex_split(v, f)
    except Exception as e:                      # BenchError: no coacd, an open mesh, no pieces
        warn.append(i18n.t("{overlay}: body {name} can't touch things as shaped ({error}); it touches things as a "
                           "simple box instead", overlay=OVERLAY, name=name, error=e))
        return []

    def write(folder: Path) -> list[Path]:
        folder.mkdir(parents=True, exist_ok=True)
        out = []
        for i, (pv, pf) in enumerate(pieces):
            out.append(folder / f"{tag}_{i:03d}.obj")
            write_obj(out[-1], pv, pf)
        return out

    try:
        if base_dir is None:
            raise OSError("no part folder")
        write(Path(base_dir) / rel)
        return [{"mesh": (rel / f"{tag}_{i:03d}.obj").as_posix()} for i in range(len(pieces))]
    except OSError:
        from ..cad.fit import SHAPED_CACHE
        warn.append(i18n.t("{overlay}: body {name}: the part folder can't be written, so its shaped collision "
                           "pieces are kept in the temporary folder instead", overlay=OVERLAY, name=name))
        return [{"mesh": p.as_posix()} for p in write(SHAPED_CACHE / "overlay")]


def apply_overlay(part: Part, edits: dict, source: str) -> tuple[Part, list[str]]:
    """The part with `edits` (the parsed overlay file; `source` names it for messages) applied, and warnings."""
    _check_keys(edits, TOP_KEYS, "the file")
    d = copy.deepcopy(part_to_dict(part))
    by_name = {b["name"]: b for b in d["body"]}
    orig = {b.name: b for b in part.bodies}
    warns: list[str] = []
    fixed: set[str] = set()
    bodies = edits.get("body", {})
    if not isinstance(bodies, dict):
        raise _err(i18n.t("{body_name_table} tables expected", body_name_table="[body.<name>]"))
    for name, e in bodies.items():
        _check_keys(e, BODY_KEYS, f"[body.{name}]")
        if name not in by_name:
            warns.append(i18n.t("{overlay}: body {name} is no longer in the part; its edits were dropped",
                                overlay=OVERLAY, name=name))
            continue
        b = by_name[name]
        if "mass" in e:
            new = _number(e["mass"], f"[body.{name}] mass")
            _set_mass(b, new)
            fixed.add(name)
        if "collision" in e:
            mode = e["collision"]
            if mode not in COLLISION:
                raise _err(i18n.t("{body_name_table} collision must be one of {collision}, not {mode}",
                                  collision=', '.join(COLLISION), mode=repr(mode),
                                  body_name_table="[body.{}]".format(name)))
            _collision(b, orig[name], mode, part.base_dir, warns)
        if "joint" in e:
            je = e["joint"]
            _check_keys(je, JOINT_KEYS, f"[body.{name}.joint]")
            if "joint" not in b:
                warns.append(i18n.t("{overlay}: body {name} has no joint; its joint edits were dropped",
                                    overlay=OVERLAY, name=name))
            else:
                b["joint"].update(je)
    if "total_mass" in edits:
        total = _number(edits["total_mass"], "total_mass")
        free = [b for b in d["body"] if b["name"] not in fixed]
        left = total - sum(b["mass"] for b in d["body"] if b["name"] in fixed)
        old = sum(b["mass"] for b in free)
        if not free:
            warns.append(i18n.t("{overlay}: total_mass was ignored: every body has its own mass", overlay=OVERLAY))
        elif old > 0:
            if left <= 0:
                raise _err(i18n.t("total_mass {total} kg is no more than the masses set on the bodies; raise it",
                                  total=total))
            for b in free:
                _set_mass(b, b["mass"] * left / old)
    for key in ("motor", "encoder", "gyro", "mount"):
        if key in edits:
            if not isinstance(edits[key], list) or not all(isinstance(x, dict) for x in edits[key]):
                raise _err(i18n.t("[[{key}]] entries expected", key=key))
            d.setdefault(key, []).extend(copy.deepcopy(edits[key]))
    out = part_from_dict(d, source, part.base_dir)
    grips = {name: e["grip"] for name, e in bodies.items() if "grip" in e and name in by_name}
    if grips:
        from .grip import apply_grip
        out, w = apply_grip(out, grips, OVERLAY)
        warns += w
        out.warnings = []                       # load_part sets them (warns)
    return out, warns


def _set_mass(b: dict, new: float) -> None:
    k = new / b["mass"] if b["mass"] > 0 else None
    b["mass"] = new
    if "inertia" in b and k is not None:
        b["inertia"] = [x * k for x in b["inertia"]]
