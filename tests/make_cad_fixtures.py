"""Makes the CAD test fixtures in tests/data/cad (climber*.step and stl/*.stl). Outputs are committed; only
re-run this to change them:

    freecad.cmd tests/make_cad_fixtures.py

The FreeCAD snap can't write outside $HOME's non-hidden folders, so this writes under
~/snap/freecad/common/cadfix/ and copies into tests/data/cad/ (run from the repo root). Sizes are millimeters.
"""
import os
import shutil

import FreeCAD as App
import Import
import Part

OUT = os.path.expanduser("~/snap/freecad/common/cadfix")
DEST = os.path.join(os.getcwd(), "tests", "data", "cad")
os.makedirs(OUT, exist_ok=True)
# FreeCAD renames repeated labels (bolt001...) unless told not to; the repeated-name tests need identical names
App.ParamGet("User parameter:BaseApp/Preferences/Document").SetBool("DuplicateLabels", True)


def V(*a):
    return App.Vector(*a)


def box(doc, parent, name, size, pos):
    b = doc.addObject("Part::Box", name)
    b.Length, b.Width, b.Height = size
    b.Placement.Base = V(*pos)
    b.Label = name
    parent.addObject(b)
    return b


def cyl(doc, parent, name, r, h, pos, along_x=False):
    c = doc.addObject("Part::Cylinder", name)
    c.Radius, c.Height = r, h
    c.Placement = App.Placement(V(*pos), App.Rotation(V(0, 1, 0), 90) if along_x else App.Rotation())
    c.Label = name
    parent.addObject(c)
    return c


def build(arm_b_len=800, bolts=6, right_name="hook_assy"):
    doc = App.newDocument("climber")
    top = doc.addObject("App::Part", "Climber")
    top.Label = "Climber"
    box(doc, top, "base_plate", (300, 200, 10), (0, 0, 0))
    box(doc, top, "arm_A", (20, 20, 800), (50, 90, 10))
    box(doc, top, "arm_B", (20, 20, arm_b_len), (230, 90, 10))
    for i, (x, h) in enumerate(((50, 800), (230, arm_b_len))):
        a = doc.addObject("App::Part", f"assy{i}")
        top.addObject(a)
        a.Label = "hook_assy" if i == 0 else right_name
        # identical hook in each: the sub-assembly's placement carries it to the arm top
        cyl(doc, a, f"hook_{i}", 8, 60, (0, 0, 0), along_x=True)
        a.Placement.Base = V(x - 20, 100, 10 + h + 8)
        a.Label = "hook_assy" if i == 0 else right_name
        a.Group[0].Label = "hook"
    for i in range(bolts):
        cyl(doc, top, f"bolt_{i}", 3, 20, (30 + 40 * i, 20, 10))
        top.Group[-1].Label = "bolt"
    doc.recompute()
    return doc, top


def export(doc, top, name, rot=None):
    if rot:
        # the export ignores an App::Part's own placement, so turn each child instead
        for c in top.Group:
            c.Placement = rot.multiply(c.Placement)
        doc.recompute()
    path = os.path.join(OUT, name)
    Import.export([top], path)
    shutil.copy(path, os.path.join(DEST, name))
    App.closeDocument(doc.Name)


def stl(path, shape, flip=False):
    pts, tris = shape.tessellate(0.5)
    with open(path, "w") as f:
        f.write("solid part\n")
        for a, b, c in tris:
            if flip:
                b, c = c, b
            f.write("facet normal 0 0 0\nouter loop\n")
            for i in (a, b, c):
                f.write("vertex %.6f %.6f %.6f\n" % tuple(pts[i]))
            f.write("endloop\nendfacet\n")
        f.write("endsolid part\n")


doc, top = build()
export(doc, top, "climber.step")
doc, top = build(arm_b_len=900, bolts=5, right_name="hook_assy_right")
export(doc, top, "climber_v2.step")
doc, top = build()
export(doc, top, "climber_yup.step", rot=App.Placement(V(0, 0, 0), App.Rotation(V(1, 0, 0), -90)))

os.makedirs(os.path.join(DEST, "stl"), exist_ok=True)
for name, shape in (("base_plate", Part.makeBox(300, 200, 10)),
                    ("arm_A", Part.makeBox(20, 20, 800, V(50, 90, 10))),
                    ("hook", Part.makeCylinder(8, 60, V(0, 0, 0), V(1, 0, 0)))):
    stl(os.path.join(DEST, "stl", name + ".stl"), shape)
stl(os.path.join(DEST, "stl", "hook_inside_out.stl"), Part.makeCylinder(8, 60, V(0, 0, 0), V(1, 0, 0)), flip=True)
