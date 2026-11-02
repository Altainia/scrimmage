"""Feeding in a block test's run, and one outcome for every fed piece (scrimmage/blocks/feeds.py, run.py)."""

from __future__ import annotations

import threading

import pytest

from scrimmage.assembler.templates import new_config
from scrimmage.blocks import run as blocks_run
from scrimmage.blocks.feeds import is_standin, sentence
from scrimmage.blocks.program import loads
from scrimmage.blocks.run import TestRun, world_for
from scrimmage.climb.run import SETTLE_S

# A bin on the stand-in's top, written by hand (box sizes are full sizes): a floor and four walls 0.3 m high over
# the whole top. The robot faces
# the station (heading 180), so a fed ball comes in over the front wall and lands near the back.
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
          standin: bool = True, robot: str = "bot.toml", head: str = "", setup: str = ""):
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
    path.write_text(f'[test]\nrobot = "{robot}"\nhopper = ["hopper.bin"]\n{head}\n{blocks}{setup}', encoding="utf-8")
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
    assert out[1] == f"  stuck in the robot: a piece from feeder (fed at {SETTLE_S:.1f} s) rests on shelf.plate"


def test_a_piece_on_the_standins_own_body_is_not_stuck(tmp_path):
    field = station_file(tmp_path, (1.523, 4.0, 1.0), extra="stock = 1\n")
    r = run(write(tmp_path, FEED_ONE, field, HOPPER + SHELF_STOCK))
    assert r.feeds[0]["outcome"] == "missed" and r.feeds[0]["why"] == "", r.feeds
    assert r.outcome == "0 of 1 made it in (0%); 1 missed"


def test_on_another_robot_every_body_counts(tmp_path):
    field = station_file(tmp_path, (1.523, 4.0, 1.0), extra="stock = 1\n")
    r = run(write(tmp_path, FEED_ONE, field, HOPPER + SHELF_STOCK, standin=False))
    assert r.feeds[0]["outcome"] == "stuck" and r.feeds[0]["body"] == "shelf.chassis", r.feeds


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
