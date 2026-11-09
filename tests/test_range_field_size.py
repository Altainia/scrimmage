"""A range's field size: `[range] field = { length, width }` (2024 is 16.541 x 8.211 m), the generated field's walls
following it, and the warning for a goal or a station outside them."""

from pathlib import Path

import numpy as np
import pytest

from range_fixtures import range_cfg
from scrimmage.checks import Said
from scrimmage.testrange.spec import BUILTIN_RANGES, FIELD_LENGTH, FIELD_WIDTH, RangeError, load_range

BASE = '''
[range]
name = "test"
ball = "2026-fuel"
{field}
[[goal]]
name = "g"
pos = {goal}
rim_height = 2.0
opening = {{ sides = 6, across_flats = 1.0 }}
'''


def load(tmp_path, field="", goal="[4.0, 4.0]", more=""):
    (tmp_path / "r.toml").write_text(BASE.format(field=field, goal=goal) + more)
    return load_range("r.toml", tmp_path)


def test_default_field_is_the_generated_one(tmp_path):
    rng = load(tmp_path)
    assert (rng.field_length, rng.field_width) == (FIELD_LENGTH, FIELD_WIDTH) == (16.54, 8.07)
    assert rng.warnings == ()


@pytest.mark.parametrize("name", BUILTIN_RANGES)
def test_builtin_ranges_keep_their_field_and_have_no_warnings(name):
    rng = load_range(name, Path("."))
    assert (rng.field_length, rng.field_width) == (FIELD_LENGTH, FIELD_WIDTH)
    assert rng.warnings == ()


def test_2024_field_size_lets_pieces_lie_by_the_far_wall(tmp_path):
    piece = '[[floor_balls]]\nat = [[4.0, 8.12]]\n'          # 8.12 m: past the 8.07 m field's wall
    with pytest.raises(RangeError, match="off the field"):
        load(tmp_path, more=piece)
    rng = load(tmp_path, field="field = { length = 16.541, width = 8.211 }", more=piece)
    assert (rng.field_length, rng.field_width) == (16.541, 8.211)
    assert rng.floor_balls.tolist() == [[4.0, 8.12]]


@pytest.mark.parametrize("field, match", [
    ("field = { length = 16.541 }", "missing required key 'width'"),
    ("field = { length = 16.541, width = -8 }", "width must be positive"),
    ("field = { length = 16.541, width = 8.211, depth = 1 }", "unknown key 'depth'"),
    ("field = { length = 100.0, width = 8.211 }", "length must be from 2 to 40 m"),
    ("field = 16.5", "must be a table"),
])
def test_bad_field_refused_in_words(tmp_path, field, match):
    with pytest.raises(RangeError, match=match):
        load(tmp_path, field=field)


def test_a_goal_outside_the_walls_is_warned_about(tmp_path):
    # the dry run's amp_blue at y = 8.21 on the 8.07 m field: accepted with no word, and unreachable
    rng = load(tmp_path, goal="[1.84, 8.21]")
    assert len(rng.warnings) == 1
    w = rng.warnings[0]
    assert isinstance(w, Said)
    assert "goal 'g'" in w and "outside the field" in w and "8.07" in w and "field = { length, width }" in w
    assert load(tmp_path, field="field = { length = 16.541, width = 8.211 }", goal="[1.84, 8.21]").warnings == ()


def test_a_station_outside_the_walls_is_refused_on_its_own_field(tmp_path):
    station = ('[[station]]\nname = "s"\nrelease = [15.0, 8.12, 1.0]\nheading_deg = 0.0\n')
    with pytest.raises(RangeError, match=r"release point.*off the field \(0\.\.16\.54 x 0\.\.8\.07"):
        load(tmp_path, more=station)
    rng = load(tmp_path, field="field = { length = 16.541, width = 8.211 }", more=station)
    assert rng.stations[0].release == (15.0, 8.12, 1.0)


def test_the_generated_field_follows_the_range(tmp_path):
    from scrimmage.parts.assemble import assemble
    (tmp_path / "r.toml").write_text(BASE.format(field="field = { length = 16.541, width = 8.211 }",
                                                 goal="[4.0, 4.0]"))
    built = assemble(range_cfg(range={"file": "r.toml"}), tmp_path)
    m = built.model
    carpet = m.geom("carpet")
    assert np.allclose(m.geom_pos[carpet.id][:2], [16.541 / 2, 8.211 / 2])
    assert np.allclose(m.geom_size[carpet.id][:2], [16.541 / 2, 8.211 / 2])
    # the walls' inner faces on the field's edges
    left, red = m.geom("wall_left").id, m.geom("wall_red").id
    assert m.geom_pos[left][1] - m.geom_size[left][1] == pytest.approx(8.211)
    assert m.geom_pos[red][0] - m.geom_size[red][0] == pytest.approx(16.541)
    plain = assemble(range_cfg(), Path("."))
    left = plain.model.geom("wall_left").id
    assert plain.model.geom_pos[left][1] - plain.model.geom_size[left][1] == pytest.approx(8.07)


def test_the_warning_reaches_the_built_robot(tmp_path):
    from scrimmage.parts.assemble import assemble
    (tmp_path / "r.toml").write_text(BASE.format(field="", goal="[1.84, 8.21]"))
    built = assemble(range_cfg(range={"file": "r.toml"}), tmp_path)
    assert any("goal 'g'" in w and "outside the field" in w for w in built.warnings)
