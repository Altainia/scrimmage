import dataclasses
from pathlib import Path

import pytest

from scrimmage.parts.model import (FORMAT, PartError, dump_part, load_part, part_from_dict, part_to_dict,
                                    quat_from_rpy)

GOLDEN = Path(__file__).parent / "parts" / "intake"


def minimal(**over):
    d = {"part": {"name": "p", "format": 1},
         "body": [{"name": "base", "mass": 1.0, "geom": [{"box": [0.1, 0.1, 0.1]}]},
                  {"name": "arm", "parent": "base", "mass": 0.5, "geom": [{"box": [0.3, 0.02, 0.02]}],
                   "joint": {"name": "pivot", "hinge": "y", "range": [-30, 90]}}],
         "motor": [{"slot": "pivot", "joint": "pivot", "motor": "KrakenX60", "gearing": 50}]}
    d.update(over)
    return d


def kitchen_sink_dict():
    """A part that uses every field. Needs a file a.stl next to it when loaded with a base_dir."""
    return {
        "part": {"name": "kitchen", "format": 1},
        "body": [
            {"name": "base", "mass": 1, "com": [0, 0, 0.1], "inertia": [0.1, 0.2, 0.3], "pos": [1, 2, 3],
             "rot": [10, 20, 30],
             "geom": [{"mesh": "a.stl", "pos": [1, 0, 0], "rot": [0, 0, 5], "friction": 0.5, "condim": 4,
                       "solref": [0.02, 1], "color": [1, 0, 0, 1], "collide": False},
                      {"sphere": 0.1},
                      {"cylinder": [0.1, 0.2], "axis": "x"}]},
            {"name": "arm", "parent": "base", "mass": 0.5, "geom": [],
             "joint": {"name": "s", "slide": [1, 1, 0], "range": [0, 0.3], "damping": 0.1, "frictionloss": 0.2,
                       "armature": 0.3, "spring": {"stiffness": 5, "rest": 0.1}}},
            {"name": "arm2", "parent": "base", "mass": 0.5, "geom": [{"box": [1, 1, 1]}],
             "joint": {"name": "h", "hinge": [0.3, -0.7, 0.2], "range": [-10, 10]}},
        ],
        "coupling": [{"joints": ["s", "h"], "ratio": -2}],
        "motor": [{"slot": "m", "joint": "s", "motor": "KrakenX60", "count": 2, "gearing": 4, "invert": True,
                   "efficiency": 0.9}],
        "encoder": [{"slot": "e", "joint": "h", "ratio": 2, "invert": True}],
        "gyro": [{"slot": "g", "body": "base"}],
        "mount": [{"name": "t", "body": "arm", "pos": [1, 0, 0], "rot": [0, 0, 90]}],
    }


def same_part(a, b):
    """Equal parts, ignoring where they were loaded from (base_dir)."""
    return dataclasses.replace(a, base_dir=None) == dataclasses.replace(b, base_dir=None)


def write_mesh(folder: Path):
    (folder / "a.stl").write_bytes(b"solid a\nendsolid a\n")


def test_golden_part_loads():
    p = load_part(GOLDEN)
    assert p.name == "intake"
    assert [b.name for b in p.bodies] == ["frame", "roller"]
    assert p.bodies[1].joint.kind == "hinge" and p.bodies[1].joint.axis == (0.0, 1.0, 0.0)
    assert p.motors[0].gearing == 3 and p.motors[0].count == 1
    assert p.mounts[0].pos == (0.0, 0.0, 0.025)
    assert p.slot_kinds() == {"roller": "motor"}


def test_round_trip(tmp_path):
    p = load_part(GOLDEN)
    dump_part(p, tmp_path / "part.toml")
    q = load_part(tmp_path)
    assert part_to_dict(q) == part_to_dict(p)
    assert same_part(q, p)


def test_kitchen_sink_round_trip(tmp_path):
    write_mesh(tmp_path)
    p = part_from_dict(kitchen_sink_dict(), "kitchen.toml", tmp_path)
    dump_part(p, tmp_path / "part.toml")
    q = load_part(tmp_path)
    assert q == p
    assert q.base_dir == tmp_path
    assert q.bodies[1].joint.kind == "slide"
    assert q.bodies[1].joint.spring == (5.0, 0.1)
    assert q.motors[0].count == 2 and q.motors[0].invert and q.motors[0].efficiency == 0.9


def test_custom_axis_is_stable_across_round_trips(tmp_path):
    write_mesh(tmp_path)
    d = kitchen_sink_dict()
    d["body"][1]["joint"]["slide"] = [1, 1, 0]
    p = part_from_dict(d, "x.toml", tmp_path)
    dump_part(p, tmp_path / "once.toml")
    q = load_part(tmp_path / "once.toml")
    dump_part(q, tmp_path / "twice.toml")
    r = load_part(tmp_path / "twice.toml")
    assert q.bodies[1].joint.axis == p.bodies[1].joint.axis == r.bodies[1].joint.axis
    assert (tmp_path / "once.toml").read_text() == (tmp_path / "twice.toml").read_text()
    assert r == q
    assert sum(x * x for x in q.bodies[1].joint.axis) == pytest.approx(1.0, abs=1e-12)


def test_dump_layout_is_hand_editable(tmp_path):
    dump_part(load_part(GOLDEN), tmp_path / "part.toml")
    text = (tmp_path / "part.toml").read_text()
    assert text.startswith("[part]\nname = \"intake\"\nformat = 1\n")
    assert "[[body]]" in text and "[[motor]]" in text and "[[mount]]" in text
    assert "pos = [0.15, 0.0, 0.0]" in text
    assert "[[body.geom]]" not in text and "motor = [" not in text


def test_newer_format_is_rejected():
    with pytest.raises(PartError, match="newer scrimmage"):
        part_from_dict(minimal(part={"name": "p", "format": FORMAT + 1}), "x.toml", None)


@pytest.mark.parametrize("change, message", [
    (lambda d: d["body"][0].update(mass=0), r"body 'base': mass must be greater than 0"),
    (lambda d: d["body"][0].update(mass=float("nan")), r"body 'base': mass must be a finite number"),
    (lambda d: d["body"][0].update(pos=[float("inf"), 0, 0]), r"body 'base': pos must contain only finite numbers"),
    (lambda d: d["body"][1]["joint"].update(range=[90, -30]), r"range must be \[min, max\]"),
    (lambda d: d["motor"][0].update(gearing=0), r"gearing must be greater than 0"),
    (lambda d: d["motor"][0].update(joint="nope"), r"motor slot 'pivot': no joint 'nope'.*pivot"),
    (lambda d: d["motor"][0].pop("joint"), r"motor slot 'pivot': missing joint"),
    (lambda d: d["motor"][0].update(bogus=1), r"motor slot 'pivot': unknown key 'bogus'"),
    (lambda d: d["motor"][0].update(motor="Flux"), r"Unknown motor 'Flux'"),
    (lambda d: d["motor"][0].update(invert="yes"), r"motor slot 'pivot': invert must be true or false"),
    (lambda d: d["body"][1].update(parent="ghost"), r"body 'arm': parent 'ghost' isn't an earlier body"),
    (lambda d: d["body"][0].update(joint={"name": "j", "hinge": "z"}), r"the root body 'base' can't have a joint"),
    (lambda d: d["body"][0].update(geom=[{"box": [0.1, 0, 0.1]}]), r"box sizes must be greater than 0"),
    (lambda d: d["body"][0].update(geom=[{"box": [1, 1, 1], "sphere": 1}]), r"exactly one of box, cylinder, sphere, mesh"),
    (lambda d: d["body"][0].update(geom=[{"cylinder": [1, 1], "axis": [0, 1, 0]}]), r'axis must be "x", "y" or "z"'),
    (lambda d: d["body"][0].update(geom={"box": [1, 1, 1]}), r"geom must be an array like `geom = \[\{ box = \[\.\.\.\] \}\]`"),
    (lambda d: d["body"][0].update(color=1), r"unknown key 'color'.*allowed"),
    (lambda d: d["body"][0].update(geom=[{"box": [1, 1, 1], "collide": "no"}]), r"collide must be true or false"),
    (lambda d: d.update(coupling=[{"joints": ["pivot", "nope"], "ratio": 2}]), r"coupling: no joint 'nope'"),
    (lambda d: d.update(coupling=[{"joints": ["pivot", "pivot"], "ratio": 2, "bogus": 1}]), r"coupling\[0\]: unknown key 'bogus'"),
    (lambda d: d.update(mount=[{"name": "m", "body": "nope"}]), r"mount 'm': no body 'nope'"),
    (lambda d: d.update(mount=[{"body": "base", "bogus": 1}]), r"mount\[0\]: unknown key 'bogus'"),
    (lambda d: d.update(encoder=[{"slot": "e", "joint": "pivot", "ratio": 0}]), r"encoder slot 'e': ratio must not be 0"),
    (lambda d: d.update(encoder=[{"joint": "pivot", "bogus": 1}]), r"encoder slot\[0\]: unknown key 'bogus'"),
    (lambda d: d.update(gyro=[{"slot": "g", "body": "base", "bogus": 1}]), r"gyro slot 'g': unknown key 'bogus'"),
    (lambda d: d["motor"].append({"slot": "pivot", "joint": "pivot", "motor": "NEO"}), r"slot 'pivot' is defined twice"),
    (lambda d: d.update(part={"name": "p", "format": 0}), r"format must be 1 or more"),
])
def test_validation_errors(change, message):
    d = minimal()
    change(d)
    with pytest.raises(PartError, match=message):
        part_from_dict(d, "x.toml", None)


def test_errors_name_the_source():
    d = minimal()
    d["body"][0]["mass"] = -1
    with pytest.raises(PartError, match=r"^x\.toml: "):
        part_from_dict(d, "x.toml", None)


def test_missing_mesh_names_the_path(tmp_path):
    d = minimal()
    d["body"][0]["geom"] = [{"mesh": "meshes/base.stl"}]
    with pytest.raises(PartError, match=r"meshes/base\.stl"):
        part_from_dict(d, "x.toml", tmp_path)


def test_load_part_missing_file(tmp_path):
    with pytest.raises(PartError, match="part file not found"):
        load_part(tmp_path / "nope.toml")


def test_load_part_folder_without_part_toml(tmp_path):
    with pytest.raises(PartError, match=r"part file not found: .*part\.toml"):
        load_part(tmp_path)


def test_load_part_syntax_error_names_the_file(tmp_path):
    (tmp_path / "part.toml").write_text("[part\nname =\n")
    with pytest.raises(PartError, match=r"part\.toml: "):
        load_part(tmp_path)


def test_quat_from_rpy_yaw_turns_x_to_y():
    import mujoco
    import numpy as np
    q = np.array(quat_from_rpy([0, 0, 90]))
    v = np.zeros(3)
    mujoco.mju_rotVecQuat(v, np.array([1.0, 0, 0]), q)
    assert v == pytest.approx([0, 1, 0], abs=1e-9)
    q = np.array(quat_from_rpy([90, 0, 90]))   # roll first (fixed axes), then yaw
    mujoco.mju_rotVecQuat(v, np.array([0, 1.0, 0]), q)
    assert v == pytest.approx([0, 0, 1], abs=1e-9)


def test_toml_value_escapes_del_and_controls():
    import tomllib
    from scrimmage.parts.toml_writer import _toml_value
    for s in ["a\x7fb", "a\x00b", "tab\tq\"\\", "é\x1f"]:
        text = _toml_value(s)
        assert "\x7f" not in text
        assert tomllib.loads(f"v = {text}")["v"] == s
    assert _toml_value("\x7f") == '"\\u007f"'


def test_axis_on_a_non_cylinder_is_an_error():
    d = kitchen_sink_dict()
    d["body"][0]["geom"] = [{"box": [1, 1, 1], "axis": "y"}]
    with pytest.raises(PartError, match="axis is for cylinders only; turn other shapes with rot"):
        part_from_dict(d, "x.toml", None)


def test_loop_and_inertia_rot_round_trip(tmp_path):
    d = minimal()
    d["body"].append({"name": "link", "parent": "base", "mass": 0.2, "geom": [{"box": [0.1, 0.02, 0.02]}],
                      "joint": {"name": "j2", "hinge": "y"}})
    d["body"][1].update(com=[0.1, 0, 0], inertia=[0.001, 0.002, 0.003], inertia_rot=[0, 0, 30])
    d["loop"] = [{"body1": "arm", "body2": "link", "pos": [0.3, 0, 0]}]
    p = part_from_dict(d, "x.toml", None)
    assert p.loops[0].body1 == "arm" and p.loops[0].pos == (0.3, 0.0, 0.0)
    assert p.bodies[1].inertia_rot == (0.0, 0.0, 30.0)
    dump_part(p, tmp_path / "part.toml")
    assert same_part(load_part(tmp_path), p)


@pytest.mark.parametrize("change, message", [
    (lambda d: d.update(loop=[{"body1": "arm", "body2": "arm", "pos": [0, 0, 0]}]), r"loop.*two different bodies"),
    (lambda d: d.update(loop=[{"body1": "arm", "body2": "nope", "pos": [0, 0, 0]}]), r"loop.*no body 'nope'"),
    (lambda d: d["body"][1].update(inertia_rot=[0, 0, 30]), r"inertia_rot needs inertia"),
    # cleanup E4: the triangle inequality, refused here in words rather than by MuJoCo at build time
    (lambda d: d["body"][1].update(com=[0, 0, 0], inertia=[1, 0.1, 0.1]),
     r"body 'arm': inertia \[1, 0.1, 0.1\] can't be a real body's: none of the three values can be more than the "
     r"other two added together"),
])
def test_loop_and_inertia_rot_errors(change, message):
    d = minimal()
    change(d)
    with pytest.raises(PartError, match=message):
        part_from_dict(d, "x.toml", None)


def test_writer_is_stdlib_only():
    import ast
    import sys
    src = (Path(__file__).parent.parent / "scrimmage" / "parts" / "toml_writer.py").read_text()
    tree = ast.parse(src)
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= set(sys.stdlib_module_names) | {"__future__"}, mods


def _hooked(**geom):
    d = minimal()
    d["body"][1]["geom"] = [{"capsule": [0.005, 0.04], "rot": [0, 30, 0], "name": "hook0.tip", "priority": 1,
                             "condim": 3, "friction": 0.35, **geom}]
    return d


def test_capsule_name_and_priority_round_trip_and_assemble(tmp_path):
    import mujoco
    from scrimmage.parts.assemble import assemble
    p = part_from_dict(_hooked(), "x.toml", None)
    g = p.bodies[1].geoms[0]
    assert (g.shape, g.size, g.name, g.priority) == ("capsule", (0.005, 0.04), "hook0.tip", 1)
    dump_part(p, tmp_path / "part.toml")
    assert same_part(load_part(tmp_path), p)
    built = assemble({"part": [{"name": "c", "path": "."}]}, tmp_path)
    m = built.model
    gid = m.geom("c.hook0.tip").id
    assert m.geom_type[gid] == mujoco.mjtGeom.mjGEOM_CAPSULE
    assert m.geom_size[gid][:2] == pytest.approx([0.005, 0.02])
    assert m.geom_priority[gid] == 1 and m.geom_friction[gid][0] == pytest.approx(0.35)
    assert m.geom_priority[m.geom_bodyid == m.body("c.base").id].tolist() == [0]   # unnamed geoms keep priority 0


@pytest.mark.parametrize("geom, message", [
    ({"priority": -1}, r"priority must be a whole number, 0 or more"),
    ({"priority": 1.5}, r"priority must be a whole number, 0 or more"),
    ({"name": ""}, r"name must be letters, digits, _ and \."),
    ({"name": "a b"}, r"name must be letters, digits, _ and \."),
    ({"tread": True}, r"a tread geom is named by its body.*remove name"),
    ({"capsule": [0.005, 0]}, r"capsule \[radius, length\] must be greater than 0"),
])
def test_geom_name_priority_and_capsule_errors(geom, message):
    with pytest.raises(PartError, match=message):
        part_from_dict(_hooked(**geom), "x.toml", None)


def test_geom_names_are_unique_in_a_part():
    d = _hooked()
    d["body"][0]["geom"].append({"box": [0.1, 0.1, 0.1], "name": "hook0.tip"})
    with pytest.raises(PartError, match=r"geom name 'hook0.tip' is used twice"):
        part_from_dict(d, "x.toml", None)


@pytest.mark.parametrize("tread_first", [True, False])
def test_geom_name_clashing_with_a_tread_name_is_an_error(tread_first):
    d = _hooked(name="arm.tread0")
    tread = {"cylinder": [0.05, 0.02], "tread": True}
    if tread_first:
        d["body"][1]["geom"].insert(0, tread)         # the tread is arm's geom 0: auto-named arm.tread0
        d["body"][1]["geom"][1]["name"] = "arm.tread0"
    else:
        d["body"][1]["geom"].insert(0, tread)
        d["body"][0]["geom"].append({"box": [0.1, 0.1, 0.1], "name": "arm.tread0"})
        d["body"][1]["geom"][1]["name"] = "hook0.tip"
    with pytest.raises(PartError, match=r"geom name 'arm.tread0' is the name of body 'arm''s tread geom 0"):
        part_from_dict(d, "x.toml", None)


def test_limit_solref_round_trips_and_reaches_the_model(tmp_path):
    from scrimmage.parts.assemble import assemble
    d = minimal()
    d["body"][1]["joint"]["limit_solref"] = [0.004, 1.0]
    p = part_from_dict(d, "x.toml", None)
    assert p.bodies[1].joint.limit_solref == (0.004, 1.0)
    dump_part(p, tmp_path / "part.toml")
    assert same_part(load_part(tmp_path), p)
    m = assemble({"part": [{"name": "c", "path": "."}]}, tmp_path).model
    assert m.jnt_solref[m.joint("c.pivot").id] == pytest.approx([0.004, 1.0])


@pytest.mark.parametrize("joint, message", [
    ({"limit_solref": [0, 1]}, r"limit_solref must be \[timeconst, dampratio\], both greater than 0"),
    ({"limit_solref": [0.004, 1], "range": None}, r"limit_solref sets the range's stops: give the joint a range"),
])
def test_limit_solref_errors(joint, message):
    d = minimal()
    d["body"][1]["joint"].update(joint)
    if d["body"][1]["joint"]["range"] is None:
        del d["body"][1]["joint"]["range"]
    with pytest.raises(PartError, match=message):
        part_from_dict(d, "x.toml", None)
