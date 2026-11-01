"""REV (SPARK MAX / SPARK Flex) bindings: motors and absolute encoders on the ScrimmageREV helper SimDevices."""

import json
import math
import tomllib
from pathlib import Path

import pytest

from scrimmage.bindings import _rev_device
from scrimmage.protocol import HalState
from scrimmage.world import World

ROOT = Path(__file__).resolve().parents[1]

ARM_XML = """<mujoco><worldbody><body name="arm"><joint name="shoulder" type="hinge" axis="0 1 0"
range="-0.5 1.0" limited="true"/><geom type="box" size="0.1 0.1 0.1" mass="1"/></body></worldbody></mujoco>"""
FREE_XML = """<mujoco><worldbody><body name="arm"><joint name="shoulder" type="hinge" axis="0 1 0"/>
<geom type="box" size="0.1 0.1 0.1" mass="1"/></body></worldbody></mujoco>"""


def world(tmp_path, extra: str, xml: str = ARM_XML) -> World:
    (tmp_path / "m.xml").write_text(xml)
    cfg = tomllib.loads('[model]\nmjcf = "m.xml"\n[robot]\nbody = "arm"\n' + extra)
    return World(cfg, tmp_path)


def set_joint(w: World, pos: float, vel: float = 0.0) -> None:
    import mujoco
    w.data.qpos[0] = pos
    w.data.qvel[0] = vel
    mujoco.mj_forward(w.model, w.data)


def ups(w: World, hal: HalState | None = None) -> dict:
    return {(t, d): data for t, d, data in w.sensor_updates(hal or HalState())}


def hal_with(device: str, volts: float, enabled: bool = True) -> HalState:
    hal = HalState()
    hal.apply(json.dumps({"type": "ScrimmageREV", "device": device, "data": {"<motorVoltage": volts}}))
    hal.apply(json.dumps({"type": "DriverStation", "device": "", "data": {">enabled": enabled}}))
    return hal


MAX = '[[motor]]\nname = "m"\nrev = "SparkMax:3"\njoint = "shoulder"\ngear_ratio = 10.0\n'


def test_rev_device_names():
    assert _rev_device({"rev": "SparkMax:3"}, "motor") == "SparkMax[3]"
    assert _rev_device({"rev": "SparkFlex:12"}, "motor") == "SparkFlex[12]"
    assert _rev_device({"rev": "SparkMaxAbs:3"}, "encoder") == "SparkMax[3]"
    assert _rev_device({"rev": "SparkFlexAbs:4"}, "encoder") == "SparkFlex[4]"


@pytest.mark.parametrize("spec,kind,message", [
    ("TalonFX:1", "motor", r"Kind one of SparkMax, SparkFlex"),
    ("SparkMaxAbs:1", "motor", r"only SparkMax or SparkFlex is supported there"),
    ("SparkMax:1", "encoder", r"only SparkMaxAbs or SparkFlexAbs is supported there"),
    ("Spark:1", "encoder", r"Kind one of SparkMaxAbs, SparkFlexAbs"),
    ("SparkMax", "motor", r"expected \"<Kind>:<id>\""),
    ("SparkMax:99", "motor", r"CAN id must be an integer 0-62"),
])
def test_rev_device_errors(spec, kind, message):
    with pytest.raises(ValueError, match=message):
        _rev_device({"rev": spec}, kind)


def test_motor_applies_voltage_with_sign_and_defaults(tmp_path):
    w = world(tmp_path, MAX, FREE_XML)
    m = w.motors[0]
    assert m.kind == "REV" and m.device_key == ("ScrimmageREV", "SparkMax[3]")
    assert m.brake is True
    assert m.motor.free_speed == pytest.approx(get_free("NEO"))
    hal = hal_with("SparkMax[3]", 6.0)
    for _ in range(100):
        w.step(hal)
    assert m.command == pytest.approx(6.0 / w.vbatt, rel=0.05)
    assert w.joint_states()["shoulder"][1] > 0.5

    wi = world(tmp_path, MAX + "invert = true\n", FREE_XML)
    for _ in range(100):
        wi.step(hal)
    assert wi.joint_states()["shoulder"][1] < -0.5


def get_free(name):
    from scrimmage.motors import get_motor
    return get_motor(name).free_speed


def test_flex_defaults_to_neo_vortex_and_explicit_motor_wins(tmp_path):
    flex = MAX.replace("SparkMax", "SparkFlex")
    assert world(tmp_path, flex, FREE_XML).motors[0].motor.free_speed == pytest.approx(get_free("NeoVortex"))
    w = world(tmp_path, flex + 'motor = "NEO550"\n', FREE_XML)
    assert w.motors[0].motor.free_speed == pytest.approx(get_free("NEO550"))


def test_motor_disabled_is_zero_volts(tmp_path):
    w = world(tmp_path, MAX, FREE_XML)
    hal = hal_with("SparkMax[3]", 6.0, enabled=False)
    for _ in range(20):
        w.step(hal)
    assert w.motors[0].voltage == 0.0 and w.motors[0].command == 0.0


def test_neutral_coast_opens_the_circuit(tmp_path):
    w = world(tmp_path, MAX + 'neutral = "coast"\n', FREE_XML)
    set_joint(w, 0.0, 10.0)
    w.step(hal_with("SparkMax[3]", 0.0))
    assert w.motors[0].current == 0.0


def test_motor_updates(tmp_path):
    w = world(tmp_path, MAX + "invert = true\n", FREE_XML)
    set_joint(w, 0.0, 2 * math.pi)  # 1 rev/s at the joint
    hal = hal_with("SparkMax[3]", 3.0)
    w.step(hal)
    u = ups(w, hal)[("ScrimmageREV", "SparkMax[3]")]
    assert u[">velocity"] == pytest.approx(w.data.qvel[0] * 10 * -1 * 60 / (2 * math.pi))
    # motor shaft rotations since the start, physical frame: the helper sets the built-in encoder from it (an
    # integral of velocity would drift)
    assert u[">position"] == pytest.approx(w.data.qpos[0] * 10 * -1 / (2 * math.pi))
    assert u[">vbus"] == pytest.approx(w.vbatt)
    assert u[">current"] == pytest.approx(abs(w.motors[0].current))
    assert u[">motor"] == 1.0                 # REV_MOTOR_CODES["NEO"]: HALSim can't carry strings to a SimDevice
    assert ">fwdLimit" not in u and ">revLimit" not in u


def test_motor_current_is_per_motor_for_a_gearbox_of_several(tmp_path):
    """count = 2 on one SPARK: the binding's current is the gearbox total, but the SPARK (and its smart current limit)
    sees one motor's."""
    w = world(tmp_path, MAX + "count = 2\n", FREE_XML)
    set_joint(w, 0.0, 2 * math.pi)
    hal = hal_with("SparkMax[3]", 6.0)
    w.step(hal)
    assert abs(w.motors[0].current) > 1.0
    assert ups(w, hal)[("ScrimmageREV", "SparkMax[3]")][">current"] == pytest.approx(abs(w.motors[0].current) / 2)


def test_motor_position_counts_from_the_joint_start(tmp_path):
    xml = FREE_XML.replace('axis="0 1 0"/>', 'axis="0 1 0" ref="30"/>')    # the joint starts at 30 degrees
    w = world(tmp_path, MAX, xml)
    assert w.model.qpos0[0] == pytest.approx(math.radians(30))
    set_joint(w, math.radians(30) + 2 * math.pi)                           # one joint turn from the start
    assert ups(w)[("ScrimmageREV", "SparkMax[3]")][">position"] == pytest.approx(10.0)


def test_flex_motor_name(tmp_path):
    w = world(tmp_path, MAX.replace("SparkMax", "SparkFlex"), FREE_XML)
    assert ups(w)[("ScrimmageREV", "SparkFlex[3]")][">motor"] == 3.0


def test_motor_codes_cover_the_rev_motors_and_default_the_rest(tmp_path):
    from scrimmage.bindings import REV_MOTOR_CODES
    assert REV_MOTOR_CODES == {"NEO": 1, "NEO550": 2, "NeoVortex": 3}
    w = world(tmp_path, MAX + 'motor = "NEO550"\n', FREE_XML)
    assert ups(w)[("ScrimmageREV", "SparkMax[3]")][">motor"] == 2.0
    w = world(tmp_path, MAX + 'motor = "Falcon500"\n', FREE_XML)
    assert ups(w)[("ScrimmageREV", "SparkMax[3]")][">motor"] == 0.0   # the helper keeps the SPARK's usual motor


def test_limits_pressed_at_range_ends(tmp_path):
    w = world(tmp_path, MAX + "limits = true\n")
    key = ("ScrimmageREV", "SparkMax[3]")
    set_joint(w, 0.0)
    u = ups(w)[key]
    assert (u[">fwdLimit"], u[">revLimit"]) == (False, False)
    set_joint(w, 1.0)
    u = ups(w)[key]
    assert (u[">fwdLimit"], u[">revLimit"]) == (True, False)
    set_joint(w, 1.2)
    assert ups(w)[key][">fwdLimit"] is True
    set_joint(w, -0.5)
    u = ups(w)[key]
    assert (u[">fwdLimit"], u[">revLimit"]) == (False, True)


def test_limits_need_a_joint_range(tmp_path):
    with pytest.raises(ValueError, match=r"limits = true.*range"):
        world(tmp_path, MAX + "limits = true\n", FREE_XML)


def test_encoder_abs_rotations(tmp_path):
    w = world(tmp_path, '[[encoder]]\nrev = "SparkMaxAbs:3"\njoint = "shoulder"\nratio = 2.0\n', FREE_XML)
    e = w.encoders[0]
    assert e.kind == "REV" and e.device_key == ("ScrimmageREV", "SparkMax[3]")
    set_joint(w, 2 * math.pi * 1.5)
    assert ups(w)[("ScrimmageREV", "SparkMax[3]")][">absRotations"] == pytest.approx(3.0)
    wi = world(tmp_path, '[[encoder]]\nrev = "SparkFlexAbs:4"\njoint = "shoulder"\ninvert = true\n', FREE_XML)
    set_joint(wi, math.pi)
    assert ups(wi)[("ScrimmageREV", "SparkFlex[4]")][">absRotations"] == pytest.approx(-0.5)
    wo = world(tmp_path, '[[encoder]]\nrev = "SparkMaxAbs:5"\njoint = "shoulder"\ninvert = true\noffset = 0.25\n',
               FREE_XML)
    set_joint(wo, math.pi)
    assert ups(wo)[("ScrimmageREV", "SparkMax[5]")][">absRotations"] == pytest.approx(-0.25)   # magnet sits at 0.25


def test_gyro_value_names(tmp_path):
    """WPILib's ADIS16470_IMU SimDevice is "Gyro:ADIS16470[<port>]" with gyro_angle_z / gyro_rate_z (CCW positive)."""
    w = world(tmp_path, '[[gyro]]\ndevice = "ADIS16470[0]"\nbody = "arm"\nangle = "gyro_angle_z"\n'
                        'rate = "gyro_rate_z"\ncw_positive = false\n', FREE_XML)
    u = ups(w)[("Gyro", "ADIS16470[0]")]
    assert set(u) == {">connected", ">gyro_angle_z", ">gyro_rate_z"}
    w = world(tmp_path, '[[gyro]]\ndevice = "ADXRS450[0]"\nbody = "arm"\naxis = "y"\n', FREE_XML)
    assert set(ups(w)[("Gyro", "ADXRS450[0]")]) == {">connected", ">angle_y", ">rate_y"}


def test_motor_and_encoder_may_share_a_spark(tmp_path):
    w = world(tmp_path, MAX + '[[encoder]]\nrev = "SparkMaxAbs:3"\njoint = "shoulder"\n', FREE_XML)
    assert len(w.motors) == 1 and len(w.encoders) == 1


def test_two_motors_on_one_spark_are_rejected(tmp_path):
    with pytest.raises(ValueError, match=r"SparkMax\[3\] is bound by motor m and motor n"):
        world(tmp_path, MAX + MAX.replace('"m"', '"n"'), FREE_XML)


def test_two_encoders_on_one_spark_are_rejected(tmp_path):
    enc = '[[encoder]]\nrev = "SparkMaxAbs:3"\njoint = "shoulder"\n'
    with pytest.raises(ValueError, match=r"SparkMax\[3\] is bound by encoder #1 and encoder #2"):
        world(tmp_path, enc + enc, FREE_XML)


def test_one_of_rev_ctre_pwm(tmp_path):
    with pytest.raises(ValueError, match="give only one of"):
        world(tmp_path, MAX + 'pwm = 1\n', FREE_XML)
