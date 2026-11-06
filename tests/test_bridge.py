"""Bridge against a fake robot program over a real WebSocket: real-time and lockstep stepping."""

import asyncio
import csv
import logging
import re
import time

import pytest

from fake_robot import PHOENIX_CONFIG, ROTOR, FakeRobot, make_bridge, next_port, running, wait_until
import scrimmage.bridge as bridge_mod
import scrimmage.lockstep as lockstep_mod
from scrimmage.protocol import frame


def test_lockstep_steps_in_order_after_sensors():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True) as robot:
                await wait_until(lambda: len(robot.step_seqs()) >= 50)
                seqs = robot.step_seqs()
                assert seqs == list(range(1, len(seqs) + 1))
                # every step is 5 ms of physics (the bridge may be one step ahead of what we've received)
                assert bridge.world.time == pytest.approx(len(seqs) * 0.005, abs=0.0101)
        kinds = [(m["type"], m["device"]) for m in robot.frames]
        engage = next(i for i, m in enumerate(robot.frames) if m["data"].get(">engage") is True)
        assert robot.frames[engage]["data"][">step_us"] == 5000
        first_step = kinds.index(lockstep_mod.LOCKSTEP_DEVICE, engage + 1)
        assert ("RoboRIO", "") in kinds[engage + 1:first_step]   # sensors resent after engage, before step 1
    asyncio.run(main())


def test_lockstep_waits_for_each_ack():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True, auto_ack=False) as robot:
                await wait_until(lambda: len(robot.step_seqs()) == 1)
                t = bridge.world.time
                await asyncio.sleep(0.3)
                assert robot.step_seqs() == [1]
                assert bridge.world.time == t               # no physics while the robot is mid-step
                await robot.ack_held()
                await wait_until(lambda: len(robot.step_seqs()) == 2)
    asyncio.run(main())


def test_lockstep_step_waits_for_new_device_values_that_arrive_after_done_seq():
    """halsim_ws_client relays a device created mid-step from its own thread, so it can arrive after done_seq. The
    extension's sim_values (how many SimDevice values exist) makes the bridge wait for it before the next step."""
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True, auto_ack=False) as robot:
                await wait_until(lambda: len(robot.step_seqs()) == 1)
                await robot._send({"<sim_values": 0})
                await wait_until(lambda: ("SimDevice", "ScrimmageLockstep", "<sim_values") in bridge.sim_values_seen)
                need = len(bridge.sim_values_seen) + 1           # one value the robot created that hasn't arrived
                robot.held.clear()
                await robot._send({"<sim_values": need, "<done_seq": 1})
                await asyncio.sleep(0.3)
                assert robot.step_seqs() == [1]                  # step 1's outputs aren't complete yet
                await robot.ws.send(frame("SimDevice", "SPARK MAX [3]", {"<Applied Output": 0.5}))
                await wait_until(lambda: len(robot.step_seqs()) == 2)
                assert bridge.hal.get("SimDevice", "SPARK MAX [3]", "<Applied Output") == 0.5
    asyncio.run(main())


def test_lockstep_carries_on_once_when_counted_values_never_arrive(monkeypatch, caplog):
    monkeypatch.setattr(lockstep_mod, "VALUES_WAIT", 0.2)
    caplog.set_level(logging.WARNING, logger="scrimmage")

    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True, auto_ack=False) as robot:
                await wait_until(lambda: len(robot.step_seqs()) == 1)
                await robot._send({"<sim_values": 0})
                await wait_until(lambda: ("SimDevice", "ScrimmageLockstep", "<sim_values") in bridge.sim_values_seen)
                need = len(bridge.sim_values_seen) + 3           # e.g. devices a HALSIMWS filter keeps back
                robot.held.clear()
                await robot._send({"<sim_values": need, "<done_seq": 1})
                await wait_until(lambda: len(robot.step_seqs()) == 2)
                for seq in (2, 3):                               # no more waiting: later steps go straight on
                    t0 = time.monotonic()
                    robot.held.clear()
                    await robot._send({"<sim_values": need, "<done_seq": seq})
                    await wait_until(lambda: len(robot.step_seqs()) == seq + 1)
                    assert time.monotonic() - t0 < 0.15
        assert caplog.text.count("never reached scrimmage") == 1
        assert "3 simulated device value(s)" in caplog.text
    asyncio.run(main())


def test_messages_from_sim_devices_are_told_from_built_in_providers():
    assert bridge_mod.from_sim_device(("SimDevice", "SPARK MAX [3]"))
    assert bridge_mod.from_sim_device(("CANMotor", "Talon FX (v6)[1]"))
    assert bridge_mod.from_sim_device(("Accel", "ADXL345_I2C[1]"))
    for key in [("PWM", "0"), ("DriverStation", ""), ("Joystick", "0"), ("Accel", "BuiltInAccel"), ("RoboRIO", "")]:
        assert not bridge_mod.from_sim_device(key), key


def test_lockstep_warns_while_robot_stalls(monkeypatch, caplog):
    monkeypatch.setattr(lockstep_mod, "STALL_FIRST_WARNING", 0.2)
    caplog.set_level(logging.WARNING, logger="scrimmage")

    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True, auto_ack=False) as robot:
                await wait_until(lambda: len(robot.step_seqs()) == 1)
                await asyncio.sleep(0.4)
        assert "waiting on robot program (step 1)" in caplog.text
    asyncio.run(main())


def test_lockstep_continues_from_robot_done_seq():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None)
        async with running(bridge):
            # a robot program that already did 10 steps with an earlier scrimmage
            async with FakeRobot(port, lockstep=True, done_seq=10) as robot:
                await wait_until(lambda: len(robot.step_seqs()) >= 3)
                assert robot.step_seqs()[:3] == [11, 12, 13]
    asyncio.run(main())


def test_robot_disconnect_mid_step_returns_to_idle():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None)
        async with running(bridge):
            first = FakeRobot(port, lockstep=True, auto_ack=False)
            await first.__aenter__()
            await wait_until(lambda: len(first.step_seqs()) == 1)
            await first.close()                              # robot program crashed mid-step
            await wait_until(lambda: bridge.ws is None)
            async with FakeRobot(port, lockstep=True) as second:
                await wait_until(lambda: len(second.step_seqs()) >= 5)
                assert second.step_seqs()[0] == 1
    asyncio.run(main())


def test_realtime_fallback_when_extension_missing(monkeypatch, caplog):
    monkeypatch.setattr(bridge_mod, "DETECT_WINDOW", 0.2)
    caplog.set_level(logging.INFO, logger="scrimmage")

    async def main():
        port = next_port()
        bridge = make_bridge(port)
        async with running(bridge):
            async with FakeRobot(port):
                await asyncio.sleep(0.5)
                assert not bridge.done.is_set()
                assert bridge.world.time == pytest.approx(0.5, abs=0.15)   # real time
        assert "lockstep extension not loaded; running in real time" in caplog.text
    asyncio.run(main())


def test_speed_other_than_real_time_needs_lockstep(monkeypatch, caplog):
    monkeypatch.setattr(bridge_mod, "DETECT_WINDOW", 0.2)

    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None)
        async with running(bridge):
            async with FakeRobot(port):
                await asyncio.wait_for(bridge.done.wait(), 2)
        assert bridge.exit_code == 2
        assert "needs the scrimmage_lockstep extension" in caplog.text
    asyncio.run(main())


def test_real_ds_runs_real_time_whatever_the_speed_without_the_extension(monkeypatch, caplog):
    monkeypatch.setattr(bridge_mod, "DETECT_WINDOW", 0.2)
    caplog.set_level(logging.INFO, logger="scrimmage")

    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None, real_ds=True)
        async with running(bridge):
            async with FakeRobot(port):
                await asyncio.sleep(0.5)
                assert not bridge.done.is_set() and bridge.exit_code != 2
        assert "needs the scrimmage_lockstep extension" not in caplog.text
        assert bridge_mod.REAL_DS_REASON in caplog.text
    asyncio.run(main())


def test_lockstep_max_speed_beats_real_time():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None)
        bridge.ds.active = True
        bridge.ds.set_mode("auto")
        async with running(bridge):
            async with FakeRobot(port, lockstep=True) as robot:
                t0 = time.monotonic()
                await wait_until(lambda: bridge.world.time >= 1.5, 10)
                bridge.done.set()
                wall = time.monotonic() - t0
        assert bridge.world.time >= 1.5  # stopped from outside, so it may run a few steps past
        assert wall < 1.5
        assert True in robot.ds_enabled()
    asyncio.run(main())


def test_lockstep_logs_rate_summary(caplog):
    caplog.set_level(logging.INFO, logger="scrimmage")

    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True):
                await wait_until(lambda: bridge.world.time >= 0.2, 10)
                bridge.done.set()
    asyncio.run(main())
    assert re.search(r"lockstep: simulated [\d.]+ s in [\d.]+ s \([\d.]+x real time\)", caplog.text), caplog.text


def test_lockstep_speed_one_keeps_real_time():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=1.0)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True) as robot:
                await wait_until(lambda: len(robot.step_seqs()) >= 1)
                await asyncio.sleep(0.5)
                assert bridge.world.time == pytest.approx(0.5, abs=0.1)
    asyncio.run(main())


def test_bad_step_is_rejected():
    with pytest.raises(ValueError, match="whole multiple"):
        make_bridge(next_port(), step=0.0025)


def test_slow_speed_that_would_starve_the_watchdog_is_rejected():
    with pytest.raises(ValueError, match="watchdog"):
        make_bridge(next_port(), speed=0.005, step=0.005)       # 1 s of wall time per step
    make_bridge(next_port(), speed=0.01, step=0.005)             # 0.5 s per step is fine


def test_lockstep_drops_rows_logged_before_hand_off(tmp_path):
    async def main():
        port = next_port()
        log = tmp_path / "run.csv"
        bridge = make_bridge(port, speed=None, log_path=log)
        async with running(bridge):
            # the device shows up late, so the bridge runs (and logs) in real time first
            async with FakeRobot(port, lockstep=True, announce_delay=0.1) as robot:
                await wait_until(lambda: len(robot.step_seqs()) >= 20)
        rows = list(csv.DictReader(open(log)))
        assert rows[0]["t"] == "0.005", rows[:3]
        ts = [float(r["t"]) for r in rows]
        assert ts == sorted(set(ts))
    asyncio.run(main())


def test_lockstep_reanchors_pacing_after_a_stall():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=1.0)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True, auto_ack=False) as robot:
                await wait_until(lambda: len(robot.step_seqs()) == 1)
                await asyncio.sleep(1.0)                    # robot program stalled (breakpoint)
                robot.auto_ack = True
                await robot.ack_held()
                t0, w0 = time.monotonic(), bridge.world.time
                await asyncio.sleep(0.5)
                # sim time tracks wall time again rather than racing to catch up the stall
                assert bridge.world.time - w0 == pytest.approx(time.monotonic() - t0, abs=0.1)
    asyncio.run(main())


def test_exit_releases_robot_clock():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True) as robot:
                await wait_until(lambda: bridge.world.time >= 0.2, 10)
                bridge.done.set()
                await wait_until(lambda: robot.ws.close_code is not None)
        assert robot.engage_values()[-1] is False
    asyncio.run(main())


def test_bridge_sends_ds_state_and_mode_changes_immediately():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None)
        bridge.ds.active = True
        async with running(bridge):
            async with FakeRobot(port, lockstep=True) as robot:
                await wait_until(lambda: len(robot.step_seqs()) >= 20)
                assert robot.ds_enabled() and not any(robot.ds_enabled())
                n = len(robot.step_seqs())
                bridge.ds.set_mode("teleop")
                await wait_until(lambda: any(robot.ds_enabled()))
                # sent on the bridge's next tick, not up to DS_PERIOD (4 steps) later; a few steps may be in flight
                first_on = next(i for i, m in enumerate(robot.frames)
                                if m["type"] == "DriverStation" and m["data"][">enabled"])
                steps_before = sum(1 for m in robot.frames[:first_on]
                                   if (m["type"], m["device"]) == lockstep_mod.LOCKSTEP_DEVICE
                                   and ">step_seq" in m["data"])
                assert steps_before <= n + 3
    asyncio.run(main())


def test_held_bridge_steps_only_when_asked():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None, held=True)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True) as robot:
                await wait_until(lambda: bridge.mode == "lockstep" and bridge.driver is not None)
                await asyncio.sleep(1.2)
                assert robot.step_seqs() == [] and bridge.world.time == 0.0
                assert len(robot.keepalives()) >= 2          # feeding the watchdog while held
                for _ in range(10):
                    assert await bridge.driver.step_once() == "ok"
                assert robot.step_seqs() == list(range(1, 11))
                assert bridge.world.time == pytest.approx(0.05)
    asyncio.run(main())


def test_step_once_times_out_when_robot_stops_acking():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None, held=True)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True, auto_ack=False):
                await wait_until(lambda: bridge.driver is not None)
                t0 = time.monotonic()
                assert await bridge.driver.step_once(timeout=0.3) == "timeout"
                assert 0.3 <= time.monotonic() - t0 < 1.0
    asyncio.run(main())


def test_engage_is_resent_until_acknowledged(monkeypatch):
    monkeypatch.setattr(lockstep_mod, "ENGAGE_RESEND", 0.2)

    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None, held=True)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True, ignore_engages=2) as robot:
                await wait_until(lambda: bridge.mode == "lockstep")
                assert robot.engage_values().count(True) >= 3
    asyncio.run(main())


def test_port_zero_binds_a_free_port():
    async def main():
        bridge = make_bridge(0, speed=None, held=True)
        async with running(bridge):
            await asyncio.wait_for(bridge.listening.wait(), 2)
            assert bridge.bound_port and bridge.bound_port > 0
            async with FakeRobot(bridge.bound_port, lockstep=True):
                await wait_until(lambda: bridge.mode == "lockstep")
    asyncio.run(main())


def test_held_realtime_fallback_is_not_an_error(monkeypatch):
    monkeypatch.setattr(bridge_mod, "DETECT_WINDOW", 0.2)

    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None, held=True)
        async with running(bridge):
            async with FakeRobot(port):
                await wait_until(lambda: bridge.mode == "realtime")
                assert not bridge.done.is_set() and bridge.exit_code == 0
    asyncio.run(main())


def test_step_once_does_not_run_ahead_of_an_unacked_step():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None, held=True)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True, auto_ack=False) as robot:
                await wait_until(lambda: bridge.driver is not None)
                assert await bridge.driver.step_once(timeout=0.3) == "timeout"
                t = bridge.world.time
                assert await bridge.driver.step_once(timeout=0.3) == "timeout"
                assert bridge.world.time == t and robot.held == [1]
                await robot.ack_held()
                robot.auto_ack = True
                assert await bridge.driver.step_once(timeout=2) == "ok"
                assert bridge.world.time == pytest.approx(t + 0.005)
    asyncio.run(main())


def test_step_once_after_disconnect():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None, held=True)
        async with running(bridge):
            robot = FakeRobot(port, lockstep=True)
            await robot.__aenter__()
            await wait_until(lambda: bridge.driver is not None)
            driver = bridge.driver
            await robot.close()
            await wait_until(lambda: bridge.driver is None, timeout=0.2)
            t = bridge.world.time
            assert await driver.step_once() == "disconnected"
            assert bridge.world.time == t
    asyncio.run(main())


def test_failed_log_setup_still_closes_nt_link(tmp_path, monkeypatch):
    pytest.importorskip("ntcore")
    import tomllib
    from fake_robot import ROOT
    from scrimmage.nt_reader import NetworkTablesLink
    from scrimmage.bridge import Bridge
    from scrimmage.world import World

    closed = []
    real_close = NetworkTablesLink.close

    def close(self):
        closed.append(self)
        real_close(self)
    monkeypatch.setattr(NetworkTablesLink, "close", close)

    async def main():
        cfg = tomllib.loads((ROOT / "configs" / "kitbot.toml").read_text())
        bridge = Bridge(World(cfg, ROOT / "configs"), "localhost", next_port(), nt=True, nt_port=next_port(),
                        log_path=tmp_path / "missing-dir" / "run.csv")

        async def stop_soon():
            await asyncio.sleep(0.3)
            bridge.done.set()
        stopper = asyncio.create_task(stop_soon())
        with pytest.raises(OSError):
            await asyncio.wait_for(bridge.run(), 5)
        await stopper
        assert bridge.nt_link is not None and closed == [bridge.nt_link]
    asyncio.run(main())


def test_held_bridge_records_a_watchdog_release():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None, held=True)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True, release_after=1):
                await wait_until(lambda: bridge.mode == "lockstep" and bridge.driver is not None)
                await wait_until(lambda: bridge.lockstep_released_at is not None)
                assert bridge.lockstep_released_at == 0.0  # the first keep-alive goes out before any step
                assert await bridge.driver.step_once() == "ok"
                assert bridge.lockstep_released_at == 0.0  # kept: the run is no longer repeatable
                bridge._restart_clock()
                assert bridge.lockstep_released_at is None
    asyncio.run(main())


def announced(bridge):
    """Each bound Phoenix device has announced itself, as the robot program does when it creates one (its first frame
    is its initial values, never judged as a write), and the run is past the devices' boot."""
    for dev in bridge._watched:
        bridge._check_inputs(dev, {})
    bridge.world.data.time = bridge_mod.PHOENIX_BOOT
    return bridge


def _last_sent(robot: FakeRobot, dev: tuple[str, str], key: str):
    return [m["data"][key] for m in robot.frames if (m["type"], m["device"]) == dev and key in m["data"]][-1]


REASON = "Phoenix 6 simulation runs on the wall clock, so this robot runs in real time (not lockstep)"


def test_phoenix_releases_lockstep_and_runs_in_real_time(caplog):
    """A Phoenix 6 device appearing while held: scrimmage frees the robot's clock (>engage false) and keeps physics
    on the wall clock. The extension's <engaged false that follows isn't a watchdog release."""
    caplog.set_level(logging.INFO, logger="scrimmage")

    async def main():
        port = next_port()
        bridge = make_bridge(port, PHOENIX_CONFIG, speed=None, held=True)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True) as robot:
                await wait_until(lambda: bridge.driver is not None)
                assert not bridge.phoenix and bridge.realtime_reason is None
                assert await bridge.driver.step_once() == "ok"
                await robot.ws.send(frame("CANMotor", "Talon FX (v6)[1]", {"<motorVoltage": 0.0}))
                await wait_until(lambda: bridge.mode == "realtime")
                assert bridge.phoenix and bridge.realtime_reason == REASON and bridge.effective_speed == 1.0
                assert robot.engage_values() == [True, False]
                await wait_until(lambda: robot._free is not None)
                await asyncio.sleep(0.1)
                assert bridge.driver is None and bridge.lockstep_released_at is None
                steps = robot.step_seqs()
                t0, w0 = bridge.world.time, time.monotonic()
                await asyncio.sleep(0.5)
                rate = (bridge.world.time - t0) / (time.monotonic() - w0)
                assert robot.step_seqs() == steps == [1]       # no more lockstep steps
                assert bridge.mode == "realtime"                # and it doesn't engage again
        assert 0.7 < rate < 1.3, rate
    asyncio.run(main())
    assert "ran free" not in caplog.text
    assert [r.getMessage() for r in caplog.records if r.getMessage().startswith("Phoenix 6")] == [REASON]


def test_phoenix_appearing_during_a_held_step_starts_real_time_physics_at_once():
    """A Phoenix 6 device appearing while a held step is in flight: that step frees the clock when it ends, and the
    hold, partway through a keep-alive period, hands over to real-time physics at once rather than at its end."""
    async def main():
        port = next_port()
        bridge = make_bridge(port, PHOENIX_CONFIG, speed=None, held=True)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True) as robot:
                await wait_until(lambda: bridge.driver is not None)
                driver = bridge.driver
                driver._stepping = True                  # a step in flight (Session.advance)
                await robot.ws.send(frame("CANMotor", "Talon FX (v6)[1]", {"<motorVoltage": 0.0}))
                await wait_until(lambda: bridge.phoenix)
                await asyncio.sleep(0.05)                # the hold woke, saw the step, and waits again
                driver._stepping = False
                t0, w0 = bridge.world.time, time.monotonic()
                assert await driver._release()   # what the step does as it ends
                await wait_until(lambda: bridge.world.time > t0)
                return time.monotonic() - w0
    waited = asyncio.run(main())
    assert waited < 0.2, waited   # not up to KEEPALIVE_PERIOD (0.5 s)


def test_foreign_writes_to_phoenix_devices_are_recorded_in_real_time(caplog):
    caplog.set_level(logging.WARNING, logger="scrimmage")

    async def main():
        port = next_port()
        bridge = make_bridge(port, PHOENIX_CONFIG, speed=None, held=True)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True, phoenix=True) as robot:
                await wait_until(lambda: bridge.mode == "realtime")
                await wait_until(lambda: any(m["device"] == ROTOR[1] for m in robot.frames))
                # halsim_ws_client relays our own writes back: an echo isn't foreign
                echo = _last_sent(robot, ROTOR, ">rawPositionInput")
                await robot.ws.send(frame(*ROTOR, {">rawPositionInput": echo}))
                await wait_until(lambda: bridge.hal.get(*ROTOR, ">rawPositionInput") == echo)
                await asyncio.sleep(0.05)
                assert bridge.foreign_writes == {}
                await robot.ws.send(frame(*ROTOR, {">rawPositionInput": 5.0}))
                await wait_until(lambda: bridge.foreign_writes != {})
                fw = bridge.foreign_writes["CANEncoder/Talon FX (v6)[1]/Rotor Sensor >rawPositionInput"]
                assert fw["device"] == "Talon FX (v6)[1]/Rotor Sensor" and fw["key"] == ">rawPositionInput"
                assert fw["value"] == 5.0 and fw["count"] == 1 and fw["first_t"] > 0
    asyncio.run(main())
    warns = [r.getMessage() for r in caplog.records if "writes simulated" in r.getMessage()]
    assert len(warns) == 1
    assert re.match(r"something in the robot program writes simulated sensor values for Talon FX \(v6\)\[1\] "
                    r"\(rotor position, first at t=\d+\.\d{3}\), so physics and robot code disagree; check that "
                    r"the scrimmage Java agent is built \(`scrimmage java build`\) and loaded", warns[0]), warns[0]
    assert "remove or guard the robot-side sim model" in warns[0]


def test_fast_mode_with_phoenix_devices_says_they_force_real_time(caplog):
    caplog.set_level(logging.WARNING, logger="scrimmage")
    bridge = make_bridge(0, PHOENIX_CONFIG, speed=None, held=True, sim_mode="fast")
    assert bridge.fast_phoenix_message is None
    for dev in (("CANMotor", "Talon FX (v6)[1]"), ("CANEncoder", "Talon FX (v6)[1]/Rotor Sensor"),
                ("CANEncoder", "CANcoder (v6)[2]")):
        bridge.hal.devices[dev] = {}
    bridge._phoenix_seen()
    msg = ("fast mode, but the robot program created Phoenix 6 devices (CANcoder (v6)[2], Talon FX (v6)[1]): they "
           "force real time; use the fast-mode IO for them")
    assert bridge.fast_phoenix_message == msg

    async def main():
        await bridge._on_tick(1.9)
        assert msg not in caplog.text            # devices may still be on their way
        await bridge._on_tick(2.0)
        await bridge._on_tick(2.5)
    asyncio.run(main())
    assert [r.getMessage() for r in caplog.records if r.getMessage().startswith("fast mode, but")] == [msg]
    assert make_bridge(0, PHOENIX_CONFIG, speed=None, held=True, sim_mode="phoenix").fast_phoenix_message is None


def test_a_phoenix_device_booting_after_the_first_push_is_not_a_foreign_write():
    """In real time, sensor values go out as soon as the clock is released, and a Phoenix device that boots a little
    later (on a slow computer) re-asserts its initial values. Its own initial value, early on, isn't a write."""
    bridge = make_bridge(0, PHOENIX_CONFIG, speed=None, held=True)
    cc = ("CANEncoder", "CANcoder (v6)[2]")
    bridge._check_inputs(cc, {">supplyVoltage": 12.0, ">rawPositionInput": 0.0})   # announced on creation
    bridge.cache.diff(*cc, {">supplyVoltage": 5.0, ">rawPositionInput": 0.3})
    bridge.world.data.time = 0.2
    bridge._check_inputs(cc, {">supplyVoltage": 12.0})                  # the device booting: not a write
    bridge.settle_foreign_writes()
    assert bridge.foreign_writes == {}
    bridge._check_inputs(cc, {">rawPositionInput": 4.0})                 # any other value still is
    bridge.world.data.time = bridge_mod.PHOENIX_BOOT + 0.5
    bridge._check_inputs(cc, {">supplyVoltage": 12.0})                  # and so is the initial value, later
    bridge.settle_foreign_writes()
    assert sorted(bridge.foreign_writes) == ["CANEncoder/CANcoder (v6)[2] >rawPositionInput",
                                             "CANEncoder/CANcoder (v6)[2] >supplyVoltage"]


def test_a_phoenix_device_s_late_boot_on_a_slow_computer_is_not_a_foreign_write():
    """Phoenix boots on the wall clock. On a slow CI runner four CANcoders re-asserted their initial values (twice
    each) at t = 1.057, past the 1 s once allowed. The boot window counts from when each device appeared, so a device
    the robot program creates late gets the whole window too."""
    bridge = make_bridge(0, PHOENIX_CONFIG, speed=None, held=True)
    cc = ("CANEncoder", "CANcoder (v6)[2]")
    talon = ("CANMotor", "Talon FX (v6)[1]")
    bridge._check_inputs(cc, {">supplyVoltage": 12.0, ">rawPositionInput": 0.0})   # created at t = 0
    bridge.cache.diff(*cc, {">supplyVoltage": 5.0, ">rawPositionInput": 0.4873})
    for t in (1.057, 1.077):                                                         # as measured on CI
        bridge.world.data.time = t
        bridge._check_inputs(cc, {">supplyVoltage": 12.0, ">rawPositionInput": 0.0})
    bridge.settle_foreign_writes()
    assert bridge.foreign_writes == {}

    bridge.world.data.time = 3.0
    bridge._check_inputs(talon, {">supplyVoltage": 12.0})                # created late, at t = 3
    bridge.cache.diff(*talon, {">supplyVoltage": 12.5})
    bridge.world.data.time = 3.0 + 1.06
    bridge._check_inputs(talon, {">supplyVoltage": 12.0})                # its boot, 1.06 s after it appeared
    bridge.settle_foreign_writes()
    assert bridge.foreign_writes == {}
    bridge.world.data.time = 3.0 + bridge_mod.PHOENIX_BOOT
    bridge._check_inputs(talon, {">supplyVoltage": 12.0})                # past its window: a write
    bridge._check_inputs(cc, {">supplyVoltage": 12.0})                   # and long past the CANcoder's
    bridge.settle_foreign_writes()
    assert sorted(bridge.foreign_writes) == ["CANEncoder/CANcoder (v6)[2] >supplyVoltage",
                                             "CANMotor/Talon FX (v6)[1] >supplyVoltage"]


def test_a_phoenix_device_announced_after_our_first_send_gets_our_values_again():
    """scrimmage can send a device's values before the robot program creates it; halsim_ws_client drops those. The
    device then announces its own values (and repeats them until ours arrive): not writes, and ours go out again."""
    bridge = make_bridge(0, PHOENIX_CONFIG, speed=None, held=True)
    cc = ("CANEncoder", "CANcoder (v6)[2]")
    bridge.cache.diff(*cc, {">supplyVoltage": 5.0, ">velocity": 0.0275})   # sent before the device exists
    bridge.world.data.time = 0.001
    bridge._check_inputs(cc, {">supplyVoltage": 12.0, ">velocity": 0.0})    # the device announced on creation
    assert bridge.cache.sent_history(*cc, ">supplyVoltage") == []           # so ours are resent
    assert bridge.cache.diff(*cc, {">supplyVoltage": 5.0}) == {">supplyVoltage": 5.0}
    bridge.world.data.time = 0.02
    bridge._check_inputs(cc, {">supplyVoltage": 12.0, ">velocity": 0.0})    # still its own, before ours land
    bridge.settle_foreign_writes()
    assert bridge.foreign_writes == {}
    bridge._check_inputs(cc, {">supplyVoltage": 7.0})                       # a value of nobody's is a write
    bridge.settle_foreign_writes()
    assert list(bridge.foreign_writes) == ["CANEncoder/CANcoder (v6)[2] >supplyVoltage"]


def test_a_booting_phoenix_device_s_own_values_are_compared_with_tolerance():
    """A Talon announces 12 V, then reports its own value with a modeled supply drop (11.9955 V) until ours (12.5 V)
    lands: still its own, not a write."""
    bridge = make_bridge(0, PHOENIX_CONFIG, speed=None, held=True)
    talon = ("CANMotor", "Talon FX (v6)[1]")
    bridge._check_inputs(talon, {">supplyVoltage": 12.0})                  # created (measured, Phoenix 6 26.1)
    bridge.cache.diff(*talon, {">supplyVoltage": 12.5})                    # ours, resent
    bridge.world.data.time = 0.004
    bridge._check_inputs(talon, {">supplyVoltage": 11.995546875})          # its own, before ours lands
    bridge._check_inputs(talon, {">supplyVoltage": 12.496875})              # then ours
    bridge.settle_foreign_writes()
    assert bridge.foreign_writes == {}
    bridge._check_inputs(talon, {">supplyVoltage": 3.0})                   # another value is still a write
    bridge.settle_foreign_writes()
    assert list(bridge.foreign_writes) == ["CANMotor/Talon FX (v6)[1] >supplyVoltage"]


def test_own_echoes_are_not_foreign():
    bridge = announced(make_bridge(0, PHOENIX_CONFIG, speed=None, held=True))
    now = [100.0]
    bridge.cache.clock = lambda: now[0]
    cc, pigeon, talon = ("CANEncoder", "CANcoder (v6)[2]"), ("CANGyro", "Pigeon 2 (v6)[3]"), ("CANMotor", "Talon FX (v6)[1]")
    sent = [1.0 + 0.1 * i for i in range(9)]                # 9 sends: the history keeps the last 8
    for v in sent:
        bridge.cache.diff(*ROTOR, {">rawPositionInput": v})
    bridge.cache.diff(*cc, {">rawPositionInput": 0.3, ">velocity": 0.5})
    bridge.cache.diff(*pigeon, {">rawYawInput": -10.0})
    bridge.cache.diff(*talon, {">supplyVoltage": 12.0})
    for v in sent[1:]:
        bridge._check_inputs(ROTOR, {">rawPositionInput": v})            # any of the last 8 sent values
    bridge._check_inputs(ROTOR, {">rawPositionInput": sent[-1] + 0.9 / 4096})  # Phoenix quantizes the rotor
    bridge._check_inputs(cc, {">rawPositionInput": 0.3 + 0.9 / 4096})   # and the CANcoder to 1/4096 rev
    bridge._check_inputs(cc, {">velocity": 0.5 + 9.0 / 4096})          # and velocities
    bridge._check_inputs(pigeon, {">rawYawInput": 350.0})                # the Pigeon wraps yaw to [0, 360)
    bridge._check_inputs(talon, {">supplyVoltage": 11.9955})             # and models a little supply drop
    bridge._check_inputs(ROTOR, {">acceleration": 3.0})                  # never sent by scrimmage: ignored
    bridge._check_inputs(("CANEncoder", "Unbound (v6)[9]"), {">rawPositionInput": 7.0})  # not bound: ignored
    bridge._check_inputs(ROTOR, {"<position": 7.0})                      # outputs aren't inputs
    bridge._check_inputs(ROTOR, {">rawPositionInput": 2.5})              # the value scrimmage sends next...
    bridge.cache.diff(*ROTOR, {">rawPositionInput": 2.5})
    bridge.settle_foreign_writes()
    assert bridge.foreign_writes == {}
    for _ in range(1100):                                                 # LATE_ECHO after the first sends, the robot
        now[0] += 0.01                                                    # program talking all along...
        bridge._check_inputs(("DriverStation", ""), {">enabled": True})
    bridge.cache.diff(*ROTOR, {">rawPositionInput": 2.6})                 # ...the history is the last 8 again
    bridge._check_inputs(ROTOR, {">rawPositionInput": sent[0]})          # older than the last 8
    bridge._check_inputs(cc, {">rawPositionInput": 0.3 + 3.0 / 4096})
    bridge.settle_foreign_writes()
    assert sorted(bridge.foreign_writes) == ["CANEncoder/CANcoder (v6)[2] >rawPositionInput",
                                             "CANEncoder/Talon FX (v6)[1]/Rotor Sensor >rawPositionInput"]
    for v in (9.0, 2.5, 9.5):                     # one pending suspect per key (2.5 was sent: not a suspect)
        bridge._check_inputs(ROTOR, {">rawPositionInput": v})
    assert len(bridge._suspects) == 1
    bridge.settle_foreign_writes()
    assert bridge.foreign_writes["CANEncoder/Talon FX (v6)[1]/Rotor Sensor >rawPositionInput"]["count"] == 3
    bridge._restart_clock()
    assert bridge.foreign_writes == {} and bridge.cache.sent_history(*ROTOR, ">rawPositionInput") == []


def test_realtime_echo_after_a_long_pause_is_not_foreign():
    """Real time sends every 5 ms; a JVM GC/JIT pause holds an echo back past the last 8 sends."""
    bridge = announced(make_bridge(0, PHOENIX_CONFIG, speed=None, held=True))
    now = [50.0]
    bridge.cache.clock = lambda: now[0]
    for i in range(60):                           # 300 ms of 5 ms sensor pushes
        bridge.cache.diff(*ROTOR, {">rawPositionInput": 1.0 + 0.01 * i})
        now[0] += 0.005
    bridge._check_inputs(ROTOR, {">rawPositionInput": 1.0})                # the oldest value comes back late
    bridge._check_inputs(ROTOR, {">rawPositionInput": 1.0 + 0.01 * 20})
    bridge.settle_foreign_writes()
    assert bridge.foreign_writes == {}
    bridge._check_inputs(ROTOR, {">rawPositionInput": 123.0})              # never sent: still foreign
    bridge.settle_foreign_writes()
    assert list(bridge.foreign_writes) == ["CANEncoder/Talon FX (v6)[1]/Rotor Sensor >rawPositionInput"]


def test_echoes_of_a_stalled_robot_s_backlog_are_not_foreign():
    """A robot program that stalls (a slow CI runner starving the JVM; reproduced by SIGSTOPping it for 3 s during
    the Phoenix scenario's spin-up) relays, once it resumes, every value sent meanwhile: by then more than
    SENT_WINDOW old, but they're still its echoes: the window of values kept widens by twice the longest it has
    been behind (silent, up to now, or relaying superseded values), which also covers Phoenix's own rewrites of the
    same values trailing the backlog. A value nobody sent is still foreign."""
    bridge = announced(make_bridge(0, PHOENIX_CONFIG, speed=None, held=True))
    now = [50.0]
    bridge.cache.clock = lambda: now[0]
    bridge._mark_released()                                         # real time: how far behind it falls counts
    talon = ("CANMotor", "Talon FX (v6)[1]")
    sent = [12.5 - 0.004 * i for i in range(600)]          # 3 s of 5 ms pushes, the bus sagging each one
    bridge.cache.diff(*talon, {">supplyVoltage": sent[0]})
    bridge._check_inputs(talon, {">supplyVoltage": sent[0]})       # relayed at once: the robot's caught up
    for v in sent[1:]:                                              # then it stalls: nothing comes back
        now[0] += 0.005
        bridge.cache.diff(*talon, {">supplyVoltage": v})
    now[0] += 0.5
    for v in sent[1:]:                                              # it resumes: the backlog, in order
        bridge._check_inputs(talon, {">supplyVoltage": v})
        now[0] += 0.0005
    now[0] += 0.7
    for v in sent[1::4]:            # then Phoenix's own rewrites of the same values, trailing (from 4.1 s late)
        bridge._check_inputs(talon, {">supplyVoltage": v})
        bridge.cache.diff(*talon, {">supplyVoltage": 12.5})         # while new values go out and are relayed
        bridge._check_inputs(talon, {">supplyVoltage": 12.5})
        now[0] += 0.002
    bridge.settle_foreign_writes()
    assert bridge.foreign_writes == {}
    bridge._check_inputs(talon, {">supplyVoltage": 3.0})
    bridge.settle_foreign_writes()
    assert list(bridge.foreign_writes) == ["CANMotor/Talon FX (v6)[1] >supplyVoltage"]


def test_late_echoes_from_a_robot_that_keeps_talking_are_not_foreign():
    """A starved robot program need not go silent: its HALSim thread keeps relaying frames while Phoenix 6's own
    rewrites of our values fall seconds behind (CI run 40898167246: every Talon whose values were changing flagged at
    one instant, the robot program 3.2 s behind). Nothing measured it behind before those rewrites came, so a window of
    SENT_WINDOW + twice that lag dropped them. The values kept reach LATE_ECHO back whatever the lag, and a late echo
    counts how far behind it was. A value nobody sent is still foreign."""
    bridge = announced(make_bridge(0, PHOENIX_CONFIG, speed=None, held=True))
    now = [50.0]
    bridge.cache.clock = lambda: now[0]
    bridge._mark_released()
    talon, ds = ("CANMotor", "Talon FX (v6)[1]"), ("DriverStation", "")
    sent = [12.5 - 0.001 * i for i in range(400)]          # 3.2 s of 8 ms pushes (physics behind), the bus sagging
    bridge.cache.diff(*talon, {">supplyVoltage": sent[0]})
    bridge._check_inputs(talon, {">supplyVoltage": sent[0]})
    for v in sent[1:]:
        now[0] += 0.008
        bridge.cache.diff(*talon, {">supplyVoltage": v})
        bridge._check_inputs(ds, {">enabled": True})       # still talking: never silent
    for v in sent[:40]:                                     # Phoenix's rewrites of the values of 3.2 s ago
        bridge._check_inputs(talon, {">supplyVoltage": v})
        bridge._check_inputs(ds, {">enabled": True})
        now[0] += 0.005
    bridge.settle_foreign_writes()
    assert bridge.foreign_writes == {}
    assert bridge.robot_behind > 3.0
    bridge._check_inputs(talon, {">supplyVoltage": 3.0})
    bridge.settle_foreign_writes()
    assert list(bridge.foreign_writes) == ["CANMotor/Talon FX (v6)[1] >supplyVoltage"]


def test_robot_behind_counts_only_once_the_clock_runs_free():
    """How far behind real time the robot program has been, for the report: silences and late relays once Phoenix 6
    devices freed its clock (booting, held in lockstep, it may be silent as long as it likes)."""
    bridge = make_bridge(0, PHOENIX_CONFIG, speed=None, held=True)
    now = [10.0]
    bridge.cache.clock = lambda: now[0]
    ds = ("DriverStation", "")
    bridge._check_inputs(ds, {">enabled": False})
    now[0] += 3.0
    bridge._check_inputs(ds, {">enabled": False})            # 3 s silent while booting: not counted
    now[0] += 2.0                                             # held 2 s more, then the clock is freed...
    bridge._mark_released()
    now[0] += 0.1
    bridge._check_inputs(ds, {">enabled": False})            # ...and the gap spanning the release isn't counted
    assert bridge.robot_behind == 0.0 and bridge.cache.lag == 0.0
    now[0] += 0.3
    bridge._check_inputs(ds, {">enabled": True})              # 0.3 s silent in real time
    assert bridge.robot_behind == pytest.approx(0.3)
    talon = ("CANMotor", "Talon FX (v6)[1]")
    bridge._check_inputs(talon, {">supplyVoltage": 12.0})     # announced
    for v in (12.4, 12.3, 12.2):
        bridge.cache.diff(*talon, {">supplyVoltage": v})
        now[0] += 0.2
        bridge._check_inputs(ds, {">enabled": True})          # talking all along
    bridge._check_inputs(talon, {">supplyVoltage": 12.4})     # but relaying 12.4 with 12.3 out 0.4 s since
    assert bridge.robot_behind == pytest.approx(0.4)
    bridge._restart_clock()                                   # attach mode's reset: still real time
    assert bridge.robot_behind == 0.0 and bridge.cache.lag == 0.0
    now[0] += 0.5
    bridge._check_inputs(ds, {">enabled": True})              # the reset restarts the count from the next frame
    now[0] += 0.2
    bridge._check_inputs(ds, {">enabled": True})
    assert bridge.robot_behind == pytest.approx(0.2)


def test_sent_history_window_widens_by_how_far_behind_the_robot_program_has_been():
    """Values are kept SENT_WINDOW plus twice the longest the robot program has been behind: silent (up to now), or
    relaying a value after a newer one had gone out (and LATE_ECHO at least)."""
    from scrimmage.protocol import LATE_ECHO, SENT_HISTORY, SENT_WINDOW, OutboundCache
    now = [0.0]
    c = OutboundCache(clock=lambda: now[0])
    c.heard()
    now[0] += 5.0
    assert c.heard() == 0.0 and c.lag == 0.0                  # not counted until the robot runs in real time
    now[0] = 0.0
    c.track_lag()
    for v in range(100):                                      # 1 s of sends, the robot program answering each
        c.diff("CANMotor", "d", {">x": float(v)})
        c.heard()
        now[0] += 0.01
    assert c.lag == pytest.approx(0.01)
    now[0] += 6.0                                             # then silent for 6 s: nothing it may yet relay goes
    c.diff("CANMotor", "d", {">x": 100.0})
    assert len(c.sent_history("CANMotor", "d", ">x")) == 101
    c.heard()                                                 # it was 6 s behind: kept 1 + 2 * 6 s from now on
    assert c.lag == pytest.approx(6.01)
    assert SENT_WINDOW + 2 * 6.01 > LATE_ECHO
    while now[0] < SENT_WINDOW + 2 * 6.01 + 0.005:           # the robot program talking again, until value 0 is
        now[0] += 0.005                                       # just past that...
        c.heard()
    assert c.lag == pytest.approx(6.01)
    c.diff("CANMotor", "d", {">x": 101.0})
    assert c.sent_history("CANMotor", "d", ">x")[0] == 1.0    # ...and goes; value 1, sent 0.01 s later, stays

    c = OutboundCache(clock=lambda: now[0])
    c.track_lag()
    for v in range(20):
        c.diff("CANMotor", "d", {">x": float(v)})
        c.heard()
        now[0] += 0.1
    sent = c.sent_history("CANMotor", "d", ">x")
    c.relayed("CANMotor", "d", ">x", sent.index(12.0))       # value 12 relayed when value 13 went out 0.7 s before
    assert c.lag == pytest.approx(0.7)
    c.relayed("CANMotor", "d", ">x", len(sent) - 1)          # the newest: no lag
    assert c.lag == pytest.approx(0.7)
    c.reset()
    assert c.lag == 0.0 and c.window(now[0]) == LATE_ECHO


def test_sent_history_is_time_windowed_and_capped():
    from scrimmage.protocol import LATE_ECHO, SENT_CAP, SENT_HISTORY, OutboundCache
    now = [0.0]
    c = OutboundCache(clock=lambda: now[0])
    for v in range(40):
        c.diff("CANEncoder", "d", {">x": float(v)})
        now[0] += 0.01
    assert len(c.sent_history("CANEncoder", "d", ">x")) == 40              # all within the window
    now[0] += LATE_ECHO
    c.diff("CANEncoder", "d", {">x": 99.0})
    assert c.sent_history("CANEncoder", "d", ">x")[-SENT_HISTORY:][-1] == 99.0
    assert len(c.sent_history("CANEncoder", "d", ">x")) == SENT_HISTORY    # stale ones go, the last 8 stay
    for v in range(SENT_CAP * 2):                                          # all at one instant: capped
        c.diff("CANEncoder", "d", {">y": float(v)})
    assert len(c.sent_history("CANEncoder", "d", ">y")) == SENT_CAP


def test_foreign_write_cause_names_the_agent_build_step():
    from scrimmage import foreign_writes as fwn
    assert "scrimmage java build" in fwn.cause("missing") and "scrimmage java build" in fwn.cause(None)


@pytest.mark.parametrize("agent", ["missing", "loaded", None])
def test_foreign_write_message_gives_robotpy_robots_python_advice(agent):
    from scrimmage import foreign_writes as fwn
    writes = [{"device": "Talon FX (v6)[1]/Rotor Sensor", "key": ">velocity", "first_t": 0.5}]
    py = fwn.message("Talon FX (v6)[1]", writes, agent, "python")
    assert "sim_state.set_" in py and "RobotPy" in py
    assert "java" not in py.lower() and "getSimState" not in py
    java = fwn.message("Talon FX (v6)[1]", writes, agent, "java")
    assert java == fwn.message("Talon FX (v6)[1]", writes, agent) and "getSimState().set" in java


def test_sent_history_keeps_the_last_eight_values_across_refreshes():
    from scrimmage.protocol import OutboundCache
    c = OutboundCache()
    c.clock = lambda: 1000.0
    for v in range(10):
        c.diff("CANEncoder", "d", {">x": float(v)})
    c.clock = lambda: 2000.0                      # well past the window: only the last 8 remain
    c.clear()                                     # a full refresh resends, but remembers what was sent
    c.diff("CANEncoder", "d", {">x": 9.0})
    assert c.sent_history("CANEncoder", "d", ">x") == [float(v) for v in range(2, 10)]
    c.forget("CANEncoder", "d")                   # the device was re-initialized: its old values are gone
    assert c.sent_history("CANEncoder", "d", ">x") == []


def test_unheld_speed_with_phoenix_paces_at_real_time(caplog):
    caplog.set_level(logging.WARNING, logger="scrimmage")

    async def main():
        port = next_port()
        bridge = make_bridge(port, PHOENIX_CONFIG, speed=None)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True, phoenix=True):
                await wait_until(lambda: bridge.mode == "realtime" and bridge.phoenix)
                t0 = bridge.world.time
                await asyncio.sleep(0.6)
                rate = (bridge.world.time - t0) / 0.6
        assert 0.6 < rate < 1.3, rate
    asyncio.run(main())
    warns = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Phoenix 6")]
    assert warns == [REASON]


def test_large_values_are_compared_at_float32_precision():
    """Phoenix rewrites its inputs as float32 (and wraps yaw): far from zero, one ulp is more than a count."""
    bridge = announced(make_bridge(0, PHOENIX_CONFIG, speed=None, held=True))
    cc, pigeon = ("CANEncoder", "CANcoder (v6)[2]"), ("CANGyro", "Pigeon 2 (v6)[3]")
    bridge.cache.diff(*ROTOR, {">rawPositionInput": 20021.546328359338})
    bridge.cache.diff(*cc, {">rawPositionInput": 20002.424643364935})
    bridge.cache.diff(*pigeon, {">rawYawInput": 19867.386369686108})
    bridge._check_inputs(ROTOR, {">rawPositionInput": 20021.54638671875})   # measured Phoenix 6 26.1.2 rewrites
    bridge._check_inputs(cc, {">rawPositionInput": 20002.424560546875})
    bridge._check_inputs(pigeon, {">rawYawInput": 67.38671875})
    bridge._check_inputs(ROTOR, {">rawPositionInput": 20021.546328359338 + 0.01})  # still caught
    bridge.settle_foreign_writes()
    assert list(bridge.foreign_writes) == ["CANEncoder/Talon FX (v6)[1]/Rotor Sensor >rawPositionInput"]


def test_one_warning_per_device_naming_keys_in_words(caplog):
    caplog.set_level(logging.WARNING, logger="scrimmage")
    bridge = announced(make_bridge(0, PHOENIX_CONFIG, speed=None, held=True))
    talon, pigeon = ("CANMotor", "Talon FX (v6)[1]"), ("CANGyro", "Pigeon 2 (v6)[3]")
    bridge.agent_state = lambda: "loaded"
    bridge.cache.diff(*ROTOR, {">rawPositionInput": 1.0, ">velocity": 2.0})
    bridge.cache.diff(*talon, {">supplyVoltage": 12.0})
    bridge.cache.diff(*pigeon, {">rawYawInput": 0.0})
    bridge._check_inputs(ROTOR, {">rawPositionInput": 5.0, ">velocity": 7.0})
    bridge._check_inputs(talon, {">supplyVoltage": 6.0})
    bridge._check_inputs(pigeon, {">rawYawInput": 33.0})
    bridge.settle_foreign_writes()
    bridge._check_inputs(ROTOR, {">rawPositionInput": 6.0})
    bridge.settle_foreign_writes()
    warns = sorted(r.getMessage() for r in caplog.records if "writes simulated" in r.getMessage())
    assert len(warns) == 2
    assert warns[0].startswith("something in the robot program writes simulated sensor values for Pigeon 2 (v6)[3] (yaw")
    assert ("writes simulated sensor values for Talon FX (v6)[1] (rotor position, rotor velocity, supply voltage, "
            "first at t=2.000)") in warns[1]
    assert "without going through the Phoenix SimState setters" in warns[1]


def test_pacing_switches_to_real_time_when_phoenix_appears_mid_run():
    async def main():
        port = next_port()
        bridge = make_bridge(port, PHOENIX_CONFIG, speed=None)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True) as robot:
                await wait_until(lambda: bridge.mode == "lockstep" and bridge.driver is not None)
                t0, w0 = bridge.world.time, time.monotonic()
                await asyncio.sleep(0.3)
                fast = (bridge.world.time - t0) / (time.monotonic() - w0)
                await robot.ws.send(frame("CANMotor", "Talon FX (v6)[1]", {"<motorVoltage": 0.0}))
                await wait_until(lambda: bridge.mode == "realtime")
                await asyncio.sleep(0.05)
                t0, w0 = bridge.world.time, time.monotonic()
                await asyncio.sleep(0.6)
                paced = (bridge.world.time - t0) / (time.monotonic() - w0)
        assert fast > 2.0, fast
        assert 0.7 < paced < 1.3, paced
    asyncio.run(main())


def test_missing_phoenix_devices_are_reported_two_seconds_after_connecting(caplog):
    """A configured Phoenix device the robot never created: name a similar device (same kind and id) if there
    is one, otherwise say which Phoenix devices the robot did create."""
    caplog.set_level(logging.WARNING, logger="scrimmage")
    bridge = make_bridge(0, PHOENIX_CONFIG, speed=None, held=True)  # TalonFX 1 and 4, CANcoder 2, Pigeon 3
    for dev in (("CANMotor", "Talon FX (v6)[1] (canivore)"), ("CANMotor", "Talon FX (v6)[4]"),
                ("CANEncoder", "Talon FX (v6)[4]/Rotor Sensor"), ("CANEncoder", "CANcoder (v6)[0]"),
                ("CANGyro", "Pigeon 2 (v6)[3]")):
        bridge.hal.devices[dev] = {}

    def warnings():
        return [r.getMessage() for r in caplog.records if "config expects" in r.getMessage()]

    async def main():
        await bridge._on_tick(1.9)
        assert warnings() == []                 # devices may still be on their way
        await bridge._on_tick(1.9999999999)     # 2000 summed 1 ms timesteps fall just short of 2.0
        await bridge._on_tick(2.5)              # once per connection
    asyncio.run(main())
    assert sorted(warnings()) == sorted([
        'config expects Talon FX (v6)[1], robot has Talon FX (v6)[1] (canivore): set device = '
        '"Talon FX (v6)[1] (canivore)" in that binding',
        "config expects CANcoder (v6)[2], but the robot program hasn't created it 2 s after connecting; "
        "check the CAN ids in the config (the robot has CANcoder (v6)[0], Pigeon 2 (v6)[3], Talon FX (v6)[1] "
        "(canivore), Talon FX (v6)[4])",
    ]), warnings()
    bridge._restart_clock()                     # a new connection checks again
    caplog.clear()
    bridge.hal.devices.clear()
    asyncio.run(bridge._on_tick(2.0))
    assert len(warnings()) == 1 and "Talon FX (v6)[1], Talon FX (v6)[4], CANcoder (v6)[2], Pigeon 2 (v6)[3]" \
        in warnings()[0] and "hasn't created them" in warnings()[0] and "the robot has no Phoenix 6 devices" \
        in warnings()[0], warnings()


def test_present_phoenix_devices_are_not_reported(caplog):
    caplog.set_level(logging.WARNING, logger="scrimmage")
    bridge = make_bridge(0, PHOENIX_CONFIG, speed=None, held=True)
    for dev in (("CANMotor", "Talon FX (v6)[1]"), ("CANMotor", "Talon FX (v6)[4]"),
                ("CANEncoder", "CANcoder (v6)[2]"), ("CANGyro", "Pigeon 2 (v6)[3]")):
        bridge.hal.devices[dev] = {}
    asyncio.run(bridge._on_tick(3.0))
    assert not [r for r in caplog.records if "config expects" in r.getMessage()]


REV_XML = """<mujoco><worldbody><body name="arm"><freejoint/><geom type="box" size="0.1 0.1 0.1" mass="1"/>
<body name="link"><joint name="shoulder" type="hinge" axis="0 1 0"/><geom type="box" size="0.1 0.1 0.1" mass="1"/>
</body></body></worldbody></mujoco>"""
REV_BINDINGS = ('[[motor]]\nrev = "SparkMax:3"\njoint = "shoulder"\n'
                '[[motor]]\nname = "flex"\nrev = "SparkFlex:5"\njoint = "shoulder"\n'
                '[[motor]]\nname = "nine"\nrev = "SparkMax:9"\njoint = "shoulder"\n'
                '[[encoder]]\nrev = "SparkMaxAbs:3"\njoint = "shoulder"\n')


def rev_bridge(tmp_path, port: int = 0, bindings: str = REV_BINDINGS, **kw):
    import tomllib
    from scrimmage.bridge import Bridge
    from scrimmage.world import World
    (tmp_path / "m.xml").write_text(REV_XML)
    cfg = tomllib.loads('[model]\nmjcf = "m.xml"\ntimestep = 0.001\n[robot]\nbody = "arm"\n' + bindings)
    return Bridge(World(cfg, tmp_path), "localhost", port, nt=False, **kw)


def _messages(caplog, text: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if text in r.getMessage()]


def test_missing_rev_devices_are_reported_two_seconds_after_connecting(caplog, tmp_path):
    """Bound SPARKs the robot never created: a SPARK of the other kind with the same id gets a hint, the rest
    share one message listing the SPARKs the robot did create (the Phoenix check's wording)."""
    caplog.set_level(logging.WARNING, logger="scrimmage")
    bridge = rev_bridge(tmp_path, speed=None, held=True)
    for dev in (("ScrimmageREV", "SparkMax[3]"), ("ScrimmageREV", "SparkMax[5]"), ("SimDevice", "SPARK MAX [3]")):
        bridge.hal.devices[dev] = {}

    async def main():
        await bridge._on_tick(1.9)
        assert _messages(caplog, "config expects") == []
        await bridge._on_tick(2.0)
        await bridge._on_tick(2.5)
    asyncio.run(main())
    assert sorted(_messages(caplog, "config expects")) == sorted([
        "config expects SparkFlex[5], robot has SparkMax[5]: use SparkMax in that binding's rev (\"SparkMax:5\")",
        "config expects SparkMax[9], but the robot program hasn't created it 2 s after connecting; "
        "check the CAN ids in the config (the robot has SparkMax[3], SparkMax[5])",
    ]), _messages(caplog, "config expects")
    bridge._restart_clock()
    caplog.clear()
    bridge.hal.devices.clear()
    asyncio.run(bridge._on_tick(2.0))
    (msg,) = _messages(caplog, "config expects")
    assert "SparkMax[3], SparkFlex[5], SparkMax[9]" in msg and "the robot has no REV SPARKs" in msg, msg


def test_rev_spark_nothing_binds_is_reported_once_and_never_sets_the_phoenix_flag(caplog, tmp_path):
    caplog.set_level(logging.INFO, logger="scrimmage")

    async def main():
        port = next_port()
        bridge = rev_bridge(tmp_path, port, speed=None)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True) as robot:
                await wait_until(lambda: bridge.mode == "lockstep")
                for dev in (("ScrimmageREV", "SparkMax[3]"), ("ScrimmageREV", "SparkMax[7]")):
                    await robot.ws.send(frame(*dev, {"<motorVoltage": 0.0}))
                    await robot.ws.send(frame(*dev, {"<motorVoltage": 1.0}))
                await robot.ws.send(frame("SimDevice", "SPARK MAX [7]", {"<Applied Output": 0.0}))
                await wait_until(lambda: ("ScrimmageREV", "SparkMax[7]") in bridge.hal.devices
                                 and ("SimDevice", "SPARK MAX [7]") in bridge.hal.devices)
                await asyncio.sleep(0.2)
                assert not bridge.phoenix and bridge.effective_speed is None and bridge.realtime_reason is None
    asyncio.run(main())
    assert _messages(caplog, "nothing in the config binds") == [
        'the robot program created SPARK MAX 7, but nothing in the config binds it: it runs in REV\'s simulation '
        'with no physics (bind it with rev = "SparkMax:7")']
    assert not _messages(caplog, "Phoenix 6")


def test_rev_sim_unavailable_is_an_error_naming_the_revlib_needed(caplog, tmp_path):
    caplog.set_level(logging.WARNING, logger="scrimmage")
    bridge = rev_bridge(tmp_path, speed=None, held=True)
    state = None
    bridge.rev_state = lambda: state

    async def main():
        nonlocal state
        await bridge._on_tick(0.1)
        state = "unavailable: no com.revrobotics.spark.SparkSim"
        await bridge._on_tick(0.2)
        await bridge._on_tick(0.3)
        await bridge._on_tick(2.0)     # no REV "config expects" warnings on top of the error
    asyncio.run(main())
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1, errors
    msg = errors[0].getMessage()
    assert "no com.revrobotics.spark.SparkSim" in msg and "update REVLib to 2026.0.5 or later (vendordeps/REVLib.json)" in msg
    assert "vendordep" in msg
    assert not _messages(caplog, "config expects")


def test_rev_sim_unavailable_is_ignored_when_the_config_binds_no_rev(caplog):
    caplog.set_level(logging.WARNING, logger="scrimmage")
    bridge = make_bridge(0, speed=None, held=True)
    bridge.rev_state = lambda: "unavailable: whatever"
    asyncio.run(bridge._on_tick(0.1))
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]


def test_rev_other_kind_hint_for_an_absolute_encoder_names_the_abs_form(caplog, tmp_path):
    caplog.set_level(logging.WARNING, logger="scrimmage")
    bridge = rev_bridge(tmp_path, bindings='[[encoder]]\nrev = "SparkFlexAbs:5"\njoint = "shoulder"\n',
                        speed=None, held=True)
    bridge.hal.devices[("ScrimmageREV", "SparkMax[5]")] = {}
    asyncio.run(bridge._on_tick(2.0))
    assert _messages(caplog, "config expects") == [
        "config expects SparkFlex[5], robot has SparkMax[5]: use SparkMax in that binding's rev (\"SparkMaxAbs:5\")"]


def test_rev_inputs_are_resent_when_the_helper_device_appears(tmp_path):
    """halsim_ws_client drops values for a SimDevice that doesn't exist yet, and the agent creates a SPARK's ScrimmageREV
    helper a robot loop after the SPARK (no <init either): scrimmage resends all its inputs when the helper first
    appears, not up to FULL_REFRESH later, so values that don't change (a module at rest, the motor code) arrive."""
    def rev_frames(robot, start):
        return [m["data"] for m in robot.frames[start:] if (m["type"], m["device"]) == ("ScrimmageREV", "SparkMax[3]")]

    async def main():
        port = next_port()
        bridge = rev_bridge(tmp_path, port, bindings='[[motor]]\nrev = "SparkMax:3"\njoint = "shoulder"\n'
                            '[[encoder]]\nrev = "SparkMaxAbs:3"\njoint = "shoulder"\noffset = 0.3\n',
                            speed=None, held=True)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True) as robot:
                await wait_until(lambda: bridge.driver is not None)
                for _ in range(2):
                    assert await bridge.driver.step_once() == "ok"
                assert any(">motor" in d for d in rev_frames(robot, 0))     # sent before the helper existed: lost
                await robot.ws.send(frame("ScrimmageREV", "SparkMax[3]", {"<motorVoltage": 0.0, ">motor": 0.0,
                                                                      ">absRotations": 0.0}))
                await wait_until(lambda: ("ScrimmageREV", "SparkMax[3]") in bridge.hal.devices)
                mark = len(robot.frames)
                assert await bridge.driver.step_once() == "ok"
                sent = {k: v for d in rev_frames(robot, mark) for k, v in d.items()}
                assert sent.get(">motor") == 1.0 and sent.get(">absRotations") == pytest.approx(0.3), sent
                assert {">position", ">velocity", ">vbus", ">current"} <= set(sent), sent
                # only its first appearance: later frames from the helper don't resend what hasn't changed
                await robot.ws.send(frame("ScrimmageREV", "SparkMax[3]", {"<motorVoltage": 0.0, ">fwdLimit": False}))
                await wait_until(lambda: bridge.hal.get("ScrimmageREV", "SparkMax[3]", ">fwdLimit") is False)
                mark = len(robot.frames)
                assert await bridge.driver.step_once() == "ok"
                assert not any(">motor" in d or ">absRotations" in d for d in rev_frames(robot, mark))
                assert bridge.world.time < bridge_mod.FULL_REFRESH
    asyncio.run(main())


@pytest.mark.parametrize("agent", ["missing", "loaded", None])
def test_a_helper_present_means_the_agent_drives_rev_whatever_the_console_says(caplog, tmp_path, agent):
    """The console's agent line can arrive well after a fast lockstep run's 2 s check ("missing" then): with any
    ScrimmageREV helper present the agent is driving REV, so a bound SPARK still missing is a CAN id (or lazy) matter."""
    caplog.set_level(logging.WARNING, logger="scrimmage")
    bridge = rev_bridge(tmp_path, speed=None, held=True)
    bridge.agent_state = lambda: agent
    for dev in (("SimDevice", "SPARK MAX [3]"), ("ScrimmageREV", "SparkMax[3]")):
        bridge.hal.devices[dev] = {}
    asyncio.run(bridge._on_tick(2.0))
    (msg,) = _messages(caplog, "config expects")
    assert "SparkFlex[5], SparkMax[9]" in msg and "check the CAN ids" in msg and "agent" not in msg, msg


@pytest.mark.parametrize("agent, reason", [
    ("missing", "the scrimmage Java agent isn't loaded or is out of date"),
    (None, "the scrimmage Java agent isn't loaded or is out of date"),
    ("loaded", "an out-of-date agent jar?"),
    ("disabled", "the scrimmage Java agent was disabled (-Dscrimmage.agent.disable)"),
])
def test_revs_own_sparks_without_helpers_blame_the_agent(caplog, tmp_path, agent, reason):
    """REV's own SimDevices show the robot created SPARKs 3 and 5 but no helper did (agent not loaded, disabled, or
    too old): those get the agent message, the console's state only words the reason. SPARK 9, which the robot didn't
    create at all, still gets the CAN-id message."""
    caplog.set_level(logging.WARNING, logger="scrimmage")
    bridge = rev_bridge(tmp_path, speed=None, held=True)
    bridge.agent_state = lambda: agent
    for dev in (("SimDevice", "SPARK MAX [3]"), ("SimDevice", "SPARK Flex [5]"),
                ("SimDevice", "SPARK MAX [3] ABSOLUTE ENCODER"), ("SimDevice", "SPARK MAX [7]")):
        bridge.hal.devices[dev] = {}
    asyncio.run(bridge._on_tick(2.0))
    msgs = _messages(caplog, "config expects")
    assert len(msgs) == 2, msgs
    agent_msg = next(m for m in msgs if "agent" in m)
    assert agent_msg == ("config expects SparkMax[3], SparkFlex[5], but the scrimmage agent isn't driving the "
                         "robot's REV SPARKs: the robot program created SPARK MAX 3, SPARK Flex 5 with no scrimmage "
                         f"helper ({reason}); run the robot program through scrimmage or its Gradle init script "
                         "(see `scrimmage java path`), after `scrimmage java build`"), agent_msg
    other = next(m for m in msgs if m is not agent_msg)
    assert other.startswith("config expects SparkMax[9], but the robot program hasn't created it") \
        and "check the CAN ids" in other, other


@pytest.mark.parametrize("agent", ["missing", "disabled"])
def test_no_spark_at_all_is_a_can_id_message_whatever_the_console_says(caplog, tmp_path, agent):
    caplog.set_level(logging.WARNING, logger="scrimmage")
    bridge = rev_bridge(tmp_path, speed=None, held=True)
    bridge.agent_state = lambda: agent
    asyncio.run(bridge._on_tick(2.0))
    (msg,) = _messages(caplog, "config expects")
    assert "check the CAN ids" in msg and "agent" not in msg, msg


def test_robot_language_from_the_command():
    from scrimmage.bridge import robot_language
    assert robot_language(["/x/python", "-m", "robotpy", "sim", "--scrimmage-lockstep"]) == "python"
    assert robot_language(["robotpy", "sim"]) == "python"
    assert robot_language(["./gradlew", "-I", "i.gradle", "simulateJava"]) == "java"
    assert robot_language(["gradle.bat", "simulateJava"]) == "java"
    assert robot_language(["{python}", "fake_robot_main.py"]) is None


def _hal_with(*devs):
    from scrimmage.protocol import HalState
    hal = HalState()
    for dev in devs:
        hal.devices[dev] = {}
    return hal


def test_rev_wording_java_is_unchanged_and_robotpy_names_lockstep_and_robotpy_rev():
    from scrimmage.bridge import missing_rev_warnings, rev_unavailable_message
    hal = _hal_with(("SimDevice", "SPARK MAX [3]"))
    expected = [("ScrimmageREV", "SparkMax[3]")]
    (java,) = missing_rev_warnings(hal, expected, agent="missing")
    assert java == missing_rev_warnings(hal, expected, agent="missing", language="java")[0]
    assert "scrimmage agent isn't driving" in java and "scrimmage java build" in java
    (py,) = missing_rev_warnings(hal, expected, agent="missing", language="python")
    assert "the scrimmage REV helper isn't driving the robot's REV SPARKs" in py
    assert "add `--scrimmage-lockstep` to `[robot_program] command`, or update scrimmage-lockstep " \
        "(`pip install ./lockstep`)" in py
    assert "scrimmage java build" not in py and "Java agent" not in py
    (loaded,) = missing_rev_warnings(hal, expected, agent="loaded", language="python")
    assert "before scrimmage's REV helper loaded" in loaded and "out-of-date" not in loaded
    state = "unavailable: no SparkSim.iterate"
    assert "update REVLib to 2026.0.5" in rev_unavailable_message(state)
    assert rev_unavailable_message(state, "java") == rev_unavailable_message(state)
    py = rev_unavailable_message(state, "python")
    assert "no SparkSim.iterate" in py and 'update robotpy-rev (`pip install "robotpy-rev>=2026.0.4,<2027"`)' in py
    assert "REVLib" not in py


def test_robotpy_sparks_all_made_at_import_time_get_the_import_time_message():
    """The helper loaded but no SparkSim was built, so there is no ScrimmageREV device at all."""
    from scrimmage.bridge import missing_rev_warnings
    hal = _hal_with(("SimDevice", "SPARK MAX [3]"))
    (msg,) = missing_rev_warnings(hal, [("ScrimmageREV", "SparkMax[3]")], agent="loaded", language="python")
    assert "created SPARK MAX 3 before scrimmage's REV helper loaded" in msg
    assert "create SPARKs in robotInit or a subsystem constructor" in msg


def test_robotpy_spark_made_before_the_helper_loaded_gets_its_own_message():
    """A SPARK created at import time of robot.py has REVLib's SimDevice but no helper, while other SPARKs do."""
    from scrimmage.bridge import missing_rev_warnings
    hal = _hal_with(("ScrimmageREV", "SparkMax[5]"), ("SimDevice", "SPARK MAX [5]"), ("SimDevice", "SPARK MAX [3]"))
    expected = [("ScrimmageREV", "SparkMax[3]"), ("ScrimmageREV", "SparkMax[5]")]
    (msg,) = missing_rev_warnings(hal, expected, agent="loaded", language="python")
    assert msg == ("config expects SparkMax[3], but the robot program created SPARK MAX 3 before scrimmage's REV "
                   "helper loaded (for example at import time of robot.py): create SPARKs in robotInit or a "
                   "subsystem constructor")
    # Java behavior unchanged: a CAN-id message listing what the robot has
    (jmsg,) = missing_rev_warnings(hal, expected, agent="loaded", language="java")
    assert "check the CAN ids" in jmsg


def test_missing_scrimmage_devices_name_the_fast_mode_check(caplog, tmp_path):
    caplog.set_level(logging.WARNING, logger="scrimmage")
    bindings = ('[[motor]]\nscrimmage = "x"\njoint = "shoulder"\n[[motor]]\nscrimmage = "y"\njoint = "shoulder"\n'
                '[[encoder]]\nscrimmage = "enc"\njoint = "shoulder"\n[[gyro]]\nscrimmage = "imu"\nbody = "arm"\n')
    bridge = rev_bridge(tmp_path, speed=None, held=True, bindings=bindings)
    bridge.hal.devices[("ScrimmageMotor", "y")] = {}
    asyncio.run(bridge._on_tick(2.0))
    msgs = _messages(caplog, "fast mode:")
    assert msgs == [f"fast mode: the robot program never created {dev}; does it pick its fast-mode IO when "
                    "Scrimmage.fastMode() is true? (it created ScrimmageMotor:y)"
                    for dev in ("ScrimmageMotor:x", "ScrimmageEncoder:enc", "ScrimmageGyro:imu")]


def test_missing_scrimmage_devices_in_phoenix_mode_say_so_once(caplog, tmp_path):
    """Fast-mode bindings in a Phoenix-mode run: the robot picks its Phoenix IO, so none of them appear. One warning
    gives that cause, not one per device."""
    caplog.set_level(logging.WARNING, logger="scrimmage")
    bindings = "".join(f'[[motor]]\nscrimmage = "m{i}"\njoint = "shoulder"\n' for i in range(5))
    bridge = rev_bridge(tmp_path, speed=None, held=True, bindings=bindings)
    bridge.sim_mode = "phoenix"
    asyncio.run(bridge._on_tick(2.0))
    assert _messages(caplog, "fast mode:") == []
    assert _messages(caplog, "binds fast-mode devices") == [
        "the config binds fast-mode devices (ScrimmageMotor:m0, ScrimmageMotor:m1, ScrimmageMotor:m2 and 2 more), but this "
        "run isn't in fast mode, so robot code that checks Scrimmage.fastMode() uses its Phoenix IO: set "
        '[robot_program] sim_mode = "fast" or run with --sim-mode fast']


def test_created_but_unbound_scrimmage_device_is_named_once(caplog, tmp_path):
    caplog.set_level(logging.WARNING, logger="scrimmage")
    bridge = rev_bridge(tmp_path, speed=None, held=True,
                        bindings='[[motor]]\nscrimmage = "fl_drve"\njoint = "shoulder"\n')
    bridge._scrimmage_seen(("ScrimmageMotor", "fl_drive"))
    bridge._scrimmage_seen(("ScrimmageMotor", "fl_drive"))
    bridge._scrimmage_seen(("ScrimmageMotor", "fl_drve"))                 # the bound one: no report
    assert _messages(caplog, "nothing in the config binds it") == [
        'fast mode: the robot program created ScrimmageMotor:fl_drive, but nothing in the config binds it: it gets no '
        'physics (bind it with scrimmage = "fl_drive")']
    bridge.hal.devices[("ScrimmageMotor", "fl_drive")] = {}
    asyncio.run(bridge._on_tick(2.0))
    (msg,) = _messages(caplog, "never created")
    assert "never created ScrimmageMotor:fl_drve;" in msg and msg.endswith("(it created ScrimmageMotor:fl_drive)"), msg


def test_scrimmage_device_created_late_gets_its_unchanged_inputs(tmp_path):
    bridge = rev_bridge(tmp_path, speed=None, held=True,
                        bindings='[[encoder]]\nscrimmage = "cc"\njoint = "shoulder"\n')
    key = ("ScrimmageEncoder", "cc")
    frames = lambda: bridge.cache.frames(bridge.world.sensor_updates(bridge.hal))
    assert any(key[1] in f for f in frames())          # first push: the device doesn't exist on the robot yet
    assert not any(key[1] in f for f in frames())      # unchanged values are not resent...
    bridge._scrimmage_seen(key)                           # ...until the robot creates the device
    assert any(key[1] in f for f in frames())
