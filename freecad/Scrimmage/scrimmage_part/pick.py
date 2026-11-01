"""Mount frames picked from the 3D selection (runs inside FreeCAD; no GUI imports, so it is tested headless).

The panel passes what `FreeCADGui.Selection.getSelectionEx("", 0)` gives: a root object and a full sub-element path
(e.g. the Assembly and "Arm.Face6"), or a component and its own element ("Face6"). The picked element sets a frame:
- a flat face: its center (center of mass of the face), z = the outward normal;
- a circular edge: the circle's center, z = its axis;
- a cylindrical face: the point on its axis level with the face's center, z = the axis.
A circle's or cylinder's axis has no meaningful sign (OCC may give either), so it is taken pointing along the
positive side of its largest component in the component's frame.
The frame's x/y follow the shortest rotation from the component's own +z. It is stored in the component's own frame
(settings.py `frame_local`), so the mount follows the component when the Assembly moves.
"""

import re

from .overview import unique_name
from .read import _global, component_names, plc_to_frame
from .tree import sanitize


class PickError(Exception):
    """The selection can't make a mount; the message says what to pick instead."""


WHAT = "pick a flat face, a round edge or a cylindrical face of a part in the Assembly"


def point_axis(sub):
    """(center, z axis, directed) of a picked sub-shape (FreeCAD Vectors in the sub-shape's own coordinates);
    `directed` is False when the axis's sign means nothing (a circle or a cylinder). Raises PickError."""
    import Part
    kind = getattr(sub, "ShapeType", None)
    if kind == "Face":
        surf = sub.Surface
        if isinstance(surf, Part.Plane):
            c = sub.CenterOfMass
            u, v = surf.parameter(c)
            return c, sub.normalAt(u, v), True
        if isinstance(surf, Part.Cylinder):
            a = surf.Axis
            o = surf.Center
            return o + a * (sub.CenterOfMass - o).dot(a), a, False
    elif kind == "Edge" and isinstance(sub.Curve, Part.Circle):
        return sub.Curve.Center, sub.Curve.Axis, False
    what = kind.lower() if kind else "nothing"
    if kind in ("Face", "Edge"):
        geom = type(sub.Surface if kind == "Face" else sub.Curve).__name__.lower()
        what = f"a {geom} {what}"
    raise PickError(f"{WHAT} (got {what})")


def _element(subname):
    """The element at the end of a sub-element path ("Face6"), mapped-name prefixes (";...") dropped."""
    last = subname.split(".")[-1]
    m = re.search(r"((Face|Edge|Vertex)\d+)$", last)
    return m.group(1) if m else last


def resolve(asm, root, subname):
    """(component object, picked sub-shape in global coordinates) for a selection `root` + `subname`; raises
    PickError when it isn't an element of one of `asm`'s components."""
    import Part
    names = subname.split(".")[:-1]                     # object path below root
    chain = [root]
    for n in names:
        o = root.Document.getObject(n)
        if o is None:
            break
        chain.append(o)
    comp = None
    members = {o.Name for o in asm.Group}
    if root == asm:
        comp = chain[1] if len(chain) > 1 else None
    else:
        for i, o in enumerate(chain):
            if o == asm and i + 1 < len(chain):
                comp = chain[i + 1]
                break
        else:
            if root.Name in members:
                comp = root
    if comp is None or comp.Name not in members:
        raise PickError(f"the selection ({root.Label}.{subname}) isn't part of the Assembly '{asm.Label}'; {WHAT}")
    if comp.Name not in component_names(asm):
        raise PickError(f"'{comp.Label}' isn't exported as a part of the Assembly (it is hidden and unjointed, or "
                        f"not a solid part); {WHAT}")
    if not _element(subname):
        raise PickError(f"{WHAT} (got the whole of {comp.Label})")
    sub = Part.getShape(root, subname, needSubElement=True)
    if sub.isNull():
        raise PickError(f"{WHAT} (got nothing)")
    return comp, sub


def pick(asm, root, subname, used):
    """A new mount `(name, comp_name, frame_local)` from a selection; `name` is `<component label>_mount`, made unique
    against `used` (a set of mount names). Raises PickError."""
    import FreeCAD
    comp, sub = resolve(asm, root, subname)
    pos, axis, directed = point_axis(sub)
    to_local = _global(comp).inverse()
    z = to_local.Rotation.multVec(axis)
    z.normalize()
    if not directed and max((z.x, z.y, z.z), key=abs) < 0:
        z = -z
    local = FreeCAD.Placement(to_local.multVec(pos), FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), z))
    (x, y, z), (w, qx, qy, qz) = plc_to_frame(local)
    frame = ([_r(x), _r(y), _r(z)], [_r(w), _r(qx), _r(qy), _r(qz)])
    return unique_name(f"{sanitize(comp.Label)}_mount", used), comp.Name, frame


def _r(v):
    v = round(float(v), 9)
    return 0.0 if v == 0 else v


def selection_path(asm, comp_name):
    """(root object name, sub-element path) that selects component `comp_name` of `asm` in the 3D view, for
    `FreeCADGui.Selection.addSelection(doc, root, sub)`."""
    parents = asm.Parents                               # [(root, "path.to.Assembly.")] when nested
    if parents:
        root, prefix = parents[0]
        return root.Name, f"{prefix}{comp_name}."
    return asm.Name, f"{comp_name}."
