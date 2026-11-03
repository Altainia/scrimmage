"""The built-in ranges for moving elements and collisions: the 2026 TRENCH (2026-trench), the 2016 DEFENSES
(2016-defenses), and the 2020 CONTROL PANEL wheel in terrain-library."""

from __future__ import annotations

import math

import mujoco
import pytest

from range_fixtures import range_cfg
from scrimmage.parts.field import FIELD_BITS
from scrimmage.testrange.build_elements import ELEMENT_GROUP, add_element
from scrimmage.testrange.elements import IN, Box, Hinged, Sliding
from scrimmage.testrange.spec import load_range
from scrimmage.world import World

G = 9.81


def _element(rng: str, name: str):
    return next(e for e in load_range(rng, ".").elements if e.name == name)


def _alone(e):
    """A carpet and the one element, at a 1 ms step: its joint is the model's only one."""
    spec = mujoco.MjSpec()
    spec.option.timestep = 0.001
    spec.worldbody.add_geom(name="carpet", type=mujoco.mjtGeom.mjGEOM_PLANE, size=[10, 10, 0.1], group=ELEMENT_GROUP,
                            **FIELD_BITS)
    add_element(spec, e)
    m = spec.compile()
    return m, mujoco.MjData(m)


def _push(m, d, seconds: float, force: float) -> float:
    """A steady generalized force on the lone joint for `seconds`; its position after (rad or m from rest)."""
    for _ in range(round(seconds / m.opt.timestep)):
        d.qfrc_applied[0] = force
        mujoco.mj_step(m, d)
    return float(d.qpos[0])


def test_trench_clearance():
    rng = load_range("2026-trench", ".")
    arm = next(e for e in rng.elements if e.name == "trench_arm")
    assert isinstance(arm, Box)
    assert arm.base_height / IN == pytest.approx(22.25, abs=0.01)
    w = World(range_cfg(range={"file": "2026-trench", "start": "approach"}), None)   # the start isn't refused
    m, d = w.model, w.data
    mujoco.mj_forward(m, d)
    body = m.body("element.trench_arm").id
    (g,) = [g for g in range(m.ngeom) if m.geom_bodyid[g] == body]
    underside = d.geom_xpos[g][2] - m.geom_size[g][2]
    assert underside / IN == pytest.approx(22.25, abs=0.01)
    # the approach start is 1.5 m before the opening, centered on it, facing through it (+x)
    x, y, heading = rng.starts["approach"]
    near = arm.center[0] - arm.size[0] / 2.0
    assert heading == 0.0 and near - x == pytest.approx(1.5, abs=0.01) and y == pytest.approx(arm.center[1])
    # the opening between the side structures is the manual's 50.34 in, the robot's 0.9 m outline fits through
    sides = sorted((e for e in rng.elements if e.name.startswith("trench_side")), key=lambda e: e.center[1])
    gap = (sides[1].center[1] - sides[1].size[1] / 2.0) - (sides[0].center[1] + sides[0].size[1] / 2.0)
    assert gap / IN == pytest.approx(50.34, abs=0.01)


DEFENSE_JOINTS = {"portcullis": "slide", "sally_port": "hinge", "drawbridge": "hinge", "cheval_1": "hinge",
                  "cheval_2": "hinge", "cheval_3": "hinge", "cheval_4": "hinge"}


def test_defenses_build():
    rng = load_range("2016-defenses", ".")
    assert {"portcullis", "sally_port", "drawbridge", "cheval_de_frise"} <= set(rng.starts)
    w = World(range_cfg(range={"file": "2016-defenses", "start": "portcullis"}), None)
    for name, kind in DEFENSE_JOINTS.items():
        assert w.model.joint(f"element.{name}.{kind}") is not None
    for _ in range(500):            # everything settles on its stops or springs without flying apart
        mujoco.mj_step(w.model, w.data)
    for name, kind in DEFENSE_JOINTS.items():
        j = w.model.joint(f"element.{name}.{kind}")
        assert abs(w.data.qvel[j.dofadr[0]]) < 0.05, name

    # the portcullis: 5 lb (22.2 N) to lift, its weight (C2), within 5 %
    p = _element("2016-defenses", "portcullis")
    assert isinstance(p, Sliding) and p.axis == "z" and p.breakaway == 0.0
    lift = 5.0 * 4.4482
    assert p.mass * G == pytest.approx(lift, rel=0.05)
    m, d = _alone(p)
    assert _push(m, d, 1.0, 0.95 * lift) < 0.001        # 5 % under doesn't lift it
    mujoco.mj_resetData(m, d)
    assert _push(m, d, 1.0, 1.05 * lift) > 0.05         # 5 % over does

    # the sally port: 1.5 lb (6.7 N) at its outer edge, the door's width from the hinge, to open toward -x
    s = _element("2016-defenses", "sally_port")
    assert isinstance(s, Hinged) and s.hinge == "left"
    handle = s.size[1]
    assert s.breakaway / handle == pytest.approx(1.5 * 4.4482, rel=0.01)
    m, d = _alone(s)
    assert abs(math.degrees(_push(m, d, 1.0, -0.95 * s.breakaway))) < 0.2
    mujoco.mj_resetData(m, d)
    assert math.degrees(_push(m, d, 1.0, -1.05 * s.breakaway)) < -1.0

    # the drawbridge: upright on its bottom hinge, lowering toward -x; its spring stands it back up
    b = _element("2016-defenses", "drawbridge")
    assert isinstance(b, Hinged) and b.hinge == "bottom" and b.ground and b.range_deg == (-90.0, 0.0)
    m, d = _alone(b)
    d.qpos[0] = math.radians(-80.0)
    assert math.degrees(_push(m, d, 5.0, 0.0)) > -5.0

    # the cheval de frise: four plates on a horizontal center hinge, resting on alternate sides, pressed level
    plates = [_element("2016-defenses", f"cheval_{k}") for k in range(1, 5)]
    assert all(e.hinge == "center" and e.axis == "y" for e in plates)
    assert [math.copysign(1.0, e.rest_deg) for e in plates] == [1.0, -1.0, 1.0, -1.0]
    m, d = _alone(plates[0])
    level = -math.radians(plates[0].rest_deg)
    assert _push(m, d, 2.0, -3.0) < level             # a robot's press tilts it past level


def test_control_panel_spins():
    e = _element("terrain-library", "control_panel")
    assert isinstance(e, Hinged) and e.shape == "disc" and e.hinge == "center" and e.range_deg is None
    assert e.diameter / IN == pytest.approx(32.0) and e.base_height / IN == pytest.approx(30.25)
    m, d = _alone(e)
    mujoco.mj_forward(m, d)
    assert d.xaxis[0] == pytest.approx([0.0, 0.0, 1.0])        # vertical axis
    assert math.degrees(_push(m, d, 6.0, 2.0)) > 3 * 360.0     # three turns under a steady 2 N m


def test_test_setup_lists_them():
    from scrimmage.assembler.test_session import field_choices
    files = [f["file"] for f in field_choices()]
    assert "2026-trench" in files and "2016-defenses" in files
