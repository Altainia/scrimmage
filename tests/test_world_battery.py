"""The shared battery under a heavy load: the bus voltage is solved within each step, so it sags smoothly instead of
flipping between 0 V and nominal (the one-step lag it once had), and it agrees with the currents the motors draw."""

import numpy as np
import pytest

from scrimmage.motors import get_motor
from scrimmage.protocol import HalState
from scrimmage.world import World

BASE = {"name": "base", "stock": "chassis", "mass": 200.0}
# Four shooter-like flywheels, each two Kraken X60s direct: from rest, at full output, eight motors at stall would
# want about 3000 A, 60 V of sag on the default 0.02 ohm battery. With the bus voltage taken from the last step's
# currents it flipped between 0 V and nominal every step.
ROLLERS = [{"name": f"r{i}", "stock": "flywheel", "attach": "base.top", "pos": [0.0, 0.3 * i - 0.45, 0.1],
            "moi": 0.005, "count": 2, "ids": {"spin": f"PWM:{i}"}} for i in range(4)]
BATTERY = {"nominal_voltage": 12.5, "resistance": 0.02}


def heavy_world() -> World:
    return World({"part": [BASE, *ROLLERS], "robot": {"start_pose": [2.0, 4.0, 0.0]}, "battery": BATTERY,
                  "field": {"obstacle": False}}, None)


def full_output(w: World, steps: int) -> np.ndarray:
    hal = HalState()
    for i in range(4):
        hal.devices[("PWM", str(i))] = {"<init": True, "<speed": 1.0}
    out = []
    for _ in range(steps):
        w.step(hal)
        out.append(w.vbatt)
    return np.array(out)


def test_heavy_load_sags_smoothly():
    w = heavy_world()
    v = full_output(w, 500)
    assert np.all((v > 0.0) & (v <= BATTERY["nominal_voltage"])), v[:10]
    # near stall the motors share the battery: 8 Krakens' 0.033 ohm windings in parallel with nothing else, so the
    # bus starts near 12.5 / (1 + 0.02 * 8 / R): about 2 V, and rises as they speed up
    r = get_motor("KrakenX60").R
    assert v[0] == pytest.approx(12.5 / (1 + 0.02 * 8 / r), rel=0.05)
    steps = np.diff(v)
    assert np.all(np.abs(steps) < 0.05), np.abs(steps).max()     # no flipping: tiny changes step to step
    assert np.all(steps > -1e-6)                                  # sagging less as the motors speed up


def test_bus_voltage_agrees_with_the_currents():
    w = heavy_world()
    full_output(w, 50)
    total = sum(mb.supply_current for mb in w.motors)
    assert w.vbatt == pytest.approx(BATTERY["nominal_voltage"] - BATTERY["resistance"] * total, abs=1e-3)
    for mb in w.motors:
        assert abs(mb.voltage) <= w.vbatt + 1e-9          # no controller puts out more than its supply


def test_idle_robot_sits_at_nominal():
    w = heavy_world()
    hal = HalState()
    for _ in range(10):
        w.step(hal)
    assert w.vbatt == BATTERY["nominal_voltage"]


def test_drive_robot_current_at_start_speed():
    """A kitbot at full output from rest on the default battery: each drive motor's current is the motor's at its
    shaft's speed at the step's start (its real load is the chassis, through the carpet, which a per-joint estimate
    of the step's end speed can't see), the bus agrees with the summed currents, and it doesn't oscillate."""
    from range_fixtures import KITBOT
    w = World({"part": KITBOT, "robot": {"start_pose": [2.0, 4.0, 0.0]}, "battery": BATTERY,
               "field": {"obstacle": False}}, None)
    hal = HalState()
    for port, speed in (("0", 1.0), ("1", -1.0)):          # straight ahead: the right side is inverted
        hal.devices[("PWM", port)] = {"<init": True, "<speed": speed}
    v = []
    for _ in range(500):
        start = [w.data.qvel[mb.dadr] * mb.ratio for mb in w.motors]
        w.step(hal)
        v.append(w.vbatt)
        for mb, speed in zip(w.motors, start):
            assert mb.current == pytest.approx(mb.motor.current(speed, mb.voltage), rel=1e-12)
        total = sum(mb.supply_current for mb in w.motors)
        assert w.vbatt == pytest.approx(BATTERY["nominal_voltage"] - BATTERY["resistance"] * max(0.0, total), abs=1e-3)
    v = np.array(v)
    assert np.all((v > 0.0) & (v <= BATTERY["nominal_voltage"]))
    # The wheels stick and slip on the carpet as the robot accelerates (they do with a stiff, 0 ohm battery too), so
    # the load wiggles and the bus with it, by tenths of a volt; with the old lagged bus it swung by 10 V a step
    # (1.4, 11.3, 5.3, ... V).
    assert np.abs(np.diff(v)).max() < 1.0, np.abs(np.diff(v)).max()


def test_bus_solve_is_cheap_at_steady_load():
    """The bus solve mustn't slow robots down (no range, no balls): at a steady load it costs at most 3 supply
    evaluations a step (Newton from the last step's answer; the bracketed solve it replaced took 6 to 8). Counted, not
    timed, so a busy CI machine can't flake it. The rangeless swerve of tests/test_range_model.py: 6 V on its four
    drives, its wheels sticking and slipping on the carpet so the load moves a little every step."""
    from pathlib import Path
    from scrimmage.config import build_world, load_config
    w = build_world(load_config(Path(__file__).resolve().parent.parent / "configs" / "swerve.toml"))
    hal = HalState()
    hal.devices[("DriverStation", "")] = {">enabled": True}
    for i in (1, 3, 5, 7):
        hal.devices[("CANMotor", f"Talon FX (v6)[{i}]")] = {"<motorVoltage": 6.0}
    for _ in range(500):
        w.step(hal)
    per_step = []
    for _ in range(1000):
        before = w.bus_evaluations
        w.step(hal)
        per_step.append(w.bus_evaluations - before)
        total = sum(mb.supply_current for mb in w.motors)
        assert w.vbatt == pytest.approx(12.5 - 0.02 * max(0.0, total), abs=1e-3)   # and still the right answer
    assert max(per_step) <= 3, max(per_step)
    assert w.vbatt < 12.5                                 # it does draw


@pytest.mark.parametrize("output", [{"<init": True, "<speed": 0.7}, {"<motorVoltage": 6.0}, {"<motorVoltage": -11.0}])
def test_supply_slope_matches_finite_difference(output):
    """The Newton solve's derivative of each motor's supply current: a duty cycle, a voltage under the bus and one
    clamped to it (with the shaft spinning, so the back-EMF counts)."""
    from pathlib import Path
    from scrimmage.bindings import MotorBinding
    from scrimmage.config import build_world, load_config
    w = build_world(load_config(Path(__file__).resolve().parent.parent / "configs" / "swerve.toml"))
    duty = "<speed" in output
    joint = _drive_joint(w)
    mb = MotorBinding({"name": "m", "pwm": 0, "joint": joint, "motor": "KrakenX60"} if duty else
                      {"name": "m", "ctre": "TalonFX:9", "joint": joint}, w.model)
    w.data.qvel[mb.dadr] = 30.0
    hal = HalState()
    hal.devices[("PWM", "0") if duty else ("CANMotor", "Talon FX (v6)[9]")] = dict(output)
    mb.read(hal, w.data)
    for bus in (4.0, 9.0, 12.5):
        i, di = mb.supply_and_slope(bus)
        assert i == mb.supply_at(bus)
        h = 1e-6
        assert di == pytest.approx((mb.supply_at(bus + h) - mb.supply_at(bus - h)) / (2 * h), rel=1e-5, abs=1e-6)


def _drive_joint(w) -> str:
    import mujoco
    return mujoco.mj_id2name(w.model, mujoco.mjtObj.mjOBJ_JOINT, int(w.model.dof_jntid[w.motors[0].dadr]))
