"""The CANrange intake example (examples/blocks/canrange): its robot config parses, its sensor is a CTRE CANrange, the
README quotes the sensor as the config has it, and the test passes."""

from __future__ import annotations

import tomllib
from pathlib import Path

from scrimmage.blocks.palette import problems
from scrimmage.blocks.program import load
from scrimmage.blocks.run import TestRun, world_for

CANRANGE = Path(__file__).resolve().parent.parent / "examples" / "blocks" / "canrange"


def test_the_config_parses_with_a_canrange_sensor():
    program = load(CANRANGE / "stop.test.toml")
    world = world_for(program)
    (spec,) = [s for s in world.sensors.specs if s.name == "bin"]
    assert spec.device == "CANrange" and spec.device_id == 2 and spec.length == 0.40
    assert problems(program, world) == []
    raw = tomllib.loads((CANRANGE / "canrange-standin.toml").read_text(encoding="utf-8"))
    assert raw["sensor"][0]["ctre"] == "CANrange:2"
    readme = (CANRANGE / "README.md").read_text(encoding="utf-8")
    assert 'toward = [0, -1, 0]\nmax_range = 0.40\nthreshold = 0.25\nctre = "CANrange:2"' in readme
    assert raw["sensor"][0]["threshold"] == 0.25    # the robot code's threshold, in the README, is the config's
    assert ("cfg.ProximityParams.ProximityThreshold = 0.25;" in readme
            and "bin.getConfigurator().apply(cfg);" in readme
            and readme.index("apply(cfg)") < readme.index("getIsDetected"))


def test_the_test_passes_with_the_ball_in_the_bin():
    program = load(CANRANGE / "stop.test.toml")
    result = TestRun(world_for(program), program).run()
    assert result.status == "finished", result.outcome
    assert [c["line"] for c in result.checks] == ["passed: bin is on at 3.0 s (block 7)",
                                                  "passed: a ball reaches intake.hopper at 3.0 s (block 8)"]
