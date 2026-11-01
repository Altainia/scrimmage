"""Exporter, Task 5: mass, COM and principal inertia (mass.py), body meshes and wheel cylinders (mesh.py).

The numbers come from headless FreeCAD (tests/freecad/make_fixtures.py); the expected values are computed here,
independently, from the boxes' dimensions and placements. `inertia_rot` is checked through scrimmage's own
`quat_from_rpy` and through MuJoCo, comparing inertia tensors (MuJoCo may reorder principal axes)."""

import base64
import json
import math

import numpy as np
import pytest

from freecad_harness import needs_freecad, run_in_freecad
from scrimmage.parts.model import quat_from_rpy

RHO_AL = 2700.0

SCRIPT = r'''
import base64, json, os, FreeCAD as App, Part
from scrimmage_part import read, tree, mass, mesh
from scrimmage_part.errors import ExportError
paths = json.loads(open(os.path.join(WORK, "paths.json")).read())
ID = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0))
out = {}

def inertial(*a):
    m, com, pm, rpy = mass.body_inertial(*a)
    return dict(mass=m, com=list(com), pm=list(pm), rpy=list(rpy))

def frame_of(o):
    return read.plc_to_frame(o.Placement)

def stl(comps, frame, name):
    p = os.path.join(WORK, name)
    mesh.write_body_stl(comps, frame, p)
    return base64.b64encode(open(p, "rb").read()).decode()

def load(name):
    doc = App.openDocument(paths[name])
    e = read.read_assembly(read.find_assembly(doc))
    return doc, e

# --- the mass fixture: materials, overrides, an L-shaped App::Part
doc, e = load("mass")
c = e.components
out["plain"] = inertial([c["Plain"]], {}, ID)
out["plain_override"] = inertial([c["Plain"]], {"Plain": 1.0}, ID)
out["alu"] = inertial([c["Alu"]], {}, ID)
out["steel"] = inertial([c["Steel"]], {}, ID)
out["steel_density"] = doc.getObject("Steel").ShapeMaterial.PhysicalProperties.get("Density")
out["plain_material"] = doc.getObject("Plain").ShapeMaterial.Name
out["plain_frame"] = frame_of(doc.getObject("Plain"))
out["l"] = inertial([c["L"]], {}, ID)
out["l_frames"] = {n: frame_of(doc.getObject(n)) for n in ("L", "LA", "LB")}
out["l_stl"] = stl([c["L"]], ID, "l.stl")
lf = ((10.0, -20.0, 30.0), (0.9238795325112867, 0.0, 0.3826834323650898, 0.0))     # 45 deg about y
out["two"] = inertial([c["L"], c["Alu"]], {"Alu": 0.5}, lf)
out["two_frame"] = lf
out["alu_frame"] = frame_of(doc.getObject("Alu"))
App.closeDocument(doc.Name)

# --- the arm: a box yawed 15 deg, hinged about world +y
doc, e = load("arm")
t = tree.build(e)
arm = [b for b in t.bodies if b.name == "Arm"][0]
out["arm"] = inertial([e.components[n] for n in arm.comps], {}, arm.frame_world)
out["arm_body_frame"] = [list(arm.frame_world[0]), list(arm.frame_world[1])]
out["arm_comp_frame"] = frame_of(doc.getObject("Arm"))
out["arm_stl"] = stl([e.components[n] for n in arm.comps], arm.frame_world, "arm.stl")
App.closeDocument(doc.Name)

# --- the wheel of base2w
doc, e = load("base2w")
t = tree.build(e)
w = [b for b in t.bodies if b.name == "WheelL"][0]
r, wd, ctr = mesh.wheel_cylinder([e.components[n] for n in w.comps], w.frame_world, (0.0, 0.0, 1.0))
out["wheel"] = [r, wd, list(ctr)]
App.closeDocument(doc.Name)

# --- zero mass is an error naming the component
doc, e = load("bad_zero_mass")
try:
    mass.body_inertial([e.components["Ghost"]], {}, ID)
    out["zero"] = None
except ExportError as err:
    out["zero"] = err.problems
out["ghost_override"] = inertial([e.components["Ghost"]], {"Ghost": 0.25}, ID)
App.closeDocument(doc.Name)
print("SCRIMMAGE_JSON:" + json.dumps(out))
'''


@pytest.fixture(scope="module")
def res(fixtures_dir):
    return run_in_freecad(SCRIPT, files={"paths.json": json.dumps(fixtures_dir)})


# ---------------------------------------------------------------- independent rigid-body math (numpy)

def qmat(q):
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def tf(frame):
    """4x4 homogeneous matrix of a (pos, quat) frame."""
    m = np.eye(4)
    m[:3, :3] = qmat(frame[1])
    m[:3, 3] = frame[0]
    return m


def box(dims, to_body, rho=RHO_AL):
    """(mass kg, com m, inertia about com kg m^2 in body axes, corners m) of an L x W x H mm box whose local frame is
    `to_body` (4x4, mm) in the body frame."""
    d = np.array(dims, float) / 1000.0
    m = rho * d.prod()
    i = m / 12 * np.diag([d[1] ** 2 + d[2] ** 2, d[0] ** 2 + d[2] ** 2, d[0] ** 2 + d[1] ** 2])
    r = to_body[:3, :3]
    com = (to_body @ np.append(np.array(dims) / 2.0, 1))[:3] / 1000.0
    corners = np.array([(to_body @ np.array([a, b, c, 1.0]))[:3] / 1000.0
                        for a in (0, dims[0]) for b in (0, dims[1]) for c in (0, dims[2])])
    return m, com, r @ i @ r.T, corners


def combine(items):
    m = sum(x[0] for x in items)
    com = sum(x[0] * x[1] for x in items) / m
    i = np.zeros((3, 3))
    for mi, ci, ii, *_ in items:
        dv = ci - com
        i += ii + mi * (dv @ dv * np.eye(3) - np.outer(dv, dv))
    return m, com, i


def tensor(d):
    """The body inertia tensor scrimmage builds from the exporter's output (principal moments + inertia_rot)."""
    r = qmat(quat_from_rpy(d["rpy"]))
    return r @ np.diag(d["pm"]) @ r.T


def check(d, m, com, i):
    assert d["mass"] == pytest.approx(m, rel=1e-9)
    assert d["com"] == pytest.approx(list(com), abs=1e-9)
    assert sorted(d["pm"]) == pytest.approx(sorted(np.linalg.eigvalsh(i)), rel=1e-7, abs=1e-12)
    assert np.allclose(tensor(d), i, rtol=0, atol=1e-9 * max(1e-9, np.abs(i).max()) + 1e-12)


# ---------------------------------------------------------------- tests

@needs_freecad
def test_density_defaults_materials_and_overrides(res):
    assert res["plain_material"] == "Default"
    assert res["plain"]["mass"] == pytest.approx(0.08 * 2.7, abs=1e-6)          # 100x40x20 mm of aluminum 2700
    assert res["alu"]["mass"] == pytest.approx(0.216, abs=1e-6)                  # Aluminum-6061-T6: 2.7e-06 kg/mm^3
    value, unit = res["steel_density"].split(" ", 1)
    assert unit == "kg/mm^3" and float(value) * 1e9 != pytest.approx(RHO_AL)
    assert res["steel"]["mass"] == pytest.approx(80000e-9 * float(value) * 1e9, rel=1e-9)
    # an override replaces the mass; the volume still shapes the inertia
    m, com, i, _ = box((100, 40, 20), tf(res["plain_frame"]), rho=1.0 / 80000e-9)
    assert m == pytest.approx(1.0)
    check(res["plain_override"], m, com, i)
    check(res["plain"], *box((100, 40, 20), tf(res["plain_frame"]))[:3])


@needs_freecad
def test_arm_com_and_principal_inertia_in_the_joint_frame(res):
    to_body = np.linalg.inv(tf(res["arm_body_frame"])) @ tf(res["arm_comp_frame"])
    m, com, i, _ = box((300, 20, 20), to_body)
    d = res["arm"]
    assert d["mass"] == pytest.approx(0.3 * 0.02 * 0.02 * RHO_AL, rel=1e-9)
    a, b = m * (0.02 ** 2 + 0.02 ** 2) / 12, m * (0.3 ** 2 + 0.02 ** 2) / 12
    assert sorted(d["pm"]) == pytest.approx([a, b, b], rel=1e-9)
    check(d, m, com, i)
    # the box's long (x) axis, seen from the joint frame, is the principal axis of the odd moment `a`
    k = min(range(3), key=lambda n: abs(d["pm"][n] - a))
    long_axis = qmat(quat_from_rpy(d["rpy"]))[:, k]
    assert abs(long_axis @ to_body[:3, 0]) == pytest.approx(1, abs=1e-9)


@needs_freecad
def test_l_shaped_app_part_is_the_parallel_axis_sum(res):
    f = res["l_frames"]
    part = tf(f["L"])
    la = box((120, 20, 20), part @ tf(f["LA"]))
    lb = box((20, 30, 90), part @ tf(f["LB"]))
    check(res["l"], *combine([la, lb]))
    # two components in a rotated, offset body frame, one with a mass override
    inv = np.linalg.inv(tf(res["two_frame"]))
    la = box((120, 20, 20), inv @ part @ tf(f["LA"]))
    lb = box((20, 30, 90), inv @ part @ tf(f["LB"]))
    al = box((100, 40, 20), inv @ tf(res["alu_frame"]), rho=0.5 / 80000e-9)
    check(res["two"], *combine([la, lb, al]))


@needs_freecad
def test_inertia_rot_round_trips_through_mujoco(res):
    import mujoco
    for key in ("arm", "l", "two", "plain"):
        d = res[key]
        q = " ".join(f"{v:.17g}" for v in quat_from_rpy(d["rpy"]))
        xml = (f'<mujoco><worldbody><body><freejoint/><inertial pos="0 0 0" mass="{d["mass"]!r}" quat="{q}" '
               f'diaginertia="{" ".join(repr(v) for v in d["pm"])}"/></body></worldbody></mujoco>')
        model = mujoco.MjModel.from_xml_string(xml)
        r = np.zeros(9)
        mujoco.mju_quat2Mat(r, model.body_iquat[1])
        r = r.reshape(3, 3)
        got = r @ np.diag(model.body_inertia[1]) @ r.T
        assert np.allclose(got, tensor(d), rtol=1e-6, atol=1e-12), key


@needs_freecad
def test_zero_mass_is_an_error_unless_overridden(res):
    (p,) = res["zero"]
    assert "Ghost" in p and "override" in p
    g = res["ghost_override"]
    assert g["mass"] == 0.25 and g["pm"] == [0.0, 0.0, 0.0]


def _stl_model(data, path):
    import mujoco
    p = path
    p.write_bytes(data)
    xml = (f'<mujoco><asset><mesh name="m" file="{p}"/></asset><worldbody><body><geom type="mesh" mesh="m"/>'
           f'</body></worldbody></mujoco>')
    model = mujoco.MjModel.from_xml_string(xml)
    mdata = mujoco.MjData(model)
    mujoco.mj_forward(model, mdata)
    a, n = model.mesh_vertadr[0], model.mesh_vertnum[0]
    v = model.mesh_vert[a:a + n]
    world = mdata.geom_xpos[0] + v @ mdata.geom_xmat[0].reshape(3, 3).T       # undo MuJoCo's mesh recentring
    return world.min(axis=0), world.max(axis=0)


@needs_freecad
def test_stl_loads_in_mujoco_in_meters_in_the_body_frame(res, tmp_path):
    to_body = np.linalg.inv(tf(res["arm_body_frame"])) @ tf(res["arm_comp_frame"])
    corners = box((300, 20, 20), to_body)[3]
    lo, hi = _stl_model(base64.b64decode(res["arm_stl"]), tmp_path / "arm.stl")
    assert lo == pytest.approx(corners.min(axis=0), abs=1e-6)
    assert hi == pytest.approx(corners.max(axis=0), abs=1e-6)
    f = res["l_frames"]
    part = tf(f["L"])
    corners = np.vstack([box((120, 20, 20), part @ tf(f["LA"]))[3], box((20, 30, 90), part @ tf(f["LB"]))[3]])
    lo, hi = _stl_model(base64.b64decode(res["l_stl"]), tmp_path / "l.stl")
    assert lo == pytest.approx(corners.min(axis=0), abs=1e-6)
    assert hi == pytest.approx(corners.max(axis=0), abs=1e-6)


@needs_freecad
def test_wheel_cylinder(res):
    r, w, ctr = res["wheel"]
    assert r == pytest.approx(0.0508, abs=1e-4)
    assert w == pytest.approx(0.040, abs=1e-4)
    assert ctr == pytest.approx([0, 0, 0], abs=1e-6)


# ---------------------------------------------------------------- the eigen-decomposition and rpy (no FreeCAD)

def _mass_module():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "freecad" / "Scrimmage"))
    from scrimmage_part import mass
    return mass


@pytest.mark.parametrize("rpy", [(10, 20, 30), (-170, 60, 95), (0, 90, 40), (25, -90, -60), (180, 0, 0), (0, 0, 0)])
@pytest.mark.parametrize("moments", [(1.0, 2.0, 3.0), (2.0, 2.0, 5.0), (1e-4, 3e-3, 3e-3), (4.0, 4.0, 4.0)])
def test_principal_and_rpy_round_trip(rpy, moments):
    """Any tensor R diag R^T comes back as the same tensor through principal() -> rpy_deg() -> quat_from_rpy, including
    pitch +-90 (gimbal lock) and repeated moments."""
    mass = _mass_module()
    r = qmat(quat_from_rpy(rpy))
    t = r @ np.diag(moments) @ r.T
    pm, rot = mass.principal(t.tolist())
    assert pm == pytest.approx(sorted(moments), rel=1e-12, abs=1e-15)
    assert np.linalg.det(np.array(rot)) == pytest.approx(1)
    back = mass.rpy_deg(rot)
    r2 = qmat(quat_from_rpy(back))
    assert np.allclose(r2, np.array(rot), atol=1e-9)
    assert np.allclose(r2 @ np.diag(pm) @ r2.T, t, atol=1e-12 * max(moments))


def test_jacobi_random_symmetric():
    mass = _mass_module()
    rng = np.random.default_rng(5)
    for _ in range(200):
        a = rng.normal(size=(3, 3))
        t = a @ a.T
        w, v = mass.jacobi(t.tolist())
        v = np.array(v)
        assert np.allclose(v @ np.diag(w) @ v.T, t, atol=1e-12 * np.abs(t).max())
        assert np.allclose(v.T @ v, np.eye(3), atol=1e-12)
