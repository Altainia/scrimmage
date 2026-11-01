"""scenarios/climb_high.toml: the climber robot (configs/climber_robot.toml) climbs the 2022 HANGAR to HIGH in teleop,
the fake robot (tests/fake_robot_main.py --climb) playing examples/climb/high_timed.toml: high.toml's setpoints at
the times its steps started in `climb run` (tests/climb_timed.py), each held by a position loop on its encoder.
Through the CLI, as a user runs it."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import tomlkit

import climb_timed
from fake_robot import TimedClimb

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "scenarios" / "climb_high.toml"
CONFIG = ROOT / "configs" / "climber_robot.toml"
TIMED = ROOT / "examples" / "climb" / "high_timed.toml"


def run(scenario: Path, out: Path) -> tuple[int, dict]:
    r = subprocess.run([sys.executable, "-m", "scrimmage", "run", str(scenario), "--out", str(out)],
                       capture_output=True, text=True, timeout=120)
    assert (out / "report.json").exists(), r.stdout + r.stderr
    return r.returncode, json.loads((out / "report.json").read_text())


def checks(report: dict) -> dict[str, dict]:
    return {c["check"]: c for c in report["steps"][-1]["checks"]}


def variant(tmp_path: Path, *, shift: float = 0.0, drop: str | None = None) -> Path:
    """climb_high.toml with a changed copy of the timed climb: every setpoint `shift` s later (none before 0), and
    `drop`'s setpoints removed."""
    raw = tomllib.loads(TIMED.read_text())
    raw["setpoint"] = [{**sp, "t": round(max(0.0, sp["t"] + shift), 3)} for sp in raw["setpoint"]
                       if sp["joint"] != drop]
    timed = tmp_path / "timed.toml"
    timed.write_text(tomlkit.dumps(raw))
    cfg = tomlkit.parse(CONFIG.read_text())
    cfg["robot_program"]["command"] = ["{python}", "fake_robot_main.py", "--lockstep", "--climb", str(timed)]
    cfg["robot_program"]["cwd"] = str(ROOT / "tests")
    config = tmp_path / "climber_robot.toml"
    config.write_text(tomlkit.dumps(cfg))
    scenario = tmp_path / "climb_high.toml"
    scenario.write_text(SCENARIO.read_text().replace('"../configs/climber_robot.toml"', json.dumps(str(config))))
    return scenario


ALL = {"by_time climb.points >= 10 by t=9", "at_end climb.points >= 10", "always climb.hook_load <= 4000"}


def test_climb_high_passes(tmp_path):
    code, report = run(SCENARIO, tmp_path / "out")
    cs = checks(report)
    assert code == 0, cs
    assert set(cs) == ALL
    assert all(c["passed"] for c in cs.values())
    # HIGH first touched about 7.2 s into teleop (the climb run: held at 7.77 s), hooks under about 1.1 kN
    assert "reached 10 at t=7." in cs["by_time climb.points >= 10 by t=9"]["message"]
    assert 800 < cs["always climb.hook_load <= 4000"]["measured"] < 1600


def test_two_runs_give_identical_reports_and_csv(tmp_path):
    for name in ("a", "b"):
        run(SCENARIO, tmp_path / name)
    for f in ("report.json", "run.csv"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes(), f


@pytest.mark.parametrize("shift", [0.02, -0.02])
def test_climb_high_survives_its_setpoints_20ms_off(tmp_path, shift):
    """The loops are closed on the encoders, so the climb doesn't hang on the exact times: every setpoint 20 ms later,
    or earlier (step 1's, at 0, stays at 0), still reaches HIGH and passes every check."""
    code, report = run(variant(tmp_path, shift=shift), tmp_path / "out")
    cs = checks(report)
    assert code == 0 and all(c["passed"] for c in cs.values()), cs


def test_without_the_high_pivot_it_fails_on_points(tmp_path):
    """The high climber's pivot setpoints removed: the arm never leans over HIGH, so the robot doesn't get there; it
    fails both points checks and nothing else."""
    code, report = run(variant(tmp_path, drop="high.pivot"), tmp_path / "out")
    cs = checks(report)
    assert code == 1
    failed = {name for name, c in cs.items() if not c["passed"]}
    assert failed == {"by_time climb.points >= 10 by t=9", "at_end climb.points >= 10"}, cs
    assert cs["at_end climb.points >= 10"]["measured"] < 10


def test_timed_climb_setpoints_and_loops(tmp_path):
    p = tmp_path / "t.toml"
    p.write_text("loop = 0.005\n"
                 '[[joint]]\nname = "a"\npwm = 2\nencoder = 4\ncounts_per_unit = 1000.0\nkp = 400.0\n'
                 '[[setpoint]]\nt = 0.5\njoint = "a"\nto = 0.1\nmax_voltage = 3.0\n'
                 '[[setpoint]]\nt = 0.0\njoint = "a"\nvoltage = -2.0\n')
    c = TimedClimb.load(p)
    assert c.at(-0.01) == {}
    assert c.at(0.0)["a"]["voltage"] == -2.0 and c.at(0.499)["a"]["voltage"] == -2.0
    assert c.at(0.5)["a"]["to"] == 0.1 and c.at(9.0)["a"]["to"] == 0.1
    assert c.volts(c.at(0.0)["a"], 0.0, 12.0) == -2.0                   # open loop
    assert c.volts(c.at(1.0)["a"], 0.095, 12.0) == pytest.approx(2.0)   # 400 V/m x 5 mm
    assert c.volts(c.at(1.0)["a"], 0.0, 12.0) == 3.0                    # capped at max_voltage
    p.write_text('loop = 0.005\n[[joint]]\nname = "a"\npwm = 2\nencoder = 4\ncounts_per_unit = 1.0\nkp = 1.0\n'
                 '[[setpoint]]\nt = 0.0\njoint = "b"\nto = 1.0\n')
    with pytest.raises(ValueError, match="a joint of the"):
        TimedClimb.load(p)


def test_the_timed_climb_comes_from_the_climb_run():
    """The setpoints are high.toml's drives at the times its steps started in `climb run` (to within a step's few
    ms: regenerate the file after changing the climb, with the command in its header)."""
    raw = tomllib.loads(TIMED.read_text())
    _, result = climb_timed.timed_text(CONFIG, ROOT / "examples" / "climb" / "high.toml", "")
    assert result.status == "reached HIGH"
    starts = sorted({round(s.start, 3) for s in result.steps if s.start > 0})
    file_times = sorted({sp["t"] for sp in raw["setpoint"] if sp["t"] > 0})
    assert file_times and all(min(abs(t - s) for s in starts) < 0.05 for t in file_times), (file_times, starts)
