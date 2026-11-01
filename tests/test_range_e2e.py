"""The fast-mode sample robot (examples/java_fastmode) shooting on the 2026 test range: its Shoot Left, Shoot Right
and Shoot Moving autos in fast mode (lockstep: the scores repeat exactly), Shoot Left in Phoenix mode (real time),
fast mode's speed with a launcher and its balls, and its aiming table being the one the launcher model makes."""

import asyncio
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from phoenix_runs import machine_too_slow
from scrimmage import java_tools
from scrimmage.config import load_config
from scrimmage.scenario import config_for, load_scenario
from scrimmage.session import Session

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / "examples" / "java_fastmode"
SCENARIOS = ROOT / "scenarios"
LEFT = SCENARIOS / "fastmode_shoot_left.toml"
RIGHT = SCENARIOS / "fastmode_shoot_right.toml"
MOVING = SCENARIOS / "fastmode_shoot_moving.toml"
SPEED = SCENARIOS / "fastmode_shoot_speed.toml"
PHOENIX = SCENARIOS / "fastmode_shoot_phoenix.toml"
AIM_TABLE = PROJECT / "src" / "main" / "java" / "frc" / "robot" / "subsystems" / "shooter" / "AimTable.java"
# The command AimTable.java's header and the docs give, for the table's first rows only (the whole table takes about
# 7 minutes; each row after the first follows the one before it, so the first rows are made exactly as in the whole)
AIM_ARGS = ["launcher", "aim", str(ROOT / "configs" / "fastmode_range.toml"), "--goal", "hub_blue",
            "--distances", "1.5:2.0:0.25", "--java-class", "frc.robot.subsystems.shooter.AimTable"]
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


@pytest.fixture(scope="module")
def aim_regenerated(tmp_path_factory):
    """`scrimmage launcher aim` regenerating the table's first rows, started early: it takes a minute of one CPU, so
    it runs beside the fast-mode runs (lockstep: a busy machine slows them, it doesn't change them) and is waited for
    before the timed and real-time tests."""
    out = tmp_path_factory.mktemp("aim") / "AimTable.java"
    proc = subprocess.Popen([sys.executable, "-m", "scrimmage", *AIM_ARGS, "--java", str(out)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    yield proc, out
    if proc.poll() is None:
        proc.kill()
        proc.wait()


def run(scenario: Path, out: Path) -> tuple[subprocess.CompletedProcess, dict]:
    r = subprocess.run([sys.executable, "-m", "scrimmage", "run", str(scenario), "--out", str(out)],
                       capture_output=True, text=True, timeout=600)
    report = out / "report.json"
    assert report.exists(), r.stdout + r.stderr[-3000:]
    return r, json.loads(report.read_text())


def assert_passed(r: subprocess.CompletedProcess, data: dict) -> None:
    assert r.returncode == 0, r.stdout + r.stderr[-3000:]
    assert data["passed"] and data["lockstep"] is True, data["steps"]
    kinds = {c["check"].split()[0] for s in data["steps"] for c in s["checks"]}
    assert {"by_time", "at_end"} <= kinds, kinds


@pytest.fixture(scope="module")
def moving_run(tmp_path_factory, aim_regenerated):
    out = tmp_path_factory.mktemp("moving") / "out"
    r, data = run(MOVING, out)
    return r, data, out


def test_shoot_left_scores(tmp_path, aim_regenerated):
    r, data = run(LEFT, tmp_path / "out")
    assert_passed(r, data)
    assert data["range"]["goals"]["hub_blue"] == 8


def test_shoot_right_scores(tmp_path, aim_regenerated):
    r, data = run(RIGHT, tmp_path / "out")
    assert_passed(r, data)
    assert data["range"]["goals"]["hub_blue"] == 8


def test_shoot_moving_scores(moving_run):
    r, data, _ = moving_run
    assert_passed(r, data)
    collected = [b for b in data["balls"] if b["origin"] != "preload" and b.get("fired_t") is not None]
    assert collected, "Shoot Moving fired no FUEL it collected from the NEUTRAL ZONE"
    moving = [b for b in data["balls"] if b["origin"] == "preload" and b["outcome"] == "scored"
              and math.hypot(*b["robot_velocity"][:2]) > 1.0]
    assert moving, "no preloaded FUEL scored from a robot moving faster than 1 m/s"


def test_fast_mode_scores_repeat_exactly(moving_run, tmp_path):
    _, first, out1 = moving_run
    out2 = tmp_path / "out"
    _, second = run(MOVING, out2)
    assert first["range"] == second["range"]
    assert first["balls"] == second["balls"]
    assert (out1 / "run.csv").read_text() == (out2 / "run.csv").read_text()


def test_aim_table_is_current(aim_regenerated):
    proc, out = aim_regenerated
    log, _ = proc.communicate(timeout=600)
    assert proc.returncode == 0, log
    rows = lambda text: [line.rstrip(",") for line in text.splitlines() if line.startswith("        {")]
    made, committed = rows(out.read_text()), rows(AIM_TABLE.read_text())
    assert len(made) == 3
    assert made == committed[:3], (
        "AimTable.java isn't what the launcher model makes of configs/fastmode_range.toml: regenerate it with the "
        "command in its header (see Shooter.java)")


WARM_UP = 2.0     # simulated s before timing: the robot JVM's JIT warm-up isn't fast mode's speed
TIMED = 4.0       # simulated s timed, in autonomous: the preload fired on the move
# The speed scenario preloads 8 FUEL; 4 have fired when the timing starts, the rest fire in the timed part. Measured
# (simulated s per wall s over TIMED), pinned to 2 cores (taskset) here, before and after the range step was made
# cheaper (October 2026): 8 preloaded 1.7x -> 2.3 to 2.7x, 4 preloaded 2.2x -> 2.8 to 3.6x.
#
# On CI (CI set) the test doesn't time against the wall clock: a shared 2-vCPU runner measured the run at 1.44x and,
# on the same code a few hours later, at 0.97x and 0.91x. Comparing the range world's step cost with the same robot's
# without the range didn't hold either: 1.52 to 1.61 here, 1.92 on a busy runner, where the 360 floor balls' parked
# bodies put back (the regression it was for) measured 2.05 to 2.11 here. On CI the run still has to go in lockstep
# and fire in the timed part; test_ball_bodies_fixed_count guards the floor balls without a clock.


def test_range_fast_mode_faster_than_real_time(aim_regenerated):
    """The speed scenario's robot (Shoot Moving with its preloaded FUEL, the NEUTRAL ZONE's turned off) in lockstep:
    faster than real time past the JIT warm-up, as test_fast_mode_is_faster_than_real_time. Not timed on CI."""
    aim_regenerated[0].wait(timeout=1200)     # not timed against the table's regeneration
    scenario = load_scenario(SPEED)
    assert scenario.range["floor_balls"] is False
    config = config_for(scenario, load_config(scenario.config_path))

    async def main():
        s = Session(config, nt=False, sim_mode="fast")
        try:
            await s.start()
            s.set_mode("auto")
            await s.advance(WARM_UP)
            fired0 = s.world.test_range.counts()["fired"]
            wall0 = time.monotonic()
            await s.advance(TIMED)
            wall = time.monotonic() - wall0
            assert s.status()["lockstep"] is True
            assert s.world.test_range.counts()["fired"] > fired0      # the launcher fired in the timed part
            if not os.environ.get("CI"):
                assert TIMED / wall > 1.0, f"simulated {TIMED} s in {wall:.2f} s"
        finally:
            await s.stop()
    asyncio.run(main())


def test_phoenix_mode_shoot_scenario_passes(tmp_path, aim_regenerated):
    aim_regenerated[0].wait(timeout=1200)     # Phoenix mode runs on the wall clock: no CPU taken from it
    r, data = run(PHOENIX, tmp_path / "out")
    skip = machine_too_slow(data, lambda check: check.startswith(("by_time fired ", "at_end fired ")))
    if skip:
        pytest.skip(skip)
    assert r.returncode == 0, r.stdout + r.stderr[-3000:]
    assert data["passed"] and any("runs in real time (not lockstep)" in n for n in data["notes"]), data
