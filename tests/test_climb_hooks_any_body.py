"""A body on a CAD part counts as a hook (ClimbMonitor's hook_bodies); the generic ladder ranges, round and square."""

from __future__ import annotations

import mujoco
import pytest

from range_fixtures import drive, range_cfg
from scrimmage.protocol import HalState
from scrimmage.testrange.elements import IN, Bar
from scrimmage.testrange.spec import load_range
from scrimmage.terrain.climb import ClimbMonitor
from scrimmage.world import World

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
pos = [1.0, 0.0, %(z)s]
mass = 0.5
geom = [{ box = [0.1, 0.1, 0.1] }]
"""


def ladder_world(folder, file="ladder", z=0.0):
    """A kitbot with a hand-written `arm` part (in `folder`) whose `paw` box is 1.0 m ahead of the robot's top
    mount, `z` above it."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "arm").mkdir(exist_ok=True)
    (folder / "arm" / "part.toml").write_text(PAW_PART % {"z": z}, encoding="utf-8")
    cfg = range_cfg(range={"file": file, "start": "below"})
    cfg["part"] = cfg["part"] + [{"name": "arm", "path": "arm", "attach": "drive.top"}]
    return World(cfg, folder)


# How far the paw starts below rung1's top. The robot stands on its wheels as well, so the rung lifts the front and
# the paw settles nearer the top than this. At 5 mm the paw ended a few micrometers into the rung and bounced in and
# out of contact while it settled: whether it touched at the last step was down to rounding, and on Windows it
# didn't. At 20 mm it stays about 0.5 mm in and carries hundreds of newtons.
PUSH = 0.02


@pytest.fixture
def world(tmp_path):
    """The paw pressed onto rung1: its bottom PUSH below the rung's top (24 in), measured in the same world."""
    probe = ladder_world(tmp_path / "probe")
    mujoco.mj_forward(probe.model, probe.data)
    top = float(probe.data.xpos[probe.model.body("arm.base").id][2])
    w = ladder_world(tmp_path / "real", z=24 * IN - PUSH + 0.05 - top)
    mujoco.mj_forward(w.model, w.data)
    g = w.model.geom("element.rung1.pipe").id
    rung_top = float(w.data.geom_xpos[g][2]) + float(w.model.geom_size[g][0])
    paw_bottom = float(w.data.xpos[w.model.body("arm.paw").id][2]) - 0.05
    assert rung_top - paw_bottom == pytest.approx(PUSH, abs=1e-6)
    return w


def settle_and_watch(world, mon, seconds=0.1):
    """Step `world` 0.1 s to settle, then `seconds` more; mon.contact_state() after each of those later steps."""
    drive(world, 0.1)
    hal = HalState()
    states = []
    for _ in range(int(seconds / world.model.opt.timestep)):
        world.step(hal)
        states.append(mon.contact_state())
    return states


def paw_depth(world):
    """How far (m) the paw is into rung1: the deepest contact between them, 0 when they don't touch."""
    m, d = world.model, world.data
    pair = {m.body("arm.paw").id, m.body("element.rung1").id}
    return max([-d.contact[i].dist for i in range(d.ncon)
                if {int(m.geom_bodyid[d.contact[i].geom1]), int(m.geom_bodyid[d.contact[i].geom2])} == pair],
               default=0.0)


def test_a_body_counts_as_a_hook(world):
    mon = world.climb_monitor(["arm.paw"])
    assert mon.hooks == ["arm.paw"]
    assert "climb.hook.arm.paw.load" in mon.sample()
    states = settle_and_watch(world, mon)
    # the paw is a hook on rung1 at every step, not just at the last one, and well into it
    assert all(hooked == {"rung1": {"arm.paw"}} and not others for _, _, hooked, others in states)
    assert paw_depth(world) > 1e-4
    assert mon.sample()["climb.hook.arm.paw.load"] > 50.0
    # the paw is part of the hook assembly: it may touch a bar
    paw = [g for g in range(world.model.ngeom) if world.model.body(world.model.geom_bodyid[g]).name == "arm.paw"]
    assert paw and all(mon.is_hook_assembly(g) for g in paw)


def test_without_hook_bodies_the_body_is_not_a_hook(world):
    mon = world.climb_monitor()
    assert mon.hooks == []
    states = settle_and_watch(world, mon)
    assert all(touched == {"rung1"} and hooked == {} for _, touched, hooked, _ in states)
    assert all({name for _, name, _ in others} == {"rung1"} for _, _, _, others in states)
    assert paw_depth(world) > 1e-4


def test_climb_monitor_rebuilds_for_a_different_list(world):
    first = world.climb_monitor()
    assert world.climb_monitor() is first
    second = world.climb_monitor(["arm.paw"])
    assert second is not first and world.climb_monitor(["arm.paw"]) is second
    assert world.climb_monitor() is second          # no argument keeps what is there
    assert world.climb_monitor([]).hooks == []


def test_an_unknown_hook_body_names_itself(world):
    with pytest.raises(ValueError, match=r"arm\.nope.*arm\.paw"):
        ClimbMonitor(world, ["arm.nope"])


@pytest.mark.parametrize("name,section,side", [("ladder", "round", 1.66), ("ladder-square", "square", 1.5)])
def test_ladder_ranges_build(name, section, side):
    rng = load_range(name, ".")
    assert rng.climb is None and len(rng.floor_balls) == 0
    bars = [e for e in rng.elements if isinstance(e, Bar)]
    assert [b.name for b in bars] == ["rung1", "rung2", "rung3", "rung4"]
    assert all(b.section == section and b.diameter == pytest.approx(side * IN) for b in bars)
    assert all(b.length == pytest.approx(30 * IN) for b in bars)
    w = World(range_cfg(range={"file": name, "start": "below"}), None)
    m, d = w.model, w.data
    mujoco.mj_forward(m, d)
    for b, top in zip(bars, (24, 36, 48, 60)):
        g = m.geom(f"element.{b.name}.pipe").id
        assert float(d.geom_xpos[g][2]) + float(m.geom_size[g][0]) == pytest.approx(top * IN)
        assert m.geom_type[g] == (mujoco.mjtGeom.mjGEOM_BOX if section == "square" else mujoco.mjtGeom.mjGEOM_CAPSULE)
    assert set(w.parts.layout.bars) == {"rung1", "rung2", "rung3", "rung4"}
