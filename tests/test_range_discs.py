"""Discs and slot goals in the world: the 2013 range, disc free bodies under Hummel's aerodynamics, and scoring.

tests/data/range_ball_golden.npz holds the final qpos and goal counts of a 2026 ball run (see _ball_range_run),
written by running this module:
    .venv/bin/python tests/test_range_discs.py
It was written by the code before discs existed, so it pins ball ranges as they were.
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import mujoco
import numpy as np
import pytest

from range_fixtures import drive, range_cfg, spares_world
from scrimmage.disc import flight
from scrimmage.disc.aero import RHO
from scrimmage.disc.inputs import Disc, DiscAir, aero_set, disc_preset
from scrimmage.protocol import HalState
from scrimmage.testrange.balls import BallState, Balls
from scrimmage.testrange.build import BALL_BITS, add_range
from scrimmage.testrange.spec import RectOpening, load_range
from scrimmage.world import World

ROOT = Path(__file__).resolve().parent.parent
BALL_GOLDEN = ROOT / "tests" / "data" / "range_ball_golden.npz"


IN = 0.0254
WALL_X = 16.54          # the goal wall: the generated field's red alliance wall (2013.toml)
HIGH = (WALL_X, 4.035, (104.125 + 6.0) * IN)     # the HIGH goal opening's center
FRISBEE = disc_preset("2013-frisbee")


# ---------------------------------------------------------------------------------------------------- the range

def test_2013_range_loads_and_builds():
    rng = load_range("2013", ROOT)
    assert isinstance(rng.piece, Disc) and rng.disc == FRISBEE and rng.ball is None
    assert rng.piece_preset == "2013-frisbee"
    goals = {g.name: g for g in rng.goals}
    assert set(goals) == {"high", "middle_left", "middle_right", "low"}
    high = goals["high"]
    assert isinstance(high.opening, RectOpening)
    assert (high.opening.width, high.opening.height) == pytest.approx((54 * IN, 12 * IN))
    assert high.rim_height == pytest.approx(HIGH[2], abs=1e-3)
    for name, size in (("middle_left", (54, 21)), ("middle_right", (54, 21)), ("low", (29, 24))):
        o = goals[name].opening
        assert (o.width, o.height) == pytest.approx((size[0] * IN, size[1] * IN))
    assert goals["middle_left"].pos[1] - high.pos[1] == pytest.approx(58 * IN, abs=1e-3)
    for g in goals.values():       # every opening is on the wall plane, scored from the field side (-x)
        s = g.scoring_surface()
        np.testing.assert_allclose(s.normal, [-1.0, 0.0, 0.0], atol=1e-12)
        assert s.center[0] == pytest.approx(WALL_X)
    s = high.scoring_surface()     # the rectangle: 54 in across (y), 12 in tall (z)
    corners = s.center + s.polygon @ s.axes
    assert np.ptp(corners[:, 1]) == pytest.approx(54 * IN) and np.ptp(corners[:, 2]) == pytest.approx(12 * IN)
    assert corners[:, 2].min() == pytest.approx(104.125 * IN, abs=1e-3)

    w = World(range_cfg(range={"file": "2013", "start": "auto_line_left"}), None)
    names = {mujoco.mj_id2name(w.model, mujoco.mjtObj.mjOBJ_GEOM, g) for g in range(w.model.ngeom)}
    for want in ("high.frame_top", "high.frame_bottom", "high.frame_left", "high.frame_right", "high.box_top",
                 "high.box_bottom", "high.box_left", "high.box_right", "high.back", "alliance_wall.box",
                 "pyramid_leg_0.box", "pyramid_base.box"):
        assert want in names, want
    assert w.robot_pose()[0] == pytest.approx(rng.starts["auto_line_left"][0])
    hal = HalState()
    for _ in range(200):
        w.step(hal)
    assert np.all(np.isfinite(w.data.qpos))


def test_disc_range_rejects_both_pieces(tmp_path):
    from scrimmage.testrange.spec import RangeError
    text = (ROOT / "scrimmage/testrange/ranges/2013.toml").read_text(encoding="utf-8")
    bad = tmp_path / "both.toml"
    bad.write_text(text.replace('disc = "2013-frisbee"', 'disc = "2013-frisbee"\nball = "2026-fuel"', 1))
    with pytest.raises(RangeError, match="both ball and disc"):
        load_range(str(bad), tmp_path)
    bad.write_text(text.replace('disc = "2013-frisbee"', 'disc = "nope"', 1))
    with pytest.raises(RangeError, match="unknown disc preset 'nope'"):
        load_range(str(bad), tmp_path)


SLOT_GOAL = ('[[goal]]\nname = "g"\npos = [8.0, 4.0]\ntilt_deg = 90.0\nrim_height = 2.0\n'
             'opening = { kind = "rect", width = 1.0, height = 0.3 }\n')


@pytest.mark.parametrize("extra, match", [
    ('funnel = { depth = 0.1, slope_deg = 60.0 }\n', "rect opening with a funnel"),
    ('slot = { depth = 0.6 }\n', "missing required key 'frame'"),
    ('[[obstacle]]\nname = "o"\npos = [1.0, 1.0, 0.5]\nfrom = [0.0, 0.0, 0.0]\n', "one of a box"),
    ('[[obstacle]]\nname = "g"\npos = [1.0, 1.0, 0.5]\nsize = [1.0, 1.0, 1.0]\n', "shares its name"),
    ('[[obstacle]]\nname = "o"\nfrom = [1.0, 1.0, 0.5]\nto = [1.0, 1.0, 0.5]\nwidth = 0.1\n', "the same point"),
])
def test_slot_and_obstacle_mistakes_named(tmp_path, extra, match):
    from scrimmage.testrange.spec import RangeError
    f = tmp_path / "r.toml"
    f.write_text('[range]\nname = "r"\ndisc = "2013-frisbee"\n' + SLOT_GOAL + extra)
    with pytest.raises(RangeError, match=match):
        load_range(str(f), tmp_path)


def test_polygon_slot_and_bar_obstacle():
    """slot belongs to a rect opening; a bar obstacle runs from its `from` to its `to`."""
    from scrimmage.testrange.spec import RangeError, parse_range
    raw = {"range": {"name": "r", "ball": "2026-fuel"},
           "goal": [{"name": "g", "pos": [8.0, 4.0], "rim_height": 2.0, "opening": {"sides": 6, "across_flats": 1.0},
                     "slot": {"depth": 0.6, "frame": 0.1}}]}
    with pytest.raises(RangeError, match="slot = .* is a rect opening's"):
        parse_range(raw, Path("r.toml"))
    raw = {"range": {"name": "r", "ball": "2026-fuel"},
           "obstacle": [{"name": "leg", "from": [1.0, 2.0, 0.0], "to": [0.5, 2.5, 2.0], "width": 0.04}]}
    o = parse_range(raw, Path("r.toml")).obstacles[0]
    axis = o.rotation()[:, 2] * o.size[2] / 2.0
    np.testing.assert_allclose(np.array(o.pos) + axis, [0.5, 2.5, 2.0], atol=1e-12)
    np.testing.assert_allclose(np.array(o.pos) - axis, [1.0, 2.0, 0.0], atol=1e-12)


# ---------------------------------------------------------------------------------------------------- a disc world

def disc_world(n_slots: int = 1, floor=(), aero: str = "pc-long", density: float = RHO):
    """The generated field with the 2013 range's goals, wall and pyramid, and `n_slots` parked discs (after any
    `floor` discs, lying flat), in air of `density`; the assembler's solver options. (model, data, Balls)."""
    rng = load_range("2013", ROOT)
    rng = dataclasses.replace(rng, floor_balls=np.array(floor, dtype=float).reshape(-1, 2), aero=aero_set(aero))
    spec = mujoco.MjSpec()
    spec.compiler.degree = False
    spec.option.timestep = 0.001
    spec.option.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST
    spec.option.cone = mujoco.mjtCone.mjCONE_ELLIPTIC
    spec.option.impratio = 10
    from scrimmage.parts.field import add_field
    add_field(spec, {}, obstacle_default=False)
    layout = add_range(spec, rng, n_slots)
    m = spec.compile()
    d = mujoco.MjData(m)
    balls = Balls(m, d, layout, rng, DiscAir(density=density))
    for i, (x, y) in enumerate(rng.floor_balls):
        balls.put(i, (float(x), float(y), balls.rest_z), BALL_BITS)
    mujoco.mj_forward(m, d)
    return m, d, balls


def step(m, d, balls) -> list:
    """One physics step as RangeRuntime takes it."""
    prev = balls.positions()
    balls.apply_flight_forces()
    balls.apply_rolling_resistance()
    mujoco.mj_step(m, d)
    return balls.after_step(prev)


def launch(balls, i, state: flight.DiscState) -> None:
    rot = flight.matrix_from_quat(state.quat)
    balls.place(i, state.pos, state.vel, rot @ state.omega_body, BALL_BITS, quat=state.quat)


def tilted_state(pos, speed, pitch_deg, spin, bank_deg=0.0) -> flight.DiscState:
    """A disc flying along +x at `speed`, its velocity and its plane pitched up `pitch_deg`, banked `bank_deg` about
    the velocity (right side down for positive), spinning `spin` rad/s about its axis (+: counter-clockwise from
    above)."""
    p, b = math.radians(pitch_deg), math.radians(bank_deg)
    ry = np.array([[math.cos(p), 0, -math.sin(p)], [0, 1, 0], [math.sin(p), 0, math.cos(p)]])
    rx = np.array([[1, 0, 0], [0, math.cos(b), -math.sin(b)], [0, math.sin(b), math.cos(b)]])
    rot = ry @ rx
    return flight.DiscState(pos=np.array(pos, dtype=float), vel=speed * rot[:, 0], quat=flight.quat_from_matrix(rot),
                            omega_body=np.array([0.0, 0.0, spin]))


def _world_and_standalone(state, travel: float):
    """The world's flight of `state` (positions and quaternions, a row per step: t, pos, quat) until its center has
    gone `travel` downrange, and the standalone flight's samples at the same times."""
    ref = flight.fly(state, FRISBEE, aero_set("pc-long"), RHO, max_time=3.0)
    m, d, balls = disc_world()
    launch(balls, 0, state)
    rows = [np.concatenate([[0.0], balls.positions()[0], balls._qpos[0, 3:7]])]
    while balls.positions()[0][0] < state.pos[0] + travel:
        step(m, d, balls)
        assert balls.state[0] == BallState.FIELD and d.ncon == 0, "the disc hit something"
        rows.append(np.concatenate([[d.time], balls.positions()[0], balls._qpos[0, 3:7]]))
    rows = np.array(rows)
    assert np.allclose(ref.samples[:len(rows), 0], rows[:, 0], atol=1e-9)
    return rows, ref.samples[:len(rows)]


def test_world_flight_matches_standalone_within_5cm():
    """The same exit state (the measured FRC shot: 12.2 m/s from 0.76 m, here 12 deg up, at an advance ratio near 1)
    flown by MuJoCo (implicitfast, 1 ms, the aerodynamics applied once per step) and by the standalone RK4: within
    5 cm over 5 m of travel, banked to the same side. Then on to 13 m downrange (over the pyramid, under its top),
    where the curve is centimeters: it drifts to the same side. Flown from the blue wall, where the 2013 range has
    nothing to hit. Measured: 1.4 mm over 5 m and 7 mm over 13 m, for either spin."""
    for spin in (90.0, -90.0):
        state = tilted_state((1.0, 4.035, 0.76), 12.2, 12.0, spin)
        world, alone = _world_and_standalone(state, 13.0)
        n = int(np.argmax(world[:, 1] >= state.pos[0] + 5.0)) + 1      # the steps to 5 m
        dev = np.linalg.norm(world[:, 1:4] - alone[:, flight.POS], axis=1)
        assert dev[:n].max() < 0.05
        # the same bank side: the disc's axis leans the same way at 5 m (its y component: the bank across the flight)
        lean_w = flight.matrix_from_quat(world[n - 1, 4:8])[1, 2]
        lean_s = flight.matrix_from_quat(alone[n - 1, flight.QUAT])[1, 2]
        assert abs(lean_s) > 0.01 and np.sign(lean_w) == np.sign(lean_s)
        drift_w, drift_s = world[-1, 2] - 4.035, alone[-1, 2] - 4.035
        assert abs(drift_s) > 0.02 and np.sign(drift_w) == np.sign(drift_s)
        assert dev.max() < 0.05


def test_world_flight_without_aero_is_a_parabola():
    state = tilted_state((1.0, 4.035, 0.76), 10.0, 20.0, 80.0)
    m, d, balls = disc_world(aero="none")
    launch(balls, 0, state)
    for _ in range(300):
        step(m, d, balls)
    t = d.time
    want = state.pos + state.vel * t + np.array([0.0, 0.0, -0.5 * 9.81 * t * t])
    np.testing.assert_allclose(balls.positions()[0], want, atol=0.01)
    assert not balls._xfrc[0].any()


def _fly_at_wall(state, seconds=1.0):
    m, d, balls = disc_world()
    launch(balls, 0, state)
    events, hits = [], set()
    for _ in range(int(seconds / m.opt.timestep)):
        events += step(m, d, balls)
        for c in d.contact[:d.ncon]:
            hits.update(mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, int(g)) for g in c.geom)
    return balls, events, hits


def test_disc_through_high_goal_scores():
    state = tilted_state((WALL_X - 1.5, HIGH[1], HIGH[2] - 0.02), 10.0, 0.0, 80.0)
    balls, events, _ = _fly_at_wall(state)
    assert events == [(0, "scored")]
    assert balls.goal_counts == {"high": 1, "middle_left": 0, "middle_right": 0, "low": 0}
    assert balls.state[0] == BallState.SCORED and balls.log[0].goal == "high"


def test_disc_hitting_the_frame_bounces_and_does_not_score():
    """Aimed at the frame's top bar, just above the HIGH opening: it bounces back onto the field."""
    state = tilted_state((WALL_X - 1.0, HIGH[1], HIGH[2] + 6 * IN + 2 * IN), 8.0, 0.0, 80.0)
    balls, events, hits = _fly_at_wall(state, 0.6)
    assert "high.frame_top" in hits
    assert events == [] and sum(balls.goal_counts.values()) == 0
    assert balls.state[0] == BallState.FIELD
    assert balls.positions()[0][0] < WALL_X - 0.1
    assert balls._qvel[0, 0] < 0.0              # heading back down the field


def test_disc_resting_flat_does_not_drift():
    m, d, balls = disc_world(n_slots=0, floor=[(8.0, 4.0)])
    start = balls.positions()[0].copy()
    assert start[2] == pytest.approx(FRISBEE.height / 2)
    for _ in range(2000):
        step(m, d, balls)
    assert np.linalg.norm(balls.positions()[0] - start) < 1e-3
    assert not balls._xfrc[0].any()


def test_disc_air_density_is_the_configured_one():
    """In-world disc aerodynamics use the Balls' air density (as ball flight does), not the module's constant."""
    state = tilted_state((1.0, 4.035, 1.5), 12.0, 10.0, 80.0)
    forces = []
    for density in (RHO, 2.0 * RHO):
        m, d, balls = disc_world(density=density)
        launch(balls, 0, state)
        balls.apply_flight_forces()
        forces.append(balls._xfrc[0].copy())
    assert np.linalg.norm(forces[0][:3]) > 0.1
    # the force is q A C: linear in the density (the torque's spin-down term, Hummel's dimensional C_Nr, isn't)
    np.testing.assert_allclose(forces[1][:3], 2.0 * forces[0][:3], rtol=1e-12)


@pytest.mark.parametrize("y, z, spin", [
    (HIGH[1], 2.2, 80.0),               # under the HIGH goal, above the wall
    (HIGH[1] + 1.47, 2.95, 80.0),       # above middle_left's opening and frame
    (1.55, 2.5, -80.0),                 # beside middle_right's frame, toward the wall's end
])
def test_backing_plate_returns_a_miss_above_the_wall(y, z, spin):
    """A disc into the goal band but through no opening hits the backing plate and comes back onto the field (it used
    to pass between the goals and leave). Near the wall's end it is spun so its rim rolls it back toward the
    field's middle: spun the other way the rim's kick along the plate carries it over the low side wall."""
    state = tilted_state((WALL_X - 1.5, y, z), 9.0, 0.0, spin)
    balls, events, hits = _fly_at_wall(state, 0.6)
    assert events == [] and sum(balls.goal_counts.values()) == 0
    assert any(h.startswith("goal_backing") for h in hits if h), hits
    assert balls.state[0] == BallState.FIELD
    assert balls.positions()[0][0] < WALL_X - 0.1


def test_starts_are_in_the_auto_zone_clear_of_the_pyramid():
    """Every start is in the blue AUTO ZONE (between the red wall and the blue auto line, MAN-6), and a 0.9 m square
    robot there (turned to its heading) clears the pyramid's base; the robot stays put on its start."""
    rng = load_range("2013", ROOT)
    auto_line = WALL_X - 216 * IN
    pyramid = {o.name: o for o in rng.obstacles}["pyramid_base"]
    lo = np.array(pyramid.pos[:2]) - np.array(pyramid.size[:2]) / 2
    hi = np.array(pyramid.pos[:2]) + np.array(pyramid.size[:2]) / 2
    assert rng.starts
    for name, (x, y, heading) in rng.starts.items():
        h = math.radians(heading)
        rot = np.array([[math.cos(h), -math.sin(h)], [math.sin(h), math.cos(h)]])
        corners = np.array([x, y]) + np.array([[sx, sy] for sx in (-0.45, 0.45) for sy in (-0.45, 0.45)]) @ rot.T
        assert (corners[:, 0] > auto_line).all() and (corners[:, 0] < WALL_X).all(), name
        # separated from the base's square along x or y (the base is axis-aligned)
        assert (corners[:, 0] > hi[0]).all() or (corners[:, 0] < lo[0]).all() or (corners[:, 1] > hi[1]).all() \
            or (corners[:, 1] < lo[1]).all(), name
        w = World(range_cfg(range={"file": "2013", "start": name}), None)
        hal = HalState()
        for _ in range(300):
            w.step(hal)
        assert np.hypot(w.robot_pose()[0] - x, w.robot_pose()[1] - y) < 0.01, name


# ---------------------------------------------------------------------------------------------------- ball ranges

def _ball_range_run() -> World:
    """The kitbot on the 2026 range, driving at half speed, with three spare balls in play: a lob with backspin
    toward the blue hub, a ball rolling on the carpet and one dropped on the rim. 1.5 s."""
    w = spares_world(range_cfg())
    balls = w.test_range.balls
    free = [int(i) for i in np.flatnonzero(balls.state == BallState.SPARE)[:3]]
    balls.place(free[0], np.array([2.0, 4.035, 0.6]), np.array([5.6, 0.0, 6.0]), np.array([0.0, -200.0, 0.0]),
                BALL_BITS)
    balls.place(free[1], np.array([6.0, 1.0, 0.0751]), np.array([2.0, 0.5, 0.0]), np.array([6.0, -26.0, 0.0]),
                BALL_BITS)
    balls.place(free[2], np.array([4.625, 4.035 + 0.57, 2.2]), np.zeros(3), np.zeros(3), BALL_BITS)
    drive(w, 1.5, p0=0.5, p1=-0.5)
    return w


def write_ball_golden() -> None:
    w = _ball_range_run()
    goals = w.test_range.counts()["goals"]
    np.savez(BALL_GOLDEN, qpos=w.data.qpos.copy(), qvel=w.data.qvel.copy(),
             goals=np.array([goals[k] for k in sorted(goals)]))


def test_ball_ranges_unchanged():
    """A 2026 ball run ends where the code before discs left it (and the rangeless goldens, test_range_model.py)."""
    golden = np.load(BALL_GOLDEN)
    w = _ball_range_run()
    goals = w.test_range.counts()["goals"]
    np.testing.assert_allclose(w.data.qpos, golden["qpos"], rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(w.data.qvel, golden["qvel"], rtol=1e-12, atol=1e-12)
    assert [goals[k] for k in sorted(goals)] == golden["goals"].tolist()


if __name__ == "__main__":
    write_ball_golden()
