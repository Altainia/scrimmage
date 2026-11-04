"""Sensors under the Test tab (sub-project 26, goals 3 and 4): the `sensor` condition, the `sensor.<name>` chart
signals, the palette's sensors and their problems, and the sensors model_view gives the replay. The robot is the
intake example's stand-in (examples/blocks/intake) with a beam break across its mouth."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from scrimmage.assembler.view import model_view
from scrimmage.blocks.conditions import describe
from scrimmage.blocks.palette import palette, problems
from scrimmage.blocks.program import Cond, TestFileError, dumps, from_json, loads, to_json
from scrimmage.blocks.run import TestRun, world_for
from scrimmage.sensors.colors import NAMED_COLORS

ROOT = Path(__file__).resolve().parent.parent
INTAKE = ROOT / "examples" / "blocks" / "intake"
# A beam across the intake's mouth, between the hopper's lip and the roller (as tests/test_sensors.py's MOUTH).
MOUTH = """
[[sensor]]
name = "mouth"
kind = "beam"
body = "intake.frame"
from = [0.32, 0.17, 0.05]
to = [0.32, -0.17, 0.05]
"""
RANGE = """
[[sensor]]
name = "range"
kind = "distance"
body = "drive.chassis"
at = [0.3, 0.0, 0.1]
toward = [1, 0, 0]
"""
EYE = """
[[sensor]]
name = "eye"
kind = "color"
body = "intake.frame"
at = [0.32, 0.0, 0.2]
toward = [0, 0, -1]
"""
# Drive at the ball with the roller on until the beam breaks, then stop everything: the ball stays in the mouth.
STOP_AT_THE_BEAM = """
[[block]]
do = "spin"
motor = "intake.roller"
volts = 6.0

[[block]]
do = "spin"
motor = "dt.left_2"
volts = 4.0

[[block]]
do = "spin"
motor = "dt.right_2"
volts = 4.0

[[block]]
do = "wait_until"
until = { sensor = { sensor = "mouth", is = "on" } }
timeout = 5.0

[[block]]
do = "stop"
motor = "all"

[[block]]
do = "wait"
seconds = 1.0

[[block]]
do = "expect"
that = { sensor = { sensor = "mouth", is = "on" } }
"""


def _robot(tmp: Path, sensors: str = MOUTH) -> Path:
    """The intake stand-in, written into `tmp` with its part folder's path made absolute, and `sensors`."""
    text = (INTAKE / "intake-standin.toml").read_text(encoding="utf-8")
    text = text.replace('path = "parts/intake"', f'path = "{(INTAKE / "parts" / "intake").as_posix()}"')
    path = tmp / "robot.toml"
    path.write_text(text + sensors, encoding="utf-8")
    return path


def _program(tmp: Path, blocks: str, sensors: str = MOUTH):
    _robot(tmp, sensors)
    return loads('[test]\nrobot = "robot.toml"\n' + blocks, tmp / "beam.test.toml")


def _expect(cond: str) -> str:
    return f'[[block]]\ndo = "expect"\nthat = {{ sensor = {cond} }}\n'


# ---- the run ---------------------------------------------------------------------------------------------------------

def test_the_intake_stops_when_the_beam_breaks_and_holds_the_ball_in_the_mouth(tmp_path):
    p = _program(tmp_path, STOP_AT_THE_BEAM)
    world = world_for(p)
    result = TestRun(world, p).run()
    assert result.passed, result.outcome
    sig, t = result.signals["sensor.mouth"], result.signals["t"]
    assert set(sig) == {0.0, 1.0}
    broke = t[sig.index(1.0)]
    assert broke > 0.5                                    # clear while the robot drives at the ball
    assert all(v == 1.0 for v in sig[sig.index(1.0):])    # broken from then on: the ball is held in the mouth
    # the ball is in the mouth, not in the hopper behind it
    frame = world.model.body("intake.frame").id
    local = world.data.xmat[frame].reshape(3, 3).T @ (world.test_range.balls.position(0) - world.data.xpos[frame])
    assert 0.2 < local[0] < 0.45, local


def test_a_color_sensor_over_the_mouth_sees_the_held_ball_as_yellow(tmp_path):
    """The 2026 FUEL is yellow: a color sensor looking down into the mouth sees it once the beam has stopped it, and
    its signal is yellow's index."""
    p = _program(tmp_path, STOP_AT_THE_BEAM + _expect('{ sensor = "eye", is = "on", sees = "yellow" }')
                 + _expect('{ sensor = "eye", is = "off", sees = "blue" }'), MOUTH + EYE)
    result = TestRun(world_for(p), p).run()
    assert result.passed, result.outcome
    assert math.isnan(result.signals["sensor.eye"][0])
    assert result.signals["sensor.eye"][-1] == float(list(NAMED_COLORS).index("yellow"))


def test_a_failed_sensor_check_says_what_it_read(tmp_path):
    p = _program(tmp_path, _expect('{ sensor = "mouth", is = "on" }'))
    result = TestRun(world_for(p), p).run()
    assert result.status == "check failed"
    assert "expected mouth on, but it was off (clear)" in result.outcome, result.outcome
    p = _program(tmp_path, _expect('{ sensor = "range", is = "on" }'), MOUTH + RANGE)
    result = TestRun(world_for(p), p).run()
    assert "expected range on, but it read " in result.outcome and "(it is on closer than 39.4 in)" in result.outcome
    p = _program(tmp_path, _expect('{ sensor = "eye", is = "on", sees = "red" }'), EYE)
    result = TestRun(world_for(p), p).run()
    assert "expected eye to see red, but it saw no piece" in result.outcome, result.outcome


def test_a_waiting_sensor_check_is_worded_while_it_waits(tmp_path):
    p = _program(tmp_path, '[[block]]\ndo = "wait_until"\nuntil = { sensor = { sensor = "eye", is = "on", '
                           'sees = "yellow" } }\ntimeout = 0.2\n', EYE)
    result = TestRun(world_for(p), p).run()
    assert result.status == "timed out"
    assert "eye to see yellow" in result.outcome, result.outcome


def test_sensor_signals_by_kind(tmp_path):
    """A beam is 0 or 1, a distance inches (its max_range with nothing in range), a color sensor NaN while it sees
    no piece."""
    p = _program(tmp_path, '[[block]]\ndo = "wait"\nseconds = 0.1\n', MOUTH + RANGE + EYE)
    world = world_for(p)
    pal = palette(world, [])
    assert pal["sensors"] == [{"name": "eye", "kind": "color"}, {"name": "mouth", "kind": "beam"},
                              {"name": "range", "kind": "distance"}]
    assert pal["sensor_colors"] == list(NAMED_COLORS)
    assert {"sensor.eye", "sensor.mouth", "sensor.range"} <= set(pal["signals"])
    result = TestRun(world, p).run()
    assert set(result.signals["sensor.mouth"]) == {0.0}
    assert all(math.isnan(v) for v in result.signals["sensor.eye"])
    # the ball sits ahead of the robot ("facing_ball"): the distance sensor sees it, closer than 2 m
    reach = world.sensors.reading("range").distance / 0.0254
    assert result.signals["sensor.range"][-1] == pytest.approx(reach)
    assert 0 < reach < 2.0 / 0.0254


def test_a_red_ball_reads_red_and_its_signal_is_reds_index(tmp_path):
    """The intake field with its ball colored red (a range file's [range] color, spec D5): the color sensor over the
    mouth sees red once the beam has stopped the ball, and its signal is red's index."""
    field = (ROOT / "scrimmage" / "testrange" / "ranges" / "intake.toml").read_text(encoding="utf-8")
    field = field.replace('ball = "2026-fuel" ', 'ball = "2026-fuel"\ncolor = "red" ', 1)
    assert 'color = "red"' in field
    (tmp_path / "red.toml").write_text(field, encoding="utf-8")
    _robot(tmp_path, MOUTH + EYE)
    robot = tmp_path / "robot.toml"
    robot.write_text(robot.read_text(encoding="utf-8").replace('file = "intake"', 'file = "red.toml"'),
                     encoding="utf-8")
    p = loads('[test]\nrobot = "robot.toml"\n' + STOP_AT_THE_BEAM
              + _expect('{ sensor = "eye", is = "on", sees = "red" }')
              + _expect('{ sensor = "eye", is = "off", sees = "yellow" }'), tmp_path / "red.test.toml")
    result = TestRun(world_for(p), p).run()
    assert result.passed, result.outcome
    assert result.signals["sensor.eye"][-1] == float(list(NAMED_COLORS).index("red"))


def test_a_robot_without_sensors_has_no_sensor_signals(tmp_path):
    p = _program(tmp_path, '[[block]]\ndo = "wait"\nseconds = 0.05\n', "")
    world = world_for(p)
    pal = palette(world, [])
    assert pal["sensors"] == [] and not [s for s in pal["signals"] if s.startswith("sensor.")]
    assert model_view(world)["sensors"] == []
    assert not [s for s in TestRun(world, p).run().signals if s.startswith("sensor.")]


# ---- the file and the words ----------------------------------------------------------------------------------------

def test_the_sensor_condition_round_trips(tmp_path):
    text = ('[test]\nrobot = "robot.toml"\n'
            + _expect('{ sensor = "mouth", is = "on" }') + _expect('{ sensor = "eye", is = "off", sees = "red" }'))
    p = loads(text, tmp_path / "t.test.toml")
    assert p.blocks[0].args["that"] == Cond("sensor", {"sensor": "mouth", "is": "on", "sees": None})
    assert p.blocks[1].args["that"] == Cond("sensor", {"sensor": "eye", "is": "off", "sees": "red"})
    assert loads(dumps(p), p.path).blocks == p.blocks
    j = to_json(p)
    assert j["blocks"][0]["that"] == {"sensor": {"sensor": "mouth", "is": "on"}}
    assert j["blocks"][1]["that"] == {"sensor": {"sensor": "eye", "is": "off", "sees": "red"}}
    assert from_json(j, p.path).blocks == p.blocks


@pytest.mark.parametrize("cond, words", [
    ('{ sensor = "mouth" }', 'is must be "on" or "off", not None'),
    ('{ sensor = "mouth", is = "broken" }', 'is must be "on" or "off", not \'broken\''),
    ('{ sensor = "eye", is = "on", sees = "pink" }', "sees must be one of the colors red, blue, yellow"),
    ('{ is = "on" }', "sensor must be a name in quotes"),
    ('{ sensor = "eye", is = "on", color = "red" }', "'color' isn't something this kind of block uses"),
])
def test_a_bad_sensor_condition_says_what(tmp_path, cond, words):
    with pytest.raises(TestFileError, match="block 1") as e:
        loads('[test]\nrobot = "robot.toml"\n' + _expect(cond), tmp_path / "t.test.toml")
    assert words in str(e.value)


def test_describe_words_a_sensor_check():
    assert describe(Cond("sensor", {"sensor": "beam", "is": "on", "sees": None})) == "beam is on"
    assert describe(Cond("sensor", {"sensor": "beam", "is": "off", "sees": None})) == "beam is off"
    assert describe(Cond("sensor", {"sensor": "color", "is": "on", "sees": "red"})) == "color is seeing red"
    assert describe(Cond("sensor", {"sensor": "color", "is": "off", "sees": "red"})) == "color isn't seeing red"


def test_a_repeat_or_wait_on_a_color_reads_plainly_when_it_runs_out(tmp_path):
    """A color sensor's check in the run's sentences: never a double negative."""
    repeat = ('[[block]]\ndo = "repeat"\nuntil = { sensor = { sensor = "eye", is = "on", sees = "red" } }\nmax = 3\n'
              '\n  [[block.body]]\n  do = "wait"\n  seconds = 0.01\n')
    p = _program(tmp_path, repeat, EYE)
    assert TestRun(world_for(p), p).run().outcome.endswith("stopped after 3 times without eye seeing red")
    held = STOP_AT_THE_BEAM + ('[[block]]\ndo = "repeat"\nuntil = { sensor = { sensor = "eye", is = "off", '
                               'sees = "yellow" } }\nmax = 3\n\n  [[block.body]]\n  do = "wait"\n  seconds = 0.01\n')
    p = _program(tmp_path, held, MOUTH + EYE)
    outcome = TestRun(world_for(p), p).run().outcome
    assert outcome.endswith("stopped after 3 times without eye losing sight of yellow"), outcome
    p = _program(tmp_path, held.replace('do = "repeat"', 'do = "wait_until"').replace("max = 3", "timeout = 0.1")
                 .split("\n  [[block.body]]")[0], MOUTH + EYE)
    outcome = TestRun(world_for(p), p).run().outcome
    assert "waiting for eye to lose sight of yellow timed out" in outcome, outcome


# ---- names ---------------------------------------------------------------------------------------------------------

def _problems(tmp, cond: str, sensors: str = MOUTH + EYE) -> list[str]:
    p = _program(tmp, _expect(cond), sensors)
    return [x["message"] for x in problems(p, world_for(p))]


def test_the_names_checks(tmp_path):
    assert _problems(tmp_path, '{ sensor = "mouth", is = "on" }') == []
    assert _problems(tmp_path, '{ sensor = "eye", is = "off", sees = "blue" }') == []
    assert _problems(tmp_path, '{ sensor = "nose", is = "on" }') == [
        "nose isn't a sensor; the robot's sensors: eye, mouth"]
    assert _problems(tmp_path, '{ sensor = "mouth", is = "on", sees = "red" }') == [
        "mouth is a beam break, which can't see colors; only a color sensor can see red"]
    assert _problems(tmp_path, '{ sensor = "mouth", is = "on" }', "") == [
        "this robot has no sensors; add a [[sensor]] to its config"]


# ---- the replay ----------------------------------------------------------------------------------------------------

def test_model_view_lists_the_sensors_as_pins_on_their_movers(tmp_path):
    p = _program(tmp_path, "", MOUTH + RANGE)
    world = world_for(p)
    v = model_view(world, include_world=True)
    mouth, rng = v["sensors"]
    assert mouth == {"name": "mouth", "kind": "beam", "a": mouth["a"], "b": mouth["b"]}
    assert v["movers"][mouth["a"][0]]["name"] == "intake.frame" and mouth["b"][0] == mouth["a"][0]
    assert mouth["a"][1] == pytest.approx([0.32, 0.17, 0.05]) and mouth["b"][1] == pytest.approx([0.32, -0.17, 0.05])
    assert rng["kind"] == "distance" and v["movers"][rng["a"][0]]["name"] == "drive.chassis"
    assert rng["a"][1] == pytest.approx([0.3, 0.0, 0.1]) and rng["b"][1] == pytest.approx([2.3, 0.0, 0.1])
    assert rng["threshold_in"] == pytest.approx(1.0 / 0.0254)
