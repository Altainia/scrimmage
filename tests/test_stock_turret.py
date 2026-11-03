"""The stock turret: a platform on a vertical hinge that carries a launcher."""

from __future__ import annotations

import math

import mujoco
import numpy as np
import pytest

from scrimmage.parts.assemble import assemble
from scrimmage.parts.model import PartError
from scrimmage.parts.stock import build_stock


def assembled(**params):
    cfg = {"part": [{"name": "drive", "stock": "chassis"},
                    {"name": "turret", "stock": "turret", "attach": "drive.top", **params},
                    {"name": "launcher", "stock": "launcher", "attach": "turret.top"}]}
    return assemble(cfg, ".")


def test_turret_builds():
    built = assembled()
    m = built.model
    j = m.joint("turret.turn")
    assert j.type[0] == mujoco.mjtJoint.mjJNT_HINGE
    assert np.allclose(j.axis, [0, 0, 1])
    assert np.allclose(j.range, [-math.pi, math.pi])
    assert build_stock("turret", {}, "t").slot_kinds() == {"turn": "motor", "encoder": "encoder"}


def test_turret_turns_the_launcher():
    built = assembled()
    m, d = built.model, mujoco.MjData(built.model)
    d.qpos[m.joint("turret.turn").qposadr[0]] = math.pi / 2
    mujoco.mj_kinematics(m, d)
    x_axis = d.xmat[m.body("launcher.base").id].reshape(3, 3)[:, 0]
    assert np.allclose(x_axis, [0, 1, 0], atol=1e-9)


def test_turret_range_limit():
    with pytest.raises(PartError, match="360"):
        assembled(range=[-400, 0])
