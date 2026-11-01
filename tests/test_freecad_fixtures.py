"""The headless FreeCAD fixture assemblies: right joints, a grounded body, and every joint built satisfied
(its two frames coincide in world coordinates under: world frame = UtilsAssembly.getJcsGlobalPlc(PlacementN, ReferenceN))."""

import pytest

from freecad_harness import needs_freecad, run_in_freecad

COUPLING = {"Belt", "RackPinion", "Gears", "Screw"}
GUI = {"arm_gui", "arm_nested", "arm_link"}

EXPECTED = {
    "arm": {"Revolute": 1},
    "arm_gui": {"Revolute": 1},
    "arm_nested": {"Revolute": 1},
    "arm_link": {"Revolute": 1},
    "slider": {"Revolute": 1, "Slider": 1, "RackPinion": 1},
    "belt": {"Revolute": 2, "Belt": 1},
    "fourbar": {"Revolute": 4},
    "base2w": {"Revolute": 2},
    "bad_ungrounded": {"Revolute": 1},
    "bad_ball": {"Ball": 1},
    "slider_crank": {"Revolute": 3, "Slider": 1},
    "limits_offset": {"Revolute": 1, "Slider": 1},
    "bad_slider_loop": {"Slider": 2},
    "bad_zero_mass": {"Revolute": 1},
    "bad_motor_on_loop": {"Revolute": 4},
    "mass": {},
    "bad_chain": {"Revolute": 3, "Belt": 1},
    "shell": {"Revolute": 1, "Fixed": 1},
    "xlink": {"Revolute": 1},
}

INSPECT = r'''
import json, os, FreeCAD as App, UtilsAssembly as U
out = {}
q = lambda v: float(getattr(v, 'Value', v))
for name, path in json.loads(open(os.path.join(WORK, "paths.json")).read()).items():
    doc = App.openDocument(path)
    asm = [o for o in doc.Objects if o.TypeId == "Assembly::AssemblyObject"][0]
    joints, grounded = [], []
    for o in doc.Objects:
        if hasattr(o, "ObjectToGround"):
            grounded.append(o.ObjectToGround.Name)
        elif hasattr(o, "JointType"):
            c1, c2 = o.Reference1[0], o.Reference2[0]
            f1 = U.getJcsGlobalPlc(o.Placement1, o.Reference1)       # global placement of the component * PlacementN
            f2 = U.getJcsGlobalPlc(o.Placement2, o.Reference2)
            assert U.getObject(o.Reference1).Name == c1.Name or o.Reference1[0].TypeId == "App::Part", o.Name
            z1, z2 = f1.Rotation.multVec(App.Vector(0, 0, 1)), f2.Rotation.multVec(App.Vector(0, 0, 1))
            joints.append(dict(name=o.Name, type=o.JointType, c1=c1.Name, c2=c2.Name, detach=[o.Detach1, o.Detach2],
                               comp1=U.getObject(o.Reference1).Name, comp2=U.getObject(o.Reference2).Name,
                               subs=[o.Reference1[1], o.Reference2[1]],
                               plc_identity=[o.Placement1.isIdentity(), o.Placement2.isIdentity()],
                               ref_obj=[c1.TypeId, c2.TypeId],
                               p1=list(f1.Base), p2=list(f2.Base), z1=list(z1), z2=list(z2),
                               rot_same=f1.Rotation.isSame(f2.Rotation, 1e-9),
                               limits=[o.EnableAngleMin, q(o.AngleMin), o.EnableAngleMax, q(o.AngleMax),
                                       o.EnableLengthMin, q(o.LengthMin), o.EnableLengthMax, q(o.LengthMax)],
                               dist=[q(o.Distance), q(o.Distance2)]))
    before = {o.Name: o.Placement for o in doc.Objects if o.TypeId.startswith("Part::") and hasattr(o, "Placement")}
    asm.solve()
    moved = [k for k, p in before.items() if not p.isSame(doc.getObject(k).Placement, 1e-6)]
    vol = {o.Name: (o.Shape.Volume if hasattr(o, "Shape") and not o.Shape.isNull() else 0.0)
           for o in doc.Objects if o.TypeId.startswith("Part::") and o.Name in {j["c2"] for j in joints} | set(grounded)}
    out[name] = dict(joints=joints, grounded=grounded, moved=moved, vol=vol,
                     lcs=[o.Label for o in doc.Objects if o.TypeId == "Part::LocalCoordinateSystem"])
    App.closeDocument(doc.Name)
print("SCRIMMAGE_JSON:" + json.dumps(out))
'''


@pytest.fixture(scope="module")
def info(fixtures_dir):
    import json
    return run_in_freecad(INSPECT, files={"paths.json": json.dumps(fixtures_dir)})


@needs_freecad
def test_fixtures_build(fixtures_dir, info):
    import os
    assert set(fixtures_dir) == set(EXPECTED)
    for name, path in fixtures_dir.items():
        assert os.path.isfile(path), name
        types = {}
        for j in info[name]["joints"]:
            types[j["type"]] = types.get(j["type"], 0) + 1
        assert types == EXPECTED[name], name
        if name == "bad_ungrounded":
            assert info[name]["grounded"] == []
        else:
            assert len(info[name]["grounded"]) == 1, name


@needs_freecad
def test_joint_frames_coincide_in_world(info):
    for name, d in info.items():
        for j in d["joints"]:
            if j["type"] in COUPLING:
                continue              # a coupling reuses its two parts' own joint frames (checked below)
            if name == "limits_offset":
                continue              # moved along its joints on purpose (checked in test_fixture_specifics)
            assert all(abs(a - b) < 1e-6 for a, b in zip(j["p1"], j["p2"])), (name, j)
            dot = sum(a * b for a, b in zip(j["z1"], j["z2"]))
            if name in GUI:
                assert abs(dot) == pytest.approx(1, abs=1e-6), (name, j)    # mated faces: axes may be anti-parallel
                assert j["detach"] == [False, False]
                assert j["plc_identity"] == [False, False]                  # FreeCAD computed them from the sub-elements
            else:
                assert dot == pytest.approx(1, abs=1e-6), (name, j)
                assert j["rot_same"], (name, j)
                assert j["detach"] == [True, True]


@needs_freecad
def test_coupling_frames_are_the_parts_own_joint_frames(info):
    """RackPinion/Belt are made as in the GUI: each end sits on the same frame as that part's Revolute/Slider."""
    for name in ("slider", "belt"):
        joints = info[name]["joints"]
        for c in (j for j in joints if j["type"] in COUPLING):
            for comp, pos, z in ((c["comp1"], c["p1"], c["z1"]), (c["comp2"], c["p2"], c["z2"])):
                own = [j for j in joints if j["type"] not in COUPLING and comp in (j["comp1"], j["comp2"])]
                assert len(own) == 1, (name, comp)
                o = own[0]
                p, oz = (o["p1"], o["z1"]) if o["comp1"] == comp else (o["p2"], o["z2"])
                assert pos == pytest.approx(p, abs=1e-6) and z == pytest.approx(oz, abs=1e-6), (name, comp)


@needs_freecad
def test_gui_references_resolve_to_components(info):
    g = info["arm_gui"]["joints"][0]
    assert (g["comp1"], g["comp2"]) == ("Base", "Arm")
    assert g["subs"] == [["Face4", "Vertex8"], ["Edge1", "Edge1"]]
    n = info["arm_nested"]["joints"][0]
    assert n["ref_obj"][0] == "App::Part" and n["subs"][0] == ["Base.Face4", "Base.Vertex8"]
    assert (n["comp1"], n["comp2"]) == ("Base", "Arm")                       # UtilsAssembly.getObject descends into the Part


@needs_freecad
def test_solver_moves_nothing(info):
    for name, d in info.items():
        assert d["moved"] == [], name


@needs_freecad
def test_fixture_specifics(info):
    arm = info["arm"]
    assert arm["lcs"] == ["mount_top"]
    h = arm["joints"][0]
    assert h["limits"][:4] == [True, -30.0, True, 120.0]
    assert h["z1"] == pytest.approx([0, 1, 0])                      # hinge about +y
    assert h["p1"] == pytest.approx([100, 20, 10])
    sl = [j for j in info["slider"]["joints"] if j["type"] == "Slider"][0]
    assert sl["limits"][4:] == [True, 0.0, True, 500.0]
    assert sl["z1"] == pytest.approx([0, 0, 1])
    rp = [j for j in info["slider"]["joints"] if j["type"] == "RackPinion"][0]
    assert rp["dist"][0] == 20.0
    belt = [j for j in info["belt"]["joints"] if j["type"] == "Belt"][0]
    assert belt["dist"] == [20.0, 40.0]
    revs = [j for j in info["belt"]["joints"] if j["type"] == "Revolute"]
    assert revs[0]["z1"] == pytest.approx(revs[1]["z1"]) and revs[0]["z1"] == pytest.approx([0, 1, 0])   # parallel axes
    assert revs[0]["p1"] != pytest.approx(revs[1]["p1"])
    pins = {j["name"]: j["p1"] for j in info["fourbar"]["joints"]}
    import math
    d = lambda a, b: math.dist(pins[a], pins[b])
    assert d("PinGroundCrank", "PinRockerGround") == pytest.approx(100)
    assert d("PinGroundCrank", "PinCrankCoupler") == pytest.approx(40)
    assert d("PinCrankCoupler", "PinCouplerRocker") == pytest.approx(120)
    assert d("PinCouplerRocker", "PinRockerGround") == pytest.approx(80)
    ys = sorted(j["p1"][1] for j in info["base2w"]["joints"])
    assert ys == pytest.approx([-250, 250])
    assert info["bad_zero_mass"]["vol"]["Ghost"] == 0.0
    lo = {j["name"]: j for j in info["limits_offset"]["joints"]}
    assert lo["Hinge"]["p1"] == pytest.approx(lo["Hinge"]["p2"]) and lo["Hinge"]["z1"] == pytest.approx(lo["Hinge"]["z2"])
    assert [b - a for a, b in zip(lo["Slide"]["p1"], lo["Slide"]["p2"])] == pytest.approx([0, 0, 100])
    assert lo["Hinge"]["limits"][:4] == [True, -30.0, True, 120.0] and lo["Slide"]["limits"][4:] == [True, 0.0, True, 500.0]
