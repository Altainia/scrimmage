import math

from scrimmage.launcher.inputs import BatteryIn, Control, Shaft
from scrimmage.launcher.shaft import ShaftModel, VelocityController

DT = 0.001


def run(shaft, control, seconds, battery=None):
    model = ShaftModel(shaft, battery or BatteryIn())
    ctrl = VelocityController(control, shaft)
    log = []
    for _ in range(round(seconds / DT)):
        model.step(DT, ctrl.voltage(model), 0.0)
        log.append((model.speed, model.stator_current, model.supply_current, model.bus_voltage))
    return model, log


def test_spins_up_to_target():
    model, _ = run(Shaft(wheel_inertia=0.002), Control(target_rps=50.0, kP=0.5), 3.0)
    assert math.isclose(model.speed / (2 * math.pi), 50.0, rel_tol=0.02)


def time_to(log, speed):
    return next(i for i, row in enumerate(log) if row[0] >= speed)


def test_heavier_spins_up_slower():
    control = Control(target_rps=50.0, kP=0.5)
    _, light = run(Shaft(wheel_inertia=0.002), control, 3.0)
    _, heavy = run(Shaft(wheel_inertia=0.002, added_inertia=0.004), control, 3.0)
    goal = 0.9 * 50.0 * 2 * math.pi
    assert time_to(heavy, goal) > time_to(light, goal)


def test_stator_limit_never_exceeded():
    _, log = run(Shaft(wheel_inertia=0.002), Control(target_rps=50.0, kP=5.0, stator_limit=40.0), 1.0)
    peak = max(abs(row[1]) for row in log)
    assert peak <= 40.5
    assert peak > 35.0  # the limit is actually what shapes the spin-up


def test_supply_limit_never_exceeded():
    _, log = run(Shaft(wheel_inertia=0.002), Control(target_rps=50.0, kP=5.0, supply_limit=30.0), 1.0)
    peak = max(abs(row[2]) for row in log)
    assert peak <= 30.5
    assert peak > 25.0


def test_battery_sag_and_recovery():
    battery = BatteryIn()
    _, log = run(Shaft(wheel_inertia=0.002), Control(target_rps=50.0, kP=5.0), 3.0, battery)
    assert min(row[3] for row in log) < battery.nominal_voltage - 0.5
    assert math.isclose(log[-1][3], battery.nominal_voltage, abs_tol=0.1)


def test_free_speed_cap():
    shaft = Shaft(wheel_inertia=0.002)
    model, _ = run(shaft, Control(target_rps=1000.0, kP=5.0), 5.0)
    from scrimmage.motors import get_motor

    free = get_motor(shaft.motor, shaft.motor_count).Kv * model.bus_voltage / shaft.gearing
    assert math.isclose(model.speed, free, rel_tol=0.02)


def test_load_torque_slows_wheel():
    shaft = Shaft(wheel_inertia=0.002)
    model = ShaftModel(shaft, BatteryIn())
    free = ShaftModel(shaft, BatteryIn())
    for _ in range(500):
        model.step(DT, 6.0, 2.0)
        free.step(DT, 6.0, 0.0)
    assert model.speed < free.speed


def test_current_limits_are_per_motor():
    # As on a Talon, each motor has its own limit: two motors draw up to twice it in total.
    shaft = Shaft(wheel_inertia=0.002, motor_count=2)
    _, log = run(shaft, Control(target_rps=50.0, kP=5.0, stator_limit=40.0), 1.0)
    peak = max(abs(row[1]) for row in log)
    assert 45.0 < peak <= 2 * 40.0 + 0.5
    _, log = run(shaft, Control(target_rps=50.0, kP=5.0, supply_limit=30.0), 1.0)
    peak = max(abs(row[2]) for row in log)
    assert 35.0 < peak <= 2 * 30.0 + 0.5


def test_zero_step_is_a_no_op():
    model = ShaftModel(Shaft(wheel_inertia=0.002), BatteryIn())
    model.speed = 100.0
    model.step(0.0, 12.0, 1.0)
    assert model.speed == 100.0 and model.stator_current == 0.0
    model.step(-0.001, 12.0, 1.0)
    assert model.speed == 100.0
