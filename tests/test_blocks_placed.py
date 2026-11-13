"""The `rests_on` and `seated` conditions and checks: a coral's seated depth on a rod (blocks/placed.py), what a piece
rests on (a coral on a branch, totes on a shelf and on each other), the seated signal (empty while off the rod), and
the words."""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pieces_helpers import CORAL_X, grab_test, squeeze, wait

from range_fixtures import range_cfg
from scrimmage.blocks import store
from scrimmage.blocks.feeds import counts, made_it_in, sentence
from scrimmage.blocks.conditions import describe
from scrimmage.blocks.palette import palette, problems
from scrimmage.blocks.placed import (KNOCK_OFF_SHARE, ON_PIECE, REST_S, Placed, seated_depth, seated_words)
from scrimmage.blocks.program import Cond, TestFileError, dumps, load, loads
from scrimmage.blocks.run import TestRun, _recast, world_for
from scrimmage.protocol import HalState
from scrimmage.testrange.build import BALL_BITS
from scrimmage.testrange.elements import IN, Rod
from scrimmage.testrange.pieces import parse_kind
from scrimmage.world import World

EYE = np.eye(3)
ACROSS = np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]])     # the kind's axis (z) along world x


def _kind(table: dict, name: str = "coral"):
    return parse_kind(Path(f"{name}.toml"), name, table, Path("."))


CORAL = _kind({"preset": "2025-coral"})
CORAL_L = CORAL.hole.length
PEG = Rod("peg", (0.0, 0.0, 0.0), (0.0, 0.0, 4 * IN), 1.0 * IN)     # upright, 4 in long


def _low_end_at(z: float) -> np.ndarray:
    """An upright coral's center with its hole's low end at height z."""
    return np.array([0.0, 0.0, z + CORAL_L / 2])


def step(w, seconds: float, placed: Placed | None = None) -> None:
    for _ in range(int(round(seconds / w.model.opt.timestep))):
        w.step(HalState())
        if placed is not None:
            placed.update(float(w.data.time))


# ---- the depth --------------------------------------------------------------------------------------------------

def test_seated_depth_on_a_vertical_rod():
    d, could = seated_depth(PEG, CORAL, _low_end_at(0.5 * IN), EYE)
    assert d / IN == pytest.approx(3.5) and could / IN == pytest.approx(4.0)
    assert seated_words(d, could) == "seated 3.5 in of 4 in"
    assert seated_words(0.8 * IN, 4 * IN) == "only 0.8 in on: could be knocked off"
    assert seated_words(1.4 * IN, 4 * IN) == "seated 1.4 in of 4 in"       # a third of 4 in is 1.33 in
    assert KNOCK_OFF_SHARE == pytest.approx(1 / 3)


def test_a_coral_longer_than_its_rod_down_against_the_base_is_seated_all_the_way():
    """The low end a hair past the base (pressed against a post), the top past the tip: the rod runs right through."""
    d, could = seated_depth(PEG, CORAL, _low_end_at(-0.001), EYE)
    assert d == pytest.approx(could) == pytest.approx(4 * IN)


def test_a_slanted_coral_on_a_slanted_rod():
    u = np.array([-math.cos(math.radians(35)), 0.0, math.sin(math.radians(35))])
    rod = Rod("branch", (0.0, 0.0, 0.0), tuple(11 * IN * u), 1.66 * IN)
    rot = np.column_stack([np.cross(u, [0.0, 1.0, 0.0]), [0.0, 1.0, 0.0], u])     # the kind's z along the rod
    center = (11 * IN - 6 * IN + CORAL_L / 2) * u                                    # 6 in on
    d, could = seated_depth(rod, CORAL, center, rot)
    assert d / IN == pytest.approx(6.0) and could / IN == pytest.approx(11.0)
    flipped = rot @ np.diag([1.0, -1.0, -1.0])                                      # the hole's axis the other way
    assert seated_depth(rod, CORAL, center, flipped)[0] / IN == pytest.approx(6.0)


def test_a_coral_threaded_from_the_base_end_counts_only_the_rod_it_covers():
    """A rod free at its base end: a coral 1.0 in onto it from below reads 1.0 in, not fully seated."""
    d, could = seated_depth(PEG, CORAL, _low_end_at(1.0 * IN - CORAL_L), EYE)
    assert d / IN == pytest.approx(1.0) and could / IN == pytest.approx(4.0)
    assert seated_words(d, could) == "only 1 in on: could be knocked off"


def test_review_focus_1_lying_across_or_on_the_end_is_empty():
    pos_across = np.array([0.0, 0.0, 4 * IN + 0.5 * IN + 2.25 * IN])          # lying across the peg's tip
    assert seated_depth(PEG, CORAL, pos_across, ACROSS) is None
    assert seated_depth(PEG, CORAL, _low_end_at(8 * IN), EYE) is None           # 4 in above the tip
    assert seated_depth(PEG, CORAL, _low_end_at(4 * IN + 0.001), EYE) is None   # standing on the tip's end
    off = _low_end_at(1 * IN) + [2.5 * IN, 0.0, 0.0]                            # beside it, the rod outside the hole
    assert seated_depth(PEG, CORAL, off, EYE) is None
    tilted = np.array([[1.0, 0.0, 0.0], [0.0, math.cos(0.6), -math.sin(0.6)], [0.0, math.sin(0.6), math.cos(0.6)]])
    assert seated_depth(PEG, CORAL, _low_end_at(1 * IN), tilted) is None        # 34 degrees off the rod's axis
    solid_tube = _kind({"shape": "cylinder", "diameter_in": 4.5, "length_in": 11.875, "mass": 0.6,
                        "material": "pvc"}, "tube")
    assert seated_depth(PEG, solid_tube, _low_end_at(0.5 * IN), EYE) is None


# ---- in the world -----------------------------------------------------------------------------------------------

BRANCH_U = np.array([-math.cos(math.radians(35)), 0.0, math.sin(math.radians(35))])
BRANCH_BASE = np.array([12.0, 4.0, 0.6])
BRANCH_TIP = BRANCH_BASE + 11 * IN * BRANCH_U


def _world(tmp_path, text: str) -> World:
    path = tmp_path / "field.toml"
    path.write_text(text, encoding="utf-8")
    return World(range_cfg(range={"file": str(path), "floor_balls": True}), None)


def branch_world(tmp_path) -> World:
    """A 35 degree branch, 11 in long, off a post, as the reef's L2 (its friction too; a coral on the floor, far
    off)."""
    tip = ", ".join(f"{v:.6f}" for v in BRANCH_TIP)
    return _world(tmp_path, f'''
[range]
name = "a branch"

[piece.coral]
preset = "2025-coral"

[[floor_balls]]
at = [[2.0, 2.0]]

[[element]]
name = "post"
kind = "box"
center = [12.05, 4.0]
size = [0.1, 0.1, 0.65]

[[element]]
name = "branch"
kind = "rod"
base = [12.0, 4.0, 0.6]
tip = [{tip}]
diameter_in = 1.66
friction = 0.4
''')


def test_a_coral_dropped_onto_a_branch_is_seated_and_rests_on_it(tmp_path):
    w = branch_world(tmp_path)
    b = w.test_range.balls
    placed = Placed(w)
    center = BRANCH_TIP + BRANCH_U * (CORAL_L / 2 - 6 * IN)            # let go 6 in onto it, along it
    b.place(0, tuple(center), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), BALL_BITS, quat=CORAL.orient(35.0, 180.0))
    step(w, 0.05, placed)
    assert placed.seated("branch")[0] / IN == pytest.approx(6.0, abs=0.3)
    assert not placed.rests_on(None, "branch")                         # not yet at rest for half a second
    step(w, 2.0, placed)
    depth, could = placed.seated("branch")
    assert could == pytest.approx(11 * IN)
    assert depth / IN == pytest.approx(10.2, abs=0.3)                  # it slid down until its rim met the post
    assert placed.seated("branch", 0) == (depth, could) and placed.seated("post") is None
    assert placed.rests_on(None, "branch") and placed.rests_on(0, "branch")
    assert not placed.rests_on(None, "post") and not placed.rests_on(None, ON_PIECE)
    assert placed.why_not(0, "post") == ""


def test_a_coral_lying_across_a_rod_rests_on_it_with_no_depth(tmp_path):
    """Review focus 1: lying across a beam, the coral is on no rod (empty, not 0) and still rests on the beam."""
    w = _world(tmp_path, '''
[range]
name = "a beam"

[piece.coral]
preset = "2025-coral"

[[floor_balls]]
at = [[2.0, 2.0]]

[[element]]
name = "beam"
kind = "rod"
base = [11.0, 4.0, 0.5]
tip = [13.0, 4.0, 0.5]
diameter_in = 1.66
''')
    b = w.test_range.balls
    placed = Placed(w)
    b.place(0, (12.0, 4.0, 0.5 + 0.83 * IN + 2.25 * IN + 0.01), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), BALL_BITS,
            quat=CORAL.orient(0.0, 90.0))
    step(w, 2.0, placed)
    assert placed.seated("beam") is None
    assert placed.rests_on(None, "beam")


TOTES = '''
[range]
name = "a shelf"

[piece.tote]
preset = "2015-tote"

[[floor_balls]]
at = [[2.0, 2.0], [2.0, 6.0]]

[[element]]
name = "shelf"
kind = "box"
center = [12.0, 4.0]
size = [1.0, 1.0, 0.3]
'''


def test_a_tote_rests_on_a_shelf_and_on_another_tote(tmp_path):
    w = _world(tmp_path, TOTES)
    b = w.test_range.balls
    placed = Placed(w)
    h = 12.1 * IN
    bottom, top = 0, 1
    b.place(bottom, (12.0, 4.0, 0.3 + h / 2 + 0.005), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), BALL_BITS)
    b.place(top, (12.0, 4.0, 0.3 + 1.5 * h + 0.012), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), BALL_BITS)
    step(w, 0.3, placed)
    assert not placed.rests_on(top, ON_PIECE)                          # settled, but not for REST_S yet
    assert placed.why_not(top, ON_PIECE).startswith("it has rested only 0.")
    step(w, REST_S, placed)
    assert placed.rests_on(top, ON_PIECE) and placed.rests_on(bottom, "shelf")
    assert not placed.rests_on(top, "shelf") and not placed.rests_on(bottom, ON_PIECE)
    assert placed.rests_on(None, "shelf") and placed.rests_on(None, ON_PIECE)
    assert placed.seated("shelf") is None                              # a solid piece has no depth


def test_without_rests_the_contacts_are_not_read(tmp_path):
    w = _world(tmp_path, TOTES)
    placed = Placed(w, rests=False)
    w.test_range.balls.place(0, (12.0, 4.0, 0.3 + 6.1 * IN), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), BALL_BITS)
    step(w, 1.0, placed)
    assert not placed.rests_on(0, "shelf") and placed._last == {}


# ---- in a run ---------------------------------------------------------------------------------------------------

PEG_RANGE = '''
[range]
name = "A peg"

[piece.coral]
preset = "2025-coral"

[[element]]
name = "pad"
kind = "box"
center = [6.0, 4.0]
size = [0.3, 0.3, 0.0127]

[[element]]
name = "peg"
kind = "rod"
base = [6.0, 4.0, 0.0127]
tip = [6.0, 4.0, 0.1143]
diameter_in = 1.0

[[station]]
name = "drop"
release = [6.0, 4.0, 0.36]
heading_deg = 0.0
tip_deg = 90

[start.facing_coral]
pose = [2.0, 4.0, 0.0]
'''
FEED = '''
[[block]]
do = "feed_one"
station = "drop"
'''


def expect(cond: str) -> str:
    return f'''
[[block]]
do = "expect"
that = {{ {cond} }}
'''


def _run(folder: Path, range_text: str, text: str):
    """The grabber stand-in (tests/pieces_helpers.py) on `range_text`, playing `text`: (program, result)."""
    path = grab_test(folder, text)
    (folder / "grab.toml").write_text(range_text, encoding="utf-8")
    program = load(path)
    return program, TestRun(world_for(program), program).run()


@pytest.fixture(scope="module")
def peg_run(tmp_path_factory):
    """A coral dropped standing on end over a 4 in peg on a half inch pad: it ends seated 4.0 in of 4.0 in."""
    folder = tmp_path_factory.mktemp("peg")
    text = (wait(0.5) + FEED + wait(2.0) + expect('seated = { on = "peg", at_least_in = 3 }')
            + expect('rests_on = { on = "pad" }') + expect('seated = { on = "peg", at_least_in = 5 }'))
    program, result = _run(folder, PEG_RANGE, text)
    return folder, program, result


def test_the_seated_signal_is_charted_and_empty_while_off(peg_run):
    folder, program, r = peg_run
    s = r.signals["seated.peg"]
    assert len(s) == len(r.signals["t"]) and math.isnan(s[0])         # in the run: NaN while the coral is parked
    assert s[-1] == pytest.approx(4.0, abs=0.05)                       # inches
    kept = store.save_run(r, program, folder / "runs")
    read = store.load_result(folder / "runs", kept.name)["signals"]["seated.peg"]
    assert read[0] is None and read[-1] == pytest.approx(s[-1])        # read back as JSON has it: null
    from scrimmage.mcp_server import _signal_summary
    summary = _signal_summary(r.signals)["seated.peg"]                # the empty samples left out
    assert 0.0 <= summary["min"] < 1.0 and summary["max"] == pytest.approx(4.0, abs=0.05)
    assert summary["final"] == pytest.approx(s[-1])
    assert _signal_summary({"t": [0.0, 1.0], "seated.x": [math.nan, math.nan]})["seated.x"] == {
        "min": None, "max": None, "final": None}


def test_seated_and_rests_on_checks_in_a_run(peg_run):
    _, _, r = peg_run
    lines = [c["line"] for c in r.checks]
    assert lines[0].startswith("passed: a coral is seated at least 3 in on peg")
    assert lines[1].startswith("passed: a coral rests on pad")
    assert r.status == "check failed"
    assert "the coral is seated 4 in of 4 in on peg, less than 5 in" in r.outcome
    assert r.seated == {"peg": "seated 4 in of 4 in"}                  # the depth at the end, for the run panel


def test_a_fed_coral_seated_on_the_peg_is_placed_not_missed(peg_run):
    """Controller ruling P33: a fed piece on a rod a seated check names is placed there; with no hopper the sentence
    leads with it."""
    _, _, r = peg_run
    (row,) = r.feeds
    assert (row["outcome"], row["why"]) == ("placed", "peg")
    assert r.counts == {"fed": 1, "in": 0, "stuck": 0, "missed": 0, "placed": 1}
    assert r.outcome.endswith("; 1 of 1 placed on peg"), r.outcome
    assert not made_it_in(r.counts, 1)                                 # made it in stays the hopper's


def test_with_a_hopper_too_the_placed_pieces_follow_the_ones_in(tmp_path):
    path = grab_test(tmp_path, wait(0.5) + FEED + wait(2.0) + expect('seated = { on = "peg" }'),
                     hopper=["grabber.jaw_b", "grabber.jaw_f"])
    (tmp_path / "grab.toml").write_text(PEG_RANGE, encoding="utf-8")
    r = TestRun(world_for(load(path)), load(path)).run()
    assert r.status == "finished", r.outcome
    assert r.outcome == "1 placed on peg"       # every piece placed: that is the success, so no "made it in" fraction


def test_a_piece_on_an_element_no_condition_names_is_missed(tmp_path):
    """Placed only on what the test names: the same drop with no seated or rests_on condition is missed, as before."""
    _, r = _run(tmp_path, PEG_RANGE, wait(0.5) + FEED + wait(2.0))
    assert r.feeds[0]["outcome"] == "missed" and "placed" not in r.counts
    assert r.outcome.startswith("0 of 1 made it in (0%); 1 missed"), r.outcome


def test_the_placed_sentences():
    def row(outcome, why=""):
        return {"outcome": outcome, "why": why}
    three = [row("placed", "branch_l2"), row("placed", "branch_l2"), row("placed", "branch_l3")]
    assert sentence(three, hopper=False) == "3 of 3 placed (2 on branch_l2, 1 on branch_l3)"
    mixed = [row("in"), row("in"), row("in"), row("placed", "peg"), row("placed", "peg")]
    assert sentence(mixed) == "3 of 5 made it in (60%); 2 placed on peg"
    assert sentence(mixed, hopper=False) == "2 of 5 placed on peg; 3 made it in"
    assert sentence([row("placed", "peg"), row("missed", "on the floor")], hopper=False) == "1 of 2 placed on peg; 1 missed"
    assert counts(mixed) == {"fed": 5, "in": 3, "stuck": 0, "missed": 0, "placed": 2}
    assert "placed" not in counts([row("in")])


def test_review_focus_1_a_coral_lying_across_isnt_on_the_rod_but_rests_on_it(tmp_path):
    beam = PEG_RANGE.replace('''base = [6.0, 4.0, 0.0127]
tip = [6.0, 4.0, 0.1143]''', '''base = [6.0, 3.7, 0.5]
tip = [6.0, 4.3, 0.5]''').replace("tip_deg = 90", "tip_deg = 0").replace("0.36]", "0.68]")    # across, 0.5 m up
    text = (FEED + wait(1.5) + expect('rests_on = { on = "peg" }')
            + expect('seated = { on = "peg" }'))
    _, r = _run(tmp_path, beam, text)
    assert all(math.isnan(v) for v in r.signals["seated.peg"])
    assert r.checks[0]["passed"], r.outcome
    assert r.checks[1]["failure"] == "the coral isn't on peg"


def test_a_robot_touching_the_piece_means_it_doesnt_rest(tmp_path):
    x = 2.0 + 0.43 + CORAL_X
    shelf = f'''
[range]
name = "A shelf"

[piece.coral]
preset = "2025-coral"

[[element]]
name = "shelf"
kind = "box"
center = [{x}, 4.0]
size = [0.1, 0.4, 0.01]

[[station]]
name = "drop"
release = [{x}, 4.0, 0.2]
heading_deg = 0.0
turn_deg = 90

[start.facing_coral]
pose = [2.0, 4.0, 0.0]
'''
    on_shelf = expect('rests_on = { on = "shelf" }')
    _, r = _run(tmp_path / "open", shelf, FEED + wait(1.5) + on_shelf)
    assert r.checks[0]["passed"], r.outcome                           # between the open jaws
    _, r = _run(tmp_path / "closed", shelf, FEED + wait(1.0) + squeeze(4.0) + wait(1.0) + on_shelf)
    assert r.checks[0]["failure"] == "the coral doesn't rest on shelf (a robot body still touches it)"


def test_the_knocked_off_and_not_on_sentences():
    """TestRun._not_placed with a stand-in watch: each reading's sentence."""
    readings = {"none": None, "low": (0.8 * IN, 4 * IN), "short": (2.0 * IN, 4 * IN)}
    for name, got in readings.items():
        watch = SimpleNamespace(piece_index=lambda p: None, kinds={"coral": CORAL}, piece_names={},
                                placed=SimpleNamespace(seated=lambda rod, i, got=got: got,
                                                       why_not=lambda i, on: ""))
        run = SimpleNamespace(watch=watch)
        said = TestRun._not_placed(run, "seated", {"piece": "any", "on": "branch_l2", "at_least_in": 3.0})
        assert said == {"none": "the coral isn't on branch_l2",
                        "low": "the coral is seated only 0.8 in on branch_l2 (could be knocked off)",
                        "short": "the coral is seated 2 in of 4 in on branch_l2, less than 3 in"}[name]
    named = SimpleNamespace(piece_index=lambda p: 0, kinds={"coral": CORAL}, piece_names={"ball.0": "coral 1"},
                            placed=SimpleNamespace(why_not=lambda i, on: ""))
    assert TestRun._not_placed(SimpleNamespace(watch=named), "rests_on",
                           {"piece": "ball.0", "on": "shelf"}) == "coral 1 doesn't rest on shelf"


# ---- the file, the words, the palette ---------------------------------------------------------------------------

def test_seated_and_rests_on_round_trip_and_describe(tmp_path):
    text = (expect('rests_on = { on = "shelf" }') + expect('rests_on = { piece = "ball.0", on = "a piece" }')
            + expect('seated = { on = "branch_l2", at_least_in = 3 }') + expect('seated = { on = "branch_l2" }'))
    path = grab_test(tmp_path, text)
    p = load(path)
    conds = [b.args["that"] for b in p.blocks]
    assert conds == [Cond("rests_on", {"piece": "any", "on": "shelf"}),
                     Cond("rests_on", {"piece": "ball.0", "on": "a piece"}),
                     Cond("seated", {"piece": "any", "on": "branch_l2", "at_least_in": 3.0}),
                     Cond("seated", {"piece": "any", "on": "branch_l2", "at_least_in": 0.0})]
    assert loads(dumps(p), path).blocks == p.blocks
    kinds = {"coral": CORAL}
    assert describe(conds[0], kinds=kinds) == "a coral rests on shelf"
    assert describe(conds[1], kinds=kinds, names={"ball.0": "coral 1"}) == "coral 1 rests on a piece"
    assert describe(conds[2], kinds=kinds) == "a coral is seated at least 3 in on branch_l2"
    assert describe(conds[3], kinds=kinds) == "a coral is seated on branch_l2"
    assert _recast("a coral rests on shelf", 1) == "a coral to rest on shelf"
    assert _recast("a coral rests on shelf", 0) == "a coral resting on shelf"
    for bad, match in (('seated = { on = "b", at_least_in = -1 }', "can't be negative"),
                       ('seated = { on = "b", at_least_in = "3" }', "must be a number"),
                       ('rests_on = { by = "shelf" }', "by"), ('rests_on = { piece = "any" }', "on")):
        with pytest.raises(TestFileError, match=match):
            loads("[test]\nrobot = \"r.toml\"\n" + expect(bad), path)


def test_the_palette_lists_the_seated_signals_and_the_problems_name_a_missing_rod(tmp_path):
    text = (expect('seated = { on = "branch_x" }') + expect('rests_on = { on = "shelf_x" }')
            + expect('rests_on = { on = "a piece" }') + expect('seated = { on = "peg" }')
            + expect('rests_on = { on = "peg" }'))
    path = grab_test(tmp_path, text)
    (tmp_path / "grab.toml").write_text(PEG_RANGE, encoding="utf-8")
    p = load(path)
    w = world_for(p)
    pal = palette(w, [])
    assert pal["rods"] == ["peg"] and pal["signals"][-1] == "seated.peg"
    assert [(x["place"], x["message"]) for x in problems(p, w)] == [
        ([0], 'no rod "branch_x" on this field'), ([1], 'no element "shelf_x" on this field')]


def test_seated_on_a_field_without_hollow_pieces(tmp_path):
    no_hole = PEG_RANGE.replace('preset = "2025-coral"', 'preset = "2015-tote"').replace("0.36]", "0.8]")
    path = grab_test(tmp_path, expect('seated = { on = "peg" }'))
    (tmp_path / "grab.toml").write_text(no_hole, encoding="utf-8")
    p = load(path)
    w = world_for(p)
    assert "seated.peg" not in palette(w, [])["signals"]
    assert [x["message"] for x in problems(p, w)] == ["this field's pieces have no hole to go on a rod"]
