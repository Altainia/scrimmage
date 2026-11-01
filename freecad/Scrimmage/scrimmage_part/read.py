"""Read a FreeCAD 1.1 Assembly into plain data for `tree.build` (and, through `Comp`, for mass and meshes).

Everything is in the Assembly's own coordinates (the Assembly's global placement is divided out), lengths in mm.
Frames are `(pos_mm, quat_wxyz)` tuples. FreeCAD objects are kept only where shapes must be transformed later.

Shapes:
- `Comp(name, label, shape, placement, material_density_kg_m3, pieces, nodensity)`: one component = one *moving
  part* (a direct child of the Assembly: a Part feature, Body, App::Part, App::Link, ...). `shape` is its whole content
  (`Part.getShape`, hidden children left out) in the component's own frame; `placement` the component's FreeCAD
  `Placement` relative to the Assembly (so `placement * shape` is the component in Assembly coordinates);
  `pieces` is `[(shape, density_kg_m3 | None)]`, one per solid object inside the component, each shape in the
  component's frame — the authority for mass; `material_density_kg_m3` is the density the pieces share, or None
  (no material, or mixed). FreeCAD's "Default" material counts as no material (None). `nodensity` lists the names of
  real materials on its pieces that have no Density (those pieces fall back to aluminum, like unassigned ones).
- `J(name, label, type, comp1, comp2, frame1_world, frame2_world, limits, radii, suppressed)`: a joint. `comp1/2` are
  the components (`Reference1/2[0]`, FreeCAD's moving part) or None when the reference is broken; `frame1/2_world` the
  joint frames of the two ends (FreeCAD's `getJcsGlobalPlc`, made Assembly-relative); `limits` `(min | None,
  max | None)` in degrees (Revolute) / mm (Slider) as FreeCAD stores them (Reference2 relative to Reference1), None when
  neither is enabled; `radii` `(Distance, Distance2)` in mm for Gears/Belt/RackPinion, else None.
- `Extracted(components, grounded, joints, lcs_mounts, warnings, problems)`: `components` a dict internal name ->
  Comp in document order; `grounded` the grounded components' names; `joints` the joints in the joint group's
  order; `lcs_mounts` `[(label, frame, comp_name | None)]` for each coordinate system labeled `mount_*` (comp is the
  component it lives in or is attached to); `warnings`/`problems` sentences found while reading.
"""

from typing import NamedTuple, Optional

COUPLINGS = ("Gears", "Belt", "RackPinion", "Screw")
LCS_TYPES = ("App::LocalCoordinateSystem", "Part::LocalCoordinateSystem", "PartDesign::CoordinateSystem")


class Comp(NamedTuple):
    name: str
    label: str
    shape: object
    placement: object
    material_density_kg_m3: Optional[float]
    pieces: list
    nodensity: tuple = ()


class J(NamedTuple):
    name: str
    label: str
    type: str
    comp1: Optional[str]
    comp2: Optional[str]
    frame1_world: tuple
    frame2_world: tuple
    limits: Optional[tuple]
    radii: Optional[tuple]
    suppressed: bool


class Extracted(NamedTuple):
    components: dict
    grounded: list
    joints: list
    lcs_mounts: list
    warnings: list
    problems: list


def plc_to_frame(plc):
    """A FreeCAD Placement as ((x, y, z) mm, (w, x, y, z))."""
    x, y, z, w = plc.Rotation.Q
    return (float(plc.Base.x), float(plc.Base.y), float(plc.Base.z)), (float(w), float(x), float(y), float(z))


def find_assembly(doc):
    """The document's Assembly (the first top-level one)."""
    from .errors import ExportError
    for o in doc.RootObjects:
        if o.TypeId == "Assembly::AssemblyObject":
            return o
    for o in doc.Objects:
        if o.TypeId == "Assembly::AssemblyObject":
            return o
    raise ExportError([f"document '{doc.Label}' has no Assembly"])


def _q(v):
    return float(getattr(v, "Value", v))


def _is_lcs(o):
    for t in LCS_TYPES:
        try:
            if o.isDerivedFrom(t):
                return True
        except Exception:               # a type this FreeCAD doesn't have
            pass
    return False


def _global(o):
    """o's global placement. App::Link has no getGlobalPlacement(); FreeCAD's own helper walks o.Parents."""
    if hasattr(o, "getGlobalPlacement"):
        return o.getGlobalPlacement()
    import UtilsAssembly
    return UtilsAssembly.getGlobalPlacement([o, [""]], o)


def _is_link(o):
    return o.isDerivedFrom("App::Link") or o.isDerivedFrom("App::LinkElement")


def _children(o):
    """Children of a container, seen through a link."""
    t = o.getLinkedObject(True) if _is_link(o) else o
    if _is_link(o) and getattr(o, "ElementList", None):
        return list(o.ElementList)
    if t.isDerivedFrom("App::Part") or t.isDerivedFrom("App::DocumentObjectGroup"):
        return list(t.Group)
    return []


def density_kg_m3(obj):
    """Density of obj's FreeCAD material in kg/m^3, or None (no material, the "Default" one, or no Density)."""
    import FreeCAD
    mat = getattr(obj, "ShapeMaterial", None)
    if mat is None or mat.Name == "Default":
        return None
    try:
        d = mat.PhysicalProperties.get("Density")
        if not d:
            return None
        v = FreeCAD.Units.Quantity(d).getValueAs("kg/m^3")
        return float(getattr(v, "Value", v))
    except Exception:
        return None


def material_without_density(obj):
    """The name of obj's material when it is a real one (not "Default") that has no usable Density, else None."""
    mat = getattr(obj, "ShapeMaterial", None)
    if mat is None or mat.Name == "Default" or density_kg_m3(obj) is not None:
        return None
    return mat.Name


def _pieces(comp, nodensity=None):
    """[(shape in comp's frame, density | None)] for each solid object inside the component (hidden ones skipped,
    as Part.getShape does)."""
    import Part
    out = []

    def walk(o, path):
        if path and not getattr(o, "Visibility", True):
            return
        t = o.getLinkedObject(True) if _is_link(o) else o
        if t.isDerivedFrom("Part::Feature") and not _is_lcs(t):
            s = Part.getShape(comp, path, needSubElement=False, transform=False)
            if not s.isNull():
                out.append((s, density_kg_m3(t)))
                missing = material_without_density(t)
                if missing is not None and nodensity is not None and missing not in nodensity:
                    nodensity.append(missing)
            return
        for c in _children(o):
            walk(c, path + c.Name + ".")

    walk(comp, "")
    return out


def _lcs_in(comp):
    """mount_* coordinate systems inside a component (not through links)."""
    found = []

    def walk(o):
        for c in getattr(o, "Group", []) if not _is_link(o) else []:
            if _is_lcs(c):
                found.append(c)
            walk(c)

    walk(comp)
    return found


def _joint_objects(asm):
    import UtilsAssembly
    jg = UtilsAssembly.getJointGroup(asm)
    return list(jg.Group) if jg is not None else []


def _referenced(jobjs):
    """Names of the objects the joints (and grounded joints) point at."""
    referenced = set()
    for o in jobjs:
        for prop in ("Reference1", "Reference2", "ObjectToGround"):
            r = getattr(o, prop, None)
            if r is None:
                continue
            obj = r[0] if isinstance(r, (tuple, list)) else r
            if obj is not None:
                referenced.add(obj.Name)
    return referenced


def _is_component(o, referenced):
    """Whether `o`, a direct child of the Assembly, is a component (a moving part) for `read_assembly`."""
    if o.TypeId.startswith("Assembly::") and o.TypeId != "Assembly::AssemblyObject":
        return False                                    # joint/view/BOM/simulation groups
    if _is_lcs(o) and o.Name not in referenced:
        return False                                    # an LCS is a part only when a joint uses it (no mass)
    t = o.getLinkedObject(True) if _is_link(o) else o
    if not (t.isDerivedFrom("Part::Feature") or t.isDerivedFrom("App::Part") or _is_lcs(t)):
        return False
    return getattr(o, "Visibility", True) or o.Name in referenced


def component_names(asm):
    """Internal names of `asm`'s components, as `read_assembly` finds them (cheap: no shapes are read)."""
    referenced = _referenced(_joint_objects(asm))
    return [o.Name for o in asm.Group if _is_component(o, referenced)]


def read_assembly(asm):
    """Read `asm` (an Assembly::AssemblyObject) into `Extracted`. Reads the saved, solved state; never solves."""
    import Part
    import UtilsAssembly

    inv = _global(asm).inverse()
    warnings, problems = [], []

    jobjs = _joint_objects(asm)
    referenced = _referenced(jobjs)

    # components: the Assembly's direct children that are parts
    components, mount_objs = {}, []
    for o in asm.Group:
        if _is_lcs(o) and o.Name not in referenced and o.Label.startswith("mount_"):
            mount_objs.append((o, None))
            continue
        if not _is_component(o, referenced):
            continue
        nodensity = []
        pieces = _pieces(o, nodensity)
        ds = {d for _, d in pieces}
        shape = Part.getShape(o, "", needSubElement=False, transform=False)
        components[o.Name] = Comp(o.Name, o.Label, shape, inv * _global(o),
                                  ds.pop() if len(ds) == 1 else None, pieces, tuple(nodensity))
        for lcs in _lcs_in(o):
            if lcs.Label.startswith("mount_"):
                mount_objs.append((lcs, o.Name))

    def frame(plc, ref):
        if UtilsAssembly.getObject(ref) is None:
            g = _global(ref[0]) * plc
        else:
            g = UtilsAssembly.getJcsGlobalPlc(plc, ref)
        return plc_to_frame(inv * g)

    def comp_of(ref):
        if ref is None or len(ref) != 2 or ref[0] is None:
            return None
        part = ref[0]                                   # UtilsAssembly.getMovingPart
        if part == asm or part.Name not in components:
            return None
        return part.Name

    grounded, joints = [], []
    for o in jobjs:
        if hasattr(o, "ObjectToGround"):
            g = o.ObjectToGround
            if g is not None and g.Name in components:
                grounded.append(g.Name)
            elif g is not None:
                warnings.append(f"grounded object '{g.Label}' isn't a part of the Assembly; ignored")
            continue
        if not hasattr(o, "JointType"):
            continue
        kind = o.JointType
        r1, r2 = o.Reference1, o.Reference2
        c1, c2 = comp_of(r1), comp_of(r2)
        f1 = frame(o.Placement1, r1) if c1 else ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0))
        f2 = frame(o.Placement2, r2) if c2 else ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0))
        limits = None
        if kind in ("Revolute", "Slider"):
            what = "Angle" if kind == "Revolute" else "Length"
            lo = _q(getattr(o, what + "Min")) if getattr(o, "Enable" + what + "Min", False) else None
            hi = _q(getattr(o, what + "Max")) if getattr(o, "Enable" + what + "Max", False) else None
            if lo is not None or hi is not None:
                limits = (lo, hi)
        radii = (_q(o.Distance), _q(getattr(o, "Distance2", 0.0))) if kind in COUPLINGS else None
        joints.append(J(o.Name, o.Label, kind, c1, c2, f1, f2, limits, radii, bool(getattr(o, "Suppressed", False))))

    mounts = []
    for lcs, comp in mount_objs:
        if comp is None:
            for sup in (getattr(lcs, "AttachmentSupport", None) or []):
                obj = sup[0] if isinstance(sup, (tuple, list)) else sup
                cand = obj
                while cand is not None and cand.Name not in components:
                    ps = [p for p in cand.InList if p != asm and not p.TypeId.startswith("Assembly::")]
                    cand = ps[0] if ps else None
                if cand is not None:
                    comp = cand.Name
                    break
        mounts.append((lcs.Label, plc_to_frame(inv * _global(lcs)), comp))

    return Extracted(components, grounded, joints, mounts, warnings, problems)
