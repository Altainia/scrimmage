"""Makes the CAD test fixtures in tests/data/cad (climber*.step, crank_climber.step and stl/*.stl). Outputs are
committed; only re-run this to change them:

    freecad.cmd tests/make_cad_fixtures.py

The FreeCAD snap can't write outside $HOME's non-hidden folders, so this writes under
~/snap/freecad/common/cadfix/ and copies into tests/data/cad/ (run from the repo root). Sizes are millimeters.
"""
import math
import os
import shutil

try:
    import FreeCAD as App
    import Import
    import Part
except ImportError:    # the NAUO fixture below is made without FreeCAD
    App = None

OUT = os.path.expanduser("~/snap/freecad/common/cadfix")
DEST = os.path.join(os.getcwd(), "tests", "data", "cad")
if App is not None:
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


def cyl_y(doc, parent, name, r, h, pos):
    """A cylinder along +y from `pos` (the center of its first face)."""
    c = doc.addObject("Part::Cylinder", name)
    c.Radius, c.Height = r, h
    c.Placement = App.Placement(V(*pos), App.Rotation(V(1, 0, 0), -90))
    c.Label = name
    parent.addObject(c)
    return c


CRANK_AT = (190, 660)     # mm: the crank's shaft (x forward, z up); it turns about y
CRANK_LEAN = 0.0          # degrees the arms' line leans forward from vertical, about the shaft (tried: 20)


def build_crank_climber():
    """The train-wheel ladder climber (examples/blocks/ladder_climber), drawn in robot coordinates (x forward, y left,
    z up, the floor under the robot's center at 0) so its import needs no frame. One crank (a shaft with a disc each
    side, turning about y at CRANK_AT) carries two pins 200 mm out, 180 degrees apart. Each pin's rod (280 mm between
    pins) hangs down to an arm that slides along a line through the shaft (a slider-crank), so the arms rise and fall
    in turn, 400 mm each. That line can lean CRANK_LEAN forward (the arm angle). A bracket carries each arm's hook
    forward to just past the bumper: a finger over the rung, a lip at its tip, and a back plate below the hinge pin
    that the rung meets as the robot drives in. Drawn with arm_A at the top of its stroke (its finger 45 mm and its
    lip 20 mm over rung1, whose top is 610 mm up) and arm_B at the bottom.

    The moving parts are drawn upright about the shaft (`local`: x, y, z from the shaft) and leaned as placed."""
    doc = App.newDocument("crank_climber")
    top = doc.addObject("App::Part", "CrankClimber")
    top.Label = "CrankClimber"
    cx, cz = CRANK_AT
    lean = App.Placement(V(cx, 0, cz), App.Rotation(V(0, 1, 0), CRANK_LEAN))

    def assy(name):
        a = doc.addObject("App::Part", name)
        a.Label = name
        top.addObject(a)
        return a

    def local(obj):
        obj.Placement = lean.multiply(obj.Placement)
        return obj

    base = assy("base")
    box(doc, base, "base_plate", (360, 600, 12), (-30, -300, 140))
    for side, y in (("L", 190), ("R", -210)):
        box(doc, base, f"tower_{side}", (30, 20, cz + 20 - 152), (cx - 15, y, 152))
    for side, y in (("L", 270), ("R", -290)):
        down = (cz - 152) / math.cos(math.radians(CRANK_LEAN))      # from the shaft down to the base plate
        local(box(doc, base, f"guide_{side}", (14, 20, down + 140), (-28, y, -down)))
    cyl_y(doc, base, "neo_motor", 30, 60, (cx, -150, cz))
    box(doc, base, "gearbox", (60, 40, 60), (cx - 30, -90, cz - 30))

    crank = assy("crank")
    cyl_y(doc, crank, "shaft", 10, 520, (cx, -260, cz))
    cyl_y(doc, crank, "disc_L", 210, 6, (cx, 224, cz))
    cyl_y(doc, crank, "disc_R", 210, 6, (cx, -230, cz))
    local(cyl_y(doc, crank, "crank_pin_A", 8, 34, (0, 230, 200)))
    local(cyl_y(doc, crank, "crank_pin_B", 8, 34, (0, -264, -200)))

    # per side: its y, where its crank pin is (200 up or down) and its arm's pin (280 below that)
    for s, y, pin in (("A", 270, 200), ("B", -290, -200)):
        arm_pin, hinge = pin - 280, pin - 280 + 75
        rod = assy(f"rod_{s}")
        local(box(doc, rod, f"rod_{s}_bar", (16, 10, 296), (-8, y - 16 if s == "A" else y + 26, pin - 288)))
        arm = assy(f"arm_{s}")
        local(cyl_y(doc, arm, f"arm_pin_{s}", 6, 16, (0, y - 16 if s == "A" else y + 20, arm_pin)))
        local(box(doc, arm, f"arm_{s}_bar", (20, 20, 95), (-10, y, arm_pin - 20)))
        local(box(doc, arm, f"bracket_{s}", (228, 20, 20), (10, y, hinge - 20)))
        hook = assy(f"hook_{s}")
        local(cyl_y(doc, hook, f"hook_pin_{s}", 4, 20, (244, y, hinge)))
        local(box(doc, hook, f"finger_{s}", (112, 20, 15), (238, y, hinge)))
        local(box(doc, hook, f"lip_{s}", (15, 20, 25), (335, y, hinge - 25)))
        local(box(doc, hook, f"back_{s}", (12, 20, 110), (238, y, hinge - 110)))
    doc.recompute()
    return doc, top


if App is not None:
    doc, top = build_crank_climber()
    export(doc, top, "crank_climber.step")
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


def make_nauo_fixture(src=None, dst=None):
    """climber_nauo.step: climber.step with every NEXT_ASSEMBLY_USAGE_OCCURRENCE('<id>','<name>',... rewritten, in
    file order, to ('NAUO<n>',' ',' ',..., as SolidWorks writes it. Needs no FreeCAD:

        python tests/make_cad_fixtures.py
    """
    import re
    src = src or os.path.join(DEST, "climber.step")
    dst = dst or os.path.join(DEST, "climber_nauo.step")
    count = iter(range(1, 10 ** 6))
    text = re.sub(r"(NEXT_ASSEMBLY_USAGE_OCCURRENCE\()'[^']*','[^']*','[^']*'",
                  lambda m: f"{m.group(1)}'NAUO{next(count)}',' ',' '", open(src, encoding="utf-8").read())
    open(dst, "w", encoding="utf-8").write(text)


if __name__ == "__main__" or App is not None:    # freecad.cmd runs this file under another __name__
    make_nauo_fixture()
