"""Cylinders in the world: valves read from a CTRE PCM through the HAL, the PCM's inputs sent back, and the
compressor's current on the battery."""

from __future__ import annotations

import math
import tomllib
from pathlib import Path

import pytest

from scrimmage.protocol import HalState
from scrimmage.world import World

ROOT = Path(__file__).resolve().parent.parent


def robot(valve=None, pneumatics=None, **extra) -> dict:
    cyl = {"name": "cyl", "stock": "cylinder", "attach": "drive.top"}
    if valve is not None:
        cyl["ids"] = {"valve": valve}
    cfg = {"part": [{"name": "drive", "stock": "chassis"}, cyl], **extra}
    if pneumatics is not None:
        cfg["pneumatics"] = pneumatics
    return cfg


def hal(*devices) -> HalState:
    h = HalState()
    h.devices[("DriverStation", "")] = {">enabled": True}
    for typ, dev, data in devices:
        h.devices[(typ, dev)] = data
    return h


def run(world: World, h: HalState, seconds: float) -> None:
    for _ in range(round(seconds / world.model.opt.timestep)):
        world.step(h)


def test_solenoid_extends():
    w = World(robot("PCM:3"), ROOT)
    run(w, hal(("Solenoid", "0,3", {"<output": True})), 1.0)
    assert w.pneumatics.extension("cyl") >= 0.15
    assert w.pneumatics.system.stored_psi < 120


def test_solenoid_off_stays_retracted():
    w = World(robot("PCM:3"), ROOT)
    run(w, hal(("Solenoid", "0,3", {"<output": False})), 0.5)
    assert w.pneumatics.extension("cyl") < 0.001


def test_inverted_solenoid_extends_when_off():
    w = World(robot({"pcm": 0, "channel": 3, "invert": True}), ROOT)
    run(w, hal(), 1.0)
    assert w.pneumatics.extension("cyl") >= 0.15


def test_disabled_robot_valves_off():
    w = World(robot("PCM:3"), ROOT)
    h = hal(("Solenoid", "0,3", {"<output": True}))
    h.devices[("DriverStation", "")] = {">enabled": False}
    run(w, h, 0.5)
    assert w.pneumatics.extension("cyl") < 0.001


def test_unbound_valve_warns():
    w = World(robot(), ROOT)
    run(w, hal(("Solenoid", "0,3", {"<output": True})), 1.0)
    assert w.pneumatics.extension("cyl") < 0.001
    assert "cyl.valve has no device ID, so it won't move" in w.warnings
    assert w.warnings.count("cyl.valve has no device ID, so it won't move") == 1


def test_command_overrides_until_reset():
    w = World(robot(), ROOT)
    w.pneumatics.command("cyl", "extend")
    run(w, hal(), 1.0)
    assert w.pneumatics.extension("cyl") >= 0.15
    w.reset()
    run(w, hal(), 0.2)
    assert w.pneumatics.extension("cyl") < 0.001
    with pytest.raises(ValueError, match="cyl"):
        w.pneumatics.command("nope", "extend")


def test_double_both_on():
    w = World(robot("PCM:3,4"), ROOT)
    run(w, hal(("Solenoid", "0,3", {"<output": True})), 0.5)
    out = w.pneumatics.extension("cyl")
    assert out >= 0.15
    run(w, hal(("Solenoid", "0,3", {"<output": True}), ("Solenoid", "0,4", {"<output": True})), 0.5)
    assert w.pneumatics.extension("cyl") == pytest.approx(out, abs=0.005)
    named = [m for m in w.pneumatics.warnings if "cyl.valve" in m]
    assert len(named) == 1 and named[0] in w.warnings
    run(w, hal(("Solenoid", "0,4", {"<output": True})), 0.5)
    assert w.pneumatics.extension("cyl") < 0.001


def test_double_neither_holds():
    w = World(robot("PCM:3,4"), ROOT)
    run(w, hal(("Solenoid", "0,3", {"<output": True})), 0.5)
    out = w.pneumatics.extension("cyl")
    run(w, hal(), 0.5)
    assert w.pneumatics.extension("cyl") == pytest.approx(out, abs=0.005)
    assert w.pneumatics.warnings == []


def test_pcm_inputs():
    w = World(robot("PCM:3"), ROOT)
    run(w, hal(), 0.01)
    pcm = [data for typ, dev, data in w.sensor_updates(hal()) if (typ, dev) == ("CTREPCM", "0")]
    assert len(pcm) == 1
    assert pcm[0][">pressure_switch"] is False     # 120 psi at the start: full, not calling for air
    assert pcm[0][">current"] == 0.0 and pcm[0][">on"] is False


def test_ph_valve_reads_its_bit_of_the_hub_mask():
    w = World(robot("PH:3"), ROOT)
    run(w, hal(("Solenoid", "1,3", {"<output": True}), ("ScrimmagePH", "1", {"<solenoids": 1 << 4})), 0.5)
    assert w.pneumatics.extension("cyl") < 0.001        # a PCM's channel 3 and the hub's channel 4 aren't this valve
    run(w, hal(("ScrimmagePH", "1", {"<solenoids": 1 << 3})), 1.0)
    assert w.pneumatics.extension("cyl") >= 0.15
    assert not any(typ == "CTREPCM" for typ, _, _ in w.sensor_updates(hal()))


def test_ph_double_solenoid_and_disabled():
    w = World(robot({"ph": 2, "forward": 14, "reverse": 15}), ROOT)
    h = hal(("ScrimmagePH", "2", {"<solenoids": 1 << 14}))
    h.devices[("DriverStation", "")] = {">enabled": False}
    run(w, h, 0.5)
    assert w.pneumatics.extension("cyl") < 0.001        # disabled: the hub's outputs count as off
    run(w, hal(("ScrimmagePH", "2", {"<solenoids": 1 << 14})), 1.0)
    assert w.pneumatics.extension("cyl") >= 0.15
    run(w, hal(("ScrimmagePH", "2", {"<solenoids": 1 << 15})), 1.0)
    assert w.pneumatics.extension("cyl") < 0.001


def test_ph_closed_loop_and_inputs():
    w = World(robot("PH:3", pneumatics={"start_psi": 90}), ROOT)
    run(w, hal(), 0.01)
    assert not w.pneumatics.system.compressor_on
    h = hal(("ScrimmagePH", "1", {"<closed_loop": True}))
    run(w, h, 0.01)
    assert w.pneumatics.system.compressor_on
    ph = [data for typ, dev, data in w.sensor_updates(h) if (typ, dev) == ("ScrimmagePH", "1")]
    assert len(ph) == 1
    assert ph[0][">pressure_switch"] is True and ph[0][">compressor_on"] is True
    assert 7 <= ph[0][">compressor_current"] <= 11


def test_ph_without_the_extension_warns():
    w = World(robot("PH:3"), ROOT)
    msg = ("cyl.valve is on a REV Pneumatic Hub, which needs the lockstep extension "
           "(`--scrimmage-lockstep` for RobotPy, the Gradle init script for Java)")
    assert w.pneumatics.missing_extension_warnings(hal()) == [msg]
    assert w.pneumatics.missing_extension_warnings(hal(("ScrimmagePH", "1", {"<solenoids": 0}))) == []
    assert World(robot("PCM:3"), ROOT).pneumatics.missing_extension_warnings(hal()) == []


def test_compressor_loads_battery():
    w = World(robot("PCM:3", pneumatics={"start_psi": 90}), ROOT)
    h = hal(("CTREPCM", "0", {"<closed_loop": True}))
    run(w, h, 0.01)
    assert w.pneumatics.system.compressor_on
    drop = w.battery.nominal_voltage - w.vbatt
    r = w.battery.resistance
    assert 7 * r <= drop <= 11 * r
    pcm = [data for typ, dev, data in w.sensor_updates(h) if (typ, dev) == ("CTREPCM", "0")][0]
    assert pcm[">on"] is True and pcm[">pressure_switch"] is True and 7 <= pcm[">current"] <= 11


def test_compressor_off_without_closed_loop():
    w = World(robot("PCM:3", pneumatics={"start_psi": 90}), ROOT)
    run(w, hal(), 0.01)
    assert not w.pneumatics.system.compressor_on and w.vbatt == w.battery.nominal_voltage
    w.pneumatics.always_closed_loop = True
    run(w, hal(), 0.01)
    assert w.pneumatics.system.compressor_on


def test_reset_restores_air():
    w = World(robot("PCM:3"), ROOT)
    run(w, hal(("Solenoid", "0,3", {"<output": True})), 0.5)
    assert w.pneumatics.system.stored_psi < 120
    w.reset()
    assert w.pneumatics.system.stored_psi == pytest.approx(120)


@pytest.mark.parametrize("output_after", [False, True])
def test_reset_robot_refills_chambers(output_after):
    """reset_robot() puts the rod back; the air is refilled for the new position, so there's no force spike."""
    from scrimmage.pneumatics.model import PSI
    w = World(robot("PCM:3"), ROOT)
    rt = w.pneumatics
    run(w, hal(("Solenoid", "0,3", {"<output": True})), 1.0)
    assert rt.extension("cyl") >= 0.15
    psi = rt.system.stored_psi
    w.reset_robot()
    assert rt.extension("cyl") < 0.001
    assert rt.system.stored_psi == pytest.approx(psi)        # the tanks keep their air
    spec = w.parts.cylinders[0]
    stall = 1.05 * rt.system.config.working_psi * PSI * math.pi * spec.bore ** 2 / 4
    act = w.model.actuator("cyl.cylinder").id
    h = hal(("Solenoid", "0,3", {"<output": output_after}))
    fresh = World(robot("PCM:3"), ROOT)       # the same 10 ms from a fresh start: how far the rod may go
    for _ in range(10):
        w.step(h)
        fresh.step(h)
        assert abs(float(w.data.ctrl[act])) <= stall
        assert rt.extension("cyl") <= fresh.pneumatics.extension("cyl") + 0.001


def test_no_cylinders_no_runtime():
    cfg = tomllib.loads((ROOT / "configs" / "kitbot.toml").read_text())
    assert World(cfg, ROOT / "configs").pneumatics is None


def test_pneumatics_section_checked():
    with pytest.raises(ValueError, match="60 psi"):
        World(robot("PCM:3", pneumatics={"working_psi": 80}), ROOT)
    with pytest.raises(ValueError, match="tank_ml") as e:
        World(robot("PCM:3", pneumatics={"tank_size": 1}), ROOT)
    assert "tank_size" in str(e.value)
