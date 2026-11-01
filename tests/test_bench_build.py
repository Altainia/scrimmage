"""Building the bench model: shapes, shafts, curves cut into chord boxes, and the piece pool."""

import math
import tomllib
from pathlib import Path

import mujoco
import numpy as np
import pytest

from scrimmage.bench.build import build_bench, check_clear, cut_curve
from scrimmage.bench.spec import BenchError, parse_bench, with_overrides
from scrimmage.parts.assemble import TREAD_STIFFNESS, tread_solref

HEAD = """
[bench]
name = "test"
author = "Maya"
idea = "a test bench"
"""

CAR = """
[piece]
name = "car"
kind = "rigid"
shape = "box"
size = [0.10, 0.06, 0.04]
mass = 0.2
stiffness = 5000
"""

BALL = """
[piece]
preset = "2026-fuel"
"""

SHAPES = """
[shaft.top]
motor = "KrakenX60"
target_rps = 50
kP = 0.1
wheel_inertia = 0.0005

[shaft.bottom]
coupled_to = "top"
ratio = -2.0
wheel_inertia = 0.0004
added_inertia = 0.0001

[[wheel]]
name = "top_left"
shaft = "top"
center = [0.0, -0.05, 0.08]
diameter = 0.1
width = 0.03
tread = "35A"

[[wheel]]
name = "top_right"
shaft = "top"
center = [0.0, 0.05, 0.08]
diameter = 0.1
width = 0.03

[[wheel]]
name = "bottom"
shaft = "bottom"
center = [0.0, 0.0, -0.06]
diameter = 0.1
width = 0.08

[[plate]]
name = "floor"
center = [-0.3, 0.0, -0.03]
size = [0.3, 0.2, 0.01]
material = "UHMW"

[[bar]]
name = "rail"
a = [0.2, -0.1, 0.0]
b = [0.2, 0.1, 0.0]
diameter = 0.02
material = "steel"

[[curve]]
name = "hood"
center = [0.0, 0.0, 0.0]
radius = 0.2
start_deg = 0
end_deg = 90
width = 0.1

[[inlet]]
name = "feed"
center = [-0.3, 0.0, 0.05]
size = [0.1, 0.1, 0.05]
count = 4

[[outlet]]
name = "shot"
center = [0.6, 0.0, 0.0]
size = [0.2, 0.5, 0.5]
"""


def bench_from(text: str, **overrides):
    raw = tomllib.loads(text)
    if overrides:
        raw = with_overrides(raw, overrides)
    return parse_bench(raw, Path("."))


def test_shapes_and_shafts_built():
    b = bench_from(HEAD + CAR + SHAPES)
    built = build_bench(b)
    m, lay = built.model, built.layout
    names = list(lay.shape_of_geom.values())
    assert names.count("top_left") == names.count("top_right") == names.count("bottom") == 1
    assert names.count("floor") == 1 and names.count("rail") == 1
    assert names.count("hood") == len(cut_curve(b.curves[0])) == 18  # 90 deg in 5 deg chords
    assert set(lay.wheel_geoms) == {"top_left", "top_right", "bottom"}
    # one hinge per shaft: both top wheels ride one body on one dof
    assert set(lay.shaft_dof) == {"top", "bottom"}
    top_g, right_g = lay.wheel_geoms["top_left"], lay.wheel_geoms["top_right"]
    assert m.geom_bodyid[top_g] == m.geom_bodyid[right_g]
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    for name, dof in lay.shaft_dof.items():
        j = m.dof_jntid[dof]
        assert m.jnt_type[j] == mujoco.mjtJoint.mjJNT_HINGE
        assert abs(d.xaxis[j] @ np.array([0.0, 1.0, 0.0])) == pytest.approx(1.0, rel=1e-9)
    assert np.allclose(d.xanchor[m.dof_jntid[lay.shaft_dof["bottom"]]], [0.0, 0.0, -0.06])
    # the hinge's inertia is the shaft's wheel plus added inertia
    M = np.zeros((m.nv, m.nv))
    mujoco.mj_fullM(m, d, M)
    assert M[lay.shaft_dof["top"], lay.shaft_dof["top"]] == pytest.approx(0.0005, rel=1e-6)
    assert M[lay.shaft_dof["bottom"], lay.shaft_dof["bottom"]] == pytest.approx(0.0005, rel=1e-6)
    # the coupling: bottom = -2 top, an equality JOINT constraint; turning the top shaft turns the bottom at -2x
    assert m.neq == 1 and m.eq_type[0] == mujoco.mjtEq.mjEQ_JOINT
    d.qvel[lay.shaft_dof["top"]] = 10.0
    for _ in range(200):
        d.qfrc_applied[:] = 0
        mujoco.mj_step(m, d)
    assert d.qvel[lay.shaft_dof["bottom"]] == pytest.approx(-2.0 * d.qvel[lay.shaft_dof["top"]], rel=1e-3)
    # the pool: one free body per piece, parked outside the bounds and apart
    assert len(lay.piece_bodies) == 4 == len(lay.parked) == len(lay.piece_qpos) == len(lay.piece_qvel)
    lo, hi = lay.bounds
    for p in lay.parked:
        assert np.any(p < lo) or np.any(p > hi)
    gaps = [np.linalg.norm(p - q) for i, p in enumerate(lay.parked) for q in lay.parked[i + 1:]]
    assert min(gaps) > 0.124  # the car's diagonal
    # bounds: everything grown by 0.5 m; the outlet is inside them
    assert lo[0] == pytest.approx(-0.45 - 0.5, rel=1e-6)
    assert hi[0] == pytest.approx(0.7 + 0.5, rel=1e-6)
    assert set(lay.outlet_boxes) == {"shot"} and set(lay.inlet_boxes) == {"feed"}
    olo, ohi = lay.outlet_boxes["shot"]
    assert np.allclose(olo, [0.5, -0.25, -0.25]) and np.allclose(ohi, [0.7, 0.25, 0.25])


def test_wheels_on_a_shaft_must_share_its_axis():
    with pytest.raises(BenchError, match="top_right.*axis"):
        build_bench(bench_from(HEAD + CAR + SHAPES, **{"wheel.top_right.center": [0.0, 0.05, 0.09]}))


def test_rigid_piece_takes_shape_friction_and_series_stiffness():
    b = bench_from(HEAD + CAR + SHAPES)
    built = build_bench(b)
    m, lay = built.model, built.layout
    dt = m.opt.timestep
    floor = next(g for g, n in lay.shape_of_geom.items() if n == "floor")
    wheel = lay.wheel_geoms["bottom"]
    piece_geom = m.body_geomadr[lay.piece_bodies[0]]
    # the shape outranks the piece, so its friction and contact stiffness are the ones MuJoCo uses
    assert m.geom_priority[floor] > m.geom_priority[piece_geom]
    assert m.geom_friction[floor][0] == pytest.approx(0.2, rel=1e-9)   # UHMW's rigid_friction
    assert m.geom_friction[wheel][0] == pytest.approx(b.materials["35A"].rigid_friction, rel=1e-9)
    k = 1.0 / (1.0 / TREAD_STIFFNESS + 1.0 / 5000.0)
    assert m.geom_solref[wheel][0] == pytest.approx(tread_solref(k, 1.0, 0.2, 1, dt)[0], rel=1e-6)
    assert m.geom_solref[floor][0] == pytest.approx(tread_solref(5000.0, 1.0, 0.2, 1, dt)[0], rel=1e-6)
    # a car dropped on the floor: the contact carries the floor's numbers
    d = mujoco.MjData(m)
    q = lay.piece_qpos[0]
    d.qpos[q:q + 7] = [-0.3, 0.0, -0.025 + 0.02 + 0.001, 1, 0, 0, 0]  # 1 mm above the floor
    for _ in range(200):
        mujoco.mj_step(m, d)
    con = [c for c in d.contact[:d.ncon] if piece_geom in (c.geom1, c.geom2)]
    assert con and all(c.friction[0] == pytest.approx(0.2, rel=1e-9) for c in con)


def test_cut_curve_lies_on_the_arc():
    spiral = SHAPES.replace("radius = 0.2\nstart_deg = 0\nend_deg = 90",
                            "radius_start = 0.25\nradius_end = 0.15\nstart_deg = 240\nend_deg = 140")
    b = bench_from(HEAD + CAR + spiral)
    assert b.curves[0].radius_start == 0.25
    c = b.curves[0]
    boxes = cut_curve(c, max_deg=5.0)
    assert len(boxes) == 20

    def radius_at(phi):
        return c.radius_start + (c.radius_end - c.radius_start) * (phi - c.start_deg) / (c.end_deg - c.start_deg)

    for center, quat, half in boxes:
        R = np.zeros(9)
        mujoco.mju_quat2Mat(R, quat)
        R = R.reshape(3, 3)
        assert half[1] == pytest.approx(c.width / 2, rel=1e-9) and half[2] == pytest.approx(c.thickness / 2, rel=1e-9)
        # the face toward the axis: sample along the chord, each point within 0.5 mm of the arc at its angle
        to_axis = -np.array([center[0], 0.0, center[2]])
        n = R[:, 2] if R[:, 2] @ to_axis > 0 else -R[:, 2]
        for s in np.linspace(-1, 1, 11):
            p = center + n * half[2] + R[:, 0] * half[0] * s
            phi = math.degrees(math.atan2(p[2], p[0])) % 360
            assert abs(math.hypot(p[0], p[2]) - radius_at(phi)) < 0.5e-3
    # the curve covers its whole span: the end chords start and stop on the end angles
    ends = []
    for center, quat, half in (boxes[0], boxes[-1]):
        R = np.zeros(9)
        mujoco.mju_quat2Mat(R, quat)
        R = R.reshape(3, 3)
        ends += [center + R[:, 0] * half[0], center - R[:, 0] * half[0]]
    angles = sorted(math.degrees(math.atan2(p[2], p[0])) % 360 for p in ends)
    assert angles[0] < 141.0 and angles[-1] > 239.0


def test_curve_angles_follow_the_hood_convention():
    # about +y, 0 deg is +x and 90 deg is +z
    c = bench_from(HEAD + CAR + SHAPES, **{"curve.hood.start_deg": 85, "curve.hood.end_deg": 95}).curves[0]
    center, _, _ = cut_curve(c, max_deg=10.0)[0]
    assert center[2] > 0.19 and abs(center[0]) < 0.01


POOL = HEAD + BALL + """
[shaft.s]
motor = "KrakenX60"
target_rps = 50
kP = 0.1
wheel_inertia = 0.0005

[[wheel]]
name = "w"
shaft = "s"
center = [0.0, 0.0, 0.0]
diameter = 0.1
width = 0.1

[[curve]]
name = "hood"
center = [0.0, 0.0, 0.0]
radius = 0.2
start_deg = 30
end_deg = 240
width = 0.12

[[inlet]]
name = "a"
center = [-0.3, 0.0, 0.4]
size = [0.2, 0.1, 0.2]
count = 30

[[inlet]]
name = "b"
center = [0.3, 0.0, 0.4]
size = [0.2, 0.1, 0.2]
count = 20

[[outlet]]
name = "shot"
center = [0.4, 0.0, 0.0]
size = [0.1, 0.3, 0.3]
"""


def test_pool_parked_and_squishy_never_collide_natively():
    built = build_bench(bench_from(POOL))
    m, lay = built.model, built.layout
    assert len(lay.piece_bodies) == 50
    piece_geoms = {g for g in range(m.ngeom) if m.geom_bodyid[g] in set(lay.piece_bodies)}
    assert all(m.geom_contype[g] == 0 and m.geom_conaffinity[g] == 0 for g in piece_geoms)
    d = mujoco.MjData(m)
    # move a few pieces right into the wheel and the hood: still no native contact
    for i, at in enumerate([(0.0, 0.0, 0.05), (0.0, 0.0, 0.2), (-0.1, 0.0, 0.17)]):
        q = lay.piece_qpos[i]
        d.qpos[q:q + 3] = at
    for _ in range(100):
        mujoco.mj_step(m, d)
    hits = [c for c in d.contact[:d.ncon] if c.geom1 in piece_geoms or c.geom2 in piece_geoms]
    assert d.ncon == 0 and not hits


def test_pool_size_is_capped():
    with pytest.raises(BenchError, match="200"):
        build_bench(bench_from(POOL, **{"inlet.a.count": 190}))


def test_check_clear():
    # the pinch-car file is clear
    check_clear(build_bench(bench_from(HEAD + CAR + SHAPES)))
    # an inlet zone overlapping a plate
    with pytest.raises(BenchError, match="inlet 'feed'.*'floor'"):
        check_clear(build_bench(bench_from(HEAD + CAR + SHAPES, **{"inlet.feed.center": [-0.3, 0.0, -0.02]})))
    # a piece spawned at the inlet's edge would sit inside a wheel, though the zone itself misses it
    with pytest.raises(BenchError, match="inlet 'feed'.*'bottom'"):
        check_clear(build_bench(bench_from(HEAD + CAR + SHAPES, **{"inlet.feed.center": [-0.08, 0.0, 0.0],
                                                                   "inlet.feed.size": [0.01, 0.01, 0.01]})))
    # a car spawned resting on the floor's top face is clear (touching is not overlapping)
    check_clear(build_bench(bench_from(HEAD + CAR + SHAPES, **{"inlet.feed.center": [-0.3, 0.0, 0.005],
                                                               "inlet.feed.size": [0.1, 0.1, 0.02]})))


SPRINGY = HEAD + BALL + """
[[bar]]
name = "bounce"
center = [0.0, 0.0, 0.3]
axis = [0.0, 1.0, 0.0]
length = 0.3
diameter = 0.025
material = "aluminum"
spring = 2000
travel = 0.02
spring_axis = [1.0, 0.0, 1.0]

[[inlet]]
name = "feed"
center = [-0.3, 0.0, 0.05]
size = [0.1, 0.1, 0.05]
"""


def test_springy_bar_has_a_slide():
    built = build_bench(bench_from(SPRINGY))
    m, lay = built.model, built.layout
    dof = lay.bar_dof["bounce"]
    j = m.dof_jntid[dof]
    assert m.jnt_type[j] == mujoco.mjtJoint.mjJNT_SLIDE
    assert m.jnt_stiffness[j] == pytest.approx(2000.0, rel=1e-9)
    assert m.jnt_limited[j] and np.allclose(m.jnt_range[j], [-0.02, 0.02])
    d = mujoco.MjData(m)
    mujoco.mj_kinematics(m, d)
    assert np.allclose(d.xaxis[j], np.array([1.0, 0.0, 1.0]) / math.sqrt(2))
    g = next(g for g, n in lay.shape_of_geom.items() if n == "bounce")
    assert m.geom_bodyid[g] == m.jnt_bodyid[j]
    assert "bounce" not in lay.shaft_dof


def test_mesh_piece_builds_in_its_units(tmp_path):
    # a 20 mm cube as an OBJ, in millimeters; it collides as its convex hull
    v = [(x, y, z) for x in (0, 20) for y in (0, 20) for z in (0, 20)]
    faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6), (0, 2, 6),
             (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    lines = [f"v {x} {y} {z}" for x, y, z in v] + [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in faces]
    (tmp_path / "cube.obj").write_text("\n".join(lines) + "\n")
    piece = '[piece]\nname = "cube"\nkind = "rigid"\nshape = "mesh"\nmesh = "cube.obj"\nunits = "mm"\n' \
            'mass = 0.05\ninertia = [3e-6, 3e-6, 3e-6]\n'
    built = build_bench(parse_bench(tomllib.loads(HEAD + piece + SHAPES), tmp_path))
    m, g = built.model, built.layout.piece_geoms[0]
    assert m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH
    assert np.allclose(m.geom_aabb[g, 3:], 0.01, rtol=1e-6)
    check_clear(built)


def test_mesh_piece_is_centered_on_its_centroid(tmp_path):
    # a 40 x 20 x 10 mm block authored 100 mm off the file's origin: the body's origin and center of mass sit at the
    # block's centroid, where MuJoCo puts the geom, and the inertia is about the file's axes
    v = [(x + 100, y, z) for x in (0, 40) for y in (0, 20) for z in (0, 10)]
    faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6), (0, 2, 6),
             (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    lines = [f"v {x} {y} {z}" for x, y, z in v] + [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in faces]
    (tmp_path / "block.obj").write_text("\n".join(lines) + "\n")
    piece = '[piece]\nname = "block"\nkind = "rigid"\nshape = "mesh"\nmesh = "block.obj"\nunits = "mm"\n' \
            'mass = 0.05\ninertia = [2e-6, 7e-6, 8e-6]\n'
    built = build_bench(parse_bench(tomllib.loads(HEAD + piece + SHAPES), tmp_path))
    m, lay = built.model, built.layout
    b, g = lay.piece_bodies[0], lay.piece_geoms[0]
    assert np.allclose(m.geom_pos[g], 0.0, atol=1e-9) and np.allclose(m.body_ipos[b], 0.0, atol=1e-12)
    assert np.allclose(m.body_inertia[b], [2e-6, 7e-6, 8e-6], rtol=1e-9)
    d = mujoco.MjData(m)
    mujoco.mj_kinematics(m, d)
    assert np.allclose(d.geom_xpos[g], d.xipos[b], atol=1e-9)
    # the block's long side still lies along the file's x: the body's frame is the file's
    R = d.geom_xmat[g].reshape(3, 3)
    ext = np.abs(R) @ m.geom_aabb[g, 3:]
    assert np.allclose(ext, [0.02, 0.01, 0.005], rtol=1e-6)


def test_rigid_piece_on_a_springy_bar_sinks_its_stiffness():
    # tread_solref's sag is set with the mass MuJoCo's solver uses (1 / body_invweight0), which for a body on a
    # slide is not its mass: a 0.2 kg rock with a 5000 N/m skin, resting on a springy bar, sinks m g / k
    rock = '[piece]\nname = "rock"\nkind = "rigid"\nshape = "sphere"\nsize = [0.05]\nmass = 0.2\nstiffness = 5000\n'
    text = SPRINGY.replace(BALL, rock).replace("spring_axis = [1.0, 0.0, 1.0]", "spring_axis = [0.0, 0.0, 1.0]")
    built = build_bench(bench_from(text))
    m, lay = built.model, built.layout
    bar = m.body("bar:bounce").id
    assert 1.0 / m.body_invweight0[bar, 0] > 2 * m.body_mass[bar]  # the case the solver mass matters for
    d = mujoco.MjData(m)
    q = lay.piece_qpos[0]
    d.qpos[q:q + 3] = [0.0, 0.0, 0.3 + 0.0125 + 0.025]  # on top of the bar
    sags = []
    for i in range(8000):
        mujoco.mj_step(m, d)
        if i >= 4000:  # the bar's spring is undamped: average over its bobbing
            con = [c for c in d.contact[:d.ncon] if lay.piece_geoms[0] in (c.geom1, c.geom2)]
            sags.append(-con[0].dist if con else 0.0)
    assert np.mean(sags) == pytest.approx(0.2 * 9.81 / 5000, rel=0.03)


def test_check_clear_is_exact_for_a_ball():
    """A ball's inlet must stay a radius clear of every shape, measured from the zone itself: a ball beside a bar on a
    diagonal is clear when its surface is clear (a box grown by the radius would wrongly hit the bar with a corner)."""
    r, bar_r = 0.1501 / 2, 0.0125
    text = HEAD + BALL + """
[[bar]]
name = "rail"
a = [0.0, -0.2, 0.0]
b = [0.0, 0.2, 0.0]
diameter = 0.025

[[inlet]]
name = "feed"
center = [0.0, 0.0, 0.0]
size = [0.001, 0.001, 0.001]
"""

    def at(dist):
        c = dist / math.sqrt(2)
        return build_bench(bench_from(text, **{"inlet.feed.center": [c, 0.0, c]}))

    corner = 0.0005 * math.sqrt(2)  # the zone's corner nearest the bar, on the diagonal
    check_clear(at(r + bar_r + corner + 0.0001))
    with pytest.raises(BenchError, match="inlet 'feed' overlaps shape 'rail' by 0.5 mm"):
        check_clear(at(r + bar_r + corner - 0.0005))
