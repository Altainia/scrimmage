"""The conditions a test's blocks wait for and check, watched every physics step (scrimmage/blocks/conditions.py)."""

from __future__ import annotations

import math
import tomllib
from pathlib import Path

import mujoco
import pytest

from range_fixtures import range_cfg, spares_world
from scrimmage.assembler.templates import new_config
from scrimmage.blocks.conditions import Watch, describe
from scrimmage.blocks.program import Cond, Program
from scrimmage.protocol import HalState
from scrimmage.world import World

IN = 0.0254
PAW_PART = """\
[part]
name = "arm"
format = 1

[[body]]
name = "base"
mass = 0.5
geom = [{ box = [0.05, 0.05, 0.05], collide = false }]

[[body]]
name = "paw"
parent = "base"
pos = [%(x)s, 0.0, %(z)s]
mass = 0.5
geom = [{ box = [0.1, 0.1, 0.1] }]
"""


def program(hooks=(), blocks=()) -> Program:
    return Program(Path("/tmp/x.test.toml"), Path("r.toml"), list(hooks), blocks=list(blocks))


def tilt(above=None, below=None) -> Cond:
    return Cond("tilt", {"above": above} if above is not None else {"below": below})


@pytest.fixture(scope="module")
def standin():
    cfg = tomllib.loads(new_config("standin"))
    cfg["part"].append({"name": "climber", "stock": "climber", "attach": "drive.top", "ids": {"extend": "PWM:2"}})
    return World(cfg, None)


def test_tilt(standin):
    w = standin
    w.reset()
    q = w.free_qadr
    h = math.radians(30.0) / 2
    w.data.qpos[q + 3:q + 7] = [math.cos(h), math.sin(h), 0.0, 0.0]      # 30 deg about x
    mujoco.mj_forward(w.model, w.data)
    watch = Watch(w, program())
    watch.update()
    assert watch.tilt == pytest.approx(30.0, abs=1e-6)
    assert watch.holds(tilt(above=20.0)) and not watch.holds(tilt(below=20.0))
    w.reset()
    watch.update()
    assert watch.holds(tilt(below=20.0)) and not watch.holds(tilt(above=20.0))


def test_joint(standin):
    w = standin
    w.reset()
    m = w.model
    w.data.qpos[m.jnt_qposadr[m.joint("climber.stage1").id]] = 4 * IN
    mujoco.mj_forward(m, w.data)
    watch = Watch(w, program())
    assert watch.holds(Cond("joint", {"name": "climber.stage1", "above": 3.0, "unit": "in"}))
    assert not watch.holds(Cond("joint", {"name": "climber.stage1", "below": 3.0, "unit": "in"}))
    assert watch.holds(Cond("joint", {"name": "climber.stage1", "below": 0.11, "unit": "m"}))
    w.reset()


def test_hinge_joint_in_degrees(standin):
    w = standin
    w.reset()
    m = w.model
    w.data.qpos[m.jnt_qposadr[m.joint("climber.latch0").id]] = math.radians(30.0)
    mujoco.mj_forward(m, w.data)
    watch = Watch(w, program())
    assert watch.holds(Cond("joint", {"name": "climber.latch0", "above": 20.0, "unit": "deg"}))
    assert not watch.holds(Cond("joint", {"name": "climber.latch0", "below": 20.0, "unit": "deg"}))
    assert not watch.holds(Cond("joint", {"name": "climber.latch0", "above": 40.0, "unit": "deg"}))
    w.reset()


def paw_world(folder, x=0.0, z=0.3) -> World:
    """A small box robot (no wheels) on the ladder with a hand-written `arm` part whose `paw` box (0.1 m, 0.5 kg) is
    (x, 0, z) from the robot's top mount."""
    (folder / "arm").mkdir(parents=True)
    (folder / "arm" / "part.toml").write_text(PAW_PART % {"x": x, "z": z}, encoding="utf-8")
    cfg = range_cfg(range={"file": "ladder", "start": "below"},
                    part=[{"name": "drive", "stock": "chassis", "length": 0.4, "width": 0.4},
                          {"name": "arm", "path": "arm", "attach": "drive.top"}])
    return World(cfg, folder)


def paw_geom(w) -> int:
    m = w.model
    return next(g for g in range(m.ngeom) if m.body(m.geom_bodyid[g]).name == "arm.paw")


def step(w, watch, seconds):
    hal = HalState()
    for _ in range(round(seconds / w.model.opt.timestep)):
        w.step(hal)
        watch.update()
        yield


@pytest.fixture(scope="module")
def hanging(tmp_path_factory):
    """The box robot, its `arm.paw` right over its center, put 5 cm above rung1 so it drops onto it and hangs there,
    every bit of its weight on the paw."""
    w = paw_world(tmp_path_factory.mktemp("hang"))
    m, d = w.model, w.data
    q = w.free_qadr
    d.qpos[q] += 1.0                                # under the rungs (1.0 m ahead of the start)
    mujoco.mj_forward(m, d)
    paw = paw_geom(w)
    d.qpos[q + 2] += 24 * IN + 0.05 - (float(d.geom_xpos[paw][2]) - float(m.geom_size[paw][2]))
    mujoco.mj_forward(m, d)
    return w


def test_next_bar_follows_the_bars_hung_from(hanging):
    w = hanging
    watch = Watch(w, program(hooks=["arm.paw"]))
    assert watch.weight == pytest.approx(float(w.model.body_subtreemass[w.robot_body]) * 9.81)
    assert watch.next_bar() == "rung1" and watch.hung == []
    hang1 = Cond("hanging_from", {"bar": "rung1"})
    hang_next = Cond("hanging_from", {"bar": "next bar"})
    touch1 = Cond("touch", {"robot": "hook", "field": "rung1"})
    touch_next = Cond("touch", {"robot": "arm", "field": "next bar"})
    seen_touch_before_hang = False
    for _ in step(w, watch, 0.5):
        if watch.holds(touch1) and not watch.holds(hang1):
            seen_touch_before_hang = True
            assert watch.holds(touch_next)          # rung1 is the next bar until the robot hangs from it
    assert seen_touch_before_hang
    assert watch.holds(hang1) and watch.hung == ["rung1"]
    assert watch.load["rung1"] == pytest.approx(watch.weight, rel=0.02)     # at rest, the whole weight
    assert watch.next_bar() == "rung2"
    assert not watch.holds(hang_next) and not watch.holds(touch_next)
    assert watch.holds(touch1)
    assert not watch.holds(Cond("hanging_from", {"bar": "rung2"}))
    # any bar: rung1 is one; a bar the paw doesn't touch is not
    assert watch.holds(Cond("touch", {"robot": "arm", "field": "any bar"}))
    assert not watch.holds(Cond("touch", {"robot": "arm", "field": "rung3"}))


def test_a_touch_on_a_field_body_counts_all_of_its_geoms():
    """A hub is one field body with numbered geoms (hub_blue.body_0, .funnel_0, .net...): touching it by name is any
    contact with any of them."""
    w = spares_world(range_cfg())
    watch = Watch(w, program())
    m = w.model
    hub = m.body("hub_blue").id
    geoms = watch._field_side("hub_blue")
    assert geoms == {g for g in range(m.ngeom) if int(m.geom_bodyid[g]) == hub} and len(geoms) > 3
    chassis = next(iter(watch._robot_side("drive")))
    cond = Cond("touch", {"robot": "drive", "field": "hub_blue"})
    for g in sorted(geoms):
        watch._pairs = {(chassis, g)}
        assert watch.holds(cond), m.geom(g).name
    watch._pairs = set()
    assert not watch.holds(cond)


def test_piece_inside_a_body():
    w = spares_world(range_cfg())
    m, d = w.model, w.data
    watch = Watch(w, program())
    inside = Cond("piece_in", {"body": "drive.chassis", "piece": "any"})
    assert not watch.holds(inside)
    ball = w.parts.layout.ball_names[0]
    q = int(m.jnt_qposadr[m.body_jntadr[m.body(ball).id]])
    d.qpos[q:q + 3] = d.xpos[w.robot_body] + d.xmat[w.robot_body].reshape(3, 3) @ [0.0, 0.0, 0.08]  # in the frame
    mujoco.mj_forward(m, d)
    assert watch.holds(inside) and watch.holds(Cond("piece_in", {"body": "drive.chassis", "piece": ball}))
    other = w.parts.layout.ball_names[1]
    assert not watch.holds(Cond("piece_in", {"body": "drive.chassis", "piece": other}))


def test_describe():
    assert describe(Cond("touch", {"robot": "hook", "field": "any bar"}), next_bar="rung3") == "a hook touches any bar"
    assert describe(Cond("touch", {"robot": "arm.paw", "field": "any bar"})) == "arm.paw touches any bar"
    assert describe(Cond("touch", {"robot": "hook", "field": "rung2"})) == "a hook touches rung2"
    assert describe(tilt(above=20.0)) == "the robot tilts more than 20°"
    assert describe(Cond("joint", {"name": "climber.arm", "above": 40.0, "unit": "deg"})) == "climber.arm passes 40°"
    assert describe(Cond("piece_in", {"body": "intake.hopper", "piece": "any"})) == "a ball reaches intake.hopper"
    assert describe(Cond("hanging_from", {"bar": "rung4"})) == "the robot hangs from rung4"
    assert describe(Cond("hanging_from", {"bar": "next bar"})) == "the robot hangs from the next bar"
    assert describe(Cond("touch", {"robot": "hook", "field": "next bar"}), next_bar="rung3") == "a hook touches rung3"
    assert describe(tilt(below=5.0)) == "the robot tilts less than 5°"
    assert describe(Cond("joint", {"name": "lift.stage1", "below": 2.5, "unit": "in"})) == \
        "lift.stage1 goes below 2.5 in"
    assert describe(Cond("level", {"level": "HIGH"})) == "the robot hangs at HIGH"


def test_a_push_into_a_bar_from_the_side_is_no_hang(tmp_path):
    """The box robot on the floor, its paw at rung1's height just short of it, pushed forward into the rung at 400 N:
    the paw presses the rung hard (the climb monitor's hook load, every direction, is well over a quarter of the
    weight) but holds nothing up from it for long: the contact's friction carries a moment's spike as the paw hits."""
    probe = paw_world(tmp_path / "probe", x=0.0, z=0.0)
    mujoco.mj_forward(probe.model, probe.data)
    rung = probe.model.geom("element.rung1.pipe").id
    paw = probe.data.geom_xpos[paw_geom(probe)]
    rung_at = probe.data.geom_xpos[rung]
    x = float(rung_at[0] - paw[0]) - 0.83 * IN - 0.05 - 0.01         # the paw's face 1 cm short of the rung
    w = paw_world(tmp_path / "real", x=x, z=float(rung_at[2] - paw[2]))
    watch = Watch(w, program(hooks=["arm.paw"]))
    w.set_robot_force(400.0, 0.0)
    pressed = 0.0
    for _ in step(w, watch, 0.6):
        assert not watch.hanging
        if watch.climb["climb.hook.arm.paw.load"] > 0.25 * watch.weight:
            pressed += w.model.opt.timestep
    assert pressed > 0.3 and watch.holds(Cond("touch", {"robot": "arm.paw", "field": "rung1"}))
    assert watch.mon.contact_state()[0]                                 # on the floor all along
    assert watch.hung == [] and watch.next_bar() == "rung1"


def test_a_hook_carrying_less_than_a_quarter_is_no_hang(tmp_path):
    """The box robot on the floor with its paw 1.5 m ahead, resting on rung1's top: the rung carries part of its
    weight, less than a quarter, for well over 0.1 s."""
    probe = paw_world(tmp_path / "probe", x=1.5, z=0.0)
    mujoco.mj_forward(probe.model, probe.data)
    g = paw_geom(probe)
    z = 24 * IN - 0.002 - (float(probe.data.geom_xpos[g][2]) - float(probe.model.geom_size[g][2]))
    w = paw_world(tmp_path / "real", x=1.5, z=z)
    m, d = w.model, w.data
    d.qpos[w.free_qadr] -= 0.5                     # back, so the paw is over the rung
    mujoco.mj_forward(m, d)
    watch = Watch(w, program(hooks=["arm.paw"]))
    carried = 0.0
    for i, _ in enumerate(step(w, watch, 0.6)):
        if i * m.opt.timestep < 0.2:
            continue                                # settling onto the floor and the rung
        assert 0.05 * watch.weight < watch.load["rung1"] < 0.25 * watch.weight
        carried += m.opt.timestep
    assert carried > 0.3
    assert not watch.holds(Cond("hanging_from", {"bar": "rung1"})) and watch.hung == []
