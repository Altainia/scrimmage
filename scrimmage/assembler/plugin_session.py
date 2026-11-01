"""The Part tab's plugin mode: a part folder the FreeCAD add-on made (no `import-origin.toml` marker).

Its part.toml is never written (the add-on writes it again on every export); the edits go in the overlay
(`scrimmage-edits.toml`, scrimmage/parts/overlay.py), and only body-level ones: a body's weight, collision and joint
limits, and the part's total weight. Piece edits are refused: the folder has no pieces, only the bodies the add-on
merged them into. Each edit is checked by applying it to the part before it is written. A poll every 0.25 s picks up
outside writes of part.toml or the overlay (a re-export, a hand edit); one that doesn't load makes the part read-only
with the reason until it loads again. Versions, history and the 409 rule are part_session's.

Each body in the state has `typed_mass` (the weight typed here, else None) and `changed` (the overlay's keys for it:
"mass", "collision" and the joint's "range", "spring", "damping", "frictionloss"), so the page can show which values
were changed here and which come from the plugin.
"""

from __future__ import annotations

import copy
import os
import tomllib
import uuid
from pathlib import Path

import numpy as np
import tomlkit

from ..cad.edits import EditError
from ..parts.model import PartError, load_part, part_from_dict
from ..parts.overlay import JOINT_KEYS, OVERLAY, _rot, apply_overlay
from .bench_session import SessionError
from .part_session import NEED_STEP, PIECE_OPS, _floats, _mesh, _refused, _Session, _sig


class PluginSession(_Session):
    mode = "plugin"

    def __init__(self, folder: Path, version: int = 0):
        super().__init__(version)
        self.path = Path(folder).resolve()
        self._overlay = self.path / OVERLAY
        self.edits: dict = {}
        self.part = None
        self.read_only_reason: str | None = None
        self._sig = None
        self._view_key = self._view = None
        with self._lock:
            self._sync()
        self._start(("part-poll", self._poll))

    def _base(self):
        file = self.path / "part.toml"
        return part_from_dict(tomllib.loads(file.read_text(encoding="utf-8")), str(file), self.path)

    def _sync(self) -> None:
        sig = _sig(self.path / "part.toml", self._overlay)
        if sig == self._sig:
            return
        self._sig = sig
        old = self.edits
        try:
            edits = tomllib.loads(self._overlay.read_text(encoding="utf-8")) if self._overlay.is_file() else {}
            part = load_part(self.path)
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, PartError) as e:
            self.read_only_reason = f"{e}; fix it in a text editor"
            self._bump()
            return
        if self.part is not None and edits != old:
            self._push(old)
        self.edits, self.part, self.read_only_reason = edits, part, None
        self._bump(changed=True)

    def _write(self, edits: dict) -> None:
        try:
            if edits:
                tmp = self._overlay.with_name(f".{OVERLAY}.{uuid.uuid4().hex[:8]}.tmp")
                tmp.write_text(tomlkit.dumps(edits), encoding="utf-8")
                os.replace(tmp, self._overlay)
            else:
                self._overlay.unlink(missing_ok=True)
        except OSError as e:
            raise SessionError(500, f"can't save {OVERLAY}: {e.strerror or e}") from None

    def _set(self, edits: dict, push: bool = True) -> tuple[int, dict]:
        try:
            part, notes = apply_overlay(self._base(), edits, str(self._overlay))
        except (OSError, tomllib.TOMLDecodeError, PartError) as e:
            m = str(e)
            return 400, {"ok": False, "field": "status", "message": m, "error": m,
                         "errors": [{"field": "status", "message": m}]}
        part.warnings = notes
        if push:
            self._push(self.edits)
        self._write(edits)
        self.edits, self.part, self._messages = edits, part, []
        self._sig = _sig(self.path / "part.toml", self._overlay)
        self._bump(changed=True)
        return 200, self.state()

    def _op(self, op: str, a: dict) -> dict:
        """The overlay with body-level edit `op` applied (args as the import's edits take them)."""
        if op in PIECE_OPS or (op == "set_mass" and a.get("piece") is not None):
            raise EditError("op", NEED_STEP)
        edits = copy.deepcopy(self.edits)
        bodies = {b.name: b for b in self.part.bodies}

        def body(field="body") -> dict:
            if a.get(field) not in bodies:
                raise EditError(field, f"There is no body called {a.get(field)}: pick one from the list.")
            return edits.setdefault("body", {}).setdefault(a[field], {})

        def number(v, field, what):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) or v <= 0:
                raise EditError(field, f"{what} must be a number above zero: enter one.")
            return float(v)

        if op == "set_mass":
            if "kg" not in a:
                raise EditError("kg", "Say the mass in kg, or kg = none to clear it.")
            b = body()
            b.pop("mass", None) if a["kg"] is None else b.update(mass=number(a["kg"], "kg", "The mass"))
        elif op == "set_collision":
            if a.get("mode") not in ("none", "wheel", "shaped", "simple"):
                raise EditError("mode", f"Collision is one of none, wheel, shaped, simple, not {a.get('mode')!r}: "
                                        "pick one.")
            body()["collision"] = a["mode"]
        elif op == "set_joint":
            spec = a.get("joint")
            if bodies.get(a.get("body")) is not None and bodies[a["body"]].joint is None:
                raise EditError("body", f"{a['body']} has no joint in the CAD plugin's part: add it there.")
            b = body()
            if not isinstance(spec, dict) or any(k not in JOINT_KEYS for k in spec):
                raise EditError("joint", "Only the joint's range, spring, damping and frictionloss can change "
                                         "here; the rest comes from the CAD plugin.")
            j = b.setdefault("joint", {})
            for k, v in spec.items():
                j.pop(k, None) if v is None else j.__setitem__(k, list(v) if isinstance(v, (list, tuple)) else v)
            if not j:
                b.pop("joint")
        elif op == "set_total":
            kg = a.get("kg")
            edits.pop("total_mass", None) if kg is None else edits.update(total_mass=number(kg, "kg",
                                                                                            "The total mass"))
        else:
            raise EditError("op", f"{op} needs the CAD file: import the STEP instead.")
        edits["body"] = {k: v for k, v in edits.get("body", {}).items() if v}
        if not edits["body"]:
            del edits["body"]
        return edits

    def edit(self, body: dict) -> tuple[int, dict]:
        op, args = body.get("op"), body.get("args", {})
        if not isinstance(op, str) or not isinstance(args, dict):
            raise SessionError(400, "op (the edit's name) and args (an object) are required")
        with self._lock:
            self._check_open()
            self._sync()
            self._fresh(body)
            if self.read_only_reason is not None:
                raise SessionError(409, self.read_only_reason)
            try:
                edits = self._op(op, args)
            except EditError as e:
                return _refused(e)
            return self._set(edits)

    def history(self, which: str, body: dict) -> tuple[int, dict]:
        with self._lock:
            self._check_open()
            self._sync()
            self._fresh(body)
            if self.read_only_reason is not None:
                raise SessionError(409, self.read_only_reason)
            stack, other = (self._undo, self._redo) if which == "undo" else (self._redo, self._undo)
            if not stack:
                return 200, {**self.state(), "ok": False}
            edits = stack.pop()
            other.append(self.edits)
            return self._set(edits, push=False)

    def _poses(self) -> dict:
        """Each body's (rotation, position) in the part frame at zero pose."""
        out: dict = {}
        for b in self.part.bodies:   # part.toml lists a parent before its children
            R0, p0 = out.get(b.parent, (np.eye(3), np.zeros(3)))
            out[b.name] = (R0 @ _rot(b.rot), R0 @ np.asarray(b.pos, float) + p0)
        return out

    def state(self) -> dict:
        with self._lock:
            out = {"version": self.version, "mode": "plugin", "path": str(self.path),
                   "name": self.part.name if self.part else self.path.name, "source": None, "source_copied": False, "units": "m",
                   "frame": None, "pieces": [], "bodies": [], "parts": [], "couplings": [], "loops": [],
                   "checklist": [],
                   "messages": list(self._messages), "build": {"state": "idle", "message": None},
                   "can_undo": bool(self._undo), "can_redo": bool(self._redo),
                   "read_only_reason": self.read_only_reason}
            if self.part is None:
                return out
            p, poses, bes = self.part, self._poses(), self.edits.get("body", {})
            total, moment = 0.0, np.zeros(3)
            for b in p.bodies:
                R, x = poses[b.name]
                com = R @ np.asarray(b.com if b.com is not None else (0, 0, 0), float) + x
                total, moment = total + b.mass, moment + b.mass * com
                j = b.joint
                joints = {j.name} if j else set()
                be = bes.get(b.name, {})
                # what was changed here (in the overlay), as the GUI's field names; the rest is the plugin's
                changed = sorted({k for k in ("mass", "collision") if k in be} | set(be.get("joint", {})))
                out["bodies"].append({
                    "name": b.name, "parent": b.parent, "part": p.name if b.parent is None else None,
                    "joint": None if j is None else {"kind": j.kind, "axis": _floats(R @ np.asarray(j.axis, float)),
                                                     "range": j.range, "spring": j.spring, "damping": j.damping,
                                                     "frictionloss": j.frictionloss},
                    "motor": [m.slot for m in p.motors if m.joint in joints],
                    "encoder": [e.slot for e in p.encoders if e.joint in joints],
                    "gyro": any(g.body == b.name for g in p.gyros),
                    "mounts": [m.name for m in p.mounts if m.body == b.name],
                    "collision": be.get("collision"), "mass": float(b.mass), "com": _floats(com),
                    "typed_mass": be.get("mass"), "changed": changed})
            out["parts"] = [{"name": p.name, "folder": str(self.path), "total_mass": self.edits.get("total_mass"),
                             "mass": total, "com": _floats(moment / total) if total > 0 else None}]
            out["checklist"] = [{"level": "check", "text": w, "target": {"kind": "frame", "keys": []}}
                                for w in p.warnings]
            return out

    def view(self) -> dict:
        """Each body's mesh geoms, joined, in the part frame (keyed by body name); cached per file signature."""
        from ..bench.cad import read_mesh   # not at the top: it pulls in mujoco
        from ..bench.spec import BenchError
        with self._lock:
            if self.part is None:
                return {"pieces": []}
            if self._view_key != self._sig:
                pieces, poses = [], self._poses()
                for b in self.part.bodies:
                    R, x = poses[b.name]
                    vs, fs, n = [], [], 0
                    for g in b.geoms:
                        if g.shape != "mesh":
                            continue
                        try:
                            v, f = read_mesh(self.path / g.size)
                        except BenchError:
                            continue
                        vs.append((v @ _rot(g.rot).T + np.asarray(g.pos, float)) @ R.T + x)
                        fs.append(f + n)
                        n += len(v)
                    if vs:
                        pieces.append({"key": b.name, **_mesh(np.vstack(vs), np.vstack(fs))})
                self._view, self._view_key = {"pieces": pieces}, self._sig
            return self._view
