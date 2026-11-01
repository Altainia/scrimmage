"""Fast-mode Scrimmage devices (the fastmode library's ScrimmageMotor/ScrimmageEncoder/ScrimmageGyro SimDevices)."""

import json
import math
import tomllib

import mujoco
import pytest

from scrimmage.protocol import HalState
from scrimmage.world import World

FREE_XML = """<mujoco><worldbody><body name="arm"><joint name="shoulder" type="hinge" axis="0 1 0"/>
<geom type="box" size="0.1 0.1 0.1" mass="1"/></body></worldbody></mujoco>"""
YAW_XML = """<mujoco><worldbody><body name="arm"><freejoint/><geom type="box" size="0.1 0.1 0.1" mass="1"/>
</body></worldbody></mujoco>"""


def world(tmp_path, extra: str, xml: str = FREE_XML) -> World:
    (tmp_path / "m.xml").write_text(xml)
    cfg = tomllib.loads('[model]\nmjcf = "m.xml"\n[robot]\nbody = "arm"\n' + extra)
    return World(cfg, tmp_path)


def ups(w: World) -> dict:
    return {(t, d): data for t, d, data in w.sensor_updates(HalState())}


def hal_with(name: str, volts: float, brake=None, enabled: bool = True) -> HalState:
    hal = HalState()
    data = {"<motorVoltage": volts} | ({} if brake is None else {"<brake": brake})
    hal.apply(json.dumps({"type": "ScrimmageMotor", "device": name, "data": data}))
    hal.apply(json.dumps({"type": "DriverStation", "device": "", "data": {">enabled": enabled}}))
    return hal


MOTOR = '[[motor]]\nname = "m"\nscrimmage = "arm_drive"\njoint = "shoulder"\ngear_ratio = 10.0\nmotor = "KrakenX60"\n'


def test_motor_voltage_gives_torque_in_the_voltage_sign(tmp_path):
    w = world(tmp_path, MOTOR)
    m = w.motors[0]
    assert m.kind == "Scrimmage" and m.device_key == ("ScrimmageMotor", "arm_drive")
    hal = hal_with("arm_drive", 6.0)
    for _ in range(100):
        w.step(hal)
    assert m.command == pytest.approx(6.0 / w.vbatt, rel=0.05)
    assert w.joint_states()["shoulder"][1] > 0.5
    w2 = world(tmp_path, MOTOR)
    hal = hal_with("arm_drive", -6.0)
    for _ in range(100):
        w2.step(hal)
    assert w2.joint_states()["shoulder"][1] < -0.5


def test_motor_disabled_is_zero_volts_and_brake_follows_the_device(tmp_path):
    w = world(tmp_path, MOTOR)
    for _ in range(5):
        w.step(hal_with("arm_drive", 6.0, enabled=False))
    assert w.motors[0].voltage == 0.0
    w.step(hal_with("arm_drive", 0.0, brake=False))
    assert w.motors[0].brake is False
    w.step(hal_with("arm_drive", 0.0, brake=True))
    assert w.motors[0].brake is True


def test_motor_writes_rotor_units_with_gear_ratio_and_invert(tmp_path):
    w = world(tmp_path, MOTOR)
    w.data.qpos[0] = math.pi / 2
    w.data.qvel[0] = math.pi
    mujoco.mj_forward(w.model, w.data)
    u = ups(w)[("ScrimmageMotor", "arm_drive")]
    assert u[">position"] == pytest.approx(2.5) and u[">velocity"] == pytest.approx(5.0)
    assert u[">vbus"] == pytest.approx(w.vbatt)
    inv = world(tmp_path, MOTOR + "invert = true\n")
    inv.data.qpos[0] = math.pi / 2
    inv.data.qvel[0] = math.pi
    mujoco.mj_forward(inv.model, inv.data)
    u = ups(inv)[("ScrimmageMotor", "arm_drive")]
    assert u[">position"] == pytest.approx(-2.5) and u[">velocity"] == pytest.approx(-5.0)


def test_encoder_reports_magnet_rotations(tmp_path):
    w = world(tmp_path, '[[encoder]]\nscrimmage = "cc"\njoint = "shoulder"\n')
    e = w.encoders[0]
    assert e.kind == "Scrimmage" and e.device_key == ("ScrimmageEncoder", "cc")
    w.data.qpos[0] = math.pi / 2
    w.data.qvel[0] = math.pi
    mujoco.mj_forward(w.model, w.data)
    u = ups(w)[("ScrimmageEncoder", "cc")]
    assert u[">position"] == pytest.approx(0.25) and u[">velocity"] == pytest.approx(0.5)


def test_gyro_reports_unwrapped_ccw_yaw_and_rate(tmp_path):
    w = world(tmp_path, '[[gyro]]\nscrimmage = "imu"\nbody = "arm"\n', YAW_XML)
    g = w.gyros[0]
    assert g.device_key == ("ScrimmageGyro", "imu")
    ups(w)                                           # the first sample is the zero
    yaw = math.radians(30)
    w.data.qpos[3:7] = [math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)]
    w.data.qvel[5] = math.radians(10)
    mujoco.mj_forward(w.model, w.data)
    u = ups(w)[("ScrimmageGyro", "imu")]
    assert u[">yaw"] == pytest.approx(30.0) and u[">rate"] == pytest.approx(10.0)


def test_one_device_name_one_binding(tmp_path):
    with pytest.raises(ValueError, match="arm_drive"):
        world(tmp_path, MOTOR + MOTOR)
    with pytest.raises(ValueError, match="sets pwm and scrimmage"):
        world(tmp_path, '[[motor]]\npwm = 1\nscrimmage = "x"\njoint = "shoulder"\n')


@pytest.mark.parametrize("keys, named", [('ctre = "Pigeon2:1"\nscrimmage = "imu"\n', "ctre and scrimmage"),
                                         ('scrimmage = "imu"\ndevice = "ADXRS450[0]"\n', "scrimmage and device"),
                                         ('ctre = "Pigeon2:1"\ndevice = "ADXRS450[0]"\n', "ctre and device")])
def test_gyro_takes_one_of_ctre_scrimmage_device(tmp_path, keys, named):
    with pytest.raises(ValueError, match=f"gyro sets {named}: give only one of ctre, scrimmage, device"):
        world(tmp_path, '[[gyro]]\n' + keys + 'body = "arm"\n', YAW_XML)
