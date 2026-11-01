"""scenarios/disc_auto_line.toml through the CLI: the disc robot (configs/disc_robot.toml, tests/fake_robot_main.py
--shoot as its robot program) shoots its 4 preloaded discs from the auto line into the 2013 HIGH goal. The same robot
with its channel tilted 8 degrees lower hits the wall under the slot, and the score check fails."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "scenarios" / "disc_auto_line.toml"
CONFIG = ROOT / "configs" / "disc_robot.toml"
TILT = re.compile(r"^tilt_angle = ([0-9.]+)", re.M)


def run_cli(scenario: Path, out: Path) -> tuple[subprocess.CompletedProcess, dict]:
    proc = subprocess.run([sys.executable, "-m", "scrimmage", "run", str(scenario), "--out", str(out),
                           "--speed", "max"], capture_output=True, text=True, timeout=120, cwd=ROOT)
    return proc, json.loads((out / "report.json").read_text())


def test_disc_auto_line_scores_four_in_the_high_goal(tmp_path):
    proc, rep = run_cli(SCENARIO, tmp_path / "out")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS" in proc.stdout
    assert rep["range"]["goals"]["high"] == 4 and rep["range"]["fired"] == 4
    assert all(b["outcome"] == "scored" and b["goal"] == "high" for b in rep["balls"])


def test_disc_auto_line_eight_degrees_lower_misses(tmp_path):
    """The channel 8 degrees lower: the four discs still fire but hit the wall under the HIGH goal."""
    text = CONFIG.read_text()
    tilt = float(TILT.search(text).group(1))
    text = TILT.sub(f"tilt_angle = {tilt - 8.0}", text, count=1)
    text = text.replace('cwd = "../tests"', f'cwd = "{ROOT / "tests"}"')
    config = tmp_path / "disc_robot_low.toml"
    config.write_text(text)
    scenario = tmp_path / "disc_auto_line_low.toml"
    scenario.write_text(SCENARIO.read_text().replace('config = "../configs/disc_robot.toml"', f'config = "{config}"'))

    proc, rep = run_cli(scenario, tmp_path / "out")
    assert proc.returncode != 0 and "FAIL" in proc.stdout, proc.stdout + proc.stderr
    checks = {c["check"]: c["passed"] for c in rep["steps"][1]["checks"]}
    assert checks == {"by_time score.high >= 3 by t=6": False, "at_end fired >= 4": True,
                      "console contains /shoot: feeding/": True}
    assert rep["range"]["goals"]["high"] == 0 and rep["range"]["fired"] == 4
