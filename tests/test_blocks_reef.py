"""The reef field (a built-in range: three 2025 branches on a post, a coral on the carpet, a coral station) and the
grabber example on it (examples/blocks/reef): the field loads with its starts clear, a fed coral comes to rest on the
carpet, and the grabber closes on the floor coral and lifts it."""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import numpy as np
import pytest

from scrimmage.blocks.program import load
from scrimmage.blocks.run import TestRun, world_for
from scrimmage.protocol import HalState
from scrimmage.testrange.balls import BallState
from scrimmage.testrange.elements import IN, ROD_START_CLEARANCE, ROBOT_OUTLINE, Box, Rod, _on_floor, _rect, \
    _separated
from scrimmage.testrange.spec import BUILTIN_RANGES, load_range

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "blocks" / "reef"


def test_reef_is_a_built_in_range():
    assert "reef" in BUILTIN_RANGES and BUILTIN_RANGES["reef"].is_file()


def test_the_reef_field_loads_with_three_branches_and_a_station():
    rng = load_range("reef", ROOT)
    assert rng.name == "A reef branch to place coral on" and rng.piece_preset == "2025-coral"
    rods = {e.name: e for e in rng.elements if isinstance(e, Rod)}
    assert list(rods) == ["branch_l2", "branch_l3", "branch_l4"]
    assert [e.name for e in rng.elements if isinstance(e, Box)] == ["post"]
    for name, top_in in (("branch_l2", 31.875), ("branch_l3", 47.625)):
        rod = rods[name]
        rise = math.degrees(math.asin(rod.axis()[2]))
        assert rise == pytest.approx(35.0, abs=0.05)                       # angled up at 35 degrees
        assert rod.axis()[0] < 0                                            # rising toward the robot (-x)
        # its round end cap's top is the manual's highest point, inset 1 5/8 in from the base face (x = 5.0)
        r = rod.diameter / 2
        cap = np.array(rod.tip) - r * np.array(rod.axis())
        assert cap[2] + r == pytest.approx(top_in * IN, abs=1e-3)
        assert cap[0] == pytest.approx(5.0 + 1.625 * IN, abs=1e-3)
        assert rod.diameter == pytest.approx(1.66 * IN)
    l4 = rods["branch_l4"]
    assert l4.axis() == pytest.approx([0, 0, 1]) and l4.tip[2] == pytest.approx(72 * IN)
    # every branch is above a robot's head (P14): its lowest surface clears the start check's height
    assert all(min(e.base[2], e.tip[2]) - e.diameter / 2 > ROD_START_CLEARANCE for e in rods.values())
    (st,) = rng.stations
    assert st.name == "coral_station" and st.chute.slope_deg == 55.0
    assert st.kind_of(rng).label == "coral"


def test_the_reef_fields_starts_are_clear_and_the_coral_lies_in_line_ahead():
    rng = load_range("reef", ROOT)
    assert set(rng.starts) == {"facing_coral", "at_coral_station"}
    for x, y, heading in rng.starts.values():
        outline = _rect((x, y), heading, ROBOT_OUTLINE, ROBOT_OUTLINE)
        assert all(_separated(outline, f) for e in rng.elements for f in _on_floor(e, start=True))
    x, y, heading = rng.starts["facing_coral"]
    (cx, cy), = rng.floor_balls
    assert heading == 0.0 and cy == y and cx - x == pytest.approx(0.63)
    # the station's start faces it, its bumpers at the chute's low end
    st = rng.stations[0]
    x, y, heading = rng.starts["at_coral_station"]
    low_x = st.release[0] + st.chute.length * math.cos(math.radians(st.chute.slope_deg))
    assert heading == 180.0 and y == st.release[1] and x - ROBOT_OUTLINE / 2 == pytest.approx(low_x, abs=1e-3)


def reef_world():
    from range_fixtures import range_cfg
    from scrimmage.world import World

    return World(range_cfg(range={"file": "reef", "floor_balls": True}), None)


def advance(w, seconds: float) -> None:
    for _ in range(int(round(seconds / w.model.opt.timestep))):
        w.step(HalState())


def coral_axis(w, i: int) -> np.ndarray:
    b = w.test_range.balls
    return w.data.xmat[b.bodies[i]].reshape(3, 3) @ np.array(w.test_range.range.kinds["2025-coral"].axis)


def test_the_floor_coral_rests_on_the_carpet_lying_in_line():
    w = reef_world()
    advance(w, 1.0)
    b = w.test_range.balls
    x, y, z = b.positions()[0]
    assert (x, y) == pytest.approx((2.63, 4.0), abs=0.01) and z == pytest.approx(4.5 * IN / 2, abs=0.01)
    assert abs(coral_axis(w, 0)[0]) > 0.99                 # in line with the start's heading, along x


def test_a_coral_fed_at_the_coral_station_comes_to_rest_on_the_carpet():
    w = reef_world()
    w.set_robot_pose(4.0, 6.5, 0.0)                         # out of the coral's way
    st = w.test_range.range.stations[0]
    w.test_range.feeder.request("coral_station")
    advance(w, 4.0)
    i = w.test_range.feeder.fed[0].ball
    b = w.test_range.balls
    x, y, z = b.positions()[i]
    low_x = st.release[0] + st.chute.length * math.cos(math.radians(st.chute.slope_deg))
    assert b.state[i] == BallState.FIELD and x > low_x and abs(y - 1.5) < 1.0
    assert z == pytest.approx(4.5 * IN / 2, abs=0.01) and abs(coral_axis(w, i)[2]) < 0.05   # lying on its side
    assert np.linalg.norm(w.data.cvel[b.bodies[i]][3:]) < 0.01                               # and at rest


def squeeze(volts: float) -> str:
    return "".join(f'\n[[block]]\ndo = "spin"\nmotor = "grabber.{jaw}"\nvolts = {volts}\n'
                   for jaw in ("jaw_left", "jaw_right"))


def move_arm(deg: float) -> str:
    return f'\n[[block]]\ndo = "move"\njoint = "grabber.arm"\nto = {deg}\nunit = "deg"\n'


def wait(seconds: float) -> str:
    return f'\n[[block]]\ndo = "wait"\nseconds = {seconds}\n'


LIFT = (squeeze(4.0) + wait(1.0) + move_arm(-15) + wait(0.5)
        + '\n[[block]]\ndo = "expect"\nthat = { held = { piece = "any", by = "grabber" } }\nthroughout = true\n'
        + move_arm(-90) + wait(0.5))


def test_the_grabber_closes_on_the_coral_and_lifts_it(tmp_path):
    folder = tmp_path / "reef"
    shutil.copytree(EXAMPLE, folder)
    path = folder / "lift.test.toml"
    path.write_text('[test]\nrobot = "grabber-standin.toml"\nchart = ["grabber.arm"]\n' + LIFT, encoding="utf-8")
    program = load(path)
    world = world_for(program)
    r = TestRun(world, program).run()
    assert r.status == "finished" and r.checks[-1]["passed"], r.outcome
    assert r.checks[-1]["text"] == "a coral is held by grabber"
    assert min(r.signals["grabber.arm"]) < -85.0                             # it really swung
    assert world.test_range.balls.positions()[0][2] > 0.4                    # the coral is up off the carpet


def test_a_new_test_of_the_grabber_starts_with_the_coral_held(tmp_path):
    """The starter blocks a new test of the reef example gets (test_session.starter_blocks) close the jaws, lift the
    coral and finish with it held throughout: Test it reads "held" without editing (ruling P24)."""
    from scrimmage.assembler import test_session as ts
    from scrimmage.blocks.palette import palette
    from scrimmage.blocks.program import Program, dumps
    from scrimmage.config import build_world, load_config
    robot = EXAMPLE / "grabber-standin.toml"
    world = build_world(load_config(robot))
    pal = palette(world, [])
    path = tmp_path / "g.test.toml"
    path.write_text(dumps(Program(path, robot, blocks=ts.starter_blocks(pal),
                                  hopper=ts.starter_hopper(pal, ts.part_roots(world)))), encoding="utf-8")
    program = load(path)
    assert program.blocks[0].do == "spin" and program.hopper == ["grabber.jaw_left", "grabber.jaw_right"]
    result = TestRun(world_for(program), program).run()
    assert result.status == "finished" and result.checks and all(c["passed"] for c in result.checks), result.outcome
    assert result.checks[-1]["throughout"]
