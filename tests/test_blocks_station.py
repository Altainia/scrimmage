"""The hopper fed from the station (examples/blocks/station): a hand-written bin on the stand-in robot, which starts
with its bumpers at the chute's low end on the built-in station field. The test feeds until 5 are in the hopper,
stops, and checks at least 80% of what was fed made it in; the run ends with the counts sentence."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from scrimmage.blocks.palette import problems
from scrimmage.blocks.program import load
from scrimmage.blocks.run import TestRun, world_for

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "blocks" / "station"


@pytest.fixture(scope="module")
def station():
    program = load(EXAMPLE / "feed.test.toml")
    world = world_for(program)
    return {"program": program, "world": world, "result": TestRun(world, program).run()}


def test_the_example_is_the_standin_at_the_station(station):
    raw = tomllib.loads((EXAMPLE / "hopper-standin.toml").read_text(encoding="utf-8"))
    assert raw["robot"]["standin"] is True
    assert raw["range"] == {"file": "station", "start": "at_station"}
    assert station["program"].hopper == ["hopper.bin"]
    assert problems(station["program"], station["world"]) == []


def test_the_station_example_catches_at_least_80_percent(station):
    r = station["result"]
    assert r.status == "finished", r.outcome
    assert r.checks[-1]["passed"], r.checks
    c = r.counts
    assert c["in"] >= 5
    assert c["in"] + c["stuck"] + c["missed"] == c["fed"] == len(r.feeds)
    assert re.match(r"^\d+ of \d+ made it in \(\d+%\)", r.outcome), r.outcome
