"""Hinged and sliding field elements: a panel on a hinge (a door, a flap, a spinning panel) or on a slide (a
portcullis, a touchpad), parsed from a range file and built in MuJoCo."""

from __future__ import annotations

import math
import tomllib
from pathlib import Path

import mujoco
import numpy as np
import pytest

from scrimmage.parts.field import FIELD_BITS
from scrimmage.testrange.build_elements import ELEMENT_GROUP, add_element, panel_parts
from scrimmage.testrange.elements import IN, Hinged, Sliding
from scrimmage.testrange.spec import RangeError, load_range, parse_range

HEAD = '[range]\nname = "t"\nball = "2026-fuel"\n\n[[element]]\nname = "{name}"\nkind = "{kind}"\ncenter = [4.0, 3.0]\n'


def _range(kind: str, body: str, name: str = "e"):
    return parse_range(tomllib.loads(HEAD.format(name=name, kind=kind) + body), Path("t.toml"))


def _element(kind: str, body: str, name: str = "e"):
    return next(e for e in _range(kind, body, name).elements if e.name == name)


def _world(e):
    spec = mujoco.MjSpec()
    spec.option.timestep = 0.001
    spec.worldbody.add_geom(name="carpet", type=mujoco.mjtGeom.mjGEOM_PLANE, size=[10, 10, 0.1], group=ELEMENT_GROUP,
                            **FIELD_BITS)
    add_element(spec, e)
    m = spec.compile()
    return m, mujoco.MjData(m)


def _joint(m, name: str, kind: str):
    j = m.joint(f"element.{name}.{kind}")
    return j, int(j.qposadr[0]), int(j.dofadr[0])


def _run(m, d, seconds: float, torque: float = 0.0, dof: int = 0) -> list[float]:
    out = []
    for _ in range(round(seconds / m.opt.timestep)):
        d.qfrc_applied[:] = 0.0
        d.qfrc_applied[dof] = torque
        mujoco.mj_step(m, d)
        out.append(float(d.qpos[m.jnt_qposadr[0]]))
    return out


DOOR = 'hinge = "left"\nsize = [0.05, 0.9, 1.2]\nrange_deg = [0, 90]\nmass = 5\n'


def test_hinged_parses_and_builds(tmp_path):
    e = _element("hinged", DOOR, "door")
    assert isinstance(e, Hinged)
    assert (e.shape, e.hinge, e.size, e.range_deg, e.rest_deg, e.mass) == (
        "box", "left", (0.05, 0.9, 1.2), (0.0, 90.0), 0.0, 5.0)
    assert e.damping == pytest.approx(0.05)          # no spring: the estimate
    m, d = _world(e)
    j, q, _ = _joint(m, "door", "hinge")
    assert m.jnt_type[j.id] == mujoco.mjtJoint.mjJNT_HINGE and bool(m.jnt_limited[j.id])
    assert m.jnt_range[j.id] == pytest.approx([0.0, math.pi / 2.0])
    assert d.qpos[q] == 0.0
    g = m.geom("element.door.panel")
    assert (int(g.contype[0]), int(g.conaffinity[0])) == (FIELD_BITS["contype"], FIELD_BITS["conaffinity"])
    assert m.body_mass[m.body("element.door").id] == pytest.approx(5.0)
    # the hinge axis is the panel's +y edge, upright: the panel's center is 0.45 m off it
    mujoco.mj_forward(m, d)
    axis = d.xanchor[j.id]
    assert axis[:2] == pytest.approx([4.0, 3.45]) and d.xaxis[j.id] == pytest.approx([0.0, 0.0, 1.0])
    assert d.geom_xpos[g.id] == pytest.approx([4.0, 3.0, 0.6])
    # load_range reads it from a file as well
    (tmp_path / "r.toml").write_text(HEAD.format(name="door", kind="hinged") + DOOR)
    assert load_range("r.toml", tmp_path).elements == (e,)


PORTCULLIS = 'axis = "z"\nsize = [0.05, 1.2, 0.8]\nrange = [0, 0.6]\nmass = 2.27\n'


def test_sliding_portcullis_takes_its_weight():
    e = _element("sliding", PORTCULLIS, "portcullis")
    assert isinstance(e, Sliding)
    m, d = _world(e)
    j, q, v = _joint(m, "portcullis", "slide")
    assert m.jnt_range[j.id] == pytest.approx([0.0, 0.6])
    rest = _run(m, d, 1.0)                     # on its lower stop: it neither sinks through nor bounces
    assert max(abs(x) for x in rest) <= 0.001
    weight = 2.27 * -m.opt.gravity[2]          # 22.27 N
    assert 22.0 < weight < 23.0
    held = _run(m, d, 1.0, torque=22.0, dof=v)
    assert max(held) < 0.001                   # 22.0 N doesn't lift it
    lifted = _run(m, d, 1.0, torque=23.0, dof=v)
    assert lifted[-1] > 0.05                   # 23.0 N does


def test_light_flap_is_stable():
    flap = ('hinge = "top"\nsize = [0.01, 0.3, 0.2]\nbase_height = 0.3\nrange_deg = [0, 90]\nmass = 0.05\n'
            'stiffness = 5\n')
    e = _element("hinged", flap, "flap")
    m, d = _world(e)
    _, q, v = _joint(m, "flap", "hinge")
    # rad/s, out through its range and into its stop at 0: the flap's 0.2 m tip struck at 4 m/s, a robot at full
    # speed
    for knock in (20.0, -20.0):
        mujoco.mj_resetData(m, d)
        d.qvel[v] = knock
        angles = np.degrees(_run(m, d, 2.0))
        assert np.all(np.isfinite(angles))
        assert angles.min() >= -1.0 and angles.max() <= 91.0, (knock, angles.min(), angles.max())
        assert abs(angles[-1]) < 0.1           # and it settles back to rest


def test_breakaway_holds_until_pushed():
    e = _element("hinged", DOOR.replace("range_deg = [0, 90]\n", "range_deg = [-90, 90]\n") + "breakaway = 2\n",
                 "door")
    m, d = _world(e)
    _, q, v = _joint(m, "door", "hinge")
    assert m.dof_frictionloss[v] == pytest.approx(2.0)
    held = _run(m, d, 1.0, torque=1.9, dof=v)
    assert abs(math.degrees(held[-1])) < 0.2
    mujoco.mj_resetData(m, d)
    turned = _run(m, d, 1.0, torque=2.1, dof=v)
    assert math.degrees(turned[-1]) > 1.0


def test_spinning_panel_has_no_stops():
    e = _element("hinged", 'shape = "disc"\nhinge = "center"\ndiameter = 0.81\nthickness = 0.02\nbase_height = 0.7\n'
                 'mass = 1.0\n', "wheel")
    assert e.range_deg is None and e.size is None
    m, d = _world(e)
    j, q, v = _joint(m, "wheel", "hinge")
    assert not bool(m.jnt_limited[j.id])
    mujoco.mj_forward(m, d)
    assert d.xaxis[j.id] == pytest.approx([0.0, 0.0, 1.0])     # it lies flat, turning about its own axis
    assert m.geom("element.wheel.panel").type == mujoco.mjtGeom.mjGEOM_CYLINDER
    angles = _run(m, d, 3.0, torque=1.0, dof=v)
    assert math.degrees(angles[-1]) > 1080.0


def test_rest_outside_range_errors():
    with pytest.raises(RangeError, match=r"rest_deg 120 is outside range_deg \[0, 90\]"):
        _element("hinged", DOOR + "rest_deg = 120\n")
    with pytest.raises(RangeError, match=r"rest 0.7 is outside range \[0, 0.6\]"):
        _element("sliding", PORTCULLIS + "rest = 0.7\n")
    for bad, words in (('hinge = "middle"', "hinge must be one of"), ('axis = "w"', "axis must be"),
                       ("range_deg = [90, 0]", "range_deg"), ("stiffness = -1", "stiffness")):
        kind, body = ("sliding", PORTCULLIS) if "axis" in bad else ("hinged", DOOR)
        key = bad.split(" = ")[0]
        body = "\n".join(line for line in body.splitlines() if not line.startswith(key + " ")) + "\n" + bad + "\n"
        with pytest.raises(RangeError, match=words):
            _element(kind, body)
    with pytest.raises(RangeError, match="diameter"):
        _element("hinged", DOOR + "diameter = 0.5\n")


def test_inch_forms():
    metric = _element("sliding", 'axis = "z"\nsize = [0.0254, 0.254, 0.508]\nbase_height = 0.0508\n'
                      'range = [0, 0.254]\nrest = 0.0254\nmass = 2\n')
    inch = _element("sliding", 'axis = "z"\nsize_in = [1, 10, 20]\nbase_height_in = 2\n'
                    'range_in = [0, 10]\nrest_in = 1\nmass = 2\n')
    assert inch.size == pytest.approx(metric.size) and inch.range == pytest.approx(metric.range)
    assert inch.rest == pytest.approx(metric.rest) and inch.base_height == pytest.approx(metric.base_height)
    disc = _element("hinged", 'shape = "disc"\nhinge = "center"\ndiameter_in = 32\nthickness_in = 1\nmass = 1\n')
    assert disc.diameter == pytest.approx(32 * IN) and disc.thickness == pytest.approx(IN)
    with pytest.raises(RangeError, match="both range and range_in"):
        _element("sliding", PORTCULLIS + "range_in = [0, 10]\n")


def test_rest_is_joint_zero():
    """A panel described closed with its spring resting 30 deg open: built turned to its rest, the joint reads 0
    there, and the stops are the range less the rest."""
    e = _element("hinged", DOOR + "rest_deg = 30\nstiffness = 1\n", "door")
    m, d = _world(e)
    j, q, _ = _joint(m, "door", "hinge")
    assert m.jnt_range[j.id] == pytest.approx(np.radians([-30.0, 60.0]))
    assert m.qpos_spring[q] == 0.0 and d.qpos[q] == 0.0
    mujoco.mj_forward(m, d)
    # the panel's center turned 30 deg about the upright hinge at (4, 3.45): 0.45 m from it, toward -y then +x
    c = d.geom_xpos[m.geom("element.door.panel").id]
    turned = math.radians(30.0)
    assert c[:2] == pytest.approx([4.0 + 0.45 * math.sin(turned), 3.45 - 0.45 * math.cos(turned)])
    _run(m, d, 1.0)
    assert abs(d.qpos[q]) < 1e-6                 # it stays at rest


def test_sliding_rest_is_joint_zero():
    """A touchpad described out, its spring resting 0.02 m in (-x): built there, its stops the range less the rest."""
    e = _element("sliding", 'axis = "x"\nsize = [0.05, 0.3, 0.3]\nbase_height = 0.3\nrange = [-0.05, 0]\n'
                 'rest = -0.02\nstiffness = 500\nmass = 0.2\n', "pad")
    m, d = _world(e)
    j, q, _ = _joint(m, "pad", "slide")
    assert m.jnt_range[j.id] == pytest.approx([-0.03, 0.02])
    mujoco.mj_forward(m, d)
    assert d.geom_xpos[m.geom("element.pad.panel").id] == pytest.approx([3.98, 3.0, 0.45])
    assert panel_parts(e)[0] == pytest.approx([-0.02, 0.0, 0.45])


def test_moving_elements_in_a_world(tmp_path):
    """On a range: the layout holds each joint's address, and the robot driving into the door swings it."""
    from range_fixtures import range_cfg

    from scrimmage.protocol import HalState
    from scrimmage.world import World
    path = tmp_path / "r.toml"
    path.write_text(HEAD.format(name="door", kind="hinged").replace("[4.0, 3.0]", "[5.0, 2.0]") + DOOR
                    + '\n[[element]]\nname = "pad"\nkind = "sliding"\ncenter = [8.0, 6.0]\naxis = "x"\n'
                    'size = [0.05, 0.3, 0.3]\nbase_height = 0.3\nrange = [-0.05, 0]\nstiffness = 500\nmass = 0.2\n'
                    '\n[start.s]\npose = [3.0, 2.0, 0.0]\n')
    w = World(range_cfg(range={"file": str(path), "start": "s"}), None)
    m, d = w.model, w.data
    layout = w.parts.layout
    assert layout.moving == {"door": int(m.joint("element.door.hinge").qposadr[0]),
                             "pad": int(m.joint("element.pad.slide").qposadr[0])}
    q = layout.moving["door"]
    assert d.qpos[q] == 0.0
    w.set_robot_force(400.0, 0.0)          # shove the robot into the door, its face about 1.6 m ahead of the bumper
    hal = HalState()
    for _ in range(3000):
        w.step(hal)
    assert math.degrees(d.qpos[q]) > 10.0        # it swings open, about its +y edge
