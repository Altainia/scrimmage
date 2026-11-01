"""Robot terrain signals: ground gap, wheels down, air time, tilt, pitch, roll, z, stranded and tipped, read by the
TerrainMonitor at physics rate and peak-held between scenario ticks."""

from __future__ import annotations

import math
import tomllib
from pathlib import Path
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

from range_fixtures import range_cfg, spares_world
from scrimmage.bindings import pigeon_attitude
from scrimmage.checks import ScenarioError, SignalCheck, resolve_signal, tick_checks
from scrimmage.protocol import HalState
from scrimmage.terrain.monitor import STRANDED_S, TIP_DEG
from scrimmage.testrange.build import BALL_BITS
from scrimmage.world import World

ROOT = Path(__file__).resolve().parents[1]
TICK = 0.005                # s: a lockstep step, as configs/fastmode.toml
SWERVE = [{"name": "drive", "stock": "chassis"}] + [
    {"name": c, "stock": "swerve/MK4i", "attach": f"drive.{m}",
     "ids": {"drive": f"PWM:{i}", "steer": f"PWM:{i + 4}"}}
    for i, (c, m) in enumerate((("fl", "front_left"), ("fr", "front_right"), ("bl", "back_left"),
                                ("br", "back_right")))]
PLAIN = {"field": {"obstacle": False}, "robot": {"start_pose": [2.0, 4.0, 0.0]}}


def swerve_world() -> World:
    return World({"part": SWERVE, **PLAIN}, None)


def hal_for(**pwm) -> HalState:
    hal = HalState()
    for port, speed in pwm.items():
        hal.devices[("PWM", port.removeprefix("p"))] = {"<init": True, "<speed": speed}
    return hal


def run(w: World, seconds: float, hal: HalState | None = None) -> list[dict]:
    """Step `w` in TICK ticks; each tick's sample, consumed as the scenario runner's tick does."""
    hal = hal or HalState()
    per_tick = round(TICK / w.model.opt.timestep)
    out = []
    for _ in range(round(seconds / TICK)):
        for _ in range(per_tick):
            w.step(hal)
        out.append(w.terrain.sample())
        w.terrain.consume()
    return out


def lift(w: World, dz: float, quat=None) -> None:
    """Move the robot up by dz (and turn it to `quat`), at rest."""
    q = w.free_qadr
    w.data.qpos[q + 2] += dz
    if quat is not None:
        w.data.qpos[q + 3:q + 7] = quat
    w.data.qvel[w.free_dadr:w.free_dadr + 6] = 0
    mujoco.mj_forward(w.model, w.data)


def test_flat_carpet_reads_zero_gap_four_wheels_level():
    w = swerve_world()
    assert w.terrain is None
    mon = w.terrain_monitor()
    assert w.terrain is mon and w.terrain_monitor() is mon
    s = run(w, 0.5)[-1]
    assert s["robot.ground_gap"] == 0.0
    assert s["robot.wheels_down"] == 4
    assert s["robot.air_time"] == 0.0
    assert s["robot.tilt"] < 0.5 and abs(s["robot.pitch"]) < 0.5 and abs(s["robot.roll"]) < 0.5
    assert s["robot.tipped"] == 0 and s["robot.stranded"] == 0
    assert mon.first_tipped is None and mon.first_stranded is None


def test_dropped_robot_reports_gap_and_air_time():
    w = swerve_world()
    w.terrain_monitor()
    run(w, 0.3)
    z0 = w.data.xpos[w.robot_body][2]
    lift(w, 0.10)
    samples = run(w, 0.5)
    assert max(s["robot.ground_gap"] for s in samples) == pytest.approx(0.10, abs=0.01)
    fall = math.sqrt(2 * 0.10 / 9.81)          # 0.14 s
    assert max(s["robot.air_time"] for s in samples) == pytest.approx(fall, abs=0.02)
    assert max(s["robot.z"] for s in samples) == pytest.approx(z0 + 0.10, abs=0.005)
    assert samples[-1]["robot.ground_gap"] == 0.0 and samples[-1]["robot.air_time"] == 0.0
    assert min(s["robot.wheels_down"] for s in samples) == 0 and samples[-1]["robot.wheels_down"] == 4
    assert not any(s["robot.stranded"] for s in samples)     # in the air, not resting on anything


def test_robot_on_balls_is_stranded_and_gap_is_height_above_carpet():
    w = spares_world(range_cfg())
    mon = w.terrain_monitor()
    x, y, _ = w.robot_pose()
    r = w.parts.range.ball.diameter / 2
    balls = []
    for dx in (-0.15, 0.15):                   # under the frame, inside the wheels (at +-0.26, +-0.255)
        for dy in (-0.12, 0.12):
            i = len(balls)
            w.test_range.balls.place(i, np.array([x + dx, y + dy, r]), np.zeros(3), np.zeros(3), BALL_BITS)
            balls.append(f"ball.{i}")
    lift(w, 2 * r - 0.08 + 0.005)             # the frame's underside (0.08 m up) just above the balls
    samples = run(w, 1.0)
    end = samples[-1]
    assert end["robot.wheels_down"] < 2
    assert end["robot.ground_gap"] > 0.03
    assert end["robot.air_time"] == 0.0        # resting on the balls, not in the air
    assert end["robot.stranded"] == 1
    t, on = mon.first_stranded
    assert on in balls
    assert STRANDED_S < t < STRANDED_S + 0.3   # it bounces on the balls a little: those steps don't count
    assert not samples[int(0.4 / TICK)]["robot.stranded"]


def test_upside_down_robot_tilt_and_tipped_latches():
    w = swerve_world()
    mon = w.terrain_monitor()
    a = math.radians(120.0) / 2
    lift(w, 0.5, [math.cos(a), math.sin(a), 0.0, 0.0])
    s = mon.sample()
    assert s["robot.tilt"] == pytest.approx(120.0, abs=0.1)
    assert s["robot.roll"] == pytest.approx(120.0, abs=0.1)         # left side up
    assert s["robot.tipped"] == 0                                   # no physics step yet
    samples = run(w, 1.0)
    assert samples[0]["robot.tipped"] == 1
    t, tilt, (x, y, heading) = mon.first_tipped
    assert tilt > TIP_DEG and t == pytest.approx(w.model.opt.timestep)
    assert (x, y) == pytest.approx((2.0, 4.0), abs=0.05)
    assert all(s["robot.tipped"] == 1 for s in samples)                # latched, whatever it does next
    w.reset_robot()                             # the robot back at its start clears it, as reset does
    assert mon.first_tipped is None
    assert all(s["robot.tipped"] == 0 for s in run(w, 0.1))
    lift(w, 0.5, [math.cos(a), math.sin(a), 0.0, 0.0])
    assert run(w, 0.1)[-1]["robot.tipped"] == 1
    w.reset()
    assert mon.first_tipped is None
    assert run(w, 0.1)[-1]["robot.tipped"] == 0


def test_pitch_and_roll_match_the_pigeon():
    """robot.pitch and robot.roll are the Pigeon 2's (bindings.pigeon_attitude): pitch nose down positive."""
    w = swerve_world()
    mon = w.terrain_monitor()
    a = math.radians(10.0) / 2                 # a right-hand turn about y of +10 deg puts the nose (+x) down
    lift(w, 0.3, [math.cos(a), 0.0, math.sin(a), 0.0])
    s = mon.sample()
    _, pitch, roll, _ = pigeon_attitude(w.data.xquat[w.robot_body], np.zeros(3))
    assert s["robot.pitch"] == pitch and s["robot.roll"] == roll
    assert s["robot.pitch"] == pytest.approx(10.0, abs=0.01)
    assert s["robot.tilt"] == pytest.approx(10.0, abs=0.01)


def test_peak_hold_sees_a_hop_shorter_than_a_tick():
    w = swerve_world()
    w.terrain_monitor()
    run(w, 0.3)
    checks = [SignalCheck("always", {"signal": "robot.ground_gap", "max": 0.01}, w, "check 1"),
              SignalCheck("always", {"signal": "robot.ground_gap", "max": 0.02}, w, "check 2")]
    session = SimpleNamespace(world=w, t=w.time)
    for c in checks:
        c.begin(session)
    hal = HalState()
    q, v = w.data.qpos.copy(), w.data.qvel.copy()
    for k in range(5):                          # one 5 ms tick: 3 ms lifted 0.03 m, then back down
        if k < 3:
            w.data.qpos[:], w.data.qvel[:] = q, v
            w.data.qpos[w.free_qadr + 2] += 0.03
        elif k == 3:
            w.data.qpos[:], w.data.qvel[:] = q, v
        w.step(hal)
    assert w.terrain.sample()["robot.ground_gap"] == pytest.approx(0.03, abs=0.005)
    tick_checks(checks, session, w.time)
    results = [c.end(session) for c in checks]
    assert [r.passed for r in results] == [False, False]                  # both saw it: one hold per tick
    assert all(r.measured == pytest.approx(0.03, abs=0.005) for r in results)
    assert w.terrain.sample()["robot.ground_gap"] == 0.0                  # consumed: back on the ground


def test_swerve_spinning_in_place_keeps_four_wheels_down():
    """A scrubbing turn (modules straight, the sides driven opposite ways) loses wheel contact on most physics steps;
    the wheels are still down, so wheels_down holds 4 and the robot is never stranded."""
    w = swerve_world()
    w.terrain_monitor()
    samples = run(w, 2.0, hal_for(p0=-0.6, p2=-0.6, p1=0.6, p3=0.6))
    assert abs(w.robot_pose()[2]) > 0.5                                   # it turned
    assert all(s["robot.wheels_down"] == 4 for s in samples)
    assert all(s["robot.ground_gap"] == 0.0 for s in samples)
    assert not any(s["robot.stranded"] for s in samples)


def test_rejected_wheel_signal_leaves_no_monitor():
    w = World({"model": {"mjcf": "models/kitbot.xml"}}, ROOT / "tests")
    with pytest.raises(ScenarioError, match="no tread wheels"):
        resolve_signal("robot.wheels_down", w)
    assert w.terrain is None


def test_mjcf_robot_without_tread_errors_for_wheel_signals_only():
    w = World({"model": {"mjcf": "models/kitbot.xml"}}, ROOT / "tests")
    for name in ("robot.ground_gap", "robot.wheels_down", "robot.stranded"):
        with pytest.raises(ScenarioError, match="no tread wheels"):
            resolve_signal(name, w)
    session = SimpleNamespace(world=w)
    for name in ("robot.tilt", "robot.pitch", "robot.roll", "robot.z", "robot.tipped", "robot.air_time"):
        sig = resolve_signal(name, w)
        hal = HalState()
        for _ in range(200):
            w.step(hal)
        assert isinstance(sig.read(session), (int, float))
    assert resolve_signal("robot.tilt", w).read(session) < 1.0
    assert resolve_signal("robot.air_time", w).read(session) == 0.0
    w.terrain.consume()
    assert w.terrain.sample()["robot.wheels_down"] is None


def test_platform_angle_signal():
    w = World(range_cfg(range={"file": "2023-charge-station", "start": "approach"}), None)
    assert w.terrain is not None                # a range with elements has a monitor from the start
    a, _ = w.parts.layout.platforms["charge_station"]
    w.data.qpos[a] = math.radians(12.0)
    mujoco.mj_forward(w.model, w.data)
    sig = resolve_signal("element.charge_station.angle", w)
    assert sig.read(SimpleNamespace(world=w)) == pytest.approx(12.0)
    assert w.terrain.platform_angle("charge_station") == pytest.approx(12.0)
    with pytest.raises(ScenarioError, match="charge_station"):
        resolve_signal("element.nope.angle", w)
    with pytest.raises(ScenarioError, match="angle"):
        resolve_signal("element.charge_station.tilt", w)
    bump = World(range_cfg(range={"file": "2026-bump", "start": "approach"}), None)
    with pytest.raises(ScenarioError, match="platform"):
        resolve_signal("element.bump.angle", bump)


def test_unknown_robot_signal_lists_them():
    w = swerve_world()
    with pytest.raises(ScenarioError, match="robot.ground_gap"):
        resolve_signal("robot.height", w)
    assert w.terrain is None


def test_no_monitor_without_elements_or_signals():
    path = ROOT / "configs" / "fastmode.toml"
    w = World(tomllib.loads(path.read_text()), path.parent)
    hal = HalState()
    for _ in range(100):
        w.step(hal)
    assert w.terrain is None
    w.reset()
    w.reset_robot()
    assert w.terrain is None


def test_monitor_is_deterministic():
    def once():
        w = swerve_world()
        w.terrain_monitor()
        lift(w, 0.05)
        return run(w, 0.3, hal_for(p0=0.5, p1=0.5, p2=0.5, p3=0.5))
    assert once() == once()


def test_picker_lists_the_terrain_signals():
    from scrimmage.assembler.signals import signals
    mjcf = signals(World({"model": {"mjcf": "models/kitbot.xml"}}, ROOT / "tests"))["fixed"]
    assert "robot.tilt" in mjcf and "robot.air_time" in mjcf and "robot.ground_gap" not in mjcf
    assert "robot.ground_gap" in signals(swerve_world())["fixed"]
    station = signals(World(range_cfg(range={"file": "2023-charge-station", "start": "approach"}), None))
    assert station["range"][-1] == "element.charge_station.angle"
