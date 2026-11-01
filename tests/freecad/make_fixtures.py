"""Build FreeCAD Assembly fixtures headlessly. Runs INSIDE FreeCAD (no scrimmage/numpy imports): `build(name, path)`.

All dimensions mm. Joints are built *satisfied*: both joint frames coincide in world coordinates, so the solver has
nothing to move.

FreeCAD 1.1 joint-frame convention (probed): `Placement1/2` is the joint frame expressed in the *referenced component's
own local frame* (the sub-element in Reference1/2 is only the GUI's way of computing it). It is recomputed from the
sub-element + Offset1/2 unless `Detach1/2` is True, so we set Detach=True and write Placement1/2 ourselves. World frame
of a joint end = UtilsAssembly.getJcsGlobalPlc(PlacementN, ReferenceN): the referenced object's global placement
(which includes the Assembly's own placement and any App::Part or App::Link above the element) * PlacementN; never
`component.Placement * PlacementN`, which is wrong for nested and linked parts. Revolute axis / Slider direction =
frame +z."""

import os

import math

import FreeCAD as App
import JointObject
import UtilsAssembly

P, V, R = App.Placement, App.Vector, App.Rotation

Z_TO_Y = R(V(0, 0, 1), V(0, 1, 0))   # frame whose z axis is world +y
Z_TO_X = R(V(0, 0, 1), V(1, 0, 0))


class Fix:
    def __init__(self, name):
        self.doc = App.newDocument(name)
        self.asm = self.doc.addObject("Assembly::AssemblyObject", "Assembly")
        self.jg = self.asm.newObject("Assembly::JointGroup", "Joints")
        self.n = 0

    def box(self, name, l, w, h, plc=None):
        o = self.asm.newObject("Part::Box", name)
        o.Length, o.Width, o.Height = l, w, h
        if plc:
            o.Placement = plc
        return o

    def cyl(self, name, r, h, plc=None):
        o = self.asm.newObject("Part::Cylinder", name)
        o.Radius, o.Height = r, h
        if plc:
            o.Placement = plc
        return o

    def ground(self, comp):
        g = self.jg.newObject("App::FeaturePython", "Ground_" + comp.Name)
        JointObject.GroundedJoint(g, comp)
        return g

    def joint(self, kind, c1, c2, world_frame, name=None, world_frame2=None):
        """Joint of `kind` between components, both ends at `world_frame` (a Placement) unless world_frame2 is given."""
        j = self.jg.newObject("App::FeaturePython", name or f"{kind}_{c1.Name}_{c2.Name}")
        JointObject.Joint(j, JointObject.JointTypes.index(kind))
        j.Detach1 = True
        j.Detach2 = True
        j.Reference1 = [c1, ["", ""]]
        j.Reference2 = [c2, ["", ""]]
        j.Placement1 = c1.Placement.inverse() * world_frame
        j.Placement2 = c2.Placement.inverse() * (world_frame2 or world_frame)
        return j

    def save(self, path):
        self.doc.recompute()
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)


def _link(f, name, start, end, y=0.0, t=10.0):
    """Box link from start to end in the xz plane (axis along +y joints); local origin at `start`, x along the link."""
    d = end - start
    length = math.hypot(d.x, d.z)
    ang = math.degrees(math.atan2(d.z, d.x))
    return f.box(name, length, t, t, P(start, R(V(0, 1, 0), -ang))), length


def _frame(p, rot=Z_TO_Y):
    return P(p, rot)


def _circle_intersect(a, ra, b, rb, pick_high=True):
    """Intersection of circles centered a, b in the xz plane."""
    dx, dz = b.x - a.x, b.z - a.z
    d = math.hypot(dx, dz)
    x = (ra * ra - rb * rb + d * d) / (2 * d)
    h = math.sqrt(max(ra * ra - x * x, 0.0))
    mx, mz = a.x + x * dx / d, a.z + x * dz / d
    c = [(mx + h * dz / d, mz - h * dx / d), (mx - h * dz / d, mz + h * dx / d)]
    c.sort(key=lambda p: p[1], reverse=pick_high)
    return V(c[0][0], 0, c[0][1])


def arm(path):
    f = Fix("arm")
    base = f.box("Base", 100, 40, 20)
    f.ground(base)
    lcs = f.asm.newObject("Part::LocalCoordinateSystem", "mount_top")
    lcs.Label = "mount_top"
    lcs.Placement = P(V(50, 20, 20), R())
    hinge = _frame(V(100, 20, 10))                       # base +x end, axis +y
    ra = R(V(0, 0, 1), 15)                               # arm yawed 15 deg: frames not axis-aligned with world
    pos = hinge.Base - ra.multVec(V(10, 10, 10))         # arm-local hinge point (10, 10, 10)
    armb = f.box("Arm", 300, 20, 20, P(pos, ra))
    j = f.joint("Revolute", base, armb, hinge, "Hinge")
    j.EnableAngleMin = True
    j.EnableAngleMax = True
    j.AngleMin = -30.0
    j.AngleMax = 120.0
    f.save(path)


def slider(path):
    """Revolute pinion + Slider rack on a grounded rail, coupled by RackPinion (pitch radius in Distance). As in the
    FreeCAD GUI, the RackPinion's two frames are the same frames as the revolute's and the slider's."""
    f = Fix("slider")
    rail = f.box("Rail", 100, 40, 700, P(V(-60, -20, 0), R()))
    f.ground(rail)
    rack = f.box("Rack", 20, 20, 600, P(V(0, 0, 0), R()))
    pc = V(-20, 0, 100)                                   # pinion center: pitch radius 20 from the rack face x=0
    pinion = f.cyl("Pinion", 20, 20, P(pc - Z_TO_Y.multVec(V(0, 0, 10)), Z_TO_Y))
    sl_frame = P(V(10, 10, 0), R())                       # slide along +z
    pin_frame = _frame(pc)                                # spin about +y
    j = f.joint("Slider", rail, rack, sl_frame, "SlideRack")
    j.EnableLengthMin = True
    j.EnableLengthMax = True
    j.LengthMin = 0.0
    j.LengthMax = 500.0
    f.joint("Revolute", rail, pinion, pin_frame, "SpinPinion")
    rp = f.joint("RackPinion", rack, pinion, sl_frame, "RackPinion", world_frame2=pin_frame)
    rp.Distance = 20.0
    f.save(path)


def belt(path):
    """Two grounded Revolutes (radius 20 and 40) joined by a Belt; the Belt reuses the revolutes' frames."""
    f = Fix("belt")
    plate = f.box("Plate", 300, 20, 200, P(V(-100, -10, -100), R()))
    f.ground(plate)
    fa, fb = _frame(V(0, 0, 0)), _frame(V(150, 0, 0))
    a = f.cyl("WheelA", 20, 10, P(fa.Base - Z_TO_Y.multVec(V(0, 0, 5)), Z_TO_Y))
    b = f.cyl("WheelB", 40, 10, P(fb.Base - Z_TO_Y.multVec(V(0, 0, 5)), Z_TO_Y))
    f.joint("Revolute", plate, a, fa, "SpinA")
    f.joint("Revolute", plate, b, fb, "SpinB")
    bj = f.joint("Belt", a, b, fa, "Belt", world_frame2=fb)
    bj.Distance = 20.0
    bj.Distance2 = 40.0
    f.save(path)


def _face_with_vertex(shape, normal, comp_name):
    """Names ('Face<i>', 'Vertex<j>') of the face whose outward normal is `normal` and one of its vertices."""
    for i, face in enumerate(shape.Faces, 1):
        n = face.normalAt(0, 0)
        if (n - normal).Length < 1e-9 and face.Vertexes:
            v = face.Vertexes[0]
            for j, sv in enumerate(shape.Vertexes, 1):
                if (sv.Point - v.Point).Length < 1e-9:
                    return f"Face{i}", f"Vertex{j}"
    raise ValueError(comp_name)


def _gui_joint(f, kind, ref1, ref2, name):
    """Joint as the FreeCAD 1.1 GUI makes it: Detach False and Placement1/2 computed by FreeCAD from the sub-elements.
    Reference = [moving part, [elementpath, ...]] where the moving part is the assembly's direct child and
    elementpath runs from inside it to the element (e.g. "Face4", or "Base.Face4" below an App::Part). Probed: the
    solver ignores the [assembly, ["Comp.Face"]] form, and moves the wrong parts with it."""
    j = f.jg.newObject("App::FeaturePython", name)
    JointObject.Joint(j, JointObject.JointTypes.index(kind))
    j.Reference1 = [ref1[0], list(ref1[1])]
    j.Reference2 = [ref2[0], list(ref2[1])]
    f.doc.recompute()
    return j


def settle(f, grounded, saved):
    """Solve a GUI-style (non-detached) assembly. Probed quirk: the recompute that follows setting such a joint's
    references also rotates the *grounded* part (the whole system turns); putting it back (`saved`, its placement
    before the joint existed) and solving again leaves it put and re-seats the arm against it."""
    assert f.asm.solve() == 0
    grounded.Placement = saved
    assert f.asm.solve() == 0
    assert grounded.Placement.isSame(saved, 1e-9)


def twist(f, j, comp, degrees):
    """Rotate `comp` about the joint's world axis (a Revolute's free DOF) so it isn't left in the solver's pose."""
    g = UtilsAssembly.getJcsGlobalPlc(j.Placement1, j.Reference1)
    axis = g.Rotation.multVec(V(0, 0, 1))
    about = P(g.Base, R(axis, degrees)) * P(-g.Base, R())
    comp.Placement = about * comp.Placement
    f.doc.recompute()
    assert f.asm.solve() == 0


def arm_gui(path):
    """GUI-style revolute: a base box face+vertex vs the end circle of a rotated/translated disc 'Arm'. Frames are
    computed by FreeCAD (Detach False); the assembly is solved so the arm ends up satisfied."""
    f = Fix("arm_gui")
    base = f.box("Base", 100, 40, 20)
    f.ground(base)
    face, vtx = _face_with_vertex(base.Shape, V(0, 1, 0), "Base")        # +y face; its z axis is +y
    armc = f.cyl("Arm", 60, 20, P(V(200, 120, 35), R(30, 10, 5)))          # yaw 30, pitch 10, roll 5; far from base
    saved = base.Placement
    j = _gui_joint(f, "Revolute", (base, (face, vtx)), (armc, ("Edge1", "Edge1")), "HingeGui")
    settle(f, base, saved)
    twist(f, j, armc, 40.0)
    f.save(path)


def arm_nested(path):
    """Like arm_gui but the grounded base lives inside an App::Part that has its own placement (nested reference
    [Sub, ['Base.Face..']]); the moving part is the App::Part Sub, the element belongs to Sub.Base."""
    f = Fix("arm_nested")
    sub = f.asm.newObject("App::Part", "Sub")
    sub.Placement = P(V(30, -20, 10), R(V(0, 0, 1), 20))
    base = sub.newObject("Part::Box", "Base")
    base.Length, base.Width, base.Height = 100, 40, 20
    base.Placement = P(V(5, 5, 0), R())
    f.ground(sub)
    face, vtx = _face_with_vertex(base.Shape, V(0, 1, 0), "Base")
    armc = f.cyl("Arm", 60, 20, P(V(200, 120, 35), R(30, 10, 5)))
    saved = sub.Placement
    j = _gui_joint(f, "Revolute", (sub, (f"Base.{face}", f"Base.{vtx}")), (armc, ("Edge1", "Edge1")), "HingeNested")
    settle(f, sub, saved)
    twist(f, j, armc, 40.0)
    f.save(path)


def arm_link(path):
    """The GUI path for real assemblies: the arm is an App::Link (to a hidden cylinder outside the assembly) and the
    joint is made with `Proxy.setJointConnectors`, as the Revolute command does. Afterwards the Assembly itself is
    moved, so a reader must make frames relative to the Assembly: in Assembly coordinates the hinge is still the
    base's +y face corner (100, 40, 0)."""
    f = Fix("arm_link")
    base = f.box("Base", 100, 40, 20)
    f.ground(base)
    src = f.doc.addObject("Part::Cylinder", "ArmSrc")
    src.Radius, src.Height = 60, 20
    src.Visibility = False
    lk = f.asm.newObject("App::Link", "ArmLink")
    lk.LinkedObject = src
    lk.Placement = P(V(200, 120, 35), R(30, 10, 5))
    f.doc.recompute()
    face, vtx = _face_with_vertex(base.Shape, V(0, 1, 0), "Base")
    saved = base.Placement
    j = f.jg.newObject("App::FeaturePython", "HingeLink")
    JointObject.Joint(j, JointObject.JointTypes.index("Revolute"))
    j.Proxy.setJointConnectors(j, [[base, [face, vtx]], [lk, ["Edge1", "Edge1"]]])
    settle(f, base, saved)
    twist(f, j, lk, 40.0)
    f.asm.Placement = P(V(500, -300, 50), R(V(0, 0, 1), 30))
    f.save(path)


def _fourbar_doc(name, grounded_extra=None):
    f = Fix(name)
    a, b_, c, d = 100.0, 40.0, 120.0, 80.0
    g1, g2 = V(0, 0, 0), V(a, 0, 0)
    th = math.radians(60.0)
    pa = V(b_ * math.cos(th), 0, b_ * math.sin(th))
    pb = _circle_intersect(pa, c, g2, d)
    frame = f.box("Frame", a + 40, 20, 10, P(V(-20, -10, -20), R()))
    f.ground(frame)
    crank, _ = _link(f, "Crank", g1, pa)
    coupler, _ = _link(f, "Coupler", pa, pb)
    rocker, _ = _link(f, "Rocker", g2, pb)
    f.joint("Revolute", frame, crank, _frame(g1), "PinGroundCrank")
    f.joint("Revolute", crank, coupler, _frame(pa), "PinCrankCoupler")
    f.joint("Revolute", coupler, rocker, _frame(pb), "PinCouplerRocker")
    f.joint("Revolute", rocker, frame, _frame(g2), "PinRockerGround")
    return f


def fourbar(path):
    _fourbar_doc("fourbar").save(path)


def bad_motor_on_loop(path):
    _fourbar_doc("bad_motor_on_loop").save(path)     # Task 6 adds the motor settings


def base2w(path):
    f = Fix("base2w")
    plate = f.box("Plate", 300, 400, 20, P(V(-150, -200, -10), R()))
    f.ground(plate)
    for nm, y in (("WheelL", 250.0), ("WheelR", -250.0)):
        center = V(0, y, 0)
        pos = center - Z_TO_Y.multVec(V(0, 0, 20))       # cylinder axis (local z) -> world y, centered on `center`
        w = f.cyl(nm, 50.8, 40, P(pos, Z_TO_Y))
        f.joint("Revolute", plate, w, _frame(center), "Axle" + nm)
    f.save(path)


def bad_ungrounded(path):
    f = Fix("bad_ungrounded")
    a = f.box("A", 100, 40, 20)
    b = f.box("B", 100, 20, 20, P(V(100, 10, 0), R()))
    f.joint("Revolute", a, b, _frame(V(100, 20, 10)), "Hinge")
    f.save(path)


def bad_ball(path):
    f = Fix("bad_ball")
    a = f.box("A", 100, 40, 20)
    f.ground(a)
    b = f.box("B", 100, 20, 20, P(V(100, 10, 0), R()))
    f.joint("Ball", a, b, P(V(100, 20, 10), R()), "Ball")
    f.save(path)


def slider_crank(path):
    """Slider-crank: ground -rev- crank -rev- rod -rev- block -slider- ground. It exports: the tree takes the Slider
    and the loop closes at a Revolute (Ruling R6)."""
    f = Fix("slider_crank")
    o = V(0, 0, 0)
    th = math.radians(60.0)
    pa = V(40 * math.cos(th), 0, 40 * math.sin(th))
    px = pa.x + math.sqrt(120.0 ** 2 - pa.z ** 2)
    pp = V(px, 0, 0)
    frame = f.box("Frame", px + 100, 20, 10, P(V(-20, -10, -20), R()))
    f.ground(frame)
    crank, _ = _link(f, "Crank", o, pa)
    rod, _ = _link(f, "Rod", pa, pp)
    block = f.box("Block", 30, 20, 20, P(V(px - 15, -10, -10), R()))
    f.joint("Revolute", frame, crank, _frame(o), "PinGroundCrank")
    f.joint("Revolute", crank, rod, _frame(pa), "PinCrankRod")
    f.joint("Revolute", rod, block, _frame(pp), "PinRodBlock")
    f.joint("Slider", frame, block, _frame(pp, Z_TO_X), "SlideBlock")
    f.save(path)


def bad_slider_loop(path):
    """A loop that can only close at a Slider: a carriage on two parallel rails (two Sliders, ground to carriage)."""
    f = Fix("bad_slider_loop")
    frame = f.box("Frame", 400, 100, 10, P(V(0, -50, -20), R()))
    f.ground(frame)
    car = f.box("Carriage", 60, 100, 20, P(V(100, -50, -10), R()))
    f.joint("Slider", frame, car, _frame(V(100, -40, 0), Z_TO_X), "SlideLeft")
    f.joint("Slider", frame, car, _frame(V(100, 40, 0), Z_TO_X), "SlideRight")
    f.save(path)


def limits_offset(path):
    """Limited joints not saved at 0: the arm is twisted +40 deg about its hinge (limits -30..120) and the block is
    slid +100 mm along its slider (limits 0..500), after the joints were made (FreeCAD's limits are absolute)."""
    f = Fix("limits_offset")
    base = f.box("Base", 100, 40, 20)
    f.ground(base)
    hinge = _frame(V(100, 20, 10))                                    # axis +y
    armb = f.box("Arm", 300, 20, 20, P(V(90, 10, 0), R()))          # arm-local hinge point (10, 10, 10)
    j = f.joint("Revolute", base, armb, hinge, "Hinge")
    j.EnableAngleMin = j.EnableAngleMax = True
    j.AngleMin, j.AngleMax = -30.0, 120.0
    armb.Placement = P(hinge.Base, R(V(0, 1, 0), 40)) * P(-hinge.Base, R()) * armb.Placement
    blk = f.box("Block", 20, 20, 20, P(V(0, 60, 0), R()))
    s = f.joint("Slider", base, blk, P(V(10, 70, 10), R()), "Slide")  # along +z
    s.EnableLengthMin = s.EnableLengthMax = True
    s.LengthMin, s.LengthMax = 0.0, 500.0
    blk.Placement = P(V(0, 0, 100), R()) * blk.Placement
    f.save(path)


def bad_zero_mass(path):
    """A body that is only a Part::LocalCoordinateSystem (no solid, volume 0) hinged to the grounded base."""
    f = Fix("bad_zero_mass")
    base = f.box("Base", 100, 40, 20)
    f.ground(base)
    ghost = f.asm.newObject("Part::LocalCoordinateSystem", "Ghost")
    ghost.Placement = P(V(100, 20, 10), R())
    f.joint("Revolute", base, ghost, _frame(V(100, 20, 10)), "Hinge")
    f.save(path)


def _material(name):
    """The FreeCAD 1.x library material called `name`."""
    import Materials
    for uuid, m in Materials.MaterialManager().Materials.items():
        if m.Name == name:
            return Materials.MaterialManager().getMaterial(uuid)
    raise ValueError(name)


def mass(path):
    """Mass/inertia/mesh fixture (not a mechanism; only Plain is grounded, nothing is jointed). Boxes 100x40x20:
    Plain (no material assigned, i.e. FreeCAD's Default), Alu (Aluminum-6061-T6), Steel (Steel-Generic); and an
    L-shaped App::Part 'L' (its own placement) holding two boxes LA, LB with their own placements."""
    f = Fix("mass")
    plain = f.box("Plain", 100, 40, 20, P(V(-300, 0, 0), R(V(1, 2, 3), 25)))
    f.ground(plain)
    alu = f.box("Alu", 100, 40, 20, P(V(0, 200, 0), R()))
    alu.ShapeMaterial = _material("Aluminum-6061-T6")
    steel = f.box("Steel", 100, 40, 20, P(V(0, 400, 0), R()))
    steel.ShapeMaterial = _material("Steel-Generic")
    lp = f.asm.newObject("App::Part", "L")
    lp.Placement = P(V(200, -100, 50), R(30, 20, 10))
    la = lp.newObject("Part::Box", "LA")
    la.Length, la.Width, la.Height = 120, 20, 20
    la.Placement = P(V(5, 0, 0), R(V(0, 0, 1), 10))
    lb = lp.newObject("Part::Box", "LB")
    lb.Length, lb.Width, lb.Height = 20, 30, 90
    lb.Placement = P(V(-10, -5, 20), R(V(1, 0, 0), -15))
    f.save(path)


def bad_chain(path):
    """Ruling R10: a sprocket on the shoulder axis (turning on the base) chained by a Belt to a wrist on the arm. The
    two coupled joints move relative to different parts (base and arm), which scrimmage can't couple."""
    f = Fix("bad_chain")
    base = f.box("Base", 100, 40, 20, P(V(-100, -20, -10), R()))
    f.ground(base)
    shoulder = _frame(V(0, 20, 0))                                        # axis +y, on the base's +y face
    armb = f.box("Arm", 300, 20, 20, P(V(0, 20, -10), R()))
    sprocket = f.cyl("Sprocket", 20, 10, P(V(0, -30, 0), Z_TO_Y))        # on the same axis line, the other side
    wrist = _frame(V(300, 20, 0))
    hand = f.box("Hand", 60, 20, 20, P(V(300, 20, -10), R()))
    f.joint("Revolute", base, armb, shoulder, "Shoulder")
    f.joint("Revolute", base, sprocket, shoulder, "SprocketSpin")
    f.joint("Revolute", armb, hand, wrist, "Wrist")
    ch = f.joint("Belt", sprocket, hand, shoulder, "Chain", world_frame2=wrist)
    ch.Distance, ch.Distance2 = 20.0, 20.0
    f.save(path)


def shell(path):
    """Faces with no volume: 'Sheet' (a Part::Plane, 200 x 100 mm in its xy plane) on a Revolute, given a mass override
    in the tests, and 'Skin' (another plane) fixed to the grounded box, whose mass is silently lost without a warning."""
    f = Fix("shell")
    base = f.box("Base", 100, 40, 20)
    f.ground(base)
    sheet = f.asm.newObject("Part::Plane", "Sheet")
    sheet.Length, sheet.Width = 200.0, 100.0
    sheet.Placement = P(V(100, 20, 10), R(V(1, 0, 0), 90))                # stands in the xz plane, hinged on +y
    skin = f.asm.newObject("Part::Plane", "Skin")
    skin.Length, skin.Width = 100.0, 40.0
    skin.Placement = P(V(0, 0, 20), R())                                   # the box's top face
    f.joint("Revolute", base, sheet, _frame(V(100, 20, 10)), "Hinge")
    f.joint("Fixed", base, skin, P(V(0, 0, 20), R()), "Glue")
    f.save(path)


XLINK_LIB = "xlink_lib.FCStd"


def xlink(path):
    """Cross-document links: the Assembly's arm is an App::Link to a cylinder in a second document, saved next to it as
    xlink_lib.FCStd (FreeCAD stores the link's file relative to this one)."""
    lib = App.newDocument("xlink_lib")
    src = lib.addObject("Part::Cylinder", "Wheel")
    src.Radius, src.Height = 60, 20
    lib.recompute()
    lib.saveAs(os.path.join(os.path.dirname(path), XLINK_LIB))
    f = Fix("xlink")
    f.doc.saveAs(path)                                    # a link into another document needs a saved owner
    base = f.box("Base", 100, 40, 20)
    f.ground(base)
    lk = f.asm.newObject("App::Link", "ArmLink")
    lk.LinkedObject = src
    lk.Placement = P(V(100, 40, 10), Z_TO_Y)               # the cylinder's axis along +y, its end face on the base's
    f.joint("Revolute", base, lk, _frame(V(100, 40, 10)), "Spin")
    f.save(path)
    App.closeDocument(lib.Name)


BUILDERS = {fn.__name__: fn for fn in (arm, arm_gui, arm_nested, arm_link, slider, belt, fourbar, base2w, slider_crank, limits_offset, bad_ungrounded,
                                       bad_ball, bad_slider_loop,
                                       bad_zero_mass, bad_motor_on_loop, mass, bad_chain, shell, xlink)}
NAMES = list(BUILDERS)


def build(name, path):
    BUILDERS[name](path)
