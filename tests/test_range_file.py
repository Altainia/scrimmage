import math
import re
from pathlib import Path

import numpy as np
import pytest

from scrimmage.launcher.inputs import PRESETS
from scrimmage.testrange.spec import (
    BUILTIN_RANGES, Funnel, Goal, Opening, RangeError, load_range,
)

# Manual section 6.3.4 (https://firstfrc.blob.core.windows.net/frc2026/Manual/HTML/2026GameManual.htm): 504 FUEL,
# less 24 in each DEPOT and each OUTPOST CHUTE and up to 8 preloaded in each ROBOT, leaves "360 to 408" in the
# NEUTRAL ZONE. The field CAD stages 360 (every ROBOT preloaded), so the range does too.
NEUTRAL_ZONE_FUEL = 360

MINIMAL = '''
[range]
name = "test"
ball = "2026-fuel"
[[goal]]
name = "g"
pos = [4.0, 4.0]
rim_height = 2.0
opening = { sides = 6, across_flats = 1.0 }
'''


def write(tmp_path, text, name="r.toml"):
    p = tmp_path / name
    p.write_text(text)
    return p


def load_text(tmp_path, text):
    write(tmp_path, text)
    return load_range("r.toml", tmp_path)


def goal(**kw):
    base = dict(name="g", pos=(0.0, 0.0), heading_deg=0.0, rim_height=2.0, tilt_deg=0.0,
                opening=Opening(sides=6, across_flats=1.0, rotation_deg=0.0), funnel=None, body=None, plates=())
    base.update(kw)
    return Goal(**base)


def test_2026_hub_matches_spec():
    rng = load_range("2026", Path("."))
    hubs = {g.name: g for g in rng.goals}
    blue, red = hubs["hub_blue"], hubs["hub_red"]
    for g in (blue, red):
        assert g.opening.sides == 6 and g.opening.across_flats == pytest.approx(1.060)
        assert g.rim_height == pytest.approx(1.829)
        assert g.funnel.depth == pytest.approx(0.395) and g.funnel.slope_deg == pytest.approx(60.0)
        assert g.throat_across_flats() == pytest.approx(0.593, abs=0.015)
        assert g.scoring_surface().center[2] == pytest.approx(1.434, abs=0.002)
    assert blue.pos == pytest.approx((4.625, 4.035))
    assert red.pos == pytest.approx((16.541 - 4.625, 4.035))
    assert rng.ball_preset == "2026-fuel" and rng.ball.diameter == pytest.approx(0.1501)


@pytest.mark.parametrize("name", BUILTIN_RANGES)
def test_builtin_every_number_cited(name):
    text = BUILTIN_RANGES[name].read_text()
    bad = []
    for n, line in enumerate(text.splitlines(), 1):
        code, _, comment = line.partition("#")
        if re.search(r"\d", code) and not re.search(r"\b(source|choice|estimate):", comment):
            bad.append(f"{n}: {line}")
    assert not bad, "uncited numbers:\n" + "\n".join(bad)


def test_2026_floor_balls_load():
    rng = load_range("2026", Path("."))
    fb = rng.floor_balls
    assert fb.shape == (NEUTRAL_ZONE_FUEL, 2)
    d = np.linalg.norm(fb[:, None, :] - fb[None, :, :], axis=-1)
    d[np.arange(len(fb)), np.arange(len(fb))] = np.inf
    assert d.min() >= rng.ball.diameter
    assert {"blue_left", "blue_center", "blue_right"} <= set(rng.starts)


def test_hexagon_corners_on_x():
    g = goal(pos=(3.0, 4.0), rim_height=1.5)
    v = g.rim_vertices() - np.array([3.0, 4.0, 1.5])
    r = 1.0 / (2 * math.cos(math.pi / 6))
    assert v.shape == (6, 3) and np.allclose(v[:, 2], 0.0)
    assert np.allclose(v[0], [r, 0, 0]) and np.allclose(v[3], [-r, 0, 0])
    assert np.isclose(v[:, 1].max(), 0.5) and np.isclose(v[:, 1].min(), -0.5)
    # the flats facing +-y: two corners on each at y = +-across_flats/2
    assert np.sum(np.isclose(v[:, 1], 0.5)) == 2 and np.sum(np.isclose(v[:, 1], -0.5)) == 2


def test_crossing_downward_inside_scores():
    s = goal(pos=(3.0, 4.0)).scoring_surface()
    prev = np.array([[3.1, 4.0, 2.05], [2.9, 4.2, 2.01]])
    now = np.array([[3.1, 4.0, 1.95], [2.9, 4.2, 2.0]])  # the second lands exactly on the plane: counts
    assert s.crossed(prev, now).tolist() == [True, True]


def test_crossing_upward_does_not():
    s = goal(pos=(3.0, 4.0)).scoring_surface()
    assert s.crossed(np.array([[3.0, 4.0, 1.95]]), np.array([[3.0, 4.0, 2.05]])).tolist() == [False]


def test_crossing_outside_outline_does_not():
    s = goal(pos=(3.0, 4.0)).scoring_surface()
    # beyond a flat (y = 0.5) and beyond a corner (x = 0.577), both just outside
    prev = np.array([[3.0, 4.52, 2.05], [3.59, 4.0, 2.05], [3.0, 4.5, 2.05]])
    now = np.array([[3.0, 4.52, 1.95], [3.59, 4.0, 1.95], [3.0, 4.5, 1.95]])  # the last on the flat: inside
    assert s.crossed(prev, now).tolist() == [False, False, True]


def test_vertical_goal_crossing_direction():
    s = goal(pos=(3.0, 4.0), tilt_deg=90.0).scoring_surface()
    assert np.allclose(s.normal, [-1, 0, 0])
    through = s.crossed(np.array([[2.9, 4.0, 2.1]]), np.array([[3.1, 4.0, 2.1]]))
    back = s.crossed(np.array([[3.1, 4.0, 2.1]]), np.array([[2.9, 4.0, 2.1]]))
    assert through.tolist() == [True] and back.tolist() == [False]


def test_upward_goal_scores_rising_pieces_only():
    # the 2024 SPEAKER opening: entered going up and into the wall. Tilt past 90 turns the entry normal on from -x
    # toward -z, so at 180 a piece scores going up
    up = goal(pos=(3.0, 4.0), tilt_deg=180.0).scoring_surface()
    assert np.allclose(up.normal, [0, 0, -1])
    assert up.crossed(np.array([[3.0, 4.0, 1.95]]), np.array([[3.0, 4.0, 2.05]])).tolist() == [True]
    assert up.crossed(np.array([[3.0, 4.0, 2.05]]), np.array([[3.0, 4.0, 1.95]])).tolist() == [False]
    # 166 deg: the opening faces down and out of the wall (+x is into the wall): a piece rising toward the wall
    # scores, one falling back out doesn't
    s = goal(pos=(3.0, 4.0), tilt_deg=166.0).scoring_surface()
    assert s.normal[2] < -0.9 and s.normal[0] < 0
    rising = s.crossed(np.array([[2.95, 4.0, 1.9]]), np.array([[3.0, 4.0, 2.1]]))
    falling = s.crossed(np.array([[3.0, 4.0, 2.1]]), np.array([[2.95, 4.0, 1.9]]))
    assert rising.tolist() == [True] and falling.tolist() == [False]


def test_the_2024_speaker_scores_the_dry_runs_rising_shot():
    # the kickoff dry run's SPEAKER: the opening's low edge at the wall, its front lip 14.8 deg higher, entered going
    # up. Heading into the wall (180 at the blue wall), tilt 180 - 14.8. The traced ball went (0.259, 2.039) ->
    # (0.061, 2.107) and must score; the same path back out must not
    from scrimmage.testrange.spec import RectOpening
    g = goal(pos=(0.2293, 5.5446), heading_deg=180.0, rim_height=2.05, tilt_deg=165.2,
             opening=RectOpening(width=1.0427, height=0.4742))
    s = g.scoring_surface()
    a, b = np.array([[0.259, 5.5446, 2.039]]), np.array([[0.061, 5.5446, 2.107]])
    assert s.crossed(a, b).tolist() == [True] and s.crossed(b, a).tolist() == [False]


@pytest.mark.parametrize("tilt", ["194.8", "-165.2", "nan", "inf"])
def test_tilt_outside_0_to_180_refused_in_words(tmp_path, tilt):
    with pytest.raises(RangeError, match=r"tilt_deg must be from 0 to 180.*going up.*heading_deg"):
        load_text(tmp_path, MINIMAL + f"tilt_deg = {tilt}\n")


def test_tilt_180_loads(tmp_path):
    assert load_text(tmp_path, MINIMAL + "tilt_deg = 165.2\n").goals[0].tilt_deg == pytest.approx(165.2)


def test_funnelled_surface_is_the_throat():
    g = goal(funnel=Funnel(depth=0.3, slope_deg=45.0))
    assert g.throat_across_flats() == pytest.approx(1.0 - 2 * 0.3)
    s = g.scoring_surface()
    assert s.center[2] == pytest.approx(1.7)
    assert np.abs(s.polygon[:, 1]).max() == pytest.approx(0.2)


def test_throat_narrower_than_ball_rejected(tmp_path):
    text = MINIMAL + "funnel = { depth = 0.45, slope_deg = 60.0 }\n"  # 1.0 - 2 * 0.45 / tan 60 = 0.48: fits
    assert load_text(tmp_path, text).goals[0].throat_across_flats() == pytest.approx(0.4804, abs=1e-3)
    text = MINIMAL + "funnel = { depth = 0.8, slope_deg = 60.0 }\n"  # 1.0 - 0.924 = 0.076 < 0.1501
    with pytest.raises(RangeError, match=r"r\.toml.*funnel.*throat.*0\.1501"):
        load_text(tmp_path, text)


def test_unknown_key_lists_valid_keys(tmp_path):
    with pytest.raises(RangeError, match=r"r\.toml.*'hieght'.*\[\[goal\]\].*valid keys:.*rim_height"):
        load_text(tmp_path, MINIMAL + "hieght = 2.0\n")
    with pytest.raises(RangeError, match=r"r\.toml.*'size'.*opening.*valid keys:.*across_flats.*diameter"):
        load_text(tmp_path, MINIMAL.replace("across_flats = 1.0", "across_flats = 1.0, size = 2"))


def test_unknown_ball_preset_lists_presets(tmp_path):
    with pytest.raises(RangeError) as e:
        load_text(tmp_path, MINIMAL.replace("2026-fuel", "2027-rock"))
    assert "r.toml" in str(e.value) and "'2027-rock'" in str(e.value)
    for name in PRESETS["ball"]:
        assert name in str(e.value)


def test_relative_path_and_builtin(tmp_path):
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub", MINIMAL)
    rng = load_range("sub/r.toml", tmp_path)
    assert rng.path == tmp_path / "sub" / "r.toml" and rng.name == "test"
    assert load_range("2026", tmp_path).path == BUILTIN_RANGES["2026"]
    with pytest.raises(RangeError, match="nope.toml"):
        load_range("nope.toml", tmp_path)


def test_circle_opening(tmp_path):
    rng = load_text(tmp_path, MINIMAL.replace("sides = 6, across_flats = 1.0", "diameter = 0.9"))
    o = rng.goals[0].opening
    assert o.sides == 24 and o.across_flats == pytest.approx(0.9 * math.cos(math.pi / 24))
    v = rng.goals[0].rim_vertices() - np.array([4.0, 4.0, 2.0])
    assert np.allclose(np.linalg.norm(v, axis=1), 0.45)


@pytest.mark.parametrize("opening, match", [
    ("{ rotation_deg = 0.0 }", "neither"),
    ("{ sides = 6, across_flats = 1.0, diameter = 1.0 }", "both"),
])
def test_opening_needs_sides_or_diameter(tmp_path, opening, match):
    with pytest.raises(RangeError, match=rf"r\.toml.*opening.*{match}"):
        load_text(tmp_path, MINIMAL.replace("{ sides = 6, across_flats = 1.0 }", opening))


def test_duplicate_goal_rejected(tmp_path):
    with pytest.raises(RangeError, match=r"r\.toml.*two goals named 'g'"):
        load_text(tmp_path, MINIMAL + '[[goal]]\nname = "g"\npos = [9.0, 4.0]\nrim_height = 2.0\n'
                  'opening = { diameter = 1.0 }\n')


def test_duplicate_start_rejected(tmp_path):
    with pytest.raises(RangeError, match=r"r\.toml"):
        load_text(tmp_path, MINIMAL + '[start.a]\npose = [1.0, 1.0, 0.0]\n[start.a]\npose = [2.0, 1.0, 0.0]\n')


@pytest.mark.parametrize("fb, match", [
    ("at = [[2.0, 2.0], [2.1, 2.0]]", "overlap"),
    ("grid = { corner = [2.0, 2.0], step = [0.1, 0.2], count = [2, 2] }", "overlap"),
    ("at = [[0.05, 2.0]]", "off the field"),
    ("grid = { corner = [16.0, 7.5], step = [0.2, 0.2], count = [2, 4] }", "off the field"),
])
def test_bad_floor_balls_rejected(tmp_path, fb, match):
    with pytest.raises(RangeError, match=rf"r\.toml.*floor_balls.*{match}"):
        load_text(tmp_path, MINIMAL + f"[[floor_balls]]\n{fb}\n")


def test_floor_balls_across_entries_checked(tmp_path):
    text = MINIMAL + "[[floor_balls]]\nat = [[2.0, 2.0]]\n[[floor_balls]]\nat = [[2.0, 2.1]]\n"
    with pytest.raises(RangeError, match="overlap"):
        load_text(tmp_path, text)


def test_grid_and_starts(tmp_path):
    text = MINIMAL + ("[[floor_balls]]\ngrid = { corner = [7.0, 2.0], step = [0.16, 0.2], count = [2, 3] }\n"
                      "[start.a]\npose = [3.5, 6.0, 180.0]\n")
    rng = load_text(tmp_path, text)
    assert rng.floor_balls.shape == (6, 2)
    assert rng.floor_balls[0].tolist() == [7.0, 2.0] and rng.floor_balls[-1].tolist() == pytest.approx([7.16, 2.4])
    assert rng.starts == {"a": (3.5, 6.0, 180.0)}


def test_plate_parsed(tmp_path):
    text = MINIMAL + '[[goal.plate]]\nname = "net"\npos = [0.8, 0.0, 0.5]\nsize = [1.4, 1.3]\n'
    p = load_text(tmp_path, text).goals[0].plates[0]
    assert p.name == "net" and p.pos == (0.8, 0.0, 0.5) and p.size == (1.4, 1.3) and p.rpy_deg == (0.0, 0.0, 0.0)


def test_funnel_throat_override(tmp_path):
    # P13: a measured throat wins over the one the walls' depth and slope give; it must fit inside the opening
    text = MINIMAL + "funnel = { depth = 0.3, slope_deg = 45.0, throat = 0.35 }\n"
    g = load_text(tmp_path, text).goals[0]
    assert g.throat_across_flats() == pytest.approx(0.35)
    assert np.abs(g.scoring_surface().polygon[:, 1]).max() == pytest.approx(0.175)
    with pytest.raises(RangeError, match=r"r\.toml.*funnel.*throat"):
        load_text(tmp_path, MINIMAL + "funnel = { depth = 0.3, slope_deg = 45.0, throat = 1.2 }\n")


def test_carpet_rolling_resistance(tmp_path):
    from scrimmage.testrange.spec import DEFAULT_ROLLING_RESISTANCE
    assert load_text(tmp_path, MINIMAL).rolling_resistance == DEFAULT_ROLLING_RESISTANCE
    assert load_range("2026", Path(".")).rolling_resistance == DEFAULT_ROLLING_RESISTANCE
    assert load_text(tmp_path, MINIMAL + "[carpet]\nrolling_resistance = 0.02\n").rolling_resistance == 0.02
    assert load_text(tmp_path, MINIMAL + "[carpet]\nrolling_resistance = 0\n").rolling_resistance == 0.0
    with pytest.raises(RangeError, match=r"\[carpet\] rolling_resistance must be 0 or more"):
        load_text(tmp_path, MINIMAL + "[carpet]\nrolling_resistance = -0.1\n")
    with pytest.raises(RangeError, match="unknown key 'friction' in \\[carpet\\]"):
        load_text(tmp_path, MINIMAL + "[carpet]\nfriction = 1.0\n")
