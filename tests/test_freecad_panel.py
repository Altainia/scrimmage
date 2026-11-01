"""Exporter, Task 7: the task panel's headless logic (pick.py, overview.py) and, opt-in, the GUI smoke run.

The panel (freecad/Scrimmage/scrimmage_part/panel.py) only lays out widgets, edits settings and calls these modules and
`export`; everything it computes is tested here. `test_gui_smoke` opens the real FreeCAD GUI (tests/freecad/
gui_smoke.py): it runs only with SCRIMMAGE_FREECAD_GUI=1 and a desktop, never in CI."""

import json
import math
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from freecad_harness import needs_freecad, run_in_freecad

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "freecad" / "Scrimmage"))
from scrimmage_part import overview  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- plain Python (no FreeCAD)

def test_unique_name_and_default_export_dir():
    assert overview.unique_name("arm_mount", set()) == "arm_mount"
    assert overview.unique_name("arm_mount", {"arm_mount", "arm_mount_2"}) == "arm_mount_3"
    assert overview.default_export_dir("/home/u/cad/robot.FCStd", "My Arm") == "/home/u/cad/My_Arm"
    assert overview.default_export_dir("", "Assembly") == ""


def test_summary_and_problems_text():
    s = dict(part="arm", bodies=["Base", "Arm"], joints=["Hinge"], loops=1, couplings=0, slots=["pivot", "enc"],
             mounts=["top"], mass_kg=0.54, warnings=["slot 'x' was on joint 'J', which no longer exists; dropped"],
             out_dir="/tmp/arm")
    text = overview.summary_text(s)
    for want in ("Exported 'arm' to /tmp/arm", "Bodies (2): Base, Arm", "Joints (1): Hinge", "Loops: 1",
                 "Slots (2): pivot, enc", "Mounts (1): top", "Total mass: 0.540 kg", "Warnings:", "- slot 'x'"):
        assert want in text
    assert "Couplings" not in text                               # zero counts are left out
    p = overview.problems_text(["ground one component", "joint 'B' is broken"])
    assert p.startswith("Nothing was exported. Fix these 2 problems:") and "- joint 'B' is broken" in p


# ---------------------------------------------------------------- inside headless FreeCAD

PICK = r"""
import json
import FreeCAD as App, Part
from scrimmage_part import overview, pick, read

def r(v): return [round(float(x), 9) for x in v]
def fr(f): return [r(f[0]), r(f[1])]

out = {}
doc = App.openDocument(PATHS["arm"])
asm = read.find_assembly(doc)
arm = doc.getObject("Arm")
# picked through the Assembly (getSelectionEx("", 0) gives the root object and the full sub-element path)
for sub in ("Arm.Face6", "Arm.Face1", "Arm.Edge1", "Arm.Vertex1"):
    try:
        name, comp, frame = pick.pick(asm, asm, sub, {"Arm_mount"})
        out[sub] = [name, comp, fr(frame)]
    except pick.PickError as e:
        out[sub] = str(e)
# picked with the component itself as the root (a resolved selection)
out["direct"] = fr(pick.pick(asm, arm, "Face6", set())[2])
# the stored local frame, put back through the component's placement, lands on the global face center
name, comp, frame = pick.pick(asm, asm, "Arm.Face6", set())
g = read._global(arm) * App.Placement(App.Vector(*frame[0]), App.Rotation(*frame[1][1:], frame[1][0]))
f6 = Part.getShape(asm, "Arm.Face6", needSubElement=True)
out["global_ok"] = (g.Base - f6.CenterOfMass).Length < 1e-6 and (g.Rotation.multVec(App.Vector(0, 0, 1)) - App.Vector(0, 0, 1)).Length < 1e-9
# a face of an object outside the Assembly
other = doc.addObject("Part::Box", "Loose")
doc.recompute()
try:
    pick.pick(asm, other, "Face1", set())
    out["loose"] = None
except pick.PickError as e:
    out["loose"] = str(e)
out["sel_path"] = list(pick.selection_path(asm, "Arm"))
App.closeDocument(doc.Name)

doc = App.openDocument(PATHS["arm_gui"])
asm = read.find_assembly(doc)
for sub in ("Arm.Edge3", "Arm.Face1", "Arm.Face3"):       # top circle, cylindrical side, top disc
    out["gui_" + sub] = fr(pick.pick(asm, asm, sub, set())[2])
App.closeDocument(doc.Name)

doc = App.openDocument(PATHS["arm_nested"])               # the component is an App::Part holding the Base box
asm = read.find_assembly(doc)
out["nested"] = list(pick.pick(asm, asm, "Sub.Base.Face6", set()))
App.closeDocument(doc.Name)

doc = App.openDocument(PATHS["arm_link"])                 # the component is an App::Link; the Assembly is moved
asm = read.find_assembly(doc)
out["link"] = list(pick.pick(asm, asm, "ArmLink.Face3", set()))
hidden = asm.newObject("Part::Box", "Hidden")             # hidden and unjointed: read doesn't export it
hidden.Visibility = False
doc.recompute()
try:
    pick.pick(asm, asm, "Hidden.Face6", set())
    out["hidden"] = None
except pick.PickError as e:
    out["hidden"] = str(e)
App.closeDocument(doc.Name)

for n in ("arm", "fourbar", "bad_ungrounded"):
    doc = App.openDocument(PATHS[n])
    out["ov_" + n] = overview.overview(doc)
    App.closeDocument(doc.Name)
print("SCRIMMAGE_JSON:" + json.dumps(out))
"""


@pytest.fixture(scope="module")
def picked(fixtures_dir):
    return run_in_freecad(f"PATHS = {json.dumps(fixtures_dir)}\n" + PICK)


@needs_freecad
def test_pick_planar_face_gives_center_and_outward_normal_in_component_frame(picked):
    name, comp, (pos, q) = picked["Arm.Face6"]
    assert comp == "Arm" and name == "Arm_mount_2"                # unique against the names already used
    assert pos == pytest.approx([150.0, 10.0, 20.0]) and q == pytest.approx([1, 0, 0, 0], abs=1e-9)
    _, _, (pos, q) = picked["Arm.Face1"]                           # the x = 0 end: normal -x
    assert pos == pytest.approx([0.0, 10.0, 10.0])
    w, x, y, z = q
    assert (2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)) == pytest.approx((-1, 0, 0), abs=1e-9)
    assert picked["direct"] == picked["Arm.Face6"][2]
    assert picked["global_ok"] is True


@needs_freecad
def test_pick_rejects_straight_edges_vertices_and_outside_objects(picked):
    assert "flat face, a round edge or a cylindrical face" in picked["Arm.Edge1"]   # a box edge is straight
    assert "flat face, a round edge or a cylindrical face" in picked["Arm.Vertex1"]
    assert "isn't part of the Assembly" in picked["loose"]
    assert picked["sel_path"] == ["Assembly", "Arm."]


@needs_freecad
def test_pick_circular_edge_and_cylindrical_face_give_center_and_axis(picked):
    # Part::Cylinder r60 h20 (probed): Edge3 the top circle, Face1 the side, Face3 the top disc; local frame, axis +z
    assert picked["gui_Arm.Edge3"][0] == pytest.approx([0, 0, 20], abs=1e-6)
    assert picked["gui_Arm.Face1"][0] == pytest.approx([0, 0, 10], abs=1e-6)
    assert picked["gui_Arm.Face3"][0] == pytest.approx([0, 0, 20], abs=1e-6)
    for k in ("gui_Arm.Edge3", "gui_Arm.Face1", "gui_Arm.Face3"):
        assert picked[k][1] == pytest.approx([1, 0, 0, 0], abs=1e-6)


@needs_freecad
def test_pick_on_app_part_and_app_link_components_and_not_on_non_parts(picked):
    name, comp, (pos, q) = picked["nested"]
    assert (name, comp) == ("Sub_mount", "Sub")                    # framed in the App::Part, not in its Base box
    assert pos == pytest.approx([55.0, 25.0, 20.0], abs=1e-6) and q == pytest.approx([1, 0, 0, 0], abs=1e-9)
    name, comp, (pos, q) = picked["link"]
    assert comp == "ArmLink" and pos == pytest.approx([0, 0, 20], abs=1e-6) and q == pytest.approx([1, 0, 0, 0], abs=1e-9)
    assert "'Hidden' isn't exported as a part" in picked["hidden"]


UNDO = r"""
import json
import FreeCAD as App
from scrimmage_part import settings
d = App.newDocument("undo")
d.UndoMode = 1
settings.save_undoable(d, settings.ScrimmageSettings(export_dir="a"))
settings.save_undoable(d, settings.ScrimmageSettings(export_dir="b", mass_overrides={"Arm": 2.0}))
out = {"before": settings.get(d).export_dir, "names": list(d.UndoNames)}
d.undo()
s = settings.get(d)
out["after_one"] = [s.export_dir, s.mass_overrides]
d.undo()
out["after_two"] = settings.get(d) is None
d.redo()
out["redo"] = settings.get(d).export_dir
print("SCRIMMAGE_JSON:" + json.dumps(out))
"""


GUARD = r"""
import json
import FreeCAD as App
from scrimmage_part import settings
d = App.newDocument("guard")
d.UndoMode = 1
S = settings.ScrimmageSettings
out = {"none": settings.data(d)}
first = settings.save_guarded(d, S(export_dir="a"), None)          # no object yet: expected None
out["first"] = json.loads(first)["export_dir"]
out["stale"] = settings.save_guarded(d, S(export_dir="x"), None)   # caller thinks there is none: refused
second = settings.save_guarded(d, S(export_dir="b"), first)
d.undo()                                                           # back to "a" under the panel holding `second`
out["after_undo"] = settings.save_guarded(d, S(export_dir="c"), second)
out["kept"] = settings.get(d).export_dir
print("SCRIMMAGE_JSON:" + json.dumps(out))
"""


@needs_freecad
def test_save_guarded_refuses_to_overwrite_settings_changed_elsewhere():
    out = run_in_freecad(GUARD)
    assert out["none"] is None and out["first"] == "a"
    assert out["stale"] is None and out["after_undo"] is None      # nothing written...
    assert out["kept"] == "a"                                      # ...so the undone state survives


@needs_freecad
def test_settings_changes_are_undoable():
    out = run_in_freecad(UNDO)
    assert out["before"] == "b" and out["names"][:2] == ["scrimmage settings"] * 2
    assert out["after_one"] == ["a", {}]                           # one Ctrl+Z restores the previous settings
    assert out["after_two"] is True and out["redo"] == "a"         # the first change made the ScrimmagePart object


@needs_freecad
def test_overview_lists_bodies_masses_joints_and_lcs_mounts(picked):
    ov = picked["ov_arm"]
    assert ov["problems"] == []
    assert [(b["name"], [c["name"] for c in b["comps"]]) for b in ov["bodies"]] == [("Base", ["Base"]), ("Arm", ["Arm"])]
    base, arm = ov["bodies"]
    assert base["comps"][0]["mass_kg"] == pytest.approx(100 * 40 * 20 * 2700e-9)
    assert arm["comps"][0]["mass_kg"] == pytest.approx(300 * 20 * 20 * 2700e-9)
    assert [(j["name"], j["type"], j["role"]) for j in ov["joints"]] == [("Hinge", "Revolute", "tree")]
    assert ov["joints"][0]["limits"] == [-30.0, 120.0]
    assert ov["tree_joints"] == [{"name": "Hinge", "label": "Hinge", "type": "Revolute"}]
    assert ov["lcs_mounts"] == [{"name": "top", "comp": None}]
    assert {c["name"] for c in ov["components"]} == {"Base", "Arm"}

    roles = {j["name"]: j["role"] for j in picked["ov_fourbar"]["joints"]}
    assert sorted(roles.values()).count("loop") == 1 and "tree" in roles.values()


@needs_freecad
def test_overview_of_a_broken_assembly_lists_problems_and_its_components(picked):
    ov = picked["ov_bad_ungrounded"]
    assert any("nothing is grounded" in p for p in ov["problems"])
    assert ov["bodies"] == [] and ov["components"]                # mass overrides can still be set


# ---------------------------------------------------------------- the real GUI (opt-in)

@pytest.mark.freecad_gui
def test_gui_smoke():
    """Runs tests/freecad/gui_smoke.py in the FreeCAD GUI: opens the panel on a copy of the arm fixture, adds a motor
    slot, a picked mount and a mass override, sets the folder, clicks Export and checks the summary; then re-opens the
    saved document and exports again. SCRIMMAGE_GUI_PNG=<file> keeps its screenshot of the panel."""
    freecad = shutil.which("freecad")
    if freecad is None:
        pytest.skip("no `freecad` GUI binary")
    work = Path.home() / "snap/freecad/common" / f"scrimmage-gui-{uuid.uuid4().hex[:8]}"
    work.mkdir(parents=True)
    try:
        env = dict(os.environ, SCRIMMAGE_GUI_WORK=str(work), SCRIMMAGE_ADDON=str(ROOT / "freecad" / "Scrimmage"),
                   SCRIMMAGE_FIXTURES=str(ROOT / "tests" / "freecad" / "make_fixtures.py"))
        script = work / "gui_smoke.py"
        shutil.copy(ROOT / "tests" / "freecad" / "gui_smoke.py", script)
        proc = subprocess.run([freecad, str(script)], env=env, capture_output=True, text=True, timeout=300)
        result = work / "result.json"
        assert result.is_file(), f"no result from the GUI run:\n{proc.stdout}\n{proc.stderr}"
        r = json.loads(result.read_text())
        assert r["ok"], r
        assert r["summary"]["bodies"] == ["Base", "Arm"] and "pivot" in r["summary"]["slots"]
        assert "Arm_mount" in r["summary"]["mounts"] and r["summary"]["mass_kg"] == pytest.approx(0.216 + 2.5)
        assert Path(r["png"]).is_file()
        if os.environ.get("SCRIMMAGE_GUI_PNG"):                     # keep the screenshot (WORK is deleted)
            keep = Path(os.environ["SCRIMMAGE_GUI_PNG"])
            shutil.copy(r["png"], keep)
            shutil.copy(r["png_sections"], keep.with_name(keep.stem + "_sections.png"))
    finally:
        shutil.rmtree(work, ignore_errors=True)
