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
from scrimmage.blocks.palette import problems
from scrimmage.blocks.program import TestFileError, from_json, loads
from scrimmage.blocks.run import TestRun, points_lines, world_for
from scrimmage.blocks.store import load_result, save_run

ROOT = Path(__file__).resolve().parent.parent
RANGE = (ROOT / "scrimmage" / "testrange" / "ranges" / "2026.toml").read_text(encoding="utf-8") + '''
[[zone]]
name = "start_zone"
corners = [[3.0, 3.0], [4.2, 3.0], [4.2, 5.0], [3.0, 5.0]]

[game]
name = "test match"
auto_seconds = 5
pause_seconds = 2
teleop_seconds = 5
points = { hub_blue = { auto = 3, teleop = 1 } }
leave = { zone = "start_zone", points = 2 }
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


def make(tmp: Path, blocks: str, test: str = "match = true\n", name: str = "m.test.toml", ranged: bool = True):
    """The robot (configs/fastmode_range.toml with its robot program dropped, on the match range) and the test."""
    text = (ROOT / "configs" / "fastmode_range.toml").read_text(encoding="utf-8")
    text, n = re.subn(r"\n\[robot_program\]\n.*?(?=\n# ---- Drive)", "\n", text, flags=re.S)
    assert n == 1
    if ranged:
        text = text.replace('file = "2026"', 'file = "match_range.toml"')
        (tmp / "match_range.toml").write_text(RANGE, encoding="utf-8")
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
timeout = 20.0

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
    assert peak(pause0 + 0.5, pause1) < 0.01                        # the pause: neutral (the current dies away in 0.3 s)
    assert peak(pause1 + 0.5, pause1 + TELEOP) > 1.0                # and again in teleop
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


def test_the_points_lines_read_the_same_in_every_language_and_in_machine_text(played):
    from types import SimpleNamespace
    kept = SimpleNamespace(points=played["result"].points, match=played["result"].match)
    english = points_lines(kept)
    with units.machine_text():
        assert points_lines(kept) == english
    for lang in i18n.languages():       # points are plain numbers: no setting changes a word of them
        for system in units.SYSTEMS:
            with units.shown_in(system, lang):
                assert points_lines(kept) == english
    # the run's own sentences (the marks, the checks) are the same in every setting when made in machine text
    with units.machine_text():
        r = TestRun(played["world"], played["program"]).run()
    assert r.words is not None
    texts = {tuple(m for m in w["marks"]) for w in r.words.values()}
    assert texts == {("auto", "pause", "teleop")}
    assert len({tuple(c["line"] for c in w["checks"]) for w in r.words.values()}) == 1


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
