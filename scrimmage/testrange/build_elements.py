"""Field elements in the MuJoCo model, built from boxes and pipes (scrimmage/testrange/elements.py has the shapes).

Every element geom has FIELD_BITS, so the robot and the balls hit it. The geoms a robot drives on (bumps, ramps,
platforms, ground boxes) are in the geom group ELEMENT_GROUP, which only the carpet shares: a ray in that group finds
the ground (carpet or element) and never a ball, the robot, or a bar. Bars, switches and the other boxes (a truss)
are structure, not ground: they are in NON_GROUND_GROUP, so a robot hanging from a bar isn't resting on the floor,
and one parked under a bar doesn't read the bar as the ground.

A bump or a ramp is body `element.<name>`, welded to the world. A slope is a box whose top face is the slope.

- A bump is a crest box, a 0.5 in plate per ramp (the 2026 BUMP's HDPE), a lip box at each toe and a core box under
  the plates (standing in for the BUMP's aluminum body, so a ball or wheel can't get under the plates from the
  side). The plates run from the crest's edges down at `ramp_deg` to the lip's height; the lip boxes carry the
  profile from there to the toe, then drop to the carpet.
- A ramp is a solid deck box and, per slope, a box thick enough to fill the wedge under the slope. Its toe meets the
  carpet in a feather edge.

A platform is two bodies on two parallel hinges about the element's local y, one per hinge axis of its double hinge
(`hinge_spacing` apart, `hinge_height` up). `element.<name>.hinge` turns on the +x axis (joint
`element.<name>.tilt_plus`, from 0 to `max_tilt_deg`); the deck, `element.<name>`, turns on the -x axis carried by
it (joint `element.<name>.tilt_minus`, from -`max_tilt_deg` to 0). The tilt is the sum of the two joints (radians,
positive when the +x end goes down: a right-hand turn about local y), `platform_tilt`.

That is the real double hinge, not an equivalent: level, the deck rests on both axes (each joint at its 0 limit);
tilted, it turns about the low side's axis while the other lifts off its seat. The deck's own weight, centered
between the axes, then restores it with m g (spacing / 2) cos(tilt) less the CoM's height above the axes times
sin(tilt), about 63 N m for the 2023 CHARGE STATION; an off-center load has to beat that before the deck tips. No
spring: the drawings show none (GE-23300). Let go, the deck swings back through level and on over the other axis, as
a block rocking on two edges does, losing little at the hand-over; the joint damping is what settles it (the
CHARGE STATION's estimate takes it from 10 deg to LEVEL in under 2 s).

A bar is body `element.<name>` holding one capsule along its local x, `element.<name>.pipe` (a capsule, not a
cylinder: MuJoCo's capsule contacts are exact). Rigid, it is welded to the world. With `stiffness`, it rides a
vertical slide `element.<name>.flex`, a spring of that stiffness with the bar's damping, limited to +-FLEX_TRAVEL,
the pipe's mass BAR_MASS_PER_M per meter; the spring's rest is offset by the pipe's own weight, so unloaded it rests
at 0, its top at the given height. It is the rung's bending as one vertical spring: a load anywhere along the bar
moves all of it (the real rung bends most at mid-span). Its posts, `element.<name>.post_minus` and `post_plus`
(POST square, from the carpet up to the axis), are on a welded body `element.<name>.posts`.

A box is a welded body `element.<name>` with one box geom, `element.<name>.box`: in ELEMENT_GROUP when it is ground,
else NON_GROUND_GROUP.

A switch is body `element.<name>` on one hinge about its local y, `element.<name>.tilt` (positive when its +x end
goes down), `hinge_height` up, and its bar, `element.<name>.bar`, `bar_drop` below the hinge. It reuses a
platform's stops (+-`stop_deg`, PLATFORM_STOP_TC, critically damped) and its file's damping. Its CoM is `com_drop`
below the hinge, so it hangs level; its inertia about the CoM is an estimate, a uniform plate as long as the bar and
as tall as its drop, m (bar_length^2 + bar_drop^2) / 12 (the 2020 manual gives only the mass and the CoM). The frame
that carries the bar is not built: only the bar collides.

The stops are the joints' limits at +-`max_tilt_deg`, stiff (PLATFORM_STOP_TC) and critically damped, so the deck
lands on its stop without a bounce; the seats at 0 are the same kind of limit. Each joint carries the range file's
`damping` (an estimate there). The ramps hang off the deck's ends and turn with it: on the real station they are
hinged to the deck and their feet slide on the floor, so tilted they slope less on the low side and more on the
high side than these do.
"""

from __future__ import annotations

import math

import mujoco
import numpy as np

from ..parts.field import ELEMENT_GROUP, FIELD_BITS
from .build import RangeLayout, _quat, _rpy
from .elements import IN, POST, Bar, Box, Bump, Element, Platform, Ramp, Switch

__all__ = ["ELEMENT_GROUP", "NON_GROUND_GROUP", "add_element", "fill_layout", "platform_joints", "platform_tilt",
           "switch_joint"]

# Structure the robot can touch but doesn't drive on: bars, switches and boxes that aren't ground. Not ELEMENT_GROUP
# (the ground), and drawn by default (MuJoCo draws groups 0 to 2).
NON_GROUND_GROUP = 1

SKIN = 0.5 * IN           # m: a ramp plate's thickness (the 2026 BUMP's HDPE, manual 5.5)
LIP_RUN = 0.02            # m: how far a bump's lip box reaches back under its plate from the toe
DECK = 2.0 * IN           # m: a platform deck's thickness (estimate: the CHARGE STATION's top frame)
PLATFORM_STOP_TC = 0.005  # s: the solref time constant of a platform's stops and seats (estimate: a stiff stop)
HINGE_MASS = 0.01         # kg: the virtual link between a platform's two hinges (it has no real mass)
FLEX_TRAVEL = 0.05        # m: a compliant bar's slide limit each way (estimate: past a 4 kN impact's 25 mm at
                          # 160 kN/m, so the stop only catches a bent rung)
BAR_MASS_PER_M = 3.39     # kg/m: a compliant bar's moving mass (source: 1 1/4 in Sch 40 steel pipe, 2.27 lb/ft,
                          # ASME B36.10; the 2022 rungs, GE-22150)
SLOPE_RGBA = [0.25, 0.32, 0.55, 1.0]
DECK_RGBA = [0.42, 0.44, 0.48, 1.0]
BAR_RGBA = [0.62, 0.64, 0.68, 1.0]
STRUCTURE_RGBA = [0.55, 0.57, 0.6, 1.0]
_ALONG_X = [math.cos(math.pi / 4.0), 0.0, math.sin(math.pi / 4.0), 0.0]   # turns a capsule's z axis onto x


def _slope(top0, top1, thickness: float) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """A box whose top face runs from top0 to top1 (local (x, z), top1 further along x), `thickness` below it:
    (center, rotation, half sizes along its x and z); the caller adds the y half size."""
    a, b = np.asarray(top0, float), np.asarray(top1, float)
    run = b - a
    length = float(np.hypot(*run))
    u = run / length
    n = np.array([-u[1], u[0]])                        # the top face's upward normal
    c = (a + b) / 2.0 - n * thickness / 2.0
    rot = np.column_stack([[u[0], 0.0, u[1]], [0.0, 1.0, 0.0], [n[0], 0.0, n[1]]])
    return np.array([c[0], 0.0, c[1]]), rot, [length / 2.0, thickness / 2.0]


def _box(lo, hi) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """An upright box from local (x, z) lo to hi."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    c = (lo + hi) / 2.0
    return np.array([c[0], 0.0, c[1]]), np.eye(3), [(hi[0] - lo[0]) / 2.0, (hi[1] - lo[1]) / 2.0]


def _add(body, name: str, part, width: float, rgba, friction: float, offset=(0.0, 0.0, 0.0)) -> None:
    center, rot, (hx, hz) = part
    body.add_geom(name=name, type=mujoco.mjtGeom.mjGEOM_BOX, pos=list(center - np.asarray(offset, float)),
                  quat=_quat(rot), size=[hx, width / 2.0, hz], rgba=list(rgba), friction=[friction, 0.005, 0.0001],
                  group=ELEMENT_GROUP, **FIELD_BITS)


def bump_parts(e: Bump) -> dict:
    """The bump's boxes in its frame, by name."""
    th = math.radians(e.ramp_deg)
    c, h, lip = e.crest / 2.0, e.height, e.lip
    x_lip = c + (h - lip) / math.tan(th)               # where the plate's top meets the lip's height
    toe = e.depth / 2.0
    parts = {"crest": _box((-c, h - SKIN), (c, h))}
    parts["ramp_minus"] = _slope((-x_lip, lip), (-c, h), SKIN)
    parts["ramp_plus"] = _slope((c, h), (x_lip, lip), SKIN)
    parts["lip_minus"] = _box((-toe, 0.0), (-x_lip + LIP_RUN, lip))
    parts["lip_plus"] = _box((x_lip - LIP_RUN, 0.0), (toe, lip))
    core = (h - SKIN / math.cos(th)) / 2.0             # under the plates' undersides, half their height
    if core > 0.0:
        reach = c + core / math.tan(th)
        parts["core"] = _box((-reach, 0.0), (reach, core))
    return parts


def ramp_parts(e: Ramp) -> dict:
    th = math.radians(e.ramp_deg)
    run = e.height / math.tan(th)
    fill = e.height * math.cos(th)                     # the slope box's thickness: down to the deck's foot
    if e.two_sided:
        left, right = -e.deck / 2.0, e.deck / 2.0
        parts = {"ramp_minus": _slope((left - run, 0.0), (left, e.height), fill),
                 "ramp_plus": _slope((right, e.height), (right + run, 0.0), fill)}
    else:
        total = e.deck + run
        left, right = -total / 2.0 + run, total / 2.0
        parts = {"ramp": _slope((left - run, 0.0), (left, e.height), fill)}
    parts["deck"] = _box((left, 0.0), (right, e.height))
    return parts


def platform_parts(e: Platform) -> dict:
    """The deck and its ramps, level, in the element's frame."""
    half = e.length / 2.0
    return {"deck": _box((-half, e.height - DECK), (half, e.height)),
            "ramp_minus": _slope((-half - e.ramp_length, 0.0), (-half, e.height), SKIN),
            "ramp_plus": _slope((half, e.height), (half + e.ramp_length, 0.0), SKIN)}


def _platform(spec: mujoco.MjSpec, e: Platform, pos, quat) -> None:
    s = e.hinge_spacing / 2.0
    stop = e.max_tilt_deg if spec.compiler.degree else math.radians(e.max_tilt_deg)   # joint ranges' unit
    joint = {"type": mujoco.mjtJoint.mjJNT_HINGE, "axis": [0.0, 1.0, 0.0], "limited": True,
             "damping": e.damping, "solref_limit": [PLATFORM_STOP_TC, 1.0]}
    # in the element's frame: the +x axis at (s, 0, hinge_height), the deck's frame on the -x axis
    at = pos + _turn(quat, (s, 0.0, e.hinge_height))
    link = spec.worldbody.add_body(name=f"element.{e.name}.hinge", pos=list(at), quat=quat)
    link.mass, link.ipos, link.inertia, link.explicitinertial = HINGE_MASS, [0, 0, 0], [1e-6] * 3, True
    link.add_joint(name=f"element.{e.name}.tilt_plus", range=[0.0, stop], **joint)
    deck = link.add_body(name=f"element.{e.name}", pos=[-2.0 * s, 0.0, 0.0])
    # only the tilt axis turns, so the CoM's inertia about the other two axes is set equal to it
    deck.mass, deck.ipos, deck.inertia, deck.explicitinertial = (
        e.mass, [s, 0.0, e.com_height - e.hinge_height], [e.inertia] * 3, True)
    deck.add_joint(name=f"element.{e.name}.tilt_minus", range=[-stop, 0.0], **joint)
    origin = (-s, 0.0, e.hinge_height)
    for part, box in platform_parts(e).items():
        _add(deck, f"element.{e.name}.{part}", box, e.width, DECK_RGBA if part == "deck" else SLOPE_RGBA, e.friction,
             offset=origin)


def _turn(quat, v) -> np.ndarray:
    out = np.zeros(3)
    mujoco.mju_rotVecQuat(out, np.asarray(v, float), np.asarray(quat, float))
    return out


def _pipe(body, name: str, length: float, diameter: float, friction: float, z: float = 0.0,
          square: bool = False) -> None:
    """A capsule (or, `square`, a box of side `diameter`) `length` end to end along the body's x, its axis at height
    z in the body. Both are turned so the geom's z column is the axis and size[0] is the half side or radius."""
    r = diameter / 2.0
    if square:
        body.add_geom(name=name, type=mujoco.mjtGeom.mjGEOM_BOX, pos=[0.0, 0.0, z], quat=_ALONG_X,
                      size=[r, r, length / 2.0], rgba=BAR_RGBA, friction=[friction, 0.005, 0.0001],
                      group=NON_GROUND_GROUP, **FIELD_BITS)
        return
    body.add_geom(name=name, type=mujoco.mjtGeom.mjGEOM_CAPSULE, pos=[0.0, 0.0, z], quat=_ALONG_X,
                  size=[r, length / 2.0 - r, 0.0], rgba=BAR_RGBA, friction=[friction, 0.005, 0.0001],
                  group=NON_GROUND_GROUP, **FIELD_BITS)


def _upright(body, name: str, center, half, ground: bool, friction: float, rgba=STRUCTURE_RGBA) -> None:
    body.add_geom(name=name, type=mujoco.mjtGeom.mjGEOM_BOX, pos=list(center), size=list(half), rgba=list(rgba),
                  friction=[friction, 0.005, 0.0001], group=ELEMENT_GROUP if ground else NON_GROUND_GROUP,
                  **FIELD_BITS)


def _bar(spec: mujoco.MjSpec, e: Bar, pos, quat) -> None:
    if e.posts:
        base = spec.worldbody.add_body(name=f"element.{e.name}.posts", pos=list(pos), quat=quat)
        reach = e.length / 2.0 - POST / 2.0
        for side, k in (("minus", -1.0), ("plus", 1.0)):
            _upright(base, f"element.{e.name}.post_{side}", (k * reach, 0.0, e.height / 2.0),
                     (POST / 2.0, POST / 2.0, e.height / 2.0), False, e.friction)
    body = spec.worldbody.add_body(name=f"element.{e.name}", pos=list(pos + [0.0, 0.0, e.height]), quat=quat)
    if e.stiffness is not None:
        mass = BAR_MASS_PER_M * e.length
        r = e.diameter / 2.0
        across = mass * (e.length ** 2) / 12.0
        body.mass, body.ipos, body.inertia, body.explicitinertial = mass, [0, 0, 0], [mass * r * r, across, across], True
        weight = -mass * float(spec.option.gravity[2])
        body.add_joint(name=f"element.{e.name}.flex", type=mujoco.mjtJoint.mjJNT_SLIDE, axis=[0.0, 0.0, 1.0],
                       limited=True, range=[-FLEX_TRAVEL, FLEX_TRAVEL], stiffness=e.stiffness,
                       springref=weight / e.stiffness, damping=e.damping, solref_limit=[PLATFORM_STOP_TC, 1.0])
    _pipe(body, f"element.{e.name}.pipe", e.length, e.diameter, e.friction, square=e.section == "square")


def _switch(spec: mujoco.MjSpec, e: Switch, pos, quat) -> None:
    stop = e.stop_deg if spec.compiler.degree else math.radians(e.stop_deg)   # joint ranges' unit
    body = spec.worldbody.add_body(name=f"element.{e.name}", pos=list(pos + [0.0, 0.0, e.hinge_height]), quat=quat)
    tilt = e.mass * (e.bar_length ** 2 + e.bar_drop ** 2) / 12.0
    body.mass, body.ipos, body.inertia, body.explicitinertial = e.mass, [0.0, 0.0, -e.com_drop], [tilt] * 3, True
    body.add_joint(name=f"element.{e.name}.tilt", type=mujoco.mjtJoint.mjJNT_HINGE, axis=[0.0, 1.0, 0.0],
                   limited=True, range=[-stop, stop], damping=e.damping, solref_limit=[PLATFORM_STOP_TC, 1.0])
    _pipe(body, f"element.{e.name}.bar", e.bar_length, e.diameter, e.friction, z=-e.bar_drop)


def add_element(spec: mujoco.MjSpec, e: Element) -> None:
    """The element's bodies and geoms into `spec` (see the module docstring)."""
    pos = np.array([e.center[0], e.center[1], 0.0])
    quat = _quat(_rpy((0.0, 0.0, e.yaw_deg)))
    if isinstance(e, Platform):
        _platform(spec, e, pos, quat)
        return
    if isinstance(e, Bar):
        _bar(spec, e, pos, quat)
        return
    if isinstance(e, Switch):
        _switch(spec, e, pos, quat)
        return
    if isinstance(e, Box):
        body = spec.worldbody.add_body(name=f"element.{e.name}", pos=list(pos), quat=quat)
        sx, sy, sz = e.size
        _upright(body, f"element.{e.name}.box", (0.0, 0.0, e.base_height + sz / 2.0), (sx / 2.0, sy / 2.0, sz / 2.0),
                 e.ground, e.friction, DECK_RGBA if e.ground else STRUCTURE_RGBA)
        return
    body = spec.worldbody.add_body(name=f"element.{e.name}", pos=list(pos), quat=quat)    # no joint: welded
    parts = bump_parts(e) if isinstance(e, Bump) else ramp_parts(e)
    for part, box in parts.items():
        _add(body, f"element.{e.name}.{part}", box, e.width, DECK_RGBA if part in ("deck", "core") else SLOPE_RGBA,
             e.friction)


def platform_joints(model: mujoco.MjModel, name: str) -> tuple[int, int]:
    """The qpos addresses of platform `name`'s two hinges (tilt_plus, tilt_minus)."""
    return tuple(int(model.jnt_qposadr[model.joint(f"element.{name}.tilt_{side}").id]) for side in ("plus", "minus"))


def platform_tilt(model: mujoco.MjModel, data: mujoco.MjData, name: str) -> float:
    """Platform `name`'s tilt in radians, positive when its +x end is down."""
    a, b = platform_joints(model, name)
    return float(data.qpos[a] + data.qpos[b])


def switch_joint(model: mujoco.MjModel, name: str) -> int:
    """The qpos address of switch `name`'s hinge (radians, positive when its +x end is down)."""
    return int(model.jnt_qposadr[model.joint(f"element.{name}.tilt").id])


def fill_layout(layout: RangeLayout, model: mujoco.MjModel, elements) -> None:
    """The layout's element records, once the model is compiled: platforms' and switches' joint addresses, and each
    bar's pipe geoms."""
    layout.platforms = {e.name: platform_joints(model, e.name) for e in elements if isinstance(e, Platform)}
    layout.switches = {e.name: switch_joint(model, e.name) for e in elements if isinstance(e, Switch)}
    layout.bars = {e.name: [f"element.{e.name}.pipe"] for e in elements if isinstance(e, Bar)}
