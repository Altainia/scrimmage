"""The blocks test file format (scrimmage/blocks/program.py)."""

from pathlib import Path

import pytest

from scrimmage.blocks import program as P

HEAD = '[test]\nrobot = "r.toml"\n'
PATH = Path("/tmp/x.test.toml")

SAMPLE = HEAD + '''hooks = ["climber.hook_A"]
time_limit = 30.0
chart = ["tilt", "climber.crank"]

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
