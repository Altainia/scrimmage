"""The `held` condition and check: a coral in a hand-written grabber on the stand-in (tests/pieces_helpers.py), held
while the arm swings, slipping out with too little squeeze, and the sentence that says when and what was moving."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pieces_helpers import (EXPECT_HELD, GRABBER_PART, blocks, grab_run, grab_test, move_arm,  # noqa: F401
                            run_grab, squeeze, wait)

from scrimmage.blocks import grip_watch, store
from scrimmage.blocks.conditions import describe
from scrimmage.blocks.palette import problems
from scrimmage.blocks.program import Cond, dumps, load, loads
from scrimmage.blocks.run import TestRun, _recast, world_for
from scrimmage.testrange.build import RIGID_SOLMIX, RIGID_SOLREF
from scrimmage.testrange.pieces import parse_kind


def test_a_closed_grabber_holds_the_coral_while_the_arm_swings(grab_run):
    assert grab_run.status == "finished" and grab_run.checks[-1]["passed"], grab_run.outcome
    assert grab_run.checks[-1]["text"] == "a coral is held by grabber"
    assert grab_run.marks == []
    assert max(abs(a) for a in grab_run.signals["grabber.arm"]) > 75.0         # it really swung


def test_firm_squeezes_hold_on_plain_pads(tmp_path):
    """The pads have MuJoCo's default contact (no solref, no priority): the coral's own firm contact keeps a hard
    squeeze from creeping (testrange/build.py's RIGID_SOLREF). The edge: 0.4 V slips in the swing, 0.45 V holds."""
    assert "solref" not in GRABBER_PART and "priority" not in GRABBER_PART
    for volts in (0.45, 8.0):
        _, _, r = run_grab(tmp_path / str(volts), blocks(volts))
        assert r.status == "finished" and r.checks[-1]["passed"], (volts, r.outcome)


def test_a_rigid_piece_has_a_firm_contact_that_governs(tmp_path):
    _, world, _ = run_grab(tmp_path, wait(0.1))
    m = world.model
    body = m.body("ball.0").id
    geoms = [g for g in range(m.ngeom) if m.geom_bodyid[g] == body]
    assert geoms and all(list(m.geom_solref[g]) == list(RIGID_SOLREF) for g in geoms)
    assert all(m.geom_solmix[g] == RIGID_SOLMIX for g in geoms)


def test_too_little_squeeze_slips_and_says_when_and_what_was_moving(tmp_path):
    _, _, r = run_grab(tmp_path, blocks(0.2))
    assert r.status == "check failed", r.outcome
    # the arm (the last block started) first; then whatever else was moving then (the jaws give a little)
    assert re.match(r"a check failed \(block 9\): the coral slipped out of grabber at \d+\.\d s while "
                    r"grabber\.arm( was| and grabber\.jaw_[bf]( and grabber\.jaw_[bf])? were) moving$", r.outcome), \
        r.outcome
    (mark,) = r.marks
    assert mark["kind"] == "slip" and mark["t"] == pytest.approx(r.t_end, abs=0.02)
    assert mark["ball"] == 0                                                 # the coral: body ball.0
    assert mark["text"] == r.outcome.split(": ", 1)[1]
    assert r.checks[-1]["line"] == f"failed (block 9): {mark['text']}"      # its own time, not a second " at"


def test_held_needs_a_tenth_of_a_second(tmp_path, monkeypatch):
    """From the first step the coral meets all three, holds() is false 0.05 s on and true 0.15 s on."""
    seen = []
    update = grip_watch.HeldTracker.update

    def record(self, t, *rest):
        update(self, t, *rest)
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
    assert r.status == "check failed" and re.search(r"the coral was never held by grabber at \d+\.\d s$", r.outcome), \
        r.outcome                                                  # no loss: the check's own time is said
    assert r.marks == []


def test_review_focus_3_never_held_and_nothing_moving(tmp_path):
    """Never held: the jaws never close. A stop block counts as a command: the jaws let go more than a second after
    the last move, and the sentence names them, not "nothing"."""
    _, _, r1 = run_grab(tmp_path / "a", move_arm(-15) + wait(0.3) + EXPECT_HELD.replace("throughout = true\n", ""))
    assert "the coral was never held by grabber" in r1.outcome, r1.outcome
    stop = "".join(f'\n[[block]]\ndo = "stop"\nmotor = "grabber.{j}"\n' for j in ("jaw_b", "jaw_f"))
    _, _, r2 = run_grab(tmp_path / "b", squeeze(4.0) + wait(1.0) + move_arm(-15) + wait(0.3) + EXPECT_HELD
                        + wait(1.2) + stop + wait(1.0))
    assert r2.status == "check failed", r2.outcome
    assert re.search(r"the coral slipped out of grabber at \d+\.\d s while grabber\.jaw_[bf] and "
                     r"grabber\.jaw_[bf] were moving$", r2.outcome), r2.outcome
    assert "grabber.arm" not in r2.outcome


def test_what_was_moving(tmp_path):
    """Commanded in the second before, most recent first, then what was still turning; else nothing."""
    run = TestRun(*reversed(_loaded(tmp_path)))
    run._commanded, run._turning = {"grabber.arm": 0.5, "grabber.jaw_b": 4.5, "drive": 4.8}, {}
    assert run._moving(5.0) == "grabber.jaw_b was moving and the robot was driving"     # the joints first
    run._turning = {5.0: {"grabber.arm", "grabber.jaw_b"}}       # the arm still swinging from a block long ago
    assert run._moving(5.0) == "grabber.jaw_b and grabber.arm were moving and the robot was driving"
    run._commanded = {"grabber.arm": 4.0, "grabber.jaw_b": 4.5, "grabber.jaw_f": 4.6}
    assert run._moving(5.0) == "grabber.jaw_f, grabber.jaw_b and grabber.arm were moving"
    run._commanded, run._turning = {"grabber.arm": 0.5}, {}
    assert run._moving(5.0) == "nothing was moving"


def _loaded(folder: Path):
    p = load(grab_test(folder))
    return p, world_for(p)


def _held_run(folder: Path):
    """The 4 V run, ended with the coral held at the top of the swing: (its TestRun, the tracker, (jaw_b's pairs,
    jaw_f's pairs))."""
    program, world = _loaded(folder)
    run = TestRun(world, program)
    run.run()
    tr, m = run.watch.held, world.model
    assert tr.held(None, "grabber")
    pairs = run.watch._pairs
    side = {j: {p for p in pairs if m.body(m.geom_bodyid[p[0]]).name == f"grabber.{j}"} for j in ("jaw_b", "jaw_f")}
    assert side["jaw_b"] and side["jaw_f"]
    return run, tr, side


def test_a_clean_handoff_between_jaws_keeps_the_hold(tmp_path):
    """Only jaw_b touches for three updates (jaw_f's last sample ages out), then only jaw_f: no body it touches has
    an earlier sample, which is no news, not a slide."""
    run, tr, side = _held_run(tmp_path)
    t, dt = float(run.world.data.time), float(run.world.model.opt.timestep)
    for k, pairs in enumerate([side["jaw_b"]] * 3 + [side["jaw_f"]] * 2, start=1):
        run.watch._pairs = pairs
        tr.update(t + k * dt)
        assert tr.held(None, "grabber"), k
    assert tr.lost(None, "grabber") is None


def test_one_dropped_contact_step_keeps_the_hold_two_end_it(tmp_path):
    run, tr, side = _held_run(tmp_path)
    t, dt = float(run.world.data.time), float(run.world.model.opt.timestep)
    both = side["jaw_b"] | side["jaw_f"]
    for k, pairs in enumerate([set(), both, both], start=1):
        run.watch._pairs = pairs
        tr.update(t + k * dt)
        assert tr.held(None, "grabber"), k
    for k in (4, 5):
        run.watch._pairs = set()
        tr.update(t + k * dt)
    assert not tr.held(None, "grabber")
    when, how, kind = tr.lost(None, "grabber")
    assert when == t + 4 * dt and how == "slipped out of" and kind == "coral"     # at the first step it missed


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
