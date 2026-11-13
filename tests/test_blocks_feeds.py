"""Feeding in a block test's run, and one outcome for every fed piece (scrimmage/blocks/feeds.py, run.py)."""

from __future__ import annotations

import threading

import pytest
from pieces_helpers import run_grab

from scrimmage.assembler.templates import new_config
from scrimmage.blocks import run as blocks_run
from scrimmage.blocks.conditions import Watch
from scrimmage.blocks.feeds import is_standin, sentence
from scrimmage.blocks.program import loads
from scrimmage.blocks.run import TestRun, world_for
from scrimmage.climb.run import SETTLE_S

# A bin on the stand-in's top, written by hand (box sizes are full sizes): a floor and four walls 0.3 m high over
# the whole top. The robot faces the station (heading 180), so a fed ball comes in over the front wall and lands
# near the back.
BIN = """\
[part]
name = "hopper"
format = 1

[[body]]
name = "bin"
mass = 1.0
geom = [
  { box = [0.64, 0.60, 0.01], pos = [-0.02, 0.0, 0.005] },      # floor
  { box = [0.01, 0.60, 0.30], pos = [0.30, 0.0, 0.15] },        # front wall
  { box = [0.64, 0.01, 0.30], pos = [-0.02, 0.30, 0.15] },      # left wall
  { box = [0.64, 0.01, 0.30], pos = [-0.02, -0.30, 0.15] },     # right wall
%(back)s]

[[mount]]
name = "base"
body = "bin"
"""
BACK_WALL = "  { box = [0.01, 0.60, 0.30], pos = [-0.34, 0.0, 0.15] },       # back wall\n"
# A shelf the student added, on top of the bin's back wall: a tray with a 6 cm lip, where a ball dropped onto it
# lodges.
SHELF_PART = """\
[part]
name = "shelf"
format = 1

[[body]]
name = "plate"
mass = 0.2
geom = [
  { box = [0.20, 0.30, 0.02] },
  { box = [0.01, 0.30, 0.06], pos = [0.095, 0.0, 0.03] },
  { box = [0.01, 0.30, 0.06], pos = [-0.095, 0.0, 0.03] },
  { box = [0.20, 0.01, 0.06], pos = [0.0, 0.145, 0.03] },
  { box = [0.20, 0.01, 0.06], pos = [0.0, -0.145, 0.03] },
]

[[mount]]
name = "base"
body = "plate"
"""
HOPPER = '[[part]]\nname = "hopper"\npath = "hopper"\nattach = "drive.top"\n'
SHELF = '[[part]]\nname = "shelf"\npath = "shelf"\nattach = "drive.top"\npos = [-0.34, 0.0, 0.33]\n'
# The same shelf as a stock chassis box, one of the stand-in's own kind of part: its frame low (its top 0.08 m up)
# and its bumpers (0.02 to 0.14 m up) make the tray's lip.
SHELF_STOCK = ('[[part]]\nname = "shelf"\nstock = "chassis"\nlength = 0.2\nwidth = 0.3\nmass = 0.2\nbumper = 0.01\n'
               'frame_height = 0.06\nattach = "drive.top"\npos = [-0.34, 0.0, 0.30]\n')

FEED_UNTIL_5 = """
[[block]]
do = "start_feeding"
station = "feeder"

[[block]]
do = "wait_until"
until = { in_hopper = { at_least = 5 } }
timeout = 20.0

[[block]]
do = "stop_feeding"
station = "feeder"

[[block]]
do = "expect"
that = { made_it_in = { at_least = 50.0 } }
"""
FEED_ONE = '\n[[block]]\ndo = "feed_one"\nstation = "feeder"\n'
FEED_AND_WAIT = '\n[[block]]\ndo = "start_feeding"\nstation = "feeder"\n\n[[block]]\ndo = "wait"\nseconds = 5.0\n'


def station_file(folder, release, speed=0.0, extra="") -> str:
    """A range of our own with one station "feeder" at `release`, the stand-in at the station field's start."""
    path = folder / "field.toml"
    path.write_text(f'[range]\nname = "a test field"\nball = "2026-fuel"\n\n[[station]]\nname = "feeder"\n'
                    f'release = {list(release)}\nheading_deg = 0.0\nspeed = {speed}\n{extra}\n'
                    '[start.at_station]\npose = [1.183, 4.0, 180.0]\n', encoding="utf-8")
    return str(path)


def write(folder, blocks: str, field: str = "station", parts: str = HOPPER, back: bool = True,
          standin: bool = True, robot: str = "bot.toml", head: str = "", setup: str = "",
          hopper: str = 'hopper = ["hopper.bin"]\n'):
    """The stand-in (or, without `standin`, the same robot not marked as one) with the bin and `parts`, on `field`,
    and a test of it; the test's path."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "hopper").mkdir(exist_ok=True)
    (folder / "hopper" / "part.toml").write_text(BIN % {"back": BACK_WALL if back else ""}, encoding="utf-8")
    (folder / "shelf").mkdir(exist_ok=True)
    (folder / "shelf" / "part.toml").write_text(SHELF_PART, encoding="utf-8")
    text = new_config("standin")
    assert "standin = true" in text
    if not standin:
        text = text.replace("standin = true", "")
    text += "\n" + parts + f'\n[range]\nfile = {field!r}\nstart = "at_station"\n'.replace("'", '"')
    (folder / robot).write_text(text, encoding="utf-8")
    path = folder / "t.test.toml"
    path.write_text(f'[test]\nrobot = "{robot}"\n{hopper}{head}\n{blocks}{setup}', encoding="utf-8")
    return path


def run(path, **kw):
    program = loads(path.read_text(encoding="utf-8"), path)
    return TestRun(world_for(program), program).run(**kw)


@pytest.fixture(scope="module")
def fed_run(tmp_path_factory):
    return run(write(tmp_path_factory.mktemp("fed"), FEED_UNTIL_5))


def test_every_fed_piece_has_one_outcome_and_they_add_up(fed_run):
    c = fed_run.counts
    assert fed_run.status == "finished", fed_run.outcome
    assert c["in"] + c["stuck"] + c["missed"] == c["fed"] == len(fed_run.feeds) >= 5
    assert c["in"] >= 5
    assert fed_run.outcome.startswith(f"{c['in']} of {c['fed']} made it in ("), fed_run.outcome
    assert fed_run.seed == 1
    assert all(f["source"] == "feeder" and f["outcome"] in ("in", "stuck", "missed") for f in fed_run.feeds)


def test_a_piece_on_the_students_shelf_is_stuck_and_named(tmp_path):
    field = station_file(tmp_path, (1.523, 4.0, 1.0), extra="stock = 1\n")
    r = run(write(tmp_path, FEED_ONE, field, HOPPER + SHELF))
    assert r.feeds[0]["outcome"] == "stuck" and r.feeds[0]["body"].startswith("shelf."), r.feeds
    assert "1 stuck in the robot" in r.outcome and r.outcome == "0 of 1 made it in (0%); 1 stuck in the robot"
    assert r.feeds[0]["t_outcome"] > r.feeds[0]["t"]


def test_test_run_prints_each_stuck_piece(tmp_path, capsys, monkeypatch):
    from test_blocks_run import _cli
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    field = station_file(tmp_path, (1.523, 4.0, 1.0), extra="stock = 1\n")
    assert _cli(["test", "run", str(write(tmp_path, FEED_ONE, field, HOPPER + SHELF))]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "0 of 1 made it in (0%); 1 stuck in the robot"
    assert out[1] == f"  stuck in the robot: a piece from feeder (fed at {SETTLE_S:.1f} s) resting on shelf.plate"


def test_a_station_test_doesnt_count_the_floor_piece_ahead(tmp_path):
    """A test that feeds from a station counts the pieces the station fed, not a floor piece that lies ahead of the
    start untouched (in a station test the floor piece counts only once the robot touches it)."""
    field = station_file(tmp_path, (1.183, 4.0, 1.0), extra="stock = 1\n")     # choice: over the bin's middle
    with open(field, "a", encoding="utf-8") as f:
        f.write("\n[[floor_balls]]\nat = [[0.3, 4.3]]\n")   # choice: 0.9 m ahead of the start, 19 deg off its heading
    r = run(write(tmp_path, FEED_ONE + '\n[[block]]\ndo = "expect"\nthat = { made_it_in = { at_least = 100.0 } }\n',
                  field))
    assert r.counts == {"fed": 1, "in": 1, "stuck": 0, "missed": 0}, r.outcome
    assert r.passed and r.outcome == "1 of 1 made it in (100%)"
    assert [f["source"] for f in r.feeds] == ["feeder"]



def test_a_station_test_counts_the_floor_piece_the_robot_touches(tmp_path):
    """The same station test, with the robot driving into the floor piece ahead: once the robot has touched it, it
    counts too, so a test that intakes from the floor and feeds from a station counts both."""
    field = station_file(tmp_path, (1.183, 4.0, 1.0), extra="stock = 1\n")     # choice: over the bin's middle
    with open(field, "a", encoding="utf-8") as f:
        f.write("\n[[floor_balls]]\nat = [[0.15, 4.0]]\n")   # choice: 0.7 m straight ahead of the start
    r = run(write(tmp_path, FEED_ONE + '\n[[block]]\ndo = "drive"\ndistance = 0.6\n', field))
    assert sorted(f["source"] for f in r.feeds) == ["feeder", "floor"], r.feeds
    assert r.counts["fed"] == 2, r.outcome

def test_a_piece_on_the_standins_own_body_is_not_stuck(tmp_path):
    field = station_file(tmp_path, (1.523, 4.0, 1.0), extra="stock = 1\n")
    r = run(write(tmp_path, FEED_ONE, field, HOPPER + SHELF_STOCK))
    assert r.feeds[0]["outcome"] == "missed" and r.feeds[0]["why"] == "", r.feeds
    assert r.outcome == "0 of 1 made it in (0%); 1 missed"


def test_on_another_robot_every_body_counts(tmp_path):
    field = station_file(tmp_path, (1.523, 4.0, 1.0), extra="stock = 1\n")
    r = run(write(tmp_path, FEED_ONE, field, HOPPER + SHELF_STOCK, standin=False))
    assert r.feeds[0]["outcome"] == "stuck" and r.feeds[0]["body"] == "shelf.chassis", r.feeds


@pytest.mark.parametrize("standin", [True, False])
def test_a_piece_on_the_standins_own_frame_is_not_stuck(tmp_path, standin):
    # no bin: the ball drops onto the stand-in's own chassis frame (drive.chassis) and rests there
    field = station_file(tmp_path, (1.183, 4.0, 0.6), extra="stock = 1\n")
    r = run(write(tmp_path, FEED_ONE, field, parts="", standin=standin, hopper=""))
    f = r.feeds[0]
    if standin:
        assert f["outcome"] == "missed" and f["why"] == "", r.feeds       # at rest, on the robot, not on the floor
    else:
        assert f["outcome"] == "stuck" and f["body"] == "drive.chassis", r.feeds


def test_standin_bodies_are_the_chassis_and_drive(tmp_path):
    from scrimmage.blocks.feeds import standin_bodies
    program = loads(write(tmp_path, FEED_ONE, parts=HOPPER + SHELF).read_text(encoding="utf-8"),
                    tmp_path / "t.test.toml")
    w = world_for(program)
    m = w.model
    names = {m.body(b).name for b in standin_bodies(w)}
    robot = {m.body(b).name for b in range(m.nbody) if m.body_rootid[b] == m.body_rootid[w.robot_body]}
    assert names == {n for n in robot if n.startswith(("drive.", "dt."))} and "drive.chassis" in names
    assert "hopper.bin" not in names and "shelf.plate" not in names


# A catch tray the student added behind a bin with no back wall, hanging past the back bumper with its floor 8 cm below
# the bin's: a ball that bounces out of the bin drops into it and can't roll back up.
CATCH_PART = """\
[part]
name = "catch"
format = 1

[[body]]
name = "tray"
mass = 0.3
geom = [
  { box = [0.40, 0.60, 0.01], pos = [-0.65, 0.0, -0.075] },
  { box = [0.01, 0.60, 0.38], pos = [-0.85, 0.0, 0.11] },
  { box = [0.40, 0.01, 0.38], pos = [-0.65, 0.30, 0.11] },
  { box = [0.40, 0.01, 0.38], pos = [-0.65, -0.30, 0.11] },
]

[[mount]]
name = "base"
body = "tray"
"""


def test_a_bounce_out_that_comes_to_rest_on_the_students_part_is_stuck(tmp_path):
    # controller ruling R5: stuck wins over bounced out
    (tmp_path / "catch").mkdir()
    (tmp_path / "catch" / "part.toml").write_text(CATCH_PART, encoding="utf-8")
    catch = '[[part]]\nname = "catch"\npath = "catch"\nattach = "drive.top"\n'
    field = station_file(tmp_path, (1.2, 4.0, 0.3), speed=3.0, extra="stock = 1\n")
    blocks = FEED_ONE + '\n[[block]]\ndo = "wait"\nseconds = 3.0\n'        # it rolls a while in the tray
    r = run(write(tmp_path, blocks, field, HOPPER + catch, back=False))
    assert r.feeds[0]["outcome"] == "stuck" and r.feeds[0]["body"] == "catch.tray", r.feeds


def test_a_bounce_out_is_missed_bounced_out(tmp_path):
    field = station_file(tmp_path, (1.2, 4.0, 0.5), speed=3.0, extra="stock = 1\n")    # inside the bin, toward +x
    r = run(write(tmp_path, FEED_ONE, field, back=False))
    assert r.feeds[0]["outcome"] == "missed" and r.feeds[0]["why"] == "bounced out", r.feeds
    assert r.outcome == "0 of 1 made it in (0%); 1 missed (1 bounced out)"


def test_the_last_made_it_in_check_waits_for_the_settle(tmp_path):
    expect = '\n[[block]]\ndo = "expect"\nthat = { made_it_in = { at_least = 100.0 } }\n'
    r = run(write(tmp_path, FEED_ONE + expect))
    assert r.status == "finished" and r.settle_s > 0 and r.checks[-1]["passed"], r.outcome
    assert r.counts == {"fed": 1, "in": 1, "stuck": 0, "missed": 0} and r.outcome == "1 of 1 made it in (100%)"
    assert r.checks[-1]["t"] == pytest.approx(r.t_end)


def test_a_made_it_in_check_in_the_middle_uses_the_counts_so_far(tmp_path):
    expect = '\n[[block]]\ndo = "expect"\nthat = { made_it_in = { at_least = 100.0 } }\n'
    r = run(write(tmp_path, FEED_ONE + expect + '\n[[block]]\ndo = "wait"\nseconds = 1.0\n'))
    assert r.status == "check failed", r.outcome          # the piece was still in the air
    assert r.outcome.startswith("a check failed (block 2): expected at least 100% to make it in, but 0 of 1 did")
    assert r.outcome.endswith("; 0 of 1 made it in (0%); 1 missed (1 still moving when the run ended)"), r.outcome


def test_a_failed_last_check_ends_the_run_after_the_settle(tmp_path):
    expect = '\n[[block]]\ndo = "expect"\nthat = { made_it_in = { at_least = 100.0 } }\n'
    field = station_file(tmp_path, (3.0, 2.0, 0.5), extra="stock = 1\n")     # onto the carpet, nowhere near
    r = run(write(tmp_path, FEED_ONE + expect, field))
    assert r.status == "check failed" and not r.checks[-1]["passed"] and r.settle_s > 0
    assert r.feeds[0]["why"] == "on the floor"
    assert r.outcome == ("a check failed (block 2): expected at least 100% to make it in, but 0 of 1 did "
                         f"at {r.t_end:.1f} s; 0 of 1 made it in (0%); 1 missed")


def test_review_focus_2_a_blocked_release_point_times_out_feed_one(tmp_path):
    field = station_file(tmp_path, (3.0, 2.0, 0.076))         # set down on the carpet, where it stays
    blocks = FEED_ONE + '\n[[block]]\ndo = "wait"\nseconds = 0.5\n' + FEED_ONE + "timeout = 2.0\n"
    r = run(write(tmp_path, blocks, field))
    assert r.status == "timed out" and "its release point stayed blocked" in r.outcome
    assert r.outcome.startswith(f"feed one from feeder timed out at {SETTLE_S + 2.5:.1f} s: its release point stayed "
                                "blocked (block 3)"), r.outcome
    assert r.counts["fed"] == len(r.feeds) == 1


def test_feed_one_on_an_empty_station_does_nothing(tmp_path):
    r = run(write(tmp_path, FEED_ONE + FEED_ONE, setup="\n[setup.station.feeder]\nstock = 1\n"))
    assert r.status == "finished" and r.counts["fed"] == 1, r.outcome


@pytest.mark.parametrize("ending", ["time limit", "tipped over", "cancelled"])
def test_review_focus_3_an_early_end_still_accounts_for_every_piece(tmp_path, monkeypatch, ending):
    kw = {}
    head = ""
    if ending == "time limit":
        head = f"time_limit = {SETTLE_S + 0.15}"
    elif ending == "tipped over":
        monkeypatch.setattr(blocks_run, "TIP_DEG", -1.0)     # any tilt is too much: it tips at the first step
    else:
        cancel = threading.Event()
        kw = {"cancel": cancel, "on_sample": lambda t, row: cancel.set() if t > SETTLE_S + 0.1 else None}
    r = run(write(tmp_path, FEED_AND_WAIT, head=head), **kw)
    assert r.status == ending and r.settle_s == 0
    assert r.counts["fed"] >= 1
    assert sum(r.counts[k] for k in ("in", "stuck", "missed")) == r.counts["fed"] == len(r.feeds)
    assert any(f["why"] == "still moving when the run ended" for f in r.feeds)
    assert r.outcome.endswith("still moving when the run ended)") and "; 0 of " in r.outcome, r.outcome


def test_a_run_without_stations_is_unchanged(tmp_path):
    from test_blocks_run import MOVE_45, write_test
    path = write_test(tmp_path, MOVE_45)
    r = run(path)
    assert r.outcome == "finished" and r.feeds == [] and r.counts is None and r.seed is None and r.settle_s == 0


def test_is_standin(tmp_path):
    (tmp_path / "a.toml").write_text(new_config("standin"), encoding="utf-8")
    (tmp_path / "b.toml").write_text(new_config("differential"), encoding="utf-8")
    (tmp_path / "c-standin.toml").write_text(new_config("differential"), encoding="utf-8")
    assert is_standin(tmp_path / "a.toml") and not is_standin(tmp_path / "b.toml")
    assert is_standin(tmp_path / "c-standin.toml") and not is_standin(tmp_path / "missing.toml")


def test_a_timed_out_made_it_in_wait_reads_naturally(tmp_path):
    wait = '\n[[block]]\ndo = "wait_until"\nuntil = { made_it_in = { at_least = 80.0 } }\ntimeout = 0.5\n'
    r = run(write(tmp_path, wait))
    assert r.outcome.startswith("waiting for at least 80% to have made it in timed out at "), r.outcome


def test_a_run_whose_pieces_were_all_placed_says_where():
    on = [{"outcome": "placed", "why": "branch_l2"}]
    assert sentence(on) == "1 placed on branch_l2" and sentence(on * 2) == "2 placed on branch_l2"
    # a mixed run keeps the fraction; with no hopper the older wording stays
    assert sentence(on + rows(1, 0, 0, 0)) == "1 of 2 made it in (50%); 1 placed on branch_l2"
    assert sentence(on, hopper=False) == "1 of 1 placed on branch_l2"


def rows(n_in, stuck, bounced, moving, floor=0):
    out = [{"outcome": "in", "why": ""}] * n_in + [{"outcome": "stuck", "why": ""}] * stuck
    out += [{"outcome": "missed", "why": "bounced out"}] * bounced
    out += [{"outcome": "missed", "why": "still moving when the run ended"}] * moving
    return out + [{"outcome": "missed", "why": "on the floor"}] * floor


def test_the_sentence():
    assert sentence(rows(17, 1, 1, 1)) == ("17 of 20 made it in (85%); 1 stuck in the robot; "
                                           "2 missed (1 bounced out, 1 still moving when the run ended)")
    assert sentence(rows(2, 0, 0, 0, floor=1)) == "2 of 3 made it in (67%); 1 missed"
    assert sentence(rows(1, 0, 0, 0, floor=1)) == "1 of 2 made it in (50%); 1 missed"
    assert sentence(rows(5, 0, 0, 0)) == "5 of 5 made it in (100%)" and sentence([]) == ""


# ---- the tracker on a tiny world: a box robot and one ball, placed by hand (no physics steps) ------------------------

TINY = """
<mujoco>
  <worldbody>
    <geom name="carpet" type="plane" size="5 5 0.1"/>
    <body name="robot" pos="0 0 0.1"><freejoint/><geom name="top" type="box" size="0.2 0.2 0.1"/></body>
    <body name="ball" pos="0 0 0.249"><freejoint/><geom name="ball" type="sphere" size="0.05"/></body>
  </worldbody>
</mujoco>
"""


def _tiny():
    """An Outcomes tracking the one ball; a function that puts the ball at (x, z), and its velocity (set it)."""
    import mujoco
    import numpy as np
    from types import SimpleNamespace
    from scrimmage.blocks.feeds import Outcomes
    from scrimmage.testrange.balls import BallState
    m = mujoco.MjModel.from_xml_string(TINY)
    d = mujoco.MjData(m)
    q = int(m.jnt_qposadr[m.body("ball").jntadr[0]])
    of_geom = np.full(m.ngeom, -1)
    of_geom[m.geom("ball").id] = 0
    v = np.zeros(3)
    balls = SimpleNamespace(state=np.array([BallState.FIELD], dtype=object), rest_z=0.05,
                            ball_of_geom=lambda g: of_geom[g], position=lambda i: d.qpos[q:q + 3],
                            velocity=lambda i: v, clearance=lambda i: float(d.qpos[q + 2]) - 0.05)
    world = SimpleNamespace(model=m, data=d, robot_body=m.body("robot").id, test_range=SimpleNamespace(balls=balls))
    out = Outcomes(world, SimpleNamespace(inside=np.zeros(1, bool), hopper=[], round=np.ones(1, bool)), standin=False)
    out.track(0, "feeder", 0.0)

    def put(x, z):
        d.qpos[q:q + 3] = (x, 0.0, z)
        mujoco.mj_forward(m, d)
    return out, put, v


def test_a_piece_at_rest_that_slides_off_the_robot_onto_the_floor_isnt_stuck():
    out, put, v = _tiny()
    dt = 0.002
    put(0.0, 0.249)                         # on the robot's top
    v[0] = 1.0                              # rolling ...
    out.update(0.0)
    out.update(0.002)
    v[0] = 0.0                              # ... and at rest from some time in the step that ends at 0.004
    t = 0.002
    for _ in range(400):
        out.update(t := t + dt)
    row = out.final()[0]
    assert row["outcome"] == "stuck" and row["t_outcome"] == pytest.approx(0.002)    # that step's start
    put(0.0, 0.26)                          # its contact drops out for one step: still on the robot
    out.update(t := t + dt)
    put(0.0, 0.249)
    out.update(t := t + dt)
    assert out.final()[0]["outcome"] == "stuck"
    put(1.0, 0.05)                          # slid off, slowly enough to stay at rest, onto the carpet
    for _ in range(3):
        out.update(t := t + dt)
    row = out.final()[0]
    assert row["outcome"] == "missed" and row["why"] == "on the floor", row


# ---- a held piece made it in (tests/pieces_helpers.py's grabber, its coral tracked as a floor piece) ----------------

@pytest.fixture(scope="module")
def held_grab(tmp_path_factory):
    """The grabber swinging the coral up, its jaws where a piece should end up: (program, world, result)."""
    return run_grab(tmp_path_factory.mktemp("held"), hopper=["grabber.jaw_b", "grabber.jaw_f"], floor=True)


def test_a_held_floor_piece_counts_as_picked_up(held_grab):
    """Its center is between the jaws, outside both jaws' boxes: it is in because a jaw holds it, from when the hold
    began (after the jaws closed, before the run ended)."""
    program, world, result = held_grab
    assert result.status == "finished" and result.checks[-1]["passed"], result.outcome
    (row,) = result.feeds
    assert row["source"] == "floor" and row["outcome"] == "in" and row["why"] == ""
    assert not Watch(world, program).hopper_mask()[row["ball"]]     # not in a box: held
    assert 1.0 < row["t_outcome"] < result.t_end


def test_a_held_piece_by_a_body_not_in_the_list_is_stuck_not_in(tmp_path):
    """Held by the jaws, but only the grabber's base is where a piece should end up: it didn't make it in. At rest on
    the student's part, it is stuck in the robot, as before."""
    _, _, result = run_grab(tmp_path, hopper=["grabber.base"], floor=True)
    (row,) = result.feeds
    assert row["outcome"] == "stuck" and row["body"] in ("grabber.jaw_b", "grabber.jaw_f")   # held, rules unchanged
