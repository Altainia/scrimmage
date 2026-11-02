"""Running a block test with no robot program (scrimmage/blocks/run.py) and `scrimmage test run`."""

from __future__ import annotations

import math
import threading

import numpy as np
import pytest

from scrimmage.__main__ import main
from scrimmage.assembler.templates import new_config
from scrimmage.blocks.program import TestFileError, loads
from scrimmage.blocks.run import MOTION_HZ, SIGNAL_HZ, TestRun, world_for
from scrimmage.climb.run import SETTLE_S

ARM = """
[[part]]
name = "arm"
stock = "arm"
attach = "drive.top"
ids = { pivot = "PWM:2" }
"""
LADDER = """
[range]
file = "ladder"
start = "below"
"""


def write_test(folder, blocks: str, head: str = "", ladder: bool = False):
    """A stand-in robot with a stock arm (robot.toml) and a test of it (t.test.toml) in `folder`; the test's path."""
    robot = new_config("standin") + ARM + (LADDER if ladder else "")
    (folder / "robot.toml").write_text(robot, encoding="utf-8")
    path = folder / "t.test.toml"
    path.write_text(f'[test]\nrobot = "robot.toml"\n{head}\n{blocks}', encoding="utf-8")
    return path


def run(folder, blocks: str, head: str = "", ladder: bool = False, pose=None, **kw):
    path = write_test(folder, blocks, head, ladder)
    program = loads(path.read_text(encoding="utf-8"), path)
    world = world_for(program)
    if pose is not None:
        pose(world)
    return TestRun(world, program).run(**kw)


MOVE_45 = """
[[block]]
do = "move"
joint = "arm.pivot"
to = 45.0
unit = "deg"

[[block]]
do = "expect"
that = { joint = { name = "arm.pivot", above = 40.0, unit = "deg" } }
"""


def test_move_then_expect_finishes(tmp_path):
    r = run(tmp_path, MOVE_45)
    assert r.status == "finished", r.outcome
    assert r.outcome == "finished"
    assert len(r.checks) == 1 and r.checks[0]["passed"]
    assert "arm.pivot passes 40°" in r.checks[0]["line"]
    assert abs(r.signals["arm.pivot"][-1] - 45.0) < 2.0
    assert r.signals["t"][0] == pytest.approx(SETTLE_S, abs=1e-6)
    assert set(r.signals) >= {"t", "tilt", "arm.pivot", "arm.pivot.current"}
    assert all(len(v) == len(r.signals["t"]) for v in r.signals.values())


def test_samples_and_view(tmp_path):
    r = run(tmp_path, '[[block]]\ndo = "wait"\nseconds = 1.0\n')
    assert r.status == "finished"
    assert r.t_end == pytest.approx(SETTLE_S + 1.0, abs=2e-3)
    ts = [t for t, _ in r.motion]
    assert len(ts) == pytest.approx(MOTION_HZ * 1.0 + 1, abs=2)
    assert np.allclose(np.diff(ts), 1.0 / MOTION_HZ, atol=2e-3)
    assert all(p.shape == (len(r.movers), 7) for _, p in r.motion)
    assert len(r.signals["t"]) == pytest.approx(SIGNAL_HZ * 1.0 + 1, abs=2)
    assert r.view["geoms"] and r.view["world_geoms"]


def test_throughout_check_fails(tmp_path):
    blocks = """
[[block]]
do = "expect"
that = { joint = { name = "arm.pivot", below = 30.0, unit = "deg" } }
throughout = true
""" + MOVE_45
    r = run(tmp_path, blocks)
    assert r.status == "check failed"
    assert r.outcome.startswith("a check failed: arm.pivot goes below 30° at ")
    assert not r.checks[0]["passed"] and r.checks[0]["throughout"]


def test_repeat_that_never_ends(tmp_path):
    """Review Focus 2: a repeat until whose condition never comes true ends at its max, naming the repeat."""
    blocks = """
[[block]]
do = "repeat"
until = { hanging_from = "rung4" }
max = 3

[[block.body]]
do = "wait"
seconds = 0.1
"""
    r = run(tmp_path, blocks, ladder=True)
    assert r.status == "repeat limit"
    assert "block 1" in r.outcome and "3 times" in r.outcome
    assert r.outcome == "repeat (block 1) stopped after 3 times without the robot hanging from rung4"
    assert r.t_end == pytest.approx(SETTLE_S + 0.3, abs=5e-3)


def test_wait_until_times_out(tmp_path):
    blocks = """
[[block]]
do = "wait_until"
until = { joint = { name = "arm.pivot", above = 80.0, unit = "deg" } }
timeout = 0.5
"""
    r = run(tmp_path, blocks)
    assert r.status == "timed out"
    assert r.t_end == pytest.approx(0.8, abs=5e-3)
    assert r.outcome == "waiting for arm.pivot to pass 80° timed out at 0.8 s (block 1)"


def test_nested_place_in_timeout(tmp_path):
    blocks = """
[[block]]
do = "wait"
seconds = 0.0

[[block]]
do = "repeat"
times = 2

[[block.body]]
do = "wait_until"
until = { tilt = { above = 45.0 } }
timeout = 0.2
"""
    r = run(tmp_path, blocks)
    assert r.status == "timed out"
    assert r.outcome.endswith("(block 1 inside block 2)")
    assert r.outcome.startswith("waiting for the robot to tilt more than 45° timed out at ")


def test_tipped_over(tmp_path):
    def tilt70(world):
        m, q = world.model, world.free_qadr
        h = math.radians(70.0) / 2
        m.qpos0[q + 2] += 0.4
        m.qpos0[q + 3:q + 7] = [math.cos(h), math.sin(h), 0.0, 0.0]

    r = run(tmp_path, '[[block]]\ndo = "wait"\nseconds = 2.0\n', pose=tilt70)
    assert r.status == "tipped over"
    assert r.outcome.startswith("the robot tipped over at ")


def test_time_limit(tmp_path):
    r = run(tmp_path, '[[block]]\ndo = "wait"\nseconds = 5.0\n', head="time_limit = 1.0")
    assert r.status == "time limit"
    assert r.outcome == "ran out of time (1 s)"
    assert r.t_end == pytest.approx(1.0, abs=2e-3)


def test_drive_forward(tmp_path):
    path = write_test(tmp_path, '[[block]]\ndo = "drive"\ndistance = 0.5\n')
    program = loads(path.read_text(encoding="utf-8"), path)
    world = world_for(program)
    r = TestRun(world, program).run()
    assert r.status == "finished", r.outcome
    i = r.movers.index("drive.chassis") if "drive.chassis" in r.movers else 0
    start, end = r.motion[0][1][i], r.motion[-1][1][i]
    heading = 2 * math.atan2(float(start[6]), float(start[3]))      # yaw from the start quaternion
    travel = (end[:2] - start[:2]) @ np.array([math.cos(heading), math.sin(heading)])
    assert travel > 0.45


def test_cancel(tmp_path):
    cancel = threading.Event()
    seen = []

    def on_sample(t, row):
        seen.append(t)
        if t > 0.5:
            cancel.set()

    r = run(tmp_path, '[[block]]\ndo = "wait"\nseconds = 5.0\n', on_sample=on_sample, cancel=cancel)
    assert r.status == "cancelled"
    assert r.t_end < 0.6 and seen


def test_problems_refuse_the_run(tmp_path):
    path = write_test(tmp_path, '[[block]]\ndo = "spin"\nmotor = "climber.crank"\nvolts = 3.0\n')
    program = loads(path.read_text(encoding="utf-8"), path)
    with pytest.raises(TestFileError, match='block 1: no joint "climber.crank" on this robot any more'):
        TestRun(world_for(program), program)


def _cli(argv) -> int:
    with pytest.raises(SystemExit) as e:
        main(argv)
    return e.value.code


def test_cli_exit_codes(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    ok = tmp_path / "ok"
    ok.mkdir()
    assert _cli(["test", "run", str(write_test(ok, MOVE_45))]) == 0
    out = capsys.readouterr().out
    assert "finished" in out and "passed: arm.pivot passes 40°" in out

    late = tmp_path / "late"
    late.mkdir()
    assert _cli(["test", "run", str(write_test(late, '[[block]]\ndo = "wait"\nseconds = 5.0\n',
                                                 head="time_limit = 0.5"))]) == 1
    assert "ran out of time (0.5 s)" in capsys.readouterr().out

    gone = tmp_path / "gone"
    gone.mkdir()
    path = write_test(gone, MOVE_45)
    (gone / "robot.toml").unlink()
    assert _cli(["test", "run", str(path)]) == 2
    assert "not found" in capsys.readouterr().err
