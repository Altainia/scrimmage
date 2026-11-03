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
    assert w.pneumatics.missing_hub_warnings(hal()) == [msg]
    assert w.pneumatics.missing_hub_warnings(hal(("ScrimmagePH", "1", {"<solenoids": 0}))) == []
    assert World(robot("PCM:3"), ROOT).pneumatics.missing_hub_warnings(hal()) == []


def test_ph_on_a_hub_the_robot_never_opened_warns():
    w = World(robot({"ph": 2, "channel": 3}), ROOT)
    h = hal(("ScrimmagePH", "1", {"<solenoids": 0}), ("ScrimmagePH", "3", {"<solenoids": 0}))
    msg = ("cyl.valve: the robot never opened Pneumatic Hub 2 (it opened Pneumatic Hubs 1, 3); "
           "check the hub's number in the valve's ids")
    assert w.pneumatics.missing_hub_warnings(h) == [msg]
    assert w.pneumatics.missing_hub_warnings(hal(("ScrimmagePH", "1", {}))) == [
        "cyl.valve: the robot never opened Pneumatic Hub 2 (it opened Pneumatic Hub 1); "
        "check the hub's number in the valve's ids"]


def test_missing_hub_warnings_reach_the_world_once():
    w = World(robot("PH:3"), ROOT)
    msgs = w.pneumatics.warn_missing_hubs(hal())
    assert len(msgs) == 1 and msgs[0] in w.warnings
    w.reset()                                            # a reconnect checks again
    assert w.pneumatics.warn_missing_hubs(hal()) == msgs
    assert w.warnings.count(msgs[0]) == 1


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



def _watch(w: World, seconds: float, samples: list) -> None:
    """Step a robot with no HAL valves for `seconds`, appending (raw extension m, fed side psi, vented side psi,
    tank psi) each step. The raw extension is the tendon's length past retracted, unclamped, so a rod through its
    stops shows; the fed side is the one the valve supplies."""
    rt = w.pneumatics
    spec = w.parts.cylinders[0]
    for _ in range(round(seconds / w.model.opt.timestep)):
        w.step(hal())
        cap, rod = rt.system.chamber_psi("cyl")
        fed, vented = (cap, rod) if rt.system._valves["cyl"] == "extend" else (rod, cap)
        samples.append((rt.tendon_length("cyl") - spec.length, fed, vented, rt.system.stored_psi))


def _assert_stable(w: World, samples: list, vented_max: float) -> None:
    """Within 1 mm of each stop, but 3 mm in the first 10 ms (the prime's first step puts 60 psi on a rod against
    its soft stop); the fed side never above the regulator; the vented side at most `vented_max` psi; the tanks
    never below 0 psi."""
    spec = w.parts.cylinders[0]
    working = w.pneumatics.system.config.working_psi
    for i, (x, fed, vented, tank) in enumerate(samples):
        past_stop = 0.003 if i < 10 else 0.001
        assert -past_stop <= x <= spec.stroke + past_stop, (i, x)
        assert fed <= working + 1, (i, fed)
        assert vented <= vented_max, (i, vented)
        assert tank >= 0.0, (i, tank)

def test_big_light_cylinder_is_stable():
    """A 2 in bore on the default 0.1 kg rod at a 1 ms step: the air's spring is stiff at a stroke's end (small
    dead volume), so with a one-step-old length, or no tendon armature, the rod went through its stops and the
    chambers read ~1700 psi. No outside load acts, so both sides stay at or below the working pressure."""
    cfg = {"part": [{"name": "drive", "stock": "chassis"},
                    {"name": "cyl", "stock": "cylinder", "attach": "drive.top", "bore": 0.0508}]}
    w = World(cfg, ROOT)
    assert w.model.opt.timestep == pytest.approx(0.001)
    samples = []
    w.pneumatics.command("cyl", "extend")
    _watch(w, 1.0, samples)
    w.pneumatics.command("cyl", "retract")
    _watch(w, 1.0, samples)
    assert max(s[0] for s in samples) > w.parts.cylinders[0].stroke - 0.005      # it did go out
    assert samples[-1][0] < 0.005                                                 # and back
    _assert_stable(w, samples, vented_max=w.pneumatics.system.config.working_psi + 1)


def test_light_push_load_is_stable():
    """A push-form 2 in bore on a 0.05 kg stock arm, laid out as a real design is: the 2 in stroke ends (about 39
    degrees) before the arm's own limits (+-90), so the cylinder's stops set the arm's travel."""
    stroke = 0.0508
    cfg = {"part": [{"name": "drive", "stock": "chassis"},
                    {"name": "arm", "stock": "arm", "attach": "drive.top", "mass": 0.05},
                    {"name": "cyl", "stock": "cylinder", "attach": "drive.top", "pos": [0, 0, -0.1],
                     "bore": 0.0508, "stroke": stroke, "push": "arm.arm", "push_pos": [0.2, 0, 0]}]}
    w = World(cfg, ROOT)
    # Pin to pin: barrel at (0, 0, -0.1), arm pin 0.2 m out at angle t: L^2 = 0.05 + 0.04 sin t; retracted at t = 0.
    top = math.degrees(math.asin(((math.sqrt(0.05) + stroke) ** 2 - 0.05) / 0.04))
    pivot = w.model.joint("arm.pivot")
    lo, hi = (math.degrees(v) for v in pivot.range)
    assert lo < -1.0 and top < hi - 1.0
    angles, samples = [], []
    for state in ("extend", "retract"):
        w.pneumatics.command("cyl", state)
        for _ in range(1000):
            _watch(w, 0.001, samples)
            angles.append(math.degrees(float(w.data.qpos[pivot.qposadr[0]])))
    assert max(angles) > top - 2.0                # it did push the arm up to the stroke's end
    assert -1.0 <= min(angles) and max(angles) <= top + 1.0, (min(angles), max(angles), top)
    assert angles[-1] < 1.0                       # and back down
    # The vented side cushions the arm as it comes down: real compression, bounded loosely to catch a blow-up.
    _assert_stable(w, samples, vented_max=3 * w.pneumatics.system.config.working_psi)


def test_predicted_length_matches_forward():
    """tendon_length carries mj_step's pre-integration length forward by this step's motion: on the free form's
    slide it is the length mj_fwdPosition computes at the new position."""
    import mujoco
    cfg = {"part": [{"name": "drive", "stock": "chassis"},
                    {"name": "cyl", "stock": "cylinder", "attach": "drive.top", "bore": 0.0508}]}
    w = World(cfg, ROOT)
    w.pneumatics.command("cyl", "extend")
    run(w, hal(), 0.05)
    assert abs(float(w.data.qvel[w.model.joint("cyl.extend").dofadr[0]])) > 0.1     # it is moving
    predicted = w.pneumatics.tendon_length("cyl")
    mujoco.mj_fwdPosition(w.model, w.data)
    assert predicted == pytest.approx(float(w.data.ten_length[w.model.tendon("cyl.cylinder").id]), abs=1e-9)


def test_armature_formula():
    """The cylinder's tendon carries armature k_max dt^2 / 2, k_max the cap side's air spring at the working limit
    with only the dead volume left: so k dt^2 / m_eff stays below 2 whatever the cylinder drives."""
    from scrimmage.pneumatics.model import ATM, MAX_WORKING_PSI, PSI
    w = World(robot(), ROOT)
    spec = w.parts.cylinders[0]
    a_cap = math.pi * spec.bore ** 2 / 4
    k_max = (MAX_WORKING_PSI * PSI + ATM) * a_cap ** 2 / spec.dead_volume
    dt = w.model.opt.timestep
    tid = w.model.tendon("cyl.cylinder").id
    assert float(w.model.tendon_armature[tid]) == pytest.approx(k_max * dt ** 2 / 2, rel=1e-9)
    slow = World(robot(model={"timestep": 0.002}), ROOT)      # the run's own step sets it
    assert slow.model.opt.timestep == pytest.approx(0.002)
    assert float(slow.model.tendon_armature[tid]) == pytest.approx(k_max * 0.002 ** 2 / 2, rel=1e-9)


def test_state_reports_the_air():
    w = World(robot(), ROOT)
    w.pneumatics.command("cyl", "extend")
    run(w, hal(), 1.0)
    st = w.pneumatics.state()
    assert set(st) == {"stored_psi", "working_psi", "compressor_on", "cylinders"}
    assert st["stored_psi"] == pytest.approx(w.pneumatics.system.stored_psi, abs=0.01) and st["stored_psi"] < 120
    assert st["working_psi"] == pytest.approx(60, abs=0.01)
    assert st["compressor_on"] is False                 # no robot code turned the closed loop on
    assert st["cylinders"] == {"cyl": {"extension_m": pytest.approx(6 * 0.0254, abs=0.001)}}   # m: the stock 6 in stroke, all out


def test_get_state_has_the_air_only_with_cylinders(tmp_path):
    """The MCP tool get_state (Session.state) gains a pneumatics block on a robot with cylinders."""
    import asyncio

    from parts_fake import parts_config
    from scrimmage.session import Session

    plain = parts_config(tmp_path)
    with_cyl = tmp_path / "with_cylinder.toml"
    with_cyl.write_text(plain.read_text() + '\n[[part]]\nname = "cyl"\nstock = "cylinder"\nattach = "drive.top"\n')

    async def state(path):
        s = Session(path, nt=False)
        try:
            await s.start()
            await s.advance(0.1)
            return s.state()
        finally:
            await s.stop()

    assert "pneumatics" not in asyncio.run(state(plain))
    air = asyncio.run(state(with_cyl))["pneumatics"]
    assert air == {"stored_psi": pytest.approx(120, abs=0.01), "working_psi": pytest.approx(60, abs=0.01),
                   "compressor_on": False, "cylinders": {"cyl": {"extension_m": pytest.approx(0, abs=0.001)}}}
