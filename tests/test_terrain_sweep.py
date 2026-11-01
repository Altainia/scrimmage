"""`terrain sweep`: the fast-mode sample's robot driven straight over the 2026 BUMP (ranges/2026-bump.toml) at a few
speeds by the built-in driver, no robot program.

The stock chassis's bumpers sit 0.02 m up and reach 0.176 m past this robot's front wheel axles (bumper 0.08 m plus
module_inset 0.096 m): an approach angle of atan(0.02 / 0.176) = 6.5 deg, under the BUMP's 15 deg, so it can't drive
onto the BUMP. The sweeps here raise the bumpers to CLEARANCE, 0.06 m: atan(0.06 / 0.176) = 18.8 deg, which clears
15 deg. test_default_bumpers_stop_against_the_bump pins the stock chassis's limit.
"""

from __future__ import annotations

import csv
import math
import re
from pathlib import Path

import copy

import pytest

from range_fixtures import KITBOT, range_cfg
from scrimmage.__main__ import main
from scrimmage.protocol import HalState
from scrimmage.world import World
from scrimmage.terrain.sweep import Limits, Row, SweepError, sweep

ROOT = Path(__file__).resolve().parents[1]
CLEARANCE = 0.06             # m: bumpers this high clear the BUMP's 15 deg ramps (see the module docstring)
OVERHANG = 0.08 + 0.096      # m: configs/fastmode.toml's front bumper face past its front wheel axles
BUMP_DEG = 15.0
ELEVATOR_UP = 0.9            # m of the elevator's 1 m travel


def bump_config(tmp_path: Path, clearance: float | None = CLEARANCE, balls: bool = True) -> Path:
    """configs/fastmode.toml's robot (its device IDs kept: a team's config, unchanged) on ranges/2026-bump.toml, its
    chassis's bumpers `clearance` up (None: the stock default), with or without the range's row of balls."""
    text = (ROOT / "configs" / "fastmode.toml").read_text()
    text = re.sub(r"(?m)^start_pose = .*$", "", text)      # the range's start places the robot
    if clearance is not None:
        text = text.replace('stock = "chassis"', f'stock = "chassis"\nbumper_clearance = {clearance}', 1)
    text += f'\n[range]\nfile = "2026-bump"\nstart = "approach"\nfloor_balls = {str(balls).lower()}\n'
    path = tmp_path / "fastmode_bump.toml"
    path.write_text(text)
    return path


@pytest.mark.parametrize("on_range", [False, True])
def test_world_external_runs_each_step_before_physics(on_range):
    """World.external is called once a step, after the motors drive and before mj_step, with or without a range:
    a force it puts on the robot's free joint moves the robot."""
    cfg = range_cfg() if on_range else {"part": copy.deepcopy(KITBOT)}
    w = World(cfg, None)
    x0 = w.robot_pose()[0]
    calls = []

    def push_forward(d):
        calls.append(float(d.time))
        d.qfrc_applied[w.free_dadr] += 200.0       # N along world x

    w.external = push_forward
    for _ in range(200):
        w.step(HalState())
    assert len(calls) == 200 and calls[0] == 0.0
    assert w.robot_pose()[0] - x0 > 0.01


def test_the_test_bumpers_clear_the_bump():
    assert math.degrees(math.atan(CLEARANCE / OVERHANG)) == pytest.approx(18.8, abs=0.05)
    assert math.degrees(math.atan(CLEARANCE / OVERHANG)) > BUMP_DEG
    assert math.degrees(math.atan(0.02 / OVERHANG)) < BUMP_DEG


def test_sweep_rows_and_limit_on_the_2026_bump(tmp_path):
    result = sweep(bump_config(tmp_path), "bump", [1.0, 2.0, 3.0, 4.0], {}, Limits())
    rows = result.rows
    assert [r.speed for r in rows] == [1.0, 2.0, 3.0, 4.0]
    assert all(r.crossed for r in rows)
    gaps = [r.peak_gap for r in rows]
    assert gaps == sorted(gaps), gaps                     # faster leaves the ground higher
    assert rows[-1].peak_gap > rows[0].peak_gap
    assert not any(r.tipped or r.stranded for r in rows[:2])
    assert not rows[-1].passes(Limits())                  # 4 m/s lands on the balls past the bump and tips
    assert rows[0].peak_gap <= Limits().gap            # a crawl is safe, so there is a limit
    assert result.limit_speed is not None
    # the limit is the fastest speed with it and every slower row passing
    passing = [r.passes(Limits()) for r in rows]
    k = passing.index(False) if False in passing else len(rows)
    assert result.limit_speed == rows[k - 1].speed
    assert all(abs(r.peak_pitch) > 10.0 for r in rows)    # at least most of the 15 deg climb


def test_landing_is_past_the_crest_after_the_jump(tmp_path):
    """At 2.5 m/s the robot jumps off the crest and lands down the far ramp, short of the balls (1.01 m past the
    crest; its front wheels are 0.25 m ahead of its CoM)."""
    row, faster = sweep(bump_config(tmp_path), "bump", [2.5, 3.0], {}, Limits()).rows
    assert row.air_time > 0.05
    assert 0.2 < row.landing < 1.0
    assert not row.landed_on_balls
    assert faster.landed_on_balls          # at 3 m/s its front wheels come down on the row of balls


def test_holding_the_elevator_up_changes_peak_pitch(tmp_path):
    """Top-heavy, the robot pitches further nose down off the crest. No balls: landing on them would decide it."""
    cfg = bump_config(tmp_path, balls=False)
    down = sweep(cfg, "bump", [1.5], {}, Limits()).rows[0]
    up = sweep(cfg, "bump", [1.5], {"elevator.carriage": ELEVATOR_UP}, Limits()).rows[0]
    assert up.peak_pitch > down.peak_pitch + 3.0, (down, up)


def test_default_bumpers_stop_against_the_bump(tmp_path):
    """The stock chassis's 0.02 m bumpers (a 6.5 deg approach angle on this robot) meet the 15 deg ramp: the robot
    stops with its bumper on the ramp, the row says it didn't cross, and so no speed is safe. (Faster, it can bash its
    way over; the limit still needs every slower speed to pass.)"""
    result = sweep(bump_config(tmp_path, clearance=None), "bump", [1.0], {}, Limits())
    row = result.rows[0]
    assert not row.crossed and row.peak_pitch < -3.0          # nose up against the ramp, short of 15 deg
    assert not row.passes(Limits())
    assert result.limit_speed is None


def test_cli_writes_csv_and_md(tmp_path, capsys):
    out = tmp_path / "out"
    main(["terrain", "sweep", str(bump_config(tmp_path)), "--element", "bump", "--speeds", "1:2:1",
          "--set", "elevator.carriage=0.5", "--out", str(out)])
    with open(out / "sweep.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [float(r["speed"]) for r in rows] == [1.0, 2.0]
    assert {"speed", "peak_gap", "air_time", "landing", "peak_pitch", "peak_roll", "peak_tilt", "tipped", "stranded",
            "landed_on_balls", "crossed", "pass"} <= set(rows[0])
    md = (out / "sweep.md").read_text()
    assert "| speed" in md and "elevator.carriage = 0.5" in md and "speed limit over bump" in md
    printed = capsys.readouterr().out
    assert "speed limit over bump" in printed and str(out / "sweep.csv") in printed


def test_unknown_element_or_joint_errors_clearly(tmp_path, capsys):
    cfg = bump_config(tmp_path)
    with pytest.raises(SweepError, match=r"no element 'bunp'.*elements: bump"):
        sweep(cfg, "bunp", [1.0], {}, Limits())
    with pytest.raises(SweepError, match=r"no joint 'elevator\.carrige'.*joints: .*elevator\.carriage"):
        sweep(cfg, "bump", [1.0], {"elevator.carrige": 0.5}, Limits())
    with pytest.raises(SystemExit) as e:
        main(["terrain", "sweep", str(cfg), "--element", "bump", "--speeds", "1:2:1", "--set", "elevator.carriage"])
    assert e.value.code == 2
    assert "--set" in capsys.readouterr().err


def test_a_differential_drive_is_driven_too(tmp_path):
    """The kitbot (tests/range_fixtures.py, a 6-wheel differential drive) with its bumpers 0.06 m up: they reach
    0.15 m past its front axle, an approach angle of atan(0.06 / 0.15) = 21.8 deg. The driver turns its wheels with
    no azimuth to hold, and it crosses."""
    path = tmp_path / "kitbot_bump.toml"
    path.write_text('''
[[part]]
name = "drive"
stock = "chassis"
length = 0.66
width = 0.6
mass = 47.0
frame_height = 0.1
bumper_clearance = 0.06

[[part]]
name = "dt"
stock = "differential"
attach = "drive.center"
ids = { left = "PWM:0", right = "PWM:1" }

[range]
file = "2026-bump"
start = "approach"
floor_balls = false
''')
    row = sweep(path, "bump", [1.0], {}, Limits()).rows[0]
    assert row.crossed and not (row.tipped or row.stranded)


def test_the_tilt_limit_is_on_tilt_not_pitch_and_roll_apart():
    """Pitch 25 deg with roll 20 deg at once is a tilt of acos(cos 25 cos 20) = 31.6 deg: over a 30 deg limit though
    each is under it. That diagonal is where top-heavy robots tip."""
    tilt = math.degrees(math.acos(math.cos(math.radians(25.0)) * math.cos(math.radians(20.0))))
    assert tilt == pytest.approx(31.6, abs=0.05)
    row = Row(speed=2.0, peak_gap=0.01, air_time=0.0, landing=None, peak_pitch=25.0, peak_roll=20.0, peak_tilt=tilt,
              tipped=False, stranded=False, landed_on_balls=False)
    assert not row.passes(Limits(tilt=30.0))
    assert row.passes(Limits(tilt=32.0))


def test_each_speed_runs_from_a_fresh_world(tmp_path):
    """A row doesn't depend on the runs before it: reset puts the balls back and clears the monitor's latches. Both
    speeds land on the row of balls behind the bump, so the first run always scatters them; whether 3.5 m/s then tips
    is chaotic (it does on some machines, not on CI's), so the test needs only that the first run disturbed the world."""
    cfg = bump_config(tmp_path)
    alone = sweep(cfg, "bump", [3.0], {}, Limits()).rows[0]
    after = sweep(cfg, "bump", [3.5, 3.0], {}, Limits()).rows
    assert after[0].landed_on_balls
    assert after[1] == alone
