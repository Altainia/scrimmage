"""The disc shooter in the world: the stock `disc_shooter` part, `[shooter] kind = "disc"`, the hopper and stage
feeding the planar disc launcher (scrimmage/disc/launcher.py) on the robot's battery, and the launched disc's
pose, velocity and spin in the world."""

from __future__ import annotations

import copy
import dataclasses
import math
from pathlib import Path

import mujoco
import numpy as np
import pytest

from range_fixtures import KITBOT, range_cfg, shooter_cfg
from scrimmage.disc import run as disc_run
from scrimmage.disc.flight import matrix_from_quat
from scrimmage.disc.inputs import Mount, load_setup
from scrimmage.disc.launcher import DiscLauncher, ExitedDisc
from scrimmage.launcher.inputs import Robot
from scrimmage.parts.model import PartError
from scrimmage.parts.stock import build_stock
from scrimmage.protocol import HalState
from scrimmage.testrange.balls import BallState
from scrimmage.testrange.launch import exit_state_disc
from scrimmage.world import World

# The disc shooter on the kitbot's top mount, its one wheel group on PWM 2 (the part's defaults: the 2013 linear
# layout of examples/disc/linear.toml, four 4 in. rollers on the right, a fixed 25 deg tilt). A loader roller (PWM 5)
# carries the discs from the hopper into the channel.
DISC_PART = {"name": "shooter", "stock": "disc_shooter", "attach": "drive.top", "pos": [-0.25, 0.0, 0.25],
             "ids": {"shooter0": "PWM:2"}}
LOADER = {"name": "loader", "stock": "roller", "attach": "drive.top", "pos": [-0.35, 0.0, 0.2], "gearing": 10.0,
          "ids": {"spin": "PWM:5"}}
DISC_SHOOTER = {"kind": "disc", "part": "shooter", "wheels": {"preset": "urethane-60a-4in"},
                "layout": {"fence": "wedgetop"}, "stage": [{"name": "loader", "part": "loader", "path_length": 0.30}]}


def disc_cfg(part: dict | None = None, shooter: dict | None = None, **overrides) -> dict:
    """The kitbot with DISC_PART (its keys updated by `part`) and LOADER on the 2013 range at auto_line_left, four
    discs preloaded; [shooter] DISC_SHOOTER updated by `shooter`."""
    parts = copy.deepcopy(KITBOT) + [{**copy.deepcopy(DISC_PART), **(part or {})}, copy.deepcopy(LOADER)]
    sec = {**copy.deepcopy(DISC_SHOOTER), **copy.deepcopy(shooter or {})}
    rng = {"file": "2013", "start": "auto_line_left", "preload": 4, **overrides.pop("range", {})}
    return range_cfg(part=parts, range=rng, shooter={k: v for k, v in sec.items() if v is not None}, **overrides)


def pwm_hal(**pwm) -> HalState:
    hal = HalState()
    for port, speed in pwm.items():
        hal.devices[("PWM", port.removeprefix("p"))] = {"<init": True, "<speed": speed}
    return hal


def run(w, seconds, hal, each=None):
    for _ in range(int(round(seconds / w.model.opt.timestep))):
        w.step(hal)
        if each is not None:
            each(w)


# ------------------------------------------------------------------------------------------------ the stock part

def test_disc_shooter_part_builds():
    part = build_stock("disc_shooter", {}, "t")
    assert set(part.slot_kinds()) == {"shooter0"}
    assert part.joint_names() == ["spin0"]
    assert all(not g.collide for b in part.bodies for g in b.geoms)
    assert [m.name for m in part.mounts] == ["base"]
    spin = next(b for b in part.bodies if b.joint is not None and b.joint.name == "spin0")
    assert spin.parent == "channel"
    np.testing.assert_allclose(np.abs(spin.joint.axis), [0.0, 0.0, 1.0])    # normal to the channel plane

    art = build_stock("disc_shooter", {"tilt": "articulated", "tilt_range": [10.0, 40.0]}, "t")
    assert set(art.slot_kinds()) == {"shooter0", "tilt", "tilt_encoder"}
    assert set(art.joint_names()) == {"spin0", "tilt"}
    tilt = next(b.joint for b in art.bodies if b.joint is not None and b.joint.name == "tilt")
    assert tilt.range == pytest.approx((0.0, 30.0))

    per_wheel = build_stock("disc_shooter", {"groups": "per_wheel"}, "t")
    assert set(per_wheel.slot_kinds()) == {"shooter0", "shooter1", "shooter2", "shooter3"}
    pinch = build_stock("disc_shooter", {"layout": "pinch", "wheel_count": 1, "wheel_start": 0.2,
                                         "groups": "per_side"}, "t")
    assert set(pinch.joint_names()) == {"spin0", "spin1"}
    arc = build_stock("disc_shooter", {"layout": "arc", "wheel_count": 1, "wheel_start": 0.3,
                                       "wheel_diameter": 0.2032, "arc_deg": 120.0}, "t")
    assert arc.joint_names() == ["spin0"]
    with pytest.raises(PartError, match="an arc has one wheel"):
        build_stock("disc_shooter", {"layout": "arc"}, "t")


def test_disc_shooter_in_the_world_tilts_its_channel():
    """The channel body is tilted up by tilt_angle; the spin joints' axes are normal to it."""
    w = World(disc_cfg(part={"tilt_angle": 30.0}), None)
    m, d = w.model, w.data
    mujoco.mj_forward(m, d)
    rot = d.xmat[m.body("shooter.channel").id].reshape(3, 3)
    robot = d.xmat[w.robot_body].reshape(3, 3)
    local = robot.T @ rot                     # the channel in the robot's frame
    np.testing.assert_allclose(local[:, 0], [math.cos(math.radians(30)), 0.0, math.sin(math.radians(30))], atol=1e-9)
    axis = d.xaxis[m.joint("shooter.spin0").id]
    assert abs(axis @ rot[:, 2]) == pytest.approx(1.0, abs=1e-9)


# ------------------------------------------------------------------------------------------------ firing

def test_preloaded_discs_fire_in_world_and_fly():
    """Four preloaded discs: the wheels spin up on PWM 2, then the loader feeds them one at a time; all four leave
    the channel and fly (each climbs well above its exit point)."""
    w = World(disc_cfg(), None)
    rt = w.test_range
    balls = rt.balls
    assert rt.ball_path.held() == 4 and rt.counts()["ledger"]["robot"] == 4
    run(w, 1.0, pwm_hal(p2=1.0))
    assert rt.shooter.rps() > 50.0
    apex: dict[int, float] = {}

    def track(w):
        for i in np.flatnonzero(balls.field):
            apex[int(i)] = max(apex.get(int(i), -1.0), float(balls._qpos[i, 2]))
    run(w, 3.0, pwm_hal(p2=1.0, p5=1.0), track)
    assert balls.fired == 4
    assert rt.ball_path.held() == 0
    fired = [log for log in balls.log if not math.isnan(log.fired_t)]
    assert len(fired) == 4
    for log in fired:
        assert log.launcher_status == "launched"
        assert log.exit_speed > 8.0 and abs(log.spin_rpm) > 300.0
        assert log.hood_deg == pytest.approx(25.0)      # the tilt
        assert apex[log.id] > 1.5                       # it flew: up from a ~0.6 m exit
        assert balls.state[log.id] != BallState.STUCK
    times = sorted(log.fired_t for log in fired)
    assert all(b - a > 0.05 for a, b in zip(times, times[1:]))      # one at a time


def _first_launch(w, hal):
    """Step until the first disc is launched; (ball index, the ExitedDisc, the channel's pose and the robot's
    motion at the step's start, the launcher time at the step's start)."""
    rt = w.test_range
    seen = {}
    orig = rt._launch

    def spy(i, shot, t0, motion):
        seen.update(i=i, shot=shot, t0=t0, motion=motion)
        orig(i, shot, t0, motion)
    rt._launch = spy
    for _ in range(4000):
        w.step(hal)
        if seen:
            return seen
    raise AssertionError("no disc launched")


def test_launched_disc_pose_velocity_and_spin():
    """The disc leaves at the channel's exit with its axis normal to the channel plane, moving along the channel at
    the exit speed (the robot is still), spinning about its axis with the launcher's sign."""
    w = World(disc_cfg(), None)
    run(w, 1.0, pwm_hal(p2=1.0))
    seen = _first_launch(w, pwm_hal(p2=1.0, p5=1.0))
    i, shot = seen["i"], seen["shot"]
    rot = seen["motion"]["base_mat"]
    balls = w.test_range.balls
    # after its first world step: the orientation has turned only about the disc's axis
    q = balls._qpos[i, 3:7]
    axis = matrix_from_quat(q)[:, 2]
    assert axis @ rot[:, 2] == pytest.approx(1.0, abs=1e-3)
    vel = balls._qvel[i, :3]
    yaw = math.radians(shot.yaw_deg)
    along = math.cos(yaw) * rot[:, 0] + math.sin(yaw) * rot[:, 1]
    # one step of gravity and air: within 2 cm/s of the exit velocity
    np.testing.assert_allclose(vel, shot.speed * along, atol=0.02)
    omega_world = matrix_from_quat(q) @ balls._qvel[i, 3:]
    assert omega_world @ rot[:, 2] == pytest.approx(shot.spin, rel=0.01)
    assert shot.spin > 0.0          # a right-hand wheel spins the disc counter-clockwise from above


def test_in_world_shot_matches_standalone_exit():
    """The same voltage on the wheels from rest, the disc fed at the same time and entry speed: the world's exit speed
    and spin are the standalone DiscLauncher's within 2 %."""
    w = World(disc_cfg(), None)
    rt = w.test_range
    launcher = rt.shooter.launcher
    feeds = []
    insert = launcher.insert

    def spy(t, entry_speed=None):
        feeds.append((t, entry_speed))
        return insert(t, entry_speed)
    launcher.insert = spy
    run(w, 1.0, pwm_hal(p2=1.0))
    seen = _first_launch(w, pwm_hal(p2=1.0, p5=1.0))
    shot = seen["shot"]
    (t_in, entry), = feeds[:1]
    assert shot.t_enter == pytest.approx(t_in)

    alone = DiscLauncher(rt.shooter.config.setup, external_bus=True)
    for shaft in alone.shafts:
        shaft.set_bus_voltage(12.0)
    dt = w.model.opt.timestep
    while alone.time < t_in - 1e-9:
        alone.step(dt, [12.0])
    assert alone.time == pytest.approx(t_in, abs=1e-9)
    alone.insert(alone.time, entry)
    out: list[ExitedDisc] = []
    while not out:
        out = alone.step(dt, [12.0])
    ref = out[0]
    assert ref.status == "launched"
    assert shot.speed == pytest.approx(ref.speed, rel=0.02)
    assert shot.spin == pytest.approx(ref.spin, rel=0.02)
    log = rt.balls.log[seen["i"]]
    assert log.exit_speed == pytest.approx(shot.speed)


def test_exit_state_disc_adds_the_robot_motion_and_matches_the_standalone_frame():
    """exit_state_disc in a channel frame tilted like the standalone's (run.exit_to_flight_state) gives the same
    velocity and orientation; the robot's velocity and its turn about the channel origin add to it; the exit point is
    (exit_x, exit_y, 0) in the channel."""
    setup, _, _ = load_setup(Path(__file__).resolve().parent.parent / "examples/disc/linear.toml")
    setup = dataclasses.replace(setup, mount=Mount(height=0.8, tilt_deg=20.0), robot=Robot())
    nan = math.nan
    shot = ExitedDisc(0, "launched", 0.0, 0.01, 12.0, 90.0, 0.4, (0.64, 0.003), None, nan, ())
    ref = disc_run.exit_to_flight_state(shot, setup)
    t = math.radians(20.0)
    mat = np.array([[math.cos(t), 0.0, -math.sin(t)], [0.0, 1.0, 0.0], [math.sin(t), 0.0, math.cos(t)]])
    origin = np.array([1.0, 2.0, 0.8])
    pos, vel, quat, ang_vel = exit_state_disc(shot, origin, mat, np.zeros(6), shot.t_exit)
    np.testing.assert_allclose(vel, ref.vel, atol=1e-12)
    np.testing.assert_allclose(quat, ref.quat, atol=1e-12)
    np.testing.assert_allclose(ang_vel, matrix_from_quat(ref.quat) @ ref.omega_body, atol=1e-12)
    np.testing.assert_allclose(pos, origin + mat @ np.array([0.64, 0.003, 0.0]), atol=1e-12)
    # the robot moving at 2 m/s along y and turning at 1 rad/s about z
    body_vel = np.array([0.0, 0.0, 1.0, 0.0, 2.0, 0.0])
    pos2, vel2, _, ang_vel2 = exit_state_disc(shot, origin, mat, body_vel, shot.t_exit - 0.001)
    lever = mat @ np.array([0.64, 0.003, 0.0])
    np.testing.assert_allclose(vel2, ref.vel + [0.0, 2.0, 0.0] + np.cross([0.0, 0.0, 1.0], lever), atol=1e-12)
    np.testing.assert_allclose(ang_vel2, ang_vel + [0.0, 0.0, 1.0], atol=1e-12)
    np.testing.assert_allclose(pos2, origin + lever - 0.001 * vel2, atol=1e-12)


# ------------------------------------------------------------------------------------------------ config

def test_ball_and_disc_mismatch_is_a_clear_error():
    # a ball launcher on the disc range
    with pytest.raises(ValueError, match=r"kind = \"ball\".*'FRC 2013 ULTIMATE ASCENT'.*discs.*'2013-frisbee'"):
        World(shooter_cfg(range={"file": "2013", "start": None}), None)
    # a disc shooter on a ball range
    with pytest.raises(ValueError, match=r"kind = \"disc\".*balls.*'2026-fuel'"):
        World(disc_cfg(range={"file": "2026", "start": None, "preload": 0}), None)
    # a disc shooter in the wrong part
    cfg = disc_cfg()
    cfg["part"].append({"name": "launcher", "stock": "launcher", "attach": "drive.top", "pos": [0.2, 0.0, 0.3]})
    cfg["shooter"]["part"] = "launcher"
    with pytest.raises(ValueError, match="'launcher' is a stock launcher part.*stock disc_shooter"):
        World(cfg, None)
    with pytest.raises(ValueError, match="kind must be"):
        World(disc_cfg(shooter={"kind": "frisbee"}, range={"preload": 0}), None)


@pytest.mark.parametrize("shooter, match", [
    ({"layout": {"fence": "wedgetop", "channel_length": 0.6}}, r"\[shooter.layout\] channel_length comes from the "
                                                                r"disc_shooter part 'shooter'"),
    ({"wheels": {"preset": "urethane-60a-4in", "diameter": 0.2}}, r"\[shooter.wheels\] diameter comes from"),
    ({"air": {"aero": "hh02"}}, r"aero comes from the range"),
    ({"lanes": [0.0]}, r"lanes: a disc shooter feeds one disc at a time"),
    ({"layout": None}, r"missing fence|fence"),
])
def test_disc_shooter_config_mistakes_named(shooter, match):
    with pytest.raises(ValueError, match=match):
        World(disc_cfg(shooter=shooter), None)


def test_disc_hopper_holds_four_by_default():
    """G24: a robot holds at most 4 discs; [shooter.hopper] capacity can change it."""
    with pytest.raises(PartError, match="hopper capacity = 4"):
        World(disc_cfg(range={"preload": 5}), None)
    w = World(disc_cfg(range={"preload": 5}, shooter={"hopper": {"capacity": 6}}), None)
    assert w.test_range.ball_path.held() == 5


# ------------------------------------------------------------------------------------------------ the rest

def _until(w, hal, done, seconds=3.0):
    for _ in range(int(round(seconds / w.model.opt.timestep))):
        w.step(hal)
        if done(w):
            return
    raise AssertionError("never happened")


def test_reset_robot_returns_the_channel_disc_to_the_hopper():
    w = World(disc_cfg(), None)
    rt = w.test_range
    run(w, 0.5, pwm_hal(p2=1.0))
    _until(w, pwm_hal(p2=1.0, p5=1.0), lambda w: rt.shooter.launcher.in_contact())
    assert rt.ball_path.in_launcher
    w.reset_robot()
    assert not rt.shooter.launcher.in_contact() and not rt.ball_path.in_launcher
    assert rt.ball_path.held() == 4 and rt.counts()["ledger"]["robot"] == 4
    assert rt.shooter.rps() == 0.0


def test_launcher_beam_break_sees_the_channel():
    w = World(disc_cfg(shooter={"beam_break": [{"at": "launcher", "dio": 0}]}), None)
    rt = w.test_range
    assert rt.ball_path.beam_updates() == [("DIO", "0", {"<>value": True})]
    run(w, 0.5, pwm_hal(p2=1.0))
    _until(w, pwm_hal(p2=1.0, p5=1.0), lambda w: rt.shooter.launcher.in_contact())
    assert rt.ball_path.beam_updates() == [("DIO", "0", {"<>value": False})]


def test_articulated_tilt_sets_the_launch_angle():
    """An articulated channel with no tilt motor bound rests on its low stop: the disc leaves at that angle."""
    w = World(disc_cfg(part={"tilt": "articulated", "tilt_range": [12.0, 40.0]}), None)
    run(w, 1.0, pwm_hal(p2=1.0))
    assert w.test_range.shooter.tilt_deg() == pytest.approx(12.0, abs=0.2)
    seen = _first_launch(w, pwm_hal(p2=1.0, p5=1.0))
    # the channel's x in the robot's frame is pitched up by the tilt (the kitbot itself sits 0.65 deg nose-up on
    # its contacts, so not in the world's)
    local = w.data.xmat[w.robot_body].reshape(3, 3).T @ seen["motion"]["base_mat"]
    assert math.degrees(math.atan2(local[2, 0], local[0, 0])) == pytest.approx(12.0, abs=0.2)
    assert w.test_range.balls.log[seen["i"]].hood_deg == pytest.approx(12.0, abs=0.2)


def test_aim_table_refuses_a_disc_shooter(tmp_path):
    from scrimmage.testrange.aim import AimError, load_model
    cfg = tmp_path / "disc.toml"
    cfg.write_text('''
[battery]
nominal_voltage = 12.0
resistance = 0.0
[range]
file = "2013"
floor_balls = false
[[part]]
name = "drive"
stock = "chassis"
[[part]]
name = "shooter"
stock = "disc_shooter"
attach = "drive.top"
[[part]]
name = "loader"
stock = "roller"
attach = "drive.top"
pos = [-0.3, 0.0, 0.2]
[shooter]
kind = "disc"
part = "shooter"
wheels = { preset = "urethane-60a-4in" }
layout = { fence = "wedgetop" }
[[shooter.stage]]
name = "loader"
part = "loader"
path_length = 0.3
''')
    with pytest.raises(AimError, match="disc table"):
        load_model(cfg, "high")


def test_wheel_groups_share_the_robot_battery():
    """Two wheel groups (a pinch, one per side) on their own PWMs: each shaft sees the world's solved bus voltage,
    their supply current sags it, and each group's binding reports its shaft's current; the spin joints mirror the
    shafts."""
    part = {"layout": "pinch", "wheel_count": 1, "wheel_start": 0.2, "groups": "per_side",
            "ids": {"shooter0": "PWM:2", "shooter1": "PWM:3"}}
    w = World(disc_cfg(part=part, shooter={"layout": {}}, battery={"resistance": 0.02}), None)
    rt = w.test_range
    shafts = rt.shooter.launcher.shafts
    assert len(shafts) == 2
    for _ in range(50):
        w.step(pwm_hal(p2=1.0, p3=0.5))
        assert all(s.bus_voltage == w.vbatt for s in shafts)
    total = sum(s.supply_current for s in shafts)
    assert total > 10.0 and w.vbatt < 12.0 - 0.02 * total + 0.05
    assert shafts[0].speed > shafts[1].speed > 0.0
    for g, shaft in enumerate(shafts):
        (mb,) = [mb for mb in w.motors if mb.name == f"shooter.shooter{g}"]
        assert mb.external and mb.current == pytest.approx(shaft.stator_current)
        dof = w.model.jnt_dofadr[w.model.joint(f"shooter.spin{g}").id]
        assert w.data.qvel[dof] == shaft.speed


def test_disc_setup_file_must_agree_with_the_part():
    """A disc setup file (`scrimmage disc run`'s) gives the tread and the fence; what the part gives must agree."""
    root = Path(__file__).resolve().parent.parent
    sec = {"setup": "examples/disc/linear.toml", "wheels": None, "layout": None}
    w = World(disc_cfg(part={"motor": "CIM"}, shooter=sec), root)
    setup = w.test_range.shooter.config.setup
    assert setup.layout.fence_friction == pytest.approx(1.0) and setup.wheels.durometer == pytest.approx(60.0)
    assert setup.shafts[0].motor == "CIM" and setup.shafts[0].wheel_inertia == pytest.approx(0.0008)
    with pytest.raises(ValueError, match=r"\[shaft\] motor = CIM but the disc_shooter part 'shooter'"):
        World(disc_cfg(shooter=sec), root)
    with pytest.raises(ValueError, match=r"\[layout\] channel_length = 0.5 but the disc_shooter part 'shooter' gives "
                                         r"0.6 \(from its channel_length\)"):
        World(disc_cfg(part={"motor": "CIM", "channel_length": 0.6}, shooter=sec), root)
