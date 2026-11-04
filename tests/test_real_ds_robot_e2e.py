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

from fake_ds import FakeDS  # noqa: E402
from scrimmage.driverstation import REAL_DS_DRIVING, REAL_DS_WAITING  # noqa: E402
from scrimmage.session import Session, SessionError  # noqa: E402

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
    "B"; 3 s; then the DS stops, and 1 s later what scrimmage shows is recorded."""
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
            try:
                s.set_mode("auto")
                out["refused"] = None
            except SessionError as e:
                out["refused"] = str(e)
            ds.stop()
            await asyncio.sleep(1.0)
            out["after_stop"] = s.state()
            out["hal_after_stop"] = s.hal("DriverStation").get("DriverStation/", {})
            text = "\n".join(line["text"] for line in s.console(0)["lines"])
            out["console"] = text
            return out
        finally:
            ds.stop()
            await s.stop()
    return asyncio.run(main())


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
    packet, so scrimmage shows "connected" and disabled."""
    hal, after = run["hal_after_stop"], run["after_stop"]
    expected = "connected" if hal.get(">ds") is True else REAL_DS_WAITING
    assert after["driver_station"] == expected, (after, hal)
    # The behavior seen, pinned: if these fail, the extension changed, and docs/driver-station.md (what scrimmage
    # shows when the DS stops) must change with it.
    assert hal.get(">ds") is True, hal
    assert after["mode"] == "disabled", (after, hal)   # the extension's 100 ms auto-disable


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

    def until(text: str, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while text not in "".join(out) and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert text in "".join(out), "".join(out)[-3000:]
    try:
        until("robot program connected")
        until(REAL_DS_WAITING)
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

