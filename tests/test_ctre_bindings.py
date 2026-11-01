"""Phoenix 6 (CTRE) bindings: TalonFX, CANcoder and Pigeon 2 devices driven from the MuJoCo world."""

import json
import math
import tomllib
from pathlib import Path

import numpy as np
import pytest

from scrimmage.bindings import _ctre_device, pigeon_attitude, resolve_device_key
from scrimmage.protocol import HalState
from scrimmage.world import World

ROOT = Path(__file__).resolve().parents[1]


def world_with(extra: str) -> World:
    """The hand-written kitbot model (tests/models/kitbot.xml) with only the bindings in `extra`."""
    text = (ROOT / "tests" / "fake_robot.toml").read_text().split("[[motor]]")[0]
    cfg = tomllib.loads(text + extra)
    return World(cfg, ROOT / "tests")


CTRE = """
[[motor]]
name = "lw"
ctre = "TalonFX:1"
joint = "left_center"
motor = "KrakenX60"
gear_ratio = 6.75
[[encoder]]
ctre = "CANcoder:2"
joint = "left_center"
offset = 0.1
[[gyro]]
ctre = "Pigeon2:3"
body = "chassis"
"""


def updates_by_key(w: World, hal: HalState) -> dict:
    return {(t, d): data for t, d, data in w.sensor_updates(hal)}


def set_joint(w: World, name: str, pos: float, vel: float) -> None:
    import mujoco
    j = mujoco.mj_name2id(w.model, mujoco.mjtObj.mjOBJ_JOINT, name)
    w.data.qpos[w.model.jnt_qposadr[j]] = pos
    w.data.qvel[w.model.jnt_dofadr[j]] = vel
    mujoco.mj_forward(w.model, w.data)


def test_talonfx_rotor_sensor_and_supply():
    w = world_with(CTRE)
    set_joint(w, "left_center", 2 * math.pi, 4 * math.pi)  # 1 rev, 2 rev/s at the joint
    u = updates_by_key(w, HalState())
    rotor = u[("CANEncoder", "Talon FX (v6)[1]/Rotor Sensor")]
    assert rotor[">rawPositionInput"] == pytest.approx(6.75)
    assert rotor[">velocity"] == pytest.approx(13.5)
    assert u[("CANMotor", "Talon FX (v6)[1]")][">supplyVoltage"] == pytest.approx(w.vbatt)


def test_talonfx_applies_motor_voltage_as_torque():
    w = world_with(CTRE)
    hal = HalState()
    hal.apply(json.dumps({"type": "CANMotor", "device": "Talon FX (v6)[1]", "data": {"<motorVoltage": 6.0}}))
    hal.apply(json.dumps({"type": "DriverStation", "device": "", "data": {">enabled": True}}))
    for _ in range(200):
        w.step(hal)
    m = [mb for mb in w.motors if mb.name == "lw"][0]
    assert m.current > 1.0 and w.joint_states()["left_center"][1] > 0.5
    assert m.command == pytest.approx(6.0 / w.vbatt, rel=0.05)
    assert m.supply_current > 0.0


def test_talonfx_disabled_and_brake_at_zero_volts():
    w = world_with(CTRE.replace('gear_ratio = 6.75', 'gear_ratio = 6.75\nneutral = "brake"'))
    hal = HalState()
    hal.apply(json.dumps({"type": "CANMotor", "device": "Talon FX (v6)[1]", "data": {"<motorVoltage": 6.0}}))
    hal.apply(json.dumps({"type": "DriverStation", "device": "", "data": {">enabled": False}}))
    for _ in range(50):
        w.step(hal)
    m = [mb for mb in w.motors if mb.name == "lw"][0]
    assert m.voltage == 0.0 and m.command == 0.0
    # brake at 0 V: a spinning wheel gets back-EMF current opposing it
    set_joint(w, "left_center", 0.0, 10.0)
    hal.apply(json.dumps({"type": "CANMotor", "device": "Talon FX (v6)[1]", "data": {"<motorVoltage": 0.0}}))
    w.step(hal)
    assert m.current < -1.0


def test_talonfx_defaults_to_coast_at_zero_volts():
    """Phoenix 6's factory NeutralMode is Coast, so a ctre motor binding defaults to coast."""
    w = world_with(CTRE)
    m = [mb for mb in w.motors if mb.name == "lw"][0]
    assert m.brake is False
    set_joint(w, "left_center", 0.0, 10.0)
    hal = HalState()
    hal.apply(json.dumps({"type": "CANMotor", "device": "Talon FX (v6)[1]", "data": {"<motorVoltage": 0.0}}))
    w.step(hal)
    assert m.current == 0.0 and m.supply_current == 0.0


def test_pwm_motor_still_defaults_to_brake():
    w = world_with('[[motor]]\npwm = 0\njoint = "left_center"\n')
    assert w.motors[0].brake is True


SLIDE_XML = """<mujoco><worldbody><body name="carriage"><joint name="lift" type="slide" axis="0 0 1"/>
<geom type="box" size="0.1 0.1 0.1" mass="1"/></body></worldbody></mujoco>"""


def test_talonfx_rotor_on_a_slide_joint(tmp_path):
    """gear_ratio is motor radians per joint unit, so a 0.1 m elevator at ratio 50 is 50 * 0.1 / 2pi rotor revs."""
    (tmp_path / "m.xml").write_text(SLIDE_XML)
    cfg = tomllib.loads('[model]\nmjcf = "m.xml"\n[robot]\nbody = "carriage"\n'
                        '[[motor]]\nctre = "TalonFX:5"\njoint = "lift"\nmotor = "KrakenX60"\ngear_ratio = 50.0\n')
    w = World(cfg, tmp_path)
    set_joint(w, "lift", 0.1, 0.2)
    u = updates_by_key(w, HalState())
    rotor = u[("CANEncoder", "Talon FX (v6)[5]/Rotor Sensor")]
    assert rotor[">rawPositionInput"] == pytest.approx(50 * 0.1 / (2 * math.pi))
    assert rotor[">velocity"] == pytest.approx(50 * 0.2 / (2 * math.pi))


def test_inverted_talonfx_binding_flips_voltage_and_sensor():
    w = world_with(CTRE.replace('gear_ratio = 6.75', 'gear_ratio = 6.75\ninvert = true'))
    set_joint(w, "left_center", 2 * math.pi, 0.0)
    u = updates_by_key(w, HalState())
    assert u[("CANEncoder", "Talon FX (v6)[1]/Rotor Sensor")][">rawPositionInput"] == pytest.approx(-6.75)
    hal = HalState()
    hal.apply(json.dumps({"type": "CANMotor", "device": "Talon FX (v6)[1]", "data": {"<motorVoltage": 6.0}}))
    for _ in range(200):
        w.step(hal)
    assert w.joint_states()["left_center"][1] < -0.5


def test_cancoder_and_pigeon():
    w = world_with(CTRE)
    set_joint(w, "left_center", math.pi, 0.0)
    w.sensor_updates(HalState())   # the Pigeon boots at yaw 0 wherever the robot points, then tracks turns
    w.set_robot_pose(2.0, 4.0, 30.0)
    u = updates_by_key(w, HalState())
    cc = u[("CANEncoder", "CANcoder (v6)[2]")]
    assert cc[">rawPositionInput"] == pytest.approx(0.6)        # 0.5 rev + 0.1 offset
    assert cc[">supplyVoltage"] == 5.0
    pg = u[("CANGyro", "Pigeon 2 (v6)[3]")]
    assert pg[">rawYawInput"] == pytest.approx(30.0, abs=0.01)  # CCW positive, like Pigeon2.getYaw()
    assert pg[">supplyVoltage"] == 12.0


def test_pigeon_yaw_unwraps():
    w = world_with(CTRE)
    g = w.gyros[0]
    for hdg in range(0, 400, 20):
        w.set_robot_pose(2.0, 4.0, float(hdg))
        u = updates_by_key(w, HalState())
    assert u[("CANGyro", "Pigeon 2 (v6)[3]")][">rawYawInput"] == pytest.approx(380.0, abs=0.01)
    assert g.device_key == ("CANGyro", "Pigeon 2 (v6)[3]")


def _quat(axis, deg):
    a = np.asarray(axis, float) / np.linalg.norm(axis)
    h = math.radians(deg) / 2
    return np.array([math.cos(h), *(math.sin(h) * a)])


def _qmul(a, b):
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return np.array([w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2, w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                     w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2, w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2])


def test_pigeon_attitude_axes():
    """Pigeon 2 axes: X forward, Y left, Z up; roll about X, pitch about Y (right-hand rule)."""
    yaw, pitch, roll, _ = pigeon_attitude(_quat([1, 0, 0], 10), np.zeros(3))
    assert (yaw, pitch, roll) == pytest.approx((0, 0, 10), abs=1e-9)
    yaw, pitch, roll, _ = pigeon_attitude(_quat([0, 1, 0], -15), np.zeros(3))   # nose up
    assert (yaw, pitch, roll) == pytest.approx((0, -15, 0), abs=1e-9)
    # yaw 90 then pitch 20 (intrinsic Z-Y-X): yaw and pitch both recovered
    q = _qmul(_quat([0, 0, 1], 90), _quat([0, 1, 0], 20))
    yaw, pitch, roll, _ = pigeon_attitude(q, np.zeros(3))
    assert (yaw, pitch, roll) == pytest.approx((90, 20, 0), abs=1e-9)


def test_pigeon_rates_are_in_the_body_frame():
    # robot yawed 90 deg CCW; a world-frame spin about world +x is a spin about the robot's -y
    _, _, _, rates = pigeon_attitude(_quat([0, 0, 1], 90), np.array([1.0, 0.0, 0.5]))
    assert rates == pytest.approx([0.0, -math.degrees(1.0), math.degrees(0.5)], abs=1e-9)


def test_ctre_device_names():
    assert _ctre_device({"ctre": "TalonFX:1"}, "motor") == "Talon FX (v6)[1]"
    assert _ctre_device({"ctre": "CANcoder:2"}, "encoder") == "CANcoder (v6)[2]"
    assert _ctre_device({"ctre": "Pigeon2:3"}, "gyro") == "Pigeon 2 (v6)[3]"
    assert _ctre_device({"ctre": "TalonFX:1", "device": "Custom[1]"}, "motor") == "Custom[1]"
    with pytest.raises(ValueError, match="TalonFX.*CANcoder.*Pigeon2"):
        _ctre_device({"ctre": "Falcon:1"}, "motor")
    with pytest.raises(ValueError, match="TalonFX"):
        _ctre_device({"ctre": "CANcoder:1"}, "motor")   # wrong kind for a motor binding
    assert _ctre_device({"ctre": "TalonFX:62"}, "motor") == "Talon FX (v6)[62]"
    for bad in ("TalonFX:63", "TalonFX:-1", "TalonFX:x"):
        with pytest.raises(ValueError, match="0-62"):
            _ctre_device({"ctre": bad}, "motor")


def test_two_device_keys_in_one_binding_is_an_error():
    with pytest.raises(ValueError, match="only one"):
        world_with('[[motor]]\ncan = "Talon FX (v6)[1]"\nctre = "TalonFX:1"\njoint = "left_center"\n')
    with pytest.raises(ValueError, match="only one"):
        world_with('[[encoder]]\ncan = "X[1]"\nctre = "CANcoder:1"\njoint = "left_center"\n')


def test_device_keys():
    w = world_with(CTRE)
    assert w.motors[0].device_key == ("CANMotor", "Talon FX (v6)[1]")
    assert w.encoders[0].device_key == ("CANEncoder", "CANcoder (v6)[2]")
    hal = HalState()
    assert resolve_device_key(hal, w.encoders[0].device_key) is None
    hal.apply(json.dumps({"type": "CANEncoder", "device": "CANcoder (v6)[2]", "data": {">position": 0.0}}))
    assert resolve_device_key(hal, w.encoders[0].device_key) == "CANcoder (v6)[2]"


def test_quadrature_encoder_device_key_resolves_by_channel():
    """HALSim names Encoder devices by index, so the key matches on <channel_a instead of the name."""
    w = world_with('[[encoder]]\nchannel_a = 4\njoint = "left_center"\n')
    key = w.encoders[0].device_key
    assert key == ("Encoder", "<channel_a=4")
    hal = HalState()
    hal.apply(json.dumps({"type": "Encoder", "device": "0", "data": {"<channel_a": 2, "<init": True}}))
    assert resolve_device_key(hal, key) is None
    hal.apply(json.dumps({"type": "Encoder", "device": "1", "data": {"<channel_a": 4, "<init": True}}))
    assert resolve_device_key(hal, key) == "1"
