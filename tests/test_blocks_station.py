"""The hopper fed from the station (examples/blocks/station): a hand-written bin on the stand-in robot, which starts
with its bumpers at the chute's low end on the built-in station field. The test feeds until 5 are in the hopper,
stops, and checks at least 80% of what was fed made it in; the run ends with the counts sentence."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import numpy as np
import pytest

from scrimmage.blocks.palette import problems
from scrimmage.blocks.program import load
from scrimmage.blocks.run import TestRun, world_for
from scrimmage.testrange.spec import load_range

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "blocks" / "station"
RELEASE_Z = load_range("station", ROOT).stations[0].release[2]      # m: the station's release height


@pytest.fixture(scope="module")
def station():
    program = load(EXAMPLE / "feed.test.toml")
    world = world_for(program)
    m, d = world.model, world.data
    released: dict[str, np.ndarray] = {}        # each fed ball's velocity at the first sample after it's released

    def on_sample(t, row):
        for name in world.parts.layout.ball_names:
            b = m.body(name).id
            if name not in released and d.xpos[b][2] > RELEASE_Z - 0.01:
                released[name] = d.cvel[b][3:].copy()

    result = TestRun(world, program).run(on_sample=on_sample)
    return {"program": program, "world": world, "result": result, "released": released}


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


def test_the_station_spread_varies_each_feed(station):
    """The example's [setup] gives the station a spread, so the seed means something: no two feeds start alike."""
    assert station["program"].setup["station"]["feeder"] == {"spread_yaw_deg": 5.0, "spread_pitch_deg": 5.0,
                                                             "spread_speed": 0.2}
    v = list(station["released"].values())
    assert len(v) == len(station["result"].feeds) >= 5
    assert any(not np.allclose(a, v[0], atol=1e-3) for a in v[1:]), v
