"""The `held` condition and check: a coral in a hand-written grabber on the stand-in (tests/pieces_helpers.py), held
while the arm swings, slipping out with too little squeeze, and the sentence that says when and what was moving."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pieces_helpers import EXPECT_HELD, blocks, grab_run, grab_test, move_arm, run_grab, squeeze, wait  # noqa: F401

from scrimmage.blocks import grip_watch, store
from scrimmage.blocks.conditions import describe
from scrimmage.blocks.palette import problems
from scrimmage.blocks.program import Cond, dumps, load, loads
from scrimmage.blocks.run import _recast, world_for
from scrimmage.testrange.pieces import parse_kind


def test_a_closed_grabber_holds_the_coral_while_the_arm_swings(grab_run):
    assert grab_run.status == "finished" and grab_run.checks[-1]["passed"], grab_run.outcome
    assert grab_run.checks[-1]["text"] == "a coral is held by grabber"
    assert grab_run.marks == []
    assert max(abs(a) for a in grab_run.signals["grabber.arm"]) > 75.0         # it really swung


def test_too_little_squeeze_slips_and_says_when_and_what_was_moving(tmp_path):
    _, _, r = run_grab(tmp_path, blocks(0.2))
    assert r.status == "check failed", r.outcome
    assert re.fullmatch(r"a check failed \(block 9\): the coral slipped out of grabber at \d+\.\d s while "
                        r"grabber\.arm was moving", r.outcome), r.outcome
    (mark,) = r.marks
    assert mark["kind"] == "slip" and mark["t"] == pytest.approx(r.t_end, abs=0.02)
    assert mark["text"] == r.outcome.split(": ", 1)[1]
    assert r.checks[-1]["line"] == f"failed (block 9): {mark['text']}"      # its own time, not a second " at"


def test_held_needs_a_tenth_of_a_second(tmp_path, monkeypatch):
    """From the first step the coral meets all three, holds() is false 0.05 s on and true 0.15 s on."""
    seen = []
    update = grip_watch.HeldTracker.update

    def record(self, t):
        update(self, t)
        key = (0, "grabber")
        seen.append((t, key in self._acc, self.held(None, "grabber")))

    monkeypatch.setattr(grip_watch.HeldTracker, "update", record)
    run_grab(tmp_path, blocks(4.0, throughout=False))
    first_held = next(k for k, (_, _, held) in enumerate(seen) if held)
    start = first_held
    while start > 0 and seen[start - 1][1]:
        start -= 1
    t0 = seen[start][0]
    at = {round(t - t0, 3): held for t, _, held in seen[start:first_held + 100]}
    assert at[0.05] is False and at[0.15] is True
    assert seen[first_held][0] - t0 == pytest.approx(grip_watch.HELD_S - 0.001, abs=1e-6)   # the first step counts


def test_a_piece_on_the_floor_touching_the_jaws_isnt_held(tmp_path):
    touching = """
[[block]]
do = "expect"
that = { touch = { robot = "grabber", field = "ball.0" } }
"""
    held = EXPECT_HELD.replace("throughout = true\n", "")
    _, _, r = run_grab(tmp_path, squeeze(4.0) + wait(1.0) + touching + held)
    assert r.checks[0]["passed"], r.outcome                        # the jaws are closed on it
    assert r.status == "check failed" and r.outcome.endswith("the coral was never held by grabber"), r.outcome
    assert r.marks == []


def test_review_focus_3_never_held_and_nothing_moving(tmp_path):
    """Never held: the jaws never close. Nothing moving: the jaws let go (a stop block) more than a second after the
    last move."""
    _, _, r1 = run_grab(tmp_path / "a", move_arm(-15) + wait(0.3) + EXPECT_HELD.replace("throughout = true\n", ""))
    assert "the coral was never held by grabber" in r1.outcome, r1.outcome
    stop = "".join(f'\n[[block]]\ndo = "stop"\nmotor = "grabber.{j}"\n' for j in ("jaw_b", "jaw_f"))
    _, _, r2 = run_grab(tmp_path / "b", squeeze(4.0) + wait(1.0) + move_arm(-15) + wait(0.3) + EXPECT_HELD
                        + wait(1.2) + stop + wait(1.0))
    assert r2.status == "check failed", r2.outcome
    assert re.search(r"the coral slipped out of grabber at \d+\.\d s while nothing was moving$", r2.outcome), r2.outcome


def _coral():
    return parse_kind(Path("coral.toml"), "coral", {"preset": "2025-coral"}, Path("."))


def test_held_round_trips_and_describes(tmp_path):
    path = grab_test(tmp_path)
    p = load(path)
    (cond,) = [b.args["that"] for b in p.blocks if b.do == "expect"]
    assert cond == Cond("held", {"piece": "any", "by": "grabber"})
    assert loads(dumps(p), path).blocks == p.blocks
    with pytest.raises(ValueError, match="by"):
        loads(dumps(p).replace('by = "grabber"', 'body = "grabber"'), path)
    coral = _coral()
    assert describe(Cond("held", {"piece": "any", "by": "claw"}), kinds={"coral": coral}) == "a coral is held by claw"
    assert describe(Cond("held", {"piece": "any", "by": "claw"})) == "a piece is held by claw"     # kinds unknown
    two = {"coral": coral, "algae": coral}
    assert describe(Cond("held", {"piece": "any", "by": "claw"}), kinds=two) == "a piece is held by claw"   # mixed
    named = Cond("held", {"piece": "ball.3", "by": "claw"})
    assert describe(named, kinds={"coral": coral}, names={"ball.3": "coral 4"}) == "coral 4 is held by claw"
    assert _recast("a coral is held by claw", 1) == "a coral to be held by claw"
    assert _recast("a coral is held by claw", 0) == "a coral being held by claw"
    assert describe(Cond("piece_in", {"body": "intake.hopper", "piece": "any"})) == "a piece reaches intake.hopper"


def test_held_by_a_name_the_robot_doesnt_have(tmp_path):
    path = grab_test(tmp_path, blocks().replace('by = "grabber"', 'by = "claw"'))
    p = load(path)
    assert [x["message"] for x in problems(p, world_for(p))] == ['no body or part "claw" on this robot (held by)']


def test_a_kept_run_keeps_its_marks(tmp_path):
    program, _, r = run_grab(tmp_path / "run", blocks(0.2))
    folder = store.save_run(r, program, tmp_path / "runs")
    assert store.load_result(tmp_path / "runs", folder.name)["marks"] == r.marks != []
