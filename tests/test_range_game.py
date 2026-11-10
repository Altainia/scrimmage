"""The game a range declares (`[game]`): its phases, points, LEAVE and rules, and the points a log scores."""

import pytest

from scrimmage.testrange import check
from scrimmage.testrange.spec import RangeError, load_range

HEAD = '''
[range]
name = "t"
ball = "2026-fuel"
[[goal]]
name = "speaker_blue"
pos = [1.0, 5.0]
rim_height = 2.0
opening = { diameter = 1.0 }
[[goal]]
name = "amp_blue"
pos = [2.0, 7.0]
rim_height = 1.0
opening = { diameter = 0.5 }
[[zone]]
name = "blue_start"
corners = [[0.0, 0.0], [1.93, 0.0], [1.93, 8.07], [0.0, 8.07]]
[start.home]
pose = [1.0, 4.0, 0.0]
'''
GAME = '''
[game]
name = "2024 CRESCENDO"
auto_seconds = 15
pause_seconds = 3
teleop_seconds = 135
points = { speaker_blue = { auto = 5, teleop = 2 }, amp_blue = 1 }
leave = { zone = "blue_start", points = 2 }
[game.rules]
possession = 1
extension_in = 12
height_in = 48
'''
CLIMB = '''
[[element]]
name = "chain"
kind = "bar"
center = [5.0, 4.0]
yaw_deg = 0.0
length = 1.5
diameter_in = 0.5
height = 0.7
posts = false
level = "ONSTAGE"
[climb]
rule = "lowest-rung"
levels = { ONSTAGE = 3 }
'''


def load(tmp_path, game=GAME, head=HEAD):
    (tmp_path / "r.toml").write_text(head + game)
    return load_range("r.toml", tmp_path)


def test_the_2024_example_parses_as_given(tmp_path):
    g = load(tmp_path).game
    assert (g.name, g.auto_s, g.pause_s, g.teleop_s, g.match_s) == ("2024 CRESCENDO", 15, 3, 135, 153)
    assert g.points["speaker_blue"].auto == 5 and g.points["speaker_blue"].teleop == 2
    assert g.points["amp_blue"].auto == 1 and g.points["amp_blue"].teleop == 1      # one number: both phases
    assert (g.leave.zone, g.leave.points) == ("blue_start", 2)
    assert g.rules.possession == 1
    assert g.rules.extension == pytest.approx(0.3048) and g.rules.height == pytest.approx(1.2192)
    assert g.climb_points == {}


def test_climb_points_copy_the_climb_levels(tmp_path):
    assert load(tmp_path, GAME + CLIMB).game.climb_points == {"ONSTAGE": 3.0}


def test_every_key_but_the_phase_lengths_is_optional(tmp_path):
    g = load(tmp_path, "[game]\nauto_seconds = 15\npause_seconds = 0\nteleop_seconds = 135\n").game
    assert (g.name, g.points, g.leave, g.rules, g.pause_s) == ("", {}, None, None, 0)


def _game(body):
    return "[game]\nauto_seconds = 15\npause_seconds = 3\nteleop_seconds = 135\n" + body


@pytest.mark.parametrize("game, match", [
    (_game('points = { nowhere = 1 }'), r"r\.toml: \[game\] points 'nowhere' is not a goal of the range; goals: \w"),
    (_game('leave = { zone = "wing", points = 2 }'), r"\[game\] leave zone 'wing' is not a zone"),
    ("[game]\npause_seconds = 3\nteleop_seconds = 135\n", r"\[game\] is missing required key 'auto_seconds'"),
    ("[game]\nauto_seconds = 15\nteleop_seconds = 135\n", r"\[game\] is missing required key 'pause_seconds'"),
    ("[game]\nauto_seconds = 15\npause_seconds = 3\n", r"\[game\] is missing required key 'teleop_seconds'"),
    ("[game]\nauto_seconds = 0\npause_seconds = 3\nteleop_seconds = 135\n", r"auto_seconds must be positive"),
    ("[game]\nauto_seconds = 15\npause_seconds = -1\nteleop_seconds = 135\n", r"pause_seconds can't be negative"),
    ("[game]\nauto_seconds = 15\npause_seconds = 3\nteleop_seconds = -5\n", r"teleop_seconds must be positive"),
    (_game('points = { speaker_blue = -1 }'), r"\[game\] points speaker_blue can't be negative"),
    (_game('points = { speaker_blue = { auto = 1, teleop = -2 } }'),
     r"\[game\] points speaker_blue teleop can't be negative"),
    (_game('leave = { zone = "blue_start", points = -2 }'), r"\[game\] leave points can't be negative"),
    (_game('[game.rules]\npossession = 0'), r"\[game\.rules\] possession must be 1 or more"),
    (_game('[game.rules]\npossession = 1.5'), r"\[game\.rules\] possession must be a whole number"),
    (_game('bonus = 3'), r"unknown key 'bonus' in \[game\]"),
    (_game('[game.rules]\nspeed = 3'), r"unknown key 'speed' in \[game\.rules\]"),
    ("[game]\nauto_seconds = inf\npause_seconds = 3\nteleop_seconds = 135\n", r"auto_seconds must be a finite"),
    (_game('points = { speaker_blue = inf }'), r"\[game\] points speaker_blue must be a finite number"),
    (_game('points = { speaker_blue = { auto = 5 } }'), r"speaker_blue"),
])
def test_bad_games_are_refused_naming_file_table_and_key(tmp_path, game, match):
    with pytest.raises(RangeError, match=match):
        load(tmp_path, game)


def test_a_range_with_no_game_has_none_and_check_prints_no_game_line(tmp_path):
    rng = load(tmp_path, "")
    assert rng.game is None
    assert "game" not in "\n".join(check.summary("r.toml", rng))


def test_check_summary_names_the_game(tmp_path):
    line = check.summary("r.toml", load(tmp_path))[0]
    assert "game '2024 CRESCENDO' (15 s auto, 3 s pause, 135 s teleop, 2 goals score points)" in line


# ---------------------------------------------------------------------------------------------- the points

from scrimmage.testrange.game import Game, GoalPoints, Leave, Phases, score  # noqa: E402

GAME24 = Game("2024", 15.0, 3.0, 135.0,
              points={"speaker_blue": GoalPoints(5, 2), "amp_blue": GoalPoints(2, 1)},
              leave=Leave("blue_start", 2), climb_points={"ONSTAGE": 3.0}, goals=("speaker_blue", "amp_blue"))
PH = GAME24.phases(10.0)        # auto [10, 25), pause [25, 28), teleop [28, 163]


def rows(*pairs):
    return [{"goal": g, "scored_t": t} for g, t in pairs]


def test_phases():
    assert PH == Phases(auto=(10.0, 25.0), teleop=(28.0, 163.0))


def test_the_2024_numbers():
    log = rows(("speaker_blue", 12.0), ("speaker_blue", 20.0), ("speaker_blue", 40.0), ("amp_blue", 50.0))
    p = score(GAME24, log, PH, True, None)
    assert (p.auto, p.teleop, p.endgame, p.total) == (12, 3, 0, 15)
    assert [(i.source, i.phase, i.count, i.points) for i in p.items] == [
        ("speaker_blue", "auto", 2, 10), ("speaker_blue", "teleop", 1, 2), ("amp_blue", "teleop", 1, 1),
        ("leave", "auto", 1, 2)]
    q = score(GAME24, log, PH, True, "ONSTAGE")
    assert (q.endgame, q.total) == (3, 18) and q.items[-1].source == "climb" and q.items[-1].phase == "endgame"
    assert q.to_dict()["total"] == 18 and q.to_dict()["items"][0]["source"] == "speaker_blue"


def test_undecided_leave_and_no_level_score_nothing():
    p = score(GAME24, rows(("amp_blue", 12.0)), PH, None, None)
    assert (p.auto, p.total) == (2, 2) and all(i.source != "leave" for i in p.items)


def test_phase_edges():
    log = rows(("speaker_blue", 25.0), ("speaker_blue", 163.0), ("speaker_blue", 163.5), ("speaker_blue", 9.9),
               ("speaker_blue", 28.0))
    p = score(GAME24, log, PH, False, None)
    assert [(i.phase, i.count, i.points) for i in p.items] == [("pause", 1, 0), ("teleop", 2, 4)]
    assert p.total == 4


def test_unlisted_goal_counts_for_zero_and_a_miss_has_no_item():
    log = rows(("hoard", 12.0), ("hoard", 30.0), (None, 12.0)) + [{"goal": "speaker_blue", "scored_t": None}]
    p = score(GAME24, log, PH, False, None)
    assert [(i.source, i.phase, i.count, i.points) for i in p.items] == [
        ("hoard", "auto", 1, 0), ("hoard", "teleop", 1, 0)]
    assert p.total == 0


def test_points_so_far_with_at():
    log = rows(("speaker_blue", 12.0), ("speaker_blue", 20.0), ("speaker_blue", 40.0))
    mid = score(GAME24, log, PH, True, "ONSTAGE", at=15.0)
    assert (mid.auto, mid.teleop, mid.endgame, mid.total) == (5, 0, 0, 5)       # no leave yet, no endgame
    assert score(GAME24, log, PH, True, None, at=20.0).total == 10               # scored_t <= at counts
    assert score(GAME24, log, PH, True, None, at=26.0).total == 12               # auto over: LEAVE counts
    end = score(GAME24, log, PH, True, "ONSTAGE", at=PH.teleop[1])
    assert end == score(GAME24, log, PH, True, "ONSTAGE")                     # at the match's end: the endgame too


def test_a_game_with_no_pause(tmp_path):
    rng = load(tmp_path, "[game]\nauto_seconds = 15\npause_seconds = 0\nteleop_seconds = 135\n")
    assert rng.game.pause_s == 0 and rng.game.phases(0.0) == Phases((0.0, 15.0), (15.0, 150.0))
