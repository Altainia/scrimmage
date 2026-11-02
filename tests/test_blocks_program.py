"""The blocks test file format (scrimmage/blocks/program.py)."""

from pathlib import Path

import pytest

from scrimmage.blocks import program as P

HEAD = '[test]\nrobot = "r.toml"\n'
PATH = Path("/tmp/x.test.toml")

SAMPLE = HEAD + '''hooks = ["climber.hook_A"]
hopper = ["feeder.hopper"]
time_limit = 30.0
chart = ["tilt", "climber.crank"]

[setup]
ball = "2025-algae"
seed = 7
station = { feeder = { interval = 0.5, stock = 6, spread_yaw_deg = 3.0, spread_position = [0.0, 0.02, 0.0] } }
floor_spread = { offset_in = [-6.0, 0.0, 6.0], angle_deg = [0.0, 10.0] }

[[block]]
do = "spin"
motor = "climber.crank"
volts = 6.0

[[block]]
do = "move"
joint = "arm.shoulder"
to = 45.0
unit = "deg"
timeout = 3.0

[[block]]
do = "drive"
distance = -0.5
volts = 3.0

[[block]]
do = "drive"
seconds = 1.5

[[block]]
do = "stop"
motor = "all"

[[block]]
do = "wait"
seconds = 0.5

[[block]]
do = "wait_until"
until = { joint = { name = "arm.shoulder", above = 40.0, unit = "deg" } }
timeout = 4.0

[[block]]
do = "repeat"
until = { touch = { robot = "any hook", field = "next bar" } }
max = 5

[[block.body]]
do = "repeat"
times = 2

[[block.body.body]]
do = "expect"
that = { tilt = { below = 30.0 } }
throughout = true

[[block.body]]
do = "expect"
that = { piece_in = { body = "tray", piece = "any" } }

[[block]]
do = "expect"
that = { hanging_from = "next bar" }

[[block]]
do = "expect"
that = { level = "mid" }

[[block]]
do = "start_feeding"
station = "feeder"

[[block]]
do = "feed_one"
station = "feeder"
timeout = 4.0

[[block]]
do = "drive"
distance = 1.0
direction = "left"

[[block]]
do = "turn"
degrees = -90.0
volts = 3.0

[[block]]
do = "wait_until"
until = { in_hopper = { at_least = 3 } }

[[block]]
do = "wait_until"
until = { station_empty = "feeder" }

[[block]]
do = "expect"
that = { made_it_in = { at_least = 80.0 } }

[[block]]
do = "stop_feeding"
station = "feeder"
'''


def test_roundtrips(tmp_path):
    p = P.loads(SAMPLE, PATH)
    assert {b.do for b in p.blocks} == set(P.DO_KINDS)
    text = P.dumps(p)
    assert text.startswith("[test]\nrobot = ")
    assert P.loads(text, PATH) == p
    assert P.from_json(P.to_json(p), PATH) == p
    f = tmp_path / "a.test.toml"
    f.write_text(text, encoding="utf-8")
    assert P.load(f).blocks == p.blocks




def test_nested_bodies_are_written_as_tables():
    text = P.dumps(P.loads(SAMPLE, PATH))
    assert "[[block.body]]" in text and "[[block.body.body]]" in text


def test_defaults():
    p = P.loads(HEAD, PATH)
    assert (p.time_limit, p.hooks, p.chart, p.blocks) == (P.TIME_LIMIT, [], None, [])
    assert p.robot == Path("r.toml")


def err(text, match):
    with pytest.raises(P.TestFileError, match=match):
        P.loads(text, PATH)


def blk(body):
    return HEAD + "\n[[block]]\n" + body


def test_bad_inputs_name_the_block():
    ok = '[[block]]\ndo = "wait"\nseconds = 1\n\n'
    err(blk('do = "wait"\nseconds = 1\n') + "\n" + ok + '[[block]]\ndo = "fly"\n', "block 3: \"fly\" isn't a block")
    err(blk('do = "wait"\nseconds = -1\n'), r"block 1: seconds can't be negative")
    err(blk('do = "wait_until"\nuntil = {tilt = {above = 10}, joint = {name = "a.b", above = 1, unit = "deg"}}\n'),
        "one condition per block")
    err("[test]\n", "says which robot to test")
    err("", "says which robot to test")
    err(blk('do = "spin"\nmotor = "a.b"\nvolts = 13\n'), "between -12 and 12")
    err(blk('do = "repeat"\ntimes = 2.5\n'), "whole number")
    err(blk('do = "repeat"\ntimes = 101\n'), "1 to 100")
    err(blk('do = "repeat"\ntimes = 2\nmax = 3\n'), "max goes with until")
    err(blk('do = "wait"\nseconds = 1\nspeed = 2\n'), "block 1: 'speed'")
    err(HEAD + "robot = 3\n", "isn't valid TOML")


def test_move_needs_its_unit():
    err(blk('do = "move"\njoint = "a.b"\nto = 3\n'), r'block 1: say the unit \("deg", "in" or "m"\)')
    err(blk('do = "move"\njoint = "a.b"\nto = 3\nunit = "ft"\n'), "unit must be")


def test_robot_must_be_text():
    err("[test]\nrobot = 3\n", "robot")


def test_place_of_nested_block_and_depth():
    err(blk('do = "repeat"\ntimes = 2\n[[block.body]]\ndo = "wait"\nseconds = 1\n'
            '[[block.body]]\ndo = "wait"\nseconds = -2\n'), r"block 2 inside block 1 \(repeat\): seconds")
    deep = ('do = "repeat"\ntimes = 1\n[[block.body]]\ndo = "repeat"\ntimes = 1\n[[block.body.body]]\n'
            'do = "repeat"\ntimes = 1\n[[block.body.body.body]]\ndo = "repeat"\ntimes = 1\n'
            '[[block.body.body.body.body]]\ndo = "wait"\nseconds = 1\n')
    err(blk(deep), "at most 4 deep")
    P.loads(blk(deep.rsplit("[[block.body.body.body.body]]", 1)[0]), PATH)


def test_json_errors_are_the_same():
    d = P.to_json(P.loads(HEAD, PATH))
    d["blocks"] = [{"do": "wait", "seconds": -1}]
    with pytest.raises(P.TestFileError, match="can't be negative"):
        P.from_json(d, PATH)


def test_awkward_text_roundtrips():
    p = P.Program(PATH, Path('C:\\robots\\"my" bot\x7f\t.toml'), ['a"b', "c\\d\x01"], 5.0, ["é\x7f"],
                  [P.Block("spin", {"motor": 'q"\\.x\x7f', "volts": 1.0})])
    q = P.loads(P.dumps(p), PATH)
    assert q.robot.as_posix() == p.robot.as_posix()
    assert (q.hooks, q.chart, q.blocks) == (p.hooks, p.chart, p.blocks)
    assert P.from_json(P.to_json(p), PATH) == p


def test_json_depth_and_wording():
    leaf = {"do": "wait", "seconds": 1}
    for _ in range(4):
        leaf = {"do": "repeat", "times": 1, "body": [leaf]}
    d = {"robot": "r.toml", "blocks": [leaf]}
    with pytest.raises(P.TestFileError, match="at most 4 deep inside each other"):
        P.from_json(d, PATH)
    for bad in ({"blocks": []}, {"robot": "r.toml", "bogus": 1}, {"robot": 3}):
        with pytest.raises(P.TestFileError) as e:
            P.from_json(bad, PATH)
        assert "[test]" not in str(e.value)


def test_feed_blocks_conditions_and_setup_round_trip():
    p = P.loads(SAMPLE, PATH)
    assert P.loads(P.dumps(p), PATH) == p and P.from_json(P.to_json(p), PATH) == p
    assert p.setup["floor_spread"]["offset_in"] == [-6.0, 0.0, 6.0] and p.hopper == ["feeder.hopper"]
    assert P.placement_count(p.setup) == 6 and P.placement_count({}) == 1


def test_old_test_files_load_unchanged():
    files = sorted(Path(__file__).parent.parent.glob("examples/blocks/*/*.test.toml"))
    assert files
    for f in files:
        assert P.dumps(P.load(f)) == f.read_text(encoding="utf-8")


@pytest.mark.parametrize("bad, words", [
    ('[setup]\ncolour = "red"', '"colour" isn\'t a setting'),
    ("[setup]\nseed = 2.5", "seed must be a whole number"),
    ('[setup]\nball = "beachball"', "isn't a ball"),
    ("[setup]\nfloor_spread = { offset_in = [1,2,3,4,5,6], angle_deg = [0,10,20,30,40,50] }",
     "36 placements; at most 25"),
    ("[setup]\nfloor_spread = { distance_in = [0] }", "more than 0"),
    ("[setup]\nfloor_spread = { offset_in = [] }", "list of numbers"),
    ("[setup]\nstation = { f = { interval = 0 } }", "more than 0"),
    ("[setup]\nstation = { f = { colour = 1 } }", "isn't something"),
    ("[setup]\nstation = { f = { spread_position = [1, 2] } }", "3 numbers"),
    ("[setup]\nstation = { f = { stock = 201 } }", "stock can be at most 200 pieces, not 201"),
    ('[[block]]\ndo = "turn"\ndegrees = 0', "can't be 0"),
    ('[[block]]\ndo = "turn"\ndegrees = 721', "between -720 and 720"),
    ('[[block]]\ndo = "drive"\ndistance = -1\ndirection = "back"', "use one or the other"),
    ('[[block]]\ndo = "drive"\ndistance = -1\ndirection = "left"', "drive left with a minus distance goes right"),
    ('[[block]]\ndo = "drive"\ndistance = -1\ndirection = "right"', "drive right with a minus distance goes left"),
    ("[setup]\nfloor_spread = { height_in = [1] }", "'height_in' isn't something"),
    ('[[block]]\ndo = "wait_until"\nuntil = { station_empty = 3 }', "station_empty must be a name in quotes"),
    ('hopper = "feeder.hopper"', "hopper must be a list of names in quotes"),
    ('[[block]]\ndo = "drive"\ndistance = 1\ndirection = "up"', "direction must be one of"),
    ('[[block]]\ndo = "wait_until"\nuntil = { made_it_in = { at_least = 120 } }', "from 0 to 100"),
    ('[[block]]\ndo = "wait_until"\nuntil = { in_hopper = { at_least = 1.5 } }', "whole number from 0 to 1000"),
    ('[[block]]\ndo = "feed_one"', "station must be a name"),
])
def test_bad_setup_and_blocks_say_what(bad, words):
    with pytest.raises(P.TestFileError, match=words):
        P.loads(HEAD + bad, PATH)


def test_a_cleared_hopper_is_kept():
    """`hopper = []` (the student cleared it in Setup) is kept, so Setup doesn't preselect one again; a test that
    names no hopper has none (None), and writes none."""
    p = P.loads(HEAD + "hopper = []\n", PATH)
    assert p.hopper == [] and "hopper = []" in P.dumps(p) and P.to_json(p)["hopper"] == []
    assert P.from_json(P.to_json(p), PATH).hopper == []
    q = P.loads(HEAD, PATH)
    assert q.hopper is None and "hopper" not in P.dumps(q) and P.to_json(q)["hopper"] is None
    assert P.from_json(P.to_json(q), PATH).hopper is None
