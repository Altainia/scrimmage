"""Edit operations on the import file: one pure function, `apply_edit(imp, op, args)`, returns a new ImportFile or
raises EditError(field, message) with a plain sentence saying what's wrong and what to do."""

from __future__ import annotations

import copy
import math
from dataclasses import MISSING, fields, replace

from . import CadError
from .mass import MATERIALS, density
from .names import sanitize
from .frame import AXES, RobotFrame
from .settings import (COLLISIONS, BodySettings, EncoderSettings, ImportFile, JointSettings, MotorSettings,
                       MountSettings, PartSettings, _mentions, drop_couplings, remove_empty_bodies)
from .source import STEP_SUFFIXES, UNITS
from ..motors import MOTORS


class EditError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def _fail(field, message):
    raise EditError(field, message)


# ---------------------------------------------------------------- helpers

def _body(imp, name, field="body"):
    if not isinstance(name, str) or name not in imp.bodies:
        _fail(field, f"There is no body called {name}: pick one from the list.")
    return imp.bodies[name]


def _pieces(imp, keys, field="pieces"):
    if not isinstance(keys, (list, tuple)) or not keys:
        _fail(field, "Pick at least one piece.")
    for k in keys:
        if not isinstance(k, str) or k not in imp.pieces:
            _fail(field, f"There is no piece called {k}: pick one from the list.")
    return list(keys)


def _number(v, field, what, positive=True):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or (v <= 0 if positive else v == 0):
        _fail(field, f"{what} must be a number {'above zero' if positive else 'other than zero'}: enter one.")
    return float(v)


def _vec3(v, field, what, unit=False):
    ok = (isinstance(v, (list, tuple)) and len(v) == 3
          and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in v))
    if not ok:
        _fail(field, f"{what} needs three numbers (x, y, z): enter them.")
    out = [float(x) for x in v]
    if unit and not any(out):
        _fail(field, f"{what} can't be all zeros: give it a direction.")
    return out


def _descendants(imp, name):
    found, todo = set(), [name]
    while todo:
        cur = todo.pop()
        for n, b in imp.bodies.items():
            if b.parent == cur and n not in found:
                found.add(n)
                todo.append(n)
    return found


def _part_members(imp, name):
    def root(n):
        while imp.bodies[n].parent is not None:
            n = imp.bodies[n].parent
        return n
    r = root(name)
    return [n for n in imp.bodies if root(n) == r]


def _clear_joint(imp, name, msgs):
    """Take the joint, motor and encoder off body `name`, and the couplings that used it; say what went."""
    b = imp.bodies[name]
    gone = [w for w, has in (("joint", b.joint), ("motor", b.motor), ("encoder", b.encoder)) if has]
    b.joint = b.motor = b.encoder = None
    if gone:
        msgs.append(f"{name}'s {' and '.join(gone)} {'was' if len(gone) == 1 else 'were'} dropped")
    if drop_couplings(imp, name):
        msgs.append(f"A coupling that used {name} was dropped, because {name} no longer has a joint")


def _check_mounts(imp):
    """Mount names must stay unique inside each part (bodies may have moved between parts)."""
    seen: dict[str, set] = {}
    for n in imp.bodies:
        root = _part_members(imp, n)[0]
        for m in imp.bodies[n].mounts:
            if m.name in seen.setdefault(root, set()):
                _fail("name", f"Two mounts are called {m.name} in the same part: rename one first.")
            seen[root].add(m.name)


def _text(v, field, what):
    if not isinstance(v, str) or not v.strip():
        _fail(field, f"{what} must be text: enter a name.")
    return v.strip()


def _drop_links(imp, name):
    drop_couplings(imp, name)
    imp.loops = [c for c in imp.loops if not _mentions(c, name)]


_LABEL = {"JointSettings": "joint", "MotorSettings": "motor", "EncoderSettings": "encoder"}


def _pair(v, field, what):
    ok = (isinstance(v, (list, tuple)) and len(v) == 2
          and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in v))
    if not ok:
        _fail(field, f"{what} must be two numbers, from and to: enter both.")
    return (float(v[0]), float(v[1]))


def _build(cls, d, field):
    if not isinstance(d, dict):
        _fail(field, "These settings must be a table of values.")
    known = {f.name for f in fields(cls)}
    for k in d:
        if k not in known:
            _fail(k, f"{k} is not a setting here: remove it.")
    for f in fields(cls):
        if f.default is MISSING and f.default_factory is MISSING and f.name not in d:
            _fail(f.name, f"The {_LABEL.get(cls.__name__, 'setting')} needs a {f.name}: enter one.")
    if cls is JointSettings:
        d = dict(d)
        if d.get("range") is not None:
            d["range"] = _pair(d["range"], "range", "The range")
            if d["range"][0] >= d["range"][1]:
                _fail("range", "The range must run from a smaller number to a larger one: swap them.")
        if d.get("spring") is not None:
            d["spring"] = _pair(d["spring"], "spring", "The spring")
        for k in ("damping", "frictionloss"):
            v = d.get(k, 0.0)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                _fail(k, f"The {k} must be a number of zero or more: enter one.")
    try:
        return cls(**d)
    except CadError as e:
        _fail(field if cls is not EncoderSettings else "on", str(e))
    except (TypeError, ValueError, IndexError):
        _fail(field, f"The {_LABEL.get(cls.__name__, 'setting')} settings aren't valid: check the values.")


# ---------------------------------------------------------------- ops

def _set_material(imp, a):
    keys = _pieces(imp, a.get("pieces"))
    mat = a.get("material")
    if mat != "custom" and mat not in MATERIALS:
        _fail("material", f"Unknown material {mat!r}: pick one from the list.")
    infill, custom = a.get("infill"), a.get("custom_density")
    if mat == "custom":
        if custom is None:
            _fail("custom_density", "A custom material needs a density above zero (kg per cubic meter): enter one.")
    else:
        custom = None
    try:
        density(mat, infill, custom)
    except CadError as e:
        _fail("custom_density" if mat == "custom" and "density" in str(e) else "infill", str(e))
    for k in keys:
        p = imp.pieces[k]
        p.material, p.infill, p.custom_density, p.mass = mat, infill, custom, None


def _set_mass(imp, a):
    if "kg" not in a:
        _fail("kg", "Say the mass in kg, or kg = none to clear it.")
    kg = a.get("kg")
    if kg is not None:
        kg = _number(kg, "kg", "The mass")
    if a.get("piece") is not None:
        _pieces(imp, [a["piece"]], "piece")
        imp.pieces[a["piece"]].mass = kg
    elif a.get("body") is not None:
        _body(imp, a["body"]).mass = kg
    else:
        _fail("piece", "Say which piece or body to set the mass of.")


def _move_pieces(imp, a):
    keys = _pieces(imp, a.get("pieces"))
    _body(imp, a.get("body"))
    for k in keys:
        imp.pieces[k].body = a["body"]


def _new_body(imp, a):
    keys = _pieces(imp, a.get("pieces"))
    old = {imp.pieces[k].body for k in keys}
    if len(old) != 1:
        _fail("pieces", "These pieces are in different bodies: pick pieces from one body.")
    name = sanitize(_text(a.get("name"), "name", "The body name"))
    if name in imp.bodies:
        _fail("name", f"A body called {name} already exists: pick another name.")
    imp.bodies[name] = BodySettings(parent=old.pop())
    for k in keys:
        imp.pieces[k].body = name


def _merge_bodies(imp, a):
    into, other = a.get("into"), a.get("other")
    _body(imp, into, "into")
    ob = _body(imp, other, "other")
    if into == other:
        _fail("other", "A body can't be merged into itself: pick a different body.")
    if ob.parent is None:
        _fail("other", f"{other} is the root of a part: merge it the other way round.")
    for p in imp.pieces.values():
        if p.body == other:
            p.body = into
    for n, b in imp.bodies.items():
        if b.parent == other:
            b.parent = ob.parent if n == into else into
    if imp.bodies[into].parent == other:
        imp.bodies[into].parent = ob.parent
    msgs = []
    gone = [w for w, has in (("joint", ob.joint), ("motor", ob.motor), ("encoder", ob.encoder), ("gyro", ob.gyro),
                             ("mounts", ob.mounts)) if has]
    if gone:
        msgs.append(f"{other}'s {', '.join(gone)} {'was' if len(gone) == 1 else 'were'} dropped in the merge")
    if any(_mentions(c, other) for c in imp.couplings + imp.loops):
        msgs.append(f"The couplings and loops that used {other} were dropped")
    del imp.bodies[other]
    _drop_links(imp, other)
    return msgs


def _rename_body(imp, a):
    old = a.get("old")
    _body(imp, old, "old")
    new = sanitize(_text(a.get("new"), "new", "The new name"))
    if new != old and new in imp.bodies:
        _fail("new", f"A body called {new} already exists: pick another name.")
    imp.bodies = {(new if n == old else n): b for n, b in imp.bodies.items()}
    for b in imp.bodies.values():
        if b.parent == old:
            b.parent = new
    for p in imp.pieces.values():
        if p.body == old:
            p.body = new
    for c in imp.couplings:
        for k in ("a", "b"):
            if c.get(k) == old:
                c[k] = new
    for c in imp.loops:
        for k in ("body1", "body2"):
            if c.get(k) == old:
                c[k] = new


def _leave_out(imp, a):
    out = a.get("out")
    if not isinstance(out, bool):
        _fail("out", "Say whether to leave the pieces out: true or false.")
    for k in _pieces(imp, a.get("pieces")):
        imp.pieces[k].left_out = out


def _set_joint(imp, a):
    name = a.get("body")
    b = _body(imp, name)
    parent = a.get("parent")
    spec = a.get("joint")
    if parent is None:
        if b.parent is not None:
            _fail("parent", f"{name} needs a parent body: pick one, or make it a new part instead.")
        if spec is not None:
            _fail("joint", f"{name} is the root of a part, and a root can't have a joint: remove the joint.")
    else:
        _body(imp, parent, "parent")
        if parent == name:
            _fail("parent", f"{name} can't hang on itself: pick another body.")
        if parent in _descendants(imp, name):
            _fail("parent", f"{name} can't hang on {parent}: {parent} already hangs on {name}")
        if b.parent is None and b.part is not None:
            _fail("parent", f"{name} is the root of part {b.part}: remove that part before hanging it on another body.")
    joint = None if spec is None else _build(JointSettings, spec, "joint")
    if joint is not None:
        joint.anchor = _vec3(joint.anchor, "anchor", "The anchor")
        joint.axis = _vec3(joint.axis, "axis", "The axis", unit=True)
        if joint.kind == "slide" and b.motor is not None and not b.motor.spool_diameter:
            _fail("joint", f"{name} has a motor that needs a spool diameter to drive a slide: set the motor first.")
    b.parent = parent
    b.joint = joint
    if joint is None:
        msgs = []
        _clear_joint(imp, name, msgs)
        return msgs


def _needs_joint(imp, a):
    name = a.get("body")
    b = _body(imp, name)
    if b.joint is None:
        _fail("body", f"{name} has no joint: give it one before adding a motor or encoder.")
    return b


def _set_motor(imp, a):
    b = _needs_joint(imp, a)
    spec = a.get("settings")
    if spec is None:
        b.motor = None
        return
    m = _build(MotorSettings, spec, "settings")
    if m.motor not in MOTORS:
        _fail("motor", f"Unknown motor {m.motor!r}: pick one from the list.")
    if isinstance(m.count, bool) or not isinstance(m.count, int) or m.count < 1:
        _fail("count", "The motor count must be a whole number of at least 1: enter one.")
    m.ratio = _number(m.ratio, "ratio", "The gear ratio")
    m.efficiency = _number(m.efficiency, "efficiency", "The efficiency")
    if b.joint.kind == "slide":
        if m.spool_diameter is None:
            _fail("spool_diameter", "A motor on a slide needs a spool diameter (meters): enter one.")
        m.spool_diameter = _number(m.spool_diameter, "spool_diameter", "The spool diameter")
    b.motor = m


def _set_encoder(imp, a):
    b = _needs_joint(imp, a)
    spec = a.get("settings")
    if spec is None:
        b.encoder = None
        return
    b.encoder = _build(EncoderSettings, spec, "settings")


def _set_gyro(imp, a):
    on = a.get("on")
    if not isinstance(on, bool):
        _fail("on", "Say whether the gyro is on: true or false.")
    _body(imp, a.get("body")).gyro = on


def _add_mount(imp, a):
    b = _body(imp, a.get("body"))
    name = _text(a.get("name"), "name", "A mount name")
    taken = {m.name for n in _part_members(imp, a["body"]) for m in imp.bodies[n].mounts}
    if name in taken:
        _fail("name", f"A mount called {name} already exists in this part: pick another name.")
    b.mounts.append(MountSettings(name, _vec3(a.get("point"), "point", "The point"),
                                  _vec3(a.get("normal"), "normal", "The normal", unit=True)))


def _remove_mount(imp, a):
    b = _body(imp, a.get("body"))
    keep = [m for m in b.mounts if m.name != a.get("name")]
    if len(keep) == len(b.mounts):
        _fail("name", f"{a['body']} has no mount called {a.get('name')}: pick one from the list.")
    b.mounts = keep


def _set_collision(imp, a):
    mode = a.get("mode")
    if mode not in COLLISIONS:
        _fail("mode", f"Collision is one of {', '.join(COLLISIONS)}, not {mode!r}: pick one.")
    _body(imp, a.get("body")).collision = mode


def _set_part(imp, a):
    name = a.get("body")
    b = _body(imp, name)
    part = a.get("part")
    if part is None:
        if b.part is None:
            return
        if len(_roots(imp)) < 2:
            _fail("part", "A robot needs at least one part: keep this one.")
        parent = a.get("parent")
        if parent is None:
            _fail("parent", f"Say which body {name} should hang on once it stops being a part.")
        _body(imp, parent, "parent")
        if parent == name or parent in _descendants(imp, name):
            _fail("parent", f"{name} can't hang on {parent}: {parent} already hangs on {name}")
        imp.parts.pop(b.part, None)
        b.part, b.parent = None, parent
        return
    new = sanitize(_text(part, "part", "The part name"))
    if new in imp.parts and b.part != new:
        _fail("part", f"A part called {new} already exists: pick another name.")
    if b.part is not None:
        imp.parts[new] = imp.parts.pop(b.part)
    else:
        imp.parts[new] = PartSettings(folder=new)
        b.parent = None
        msgs = []
        _clear_joint(imp, name, msgs)
        b.part = new
        return msgs
    b.part = new


def _roots(imp):
    return [n for n, b in imp.bodies.items() if b.parent is None]


def _link_body(imp, name, field):
    b = _body(imp, name, field)
    if b.joint is None:
        _fail(field, f"{name} has no joint: couplings link bodies that move.")
    return b


def _add_coupling(imp, a):
    _link_body(imp, a.get("a"), "a")
    _link_body(imp, a.get("b"), "b")
    if a["a"] == a["b"]:
        _fail("b", "A coupling links two different bodies: pick another one.")
    ratio = _number(a.get("ratio"), "ratio", "The ratio", positive=False)
    imp.couplings.append({"a": a["a"], "b": a["b"], "ratio": ratio})


def _remove_coupling(imp, a):
    keep = [c for c in imp.couplings if not (c.get("a") == a.get("a") and c.get("b") == a.get("b"))]
    if len(keep) == len(imp.couplings):
        _fail("a", f"There is no coupling from {a.get('a')} to {a.get('b')}: pick one from the list.")
    imp.couplings = keep


def _add_loop(imp, a):
    _body(imp, a.get("body1"), "body1")
    _body(imp, a.get("body2"), "body2")
    if a["body1"] == a["body2"]:
        _fail("body2", "A loop closes between two different bodies: pick another one.")
    imp.loops.append({"body1": a["body1"], "body2": a["body2"], "point": _vec3(a.get("point"), "point", "The point")})


def _remove_loop(imp, a):
    keep = [c for c in imp.loops if not (c.get("body1") == a.get("body1") and c.get("body2") == a.get("body2"))]
    if len(keep) == len(imp.loops):
        _fail("body1", f"There is no loop between {a.get('body1')} and {a.get('body2')}: pick one from the list.")
    imp.loops = keep


def _set_frame(imp, a):
    known = {f.name for f in fields(RobotFrame)}
    for k in a:
        if k not in known:
            _fail(k, f"{k} is not a frame setting: remove it.")
    f = dict(a)
    for k in ("up", "front"):
        if k in f and f[k] not in AXES:
            _fail(k, f"The robot's {k} direction must be one of {', '.join(AXES)}, not {f[k]!r}: pick one.")
    up, front = f.get("up", imp.frame.up), f.get("front", imp.frame.front)
    if up[1] == front[1]:
        _fail("front", f"Up ({up}) and front ({front}) must point along different axes: change one.")
    if "floor" in f and (isinstance(f["floor"], bool) or not isinstance(f["floor"], (int, float))
                         or not math.isfinite(f["floor"])):
        _fail("floor", "The floor must be a number (CAD meters along up): enter one.")
    if f.get("center") is not None:
        f["center"] = _pair(f["center"], "center", "The center")
    for k in ("skip", "confirmed"):
        if k in f and not isinstance(f[k], bool):
            _fail(k, f"{k} must be true or false: pick one.")
    # a change to the frame needs checking again: only `confirmed = true` ("Looks right") marks it checked
    f["confirmed"] = f.get("confirmed", False)
    imp.frame = replace(imp.frame, **f)


def _set_total(imp, a):
    part = a.get("part")
    if part not in imp.parts:
        _fail("part", f"There is no part called {part}: pick one from the list.")
    kg = a.get("kg")
    imp.parts[part].total_mass = None if kg is None else _number(kg, "kg", "The total mass")


def _set_units(imp, a):
    if imp.source.lower().endswith(STEP_SUFFIXES):
        _fail("units", "A STEP file carries its own units: only STL and OBJ files can change them.")
    if a.get("units") not in UNITS:
        _fail("units", f"Units are one of {', '.join(UNITS)}, not {a.get('units')!r}: pick one.")
    imp.units = a["units"]


_OPS = {
    "set_material": _set_material, "set_mass": _set_mass, "move_pieces": _move_pieces, "new_body": _new_body,
    "merge_bodies": _merge_bodies, "rename_body": _rename_body, "leave_out": _leave_out, "set_joint": _set_joint,
    "set_motor": _set_motor, "set_encoder": _set_encoder, "set_gyro": _set_gyro, "add_mount": _add_mount,
    "remove_mount": _remove_mount, "set_collision": _set_collision, "set_part": _set_part,
    "add_coupling": _add_coupling, "remove_coupling": _remove_coupling, "add_loop": _add_loop,
    "remove_loop": _remove_loop, "set_frame": _set_frame, "set_total": _set_total, "set_units": _set_units,
}
_EMPTYING = {"move_pieces", "new_body"}
_CHECK_MOUNTS = {"set_joint", "set_part", "merge_bodies"}


def apply_edit(imp: ImportFile, op: str, args: dict) -> tuple[ImportFile, list[str]]:
    """(a new ImportFile with `op` applied, messages about anything the edit dropped); `imp` is untouched.
    Raises EditError(field, message)."""
    fn = _OPS.get(op)
    if fn is None:
        _fail("op", f"There is no edit called {op!r}: use one of {', '.join(_OPS)}.")
    out = copy.deepcopy(imp)
    msgs = list(fn(out, args if isinstance(args, dict) else {}) or [])
    if op in _EMPTYING:
        out, more = remove_empty_bodies(out)
        msgs += more
    if op in _CHECK_MOUNTS:
        _check_mounts(out)
    return out, msgs
