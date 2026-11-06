"""The real Driver Station driving a real RobotPy program (sub-project 28): tests/fake_ds.py plays the FRC Driver
Station app toward the robot's DS socket extension (`robotpy sim --ds-socket`, added by scrimmage), and
tests/match_probe_py prints what the robot reads. scrimmage sends no DS frames and shows the robot's own reports."""

import asyncio
import json
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("wpilib")
pytest.importorskip("halsim_ws")
pytest.importorskip("halsim_ds_socket")
pytest.importorskip("scrimmage_lockstep")

from fake_ds import FakeDS, skip_if_ds_ports_taken  # noqa: E402
from scrimmage.driverstation import REAL_DS_DRIVING, REAL_DS_WAITING  # noqa: E402
import scrimmage.session  # noqa: E402
from scrimmage.session import NOT_LISTENING, Session, SessionError  # noqa: E402

TESTS = Path(__file__).resolve().parent
PROBE = TESTS / "match_probe_py"
MATCH = re.compile(r"match data='([^']*)' alliance=(\w+) location=(\w+) fms=(\w+)")
MODE = re.compile(r"ds mode=(\w+) axis0=(\S+)")

# The config's [match] says blue1 with no FMS: a real-DS session ignores it (D4), so red2 and FMS come from the DS.
CONFIG = f"""
[model]
timestep = 0.001

[lockstep]
step = 0.005

[robot]
start_pose = [3.0, 4.0, 0.0]

[robot_program]
command = ["{{python}}", "-m", "robotpy", "sim", "--nogui", "--ws-client", "--scrimmage-lockstep"]
cwd = "{PROBE}"

[telemetry]
nt = false

[[part]]
name = "drive"
stock = "chassis"
length = 0.66
width = 0.6
mass = 47.0
frame_height = 0.1

[match]
station = "blue1"
fms = false
"""


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    """A held real-DS session; the fake DS enables teleop at red2 with the FMS and axis 0 at 0.5, then sends game data
    "B"; 3 s; then the DS stops, and 0.5 s and 1.5 s later what scrimmage shows is recorded."""
    skip_if_ds_ports_taken()
    cfg = tmp_path_factory.mktemp("real_ds_py") / "real_ds_probe.toml"
    cfg.write_text(CONFIG)

    async def main():
        s = Session(cfg, real_ds=True, nt=False)
        ds = FakeDS()
        sent = []
        try:
            await s.start()
            real = s.bridge._send

            async def spy(frames):
                sent.extend(frames)
                await real(frames)
            s.bridge._send = spy
            out = {"before": s.state()["driver_station"]}
            ds.set(True, "teleop", "red2", fms=True)
            ds.set_joystick(0, [0.5], [])
            ds.start()
            await asyncio.sleep(0.5)
            ds.send_game_data("B")
            await s.advance(3.0)
            out |= {"state": s.state(), "hal": s.hal("DriverStation").get("DriverStation/", {}),
                   "replies": ds.replies(), "sent": [json.loads(f)["type"] for f in sent]}
            # only after the robot printed teleop output: the check binds UDP 1110, safe once the robot has bound it
            out["socket_problem"] = scrimmage.session.ds_socket_problem()
            try:
                s.set_mode("auto")
                out["refused"] = None
            except SessionError as e:
                out["refused"] = str(e)
            ds.stop()
            await asyncio.sleep(0.5)
            out["just_stopped"] = s.state()
            await asyncio.sleep(1.0)
            out["after_stop"] = s.state()
            out["hal_after_stop"] = s.hal("DriverStation").get("DriverStation/", {})
            text = "\n".join(line["text"] for line in s.console(0)["lines"])
            out["console"] = text
            out["warnings"] = s.status()["warnings"]
            return out
        finally:
            ds.stop()
            await s.stop()
    return asyncio.run(main())


def test_a_listening_robot_gets_no_not_listening_note(run):
    assert run["socket_problem"] is None
    assert NOT_LISTENING not in run["warnings"]


def test_the_robot_reads_the_real_ds(run):
    modes = MODE.findall(run["console"])
    assert ("teleop", "0.50") in modes, run["console"][-2000:]
    matches = MATCH.findall(run["console"])
    assert ("B", "kRed", "2", "True") in matches, run["console"][-2000:]


def test_scrimmage_shows_the_real_ds_and_sends_no_ds_frames(run):
    assert run["before"] == REAL_DS_WAITING
    st = run["state"]
    assert st["real_ds"] is True and st["driver_station"] == "connected", (st, run["hal"])
    assert st["mode"] == "teleop" and st["match"]["station"] == "red2" and st["match"]["fms"] is True
    assert not {"DriverStation", "Joystick"} & set(run["sent"]), set(run["sent"])
    assert run["replies"] > 0
    assert run["refused"] is not None and REAL_DS_DRIVING in run["refused"]


def test_game_data_is_not_reported_back(run):
    """D4: the robot's DriverStation reports carry no >game_data (halsim_ws doesn't send it), so scrimmage can't show
    it: the robot still reads it (test_the_robot_reads_the_real_ds)."""
    assert ">game_data" not in run["hal"] and "game_data" not in run["state"]["match"], run["hal"]


def test_after_the_ds_stops_the_state_follows_the_latest_report(run):
    """D5: "waiting for the Driver Station" exactly when the robot's latest report doesn't have >ds true. Seen with
    RobotPy 2026.2.2: the extension keeps >ds true after the DS stops and disables the robot 100 ms after the last
    packet, so scrimmage shows "connected" and disabled, and once no report has come for over 1 s, match.silent."""
    hal, after = run["hal_after_stop"], run["after_stop"]
    expected = "connected" if hal.get(">ds") is True else REAL_DS_WAITING
    assert after["driver_station"] == expected, (after, hal)
    # B21: the robot reports each packet; 1.5 s after the last, match.silent says the DS went quiet (0.5 s after, not
    # yet), while driver_station stays exactly "connected" (agents compare it)
    assert run["just_stopped"]["match"]["silent"] is False and after["match"]["silent"] is True
    assert run["just_stopped"]["driver_station"] == after["driver_station"] == "connected"
    # The behavior seen, pinned: if these fail, the extension changed, and docs/driver-station.md (what scrimmage
    # shows when the DS stops) must change with it.
    assert hal.get(">ds") is True, hal
    assert after["mode"] == "disabled", (after, hal)   # the extension's 100 ms auto-disable


@pytest.mark.usefixtures("ds_ports")
def test_serve_logs_what_the_real_ds_does(tmp_path):
    """D1: `serve --real-ds --launch` adds --ds-socket, and logs the DS's mode changes from the robot's reports."""
    cfg = tmp_path / "real_ds_probe.toml"
    cfg.write_text(CONFIG)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "-m", "scrimmage", "serve", str(cfg), "--real-ds", "--launch", "--no-nt",
                             "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out: list[str] = []
    reader = threading.Thread(target=lambda: out.extend(proc.stdout), daemon=True)
    reader.start()
    ds = FakeDS()

    seen = 0   # where the last match ended: each DS line is looked for only after the one before it

    def until(text: str, timeout: float = 60, ordered: bool = True) -> None:
        nonlocal seen
        start = seen if ordered else 0
        deadline = time.monotonic() + timeout
        while (at := "".join(out).find(text, start)) < 0 and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert at >= 0, "".join(out)[-3000:]
        seen = max(seen, at + len(text))
    try:
        until("robot program connected", ordered=False)   # these two come in either order
        until(REAL_DS_WAITING, ordered=False)
        ds.set(True, "teleop", "red2")
        ds.start()
        until("Driver Station: teleop enabled")
        ds.set(False, "auto", "red2")
        until("Driver Station: disabled")
        ds.set(True, "auto", "red2")
        until("Driver Station: auto enabled")
    finally:
        ds.stop()
        proc.terminate()
        proc.wait(timeout=20)
        reader.join(timeout=5)

