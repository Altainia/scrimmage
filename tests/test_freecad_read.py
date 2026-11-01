"""Exporter, Task 4: reading a FreeCAD Assembly (read.py) and building the body tree (tree.py).

The tree builder is plain Python, so its rules are tested here without FreeCAD on hand-made `Extracted` data; the
reader is tested on the headless-FreeCAD fixtures (tests/freecad/make_fixtures.py)."""

import json
import math
import sys
from pathlib import Path

import pytest

from freecad_harness import needs_freecad, run_in_freecad

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "freecad" / "Scrimmage"))
from scrimmage_part import tree  # noqa: E402
from scrimmage_part.errors import ExportError  # noqa: E402
from scrimmage_part.read import Comp, Extracted, J  # noqa: E402

ID = (1.0, 0.0, 0.0, 0.0)
Y_UP = (math.sqrt(0.5), -math.sqrt(0.5), 0.0, 0.0)      # rotation taking z to +y (about -x by 90 degrees)
Y_DOWN = (math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0)     # z to -y


def comp(name):
    return Comp(name, name, None, None, None, [])


def joint(name, kind, c1, c2, pos=(0.0, 0.0, 0.0), q1=ID, pos2=None, q2=None, limits=None, radii=None, suppressed=False):
    return J(name, name, kind, c1, c2, (tuple(pos), q1), (tuple(pos2 or pos), q2 or q1), limits, radii, suppressed)


def ex(names, joints, grounded=("A",)):
    return Extracted({n: comp(n) for n in names}, list(grounded), list(joints), [], [], [])


def problems(e):
    with pytest.raises(ExportError) as info:
        tree.build(e)
    return info.value.problems


# ---------------------------------------------------------------- tree rules (no FreeCAD)

def test_fixed_joints_merge_and_names_are_sanitized_and_deduplicated():
    e = Extracted({"A": Comp("A", "base plate", None, None, None, []), "B": comp("B"),
                   "C": Comp("C", "base-plate", None, None, None, [])}, ["A"],
                  [joint("F", "Fixed", "A", "B"), joint("H", "Revolute", "A", "C", (1, 2, 3), Y_UP)], [], [], [])
    t = tree.build(e)
    assert [(b.name, b.comps, b.parent) for b in t.bodies] == [("base_plate", ["A", "B"], None),
                                                                ("base_plate_2", ["C"], "base_plate")]
    assert t.bodies[0].frame_world == ((0.0, 0.0, 0.0), ID)
    assert t.bodies[1].joint.name == "H" and t.bodies[1].frame_world == ((1, 2, 3), Y_UP)


def test_ungrounded_is_an_error_naming_the_components():
    (p,) = problems(ex("AB", [joint("H", "Revolute", "A", "B")], grounded=()))
    assert "grounded" in p and "A" in p and "B" in p


def test_unsupported_types_are_errors_and_suppressed_joints_are_ignored():
    p = problems(ex("ABC", [joint("Ball1", "Ball", "A", "B"), joint("Cyl", "Cylindrical", "A", "C"),
                            joint("Dist", "Distance", "B", "C", suppressed=True)]))
    assert len(p) == 2 and "Ball1" in p[0] and "Revolute, Slider or Fixed" in p[0] and "Cyl" in p[1]


def test_unreachable_groups_merge_into_the_root_with_a_warning():
    t = tree.build(ex("EABCD", [joint("H", "Revolute", "A", "B"), joint("CD", "Revolute", "C", "D")]))
    assert [b.comps for b in t.bodies] == [["E", "A", "C", "D"], ["B"]]
    assert t.bodies[0].name == "A"                   # named after the grounded group, not the first component
    (w,) = t.warnings                                # E isn't jointed to anything: merged and named too
    assert w.endswith(": E, C, D")


def test_bfs_order_tree_joints_and_a_revolute_loop():
    # four-bar: A ground, B crank, C coupler, D rocker; loop closes at C-D
    t = tree.build(ex("ABCD", [joint("AB", "Revolute", "A", "B"), joint("BC", "Revolute", "B", "C", (1, 0, 0)),
                              joint("CD", "Revolute", "C", "D", (2, 0, 0)), joint("DA", "Revolute", "D", "A", (3, 0, 0))]))
    assert [(b.name, b.parent, b.joint.name if b.joint else None) for b in t.bodies] == [
        ("A", None, None), ("B", "A", "AB"), ("D", "A", "DA"), ("C", "B", "BC")]
    (loop,) = t.loops
    assert (loop[0].name, loop[1], loop[2], loop[3]) == ("CD", "C", "D", (2, 0, 0))
    assert t.bodies[2].frame_world[0] == (3, 0, 0)         # child side: D is Reference1 of DA


def test_a_loop_closes_at_a_revolute_when_it_can_ruling_r6():
    # slider-crank: whatever the joint order, the Slider is a tree joint and the loop closes at a Revolute
    t = tree.build(ex("ABCD", [joint("AB", "Revolute", "A", "B"), joint("BC", "Revolute", "B", "C", (1, 0, 0)),
                               joint("CD", "Revolute", "C", "D", (2, 0, 0)), joint("Slide", "Slider", "A", "D", (2, 0, 0))]))
    assert [(b.name, b.parent, b.joint.name) for b in t.bodies[1:]] == [("B", "A", "AB"), ("D", "A", "Slide"),
                                                                       ("C", "D", "CD")]
    assert [x[0].name for x in t.loops] == ["BC"]


def test_a_loop_of_sliders_is_an_error():
    (p,) = problems(ex("AB", [joint("S1", "Slider", "A", "B"), joint("S2", "Slider", "A", "B", (0, 5, 0))]))
    assert "S2" in p and "loop" in p


def test_loop_not_closed_in_cad_is_an_error_ruling_r3():
    (p,) = problems(ex("ABC", [joint("AB", "Revolute", "A", "B"), joint("AC", "Revolute", "A", "C"),
                               joint("Gap", "Revolute", "B", "C", (0, 0, 0), pos2=(0, 0, 2.5))]))
    assert p == "loop at Gap isn't closed in the CAD (off by 2.5 mm)"
    t = tree.build(ex("ABC", [joint("AB", "Revolute", "A", "B"), joint("AC", "Revolute", "A", "C"),
                              joint("Gap", "Revolute", "B", "C", (0, 0, 0), pos2=(0, 0, 0.5))]))
    assert len(t.loops) == 1 and any("not solved" in w and "Gap" in w for w in t.warnings)


def test_a_slid_slider_is_solved_but_an_offset_one_is_not():
    t = tree.build(ex("AB", [joint("S", "Slider", "A", "B", (0, 0, 0), pos2=(0, 0, 100))]))
    assert t.warnings == [] and t.bodies[1].saved == pytest.approx(100)
    t = tree.build(ex("AB", [joint("S", "Slider", "A", "B", (0, 0, 0), pos2=(0, 0.5, 100))]))
    assert any("not solved" in w and "0.5 mm" in w for w in t.warnings)


def test_two_grounded_parts_joined_by_a_joint_say_unground_one():
    (p,) = problems(ex("AB", [joint("H", "Revolute", "A", "B")], grounded=("A", "B")))
    assert "H" in p and "un-ground" in p


def test_unsolved_tree_joint_warns():
    t = tree.build(ex("AB", [joint("H", "Revolute", "A", "B", (0, 0, 0), pos2=(0, 0, 0.1))]))
    assert any("not solved" in w and "H" in w for w in t.warnings)
    t = tree.build(ex("AB", [joint("H", "Revolute", "A", "B", (0, 0, 0), pos2=(0, 0, 0.001))]))
    assert t.warnings == []


def test_limits_follow_the_child_axis():
    # same z on both sides, child is Reference2: limits as written
    t = tree.build(ex("AB", [joint("H", "Revolute", "A", "B", q1=Y_UP, limits=(-30.0, 120.0))]))
    assert t.bodies[1].limits == (-30.0, 120.0) and t.bodies[1].frame_world[1] == Y_UP
    # anti-parallel GUI frames: the child's (Reference2's) axis is used and the range flips
    t = tree.build(ex("AB", [joint("H", "Revolute", "A", "B", q1=Y_UP, q2=Y_DOWN, limits=(-30.0, 120.0))]))
    assert t.bodies[1].frame_world[1] == Y_DOWN and t.bodies[1].limits == (-120.0, 30.0)
    # child is Reference1: the joint moves Reference2 relative to Reference1, so seen from the child it's reversed
    t = tree.build(ex("AB", [joint("H", "Slider", "B", "A", q1=Y_UP, limits=(0.0, None))]))
    assert t.bodies[1].comps == ["B"] and t.bodies[1].limits == (None, 0.0)


def test_limits_are_relative_to_the_saved_pose():
    c, s_ = math.cos(math.radians(20)), math.sin(math.radians(20))
    w1, x1 = Y_UP[0], Y_UP[1]
    turned = (w1 * c, x1 * c, -x1 * s_, w1 * s_)     # Y_UP * rot_z(+40): frame1 turned +40 deg about its own z
    t = tree.build(ex("AB", [joint("H", "Revolute", "A", "B", q1=Y_UP, q2=turned, limits=(-30.0, 120.0))]))
    assert t.bodies[1].saved == pytest.approx(40)
    assert t.bodies[1].limits == pytest.approx((-70.0, 80.0))
    t = tree.build(ex("AB", [joint("S", "Slider", "A", "B", (0, 0, 0), pos2=(0, 0, 100), limits=(0.0, 500.0))]))
    assert t.bodies[1].limits == pytest.approx((-100.0, 400.0))
    t = tree.build(ex("AB", [joint("S", "Slider", "B", "A", (0, 0, 0), pos2=(0, 0, 100), limits=(0.0, 500.0))]))
    assert t.bodies[1].limits == pytest.approx((-400.0, 100.0))       # seen from Reference1, reversed


def test_couplings():
    js = [joint("SA", "Revolute", "A", "B", q1=Y_UP), joint("SB", "Revolute", "A", "C", (150, 0, 0), q1=Y_UP),
          joint("Belt", "Belt", "B", "C", (0, 0, 0), Y_UP, (150, 0, 0), Y_UP, radii=(20.0, 40.0)),
          joint("Gear", "Gears", "C", "B", (150, 0, 0), Y_UP, (0, 0, 0), Y_UP, radii=(40.0, 20.0))]
    t = tree.build(ex("ABC", js))
    assert [(c[0].name, c[1].name, c[2].name, c[3]) for c in t.couplings] == [
        ("Belt", "SA", "SB", 0.5), ("Gear", "SB", "SA", -2.0)]
    # a coupling frame anti-parallel to its joint's axis flips the sign
    js[2] = joint("Belt", "Belt", "B", "C", (0, 0, 0), Y_DOWN, (150, 0, 0), Y_UP, radii=(20.0, 40.0))
    assert tree.build(ex("ABC", js[:3])).couplings[0][3] == -0.5


def test_rack_and_pinion_is_pinion_then_rack_in_mm_per_radian():
    z = ID
    js = [joint("SlideRack", "Slider", "A", "R", (10, 10, 0), z), joint("SpinPinion", "Revolute", "A", "P", (-20, 0, 100), Y_UP),
          joint("RP", "RackPinion", "R", "P", (10, 10, 0), z, (-20, 0, 100), Y_UP, radii=(20.0, 0.0))]
    (c,) = tree.build(ex("ARP", js)).couplings
    # pinion at x = -20 turning about +y: its surface at the rack (x = 0) moves along -z, so the rack goes down
    assert (c[1].name, c[2].name, c[3]) == ("SpinPinion", "SlideRack", -20.0)
    js[1] = joint("SpinPinion", "Revolute", "A", "P", (40, 0, 100), Y_UP)      # pinion on the other side of the rack
    js[2] = joint("RP", "RackPinion", "R", "P", (10, 10, 0), z, (40, 0, 100), Y_UP, radii=(20.0, 0.0))
    assert tree.build(ex("ARP", js)).couplings[0][3] == 20.0


def test_coupling_errors():
    js = [joint("SA", "Revolute", "A", "B", q1=Y_UP), joint("SB", "Revolute", "A", "C", (150, 0, 0), q1=Y_UP),
          joint("Belt", "Belt", "B", "C", (0, 0, 0), Y_UP, (150, 0, 0), Y_UP, radii=(20.0, 0.0))]
    (p,) = problems(ex("ABC", js))
    assert "Belt" in p and "zero or negative radius" in p
    js[2] = js[2]._replace(radii=(-20.0, 40.0))
    (p,) = problems(ex("ABC", js))
    assert "zero or negative radius" in p
    # a coupling on the parent side of a joint
    js = [joint("SA", "Revolute", "A", "B", q1=Y_UP), joint("SB", "Revolute", "B", "C", (150, 0, 0), q1=Y_UP),
          joint("Belt", "Belt", "A", "C", (0, 0, 0), Y_UP, (150, 0, 0), Y_UP, radii=(20.0, 40.0))]
    (p,) = problems(ex("ABC", js))
    assert "Belt" in p and "doesn't move" in p
    # coupling a loop joint
    js = [joint("AB", "Revolute", "A", "B"), joint("AC", "Revolute", "A", "C", (5, 0, 0)),
          joint("Loop", "Revolute", "B", "C", (9, 0, 0)), joint("SD", "Revolute", "A", "D", (20, 0, 0)),
          joint("G", "Gears", "C", "D", (9, 0, 0), ID, (20, 0, 0), ID, radii=(1.0, 1.0))]
    p = problems(ex("ABCD", js))
    assert any("G" in x and "Loop" in x for x in p), p


def test_a_slot_does_not_push_a_coupled_joint_onto_a_loop_r13():
    """A, B, C are a triangle of Revolutes (one must close the loop); a gear couples AB (moving B) with AD (moving D).
    A motor slot on AC used to make AC a tree joint first, which left AB closing the loop and broke the gear."""
    js = [joint("BC", "Revolute", "B", "C", (50, 0, 0), Y_UP), joint("AC", "Revolute", "A", "C", (100, 0, 0), Y_UP),
          joint("AB", "Revolute", "A", "B", (0, 0, 0), Y_UP), joint("AD", "Revolute", "A", "D", (150, 0, 0), Y_UP),
          joint("G", "Gears", "B", "D", (0, 0, 0), Y_UP, (150, 0, 0), Y_UP, radii=(20.0, 40.0))]
    for prefer in ((), ("AC",)):
        t = tree.build(ex("ABCD", js), prefer=prefer)
        assert [(c[0].name, c[1].name, c[2].name) for c in t.couplings] == [("G", "AB", "AD")], prefer
        tree_joints = {b.joint.name for b in t.bodies if b.joint is not None}
        assert {"AB", "AD"} <= tree_joints and set(prefer) <= tree_joints, (prefer, tree_joints)
        (loop,) = t.loops                   # one joint of the triangle closes the loop: never a coupled or slotted one
        assert loop[0].name in {"AC", "BC"} - set(prefer), (prefer, loop[0].name)


def test_broken_joint_is_an_error():
    (p,) = problems(ex("AB", [joint("H", "Revolute", None, "B")]))
    assert "H" in p


# ---------------------------------------------------------------- reading the fixtures (FreeCAD)

DUMP = r'''
import json, os, FreeCAD as App, Part
from scrimmage_part import read, tree
from scrimmage_part.errors import ExportError
out = {}
for name, path in json.loads(open(os.path.join(WORK, "paths.json")).read()).items():
    doc = App.openDocument(path)
    try:
        e = read.read_assembly(read.find_assembly(doc))
        d = dict(comps={n: [c.label, read.plc_to_frame(c.placement), c.material_density_kg_m3, len(c.pieces)]
                        for n, c in e.components.items()},
                 grounded=e.grounded, mounts=e.lcs_mounts, read_warnings=e.warnings, shapes={})
        for n, c in e.components.items():
            s = c.shape.copy()
            s.Placement = c.placement * s.Placement        # component -> Assembly coordinates
            ref = Part.getShape(doc.getObject(n))          # a direct child: its own placement is its Assembly placement
            bb = lambda b: [b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax] if b.isValid() else None
            d["shapes"][n] = [bb(s.BoundBox), bb(ref.BoundBox), s.Volume if not s.isNull() else 0.0,
                              sum(p.Volume for p, _ in c.pieces)]
        try:
            d["tree"] = tree.to_data(tree.build(e))
        except ExportError as err:
            d["error"] = err.problems
    finally:
        App.closeDocument(doc.Name)
    out[name] = d
print("SCRIMMAGE_JSON:" + json.dumps(out))
'''


@pytest.fixture(scope="module")
def read_out(fixtures_dir):
    return run_in_freecad(DUMP, files={"paths.json": json.dumps(fixtures_dir)})


def rot(q, v):
    """Rotate vector v by quaternion q = (w, x, y, z)."""
    w, x, y, z = q
    vx, vy, vz = v
    tx, ty, tz = 2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx)
    return (vx + w * tx + y * tz - z * ty, vy + w * ty + z * tx - x * tz, vz + w * tz + x * ty - y * tx)


def body(t, name):
    return next(b for b in t["bodies"] if b["name"] == name)


@needs_freecad
def test_component_shapes_and_pieces(read_out):
    """`placement * shape` is the component in Assembly coordinates, and its pieces add up to it."""
    for name, d in read_out.items():
        for n, (mine, ref, vol, pieces) in d["shapes"].items():
            if ref is None:
                assert mine is None and vol == 0.0, (name, n)
                continue
            assert mine == pytest.approx(ref, abs=1e-6), (name, n)
            assert pieces == pytest.approx(vol, rel=1e-9, abs=1e-6), (name, n)   # abs: a face (shell) reads ~1e-11


@needs_freecad
def test_arm(read_out):
    d = read_out["arm"]
    t = d["tree"]
    assert [(b["name"], b["parent"]) for b in t["bodies"]] == [("Base", None), ("Arm", "Base")]
    arm = body(t, "Arm")
    pos, q = arm["frame"]
    assert pos == pytest.approx([100, 20, 10], abs=0.01)                 # the base's +x end
    assert rot(q, (0, 0, 1)) == pytest.approx((0, 1, 0), abs=1e-9)      # hinge about world +y (the arm is yawed 15 deg)
    assert d["comps"]["Arm"][1][1] == pytest.approx([math.cos(math.radians(7.5)), 0, 0, math.sin(math.radians(7.5))])
    assert arm["joint"] == "Hinge" and arm["type"] == "Revolute" and arm["limits"] == pytest.approx([-30.0, 120.0])
    (m,) = d["mounts"]
    assert m[0] == "mount_top" and m[1][0] == pytest.approx([50, 20, 20]) and m[2] is None
    assert t["loops"] == [] and t["couplings"] == [] and t["warnings"] == []
    assert d["comps"]["Base"][2] is None                                 # FreeCAD's Default material = no material


def hinge_check(d, root_comp, child_comp, corner, axis):
    t = d["tree"]
    assert [b["comps"] for b in t["bodies"]] == [[root_comp], [child_comp]]
    pos, q = t["bodies"][1]["frame"]
    assert pos == pytest.approx(corner, abs=0.01)
    z = rot(q, (0, 0, 1))
    assert abs(sum(a * b for a, b in zip(z, axis))) == pytest.approx(1, abs=1e-6)
    assert t["warnings"] == [], t["warnings"]


@needs_freecad
def test_gui_joints_frames(read_out):
    hinge_check(read_out["arm_gui"], "Base", "Arm", (100, 40, 0), (0, 1, 0))
    a = math.radians(20)
    lx, ly = 105, 45                                                     # Base corner (100,40,0) + Base offset (5,5,0)
    corner = (30 + lx * math.cos(a) - ly * math.sin(a), -20 + lx * math.sin(a) + ly * math.cos(a), 10)
    d = read_out["arm_nested"]
    hinge_check(d, "Sub", "Arm", corner, (-math.sin(a), math.cos(a), 0))
    assert d["comps"]["Sub"][1][0] == pytest.approx([30, -20, 10])
    assert d["comps"]["Sub"][3] == 1                                      # one solid piece: Base


@needs_freecad
def test_app_link_frames_are_relative_to_the_assembly(read_out):
    d = read_out["arm_link"]
    assert set(d["comps"]) == {"Base", "ArmLink"}
    hinge_check(d, "Base", "ArmLink", (100, 40, 0), (0, 1, 0))          # the Assembly is moved; frames aren't
    assert d["comps"]["Base"][1] == [[0, 0, 0], [1, 0, 0, 0]]
    assert d["comps"]["ArmLink"][3] == 1


@needs_freecad
def test_slider_rack_and_pinion(read_out):
    t = read_out["slider"]["tree"]
    assert [(b["name"], b["parent"], b["joint"]) for b in t["bodies"]] == [
        ("Rail", None, None), ("Rack", "Rail", "SlideRack"), ("Pinion", "Rail", "SpinPinion")]
    assert body(t, "Rack")["limits"] == [0.0, 500.0]
    # the pinion (center x = -20, axis +y) meets the rack at x = 0, so +1 rad moves the rack 20 mm down: -0.020 m/rad
    assert t["couplings"] == [["RackPinion", "SpinPinion", "SlideRack", pytest.approx(-20.0)]]


@needs_freecad
def test_belt(read_out):
    t = read_out["belt"]["tree"]
    assert len(t["bodies"]) == 3
    assert t["couplings"] == [["Belt", "SpinA", "SpinB", pytest.approx(0.5)]]


@needs_freecad
def test_fourbar_and_base2w(read_out):
    t = read_out["fourbar"]["tree"]
    assert len(t["bodies"]) == 4
    assert sum(1 for b in t["bodies"] if b["joint"]) == 3
    (loop,) = t["loops"]
    assert loop[:3] == ["PinCouplerRocker", "Coupler", "Rocker"]
    assert t["warnings"] == []
    t = read_out["base2w"]["tree"]
    assert [b["name"] for b in t["bodies"]] == ["Plate", "WheelL", "WheelR"]


@needs_freecad
def test_slider_crank_exports_with_the_loop_at_a_revolute(read_out):
    t = read_out["slider_crank"]["tree"]
    assert body(t, "Block")["joint"] == "SlideBlock" and body(t, "Block")["parent"] == "Frame"
    (loop,) = t["loops"]
    assert loop[0] in ("PinCrankRod", "PinRodBlock") and t["warnings"] == []


@needs_freecad
def test_limits_relative_to_the_saved_pose(read_out):
    t = read_out["limits_offset"]["tree"]
    arm, blk = body(t, "Arm"), body(t, "Block")
    assert arm["saved"] == pytest.approx(40) and arm["limits"] == pytest.approx([-70, 80])
    assert blk["saved"] == pytest.approx(100) and blk["limits"] == pytest.approx([-100, 400])
    assert t["warnings"] == []                                       # a slid slider is solved


@needs_freecad
def test_bad_fixtures_raise_naming_the_culprit(read_out):
    for name, culprit in (("bad_ungrounded", "A"), ("bad_ball", "Ball"), ("bad_slider_loop", "SlideRight")):
        d = read_out[name]
        assert "tree" not in d, name
        assert any(culprit in p for p in d["error"]), (name, d["error"])
    assert "loop" in read_out["bad_slider_loop"]["error"][0]


# ---------------------------------------------------------------- final review fixes (no FreeCAD)

def rot_y(q, deg):
    """q turned `deg` about its own z."""
    c, s_ = math.cos(math.radians(deg / 2)), math.sin(math.radians(deg / 2))
    w, x, y, z = q
    return (w * c - z * s_, x * c + y * s_, y * c - x * s_, z * c + w * s_)


def test_a_coupling_across_different_parent_bodies_is_an_error_ruling_r10():
    # sprocket on the shoulder axis (on the base), chained to a wrist on the arm
    js = [joint("Shoulder", "Revolute", "A", "Arm", q1=Y_UP), joint("Sprocket", "Revolute", "A", "S", q1=Y_UP),
          joint("Wrist", "Revolute", "Arm", "Hand", (300, 0, 0), q1=Y_UP),
          joint("Chain", "Belt", "S", "Hand", (0, 0, 0), Y_UP, (300, 0, 0), Y_UP, radii=(20.0, 20.0))]
    (p,) = problems(ex(["A", "Arm", "S", "Hand"], js))
    assert "'Chain'" in p and "'Sprocket'" in p and "'Wrist'" in p
    assert "move relative to the same part" in p and "drive 'Wrist' directly with its own motor slot" in p
    # rack and pinion too: the pinion on the base, the rack sliding on the arm
    js = [joint("Shoulder", "Revolute", "A", "Arm", q1=Y_UP), joint("SpinPinion", "Revolute", "A", "P", (-20, 0, 100), Y_UP),
          joint("SlideRack", "Slider", "Arm", "R", (10, 10, 0)),
          joint("RP", "RackPinion", "R", "P", (10, 10, 0), ID, (-20, 0, 100), Y_UP, radii=(20.0, 0.0))]
    (p,) = problems(ex(["A", "Arm", "P", "R"], js))
    assert "'RP'" in p and "same part" in p and "'SlideRack'" in p


def test_saved_angles_wrap_into_the_limits():
    # limits 0..270, saved at 200 degrees: FreeCAD's atan2 reads -160, which wraps to 200
    t = tree.build(ex("AB", [joint("H", "Revolute", "A", "B", q1=Y_UP, q2=rot_y(Y_UP, 200), limits=(0.0, 270.0))]))
    assert t.bodies[1].saved == pytest.approx(200) and t.bodies[1].limits == pytest.approx((-200.0, 70.0))
    # -170..-90 saved at 190 = -170: inside after a wrap of -360 (atan2 gives -170 already)
    t = tree.build(ex("AB", [joint("H", "Revolute", "A", "B", q1=Y_UP, q2=rot_y(Y_UP, 190), limits=(-170.0, -90.0))]))
    assert t.bodies[1].limits == pytest.approx((0.0, 80.0))
    # 400..500: a range past one turn, saved at 60 = 420
    t = tree.build(ex("AB", [joint("H", "Revolute", "A", "B", q1=Y_UP, q2=rot_y(Y_UP, 60), limits=(400.0, 500.0))]))
    assert t.bodies[1].saved == pytest.approx(420) and t.bodies[1].limits == pytest.approx((-20.0, 80.0))
    # one-sided limits are unchanged near 0
    t = tree.build(ex("AB", [joint("H", "Revolute", "A", "B", q1=Y_UP, q2=rot_y(Y_UP, 10), limits=(-30.0, None))]))
    assert t.bodies[1].limits == pytest.approx((-40.0, None))
    (p,) = problems(ex("AB", [joint("H", "Revolute", "A", "B", q1=Y_UP, q2=rot_y(Y_UP, 100), limits=(0.0, 90.0))]))
    assert p == "joint 'H' is saved outside its limits; move it inside and save"
    (p,) = problems(ex("AB", [joint("S", "Slider", "A", "B", (0, 0, 0), pos2=(0, 0, 600), limits=(0.0, 500.0))]))
    assert p == "joint 'S' is saved outside its limits; move it inside and save"
    (p,) = problems(ex("AB", [joint("H", "Revolute", "A", "B", q1=Y_UP, limits=(30.0, 30.0))]))
    assert "joint 'H'" in p and "minimum" in p and "maximum" in p


def test_a_coaxial_coupling_frame_counts_as_its_joints():
    # B has two joints (SA and BD), so the Belt's frame must tell them apart: 30 mm along SA's axis (+y) is SA's
    js = [joint("SA", "Revolute", "A", "B", q1=Y_UP), joint("SB", "Revolute", "A", "C", (150, 0, 0), q1=Y_UP),
          joint("BD", "Revolute", "B", "D", (50, 0, 0), q1=Y_UP),
          joint("Belt", "Belt", "B", "C", (0, 30, 0), Y_UP, (150, 0, 0), Y_UP, radii=(20.0, 40.0))]
    (c,) = tree.build(ex("ABCD", js)).couplings
    assert (c[1].name, c[2].name, c[3]) == ("SA", "SB", 0.5)
    js[3] = js[3]._replace(frame1_world=((0.005, 30, 0), Y_DOWN))         # 0.005 mm off the line, anti-parallel
    (c,) = tree.build(ex("ABCD", js)).couplings
    assert c[1].name == "SA" and c[3] == -0.5
    js[3] = js[3]._replace(frame1_world=((0.05, 30, 0), Y_UP))            # 0.05 mm off: neither
    (p,) = problems(ex("ABCD", js))
    assert "can't tell which" in p


def test_nothing_grounded_with_no_components():
    (p,) = problems(Extracted({}, [], [], [], [], []))
    assert "nothing is grounded" in p and not p.rstrip().endswith("of") and "no parts" in p


def test_slotted_joints_are_preferred_as_tree_joints():
    js = [joint("AB", "Revolute", "A", "B"), joint("BC", "Revolute", "B", "C", (1, 0, 0)),
          joint("CD", "Revolute", "C", "D", (2, 0, 0)), joint("DA", "Revolute", "D", "A", (3, 0, 0))]
    assert [x[0].name for x in tree.build(ex("ABCD", js)).loops] == ["CD"]      # breadth-first: CD closes the loop
    t = tree.build(ex("ABCD", js), prefer={"CD"})
    assert "CD" in [b.joint.name for b in t.bodies if b.joint is not None]
    assert len(t.loops) == 1 and t.loops[0][0].name != "CD"
    # the slider-first rule (R6) still wins: a slotted Revolute doesn't make a loop close at a Slider
    js = [joint("AB", "Revolute", "A", "B"), joint("BC", "Revolute", "B", "C", (1, 0, 0)),
          joint("CD", "Revolute", "C", "D", (2, 0, 0)), joint("Slide", "Slider", "A", "D", (2, 0, 0))]
    t = tree.build(ex("ABCD", js), prefer={"AB", "BC", "CD"})
    assert "Slide" in [b.joint.name for b in t.bodies if b.joint is not None]
    assert [x[0].name for x in t.loops] != ["Slide"] and len(t.loops) == 1


@needs_freecad
def test_new_fixtures_read(read_out):
    (p,) = read_out["bad_chain"]["error"]
    assert "'Chain'" in p and "same part" in p and "drive 'Wrist'" in p
    t = read_out["shell"]["tree"]
    assert [b["comps"] for b in t["bodies"]] == [["Base", "Skin"], ["Sheet"]]
    t = read_out["xlink"]["tree"]
    assert [b["comps"] for b in t["bodies"]] == [["Base"], ["ArmLink"]]
    assert read_out["xlink"]["comps"]["ArmLink"][3] == 1                 # the linked cylinder, from the other document
