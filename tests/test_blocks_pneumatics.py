"""Pneumatics under the Test tab (sub-project 23, goal 8): a stand-in robot (examples/blocks/intake's first sections)
with a free cylinder on drive.top. The set_cylinder block sets its valve with no device ID (plan decision D3); the
cylinder and pressure conditions, the air signals and the run's air line."""

from __future__ import annotations

from pathlib import Path

import pytest

from scrimmage.blocks.conditions import describe
from scrimmage.blocks.palette import palette, problems
from scrimmage.blocks.program import Cond, TestFileError, loads
from scrimmage.blocks.run import TestRun, air_lines, world_for

ROBOT = """
[model]
timestep = 0.001

[robot]
standin = true

[battery]
nominal_voltage = 12.5
resistance = 0.02

[lockstep]
step = 0.005

[[part]]
name = "drive"
stock = "chassis"
mass = 45.0
com_offset = [0.0, 0.0, 0.0]
ids = { gyro = { device = "ADXRS450[0]", axis = "x", cw_positive = true } }

[[part]]
name = "dt"
stock = "differential"
attach = "drive.center"
ids = { left = "PWM:0", right = "PWM:1", left_encoder = { channel_a = 0, counts_per_rev = 2048 }, right_encoder = { channel_a = 2, counts_per_rev = 2048 } }

[[part]]
name = "cyl"
stock = "cylinder"
attach = "drive.top"
"""


def _program(tmp: Path, blocks: str, cylinder: str = "", pneumatics: str = ""):
    """`cylinder`: more lines of the cylinder's [[part]] table; `pneumatics`: a [pneumatics] table's lines."""
    text = ROBOT + cylinder + (f"\n[pneumatics]\n{pneumatics}" if pneumatics else "")
    (tmp / "robot.toml").write_text(text, encoding="utf-8")
    return loads('[test]\nrobot = "robot.toml"\n' + blocks, tmp / "air.test.toml")


def _set(to: str, cylinder: str = "cyl") -> str:
    return f'[[block]]\ndo = "set_cylinder"\ncylinder = "{cylinder}"\nto = "{to}"\n'


def _wait(seconds: float) -> str:
    return f'[[block]]\ndo = "wait"\nseconds = {seconds}\n'


def _cyl(state: str, cylinder: str = "cyl") -> str:
    return f'{{ cylinder = {{ cylinder = "{cylinder}", is = "{state}" }} }}'


def test_set_cylinder_extends(tmp_path):
    p = _program(tmp_path, _set("extend")
                 + f'[[block]]\ndo = "wait_until"\nuntil = {_cyl("extended")}\ntimeout = 2\n'
                 + '[[block]]\ndo = "expect"\nthat = { pressure = { below = 120 } }\n')
    world = world_for(p)
    result = TestRun(world, p).run()
    assert result.passed, result.outcome
    assert world.pneumatics.always_closed_loop
    air = result.air
    assert air["start_psi"] == pytest.approx(120.0)
    assert air["end_psi"] < 120.0
    assert air["low_psi"] <= air["end_psi"]
    assert air["below_working_t"] is None
    assert {"air.psi", "air.working_psi", "cyl.extension"} <= set(result.signals)
    assert max(result.signals["cyl.extension"]) > 5.75          # in: the 6 in stroke, at its end
    assert result.signals["air.working_psi"][-1] == pytest.approx(60.0)
    lines = air_lines(result)
    assert len(lines) == 1
    assert lines[0].startswith("air: 120 psi at the start, ")
    assert "Pneumatic numbers are estimates (UNCALIBRATED): see docs/pneumatics.md." in result.notes


def test_air_runs_out(tmp_path):
    """Review Focus 5: a big cylinder on one tank with no compressor drains it below the working pressure."""
    cycle = _set("extend") + _wait(0.5) + _set("retract") + _wait(0.5)
    body = cycle.replace("[[block]]", "[[block.body]]")
    p = _program(tmp_path, '[[block]]\ndo = "repeat"\ntimes = 6\n' + body,
                 cylinder="bore = 0.0508\n",
                 pneumatics='compressor = "none"\nstart_psi = 70\ntanks = 1\n')
    result = TestRun(world_for(p), p).run()
    assert result.status == "finished", result.outcome
    air = result.air
    assert air["start_psi"] == pytest.approx(70.0)
    assert air["compressor_s"] == 0.0
    assert air["below_working_t"] is not None
    assert -0.01 < air["low_psi"] < 60.0         # down toward the atmosphere, never below it
    lines = air_lines(result)
    assert len(lines) == 2
    t = air["below_working_t"]
    assert lines[1] == (f"the tanks fell below the 60 psi working pressure at {t:.1f} s: the cylinders pushed "
                        "weaker from then on")
    assert any("fell below the 60 psi working pressure" in line for line in lines)


def test_air_line_words():
    from scrimmage.blocks.run import RunResult
    r = RunResult("finished", "finished", 20.0, air={"start_psi": 120.0, "end_psi": 84.2, "low_psi": 81.4,
                                                     "low_t": 14.23, "compressor_s": 9.0, "below_working_t": None})
    assert air_lines(r) == ["air: 120 psi at the start, 84 psi at the end, lowest 81 psi at 14.2 s; the compressor "
                            "ran 9.0 s"]
    assert air_lines(RunResult("finished", "finished", 1.0)) == []


def test_set_cylinder_names_checked(tmp_path):
    p = _program(tmp_path, _set("extend", "arm"))
    world = world_for(p)
    msgs = [x["message"] for x in problems(p, world)]
    assert msgs == ["arm isn't a cylinder; the robot's cylinders: cyl"]
    p = _program(tmp_path, '[[block]]\ndo = "expect"\nthat = ' + _cyl("extended", "arm") + "\n")
    assert [x["message"] for x in problems(p, world_for(p))] == ["arm isn't a cylinder; the robot's cylinders: cyl"]
    assert palette(world, [])["cylinders"] == [{"name": "cyl", "double": False}]


def test_off_on_a_single_solenoid_retracts(tmp_path):
    """An unbound valve counts as single under the Test tab: off retracts it."""
    p = _program(tmp_path, _set("extend") + _wait(1.0) + _set("off") + _wait(1.0)
                 + '[[block]]\ndo = "expect"\nthat = ' + _cyl("retracted") + "\n")
    result = TestRun(world_for(p), p).run()
    assert result.passed, result.outcome
    assert max(result.signals["cyl.extension"]) > 5.75


def test_off_on_a_double_solenoid_holds(tmp_path):
    p = _program(tmp_path, _set("extend") + _wait(1.0) + _set("off") + _wait(1.0)
                 + '[[block]]\ndo = "expect"\nthat = ' + _cyl("extended") + "\n",
                 cylinder='ids = { valve = "PCM:3,4" }\n')
    world = world_for(p)
    assert palette(world, [])["cylinders"] == [{"name": "cyl", "double": True}]
    result = TestRun(world, p).run()
    assert result.passed, result.outcome


def test_no_cylinders_no_air(tmp_path):
    (tmp_path / "robot.toml").write_text(ROBOT.split("[[part]]\nname = \"cyl\"")[0], encoding="utf-8")
    p = loads('[test]\nrobot = "robot.toml"\n' + _set("extend"), tmp_path / "air.test.toml")
    world = world_for(p)
    assert [x["message"] for x in problems(p, world)] == ["this robot has no cylinder to set"]
    p = loads('[test]\nrobot = "robot.toml"\n' + _wait(0.1), tmp_path / "air.test.toml")
    result = TestRun(world, p).run()
    assert result.air is None
    assert air_lines(result) == []
    assert not any("Pneumatic" in n for n in result.notes)
    assert "air.psi" not in result.signals
    assert palette(world, [])["cylinders"] == []


def test_pressure_needs_cylinders(tmp_path):
    (tmp_path / "robot.toml").write_text(ROBOT.split("[[part]]\nname = \"cyl\"")[0], encoding="utf-8")
    p = loads('[test]\nrobot = "robot.toml"\n[[block]]\ndo = "expect"\nthat = { pressure = { above = 50 } }\n',
              tmp_path / "air.test.toml")
    assert [x["message"] for x in problems(p, world_for(p))] == ["this robot has no air system (no cylinder)"]


def test_file_shape(tmp_path):
    def load(blocks: str):
        return loads('[test]\nrobot = "robot.toml"\n' + blocks, tmp_path / "x.test.toml")

    with pytest.raises(TestFileError, match="exactly one of above or below"):
        load('[[block]]\ndo = "expect"\nthat = { pressure = { above = 50, below = 100 } }\n')
    with pytest.raises(TestFileError, match="exactly one of above or below"):
        load('[[block]]\ndo = "expect"\nthat = { pressure = { side = "working" } }\n')
    with pytest.raises(TestFileError, match='"extend", "retract" or "off"'):
        load(_set("open"))
    with pytest.raises(TestFileError, match='"stored" or "working"'):
        load('[[block]]\ndo = "expect"\nthat = { pressure = { side = "tank", above = 50 } }\n')
    with pytest.raises(TestFileError, match='"extended" or "retracted"'):
        load('[[block]]\ndo = "expect"\nthat = ' + _cyl("out") + "\n")
    p = load(_set("off") + '[[block]]\ndo = "expect"\nthat = { pressure = { side = "working", above = 50 } }\n')
    assert p.blocks[0].args == {"cylinder": "cyl", "to": "off"}
    assert p.blocks[1].args["that"] == Cond("pressure", {"side": "working", "above": 50.0})
    p = load('[[block]]\ndo = "expect"\nthat = { pressure = { below = 100 } }\n')
    assert p.blocks[0].args["that"].args == {"side": "stored", "below": 100.0}


def test_words():
    assert describe(Cond("cylinder", {"cylinder": "cyl", "is": "extended"})) == "cyl is extended"
    assert describe(Cond("pressure", {"side": "stored", "above": 80.0})) == "the air pressure is above 80 psi"
    assert describe(Cond("pressure", {"side": "working", "below": 60.0})) == "the working pressure is below 60 psi"
