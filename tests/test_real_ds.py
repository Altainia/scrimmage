"""The real-Driver-Station switch without RobotPy: the config key, the report from the robot's DriverStation values,
--ds-socket, the port check, the refusals and the scenario rules (sub-project 28), with tests/fake_robot_main.py as
the robot program."""

import asyncio
import csv
import json
import socket
from pathlib import Path

import pytest

import scrimmage.session as session_mod
from fake_ds import joystick_tag
from fake_robot import wait_until
from scrimmage.config import ConfigError, load_config, load_config_text
from scrimmage.driverstation import REAL_DS_DRIVING, REAL_DS_WAITING, real_ds_report
from scrimmage.protocol import HalState
from scrimmage.robotcode import ROBOTPY_ARGS, with_ds_socket
from scrimmage.scenario import load_scenario
from scrimmage.scenario_runner import CONFIG_REAL_DS_NOTE, REAL_DS_REFUSED, run_scenario
from scrimmage.session import Session, SessionError

TESTS = Path(__file__).resolve().parent
FAKE = TESTS / "fake_robot.toml"
NOT_LISTENING = ("the robot program isn't listening for the Driver Station (UDP 1110): RobotPy needs --ds-socket; "
                 "Java needs wpi.sim.addDriverstation()")


@pytest.fixture(autouse=True)
def quick_nt_give_up(monkeypatch):
    monkeypatch.setattr(session_mod, "NT_CONNECT_TIMEOUT", 0.5)  # the fake robot serves no NetworkTables


def fake_config(tmp_path, extra: str = "", start_timeout: float = 15) -> Path:
    """tests/fake_robot.toml with absolute paths (so it can live in tmp_path), plus `extra`."""
    text = FAKE.read_text().replace('"models/', f'"{TESTS / "models"}/').replace('cwd = "."', f'cwd = "{TESTS}"')
    text = text.replace("start_timeout = 15", f"start_timeout = {start_timeout}")
    p = tmp_path / "robot.toml"
    p.write_text(text + "\n" + extra)
    return p


# ------------------------------------------------------------------------------------------------------ the config
def test_the_config_key_says_whether_the_real_ds_drives_by_default(tmp_path):
    assert load_config(FAKE).real_ds is False
    assert load_config_text("[driver_station]\nreal = true\n", tmp_path / "a.toml").real_ds is True
    assert load_config_text("[driver_station]\nreal = false\n", tmp_path / "a.toml").real_ds is False
    with pytest.raises(ConfigError, match=r"\[driver_station\] has no key 'mode'"):
        load_config_text("[driver_station]\nreal = true\nmode = 'teleop'\n", tmp_path / "a.toml")
    with pytest.raises(ConfigError, match=r"\[driver_station\] real must be true or false"):
        load_config_text("[driver_station]\nreal = 1\n", tmp_path / "a.toml")


# ------------------------------------------------------------------------------------------------------ the report
def hal(**ds) -> HalState:
    h = HalState()
    h.apply(json.dumps({"type": "DriverStation", "device": "", "data": ds}))
    return h


SPIKE = {">enabled": True, ">autonomous": False, ">test": False, ">estop": False, ">fms": True, ">ds": True,
         ">new_data": True, ">station": "red2", ">match_time": -1.0}


def test_the_report_waits_until_the_robot_says_a_ds_is_attached():
    for h in (HalState(), hal(**{**SPIKE, ">ds": False})):
        r = real_ds_report(h)
        assert r["connected"] is False and r["mode"] == "waiting" and "game_data" not in r


def test_the_report_follows_the_robots_driver_station_values():
    r = real_ds_report(hal(**SPIKE))
    assert r == {"connected": True, "mode": "teleop", "estop": False, "station": "red2", "fms": True,
                 "match_time": -1.0}
    assert real_ds_report(hal(**{**SPIKE, ">autonomous": True}))["mode"] == "auto"
    assert real_ds_report(hal(**{**SPIKE, ">test": True}))["mode"] == "test"
    assert real_ds_report(hal(**{**SPIKE, ">enabled": False, ">autonomous": True}))["mode"] == "disabled"
    stopped = real_ds_report(hal(**{**SPIKE, ">enabled": False, ">estop": True}))
    assert stopped["mode"] == "disabled" and stopped["estop"] is True
    assert real_ds_report(hal(**{**SPIKE, ">game_data": "B"}))["game_data"] == "B"


# ------------------------------------------------------------------------------------------------------ the launch
def test_ds_socket_is_added_once_to_a_robotpy_command_only():
    py = ["/venv/bin/python", *ROBOTPY_ARGS]
    added = with_ds_socket(py)
    assert added == ["/venv/bin/python", "-m", "robotpy", "sim", "--ds-socket", "--nogui", "--ws-client",
                     "--scrimmage-lockstep"]
    assert with_ds_socket(added) == added
    assert with_ds_socket(["python", "-m", "robotpy", "--main", "bot.py", "sim"])[-1] == "--ds-socket"
    for other in (["python", "fake_robot_main.py", "--lockstep"], ["./gradlew", "simulateJava"],
                  ["python", "-m", "robotpy", "deploy"]):
        assert with_ds_socket(other) == other


def test_with_the_switch_off_the_launch_is_unchanged(tmp_path):
    cfg = fake_config(tmp_path)
    plain = load_config(cfg).robot_program
    s = Session(cfg, nt=False)
    assert s.real_ds is False
    assert s.config.robot_program.command == plain.command and s.config.robot_program.env == plain.env


def test_with_the_switch_on_the_robot_is_told_to_listen(tmp_path):
    py = tmp_path / "py.toml"
    py.write_text('[robot_program]\ncommand = ["{python}", "-m", "robotpy", "sim", "--nogui", "--ws-client"]\n'
                  'env = { A = "1" }\n')
    cfg = load_config(py)
    s = Session(cfg, real_ds=True, nt=False)
    assert s.real_ds is True
    assert s.config.robot_program.command[-3:] == ["sim", "--ds-socket", "--nogui", "--ws-client"][-3:]
    assert s.config.robot_program.command.count("--ds-socket") == 1
    assert s.config.robot_program.env == {"A": "1", "SCRIMMAGE_DS_SOCKET": "1"}
    assert "--ds-socket" not in cfg.robot_program.command and cfg.robot_program.env == {"A": "1"}   # the caller's


def test_real_ds_none_takes_the_configs_default(tmp_path):
    cfg = fake_config(tmp_path, "[driver_station]\nreal = true\n")
    assert Session(cfg, nt=False).real_ds is True
    assert Session(cfg, nt=False, real_ds=False).real_ds is False
    assert "SCRIMMAGE_DS_SOCKET" not in Session(cfg, nt=False, real_ds=False).config.robot_program.env


# ------------------------------------------------------------------------------------------------------ the ports
@pytest.mark.parametrize("kind,port", [(socket.SOCK_DGRAM, 1110), (socket.SOCK_STREAM, 1740)])
def test_a_port_in_use_stops_the_start_before_the_robot_launches(tmp_path, kind, port):
    held = socket.socket(socket.AF_INET, kind)
    held.bind(("0.0.0.0", port))
    if kind == socket.SOCK_STREAM:
        held.listen()

    async def main():
        s = Session(fake_config(tmp_path), real_ds=True, nt=False)
        try:
            with pytest.raises(SessionError, match=f"{'UDP' if port == 1110 else 'TCP'} port {port} is already in use"):
                await s.start()
            assert s.robot is None
        finally:
            await s.stop()
    try:
        asyncio.run(main())
    finally:
        held.close()


# ------------------------------------------------------------------------------------------------------ a session
def test_a_real_ds_session_steps_back_and_refuses_ds_changes(tmp_path):
    """The fake robot loads the lockstep extension but no DS socket: the session releases lockstep at once, sends
    no DS frames, runs between calls, waits for a DS, refuses the DS's controls, and after the start timeout says the
    robot isn't listening."""
    log = tmp_path / "run.csv"
    cfg = fake_config(tmp_path, start_timeout=1)

    async def main():
        s = Session(cfg, real_ds=True, nt=False, log_path=log)
        sent = []
        try:
            await s.start()
            real = s.bridge._send

            async def spy(frames):
                sent.extend(frames)
                await real(frames)
            s.bridge._send = spy
            assert s.bridge.ds.active is False and s.bridge.real_ds is True
            await wait_until(lambda: s.bridge.mode == "realtime")
            st = s.state()
            assert st["real_ds"] is True and st["mode"] == "waiting" and st["lockstep"] is False
            assert st["driver_station"] == REAL_DS_WAITING and st["match"] == real_ds_report(s.bridge.hal)
            assert st["phoenix_unpausable"] is True and "real Driver Station" in st["realtime_reason"]
            for call in (lambda: s.set_mode("auto"), lambda: s.set_joystick(0, axes=[0.5]),
                         lambda: s.set_match(station="red1")):
                with pytest.raises(SessionError, match=REAL_DS_DRIVING):
                    call()
            r = await s.advance(0.3)
            assert "Phoenix" not in r.get("warning", "")
            t1 = s.t
            await asyncio.sleep(0.3)
            assert s.t > t1 + 0.15                       # it keeps running between calls
            await wait_until(lambda: NOT_LISTENING in s.status()["warnings"], timeout=5)
            types = {json.loads(f)["type"] for f in sent}
            assert not types & {"DriverStation", "Joystick"}, types
        finally:
            await s.stop()
    asyncio.run(main())
    rows = list(csv.DictReader(log.open()))
    assert rows and {r["mode"] for r in rows} == {"waiting"}


def test_a_session_without_the_switch_says_so(tmp_path):
    async def main():
        s = Session(fake_config(tmp_path), nt=False)
        try:
            await s.start()
            st = s.state()
            assert st["real_ds"] is False and "driver_station" not in st and st["mode"] == "disabled"
            assert NOT_LISTENING not in s.status()["warnings"]
        finally:
            await s.stop()
    asyncio.run(main())


# ------------------------------------------------------------------------------------------------------ scenarios
SCENARIO = """
[[step]]
mode = "auto"
duration = 0.2
  [[step.check]]
  always = { signal = "battery", min = 12.0 }
"""


def scenario(tmp_path, cfg: Path) -> Path:
    p = tmp_path / "s.toml"
    p.write_text(f'config = "{cfg}"\n' + SCENARIO)
    return p


def test_a_scenario_refuses_an_explicit_real_ds(tmp_path):
    sc = load_scenario(scenario(tmp_path, fake_config(tmp_path)))
    rep = asyncio.run(run_scenario(sc, tmp_path / "out", real_ds=True))
    assert rep.error == REAL_DS_REFUSED and rep.exit_code == 2
    assert not (tmp_path / "out" / "robot.log").exists()        # no robot program ran


def test_a_scenario_ignores_the_configs_real_ds_with_a_note(tmp_path):
    sc = load_scenario(scenario(tmp_path, fake_config(tmp_path, "[driver_station]\nreal = true\n")))
    rep = asyncio.run(run_scenario(sc, tmp_path / "out"))
    assert rep.exit_code == 0, rep.to_dict()
    assert CONFIG_REAL_DS_NOTE in rep.notes and rep.lockstep is True


def test_a_scenario_refuses_an_open_real_ds_session(tmp_path):
    cfg = fake_config(tmp_path)
    sc = load_scenario(scenario(tmp_path, cfg))

    async def main():
        s = Session(cfg, real_ds=True, nt=False)
        try:
            await s.start()
            return await run_scenario(sc, tmp_path / "out", session=s)
        finally:
            await s.stop()
    assert asyncio.run(main()).error == REAL_DS_REFUSED


# ------------------------------------------------------------------------------------------------------ MCP
def test_mcp_passes_the_switch_and_the_refusals_on(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from mcp import Client
    from scrimmage import mcp_server
    monkeypatch.setenv("SCRIMMAGE_NT_CONNECT_TIMEOUT", "0.3")
    cfg = str(fake_config(tmp_path))
    sc = str(scenario(tmp_path, Path(cfg)))

    async def main():
        async with Client(mcp_server.server) as c:
            try:
                r = await c.call_tool("run_scenario", {"path": sc, "real_ds": True})
                assert r.is_error and REAL_DS_REFUSED in r.content[0].text
                r = await c.call_tool("start_session", {"config": cfg, "real_ds": True})
                assert not r.is_error, r.content[0].text
                st = json.loads((await c.call_tool("get_state", {})).content[0].text)
                assert st["real_ds"] is True and st["driver_station"] == REAL_DS_WAITING
                for tool, args in (("set_mode", {"mode": "auto"}), ("set_joystick", {"port": 0, "axes": [0.5]}),
                                   ("set_match", {"station": "red1"})):
                    r = await c.call_tool(tool, args)
                    assert r.is_error and REAL_DS_DRIVING in r.content[0].text, (tool, r.content[0].text)
                r = await c.call_tool("start_session", {"config": cfg, "replace": True})   # None: the config's (off)
                assert not r.is_error and json.loads(r.content[0].text)["real_ds"] is False
            finally:
                await c.call_tool("stop_session", {})
    asyncio.run(main())


# ------------------------------------------------------------------------------------------------------ the fake DS
def test_the_fake_ds_joystick_tag_follows_dscommpacket():
    # size 8 (id + data), id 0x0C; 2 axes (0.5 -> 64, -1 -> -128); 10 buttons in 2 bytes, buttons 1 and 10 set; 0 POVs
    tag = joystick_tag([0.5, -1.0], [True] + [False] * 8 + [True])
    assert tag == bytes([8, 0x0C, 2, 64, 0x80, 10, 0x02, 0x01, 0])
