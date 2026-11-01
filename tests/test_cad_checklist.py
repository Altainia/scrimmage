"""The what's-left checklist: each item's level, text and target, in order, on the climber fixture."""

import dataclasses
from pathlib import Path

import pytest

from scrimmage.cad.build import build_parts
from scrimmage.cad.checklist import Item, checklist
from scrimmage.cad.edits import apply_edit
from scrimmage.cad.settings import new_import
from scrimmage.cad.source import Source, load_source

DATA = Path(__file__).parent / "data" / "cad"
HINGE = {"kind": "hinge", "anchor": [0, 0, 0], "axis": [0, 1, 0]}


@pytest.fixture(scope="module")
def src():
    return load_source(DATA / "climber.step")


@pytest.fixture
def imp(src, tmp_path):
    """Every piece done (steel, frame confirmed), every body jointed with a motor: nothing left."""
    i = new_import(src, "climber", DATA / "climber.step", tmp_path)
    i = edit(i, "set_material", pieces=list(i.pieces), material="steel")
    i = edit(i, "set_frame", up="+z", confirmed=True)
    for b in ("arm_A", "arm_B", "hook_assy", "hook_assy_2"):
        i = edit(i, "set_joint", body=b, parent="base_plate", joint=HINGE)
        i = edit(i, "set_motor", body=b, settings={"motor": "NEO", "ratio": 9})
    return i


def edit(imp, op, **args):
    return apply_edit(imp, op, args)[0]


def run(imp, src, **kw):
    return checklist(imp, src, build_parts(imp, src), **kw)


def test_nothing_left(imp, src):
    assert run(imp, src) == []


def test_fresh_import_lists_everything_in_order(src, tmp_path):
    imp = new_import(src, "climber", DATA / "climber.step", tmp_path)
    items = run(imp, src)
    assert [i.level for i in items] == ["todo"] * 5 + ["check", "todo"]
    assert items[0].text == "7 pieces in base_plate are still guessed as aluminum"
    assert items[0].target["kind"] == "piece" and len(items[0].target["keys"]) == 7
    assert items[1] == Item("todo", "1 piece in arm_A is still guessed as aluminum",
                            {"kind": "piece", "keys": ["Climber/arm_A"]})
    assert items[-2] == Item("check", "arm_A, arm_B, hook_assy and hook_assy_2 are welded to base_plate: "
                             "give them a joint if they move",
                             {"kind": "body", "keys": ["arm_A", "arm_B", "hook_assy", "hook_assy_2"]})
    assert items[-1] == Item("todo", "Check which way is up and forward for the robot", {"kind": "frame", "keys": []})


def test_default_material_one_item_per_body(imp, src):
    for k in ["Climber/arm_A", "Climber/bolt", "Climber/bolt#2", "Climber/bolt#3"]:
        imp.pieces[k].material = None
    items = run(imp, src)
    assert items == [Item("todo", "3 pieces in base_plate are still guessed as aluminum",
                          {"kind": "piece", "keys": ["Climber/bolt", "Climber/bolt#2", "Climber/bolt#3"]}),
                     Item("todo", "1 piece in arm_A is still guessed as aluminum",
                          {"kind": "piece", "keys": ["Climber/arm_A"]})]


def test_typed_mass_or_left_out_is_not_a_guess(imp, src):
    imp.pieces["Climber/arm_A"].material = imp.pieces["Climber/arm_B"].material = None
    imp = edit(imp, "set_mass", piece="Climber/arm_A", kg=1.0)
    imp = edit(imp, "set_mass", body="arm_B", kg=1.0)
    assert run(imp, src) == []
    imp.bodies["arm_B"].mass = None
    imp = edit(imp, "leave_out", pieces=["Climber/arm_B"], out=True)
    assert [i for i in run(imp, src) if i.level == "todo"] == []     # only the "weighs nothing" message is left


def test_no_volume_is_an_error(imp, src):
    flat = [dataclasses.replace(p, f=p.f[:1]) if p.key == "Climber/arm_A" else p for p in src.pieces]
    open_src = Source(src.kind, src.path, src.units, flat, src.groups)
    items = run(imp, open_src)
    assert items[0] == Item("error", "1 piece in arm_A has no volume: type a weight or leave it out",
                            {"kind": "piece", "keys": ["Climber/arm_A"]})
    assert len(items) == 2 and "weighs nothing" in items[1].text and items[1].level == "error"   # the build's message
    imp = edit(imp, "set_mass", piece="Climber/arm_A", kg=0.5)
    assert run(imp, open_src) == []


def test_guessed_matches_are_checks(imp, src):
    items = run(imp, src, guessed=["Climber/arm_A", "Climber/bolt"])
    assert items == [Item("check", "Climber/arm_A was matched by shape after re-importing: check it",
                          {"kind": "piece", "keys": ["Climber/arm_A"]}),
                     Item("check", "Climber/bolt was matched by shape after re-importing: check it",
                          {"kind": "piece", "keys": ["Climber/bolt"]})]


def test_welded_bodies_one_check(imp, src):
    imp = edit(imp, "set_joint", body="arm_B", parent="arm_A", joint=None)
    assert run(imp, src) == [Item("check", "arm_B is welded to arm_A: give it a joint if it moves",
                                  {"kind": "body", "keys": ["arm_B"]})]


def test_jointed_without_motor(imp, src):
    imp = edit(imp, "set_motor", body="arm_A", settings=None)
    imp = edit(imp, "set_motor", body="hook_assy", settings=None)
    assert run(imp, src) == [
        Item("todo", "arm_A has a joint but no motor: add one, or leave it to move freely",
             {"kind": "body", "keys": ["arm_A"]}),
        Item("todo", "hook_assy has a joint but no motor: add one, or leave it to move freely",
             {"kind": "body", "keys": ["hook_assy"]})]


def test_frame_unconfirmed(imp, src):
    imp.frame.confirmed = False
    assert run(imp, src) == [Item("todo", "Check which way is up and forward for the robot",
                                  {"kind": "frame", "keys": []})]


def test_build_messages_come_last(imp, src):
    imp.bodies["arm_A"].collision = "wheel"
    imp.bodies["arm_A"].joint = None
    imp.frame.confirmed = False
    items = run(imp, src)
    assert items[-2:] == [Item("todo", "Check which way is up and forward for the robot", {"kind": "frame", "keys": []}),
                          Item("check", items[-1].text, {"kind": "body", "keys": ["arm_A"]})]
    assert items[-1].text.startswith("arm_A is set to touch things as a wheel")


def test_explicit_aluminum_clears_the_guess(src, tmp_path):
    imp = new_import(src, "climber", DATA / "climber.step", tmp_path)
    assert imp.pieces["Climber/arm_A"].material is None
    assert any("arm_A" in i.text and "guessed" in i.text for i in run(imp, src))
    imp = edit(imp, "set_material", pieces=list(imp.pieces), material="aluminum")
    assert not any("guessed" in i.text for i in run(imp, src))


def test_none_builds_like_aluminum(src, tmp_path):
    none = new_import(src, "climber", DATA / "climber.step", tmp_path)
    alu = edit(none, "set_material", pieces=list(none.pieces), material="aluminum")
    mass = lambda i: [p.mass for p in build_parts(i, src).parts.values()]
    assert mass(none) == mass(alu) and mass(none)[0] > 0


def test_typed_weights_message_targets_its_body(imp, src):
    imp.bodies["arm_A"].mass = 5.0
    imp.parts["climber"].total_mass = 1.0
    items = run(imp, src)
    msg = [i for i in items if i.text.startswith("The weights typed in ")]
    assert msg and msg[0].target == {"kind": "body", "keys": ["base_plate"]}   # the part's root body
