"""The camera sensor (scrimmage/sensors/camera.py, spec D6): its keys, when a tag counts as seen, what blocks the view,
when it looks, and that a robot without one (or a range without tags) pays nothing."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from range_fixtures import range_2024_with_tags, range_cfg  # noqa: E402
from scrimmage.checks import ScenarioError, resolve_signal  # noqa: E402
from scrimmage.protocol import HalState  # noqa: E402
from scrimmage.sensors.camera import in_view  # noqa: E402
from scrimmage.sensors.spec import SensorError, parse_sensors  # noqa: E402
from scrimmage.testrange.apriltags import TAG_36H11_SIZE_IN, Tag  # noqa: E402
from scrimmage.testrange.build import BALL_BITS  # noqa: E402
from scrimmage.world import World  # noqa: E402

IN = 0.0254
SIZE = TAG_36H11_SIZE_IN * IN
# spec D6's example
FRONT_CAM = {"name": "front_cam", "kind": "camera", "body": "drive.chassis", "at": [0.30, 0.0, 0.25],
             "toward": [1, 0, 0.17], "hfov_deg": 70, "vfov_deg": 50, "max_range": 5.0, "max_angle_deg": 60,
             "fps": 30}


def camera(**keys) -> dict:
    return {**FRONT_CAM, **keys}


# ---- the keys --------------------------------------------------------------------------------------------------------

def test_camera_keys_and_defaults():
    s, = parse_sensors([FRONT_CAM])
    assert (s.kind, s.body, s.start) == ("camera", "drive.chassis", (0.30, 0.0, 0.25))
    assert s.direction == pytest.approx(np.array([1, 0, 0.17]) / math.hypot(1, 0.17))
    assert (s.hfov_deg, s.vfov_deg, s.length, s.max_angle_deg, s.fps) == (70, 50, 5.0, 60, 30)
    bare = {k: v for k, v in FRONT_CAM.items() if k in ("name", "kind", "body", "at", "toward")}
    s, = parse_sensors([bare])
    assert (s.hfov_deg, s.vfov_deg, s.length, s.max_angle_deg, s.fps) == (70, 50, 5.0, 60, 30)
    inches = {k: v for k, v in camera(max_range_in=100, at_in=[10, 0, 10]).items() if k not in ("max_range", "at")}
    s, = parse_sensors([inches])
    assert s.length == pytest.approx(2.54) and s.start == pytest.approx((0.254, 0.0, 0.254))


@pytest.mark.parametrize("keys, key", [({"hfov_deg": 0}, "hfov_deg"), ({"vfov_deg": 180}, "vfov_deg"),
                                       ({"fps": 0}, "fps"), ({"fps": 1001}, "fps"),
                                       ({"max_angle_deg": 95}, "max_angle_deg"), ({"toward": [0, 0, 1]}, "toward"),
                                       ({"toward": [0.01, 0, -1]}, "toward"), ({"dio": 1}, "dio")])
def test_camera_key_errors(keys, key):
    with pytest.raises(SensorError) as e:
        parse_sensors([camera(**keys)])
    assert '[[sensor]] "front_cam"' in str(e.value) and key in str(e.value), str(e.value)
    if key == "toward":
        assert "straight up or down" in str(e.value)


def test_a_right_angle_is_the_most_edge_on():
    s, = parse_sensors([camera(max_angle_deg=90)])
    assert s.max_angle_deg == 90


# ---- when a tag counts as seen (Review Focus 2) ----------------------------------------------------------------------

def _tag() -> Tag:
    """A tag at the origin facing +x."""
    return Tag(1, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0))


def _looking(at, toward):
    """in_view's camera at `at` looking along `toward`, up as the world's z (the body's) made square to it."""
    f = np.asarray(toward, float) / np.linalg.norm(toward)
    z = np.array([0.0, 0.0, 1.0])
    up = z - (z @ f) * f
    return np.asarray(at, float), f, up / np.linalg.norm(up)


def _sees(at, toward, size=SIZE, **keys):
    s, = parse_sensors([camera(toward=[1, 0, 0], **keys)])
    return in_view(*_looking(at, toward), s, _tag(), size)


def _ray(deg: float, r: float = 2.0, axis: str = "z") -> np.ndarray:
    """A point r from the origin, `deg` off +x about the z axis (in the xy plane) or about the y axis (up)."""
    a = math.radians(deg)
    return np.array([r * math.cos(a), r * math.sin(a), 0.0] if axis == "z" else [r * math.cos(a), 0.0, r * math.sin(a)])


def test_in_view_edges():
    assert _sees([2, 0, 0], [-1, 0, 0]) == pytest.approx(2.0)
    assert _sees([-2, 0, 0], [1, 0, 0]) is None                       # behind the tag: it faces away
    assert _sees([2, 0, 0], [1, 0, 0]) is None                        # the tag behind the camera
    # off the tag's normal: the camera looks straight at the tag's center each time
    for deg, seen in ((60.0, True), (60.5, False)):
        at = _ray(deg)
        assert (_sees(at, -at) is not None) == seen, deg
        at = _ray(deg, axis="y")
        assert (_sees(at, -at) is not None) == seen, deg
    # off the camera's own axis, from in front of the tag: half of each field of view
    for axis, half in (("z", 35.0), ("y", 25.0)):
        for off, seen in ((half - 0.1, True), (half + 0.1, False)):
            a = math.radians(off)
            # turn the camera's view `off` degrees away from the tag about the up axis (z) or the left axis (y)
            look = [-math.cos(a), math.sin(a), 0.0] if axis == "z" else [-math.cos(a), 0.0, math.sin(a)]
            assert (_sees([2, 0, 0], look) is not None) == seen, (axis, off)
    # range: max_range for 6.5 in tags, and in proportion for smaller ones (R7)
    assert _sees([5.0, 0, 0], [-1, 0, 0]) == pytest.approx(5.0)
    assert _sees([5.01, 0, 0], [-1, 0, 0]) is None
    assert _sees([2.6, 0, 0], [-1, 0, 0], size=3.25 * IN) is None
    assert _sees([2.4, 0, 0], [-1, 0, 0], size=3.25 * IN) == pytest.approx(2.4)


# ---- in the world ----------------------------------------------------------------------------------------------------

def _pose(x, y, z, yaw=0.0):
    h = math.radians(yaw) / 2.0
    return {"translation": {"x": x, "y": y, "z": z},
            "rotation": {"quaternion": {"W": math.cos(h), "X": 0.0, "Y": 0.0, "Z": math.sin(h)}}}


# Tag 1 on the blue wall's face, 1.45 m up (above the 0.5 m wall); tag 2 free-standing just outside the field behind
# the blue wall, 0.3 m up, so a camera on the field sees it only through the wall. A box 2 m tall stands at (2, 4).
TWO_TAGS = {"field": {"length": 16.54, "width": 8.07},
            "tags": [{"ID": 1, "pose": _pose(0.0, 4.0, 1.45)}, {"ID": 2, "pose": _pose(-0.25, 2.0, 0.3)}]}
FIELD = '''
[range]
name = "two tags"
ball = "2026-fuel"
{apriltags}
[[floor_balls]]
at = [[8.0, 7.0]]

[[element]]
name = "block"
kind = "box"
center = [2.0, 4.0]
size = [0.3, 0.6, 2.0]
'''


def tag_world(tmp_path, *sensors, tags: bool = True) -> World:
    (tmp_path / "tags.json").write_text(json.dumps(TWO_TAGS), encoding="utf-8")
    path = tmp_path / "field.toml"
    path.write_text(FIELD.format(apriltags='apriltags = "tags.json"\n' if tags else ""), encoding="utf-8")
    return World(range_cfg(range={"file": str(path), "floor_balls": True}, sensor=list(sensors or [FRONT_CAM])), None)


def step(w, n=1):
    hal = HalState()
    for _ in range(n):
        w.step(hal)


def signal(w, name):
    return resolve_signal(name, w).read(SimpleNamespace(world=w))


def test_a_camera_sees_a_tag_and_reports_it(tmp_path):
    w = tag_world(tmp_path)
    w.set_robot_pose(3.0, 5.0, 180.0)           # tag 1 ahead, 20 deg to the right; tag 2 past the view's edge
    r = w.sensors.reading("front_cam")
    assert r.on and r.tags == (1,)
    cam = w.data.xpos[w.model.body("drive.chassis").id] + [-0.3, 0.0, 0.25]
    assert r.tag_distances[0] == pytest.approx(float(np.linalg.norm(np.array([0.0, 4.0, 1.45]) - cam)))
    assert signal(w, "sensor.front_cam") is True and signal(w, "sensor.front_cam.tags") == 1
    assert signal(w, "sensor.front_cam.sees_tag.1") == 1 and signal(w, "sensor.front_cam.sees_tag.2") == 0
    assert signal(w, "sensor.front_cam.tag_distance.1") == pytest.approx(r.tag_distances[0])
    assert signal(w, "sensor.front_cam.tag_distance.2") == 5.0          # max_range while it isn't seen
    assert w.sensors.state()["front_cam"] == {"kind": "camera", "on": True, "distance_m": None, "color": None,
                                              "tags": [1]}
    w.set_robot_pose(3.0, 5.0, 0.0)             # facing away
    assert w.sensors.reading("front_cam").tags == () and signal(w, "sensor.front_cam") is False
    assert signal(w, "sensor.front_cam.tags") == 0
    with pytest.raises(ScenarioError) as e:
        resolve_signal("sensor.front_cam.sees_tag.9", w)
    assert "1, 2" in str(e.value) and "'9'" in str(e.value)
    with pytest.raises(ScenarioError) as e:
        resolve_signal("sensor.front_cam.distance", w)
    assert "sensor.front_cam.tags" in str(e.value)


def test_a_camera_on_a_range_with_no_tags_is_an_error_for_its_signals(tmp_path):
    w = tag_world(tmp_path, tags=False)
    with pytest.raises(ScenarioError) as e:
        resolve_signal("sensor.front_cam.tags", w)
    assert "[range] apriltags" in str(e.value)
    beam = {"name": "mouth", "kind": "beam", "body": "drive.chassis", "from": [0.3, 0.2, 0.05], "to": [0.3, -0.2, 0.05]}
    w = tag_world(tmp_path, beam)
    with pytest.raises(ScenarioError) as e:
        resolve_signal("sensor.mouth.tags", w)
    assert "isn't a camera" in str(e.value)


def test_what_blocks_a_camera(tmp_path):
    """Review Focus 3: the box between the camera and the tag blocks it, and so does a ball moved onto the line. The
    robot's own geoms and the perimeter wall don't."""
    w = tag_world(tmp_path)
    w.set_robot_pose(3.0, 4.0, 180.0)           # the box stands between the camera and tag 1 (tag 2 is clear)
    assert w.sensors.reading("front_cam").tags == (2,)
    w.set_robot_pose(3.0, 5.0, 180.0)
    assert w.sensors.reading("front_cam").tags == (1,)
    # a floor ball halfway along the line to tag 1
    cam = w.data.xpos[w.model.body("drive.chassis").id] + [-0.3, 0.0, 0.25]
    mid = (cam + np.array([0.0, 4.0, 1.45])) / 2.0
    w.test_range.balls.put(0, mid, BALL_BITS)
    mujoco.mj_forward(w.model, w.data)
    w._read_sensors()
    assert w.sensors.reading("front_cam").tags == ()
    w.test_range.balls.put(0, [8.0, 7.0, 0.075], BALL_BITS)
    mujoco.mj_forward(w.model, w.data)
    w._read_sensors()
    assert w.sensors.reading("front_cam").tags == (1,)
    # through the robot: a camera low on the drive's rails (the chassis's bumpers are another body's, which the ray
    # doesn't exclude) looking back through the rear bumper, the robot facing away
    back = camera(name="back_cam", body="dt.rails", at=[0.0, 0.0, 0.05], toward=[-1, 0, 0.3])
    w = tag_world(tmp_path, back)
    w.set_robot_pose(3.0, 5.0, 0.0)
    cams = w.sensors.cameras
    before = cams.casts
    w._read_sensors()
    assert w.sensors.reading("back_cam").tags == (1,)
    assert cams.casts - before > 1, "its ray met the robot and went on"
    # through the perimeter wall: tag 2 stands behind the blue wall, 0.3 m up (the wall is 0.5 m tall)
    w = tag_world(tmp_path)
    w.set_robot_pose(2.0, 2.0, 180.0)
    before = w.sensors.cameras.casts
    w._read_sensors()
    assert w.sensors.reading("front_cam").tags == (2,)
    assert w.sensors.cameras.casts - before == 3, "the ray met the wall going in and out, and went on to the tag"


def test_a_camera_sees_tag_7_inside_its_wall(tmp_path):
    """Review Focus 1: 2024's tag 7 is 3.8 cm inside the SPEAKER's wall obstacle, and a camera in front sees it (the
    ray goes to its face point), with tag 8 beside it."""
    path = range_2024_with_tags(tmp_path)
    cam = camera(at=[0.3, 0.0, 1.0], toward=[1, 0, 0])
    w = World(range_cfg(range={"file": str(path), "floor_balls": False}, sensor=[cam]), None)
    w.set_robot_pose(2.5, 5.548, 180.0)
    assert w.sensors.reading("front_cam").tags == (7, 8)


def test_a_poke_reads_the_camera_again(tmp_path):
    """Review Focus 4: after set_pose, the reading is of the new pose at once, with no step taken."""
    w = tag_world(tmp_path)
    w.set_robot_pose(3.0, 5.0, 180.0)
    step(w, 3)
    t = w.time
    assert w.sensors.reading("front_cam").tags == (1,)
    w.set_robot_pose(3.0, 5.0, 0.0)
    assert w.time == t and w.sensors.reading("front_cam").tags == ()
    w.set_robot_pose(3.0, 5.0, 180.0)
    assert w.sensors.reading("front_cam").tags == (1,)


def test_a_camera_runs_at_its_fps(tmp_path):
    w = tag_world(tmp_path, camera(fps=10))
    w.set_robot_pose(3.0, 5.0, 180.0)
    cams = w.sensors.cameras
    frames, last = 0, cams.casts
    for _ in range(round(1.0 / w.model.opt.timestep)):
        step(w)
        if cams.casts != last:
            frames, last = frames + 1, cams.casts
    assert abs(frames - 10) <= 1, frames
    assert w.sensors.reading("front_cam").tags == (1,)


def test_no_camera_no_cost(tmp_path):
    beam = {"name": "mouth", "kind": "beam", "body": "drive.chassis", "from": [0.3, 0.2, 0.05], "to": [0.3, -0.2, 0.05]}
    w = tag_world(tmp_path, beam)
    assert w.sensors.cameras is None
    w = tag_world(tmp_path, tags=False)
    cams = w.sensors.cameras
    assert cams.layout is None
    step(w, 100)
    assert cams.casts == 0 and w.sensors.reading("front_cam").tags == ()
