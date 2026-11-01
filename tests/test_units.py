import json
import math
from pathlib import Path

import mujoco
import pytest

from scrimmage.motors import MOTORS, Battery, get_motor
from scrimmage.protocol import HalState, OutboundCache

ROOT = Path(__file__).resolve().parents[1]


def test_motor_free_speed_and_stall():
    for name, m in MOTORS.items():
        # at free speed with nominal voltage the motor draws (about) its free current
        assert m.current(m.free_speed, m.nominal_voltage) == pytest.approx(m.free_current, rel=1e-6), name
        # stalled at nominal voltage it makes stall torque
        assert m.torque(m.current(0.0, m.nominal_voltage)) == pytest.approx(m.stall_torque, rel=1e-6), name


def test_gearbox_scales_torque_not_speed():
    one, two = get_motor("NEO", 1), get_motor("NEO", 2)
    assert two.stall_torque == pytest.approx(2 * one.stall_torque)
    assert two.free_speed == pytest.approx(one.free_speed)
    assert two.R == pytest.approx(one.R / 2)


def test_unknown_motor():
    with pytest.raises(ValueError):
        get_motor("Flux Capacitor")


def test_battery_sag():
    b = Battery(12.5, 0.02)
    assert b.voltage(0) == 12.5
    assert b.voltage(100) == pytest.approx(10.5)
    assert b.voltage(10_000) == 0.0


def test_hal_state_merges_deltas_and_finds_encoders():
    s = HalState()
    s.apply(json.dumps({"type": "Encoder", "device": "0", "data": {"<init": True, "<channel_a": 4}}))
    s.apply(json.dumps({"type": "Encoder", "device": "0", "data": {"<reverse_direction": True}}))
    s.apply("not json")
    s.apply(json.dumps(["not", "an", "object"]))
    assert s.find("Encoder", "<channel_a", 4) == "0"
    assert s.get("Encoder", "0", "<reverse_direction") is True
    assert s.find("Encoder", "<channel_a", 6) is None


def test_outbound_cache_only_sends_changes():
    c = OutboundCache()
    assert len(c.frames([("AI", "0", {">voltage": 1.0})])) == 1
    assert c.frames([("AI", "0", {">voltage": 1.0})]) == []
    assert len(c.frames([("AI", "0", {">voltage": 1.5})])) == 1


def test_kitbot_model_loads_and_rolls_without_slip():
    """Constant voltage on both sides: chassis speed should match wheel surface speed."""
    m = mujoco.MjModel.from_xml_path(str(ROOT / "tests" / "models" / "kitbot.xml"))
    d = mujoco.MjData(m)
    dof = {n: m.jnt_dofadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, n)] for n in ("left_center", "right_center")}
    mot, g = get_motor("CIM", 2), 10.71
    for _ in range(1500):
        d.qfrc_applied[:] = 0
        for dd in dof.values():
            d.qfrc_applied[dd] = mot.torque(mot.current(d.qvel[dd] * g, 3.0)) * g
        mujoco.mj_step(m, d)
    wheel_speed = d.qvel[dof["left_center"]] * 0.0762
    assert d.qvel[0] > 0.5
    assert d.qvel[0] == pytest.approx(wheel_speed, rel=0.02)
    assert abs(d.qvel[1]) < 0.01  # went straight
