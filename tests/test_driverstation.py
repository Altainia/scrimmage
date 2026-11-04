import json

import pytest

from scrimmage.driverstation import DriverStationState


def test_frames_carry_mode_and_joysticks():
    ds = DriverStationState(active=True)
    ds.set_mode("auto")
    ds.set_joystick(0, axes=[0.0, -0.5], buttons=[True, False])
    frames = [json.loads(f) for f in ds.frames(0.0)]
    assert frames[0] == {"type": "Joystick", "device": "0",
                         "data": {">axes": [0.0, -0.5], ">buttons": [True, False], ">povs": [-1]}}
    dsf = frames[-1]["data"]
    assert dsf[">enabled"] is True and dsf[">autonomous"] is True and dsf[">test"] is False


def test_disabled_and_clear():
    ds = DriverStationState(active=True)
    ds.set_mode("teleop")
    ds.set_joystick(1, axes=[1.0])
    ds.clear()
    frames = [json.loads(f) for f in ds.frames(0.0)]
    assert frames[-1]["data"][">enabled"] is False
    assert frames[0]["device"] == "1" and frames[0]["data"][">axes"] == [0.0]  # neutral, not left as it was


def test_clear_joysticks_sends_neutral_inputs_for_used_ports():
    ds = DriverStationState(active=True)
    ds.set_joystick(0, axes=[0.5, -1.0], buttons=[True, False, True], povs=[90])
    ds.changed = False
    ds.clear_joysticks()
    assert ds.changed
    frames = [json.loads(f) for f in ds.frames(0.0)]
    assert frames[0] == {"type": "Joystick", "device": "0",
                         "data": {">axes": [0.0, 0.0], ">buttons": [False, False, False], ">povs": [-1]}}


def test_bad_mode_and_port_list_the_valid_values():
    ds = DriverStationState()
    with pytest.raises(ValueError, match="disabled, auto, teleop, test"):
        ds.set_mode("autonomous")
    with pytest.raises(ValueError, match="0-5"):
        ds.set_joystick(6, axes=[0.0])


def test_setters_mark_changed():
    ds = DriverStationState()
    ds.changed = False
    ds.set_mode("auto")
    assert ds.changed
