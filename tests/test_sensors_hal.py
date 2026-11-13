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

from range_fixtures import KITBOT, range_cfg, shooter_cfg, spares_world  # noqa: E402
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


def encoder_kitbot(**ids) -> list:
    """KITBOT with its drive's encoder slots given DIO ids, e.g. left_encoder="DIO:0"."""
    return [KITBOT[0], {**KITBOT[1], "ids": {**KITBOT[1]["ids"], **ids}}]


@pytest.mark.parametrize("dio", [0, 1])
def test_a_sensor_on_either_of_an_encoders_dio_channels_is_an_error_naming_both(dio):
    """An encoder on channel_a = 0 is WPILib's Encoder(0, 1): it takes DIO 0 and DIO 1."""
    with pytest.raises(ValueError, match=rf'^DIO {dio} is bound by encoder dt.left_encoder and \[\[sensor\]\] "beam": '
                                         r"give each device one binding \(an encoder on channel_a = 0 takes DIO 0 and "
                                         r"1\)$"):
        world({**BEAM, "dio": dio}, part=encoder_kitbot(left_encoder="DIO:0"))


def test_a_limit_switch_or_a_shooter_beam_break_on_an_encoders_dio_is_an_error_too():
    with pytest.raises(ValueError, match=r"^DIO 3 is bound by encoder dt.right_encoder and digital #1"):
        world(part=encoder_kitbot(right_encoder="DIO:2"), digital=[{"channel": 3, "joint": "dt.left_1"}])
    cfg = shooter_cfg(shooter={"beam_break": [{"at": "launcher", "dio": 4}]})
    cfg["part"][1]["ids"] = {**cfg["part"][1]["ids"], "left_encoder": "DIO:4"}
    with pytest.raises(ValueError, match=r"^DIO 4 is bound by encoder dt.left_encoder and \[\[shooter.beam_break\]\]"):
        World(cfg, None)


def test_a_sensor_next_to_an_encoders_channels_is_fine():
    w = world({**BEAM, "dio": 2}, part=encoder_kitbot(left_encoder="DIO:0"))
    assert w.sensors is not None


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


# ------------------------------------------------------------------------------------ CANrange and LaserCAN

CANRANGE = {"name": "cr", "kind": "distance", "body": "drive.chassis", "at": AHEAD, "toward": [1, 0, 0],
            "ctre": "CANrange:5"}
LASERCAN = {"name": "lc", "kind": "distance", "body": "drive.chassis", "at": AHEAD, "toward": [1, 0, 0],
            "grapple": "LaserCAN:7"}
CR_KEY = ("CAN", "CANrange (v6)[5]")
LC_KEY = ("ScrimmageGrapple", "LaserCAN[7]")


def test_canrange_update_seen():
    w = world(CANRANGE)
    ball_ahead(w, 0.30)
    step(w)
    assert updates(w) == {CR_KEY: {">distance": pytest.approx(0.30, abs=1e-3), ">supplyVoltage": 12.0}}


def test_canrange_update_nothing():
    w = world(CANRANGE)
    step(w)
    assert updates(w)[CR_KEY][">distance"] == w.sensors.specs[0].length


def test_lasercan_update_seen_and_nothing():
    from scrimmage.sensors.runtime import LASERCAN_STATUS_NO_TARGET, LASERCAN_STATUS_VALID
    w = world(LASERCAN)
    ball_ahead(w, 0.30)
    step(w)
    assert updates(w)[LC_KEY] == {">distance_mm": 300, ">status": LASERCAN_STATUS_VALID}
    w = world(LASERCAN)
    step(w)
    assert updates(w)[LC_KEY] == {">distance_mm": round(w.sensors.specs[0].length * 1000),
                                  ">status": LASERCAN_STATUS_NO_TARGET}


def test_two_lasercans_read_their_own():
    other = {**LASERCAN, "name": "lc2", "grapple": "LaserCAN:8", "at": [0.45, 0.2, 0.075]}
    w = world(LASERCAN, other)
    ball_ahead(w, 0.30)
    step(w)
    ups = updates(w)
    assert ups[LC_KEY][">distance_mm"] == 300
    assert ups[("ScrimmageGrapple", "LaserCAN[8]")][">status"] == 2, "its beam passes the ball"


FAST_RANGE = {"name": "fr", "kind": "distance", "body": "drive.chassis", "at": AHEAD, "toward": [1, 0, 0],
              "scrimmage": "intake_range"}
FR_KEY = ("ScrimmageCANrange", "intake_range")


def test_fast_canrange_update():
    w = world(FAST_RANGE)
    ball_ahead(w, 0.30)
    step(w)
    assert updates(w) == {FR_KEY: {">distance": pytest.approx(0.30, abs=1e-3)}}
    w = world(FAST_RANGE)
    step(w)
    assert updates(w)[FR_KEY] == {">distance": w.sensors.specs[0].length}


def test_fast_canrange_is_bound_not_extra():
    from scrimmage.devicecheck import check_devices
    w = world(FAST_RANGE)
    hal = HalState()
    hal.devices[FR_KEY] = {}
    hal.devices[("ScrimmageCANrange", "other")] = {}
    extra = [r for r in check_devices(w, hal, robot_time=5) if r.status == "extra"]
    assert [r.made for r in extra] == ["ScrimmageCANrange:other"]


def test_fast_canrange_missing_warning():
    w = world(FAST_RANGE)
    hal = HalState()
    (msg,) = w.sensors.missing_devices(hal, 2.0)
    assert 'ScrimmageCANrange "intake_range"' in msg and '"fr"' in msg and "fast-mode IO" in msg
    assert "scrimmage" in msg
    hal.devices[FR_KEY] = {}
    assert w.sensors.missing_devices(hal, 2.0) == []


def test_device_and_analog_together():
    w = world({**CANRANGE, "analog": 1})
    ball_ahead(w, 0.30)
    step(w)
    ups = updates(w)
    assert ups[("AI", "1")][">voltage"] > 0 and ups[CR_KEY][">distance"] == pytest.approx(0.30, abs=1e-3)


def test_unbound_canrange_is_listed_not_modeled():
    from scrimmage.devicecheck import check_devices
    w = world(CANRANGE)
    hal = HalState()
    hal.devices[CR_KEY] = {}
    hal.devices[("CAN", "CANrange (v6)[8]")] = {}
    extra = [r for r in check_devices(w, hal, robot_time=5) if r.status == "extra"]
    assert [(r.kind, r.made) for r in extra] == [("CANrange", "CANrange 8")]
    w.not_modeled = {("CANrange", 8)}
    assert not [r for r in check_devices(w, hal, robot_time=5) if r.status == "extra"]


def test_missing_device_warned_and_said_again_on_reconnect():
    w = world(CANRANGE, LASERCAN)
    hal = HalState()
    (a, b) = w.sensors.missing_devices(hal, 2.0)
    assert '"cr"' in a and "CANrange 5" in a and "hasn't created it 2 s after connecting" in a and "ctre" in a
    assert '"lc"' in b and "LaserCAN 7" in b and "helper" in b
    hal.devices[CR_KEY] = {}
    assert w.sensors.missing_devices(hal, 2.0) == [b], "a robot reconnect says it again"
    assert w.warnings.count(a) == 1 and w.warnings.count(b) == 1, "the run's notes have it once"


def test_missing_lasercan_names_the_ones_the_robot_created():
    w = world(LASERCAN)
    hal = HalState()
    hal.devices[("ScrimmageGrapple", "LaserCAN[8]")] = {}
    hal.devices[("ScrimmageGrapple", "LaserCAN[12]")] = {}
    (msg,) = w.sensors.missing_devices(hal, 2.0, "java", "unavailable: ignored when others exist")
    assert "created LaserCAN 8, LaserCAN 12 and no LaserCAN 7" in msg and "grapple" in msg, msg


def test_missing_lasercan_quotes_the_helpers_reason():
    w = world(LASERCAN)
    (msg,) = w.sensors.missing_devices(HalState(), 2.0, "python", "unavailable: libgrapplefrc has no LaserCAN")
    assert "helper is unavailable in the robot program (libgrapplefrc has no LaserCAN)" in msg, msg


@pytest.mark.parametrize("language, words", [
    ("java", ["scrimmage java path", "scrimmage java build", "Java agent"]),
    ("python", ["--scrimmage-lockstep", "[robot_program] command", "pip install ./lockstep", "robotInit"]),
    (None, ["scrimmage java build", "--scrimmage-lockstep", "pip install ./lockstep"]),
])
def test_missing_lasercan_says_how_to_load_the_helper(language, words):
    w = world(LASERCAN)
    (msg,) = w.sensors.missing_devices(HalState(), 2.0, language, None)
    assert all(word in msg for word in words), msg
    if language == "java":
        assert "--scrimmage-lockstep" not in msg
    if language == "python":
        assert "scrimmage java build" not in msg


def test_a_lasercan_on_a_wrong_id_is_listed_as_made():
    from scrimmage.devicecheck import check_devices
    w = world(LASERCAN)
    hal = HalState()
    hal.devices[LC_KEY] = {}
    hal.devices[("ScrimmageGrapple", "LaserCAN[8]")] = {}
    extra = [r for r in check_devices(w, hal, robot_time=5) if r.status == "extra"]
    assert [(r.kind, r.made) for r in extra] == [("LaserCAN", "LaserCAN 8")], "LaserCAN 7 is bound, 8 isn't"
    from scrimmage.bindings import parse_not_modeled
    w.not_modeled = parse_not_modeled(["LaserCAN:8"])[0]
    assert not [r for r in check_devices(w, hal, robot_time=5) if r.status == "extra"]


def test_a_lasercans_values_are_resent_when_its_simdevice_appears():
    """The helper creates its SimDevice with the robot's LaserCAN, with no <init: scrimmage resends the LaserCAN's
    values when it first appears, not up to FULL_REFRESH later."""
    from fake_robot import FakeRobot, next_port, running, wait_until
    from scrimmage.bridge import Bridge
    from scrimmage.protocol import frame

    def lc_frames(robot, start):
        return [m["data"] for m in robot.frames[start:] if (m["type"], m["device"]) == LC_KEY]

    async def main():
        port = next_port()
        cfg = range_cfg()
        cfg["sensor"] = [copy.deepcopy(LASERCAN)]
        bridge = Bridge(spares_world(cfg), "localhost", port, nt=False, speed=None, held=True)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True) as robot:
                await wait_until(lambda: bridge.driver is not None)
                for _ in range(2):
                    assert await bridge.driver.step_once() == "ok"
                assert any(">distance_mm" in d for d in lc_frames(robot, 0))   # sent before the device existed: lost
                await robot.ws.send(frame(*LC_KEY, {">status": 2}))
                await wait_until(lambda: LC_KEY in bridge.hal.devices)
                mark = len(robot.frames)
                assert await bridge.driver.step_once() == "ok"
                sent = {k: v for d in lc_frames(robot, mark) for k, v in d.items()}
                assert {">distance_mm", ">status"} <= set(sent), sent
                await robot.ws.send(frame(*LC_KEY, {">status": 2}))     # a value relayed back: not a first sight
                await asyncio.sleep(0.05)
                mark = len(robot.frames)
                assert await bridge.driver.step_once() == "ok"
                assert not lc_frames(robot, mark)
    asyncio.run(main())


def test_the_bridge_gives_the_missing_lasercan_warning_its_language_and_helper_state(caplog):
    from scrimmage.bridge import Bridge
    caplog.set_level(logging.WARNING, logger="scrimmage")
    cfg = range_cfg()
    cfg["sensor"] = [copy.deepcopy(LASERCAN)]
    bridge = Bridge(spares_world(cfg), "localhost", 0, nt=False, speed=None, held=True)
    bridge.robot_language = "python"
    asyncio.run(bridge._on_tick(2.0))
    (msg,) = [r.getMessage() for r in caplog.records if "LaserCAN 7" in r.getMessage()]
    assert "--scrimmage-lockstep" in msg, msg
    caplog.clear()
    bridge._devices_checked = False     # as on a robot reconnect
    bridge.grapple_state = lambda: "unavailable: boom"
    asyncio.run(bridge._on_tick(2.0))
    (msg,) = [r.getMessage() for r in caplog.records if "LaserCAN 7" in r.getMessage()]
    assert "(boom)" in msg, msg
