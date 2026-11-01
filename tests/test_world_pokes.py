import math
import tomllib
from pathlib import Path

import pytest

from scrimmage.protocol import HalState
from scrimmage.world import World

ROOT = Path(__file__).resolve().parents[1]


def kitbot() -> World:
    return World(tomllib.loads((ROOT / "configs" / "kitbot.toml").read_text()), ROOT / "configs")


def test_set_robot_pose():
    w = kitbot()
    w.set_robot_pose(5.0, 3.0, 90.0)
    x, y, yaw = w.robot_pose()
    assert (x, y) == pytest.approx((5.0, 3.0)) and yaw == pytest.approx(1.5708, abs=1e-3)
    assert w.robot_speed() == 0.0


def test_force_pushes_robot_along_its_direction():
    w = kitbot()
    w.set_robot_force(600.0, 0.0)  # +x, along the wheels (sideways, tire friction would hold it)
    hal = HalState()
    for _ in range(300):
        w.step(hal)
    x, y, _ = w.robot_pose()
    assert x > 2.0 + 0.05 and abs(y - 4.035) < 0.05
    w.set_robot_force(0.0, 0.0)


def test_move_static_geom_and_reset_restores_it():
    w = kitbot()
    assert "obstacle" in w.movable_objects()
    w.move_object("obstacle", 3.0, 1.0, 0.25, 45.0)
    gid = w.model.geom("obstacle").id
    assert list(w.model.geom_pos[gid]) == pytest.approx([3.0, 1.0, 0.25])
    w.reset()
    assert list(w.model.geom_pos[gid]) == pytest.approx([8.27, 4.035, 0.25])


def test_move_unknown_object_lists_movable_names():
    w = kitbot()
    with pytest.raises(ValueError, match="obstacle"):
        w.move_object("nope", 0, 0, 0, 0)
    with pytest.raises(ValueError, match="robot"):
        w.move_object("drive.chassis", 0, 0, 0, 0)


def ball_world(tmp_path) -> World:
    xml = (ROOT / "tests" / "models" / "kitbot.xml").read_text()
    ball = '<body name="ball" pos="5 5 0.2"><freejoint/><geom type="sphere" size="0.1" mass="0.5"/></body>'
    xml = xml.replace("</worldbody>", ball + "</worldbody>", 1)
    (tmp_path / "ball.xml").write_text(xml)
    cfg = tomllib.loads((ROOT / "tests" / "fake_robot.toml").read_text())
    cfg["model"]["mjcf"] = str(tmp_path / "ball.xml")
    return World(cfg, ROOT / "tests")


def test_move_free_body(tmp_path):
    w = ball_world(tmp_path)
    assert "ball" in w.movable_objects()
    assert w.object_z("ball") == pytest.approx(0.2)
    w.move_object("ball", 6, 6, 0.2, 0)
    q = w.model.jnt_qposadr[w.model.body_jntadr[w.model.body("ball").id]]
    assert list(w.data.qpos[q:q + 3]) == pytest.approx([6, 6, 0.2])
    w.move_object("ball", 7, 7, w.object_z("ball"), 0)  # default-height path keeps the current z
    assert w.data.qpos[q + 2] == pytest.approx(0.2)
    assert w.object_z("obstacle") == pytest.approx(0.25) and w.object_z("nope") is None


def test_reset_clears_force():
    w = kitbot()
    w.set_robot_force(100.0, 50.0)
    w.reset()
    assert not w.data.xfrc_applied.any()


# ---- root offset, start_pose validation, missing model --------------------------------------------------

def _root_world(**robot):
    from scrimmage.world import World
    root = {"name": "drive", "stock": "chassis", "pos": [0.5, 0, 0.2], "rot": [0, 0, 90]}
    return World({"part": [root], "robot": robot, "battery": {"resistance": 0.0}}, None)


def _heading(w):
    q = w.data.xquat[w.robot_body]
    return math.degrees(2 * math.atan2(q[3], q[0])) % 360


def test_reset_keeps_the_root_offset():
    w = _root_world(start_pose=[2, 4, 30])
    x, y = w.data.xpos[w.robot_body][:2]
    assert (x, y) == pytest.approx((2 + 0.5 * math.cos(math.radians(30)), 4 + 0.5 * math.sin(math.radians(30))))
    assert _heading(w) == pytest.approx(120)
    assert w.data.xpos[w.robot_body][2] == pytest.approx(0.2, abs=0.05)


def test_set_robot_pose_moves_the_start_pose_frame():
    w = _root_world(start_pose=[2, 4, 30])
    w.set_robot_pose(1.0, 1.0, 90.0)
    x, y = w.data.xpos[w.robot_body][:2]
    assert (x, y) == pytest.approx((1.0, 1.5), abs=1e-6)     # +0.5 along the new heading (+y)
    assert _heading(w) == pytest.approx(180)
    w.reset()
    assert _heading(w) == pytest.approx(120)


@pytest.mark.parametrize("bad", [[1, 1], [1, 2, 3, 4], "abc", [1, 2, float("nan")], [1, 2, "x"], [1, 2, True]])
def test_bad_start_pose_is_a_clear_error(bad):
    from scrimmage.world import World
    with pytest.raises(ValueError, match=r"\[robot\] start_pose"):
        _root_world(start_pose=bad)
    with pytest.raises(ValueError, match=r"\[robot\] start_pose"):
        World({"robot": {"start_pose": bad}, "model": {"mjcf": str(Path(__file__).parent / "models" / "kitbot.xml")}}, None)


def test_parts_robot_without_start_pose_spawns_at_the_default():
    root = {"name": "drive", "stock": "chassis"}
    from scrimmage.world import World
    w = World({"part": [root]}, None)
    assert w.data.xpos[w.robot_body][:2] == pytest.approx([2.0, 4.035])
    assert _heading(w) == pytest.approx(0)


def test_no_model_and_no_parts_is_a_clear_error(tmp_path):
    from scrimmage.world import World
    with pytest.raises(ValueError, match=r"describe the robot with \[\[part\]\].*\[model\] mjcf") as e:
        World({}, tmp_path)
    assert "did you mean" not in str(e.value)
    with pytest.raises(ValueError, match=r"did you mean \[\[part\]\]\?"):
        World({"parts": [{"name": "x"}]}, tmp_path)


def test_single_joint_equality_does_not_couple_the_motor_to_another_joint():
    import mujoco
    from scrimmage.bindings import _coupled_dofs
    xml = """<mujoco><worldbody>
      <body name="a"><joint name="locked" type="hinge" axis="0 0 1"/><geom size=".1"/></body>
      <body name="b" pos="1 0 0"><joint name="driven" type="hinge" axis="0 0 1"/><geom size=".1"/></body>
    </worldbody><equality><joint joint1="locked"/></equality></mujoco>"""
    model = mujoco.MjModel.from_xml_string(xml)
    assert model.eq_obj2id[0] == -1
    assert _coupled_dofs(model, "driven") == [(int(model.jnt_dofadr[1]), 1.0)]
    assert _coupled_dofs(model, "locked") == [(int(model.jnt_dofadr[0]), 1.0)]


def test_set_robot_pose_includes_the_root_bodys_own_pos_and_rot(tmp_path):
    from scrimmage.world import World
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "part.toml").write_text(
        '[part]\nname = "p"\nformat = 1\n[[body]]\nname = "b"\nmass = 5\npos = [0.3, 0, 0.1]\nrot = [0, 0, 45]\n'
        'geom = [{ box = [0.2, 0.2, 0.2], collide = false }]\n')
    w = World({"part": [{"name": "p", "path": "p", "pos": [0.5, -0.2, 0], "rot": [10, 0, 20]}],
               "robot": {"start_pose": [2, 4, 30]},
               "battery": {"resistance": 0.0}}, tmp_path)
    q = w.free_qadr
    want = w.data.qpos[q:q + 7].copy()
    w.set_robot_pose(1.0, 1.0, 90.0)
    w.set_robot_pose(2, 4, 30)
    assert w.data.qpos[q:q + 7] == pytest.approx(want, abs=1e-9)
