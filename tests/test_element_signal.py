"""A moving field element's signal, `element.<name>` (a hinged panel's angle in degrees, a sliding one's travel in
inches, 0 at rest), in scenario checks and the Test tab's chart, and the Test tab's `element` condition
(scrimmage/checks.py, scrimmage/blocks/)."""

from __future__ import annotations

import math
from types import SimpleNamespace

import mujoco
import pytest

from scrimmage.assembler.signals import signals
from scrimmage.assembler.templates import new_config
from scrimmage.blocks.conditions import describe
from scrimmage.blocks.palette import palette, problems
from scrimmage.blocks.program import Cond, TestFileError, dumps, loads
from scrimmage.blocks.run import TestRun, world_for
from scrimmage.checks import ScenarioError, SignalCheck, resolve_signal

IN = 0.0254

# A door across the stand-in's path, hinged on its +y edge (it swings away from a robot driving +x: positive about
# +z), a gate that slides up, and a fixed box.
FIELD = """
[range]
name = "a door"
ball = "2026-fuel"

[start.here]
pose = [2.5, 4.0, 0.0]

[[element]]
name = "door"
kind = "hinged"
center = [3.6, 4.0]
hinge = "left"
size = [0.05, 0.9, 1.0]
base_height = 0.05
range_deg = [0, 90]
mass = 3

[[element]]
name = "gate"
kind = "sliding"
center = [6.0, 1.5]
axis = "z"
size = [0.6, 0.05, 0.4]
base_height = 0.1
range = [0, 0.5]
mass = 2

[[element]]
name = "wall"
kind = "box"
center = [7.0, 6.5]
size = [0.3, 0.3, 0.3]
"""

DRIVE = """
[[block]]
do = "spin"
motor = "dt.left_2"
volts = 6.0

[[block]]
do = "spin"
motor = "dt.right_2"
volts = 6.0
"""

WAIT = """
[[block]]
do = "wait_until"
until = { element = { element = "door", above = 30.0 } }
timeout = 3.0
"""


def _program(folder, blocks: str):
    (folder / "door.toml").write_text(FIELD, encoding="utf-8")
    (folder / "robot.toml").write_text(new_config("standin") + '\n[range]\nfile = "door.toml"\nstart = "here"\n',
                                       encoding="utf-8")
    path = folder / "t.test.toml"
    path.write_text(f'[test]\nrobot = "robot.toml"\n{blocks}', encoding="utf-8")
    return loads(path.read_text(encoding="utf-8"), path)


def _world(folder):
    return world_for(_program(folder, ""))


def test_element_condition(tmp_path):
    """The stand-in drives into the door and pushes it open past 30 degrees; left alone, the door stays shut and the
    wait times out in plain words."""
    program = _program(tmp_path, DRIVE + WAIT + '\n[[block]]\ndo = "expect"\n'
                                               'that = { element = { element = "door", above = 30.0 } }\n')
    r = TestRun(world_for(program), program).run()
    assert r.status == "finished", r.outcome
    assert r.checks[0]["passed"] and r.checks[0]["text"] == "door is above 30°"
    door = r.signals["element.door"]
    assert door[0] == pytest.approx(0.0, abs=0.5) and max(door) > 30.0
    assert r.signals["element.gate"][-1] == pytest.approx(0.0, abs=0.1)      # untouched, on its lower stop

    (tmp_path / "alone").mkdir()
    program = _program(tmp_path / "alone", WAIT)    # nothing pushes the door
    r = TestRun(world_for(program), program).run()
    assert r.status == "timed out"
    assert r.outcome.startswith("waiting for door to be above 30° timed out at ")
    assert max(r.signals["element.door"]) < 1.0
    program = _program(tmp_path / "alone", '[[block]]\ndo = "expect"\n'
                                           'that = { element = { element = "gate", above = 2.0 } }\n')
    r = TestRun(world_for(program), program).run()
    assert r.status == "check failed" and r.checks[0]["text"] == "gate is above 2 in"
    assert r.checks[0]["failure"] == "expected gate above 2 in, but it was 0 in"


def test_element_signal_in_scenario(tmp_path):
    w = _world(tmp_path)
    session = SimpleNamespace(world=w)
    door, gate = (resolve_signal(f"element.{n}", w) for n in ("door", "gate"))
    assert door.read(session) == pytest.approx(0.0) and gate.read(session) == pytest.approx(0.0)
    w.data.qpos[w.parts.layout.moving["door"]] = math.radians(1080.0)      # three turns read 1080, unwrapped
    w.data.qpos[w.parts.layout.moving["gate"]] = 0.3
    mujoco.mj_forward(w.model, w.data)
    assert door.read(session) == pytest.approx(1080.0)
    assert gate.read(session) == pytest.approx(0.3 / IN)
    w.data.qpos[w.parts.layout.moving["door"]] = math.radians(40.0)
    session.t = w.time
    check = SignalCheck("at_end", {"signal": "element.door", "approx": 40.0, "tol": 0.01}, w, "check 1")
    check.begin(session)
    result = check.end(session)
    assert result.passed and result.measured == pytest.approx(40.0)
    with pytest.raises(ScenarioError, match="element.door"):
        resolve_signal("element.door.angle", w)                 # it isn't a platform: read it as element.door
    with pytest.raises(ScenarioError, match="moving elements: door, gate"):
        resolve_signal("element.wall", w)
    with pytest.raises(ScenarioError, match="moving elements: door, gate"):
        resolve_signal("element.nope", w)
    assert signals(w)["range"][-2:] == ["element.door", "element.gate"]


def test_element_names_checked(tmp_path):
    w = _world(tmp_path)
    pal = palette(w, [])
    assert pal["moving_elements"] == [{"name": "door", "unit": "deg"}, {"name": "gate", "unit": "in"}]
    assert {"element.door", "element.gate"} <= set(pal["signals"])

    def bad(element: str) -> list[str]:
        program = _program(tmp_path, '[[block]]\ndo = "expect"\n'
                                     f'that = {{ element = {{ element = "{element}", below = 5.0 }} }}\n')
        return [p["message"] for p in problems(program, w)]

    assert bad("door") == [] and bad("gate") == []
    assert bad("wall") == ["wall isn't a moving element; this field's moving elements: door, gate"]
    assert bad("nope") == ["nope isn't a moving element; this field's moving elements: door, gate"]


def test_element_on_a_field_without_moving_elements(tmp_path):
    (tmp_path / "robot.toml").write_text(new_config("standin"), encoding="utf-8")
    path = tmp_path / "t.test.toml"
    path.write_text('[test]\nrobot = "robot.toml"\n\n[[block]]\ndo = "expect"\n'
                    'that = { element = { element = "door", above = 5.0 } }\n', encoding="utf-8")
    program = loads(path.read_text(encoding="utf-8"), path)
    w = world_for(program)
    assert palette(w, [])["moving_elements"] == []
    assert [p["message"] for p in problems(program, w)] == [
        "door isn't a moving element; this field has no moving elements"]


def test_element_file_shape(tmp_path):
    def load(cond: str):
        return loads('[test]\nrobot = "robot.toml"\n\n[[block]]\ndo = "expect"\n' f"that = {{ element = {cond} }}\n",
                     tmp_path / "x.test.toml")

    p = load('{ element = "door", above = 45 }')
    assert p.blocks[0].args["that"] == Cond("element", {"element": "door", "above": 45.0})
    assert loads(dumps(p), tmp_path / "x.test.toml") == p
    assert load('{ element = "gate", below = -2.5 }').blocks[0].args["that"].args == {"element": "gate", "below": -2.5}
    with pytest.raises(TestFileError, match="exactly one of above or below"):
        load('{ element = "door", above = 45, below = 90 }')
    with pytest.raises(TestFileError, match="exactly one of above or below"):
        load('{ element = "door" }')
    with pytest.raises(TestFileError, match="element must be a name in quotes"):
        load("{ above = 45 }")
    with pytest.raises(TestFileError, match="'unit' isn't something"):
        load('{ element = "door", above = 45, unit = "deg" }')
    assert describe(Cond("element", {"element": "door", "above": 45.0}), units={"door": "deg"}) == "door is above 45°"
    assert describe(Cond("element", {"element": "portcullis", "above": 12.0}),
                    units={"portcullis": "in"}) == "portcullis is above 12 in"
    assert describe(Cond("element", {"element": "door", "below": 5.0}), units={"door": "deg"}) == "door is below 5°"
