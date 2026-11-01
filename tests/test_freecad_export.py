"""Exporter, Task 6: saved settings (settings.py), the export (export.py) and `scrimmage freecad export`.

Settings are written inside FreeCAD into copies of the session fixtures (tests/freecad/make_fixtures.py), so the
fixtures stay pristine. Each copy is exported, loaded with `load_part`, assembled, and driven in scrimmage's
physics: the arm's holding voltage, the four-bar's closed-form rocker angle, the rack and pinion's sign, the belt
ratio, and a two-wheel base resting on its wheel cylinders."""

import json
import math
import os
import shutil
import uuid
from pathlib import Path

import mujoco
import numpy as np
import pytest

from freecad_harness import needs_freecad, run_in_freecad
from scrimmage import freecad_tools
from scrimmage.__main__ import main
from scrimmage.motors import get_motor
from scrimmage.parts.assemble import assemble
from scrimmage.parts.model import load_part
from scrimmage.protocol import HalState
from scrimmage.world import World

pytestmark = needs_freecad

RHO_AL = 2700.0
BASE = {"name": "base", "stock": "chassis", "mass": 200.0}      # heavy, so mechanisms don't rock it
TEST_WORLD = {"battery": {"nominal_voltage": 12.0, "resistance": 0.0}, "field": {"obstacle": False}}

MOTOR = dict(count=1, invert=False, efficiency=1.0)
ARM_SETTINGS = dict(
    motors=[dict(slot="pivot", joint="Hinge", motor="KrakenX60", gear_ratio=50.0, **MOTOR)],
    encoders=[dict(slot="encoder", joint="Hinge", on="joint", invert=False),
              dict(slot="motor_turns", joint="Hinge", on="motor", invert=False)],
    mounts=[dict(name="tip", comp="Arm", frame_local=[[300.0, 10.0, 10.0], [1.0, 0.0, 0.0, 0.0]])],
    mass_overrides={"Arm": 3.0})

# (key, fixture, settings, mutation run in FreeCAD on the copy before saving, export into an existing dir?)
CASES = [
    ("arm", "arm", ARM_SETTINGS, None),
    ("arm_stale", "arm", ARM_SETTINGS, "doc.removeObject('Hinge')"),
    ("arm_dup", "arm", dict(ARM_SETTINGS, gyros=[dict(slot="pivot", body_comp="Base")]), None),
    ("arm_badmass", "arm", dict(ARM_SETTINGS, mass_overrides={"Arm": -1.0}), None),
    ("arm_wood", "arm", {}, "doc.getObject('Arm').ShapeMaterial = material('Wood')"),
    ("fourbar", "fourbar", dict(motors=[dict(slot="crank", joint="PinGroundCrank", motor="KrakenX60",
                                             gear_ratio=50.0, **MOTOR)]), None),
    ("bad_motor_on_loop", "bad_motor_on_loop",       # a slot on every joint of the loop: one of them must close it
     dict(motors=[dict(slot=f"m{i}", joint=j, motor="KrakenX60", gear_ratio=50.0, **MOTOR) for i, j in enumerate(
         ("PinGroundCrank", "PinCrankCoupler", "PinCouplerRocker", "PinRockerGround"))]), None),
    ("fourbar_slot_on_loop", "fourbar",              # breadth-first would close the loop at PinCouplerRocker
     dict(motors=[dict(slot="crank", joint="PinCouplerRocker", motor="KrakenX60", gear_ratio=50.0, **MOTOR)]), None),
    ("bad_chain", "bad_chain", {}, None),
    ("shell", "shell", dict(mass_overrides={"Sheet": 0.5}),
     "import Part; o = doc.getObject('Assembly').newObject('Part::Feature', 'Inside'); b = Part.makeBox(10, 10, 10); "
     "b.reverse(); o.Shape = b"),
    ("slider", "slider", dict(motors=[dict(slot="lift", joint="SlideRack", motor="KrakenX60", gear_ratio=10.0,
                                           radius_mm=20.0, **MOTOR)],
                              encoders=[dict(slot="spool", joint="SlideRack", on="motor", invert=False)]), None),
    ("belt", "belt", dict(motors=[dict(slot="spin", joint="SpinA", motor="KrakenX60", gear_ratio=10.0, **MOTOR)]),
     None),
    ("base2w", "base2w", dict(contact={"WheelL": "wheel", "WheelR": "wheel"}, wheel_friction={"WheelR": 0.9, "Gone": 1.0},
                              gyros=[dict(slot="gyro", body_comp="Plate")]), None),
    ("base2w_side", "base2w", dict(contact={"WheelL": "wheel", "WheelR": "wheel"}),
     "move_axle(doc, 'AxleWheelL', 'WheelL', V(0, 270, 0)); move_axle(doc, 'AxleWheelR', 'WheelR', V(0, -250, 5))"),
    ("bad_zero_mass", "bad_zero_mass", {}, None),
    ("bad_root_wheel", "base2w", dict(contact={"Plate": "wheel"}), None),
    ("arm_named", "arm", ARM_SETTINGS, "doc.getObject('Arm').Label = 'Long Arm'; doc.getObject('Hinge').Label = 'Main Pivot'"),
    ("arm_onesided", "arm", {}, "doc.getObject('Hinge').EnableAngleMax = False"),
    ("arm_suppressed", "arm", ARM_SETTINGS, "doc.getObject('Hinge').Suppressed = True"),
    ("arm_badnames", "arm", dict(motors=[dict(slot="pivot", joint="Hinge", motor="Kraken9000", gear_ratio=50.0, **MOTOR)],
                                 encoders=[dict(slot="bad name", joint="Hinge", on="joint", invert=False)],
                                 mounts=[dict(name="my-mount", comp="Arm", frame_local=[[0, 0, 0], [1, 0, 0, 0]])],
                                 wheel_friction={"Arm": -1.0}), None),
    ("arm_two_motors", "arm", dict(motors=[dict(slot="left", joint="Hinge", motor="KrakenX60", gear_ratio=50.0, **MOTOR),
                                           dict(slot="right", joint="Hinge", motor="KrakenX60", gear_ratio=60.0,
                                                **MOTOR)]), None),
    ("arm_collide", "arm", dict(contact={"Arm": "collide"}, mass_overrides={"Arm": 1.0}),
     "doc.getObject('Arm').ShapeMaterial = material('Wood')"),
]

SCRIPT = r'''
import json, os, shutil
import FreeCAD as App
from FreeCAD import Vector as V, Placement as P, Rotation as R
from scrimmage_part import export, settings
from scrimmage_part.errors import ExportError
cfg = json.loads(open(os.path.join(WORK, "cfg.json")).read())
paths, root = cfg["paths"], cfg["root"]
Z_TO_Y = R(V(0, 0, 1), V(0, 1, 0))
out = {}

def material(name):
    import Materials
    mm = Materials.MaterialManager()
    for uuid, m in mm.Materials.items():
        if m.Name == name:
            return mm.getMaterial(uuid)
    raise ValueError(name)

def move_axle(doc, joint, wheel, at):
    """Put both ends of a (detached) wheel joint at `at`, axis +y."""
    j, w, plate = doc.getObject(joint), doc.getObject(wheel), doc.getObject("Plate")
    wf = P(at, Z_TO_Y)
    j.Placement1 = plate.Placement.inverse() * wf
    j.Placement2 = w.Placement.inverse() * wf

def copy_with_settings(key, fixture, st, mutate):
    doc = App.openDocument(paths[fixture])
    if mutate:
        exec(mutate, dict(globals(), doc=doc))
    settings.save(doc, settings.ScrimmageSettings(**st))
    p = os.path.join(root, key + ".FCStd")
    doc.saveAs(p)
    App.closeDocument(doc.Name)
    doc = App.openDocument(p)                       # the settings survive a save and reopen
    back = settings.get(doc)
    App.closeDocument(doc.Name)
    return p, back.to_json() == settings.ScrimmageSettings(**st).to_json()

def run(key, src, out_dir):
    try:
        return dict(summary=export.export(src, out_dir))
    except ExportError as e:
        return dict(problems=e.problems)

for key, fixture, st, mutate in cfg["cases"]:
    p, same = copy_with_settings(key, fixture, st, mutate)
    o = os.path.join(root, "out", key)
    r = run(key, p, o)
    r["fcstd"], r["settings_round_trip"], r["out_dir"] = p, same, o
    out[key] = r

# re-export over an existing part folder replaces it; a folder that isn't a part is left alone
o = os.path.join(root, "out", "arm")
open(os.path.join(o, "stale.txt"), "w").write("x")
out["arm_again"] = run("arm_again", out["arm"]["fcstd"], o)
out["arm_again"]["stale_left"] = os.path.exists(os.path.join(o, "stale.txt"))
keep = os.path.join(root, "out", "not_a_part")
os.makedirs(keep)
open(os.path.join(keep, "keep.txt"), "w").write("mine")
out["not_a_part"] = run("not_a_part", out["arm"]["fcstd"], keep)
out["not_a_part"]["out_dir"] = keep

# a document with no settings at all: get() is None, get(create=True) makes an empty one
doc = App.openDocument(paths["arm"])
out["no_settings"] = settings.get(doc) is None
out["created"] = settings.get(doc, create=True).to_json() == settings.ScrimmageSettings().to_json()
out["hidden"] = doc.getObject("ScrimmagePart").Visibility is False
App.closeDocument(doc.Name)
out["listing"] = sorted(os.listdir(os.path.join(root, "out")))
print("SCRIMMAGE_JSON:" + json.dumps(out))
'''


@pytest.fixture(scope="module")
def ex(fixtures_dir):
    """Every case exported in one FreeCAD run; the output lives under ~/snap/freecad/common (removed afterwards)."""
    root = freecad_tools._work_root(freecad_tools.find_freecad()).parent / f"scrimmage-export-{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True)
    try:
        cfg = {"paths": fixtures_dir, "root": str(root), "cases": [list(c) for c in CASES]}
        out = run_in_freecad(SCRIPT, files={"cfg.json": json.dumps(cfg)}, timeout=600)
        out["root"] = root
        yield out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def ok(ex, key):
    r = ex[key]
    assert "summary" in r, r.get("problems")
    return r["summary"]


def bad(ex, key):
    r = ex[key]
    assert "problems" in r, r.get("summary")
    assert not os.path.exists(r["out_dir"]), "nothing is written on error"
    return r["problems"]


def part(ex, key):
    return load_part(Path(ex[key]["out_dir"]))


def body(p, name):
    return next(b for b in p.bodies if b.name == name)


def world(*parts, start=(2.0, 4.0)):
    return World({"part": list(parts), "robot": {"start_pose": [*start, 0.0]}, **TEST_WORLD}, Path("."))


def drive(w, seconds, until=None, **pwm):
    hal = HalState()
    for port, speed in pwm.items():
        hal.devices[("PWM", port.removeprefix("p"))] = {"<init": True, "<speed": speed}
    for _ in range(int(seconds / w.model.opt.timestep)):
        w.step(hal)
        if until is not None and until(w):
            return True
    return False


def on_chassis(ex, key, ids, name="x", **kw):
    return {"name": name, "path": ex[key]["out_dir"], "attach": "base.top", "ids": ids, **kw}


def jq(w, joint):
    return float(w.data.qpos[w.model.jnt_qposadr[w.model.joint(joint).id]])


# ---------------------------------------------------------------- settings and the arm


def test_settings_round_trip_and_are_hidden(ex):
    assert all(ex[k]["settings_round_trip"] for k, *_ in CASES)
    assert ex["no_settings"] and ex["created"] and ex["hidden"]


def test_arm_exports_slots_mount_range_inertia_and_relative_mesh(ex):
    s = ok(ex, "arm")
    assert s["bodies"] == ["Base", "Arm"] and s["joints"] == ["Hinge"]
    assert s["slots"] == ["pivot", "encoder", "motor_turns"] and s["mounts"] == ["top", "tip"]
    p = part(ex, "arm")
    arm = body(p, "Arm")
    assert arm.joint.kind == "hinge" and arm.joint.axis == (0.0, 0.0, 1.0)
    assert arm.joint.range == pytest.approx((-30.0, 120.0))
    assert arm.mass == pytest.approx(3.0)                                   # the override
    base = body(p, "Base")
    assert base.mass == pytest.approx(100 * 40 * 20 * 1e-9 * RHO_AL)        # aluminum, no material
    assert s["mass_kg"] == pytest.approx(3.0 + base.mass)
    assert arm.inertia_rot is not None and base.inertia_rot is not None
    for b in (arm, base):
        (g,) = [g for g in b.geoms if g.shape == "mesh"]
        assert g.size == f"meshes/{b.name}.stl" and not g.collide
        assert (Path(ex["arm"]["out_dir"]) / g.size).is_file()
    # arm frame: x along world x, y along world -z, z (the hinge) along world +y; the arm is yawed 15 deg
    c15, s15 = math.cos(math.radians(15)), math.sin(math.radians(15))
    assert arm.com == pytest.approx((0.140 * c15, 0.0, 0.140 * s15), abs=1e-6)
    assert arm.pos == pytest.approx((0.1, 0.02, 0.01)) and arm.rot == pytest.approx((-90.0, 0.0, 0.0), abs=1e-9)
    (m,) = p.motors
    assert (m.slot, m.joint, m.motor, m.gearing) == ("pivot", "Hinge", "KrakenX60", 50.0)
    enc = {e.slot: e for e in p.encoders}
    assert enc["encoder"].ratio == 1.0 and enc["motor_turns"].ratio == 50.0
    mounts = {m.name: m for m in p.mounts}
    assert mounts["top"].body == "Base" and mounts["top"].pos == pytest.approx((0.05, 0.02, 0.02))
    assert mounts["tip"].body == "Arm" and mounts["tip"].pos == pytest.approx((0.29 * c15, 0.0, 0.29 * s15), abs=1e-9)
    built = assemble({"part": [BASE, on_chassis(ex, "arm", {"pivot": "PWM:0", "encoder": "DIO:0"}, name="arm")]},
                     Path("."))
    assert {b["name"] for b in built.bindings["motor"]} == {"arm.pivot"}
    assert built.model.joint("arm.Hinge").range == pytest.approx(np.radians([-30.0, 120.0]))


def arm_after(ex, factor, seconds=1.0):
    """Drive the exported arm at `factor` x its gravity-holding voltage; return (degrees moved, final speed, the
    sign gravity turns it)."""
    p = part(ex, "arm")
    arm = body(p, "Arm")
    w = world(BASE, on_chassis(ex, "arm", {"pivot": "PWM:0"}, name="arm"))
    mujoco.mj_forward(w.model, w.data)
    bid, jid = w.model.body("arm.Arm").id, w.model.joint("arm.Hinge").id
    assert w.model.body_mass[bid] == pytest.approx(arm.mass)
    r = w.data.xipos[bid] - w.data.xanchor[jid]
    gravity = float(np.dot(w.data.xaxis[jid], np.cross(r, [0.0, 0.0, -9.81 * arm.mass])))
    # the lever is the arm's COM (exported: 140 mm along the arm, yawed 15 deg) off the horizontal +y hinge
    assert abs(gravity) == pytest.approx(3.0 * 9.81 * 0.140 * math.cos(math.radians(15)), rel=1e-3)
    m = get_motor("KrakenX60")
    volts = -gravity / 50.0 / m.Kt * m.R
    drive(w, seconds, p0=factor * volts / w.vbatt)
    q, qd = w.joint_states()["arm.Hinge"][:2]
    return math.degrees(q), qd, math.copysign(1.0, gravity)


def test_exported_arm_holding_voltage_boundary(ex):
    q, _, g = arm_after(ex, 0.9)
    assert q * g > 0.2                       # 10% under the holding voltage: gravity wins
    q, _, g = arm_after(ex, 1.1)
    assert q * g < -0.2                      # 10% over: the motor wins
    assert abs(arm_after(ex, 1.0)[1]) < 1e-3


def test_stale_settings_warn_and_are_dropped(ex):
    s = ok(ex, "arm_stale")
    w = "\n".join(s["warnings"])
    for slot in ("pivot", "encoder", "motor_turns"):
        assert f"slot '{slot}' was on joint 'Hinge', which no longer exists" in w
    assert s["slots"] == [] and s["mounts"] == ["top", "tip"]
    p = part(ex, "arm_stale")
    assert [b.name for b in p.bodies] == ["Base"] and not p.motors and not p.encoders


def test_duplicate_slots_bad_override_zero_mass_and_a_grounded_wheel_are_errors(ex):
    assert any("slot name 'pivot' is used twice" in p for p in bad(ex, "arm_dup"))
    assert any("mass override of Arm must be" in p and "greater than 0" in p for p in bad(ex, "arm_badmass"))
    (p,) = bad(ex, "bad_zero_mass")
    assert p.startswith("body 'Ghost': ") and "mass override" in p
    (p,) = bad(ex, "bad_root_wheel")
    assert "body 'Plate' is marked as a wheel" in p and "the grounded body" in p


def test_material_without_density_warns(ex):
    s = ok(ex, "arm_wood")
    assert ("material 'Wood' on Arm has no density; used 2700 kg/m³ — assign a material with a density or set a "
            "mass override") in s["warnings"]
    assert body(part(ex, "arm_wood"), "Arm").mass == pytest.approx(300 * 20 * 20 * 1e-9 * RHO_AL)


def test_reexport_replaces_a_part_folder_but_never_another_folder(ex):
    assert "summary" in ex["arm_again"] and not ex["arm_again"]["stale_left"]
    (p,) = ex["not_a_part"]["problems"]
    assert "isn't a part folder" in p
    keep = Path(ex["not_a_part"]["out_dir"])
    assert sorted(os.listdir(keep)) == ["keep.txt"] and (keep / "keep.txt").read_text() == "mine"
    assert not [n for n in ex["listing"] if n.startswith(".")], "no temporary folders left behind"


# ---------------------------------------------------------------- mechanisms in physics


def test_motor_on_a_loop_joint_names_the_tree_joint(ex):
    (p,) = bad(ex, "bad_motor_on_loop")
    assert p == ("motor slot 'm3' is on joint 'PinRockerGround', which closes a loop; put it on tree joint "
                 "'PinCouplerRocker'")


def fourbar_rocker(theta_deg):
    """Closed form, as the fixture builds it: ground pins at x=0 and x=100 (mm), crank 40, coupler 120, rocker 80;
    angles in the xz plane from +x. Returns the rocker angle (degrees), taking the upper branch."""
    a, b, c, d = 100.0, 40.0, 120.0, 80.0
    th = math.radians(theta_deg)
    pa = (b * math.cos(th), b * math.sin(th))
    dx, dz = a - pa[0], -pa[1]
    dist = math.hypot(dx, dz)
    x = (c * c - d * d + dist * dist) / (2 * dist)
    h = math.sqrt(max(c * c - x * x, 0.0))
    mx, mz = pa[0] + x * dx / dist, pa[1] + x * dz / dist
    pts = [(mx + h * dz / dist, mz - h * dx / dist), (mx - h * dz / dist, mz + h * dx / dist)]
    pb = max(pts, key=lambda p: p[1])
    return math.degrees(math.atan2(pb[1], pb[0] - a))


def test_fourbar_rocker_follows_the_closed_form(ex):
    s = ok(ex, "fourbar")
    assert s["loops"] == 1 and "PinCouplerRocker" not in s["joints"]
    p = part(ex, "fourbar")
    (lp,) = p.loops
    assert {lp.body1, lp.body2} == {"Coupler", "Rocker"}
    w = world(BASE, on_chassis(ex, "fourbar", {"crank": "PWM:0"}, name="fb", pos=[0, 0, 0.1]))
    assert drive(w, 3.0, until=lambda w: jq(w, "fb.PinGroundCrank") >= math.radians(20), p0=0.1)
    crank = math.degrees(jq(w, "fb.PinGroundCrank"))
    rocker = math.degrees(jq(w, "fb.PinRockerGround"))
    # both joints turn about world +y, which takes +x toward -z: a joint's +q lowers the xz-plane angle
    expected = -(fourbar_rocker(60.0 - crank) - fourbar_rocker(60.0))
    assert abs(expected) > 5
    assert rocker == pytest.approx(expected, abs=1.0)


def test_rack_and_pinion_sign_and_slider_gearing(ex):
    s = ok(ex, "slider")
    p = part(ex, "slider")
    (cp,) = p.couplings
    assert cp.joints == ("SpinPinion", "SlideRack") and cp.ratio == pytest.approx(-0.020)
    (m,) = p.motors
    assert m.gearing == pytest.approx(500.0)                               # 10 / 0.020 m
    (e,) = p.encoders
    assert e.ratio == pytest.approx(10 / (2 * math.pi * 0.020))            # motor turns per meter
    assert s["couplings"] == 1
    w = world(BASE, on_chassis(ex, "slider", {"lift": "PWM:0"}, name="sl"))
    mujoco.mj_forward(w.model, w.data)
    rack, pinion = w.model.joint("sl.SlideRack").id, w.model.joint("sl.SpinPinion").id
    assert w.data.xaxis[rack] == pytest.approx([0, 0, 1], abs=1e-9)       # the CAD rack's +z
    assert w.data.xaxis[pinion] == pytest.approx([0, 1, 0], abs=1e-9)     # the CAD pinion's +y
    rack_b, rail_b = w.model.body("sl.Rack").id, w.model.body("sl.Rail").id

    def height():               # the rack along the rail's (the CAD's) +z: the robot settles and tilts a little
        rail = w.data.xmat[rail_b].reshape(3, 3)
        return float((rail.T @ (w.data.xpos[rack_b] - w.data.xpos[rail_b]))[2])

    z0 = height()
    drive(w, 0.1, p0=0.3)
    mujoco.mj_forward(w.model, w.data)                                     # positions of the final qpos
    q_rack, q_pinion = jq(w, "sl.SlideRack"), jq(w, "sl.SpinPinion")
    assert q_rack > 0.01                                                   # the motor lifts the rack
    assert height() - z0 == pytest.approx(q_rack, rel=1e-3)
    # the pinion's teeth at +x of its +y axle move along -z as it turns +: +1 rad lowers the rack 20 mm (measured
    # once moving: the soft coupling lags a little in the first milliseconds)
    drive(w, 0.05, p0=0.3)
    d_rack, d_pinion = jq(w, "sl.SlideRack") - q_rack, jq(w, "sl.SpinPinion") - q_pinion
    assert d_pinion < -0.5 and d_rack / d_pinion == pytest.approx(-0.020, rel=0.01)


def test_belt_ratio_in_physics(ex):
    p = part(ex, "belt")
    (cp,) = p.couplings
    assert cp.joints == ("SpinA", "SpinB") and cp.ratio == pytest.approx(0.5)
    w = world(BASE, on_chassis(ex, "belt", {"spin": "PWM:0"}, name="bt", pos=[0, 0, 0.2]))
    drive(w, 0.1, p0=0.2)
    a, b = jq(w, "bt.SpinA"), jq(w, "bt.SpinB")
    drive(w, 0.1, p0=0.2)
    da, db = jq(w, "bt.SpinA") - a, jq(w, "bt.SpinB") - b
    assert da > 0.5 and db / da == pytest.approx(0.5, rel=0.01)


def settle_contacts(w, steps=1000):
    hal = HalState()
    for _ in range(steps):
        w.step(hal)
    m, d = w.model, w.data
    carpet = m.geom("carpet").id
    return {mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, int(m.geom_bodyid[c.geom1 if c.geom2 == carpet else c.geom2]))
            for c in d.contact[:d.ncon] if carpet in (c.geom1, c.geom2)}


def test_two_wheel_base_rests_on_its_wheel_cylinders(ex):
    s = ok(ex, "base2w")
    assert s["slots"] == ["gyro"]
    p = part(ex, "base2w")
    assert p.gyros[0].body == "Plate"
    for name, friction in (("WheelL", 1.2), ("WheelR", 0.9)):
        b = body(p, name)
        (mesh,) = [g for g in b.geoms if g.shape == "mesh"]
        (cyl,) = [g for g in b.geoms if g.shape == "cylinder"]
        assert not mesh.collide and cyl.collide and cyl.axis == "z"
        assert cyl.size == pytest.approx((0.0508, 0.040), abs=1e-6) and cyl.pos == pytest.approx((0, 0, 0), abs=1e-9)
        assert cyl.friction == friction and cyl.condim == 4
    assert not body(p, "Plate").geoms[0].collide
    # two of them, front and back, under a chassis: the robot sits on the four wheel cylinders
    parts = [BASE] + [{"name": n, "path": ex["base2w"]["out_dir"], "attach": "base.center", "pos": [x, 0, 0.0508]}
                      for n, x in (("front", 0.2), ("back", -0.2))]
    w = World({"part": parts, "robot": {"start_pose": [2.0, 4.0, 0.0]}, **TEST_WORLD}, Path("."))
    z0 = w.data.xpos[w.robot_body][2]
    assert settle_contacts(w) == {f"{n}.{wh}" for n in ("front", "back") for wh in ("WheelL", "WheelR")}
    assert abs(w.data.xpos[w.robot_body][2] - z0) < 0.005


def test_wheel_cylinder_follows_a_side_face_joint_and_off_axis_wheels_warn(ex):
    s = ok(ex, "base2w_side")
    p = part(ex, "base2w_side")
    (cyl,) = [g for g in body(p, "WheelL").geoms if g.shape == "cylinder"]
    assert cyl.pos == pytest.approx((0, 0, -0.020), abs=1e-6)              # the frame is on the +y side face
    assert cyl.size == pytest.approx((0.0508, 0.040), abs=1e-6)
    (w,) = [w for w in s["warnings"] if w.startswith("wheel ")]
    assert "wheel 'WheelR'" in w and "5.0 mm off" in w and "centered on the joint axis" in w


# ---------------------------------------------------------------- the command


def test_cli_export_prints_the_summary(ex, tmp_path, capsys):
    out = tmp_path / "arm_part"
    main(["freecad", "export", ex["arm"]["fcstd"], str(out)])
    text = capsys.readouterr().out
    assert f"to {out}" in text and "slots: pivot, encoder, motor_turns" in text and "mounts: top, tip" in text
    p = load_part(out)
    assert [b.name for b in p.bodies] == ["Base", "Arm"] and (out / "meshes" / "Arm.stl").is_file()
    assert not [n for n in os.listdir(tmp_path) if n.startswith(".")]


def test_cli_export_problems_exit_2_and_write_nothing(ex, tmp_path, capsys):
    out = tmp_path / "loop_part"
    with pytest.raises(SystemExit) as e:
        main(["freecad", "export", ex["bad_motor_on_loop"]["fcstd"], str(out)])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "can't export" in err and "closes a loop" in err
    assert os.listdir(tmp_path) == []
    other = tmp_path / "mine"
    other.mkdir()
    (other / "notes.txt").write_text("keep")
    with pytest.raises(SystemExit) as e:
        main(["freecad", "export", ex["arm"]["fcstd"], str(other)])
    assert e.value.code == 2 and "isn't a part folder" in capsys.readouterr().err
    assert os.listdir(other) == ["notes.txt"]


def test_wheel_friction_of_a_removed_component_warns(ex):
    assert "wheel friction 1.0 was on component 'Gone', which no longer exists; dropped" in ok(ex, "base2w")["warnings"]


def test_settings_are_keyed_by_internal_names_not_labels(ex):
    s = ok(ex, "arm_named")
    assert s["bodies"] == ["Base", "Long_Arm"] and s["joints"] == ["Main_Pivot"]
    p = part(ex, "arm_named")
    assert body(p, "Long_Arm").mass == pytest.approx(3.0)                  # the override keyed "Arm"
    assert {m.joint for m in p.motors} | {e.joint for e in p.encoders} == {"Main_Pivot"}
    assert {m.name: m.body for m in p.mounts}["tip"] == "Long_Arm"


def test_one_sided_limit_is_open_on_the_other_side(ex):
    assert body(part(ex, "arm_onesided"), "Arm").joint.range == (-30.0, 1e6)


def test_slots_on_a_suppressed_joint_warn_and_are_dropped(ex):
    s = ok(ex, "arm_suppressed")
    w = "\n".join(s["warnings"])
    for slot in ("pivot", "encoder", "motor_turns"):
        assert f"slot '{slot}' is on joint 'Hinge', which is suppressed; dropped" in w
    assert s["slots"] == []


def test_unknown_motor_bad_names_and_bad_friction_are_errors(ex):
    p = "\n".join(bad(ex, "arm_badnames"))
    assert "motor slot 'pivot': unknown motor 'Kraken9000'" in p
    assert "encoder slot name 'bad name' must be letters, digits and _" in p
    assert "mount name 'my-mount' must be letters, digits and _" in p
    assert "wheel friction of Arm must be a number greater than 0, not -1.0" in p


def test_two_motor_slots_on_one_joint_need_the_same_gearing(ex):
    (p,) = bad(ex, "arm_two_motors")
    assert "motor slots 'left' and 'right' both drive joint 'Hinge' with different gearing" in p


def test_collide_mark_and_no_density_warning_skipped_under_an_override(ex):
    s = ok(ex, "arm_collide")
    assert not [w for w in s["warnings"] if "has no density" in w]
    p = part(ex, "arm_collide")
    (g,) = body(p, "Arm").geoms
    assert g.shape == "mesh" and g.collide
    assert not body(p, "Base").geoms[0].collide


# ---------------------------------------------------------------- final review fixes


def test_a_slot_on_the_joint_breadth_first_would_loop_becomes_a_tree_joint(ex):
    s = ok(ex, "fourbar_slot_on_loop")
    assert "PinCouplerRocker" in s["joints"] and s["loops"] == 1
    p = part(ex, "fourbar_slot_on_loop")
    assert [m.joint for m in p.motors] == ["PinCouplerRocker"] and len(p.loops) == 1
    assemble({"part": [BASE, on_chassis(ex, "fourbar_slot_on_loop", {"crank": "PWM:0"}, name="fb")]}, Path("."))


def test_a_chain_from_the_base_to_a_wrist_on_the_arm_is_an_error_ruling_r10(ex):
    (p,) = bad(ex, "bad_chain")
    assert "coupling 'Chain'" in p and "'SprocketSpin'" in p and "'Wrist'" in p
    assert "move relative to the same part" in p and "drive 'Wrist' directly with its own motor slot" in p


def test_a_shell_with_an_override_exports_and_loads_and_lost_mass_warns(ex):
    s = ok(ex, "shell")
    w = s["warnings"]
    assert any(x.startswith("Skin has faces or shells that aren't closed solids, so it adds no mass; give it a mass "
                            "override") for x in w), w
    assert any(x.startswith("Inside has a solid with no volume (inside out or broken)") for x in w), w
    assert not [x for x in w if x.startswith("Sheet ")]                   # its override gives it its mass
    p = part(ex, "shell")
    sheet = body(p, "Sheet")
    assert sheet.mass == pytest.approx(0.5) and sheet.com is not None
    (g,) = sheet.geoms
    assert g.shape == "mesh" and not g.collide
    built = assemble({"part": [BASE, on_chassis(ex, "shell", {}, name="sh")]}, Path("."))
    assert built.model.body_mass[built.model.body("sh.Sheet").id] == pytest.approx(0.5)


def _xlink_cli(src, out, capsys):
    try:
        main(["freecad", "export", str(src), str(out)])
    except SystemExit as e:
        return e.code, capsys.readouterr()
    return 0, capsys.readouterr()


def test_cli_export_follows_links_into_other_documents(fixtures_dir, tmp_path, capsys):
    src = Path(fixtures_dir["xlink"])
    lib = src.parent / "xlink_lib.FCStd"
    # in place (FreeCAD can read the file where it is, and finds the linked document next to it)
    code, out = _xlink_cli(src, tmp_path / "in_place", capsys)
    assert code == 0, out.err
    p = load_part(tmp_path / "in_place")
    assert [b.name for b in p.bodies] == ["Base", "ArmLink"]
    assert body(p, "ArmLink").mass == pytest.approx(math.pi * 60 ** 2 * 20 * 1e-9 * RHO_AL)
    # from a folder a snap's FreeCAD can't read (/tmp): copied with the .FCStd files next to it
    (tmp_path / "src").mkdir()
    shutil.copy(src, tmp_path / "src" / src.name)
    shutil.copy(lib, tmp_path / "src" / lib.name)
    code, out = _xlink_cli(tmp_path / "src" / src.name, tmp_path / "copied", capsys)
    assert code == 0, out.err
    assert body(load_part(tmp_path / "copied"), "ArmLink").mass == pytest.approx(body(p, "ArmLink").mass)
    # the linked document missing: said so, nothing written
    (tmp_path / "alone").mkdir()
    shutil.copy(src, tmp_path / "alone" / src.name)
    code, out = _xlink_cli(tmp_path / "alone" / src.name, tmp_path / "missing", capsys)
    assert code == 2
    assert "links to parts in other FreeCAD files that weren't found (ArmLink)" in out.err
    assert "open it in the FreeCAD panel, or keep the linked files next to it" in out.err
    assert not (tmp_path / "missing").exists()
