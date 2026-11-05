"""Scenarios on the test range: the score signals, by_time checks, a scenario's [range] and robot_env, the report's
score and per-ball table, and Session.state()'s range counts.

The world tests use range_fixtures' robots on the 2026 range (floor balls off) with no robot program; the run tests
write a config with tests/fake_robot_main.py as the robot program (it drives nothing on the range, so the balls
stay where the range puts them)."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import tomlkit

import scrimmage.session as session_mod
from range_fixtures import KITBOT, range_cfg, shooter_cfg, spares_world
from scrimmage.checks import ScenarioError, build_check, resolve_signal
from scrimmage.protocol import HalState
from scrimmage.scenario import load_scenario, load_scenario_text
from scrimmage.scenario_runner import run_scenario
from scrimmage.session import Session
from scrimmage.testrange.balls import BallState
from scrimmage.testrange.build import BALL_BITS
from scrimmage.testrange.spec import BUILTIN_RANGES
from scrimmage.world import World

TESTS = Path(__file__).resolve().parent
HUB = (4.625, 4.035)        # hub_blue's opening center (scrimmage/testrange/ranges/2026.toml)
RIM = 1.829


@pytest.fixture(autouse=True)
def quick_nt_give_up(monkeypatch):
    monkeypatch.setattr(session_mod, "NT_CONNECT_TIMEOUT", 0.5)  # the fake robot serves no NetworkTables


class Sess:
    """The duck-typed session the checks read: its world and the time."""

    def __init__(self, w):
        self.world = w

    @property
    def t(self) -> float:
        return self.world.time


def drop_in_hub(w) -> int:
    """A spare ball dropped into hub_blue's funnel (it scores within about a second)."""
    i = int(np.flatnonzero(w.test_range.balls.state == BallState.SPARE)[0])
    w.test_range.balls.place(i, np.array([HUB[0], HUB[1] + 0.40, RIM + 0.3]), np.zeros(3), np.zeros(3), BALL_BITS)
    return i


def run(w, seconds, checks=(), sess=None):
    hal = HalState()
    for _ in range(int(round(seconds / w.model.opt.timestep))):
        w.step(hal)
        for c in checks:
            c.tick(sess, w.time)


def write_config(tmp_path: Path, cfg: dict, args=("--lockstep",), name="range_robot.toml") -> Path:
    """`cfg` (a range_fixtures config) with tests/fake_robot_main.py as its robot program."""
    cfg = {**cfg, "robot_program": {"command": ["{python}", "fake_robot_main.py", *args], "cwd": str(TESTS),
                                    "start_timeout": 15}}
    path = tmp_path / name
    path.write_text(tomlkit.dumps(cfg))
    return path


def write_scenario(tmp_path: Path, config: Path, body: str, name="s.toml") -> Path:
    p = tmp_path / name
    p.write_text(f'config = "{config}"\n' + body)
    return p


# ------------------------------------------------------------------------------------------------ signals

def test_score_signals_read_counts():
    w = World(shooter_cfg(range={"preload": 2}), None)
    drop_in_hub(w)
    run(w, 2.0)
    c = w.test_range.counts()
    assert c["goals"]["hub_blue"] == 1 and c["held"] == 2
    s = Sess(w)
    read = {name: resolve_signal(name, w).read(s)
            for name in ("score", "score.hub_blue", "score.hub_red", "fired", "held", "on_field")}
    assert read == {"score": c["score"], "score.hub_blue": c["goals"]["hub_blue"], "score.hub_red": 0,
                    "fired": c["fired"], "held": c["held"], "on_field": c["on_field"]}
    assert read["score"] == 1 and read["held"] == 2 and read["fired"] == 0 and read["on_field"] == 0
    assert all(type(v) is int for v in read.values())


@pytest.mark.parametrize("name", ["score", "score.hub_blue", "fired", "held", "on_field"])
def test_score_signal_without_range_explains(name):
    w = World({"part": KITBOT}, None)
    with pytest.raises(ScenarioError, match=r"needs a robot on a test range: add \[range\] to the config") as e:
        resolve_signal(name, w)
    assert str(e.value).startswith(name) and "docs/test-range.md" in str(e.value)


def test_score_signal_without_range_fails_before_the_run(tmp_path):
    """Review Focus 5: a scenario reading score on a robot without a range is an error before any step runs."""
    scn = write_scenario(tmp_path, TESTS / "fake_robot.toml",
                         '[[step]]\nmode = "auto"\nduration = 0.2\n  [[step.check]]\n'
                         '  at_end = { signal = "score", min = 1 }\n')
    rep = asyncio.run(run_scenario(load_scenario(scn), tmp_path / "out"))
    assert rep.exit_code == 2 and "[range]" in rep.error and rep.steps == []
    assert not (tmp_path / "out" / "robot.log").exists() or "fake robot connected" not in (
        tmp_path / "out" / "robot.log").read_text()


def test_scenario_range_on_a_robot_without_a_range_fails_before_the_run(tmp_path):
    scn = write_scenario(tmp_path, TESTS / "fake_robot.toml",
                         '[range]\npreload = 2\n\n[[step]]\nmode = "auto"\nduration = 0.2\n')
    rep = asyncio.run(run_scenario(load_scenario(scn), tmp_path / "out"))
    assert rep.exit_code == 2 and rep.steps == []
    assert "[range]" in rep.error and "fake_robot.toml" in rep.error


def test_unknown_goal_lists_goals():
    w = World(range_cfg(), None)
    with pytest.raises(ScenarioError, match=r"score\.hub_green.*hub_blue, hub_red"):
        resolve_signal("score.hub_green", w)


# ------------------------------------------------------------------------------------------------ by_time

def test_by_time_passes_when_reached_early():
    w = spares_world(range_cfg())
    s = Sess(w)
    c = build_check({"by_time": {"signal": "score", "min": 1, "t": 3}}, w, "s.toml, step 1")
    assert c.ticks and c.label == "by_time score >= 1 by t=3"
    c.begin(s)
    drop_in_hub(w)
    run(w, 3.0, [c], s)
    scored_t = w.test_range.ball_log()[0]["scored_t"]
    r = c.end(s)
    assert r.passed, r.message
    t = round(scored_t, 4)
    assert r.message == f"score reached 1 at t={t:g} (by 3)" and 0 < t < 3
    assert r.measured == 1


def test_by_time_fails_with_value_at_t():
    w = spares_world(range_cfg())
    s = Sess(w)
    c = build_check({"by_time": {"signal": "score", "min": 2, "t": 2}}, w, "s.toml, step 1")
    c.begin(s)
    drop_in_hub(w)
    run(w, 2.5, [c], s)
    assert w.test_range.ball_log()[0]["scored_t"] < 2
    r = c.end(s)
    assert not r.passed
    assert r.message == "score was 1 at t=2; expected >= 2 by t=2"
    assert r.measured == 1 and r.first_failure_t == pytest.approx(2.0)


def test_by_time_counts_only_up_to_t():
    """Reaching the value after t still fails: the check reads the signal at step times up to t."""
    w = spares_world(range_cfg())
    s = Sess(w)
    run(w, 1.0)                     # the step starts at t = 1: step times are from its start
    c = build_check({"by_time": {"signal": "score", "min": 1, "t": 0.1}}, w, "s.toml, step 1")
    c.begin(s)
    drop_in_hub(w)
    run(w, 2.0, [c], s)
    assert w.test_range.counts()["score"] == 1
    r = c.end(s)
    assert not r.passed and r.message == "score was 0 at t=0.1; expected >= 1 by t=0.1"


def test_by_time_t_beyond_step_rejected(tmp_path):
    body = ('config = "robot.toml"\n[[step]]\nmode = "auto"\nduration = 10\n  [[step.check]]\n'
            '  by_time = { signal = "score", min = 8, t = 15 }\n')
    with pytest.raises(ScenarioError, match=r"step 1, check 1, by_time: t = 15 is beyond the step's 10 s"):
        load_scenario_text(body, tmp_path / "s.toml")
    for spec, why in (("min = 8, t = -1", "t must be"), ("min = 8", "by_time needs t"),
                      ('min = 8, t = "x"', "t must be")):
        with pytest.raises(ScenarioError, match=why):
            load_scenario_text(body.replace("min = 8, t = 15", spec), tmp_path / "s.toml")
    assert load_scenario_text(body.replace("t = 15", "t = 10"), tmp_path / "s.toml").steps[0].checks


def test_by_time_rejects_unknown_keys():
    w = World(range_cfg(), None)
    with pytest.raises(ScenarioError, match="unknown key 'when'"):
        build_check({"by_time": {"signal": "score", "min": 1, "t": 1, "when": "end"}}, w, "s.toml, step 1")


# ------------------------------------------------------------------------------------------------ scenario keys

OVERRIDE = """
[range]
start = "blue_right"
preload = 3

[[step]]
mode = "disabled"
duration = 0.3
  [[step.check]]
  pose = { x = 3.42, y = 2.51, heading = 0, tol_xy = 0.05 }
  [[step.check]]
  at_end = { signal = "held", equals = 3 }
"""


def test_scenario_range_overrides_config(tmp_path):
    # the config places the robot itself (start_pose) and preloads nothing: the scenario's start replaces both
    cfg = write_config(tmp_path, shooter_cfg(robot={"start_pose": [2.0, 4.035, 0.0]}))
    scn = load_scenario(write_scenario(tmp_path, cfg, OVERRIDE))
    assert scn.range == {"start": "blue_right", "preload": 3}
    rep = asyncio.run(run_scenario(scn, tmp_path / "own"))
    assert rep.exit_code == 0, rep.to_dict()
    assert rep.range["held"] == 3

    async def passed_in():
        s = Session(cfg)
        await s.start()
        try:
            assert s.world.test_range.counts()["held"] == 0
            rep = await run_scenario(scn, tmp_path / "passed", session=s)
            # the run is over: the next restart goes back to the config's start and preload
            assert s.world.test_range.settings() == {"start": None, "preload": 0, "floor_balls": False, "seed": 1}
            return rep, s.state()
        finally:
            await s.stop()
    rep, state = asyncio.run(passed_in())
    assert rep.exit_code == 0, rep.to_dict()
    assert rep.range["held"] == 3
    assert state["range"]["held"] == 3 and "ledger" not in state["range"]
    assert set(state["range"]) == {"score", "goals", "fired", "held", "on_field"}


def test_scenario_range_bad_values_are_errors(tmp_path):
    cfg = write_config(tmp_path, shooter_cfg())
    for body, why in (('[range]\nstart = "nowhere"\n', "nowhere"), ("[range]\npreload = 9\n", "preload"),
                      ("[range]\nfloor_balls = 1\n", "floor_balls must be true or false"),
                      ("[range]\nlanes = 2\n", "lanes")):
        scn = write_scenario(tmp_path, cfg, body + '\n[[step]]\nmode = "disabled"\nduration = 0.1\n')
        try:
            rep = asyncio.run(run_scenario(load_scenario(scn), tmp_path / "out"))
        except ScenarioError as e:
            assert why in str(e)
            continue
        assert rep.exit_code == 2 and why in rep.error and rep.steps == [], rep.error


def test_scenario_range_mismatch_on_passed_session(tmp_path):
    """Review Focus 5: a session on one range, a scenario naming another: an error naming both, before any step."""
    cfg = write_config(tmp_path, shooter_cfg())
    other = tmp_path / "other_range.toml"
    shutil.copy(BUILTIN_RANGES["2026"], other)
    scn = load_scenario(write_scenario(tmp_path, cfg, '[range]\nfile = "other_range.toml"\npreload = 1\n\n'
                                       '[[step]]\nmode = "auto"\nduration = 0.2\n'))

    async def go():
        s = Session(cfg)
        await s.start()
        try:
            return await run_scenario(scn, tmp_path / "out", session=s), s.world.test_range.settings()
        finally:
            await s.stop()
    rep, settings = asyncio.run(go())
    assert rep.exit_code == 2 and rep.steps == []
    assert str(other) in rep.error and str(BUILTIN_RANGES["2026"]) in rep.error
    assert settings["preload"] == 0          # nothing was applied


def test_robot_env_reaches_program(tmp_path):
    scn = write_scenario(tmp_path, TESTS / "fake_robot.toml", """
robot_env = { SCRIMMAGE_AUTO = "shoot_8" }

[[step]]
mode = "auto"
duration = 0.5
  [[step.check]]
  console = { contains = "SCRIMMAGE_AUTO=shoot_8" }
""")
    rep = asyncio.run(run_scenario(load_scenario(scn), tmp_path / "out"))
    assert rep.exit_code == 0, rep.to_dict()

    async def passed_in():
        s = Session(TESTS / "fake_robot.toml")
        await s.start()
        try:
            rep = await run_scenario(load_scenario(scn), tmp_path / "passed", session=s)
            await s.restart_robot()      # no env: the config's environment again
            return rep, s.robot.env
        finally:
            await s.stop()
    rep, env = asyncio.run(passed_in())
    assert rep.exit_code == 0, rep.to_dict()
    assert "SCRIMMAGE_AUTO" not in env


def test_robot_env_values_are_text(tmp_path):
    with pytest.raises(ScenarioError, match="robot_env"):
        load_scenario_text('config = "r.toml"\nrobot_env = { A = 1 }\n[[step]]\nmode = "auto"\nduration = 1\n',
                           tmp_path / "s.toml")


# ------------------------------------------------------------------------------------------------ the report

def test_report_has_balls_and_score(tmp_path):
    cfg = write_config(tmp_path, shooter_cfg(range={"preload": 2}))
    scn = write_scenario(tmp_path, cfg, '[[step]]\nmode = "disabled"\nduration = 0.2\n  [[step.check]]\n'
                                        '  at_end = { signal = "score", equals = 0 }\n')
    out = tmp_path / "out"
    rep = asyncio.run(run_scenario(load_scenario(scn), out))
    assert rep.exit_code == 0, rep.to_dict()
    data = json.loads((out / "report.json").read_text())
    assert data["range"]["held"] == 2 and data["range"]["goals"] == {"hub_blue": 0, "hub_red": 0}
    assert [b["outcome"] for b in data["balls"]] == ["held", "held"]      # only the balls that took part
    assert data["ball_summary"] == {"untouched": 0, "spare": data["range"]["ledger"]["spare"]}
    assert len(data["ball_states"]) == sum(data["range"]["ledger"].values())
    assert data["ball_states"].count("held") == 2
    md = (out / "report.md").read_text()
    assert "## Score" in md and "## Balls" in md
    score = md.split("## Score", 1)[1].split("##", 1)[0]
    assert "hub_blue" in score and "hub_red" in score and "fired" in score and "held" in score
    table = md.split("## Balls", 1)[1]
    assert table.count("| held |") == 2
    assert f"Not listed: {data['ball_summary']['spare']} spare balls (unused slots, or floor balls turned off)." in table


# ------------------------------------------------------------------------------------------------ live outputs

def test_log_row_publishes_ball_poses():
    from scrimmage.bridge import Bridge

    class Link:
        def __init__(self):
            self.balls = []

        def publish_truth(self, x, y, yaw):
            pass

        def publish_balls(self, poses):
            self.balls.append(poses.copy())

        def odometry(self):
            return None

    w = spares_world(range_cfg())
    drop_in_hub(w)
    b = Bridge(w, "localhost", 0, nt=False)
    b.nt_link = Link()
    b._log_row(0.0, "disabled")
    assert len(b.nt_link.balls) == 1 and b.nt_link.balls[0].shape == (1, 7)
    np.testing.assert_array_equal(b.nt_link.balls[0], w.test_range.ball_poses())
    rangeless = Bridge(World({"part": KITBOT}, None), "localhost", 0, nt=False)
    rangeless.nt_link = Link()
    rangeless._log_row(0.0, "disabled")
    assert rangeless.nt_link.balls == []


def test_signals_list_the_range():
    from scrimmage.assembler.signals import signals
    assert signals(World(range_cfg(), None))["range"] == ["score", "score.hub_blue", "score.hub_red", "fired", "held",
                                                          "on_field"]
    assert "range" not in signals(World({"part": KITBOT}, None))


# ------------------------------------------------------------------------------------------------ the editor

def test_editor_places_range_and_by_time_errors(tmp_path):
    from scrimmage.assembler import scenario_validate as sv
    path = tmp_path / "x.toml"
    step = '\n[[step]]\nmode = "auto"\nduration = 1.0\n'
    kitbot = f'config = "{TESTS.parent / "configs" / "kitbot.toml"}"\n'
    cases = [
        (kitbot + step + '  [[step.check]]\n  by_time = { signal = "battery", min = 1, t = 5 }\n',
         "step0/check0/by_time.t", "beyond"),
        (kitbot + step + '  [[step.check]]\n  by_time = { signal = "score", min = 1, t = 1 }\n',
         "step0/check0/by_time.signal", "[range]"),
        (kitbot + "[range]\npreload = 1\n" + step, "scenario/range", "[range]"),
        (kitbot + "[range]\nlanes = 1\n" + step, "scenario/range", "lanes"),
        (kitbot + "robot_env = [1]\n" + step, "scenario/robot_env", "robot_env"),
    ]
    for text, field, why in cases:
        r = sv.check(text, path)
        assert [e.field for e in r.errors] == [field], (text, r.errors)
        assert why in r.errors[0].message
    cfg = tmp_path / "range.toml"
    cfg.write_text(tomlkit.dumps(range_cfg()))
    r = sv.check(f'config = "{cfg}"\n' + step + '  [[step.check]]\n  by_time = { signal = "score.hub_red", '
                 'min = 1, t = 1 }\n', path)
    assert r.ok, r.errors


def test_report_ball_row_says_where_it_scored(tmp_path):
    from scrimmage.scenario_runner import Report
    w = spares_world(range_cfg())
    i = drop_in_hub(w)
    run(w, 2.0)
    rep = Report("s.toml", "")
    rep.range, rep.balls = w.test_range.counts(), w.test_range.ball_log()
    rep.write(tmp_path)
    rows = [ln for ln in (tmp_path / "report.md").read_text().splitlines() if ln.startswith(f"| {i} |")]
    assert len(rows) == 1 and "| scored |" in rows[0]
    assert f"hub_blue at t={rep.balls[i]['scored_t']:.2f}; first hit hub_blue.funnel_" in rows[0]
    assert json.loads((tmp_path / "report.json").read_text())["balls"][i]["goal"] == "hub_blue"


@pytest.mark.parametrize("outcome,where", [("on_field", "rest at (6.10, 4.20) m"),
                                           ("out_of_field", "left at (6.10, 4.20, 0.08) m")])
def test_report_ball_row_compares_a_misses_apex_with_the_rim(outcome, where, units):
    """A ball fired that didn't score: its row says how high it got against the rim of the goal nearest its release
    (did it fall short or go over?)."""
    from scrimmage.scenario_runner import _ball_result
    units("metric")   # tests/test_display_units_bench.py has both systems
    b = {"outcome": outcome, "fired_t": 1.5, "last_pos": [6.1, 4.2, 0.08], "at_rest": True,
         "first_hit": "hub_blue.funnel_0", "apex_z": 1.7, "rim_height": 1.829, "nearest_goal": "hub_blue"}
    assert _ball_result(b) == f"{where}; first hit hub_blue.funnel_0; apex 1.70 m vs rim 1.83 m (hub_blue)"
    held = {**b, "outcome": "on_field", "fired_t": None, "apex_z": None, "nearest_goal": None, "rim_height": None,
            "first_hit": None}
    assert _ball_result(held) == "rest at (6.10, 4.20) m"      # never fired: no apex
    scored = {**b, "outcome": "scored", "goal": "hub_blue", "scored_t": 2.0}
    assert "apex" not in _ball_result(scored)


def test_report_lists_only_balls_that_took_part(tmp_path):
    """The untouched floor balls are counted, not listed: a ball scored, a ball moved by more than its radius and a
    ball nudged by less (still untouched)."""
    from types import SimpleNamespace
    from scrimmage.scenario_runner import Report, _capture_range
    w = World(range_cfg(range={"floor_balls": True}), None)
    balls = w.test_range.balls
    start = w.test_range.range.floor_balls
    # ball 0 is the grid's corner (lowest x and y), ball 1 its neighbor in +y: moves toward -y touch nothing
    balls.place(1, np.array([HUB[0], HUB[1] + 0.40, RIM + 0.3]), np.zeros(3), np.zeros(3), BALL_BITS)
    balls.place(2, np.array([start[2][0], start[0][1] - 0.5, balls.r]), np.zeros(3), np.zeros(3), BALL_BITS)
    balls.place(0, np.array([start[0][0], start[0][1] - 0.02, balls.r]), np.zeros(3), np.zeros(3), BALL_BITS)
    run(w, 2.0)
    rep = Report("s.toml", "")
    _capture_range(SimpleNamespace(world=w), rep, True)
    n = len(start)
    assert [b["id"] for b in rep.balls] == [1, 2]
    assert rep.balls[0]["outcome"] == "scored" and rep.balls[1]["outcome"] == "on_field"
    pool = sum(len(s) for s in w.parts.layout.station_slots.values())     # the OUTPOSTS' FUEL, parked
    assert rep.ball_summary == {"untouched": n - 2, "spare": pool}
    assert len(rep.ball_states) == n + pool and rep.ball_states[1] == "scored" and rep.ball_states[0] == "on_field"
    assert set(rep.ball_states[n:]) <= {"spare"}
    rep.write(tmp_path)
    md = (tmp_path / "report.md").read_text()
    assert (f"Not listed: {n - 2} floor balls untouched (still where the range put them); {pool} spare balls"
            in md)
    assert sum(ln.startswith("| ") and "---" not in ln for ln in md.split("## Balls", 1)[1].splitlines()) == 3
    json.loads((tmp_path / "report.json").read_text())       # valid JSON


# ------------------------------------------------------------------------------------------------ fix round 1

BAD_RANGE = (('start = "nowhere"', "nowhere"), ("preload = 99", "preload = 99"))


@pytest.mark.parametrize("line,why", BAD_RANGE)
def test_bad_scenario_range_is_the_scenarios_error_in_the_runner(tmp_path, line, why):
    cfg = write_config(tmp_path, shooter_cfg())
    scn = write_scenario(tmp_path, cfg, f'[range]\n{line}\n\n[[step]]\nmode = "disabled"\nduration = 0.1\n')
    rep = asyncio.run(run_scenario(load_scenario(scn), tmp_path / "out"))
    assert rep.exit_code == 2 and rep.steps == []
    assert rep.error.startswith("s.toml: range: ") and why in rep.error and str(cfg) not in rep.error, rep.error

    async def passed_in():
        s = Session(cfg)
        await s.start()
        try:
            return await run_scenario(load_scenario(scn), tmp_path / "passed", session=s)
        finally:
            await s.stop()
    rep = asyncio.run(passed_in())
    assert rep.exit_code == 2 and rep.steps == [] and rep.error.startswith("s.toml: range: "), rep.error


@pytest.mark.parametrize("line,why", BAD_RANGE)
def test_bad_scenario_range_is_the_scenarios_error_in_the_editor(tmp_path, line, why):
    from scrimmage.assembler import scenario_validate as sv
    cfg = write_config(tmp_path, shooter_cfg())
    r = sv.check(f'config = "{cfg}"\n[range]\n{line}\n\n[[step]]\nmode = "auto"\nduration = 1.0\n', tmp_path / "x.toml")
    assert r.robot_problem is None
    assert [e.field for e in r.errors] == ["scenario/range"], r.errors
    assert why in r.errors[0].message and "fix the robot" not in r.errors[0].message


def test_a_broken_robot_range_is_still_the_robots_problem(tmp_path):
    from scrimmage.assembler import scenario_validate as sv
    cfg = write_config(tmp_path, shooter_cfg(range={"start": "nowhere"}))
    r = sv.check(f'config = "{cfg}"\n[range]\npreload = 1\n\n[[step]]\nmode = "auto"\nduration = 1.0\n',
                 tmp_path / "x.toml")
    assert r.robot_problem is not None and "nowhere" in r.robot_problem


def test_robot_env_without_a_robot_program_is_an_error(tmp_path):
    cfg = tmp_path / "r.toml"
    cfg.write_text(tomlkit.dumps(range_cfg()))
    scn = write_scenario(tmp_path, cfg, 'robot_env = { SCRIMMAGE_AUTO = "x" }\n\n[[step]]\nmode = "auto"\nduration = 1\n')
    rep = asyncio.run(run_scenario(load_scenario(scn), tmp_path / "out"))
    assert rep.exit_code == 2 and "robot_env" in rep.error and "[robot_program]" in rep.error, rep.error
    from scrimmage.assembler import scenario_validate as sv
    r = sv.check(scn.read_text(), scn)
    assert [e.field for e in r.errors] == ["scenario/robot_env"], r.errors


def test_robot_env_on_an_attach_session_is_an_error(tmp_path):
    from fake_robot import FakeRobot, next_port
    scn = load_scenario(write_scenario(tmp_path, TESTS / "fake_robot.toml",
                                       'robot_env = { SCRIMMAGE_AUTO = "x" }\n\n[[step]]\nmode = "auto"\nduration = 0.1\n'))

    async def go():
        port = next_port()
        s = Session(TESTS / "fake_robot.toml", attach=True, port=port, nt=False)
        start = asyncio.ensure_future(s.start())
        await asyncio.sleep(0.5)
        async with FakeRobot(port, lockstep=True):
            await start
            try:
                rep = await run_scenario(scn, tmp_path / "out", session=s)
                with pytest.raises(session_mod.SessionError, match="attach"):
                    await s.restart_robot(env={"A": "1"})
                return rep
            finally:
                await s.stop()
    rep = asyncio.run(go())
    assert rep.exit_code == 2 and rep.steps == [] and "robot_env" in rep.error and "attach" in rep.error, rep.error


def test_rangeless_report_keeps_the_old_schema(tmp_path):
    from scrimmage.scenario_runner import Report
    d = Report("s.toml", "").to_dict()
    assert not {"range", "balls", "ball_states", "ball_summary"} & set(d)
    rep = Report("s.toml", "")
    rep.range, rep.ball_summary = {"score": 0}, {"untouched": 0, "spare": 0}
    assert {"range", "balls", "ball_states", "ball_summary"} <= set(rep.to_dict())


def test_ball_poses_resent_after_restart_clock():
    from scrimmage.bridge import Bridge

    class Link:
        def __init__(self):
            self.resets = 0

        def reset_balls(self):
            self.resets += 1

    b = Bridge(World(range_cfg(), None), "localhost", 0, nt=False)
    b.nt_link = Link()
    b._restart_clock()
    assert b.nt_link.resets == 1
