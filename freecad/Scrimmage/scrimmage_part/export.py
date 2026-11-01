"""Export a FreeCAD Assembly to a scrimmage part folder: `part.toml` plus `meshes/<body>.stl` (runs inside FreeCAD).

Conventions (docs/parts.md is the format):
- The root body's frame is the Assembly origin; each child body's frame is its joint's frame on the child side, so
  its joint is `hinge = "z"` / `slide = "z"` at the body origin, reading 0 at the saved pose. Body `pos`/`rot` are
  relative to the parent body's frame.
- Each body gets one non-colliding mesh geom (`meshes/<body>.stl`, a relative path); a body marked `collide`
  collides through it; a body marked `wheel` adds a colliding cylinder about its joint axis.
- `[[loop]]` pins sit at the loop joint's origin, given in body1's frame (meters).
- Every problem is collected and raised together as `ExportError`; nothing is written then. The folder is written
  next to `out_dir` under a temporary name and renamed into place; an existing `out_dir` is replaced only when it
  holds a `part.toml`.
"""

import math
import os
import re
import shutil
import uuid

from . import mass, mesh, read, require_freecad, settings, tree
from .errors import ExportError
from .motors import MOTOR_NAMES
from .toml_writer import dumps

NAME_RE = re.compile(r"[A-Za-z0-9_]+$")
WHEEL_FRICTION = 1.2
WHEEL_OFF_AXIS_MM = 1.0
NO_LIMIT = 1e6                    # the missing side of a one-sided joint limit: no limit (docs/parts.md, Joints)


def _clean(x):
    """A float without round-off noise (12 significant digits; tiny values become 0)."""
    x = float(f"{x:.12g}")
    return 0.0 if abs(x) < 1e-12 else x


def _plc(frame):
    return mass.placement(frame)


def _matrix(q):
    w, x, y, z = q
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]]


def _relative(parent_frame, frame):
    """`frame` (Assembly coordinates) in `parent_frame`: (pos m, rot rpy degrees)."""
    p = _plc(parent_frame).inverse() * _plc(frame)
    pos, q = read.plc_to_frame(p)
    return [_clean(v * 1e-3) for v in pos], [_clean(v) for v in mass.rpy_deg(_matrix(q))]


def _point_in(frame, point_mm):
    import FreeCAD
    v = _plc(frame).inverse().multVec(FreeCAD.Vector(*point_mm))
    return [_clean(v.x * 1e-3), _clean(v.y * 1e-3), _clean(v.z * 1e-3)]


def _centroid_mm(comps, body_frame):
    """Volume centroid of the components' solids in the body frame (mm), or None."""
    to_body = _plc(body_frame).inverse()
    vol, acc = 0.0, [0.0, 0.0, 0.0]
    for c in comps:
        if c.shape is None or c.shape.isNull():
            continue
        for solid in c.shape.Solids:
            s = solid.copy()
            s.Placement = to_body * c.placement * s.Placement
            v = s.Volume
            if v <= 0:
                continue
            m = s.CenterOfMass
            vol += v
            acc = [acc[0] + v * m.x, acc[1] + v * m.y, acc[2] + v * m.z]
    return [a / vol for a in acc] if vol > 0 else None


def _unique(base, used):
    name, k = base, 2
    while name in used:
        name, k = f"{base}_{k}", k + 1
    used.add(name)
    return name


def _is_part_folder(path):
    return os.path.isdir(path) and os.path.isfile(os.path.join(path, "part.toml"))


def _install(tmp, out_dir):
    """Rename the finished `tmp` folder to `out_dir`, replacing an existing part folder there. A rename the OS refuses
    (on Windows: a folder open in Explorer or an editor) is an ExportError; the old folder stays."""
    old = None
    try:
        if os.path.exists(out_dir):
            old = os.path.join(os.path.dirname(out_dir), f".{os.path.basename(out_dir)}.old-{uuid.uuid4().hex[:8]}")
            os.rename(out_dir, old)
        try:
            os.rename(tmp, out_dir)
        except BaseException:
            if old is not None:
                os.rename(old, out_dir)
            raise
    except OSError as e:
        raise ExportError([f"couldn't write the part folder {out_dir} ({e.strerror or e}): close anything using "
                           f"{out_dir} (Explorer, an editor) and export again"]) from None
    if old is not None:
        shutil.rmtree(old, ignore_errors=True)


def check_out_dir(out_dir):
    """A problem sentence when `out_dir` exists and isn't a part folder, else None."""
    if os.path.exists(out_dir) and not _is_part_folder(out_dir):
        return (f"the export folder {out_dir} exists and isn't a part folder (it has no part.toml); pick an empty "
                "or new folder")
    return None


def export(doc_or_path, out_dir):
    """Export the document's Assembly (a FreeCAD document, or the path of an .FCStd) to the part folder `out_dir`.
    Returns the summary `{part, bodies, joints, loops, couplings, slots, mounts, mass_kg, warnings, out_dir}` (names
    as lists; loops and couplings as counts). Raises ExportError listing every problem; nothing is written then.
    A path is opened (with the documents it links to, which FreeCAD finds next to it) and closed again."""
    require_freecad()
    import FreeCAD
    opened = []
    if isinstance(doc_or_path, (str, os.PathLike)):
        path = os.fspath(doc_or_path)
        if not os.path.isfile(path):
            raise ExportError([f"no such FreeCAD file: {path}"])
        before = set(FreeCAD.listDocuments())
        doc = FreeCAD.openDocument(path)
        opened = [n for n in FreeCAD.listDocuments() if n not in before]
    else:
        doc = doc_or_path
    try:
        if opened:
            broken = unresolved_links(doc)
            if broken:
                raise ExportError([f"{os.path.basename(path)} links to parts in other FreeCAD files that weren't found "
                                   f"({', '.join(broken)}): open it in the FreeCAD panel, or keep the linked files "
                                   "next to it"])
        return _export(doc, os.path.abspath(os.fspath(out_dir)))
    finally:
        for n in reversed(opened):
            if n in FreeCAD.listDocuments():
                FreeCAD.closeDocument(n)


def unresolved_links(doc):
    """Labels of `doc`'s links whose target wasn't found (another document that isn't where the link says)."""
    out = []
    for o in doc.Objects:
        try:
            status = o.getStatusString() if not o.isValid() else ""
        except Exception:
            status = ""
        dangling = (o.isDerivedFrom("App::Link") and "LinkedObject" in o.PropertiesList and o.LinkedObject is None)
        if "not restored" in status.lower() or dangling:
            out.append(o.Label)
    return out


def _export(doc, out_dir):
    problems = []
    bad_out = check_out_dir(out_dir)
    if bad_out:
        problems.append(bad_out)
    asm = read.find_assembly(doc)
    e = read.read_assembly(asm)
    st = settings.get(doc) or settings.ScrimmageSettings()
    try:
        t = tree.build(e, prefer=settings.slotted_joints(st))
    except ExportError as err:
        raise ExportError(problems + err.problems) from None
    warnings = list(t.warnings)
    comps = e.components
    joints_by_name = {j.name: j for j in e.joints}

    def label(n):
        return comps[n].label if n in comps else n

    body_of = {c: b.name for b in t.bodies for c in b.comps}
    tbody = {b.name: b for b in t.bodies}
    child_of_joint = {b.joint.name: b for b in t.bodies if b.joint is not None}
    loop_joints = {j.name: (b1, b2) for j, b1, b2, _ in t.loops}

    # joint names: the joints' labels, sanitized and unique
    used = set()
    jname = {b.joint.name: _unique(tree.sanitize(b.joint.label), used) for b in t.bodies if b.joint is not None}

    # mass overrides
    overrides = {}
    for comp, kg in st.mass_overrides.items():
        if comp not in comps:
            warnings.append(f"mass override of {kg} kg was on component '{comp}', which no longer exists; dropped")
        elif isinstance(kg, bool) or not isinstance(kg, (int, float)) or not kg > 0 or not math.isfinite(kg):
            problems.append(f"mass override of {label(comp)} must be a number of kg greater than 0, not {kg!r}")
        else:
            overrides[comp] = float(kg)

    # contact marks
    marks = {}
    for comp, kind in st.contact.items():
        if comp not in comps:
            warnings.append(f"the {kind} mark was on component '{comp}', which no longer exists; dropped")
        elif kind not in ("wheel", "collide"):
            problems.append(f"contact mark of {label(comp)} must be \"wheel\" or \"collide\", not {kind!r}")
        else:
            marks[comp] = kind

    friction = {}
    for comp, f in st.wheel_friction.items():
        if comp not in comps:
            warnings.append(f"wheel friction {f} was on component '{comp}', which no longer exists; dropped")
        elif isinstance(f, bool) or not isinstance(f, (int, float)) or not math.isfinite(f) or not f > 0:
            problems.append(f"wheel friction of {label(comp)} must be a number greater than 0, not {f!r}")
        else:
            friction[comp] = float(f)

    for c in comps.values():
        if c.name in overrides:
            continue                                    # its density doesn't matter: the override sets the mass
        lost = mass.lost_volume(c)
        if lost:
            warnings.append(f"{c.label} {lost}, so it adds no mass; give it a mass override, or repair it into a "
                            "closed solid")
        for m in c.nodensity:
            warnings.append(f"material '{m}' on {c.label} has no density; used {mass.DEFAULT_DENSITY:g} kg/m³ — assign "
                            "a material with a density or set a mass override")

    # bodies (meshes are tessellated later, once we know nothing failed)
    bodies, total_mass, inertials = [], 0.0, {}
    for b in t.bodies:
        bc = [comps[n] for n in b.comps]
        try:
            m, com, moments, irot = mass.body_inertial(bc, {k: v for k, v in overrides.items() if k in b.comps},
                                                       b.frame_world)
        except ExportError as err:
            problems += [f"body '{b.name}': {p}" for p in err.problems]
            continue
        inertials[b.name] = (m, com, moments, irot)
        total_mass += m
        kinds = {marks[n] for n in b.comps if n in marks}
        if "wheel" in kinds and (b.joint is None or b.joint.type != "Revolute"):
            problems.append(f"body '{b.name}' is marked as a wheel but doesn't turn on a Revolute joint (it is "
                            + ("the grounded body" if b.joint is None else f"on {b.joint.type} '{b.joint.label}'")
                            + "); mark only wheels on an axle")
        elif "wheel" in kinds:
            ctr = _centroid_mm(bc, b.frame_world)
            if ctr is not None and math.hypot(ctr[0], ctr[1]) > WHEEL_OFF_AXIS_MM:
                warnings.append(f"wheel '{b.name}': its shape's center is {math.hypot(ctr[0], ctr[1]):.1f} mm off "
                                f"its joint '{b.joint.label}' line; the collision cylinder is centered on the joint "
                                "axis")

    # loops
    loops = []
    for j, b1, b2, pin in t.loops:
        loops.append({"body1": b1, "body2": b2, "pos": _point_in(tbody[b1].frame_world, pin)})

    # couplings
    couplings = []
    for c, a, b, ratio in t.couplings:
        r = ratio / 1000.0 if c.type == "RackPinion" else ratio
        couplings.append({"joints": [jname[a.name], jname[b.name]], "ratio": _clean(r)})

    # slots
    slot_names = []

    def slot_ok(s, kind):
        n = s.get("slot")
        if not isinstance(n, str) or not NAME_RE.match(n):
            problems.append(f"{kind} slot name {n!r} must be letters, digits and _")
            return None
        if n in slot_names:
            problems.append(f"slot name '{n}' is used twice; slot names must be unique")
            return None
        slot_names.append(n)
        return n

    def tree_joint(s, kind, n):
        """The tree TBody hung on the slot's joint, or None (with a warning or problem)."""
        jn = s.get("joint")
        j = joints_by_name.get(jn)
        if j is None:
            warnings.append(f"{kind} slot '{n}' was on joint '{jn}', which no longer exists; dropped")
            return None
        if j.suppressed:
            warnings.append(f"{kind} slot '{n}' is on joint '{j.label}', which is suppressed; dropped")
            return None
        if jn in loop_joints:
            b1, b2 = loop_joints[jn]
            hung = [tbody[x].joint for x in (b2, b1) if tbody[x].joint is not None]
            hint = f"; put it on tree joint '{hung[0].label}'" if hung else ""
            problems.append(f"{kind} slot '{n}' is on joint '{j.label}', which closes a loop{hint}")
            return None
        if jn not in child_of_joint:
            problems.append(f"{kind} slot '{n}' is on {j.type} joint '{j.label}'; slots go on Revolute or Slider "
                            "joints")
            return None
        return child_of_joint[jn]

    def num(s, key, n, kind, default=None, positive=True):
        v = s.get(key, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or (positive and v <= 0):
            problems.append(f"{kind} slot '{n}': {key} must be a number greater than 0, not {v!r}")
            return None
        return float(v)

    motors, gear_of, gear_slot = [], {}, {}
    for s in st.motors:
        n = slot_ok(s, "motor")
        if n is None:
            continue
        b = tree_joint(s, "motor", n)
        if b is None:
            continue
        motor = s.get("motor")
        if motor not in MOTOR_NAMES:
            problems.append(f"motor slot '{n}': unknown motor {motor!r}; known motors: {', '.join(MOTOR_NAMES)}")
            continue
        count = s.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            problems.append(f"motor slot '{n}': count must be a whole number of motors (1 or more), not {count!r}")
            continue
        gr, eff = num(s, "gear_ratio", n, "motor", 1.0), num(s, "efficiency", n, "motor", 1.0)
        if gr is None or eff is None:
            continue
        if b.joint.type == "Slider":
            r = num(s, "radius_mm", n, "motor")
            if r is None:
                continue
            gearing = gr / (r / 1000.0)
            turns = gr / (2 * math.pi * r / 1000.0)                                 # motor turns per meter
        else:
            gearing = turns = gr                                                    # motor turns per joint turn
        other = gear_slot.get(b.joint.name)
        if other is not None and not math.isclose(gear_of[b.joint.name], turns, rel_tol=1e-9):
            problems.append(f"motor slots '{other}' and '{n}' both drive joint '{b.joint.label}' with different "
                            "gearing; motors on one joint share its gearbox, so give them the same gear ratio"
                            + (" and radius" if b.joint.type == "Slider" else ""))
            continue
        gear_of.setdefault(b.joint.name, turns)
        gear_slot.setdefault(b.joint.name, n)
        m = {"slot": n, "joint": jname[b.joint.name], "motor": motor}
        if count != 1:
            m["count"] = count
        if gearing != 1.0:
            m["gearing"] = _clean(gearing)
        if s.get("invert"):
            m["invert"] = True
        if eff != 1.0:
            m["efficiency"] = eff
        motors.append(m)

    encoders = []
    for s in st.encoders:
        n = slot_ok(s, "encoder")
        if n is None:
            continue
        b = tree_joint(s, "encoder", n)
        if b is None:
            continue
        on = s.get("on", "joint")
        if on not in ("joint", "motor"):
            problems.append(f"encoder slot '{n}': on must be \"joint\" or \"motor\", not {on!r}")
            continue
        enc = {"slot": n, "joint": jname[b.joint.name]}
        if on == "motor":
            if b.joint.name not in gear_of:
                problems.append(f"encoder slot '{n}' is on the motor shaft of joint '{b.joint.label}', which has no "
                                "motor slot; add one or put the encoder on the joint")
                continue
            enc["ratio"] = _clean(gear_of[b.joint.name])
        if s.get("invert"):
            enc["invert"] = True
        encoders.append(enc)

    gyros = []
    for s in st.gyros:
        n = slot_ok(s, "gyro")
        if n is None:
            continue
        comp = s.get("body_comp")
        if comp not in comps:
            warnings.append(f"gyro slot '{n}' was on component '{comp}', which no longer exists; dropped")
            continue
        gyros.append({"slot": n, "body": body_of[comp]})

    # mounts: LCS named mount_<name>, then picked ones
    mounts = []

    def add_mount(name, comp, frame):
        if not isinstance(name, str) or not NAME_RE.match(name):
            problems.append(f"mount name {name!r} must be letters, digits and _")
            return
        if any(m["name"] == name for m in mounts):
            problems.append(f"mount name '{name}' is used twice; mount names must be unique")
            return
        body = body_of[comp] if comp is not None else t.bodies[0].name
        pos, rot = _relative(tbody[body].frame_world, frame)
        m = {"name": name, "body": body}
        if any(pos):
            m["pos"] = pos
        if any(rot):
            m["rot"] = rot
        mounts.append(m)

    for lbl, frame, comp in e.lcs_mounts:
        add_mount(lbl[len("mount_"):], comp if comp in comps else None, frame)
    for s in st.mounts:
        comp = s.get("comp")
        if comp not in comps:
            warnings.append(f"mount '{s.get('name')}' was on component '{comp}', which no longer exists; dropped")
            continue
        try:
            (x, y, z), q = s["frame_local"]
            local = ((float(x), float(y), float(z)), tuple(float(v) for v in q))
        except (KeyError, TypeError, ValueError):
            problems.append(f"mount '{s.get('name')}' has a damaged frame; pick it again")
            continue
        world = read.plc_to_frame(comps[comp].placement * _plc(local))
        add_mount(s.get("name"), comp, world)

    if problems:
        raise ExportError(list(dict.fromkeys(problems)))

    # write: temp folder next to out_dir, then rename
    parent = os.path.dirname(out_dir)
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, f".{os.path.basename(out_dir)}.tmp-{uuid.uuid4().hex[:8]}")
    os.makedirs(os.path.join(tmp, "meshes"))
    try:
        for b in t.bodies:
            bc = [comps[n] for n in b.comps]
            m, com, moments, irot = inertials[b.name]
            d = {"name": b.name}
            if b.parent is not None:
                d["parent"] = b.parent
            d["mass"] = m
            if b.parent is not None:
                pos, rot = _relative(tbody[b.parent].frame_world, b.frame_world)
                if any(pos):
                    d["pos"] = pos
                if any(rot):
                    d["rot"] = rot
            d["com"] = [_clean(v) for v in com]
            d["inertia"] = [max(v, 1e-12) for v in moments]
            d["inertia_rot"] = [_clean(v) for v in irot]
            kinds = {marks[n] for n in b.comps if n in marks}
            geoms = []
            if mesh.write_body_stl(bc, b.frame_world, os.path.join(tmp, "meshes", f"{b.name}.stl")):
                g = {"mesh": f"meshes/{b.name}.stl"}
                if "collide" not in kinds:
                    g["collide"] = False
                geoms.append(g)
            if "wheel" in kinds:
                radius, width, ctr = mesh.wheel_cylinder(bc, b.frame_world, (0.0, 0.0, 1.0))
                fr = [friction[n] for n in b.comps if n in friction]
                g = {"cylinder": [_clean(radius), _clean(width)]}
                if any(_clean(v) for v in ctr):
                    g["pos"] = [_clean(v) for v in ctr]
                g["friction"] = fr[0] if fr else WHEEL_FRICTION
                g["condim"] = 4
                geoms.append(g)
            d["geom"] = geoms
            if b.joint is not None:
                kind = "hinge" if b.joint.type == "Revolute" else "slide"
                jd = {"name": jname[b.joint.name], kind: "z"}
                if b.limits is not None:
                    lo, hi = b.limits
                    k = 1.0 if kind == "hinge" else 1e-3
                    jd["range"] = [_clean(lo * k) if lo is not None else -NO_LIMIT,
                                   _clean(hi * k) if hi is not None else NO_LIMIT]
                d["joint"] = jd
            bodies.append(d)
        part = {"part": {"name": asm.Label, "format": 1}, "body": bodies}
        for key, items in (("loop", loops), ("coupling", couplings), ("motor", motors), ("encoder", encoders),
                           ("gyro", gyros), ("mount", mounts)):
            if items:
                part[key] = items
        with open(os.path.join(tmp, "part.toml"), "w", encoding="utf-8") as f:
            f.write(dumps(part))
        _install(tmp, out_dir)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    return dict(part=asm.Label, bodies=[b["name"] for b in bodies], joints=list(jname.values()), loops=len(loops),
                couplings=len(couplings), slots=[x["slot"] for x in motors + encoders + gyros],
                mounts=[m["name"] for m in mounts], mass_kg=total_mass, warnings=warnings, out_dir=out_dir)
