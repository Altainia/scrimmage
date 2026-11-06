import base64
import re
import shutil
import struct
from pathlib import Path

import mujoco
import numpy as np
import pytest

from scrimmage.assembler import library, validate
from scrimmage.assembler.view import model_view
from scrimmage.parts.model import quat_from_rpy
from scrimmage.parts.stock import REGISTRY, _load_all

ROOT = Path(__file__).resolve().parent.parent
SWERVE = (ROOT / "configs" / "swerve.toml").read_text()
KITBOT = (ROOT / "configs" / "kitbot.toml").read_text()


def check(text, tmp_path):
    return validate.check(text, tmp_path / "robot.toml")


CHASSIS = '[[part]]\nname = "drive"\nstock = "chassis"\nids = { gyro = "Pigeon2:1" }\n'


def swerve_with(tmp_path, old, new):
    assert old in SWERVE
    return check(SWERVE.replace(old, new, 1), tmp_path)


def fields(res):
    return [e.field for e in res.errors]


def test_valid_config_ok_and_no_prefix(tmp_path):
    res = check(SWERVE.replace("../examples/java_swerve", "."), tmp_path)
    assert res.ok and res.errors == [] and res.world is not None


def test_toml_syntax_error_is_status_without_path(tmp_path):
    res = check("[[part]\n", tmp_path)
    assert not res.ok and fields(res) == ["status"]
    assert not res.errors[0].message.startswith(str(tmp_path))


def test_unknown_slot(tmp_path):
    res = swerve_with(tmp_path, 'encoder = "CANcoder:9"', 'encoder = "CANcoder:9", turret = "TalonFX:20"')
    assert fields(res) == ["fl/ids:turret"]
    assert "no slot 'turret'" in res.errors[0].message


def test_wrong_kind_id(tmp_path):
    res = swerve_with(tmp_path, 'encoder = "CANcoder:9"', 'encoder = "TalonFX:9"')
    assert fields(res) == ["fl/ids:encoder"]


def test_bad_id_format_and_ids_not_table(tmp_path):
    res = swerve_with(tmp_path, 'encoder = "CANcoder:9"', 'encoder = "nine"')
    assert fields(res) == ["fl/ids:encoder"]
    res = check(CHASSIS.replace('ids = { gyro = "Pigeon2:1" }', 'ids = 3'), tmp_path)
    assert fields(res) == ["drive/ids"]


def test_bad_attach(tmp_path):
    res = swerve_with(tmp_path, 'attach = "drive.front_left"', 'attach = "drive.nowhere"')
    assert fields(res) == ["fl/attach"]
    res = swerve_with(tmp_path, 'attach = "drive.front_left"', 'attach = "ghost.top"')
    assert fields(res) == ["fl/attach"]
    res = swerve_with(tmp_path, 'attach = "drive.front_left"', 'attach = "drive"')
    assert fields(res) == ["fl/attach"]


def test_unknown_parameter(tmp_path):
    res = swerve_with(tmp_path, 'ratio = "L2"\nattach = "drive.front_left"', 'ratio = "L2"\nbogus = 1\nattach = "drive.front_left"')
    assert fields(res) == ["fl/param:bogus"]
    assert "unknown parameter 'bogus'" in res.errors[0].message


def test_out_of_range_parameter(tmp_path):
    res = swerve_with(tmp_path, 'module_inset = 0.0579', 'module_inset = 5.0')
    assert fields(res) == ["drive/param:module_inset"]
    res = swerve_with(tmp_path, 'ratio = "L2"\nattach = "drive.front_left"', 'ratio = "L9"\nattach = "drive.front_left"')
    assert fields(res) == ["fl/param:ratio"]
    res = check(CHASSIS + "mass = -3\n", tmp_path)
    assert fields(res) == ["drive/param:mass"]


def test_name_errors(tmp_path):
    res = swerve_with(tmp_path, 'name = "fl"', 'name = "f l"')
    assert fields(res) == ["f l/name"]
    res = swerve_with(tmp_path, 'name = "fr"', 'name = "fl"')
    assert fields(res) == ["fl/name"]


def test_other_part_error_maps_to_part(tmp_path):
    res = swerve_with(tmp_path, 'stock = "swerve/MK4i"\nratio = "L2"\nattach = "drive.front_left"',
                      'stock = "nonesuch"\nattach = "drive.front_left"')
    assert fields(res) == ["fl/part"]


def test_duplicate_device_across_parts(tmp_path):
    res = swerve_with(tmp_path, 'CANcoder:10', 'CANcoder:9')
    assert not res.ok
    assert sorted(fields(res)) == ["fl/ids:encoder", "fr/ids:encoder"]
    assert all("is bound by" in e.message for e in res.errors)


def test_missing_id_warning(tmp_path):
    res = swerve_with(tmp_path, ', encoder = "CANcoder:9"', "")
    assert res.ok
    assert [w.field for w in res.warnings] == ["fl/ids:encoder"]
    assert "has no device ID" in res.warnings[0].message


def test_world_level_error_is_status(tmp_path):
    res = check(SWERVE + "\n[[motor]]\nname='x'\n", tmp_path)
    assert fields(res) == ["status"]


def test_other_warning_is_status():
    assert validate._map_warning("something odd").field == "status"


# ---------------------------------------------------------------------------------------------- library

def test_stock_slots_carry_their_kinds():
    lib = {s["name"]: s for s in library.stock()}
    assert lib["arm"]["slots"] == {"pivot": "motor", "encoder": "encoder"}
    assert lib["chassis"]["slots"] == {"gyro": "gyro"}
    assert lib["differential"]["slots"] == {"left": "motor", "right": "motor",
                                            "left_encoder": "encoder", "right_encoder": "encoder"}
    assert lib["swerve/MK4i"]["slots"] == {"drive": "motor", "steer": "motor", "encoder": "encoder"}


def test_stock_lists_registry_with_param_metadata():
    _load_all()
    lib = {s["name"]: s for s in library.stock()}
    assert set(lib) == set(REGISTRY)
    groups = {n: lib[n]["group"] for n in lib}
    assert groups["chassis"] == "Chassis" and groups["swerve/MK4i"] == "Drivetrain"
    assert groups["differential"] == "Drivetrain" and groups["arm"] == "Mechanisms"
    for name, s in lib.items():
        reg = REGISTRY[name]
        assert list(s["slots"]) == list(reg.slots) and s["mounts"] == list(reg.mounts)
        assert [p["name"] for p in s["params"]] == [p.name for p in reg.params]
        for meta, p in zip(s["params"], reg.params):
            assert meta["kind"] == p.kind and meta["unit"] == p.unit and meta["doc"] == p.shown_doc()
            assert meta["positive"] == p.positive and meta["min"] == p.min
            assert meta["max_depends"] == callable(p.max)
            assert meta["max"] == (None if callable(p.max) else p.max)
    ratio = next(p for p in lib["swerve/MK4i"]["params"] if p["name"] == "ratio")
    assert ratio["kind"] == "ratio" and "L2" in ratio["choices"] and ratio["default"] in ratio["choices"]
    inset = next(p for p in lib["chassis"]["params"] if p["name"] == "module_inset")
    assert inset["max_depends"] is True and inset["max"] is None


def test_folders_on_tests_parts_and_broken(tmp_path):
    out = library.folders([ROOT / "tests" / "parts"], ROOT / "tests")
    assert [f["path"] for f in out] == ["parts/intake"]
    f = out[0]
    assert f["name"] == "intake" and f["error"] is None
    assert f["slots"] == {"roller": "motor"} and f["mounts"] == ["top"]
    bad = tmp_path / "parts" / "broken"
    bad.mkdir(parents=True)
    (bad / "part.toml").write_text("[part\n")
    out = library.folders([tmp_path / "parts"], tmp_path)
    assert out[0]["path"] == "parts/broken" and out[0]["error"] and out[0]["slots"] == {}


def test_folders_dedupe_and_survive_unreadable_dirs(tmp_path):
    good = tmp_path / "parts" / "intake"
    shutil.copytree(ROOT / "tests" / "parts" / "intake", good)
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        out = library.folders([tmp_path, tmp_path / "parts", tmp_path / "parts", locked, good], tmp_path)
    finally:
        locked.chmod(0o755)
    assert [f["path"] for f in out] == ["parts/intake"]


# ---------------------------------------------------------------------------------------------- view

def swerve_world(tmp_path):
    res = check(SWERVE.replace("../examples/java_swerve", "."), tmp_path)
    assert res.ok
    return res.world


def test_model_view_swerve(tmp_path):
    world = swerve_world(tmp_path)
    v = model_view(world)
    assert v["instances"] == ["drive", "fl", "fr", "bl", "br"]
    assert v["meshes"] == []
    assert {g["instance"] for g in v["geoms"]} == set(v["instances"])
    assert all(g["type"] in {"box", "cylinder", "sphere", "capsule", "ellipsoid", "mesh", "plane"} for g in v["geoms"])
    mounts = {m["name"]: m for m in v["mounts"]}
    # independent: robot at (2, 4.035) heading 0, chassis 0.7 m square, modules inset 0.0579 -> +-0.2921
    fl = mounts["drive.front_left"]
    inst = {i.name: i for i in world.parts.instances}["drive"]
    mt = next(m for m in inst.part.mounts if m.name == "front_left")
    body = world.parts.model.body("drive." + mt.body)
    assert body is not None
    expect = np.array([2.0, 4.035, 0.0]) + np.array(mt.pos)    # root pos = 0, heading 0, root body at its origin
    assert np.allclose(fl["pos"], expect + np.array(inst.part.bodies[0].pos), atol=1e-6)
    assert fl["pos"][0] > 2.0 and fl["pos"][1] > 4.035          # front-left: +x, +y
    assert abs(abs(fl["pos"][0] - 2.0) - 0.2921) < 1e-3 and abs(abs(fl["pos"][1] - 4.035) - 0.2921) < 1e-3
    # fl module geoms cluster around that corner
    flg = [np.array(g["pos"]) for g in v["geoms"] if g["instance"] == "fl"]
    assert flg and all(np.linalg.norm((p - np.array(fl["pos"]))[:2]) < 0.2 for p in flg)
    # assembler-composed pose of the fl module body equals the mount pose
    fl_body = world.parts.model.body("fl." + next(iter(world.parts.instances[1].part.bodies)).name)
    d = mujoco.MjData(world.model)
    mujoco.mj_forward(world.model, d)
    assert np.allclose(d.xpos[fl_body.id][:2], fl["pos"][:2], atol=1e-6)
    assert np.isclose(abs(np.dot(d.xquat[fl_body.id], fl["quat"])), 1.0, atol=1e-6)
    assert v["floor"]["size"][0] > 1


def test_mount_pose_with_rotated_root_and_rotated_mount(tmp_path):
    part = tmp_path / "p"
    part.mkdir()
    (part / "part.toml").write_text(
        '[part]\nname = "p"\nformat = 1\n[[body]]\nname = "b"\nmass = 1\npos = [0.1, 0, 0]\nrot = [0, 0, 30]\n'
        'geom = [{ box = [0.2, 0.2, 0.2] }]\n[[mount]]\nname = "m"\nbody = "b"\npos = [0.3, 0.1, 0]\nrot = [0, 0, 90]\n')
    text = '[robot]\nstart_pose = [1.0, 2.0, 90.0]\n[[part]]\nname = "p"\npath = "p"\nrot = [0, 0, 45]\n'
    res = check(text, tmp_path)
    assert res.ok, res.errors
    m = model_view(res.world)["mounts"][0]
    # independent composition: world = frame(start pose heading) * root rot * body(pos, rot) * mount(pos, rot)
    def R(deg):
        c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    root_R = R(90) @ R(45)
    body_R = root_R @ R(30)
    pos = np.array([1.0, 2.0, 0]) + root_R @ np.array([0.1, 0, 0]) + body_R @ np.array([0.3, 0.1, 0])
    # the body is a root with its own pos; the root part's body pos is inside the root frame
    assert np.allclose(m["pos"], pos, atol=1e-6), (m["pos"], pos)
    yaw = np.radians(90 + 45 + 30 + 90)
    q = np.array([np.cos(yaw / 2), 0, 0, np.sin(yaw / 2)])
    assert np.isclose(abs(np.dot(q, m["quat"])), 1.0, atol=1e-6)


def stl_bytes(tris):
    out = b"\0" * 80 + struct.pack("<I", len(tris))
    for t in tris:
        out += struct.pack("<3f", 0, 0, 0) + b"".join(struct.pack("<3f", *v) for v in t) + b"\0\0"
    return out


def test_model_view_mesh_part(tmp_path):
    part = tmp_path / "wedge"
    part.mkdir()
    # a tetrahedron, 4 vertices, 4 faces (mm-free meters)
    a, b, c, d = (0, 0, 0), (0.1, 0, 0), (0, 0.1, 0), (0, 0, 0.1)
    (part / "wedge.stl").write_bytes(stl_bytes([(a, c, b), (a, b, d), (a, d, c), (b, c, d)]))
    (part / "part.toml").write_text('[part]\nname = "wedge"\nformat = 1\n[[body]]\nname = "b"\nmass = 1\n'
                                    'geom = [{ mesh = "wedge.stl" }]\n')
    res = check('[[part]]\nname = "w"\npath = "wedge"\n', tmp_path)
    assert res.ok, res.errors
    v = model_view(res.world)
    m = res.world.model
    assert len(v["meshes"]) == 1 and m.nmesh == 1
    verts = np.frombuffer(base64.b64decode(v["meshes"][0]["vert"]), "<f4").reshape(-1, 3)
    faces = np.frombuffer(base64.b64decode(v["meshes"][0]["face"]), "<i4").reshape(-1, 3)
    assert len(verts) == m.mesh_vertnum[0] == 4 and len(faces) == m.mesh_facenum[0] == 4
    assert faces.max() < len(verts)
    g = next(g for g in v["geoms"] if g["type"] == "mesh")
    assert g["mesh"] == 0
    # world vertices (quat * vert + pos) equal MuJoCo's own transform of the mesh
    gid = next(i for i in range(m.ngeom) if m.geom_type[i] == mujoco.mjtGeom.mjGEOM_MESH)
    dd = mujoco.MjData(m)
    mujoco.mj_forward(m, dd)
    want = dd.geom_xpos[gid] + verts.astype(float) @ dd.geom_xmat[gid].reshape(3, 3).T
    q = np.array(g["quat"])
    mat = np.zeros(9)
    mujoco.mju_quat2Mat(mat, q)
    got = np.array(g["pos"]) + verts.astype(float) @ mat.reshape(3, 3).T
    assert np.allclose(got, want, atol=1e-5)
