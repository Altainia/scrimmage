"""The game's rules in a Test-tab match (possession, extension, height): checked each step, reported once at the
first breach with the peak value, never enforced. Possession counts the pieces the ball path holds plus the ones held
by contact; extension and height are the climb monitor's. A game with no `[game.rules]` tracks none of it."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from scrimmage import units
from scrimmage.blocks import possession
from scrimmage.blocks.possession import POSSESSION_GRACE_S, RuleWatch
from scrimmage.blocks.program import loads
from scrimmage.blocks.run import TestRun, rules_lines, world_for
from scrimmage.climb.run import ClimbRun
from scrimmage.climb.script import G106_HEIGHT, G107_EXTENSION, parse_script
from scrimmage.config import build_world, load_config
from scrimmage.testrange.game import Rules

ROOT = Path(__file__).resolve().parent.parent
IN = 0.0254
BASE = (ROOT / "scrimmage" / "testrange" / "ranges" / "2026.toml").read_text(encoding="utf-8")
HANGAR = (ROOT / "scrimmage" / "testrange" / "ranges" / "2022-hangar.toml").read_text(encoding="utf-8")


# ---- the counting, without a world --------------------------------------------------------------------------------


def stub() -> RuleWatch:
    rw = RuleWatch.__new__(RuleWatch)
    rw.breaches, rw._episode = {}, {}
    return rw


def over(rw: RuleWatch, count: float, seconds: float, start: float = 1.0, dt: float = 0.005) -> None:
    """`count` pieces against a limit of 1 at every step from `start` for `seconds`, then one fewer."""
    for k in range(round(seconds / dt)):
        rw._see("possession", start + k * dt, count, 1.0, POSSESSION_GRACE_S)
    rw._see("possession", start + seconds, 1.0, 1.0, POSSESSION_GRACE_S)


def test_more_than_the_limit_for_3_1_s_is_a_breach_and_for_2_9_s_is_not():
    rw = stub()
    over(rw, 2.0, 2.9)
    assert rw.breaches == {}
    rw = stub()
    over(rw, 2.0, 3.1)
    assert rw.breaches["possession"] == {"rule": "possession", "t": 1.0, "value": 2.0, "limit": 1.0}


def test_exactly_3_s_over_the_limit_is_not_a_breach():
    """G403 says more than MOMENTARY: a stretch of exactly the grace isn't a breach."""
    rw = stub()
    rw._see("possession", 1.0, 2.0, 1.0, POSSESSION_GRACE_S)
    rw._see("possession", 1.0 + POSSESSION_GRACE_S, 2.0, 1.0, POSSESSION_GRACE_S)
    assert rw.breaches == {}
    rw._see("possession", 1.0 + POSSESSION_GRACE_S + 0.005, 2.0, 1.0, POSSESSION_GRACE_S)
    assert rw.breaches["possession"]["t"] == 1.0


def test_a_short_stretch_over_does_not_add_up():
    rw = stub()
    for start in (1.0, 5.0, 9.0):           # three 2 s stretches apart: the grace starts again each time
        over(rw, 2.0, 2.0, start)
    assert rw.breaches == {}


def test_a_breach_is_reported_once_with_its_peak():
    rw = stub()
    over(rw, 2.0, 3.5, start=1.0)
    over(rw, 5.0, 0.5, start=10.0)          # later and higher: the record's value rises, its time stays
    over(rw, 3.0, 4.0, start=20.0)          # later and lower: nothing changes
    assert rw.result() == [{"rule": "possession", "t": 1.0, "value": 5.0, "limit": 1.0}]


def test_the_peak_while_over_comes_before_the_grace_runs_out():
    rw = stub()
    for k, n in enumerate([2.0] * 100 + [4.0] * 100 + [2.0] * 500):
        rw._see("possession", 1.0 + k * 0.005, n, 1.0, POSSESSION_GRACE_S)
    assert rw.breaches["possession"]["value"] == 4.0 and rw.breaches["possession"]["t"] == 1.0


def test_extension_is_a_breach_the_moment_it_is_over():
    rw = stub()
    rw._see("extension", 1.0, 0.30, 0.3048, 0.0)
    assert rw.breaches == {}
    rw._see("extension", 1.5, 0.32, 0.3048, 0.0)
    rw._see("extension", 1.6, 0.35, 0.3048, 0.0)
    rw._see("extension", 1.7, 0.10, 0.3048, 0.0)
    assert rw.result() == [{"rule": "extension", "t": 1.5, "value": 0.35, "limit": 0.3048}]


# ---- a match on the 2026 range ------------------------------------------------------------------------------------


def shooter(tmp: Path, rules: str, preload: int = 8, floor_ball: bool = False, blocks: str = "") -> object:
    """The fastmode robot on the 2026 range with a `[game]` (rules as given) and the test."""
    text = (ROOT / "configs" / "fastmode_range.toml").read_text(encoding="utf-8")
    text, n = re.subn(r"\n\[robot_program\]\n.*?(?=\n# ---- Drive)", "\n", text, flags=re.S)
    assert n == 1
    text = text.replace('file = "2026"', 'file = "match_range.toml"').replace("preload = 8", f"preload = {preload}")
    extra = '\n[[floor_balls]]\nat = [[2.9, 4.035]]\n' if floor_ball else ""
    game = f'\n[game]\nname = "t"\nauto_seconds = 4\npause_seconds = 1\nteleop_seconds = 4\n{rules}'
    (tmp / "match_range.toml").write_text(BASE + extra + game, encoding="utf-8")
    (tmp / "shooter.toml").write_text(text, encoding="utf-8")
    return loads('[test]\nrobot = "shooter.toml"\nmatch = true\n' + blocks, tmp / "m.test.toml")


WAIT = '[[block]]\ndo = "wait"\nseconds = 1.0\n'
SHOOT = """
[[block]]
do = "move"
joint = "launcher.hood"
to = 40.0
unit = "deg"
[[block]]
do = "spin"
motor = "launcher.spin"
volts = 11.0
[[block]]
do = "spin"
motor = "hopper_floor.spin"
volts = 8.0
[[block]]
do = "spin"
motor = "feeder.spin"
volts = 8.0
[[block]]
do = "wait"
seconds = 1.0
"""
PUSH = '[[block]]\ndo = "drive"\ndirection = "back"\ndistance = 1.5\nvolts = 4.0\n'


def play(program):
    world = world_for(program)
    run = TestRun(world, program)
    return run, run.run()


def test_pieces_in_the_hopper_are_controlled(tmp_path):
    run, r = play(shooter(tmp_path, "[game.rules]\npossession = 7\n", blocks=WAIT))
    (b,) = r.rules
    assert b["rule"] == "possession" and b["limit"] == 7 and b["value"] == 8
    assert b["t"] == pytest.approx(r.match["start"], abs=0.02)         # when it went over, not when the grace ran out
    assert run.rule_watch.controlled() <= 8


def test_a_limit_the_robot_keeps_to_is_no_breach(tmp_path):
    _, r = play(shooter(tmp_path, "[game.rules]\npossession = 8\n", blocks=WAIT))
    assert r.rules == []


def watch_counts(monkeypatch) -> list:
    """Every step's controlled count in the runs that follow."""
    seen: list = []
    see = RuleWatch.see

    def spy(self, t):
        see(self, t)
        if self.held is not None:
            seen.append((t, self.controlled()))
    monkeypatch.setattr(RuleWatch, "see", spy)
    return seen


def test_shooting_it_down_inside_the_grace_is_no_breach(tmp_path, monkeypatch):
    seen = watch_counts(monkeypatch)
    _, r = play(shooter(tmp_path, "[game.rules]\npossession = 7\n", blocks=SHOOT))
    start = r.match["start"]
    below = next(t for t, n in seen if n < 8)
    assert below - start < POSSESSION_GRACE_S - 0.2, "the robot shoots its first ball well inside the grace"
    assert r.rules == []


def test_a_piece_pushed_along_the_floor_is_not_controlled(tmp_path, monkeypatch):
    # no preload, one piece behind the robot, which drives back into it and pushes it: possession 1 is never over,
    # and nothing is held by contact (the ray under the piece finds the carpet)
    seen = watch_counts(monkeypatch)
    program = shooter(tmp_path, "[game.rules]\npossession = 1\n", preload=0, floor_ball=True, blocks=PUSH)
    _, r = play(program)
    assert r.status == "finished", r.outcome
    assert seen and max(n for _, n in seen) == 0
    assert r.rules == []


def test_no_rules_tracks_nothing(tmp_path, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a game with no rules made a tracker")
    monkeypatch.setattr(possession, "HeldTracker", refuse)
    run, r = play(shooter(tmp_path, "", blocks=WAIT))
    assert r.status == "finished" and r.rules == [] and run.rule_watch is None
    assert run.world.climb is None or run.world.climb.extent is False


def test_rules_with_only_possession_do_not_read_the_extent(tmp_path):
    run, r = play(shooter(tmp_path, "[game.rules]\npossession = 9\n", blocks=WAIT))
    assert run.rule_watch.mon is None
    assert run.world.climb is None or run.world.climb.extent is False


# ---- extension and height: a robot with an arm ----------------------------------------------------------------------


def climber(tmp: Path, rules: str, pivot_deg: float = 30.0):
    """configs/climber_robot.toml on a copy of the 2022 hangar with a `[game]`; the `high` climber leans forward."""
    (tmp / "hangar_game.toml").write_text(
        HANGAR + f'\n[game]\nname = "t"\nauto_seconds = 2\npause_seconds = 1\nteleop_seconds = 3\n{rules}',
        encoding="utf-8")
    cfg = (ROOT / "configs" / "climber_robot.toml").read_text(encoding="utf-8")
    cfg = cfg.replace('file = "2022-hangar"', 'file = "hangar_game.toml"')
    cfg = re.sub(r"\[robot_program\].*?(?=\n\[telemetry\])", "", cfg, flags=re.S)
    (tmp / "climber.toml").write_text(cfg, encoding="utf-8")
    blocks = f'[[block]]\ndo = "move"\njoint = "high.pivot"\nto = {pivot_deg}\nunit = "deg"\n'
    return loads('[test]\nrobot = "climber.toml"\nmatch = true\n' + blocks, tmp / "c.test.toml")


def test_an_arm_14_in_past_the_frame_on_a_12_in_rule_is_a_breach(tmp_path):
    run, r = play(climber(tmp_path, "[game.rules]\nextension_in = 12\n"))
    assert r.status == "finished", r.outcome
    (b,) = r.rules
    assert b["rule"] == "extension" and b["limit"] == pytest.approx(12 * IN)
    assert b["value"] / IN == pytest.approx(14, abs=1.0)
    assert r.match["start"] <= b["t"] <= r.match["teleop"][1]
    assert b["value"] >= run.world.climb.sample()["climb.extension"] - 1e-6      # the peak, not the last reading


def test_the_same_arm_on_a_16_in_rule_is_no_breach(tmp_path):
    _, r = play(climber(tmp_path, "[game.rules]\nextension_in = 16\n"))
    assert r.rules == []


def test_height_is_the_robots_own_height(tmp_path):
    run, r = play(climber(tmp_path, "[game.rules]\nheight_in = 20\n"))
    (b,) = r.rules
    assert b["rule"] == "height" and b["limit"] == pytest.approx(20 * IN)
    assert b["value"] == pytest.approx(run.world.climb.own_height(), abs=0.05) and b["value"] > b["limit"]
    _, r = play(climber(tmp_path, "[game.rules]\nheight_in = 66\n"))
    assert r.rules == []


# ---- the lines, in the viewer's units ---------------------------------------------------------------------------


def test_the_rules_lines_read_in_the_viewers_units():
    rules = [{"rule": "possession", "t": 4.2, "value": 2, "limit": 1},
             {"rule": "extension", "t": 5.0, "value": 14.1 * IN, "limit": 12 * IN},
             {"rule": "height", "t": 6.0, "value": 50 * IN, "limit": 48 * IN}]
    kept = SimpleNamespace(rules=rules)
    assert rules_lines(kept) == ["rule broken at 4.2 s: held 2 pieces (limit 1)",
                                 "rule broken at 5.0 s: reached 14.1 in past the frame (limit 12 in)",
                                 "rule broken at 6.0 s: stood 50 in tall (limit 48 in)"]
    with units.shown_in("metric", "en"):
        assert rules_lines(kept)[1] == "rule broken at 5.0 s: reached 358.1 mm past the frame (limit 304.8 mm)"
    assert rules_lines(SimpleNamespace(rules=[])) == [] and rules_lines(SimpleNamespace(rules=None)) == []
    one = rules_lines(SimpleNamespace(rules=[{"rule": "possession", "t": 1, "value": 1, "limit": 0}]))
    assert one == ["rule broken at 1.0 s: held 1 piece (limit 0)"]


def test_the_rules_lines_are_the_same_in_machine_text_in_every_language():
    from scrimmage import i18n
    kept = SimpleNamespace(rules=[{"rule": "extension", "t": 5.0, "value": 14.1 * IN, "limit": 12 * IN}])
    with units.machine_text():
        english = rules_lines(kept)
    for lang in i18n.languages():
        for system in units.SYSTEMS:
            with units.shown_in(system, lang), units.machine_text():
                assert rules_lines(kept) == english


# ---- climb run takes its limits from the range's rules -------------------------------------------------------------


def climb_world(tmp: Path, rules: str):
    (tmp / "hangar_game.toml").write_text(
        HANGAR + f'\n[game]\nname = "t"\nauto_seconds = 2\npause_seconds = 1\nteleop_seconds = 3\n{rules}',
        encoding="utf-8")
    cfg = (ROOT / "configs" / "climber_robot.toml").read_text(encoding="utf-8").replace(
        'file = "2022-hangar"', 'file = "hangar_game.toml"')
    cfg = re.sub(r"\[robot_program\].*?(?=\n\[telemetry\])", "", cfg, flags=re.S)
    (tmp / "climber.toml").write_text(cfg, encoding="utf-8")
    return build_world(load_config(tmp / "climber.toml"))


STEP = {"step": [{"wait": {"time": 0.01}}]}


def test_climb_run_limits_come_from_the_ranges_rules(tmp_path):
    w = climb_world(tmp_path, "[game.rules]\nextension_in = 12\nheight_in = 48\n")
    lim = ClimbRun(w, parse_script(STEP, "s")).limits
    assert lim.extension == pytest.approx(12 * IN) and lim.height == pytest.approx(48 * IN)


def test_a_scripts_own_limits_win_key_by_key(tmp_path):
    w = climb_world(tmp_path, "[game.rules]\nextension_in = 12\nheight_in = 48\n")
    lim = ClimbRun(w, parse_script({**STEP, "limits": {"extension_in": 20}}, "s")).limits
    assert lim.extension == pytest.approx(20 * IN) and lim.height == pytest.approx(48 * IN)


def test_climb_run_keeps_the_2022_constants_without_rules(tmp_path):
    lim = ClimbRun(climb_world(tmp_path, ""), parse_script(STEP, "s")).limits
    assert (lim.extension, lim.height) == (G107_EXTENSION, G106_HEIGHT)
    lim = ClimbRun(climb_world(tmp_path, "[game.rules]\npossession = 1\n"), parse_script(STEP, "s")).limits
    assert (lim.extension, lim.height) == (G107_EXTENSION, G106_HEIGHT)      # rules without a length: the fallback
    w = build_world(load_config(ROOT / "configs" / "climber_robot.toml"))   # a range with no game at all
    assert ClimbRun(w, parse_script(STEP, "s")).limits.extension == G107_EXTENSION


def test_a_breach_of_a_game_limit_is_not_named_for_a_2022_rule(tmp_path):
    from scrimmage.climb.run import format_markdown
    w = climb_world(tmp_path, "[game.rules]\nextension_in = 1\nheight_in = 2\n")
    r = ClimbRun(w, parse_script({"step": [{"drive": {"joint": "high.pivot", "to_deg": 30.0},
                                            "wait": {"time": 1.0}}]}, "s")).run()
    assert {n.rule for n in r.notes} == {"G107", "G106"}                 # the machine field is stable
    assert {n.label for n in r.notes} == {"the extension limit", "the height limit"}
    text = format_markdown(r, "c", "s") + " ".join(row.event for row in r.rows)
    assert "G107" not in text and "G106" not in text and "the extension limit: over 1 in" in text
    # the script's own limit keeps the rule's name
    w = climb_world(tmp_path, "[game.rules]\nextension_in = 1\n")
    script = parse_script({"limits": {"extension_in": 1}, "step": [{"drive": {"joint": "high.pivot", "to_deg": 30.0},
                                                                    "wait": {"time": 1.0}}]}, "s")
    assert [n.label for n in ClimbRun(w, script).run().notes if n.rule == "G107"] == ["G107"]
