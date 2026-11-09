"""`launcher aim` at a goal's scoring surface: a SPEAKER-like goal entered going up under a roof gets scoring rows,
a shot through the roof isn't offered, five distances take seconds, and the hub's rows are what they were before."""

from __future__ import annotations

import dataclasses
import math
import os
import time
import tomllib

import numpy as np
import pytest
import tomlkit

from range_fixtures import SHOOTER, shooter_cfg
from scrimmage.motors import get_motor
from scrimmage.protocol import HalState
from scrimmage.world import World
from scrimmage.launcher.flight import fly_path, fly_state
from scrimmage.launcher.inputs import Air, Ball, Goal as FlightGoal
from scrimmage.testrange import aim
from scrimmage.testrange.spec import load_range

# A SPEAKER-like goal, its numbers the 2024 blue SPEAKER's (tests/data/range_2024/2024.toml) on a small field: an
# opening 1.04 m wide and 0.47 m deep, rising 15 deg toward the field (entered going up), a wall below it and a hood
# roof above it, from the opening's front lip up to the wall. The ball is the 2026 FUEL: scrimmage's [shooter] takes
# balls only, so a ball stands in for the NOTE (a NOTE shooter is task 5's).
SPEAKER_RANGE = """
[range]
name = "A SPEAKER-like goal"
ball = "2026-fuel"

[[obstacle]]
name = "wall"
pos = [-0.0500, 4.0, 0.9947]
size = [0.1, 1.0427, 1.9894]

[[obstacle]]
name = "roof"
pos = [0.2293, 4.0, 2.3148]
size = [0.5761, 1.0427, 0.03]
rpy_deg = [0.0, 37.25, 0.0]

[[goal]]
name = "speaker"
pos = [0.2293, 4.0]
heading_deg = 180.0
rim_height = 2.05
tilt_deg = 165.0
opening = { kind = "rect", width = 1.0427, height = 0.4742 }

[start.front]
pose = [3.0, 4.0, 180.0]
"""
DISTANCES = [1.25, 1.5, 1.75, 2.0, 4.0]     # m; its reach going up ends at 2.3 m (UPWARD_APEX_MARGIN), so 4 m is past it
TIME_LIMIT = 15.0                           # s for the five, the target (it was 144 s before the speed-ups)


@pytest.fixture(scope="module")
def speaker_cfg(tmp_path_factory):
    d = tmp_path_factory.mktemp("speaker")
    (d / "speaker.toml").write_text(SPEAKER_RANGE, encoding="utf-8")
    # wheels gripping harder than the fixture's (1.5, not 1.0): its 7.4 m/s top speed can't get a ball up to 2 m
    # beyond about 2.4 m away
    cfg = shooter_cfg(launcher={"hood_gap": 0.10}, range={"file": "speaker.toml", "start": "front"},
                      shooter={"wheels": {**SHOOTER["wheels"], "friction": 1.5}})
    path = d / "robot.toml"
    path.write_text(tomlkit.dumps(cfg), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def timed_table(speaker_cfg):
    """The SPEAKER table at DISTANCES with the default 2 deg hood step (26 angles), and the seconds it took, model
    loading included."""
    warnings = []
    start = time.perf_counter()
    model = aim.load_model(speaker_cfg, "speaker")
    rows = aim.aim_table(speaker_cfg, "speaker", DISTANCES, model=model, on_unreachable=warnings.append)
    return model, rows, time.perf_counter() - start, warnings


@pytest.fixture(scope="module")
def world_table(speaker_cfg):
    """The SPEAKER table at three distances to the end of its reach with a 4 deg hood step, for flying in the world."""
    model = aim.load_model(speaker_cfg, "speaker", hood_step_deg=4.0)
    return aim.aim_table(speaker_cfg, "speaker", [1.5, 2.0, 2.3], model=model, hood_step_deg=4.0)


def _fly_in_world(cfg_path, distance: float, rps: float, hood: float) -> dict:
    """The robot `distance` back from the goal on its approach line, facing it, with one ball in its hopper; robot code
    that holds the hood angle and both wheels at `rps` (kV feedforward plus 0.5 V per rps, trimmed onto the speed
    before the feed), then runs the hopper floor and the feeder: the ball's log once it has scored, or after 2.5 s of feeding."""
    cfg = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    cfg["range"] = {**{k: v for k, v in cfg["range"].items() if k != "start"}, "preload": 1}
    cfg.setdefault("robot", {})["start_pose"] = [0.2293 + distance, 4.0, 180.0]
    w = World(cfg, cfg_path.parent)
    rt, m, d = w.test_range, w.model, w.data
    joint = m.joint("launcher.hood")
    qadr = m.jnt_qposadr[joint.id]
    kv = 2.0 * math.pi / get_motor("KrakenX60", 1).Kv
    low = rt.shooter.config.hood_range[0]           # the hood joint's 0 (hood_gap moves it off 20 deg)
    trim = 0.0
    for k in range(5000):
        feeding = k >= 2500
        error = rps - rt.shooter.rps()
        if not feeding:
            trim += 0.5 * error * 0.001
        pwm = {"2": (kv * rps + 0.5 * error + trim) / w.vbatt}
        herr = math.radians(hood - low) - d.qpos[qadr]
        pwm["3"] = 4.0 * herr                       # the hood's PWM sets its speed, so P alone holds it
        pwm["5"] = pwm["6"] = 1.0 if feeding else 0.0
        hal = HalState()
        for port, value in pwm.items():
            hal.devices[("PWM", port)] = {"<init": True, "<speed": float(np.clip(value, -1.0, 1.0))}
        w.step(hal)
        if rt.counts()["goals"].get("speaker"):
            break
    return rt.ball_log()[0]


def _through(model, path):
    """The first sample index k where the path's center goes through the surface's plane from its normal's side,
    and the crossing point."""
    s = model.goal.scoring_surface()
    side = (path[:, 1:4] - s.center) @ s.normal
    k = int(np.flatnonzero((side[:-1] > 0.0) & (side[1:] <= 0.0))[0])
    f = side[k] / (side[k] - side[k + 1])
    return k, path[k, 1:4] + f * (path[k + 1, 1:4] - path[k, 1:4])


def _touches(centers, radius, obstacle) -> bool:
    """Does a ball of `radius` at any of `centers` overlap the obstacle's box?"""
    local = (centers - np.array(obstacle.pos)) @ obstacle.rotation()
    gap = np.maximum(np.abs(local) - np.array(obstacle.size) / 2.0, 0.0)
    return bool((np.linalg.norm(gap, axis=1) <= radius).any())


def test_speaker_rows_go_up_through_the_opening_untouched(speaker_cfg, timed_table):
    model, rows, _, warnings = timed_table
    s = model.goal.scoring_surface()
    radius = model.setup.ball.diameter / 2.0
    obstacles = load_range("speaker.toml", speaker_cfg.parent).obstacles
    scoring = [r for r in rows if r.status == "ok"]
    assert len(scoring) >= 3, aim.to_csv(rows)
    assert rows[-1].status == aim.UNREACHABLE and "4 m" in warnings[0]
    for row in scoring:
        path = aim.surface_path(model, row)
        k, at = _through(model, path)
        assert s.crossed(path[k, 1:4], path[k + 1, 1:4])[0]           # through the opening, from the field side
        # going up through it: against its normal, which points down (and 15 deg out to the field). The flattest rows
        # peak at the opening, so some are just past their apex, still moving up through the tilted opening.
        assert s.normal[2] < 0.0 and (path[k + 1, 1:4] - path[k, 1:4]) @ s.normal < 0.0
        q = (at - s.center) @ s.axes.T                                # in the opening: a radius inside its edges
        assert abs(q[0]) <= 0.4742 / 2 - radius and abs(q[1]) <= 1.0427 / 2 - radius
        assert np.linalg.norm(at - s.center - q[1] * s.axes[1]) < aim.HIT_TOLERANCE   # at the center (lane aside)
        assert not any(_touches(path[:k + 1, 1:4], radius, o) for o in obstacles), row
        assert row.flight_s == pytest.approx(path[k, 0], abs=2e-3)
        assert row.apex == pytest.approx(path[:, 3].max())
        # on the approach line: the robot center `distance` back from the goal along the normal's level part (+x),
        # facing it, so the launcher frame's origin (below the shaft) is `forward` nearer the goal than the center
        origin = aim._SurfaceAim(model).on_field(np.zeros((1, 4)), row.distance)[0]
        assert origin == pytest.approx([0.2293 + row.distance - model.forward, 4.0, 0.0])
    # the first row is the lowest apex that scores
    first = aim._SurfaceAim(model).solutions(scoring[0].distance, (10.0, 120.0))
    assert scoring[0] == min(first, key=lambda r: (r.apex, r.wheel_rps))


class _NoMargin(aim._SurfaceAim):
    """The aim without UPWARD_APEX_MARGIN, which on its own rules out the shots that come down over the roof."""

    def apex_margin(self) -> float:
        return 0.0


def test_a_shot_through_the_roof_is_not_offered(speaker_cfg):
    """Without the range's boxes, some shots come down into the opening just past their apex over the roof's front
    lip; with them, those aren't offered (and the table's row isn't one). The apex margin is left out here, so the
    boxes alone must turn them away."""
    model = aim.load_model(speaker_cfg, "speaker")
    radius = model.setup.ball.diameter / 2.0
    roof = next(o for o in load_range("speaker.toml", speaker_cfg.parent).obstacles if o.name == "roof")
    free = _NoMargin(dataclasses.replace(model, boxes=()))
    real = _NoMargin(model)
    through_roof = 0
    for distance in (2.5, 3.0):
        offered = real.solutions(distance, (10.0, 120.0))
        assert offered
        for row in free.solutions(distance, (10.0, 120.0)):
            path = aim.surface_path(model, row)
            k, _ = _through(model, path)
            if _touches(path[:k + 1, 1:4], radius, roof):
                through_roof += 1
                assert not any(r.hood_deg == row.hood_deg and r.wheel_rps == pytest.approx(row.wheel_rps, abs=1e-6)
                               for r in offered), row
    assert through_roof >= 2


@pytest.mark.skipif(bool(os.environ.get("CI")), reason="timing is only meaningful on a quiet machine")
def test_five_speaker_distances_within_15_seconds(timed_table):
    _, rows, seconds, _ = timed_table
    print(f"\n{len(rows)} SPEAKER distances, 26 hood angles: {seconds:.1f} s")
    assert len(rows) == 5
    assert seconds < TIME_LIMIT


def test_fly_path_is_fly_state_flight():
    """fly_path flies fly_state's ball: the same rim-height crossing and floor, to rounding."""
    ball = Ball(diameter=0.15, mass=0.215, stiffness_n=60.0, restitution=0.6)
    air = Air(density=1.2, drag_coefficient=0.47, lift_slope=1.0, lift_max=0.3)
    goal = FlightGoal(distance=3.0, height=1.83, diameter=1.06)
    pos, vel, axis = np.array([0.0, 0.05, 0.5]), np.array([5.0, 0.3, 6.0]), np.array([0.05, -1.0, 0.0])
    axis /= np.linalg.norm(axis)
    landing = fly_state(pos, vel, axis, 200.0, ball, air, goal)
    p = fly_path(pos, vel, axis, 200.0, ball, air)
    z = p[:, 3]
    k = int(np.flatnonzero((z[:-1] > goal.height) & (z[1:] <= goal.height))[0])
    f = (z[k] - goal.height) / (z[k] - z[k + 1])
    assert p[k, 1] + f * (p[k + 1, 1] - p[k, 1]) == pytest.approx(landing.at_goal[0], abs=1e-9)
    assert p[k, 2] + f * (p[k + 1, 2] - p[k, 2]) == pytest.approx(landing.at_goal[1], abs=1e-9)
    assert p[k, 0] + f * (p[k + 1, 0] - p[k, 0]) == pytest.approx(landing.goal_time, abs=1e-9)
    assert z.max() == pytest.approx(landing.apex, abs=1e-9)
    assert p[-1, 3] <= ball.diameter / 2 < p[-2, 3]


# Recorded at 09e745b (task 3's head, aim.py as on main), before the surface aim: the hub's rows, bit for bit.
HUB_ROWS = [
    ("0x1.8000000000000p+0", "0x1.5b4ae00000000p+5", "0x1.1800000000000p+6", "0x1.b99e6d786166fp-1",
     "0x1.19a83dec965d6p+1"),
    ("0x1.4000000000000p+1", "0x1.7641600000000p+5", "0x1.e000000000000p+5", "0x1.ba40924f18c2fp-1",
     "0x1.19da59031c552p+1"),
    ("0x1.8000000000000p+1", "0x1.89dc200000000p+5", "0x1.9000000000000p+5", "0x1.7ee5f1ce4ec60p-1",
     "0x1.019afcc82c997p+1"),
]


def test_hub_rows_are_unchanged(tmp_path):
    path = tmp_path / "robot.toml"
    path.write_text(tomlkit.dumps(shooter_cfg(launcher={"hood_gap": 0.10})), encoding="utf-8")
    warnings = []
    rows = aim.aim_table(path, "hub_blue", [1.5, 2.5, 3.0, 4.0], hood_step_deg=10.0, on_unreachable=warnings.append)
    assert [tuple(float(getattr(r, f)).hex() for f in aim.ROW_FIELDS) for r in rows[:3]] == HUB_ROWS
    assert rows[3].status == aim.UNREACHABLE and all(math.isnan(getattr(rows[3], f)) for f in aim.ROW_FIELDS[1:])
    assert "rim height" in warnings[0]
    java = aim.to_java(rows, "A", "cmd")
    assert "// angle whose ball crosses the goal's rim height descending at its center, the time from exit to that" \
        in java.splitlines()


def test_the_ball_rows_score_in_the_world_with_the_wheels_2_percent_off(speaker_cfg, world_table):
    """Each ball row flown in the world, the robot at the row's distance, the hood at its angle and the wheels at its
    speed and 2% either side: every shot scores in the SPEAKER-like goal (the ball counterpart of the NOTE re-fly in
    tests/test_range_2024.py). Without UPWARD_APEX_MARGIN the rows peaked at the opening's center and crossed flat
    there, a centimeter under its field-side edge; the world's ball flew 1.5 to 2.5 cm higher and 6 of the 12 shots
    caught that edge (rim_1), all of them at the row's speed or 2% above it."""
    missed = []
    ok = [r for r in world_table if r.status == "ok"]
    assert len(ok) >= 2
    for row in ok:
        for f in (0.98, 1.0, 1.02):
            log = _fly_in_world(speaker_cfg, row.distance, row.wheel_rps * f, row.hood_deg)
            if log["goal"] != "speaker":
                missed.append((row.distance, row.hood_deg, round(row.wheel_rps, 2), f, log["goal"], log["first_hit"]))
    assert not missed, missed
