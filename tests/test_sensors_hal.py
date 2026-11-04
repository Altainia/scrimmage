"""What robot code reads from [[sensor]]s (scrimmage/sensors): DIO for beam breaks and distance switches, AI for
analog distance sensors, and a REV Color Sensor V3's SimDevice; the duplicate-channel check (Review Focus 3); and the
bridge's handling of the color sensor's SimDevice."""

from __future__ import annotations

import asyncio
import copy
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from range_fixtures import range_cfg, shooter_cfg, spares_world  # noqa: E402
from scrimmage.protocol import HalState  # noqa: E402
from scrimmage.sensors.runtime import color_values  # noqa: E402
from scrimmage.sensors.spec import color_device, parse_sensors, sensor_devices  # noqa: E402
from scrimmage.testrange.build import BALL_BITS  # noqa: E402
from scrimmage.world import World  # noqa: E402

# In the kitbot's chassis frame (origin on the floor at its center): past the front bumper, at the height of a 2026
# FUEL's center (the 2026 range's ball, 0.075 m in radius) resting on the carpet.
AHEAD = [0.45, 0.0, 0.075]
BEAM = {"name": "beam", "kind": "beam", "body": "drive.chassis", "from": [0.6, 0.2, 0.075], "to": [0.6, -0.2, 0.075],
        "dio": 0}
RANGE = {"name": "range", "kind": "distance", "body": "drive.chassis", "at": AHEAD, "toward": [1, 0, 0],
         "analog": 1, "dio": 2, "threshold": 0.3}
EYE = {"name": "eye", "kind": "color", "body": "drive.chassis", "at": AHEAD, "toward": [1, 0, 0], "i2c": "mxp"}


def world(*sensors, **overrides) -> World:
    cfg = range_cfg(**overrides)
    cfg["sensor"] = [copy.deepcopy(s) for s in sensors]
    return spares_world(cfg)


def step(w, n=1):
    hal = HalState()
    for _ in range(n):
        w.step(hal)


def updates(w) -> dict:
    """The sensors' HAL inputs, by (type, device)."""
    return {(typ, dev): data for typ, dev, data in w.sensors.sensor_updates()}


def ball_ahead(w, gap: float):
    """Spare ball 0 on the carpet, `gap` m past AHEAD along the chassis's x."""
    b = w.model.body("drive.chassis").id
    at = w.data.xpos[b] + w.data.xmat[b].reshape(3, 3) @ AHEAD
    r = w.test_range.balls.r
    w.test_range.balls.put(0, [at[0] + gap + r, at[1], r], BALL_BITS)


# --------------------------------------------------------------------------------------------------- the updates

def test_a_beam_break_is_a_dio_that_reads_false_while_broken():
    w = world(BEAM, {**BEAM, "name": "high", "dio": 1, "active_low": False})
    step(w)
    assert updates(w) == {("DIO", "0"): {"<>value": True}, ("DIO", "1"): {"<>value": False}}, "clear"
    ball_ahead(w, 0.15 - w.test_range.balls.r)      # the ball's center at x 0.6, on the beam
    step(w)
    assert updates(w) == {("DIO", "0"): {"<>value": False}, ("DIO", "1"): {"<>value": True}}, "broken"


def test_a_distance_sensor_is_an_analog_voltage_and_a_switch_at_its_threshold():
    w = world(RANGE)
    step(w)
    ups = updates(w)
    assert ups[("AI", "1")] == {">voltage": pytest.approx(2.0 * 0.977)}, "nothing in range: the max, 2 m"
    assert ups[("DIO", "2")] == {"<>value": True}, "off (active-low: high)"
    ball_ahead(w, 0.2)
    step(w)
    ups = updates(w)
    assert ups[("AI", "1")][">voltage"] == pytest.approx(0.2 * 0.977, abs=0.002)
    assert ups[("DIO", "2")] == {"<>value": False}, "closer than 0.3 m: on, reading low"


def test_an_analog_distance_sensor_is_clamped_to_five_volts():
    w = world({**RANGE, "max_range": 6.0, "threshold": 1.0})
    step(w)
    assert updates(w)[("AI", "1")] == {">voltage": 5.0}, "6 m at 0.977 V/m would be 5.86 V"


def test_a_color_sensor_is_its_simdevice_with_red_green_blue_ir_and_proximity():
    w = world(EYE, {**EYE, "name": "low", "i2c": "onboard"})
    step(w)
    ups = updates(w)
    assert set(ups) == {("SimDevice", "REV Color Sensor V3[1,82]"), ("SimDevice", "REV Color Sensor V3[0,82]")}
    dark = ups[("SimDevice", "REV Color Sensor V3[1,82]")]
    assert set(dark) == {">Red", ">Green", ">Blue", ">IR", ">Proximity"}
    assert dark[">Proximity"] == 0 and max(dark[">Red"], dark[">Green"], dark[">Blue"]) < 50, "sees nothing"
    ball_ahead(w, 0.03)
    step(w)
    seen = updates(w)[("SimDevice", "REV Color Sensor V3[1,82]")]
    values = color_values(w.sensors.reading("eye"))
    assert seen == {f">{k}": v for k, v in values.items()}
    assert seen[">Proximity"] > 1000 and seen[">Red"] > 500, seen   # a yellow 2026 FUEL 3 cm away
    assert all(isinstance(v, int) for v in seen.values())


def test_a_color_sensor_with_no_i2c_port_and_a_sensor_with_no_channel_send_nothing():
    w = world({k: v for k, v in EYE.items() if k != "i2c"}, {k: v for k, v in BEAM.items() if k != "dio"})
    step(w)
    assert w.sensors.sensor_updates() == []


def test_the_world_sends_the_sensors_with_everything_else():
    w = world(BEAM)
    step(w)
    assert ("DIO", "0", {"<>value": True}) in w.sensor_updates(HalState())


def test_color_devices_are_named_as_revlib_names_them():
    assert color_device("onboard") == ("SimDevice", "REV Color Sensor V3[0,82]")
    assert color_device("mxp") == ("SimDevice", "REV Color Sensor V3[1,82]")
    keys = [d.device_key for d in sensor_devices(parse_sensors([BEAM, RANGE, EYE]))]
    assert keys == [("DIO", "0"), ("AI", "1"), ("DIO", "2"), ("SimDevice", "REV Color Sensor V3[1,82]")]


# ------------------------------------------------------------------------------------- Review Focus 3: channels

def test_two_sensors_on_one_dio_is_an_error_naming_both():
    with pytest.raises(ValueError, match=r'DIO 0 is bound by \[\[sensor\]\] "beam" and \[\[sensor\]\] "range": '
                                         r"give each device one binding"):
        world(BEAM, {**RANGE, "dio": 0})


def test_two_color_sensors_on_one_port_is_an_error_naming_both():
    with pytest.raises(ValueError, match=r'^REV Color Sensor V3\[1,82\] is bound by \[\[sensor\]\] "eye" and '
                                         r'\[\[sensor\]\] "eye2"'):
        world(EYE, {**EYE, "name": "eye2"})


def test_a_sensor_on_a_shooter_beam_breaks_dio_is_an_error_naming_both():
    cfg = shooter_cfg(shooter={"beam_break": [{"at": "launcher", "dio": 7}]})
    cfg["sensor"] = [{**BEAM, "dio": 7}]
    with pytest.raises(ValueError, match=r'DIO 7 is bound by \[\[shooter.beam_break\]\] at launcher and '
                                         r'\[\[sensor\]\] "beam"'):
        World(cfg, None)


def test_a_sensor_on_a_limit_switchs_dio_or_a_potentiometers_analog_is_an_error():
    with pytest.raises(ValueError, match=r'AI 1 is bound by analog #1 and \[\[sensor\]\] "range"'):
        world(RANGE, analog=[{"port": 1, "joint": "dt.left_1"}])
    with pytest.raises(ValueError, match=r'DIO 0 is bound by digital #1 and \[\[sensor\]\] "beam"'):
        world(BEAM, digital=[{"channel": 0, "joint": "dt.left_1"}])


def test_one_distance_sensors_dio_and_analog_on_the_same_number_are_two_devices():
    w = world({**RANGE, "analog": 2, "dio": 2})
    step(w)
    assert {("AI", "2"), ("DIO", "2")} == set(updates(w))


# ------------------------------------------------------------------------------- the bridge and the SimDevice

def color_bridge(tmp_path):
    from scrimmage.bridge import Bridge
    cfg = range_cfg()
    cfg["sensor"] = [copy.deepcopy(EYE)]
    return Bridge(spares_world(cfg), "localhost", 0, nt=False, speed=None, held=True)


def test_a_color_sensor_the_robot_never_created_is_reported(caplog, tmp_path):
    caplog.set_level(logging.WARNING, logger="scrimmage")
    bridge = color_bridge(tmp_path)
    bridge.hal.devices[("SimDevice", "REV Color Sensor V3[0,82]")] = {}

    async def main():
        await bridge._on_tick(1.9)
        assert not [r for r in caplog.records if "Color Sensor" in r.getMessage()]
        await bridge._on_tick(2.0)
        await bridge._on_tick(2.5)
    asyncio.run(main())
    msgs = [r.getMessage() for r in caplog.records if "Color Sensor" in r.getMessage()]
    assert msgs == ['[[sensor]] "eye" is a REV Color Sensor V3 on the MXP I2C port, but the robot program hasn\'t '
                    "created one there 2 s after connecting (it has one on the onboard port); check the port the "
                    "robot code gives ColorSensorV3 and the sensor's i2c"], msgs
    assert msgs[0] in bridge.world.warnings


def test_a_color_sensor_the_robot_created_is_not_reported(caplog, tmp_path):
    caplog.set_level(logging.WARNING, logger="scrimmage")
    bridge = color_bridge(tmp_path)
    bridge.hal.devices[("SimDevice", "REV Color Sensor V3[1,82]")] = {">Red": 0.0}
    asyncio.run(bridge._on_tick(2.0))
    assert not [r for r in caplog.records if "Color Sensor" in r.getMessage()]


def test_the_color_sensors_values_are_resent_when_its_simdevice_appears(tmp_path):
    """halsim_ws_client drops values for a SimDevice that doesn't exist yet, and a SimDevice sends no <init: scrimmage
    resends the color sensor's values when the robot program first creates it, not up to FULL_REFRESH later."""
    from fake_robot import FakeRobot, next_port, running, wait_until
    from scrimmage.bridge import Bridge
    from scrimmage.protocol import frame

    key = ("SimDevice", "REV Color Sensor V3[1,82]")

    def color_frames(robot, start):
        return [m["data"] for m in robot.frames[start:] if (m["type"], m["device"]) == key]

    async def main():
        port = next_port()
        cfg = range_cfg()
        cfg["sensor"] = [copy.deepcopy(EYE)]
        bridge = Bridge(spares_world(cfg), "localhost", port, nt=False, speed=None, held=True)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True) as robot:
                await wait_until(lambda: bridge.driver is not None)
                for _ in range(2):
                    assert await bridge.driver.step_once() == "ok"
                assert any(">Red" in d for d in color_frames(robot, 0))     # sent before the device existed: lost
                await robot.ws.send(frame(*key, {">Red": 0.0}))
                await wait_until(lambda: key in bridge.hal.devices)
                mark = len(robot.frames)
                assert await bridge.driver.step_once() == "ok"
                sent = {k: v for d in color_frames(robot, mark) for k, v in d.items()}
                assert {">Red", ">Green", ">Blue", ">IR", ">Proximity"} <= set(sent), sent
                # only its first appearance: the robot relaying a value back doesn't resend what hasn't changed
                await robot.ws.send(frame(*key, {">Green": 14.0}))
                await asyncio.sleep(0.05)
                mark = len(robot.frames)
                assert await bridge.driver.step_once() == "ok"
                assert not color_frames(robot, mark)
                assert bridge.world.time < 0.5
    asyncio.run(main())
