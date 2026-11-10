"""A scenario's points (spec D6): the range's `[game]` scored over the scenario's own steps, read as the signals
`points`, `points.auto`, `points.teleop` and `points.endgame`, shown in the report's Score section.

The world tests use the shooter robot on a copy of the 2026 range with a start zone and a `[game]` added
(hub_blue is worth 3 in auto and 1 in teleop; LEAVE is 2), no robot program: they play the steps by hand through
ScenarioMatch, as the runner does. The run tests use tests/fake_robot_main.py as the robot program."""

from __future__ import annotations

import asyncio
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import scrimmage.session as session_mod
from range_fixtures import drive, shooter_cfg, spares_world
from scrimmage.checks import ScenarioError, resolve_signal
from scrimmage.scenario import load_scenario_text
from scrimmage.protocol import HalState
from scrimmage.scenario_points import ScenarioMatch, points_lines
from scrimmage.scenario_runner import run_scenario
from scrimmage.session import Session, SessionError
from scrimmage.testrange.balls import BallState
from scrimmage.testrange.build import BALL_BITS
from scrimmage.world import World
from test_range_checks import HUB, RIM, Sess, write_config, write_scenario

ROOT = Path(__file__).resolve().parent.parent
BASE = (ROOT / "scrimmage" / "testrange" / "ranges" / "2026.toml").read_text(encoding="utf-8")
GAME = '''
[[zone]]
name = "start_zone"
corners = [[{x0}, {y0}], [{x1}, {y0}], [{x1}, {y1}], [{x0}, {y1}]]

[game]
name = "test match"
auto_seconds = 5
pause_seconds = 2
teleop_seconds = 5
points = {{ hub_blue = {{ auto = 3, teleop = 1 }} }}
leave = {{ zone = "start_zone", points = 2 }}
'''


@pytest.fixture(autouse=True)
def quick_nt_give_up(monkeypatch):
    monkeypatch.setattr(session_mod, "NT_CONNECT_TIMEOUT", 0.5)


@pytest.fixture
def game_range(tmp_path):
    """A range file: the 2026 range plus a [game]; the start zone is a box round where the robot starts."""
    x, y, _ = World(shooter_cfg(), None).robot_pose()
    path = tmp_path / "game_range.toml"
    path.write_text(BASE + GAME.format(x0=x - 0.6, y0=y - 0.6, x1=x + 0.6, y1=y + 0.6), encoding="utf-8")
    return path


def game_cfg(path: Path) -> dict:
    return shooter_cfg(range={"file": str(path), "floor_balls": False})


def drop(w) -> None:
    i = int(np.flatnonzero(w.test_range.balls.state == BallState.SPARE)[0])
    w.test_range.balls.place(i, np.array([HUB[0], HUB[1] + 0.40, RIM + 0.3]), np.zeros(3), np.zeros(3), BALL_BITS)


def play(w, m, mode, seconds, *, drop_first=False, **pwm) -> None:
    """One scenario step: begin, an optional piece dropped at its start, `seconds` of physics, finish."""
    m.begin(mode, w.time, seconds)
    if drop_first:
        drop(w)
    drive(w, seconds, **pwm)
    m.finish(w.time)


def world_with_match(path: Path, steps: int):
    w = spares_world(game_cfg(path))
    m = w.scenario_match = ScenarioMatch(w, steps)
    return w, m


# ------------------------------------------------------------------------------------------------ the phases

def test_pieces_split_by_the_step_mode(game_range):
    w, m = world_with_match(game_range, 3)
    play(w, m, "auto", 3.0, drop_first=True)
    play(w, m, "disabled", 3.0)
    play(w, m, "teleop", 3.0, drop_first=True)
    m.close()
    p = m.points()
    assert (p.auto, p.teleop, p.endgame, p.total) == (3.0, 1.0, 0.0, 4.0)
    assert [(i.source, i.phase, i.count) for i in p.items] == [("hub_blue", "auto", 1), ("hub_blue", "teleop", 1)]


def test_a_piece_scored_in_a_disabled_step_scores_nothing(game_range):
    w, m = world_with_match(game_range, 2)
    play(w, m, "disabled", 3.0, drop_first=True)
    play(w, m, "teleop", 1.0)
    p = m.points()
    assert p.total == 0.0
    assert [(i.source, i.phase, i.count, i.points) for i in p.items] == [("hub_blue", "pause", 1, 0.0)]


def test_a_test_mode_step_is_neither_phase(game_range):
    w, m = world_with_match(game_range, 1)
    play(w, m, "test", 3.0, drop_first=True)
    assert m.points().items == () and m.phases.of(1.0) is None


def test_steps_can_repeat(game_range):
    w, m = world_with_match(game_range, 4)
    play(w, m, "auto", 3.0, drop_first=True)
    play(w, m, "teleop", 3.0, drop_first=True)
    play(w, m, "auto", 3.0, drop_first=True)
    play(w, m, "teleop", 3.0)
    assert (m.points().auto, m.points().teleop) == (6.0, 1.0)


def test_points_auto_read_partway_through(game_range):
    w, m = world_with_match(game_range, 2)
    sess = Sess(w)
    auto, teleop, total = (resolve_signal(n, w) for n in ("points.auto", "points.teleop", "points"))
    m.begin("auto", w.time, 3.0)
    drop(w)
    drive(w, 0.05)
    early = auto.read(sess)
    drive(w, 2.0)
    mid = (auto.read(sess), teleop.read(sess), total.read(sess))
    drive(w, 0.95)
    m.finish(w.time)
    play(w, m, "teleop", 1.0)
    assert early == 0.0                          # not scored yet
    assert mid == (3.0, 0.0, 3.0)                # scored by t = 2.05, still in the auto step
    assert auto.read(sess) == 3.0 and teleop.read(sess) == 0.0   # a later read at the end: the same


def test_a_read_never_counts_a_piece_scored_after_it(game_range):
    w, m = world_with_match(game_range, 1)
    sess = Sess(w)
    auto = resolve_signal("points.auto", w)
    m.begin("auto", w.time, 3.0)
    drop(w)
    drive(w, 2.0)
    at_two = auto.read(sess)
    assert at_two == 3.0
    scored_t = [r["scored_t"] for r in w.test_range.ball_log() if r["goal"]][0]
    assert scored_t <= w.time and m.points(at=scored_t - 0.01).auto == 0.0


def test_leave_scores_when_the_robot_is_out_of_the_zone_as_auto_ends(game_range):
    w, m = world_with_match(game_range, 2)
    play(w, m, "auto", 3.0, p0=1.0, p1=-1.0)         # drives out of the box (1.2 m across) and ends there
    play(w, m, "teleop", 1.0)
    assert any(i.source == "leave" for i in m.points().items)
    assert m.points().auto == 2.0


def test_no_leave_when_the_robot_stays_in_the_zone(game_range):
    w, m = world_with_match(game_range, 2)
    play(w, m, "auto", 1.0)
    play(w, m, "teleop", 1.0)
    assert not any(i.source == "leave" for i in m.points().items)


def test_leave_counts_in_an_at_end_read_of_the_auto_step(game_range):
    w, m = world_with_match(game_range, 2)
    auto = resolve_signal("points.auto", w)
    m.begin("auto", w.time, 3.0)
    drive(w, 3.0, p0=1.0, p1=-1.0)
    assert auto.read(Sess(w)) == 2.0                 # the step's own end, before finish()


# ------------------------------------------------------------------------------------------------ 2024: endgame and the Score line

def match_2024(n_steps: int):
    """The 2024 world (the speaker example's robot, on the built-in range with its [game] and ONSTAGE level), and a
    ScenarioMatch whose ball log and held climb level are set by hand: the cheapest honest test of the endgame and
    of a Score line with points, with no robot code and no climb. Real ball logging is the scenario runs' above, not
    these."""
    cfg = tomllib.loads((ROOT / "examples" / "blocks" / "speaker" / "note-shooter-standin.toml").read_text(encoding="utf-8"))
    w = World(cfg, ROOT / "examples" / "blocks" / "speaker")
    m = w.scenario_match = ScenarioMatch(w, n_steps)
    w.climb = SimpleNamespace(sample=lambda: {"climb.level": 1}, after_physics=lambda: None)        # holding the first level: ONSTAGE
    return w, m


def hold(w, seconds: float) -> None:
    for _ in range(int(seconds / w.model.opt.timestep)):
        w.step(HalState())


def test_a_2024_scenario_scores_the_endgame_when_it_ends_and_says_it_in_the_score_lines():
    w, m = match_2024(2)
    m.begin("auto", w.time, 0.2)
    hold(w, 0.2)
    m.finish(w.time)
    t_auto = w.time - 0.1
    m.begin("teleop", w.time, 0.2)
    hold(w, 0.1)
    t_teleop = w.time
    hold(w, 0.1)
    m.finish(w.time)
    m.log = lambda: [{"goal": "speaker_blue", "scored_t": t_auto}, {"goal": "amp_blue", "scored_t": t_teleop}]
    mid = m.points(at=t_teleop)
    assert mid.endgame == 0.0                           # the match isn't over yet: the climb isn't scored
    m.close()
    p = m.points()
    assert (p.auto, p.teleop, p.endgame, p.total) == (5.0, 1.0, 3.0, 9.0)      # SPEAKER 5 in auto, AMP 1 in teleop, ONSTAGE 3
    assert [(i.source, i.phase, i.points) for i in p.items] == [
        ("speaker_blue", "auto", 5.0), ("amp_blue", "teleop", 1.0), ("climb", "endgame", 3.0)]
    rep = m.report()
    assert rep["level"] == "ONSTAGE"
    assert points_lines(rep) == ["9 points: 5 in auto, 1 in teleop, 3 in endgame",
                                 "1 scored in speaker_blue in auto: 5 points",
                                 "1 scored in amp_blue in teleop: 1 point",
                                 "climbed to ONSTAGE: 3 points"]


# ------------------------------------------------------------------------------------------------ the errors

def test_a_points_signal_on_a_range_with_no_game_is_an_error():
    w = World(shooter_cfg(), None)
    with pytest.raises(ScenarioError, match=r"points.auto needs a \[game\] table in the range file"):
        resolve_signal("points.auto", w)


def test_a_points_signal_with_no_range_is_an_error():
    from range_fixtures import KITBOT
    w = World({"part": KITBOT, "battery": {"nominal_voltage": 12.0, "resistance": 0.0}}, None)
    with pytest.raises(ScenarioError, match=r"points needs a robot on a test range"):
        resolve_signal("points", w)


def test_an_unknown_points_signal_lists_the_four(game_range):
    w = World(game_cfg(game_range), None)
    with pytest.raises(ScenarioError, match=r"the points signals are points, points.auto, points.teleop, "
                                            r"points.endgame"):
        resolve_signal("points.pause", w)


def test_the_no_robot_program_error_points_at_the_test_tab(tmp_path):
    cfg = write_config(tmp_path, shooter_cfg())
    text = cfg.read_text().split("[robot_program]")[0]
    bare = tmp_path / "bare.toml"
    bare.write_text(text)
    with pytest.raises(SessionError) as e:
        Session(bare)
    msg = str(e.value)
    assert "has no [robot_program] section" in msg
    assert "A match without robot code is a Test-tab test with [test] match = true" in msg


# ------------------------------------------------------------------------------------------------ a run

def test_a_scenario_reads_points_and_the_report_says_them(tmp_path, game_range):
    cfg = write_config(tmp_path, game_cfg(game_range))
    scn = write_scenario(tmp_path, cfg, '''
[[step]]
name = "auto"
mode = "auto"
duration = 0.3
[[step.check]]
at_end = { signal = "points.auto", equals = 0 }

[[step]]
name = "teleop"
mode = "teleop"
duration = 0.3
[[step.check]]
at_end = { signal = "points", equals = 0 }
''')
    report = asyncio.run(run_scenario(load_scenario_text(scn.read_text(), scn), tmp_path / "out"))
    assert report.error is None and report.passed, report.summary()
    assert report.points is not None and report.points["points"]["total"] == 0.0
    data = json.loads((tmp_path / "out" / "report.json").read_text())
    assert data["points"]["total"] == 0 and data["points"]["items"] == []
    md = (tmp_path / "out" / "report.md").read_text()
    assert "- 0 points: 0 in auto, 0 in teleop, 0 in endgame" in md


def test_a_scenario_on_a_range_with_no_game_has_no_points(tmp_path):
    cfg = write_config(tmp_path, shooter_cfg())
    scn = write_scenario(tmp_path, cfg, '''
[[step]]
name = "auto"
mode = "auto"
duration = 0.3
''')
    report = asyncio.run(run_scenario(load_scenario_text(scn.read_text(), scn), tmp_path / "out"))
    assert report.error is None and report.points is None
    assert "points" not in json.loads((tmp_path / "out" / "report.json").read_text())
    assert "points" not in (tmp_path / "out" / "report.md").read_text()


def test_a_points_signal_in_a_scenario_with_no_game_is_reported_as_an_error(tmp_path):
    cfg = write_config(tmp_path, shooter_cfg())
    scn = write_scenario(tmp_path, cfg, '''
[[step]]
name = "auto"
mode = "auto"
duration = 0.3
[[step.check]]
at_end = { signal = "points", min = 0 }
''')
    report = asyncio.run(run_scenario(load_scenario_text(scn.read_text(), scn), tmp_path / "out"))
    assert report.error is not None and "needs a [game] table" in str(report.error)
