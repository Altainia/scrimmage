"""End-to-end: real RobotPy robot programs through `scrimmage run` and Session.

Needs robotpy[sim], robotpy-halsim-ws and the lockstep extension (pip install ./lockstep).
"""

import asyncio
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("wpilib")
pytest.importorskip("halsim_ws")

from scrimmage.session import Session, SessionError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def cli(*args, timeout=180):
    return subprocess.run([sys.executable, "-m", "scrimmage", *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True, timeout=timeout)


def variant(tmp_path, src: Path, *replacements) -> Path:
    """A copy of a config with absolute paths, so it can live in tmp_path, plus text replacements."""
    text = src.read_text().replace('"../tests/models/', f'"{ROOT / "tests" / "models"}/')
    text = text.replace('cwd = "../examples/robotpy_kitbot"', f'cwd = "{ROOT / "examples" / "robotpy_kitbot"}"')
    for old, new in replacements:
        text = text.replace(old, new)
    p = tmp_path / src.name
    p.write_text(text)
    return p


def test_kitbot_auto_scenario_passes(tmp_path):
    pytest.importorskip("scrimmage_lockstep")
    out = tmp_path / "out"
    r = cli("run", ROOT / "scenarios" / "kitbot_auto.toml", "--out", out)
    assert r.returncode == 0, r.stdout + r.stderr[-3000:]
    data = json.loads((out / "report.json").read_text())
    assert data["passed"] and data["lockstep"]
    shots = data["steps"][1]["screenshots"]
    assert any("top" in s for s in shots) and any("contact-sheet" in s for s in shots)
    assert all((out / s).read_bytes()[:4] == b"\x89PNG" for s in shots)
    m = re.search(r"\(([\d.]+)x real time\)", r.stderr)
    # faster than real time even where screenshots are drawn in software (CI's OSMesa runs at about 1.9x)
    assert m and float(m.group(1)) >= 1.5, r.stderr[-2000:]
    print(f"lockstep rate {m.group(1)}x")


def test_wrong_target_fails_with_measured_values(tmp_path):
    pytest.importorskip("scrimmage_lockstep")
    out = tmp_path / "out"
    r = cli("run", ROOT / "tests" / "scenarios" / "kitbot_auto_wrong_target.toml", "--out", out)
    assert r.returncode == 1, r.stdout + r.stderr[-2000:]
    pose = json.loads((out / "report.json").read_text())["steps"][1]["checks"][0]
    assert not pose["passed"] and "m from target" in pose["message"] and pose["measured"]["x"] < 5


def test_runs_are_repeatable(tmp_path):
    pytest.importorskip("scrimmage_lockstep")
    logs = []
    for i in range(2):
        out = tmp_path / f"out{i}"
        r = cli("run", ROOT / "tests" / "scenarios" / "kitbot_auto_wrong_target.toml", "--out", out)
        assert r.returncode == 1, r.stderr[-2000:]  # fails its pose check on purpose; runs still repeat
        rows = list(csv.DictReader(open(out / "run.csv")))
        logs.append([{k: v for k, v in row.items() if not k.startswith("odo_")} for row in rows])
    assert len(logs[0]) > 200 and logs[0] == logs[1]


def test_realtime_scenario_without_extension(tmp_path):
    cfg = variant(tmp_path, ROOT / "configs" / "kitbot.toml", (', "--scrimmage-lockstep"]', "]"))
    sc = tmp_path / "rt.toml"
    text = (ROOT / "scenarios" / "kitbot_auto.toml").read_text()
    # no screenshots: drawing one stalls a real-time run (seconds per frame with CI's software GL), and this test is
    # about the robot keeping up with the wall clock
    shots = text[text.index("  [[step.screenshot]]"):text.index("[[step]]", text.index("  [[step.screenshot]]"))]
    sc.write_text(text.replace(shots, "\n")
                  .replace('config = "../configs/kitbot.toml"', f'config = "{cfg}"\nspeed = 1\nrequire_lockstep = false'))
    r = cli("run", sc, "--out", tmp_path / "out")
    assert r.returncode == 0, r.stdout + r.stderr[-3000:]
    assert json.loads((tmp_path / "out" / "report.json").read_text())["lockstep"] is False
    assert "behind real time" not in r.stderr


def test_can_shim_round_trip_via_session():
    async def main():
        s = Session(ROOT / "tests" / "can_robot.toml", speed=1.0, nt=False)
        try:
            await s.start()
            await s.advance(1.5)
            s.set_mode("auto")
            await s.advance(2.5)
            s.set_mode("disabled")
            await s.advance(0.5)
            text = "\n".join(line["text"] for line in s.console()["lines"])
            state = s.state()
        finally:
            await s.stop()
        lines = re.findall(r"CAN rot=([-\d.]+),([-\d.]+) rps=([-\d.]+),([-\d.]+) vbus=([\d.]+)", text)
        io = re.findall(r"dio=(True|False) ai=([\d.]+)", text)
        assert io[0] == ("False", "0.000") and io[-1][0] == "True" and 0.0 < float(io[-1][1]) <= 5.0
        assert lines, text[-2000:]
        l_rot, r_rot, l_rps, r_rps, vbus = map(float, lines[-1])
        assert l_rot > 20 and r_rot < -20 and l_rps == pytest.approx(-r_rps, rel=0.02) and 11.0 < vbus <= 12.5
        assert state["pose"]["x"] > 4.0 and abs(state["pose"]["heading_deg"]) < 3
    asyncio.run(main())


def test_session_on_real_kitbot_reads_nt_and_screenshots():
    pytest.importorskip("scrimmage_lockstep")
    pytest.importorskip("ntcore")

    async def main():
        s = Session(ROOT / "configs" / "kitbot.toml")
        try:
            st = await s.start()
            assert st["lockstep"]
            s.set_mode("auto")
            r = await s.advance(3.0)
            assert r["pose"]["x"] > 2.5
            # NT travels on wall-clock timers, and scrimmage's NT client can take ~1 s of wall time to
            # connect to the robot's NT server (3 s of simulation at max speed may take less than that)
            for _ in range(50):
                await asyncio.sleep(0.1)
                gyro = s.nt_get(["/SmartDashboard/Gyro deg"])["values"]
                if gyro:
                    break
            assert "/SmartDashboard/Gyro deg" in gyro
            assert s.screenshot("top", width=480, height=300)[:4] == b"\x89PNG"
        finally:
            await s.stop()
    asyncio.run(main())


def test_session_restart_after_probe_crash():
    pytest.importorskip("scrimmage_lockstep")

    async def main():
        s = Session(ROOT / "tests" / "probe.toml", nt=False)
        try:
            await s.start()
            with pytest.raises(SessionError, match="exited") as e:
                await s.advance(1.0)                 # loop 10 (t=0.2 s) raises
            assert any("Traceback" in l for l in e.value.console)
            assert s.robot.returncode >= 0, e.value  # exited on its own, not killed by a signal (no segfault)
            st = await s.restart_robot()
            assert st["lockstep"] and st["t"] == 0.0
            assert (await s.advance(0.1))["t"] == pytest.approx(0.1)
        finally:
            await s.stop()
    asyncio.run(main())


def test_cli_errors(tmp_path):
    bad_step = variant(tmp_path, ROOT / "configs" / "kitbot.toml", ("step = 0.005", 'step = "fast"'))
    kitbot = ROOT / "configs" / "kitbot.toml"
    for args, msg in [(["serve", kitbot, "--speed", "0"], "speed must be a positive number"),
                      (["serve", kitbot, "--speed", "fast"], "speed must be a positive number"),
                      (["serve", kitbot, "--step", "0.0025"], "whole multiple of the physics timestep"),
                      (["serve", bad_step], "[lockstep] step must be a number"),
                      (["run", tmp_path / "missing.toml"], "scenario file not found"),
                      ([kitbot], "invalid choice")]:
        r = cli(*args, timeout=30)
        assert r.returncode == 2, args
        assert msg in r.stderr, (args, r.stderr)


def test_elevator_arm_scenario_passes(tmp_path):
    pytest.importorskip("scrimmage_lockstep")
    out = tmp_path / "out"
    r = cli("run", ROOT / "scenarios" / "elevator_arm.toml", "--out", out)
    assert r.returncode == 0, r.stdout + r.stderr[-3000:]
    data = json.loads((out / "report.json").read_text())
    assert data["passed"] and data["lockstep"]
    assert all(c["passed"] for c in data["steps"][1]["checks"]) and len(data["steps"][1]["checks"]) == 4


def test_robotpy_maxswerve_auto_scenario(tmp_path):
    """robotpy-rev's MAXSwerve example (SPARK MAX velocity and absolute-encoder position loops, an ADIS16448) runs its
    own trajectory auto under lockstep, driven by REV's simulated SPARKs through the scrimmage REV helper."""
    pytest.importorskip("scrimmage_lockstep")
    pytest.importorskip("rev")
    pytest.importorskip("commands2")
    out = tmp_path / "out"
    r = cli("run", ROOT / "scenarios" / "maxswerve_py_auto.toml", "--out", out, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr[-3000:]
    data = json.loads((out / "report.json").read_text())
    assert data["passed"] and data["lockstep"], data
    checks = {c["check"]: c["passed"] for c in data["implicit"]}
    assert checks.get("REV simulation available") and checks.get("lockstep stayed engaged"), data["implicit"]
    assert not any("runs in real time (not lockstep)" in n for n in data.get("notes", []))
    assert "scrimmage rev helper: loaded" in (out / "robot.log").read_text()
