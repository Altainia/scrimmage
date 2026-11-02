"""Running a block test with no robot program (scrimmage/blocks/run.py) and `scrimmage test run`."""

from __future__ import annotations

import math
import re
import threading

import numpy as np
import pytest

from scrimmage.__main__ import main
from scrimmage.assembler.templates import new_config
from scrimmage.blocks.palette import palette, problems
from scrimmage.blocks.program import TestFileError, loads
from scrimmage.config import build_world, load_config
from test_blocks_conditions import IN, paw_geom, paw_world
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
    assert re.fullmatch(r"a check failed \(block 1\): expected arm\.pivot below 30°, but it was 3\d(\.\d)?° at 0\.\d s",
                        r.outcome), r.outcome
    assert not r.checks[0]["passed"] and r.checks[0]["throughout"]
    assert r.checks[0]["line"].startswith("failed (block 1): expected arm.pivot below 30°, but it was ")


def test_a_failed_tilt_check_says_how_far_it_tilted(tmp_path):
    r = run(tmp_path, '[[block]]\ndo = "expect"\nthat = { tilt = { above = 20.0 } }\n')
    assert re.fullmatch(r"a check failed \(block 1\): expected the robot to tilt more than 20°, "
                        r"but it tilted \d(\.\d)?° at 0\.3 s", r.outcome), r.outcome


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
    assert r.outcome == "ran out of time (1 s), waiting 5 s (block 1)"
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


def test_time_limit_names_the_repeat_under_way(tmp_path):
    """Review Focus 2: a repeat until that the time limit ends names the repeat and what it was doing."""
    blocks = """
[[block]]
do = "repeat"
until = { hanging_from = "rung4" }
max = 100

[[block.body]]
do = "wait_until"
until = { touch = { robot = "drive", field = "rung4" } }
timeout = 5.0
"""
    r = run(tmp_path, blocks, head="time_limit = 1.0", ladder=True)
    assert r.status == "time limit"
    assert r.outcome == "ran out of time (1 s) during repeat (block 1), waiting for drive to touch rung4"


def test_a_repeat_of_instant_blocks_goes_round_once_a_step(tmp_path):
    blocks = """
[[block]]
do = "repeat"
times = 5

[[block.body]]
do = "spin"
motor = "arm.pivot"
volts = 0.0
"""
    r = run(tmp_path, blocks)
    assert r.status == "finished"
    assert r.t_end == pytest.approx(SETTLE_S + 4 * 0.001, abs=1e-6)       # rounds 2 to 5, one a step


def test_drive_times_out(tmp_path):
    r = run(tmp_path, '[[block]]\ndo = "drive"\ndistance = 5.0\ntimeout = 0.3\n')
    assert r.status == "timed out"
    assert re.fullmatch(r"drive \(block 1\) didn't reach 5 m in 0\.3 s \(went 0\.\d\d m\)", r.outcome), r.outcome


def test_move_timeout_says_where_the_joint_stopped(tmp_path):
    r = run(tmp_path, '[[block]]\ndo = "move"\njoint = "arm.pivot"\nto = 120.0\nunit = "deg"\ntimeout = 0.5\n')
    assert r.status == "timed out"
    m = re.fullmatch(r"arm\.pivot stopped at ([\d.]+)° short of 120° \(block 1\)", r.outcome)
    assert m and 85.0 < float(m.group(1)) < 95.0, r.outcome           # at its 90° stop


def test_a_time_limit_inside_the_settle_is_refused(tmp_path):
    with pytest.raises(TestFileError, match="the time limit must be longer than 0.3 s"):
        loads('[test]\nrobot = "r.toml"\ntime_limit = 0.3\n', tmp_path / "t.test.toml")


def test_drive_timeout_goes_with_distance(tmp_path):
    with pytest.raises(TestFileError, match="timeout goes with distance, not seconds"):
        loads('[test]\nrobot = "r.toml"\n[[block]]\ndo = "drive"\nseconds = 1.0\ntimeout = 2.0\n',
              tmp_path / "t.test.toml")


def test_a_swerve_robot_drives():
    """Sub-project 21 lifts ruling R6 (drive on a tank drive only): a swerve robot's drive block has no problems."""
    world = build_world(load_config("configs/swerve.toml"))
    assert palette(world, [])["drive"] is True and palette(world, [])["drive_kind"] == "swerve"
    program = loads('[test]\nrobot = "swerve.toml"\n[[block]]\ndo = "drive"\ndistance = 1.0\n', "t.test.toml")
    assert problems(program, world) == []


def _resting_on_rung1(folder):
    """The box robot of the conditions tests, its `arm.paw` hook 1 cm over rung1 at the start, so it rests on the rung
    by the end of the settle."""
    w = paw_world(folder)
    m, d = w.model, w.data
    q = w.free_qadr
    x, y, heading = w.test_range.range.starts["below"]
    w.test_range.range.starts["below"] = (x + 1.0, y, heading)     # under the rungs: every reset starts it there
    w.reset()
    paw = paw_geom(w)
    m.qpos0[q + 2] += 24 * IN + 0.01 - (float(d.geom_xpos[paw][2]) - float(m.geom_size[paw][2]))
    return w


def _paw_run(folder, blocks: str):
    program = loads(f'[test]\nrobot = "r.toml"\nhooks = ["arm.paw"]\n{blocks}', folder / "t.test.toml")
    return TestRun(_resting_on_rung1(folder), program).run()


def test_first_block_sees_a_hook_resting_on_a_bar(tmp_path):
    r = _paw_run(tmp_path, '[[block]]\ndo = "expect"\nthat = { touch = { robot = "hook", field = "rung1" } }\n')
    assert r.status == "finished", r.outcome
    assert r.checks[0]["passed"]


def test_a_failed_touch_or_hang_check_says_what_didnt_happen(tmp_path):
    r = _paw_run(tmp_path / "a", '[[block]]\ndo = "expect"\nthat = { touch = { robot = "hook", field = "rung2" } }\n')
    assert r.outcome == "a check failed (block 1): no hook touched rung2 at 0.3 s"
    r = _paw_run(tmp_path / "b", '[[block]]\ndo = "wait"\nseconds = 0.3\n\n'
                                  '[[block]]\ndo = "expect"\nthat = { hanging_from = "next bar" }\n')
    assert r.outcome == "reached rung1, then a check failed (block 2): the robot wasn't hanging from rung2 at 0.6 s"


def test_world_for_applies_the_setup(tmp_path):
    robot = new_config("standin") + '\n[range]\nfile = "station"\nseed = 3\n'
    (tmp_path / "robot.toml").write_text(robot, encoding="utf-8")
    path = tmp_path / "t.test.toml"
    path.write_text('[test]\nrobot = "robot.toml"\n\n[setup]\nball = "2017-fuel"\nseed = 9\n'
                    "station = { feeder = { stock = 4 } }\n", encoding="utf-8")
    w = world_for(loads(path.read_text(encoding="utf-8"), path))
    assert w.test_range.range.ball.diameter == pytest.approx(0.127) and w.test_range.feeder.seed == 9
    assert len(w.parts.layout.station_slots["feeder"]) == 4


def test_setup_on_the_plain_field_says_it_needs_a_field(tmp_path):
    (tmp_path / "robot.toml").write_text(new_config("standin"), encoding="utf-8")
    path = tmp_path / "t.test.toml"
    path.write_text('[test]\nrobot = "robot.toml"\n\n[setup]\nseed = 2\n', encoding="utf-8")
    with pytest.raises(TestFileError, match="need a field; this robot is on the plain field"):
        world_for(loads(path.read_text(encoding="utf-8"), path))


def test_setup_config_leaves_the_original_alone():
    from scrimmage.blocks.run import setup_config
    raw = {"range": {"file": "station", "station": {"feeder": {"stock": 1}}}}
    out = setup_config(raw, {"station": {"feeder": {"interval": 2.0}}})
    assert out["range"]["station"] == {"feeder": {"stock": 1, "interval": 2.0}}
    assert raw["range"]["station"] == {"feeder": {"stock": 1}} and setup_config(raw, {}) is raw
