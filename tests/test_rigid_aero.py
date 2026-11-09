"""A rigid piece's drag (scrimmage/launcher/rigid_aero.py), the shared 6-DOF flight (scrimmage/launcher/flight6.py),
the `[piece.*]` keys drag_coefficient and spin_damping, and a rigid NOTE flying in the world with that drag."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from range_fixtures import range_cfg
from scrimmage.launcher import flight6, rigid_aero
from scrimmage.launcher.flight6 import matrix_from_quat
from scrimmage.protocol import HalState
from scrimmage.testrange.build import BALL_BITS
from scrimmage.testrange.pieces import kind_preset, parse_kind
from scrimmage.testrange.spec import RangeError, piece_box, piece_sides
from scrimmage.world import World

IN = 0.0254
SIDE, THICK = 14 * IN, 2 * IN                       # a NOTE's bounding box: 0.3556 x 0.3556 x 0.0508 m
NOTE_BOX = (SIDE / 2, SIDE / 2, THICK / 2)          # half sizes, the ring's axis along body z
RHO = 1.2
P = Path("r.toml")
CORAL = {"shape": "tube", "diameter_in": 4.5, "length_in": 11.875, "mass": 0.7, "friction": 0.4}


def axis_angle(axis, angle: float) -> np.ndarray:
    k = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
    kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(angle) * kx + (1 - math.cos(angle)) * kx @ kx


def quat_of(rot: np.ndarray) -> np.ndarray:
    from scrimmage.disc.flight import quat_from_matrix
    return quat_from_matrix(rot)


# --------------------------------------------------------------------------------------------- the projected area

def test_note_box_is_piece_box():
    """The world's half sizes are spec.piece_box's (piece_sides sorted): the NOTE's ring axis is body z."""
    note = kind_preset("2024-note")
    np.testing.assert_allclose(piece_box(note), (SIDE, SIDE, THICK), atol=1e-9)
    assert piece_sides(note) == tuple(sorted(piece_box(note)))
    np.testing.assert_allclose(rigid_aero.half_extents(note), NOTE_BOX, atol=1e-9)


def test_projected_area_face_on_edge_on_and_at_45():
    face = rigid_aero.projected_area(NOTE_BOX, (0.0, 0.0, 3.0))
    assert face == pytest.approx(SIDE * SIDE, rel=1e-12)
    for edge_dir in ((1.0, 0.0, 0.0), (0.0, -2.0, 0.0)):
        assert rigid_aero.projected_area(NOTE_BOX, edge_dir) == pytest.approx(SIDE * THICK, rel=1e-12)
    edge = SIDE * THICK
    s = math.sqrt(0.5)
    at45 = rigid_aero.projected_area(NOTE_BOX, (s, 0.0, s))     # tipped 45 deg from face-on toward an edge
    assert edge < at45 < face
    assert at45 == pytest.approx(face * s + edge * s, rel=1e-12)   # the |n . v_hat| sum
    v = np.array([0.3, -0.5, 0.8])
    v_hat = v / np.linalg.norm(v)
    want = SIDE * THICK * abs(v_hat[0]) + SIDE * THICK * abs(v_hat[1]) + SIDE * SIDE * abs(v_hat[2])
    assert rigid_aero.projected_area(NOTE_BOX, v) == pytest.approx(want, rel=1e-12)
    assert rigid_aero.projected_area(NOTE_BOX, (0.0, 0.0, 0.0)) == 0.0


def test_projected_area_turns_with_the_piece():
    """forces() reads the area in the body frame: a NOTE tipped 90 deg about x flies edge-on along world z."""
    rot = axis_angle((1, 0, 0), math.pi / 2)
    f, _ = rigid_aero.forces(NOTE_BOX, 1.0, 0.0, 2.0, (0.0, 0.0, 1.0), rot, np.zeros(3))
    assert -f[2] == pytest.approx(0.5 * 2.0 * SIDE * THICK, rel=1e-12)
    f, _ = rigid_aero.forces(NOTE_BOX, 1.0, 0.0, 2.0, (0.0, 0.0, 1.0), np.eye(3), np.zeros(3))
    assert -f[2] == pytest.approx(0.5 * 2.0 * SIDE * SIDE, rel=1e-12)


# -------------------------------------------------------------------------------------------------- the force

def test_drag_opposes_the_velocity_with_half_rho_cd_a_v_squared():
    rng = np.random.default_rng(7)
    for _ in range(50):
        rot = axis_angle(rng.normal(size=3), rng.uniform(0, math.pi))
        v = rng.normal(size=3) * 8.0
        cd = rng.uniform(0.5, 1.5)
        f, tau = rigid_aero.forces(NOTE_BOX, cd, 0.0, RHO, v, rot, rng.normal(size=3))
        speed = np.linalg.norm(v)
        a = rigid_aero.projected_area(NOTE_BOX, rot.T @ v)
        assert np.linalg.norm(f) == pytest.approx(0.5 * RHO * cd * a * speed ** 2, rel=1e-12)
        np.testing.assert_allclose(f / np.linalg.norm(f), -v / speed, atol=1e-12)
        assert not tau.any()
    f, tau = rigid_aero.forces(NOTE_BOX, 1.17, 0.0, RHO, np.zeros(3), np.eye(3), np.zeros(3))
    assert not f.any() and not tau.any()


def test_spin_damping_is_zero_by_default_and_minus_c_omega_when_set():
    note = kind_preset("2024-note")
    assert note.spin_damping == 0.0 and rigid_aero.SPIN_DAMPING == 0.0
    w = np.array([1.0, -20.0, 3.0])
    _, tau = rigid_aero.forces(NOTE_BOX, note.drag_coefficient, note.spin_damping, RHO, (5.0, 0, 0), np.eye(3), w)
    assert not tau.any()
    _, tau = rigid_aero.forces(NOTE_BOX, 1.17, 0.002, RHO, (5.0, 0, 0), np.eye(3), w)
    np.testing.assert_allclose(tau, -0.002 * w, rtol=1e-12)
    _, tau = rigid_aero.forces(NOTE_BOX, 1.17, 0.002, RHO, np.zeros(3), np.eye(3), w)   # at rest too
    np.testing.assert_allclose(tau, -0.002 * w, rtol=1e-12)


def test_force_fn_works_in_the_body_frame():
    """flight6's force function: body angular velocity in, body torque out; the same force as forces()."""
    kind = parse_kind(P, "n", {"preset": "2024-note", "spin_damping": 0.01}, Path("."))
    fn = rigid_aero.force_fn(kind, RHO)
    rot = axis_angle((0.2, 1.0, 0.4), 0.9)
    w_body, v = np.array([0.5, 2.0, -30.0]), np.array([6.0, 1.0, 3.0])
    f, tau_body = fn(v, rot, w_body)
    f_w, tau_w = rigid_aero.forces(NOTE_BOX, 1.17, 0.01, RHO, v, rot, rot @ w_body)
    np.testing.assert_allclose(f, f_w, rtol=1e-9)
    np.testing.assert_allclose(tau_body, -0.01 * w_body, rtol=1e-9)


# ------------------------------------------------------------------------------------------------- the keys

def test_air_keys_default_and_set():
    note = kind_preset("2024-note")
    assert note.drag_coefficient == 1.17 == rigid_aero.DRAG_COEFFICIENT
    coral = parse_kind(P, "c", {**CORAL, "drag_coefficient": 0.9, "spin_damping": 0.001}, Path("."))
    assert (coral.drag_coefficient, coral.spin_damping) == (0.9, 0.001)
    preset = parse_kind(P, "n", {"preset": "2024-note", "drag_coefficient": 0}, Path("."))
    assert preset.drag_coefficient == 0.0 and preset.spin_damping == 0.0       # 0: no drag, allowed


@pytest.mark.parametrize("key, value, match", [
    ("drag_coefficient", -0.5, r"\[piece\.c\]: drag_coefficient can't be negative, not -0\.5"),
    ("spin_damping", -1, r"\[piece\.c\]: spin_damping can't be negative, not -1"),
    ("drag_coefficient", "big", r"\[piece\.c\]: drag_coefficient must be a number, not 'big'"),
    ("spin_damping", True, r"\[piece\.c\]: spin_damping must be a number, not True"),
    ("drag_coefficient", math.inf, r"\[piece\.c\]: drag_coefficient must be a number, not inf"),
    ("drag_coefficient", math.nan, r"\[piece\.c\]: drag_coefficient must be a number, not nan"),
])
def test_air_keys_refused_in_words(key, value, match):
    with pytest.raises(RangeError, match=match):
        parse_kind(P, "c", {**CORAL, key: value}, Path("."))


def test_air_keys_refused_on_a_ball_or_disc():
    with pytest.raises(RangeError, match=r"drag_coefficient is a rigid piece's air drag; a ball flies with the "
                                         r"\[air\] drag and a disc with its aero set"):
        parse_kind(P, "b", {"ball": "2026-fuel", "drag_coefficient": 0.5}, Path("."))
    with pytest.raises(RangeError, match=r"spin_damping is a rigid piece's air drag"):
        parse_kind(P, "d", {"disc": "2013-frisbee", "spin_damping": 0.5}, Path("."))


# ---------------------------------------------------------------------------------------------- the flight

def no_force(vel, rot, w):
    return np.zeros(3), np.zeros(3)


def test_flight6_without_forces_is_a_parabola_and_keeps_angular_momentum():
    """An asymmetric full tensor, tumbling: the center flies an exact parabola and H = R I w stays put."""
    rot0 = axis_angle((1.0, 2.0, 0.5), 0.7)
    inertia = rot0 @ np.diag([0.002, 0.004, 0.0055]) @ rot0.T
    w0 = np.array([3.0, -8.0, 20.0])
    pos, vel = np.array([0.0, 0.0, 1.0]), np.array([6.0, 1.0, 4.0])
    out = flight6.fly(pos, vel, (1.0, 0.0, 0.0, 0.0), w0, 0.235, inertia, no_force, max_time=1.0)
    assert out.stopped == "max_time" and out.landing is None
    t = out.samples[:, 0]
    want = pos + vel * t[:, None] + np.outer(t * t, [0.0, 0.0, -0.5 * flight6.GRAVITY])
    np.testing.assert_allclose(out.samples[:, flight6.POS], want, atol=1e-9)
    h0 = inertia @ w0
    for row in out.samples[::100]:
        r = matrix_from_quat(row[flight6.QUAT])
        np.testing.assert_allclose(r @ inertia @ row[flight6.OMEGA], h0, rtol=1e-6, atol=1e-9)


def test_flight6_diagonal_and_full_inertia_agree():
    diag = np.array([0.002, 0.002, 0.004])
    w0 = np.array([0.5, 0.2, 30.0])
    a = flight6.fly((0, 0, 1), (5, 0, 3), (1, 0, 0, 0), w0, 0.2, diag, no_force, max_time=0.5)
    b = flight6.fly((0, 0, 1), (5, 0, 3), (1, 0, 0, 0), w0, 0.2, np.diag(diag), no_force, max_time=0.5)
    np.testing.assert_allclose(a.samples, b.samples, rtol=1e-9, atol=1e-12)


def test_flight6_floor_and_until():
    out = flight6.fly((0, 0, 1), (3, 0, 0), (1, 0, 0, 0), np.zeros(3), 1.0, np.ones(3), no_force, floor=0.0)
    assert out.stopped == "floor"
    t = math.sqrt(2.0 / flight6.GRAVITY)
    assert out.flight_time == pytest.approx(t, abs=1e-9)
    np.testing.assert_allclose(out.landing, (3 * t, 0, 0), atol=1e-9)
    out = flight6.fly((0, 0, 1), (3, 0, 0), (1, 0, 0, 0), np.zeros(3), 1.0, np.ones(3), no_force, floor=0.0,
                      until=lambda pos, prev: pos[0] >= 1.0 > prev[0])
    assert out.stopped == "until" and out.landing is None
    assert out.pos[0] >= 1.0 > out.samples[-2, 1]


def test_flight6_drag_lands_short():
    note = kind_preset("2024-note")
    fn = rigid_aero.force_fn(note, RHO)
    vel = 10.0 * np.array([math.cos(math.radians(30)), 0.0, math.sin(math.radians(30))])
    drag = flight6.fly((0, 0, 1), vel, (1, 0, 0, 0), np.zeros(3), note.mass, note.inertia[:3], fn, floor=0.3)
    free = flight6.fly((0, 0, 1), vel, (1, 0, 0, 0), np.zeros(3), note.mass, note.inertia[:3], no_force, floor=0.3)
    assert drag.landing[0] < free.landing[0] - 1.0


# ---------------------------------------------------------------------------------------------- the world

NOTE_FIELD = '[range]\nname = "n"\n\n[piece.note]\npreset = "2024-note"\n\n[[floor_balls]]\nat = [[12.0, 6.0]]\n' \
             'tip_deg = 90\n'
START = np.array([2.0, 2.0, 1.0])       # clear of the robot (at y 4.035) and the walls
LANDING_Z = 0.3                         # the center's height read as landed: above any edge touching the carpet
UP_DEG, SPEED = 30.0, 10.0


@pytest.fixture(scope="module")
def note_world(tmp_path_factory):
    path = tmp_path_factory.mktemp("note") / "field.toml"
    path.write_text(NOTE_FIELD, encoding="utf-8")
    return World(range_cfg(range={"file": str(path), "floor_balls": True}), None)


def throw(w, quat) -> float:
    """Throw slot 0 from START at SPEED, UP_DEG up along +x, turned to `quat`, no spin: x where the center falls
    through LANDING_Z (interpolated in the step)."""
    b = w.test_range.balls
    a = math.radians(UP_DEG)
    b.place(0, START, SPEED * np.array([math.cos(a), 0.0, math.sin(a)]), (0.0, 0.0, 0.0), BALL_BITS, quat=quat)
    prev = b.positions()[0].copy()
    for _ in range(3000):
        w.step(HalState())
        p = b.positions()[0].copy()
        if p[2] < LANDING_Z <= prev[2]:
            f = (prev[2] - LANDING_Z) / (prev[2] - p[2])
            return float(prev[0] + f * (p[0] - prev[0]))
        prev = p
    raise AssertionError("the NOTE never came down")


def pitched(up: float) -> np.ndarray:
    """Body x pitched `up` radians above +x (body z tipped back the same)."""
    return quat_of(np.array([[math.cos(up), 0, -math.sin(up)], [0, 1, 0], [math.sin(up), 0, math.cos(up)]]))


def gravity_only_x() -> float:
    a = math.radians(UP_DEG)
    vz = SPEED * math.sin(a)
    t = (vz + math.sqrt(vz * vz + 2 * flight6.GRAVITY * (START[2] - LANDING_Z))) / flight6.GRAVITY
    return float(START[0] + SPEED * math.cos(a) * t)


def test_a_thrown_note_lands_short_of_gravity_alone(note_world):
    flat = throw(note_world, pitched(math.radians(UP_DEG)))
    assert flat < gravity_only_x() - 1.0        # measured: 7.4 m against 11.6 m


def test_a_note_flying_in_its_own_plane_goes_further_than_face_first(note_world):
    """Flat and moving in its own plane, the air meets its 0.018 m^2 edge; face first, its 0.126 m^2 face."""
    flat = throw(note_world, pitched(math.radians(UP_DEG)))
    face = throw(note_world, pitched(math.radians(UP_DEG) - math.pi / 2))
    assert flat > face + 1.0                    # measured: 7.4 m against 5.5 m


@pytest.mark.parametrize("extra", [0.0, -math.pi / 2, -math.pi / 4])
def test_the_world_and_flight6_agree(note_world, extra):
    """The same throw in MuJoCo (implicitfast, the drag held over each 1 ms step) and in flight6 (RK4): within
    3 cm. Measured: 7 mm flat, 12 mm face first."""
    q = pitched(math.radians(UP_DEG) + extra)
    world = throw(note_world, q)
    note = note_world.test_range.balls.kinds["note"]
    a = math.radians(UP_DEG)
    xx, yy, zz, xy, xz, yz = note.inertia
    out = flight6.fly(START, SPEED * np.array([math.cos(a), 0.0, math.sin(a)]), q, np.zeros(3), note.mass,
                      np.array([[xx, xy, xz], [xy, yy, yz], [xz, yz, zz]]),
                      rigid_aero.force_fn(note, note_world.test_range.balls.air.density), floor=LANDING_Z)
    assert out.landing[0] == pytest.approx(world, abs=0.03)


def test_a_resting_note_feels_no_air(note_world):
    """Lying on the carpet it touches it: no applied force, so it stays put as before."""
    b = note_world.test_range.balls
    note = b.kinds["note"]
    b.place(0, (12.0, 6.0, b.rest_z_of(0)), (0.3, 0.0, 0.0), (0.0, 0.0, 0.0), BALL_BITS, quat=note.orient(90.0, 0.0))
    for _ in range(300):
        note_world.step(HalState())
    b.apply_flight_forces()
    assert not b._xfrc[0].any()
