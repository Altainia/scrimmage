"""Tread wheels: drive wheels flagged `tread`, their contact a spring of the drivetrain's tread stiffness and damping."""

import math

import mujoco
import pytest

from scrimmage.parts.assemble import TREAD_STIFFNESS, assemble, tread_solref
from scrimmage.protocol import HalState
from scrimmage.world import World

G = 9.81
SWERVE = [{"name": "drive", "stock": "chassis"}] + [
    {"name": c, "stock": "swerve/MK4i", "attach": f"drive.{m}"}
    for c, m in (("fl", "front_left"), ("fr", "front_right"), ("bl", "back_left"), ("br", "back_right"))]
KITBOT = [{"name": "drive", "stock": "chassis", "length": 0.66, "width": 0.6, "mass": 47.0, "frame_height": 0.1},
          {"name": "dt", "stock": "differential", "attach": "drive.center"}]
BUMPER_CLEARANCE = 0.019     # m: the stock chassis's bumpers above the carpet, resting
TEST_WORLD = {"field": {"obstacle": False}, "robot": {"start_pose": [2.0, 4.0, 0.0]}}


def world(parts) -> World:
    return World({"part": parts, **TEST_WORLD}, None)


def step(w: World, seconds: float, record=None):
    hal = HalState()
    for _ in range(round(seconds / w.model.opt.timestep)):
        w.step(hal)
        if record is not None:
            record.append(float(w.data.xpos[w.robot_body][2]))


def robot_mass(w: World) -> float:
    return float(w.model.body_subtreemass[w.robot_body])


def test_stock_wheels_are_tread():
    swerve = assemble({"part": SWERVE}, None)
    assert sorted(swerve.tread_geoms) == [f"{c}.wheel.tread0" for c in ("bl", "br", "fl", "fr")]
    tank = assemble({"part": [KITBOT[0], {**KITBOT[1], "wheels_per_side": 4}]}, None)
    assert sorted(tank.tread_geoms) == sorted(f"dt.{s}_{i}.tread0" for s in ("left", "right") for i in (1, 2, 3, 4))
    for name in swerve.tread_geoms:
        assert swerve.model.geom(name).id >= 0


def test_tread_solref_follows_stiffness_and_mass():
    soft, stiff = tread_solref(1e5, 1.0, 15.0), tread_solref(4e5, 1.0, 15.0)
    assert stiff[0] == pytest.approx(soft[0] / 2)          # timeconst goes as 1 / sqrt(k)
    assert tread_solref(2e5, 0.5, 15.0)[1] == 0.5
    assert tread_solref(1e12, 1.0, 1.0)[0] == pytest.approx(0.002)   # never below 2 of the 1 ms steps


def test_explicit_solref_wins(tmp_path):
    (tmp_path / "part.toml").write_text(
        '[part]\nname = "w"\nformat = 1\n[[body]]\nname = "b"\nmass = 1.0\n'
        '[[body.geom]]\ncylinder = [0.05, 0.04]\ntread = true\nsolref = [0.004, 1.0]\n'
        '[[body.geom]]\nbox = [0.1, 0.1, 0.1]\npos = [0, 0, 0.2]\ntread = true\n')
    b = assemble({"part": [{"name": "w", "path": "."}]}, tmp_path)
    assert b.tread_geoms == ["w.b.tread0", "w.b.tread1"]
    assert list(b.model.geom("w.b.tread0").solref) == [0.004, 1.0]
    box = b.model.geom("w.b.tread1")
    solver_mass = 1.0 / b.model.body_invweight0[box.bodyid[0], 0]
    assert list(box.solref) == pytest.approx(tread_solref(TREAD_STIFFNESS, 1.0, solver_mass, contacts=4))
    assert list(b.spec.geom("w.b.tread1").solref) == pytest.approx(list(box.solref))


def wheel_sag(w: World) -> dict[str, float]:
    """Each tread geom's mean penetration into the carpet over its contacts (a cylinder touches at two points)."""
    m, d = w.model, w.data
    carpet = m.geom("carpet").id
    treads = {m.geom(n).id: n for n in w.parts.tread_geoms}
    out: dict[str, list[float]] = {}
    for c in d.contact[:d.ncon]:
        other = c.geom2 if c.geom1 == carpet else c.geom1 if c.geom2 == carpet else None
        if other in treads:
            out.setdefault(treads[other], []).append(-c.dist)
    return {k: sum(v) / len(v) for k, v in out.items()}


@pytest.mark.parametrize("parts, stiffness", [
    (SWERVE, 1e5), (SWERVE, 2e5), (SWERVE, 4e5),
    ([{**SWERVE[0], "mass": 200.0}] + SWERVE[1:], 2e5),                  # a heavy robot sinks further
    ([KITBOT[0], {**KITBOT[1], "drop_center": 0.0}], 2e5),               # six wheels share it
])
def test_static_sag_matches_stiffness(parts, stiffness):
    parts = [parts[0]] + [{**p, "tread_stiffness": stiffness} for p in parts[1:]]
    w = world(parts)
    step(w, 1.5)
    sag = wheel_sag(w)
    assert sorted(sag) == sorted(w.parts.tread_geoms)                      # every wheel on the carpet
    share = robot_mass(w) / len(sag)
    assert sum(sag.values()) / len(sag) == pytest.approx(share * G / stiffness, rel=0.25)


def test_landing_does_not_bounce_like_a_ball():
    w = world(SWERVE)
    step(w, 1.0)
    rest = float(w.data.xpos[w.robot_body][2])
    w.data.qpos[w.model.jnt_qposadr[w.model.body_jntadr[w.robot_body]] + 2] += 0.15
    w.data.qvel[:] = 0.0
    mujoco.mj_forward(w.model, w.data)
    z: list[float] = []
    step(w, 1.5, z)
    first = next(i for i in range(1, len(z) - 1) if z[i] <= z[i - 1] and z[i] < z[i + 1])   # the first impact's bottom
    assert z[first] < rest
    assert max(z[first:]) - rest < 0.02
    assert z[-1] == pytest.approx(rest, abs=0.002)


@pytest.mark.parametrize("parts", [SWERVE, [KITBOT[0], {**KITBOT[1], "drop_center": 0.0}]], ids=["swerve", "kitbot"])
def test_landing_sinks_short_of_the_frame(parts):
    """A 0.15 m drop, measured from where the wheels first touch (the static sag plus the overshoot). MuJoCo's soft
    contact sinks deeper than a bare critically damped spring of the robot's mass on its treads (TREAD_IMPEDANCE
    says why): at least the spring's depth, and short of the stock chassis's bumpers, 19 mm up."""
    w = world(parts)
    step(w, 1.0)
    rest = float(w.data.xpos[w.robot_body][2])
    mass, n = robot_mass(w), len(w.parts.tread_geoms)
    w.data.qpos[w.model.jnt_qposadr[w.model.body_jntadr[w.robot_body]] + 2] += 0.15
    w.data.qvel[:] = 0.0
    mujoco.mj_forward(w.model, w.data)
    z: list[float] = []
    step(w, 1.0, z)
    omega = (n * TREAD_STIFFNESS / mass) ** 0.5
    sag, v = mass * G / (n * TREAD_STIFFNESS), (2 * G * 0.15) ** 0.5
    # x(t) = (x0 + (v + omega x0) t) exp(-omega t) about the resting height, from x0 = -sag at touch-down
    t = v / (omega * (v - omega * sag))
    spring = sag + (-sag + (v - omega * sag) * t) * math.exp(-omega * t)
    assert spring < sag + rest - min(z) < BUMPER_CLEARANCE


def test_tread_key_round_trips_and_must_collide():
    from scrimmage.parts.model import PartError, part_from_dict, part_to_dict
    d = {"part": {"name": "w", "format": 1},
         "body": [{"name": "b", "mass": 1.0, "geom": [{"cylinder": [0.05, 0.04], "tread": True}]}]}
    part = part_from_dict(d, "test", None)
    assert part.bodies[0].geoms[0].tread
    assert part_to_dict(part)["body"][0]["geom"][0]["tread"] is True
    d["body"][0]["geom"][0]["collide"] = False
    with pytest.raises(PartError, match="a tread geom must collide"):
        part_from_dict(d, "test", None)
