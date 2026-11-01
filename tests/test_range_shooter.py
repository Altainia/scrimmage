"""The launcher part and the shooter in the world: the stock part, the [shooter] section, the shooter shaft run by the
launcher model on the shared battery and mirrored to the spin joint, and the hood joint setting the exit angle."""

from __future__ import annotations

import math

import mujoco
import pytest

from range_fixtures import shooter_cfg
from scrimmage.launcher.inputs import Shaft
from scrimmage.launcher.shaft import ShaftModel
from scrimmage.parts.model import PartError
from scrimmage.parts.stock import build_stock, stock_params
from scrimmage.protocol import HalState
from scrimmage.world import World

TALON = "Talon FX (v6)[1]"


def pwm_hal(**pwm) -> HalState:
    hal = HalState()
    for port, speed in pwm.items():
        hal.devices[("PWM", port.removeprefix("p"))] = {"<init": True, "<speed": speed}
    return hal


def talon_hal(volts: float) -> HalState:
    hal = HalState()
    hal.devices[("CANMotor", TALON)] = {"<motorVoltage": volts}
    return hal


def run(w, seconds, hal, each=None):
    for _ in range(int(round(seconds / w.model.opt.timestep))):
        if each is not None:
            each(w)
        w.step(hal)


def spin_dof(w) -> int:
    return int(w.model.jnt_dofadr[w.model.joint("launcher.spin").id])


def shooter_bindings(w):
    return [mb for mb in w.motors if mb.name.startswith("launcher.shooter")]


# ------------------------------------------------------------------------------------------------ the stock part

def test_stock_launcher_builds_both_hoods():
    art = build_stock("launcher", {}, "t")
    assert set(art.slot_kinds()) == {"shooter", "hood", "hood_encoder"}
    assert set(art.joint_names()) == {"spin", "hood"}
    hood = next(b.joint for b in art.bodies if b.joint is not None and b.joint.name == "hood")
    assert hood.range == pytest.approx((0.0, 50.0))
    assert all(not g.collide for b in art.bodies for g in b.geoms)
    assert [m.name for m in art.mounts] == ["base"]
    fixed = build_stock("launcher", {"hood": "fixed"}, "t")
    assert set(fixed.slot_kinds()) == {"shooter"}
    assert fixed.joint_names() == ["spin"]


def test_stock_params_fills_defaults():
    p = stock_params("launcher", {"gearing": 1.5}, "t")
    assert p["gearing"] == 1.5 and p["wheel_diameter"] == 0.1016 and p["hood_range"] == (20.0, 70.0)
    with pytest.raises(PartError, match="unknown parameter 'wheel_dia'"):
        stock_params("launcher", {"wheel_dia": 0.1}, "t")


def test_instance_keeps_stock_params():
    w = World(shooter_cfg(launcher={"gearing": 1.5}), None)
    insts = {i.name: i for i in w.parts.instances}
    assert insts["launcher"].stock_params["gearing"] == 1.5
    assert insts["drive"].stock_params["length"] == 0.66


# ------------------------------------------------------------------------------------------------ the shaft

def test_shooter_spins_up_like_standalone():
    w = World(shooter_cfg(launcher={"ids": {"shooter": "TalonFX:1"}}), None)
    run(w, 2.0, talon_hal(10.0))
    ref = ShaftModel(Shaft(wheel_inertia=0.002, added_inertia=0.0, motor="KrakenX60", motor_count=1, gearing=1.0), None)
    for _ in range(2000):
        ref.step(0.001, 10.0, 0.0)
    assert ref.speed > 100.0
    assert w.data.qvel[spin_dof(w)] == pytest.approx(ref.speed, rel=1e-6)
    assert w.test_range.shooter.rps() == pytest.approx(ref.speed / (2 * math.pi), rel=1e-12)
    qadr = w.model.jnt_qposadr[w.model.joint("launcher.spin").id]
    assert w.data.qpos[qadr] - w.model.qpos0[qadr] == pytest.approx(ref.angle, rel=1e-6)


def test_encoder_reads_shaft():
    w = World(shooter_cfg(launcher={"ids": {"shooter": "TalonFX:1"}, "gearing": 1.5}), None)
    hal = talon_hal(8.0)
    run(w, 0.5, hal)
    ups = {(t, d): v for t, d, v in w.sensor_updates(hal)}
    rotor = ups[("CANEncoder", TALON + "/Rotor Sensor")]
    rps = w.test_range.shooter.rps()
    assert rps > 10.0
    assert rotor[">velocity"] == pytest.approx(rps * 1.5, rel=1e-6)


def test_shooter_sags_shared_battery():
    w = World(shooter_cfg(battery={"resistance": 0.02}), None)
    hal = pwm_hal(p0=0.5, p2=1.0)
    drive = next(mb for mb in w.motors if mb.name == "dt.left")
    shaft = w.test_range.shooter.launcher.shaft
    for _ in range(50):
        w.step(hal)
        # the bus voltage is solved within the step (not the last step's): the drive and the shooter see that bus
        assert drive.voltage == pytest.approx(0.5 * w.vbatt, rel=1e-12)
        assert shaft.bus_voltage == w.vbatt
        others = sum(mb.supply_current for mb in w.motors if not mb.external)
        # counted once: the solve takes the shooter's current at the step's start speed, the shaft model its
        # step average, a little lower as it spins up
        assert w.vbatt == pytest.approx(w.battery.voltage(others + shaft.supply_current), rel=0.01)
        assert w.vbatt == pytest.approx(
            w.battery.voltage(others + w.test_range.shooter.supply_at(w.vbatt)), abs=0.05)
    assert shaft.supply_current > 10.0
    assert w.vbatt < 12.0 - 0.02 * shaft.supply_current + 0.05
    (mb,) = shooter_bindings(w)
    assert mb.supply_current == pytest.approx(shaft.supply_current)
    assert mb.current == pytest.approx(shaft.stator_current)


def test_external_binding_applies_no_torque():
    w = World(shooter_cfg(), None)
    base = float(w.model.dof_damping[spin_dof(w)])
    w.step(pwm_hal(p2=1.0))
    (mb,) = shooter_bindings(w)
    assert mb.external and mb.voltage == pytest.approx(12.0)
    assert w.data.qfrc_applied[spin_dof(w)] == 0.0
    assert w.model.dof_damping[spin_dof(w)] == base
    assert w.data.qvel[spin_dof(w)] == w.test_range.shooter.launcher.shaft.speed > 0.0


def test_reversed_shooter_draws_while_plugging():
    """A reverse voltage on a forward-spinning shaft brakes it by plugging: the current is against the shaft's spin
    and the controller's duty is negative, so the battery supplies it (signed duty, like MotorBinding.supply_at), not
    regeneration offsetting the other motors."""
    w = World(shooter_cfg(launcher={"ids": {"shooter": "TalonFX:1"}}, battery={"resistance": 0.02}), None)
    run(w, 1.0, talon_hal(10.0))
    shooter = w.test_range.shooter
    assert shooter.launcher.shaft.speed > 100.0
    w.step(talon_hal(-10.0))
    assert shooter.launcher.shaft.speed > 50.0             # still spinning forward
    assert shooter.supply_at(w.vbatt) > 10.0
    assert shooter.supply_at(0.0) > 10.0                   # the limit as the bus falls: the back-EMF alone drives it
    assert w.vbatt < 12.0 - 0.02 * 10.0                    # the battery sags under it
    assert shooter.supply_current() > 10.0                 # and the bindings report the draw


@pytest.mark.parametrize("volts", [10.0, -10.0, 4.0, 14.0])
def test_shooter_supply_slope_matches_finite_difference(volts):
    """The Newton bus solve's derivative of the shooter's supply current, driving and plugging."""
    w = World(shooter_cfg(launcher={"ids": {"shooter": "TalonFX:1"}}, battery={"resistance": 0.02}), None)
    run(w, 0.3, talon_hal(10.0))
    w.step(talon_hal(volts))
    shooter = w.test_range.shooter
    for bus in (6.0, 9.0, 12.0):
        i, di = shooter.supply_and_slope(bus)
        h = 1e-6
        assert i == shooter.supply_at(bus)
        assert di == pytest.approx((shooter.supply_at(bus + h) - shooter.supply_at(bus - h)) / (2 * h),
                                   rel=1e-5, abs=1e-6)


def test_coasting_shooter_keeps_its_speed():
    """A coast-mode motor at neutral is an open circuit: no current, so the wheel (no friction in the model) keeps
    its speed instead of braking on its back-EMF."""
    w = World(shooter_cfg(launcher={"ids": {"shooter": "TalonFX:1"}}), None)
    run(w, 0.3, talon_hal(10.0))
    shaft = w.test_range.shooter.launcher.shaft
    speed = shaft.speed
    run(w, 0.3, talon_hal(0.0))
    assert shaft.speed == pytest.approx(speed, rel=1e-9)
    assert shaft.supply_current == pytest.approx(0.0, abs=1e-9)


def test_brake_mode_slows_the_shooter_faster_than_coast():
    """At neutral a brake-mode motor shorts its winding (0 V): the shaft brakes on its back-EMF; coast keeps it."""
    speeds = {}
    for neutral in ("brake", "coast"):
        w = World(shooter_cfg(launcher={"ids": {"shooter": {"ctre": "TalonFX:1", "neutral": neutral}}}), None)
        run(w, 0.3, talon_hal(10.0))
        before = w.test_range.shooter.launcher.shaft.speed
        run(w, 0.2, talon_hal(0.0))
        speeds[neutral] = w.test_range.shooter.launcher.shaft.speed / before
    assert speeds["coast"] == pytest.approx(1.0, rel=1e-9)
    assert speeds["brake"] < 0.5


def test_unbound_shooter_coasts():
    w = World(shooter_cfg(launcher={"ids": {"hood": "PWM:3"}}), None)
    sh = w.test_range.shooter
    assert sh.bindings == []
    sh.launcher.shaft.speed = 100.0
    run(w, 0.2, pwm_hal())
    assert sh.launcher.shaft.speed == pytest.approx(100.0, rel=1e-9)
    assert w.data.qvel[spin_dof(w)] == pytest.approx(100.0, rel=1e-9)


def test_reset_restarts_the_launcher():
    w = World(shooter_cfg(), None)
    run(w, 0.2, pwm_hal(p2=1.0))
    old = w.test_range.shooter.launcher
    assert old.shaft.speed > 0.0 and old.time > 0.0
    w.reset()
    new = w.test_range.shooter.launcher
    assert new.time == 0.0 and new.shaft.speed == 0.0 and new.shaft.angle == 0.0
    assert w.data.qvel[spin_dof(w)] == 0.0
    w.step(pwm_hal(p2=1.0))
    assert shooter_bindings(w)[0].external


# ------------------------------------------------------------------------------------------------ the hood

def test_hood_angle_follows_joint():
    w = World(shooter_cfg(), None)
    sh = w.test_range.shooter
    joint = w.model.joint("launcher.hood")
    qadr = int(w.model.jnt_qposadr[joint.id])
    seen = []

    def check(w):
        assert sh.hood_deg() == pytest.approx(20.0 + math.degrees(w.data.qpos[qadr]), rel=1e-9)
        seen.append(sh.hood_deg())

    run(w, 0.15, pwm_hal(p3=0.2), each=check)
    assert sh.launcher.exit_angle_deg == pytest.approx(seen[-1], rel=1e-12)   # set from the joint, each step
    assert 25.0 < sh.hood_deg() < 70.0
    w.step(pwm_hal(p3=0.2))
    assert sh.launcher.exit_angle_deg != seen[-1]


def test_fixed_hood_uses_hood_angle():
    w = World(shooter_cfg(launcher={"hood": "fixed", "hood_angle": 45.0, "ids": {"shooter": "PWM:2"}}), None)
    sh = w.test_range.shooter
    assert sh.hood_deg() == 45.0 and sh.launcher.exit_angle_deg == 45.0
    run(w, 0.05, pwm_hal(p2=0.5))
    assert sh.hood_deg() == 45.0 and sh.launcher.exit_angle_deg == 45.0


# ------------------------------------------------------------------------------------------------ the [shooter] section

def test_shooter_config_built_from_the_part_and_range():
    w = World(shooter_cfg(launcher={"wheel_diameter": 0.1, "hood_gap": 0.11, "wheel_moi": 0.003, "added_moi": 0.001,
                                    "count": 2, "gearing": 1.25, "ids": {"shooter": ["PWM:2", "PWM:4"]}}), None)
    s = w.test_range.shooter.config.setup
    assert s.wheels.diameter == 0.1 and s.hood.gap == 0.11 and s.hood.entry_deg == 240.0
    assert s.hood.exit_angle_deg == 20.0
    assert s.shaft.wheel_inertia == 0.003 and s.shaft.added_inertia == 0.001
    assert s.shaft.motor_count == 2 and s.shaft.gearing == 1.25
    assert s.launcher.width == 0.36
    base = w.model.body("launcher.base").id
    assert s.launcher.height == pytest.approx(w.data.xpos[base][2], abs=1e-9)
    assert s.ball == w.parts.range.ball
    assert s.wheels.durometer == 35.0 and s.wheels.friction == 1.0 and s.hood.friction == 0.3
    assert w.test_range.shooter.config.lanes == (-0.08, 0.08)
    assert len(shooter_bindings(w)) == 2 and all(mb.external for mb in shooter_bindings(w))


def test_shooter_key_from_part_rejected():
    cfg = shooter_cfg(shooter={"wheels": {"durometer": 35.0, "friction": 1.0, "diameter": 0.1}})
    with pytest.raises(ValueError, match=r"\[shooter\.wheels\] diameter.*launcher part 'launcher'.*wheel_diameter"):
        World(cfg, None)
    with pytest.raises(ValueError, match=r"\[shooter\] ball.*range"):
        World(shooter_cfg(shooter={"ball": {"preset": "2020-power-cell"}}), None)
    with pytest.raises(ValueError, match=r"\[shooter\.hood\] exit_angle_deg.*hood joint"):
        World(shooter_cfg(shooter={"hood": {"friction": 0.3, "exit_angle_deg": 40.0}}), None)
    with pytest.raises(ValueError, match=r"\[shooter\] shaft.*launcher part"):
        World(shooter_cfg(shooter={"shaft": {"gearing": 2.0}}), None)


def test_setup_file_conflict_names_both(tmp_path):
    (tmp_path / "mine.toml").write_text("[wheels]\ndiameter = 0.0762\npreset = \"30A\"\n[goal]\ndistance = 3.0\n",
                                        encoding="utf-8")
    cfg = shooter_cfg(shooter={"setup": "mine.toml", "wheels": None})
    with pytest.raises(ValueError, match=r"mine\.toml.*\[wheels\] diameter = 0\.0762.*wheel_diameter = 0\.1016"):
        World(cfg, tmp_path)


def test_setup_file_supplies_sections(tmp_path):
    (tmp_path / "mine.toml").write_text(
        "[wheels]\npreset = \"30A\"\ndiameter = 0.1016\nwidth = 0.04\n[hood]\nfriction = 0.6\nflare_deg = 20.0\n"
        "exit_angle_deg = 45.0\n[launcher]\nheight = 0.5\nwidth = 0.36\n"
        "[control]\ntarget_rps = 60.0\nkP = 0.2\n[goal]\ndistance = 3.0\nheight = 2.0\ndiameter = 1.0\n",
        encoding="utf-8")
    w = World(shooter_cfg(shooter={"setup": "mine.toml", "wheels": {"width": 0.05}, "hood": None}), tmp_path)
    s = w.test_range.shooter.config.setup
    assert s.wheels.width == 0.05 and s.wheels.durometer == 30.0     # the inline section overrides the file's
    assert s.hood.friction == 0.6 and s.hood.flare_deg == 20.0
    # the file's [launcher] height is ignored (the mount gives it); its exit angle lies within hood_range
    assert s.launcher.height == pytest.approx(w.data.xpos[w.model.body("launcher.base").id][2], abs=1e-9) != 0.5
    assert s.hood.exit_angle_deg == 20.0


def test_shooter_without_range_rejected():
    cfg = shooter_cfg(range=None)
    with pytest.raises(ValueError, match=r"\[shooter\].*\[range\]"):
        World(cfg, None)


def test_hood_range_wrap_rejected():
    with pytest.raises(ValueError, match=r"\[shooter\].*hood_range.*wrap"):
        World(shooter_cfg(launcher={"hood_range": [20.0, 150.0]}), None)
    with pytest.raises(ValueError, match=r"\[shooter\].*hood_angle.*wrap"):
        World(shooter_cfg(launcher={"hood": "fixed", "hood_angle": 80.0, "hood_entry_deg": 160.0,
                                    "ids": {"shooter": "PWM:2"}}), None)


def test_shooter_part_must_be_a_stock_launcher():
    with pytest.raises(ValueError, match=r"\[shooter\] part 'nope'.*parts: drive, dt, launcher"):
        World(shooter_cfg(shooter={"part": "nope"}), None)
    with pytest.raises(ValueError, match=r"\[shooter\] part 'drive'.*stock launcher"):
        World(shooter_cfg(shooter={"part": "drive"}), None)


def test_lane_must_fit_the_launcher():
    with pytest.raises(ValueError, match=r"\[shooter\] lanes.*0\.12"):
        World(shooter_cfg(shooter={"lanes": [-0.12, 0.08]}), None)


def test_unknown_shooter_key_rejected():
    with pytest.raises(ValueError, match=r"\[shooter\].*'feed'.*allowed"):
        World(shooter_cfg(shooter={"feed": {"count": 3}}), None)


def test_preload_over_hopper_capacity_rejected():
    """Task 3 reads [shooter.hopper] capacity for the robot's ball slots."""
    with pytest.raises(PartError, match=r"preload = 5.*capacity = 3"):
        World(shooter_cfg(range={"preload": 5}, shooter={"hopper": {"capacity": 3}}), None)
    w = World(shooter_cfg(range={"preload": 3}, shooter={"hopper": {"capacity": 3}}), None)
    assert len(w.parts.layout.ball_names) - w.parts.layout.n_floor == 3
    assert w.test_range.shooter.config.capacity == 3
