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

A rod is a welded body `element.<name>` holding one geom, `element.<name>.rod`: a capsule (exact contacts, its
rounded ends reaching the base and tip) or, for a square section, a box, its axis from base to tip (rod_parts). It is
structure, not ground.

A switch is body `element.<name>` on one hinge about its local y, `element.<name>.tilt` (positive when its +x end
goes down), `hinge_height` up, and its bar, `element.<name>.bar`, `bar_drop` below the hinge. It reuses a
platform's stops (+-`stop_deg`, PLATFORM_STOP_TC, critically damped) and its file's damping. Its CoM is `com_drop`
below the hinge, so it hangs level; its inertia about the CoM is an estimate, a uniform plate as long as the bar and
as tall as its drop, m (bar_length^2 + bar_drop^2) / 12 (the 2020 manual gives only the mass and the CoM). The frame
that carries the bar is not built: only the bar collides.

A hinged or sliding element is body `element.<name>` with one geom, `element.<name>.panel` (a box, or a cylinder
for a disc), on joint `element.<name>.hinge` (about the hinge's axis, elements.hinge_axis) or `element.<name>.slide`
(along its local axis). The body is built at rest (turned `rest_deg` about the hinge, or moved `rest` along the
slide, from the panel as described), so the joint reads 0 at rest: the spring's springref is 0 (with a `preload`,
preload / stiffness, so at rest it presses the panel into the stop the rest is on that hard) and the stops are the
file's range less the rest. Positive is a right-hand turn about the axis (C3), or travel along it: about +y for
"bottom" and "top" (a panel on its bottom edge tips its top toward +x; one hung from its top edge swings its bottom
toward -x), about +z for the side and end edges and a flat disc (counter-clockwise seen from above), along +x, +y or
+z for a slide. The stops are a platform's (PLATFORM_STOP_TC, critically damped); without `range_deg` a hinged panel
turns freely. The breakaway is the joint's frictionloss, its solref as stiff as the stops so a push under it holds.
The panel is a uniform box or disc of the element's mass. It is structure (NON_GROUND_GROUP) or, with `ground`,
ground (ELEMENT_GROUP).

The stops are the joints' limits at +-`max_tilt_deg`, stiff (PLATFORM_STOP_TC) and critically damped, so the deck
lands on its stop without a bounce; the seats at 0 are the same kind of limit. Each joint carries the range file's
`damping` (an estimate there). The ramps hang off the deck's ends and turn with it: on the real station they are
hinged to the deck and their feet slide on the floor, so tilted they slope less on the low side and more on the
high side than these do.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import mujoco
import numpy as np

from ..parts.field import ELEMENT_GROUP, FIELD_BITS
from .build import FUNNEL_RGBA, PLATE_FRICTION, RangeLayout, _quat, _rpy
from .elements import (IN, POST, Bar, Box, Bump, Element, Hinged, Platform, Ramp, Rod, Sliding, Switch, hinge_axis,
                       panel_center, panel_inertia, panel_size)
from .spec import _rot_z
from .station import CHUTE_PLATE, CHUTE_WALL_BALLS, Station

if TYPE_CHECKING:
    from .pieces import PieceKind

__all__ = ["ELEMENT_GROUP", "NON_GROUND_GROUP", "add_chute", "add_element", "fill_layout", "moving_joint",
           "panel_parts", "platform_joints", "platform_tilt", "switch_joint"]

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


def chute_parts(st: Station, kind: PieceKind) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """A station's chute for pieces of `kind`, as boxes in the field: (center, rotation, half sizes) for "floor",
    "wall_left" and "wall_right". The floor's top plane is tangent to the piece resting at the release point
    (Station.chute_top) and runs down along the heading, `length` from under the release point; the walls stand
    CHUTE_WALL_BALLS piece extents above it, the left one on the heading's left. A ball on a slope touches it
    r sin(slope) back up the slope from under its center, so the floor's high end starts one more ball radius up the
    slope from that point: a ball set down at the release point lands on the floor's face, not on its top edge. A
    rigid piece touches within its bounding radius of its center, so that radius stands in for r.
    """
    c = st.chute
    s = math.radians(c.slope_deg)
    width = st.chute_width(kind)
    x, y = st.release[:2]
    r = kind.ball.diameter / 2.0 if kind.ball is not None else kind.radius
    top0 = (0.0, st.chute_top(kind))                    # in the heading's vertical plane: (along, up)
    # the ball touches at (-r sin s, z - r cos s); one radius further up the slope is r cos s further back
    back = r * (math.sin(s) + math.cos(s))
    high = (-back, top0[1] + back * math.tan(s))
    low = (c.length * math.cos(s), top0[1] - c.length * math.sin(s))
    center, rot, (hx, hz) = _slope(high, low, CHUTE_PLATE)
    rz = _rot_z(st.heading_deg)
    origin = np.array([x, y, 0.0])
    wall_h = CHUTE_WALL_BALLS * kind.extent
    up = rot[:, 2]                                       # the floor's upward normal
    surface = center + up * CHUTE_PLATE / 2.0            # the middle of the floor's top face
    side = CHUTE_PLATE / 2.0 + width / 2.0
    parts = {"floor": (center, (hx, width / 2.0 + CHUTE_PLATE, hz))}
    for name, k in (("wall_left", 1.0), ("wall_right", -1.0)):
        parts[name] = (surface + up * wall_h / 2.0 + np.array([0.0, k * side, 0.0]),
                       (hx, CHUTE_PLATE / 2.0, wall_h / 2.0))
    return {name: (origin + rz @ c3, rz @ rot, np.array(half)) for name, (c3, half) in parts.items()}


def add_chute(spec: mujoco.MjSpec, st: Station, kind: PieceKind) -> None:
    """Station `st`'s chute for pieces of `kind` (chute_parts) as body `element.<station>.chute`, welded to the
    world: geoms `element.<station>.chute.floor`, `.wall_left` and `.wall_right`, clear polycarbonate. Structure, not
    ground."""
    body = spec.worldbody.add_body(name=f"element.{st.name}.chute")
    for part, (center, rot, half) in chute_parts(st, kind).items():
        body.add_geom(name=f"element.{st.name}.chute.{part}", type=mujoco.mjtGeom.mjGEOM_BOX, pos=list(center),
                      quat=_quat(rot), size=list(half), rgba=list(FUNNEL_RGBA), friction=list(PLATE_FRICTION),
                      group=NON_GROUND_GROUP, **FIELD_BITS)


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


def rod_parts(e: Rod) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The rod as one box around its whole extent, in the field: (center, rotation, half sizes). The rotation's z
    column is the axis from base to tip, x the horizontal across it (the field's x for an upright rod); the halves are
    the radius twice, then half the length, so a round rod's end caps are inside the box."""
    axis = e.axis()
    across = np.cross([0.0, 0.0, 1.0], axis)
    across = np.array([1.0, 0.0, 0.0]) if np.linalg.norm(across) < 1e-9 else across / np.linalg.norm(across)
    rot = np.column_stack([across, np.cross(axis, across), axis])
    center = (np.asarray(e.base, float) + np.asarray(e.tip, float)) / 2.0
    r = e.diameter / 2.0
    return center, rot, np.array([r, r, e.length() / 2.0])


def _rod(spec: mujoco.MjSpec, e: Rod) -> None:
    """A welded body with the one geom `element.<name>.rod`, a capsule (or a box) whose axis runs base to tip. A
    capsule's rounded ends reach the base and tip exactly."""
    center, rot, (rx, _, hz) = rod_parts(e)
    body = spec.worldbody.add_body(name=f"element.{e.name}", pos=list(center), quat=_quat(rot))
    shape = dict(type=mujoco.mjtGeom.mjGEOM_BOX, size=[rx, rx, hz]) if e.section == "square" else dict(
        type=mujoco.mjtGeom.mjGEOM_CAPSULE, size=[rx, hz - rx, 0.0])
    body.add_geom(name=f"element.{e.name}.rod", rgba=BAR_RGBA, friction=[e.friction, 0.005, 0.0001],
                  group=NON_GROUND_GROUP, **shape, **FIELD_BITS)


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


def _axis_angle(axis, angle: float) -> np.ndarray:
    q = np.zeros(4)
    mujoco.mju_axisAngle2Quat(q, np.asarray(axis, float), angle)
    return q


def _rest_frame(e: Hinged | Sliding) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The moving body's origin and quaternion in the element's frame at rest (joint 0), and the panel's center in
    the body's frame: a hinged panel's body is at its hinge, turned `rest_deg` about it from the panel as described;
    a sliding panel's is at its center, `rest` along its axis."""
    if isinstance(e, Hinged):
        point, axis = hinge_axis(e)
        return point, _axis_angle(axis, math.radians(e.rest_deg)), panel_center(e) - point
    return panel_center(e) + e.rest * np.eye(3)["xyz".index(e.axis)], np.array([1.0, 0.0, 0.0, 0.0]), np.zeros(3)


def panel_parts(e: Hinged | Sliding) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The panel at rest as one box (a disc's bounding box) in the element's frame: (center, rotation, half sizes)."""
    origin, quat, arm = _rest_frame(e)
    rot = np.zeros(9)
    mujoco.mju_quat2Mat(rot, quat)
    rot = rot.reshape(3, 3)
    size = panel_size(e) if isinstance(e, Hinged) else e.size
    return origin + rot @ arm, rot, np.asarray(size, float) / 2.0


def _moving(spec: mujoco.MjSpec, e: Hinged | Sliding, pos, quat) -> None:
    """Body `element.<name>` on joint `element.<name>.hinge` or `.slide`, 0 at rest (see the module docstring)."""
    origin, turn, arm = _rest_frame(e)
    at = np.zeros(4)
    mujoco.mju_mulQuat(at, np.asarray(quat, float), turn)
    body = spec.worldbody.add_body(name=f"element.{e.name}", pos=list(pos + _turn(quat, origin)), quat=list(at))
    body.mass, body.ipos, body.inertia, body.explicitinertial = e.mass, list(arm), list(panel_inertia(e)), True
    hinged = isinstance(e, Hinged)
    if hinged:
        axis = hinge_axis(e)[1]
        span = e.range_deg
        lo_hi = None if span is None else [a - e.rest_deg for a in span]
        if lo_hi is not None and not spec.compiler.degree:    # joint ranges' unit
            lo_hi = [math.radians(a) for a in lo_hi]
        stiffness = math.degrees(e.stiffness)     # N m/deg to N m/rad
    else:
        axis = np.eye(3)["xyz".index(e.axis)]
        lo_hi = [a - e.rest for a in e.range]
        stiffness = e.stiffness
    # a preload winds the spring up past the rest: at 0 it pushes k springref = preload, into the stop the rest is on
    springref = e.preload / stiffness if e.preload else 0.0
    if hinged and springref and spec.compiler.degree:
        springref = math.degrees(springref)
    limits = {"limited": False} if lo_hi is None else {"limited": True, "range": lo_hi,
                                                        "solref_limit": [PLATFORM_STOP_TC, 1.0]}
    body.add_joint(name=f"element.{e.name}.{'hinge' if hinged else 'slide'}",
                   type=mujoco.mjtJoint.mjJNT_HINGE if hinged else mujoco.mjtJoint.mjJNT_SLIDE, axis=list(axis),
                   stiffness=stiffness, springref=springref, damping=e.damping, frictionloss=e.breakaway,
                   solref_friction=[PLATFORM_STOP_TC, 1.0], **limits)
    shape = (dict(type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[e.diameter / 2.0, e.thickness / 2.0, 0.0])
             if hinged and e.shape == "disc" else
             dict(type=mujoco.mjtGeom.mjGEOM_BOX, size=list(np.asarray(panel_size(e) if hinged else e.size) / 2.0)))
    body.add_geom(name=f"element.{e.name}.panel", pos=list(arm), rgba=list(STRUCTURE_RGBA),
                  friction=[e.friction, 0.005, 0.0001], group=ELEMENT_GROUP if e.ground else NON_GROUND_GROUP,
                  **shape, **FIELD_BITS)


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
    if isinstance(e, Rod):
        _rod(spec, e)
        return
    if isinstance(e, (Hinged, Sliding)):
        _moving(spec, e, pos, quat)
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


def moving_joint(model: mujoco.MjModel, e: Hinged | Sliding) -> int:
    """The qpos address of a hinged or sliding element's joint (radians or m from its rest)."""
    return int(model.jnt_qposadr[model.joint(f"element.{e.name}.{'hinge' if isinstance(e, Hinged) else 'slide'}").id])


def fill_layout(layout: RangeLayout, model: mujoco.MjModel, elements) -> None:
    """The layout's element records, once the model is compiled: platforms', switches' and moving elements' joint
    addresses, and each bar's pipe geoms."""
    layout.platforms = {e.name: platform_joints(model, e.name) for e in elements if isinstance(e, Platform)}
    layout.switches = {e.name: switch_joint(model, e.name) for e in elements if isinstance(e, Switch)}
    layout.bars = {e.name: [f"element.{e.name}.pipe"] for e in elements if isinstance(e, Bar)}
    layout.rods = {e.name: e for e in elements if isinstance(e, Rod)}
    layout.moving = {e.name: moving_joint(model, e) for e in elements if isinstance(e, (Hinged, Sliding))}
