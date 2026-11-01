"""The fast-mode sample robot (examples/java_fastmode) over the 2026 BUMP: its "Over the Bump" autos pass their
ground-gap and tilt checks at the sample's speeds (BUMP_SPEED, BUMP_SPEED_ELEVATOR_UP), and the same auto driven
faster (SCRIMMAGE_BUMP_SPEED, through the scenario's robot_env) fails on robot.ground_gap."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scrimmage import java_tools

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / "examples" / "java_fastmode"
BUMP = ROOT / "scenarios" / "fastmode_bump.toml"
ELEVATOR_UP = ROOT / "scenarios" / "fastmode_bump_elevator_up.toml"
# Faster than the sample's 2 m/s. The sweep's limit is 2 m/s (0.058 m of gap at 2.25, 0.084 at 2.5), but its driver
# holds speed up the ramp with no torque limit, and the sample's drive doesn't: commanded 2.5 m/s, it slows to about
# 2 m/s on the climb and still passes (0.043 m). At 3 m/s it leaves the ground by 0.082 m and doesn't tip.
TOO_FAST = 3.0
GAP_CHECK = "always robot.ground_gap"
pytestmark = pytest.mark.java


@pytest.fixture(scope="module", autouse=True)
def built():
    java_tools.build_agent()
    r = subprocess.run(["./gradlew", "build"], cwd=PROJECT, env=dict(os.environ, JAVA_HOME=str(java_tools.find_jdk())),
                       capture_output=True, text=True, timeout=900)   # a cold Gradle cache: keep it out of start_timeout
    assert r.returncode == 0, "gradlew build failed:\n" + (r.stdout + r.stderr)[-3000:]


def run(scenario: Path, out: Path) -> tuple[subprocess.CompletedProcess, dict]:
    r = subprocess.run([sys.executable, "-m", "scrimmage", "run", str(scenario), "--out", str(out)],
                       capture_output=True, text=True, timeout=600)
    report = out / "report.json"
    assert report.exists(), r.stdout + r.stderr[-3000:]
    return r, json.loads(report.read_text())


def checks(data: dict) -> dict[str, bool]:
    return {c["check"]: c["passed"] for s in data["steps"] for c in s["checks"]}


@pytest.mark.parametrize("scenario", [BUMP, ELEVATOR_UP], ids=["elevator_down", "elevator_up"])
def test_over_the_bump_passes_at_the_sample_speed(scenario, tmp_path):
    r, data = run(scenario, tmp_path / "out")
    assert r.returncode == 0, r.stdout + r.stderr[-3000:]
    assert data["passed"] and data["lockstep"] is True, data["steps"]
    assert any(k.startswith(GAP_CHECK) for k in checks(data)), checks(data)
    assert not any(n.startswith(("the robot tipped", "the robot was stranded")) for n in data["notes"]), data["notes"]


def test_over_the_bump_too_fast_fails_on_ground_gap(tmp_path):
    text = BUMP.read_text()
    text = text.replace('config = "../configs/', f'config = "{ROOT / "configs"}/')
    text = text.replace('robot_env = { SCRIMMAGE_AUTO = "Over the Bump" }',
                        f'robot_env = {{ SCRIMMAGE_AUTO = "Over the Bump", SCRIMMAGE_BUMP_SPEED = "{TOO_FAST}" }}')
    text = text.replace('"bump: driving at 2.00 m/s"', f'"bump: driving at {TOO_FAST:.2f} m/s"')
    assert "SCRIMMAGE_BUMP_SPEED" in text and f"{TOO_FAST:.2f} m/s" in text
    fast = tmp_path / "fastmode_bump_fast.toml"
    fast.write_text(text)
    r, data = run(fast, tmp_path / "out")
    assert r.returncode != 0 and not data["passed"]
    failed = [k for k, ok in checks(data).items() if not ok]
    # the gap check alone: the robot drove at the faster speed (its console check passes) and didn't tip
    assert len(failed) == 1 and failed[0].startswith(GAP_CHECK), checks(data)
