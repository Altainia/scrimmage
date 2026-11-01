from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from scrimmage.testrange.elements import Bump, Platform, Ramp, footprint
from scrimmage.testrange.spec import RangeError, load_range

HEAD = '''
[range]
name = "test"
ball = "2026-fuel"
'''

BUMP = '''
[[element]]
name = "b1"
kind = "bump"
center = [4.0, 2.0]
depth_in = 44.40
width_in = 73.0
height_in = 6.51
ramp_deg = 15.0
'''


def load_text(tmp_path, text):
    (tmp_path / "r.toml").write_text(HEAD + text)
    return load_range("r.toml", tmp_path)


def test_bump_in_inches_parses_to_meters(tmp_path):
    e = load_text(tmp_path, BUMP).elements[0]
    assert isinstance(e, Bump)
    assert e.depth == pytest.approx(1.12776)
    assert e.width == pytest.approx(1.8542)
    assert e.height == pytest.approx(0.165354)
    assert (e.crest, e.lip, e.friction, e.yaw_deg) == (0.008, 0.0155, 0.6, 0.0)


def test_ramp_and_platform_parse(tmp_path):
    rng = load_text(tmp_path, '''
[[element]]
name = "r1"
kind = "ramp"
center = [1.0, 1.0]
width = 1.0
height = 0.2
ramp_deg = 10.0
deck = 0.5
[[element]]
name = "p1"
kind = "platform"
center = [8.0, 4.0]
yaw_deg = 90.0
length_in = 96
width_in = 48
height_in = 9.125
max_tilt_deg = 15
hinge_height_in = 6.57
hinge_spacing_in = 5.30
ramp_length_in = 15.125
mass = 94.8
inertia = 24.6
com_height_in = 7.27
damping = 50.0
''')
    r, p = rng.elements
    assert isinstance(r, Ramp) and r.two_sided is True
    assert isinstance(p, Platform)
    assert p.length == pytest.approx(2.4384) and p.height == pytest.approx(0.231775)
    assert p.yaw_deg == 90.0 and p.friction == 0.6


def test_footprint_corners_follow_yaw():
    b = Bump("b", (1.0, 2.0), 90.0, 1.0, 3.0, 0.1, 15.0, 0.008, 0.0155, 0.6)
    pts = footprint(b)
    assert pts.shape == (4, 2)
    assert np.allclose(sorted(map(tuple, np.round(pts, 6))),
                       sorted([(2.5, 1.5), (2.5, 2.5), (-0.5, 1.5), (-0.5, 2.5)]))


def test_unknown_kind_and_missing_keys_name_the_element(tmp_path):
    with pytest.raises(RangeError, match="element 'b1'.*kind"):
        load_text(tmp_path, BUMP.replace('"bump"', '"wedge"'))
    with pytest.raises(RangeError, match="element 'b1'.*missing required key 'ramp_deg'"):
        load_text(tmp_path, BUMP.replace("ramp_deg = 15.0\n", ""))
    with pytest.raises(RangeError, match="element 'b1'.*both.*depth"):
        load_text(tmp_path, BUMP + "depth = 1.0\n")
    with pytest.raises(RangeError, match="unknown key 'deck' in element 'b1'"):
        load_text(tmp_path, BUMP + "deck = 1.0\n")


def test_duplicate_element_names_rejected(tmp_path):
    with pytest.raises(RangeError, match="two elements named 'b1'"):
        load_text(tmp_path, BUMP + BUMP.replace("[4.0, 2.0]", "[8.0, 6.0]"))


def test_start_overlapping_an_element_rejected(tmp_path):
    with pytest.raises(RangeError, match="start 'near'.*element 'b1'"):
        load_text(tmp_path, BUMP + '[start.near]\npose = [3.0, 2.0, 0.0]\n')
    load_text(tmp_path, BUMP + '[start.far]\npose = [1.0, 2.0, 0.0]\n')


def test_floor_ball_inside_an_element_rejected(tmp_path):
    with pytest.raises(RangeError, match="floor_balls.*ball 0.*element 'b1'"):
        load_text(tmp_path, BUMP + '[[floor_balls]]\nat = [[4.0, 2.0]]\n')


def test_range_without_elements_unchanged(tmp_path):
    assert load_text(tmp_path, "").elements == ()


def test_2026_range_has_its_four_bumps():
    bumps = {e.name: e for e in load_range("2026", Path(".")).elements}
    assert {n: e.center for n, e in bumps.items()} == {
        "bump_blue_left": (4.625, 5.557), "bump_blue_right": (4.625, 2.513),
        "bump_red_left": (11.916, 2.513), "bump_red_right": (11.916, 5.557)}
    assert all(isinstance(e, Bump) and e.depth == pytest.approx(1.12776) for e in bumps.values())
    # The same bump as 2026-bump.toml's, which is bump_blue_right alone
    assert load_range("2026-bump", Path(".")).elements[0] == replace(bumps["bump_blue_right"], name="bump")


def test_bump_whose_depth_misses_its_profile_rejected(tmp_path):
    with pytest.raises(RangeError, match="element 'b1'.*depth 1.3 m doesn't fit the profile.*1.127 m"):
        load_text(tmp_path, BUMP.replace("depth_in = 44.40", "depth = 1.3"))
    with pytest.raises(RangeError, match="element 'b1'.*lip .* must be lower than the height"):
        load_text(tmp_path, BUMP + "lip = 0.2\n")
