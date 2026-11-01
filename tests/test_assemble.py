import math
from pathlib import Path

import mujoco
import numpy as np
import pytest

from scrimmage.parts.assemble import assemble
from scrimmage.parts.model import PartError, quat_from_rpy

TESTS = Path(__file__).parent


def cfg(*parts, **extra):
    d = {"part": list(parts), "robot": {"start_pose": [2.0, 4.0, 90.0]}}
    d.update(extra)
    return d


def body_pos(built, name):
    m = built.model
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    return d.xpos[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, name)].copy(), d


def test_root_part_is_free_at_start_pose_with_prefixed_names():
    b = assemble(cfg({"name": "drive", "stock": "chassis"}), TESTS)
    assert b.robot_body == "drive.chassis"
    pos, _ = body_pos(b, "drive.chassis")
    assert pos == pytest.approx([2.0, 4.0, 0.0])
    j = mujoco.mj_name2id(b.model, mujoco.mjtObj.mjOBJ_JOINT, "drive.free")
    assert int(b.model.jnt_type[j]) == int(mujoco.mjtJoint.mjJNT_FREE)
    assert b.model.body_mass[mujoco.mj_name2id(b.model, mujoco.mjtObj.mjOBJ_BODY, "drive.chassis")] == pytest.approx(46)


def test_field_is_there_with_todays_names():
    m = assemble(cfg({"name": "drive", "stock": "chassis"}), TESTS).model
    for g in ("carpet", "wall_blue", "wall_red", "wall_left", "wall_right", "obstacle"):
        assert mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, g) >= 0, g
    m = assemble(cfg({"name": "drive", "stock": "chassis"}, field={"obstacle": False}), TESTS).model
    assert mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, "obstacle") < 0


def test_attach_to_mount_with_offset_and_rotation():
    b = assemble(cfg({"name": "drive", "stock": "chassis"},
                     {"name": "intake", "path": "parts/intake", "attach": "drive.top", "pos": [0.1, 0, 0],
                      "rot": [0, 0, 90]}), TESTS)
    root, _ = body_pos(b, "drive.chassis")
    frame, _ = body_pos(b, "intake.frame")
    # the robot is yawed 90 deg at its start pose: robot +x is world +y
    assert frame - root == pytest.approx([0.0, 0.1, 0.12 + 0.02], abs=1e-9)
    roller, _ = body_pos(b, "intake.roller")
    # the intake is yawed a further 90 deg: its +x (0.15 to the roller) is world -x
    assert roller - frame == pytest.approx([-0.15, 0.0, 0.0], abs=1e-9)


def test_part_on_a_moving_body_moves_with_it():
    b = assemble(cfg({"name": "drive", "stock": "chassis"},
                     {"name": "a", "path": "parts/intake", "attach": "drive.top"},
                     {"name": "b", "path": "parts/intake", "attach": "a.roller"}), TESTS)
    m = b.model
    d = mujoco.MjData(m)
    q = m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, "a.roller")]
    mujoco.mj_forward(m, d)
    before = d.xpos[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "b.frame")].copy()
    d.qpos[q] = np.pi / 2
    mujoco.mj_forward(m, d)
    after = d.xpos[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "b.frame")]
    assert np.linalg.norm(after - before) < 1e-9          # b sits on the roller's axis...
    xq = d.xquat[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "b.frame")]
    assert abs(xq[0]) < 0.99                              # ...but turned with it


def test_mass_spread_over_geoms_by_volume():
    b = assemble(cfg({"name": "drive", "stock": "chassis"},
                     {"name": "intake", "path": "parts/intake", "attach": "drive.top"}), TESTS)
    m = b.model
    assert m.body_mass[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "intake.frame")] == pytest.approx(1.5)
    assert m.body_mass[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "intake.roller")] == pytest.approx(0.3)


def test_robot_geoms_dont_collide_with_each_other_but_hit_the_field():
    m = assemble(cfg({"name": "drive", "stock": "chassis"}), TESTS).model
    carpet = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, "carpet")
    assert (m.geom_contype[carpet], m.geom_conaffinity[carpet]) == (1, 2)
    robot = [g for g in range(m.ngeom) if m.geom_bodyid[g] != 0 and m.geom_contype[g]]
    assert robot and all((m.geom_contype[g], m.geom_conaffinity[g]) == (2, 1) for g in robot)


@pytest.mark.parametrize("parts, message", [
    ([], r"no \[\[part\]\] entries"),
    ([{"name": "a", "stock": "chassis"}, {"name": "b", "stock": "chassis"}], r"exactly one part without attach.*a, b"),
    ([{"name": "a", "stock": "chassis", "attach": "b.top"}, {"name": "b", "stock": "chassis", "attach": "a.top"}],
     r"exactly one part without attach"),
    ([{"name": "a", "stock": "chassis"}, {"name": "b", "path": "parts/intake", "attach": "c.top"},
      {"name": "c", "path": "parts/intake", "attach": "b.top"}], r"attach cycle: b, c"),
    ([{"name": "a", "stock": "chassis"}, {"name": "b", "path": "parts/intake", "attach": "z.top"}],
     r"parts\[1\] 'b': attach 'z.top': no part 'z'; parts: a, b"),
    ([{"name": "a", "stock": "chassis"}, {"name": "b", "path": "parts/intake", "attach": "a.roof"}],
     r"no mount or body 'roof' on 'a'; mounts: .*front_left.*; bodies: chassis"),
    ([{"name": "a", "stock": "chassis"}, {"name": "b", "path": "parts/intake", "attach": "a"}],
     r"attach must be \"<part>.<mount or body>\""),
    ([{"name": "a.b", "stock": "chassis"}], r"name must be letters, digits and _"),
    ([{"name": "a", "stock": "chassis"}, {"name": "a", "path": "parts/intake", "attach": "a.top"}], r"'a' is used twice"),
    ([{"name": "a", "stock": "chassis", "path": "x"}], r"exactly one of stock = .* or path = "),
    ([{"name": "a", "path": "parts/intake", "mass": 3}], r"'mass' isn't a key of a path part"),
    ([{"name": "a", "path": "parts/nowhere"}], r"part file not found: .*parts/nowhere/part.toml"),
])
def test_placement_errors(parts, message):
    with pytest.raises(PartError, match=message):
        assemble(cfg(*parts), TESTS)


# ------------------------------------------------------------------------------------ fix round 1

def write_part(tmp_path, text, name="p"):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    (d / "part.toml").write_text(f'[part]\nname = "{name}"\nformat = 1\n' + text)
    return d


def one(tmp_path, text, start=(0.0, 0.0, 0.0), **entry):
    """chassis + the part at tmp_path/p attached to the chassis top (or the root when entry says so)."""
    write_part(tmp_path, text)
    return assemble({"part": [{"name": "drive", "stock": "chassis"},
                              {"name": "p", "path": "p", "attach": "drive.top", **entry}],
                     "robot": {"start_pose": list(start)}}, tmp_path)


def bid(m, name):
    return mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, name)


def test_body_with_no_geoms_gets_explicit_inertial_at_origin(tmp_path):
    b = one(tmp_path, '[[body]]\nname = "f"\nmass = 1\ngeom = [{ box = [0.1, 0.1, 0.1] }]\n'
                      '[[body]]\nname = "pivot"\nparent = "f"\nmass = 0.5\n'
                      'joint = { name = "j", hinge = "y" }\n')
    m = b.model
    assert m.body_mass[bid(m, "p.pivot")] == pytest.approx(0.5)
    assert m.body_ipos[bid(m, "p.pivot")] == pytest.approx([0, 0, 0])


def test_root_pos_is_in_the_start_pose_frame():
    b = assemble(cfg({"name": "drive", "stock": "chassis", "pos": [1, 0, 0], "rot": [0, 0, 90]}), TESTS)
    pos, d = body_pos(b, "drive.chassis")
    # heading 90 deg: the offset +x of the start-pose frame is world +y; rot turns the part below it
    assert pos == pytest.approx([2.0, 5.0, 0.0])
    xq = d.xquat[bid(b.model, "drive.chassis")]
    assert 2 * np.arctan2(abs(xq[3]), abs(xq[0])) == pytest.approx(np.pi)   # 90 + 90 degrees


@pytest.mark.parametrize("geom, axis", [
    ('{ cylinder = [0.05, 0.2], axis = "x" }', (1, 0, 0)),
    ('{ cylinder = [0.05, 0.2], axis = "y" }', (0, 1, 0)),
    ('{ cylinder = [0.05, 0.2], axis = "y" }', (-1, 0, 0)),   # with rot 90 about z on the part
])
def test_cylinder_axis(tmp_path, geom, axis):
    rot = [0, 0, 90] if axis == (-1, 0, 0) else [0, 0, 0]
    b = one(tmp_path, f'[[body]]\nname = "f"\nmass = 1\ngeom = [{geom}]\n', rot=rot)
    m = b.model
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    g = [i for i in range(m.ngeom) if m.geom_bodyid[i] == bid(m, "p.f")][0]
    assert d.geom_xmat[g].reshape(3, 3)[:, 2] == pytest.approx(axis, abs=1e-9)


def test_coupling_direction_and_ratio(tmp_path):
    b = one(tmp_path, '[[body]]\nname = "f"\nmass = 1\ngeom = [{ box = [0.1, 0.1, 0.1] }]\n'
                      '[[body]]\nname = "a"\nparent = "f"\nmass = 0.1\ngeom = [{ sphere = 0.02 }]\n'
                      'joint = { name = "ja", hinge = "y" }\n'
                      '[[body]]\nname = "b"\nparent = "f"\nmass = 0.1\ngeom = [{ sphere = 0.02 }]\n'
                      'joint = { name = "jb", hinge = "y" }\n'
                      '[[coupling]]\njoints = ["ja", "jb"]\nratio = -2.5\n')
    m = b.model
    assert m.neq == 1
    ja = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, "p.ja")
    jb = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, "p.jb")
    # MuJoCo: joint1 = polycoef(joint2), so b (obj1) follows a (obj2)
    assert (m.eq_obj1id[0], m.eq_obj2id[0]) == (jb, ja)
    assert m.eq_data[0][:2] == pytest.approx([0, -2.5])
    d = mujoco.MjData(m)
    d.qpos[m.jnt_qposadr[ja]], d.qpos[m.jnt_qposadr[jb]] = 0.2, 0.3
    mujoco.mj_forward(m, d)
    # constraint residual is b - ratio * a (the swapped direction would give a - ratio * b = 0.95)
    assert d.efc_pos[d.efc_type == int(mujoco.mjtConstraint.mjCNSTR_EQUALITY)][0] == pytest.approx(0.3 + 2.5 * 0.2)


def test_com_and_inertia_make_an_explicit_inertial(tmp_path):
    b = one(tmp_path, '[[body]]\nname = "f"\nmass = 2\ncom = [0.01, 0.02, 0.03]\ninertia = [0.1, 0.2, 0.3]\n'
                      'geom = [{ box = [0.5, 0.5, 0.5] }]\n'
                      '[[body]]\nname = "g"\nparent = "f"\nmass = 1\ncom = [0, 0, 0.1]\n'
                      'geom = [{ box = [0.1, 0.1, 0.1] }]\njoint = { name = "j", hinge = "x" }\n')
    m = b.model
    f = bid(m, "p.f")
    assert m.body_mass[f] == pytest.approx(2)
    assert m.body_ipos[f] == pytest.approx([0.01, 0.02, 0.03])
    assert sorted(m.body_inertia[f]) == pytest.approx([0.1, 0.2, 0.3])
    g = bid(m, "p.g")   # com without inertia: 0.4 m r^2 with r = 0.05
    assert m.body_inertia[g] == pytest.approx([0.4 * 0.05 ** 2] * 3)


def test_mesh_geom(tmp_path):
    # a closed tetrahedron
    v = [(0, 0, 0), (0.2, 0, 0), (0, 0.2, 0), (0, 0, 0.2)]
    faces = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]
    import struct
    blob = bytearray(80) + struct.pack("<I", len(faces))
    for f in faces:
        blob += struct.pack("<3f", 0, 0, 0) + b"".join(struct.pack("<3f", *v[i]) for i in f) + b"\0\0"
    d = tmp_path / "p"
    d.mkdir()
    (d / "tet.stl").write_bytes(bytes(blob))
    b = one(tmp_path, '[[body]]\nname = "f"\nmass = 3\ngeom = [{ mesh = "tet.stl" }]\n')
    m = b.model
    assert m.nmesh == 1
    assert m.body_mass[bid(m, "p.f")] == pytest.approx(3)
    g = [i for i in range(m.ngeom) if m.geom_bodyid[i] == bid(m, "p.f")][0]
    assert int(m.geom_type[g]) == int(mujoco.mjtGeom.mjGEOM_MESH)


def test_hinge_range_and_spring_in_radians(tmp_path):
    b = one(tmp_path, '[[body]]\nname = "f"\nmass = 1\ngeom = [{ box = [0.1, 0.1, 0.1] }]\n'
                      '[[body]]\nname = "h"\nparent = "f"\nmass = 0.1\ngeom = [{ sphere = 0.02 }]\n'
                      'joint = { name = "jh", hinge = "y", range = [-30, 60], spring = { stiffness = 2.0, rest = 45 } }\n'
                      '[[body]]\nname = "s"\nparent = "f"\nmass = 0.1\ngeom = [{ sphere = 0.02 }]\n'
                      'joint = { name = "js", slide = "x", range = [0, 0.1], spring = { stiffness = 3.0, rest = 0.05 } }\n')
    m = b.model
    jh = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, "p.jh")
    js = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, "p.js")
    assert m.jnt_range[jh] == pytest.approx([np.radians(-30), np.radians(60)])
    assert m.jnt_stiffness[jh] == pytest.approx(2.0)
    assert m.qpos_spring[m.jnt_qposadr[jh]] == pytest.approx(np.radians(45))
    assert m.jnt_range[js] == pytest.approx([0, 0.1])
    assert m.qpos_spring[m.jnt_qposadr[js]] == pytest.approx(0.05)


def test_collide_false_geom_has_no_contact_bits():
    b = assemble(cfg({"name": "drive", "stock": "chassis"},
                     {"name": "intake", "path": "parts/intake", "attach": "drive.top"}), TESTS)
    m = b.model
    g = [i for i in range(m.ngeom) if m.geom_bodyid[i] == bid(m, "intake.roller")][0]
    assert (m.geom_contype[g], m.geom_conaffinity[g]) == (0, 0)


def test_compile_errors_are_part_errors(tmp_path):
    with pytest.raises(PartError, match="assembling the robot: "):
        one(tmp_path, '[[body]]\nname = "f"\nmass = 1\ngeom = [{ box = [0.1, 0.1, 0.1] }]\n'
                      '[[body]]\nname = "h"\nparent = "f"\nmass = 0.1\ngeom = [{ sphere = 0.02 }]\n'
                      'joint = { name = "jh", hinge = "y" }\n[[coupling]]\njoints = ["jh", "jh"]\nratio = 2\n')


def test_mount_with_rotation(tmp_path):
    write_part(tmp_path, '[[body]]\nname = "f"\nmass = 1\ngeom = [{ box = [0.1, 0.1, 0.1] }]\n'
                         '[[mount]]\nname = "side"\nbody = "f"\npos = [0.1, 0, 0]\nrot = [0, 0, 90]\n')
    write_part(tmp_path, '[[body]]\nname = "g"\nmass = 1\ngeom = [{ box = [0.1, 0.1, 0.1] }]\n'
                         '[[body]]\nname = "tip"\nparent = "g"\npos = [0.3, 0, 0]\nmass = 0.1\n'
                         'geom = [{ sphere = 0.01 }]\njoint = { name = "j", hinge = "z" }\n', name="q")
    b = assemble({"part": [{"name": "drive", "stock": "chassis"},
                           {"name": "p", "path": "p", "attach": "drive.top"},
                           {"name": "q", "path": "q", "attach": "p.side", "pos": [0.0, 0.05, 0.0]}],
                  "robot": {"start_pose": [0, 0, 0]}}, tmp_path)
    p, _ = body_pos(b, "p.f")
    g, d = body_pos(b, "q.g")
    # mount rot 90 about z: q's x is p's y; offset (0, .05, 0) in the mount frame is p's -x, on top of the mount at +0.1
    assert g - p == pytest.approx([0.05, 0.0, 0.0], abs=1e-9)
    tip, _ = body_pos(b, "q.tip")
    assert tip - g == pytest.approx([0.0, 0.3, 0.0], abs=1e-9)


def test_path_part_errors_name_the_instance(tmp_path):
    with pytest.raises(PartError, match=r"parts\[1\] 'arm'.*part file not found"):
        assemble({"part": [{"name": "drive", "stock": "chassis"},
                           {"name": "arm", "path": "nowhere", "attach": "drive.top"}]}, tmp_path)
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "part.toml").write_text("[part\n")
    with pytest.raises(PartError, match=r"parts\[1\] 'arm'"):
        assemble({"part": [{"name": "drive", "stock": "chassis"},
                           {"name": "arm", "path": "bad", "attach": "drive.top"}]}, tmp_path)


# ---- [[loop]] pins and inertia_rot ---------------------------------------------------------------------------

def fourbar_output(a, b, c, d_pos, theta, near):
    """Closed-form four-bar. Planar (x, z); an angle t points along (cos t, -sin t) (a rotation about +y).
    Crank of length a from the origin at angle theta, coupler b, rocker of length c pivoting at d_pos.
    Returns the rocker's angle, the root of the circle intersection closest to `near`."""
    p = np.array([a * math.cos(theta), -a * math.sin(theta)])
    v = p - np.asarray(d_pos, float)                 # rocker pivot -> crank tip
    n = float(np.linalg.norm(v))
    psi = math.atan2(-v[1], v[0])                    # v = n * (cos psi, -sin psi)
    k = (n * n + c * c - b * b) / (2 * c * n)        # cos(phi - psi) = k
    assert abs(k) <= 1.0
    roots = [psi + s * math.acos(k) for s in (1, -1)]
    return min(roots, key=lambda r: abs((r - near + math.pi) % (2 * math.pi) - math.pi))


def test_loop_closes_a_four_bar(tmp_path):
    a, c, d = 0.1, 0.25, 0.3
    th_c0 = th_r0 = -math.pi / 2                         # crank and rocker start pointing up (+z)
    tip = np.array([0.0, a])                            # (x, z) of the crank tip at qpos0
    gap = np.array([d, c]) - tip                        # to the rocker tip
    b = float(np.hypot(*gap))
    th_b0 = -math.atan2(gap[1], gap[0])                 # coupler direction (cos t, -sin t) = gap / b
    deg = math.degrees
    write_part(tmp_path, f"""
[[body]]
name = "ground"
mass = 100

[[body]]
name = "crank"
parent = "ground"
mass = 0.2
rot = [0, {deg(th_c0)!r}, 0]
joint = {{ name = "jc", hinge = "y", damping = 0.5 }}

[[body]]
name = "coupler"
parent = "crank"
mass = 0.2
pos = [{a!r}, 0, 0]
rot = [0, {deg(th_b0 - th_c0)!r}, 0]
joint = {{ name = "jb", hinge = "y", damping = 0.5 }}

[[body]]
name = "rocker"
parent = "ground"
mass = 0.2
pos = [{d!r}, 0, 0]
rot = [0, {deg(th_r0)!r}, 0]
joint = {{ name = "jr", hinge = "y", damping = 0.5 }}

[[loop]]
body1 = "coupler"
body2 = "rocker"
pos = [{b!r}, 0, 0]
""", name="fb")
    built = assemble({"part": [{"name": "fb", "path": "fb"}], "robot": {"start_pose": [0, 0, 0]}}, tmp_path)
    m = built.model
    eqs = [i for i in range(m.neq) if int(m.eq_type[i]) == int(mujoco.mjtEq.mjEQ_CONNECT)]
    assert len(eqs) == 1
    bid = lambda n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, n)
    assert (int(m.eq_obj1id[eqs[0]]), int(m.eq_obj2id[eqs[0]])) == (bid("fb.coupler"), bid("fb.rocker"))

    m.opt.gravity[:] = 0
    data = mujoco.MjData(m)
    jid = lambda n: m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, n)]
    qc, qr = jid("fb.jc"), jid("fb.jr")
    target = math.radians(20)
    for _ in range(1500):
        data.qpos[qc], data.qvel[m.jnt_dofadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, "fb.jc")]] = target, 0
        mujoco.mj_step(m, data)
    mujoco.mj_forward(m, data)

    def anchor(body, local):
        i = bid(body)
        return data.xpos[i] + data.xmat[i].reshape(3, 3) @ np.array(local)
    assert np.linalg.norm(anchor("fb.coupler", [b, 0, 0]) - anchor("fb.rocker", [c, 0, 0])) < 1e-3
    want = fourbar_output(a, b, c, (d, 0.0), th_c0 + target, th_r0)
    assert abs(deg(data.qpos[qr])) > 5     # the rocker really moved
    assert deg(data.qpos[qr] + th_r0 - want) == pytest.approx(0.0, abs=1.0)


def test_inertia_rot_orients_the_inertial_frame(tmp_path):
    write_part(tmp_path, '[[body]]\nname = "f"\nmass = 1\ncom = [0, 0, 0.05]\ninertia = [0.001, 0.002, 0.003]\n'
                         'inertia_rot = [10, 20, 30]\n')
    b = assemble({"part": [{"name": "p", "path": "p"}], "robot": {"start_pose": [0, 0, 0]}}, tmp_path)
    i = mujoco.mj_name2id(b.model, mujoco.mjtObj.mjOBJ_BODY, "p.f")
    assert b.model.body_iquat[i] == pytest.approx(quat_from_rpy([10, 20, 30]), abs=1e-9)


# ------------------------------------------------------------------------------------ final review fixes

def _stl(path, verts, faces):
    import struct
    blob = bytearray(80) + struct.pack("<I", len(faces))
    for f in faces:
        blob += struct.pack("<3f", 0, 0, 0) + b"".join(struct.pack("<3f", *verts[i]) for i in f) + b"\0\0"
    path.write_bytes(bytes(blob))


def test_flat_mesh_on_a_body_with_an_explicit_inertial_loads(tmp_path):
    """A shell-only CAD body (a flat plate of faces: no volume) with com and inertia (a mass override) must load:
    the mesh is only for looks and gives no inertia, so MuJoCo's mesh-volume check mustn't trip on it."""
    d = tmp_path / "p"
    d.mkdir()
    _stl(d / "plate.stl", [(0, 0, 0), (0.2, 0, 0), (0.2, 0.1, 0), (0, 0.1, 0)], [(0, 1, 2), (0, 2, 3)])
    b = one(tmp_path, '[[body]]\nname = "f"\nmass = 1.5\ncom = [0.1, 0.05, 0]\ninertia = [0.01, 0.02, 0.03]\n'
                      'geom = [{ mesh = "plate.stl", collide = false }]\n')
    m = b.model
    f = bid(m, "p.f")
    assert m.body_mass[f] == pytest.approx(1.5)
    assert m.body_ipos[f] == pytest.approx([0.1, 0.05, 0])
