"""The shared motor drive (scrimmage/hal_drive.py) and the stand-in box robot."""

from pathlib import Path

import pytest

from scrimmage.assembler import templates
from scrimmage.config import build_world, load_config
from scrimmage.hal_drive import driven_joints, position_volts
from scrimmage.parts.model import PartError
from scrimmage.parts.stock import build_stock

ROOT = Path(__file__).resolve().parent.parent


def test_position_volts():
    assert position_volts(1.0, 0.0, False, 12) == 12
    assert position_volts(-1.0, 0.0, False, 12) == -12
    assert position_volts(0.01, 0.0, False, 12) == pytest.approx(4.0)
    assert position_volts(0.1, 0.0, True, 12) == pytest.approx(6.0)


def test_driven_joints_of_the_climber_robot():
    world = build_world(load_config(ROOT / "configs" / "climber_robot.toml"))
    joints = driven_joints(world)
    assert "mid.stage1" in joints and "high.pivot" in joints


def test_chassis_com_offset():
    base = build_stock("chassis", {}, "w").bodies[0].com
    moved = build_stock("chassis", {"com_offset": [0.1, 0, 0.05]}, "w").bodies[0].com
    assert tuple(base) == pytest.approx((0, 0, 0.18))
    assert tuple(moved) == pytest.approx((0.1, 0, 0.12 + 0.11))
    with pytest.raises(PartError, match="com_offset"):
        build_stock("chassis", {"com_offset": [0.1, 0]}, "w")


def test_standin_template_assembles(tmp_path):
    cfg = tmp_path / "standin.toml"
    cfg.write_text(templates.new_config("standin"), encoding="utf-8")
    world = build_world(load_config(cfg))
    assert world.model.nbody > 1
    assert "Stand-in box robot" in cfg.read_text(encoding="utf-8")
