import copy

import pytest

from scrimmage.cad.edits import EditError, apply_edit
from scrimmage.cad.frame import RobotFrame
from scrimmage.cad.settings import (BodySettings, ImportFile, JointSettings, MotorSettings, MountSettings,
                                    PartSettings, PieceSettings, remove_empty_bodies)


def hinge():
    return JointSettings("hinge", [0, 0, 0], [0, 1, 0])


@pytest.fixture
def imp():
    return ImportFile(
        name="bot", import_id="x", source="bot.step", units="m", frame=RobotFrame(),
        pieces={"a1": PieceSettings("base"), "a2": PieceSettings("base"), "b1": PieceSettings("arm"),
                "c1": PieceSettings("hook"), "d1": PieceSettings("other")},
        bodies={"base": BodySettings(part="bot"),
                "arm": BodySettings(parent="base", joint=hinge()),
                "hook": BodySettings(parent="arm", joint=JointSettings("slide", [0, 0, 0], [0, 0, 1])),
                "other": BodySettings(parent="base", joint=hinge())},
        parts={"bot": PartSettings("bot")})


def bad(imp, op, args, field):
    with pytest.raises(EditError) as e:
        apply_edit(imp, op, args)
    assert e.value.field == field and e.value.message
    return e.value


def test_apply_edit_does_not_mutate_input(imp):
    before = copy.deepcopy(imp)
    apply_edit(imp, "set_material", {"pieces": ["a1"], "material": "steel"})
    apply_edit(imp, "merge_bodies", {"into": "base", "other": "arm"})
    assert imp == before


def test_unknown_op(imp):
    bad(imp, "frobnicate", {}, "op")


def test_set_material_clears_mass(imp):
    imp.pieces["a1"].mass = 2.0
    out, _ = apply_edit(imp, "set_material", {"pieces": ["a1", "a2"], "material": "pla", "infill": 0.2})
    assert out.pieces["a1"].material == "pla" and out.pieces["a1"].infill == 0.2 and out.pieces["a1"].mass is None
    assert out.pieces["b1"].material is None
    bad(imp, "set_material", {"pieces": ["a1"], "material": "unobtainium"}, "material")
    bad(imp, "set_material", {"pieces": ["a1"], "material": "custom"}, "custom_density")
    bad(imp, "set_material", {"pieces": ["zz"], "material": "steel"}, "pieces")
    out, _ = apply_edit(imp, "set_material", {"pieces": ["a1"], "material": "custom", "custom_density": 900})
    assert out.pieces["a1"].custom_density == 900


def test_set_mass(imp):
    assert apply_edit(imp, "set_mass", {"piece": "a1", "kg": 1.5})[0].pieces["a1"].mass == 1.5
    assert apply_edit(imp, "set_mass", {"body": "arm", "kg": 2})[0].bodies["arm"].mass == 2
    imp.bodies["arm"].mass = 3
    assert apply_edit(imp, "set_mass", {"body": "arm", "kg": None})[0].bodies["arm"].mass is None
    bad(imp, "set_mass", {"body": "arm", "kg": 0}, "kg")
    bad(imp, "set_mass", {"body": "arm", "kg": -1}, "kg")
    bad(imp, "set_mass", {"kg": 1}, "piece")


def test_move_pieces_removes_emptied_body(imp):
    out, _ = apply_edit(imp, "move_pieces", {"pieces": ["b1"], "body": "base"})
    assert out.pieces["b1"].body == "base" and "arm" not in out.bodies
    assert out.bodies["hook"].parent == "base"
    bad(imp, "move_pieces", {"pieces": ["b1"], "body": "nope"}, "body")


def test_new_body_hangs_on_old_body(imp):
    out, _ = apply_edit(imp, "new_body", {"pieces": ["a2"], "name": "lid"})
    assert out.pieces["a2"].body == "lid" and out.bodies["lid"].parent == "base"
    bad(imp, "new_body", {"pieces": ["a2"], "name": "arm"}, "name")
    bad(imp, "new_body", {"pieces": ["a2", "b1"], "name": "lid"}, "pieces")


def test_merge_drops_other_joint_and_reparents_children(imp):
    imp.bodies["arm"].motor = MotorSettings("NEO", spool_diameter=0.04)
    imp.bodies["arm"].mounts = [MountSettings("m", [0, 0, 0], [0, 0, 1])]
    imp.couplings = [{"a": "arm", "b": "other", "ratio": 1.0}]
    out, _ = apply_edit(imp, "merge_bodies", {"into": "base", "other": "arm"})
    assert "arm" not in out.bodies and out.pieces["b1"].body == "base"
    assert out.bodies["hook"].parent == "base" and out.couplings == []
    assert out.bodies["base"].mounts == []
    bad(imp, "merge_bodies", {"into": "arm", "other": "arm"}, "other")


def test_rename_body_updates_couplings_and_parent(imp):
    imp.couplings = [{"a": "arm", "b": "other", "ratio": 2.0}]
    imp.loops = [{"body1": "arm", "body2": "hook", "point": [0, 0, 0]}]
    out, _ = apply_edit(imp, "rename_body", {"old": "arm", "new": "upper arm"})
    assert list(out.bodies) == ["base", "upper_arm", "hook", "other"]
    assert out.bodies["hook"].parent == "upper_arm" and out.pieces["b1"].body == "upper_arm"
    assert out.couplings[0]["a"] == "upper_arm" and out.loops[0]["body1"] == "upper_arm"
    bad(imp, "rename_body", {"old": "arm", "new": "hook"}, "new")
    bad(imp, "rename_body", {"old": "zzz", "new": "q"}, "old")


def test_leave_out(imp):
    out, _ = apply_edit(imp, "leave_out", {"pieces": ["a1"], "out": True})
    assert out.pieces["a1"].left_out and not out.pieces["a2"].left_out
    assert not apply_edit(out, "leave_out", {"pieces": ["a1"], "out": False})[0].pieces["a1"].left_out


def test_set_joint_refuses_a_cycle(imp):
    e = bad(imp, "set_joint", {"body": "arm", "parent": "hook", "joint": None}, "parent")
    assert "arm can't hang on hook: hook already hangs on arm" in e.message
    bad(imp, "set_joint", {"body": "arm", "parent": "arm", "joint": None}, "parent")


def test_set_joint_root_cannot_have_one(imp):
    bad(imp, "set_joint", {"body": "base", "parent": None, "joint": {"kind": "hinge", "anchor": [0, 0, 0],
                                                                       "axis": [0, 0, 1]}}, "joint")


def test_set_joint_reparents_and_drops_slots_when_cleared(imp):
    imp.bodies["arm"].motor = MotorSettings("NEO", spool_diameter=0.04)
    out, _ = apply_edit(imp, "set_joint", {"body": "arm", "parent": "other",
                                        "joint": {"kind": "slide", "anchor": [0, 0, 0], "axis": [1, 0, 0],
                                                  "range": [0, 0.5]}})
    assert out.bodies["arm"].parent == "other" and out.bodies["arm"].joint.range == (0.0, 0.5)
    out, _ = apply_edit(imp, "set_joint", {"body": "arm", "parent": "base", "joint": None})
    assert out.bodies["arm"].joint is None and out.bodies["arm"].motor is None
    bad(imp, "set_joint", {"body": "arm", "parent": "base", "joint": {"kind": "wobble", "anchor": [0, 0, 0],
                                                                       "axis": [1, 0, 0]}}, "joint")


def test_motor_needs_joint_and_known_motor(imp):
    out, _ = apply_edit(imp, "set_motor", {"body": "arm", "settings": {"motor": "NEO", "count": 2, "ratio": 20}})
    assert out.bodies["arm"].motor == MotorSettings("NEO", 2, 20.0)
    bad(imp, "set_motor", {"body": "base", "settings": {"motor": "NEO"}}, "body")
    bad(imp, "set_motor", {"body": "arm", "settings": {"motor": "warp_drive"}}, "motor")
    bad(imp, "set_motor", {"body": "arm", "settings": {"motor": "NEO", "ratio": 0}}, "ratio")
    imp.bodies["arm"].motor = MotorSettings("NEO", spool_diameter=0.04)
    assert apply_edit(imp, "set_motor", {"body": "arm", "settings": None})[0].bodies["arm"].motor is None


def test_slide_motor_needs_spool(imp):
    bad(imp, "set_motor", {"body": "hook", "settings": {"motor": "NEO"}}, "spool_diameter")
    out, _ = apply_edit(imp, "set_motor", {"body": "hook", "settings": {"motor": "NEO", "spool_diameter": 0.04}})
    assert out.bodies["hook"].motor.spool_diameter == 0.04


def test_set_encoder(imp):
    out, _ = apply_edit(imp, "set_encoder", {"body": "arm", "settings": {"on": "motor", "invert": True}})
    assert out.bodies["arm"].encoder.on == "motor"
    bad(imp, "set_encoder", {"body": "base", "settings": {}}, "body")
    bad(imp, "set_encoder", {"body": "arm", "settings": {"on": "sky"}}, "on")
    assert apply_edit(out, "set_encoder", {"body": "arm", "settings": None})[0].bodies["arm"].encoder is None


def test_set_gyro(imp):
    assert apply_edit(imp, "set_gyro", {"body": "base", "on": True})[0].bodies["base"].gyro is True
    bad(imp, "set_gyro", {"body": "zz", "on": True}, "body")


def test_mounts_unique_in_part(imp):
    out, _ = apply_edit(imp, "add_mount", {"body": "arm", "name": "tip", "point": [1, 0, 0], "normal": [0, 0, 1]})
    assert out.bodies["arm"].mounts == [MountSettings("tip", [1.0, 0.0, 0.0], [0.0, 0.0, 1.0])]
    bad(out, "add_mount", {"body": "hook", "name": "tip", "point": [0, 0, 0], "normal": [0, 0, 1]}, "name")
    bad(imp, "add_mount", {"body": "arm", "name": "t", "point": [0, 0], "normal": [0, 0, 1]}, "point")
    bad(imp, "add_mount", {"body": "arm", "name": "t", "point": [0, 0, 0], "normal": [0, 0, 0]}, "normal")
    back, _ = apply_edit(out, "remove_mount", {"body": "arm", "name": "tip"})
    assert back.bodies["arm"].mounts == []
    bad(back, "remove_mount", {"body": "arm", "name": "tip"}, "name")


def test_set_collision(imp):
    assert apply_edit(imp, "set_collision", {"body": "arm", "mode": "wheel"})[0].bodies["arm"].collision == "wheel"
    bad(imp, "set_collision", {"body": "arm", "mode": "squishy"}, "mode")


def test_set_part_makes_second_part(imp):
    out, _ = apply_edit(imp, "set_part", {"body": "arm", "part": "claw"})
    assert out.bodies["arm"].parent is None and out.bodies["arm"].part == "claw"
    assert out.bodies["arm"].joint is None and out.parts["claw"].folder == "claw"
    assert out.bodies["hook"].parent == "arm"
    bad(imp, "set_part", {"body": "arm", "part": "bot"}, "part")


def test_set_part_none_joins_another_part(imp):
    two, _ = apply_edit(imp, "set_part", {"body": "arm", "part": "claw"})
    out, _ = apply_edit(two, "set_part", {"body": "arm", "part": None, "parent": "base"})
    assert out.bodies["arm"].parent == "base" and out.bodies["arm"].part is None and "claw" not in out.parts
    bad(two, "set_part", {"body": "arm", "part": None}, "parent")
    bad(imp, "set_part", {"body": "base", "part": None, "parent": "arm"}, "part")


def test_couplings(imp):
    out, _ = apply_edit(imp, "add_coupling", {"a": "arm", "b": "other", "ratio": -2})
    assert out.couplings == [{"a": "arm", "b": "other", "ratio": -2.0}]
    bad(imp, "add_coupling", {"a": "arm", "b": "base", "ratio": 1}, "b")
    bad(imp, "add_coupling", {"a": "arm", "b": "other", "ratio": 0}, "ratio")
    bad(imp, "add_coupling", {"a": "arm", "b": "arm", "ratio": 1}, "b")
    assert apply_edit(out, "remove_coupling", {"a": "arm", "b": "other"})[0].couplings == []
    bad(imp, "remove_coupling", {"a": "arm", "b": "other"}, "a")


def test_loops(imp):
    out, _ = apply_edit(imp, "add_loop", {"body1": "arm", "body2": "other", "point": [0.1, 0, 0]})
    assert out.loops == [{"body1": "arm", "body2": "other", "point": [0.1, 0.0, 0.0]}]
    bad(imp, "add_loop", {"body1": "arm", "body2": "arm", "point": [0, 0, 0]}, "body2")
    bad(imp, "add_loop", {"body1": "arm", "body2": "other", "point": [0, 0]}, "point")
    assert apply_edit(out, "remove_loop", {"body1": "arm", "body2": "other"})[0].loops == []
    bad(imp, "remove_loop", {"body1": "arm", "body2": "other"}, "body1")


def test_set_frame_up_perpendicular_to_front(imp):
    out, _ = apply_edit(imp, "set_frame", {"up": "+y", "front": "-x", "floor": 0.1})
    assert (out.frame.up, out.frame.front, out.frame.floor, out.frame.confirmed) == ("+y", "-x", 0.1, True)
    bad(imp, "set_frame", {"up": "+z", "front": "-z"}, "front")
    bad(imp, "set_frame", {"sideways": 1}, "sideways")


def test_set_total(imp):
    assert apply_edit(imp, "set_total", {"part": "bot", "kg": 5})[0].parts["bot"].total_mass == 5
    bad(imp, "set_total", {"part": "bot", "kg": 0}, "kg")
    bad(imp, "set_total", {"part": "zz", "kg": 1}, "part")
    imp.parts["bot"].total_mass = 4
    assert apply_edit(imp, "set_total", {"part": "bot", "kg": None})[0].parts["bot"].total_mass is None


def test_set_units_mesh_only(imp):
    bad(imp, "set_units", {"units": "mm"}, "units")
    imp.source = "bot_meshes"
    assert apply_edit(imp, "set_units", {"units": "mm"})[0].units == "mm"
    bad(imp, "set_units", {"units": "furlongs"}, "units")


def test_empty_root_heir_loses_its_joint(imp):
    for k in ("a1", "a2"):
        imp.pieces[k].body = "arm"
    new, msgs = remove_empty_bodies(imp)
    assert "base" not in new.bodies and new.bodies["arm"].parent is None and new.bodies["arm"].joint is None
    assert new.bodies["arm"].part == "bot" and new.bodies["other"].parent == "arm"
    assert any("arm" in m and "joint" in m and "root" in m for m in msgs)


def test_empty_root_heir_coupling_dropped(imp):
    for k in ("a1", "a2"):
        imp.pieces[k].body = "arm"
    imp.couplings = [{"a": "arm", "b": "other", "ratio": 1.0}, {"a": "other", "b": "hook", "ratio": 2.0}]
    new, msgs = remove_empty_bodies(imp)
    assert new.couplings == [{"a": "other", "b": "hook", "ratio": 2.0}]
    assert any("coupling" in m and "arm" in m for m in msgs)


def test_move_pieces_returns_messages_about_dropped_joint(imp):
    out, msgs = apply_edit(imp, "move_pieces", {"pieces": ["b1"], "body": "base"})
    assert any("arm is no longer" in m and "joint" in m for m in msgs)
    assert apply_edit(imp, "leave_out", {"pieces": ["a1"], "out": True})[1] == []


def test_clearing_joint_reports_dropped_motor_and_coupling(imp):
    imp.bodies["arm"].motor = MotorSettings("NEO")
    imp.couplings = [{"a": "arm", "b": "other", "ratio": 1.0}]
    out, msgs = apply_edit(imp, "set_joint", {"body": "arm", "parent": "base", "joint": None})
    assert out.couplings == [] and any("motor" in m for m in msgs) and any("coupling" in m for m in msgs)


def test_plain_messages_for_bad_settings(imp):
    e = bad(imp, "set_motor", {"body": "arm", "settings": {"count": 2}}, "motor")
    assert "needs a motor" in e.message and "__init__" not in e.message
    j = {"kind": "hinge", "anchor": [0, 0, 0], "axis": [0, 0, 1]}
    e = bad(imp, "set_joint", {"body": "arm", "parent": "base", "joint": {**j, "range": 5}}, "range")
    assert "two numbers" in e.message
    bad(imp, "set_joint", {"body": "arm", "parent": "base", "joint": {**j, "range": [1, 0]}}, "range")
    bad(imp, "set_joint", {"body": "arm", "parent": "base", "joint": {**j, "damping": -1}}, "damping")
    bad(imp, "set_joint", {"body": "arm", "parent": "base", "joint": {"kind": "hinge"}}, "anchor")
    bad(imp, "set_frame", {"floor": float("nan")}, "floor")
    bad(imp, "set_frame", {"front": "up"}, "front")
    bad(imp, "set_mass", {"body": "arm"}, "kg")
    bad(imp, "rename_body", {"old": ["x"], "new": "q"}, "old")
    bad(imp, "rename_body", {"old": "arm", "new": 5}, "new")
    bad(imp, "move_pieces", {"pieces": [["a1"]], "body": "base"}, "pieces")


def test_mount_names_rechecked_when_bodies_change_parts(imp):
    m = MountSettings("tip", [0, 0, 0], [0, 0, 1])
    imp.bodies["arm"].mounts = [m]
    two, _ = apply_edit(imp, "set_part", {"body": "other", "part": "claw"})
    two.bodies["other"].mounts = [MountSettings("tip", [0, 0, 0], [0, 0, 1])]
    bad(two, "set_part", {"body": "other", "part": None, "parent": "base"}, "name")
    bad(two, "set_joint", {"body": "other", "parent": "base", "joint": None}, "parent")
    out, _ = apply_edit(imp, "add_mount", {"body": "arm", "name": " lip ", "point": [0, 0, 0], "normal": [0, 0, 1]})
    assert out.bodies["arm"].mounts[-1].name == "lip"
