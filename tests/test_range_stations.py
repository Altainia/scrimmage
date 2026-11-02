import math
import tomllib

import numpy as np
import pytest

from scrimmage.testrange.build_elements import chute_parts
from scrimmage.testrange.spec import BUILTIN_RANGES, RangeError, load_range, parse_range

HEAD = '''
[range]
name = "t"
ball = "2026-fuel"
'''
FEEDER = '''
[[station]]
name = "feeder"
release = [1.0, 4.0, 0.5]
heading_deg = 0.0
'''


def parse(tmp_path, text):
    return parse_range(tomllib.loads(text), tmp_path / "r.toml")


def test_a_station_parses_with_its_defaults(tmp_path):
    rng = parse(tmp_path, HEAD + FEEDER)
    st = rng.stations[0]
    assert st.interval == 1.0 and st.stock is None and st.pool() == 50 and st.chute is None
    assert st.direction(0.0, 30.0) == pytest.approx([math.cos(math.radians(30)), 0, -0.5])


def test_release_in_inches_and_spreads(tmp_path):
    rng = parse(tmp_path, HEAD + '''
[[station]]
name = "feeder"
release_in = [39.37007874015748, 157.48031496062993, 40]
heading_deg = 90.0
pitch_deg = 20
speed = 1.5
interval = 0.4
stock = 8
spread_yaw_deg = 5
spread_pitch_deg = 3
spread_speed = 0.2
spread_position_in = [0, 1, 2]
spread_rotation_deg = [0, 0, 10]
''')
    st = rng.stations[0]
    assert st.release == pytest.approx((1.0, 4.0, 40 * 0.0254))
    assert (st.interval, st.stock, st.pool(), st.spread_speed) == (0.4, 8, 8, 0.2)
    assert st.spread_position == pytest.approx((0, 0.0254, 0.0508))
    assert st.spread_rotation_deg == (0.0, 0.0, 10.0)


def test_a_release_inside_a_box_is_refused(tmp_path):
    text = HEAD + '''
[[element]]
name = "wall"
kind = "box"
center = [1.0, 4.0]
size = [0.2, 2.0, 1.0]
''' + FEEDER
    with pytest.raises(RangeError, match=r"'feeder': its release point at \(1, 4, 0.5\) is inside element 'wall'; "
                                         r"move it clear \(the ball's center needs 0.075 m all round\)"):
        parse(tmp_path, text)


def test_a_release_inside_an_obstacle_is_refused(tmp_path):
    text = HEAD + '''
[[obstacle]]
name = "post"
pos = [1.0, 4.0, 0.5]
size = [0.3, 0.3, 1.0]
''' + FEEDER
    with pytest.raises(RangeError, match="is inside obstacle 'post'"):
        parse(tmp_path, text)


def test_a_chute_through_the_carpet_is_refused(tmp_path):
    text = HEAD + FEEDER + "chute = { length = 2.0, slope_deg = 30.0 }\n"
    with pytest.raises(RangeError, match=r"chute: its low end would be 0\.\d+ m below the carpet; make it shorter"):
        parse(tmp_path, text)


def test_a_chute_narrower_than_the_ball_is_refused(tmp_path):
    text = (HEAD + FEEDER + "chute = { length = 0.5, slope_deg = 10.0, width = 0.15 }\n").replace(
        "2026-fuel", "2010-soccer-ball")
    with pytest.raises(RangeError, match=r"chute: 0.15 m wide, no wider than the ball \(0.2223 m\)"):
        parse(tmp_path, text)


def test_a_chute_without_a_width_fits_the_ball(tmp_path):
    st = parse(tmp_path, HEAD + FEEDER + "chute = { length = 0.5, slope_deg = 10.0 }\n").stations[0]
    assert st.chute_width(0.15) == pytest.approx(0.195)


def test_the_chute_parts_slope_down_the_heading_with_walls_each_side(tmp_path):
    st = parse(tmp_path, HEAD + FEEDER.replace("heading_deg = 0.0", "heading_deg = 90.0")
               + "chute = { length = 0.5, slope_deg = 20.0 }\n").stations[0]
    parts = chute_parts(st, 0.15)
    assert set(parts) == {"floor", "wall_left", "wall_right"}
    floor_c, floor_r, floor_h = parts["floor"]
    assert floor_c[1] > 4.0 and floor_c[0] == pytest.approx(1.0)       # runs toward +y
    # the floor's top is tangent to a ball resting at the release point
    normal = floor_r[:, 2]
    assert (np.array(st.release) - (floor_c + normal * floor_h[2])) @ normal == pytest.approx(0.075)
    left, right = parts["wall_left"][0], parts["wall_right"][0]
    assert left[0] < 1.0 < right[0]                                       # left of +y is -x
    assert abs(left[0] - right[0]) == pytest.approx(0.195 + 0.25 * 0.0254)


@pytest.mark.parametrize("extra, key", [
    ("interval = 0", "interval"), ("stock = 0", "stock"), ("spread_speed = -1", "spread_speed"),
    ("pitch_deg = 100", "pitch_deg"), ("rate = 2", "rate"), ("spread_position = [0, -1, 0]", "spread_position"),
    ("release_in = [1, 2, 3]", "release"),
])
def test_bad_station_values(tmp_path, extra, key):
    with pytest.raises(RangeError, match=key):
        parse(tmp_path, HEAD + FEEDER + extra + "\n")


def test_two_stations_named_alike_are_refused(tmp_path):
    with pytest.raises(RangeError, match="shares its name"):
        parse(tmp_path, HEAD + FEEDER + FEEDER)


def test_a_station_named_like_a_goal_is_refused(tmp_path):
    goal = '[[goal]]\nname = "feeder"\npos = [4.0, 4.0]\nrim_height = 2.0\nopening = { sides = 6, across_flats = 1.0 }\n'
    with pytest.raises(RangeError, match="shares its name"):
        parse(tmp_path, HEAD + goal + FEEDER)


def test_a_release_off_the_field_or_on_the_carpet_is_refused(tmp_path):
    with pytest.raises(RangeError, match="off the field"):
        parse(tmp_path, HEAD + FEEDER.replace("[1.0, 4.0, 0.5]", "[-1.0, 4.0, 0.5]"))
    with pytest.raises(RangeError, match="above the carpet"):
        parse(tmp_path, HEAD + FEEDER.replace("0.5]", "0.05]"))


def test_a_station_in_a_disc_range_is_refused(tmp_path):
    text = HEAD.replace('ball = "2026-fuel"', 'disc = "2013-frisbee"') + FEEDER
    with pytest.raises(RangeError, match="'feeder': stations feed balls; this range's piece is a disc"):
        parse(tmp_path, text)


def test_ranges_without_stations_are_unchanged():
    from pathlib import Path
    for name in BUILTIN_RANGES:
        assert load_range(name, Path(".")).stations == ()
