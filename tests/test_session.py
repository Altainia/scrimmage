"""Session against tests/fake_robot_main.py running as a real process (no RobotPy needed)."""

import asyncio
import os
import re
import signal
import time
import types
from pathlib import Path

import pytest

import scrimmage.bridge as bridge_mod
import scrimmage.session as session_mod
from scrimmage import java_tools
from fake_robot import FakeRobot, next_port, wait_until
from scrimmage.session import Session, SessionError

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FAKE = TESTS / "fake_robot.toml"


def fake_config(tmp_path, command: str, extra: str = "") -> Path:
    """tests/fake_robot.toml with another [robot_program] command (absolute paths, so it can live in tmp_path)."""
    text = FAKE.read_text().replace('"models/', f'"{TESTS / "models"}/')
    text = text.replace('command = ["{python}", "fake_robot_main.py", "--lockstep", "--drive"]', f"command = {command}")
    text = text.replace('cwd = "."', f'cwd = "{TESTS}"')
    p = tmp_path / "robot.toml"
    p.write_text(text + "\n" + extra)
    return p


def run(coro):
    return asyncio.run(coro)


def test_start_holds_at_zero_and_advance_steps_exactly():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            st = await s.start()
            assert st["lockstep"] is True and st["t"] == 0.0
            assert st["warnings"] == [] and s.status()["warnings"] == []
            await asyncio.sleep(0.5)
            assert s.t == 0.0                                 # held: no time passes on its own
            r = await s.advance(0.1)
            assert r["t"] == pytest.approx(0.1)
            assert r["pose"]["x"] == pytest.approx(2.0, abs=0.01)
            assert set(r["motors"]) == {"left_drive", "right_drive"}
            assert "left_center" in r["joints"]
            assert any("fake robot connected" in line["text"] for line in s.console()["lines"])
        finally:
            await s.stop()
    run(main())


def test_set_mode_rejects_unknown_modes():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            assert s.set_mode("teleop")["mode"] == "teleop"
            with pytest.raises(SessionError, match="disabled, auto, teleop, test"):
                s.set_mode("autonomous")
            s.set_joystick(0, axes=[0.0, -0.5])
            with pytest.raises(SessionError, match="0-5"):
                s.set_joystick(7, axes=[0.0])
        finally:
            await s.stop()
    run(main())


def test_hal_lists_devices():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            assert "SimDevice/ScrimmageLockstep" in s.hal("lockstep")
        finally:
            await s.stop()
    run(main())


def test_robot_stall_is_reported(tmp_path):
    cfg = fake_config(tmp_path, '["{python}", "fake_robot_main.py", "--lockstep", "--stall-after", "10"]',
                      "[session]\nstep_timeout = 0.5\n")

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            t0 = time.monotonic()
            with pytest.raises(SessionError, match="stopped responding at t=0.05"):
                await s.advance(1.0)
            assert time.monotonic() - t0 < 3
            assert s.state()["t"] == pytest.approx(0.055)   # usable afterwards
        finally:
            await s.stop()
    run(main())


def test_robot_crash_is_reported_and_restart_recovers(tmp_path):
    cfg = fake_config(tmp_path, '["{python}", "fake_robot_main.py", "--lockstep", "--exit-after", "10"]')

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            with pytest.raises(SessionError, match=r"exited \(code 3\)") as e:
                await s.advance(1.0)
            assert any("Traceback" in line for line in e.value.console)
            st = await s.restart_robot()
            assert st["lockstep"] is True and st["t"] == 0.0
            assert (await s.advance(0.02))["t"] == pytest.approx(0.02)
        finally:
            await s.stop()
    run(main())


def test_start_fails_when_robot_never_connects(tmp_path):
    cfg = fake_config(tmp_path, '["{python}", "-c", "import time; print(\'sleeping\', flush=True); time.sleep(60)"]',
                      "")
    cfg.write_text(cfg.read_text().replace("start_timeout = 15", "start_timeout = 1"))

    async def main():
        s = Session(cfg, nt=False)
        with pytest.raises(SessionError, match="didn't connect within 1 s") as e:
            await s.start()
        assert "sleeping" in e.value.console
        assert s.robot is None or not s.robot.running
    run(main())


def test_start_fails_when_robot_exits_immediately(tmp_path):
    cfg = fake_config(tmp_path, '["{python}", "-c", "print(\'bad config\'); raise SystemExit(4)"]')

    async def main():
        s = Session(cfg, nt=False)
        with pytest.raises(SessionError, match=r"exited \(code 4\) before it connected") as e:
            await s.start()
        assert "bad config" in e.value.console
    run(main())


def test_realtime_fallback_session(tmp_path, monkeypatch):
    """Without the lockstep extension the session runs in real time: simulated time never gets ahead of the wall
    clock since the robot connected (when it starts at 0). An advance alone can take less wall time than it
    simulates: physics a busy machine left behind (up to MAX_LAG) catches up faster than real time."""
    monkeypatch.setattr(bridge_mod, "DETECT_WINDOW", 0.2)
    cfg = fake_config(tmp_path, '["{python}", "fake_robot_main.py"]')
    connected = []
    restart = bridge_mod.Bridge._restart_clock
    monkeypatch.setattr(bridge_mod.Bridge, "_restart_clock",
                        lambda self: (connected.append(time.monotonic()), restart(self))[-1])

    async def main():
        s = Session(cfg, nt=False)
        try:
            st = await s.start()
            assert st["lockstep"] is False
            t0 = s.t
            r = await s.advance(0.3)
            wall = time.monotonic() - connected[-1]
            # t is rounded to the step: 0.201 + 0.3 is 0.5010000000000002 in floating point, and t reads 0.501
            assert r["t"] >= t0 + 0.3 - 1e-9
            assert r["t"] <= wall + 0.002, (r["t"], wall)   # (a pass ends on a whole 1 ms step)
        finally:
            await s.stop()
    run(main())


def test_attach_mode_waits_for_a_robot_program():
    async def main():
        port = next_port()
        s = Session(FAKE, attach=True, port=port, nt=False)
        start = asyncio.create_task(s.start())
        await asyncio.sleep(0.3)
        async with FakeRobot(port, lockstep=True):
            st = await asyncio.wait_for(start, 10)
            assert st["lockstep"] is True and st["robot"] == "attached"
            assert (await s.advance(0.01))["t"] == pytest.approx(0.01)
            await s.stop()
    run(main())


def test_an_attach_mode_reset_sends_the_resets_game_data():
    """B20: an attached robot program isn't restarted, so its HAL keeps the game data it last heard: a reset after
    the game data was cleared (as a scenario clears it before its reset) sends the empty game data, rather than
    dropping the key and leaving the robot with the old "R"."""
    async def main():
        port = next_port()
        s = Session(FAKE, attach=True, port=port, nt=False)
        start = asyncio.create_task(s.start())
        await asyncio.sleep(0.3)
        async with FakeRobot(port, lockstep=True) as robot:
            await asyncio.wait_for(start, 10)
            s.set_match(game_data="R")
            await s.advance(0.05)

            def heard(mark):
                return [m["data"][">game_data"] for m in robot.frames[mark:]
                        if (m["type"], m["device"]) == ("DriverStation", "") and ">game_data" in m["data"]]
            assert heard(0)[-1] == "R"
            s.bridge.ds.set_game_data("")       # what run_scenario does before its reset
            await s.restart_robot()
            mark = len(robot.frames)
            await s.advance(0.05)
            assert heard(mark) and set(heard(mark)) == {""}
            await s.stop()
    run(main())


def test_session_without_robot_program_section(tmp_path):
    cfg = tmp_path / "bare.toml"
    cfg.write_text(FAKE.read_text().split("[robot_program]")[0].replace('"models/', f'"{TESTS / "models"}/'))
    with pytest.raises(SessionError, match="no \\[robot_program\\]"):
        Session(cfg)


def test_overlapping_advances_are_serialized():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            assert s.bridge.host == "127.0.0.1"
            ticks: dict[str, list[float]] = {"a": [], "b": []}
            a = asyncio.create_task(s.advance(0.05, on_step=ticks["a"].append))
            await asyncio.sleep(0)
            b = asyncio.create_task(s.advance(0.05, on_step=ticks["b"].append))
            await asyncio.gather(a, b)
            assert s.t == pytest.approx(0.1)
            assert max(ticks["a"]) <= min(ticks["b"])   # one finished before the other began
        finally:
            await s.stop()
    run(main())


def test_sigkill_during_paced_advance_is_reported():
    async def main():
        s = Session(FAKE, speed=0.05, nt=False)
        try:
            await s.start()
            adv = asyncio.create_task(s.advance(5.0))
            await asyncio.sleep(0.5)
            os.killpg(s.robot.proc.pid, signal.SIGKILL)
            with pytest.raises(SessionError, match="exited"):
                await asyncio.wait_for(adv, 10)
        finally:
            await s.stop()
    run(main())


def test_stop_twice_is_fine():
    async def main():
        s = Session(FAKE, nt=False)
        await s.start()
        await s.stop()
        await s.stop()
    run(main())


def test_stop_during_advance_ends_it_with_session_error():
    async def main():
        s = Session(FAKE, speed=0.05, nt=False)
        await s.start()
        adv = asyncio.create_task(s.advance(5.0))
        await asyncio.sleep(0.5)
        t0 = time.monotonic()
        await s.stop()
        with pytest.raises(SessionError):
            await asyncio.wait_for(adv, 3)
        assert time.monotonic() - t0 < 3
    run(main())


def test_calls_before_start_raise_session_error():
    s = Session(FAKE, nt=False)
    for call in (s.state, s.hal, s.console, lambda: s.set_mode("teleop"), lambda: s.set_joystick(0)):
        with pytest.raises(SessionError, match="isn't started"):
            call()
    with pytest.raises(SessionError, match="isn't started"):
        run(s.restart_robot())


def test_second_start_raises():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            with pytest.raises(SessionError, match="already been started"):
                await s.start()
        finally:
            await s.stop()
    run(main())


def test_pokes():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            assert s.set_pose(4.0, 2.0, 180.0)["pose"] == {"x": 4.0, "y": 2.0, "heading_deg": pytest.approx(180.0)}
            r = await s.push(400.0, 0.0, 0.3)
            assert r["pose"]["x"] > 4.02 and r["t"] == pytest.approx(0.3)
            assert s.move_body("obstacle", 6.0, 6.0)["t"] == pytest.approx(0.3)
            with pytest.raises(SessionError, match="obstacle"):
                s.move_body("nope", 0, 0)
            with pytest.raises(SessionError, match="duration_s"):
                await s.push(10.0, 0.0, 0.0)
            with pytest.raises(SessionError, match="x must be a finite"):
                s.set_pose(float("nan"), 0, 0)
            with pytest.raises(SessionError, match="force_n"):
                await s.push("x", 0, 0.1)
            with pytest.raises(SessionError, match="z must"):
                s.move_body("obstacle", 1, 1, float("inf"))
            assert not s.world.data.xfrc_applied[s.world.robot_body].any()
        finally:
            await s.stop()
    run(main())


def test_stopped_session_rejects_calls():
    async def main():
        s = Session(FAKE, nt=False)
        await s.start()
        await s.stop()
        with pytest.raises(SessionError, match="isn't started"):
            s.state()
        with pytest.raises(SessionError, match="isn't started"):
            s.set_pose(0, 0, 0)
        with pytest.raises(SessionError, match="isn't started"):
            await s.restart_robot()
    run(main())


def test_nt_reads_through_session(tmp_path, monkeypatch):
    ntcore = pytest.importorskip("ntcore")
    # srv stands in for the robot program's own NT server, so it isn't "another robot program" on the port
    monkeypatch.setattr(session_mod, "_listening", lambda host, port: False)
    cfg = fake_config(tmp_path, '["{python}", "fake_robot_main.py", "--lockstep"]', "[telemetry]\nnt_port = 5892\n")
    srv = ntcore.NetworkTableInstance.create()

    async def main(pub):
        s = Session(cfg)
        try:
            await s.start()
            for _ in range(50):
                if s.nt_value("/SmartDashboard/Battery V") is not None:
                    break
                await asyncio.sleep(0.1)
            else:
                pytest.fail("/SmartDashboard/Battery V never arrived over NetworkTables within 5 s")
            assert s.nt_get(["/SmartDashboard/Battery V", "/missing"]) == {
                "t": 0.0, "values": {"/SmartDashboard/Battery V": 12.1}, "missing": ["/missing"]}
            assert any(t["name"] == "/SmartDashboard/Battery V" for t in s.nt_list("/SmartDashboard")["topics"])
        finally:
            await s.stop()
    pub = None
    try:
        srv.startServer(str(tmp_path / "nt.json"), "", 0, 5892)
        pub = srv.getDoubleTopic("/SmartDashboard/Battery V").publish()  # a dropped publisher unpublishes
        pub.set(12.1)
        run(main(pub))
    finally:
        if pub is not None:     # before the instance goes: a freed stale handle would hit a later instance's
            pub.close()
        srv.stopServer()
        ntcore.NetworkTableInstance.destroy(srv)


def test_nt_disabled_session_says_how_to_fix():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            with pytest.raises(SessionError, match="NetworkTables"):
                s.nt_list()
        finally:
            await s.stop()
    run(main())


def test_screenshot_and_contact_sheet():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            png = s.screenshot("top", width=320, height=200)
            assert png[:4] == b"\x89PNG"
            sheet = await s.contact_sheet(0.4, frames=4, view="follow", width=160, height=100)
            assert sheet[:4] == b"\x89PNG" and s.t == pytest.approx(0.4)
            with pytest.raises(SessionError, match="follow"):
                s.screenshot("sideways")
        finally:
            await s.stop()
    run(main())


def test_a_slow_picture_does_not_stall_the_event_loop(monkeypatch):
    """Pictures are drawn on the render thread from a snapshot: the loop keeps sending the keep-alives that hold
    the robot paused (it frees its clock after 2 s without them), however slow GL is (CI's software OSMesa)."""
    from scrimmage import render as render_mod
    orig = render_mod.Renderer.render

    def slow(self, *a, **kw):
        time.sleep(0.6)
        return orig(self, *a, **kw)
    monkeypatch.setattr(render_mod.Renderer, "render", slow)

    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            ticks = 0

            async def ticker():
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.02)
                    ticks += 1
            task = asyncio.create_task(ticker())
            img = await s.render_async("top", width=160, height=100)
            task.cancel()
            assert img.shape == (100, 160, 3) and ticks >= 15
            png = await s.screenshot_png("follow", width=160, height=100)   # the sync path still works too
            assert png[:4] == b"\x89PNG" and s.render("top", width=80, height=50).shape == (50, 80, 3)
        finally:
            await s.stop()
    run(main())


def test_picture_arguments_are_validated():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            for kw in ({"width": 0}, {"height": 5000}):
                with pytest.raises(SessionError, match=str(list(kw.values())[0])):
                    s.screenshot("top", **kw)
            with pytest.raises(SessionError, match="lookat"):
                s.screenshot("free", camera={"lookat": [1, 2]})
            with pytest.raises(SessionError, match="unknown camera key"):
                s.screenshot("free", camera={"lookat": [1, 2, 0], "zoom": 1})
            for args in ((0, 4), (0.4, 0), ("x", 4), (0.4, 17), (0.4, 2.5)):
                with pytest.raises(SessionError, match="seconds|frames"):
                    await s.contact_sheet(*args)
            with pytest.raises(SessionError, match="width"):
                await s.contact_sheet(0.4, 2, width=0)
            assert s.t == 0.0
        finally:
            await s.stop()
    run(main())


def test_start_goes_on_when_networktables_never_connects(tmp_path, caplog):
    pytest.importorskip("ntcore")
    cfg = fake_config(tmp_path, '["{python}", "fake_robot_main.py", "--lockstep"]', "[telemetry]\nnt_port = 5893\n")

    async def main():
        s = Session(cfg)  # NetworkTables on, but nothing serves port 5893
        try:
            t0 = time.monotonic()
            st = await s.start()
            assert st["lockstep"] and time.monotonic() - t0 < 9
            assert s.bridge.nt_link is not None and not s.bridge.nt_link.connected
            assert (await s.advance(0.1))["t"] == pytest.approx(0.1)
        finally:
            await s.stop()
    run(main())
    assert "NetworkTables didn't connect" in caplog.text


def test_watchdog_release_is_reported(tmp_path):
    cfg = fake_config(tmp_path, '["{python}", "fake_robot_main.py", "--lockstep", "--release-after", "1"]')

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            await wait_until(lambda: s.bridge.lockstep_released_at is not None)
            assert s.state()["lockstep_released_at"] == 0.0 and s.status()["lockstep_released_at"] == 0.0
            r = await s.advance(0.02)
            assert "ran free at t=0.000" in r["warning"]
        finally:
            await s.stop()
    run(main())


def test_contact_sheet_size_is_capped():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            with pytest.raises(SessionError, match="67.1 megapixels"):
                await s.contact_sheet(1.0, frames=16, width=4096, height=1024)
            assert s.t == 0.0  # refused before advancing
        finally:
            await s.stop()
    run(main())


def test_start_refuses_a_networktables_port_in_use(tmp_path, monkeypatch):
    import socket
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        cfg = fake_config(tmp_path, '["{python}", "fake_robot_main.py", "--lockstep"]',
                          f"[telemetry]\nnt_port = {port}\n")

        async def main():
            s = Session(cfg)
            with pytest.raises(SessionError, match=f"NetworkTables port {port} is already in use"):
                await s.start()
            assert s.robot is None  # nothing was launched
            monkeypatch.setattr(session_mod, "ATTACH_TIMEOUT", 0.3)
            att = Session(cfg, attach=True, port=0)  # attach mode expects the user's robot there: no check
            with pytest.raises(SessionError, match="no robot program connected"):
                await att.start()
        run(main())


def test_advance_while_the_bridge_stops_says_so():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            s.bridge._sync_viewer = s.bridge.done.set  # as when the viewer window is closed mid-step
            with pytest.raises(SessionError, match="the session is stopping"):
                await s.advance(0.1)
        finally:
            await s.stop()
    run(main())


def test_session_requires_built_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "empty-cache"))
    with pytest.raises(SessionError, match="scrimmage java build"):
        asyncio.run(Session(TESTS / "java_probe" / "scrimmage.toml", nt=False).start())


PHOENIX_MOTOR = """
[[motor]]
name = "phoenix"
ctre = "TalonFX:1"
joint = "left_center"
motor = "KrakenX60"
gear_ratio = 10.0
"""


REASON = "Phoenix 6 simulation runs on the wall clock, so this robot runs in real time (not lockstep)"
UNPAUSABLE = "Phoenix 6 devices keep running between calls: this session can't pause them"


def test_phoenix_robot_runs_in_real_time_and_says_it_cant_pause(tmp_path):
    cfg = fake_config(tmp_path, '["{python}", "fake_robot_main.py", "--lockstep", "--phoenix"]', PHOENIX_MOTOR)

    async def main():
        s = Session(cfg, nt=False)  # speed None: as fast as possible, but Phoenix runs on the wall clock
        try:
            await s.start()
            await wait_until(lambda: s.bridge.mode == "realtime")
            st = s.status()
            assert st["realtime_reason"] == REASON and st["unpausable"] is True and not st["lockstep"]
            assert UNPAUSABLE in st["warnings"] and st["lockstep_released_at"] is None
            t0 = time.monotonic()
            r = await s.advance(0.5)
            assert time.monotonic() - t0 >= 0.45
            assert r["realtime_reason"] == REASON and r["unpausable"] is True
            assert r["warning"] == UNPAUSABLE                       # said once, in the first advance
            assert r["foreign_writes"] == [] and r["agent_summary"] is None
            t1 = s.t
            await asyncio.sleep(0.3)
            assert s.t > t1 + 0.15                                  # physics kept going between calls
            assert "warning" not in await s.advance(0.05)
            assert s.phoenix_speed is None or s.phoenix_speed > 0.8   # this computer keeps up
        finally:
            await s.stop()
    run(main())


def test_a_session_without_phoenix_devices_has_no_unpausable_warning():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            r = await s.advance(0.05)
            assert r["unpausable"] is False and "warning" not in r
            assert s.status()["unpausable"] is False and UNPAUSABLE not in s.status()["warnings"]
        finally:
            await s.stop()
    run(main())


def test_fast_mode_session_with_phoenix_devices_reports_them(tmp_path):
    cfg = fake_config(tmp_path, '["{python}", "fake_robot_main.py", "--lockstep", "--phoenix"]', PHOENIX_MOTOR)

    async def main():
        s = Session(cfg, nt=False, sim_mode="fast")
        try:
            await s.start()
            await wait_until(lambda: s.bridge.mode == "realtime")
            msg = ("fast mode, but the robot program created Phoenix 6 devices (Talon FX (v6)[1]): they force real "
                   "time; use the fast-mode IO for them")
            assert msg in s.status()["warnings"]
        finally:
            await s.stop()
    run(main())


def test_status_lists_foreign_writes_and_the_agent_summary(tmp_path):
    cfg = fake_config(tmp_path, '["{python}", "fake_robot_main.py", "--lockstep", "--phoenix", "--foreign-after", "4"]',
                      PHOENIX_MOTOR)

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            await wait_until(lambda: s.bridge.mode == "realtime")
            for _ in range(20):                        # the write comes from the robot's free-running loop
                await s.advance(0.05)
                if s.status()["foreign_writes"]:        # status() judges it
                    break
            fw = s.status()["foreign_writes"]
            assert [(f["device"], f["key"], f["value"]) for f in fw] == [
                ("Talon FX (v6)[1]/Rotor Sensor", ">rawPositionInput", 5.0)]
            assert fw[0]["count"] == 1 and 0 < fw[0]["first_t"] <= s.t
            assert s.agent_state() == "missing"
            line = "scrimmage agent: ignored 3 robot-side sim writes (TalonFXSimState.setRawRotorPosition from x.y)"
            s.robot._add(b"scrimmage agent: loaded")
            assert s.agent_state() == "loaded"
            s.robot._add(b"scrimmage agent: ignored 1 robot-side sim writes (older)")
            s.robot._add(line.encode())
            for _ in range(6000):  # more than the console keeps: the summary is remembered, not searched for
                s.robot._add(b"other output")
            assert s.status()["agent_summary"] == line and s.state()["agent_summary"] == line
        finally:
            await s.stop()
    run(main())


def test_connect_timeout_hint_for_gradlew_without_the_init_script(tmp_path):
    hint = session_mod.connect_timeout_hint(["./gradlew", "simulateJava"], tmp_path)
    assert "./gradlew build" in hint and "scrimmage.init.gradle" in hint
    assert "scrimmage.init.gradle" not in session_mod.connect_timeout_hint(
        ["./gradlew", "-I", "/x/scrimmage.init.gradle", "simulateJava"], tmp_path)
    assert session_mod.connect_timeout_hint(["python", "robot.py"], tmp_path) == ""


def test_serve_launch_requires_the_built_agent_too(tmp_path, monkeypatch, capsys):
    from scrimmage.__main__ import main
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "empty-cache"))
    with pytest.raises(SystemExit) as e:
        main(["serve", str(TESTS / "java_probe" / "scrimmage.toml"), "--launch"])
    assert e.value.code == 2 and "scrimmage java build" in capsys.readouterr().err


def test_serve_launch_checks_the_lockstep_version_for_robotpy(tmp_path, monkeypatch, capsys):
    from importlib import metadata
    from scrimmage.__main__ import main
    monkeypatch.setattr(metadata, "version", lambda name: "0.4.0")
    cfg = fake_config(tmp_path, ROBOTPY_COMMAND, "")
    with pytest.raises(SystemExit) as e:
        main(["serve", str(cfg), "--launch"])
    err = capsys.readouterr().err
    # the install command works from any folder: the robot's Python and the checkout's lockstep folder, in full
    assert e.value.code == 2 and f" -m pip install {ROOT / 'lockstep'}`" in err, err


def test_rev_check_detail_names_the_robotpy_helper_for_robotpy(tmp_path):
    from scrimmage.scenario_runner import Report, _implicit

    def detail(command):
        s = Session(fake_config(tmp_path, command, REV_MOTOR), nt=False)
        s.bridge = types.SimpleNamespace(settle_foreign_writes=lambda: None, foreign_writes={})
        s.robot = types.SimpleNamespace(running=True, returncode=None, since=lambda i: [], agent_summary=None,
                                        rev_state=None, agent_state=lambda: "loaded")
        report = Report("s", "")
        _implicit(s, types.SimpleNamespace(require_lockstep=False), report, 0)
        (c,) = [c for c in report.implicit if c.check == "REV simulation available"]
        return c.message
    assert detail(ROBOTPY_COMMAND) == "the scrimmage REV helper reported no robotpy-rev problem"
    assert detail('["{python}", "fake_robot_main.py", "--lockstep"]') == \
        "the scrimmage agent reported no REVLib problem"


def test_status_lists_slot_warnings(tmp_path):
    from parts_fake import WARNING, parts_config

    async def main():
        s = Session(parts_config(tmp_path), nt=False)
        try:
            st = await s.start()
            assert WARNING in st["warnings"] and WARNING in s.status()["warnings"]
        finally:
            await s.stop()
    run(main())


REV_MOTOR = """
[[motor]]
name = "spark"
rev = "SparkMax:3"
joint = "left_center"
"""


def test_rev_unavailable_reaches_status_and_fails_a_scenario_check(tmp_path):
    from scrimmage.scenario_runner import Report, _implicit
    cfg = fake_config(tmp_path, '["{python}", "fake_robot_main.py", "--lockstep"]', REV_MOTOR)
    scenario = types.SimpleNamespace(require_lockstep=False)

    def rev_checks():
        report = Report("s", "")
        _implicit(s, scenario, report, 0)
        return [c for c in report.implicit if c.check == "REV simulation available"]

    async def main():
        nonlocal s
        s = Session(cfg, nt=False)
        try:
            await s.start()
            assert s.status()["rev_state"] is None and s.rev_problem() is None
            (ok,) = rev_checks()
            assert ok.passed
            s.robot._add(b"scrimmage agent: REV sim unavailable: no class com.revrobotics.sim.SparkMaxSim")
            assert s.status()["rev_state"] == "unavailable: no class com.revrobotics.sim.SparkMaxSim"
            (bad,) = rev_checks()
            assert not bad.passed
            assert "no class com.revrobotics.sim.SparkMaxSim" in bad.message
            assert "update REVLib to 2026.0.5 or later (vendordeps/REVLib.json)" in bad.message
            assert s.rev_problem() == bad.message
        finally:
            await s.stop()
    s = None
    run(main())


def test_no_rev_check_without_rev_bindings(tmp_path):
    from scrimmage.scenario_runner import Report, _implicit
    cfg = fake_config(tmp_path, '["{python}", "fake_robot_main.py", "--lockstep"]')

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            s.robot._add(b"scrimmage agent: REV sim unavailable: whatever")
            assert s.rev_problem() is None      # nothing bound: not scrimmage's problem
            report = Report("s", "")
            _implicit(s, types.SimpleNamespace(require_lockstep=False), report, 0)
            assert not [c for c in report.implicit if c.check == "REV simulation available"]
        finally:
            await s.stop()
    run(main())


REV_MOTOR = """
[[motor]]
name = "spark"
rev = "SparkMax:3"
joint = "left_center"
motor = "NEO"
gear_ratio = 10.0
"""
ROBOTPY_COMMAND = '["{python}", "-m", "robotpy", "sim", "--nogui", "--ws-client", "--scrimmage-lockstep"]'


@pytest.mark.parametrize("installed", [None, "0.1.0", "0.5.0"])
@pytest.mark.parametrize("extra,why", [(REV_MOTOR, "binds REV SPARKs"), ("", "runs a RobotPy robot with --scrimmage-lockstep")])
def test_robotpy_lockstep_config_refuses_old_or_missing_lockstep(tmp_path, monkeypatch, installed, extra, why):
    from importlib import metadata

    def fake_version(name):
        assert name == "scrimmage-lockstep"
        if installed is None:
            raise metadata.PackageNotFoundError(name)
        return installed
    monkeypatch.setattr(metadata, "version", fake_version)
    cfg = fake_config(tmp_path, ROBOTPY_COMMAND, extra)
    install = r"-m pip install " + re.escape(str(ROOT / "lockstep"))
    with pytest.raises(SessionError, match=why + r".*0\.6\.0 or later.*" + install):
        asyncio.run(Session(cfg, nt=False).start())


@pytest.mark.parametrize("command,extra", [
    ('["{python}", "-m", "robotpy", "sim", "--nogui", "--ws-client"]', ""),  # RobotPy in real time, no REV bindings
    ('["{python}", "fake_robot_main.py", "--lockstep"]', REV_MOTOR),         # not a RobotPy command
])
def test_lockstep_version_is_not_checked_without_robotpy_lockstep_or_rev(tmp_path, monkeypatch, command, extra):
    from importlib import metadata
    monkeypatch.setattr(metadata, "version", lambda name: "0.1.0")
    assert session_mod.lockstep_version_problem(
        Session(fake_config(tmp_path, command, extra), nt=False).config) is None


@pytest.mark.parametrize("extra", [REV_MOTOR, ""])
def test_lockstep_version_ok_at_0_6_0(tmp_path, monkeypatch, extra):
    from importlib import metadata
    monkeypatch.setattr(metadata, "version", lambda name: "0.6.0")
    cfg = Session(fake_config(tmp_path, ROBOTPY_COMMAND, extra), nt=False).config
    assert session_mod.lockstep_version_problem(cfg) is None
    monkeypatch.setattr(metadata, "version", lambda name: "0.10.1")
    assert session_mod.lockstep_version_problem(cfg) is None


@pytest.fixture
def windows_x64(monkeypatch):
    import platform
    monkeypatch.setattr(session_mod.sys, "platform", "win32")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")


@pytest.mark.parametrize("command", [ROBOTPY_COMMAND, '["./gradlew", "-I", "{scrimmage_java}/scrimmage.init.gradle", '
                                                      '"simulateJava"]'])
def test_on_windows_an_older_lockstep_runs_in_real_time_with_a_note(tmp_path, monkeypatch, windows_x64, command):
    """Windows x64 asks for 0.8.0, the first with the prebuilt extension; an older one (not too old for Linux) doesn't
    stop the session, it says the robot runs in real time and how to update."""
    from importlib import metadata
    monkeypatch.setattr(metadata, "version", lambda name: "0.7.0")
    monkeypatch.setattr(java_tools, "agent_missing_message", lambda *a, **k: None)  # no built agent in CI
    cfg = Session(fake_config(tmp_path, command), nt=False).config
    assert session_mod.lockstep_version_problem(cfg) is None
    note = session_mod.lockstep_update_note(cfg)
    assert note.startswith(f"{cfg.path} runs in real time: scrimmage-lockstep 0.7.0 has no lockstep for Windows. "
                           "Update it to 0.8.0 or later to run faster than real time: `") and "pip install" in note
    monkeypatch.setattr(metadata, "version", lambda name: "0.8.0")
    assert session_mod.lockstep_update_note(cfg) is None


def test_the_windows_note_is_for_windows_x64_lockstep_robots_only(tmp_path, monkeypatch):
    import platform
    from importlib import metadata
    monkeypatch.setattr(metadata, "version", lambda name: "0.7.0")
    cfg = Session(fake_config(tmp_path, ROBOTPY_COMMAND), nt=False).config
    for plat, machine in (("linux", "x86_64"), ("win32", "ARM64"), ("darwin", "arm64")):
        monkeypatch.setattr(session_mod.sys, "platform", plat)
        monkeypatch.setattr(platform, "machine", lambda: machine)
        assert session_mod.lockstep_update_note(cfg) is None, plat
    monkeypatch.setattr(session_mod.sys, "platform", "win32")
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    real_time = '["{python}", "-m", "robotpy", "sim", "--nogui", "--ws-client"]'
    assert session_mod.lockstep_update_note(Session(fake_config(tmp_path, real_time), nt=False).config) is None


def test_session_sim_mode_sets_the_robots_env():
    s = Session(FAKE, sim_mode="fast")
    assert s.config.robot_program.env["SCRIMMAGE_MODE"] == "fast"
    assert "SCRIMMAGE_MODE" not in Session(FAKE).config.robot_program.env


def test_session_sim_mode_leaves_the_callers_config_alone():
    cfg = session_mod.load_config(FAKE)
    s = Session(cfg, sim_mode="fast")
    assert s.config.robot_program.env["SCRIMMAGE_MODE"] == "fast" and s.config.robot_program.sim_mode == "fast"
    assert "SCRIMMAGE_MODE" not in cfg.robot_program.env and cfg.robot_program.sim_mode == "phoenix"
    assert "SCRIMMAGE_MODE" not in Session(cfg).config.robot_program.env   # a later session without the flag


def test_attached_session_warns_that_sim_mode_does_nothing(caplog):
    with caplog.at_level("WARNING", logger="scrimmage"):
        Session(FAKE, attach=True, sim_mode="fast")
    assert [r.getMessage() for r in caplog.records if "has no effect" in r.getMessage()] == [
        "sim mode 'fast' has no effect: scrimmage doesn't launch the robot program here, so it can't set "
        "SCRIMMAGE_MODE; set it in the robot program's environment yourself"]


def test_serve_without_launch_warns_that_sim_mode_does_nothing(monkeypatch, caplog):
    import scrimmage.__main__ as cli

    class Stop(Exception):
        pass

    def build(config):
        raise Stop

    monkeypatch.setattr(cli, "build_world", build)
    for argv, warned in ((["serve", str(FAKE), "--sim-mode", "fast"], 1),
                         (["serve", str(FAKE), "--sim-mode", "fast", "--launch"], 0)):
        caplog.clear()
        with caplog.at_level("WARNING", logger="scrimmage"), pytest.raises(Stop):
            cli.main(argv)
        assert len([r for r in caplog.records if "has no effect" in r.getMessage()]) == warned


def test_serve_sim_mode_overrides_the_config(monkeypatch):
    import scrimmage.__main__ as cli

    class Stop(Exception):
        pass

    seen = []

    def build(config):
        seen.append(config.robot_program.env.get("SCRIMMAGE_MODE"))
        raise Stop

    monkeypatch.setattr(cli, "build_world", build)
    for argv in (["serve", str(FAKE), "--sim-mode", "fast"], ["serve", str(FAKE)]):
        with pytest.raises(Stop):
            cli.main(argv)
    assert seen == ["fast", None]
    with pytest.raises(SystemExit):
        cli.main(["serve", str(FAKE), "--sim-mode", "bogus"])


JAVA_COMMAND = '["./gradlew", "-I", "{scrimmage_java}/scrimmage.init.gradle", "simulateJava"]'


def test_java_robot_refuses_an_installed_old_lockstep(tmp_path, monkeypatch):
    """The init script loads whatever extension is installed; before 0.6.0 its clock starts at 0, where AdvantageKit's
    LoggedRobot quits at once."""
    from importlib import metadata
    cfg = session_mod.load_config(fake_config(tmp_path, JAVA_COMMAND))   # no Session: CI's Python jobs have no agent jar
    monkeypatch.setattr(metadata, "version", lambda name: "0.5.0")
    problem = session_mod.lockstep_version_problem(cfg)
    assert problem and "runs a Java robot" in problem and "0.6.0 or later (installed: 0.5.0)" in problem
    monkeypatch.setattr(metadata, "version", lambda name: "0.6.0")
    assert session_mod.lockstep_version_problem(cfg) is None

    def missing(name):
        raise metadata.PackageNotFoundError(name)
    monkeypatch.setattr(metadata, "version", missing)
    assert session_mod.lockstep_version_problem(cfg) is None   # no extension: the init script runs it in real time


PH_VALVE = """
[[part]]
name = "cyl"
stock = "cylinder"
ids = { valve = "PH:4,5" }
"""


@pytest.mark.parametrize("command", [ROBOTPY_COMMAND, JAVA_COMMAND,
                                     '["{python}", "-m", "robotpy", "sim", "--nogui", "--ws-client"]'])
@pytest.mark.parametrize("valve", [PH_VALVE, PH_VALVE.replace('"PH:4,5"', "{ ph = 2, channel = 1 }")])
def test_valves_on_a_pneumatic_hub_need_lockstep_0_7_0(tmp_path, monkeypatch, command, valve):
    """Only the 0.7.0 extension relays a REV Pneumatic Hub, so a RobotPy or Java robot with a valve on one needs it
    (installed, even for Java); other robots keep the 0.6.0 floor."""
    from importlib import metadata
    cfg = session_mod.load_config(fake_config(tmp_path, command, valve))
    monkeypatch.setattr(metadata, "version", lambda name: "0.6.0")
    problem = session_mod.lockstep_version_problem(cfg)
    assert problem and "binds valves on a REV Pneumatic Hub" in problem
    assert "0.7.0 or later (installed: 0.6.0)" in problem and f"pip install {ROOT / 'lockstep'}" in problem
    monkeypatch.setattr(metadata, "version", lambda name: "0.7.0")
    assert session_mod.lockstep_version_problem(cfg) is None

    def missing(name):
        raise metadata.PackageNotFoundError(name)
    monkeypatch.setattr(metadata, "version", missing)
    problem = session_mod.lockstep_version_problem(cfg)
    assert problem and "binds valves on a REV Pneumatic Hub" in problem and "isn't installed" in problem


@pytest.mark.parametrize("command", [ROBOTPY_COMMAND, JAVA_COMMAND])
def test_valves_on_a_pcm_keep_the_0_6_0_floor(tmp_path, monkeypatch, command):
    from importlib import metadata
    cfg = session_mod.load_config(fake_config(tmp_path, command, PH_VALVE.replace("PH:", "PCM:")))
    monkeypatch.setattr(metadata, "version", lambda name: "0.6.0")
    assert session_mod.lockstep_version_problem(cfg) is None


def test_lockstep_install_command_quotes_for_cmd_on_windows(monkeypatch):
    """The "isn't installed" errors send Windows users to this command: cmd.exe needs double quotes, not shlex's."""
    monkeypatch.setattr(session_mod.sys, "platform", "win32")
    cmd = session_mod.lockstep_install(r"C:\Program Files\Python313\python.exe")
    assert cmd.startswith('"C:\\Program Files\\Python313\\python.exe" -m pip install ')
    assert "'" not in cmd
