import tomllib
from pathlib import Path

import mujoco
import numpy as np
import pytest

from scrimmage.assembler.view import model_view
from scrimmage.movers import Mover, mover_poses, movers
from scrimmage.protocol import HalState
from scrimmage.session import Session, SessionError
from scrimmage.world import World
from test_world_pokes import ball_world

TESTS = Path(__file__).resolve().parent
WHEELS = ["left_front", "left_center", "left_back", "right_front", "right_center", "right_back"]


def fake_world() -> World:
    return World(tomllib.loads((TESTS / "fake_robot.toml").read_text()), TESTS)


def compose(pose, lpos, lquat):
    pos, q = np.zeros(3), np.zeros(4)
    mujoco.mju_rotVecQuat(pos, np.asarray(lpos, float), np.asarray(pose[3:7], float))
    mujoco.mju_mulQuat(q, np.asarray(pose[3:7], float), np.asarray(lquat, float))
    return np.asarray(pose[:3], float) + pos, q


def test_movers_of_the_robot_only_world():
    w = fake_world()
    ms = movers(w)
    bodies = [m for m in ms if m.kind == "body"]
    assert [m.name for m in bodies] == ["chassis"] + WHEELS      # the robot's bodies only: no game pieces here
    assert ms[:len(bodies)] == bodies
    # the kitbot field's named static geoms (obstacle, walls) are movable objects too, so they follow as geom movers
    assert sorted(m.name for m in ms if m.kind == "geom") == [n for n in w.movable_objects() if n not in WHEELS + ["chassis"]]
    assert [m.id for m in bodies] == sorted(m.id for m in bodies)


def test_movers_with_free_ball_and_static_obstacle(tmp_path):
    w = ball_world(tmp_path)
    ms = movers(w)
    names = [(m.kind, m.name) for m in ms]
    assert ("body", "ball") in names and ("geom", "obstacle") in names
    first_geom = next(i for i, m in enumerate(ms) if m.kind == "geom")
    assert all(m.kind == "body" for m in ms[:first_geom]) and all(m.kind == "geom" for m in ms[first_geom:])
    geom_names = [m.name for m in ms[first_geom:]]
    assert geom_names == sorted(geom_names) and "carpet" not in geom_names
    obstacle = ms[names.index(("geom", "obstacle"))]
    assert obstacle.id == w.model.geom("obstacle").id


def test_mover_poses_match_data(tmp_path):
    w = ball_world(tmp_path)
    hal = HalState()
    for _ in range(20):
        w.step(hal)
    ms = movers(w)
    p = mover_poses(w, ms)
    assert p.shape == (len(ms), 7) and p.dtype == np.float32
    for row, m in zip(p, ms):
        if m.kind == "body":
            want = np.concatenate([w.data.xpos[m.id], w.data.xquat[m.id]])
        else:
            q = np.zeros(4)
            mujoco.mju_mat2Quat(q, w.data.geom_xmat[m.id])
            want = np.concatenate([w.data.geom_xpos[m.id], q])
        assert row == pytest.approx(want, abs=1e-6)


def test_view_lists_movers_and_composes_to_world_pose(tmp_path):
    w = ball_world(tmp_path)
    v = model_view(w, include_world=True)
    ms = movers(w)
    assert v["movers"] == [{"kind": m.kind, "name": m.name} for m in ms]
    poses = mover_poses(w, ms)
    assert v["geoms"]
    for g in v["geoms"]:
        pos, q = compose(poses[g["mover"]], g["lpos"], g["lquat"])
        assert pos == pytest.approx(g["pos"], abs=1e-5)
        assert q == pytest.approx(g["quat"], abs=1e-5) or q == pytest.approx(-np.array(g["quat"]), abs=1e-5)
    names = [(m.kind, m.name) for m in ms]
    ball_idx = names.index(("body", "ball"))
    obstacle_idx = names.index(("geom", "obstacle"))
    by_name = {g["name"]: g for g in v["world_geoms"] if g["name"]}
    assert by_name["carpet"]["mover"] == -1 and by_name["carpet"]["lpos"] == [0, 0, 0]
    assert by_name["obstacle"]["mover"] == obstacle_idx and by_name["obstacle"]["lquat"] == [1, 0, 0, 0]
    ball = [g for g in v["world_geoms"] if g["mover"] == ball_idx]
    assert len(ball) == 1 and ball[0]["type"] == "sphere"
    pos, _ = compose(poses[ball_idx], ball[0]["lpos"], ball[0]["lquat"])
    assert pos == pytest.approx(ball[0]["pos"], abs=1e-5)
    assert v["movable"] == w.movable_objects() and "ball" in v["movable"]
    assert "movable" not in model_view(w)


def test_reset_robot_restores_pose_and_mechanisms_but_not_field_objects(tmp_path):
    w = ball_world(tmp_path)
    w.move_object("ball", 6, 6, 0.2, 0)
    ball_q = w.model.jnt_qposadr[w.model.body_jntadr[w.model.body("ball").id]]
    w.set_robot_pose(5.0, 3.0, 90.0)
    hal = HalState()
    for _ in range(50):
        w.step(hal)
    w.data.qvel[:] = 0.3
    w.reset_robot()
    robot_dofs = [j for j in range(w.model.njnt)
                  if w.model.body_rootid[w.model.jnt_bodyid[j]] == w.model.body_rootid[w.robot_body]]
    for j in robot_dofs:
        a, n = int(w.model.jnt_qposadr[j]), 7 if w.model.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE else 1
        assert w.data.qpos[a:a + n] == pytest.approx(w.model.qpos0[a:a + n])
        d = int(w.model.jnt_dofadr[j])
        assert not w.data.qvel[d:d + (6 if n == 7 else 1)].any()
    assert list(w.data.qpos[ball_q:ball_q + 2]) == pytest.approx([6, 6], abs=1e-3)   # it fell a little, untouched by the reset


def test_session_set_speed_validates():
    s = Session(TESTS / "fake_robot.toml", nt=False)
    with pytest.raises(SessionError, match="each step would take"):
        s.set_speed(0.001)
    for bad in (0, -1, float("nan"), float("inf"), True, "2"):
        with pytest.raises(SessionError):
            s.set_speed(bad)
    s.set_speed(2.0)
    assert s.speed == 2.0
    s.set_speed(None)
    assert s.speed is None


def test_view_root_mover_is_the_robot_body():
    from scrimmage.config import build_world, load_config
    root = TESTS.parent
    for w in (fake_world(), build_world(load_config(root / "configs" / "swerve.toml"))):
        v = model_view(w)
        name = mujoco.mj_id2name(w.model, mujoco.mjtObj.mjOBJ_BODY, w.robot_body)
        assert v["movers"][v["root_mover"]] == {"kind": "body", "name": name}


def test_reset_robot_applies_the_configs_start_pose():
    raw = tomllib.loads((TESTS / "fake_robot.toml").read_text())
    raw["robot"]["start_pose"] = [5.0, 2.0, 90.0]     # not the MJCF's pose
    w = World(raw, TESTS)
    w.reset()
    q = w.free_qadr
    start = w.data.qpos[q:q + 7].copy()
    assert list(start[:2]) == pytest.approx([5.0, 2.0])
    w.set_robot_pose(1.0, 1.0, 0.0)
    hal = HalState()
    for _ in range(20):
        w.step(hal)
    w.reset_robot()
    assert list(w.data.qpos[q:q + 7]) == pytest.approx(list(start))
    assert not w.data.qvel[w.model.jnt_dofadr[w.model.body_jntadr[w.robot_body]]:][:6].any()
