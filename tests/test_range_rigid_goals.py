"""Goals count any piece kind: a rigid NOTE through an opening counts, a miss doesn't, a rigid-only field's goal
counts its first kind, and a throat narrower than a rigid piece's middle side warns (it may pass on edge)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from range_fixtures import range_cfg
from scrimmage import units
from scrimmage.protocol import HalState
from scrimmage.testrange.balls import BallState
from scrimmage.testrange.build import BALL_BITS
from scrimmage.testrange.spec import RangeError, parse_range, passing_width, piece_sides
from scrimmage.world import World

IN = 0.0254
NOTE_ONLY = '''
[range]
name = "speaker"

[piece.note]
preset = "2024-note"

[[floor_balls]]
at = [[12.0, 6.0]]
tip_deg = 90
'''
GOAL = '''
[[goal]]
name = "speaker"
pos = [12.0, 2.0]
rim_height = 1.0
opening = {{ diameter = 0.6 }}
{extra}
'''
MIXED = '''
[range]
name = "mixed"
ball = "2025-algae"

[piece.note]
preset = "2024-note"

[[floor_balls]]
at = [[12.0, 6.0]]

[[floor_balls]]
at = [[12.0, 4.0]]
kind = "note"
tip_deg = 90
'''


def parse(tmp_path, text):
    return parse_range(tomllib.loads(text), tmp_path / "r.toml")


def note_world(tmp_path, extra="") -> World:
    path = tmp_path / "field.toml"
    path.write_text(NOTE_ONLY + GOAL.format(extra=extra), encoding="utf-8")
    return World(range_cfg(range={"file": str(path), "floor_balls": True}), None)


def drop_note(w, x: float, y: float) -> None:
    b = w.test_range.balls
    note = b.kinds["note"]
    b.place(0, (x, y, 1.6), (0.0, 0.0, -1.0), (0.0, 0.0, 0.0), BALL_BITS, quat=note.orient(90.0, 0.0))
    for _ in range(int(round(1.5 / w.model.opt.timestep))):
        w.step(HalState())


def test_a_goal_names_a_rigid_kind(tmp_path):
    rng = parse(tmp_path, MIXED + GOAL.format(extra='kind = "note"'))
    assert rng.goals[0].kind == "note"
    assert parse(tmp_path, MIXED + GOAL.format(extra="")).goals[0].kind is None


def test_a_rigid_only_field_goal_counts_its_first_kind(tmp_path):
    w = note_world(tmp_path)
    assert w.test_range.balls.counted_by["speaker"].tolist() == [True]


def test_a_note_dropped_through_the_opening_counts(tmp_path):
    w = note_world(tmp_path)
    drop_note(w, 12.0, 2.0)
    assert w.test_range.counts()["goals"]["speaker"] == 1
    assert w.test_range.balls.state[0] != BallState.FIELD


def test_a_note_that_misses_counts_nothing(tmp_path):
    w = note_world(tmp_path)
    drop_note(w, 13.5, 2.0)
    assert w.test_range.counts()["goals"]["speaker"] == 0


def test_passing_width_is_the_diameter_or_the_smallest_side(tmp_path):
    rng = parse(tmp_path, MIXED)
    assert passing_width(rng.kinds["2025-algae"]) == pytest.approx(0.4064)
    note = rng.kinds["note"]
    assert passing_width(note) == pytest.approx(2 * IN, abs=1e-4)
    assert piece_sides(note)[1] == pytest.approx(14 * IN, abs=1e-3)


def test_a_throat_narrower_than_the_middle_side_warns_on_edge(tmp_path):
    rng = parse(tmp_path, NOTE_ONLY + GOAL.format(extra="funnel = { depth = 0.2, slope_deg = 60.0, throat = 0.3 }"))
    assert any("edge" in w for w in rng.warnings)
    rng = parse(tmp_path, NOTE_ONLY + GOAL.format(extra="funnel = { depth = 0.2, slope_deg = 60.0, throat = 0.5 }"))
    assert not any("edge" in w for w in rng.warnings)


def test_a_throat_thinner_than_the_piece_is_still_an_error(tmp_path):
    with pytest.raises(RangeError, match="thinnest side"):
        parse(tmp_path, NOTE_ONLY + GOAL.format(extra="funnel = { depth = 0.2, slope_deg = 60.0, throat = 0.04 }"))


def test_a_ball_throat_is_unchanged(tmp_path):
    ball = '[range]\nname = "b"\nball = "2025-algae"\n'
    with pytest.raises(RangeError, match=r"no wider than the ball \(" + re.escape(units.fmt(0.4064, "length")) + r"\)"):
        parse(tmp_path, ball + GOAL.format(extra="funnel = { depth = 0.2, slope_deg = 60.0, throat = 0.4 }"))
