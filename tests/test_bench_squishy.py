"""Squishy contacts: the launcher model's ball laws on MuJoCo geometry."""

import math
import tomllib
from pathlib import Path

import mujoco
import numpy as np
import pytest

from scrimmage.bench.build import build_bench
from scrimmage.bench.spec import parse_bench
from scrimmage.bench.squishy import SquishyContacts, _implicit
from scrimmage.launcher import contact as C

HEAD = """
[bench]
name = "squishy test"

[piece]
preset = "2026-fuel"

[[inlet]]
name = "feed"
center = [0.0, 0.0, 0.3]
size = [0.2, 0.2, 0.05]
count = 2

[[outlet]]
name = "out"
center = [0.6, 0.0, 0.0]
size = [0.1, 0.1, 0.1]
"""

PLATE = """
[[plate]]
name = "floor"
center = [0.0, 0.0, -0.005]
size = [0.4, 0.4, 0.01]
material = "polycarbonate"
"""

SEAM = """
[[plate]]
name = "left"
center = [-0.1, 0.0, -0.005]
size = [0.2, 0.4, 0.01]

[[plate]]
name = "right"
center = [0.1, 0.0, -0.005]
size = [0.2, 0.4, 0.01]
"""

WHEEL = """
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
width = 0.05
tread = "40A"
"""


def setup(text: str):
    bench = parse_bench(tomllib.loads(HEAD + text), Path("."))
    built = build_bench(bench)
    return bench, built, SquishyContacts(built, bench), mujoco.MjData(built.model)


def place(built, d, i, pos, vel=(0, 0, 0), spin=(0, 0, 0)):
    lay = built.layout
    q, v = lay.piece_qpos[i], lay.piece_qvel[i]
    d.qpos[q:q + 3] = pos
    d.qpos[q + 3:q + 7] = [1, 0, 0, 0]
    d.qvel[v:v + 3] = vel
    d.qvel[v + 3:v + 6] = spin  # local frame: the identity orientation makes it the world's


def step(m, d, sq):
    """The documented contract: zero both applied arrays, apply, step."""
    d.qfrc_applied[:] = 0.0
    d.xfrc_applied[:] = 0.0
    loads = sq.apply(d)
    mujoco.mj_step(m, d)
    return loads


def hold_parked(built, d):
    """Keep the unused pool piece still where it waits (Task 4's job; here it just must not fall into the bench)."""
    lay = built.layout
    q, v = lay.piece_qpos[1], lay.piece_qvel[1]
    d.qpos[q:q + 3] = lay.parked[1]
    d.qvel[v:v + 6] = 0.0


def static_sag(ball):
    """The squeeze x at which one rigid-surface contact carries the ball's weight."""
    w = ball.mass * 9.81
    lo, hi = 0.0, ball.diameter / 2
    for _ in range(100):
        x = 0.5 * (lo + hi)
        lo, hi = (x, hi) if C.contact_force(x, 0.0, ball) < w else (lo, x)
    return 0.5 * (lo + hi)


def settle(text: str, at_x: float = 0.0, t: float = 1.5, average: float = 0.67):
    """Drop the ball from touching onto the surface and let it settle. Hunt-Crossley damping is light at a resting
    squeeze (a 0.6 s decay for this ball), so the sag and the loads are averaged over the last `average` seconds,
    about ten periods of what is left of the bounce."""
    bench, built, sq, d = setup(text)
    m, ball = built.model, bench.piece.ball
    r = ball.diameter / 2
    place(built, d, 0, (at_x, 0.0, r))
    q = built.layout.piece_qpos[0] + 2
    n, tail = round(t / m.opt.timestep), round(average / m.opt.timestep)
    sag, loads = [], []
    for k in range(n):
        hold_parked(built, d)
        load = step(m, d, sq)
        if k >= n - tail:
            sag.append(r - d.qpos[q])
            loads.append(sum(load.values()))
    return bench, built, d, float(np.mean(sag)) if sag else None, float(np.mean(loads)) if loads else None


def test_ball_at_rest_on_a_plate():
    bench, built, d, sag, load = settle(PLATE)
    ball = bench.piece.ball
    assert sag == pytest.approx(static_sag(ball), rel=0.02)
    assert load == pytest.approx(ball.mass * 9.81, rel=0.02)


def test_drop_bounce_matches_restitution():
    bench, built, sq, d = setup(PLATE)
    m, ball = built.model, bench.piece.ball
    m.opt.gravity[:] = 0.0  # the restitution is defined for the impact alone
    place(built, d, 0, (0.0, 0.0, ball.diameter / 2 + 0.001), vel=(0.0, 0.0, -C.RESTITUTION_SPEED))
    v = built.layout.piece_qvel[0]
    touched = False
    for _ in range(20000):
        loads = step(m, d, sq)
        touched = touched or loads["floor"] > 0.0
        if touched and loads["floor"] == 0.0 and d.qvel[v + 2] > 0.0:
            break
    assert touched
    assert d.qvel[v + 2] / C.RESTITUTION_SPEED == pytest.approx(ball.restitution, abs=0.03)


def test_seam_is_not_counted_twice():
    bench, built, d, sag, load = settle(SEAM, at_x=0.0)
    assert sag == pytest.approx(static_sag(bench.piece.ball), rel=0.02)
    assert load == pytest.approx(bench.piece.ball.mass * 9.81, rel=0.02)


def test_wheel_reaction_is_equal_and_opposite():
    bench, built, sq, d = setup(WHEEL)
    m, lay, ball = built.model, built.layout, bench.piece.ball
    r, R = ball.diameter / 2, 0.05
    # pressed 4 mm into the wheel, above it and a little off-center, sliding and spinning against the wheel's turn
    c = np.array([0.01, 0.003, 0.0])
    c[2] = math.sqrt((R + r - 0.004) ** 2 - c[0] ** 2)
    place(built, d, 0, c, vel=(1.0, 0.2, -0.3), spin=(2.0, 30.0, -5.0))
    d.qvel[lay.shaft_dof["s"]] = 100.0
    d.qfrc_applied[:] = 0.0
    d.xfrc_applied[:] = 0.0
    loads = sq.apply(d)
    assert loads["w"] > 0.0
    body = lay.piece_bodies[0]
    F, T = d.xfrc_applied[body, :3], d.xfrc_applied[body, 3:]
    axis = d.xmat[m.body("shaft:s").id].reshape(3, 3)[:, 2]
    origin = d.xpos[m.body("shaft:s").id]
    moment = (np.cross(d.xipos[body] - origin, F) + T) @ axis
    assert abs(moment) > 0.1  # the wheel really drags the ball
    assert d.qfrc_applied[lay.shaft_dof["s"]] == pytest.approx(-moment, rel=1e-9)
    # only the shaft's dof gets a reaction; the pieces' dofs are untouched (their forces go in xfrc_applied)
    rest = np.delete(d.qfrc_applied, lay.shaft_dof["s"])
    assert np.all(rest == 0.0)


def test_apply_adds_to_what_is_there():
    bench, built, sq, d = setup(WHEEL)
    lay, ball = built.layout, bench.piece.ball
    place(built, d, 0, (0.0, 0.0, 0.05 + ball.diameter / 2 - 0.004), spin=(0.0, 10.0, 0.0))
    d.qvel[lay.shaft_dof["s"]] = 50.0
    d.qfrc_applied[:] = 0.0
    d.xfrc_applied[:] = 0.0
    sq.apply(d)
    once_q, once_x = d.qfrc_applied.copy(), d.xfrc_applied.copy()
    d.qfrc_applied[:] = 0.0
    d.xfrc_applied[:] = 0.0
    d.qfrc_applied[lay.shaft_dof["s"]] = 1.5  # a motor torque already written
    d.xfrc_applied[lay.piece_bodies[0], 2] = 0.25
    sq.apply(d)
    assert d.qfrc_applied[lay.shaft_dof["s"]] == pytest.approx(once_q[lay.shaft_dof["s"]] + 1.5, rel=1e-12)
    assert d.xfrc_applied[lay.piece_bodies[0], 2] == pytest.approx(once_x[lay.piece_bodies[0], 2] + 0.25, rel=1e-12)


def test_two_balls_squeeze_in_series():
    bench, built, sq, d = setup(PLATE)
    lay, ball = built.layout, bench.piece.ball
    r = ball.diameter / 2
    x = 0.006  # total approach of the two balls, high above the plate
    place(built, d, 0, (0.0, 0.0, 0.3))
    place(built, d, 1, (2 * r - x, 0.0, 0.3))
    d.qfrc_applied[:] = 0.0
    d.xfrc_applied[:] = 0.0
    loads = sq.apply(d)
    assert loads["floor"] == 0.0
    f0, f1 = d.xfrc_applied[lay.piece_bodies[0], :3], d.xfrc_applied[lay.piece_bodies[1], :3]
    # each ball takes half the approach at the same force: one rigid-surface contact at x / 2
    assert -f0[0] == pytest.approx(C.contact_force(x / 2, 0.0, ball), rel=1e-9)
    np.testing.assert_allclose(f1, -f0, rtol=0, atol=1e-12)


def test_parked_pieces_feel_nothing():
    bench, built, sq, d = setup(PLATE)
    lay = built.layout
    mujoco.mj_kinematics(built.model, d)
    d.qfrc_applied[:] = 0.0
    d.xfrc_applied[:] = 0.0
    loads = sq.apply(d)  # both pool pieces parked side by side, below the bounds
    assert loads == {"floor": 0.0}
    assert not d.xfrc_applied.any() and not d.qfrc_applied.any()


def test_work_on_piece_counts_only_moving_bodies():
    # on a still plate: no work done, though the ball settles and loses energy
    bench, built, d, _, _ = settle(PLATE, t=0.2, average=0.0)
    # (settle builds its own SquishyContacts; check a fresh one on the same state)
    sq = SquishyContacts(built, bench)
    for _ in range(100):
        hold_parked(built, d)
        step(built.model, d, sq)
    assert sq.work_on_piece[0] == 0.0
    # a ball dropped on a spinning wheel's top is flung: the wheel does positive work on it
    bench, built, sq, d = setup(WHEEL)
    m, lay, ball = built.model, built.layout, bench.piece.ball
    place(built, d, 0, (0.0, 0.0, 0.05 + ball.diameter / 2 - 0.002))
    w = lay.shaft_dof["s"]
    d.qvel[w] = -200.0  # top surface moving +x
    v = lay.piece_qvel[0]
    for _ in range(300):
        hold_parked(built, d)
        step(m, d, sq)
    ke = 0.5 * ball.mass * d.qvel[v:v + 3] @ d.qvel[v:v + 3]
    I = ball.inertia_factor * ball.mass * (ball.diameter / 2) ** 2
    ke += 0.5 * I * d.qvel[v + 3:v + 6] @ d.qvel[v + 3:v + 6]
    assert sq.work_on_piece[0] > 0.0
    assert ke < sq.work_on_piece[0]  # the contact loses some on the way
    assert sq.work_on_piece[1] == 0.0


def test_implicit_slip_solves_backward_euler():
    for s, a, scale in [(0.5, 0.0, 0.01), (0.5, 2.0, 0.01), (0.5, 0.3, 0.01), (0.003, 7.0, 0.01), (2.0, 0.05, 0.1)]:
        x = _implicit(s, a, scale)
        assert 0.0 <= x <= s
        assert x + a * math.tanh(x / scale) == pytest.approx(s, rel=1e-9, abs=1e-15)


def test_gripping_friction_settles_without_chatter():
    """A ball pressed 40 N onto a plate and sliding at 0.2 m/s: friction grips it within a few steps and then holds
    it. A gripping contact's time constant (about 3e-5 s here) is below the 1e-4 s step; taken explicitly the
    friction would overshoot every step, the slip jumping between two values."""
    bench, built, sq, d = setup(PLATE)
    m, lay, ball = built.model, built.layout, bench.piece.ball
    m.opt.gravity[:] = 0.0
    r, push = ball.diameter / 2, 40.0
    lo, hi = 0.0, r
    for _ in range(100):  # the squeeze that carries the push, so the ball starts at rest on the normal
        x = 0.5 * (lo + hi)
        lo, hi = (x, hi) if C.contact_force(x, 0.0, ball) < push else (lo, x)
    place(built, d, 0, (0.0, 0.0, r - x), vel=(0.2, 0.0, 0.0))
    body, v = lay.piece_bodies[0], lay.piece_qvel[0]
    slips = []
    for _ in range(200):
        hold_parked(built, d)
        d.qfrc_applied[:] = 0.0
        d.xfrc_applied[:] = 0.0
        d.xfrc_applied[body, 2] = -push
        sq.apply(d)
        mujoco.mj_step(m, d)
        # the ball's surface at the contact, against the still plate
        slips.append(d.qvel[v] - d.qvel[v + 4] * (r - x))  # turning about y keeps the local y the world y
    held = np.array(slips[20:])
    # gripped: the slip is held at the friction law's own scale (the rolling loss keeps a small load on it) ...
    assert np.all((held > 0.0) & (held < 2 * C.FRICTION_SLIP_SPEED))
    # ... and smoothly: explicit friction would jump about 0.02 m/s every step here
    assert np.max(np.abs(np.diff(held))) < 1e-3
    assert d.qvel[v] > -1e-3  # the rolling loss slows it to a stop; nothing drives it backwards
