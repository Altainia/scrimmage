"""The fast-mode sample robot (examples/java_fastmode: AdvantageKit, a Kraken swerve drive, a Kraken elevator and
an arm) under scrimmage, in fast mode (the fast-mode library's simulated devices, lockstep) and in Phoenix mode
(its real Phoenix 6 IO, on the wall clock)."""

import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from phoenix_runs import machine_too_slow
from scrimmage import java_tools
from scrimmage.session import Session

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / "examples" / "java_fastmode"
FAST = ROOT / "scenarios" / "fastmode_auto.toml"
PHOENIX = ROOT / "scenarios" / "fastmode_auto_phoenix.toml"
ELEVATOR_TARGET = 0.75     # m: ScrimmageConstants.ELEVATOR_AUTO_HEIGHT in the sample robot
ARM_TARGET = 45.0          # deg: ScrimmageConstants.ARM_AUTO_ANGLE
pytestmark = pytest.mark.java


@pytest.fixture(scope="module", autouse=True)
def built():
    java_tools.build_agent()
    # the committed vendordep must be the library this scrimmage builds (re-run `scrimmage java vendordep`)
    vendordep = json.loads((PROJECT / "vendordeps" / "Scrimmage.json").read_text())
    assert vendordep == java_tools.vendordep_json()
    r = subprocess.run(["./gradlew", "build"], cwd=PROJECT, env=dict(os.environ, JAVA_HOME=str(java_tools.find_jdk())),
                       capture_output=True, text=True, timeout=900)   # a cold Gradle cache: keep it out of start_timeout
    assert r.returncode == 0, "gradlew build failed:\n" + (r.stdout + r.stderr)[-3000:]


def run(scenario: Path, out: Path, *args: str) -> tuple[subprocess.CompletedProcess, dict]:
    r = subprocess.run([sys.executable, "-m", "scrimmage", "run", str(scenario), "--out", str(out), *args],
                       capture_output=True, text=True, timeout=600)
    report = out / "report.json"
    assert report.exists(), r.stdout + r.stderr[-3000:]
    return r, json.loads(report.read_text())


@pytest.fixture(scope="module")
def fast_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("fast") / "out"
    r, data = run(FAST, out)
    return r, data, out


def test_fast_mode_auto_scenario_passes(fast_run):
    r, data, _ = fast_run
    assert r.returncode == 0, r.stdout + r.stderr[-3000:]
    assert data["passed"] and data["lockstep"] is True, data
    checks = {c["check"]: c["passed"] for c in data["implicit"]}
    assert checks.get("lockstep stayed engaged"), data["implicit"]
    assert not any("runs in real time (not lockstep)" in n for n in data["notes"]), data["notes"]


def test_fast_mode_runs_repeat_exactly(fast_run, tmp_path):
    _, first, out1 = fast_run
    out2 = tmp_path / "out"
    _, second = run(FAST, out2)
    assert (out1 / "run.csv").read_text() == (out2 / "run.csv").read_text()
    measured = lambda d: [[c["measured"] for c in s["checks"]] for s in d["steps"]]
    assert measured(first) == measured(second)
    assert len((out1 / "run.csv").read_text().splitlines()) > 150   # 5 s of rows


@pytest.mark.skipif(shutil.which("taskset") is None, reason="needs taskset (util-linux)")
def test_fast_mode_repeats_on_one_cpu(fast_run, tmp_path):
    """On one CPU the robot loop and the control tick, woken together, interleave differently; the library's
    time-stamped requests and readings keep the run the same."""
    _, _, out1 = fast_run
    out2 = tmp_path / "out"
    r = subprocess.run(["taskset", "-c", "0", sys.executable, "-m", "scrimmage", "run", str(FAST), "--out", str(out2)],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr[-3000:]
    assert (out1 / "run.csv").read_text() == (out2 / "run.csv").read_text()


WARM_UP = 2.0     # simulated s before timing: the robot JVM's JIT warm-up isn't fast mode's speed
TIMED = 4.0       # simulated s timed, in autonomous (the drive auto, then the elevator)


def test_fast_mode_is_faster_than_real_time():
    """Measured past the warm-up: a whole 5 s run on a 2-vCPU CI runner is mostly JIT warm-up (1.2x there,
    3.1x locally). Past it, CI runners measured 1.4x to over 1.5x: the spec's claim is faster than real time."""
    async def main():
        s = Session(ROOT / "configs" / "fastmode.toml", nt=False, sim_mode="fast")
        try:
            await s.start()
            s.set_mode("auto")
            await s.advance(WARM_UP)
            wall0 = time.monotonic()
            await s.advance(TIMED)
            wall = time.monotonic() - wall0
            assert s.status()["lockstep"] is True
            assert TIMED / wall > 1.0, f"simulated {TIMED} s in {wall:.2f} s"
        finally:
            await s.stop()
    asyncio.run(main())


def test_fast_mode_creates_no_phoenix_devices():
    async def main():
        s = Session(ROOT / "configs" / "fastmode.toml", nt=False, sim_mode="fast")
        try:
            st = await s.start()
            await s.advance(2.5)                     # past the 2 s created-devices check
            devices = s.status()["devices"]
            assert st["lockstep"] is True
            assert any(d.startswith("ScrimmageMotor/") for d in devices), devices
            assert not [d for d in devices if "(v6)" in d], devices
            assert s.status()["lockstep"] is True
        finally:
            await s.stop()
    asyncio.run(main())


def _at_end(data: dict, signal: str) -> dict:
    auto = next(s for s in data["steps"] if s["name"] == "auto")
    return next(c for c in auto["checks"] if c["check"].startswith("at_end") and signal in c["check"])


def test_fast_mode_motion_magic_reaches_height_in_time(fast_run):
    _, data, _ = fast_run
    height = _at_end(data, "joint.elevator.carriage.position")
    assert height["measured"] == pytest.approx(ELEVATOR_TARGET, abs=0.01), height


def test_fast_mode_arm_cosine_reaches_its_angle_in_time(fast_run):
    """The arm on the elevator carriage: Motion Magic to 45 deg with kG scaled by the cosine of its angle."""
    _, data, out = fast_run
    angle = _at_end(data, "joint.arm.pivot.position")
    assert angle["measured"] == pytest.approx(math.radians(ARM_TARGET), abs=math.radians(1)), angle
    assert re.search(r"arm: at 4[4-6]\.\d deg", (out / "robot.log").read_text())


def test_phoenix_mode_auto_scenario_passes(tmp_path):
    r, data = run(PHOENIX, tmp_path / "out")
    # The arm is up 3.4 s into the 4 s auto: a machine too slow for real time may leave it, or the elevator, short.
    # Physics that skips ahead moves the robot without its path follower seeing it: on CI at 0.6x it overshot 0.63 m
    skip = machine_too_slow(data, lambda check: check == "pose" or check.startswith("at_end joint."))
    if skip:
        pytest.skip(skip)
    assert r.returncode == 0, r.stdout + r.stderr[-3000:]
    assert data["passed"] and any("runs in real time (not lockstep)" in n for n in data["notes"]), data
