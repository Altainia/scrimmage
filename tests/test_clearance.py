"""Clearance between the robot and a field element (scrimmage/blocks/clearance.py): the Test tab's `clears`
condition, the `clearance.<element>` chart signal and the run's closest-approach lines."""

from __future__ import annotations

import math
import re
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

from scrimmage.__main__ import main
from scrimmage.assembler.templates import new_config
from scrimmage.blocks import store
from scrimmage.blocks import clearance as clearance_mod
from scrimmage.blocks.clearance import clearance
from scrimmage.blocks.conditions import describe
from scrimmage.blocks.palette import problems
from scrimmage.blocks.program import Cond, TestFileError, dumps, loads
from scrimmage.blocks.run import TestRun, clearance_lines, world_for

IN = 0.0254
START = (2.5, 4.0)


def _field(box: str) -> str:
    return f"""
[range]
name = "a box"
ball = "2026-fuel"

[start.here]
pose = [{START[0]}, {START[1]}, 0.0]

[[element]]
name = "box"
kind = "box"
{box}
"""


OVER = _field(f"center = [{START[0]}, {START[1]}]\nsize = [1.5, 1.5, 0.1]\nbase_height = 0.30")   # over the robot
FAR = _field("center = [7.0, 6.5]\nsize = [0.3, 0.3, 0.3]")                                       # across the field
AHEAD = _field(f"center = [{START[0] + 1.2}, {START[1]}]\nsize = [0.3, 1.0, 0.3]")              # in the robot's way

DRIVE = """
[[block]]
do = "spin"
motor = "dt.left_2"
volts = 6.0

[[block]]
do = "spin"
motor = "dt.right_2"
volts = 6.0
"""

WAIT = '\n[[block]]\ndo = "wait"\nseconds = 0.2\n'
CLEARS = '\n[[block]]\ndo = "expect"\nthat = { clears = { element = "box", at_least_in = 0.5 } }\nthroughout = true\n'


def _program(folder, field: str, blocks: str, chart: list[str] | None = None, parts: str = ""):
    (folder / "box.toml").write_text(field, encoding="utf-8")
    (folder / "robot.toml").write_text(new_config("standin") + parts + '\n[range]\nfile = "box.toml"\n'
                                       'start = "here"\n', encoding="utf-8")
    head = f"chart = {chart!r}\n".replace("'", '"') if chart is not None else ""
    path = folder / "t.test.toml"
    path.write_text(f'[test]\nrobot = "robot.toml"\n{head}{blocks}', encoding="utf-8")
    return loads(path.read_text(encoding="utf-8"), path)


def _robot_top(world) -> float:
    """The highest point of the robot's colliding geoms (the treads stand a little above the chassis plate)."""
    m, d = world.model, world.data
    root = m.body_rootid[world.robot_body]
    top = -math.inf
    for g in range(m.ngeom):
        if m.body_rootid[m.geom_bodyid[g]] != root or not (m.geom_contype[g] or m.geom_conaffinity[g]):
            continue
        r = d.geom_xmat[g].reshape(3, 3)
        top = max(top, float(d.geom_xpos[g][2] + r[2] @ m.geom_aabb[g][:3] + np.abs(r[2]) @ m.geom_aabb[g][3:]))
    return top


def test_clearance_under_a_box(tmp_path):
    """The stand-in rests under a box whose bottom is 0.30 m up: the clearance is the box's bottom minus the robot's
    top, within 1 mm, in the chart signal (inches) and the run's closest line."""
    program = _program(tmp_path, OVER, WAIT, chart=["clearance.box"])
    world = world_for(program)
    r = TestRun(world, program).run()
    assert r.status == "finished", r.outcome
    want = 0.30 - _robot_top(world)
    assert 0.1 < want < 0.2
    assert clearance(world, "box") == pytest.approx(want, abs=0.001)
    assert r.signals["clearance.box"][-1] == pytest.approx(want / IN, abs=0.001 / IN)
    low = r.clearance["box"]
    assert low["min_in"] == pytest.approx(want / IN, abs=0.05) and low["t"] is not None
    assert clearance_lines(r) == [f"closest to box: {low['min_in']:.2f} in at {low['t']:.1f} s"]


def test_clearance_far(tmp_path):
    """Review Focus 4: nothing on the robot near the element. The clearance is inf, the chart shows NaN, the line
    reads "never within 20 in", and a clears check passes without crashing."""
    program = _program(tmp_path, FAR, CLEARS + WAIT, chart=["clearance.box"])
    world = world_for(program)
    r = TestRun(world, program).run()
    assert r.status == "finished" and r.passed, r.outcome
    assert clearance(world, "box") == math.inf
    assert all(math.isnan(v) for v in r.signals["clearance.box"])
    assert r.clearance == {"box": {"min_in": None, "t": None}}
    assert clearance_lines(r) == ["box: never within 20 in"]
    assert r.checks[0]["line"].startswith("passed: the robot clears box by at least 0.5 in from ")


ELEVATOR = """
[[part]]
name = "elevator"
stock = "elevator"
attach = "drive.top"
ids = { lift = "PWM:4" }
"""


def test_clearance_counts_only_colliding_parts(tmp_path):
    """Review Focus 4: a stock elevator with collide off doesn't count, so a box just over its top (and out of the
    chassis's reach) is never within 20 in; with collide = true the elevator's top is what comes close."""
    seen = {}
    for collide in (False, True):
        folder = tmp_path / str(collide)
        folder.mkdir()
        part = ELEVATOR + ("collide = true\n" if collide else "")
        program = _program(folder, FAR, "", parts=part)
        world = world_for(program)
        m, d = world.model, world.data
        mujoco.mj_forward(m, d)
        top = max(float(d.geom_xpos[g][2] + m.geom_aabb[g][5]) for g in range(m.ngeom)
                  if m.body(m.geom_bodyid[g]).name.startswith("elevator."))
        if not collide:
            assert top - _robot_top(world) > 0.6        # the chassis and treads are far below the box
        x, y = (float(v) for v in d.xpos[m.body("elevator.base").id][:2])
        box = _field(f"center = [{x}, {y}]\nsize = [0.3, 0.3, 0.1]\nbase_height = {top + 0.05}")
        program = _program(folder, box, WAIT, chart=["clearance.box"], parts=part)
        world = world_for(program)
        r = TestRun(world, program).run()
        assert r.status == "finished", r.outcome
        seen[collide] = r.clearance["box"]["min_in"]
    assert seen[False] is None
    assert seen[True] == pytest.approx(0.05 / IN, abs=0.5)


def test_clears_condition_fails_on_touch(tmp_path):
    """Driving into the box fails `expect clears box at least 0.5 throughout`, naming the time and the clearance."""
    program = _program(tmp_path, AHEAD, CLEARS + DRIVE + '\n[[block]]\ndo = "wait"\nseconds = 3.0\n')
    r = TestRun(world_for(program), program).run()
    assert r.status == "check failed", r.outcome
    m = re.fullmatch(r"a check failed \(block 1\): expected the robot to clear box by at least 0\.5 in, "
                     r"but it was (\d+\.\d\d) in at (\d+\.\d) s", r.outcome)
    assert m, r.outcome
    assert float(m[1]) < 0.5 and float(m[2]) > 0.5
    assert r.clearance["box"]["min_in"] < 0.5
    assert r.checks[0]["text"] == "the robot clears box by at least 0.5 in"


def test_clearance_cost(tmp_path, monkeypatch):
    """A run without a clears condition and without a clearance chart signal never measures a distance."""
    def boom(*args):
        raise AssertionError("mj_geomDistance called")

    monkeypatch.setattr(clearance_mod.mujoco, "mj_geomDistance", boom)
    program = _program(tmp_path, OVER, WAIT, chart=["tilt"])
    r = TestRun(world_for(program), program).run()
    assert r.status == "finished", r.outcome
    assert r.clearance is None and not any(k.startswith("clearance.") for k in r.signals)
    assert clearance_lines(r) == []
    program = _program(tmp_path, OVER, WAIT, chart=["clearance.box"])     # and one that asks for it does
    with pytest.raises(AssertionError, match="mj_geomDistance called"):
        TestRun(world_for(program), program).run()


def test_clears_file_shape(tmp_path):
    """The condition's keys and numbers, its round trip, its words and an element the field doesn't have."""
    program = _program(tmp_path, FAR, CLEARS)
    cond = program.blocks[0].args["that"]
    assert cond == Cond("clears", {"element": "box", "at_least_in": 0.5})
    assert loads(dumps(program), program.path).blocks == program.blocks
    assert describe(Cond("clears", {"element": "trench", "at_least_in": 1.0})) == \
        "the robot clears trench by at least 1 in"
    for bad, says in (("at_least_in = 0", "at_least_in must be more than 0"),
                      ("at_least_in = 25", "at_least_in can be at most 19.6"),
                      ('at_least_in = "1"', "at_least_in"),
                      ("at_least = 1", "'at_least' isn't something")):
        with pytest.raises(TestFileError, match=says):
            _program(tmp_path, FAR, CLEARS.replace("at_least_in = 0.5", bad))
    program = _program(tmp_path, FAR, CLEARS.replace('"box"', '"trench"'))
    assert [p["message"] for p in problems(program, world_for(program))] == ['no element "trench" on this field']


def test_clearance_reaches_the_cli_and_the_kept_run(tmp_path, capsys, monkeypatch):
    """`scrimmage test run` prints the closest line, and the kept run's result.json holds RunResult.clearance, which the
    Test tab's run_result words the same way."""
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    program = _program(tmp_path, OVER, WAIT, chart=["clearance.box"])
    with pytest.raises(SystemExit) as e:
        main(["test", "run", str(program.path), "--out", str(tmp_path / "runs")])
    out = capsys.readouterr().out
    assert e.value.code == 0, out
    line = re.search(r"^  (closest to box: \d+\.\d\d in at \d+\.\d s)$", out, re.M)
    assert line, out
    kept = store.load_result(tmp_path / "runs", "001")
    assert set(kept["clearance"]) == {"box"}
    assert clearance_lines(SimpleNamespace(clearance=kept["clearance"])) == [line[1]]
