"""A camera under the Test tab (spec D6): the `sees_tag` condition (R9), its words and problems, and the run's camera
changes kept for the replay. The robot is the intake example's stand-in with a camera, on a copy of its field with two
AprilTags: tag 7 straight ahead of the start, tag 8 off to its left."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from scrimmage import i18n, units
from scrimmage.assembler.view import model_view
from scrimmage.blocks.conditions import describe
from scrimmage.blocks.palette import palette, problems
from scrimmage.blocks.program import Cond, TestFileError, dumps, from_json, loads, to_json
from scrimmage.blocks.run import TestRun, world_for
from scrimmage.blocks.store import load_result, save_run
from test_blocks_sensors import MOUTH, _robot

ROOT = Path(__file__).resolve().parent.parent
CAMERA = """
[[sensor]]
name = "front_cam"
kind = "camera"
body = "drive.chassis"
at = [0.3, 0.0, 0.4]
toward = [1, 0, 0]
"""


def _pose(x, y, z, yaw):
    h = math.radians(yaw) / 2.0
    return {"translation": {"x": x, "y": y, "z": z},
            "rotation": {"quaternion": {"W": math.cos(h), "X": 0.0, "Y": 0.0, "Z": math.sin(h)}}}


# the robot starts at (2, 4) facing +x: tag 7 is 4 m ahead facing it, tag 8 2.5 m to its left facing it
TAGS = {"field": {"length": 16.54, "width": 8.07},
        "tags": [{"ID": 7, "pose": _pose(6.0, 4.0, 0.5, 180.0)}, {"ID": 8, "pose": _pose(2.0, 6.5, 0.5, -90.0)}]}


def tagged_robot(tmp: Path, sensors: str = CAMERA, tags: bool = True) -> Path:
    """The intake stand-in with `sensors`, on a copy of the intake field (with the two tags, unless not `tags`)."""
    field = (ROOT / "scrimmage" / "testrange" / "ranges" / "intake.toml").read_text(encoding="utf-8")
    if tags:
        (tmp / "tags.json").write_text(json.dumps(TAGS), encoding="utf-8")
        field = field.replace("\n[range]\n", '\n[range]\napriltags = "tags.json"\n', 1)
        assert "apriltags" in field
    (tmp / "tags.toml").write_text(field, encoding="utf-8")
    robot = _robot(tmp, sensors)
    robot.write_text(robot.read_text(encoding="utf-8").replace('file = "intake"', 'file = "tags.toml"'),
                     encoding="utf-8")
    return robot


def _program(tmp: Path, blocks: str, sensors: str = CAMERA, tags: bool = True):
    tagged_robot(tmp, sensors, tags)
    return loads('[test]\nrobot = "robot.toml"\n' + blocks, tmp / "cam.test.toml")


def _expect(cond: str) -> str:
    return f'[[block]]\ndo = "expect"\nthat = {{ sees_tag = {cond} }}\n'


SEES_7 = _expect('{ camera = "front_cam", tag = 7 }')
SEES_ANY = _expect('{ camera = "front_cam" }')


def test_the_sees_tag_condition_round_trips(tmp_path):
    p = _program(tmp_path, SEES_7 + SEES_ANY)
    conds = [b.args["that"] for b in p.blocks]
    assert [(c.kind, c.args) for c in conds] == [("sees_tag", {"camera": "front_cam", "tag": 7}),
                                                 ("sees_tag", {"camera": "front_cam", "tag": None})]
    text = dumps(p)
    assert 'sees_tag = { camera = "front_cam", tag = 7 }' in text and 'sees_tag = { camera = "front_cam" }' in text
    assert dumps(loads(text, p.path)) == text
    j = to_json(p)
    assert [b["that"] for b in j["blocks"]] == [{"sees_tag": {"camera": "front_cam", "tag": 7}},
                                                {"sees_tag": {"camera": "front_cam"}}]
    assert dumps(from_json(j, p.path)) == text
    for bad in ('{ camera = "front_cam", tag = -1 }', '{ camera = "front_cam", tag = 7.5 }',
                '{ camera = "front_cam", tag = true }', '{ camera = "front_cam", sees = "red" }', "{ tag = 7 }"):
        with pytest.raises(TestFileError):
            _program(tmp_path, _expect(bad))


def test_describe_words_a_sees_tag_check():
    seven, any_tag = Cond("sees_tag", {"camera": "front_cam", "tag": 7}), Cond("sees_tag", {"camera": "front_cam",
                                                                                           "tag": None})
    assert describe(seven) == "front_cam sees tag 7" and describe(any_tag) == "front_cam sees a tag"
    assert describe(seven, form="without") == "front_cam seeing tag 7"
    assert describe(any_tag, form="waiting") == "front_cam to see a tag"


def test_a_test_expects_a_tag_and_passes_or_fails(tmp_path):
    p = _program(tmp_path, SEES_7 + SEES_ANY)
    result = TestRun(world_for(p), p).run()
    assert result.passed, result.outcome
    assert [c["text"] for c in result.checks] == ["front_cam sees tag 7", "front_cam sees a tag"]
    assert set(result.signals["sensor.front_cam"]) == {1.0}         # its signal: how many tags it sees
    p = _program(tmp_path, _expect('{ camera = "front_cam", tag = 8 }'))
    result = TestRun(world_for(p), p).run()
    assert result.status == "check failed"
    assert "expected front_cam to see tag 8, but it saw tag 7" in result.outcome, result.outcome
    # facing away, it sees nothing
    p = _program(tmp_path, '[[block]]\ndo = "turn"\ndegrees = 180.0\n\n' + SEES_ANY)
    result = TestRun(world_for(p), p).run()
    assert "expected front_cam to see a tag, but it saw no tags" in result.outcome, result.outcome
    # a sensor check on the camera: on while it sees any tag
    p = _program(tmp_path, '[[block]]\ndo = "expect"\nthat = { sensor = { sensor = "front_cam", is = "off" } }\n')
    result = TestRun(world_for(p), p).run()
    assert "expected front_cam off, but it saw tag 7" in result.outcome, result.outcome


def test_the_failure_line_is_the_same_machine_text_in_every_language(tmp_path):
    p = _program(tmp_path, _expect('{ camera = "front_cam", tag = 8 }'))
    with units.machine_text():
        result = TestRun(world_for(p), p).run()
    assert "expected front_cam to see tag 8, but it saw tag 7 at " in result.outcome, result.outcome
    assert result.words is not None
    lines = {tuple(c["line"] for c in w["checks"]) for w in result.words.values()}
    assert len(lines) == 1, lines
    for lang in i18n.languages():
        for system in units.SYSTEMS:
            with units.shown_in(system, lang), units.machine_text():
                assert TestRun(world_for(p), p).run().outcome == result.outcome


def test_sees_tag_problems(tmp_path):
    def said(blocks, sensors=CAMERA, tags=True, folder="a"):
        tmp = tmp_path / folder
        tmp.mkdir()
        p = _program(tmp, blocks, sensors, tags)
        return [x["message"] if isinstance(x, dict) else str(x) for x in problems(p, world_for(p))]
    assert said(SEES_7 + SEES_ANY) == []
    got = said(SEES_7, sensors="", folder="none")
    assert len(got) == 1 and "no camera" in got[0] and 'kind = "camera"' in got[0], got
    got = said(_expect('{ camera = "mouth" }'), sensors=CAMERA + MOUTH, folder="beam")
    assert len(got) == 1 and "mouth isn't a camera" in got[0], got
    got = said(_expect('{ camera = "eye" }'), folder="other")
    assert len(got) == 1 and "eye isn't a camera" in got[0] and "front_cam" in got[0], got
    got = said(SEES_7, tags=False, folder="untagged")
    assert len(got) == 1 and "[range] apriltags" in got[0], got
    got = said(_expect('{ camera = "front_cam", tag = 99 }'), folder="t99")
    assert len(got) == 1 and "tag 99" in got[0] and "7, 8" in got[0], got
    got = said('[[block]]\ndo = "expect"\nthat = { sensor = { sensor = "front_cam", is = "on", sees = "red" } }\n',
               folder="sees")
    assert len(got) == 1 and "front_cam is a camera" in got[0], got


def test_the_palette_offers_the_camera_and_the_tags(tmp_path):
    p = _program(tmp_path, SEES_ANY)
    world = world_for(p)
    pal = palette(world, [])
    assert pal["sensors"] == [{"name": "front_cam", "kind": "camera"}]
    assert pal["tags"] == [7, 8] and "sensor.front_cam" in pal["signals"]
    # the replay's view cone: four corners max_range out along the edges of its fields of view
    cam, = model_view(world)["sensors"]
    assert (cam["kind"], cam["hfov_deg"], cam["vfov_deg"], cam["max_range"]) == ("camera", 70, 50, 5.0)
    assert len(cam["corners"]) == 4
    a = np.array(cam["a"][1])
    for _, corner in cam["corners"]:
        edge = np.array(corner) - a
        assert np.linalg.norm(edge) == pytest.approx(5.0)
        assert abs(math.degrees(math.atan2(edge[1], edge[0]))) == pytest.approx(35.0, abs=0.01)    # toward is +x
        assert abs(math.degrees(math.atan2(edge[2], math.hypot(edge[0], edge[1])))) < 25.0
    p = _program(tmp_path, SEES_ANY, MOUTH, tags=False)
    assert palette(world_for(p), [])["tags"] == []


def test_the_run_keeps_camera_changes(tmp_path):
    """RunResult.cameras: [t, [IDs]] at the start and each time the set changes (only then), kept with the run."""
    p = _program(tmp_path, '[[block]]\ndo = "turn"\ndegrees = 90.0\n\n[[block]]\ndo = "wait"\nseconds = 0.5\n')
    result = TestRun(world_for(p), p).run()
    kept = result.cameras["front_cam"]
    assert kept[0][1] == [7] and kept[-1][1] == [8], kept
    assert all(a[1] != b[1] for a, b in zip(kept, kept[1:])), "only changes are kept"
    assert all(a[0] < b[0] for a, b in zip(kept, kept[1:]))
    assert len(kept) <= 4, kept
    folder = save_run(result, p, tmp_path / "cam-runs")
    assert load_result(tmp_path / "cam-runs", folder.name)["cameras"] == {"front_cam": kept}
    # without a camera, nothing
    p = _program(tmp_path, '[[block]]\ndo = "wait"\nseconds = 0.05\n', MOUTH)
    assert TestRun(world_for(p), p).run().cameras == {}
