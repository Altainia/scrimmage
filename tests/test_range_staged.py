"""Pieces staged on top of a field element (2024's HIGH NOTEs on the AMP): `[[floor_balls]] height`, kickoff dry run
gap 18."""

from pathlib import Path

import mujoco
import numpy as np
import pytest

from range_fixtures import range_cfg
from scrimmage.testrange.spec import RangeError, load_range

HEAD = '''
[range]
name = "t"
ball = "2026-fuel"
[[element]]
name = "amp"
kind = "box"
center = [2.0, 2.0]
size = [0.6, 0.6, 1.2]
'''


def load(tmp_path, text):
    (tmp_path / "r.toml").write_text(HEAD + text)
    return load_range("r.toml", tmp_path)


def test_a_piece_staged_on_top_of_an_element(tmp_path):
    rng = load(tmp_path, '[[floor_balls]]\nat = [[2.0, 2.0], [4.0, 4.0]]\nheight = 1.2\n'
                         '[[floor_balls]]\nat = [[5.0, 5.0]]\n')
    assert rng.floor_heights.tolist() == [1.2, 1.2, 0.0]
    assert len(rng.warnings) == 1          # the one at (4, 4) has nothing under it
    w = rng.warnings[0]
    assert "ball 1" in w and "1.2 m" in w and "nothing under it" in w


def test_on_the_carpet_a_piece_on_an_element_is_still_refused(tmp_path):
    with pytest.raises(RangeError, match="lies on element 'amp'"):
        load(tmp_path, '[[floor_balls]]\nat = [[2.0, 2.0]]\n')


@pytest.mark.parametrize("height, match", [(-0.1, "height must be from 0 to 10"), (11.0, "height must be from 0 to 10"),
                                           ('"high"', "height must be a number")])
def test_a_bad_height_is_refused(tmp_path, height, match):
    with pytest.raises(RangeError, match=match):
        load(tmp_path, f'[[floor_balls]]\nat = [[2.0, 2.0]]\nheight = {height}\n')


def test_pieces_at_different_heights_may_share_x_and_y(tmp_path):
    (tmp_path / "r.toml").write_text(HEAD.replace('center = [2.0, 2.0]', 'center = [2.0, 2.0]\nbase_height = 0.5')
                                     + '[[floor_balls]]\nat = [[2.0, 2.0]]\nheight = 1.7\n'
                                       '[[floor_balls]]\nat = [[3.0, 3.0]]\n')
    rng = load_range("r.toml", tmp_path)
    assert rng.floor_heights.tolist() == [1.7, 0.0] and rng.warnings == ()


def test_a_staged_piece_rests_up_there_in_the_world(tmp_path):
    from scrimmage.world import World
    (tmp_path / "r.toml").write_text(HEAD + '[[floor_balls]]\nat = [[2.0, 2.0]]\nheight = 1.2\n')
    w = World(range_cfg(range={"file": "r.toml", "floor_balls": True}), tmp_path)
    w.set_robot_pose(6.0, 6.0, 0.0)
    m, d = w.model, w.data
    ball = m.body(w.parts.layout.ball_names[0]).id
    assert d.xpos[ball][2] == pytest.approx(1.2 + 0.0751, abs=0.002)
    from scrimmage.protocol import HalState
    hal = HalState()
    for _ in range(500):
        w.step(hal)
    assert d.xpos[ball][2] == pytest.approx(1.2 + 0.0751, abs=0.005)       # still on top after 0.5 s
    w.test_range.reset()
    mujoco.mj_forward(m, d)
    assert d.xpos[ball][2] == pytest.approx(1.2 + 0.0751, abs=0.002)       # and put back up there
    assert np.allclose(d.xpos[ball][:2], [2.0, 2.0])
    assert Path  # noqa: B018


def test_setups_floor_spread_never_picks_a_staged_piece(tmp_path):
    # review M5: the floor spread sets its piece down on the carpet; a staged piece (on the AMP) stays where it is
    from scrimmage.blocks.spread import spread_ball
    from scrimmage.world import World
    (tmp_path / "r.toml").write_text(HEAD + '[[floor_balls]]\nat = [[2.0, 2.0]]\nheight = 1.2\n'
                                            '[[floor_balls]]\nat = [[2.0, 3.2]]\n'
                                            '[start.s]\npose = [2.0, 0.9, 90.0]\n')
    w = World(range_cfg(range={"file": "r.toml", "floor_balls": True, "start": "s"}), tmp_path)
    i, where = spread_ball(w)
    assert i == 1 and where["distance_in"] == pytest.approx(2.3 / 0.0254)


GOAL_BODY = '''
[[goal]]
name = "amp_goal"
pos = [6.0, 6.0]
rim_height = 1.4
opening = { sides = 4, across_flats = 0.4 }
body = { size = [0.6, 0.6], height = 1.2 }
'''


def test_a_piece_staged_on_a_goals_body_wall_is_supported(tmp_path):
    # review M6: a goal's body (its four walls from the carpet to `height`) holds up a piece staged on a wall's top
    rng = load(tmp_path, GOAL_BODY + '[[floor_balls]]\nat = [[6.29, 6.0]]\nheight = 1.2\n')
    assert rng.warnings == ()
    # over the middle of the body there's no top: it falls in, and is still warned about
    inside = load(tmp_path, GOAL_BODY + '[[floor_balls]]\nat = [[6.0, 6.0]]\nheight = 1.2\n')
    assert len(inside.warnings) == 1 and "nothing under it" in inside.warnings[0]
