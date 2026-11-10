"""A match in the Test tab (`[test] match = true`): the range's `[game]` phases, the disabled pause, the points.

The range is the 2026 range with a start zone and a `[game]` added (auto 5 s, pause 2 s, teleop 5 s; hub_blue is worth
3 points in auto and 1 in teleop; LEAVE is 2). The robot is configs/fastmode_range.toml's, as in test_blocks_shooting:
it shoots its preloaded balls from the blocks, so the points come from real shots into a real hub."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scrimmage import i18n, units
from scrimmage.blocks import store
from scrimmage.blocks.palette import problems
from scrimmage.blocks.program import TestFileError, dumps, from_json, loads
from scrimmage.blocks.run import TestRun, points_lines, world_for
from scrimmage.blocks.store import load_result, save_run

ROOT = Path(__file__).resolve().parent.parent
BASE = (ROOT / "scrimmage" / "testrange" / "ranges" / "2026.toml").read_text(encoding="utf-8")
GAME = '''
[[zone]]
name = "start_zone"
corners = [[3.0, 3.0], [4.2, 3.0], [4.2, 5.0], [3.0, 5.0]]

[game]
name = "test match"
auto_seconds = {auto}
pause_seconds = {pause}
teleop_seconds = {teleop}
points = {{ hub_blue = {{ auto = 3, teleop = 1 }} }}
leave = {{ zone = "start_zone", points = 2 }}
'''
AUTO, PAUSE, TELEOP = 5.0, 2.0, 5.0

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
seconds = 2.0
"""
LEAVE = """
[[block]]
do = "stop"
motor = "all"

[[block]]
do = "drive"
direction = "back"
distance = 1.2
volts = 6.0
"""


CLIMB = '''
[climb]
rule = "lowest-point"
levels = { TOP = 4 }
'''


def make(tmp: Path, blocks: str, test: str = "match = true\n", name: str = "m.test.toml", ranged: bool = True,
         game: tuple = (AUTO, PAUSE, TELEOP), extra: str = ""):
    """The robot (configs/fastmode_range.toml with its robot program dropped, on the match range, with `extra` added
    to the range file) and the test."""
    text = (ROOT / "configs" / "fastmode_range.toml").read_text(encoding="utf-8")
    text, n = re.subn(r"\n\[robot_program\]\n.*?(?=\n# ---- Drive)", "\n", text, flags=re.S)
    assert n == 1
    if ranged:
        text = text.replace('file = "2026"', 'file = "match_range.toml"')
        (tmp / "match_range.toml").write_text(BASE + GAME.format(auto=game[0], pause=game[1], teleop=game[2]) + extra,
                                              encoding="utf-8")
    (tmp / "shooter.toml").write_text(text, encoding="utf-8")
    return loads('[test]\nrobot = "shooter.toml"\n' + test + blocks, tmp / name)


@pytest.fixture(scope="module")
def played(tmp_path_factory):
    """Shoots in auto, leaves the start zone, then waits for teleop and checks the points on the way."""
    tmp = tmp_path_factory.mktemp("match")
    blocks = SHOOT + LEAVE + """
[[block]]
do = "expect"
that = { points = { phase = "auto", at_least = 5 } }

[[block]]
do = "wait_until"
until = { match = { phase = "teleop" } }

[[block]]
do = "expect"
that = { match = { phase = "teleop" } }
"""
    program = make(tmp, blocks)
    world = world_for(program)
    return {"tmp": tmp, "program": program, "world": world, "result": TestRun(world, program).run()}


def test_the_run_lasts_the_match_after_the_settle(played):
    r = played["result"]
    assert r.status == "finished", r.outcome
    start = r.match["start"]
    assert start == pytest.approx(0.3, abs=0.01)                    # the settle is not match time
    # the blocks finish well before the match ends, yet the run plays to its end; then the pieces settle
    assert r.signals["t"][-1] - r.settle_s == pytest.approx(start + AUTO + PAUSE + TELEOP, abs=0.01)
    assert r.settle_s >= 0.0


def test_the_marks_hold_each_phase_at_its_time(played):
    r = played["result"]
    start = r.match["start"]
    marks = [(m["phase"], m["t"]) for m in r.marks if m["kind"] == "phase"]
    assert [p for p, _ in marks] == ["auto", "pause", "teleop"]
    assert [t for _, t in marks] == pytest.approx([start, start + AUTO, start + AUTO + PAUSE], abs=0.003)
    assert [m["text"] for m in r.marks if m["kind"] == "phase"] == ["auto", "pause", "teleop"]
    assert r.match["auto"] == pytest.approx([start, start + AUTO])
    assert r.match["pause"] == pytest.approx([start + AUTO, start + AUTO + PAUSE])
    assert r.match["teleop"] == pytest.approx([start + AUTO + PAUSE, start + AUTO + PAUSE + TELEOP])


def test_wait_until_teleop_waits_for_teleop(played):
    r = played["result"]
    teleop0 = r.match["teleop"][0]
    waited = r.checks[-1]
    assert r.passed, [c["line"] for c in r.checks]
    assert waited["passed"] and waited["t"] == pytest.approx(teleop0, abs=0.003)
    assert waited["line"].startswith("passed: the match is in teleop at ")


def test_points_are_scored_from_the_log(played):
    r = played["result"]
    p, m = r.points, r.match
    assert m["left"] is True and m["level"] is None
    shots = [x for x in r.pieces if x["goal"] == "hub_blue"]
    auto = [x for x in shots if x["scored_t"] < m["auto"][1]]
    teleop = [x for x in shots if m["teleop"][0] <= x["scored_t"] <= m["teleop"][1]]
    assert len(auto) >= 1
    assert p["auto"] == 3 * len(auto) + 2                           # hub_blue 3 each, and LEAVE
    assert p["teleop"] == 1 * len(teleop)
    assert p["endgame"] == 0 and p["total"] == p["auto"] + p["teleop"]
    assert {"source": "leave", "phase": "auto", "count": 1, "points": 2.0} in p["items"]
    assert r.rules == []
    checks = {c["place"][0]: c for c in r.checks}
    assert checks[len(played["program"].blocks) - 3]["passed"]      # points in auto at least 5 (LEAVE + a shot)


def holding_top(monkeypatch) -> None:
    """The robot holds the range's one climb level (TOP, 4 points) from the first step on: a stubbed climb watch, as
    tests/test_scenario_points.py's 2024 test does, with no climber and no bars."""
    from scrimmage.blocks.conditions import Watch
    update = Watch.update

    def held(self) -> None:
        update(self)
        self.climb = {"climb.level": 1}
    monkeypatch.setattr(Watch, "update", held)


def test_a_level_held_at_the_end_scores_the_endgame_even_when_the_last_step_is_short_of_it(tmp_path, monkeypatch):
    # 0.3 s + a 12 s game at dt 0.001: d.time's float error puts the last step a hair short of the end, and nothing
    # is in the air, so the settle takes no steps; the run is still scored at the match's end
    holding_top(monkeypatch)
    program = make(tmp_path, '[[block]]\ndo = "wait"\nseconds = 1.0\n', extra=CLIMB)
    r = TestRun(world_for(program), program).run()
    assert r.status == "finished", r.outcome
    end = r.match["teleop"][1]
    assert r.settle_s == 0.0 and r.t_end == pytest.approx(end, abs=1e-6) and r.t_end < end    # the case it pins
    assert r.match["level"] == "TOP"
    assert r.points["endgame"] == 4.0 and r.points["total"] == 4.0
    assert {"source": "climb", "phase": "endgame", "count": 1, "points": 4.0} in r.points["items"]
    assert "climbed to TOP: 4 points" in points_lines(r)


def test_an_endgame_points_check_passes_while_the_level_is_held(tmp_path, monkeypatch):
    # the score if the match ended now: the level held now (the result is still scored at the end)
    holding_top(monkeypatch)
    blocks = """
[[block]]
do = "wait"
seconds = 1.0

[[block]]
do = "expect"
that = { points = { phase = "endgame", at_least = 4 } }

[[block]]
do = "expect"
that = { points = { at_least = 4 } }
"""
    program = make(tmp_path, blocks, extra=CLIMB)
    r = TestRun(world_for(program), program).run()
    assert r.passed and r.status == "finished", (r.outcome, [c["line"] for c in r.checks])
    assert [c["passed"] for c in r.checks] == [True, True]
    assert all(c["t"] < r.match["auto"][1] for c in r.checks)      # read in auto, long before the match's end
    assert r.points["endgame"] == 4.0


def test_an_auto_points_check_before_auto_ends_says_leave_is_judged_then(tmp_path):
    blocks = '[[block]]\ndo = "expect"\nthat = { points = { phase = "auto", at_least = 1 } }\n'
    program = make(tmp_path, blocks)
    with units.machine_text():
        r = TestRun(world_for(program), program).run()
    assert r.status == "check failed", r.outcome
    hint = "(LEAVE counts once auto ends: wait until the match is in the pause or teleop)"
    assert r.checks[0]["failure"] == f"expected 1 or more points in auto, but there were 0 {hint}"
    assert len({tuple(c["failure"] for c in w["checks"]) for w in r.words.values()}) == 1     # the same everywhere


def test_the_settle_after_the_match_runs_with_the_robot_disabled(tmp_path, monkeypatch):
    # the motors spin to the match's end; a piece still in play keeps the settle going (here: 0.2 s, stubbed, since
    # only a fed piece is tracked), and the settle steps disabled, as at the buzzer
    from scrimmage.blocks.feeds import Outcomes
    blocks = '[[block]]\ndo = "wait"\nseconds = 10.8\n' + SHOOT
    program = make(tmp_path, blocks)
    world = world_for(program)
    end = 0.3 + AUTO + PAUSE + TELEOP
    monkeypatch.setattr(Outcomes, "settled", lambda self: float(world.data.time) >= end + 0.2)
    seen: list[tuple[float, object]] = []
    step = world.step

    def spy(hal, *a, **k):
        seen.append((float(world.data.time), hal.get("DriverStation", "", ">enabled")))
        return step(hal, *a, **k)
    world.step = spy
    r = TestRun(world, program).run()
    assert r.status == "finished", r.outcome
    assert r.match["teleop"][1] == pytest.approx(end)
    assert r.settle_s == pytest.approx(0.2, abs=0.002)
    after = [enabled for t, enabled in seen if t >= end - 1e-6]
    assert after and all(enabled is False for enabled in after)
    teleop = [enabled for t, enabled in seen if r.match["teleop"][0] + 0.01 < t < end - 0.01]
    assert teleop and all(enabled is None for enabled in teleop)     # enabled in teleop: the frame isn't sent


def test_a_points_check_passes_or_fails_on_them(played, tmp_path):
    r = played["result"]
    need = int(r.points["auto"]) + 1
    blocks = SHOOT + LEAVE + f"""
[[block]]
do = "wait_until"
until = {{ match = {{ phase = "pause" }} }}

[[block]]
do = "expect"
that = {{ points = {{ phase = "auto", at_least = {need} }} }}
"""
    program = make(tmp_path, blocks)
    r2 = TestRun(world_for(program), program).run()
    assert r2.status == "check failed", r2.outcome
    assert f"expected {need} or more points in auto, but there were {int(r.points['auto'])}" in r2.outcome
    assert "LEAVE counts" not in r2.outcome                         # checked in the pause: LEAVE is judged
    assert r2.points["auto"] == r.points["auto"]                    # the same run: the points the run had so far


def test_a_drive_across_the_pause_is_neutral_then_drives_again(tmp_path):
    blocks = """
[[block]]
do = "drive"
direction = "forward"
seconds = 9.0
volts = 4.0
"""
    program = make(tmp_path, blocks)
    r = TestRun(world_for(program), program).run()
    assert r.status == "finished", r.outcome
    start = r.match["start"]
    t = r.signals["t"]
    drive = [n for n in r.signals if n.endswith(".drive.current")]
    assert drive, list(r.signals)
    pause0, pause1 = start + AUTO, start + AUTO + PAUSE

    def peak(lo: float, hi: float) -> float:
        return max(abs(r.signals[n][i]) for n in drive for i, x in enumerate(t) if lo <= x < hi)

    assert peak(start + 1.0, pause0) > 1.0                          # driving in auto
    assert peak(pause0 + 0.5, pause1) < 0.01                        # the pause: neutral (the current dies in 0.3 s)
    assert peak(pause1 + 0.5, pause1 + TELEOP) > 1.0                # and again in teleop
    assert peak(start + 9.0 + 0.5, pause1 + TELEOP) < 0.01          # the drive's 9 s counted the pause: over by 9.5 s
    # the drive block's seconds counted match time, the pause included: 9 s is over at the match's end
    assert all(c["passed"] for c in r.checks)


def test_the_problems(tmp_path):
    # match = true on a range with no [game]
    program = make(tmp_path, SHOOT, ranged=False)                   # the plain 2026 range
    bad = problems(program, world_for(program))
    assert {"place": [], "message": "a match needs a field with a [game]; this field has none"} in bad
    # a match or points condition in a test that isn't a match
    blocks = '[[block]]\ndo = "expect"\nthat = { match = { phase = "auto" } }\n' \
             '[[block]]\ndo = "expect"\nthat = { points = { at_least = 1 } }\n'
    program = make(tmp_path, blocks, test="")
    bad = problems(program, world_for(program))
    want = "a {} condition only works in a match; put match = true in the test's [test]"
    assert [(b["place"], b["message"]) for b in bad] == [([0], want.format("match")), ([1], want.format("points"))]
    # a match test with the conditions is fine on the match range
    program = make(tmp_path, blocks, test="match = true\n")
    assert problems(program, world_for(program)) == []


def test_time_limit_with_match_is_an_error(tmp_path):
    with pytest.raises(TestFileError, match="a match sets the run's length"):
        make(tmp_path, "", test="match = true\ntime_limit = 30.0\n")
    with pytest.raises(TestFileError, match="match must be true or false"):
        make(tmp_path, "", test='match = "yes"\n')
    with pytest.raises(TestFileError, match="a match sets the run's length"):
        from_json({"robot": "r.toml", "match": True, "time_limit": 30.0, "blocks": []}, tmp_path / "x.test.toml")


def test_the_result_is_stored_and_loaded_with_its_points(played):
    r, program, tmp = played["result"], played["program"], played["tmp"]
    folder = save_run(r, program, tmp / "runs")
    kept = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    assert kept["match"] == r.match and kept["points"] == r.points and kept["rules"] == []
    loaded = load_result(tmp / "runs", folder.name)
    assert loaded["points"]["total"] == r.points["total"]
    from types import SimpleNamespace
    lines = points_lines(SimpleNamespace(points=loaded["points"], match=loaded["match"]))
    p = r.points
    assert lines[0] == f"{p['total']:g} points: {p['auto']:g} in auto, {p['teleop']:g} in teleop, " \
                       f"{p['endgame']:g} in endgame"
    assert "left the starting zone: 2 points" in lines
    assert any(re.fullmatch(r"\d+ scored in hub_blue in auto: \d+ points?", ln) for ln in lines)
    assert len(lines) == 1 + len(p["items"])


def test_the_points_lines_are_the_same_in_machine_text_in_every_language(played):
    from types import SimpleNamespace
    kept = SimpleNamespace(points=played["result"].points, match=played["result"].match)
    with units.machine_text():
        english = points_lines(kept)
    assert english[0].endswith(" in endgame") and "points: " in english[0]
    for lang in i18n.languages():
        for system in units.SYSTEMS:
            with units.shown_in(system, lang), units.machine_text():
                assert points_lines(kept) == english
    # the run's own sentences (the marks, the checks) are the same in every setting when made in machine text
    with units.machine_text():
        r = TestRun(played["world"], played["program"]).run()
    assert r.words is not None
    assert {tuple(w["marks"]) for w in r.words.values()} == {("auto", "pause", "teleop")}
    assert len({tuple(c["line"] for c in w["checks"]) for w in r.words.values()}) == 1


def test_run_result_has_the_points_lines_first(played, tmp_path):
    from scrimmage.assembler.simlock import SimLock
    from scrimmage.assembler.test_session import TestSession
    program = played["program"]
    program.path.write_text(dumps(program), encoding="utf-8")
    save_run(played["result"], program, store.runs_dir(program.path))
    out = TestSession(program.path, SimLock()).run_result("001")
    p = played["result"].points
    assert out["lines"][0].startswith(f"{p['total']:g} points: ")
    assert out["points"] == p and out["match"] == played["result"].match and out["rules"] == []


def test_an_expect_the_match_never_reached_fails_the_test(tmp_path):
    blocks = """
[[block]]
do = "wait"
seconds = 30.0

[[block]]
do = "repeat"
times = 2

[[block.body]]
do = "expect"
that = { points = { at_least = 1 } }

[[block]]
do = "expect"
that = { match = { phase = "teleop" } }
"""
    program = make(tmp_path, blocks)
    r = TestRun(world_for(program), program).run()
    assert r.status == "finished" and not r.passed
    assert [c["place"] for c in r.checks] == [[1, 0], [2]]
    assert not any(c["passed"] for c in r.checks)
    assert r.checks[1]["line"] == "failed (block 3): the match ended before this check ran"
    assert r.checks[0]["line"].startswith("failed (block 1 inside block 2)")
    assert r.checks[1]["t"] == pytest.approx(r.match["teleop"][1], abs=0.003)
    assert "the match ended before the last block" in r.outcome


def test_a_wait_for_a_phase_lasts_to_the_match_end_unless_told_otherwise(tmp_path):
    # a 15 s auto: the wait from the first block lasts 15 s, longer than a wait until's usual 10 s
    wait = """
[[block]]
do = "wait_until"
until = { match = { phase = "teleop" } }
%s
[[block]]
do = "expect"
that = { match = { phase = "teleop" } }
"""
    program = make(tmp_path, wait % "", game=(15.0, 3.0, 2.0))
    r = TestRun(world_for(program), program).run()
    assert r.status == "finished" and r.passed, r.outcome
    assert r.checks[0]["t"] == pytest.approx(r.match["teleop"][0], abs=0.003)
    # a timeout the student gives wins
    program = make(tmp_path, wait % "timeout = 1.0\n", game=(15.0, 3.0, 2.0), name="t.test.toml")
    r = TestRun(world_for(program), program).run()
    assert r.status == "timed out", r.outcome
    assert "waiting for the match to be in teleop timed out at 1.3 s" in r.outcome


def test_a_plain_test_has_none_of_these(tmp_path):
    program = make(tmp_path, SHOOT + """
[[block]]
do = "wait"
seconds = 1.0
""", test="")
    r1 = TestRun(world_for(program), program).run()
    r2 = TestRun(world_for(program), program).run()
    assert (r1.match, r1.points, r1.rules) == (None, None, None)
    assert not [m for m in r1.marks if m["kind"] == "phase"]
    folder = save_run(r1, program, tmp_path / "runs")
    kept = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    assert not {"match", "points", "rules"} & set(kept)
    assert points_lines(r1) == []
    assert r1.signals == r2.signals and r1.t_end == r2.t_end        # and the run is the same as ever
