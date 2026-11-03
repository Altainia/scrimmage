"""Building part folders from an import: frames, mass, geoms, collision, joints, slots, parts, and the folder swap."""

import math
import tomllib
from pathlib import Path

import mujoco
import numpy as np
import pytest

from scrimmage.bench.spec import BenchError
from scrimmage.cad import CadError, build
from scrimmage.cad.axis import fit_axis
from scrimmage.cad.build import build_parts, write_parts
from scrimmage.cad.edits import apply_edit
from scrimmage.cad.fit import fit_box, fit_wheel, rot_z_to
from scrimmage.cad.frame import to_robot
from scrimmage.cad.settings import JointSettings, new_import, reimport
from scrimmage.cad.source import Piece, Source, load_source
from scrimmage.parts.assemble import assemble, part_spec
from scrimmage.parts.model import load_part, quat_from_rpy

DATA = Path(__file__).parent / "data" / "cad"
HOOK = "Climber/hook_assy/hook"


@pytest.fixture(scope="module")
def src():
    return load_source(DATA / "climber.step")


def make(src, tmp_path, step="climber.step"):
    return new_import(src, "climber", DATA / step, tmp_path)


def edit(imp, op, **args):
    return apply_edit(imp, op, args)[0]


def piece(src, key):
    return next(p for p in src.pieces if p.key == key)


def side_triangle(p: Piece) -> int:
    """A triangle on the hook's round side (its normal is perpendicular to the hook's x axis)."""
    tv = p.v[p.f]
    n = np.cross(tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0])
    n /= np.linalg.norm(n, axis=1)[:, None]
    return int(np.argmin(np.abs(n[:, 0])))


def hinge_on_hook(imp, src, parent="arm_A"):
    point, d = fit_axis(piece(src, HOOK), side_triangle(piece(src, HOOK)), "round")
    joint = {"kind": "hinge", "anchor": list(point), "axis": list(d)}
    return edit(imp, "set_joint", body="hook_assy", parent=parent, joint=joint), point, d


def write(imp, src, tmp_path):
    result = build_parts(imp, src)
    return result, write_parts(result, imp, tmp_path / "climber.import.toml")


def world_verts(m, d, geom_id):
    mesh = m.geom_dataid[geom_id]
    a, n = m.mesh_vertadr[mesh], m.mesh_vertnum[mesh]
    return m.mesh_vert[a:a + n] @ d.geom_xmat[geom_id].reshape(3, 3).T + d.geom_xpos[geom_id]


# ---------------------------------------------------------------- fit.py

def test_fit_wheel_and_box():
    rng = np.random.default_rng(0)
    ang = rng.uniform(0, 2 * np.pi, 200)
    along = rng.uniform(-0.01, 0.03, 200)
    v = np.stack([along, 0.05 * np.cos(ang), 0.05 * np.sin(ang)], 1)
    r, length, center = fit_wheel(v, np.array([2.0, 0, 0]))
    assert r == pytest.approx(0.05) and length == pytest.approx(along.max() - along.min())
    assert center == pytest.approx([(along.max() + along.min()) / 2, 0, 0])

    corners = np.array([[x, y, z] for x in (0, 0.4) for y in (0, 0.1) for z in (0, 0.02)])
    size, c, rpy = fit_box(corners)
    assert sorted(size) == pytest.approx([0.02, 0.1, 0.4]) and c == pytest.approx([0.2, 0.05, 0.01])
    # a box turned 30 degrees about z is found turned
    rz = np.array([[math.cos(0.5236), -math.sin(0.5236), 0], [math.sin(0.5236), math.cos(0.5236), 0], [0, 0, 1]])
    size, c, rpy = fit_box(corners @ rz.T + [1, 2, 3])
    assert sorted(size) == pytest.approx([0.02, 0.1, 0.4], abs=1e-9)
    assert c == pytest.approx(rz @ [0.2, 0.05, 0.01] + [1, 2, 3])
    w, x, y, z = quat_from_rpy(rpy)
    q = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    local = (corners @ rz.T + [1, 2, 3] - c) @ q            # corners in the fitted box's own axes
    assert np.abs(local).max(axis=0) == pytest.approx(np.array(size) / 2, abs=1e-9)


def test_rot_z_to_points_z_along_the_normal():
    for n in ([0, 0, 1], [0, 0, -1], [1, 0, 0], [0, -1, 0], [1, 1, 1]):
        w, x, y, z = quat_from_rpy(rot_z_to(np.array(n, float)))
        zcol = [2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)]
        assert zcol == pytest.approx(np.array(n) / np.linalg.norm(n), abs=1e-12)


# ---------------------------------------------------------------- building

def test_default_import_builds_one_welded_part(tmp_path, src):
    imp = make(src, tmp_path)
    result, paths = write(imp, src, tmp_path)
    assert list(result.parts) == ["climber"] and paths == [tmp_path / "climber"]
    part = load_part(paths[0])
    assert [b.name for b in part.bodies] == ["base_plate"]
    (b,) = part.bodies
    assert b.geoms[0].shape == "mesh" and b.geoms[0].size == "meshes/base_plate.obj" and not b.geoms[0].collide
    assert b.com is not None and b.inertia is not None and b.inertia_rot is not None
    part_spec(part).compile()
    marker = tomllib.loads((paths[0] / "import-origin.toml").read_text())
    assert marker == {"import": "../climber.import.toml", "import_id": imp.import_id,
                      "origins": {"base_plate": [0.0, 0.0, 0.0]}}
    # every piece is in the one visual mesh
    v, f = result.parts["climber"].meshes["meshes/base_plate.obj"]
    assert len(f) == sum(len(p.f) for p in src.pieces)


def test_mass_matches_aluminum_volume(tmp_path, src):
    trimesh = pytest.importorskip("trimesh")
    imp = make(src, tmp_path)
    built = build_parts(imp, src).parts["climber"]
    meshes = [trimesh.Trimesh(p.v, p.f, process=False) for p in src.pieces]
    vols = [abs(m.volume) for m in meshes]
    assert built.mass == pytest.approx(2700 * sum(vols), rel=1e-9)
    assert built.part.bodies[0].mass == pytest.approx(built.mass)
    R, t = to_robot(imp.frame)
    com_cad = sum(v * m.center_mass for v, m in zip(vols, meshes)) / sum(vols)
    assert np.allclose(built.com, R @ com_cad + t, atol=1e-6)


def test_hinge_hook_moves_about_axis(tmp_path, src):
    imp, point, d = hinge_on_hook(make(src, tmp_path), src)
    result, paths = write(imp, src, tmp_path)
    part = load_part(paths[0])
    # arm_A is welded to base_plate, so the hook hangs on base_plate
    assert [(b.name, b.parent) for b in part.bodies] == [("base_plate", None), ("hook_assy", "base_plate")]
    R, t = to_robot(imp.frame)
    hook = part.bodies[1]
    assert hook.pos == pytest.approx(R @ point + t) and hook.rot == (0, 0, 0)
    assert hook.joint.name == "hook_assy" and hook.joint.kind == "hinge"
    assert hook.joint.axis == pytest.approx(R @ d)
    assert result.parts["climber"].origins["hook_assy"] == pytest.approx(R @ point + t)

    m = part_spec(part).compile()
    data = mujoco.MjData(m)
    mujoco.mj_forward(m, data)
    g = next(i for i in range(m.ngeom) if m.geom_bodyid[i] == mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY,
                                                                              "hook_assy"))
    before = world_verts(m, data, g)
    # at zero the mesh is where the CAD has it
    hook_robot = piece(src, HOOK).v @ R.T + t
    assert np.abs(np.sort(before, axis=0) - np.sort(hook_robot, axis=0)).max() < 1e-6
    data.qpos[m.jnt_qposadr[0]] = math.radians(30)
    mujoco.mj_forward(m, data)
    after = world_verts(m, data, g)
    a = R @ d
    c = R @ point + t
    k = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    rot = np.eye(3) + math.sin(math.radians(30)) * k + (1 - math.cos(math.radians(30))) * k @ k
    p = before[np.argmax(before @ [0.1, 1, 3])]             # one vertex, off the axis
    assert np.linalg.norm(after - (rot @ (p - c) + c), axis=1).min() < 1e-6
    assert np.linalg.norm(p - c - (p - c) @ a * a) > 0.005


def test_slide_motor_gearing_and_encoder_ratio(tmp_path, src):
    imp = make(src, tmp_path)
    imp = edit(imp, "set_joint", body="arm_B", parent="base_plate",
               joint={"kind": "slide", "anchor": [0.24, 0.1, 0.01], "axis": [0, 0, 1], "range": [0, 0.5]})
    imp = edit(imp, "set_motor", body="arm_B", settings={"motor": "NEO", "ratio": 25, "spool_diameter": 0.0254})
    imp = edit(imp, "set_encoder", body="arm_B", settings={"on": "motor"})
    part = build_parts(imp, src).parts["climber"].part
    (motor,) = part.motors
    assert (motor.slot, motor.joint, motor.motor) == ("arm_B_motor", "arm_B", "NEO")
    assert motor.gearing == pytest.approx(25 / 0.0127)
    (enc,) = part.encoders
    assert (enc.slot, enc.joint) == ("arm_B_encoder", "arm_B")
    assert enc.ratio == pytest.approx(25 / (math.pi * 0.0254))
    arm = next(b for b in part.bodies if b.name == "arm_B")
    assert arm.joint.kind == "slide" and arm.joint.range == (0, 0.5)

    imp = edit(imp, "set_encoder", body="arm_B", settings={"on": "joint"})
    assert build_parts(imp, src).parts["climber"].part.encoders[0].ratio == 1.0
    hinged = hinge_on_hook(imp, src)[0]
    hinged = edit(hinged, "set_motor", body="hook_assy", settings={"motor": "NEO", "ratio": 9})
    hinged = edit(hinged, "set_encoder", body="hook_assy", settings={"on": "motor"})
    hinged = edit(hinged, "set_gyro", body="arm_A", on=True)
    part = build_parts(hinged, src).parts["climber"].part
    hm = next(s for s in part.motors if s.joint == "hook_assy")
    he = next(s for s in part.encoders if s.joint == "hook_assy")
    assert hm.gearing == 9 and he.ratio == 9
    # arm_A is welded to base_plate: its gyro reads base_plate
    assert [(g.slot, g.body) for g in part.gyros] == [("arm_A_gyro", "base_plate")]


def test_total_mass_scales_computed_not_typed(tmp_path, src):
    imp = make(src, tmp_path)
    imp, _, _ = hinge_on_hook(imp, src)
    imp = edit(imp, "set_joint", body="arm_B", parent="base_plate",
               joint={"kind": "hinge", "anchor": [0.24, 0.1, 0.01], "axis": [0, 1, 0]})
    imp = edit(imp, "set_mass", body="hook_assy", kg=0.5)
    imp = edit(imp, "set_mass", piece="Climber/arm_B", kg=0.1)
    base = build_parts(imp, src).parts["climber"]
    imp = edit(imp, "set_total", part="climber", kg=10.0)
    result = build_parts(imp, src)
    assert not result.messages
    built = result.parts["climber"]
    assert built.mass == pytest.approx(10.0)
    bodies = {b.name: b for b in built.part.bodies}
    assert bodies["hook_assy"].mass == pytest.approx(0.5) and bodies["arm_B"].mass == pytest.approx(0.1)
    assert bodies["base_plate"].mass == pytest.approx(9.4)
    # the scaled base keeps its center of mass, and its inertia scales with its mass
    b0 = {b.name: b for b in base.part.bodies}["base_plate"]
    assert bodies["base_plate"].com == pytest.approx(b0.com, abs=1e-12)
    assert np.array(bodies["base_plate"].inertia) == pytest.approx(np.array(b0.inertia) * 9.4 / b0.mass)
    assert bodies["base_plate"].inertia_rot == pytest.approx(b0.inertia_rot)

    imp = edit(imp, "set_total", part="climber", kg=0.3)
    result = build_parts(imp, src)
    assert any("more than" in m and "0.3" in m for m in result.messages)
    bodies = {b.name: b.mass for b in result.parts["climber"].part.bodies}
    assert bodies["hook_assy"] == pytest.approx(0.5) and bodies["base_plate"] == pytest.approx(b0.mass)


def test_typed_piece_mass_keeps_its_com(tmp_path, src):
    imp = make(src, tmp_path)
    imp, _, _ = hinge_on_hook(imp, src)
    imp = edit(imp, "set_mass", piece=HOOK, kg=2.0)
    hook = {b.name: b for b in build_parts(imp, src).parts["climber"].part.bodies}["hook_assy"]
    plain = {b.name: b for b in build_parts(hinge_on_hook(make(src, tmp_path), src)[0], src)
             .parts["climber"].part.bodies}["hook_assy"]
    assert hook.mass == pytest.approx(2.0)
    assert hook.com == pytest.approx(plain.com, abs=1e-12)
    assert np.array(hook.inertia) == pytest.approx(np.array(plain.inertia) * 2.0 / plain.mass)


def test_y_up_part_stands_on_floor(tmp_path):
    src = load_source(DATA / "climber_yup.step")
    imp = make(src, tmp_path, "climber_yup.step")
    lo = min(float(p.v[:, 1].min()) for p in src.pieces)
    imp = edit(imp, "set_frame", up="+y", front="+x", floor=lo)
    _, paths = write(imp, src, tmp_path)
    b = assemble({"part": [{"name": "climber", "path": str(paths[0])}],
                  "robot": {"start_pose": [2.0, 4.0, 0.0]}}, tmp_path)
    m = b.model
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    root = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "climber.base_plate")
    z = np.concatenate([world_verts(m, d, g)[:, 2] for g in range(m.ngeom)
                        if m.geom_bodyid[g] == root and m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH])
    assert z.min() == pytest.approx(0.0, abs=1e-6)
    assert z.max() == pytest.approx(0.826, abs=1e-3)     # the hook top, 826 mm up


def test_attach_to_stock_chassis_lands_where_cad_says(tmp_path, src):
    imp = make(src, tmp_path)
    anchor = [0.06, 0.1, 0.01]
    imp = edit(imp, "set_joint", body="arm_A", parent="base_plate",
               joint={"kind": "hinge", "anchor": anchor, "axis": [0, 1, 0]})
    _, paths = write(imp, src, tmp_path)
    b = assemble({"part": [{"name": "drive", "stock": "chassis"},
                           {"name": "climber", "path": str(paths[0]), "attach": "drive.chassis"}],
                  "robot": {"start_pose": [2.0, 4.0, 90.0]}}, tmp_path)
    m = b.model
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    chassis = d.xpos[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "drive.chassis")]
    arm = d.xpos[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "climber.arm_A")]
    R, t = to_robot(imp.frame)
    x, y, z = R @ anchor + t
    assert arm - chassis == pytest.approx([-y, x, z], abs=1e-9)     # yawed 90 degrees: robot +x is world +y


def test_wheel_collision_cylinder_dimensions(tmp_path, src):
    imp, point, d = hinge_on_hook(make(src, tmp_path), src)
    imp = edit(imp, "set_collision", body="hook_assy", mode="wheel")
    hook = {b.name: b for b in build_parts(imp, src).parts["climber"].part.bodies}["hook_assy"]
    (cyl,) = [g for g in hook.geoms if g.collide]
    assert cyl.shape == "cylinder" and cyl.axis == "x" and cyl.rot == (0, 0, 0)
    assert cyl.size == pytest.approx((0.008, 0.06), abs=1e-6)
    assert cyl.pos == pytest.approx((0, 0, 0), abs=1e-6)       # the axis point is the hook's middle
    assert cyl.friction == 1.2 and cyl.condim == 4

    # a welded body can't be a wheel: it gets a box and a message
    imp = edit(imp, "set_collision", body="arm_A", mode="wheel")
    result = build_parts(imp, src)
    base = {b.name: b for b in result.parts["climber"].part.bodies}["base_plate"]
    (box,) = [g for g in base.geoms if g.collide]
    assert box.shape == "box" and sorted(box.size) == pytest.approx([0.02, 0.02, 0.8], abs=1e-6)
    assert box.pos == pytest.approx((0.06 - 0.15, 0.1 - 0.1, 0.41), abs=1e-6)
    assert any("arm_A" in msg and "hinge" in msg for msg in result.messages)


def test_shaped_collision_uses_convex_pieces(tmp_path, src):
    pytest.importorskip("coacd")
    imp = edit(make(src, tmp_path), "set_collision", body="base_plate", mode="shaped")
    result, paths = write(imp, src, tmp_path)
    base = result.parts["climber"].part.bodies[0]
    collide = [g for g in base.geoms if g.collide]
    assert len(collide) > 1 and all(g.shape == "mesh" for g in collide)
    for g in collide:
        assert (paths[0] / g.size).is_file()
    part_spec(load_part(paths[0])).compile()


def test_shaped_without_coacd_falls_back_with_message(monkeypatch, tmp_path, src):
    def no_coacd(path, units):
        raise BenchError(f"mesh {path} is concave, so it has to be split into convex pieces, which needs coacd: "
                         "pip install scrimmage[bench].")
    monkeypatch.setattr("scrimmage.bench.cad.convex_pieces", no_coacd)
    imp = edit(make(src, tmp_path), "set_collision", body="base_plate", mode="shaped")
    result = build_parts(imp, src)
    (box,) = [g for g in result.parts["climber"].part.bodies[0].geoms if g.collide]
    assert box.shape == "box"
    assert any("coacd" in m and "base_plate" in m for m in result.messages)


def test_simple_collision_is_a_fitted_box(tmp_path, src):
    imp = edit(make(src, tmp_path), "set_collision", body="base_plate", mode="simple")
    base = build_parts(imp, src).parts["climber"].part.bodies[0]
    (box,) = [g for g in base.geoms if g.collide]
    # the plate and the bolts standing on it (welded to it): the bolts reach 30 mm up
    assert box.shape == "box" and sorted(box.size) == pytest.approx([0.03, 0.2, 0.3], abs=1e-6)
    assert box.rot == (0, 0, 0) and box.pos == pytest.approx((0, 0, 0.015), abs=1e-6)


def test_zero_volume_body_gets_placeholder_and_message(tmp_path, src):
    open_src = Source(src.kind, src.path, src.units,
                      [Piece(p.key, p.name, p.group, p.v, p.f[: len(p.f) // 2] if p.key == HOOK else p.f)
                       for p in src.pieces], src.groups)
    imp, _, _ = hinge_on_hook(new_import(open_src, "climber", DATA / "climber.step", tmp_path), src)
    result, paths = write(imp, open_src, tmp_path)
    hook = {b.name: b for b in result.parts["climber"].part.bodies}["hook_assy"]
    assert hook.mass == 0.001
    assert any("hook_assy" in m and "0.001" in m for m in result.messages)
    part_spec(load_part(paths[0])).compile()


def test_mounts_couplings_and_loops(tmp_path, src):
    imp = make(src, tmp_path)
    imp = edit(imp, "set_joint", body="arm_A", parent="base_plate",
               joint={"kind": "hinge", "anchor": [0.06, 0.1, 0.01], "axis": [0, 1, 0]})
    imp = edit(imp, "set_joint", body="arm_B", parent="base_plate",
               joint={"kind": "hinge", "anchor": [0.24, 0.1, 0.01], "axis": [0, 1, 0]})
    imp = edit(imp, "add_mount", body="arm_A", name="tip", point=[0.06, 0.1, 0.81], normal=[1, 0, 0])
    imp = edit(imp, "add_coupling", a="arm_A", b="arm_B", ratio=-1)
    imp = edit(imp, "add_loop", body1="arm_A", body2="hook_assy", point=[0.06, 0.1, 0.81])
    part = build_parts(imp, src).parts["climber"].part
    (mount,) = part.mounts
    assert (mount.name, mount.body) == ("tip", "arm_A")
    assert mount.pos == pytest.approx((0, 0, 0.8))
    assert mount.rot == pytest.approx(rot_z_to(np.array([1.0, 0, 0])))
    (c,) = part.couplings
    assert c.joints == ("arm_A", "arm_B") and c.ratio == -1
    (lp,) = part.loops
    # hook_assy is welded to base_plate, so the loop pins arm_A to base_plate
    assert (lp.body1, lp.body2) == ("arm_A", "base_plate") and lp.pos == pytest.approx((0, 0, 0.8))


def test_second_part_and_message(tmp_path, src):
    imp = edit(make(src, tmp_path), "set_part", body="arm_B", part="right_arm")
    result, paths = write(imp, src, tmp_path)
    assert sorted(result.parts) == ["climber", "right_arm"]
    assert sorted(p.name for p in paths) == ["climber", "right_arm"]
    right = load_part(tmp_path / "right_arm")
    assert [b.name for b in right.bodies] == ["arm_B"] and right.bodies[0].pos == (0, 0, 0)
    assert "arm_B" not in [b.name for b in load_part(tmp_path / "climber").bodies]
    assert result.parts["right_arm"].origins == {"arm_B": [0.0, 0.0, 0.0]}
    assert not result.messages

    # a part root that still names a parent (and a joint): the joint is dropped, with a message
    imp.bodies["arm_B"].parent = "base_plate"
    imp.bodies["arm_B"].joint = JointSettings("hinge", [0.24, 0.1, 0.01], [0, 1, 0])
    imp, _, _ = hinge_on_hook(imp, src, parent="arm_B")      # a body under a part root belongs to that part
    result = build_parts(imp, src)
    assert "arm_B is its own part; attach it to base_plate in the robot" in result.messages
    right = result.parts["right_arm"].part
    assert [b.name for b in right.bodies] == ["arm_B", "hook_assy"] and right.bodies[0].joint is None
    assert "hook_assy" not in [b.name for b in result.parts["climber"].part.bodies]


def test_marker_says_what_a_part_hangs_on(tmp_path, src):
    """A part split off a body of another part says so in its import-origin.toml (attach = "<part>.<body>", the
    part body that holds it); a part the robot carries has no attach."""
    imp = edit(make(src, tmp_path), "set_joint", body="arm_B", parent="base_plate",
               joint={"kind": "hinge", "anchor": [0.24, 0.1, 0.01], "axis": [0, 1, 0]})
    imp = edit(imp, "set_joint", body="hook_assy_2", parent="arm_B", joint=None)
    imp = edit(imp, "set_part", body="hook_assy_2", part="right_arm")
    imp = edit(imp, "set_part", body="arm_A", part="left_arm")       # hung on base_plate, welded: its part body
    result, _ = write(imp, src, tmp_path)
    assert result.parts["right_arm"].attach == "climber.arm_B" and result.parts["left_arm"].attach == "climber.base_plate"
    assert result.parts["climber"].attach is None and not result.messages
    marker = lambda n: tomllib.loads((tmp_path / n / "import-origin.toml").read_text())
    assert marker("right_arm")["attach"] == "climber.arm_B" and "attach" not in marker("climber")
    load_part(tmp_path / "right_arm")      # part.toml is unchanged in form


def test_refuses_foreign_folder(tmp_path, src):
    imp = make(src, tmp_path)
    (tmp_path / "climber").mkdir()
    (tmp_path / "climber" / "notes.txt").write_text("mine")
    with pytest.raises(CadError, match="already holds a part that didn't come from this import; pick another name"):
        write(imp, src, tmp_path)
    assert (tmp_path / "climber" / "notes.txt").read_text() == "mine"
    (tmp_path / "climber" / "import-origin.toml").write_text('import_id = "someone-else"\n')
    with pytest.raises(CadError, match="test_refuses_foreign_folder0/climber already holds"):
        write(imp, src, tmp_path)
    # an empty folder is fine, and so is rewriting this import's own folder
    (tmp_path / "climber" / "notes.txt").unlink()
    (tmp_path / "climber" / "import-origin.toml").unlink()
    write(imp, src, tmp_path)
    (tmp_path / "climber" / "extra.txt").write_text("stale")
    write(imp, src, tmp_path)
    assert not (tmp_path / "climber" / "extra.txt").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["climber"]       # no .tmp or .old folders left


def test_folder_resolves_team_folder(monkeypatch, tmp_path, src):
    cad = tmp_path / "team cad"
    monkeypatch.setenv("SCRIMMAGE_CAD", str(cad))
    imp = make(src, tmp_path / "robot")
    imp.parts["climber"].folder = "{cad}/arms/climber"
    (tmp_path / "robot").mkdir()
    paths = write_parts(build_parts(imp, src), imp, tmp_path / "robot" / "climber.import.toml")
    assert paths == [cad / "arms" / "climber"] and (cad / "arms" / "climber" / "part.toml").is_file()
    marker = tomllib.loads((cad / "arms" / "climber" / "import-origin.toml").read_text())
    assert marker["import"] == "../../../robot/climber.import.toml"


def test_reimport_after_body_vanishes_still_builds(tmp_path, src):
    imp = make(src, tmp_path)
    imp = edit(imp, "set_joint", body="arm_B", parent="base_plate",
               joint={"kind": "slide", "anchor": [0.24, 0.1, 0.01], "axis": [0, 0, 1]})
    imp = edit(imp, "set_motor", body="arm_B", settings={"motor": "NEO", "ratio": 5, "spool_diameter": 0.02})
    imp, _, _ = hinge_on_hook(imp, src)
    imp = edit(imp, "set_motor", body="hook_assy", settings={"motor": "NEO", "ratio": 9})
    smaller = Source(src.kind, src.path, src.units, [p for p in src.pieces if p.key != "Climber/arm_B"], src.groups)
    imp, messages = reimport(imp, smaller)
    assert any("arm_B is no longer in the CAD" in m for m in messages)
    result, paths = write(imp, smaller, tmp_path)
    part = load_part(paths[0])
    assert [b.name for b in part.bodies] == ["base_plate", "hook_assy"]
    assert [s.slot for s in part.motors] == ["hook_assy_motor"]
    part_spec(part).compile()


def test_inertia_and_its_axes_match_mujoco(tmp_path):
    """A turned L of two boxes: the written mass, com, inertia and inertia_rot give the same full inertia as MuJoCo
    computes from the solids themselves (so the principal axes' rpy convention is right)."""
    def box(lo, hi):
        v = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])], float)
        f = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],
                      [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
        return v, f
    q, _ = np.linalg.qr(np.random.default_rng(1).normal(size=(3, 3)))
    q *= np.sign(np.linalg.det(q))
    pieces = [Piece(k, k, (), v @ q.T + [0.1, 0.2, 0.3], f)
              for k, (v, f) in (("a", box([0, 0, 0], [0.3, 0.05, 0.02])), ("b", box([0, 0, 0.02], [0.04, 0.05, 0.2])))]
    src = Source("mesh", tmp_path / "x", "m", pieces, [])
    imp = new_import(src, "t", tmp_path / "x.stl", tmp_path)
    (folder,) = write_parts(build_parts(imp, src), imp, tmp_path / "t.import.toml")
    m = part_spec(load_part(folder)).compile()

    R, t = to_robot(imp.frame)
    spec = mujoco.MjSpec()
    body = spec.worldbody.add_body(name="ref")
    for i, p in enumerate(pieces):
        spec.add_mesh(name=f"m{i}", uservert=(p.v @ R.T + t).ravel().tolist(), userface=p.f.ravel().tolist())
        body.add_geom(type=mujoco.mjtGeom.mjGEOM_MESH, meshname=f"m{i}", density=2700)
    ref = spec.compile()

    def full(model, bid):
        d = mujoco.MjData(model)
        mujoco.mj_forward(model, d)
        r = d.ximat[bid].reshape(3, 3)
        return model.body_mass[bid], d.xipos[bid].copy(), r @ np.diag(model.body_inertia[bid]) @ r.T
    mine, theirs = full(m, 1), full(ref, 1)
    assert mine[0] == pytest.approx(theirs[0], rel=1e-6)
    assert np.abs(mine[1] - theirs[1]).max() < 1e-7
    assert np.abs(mine[2] - theirs[2]).max() < 1e-6 * np.abs(theirs[2]).max()


# ---------------------------------------------------------------- fix round 1

def test_unparted_body_removes_its_old_folder(tmp_path, src):
    import shutil
    imp = edit(make(src, tmp_path), "set_part", body="arm_B", part="right_arm")
    write(imp, src, tmp_path)
    assert imp.written == {"climber": "climber", "right_arm": "right_arm"}
    # a copy the student made has the same marker: it is never this import's to remove
    shutil.copytree(tmp_path / "right_arm", tmp_path / "right_arm_backup")
    imp = edit(imp, "set_part", body="arm_B", part=None, parent="base_plate")
    result, paths = write(imp, src, tmp_path)
    assert paths == [tmp_path / "climber"] and not (tmp_path / "right_arm").exists()
    assert result.messages == [f"{tmp_path.name}/right_arm was removed: arm_B is part of climber again"]
    assert (tmp_path / "right_arm_backup" / "part.toml").is_file()
    assert imp.written == {"climber": "climber"}

    # a part saved to another folder: the old folder goes
    imp.parts["climber"].folder = "lifter"
    result, paths = write(imp, src, tmp_path)
    assert paths == [tmp_path / "lifter"] and not (tmp_path / "climber").exists()
    assert result.messages == [f"{tmp_path.name}/climber was removed: part climber is now saved in "
                               f"{tmp_path.name}/lifter"]
    assert imp.written == {"climber": "lifter"}


def test_recorded_folder_now_owned_by_another_import_survives(tmp_path, src):
    imp = edit(make(src, tmp_path), "set_part", body="arm_B", part="right_arm")
    write(imp, src, tmp_path)
    marker = tmp_path / "right_arm" / "import-origin.toml"
    marker.write_text(marker.read_text().replace(imp.import_id, "another-import"))
    imp = edit(imp, "set_part", body="arm_B", part=None, parent="base_plate")
    result, _ = write(imp, src, tmp_path)
    assert (tmp_path / "right_arm" / "part.toml").is_file() and not result.messages
    # and a recorded folder that now holds a different part (same import) survives too
    import shutil
    shutil.copytree(tmp_path / "climber", tmp_path / "old_left")
    imp.written = {"left_arm": "old_left", "climber": "climber"}
    result, _ = write(imp, src, tmp_path)
    assert (tmp_path / "old_left" / "part.toml").is_file() and not result.messages


def test_folder_that_cant_be_removed_is_a_plain_message(monkeypatch, tmp_path, src):
    imp = edit(make(src, tmp_path), "set_part", body="arm_B", part="right_arm")
    write(imp, src, tmp_path)
    real = build.shutil.rmtree

    def stuck(path, *a, **k):
        if Path(path).name == "right_arm":
            raise OSError(None, None)
        return real(path, *a, **k)
    monkeypatch.setattr(build.shutil, "rmtree", stuck)
    imp = edit(imp, "set_part", body="arm_B", part=None, parent="base_plate")
    result, _ = write(imp, src, tmp_path)
    assert result.messages == [f"Couldn't remove {tmp_path.name}/right_arm, an old part from this import; delete it "
                               "yourself."]
    assert imp.written == {"right_arm": "right_arm", "climber": "climber"}       # tried again next time


def test_bodies_hanging_on_each_other_are_a_plain_error(tmp_path, src):
    imp = make(src, tmp_path)
    imp.bodies["arm_A"].parent = "arm_B"
    imp.bodies["arm_A"].joint = JointSettings("hinge", [0, 0, 0], [0, 1, 0])
    imp.bodies["arm_B"].parent = "arm_A"
    imp.bodies["arm_B"].joint = JointSettings("hinge", [0, 0, 0], [0, 1, 0])
    with pytest.raises(CadError, match="^arm_A and arm_B hang on each other; give one of them another parent.$"):
        build_parts(imp, src)
    imp.bodies["arm_A"].joint = None                    # welded ones too
    imp.bodies["arm_B"].joint = None
    with pytest.raises(CadError, match="hang on each other"):
        build_parts(imp, src)


def test_no_direction_is_a_plain_error(tmp_path, src):
    imp = make(src, tmp_path)
    imp.bodies["arm_A"].joint = JointSettings("hinge", [0.06, 0.1, 0.01], [0, 0, 0])
    with pytest.raises(CadError, match="arm_A's joint axis has no direction"):
        build_parts(imp, src)
    imp = edit(make(src, tmp_path), "add_mount", body="arm_A", name="tip", point=[0, 0, 0], normal=[0, 0, 1])
    imp.bodies["arm_A"].mounts[0].normal = [0.0, 0.0, 0.0]
    with pytest.raises(CadError, match="arm_A's mount tip has no direction"):
        build_parts(imp, src)
    imp.bodies["arm_A"].mounts[0].normal = [float("nan"), 0.0, 1.0]
    with pytest.raises(CadError, match="no direction"):
        build_parts(imp, src)


def test_coacd_finding_no_pieces_does_not_say_install(monkeypatch, tmp_path, src):
    def nothing(path, units):
        raise BenchError(f"mesh {path}: coacd found no convex pieces in it")
    monkeypatch.setattr("scrimmage.bench.cad.convex_pieces", nothing)
    imp = edit(make(src, tmp_path), "set_collision", body="base_plate", mode="shaped")
    result = build_parts(imp, src)
    assert not any("pip install" in m for m in result.messages)
    assert any("base_plate couldn't be split into convex pieces" in m for m in result.messages)


def test_typed_weights_that_reach_the_total(tmp_path, src):
    imp = make(src, tmp_path)
    imp = edit(imp, "set_joint", body="arm_B", parent="base_plate",
               joint={"kind": "hinge", "anchor": [0.24, 0.1, 0.01], "axis": [0, 1, 0]})
    imp = edit(imp, "set_mass", body="arm_B", kg=2.0)
    plain = {b.name: b.mass for b in build_parts(imp, src).parts["climber"].part.bodies}
    imp = edit(imp, "set_total", part="climber", kg=2.0)
    result = build_parts(imp, src)
    assert any("already reach its total of 2 kg" in m for m in result.messages)
    assert not any("0.001" in m for m in result.messages)
    assert {b.name: b.mass for b in result.parts["climber"].part.bodies} == pytest.approx(plain)


def test_dropped_root_joint_names_its_slots_and_couplings_said_once(tmp_path, src):
    imp = make(src, tmp_path)
    imp = edit(imp, "set_joint", body="arm_B", parent="base_plate",
               joint={"kind": "hinge", "anchor": [0.24, 0.1, 0.01], "axis": [0, 1, 0]})
    imp = edit(imp, "set_motor", body="arm_B", settings={"motor": "NEO", "ratio": 5})
    imp = edit(imp, "set_joint", body="arm_A", parent="base_plate",
               joint={"kind": "hinge", "anchor": [0.06, 0.1, 0.01], "axis": [0, 1, 0]})
    imp = edit(imp, "set_motor", body="arm_A", settings={"motor": "NEO", "ratio": 5})
    imp, _, _ = hinge_on_hook(imp, src, parent="arm_B")
    imp = edit(imp, "add_coupling", a="arm_A", b="hook_assy", ratio=2)
    imp.bodies["arm_B"].part = "right_arm"          # a part root that keeps its parent, joint and motor
    imp.parts["right_arm"] = type(imp.parts["climber"])(folder="right_arm")
    result = build_parts(imp, src)
    assert "arm_B's joint and motor were dropped, because the first body of a part can't move on its own" \
        in result.messages
    assert result.messages.count("The coupling between arm_A and hook_assy was left out: both need joints in the "
                                 "same part.") == 1
    assert not result.parts["right_arm"].part.motors


def test_a_rebuild_keeps_the_grip(tmp_path, src):
    """The folder's own edits (scrimmage-edits.toml, e.g. a grip material) are carried into the rebuilt folder."""
    from scrimmage.parts.grip import set_grip
    imp = make(src, tmp_path)
    _, (folder,) = write(imp, src, tmp_path)
    (folder / "scrimmage-edits.toml").write_text("# mine\ntotal_mass = 2.0\n", encoding="utf-8")
    set_grip(folder, "base_plate", "rubber")
    before = (folder / "scrimmage-edits.toml").read_text()
    _, (again,) = write(imp, src, tmp_path)
    assert again == folder and (folder / "scrimmage-edits.toml").read_text() == before
    part = load_part(folder)
    assert part.grips == {"base_plate": "rubber"} and sum(b.mass for b in part.bodies) == pytest.approx(2.0)
