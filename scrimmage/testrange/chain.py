"""A hanging chain element (`kind = "chain"`, K5 spec D1): a 1D flex of CHAIN_EDGES equal links with both ends pinned
to the world, and a visual bead on each vertex.

**The rest shape** (R3) is the discrete catenary of N equal links whose middle vertex (index N/2) is the lowest, at
`low`, with the ends at `top`, `span` apart along the element's yaw. Link i turns atan(c (i - (N-1)/2)) from level;
c is bisected on a log scale in C_BRACKET until the sag over the width equals (top - low) / span, and the link's
length is the span over the width at unit length. Every sag-to-span ratio between those of the bracket's two ends has
a catenary; outside them (MIN_SAG_RATIO, MAX_SAG_RATIO) there is none. The chain is built in that shape, so it starts
at rest.

**The model** (the chain spike, docs/superpowers/specs/2026-11-11-chain-spike.md):
- each interior vertex is a body `element.<name>.v<i>` on three slides (`_x`, `_y`, `_z`), a point mass of one link's
  weight (CHAIN_MASS_PER_M);
- the flex `element.<name>` (dim 1, its edges capsules of the chain's radius, its two end vertices on the world body)
  collides by CHAIN_BITS (structure: NON_GROUND_GROUP): the robot, game pieces and a standing partner, as the field's
  elements do, but not a partner hanging from a chain (parts/partner.py HANGING_BITS: its weld is its hook, R9), with
  its own contact (CHAIN_CONTACT): the hook's
  priority, a solmix that makes its stiffer solref and solimp win, and friction 0 so the hook's material sets mu
  (MuJoCo takes the larger at equal priority). Against a bumper (priority 0) the chain is frictionless;
- the flex equality `element.<name>.edges` holds the links' lengths, stiff (EDGE_SOLREF, EDGE_SOLIMP). There is no
  bending stiffness, so the world's implicitfast at 1 ms runs it.

**Drawn:** a flex has no geoms, and the live view, the Test tab and replays draw geoms. So each vertex carries a bead,
`element.<name>.bead<i>`: a sphere of the chain's diameter that touches nothing (contype and conaffinity 0, group 1).
The interior beads ride their vertex bodies and show the sag. The two end beads are on a body welded to the world,
`element.<name>`, as every other element's static geoms are, so they are not a movable object. The beads are drawing
only: the sensors' rays and the camera's view pass through them, so a chain blocks neither (a ray doesn't hit a flex's
edges in MuJoCo 4.0.0, so without that a sensor would see the chain only at its beads).

**Measured:** a flex isn't a geom, so `mj_geomDistance` can't measure it. The same welded body carries
`element.<name>.probe`, a capsule of the chain's radius that touches nothing, isn't drawn (PROBE_GROUP, left out of the
view) and is never hit by a ray. Clearance (blocks/clearance.py) poses it on each edge in turn, from the flex's vertices
now, and measures to it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import mujoco
import numpy as np

from .. import i18n

if TYPE_CHECKING:
    from .elements import Chain

CHAIN_EDGES = 40  # choice: the chain spike's best (80 stretches 28 cm under two robots; 20 bends coarsely)
CHAIN_MASS_PER_M = 0.744  # estimate: #35 roller chain, 0.50 lb/ft
CHAIN_CONTACT = {"priority": 1, "solmix": 1e6, "solref": (0.002, 1.0), "solimp": (0.99, 0.999, 0.001, 0.5, 2.0),
                 "friction": (0.0, 0.005, 0.0001)}  # source: chain spike, chainlib.CONTACT
EDGE_SOLREF = (0.002, 1.0)  # source: chain spike
EDGE_SOLIMP = (0.999, 0.9999, 0.001, 0.5, 2.0)  # source: chain spike
VERTEX_INERTIA = 1e-6  # choice: a point mass
# choice: the field's contype, with no conaffinity: it touches what has conaffinity bit 1 (the robot, pieces, a standing
# partner), as FIELD_BITS does, never the field, and never a hanging partner (contype 2, conaffinity 6)
CHAIN_BITS = {"contype": 1, "conaffinity": 0}
PROBE_GROUP = 5  # choice: a group MuJoCo's renderer doesn't draw by default; the probe touches nothing
C_BRACKET = (1e-6, 1e3)  # choice: R3's bracket for the catenary's c, bisected on a log scale
BISECTIONS = 200  # choice: the log bracket shrinks to double precision well before this


def _unit_shape(c: float, n: int) -> tuple[np.ndarray, np.ndarray]:
    """The n + 1 vertices (x from the first, z from the first) of n links of unit length, link i at atan(c (i -
    (n-1)/2)) from level."""
    th = np.arctan(c * (np.arange(n) - (n - 1) / 2.0))
    x = np.concatenate([[0.0], np.cumsum(np.cos(th))])
    z = np.concatenate([[0.0], np.cumsum(np.sin(th))])
    return x, z


def _ratio(c: float, n: int) -> float:
    """The unit shape's sag (its ends above its lowest vertex) over its width (end to end)."""
    x, z = _unit_shape(c, n)
    return float((z[-1] - z.min()) / x[-1])


MIN_SAG_RATIO = _ratio(C_BRACKET[0], CHAIN_EDGES)  # source: derived: the tautest catenary R3's bracket reaches
MAX_SAG_RATIO = _ratio(C_BRACKET[1], CHAIN_EDGES)  # source: derived: the deepest (2016: the links nearly vertical)


def rest_shape(span: float, top: float, low: float, n: int = CHAIN_EDGES) -> tuple[np.ndarray, float]:
    """The chain at rest (R3): (n + 1, 2) points as (s from the middle along the chain, z), and the link length.
    ValueError when no catenary fits: the sag over the span is outside the bracket's ratios."""
    want = (top - low) / span
    lo, hi = C_BRACKET
    if not _ratio(lo, n) <= want <= _ratio(hi, n):
        raise ValueError(i18n.t("no catenary of {edges} links has a sag of {sag} m over a span of {span} m",
                                edges=n, sag=f"{top - low:g}", span=f"{span:g}"))
    for _ in range(BISECTIONS):
        c = math.sqrt(lo * hi)
        if _ratio(c, n) > want:
            hi = c
        else:
            lo = c
    x, z = _unit_shape(math.sqrt(lo * hi), n)
    link = span / float(x[-1])
    low_i = n // 2
    s = (x - x[-1] / 2.0) * link
    return np.column_stack([s, low + (z - z[low_i]) * link]), link


def _axis(yaw_deg: float) -> np.ndarray:
    a = math.radians(yaw_deg)
    return np.array([math.cos(a), math.sin(a), 0.0])


def chain_points(e: Chain) -> np.ndarray:
    """(n + 1, 3): the chain's vertices at rest, in the field."""
    pts, _ = rest_shape(e.span, e.top, e.low)
    origin = np.array([e.center[0], e.center[1], 0.0])
    return origin + pts[:, :1] * _axis(e.yaw_deg) + pts[:, 1:] * np.array([0.0, 0.0, 1.0])


def edge_boxes(e: Chain) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """The chain's edges at rest as boxes in the field: (center, rotation (columns: the box's axes), half sizes), each
    a link long along its x and the chain's diameter across."""
    p = chain_points(e)
    r = e.diameter / 2.0
    across = np.cross([0.0, 0.0, 1.0], _axis(e.yaw_deg))      # horizontal, across the chain
    out = []
    for a, b in zip(p[:-1], p[1:]):
        u = (b - a) / float(np.linalg.norm(b - a))
        out.append(((a + b) / 2.0, np.column_stack([u, across, np.cross(u, across)]),
                    np.array([float(np.linalg.norm(b - a)) / 2.0, r, r])))
    return out


def add_chain(spec: mujoco.MjSpec, e: Chain) -> None:
    """The chain's vertex bodies, flex, edge equality and beads into `spec` (see the module docstring)."""
    from .build_elements import BAR_RGBA, NON_GROUND_GROUP   # build_elements imports this module

    p = chain_points(e)
    _, link = rest_shape(e.span, e.top, e.low)
    n = len(p) - 1
    r = e.diameter / 2.0
    bead = dict(type=mujoco.mjtGeom.mjGEOM_SPHERE, size=[r, 0.0, 0.0], contype=0, conaffinity=0, group=1,
                rgba=list(BAR_RGBA))
    ends = spec.worldbody.add_body(name=f"element.{e.name}")     # no joint: welded
    bodies, verts = [], []
    for i, v in enumerate(p):
        if i in (0, n):
            ends.add_geom(name=f"element.{e.name}.bead{i}", pos=[float(c) for c in v], **bead)
            bodies.append("world")
            verts += [float(c) for c in v]
            continue
        b = spec.worldbody.add_body(name=f"element.{e.name}.v{i}", pos=[float(c) for c in v])
        b.mass, b.ipos, b.inertia, b.explicitinertial = CHAIN_MASS_PER_M * link, [0, 0, 0], [VERTEX_INERTIA] * 3, True
        for k, ax in enumerate(np.eye(3)):
            b.add_joint(name=f"element.{e.name}.v{i}_{'xyz'[k]}", type=mujoco.mjtJoint.mjJNT_SLIDE, axis=list(ax))
        b.add_geom(name=f"element.{e.name}.bead{i}", **bead)
        bodies.append(b.name)
        verts += [0.0, 0.0, 0.0]
    ends.add_geom(name=f"element.{e.name}.probe", type=mujoco.mjtGeom.mjGEOM_CAPSULE, size=[r, 0.0, 0.0],
                  fromto=[float(c) for c in (*p[n // 2 - 1], *p[n // 2])],    # at rest: the edge into the low point
                  contype=0, conaffinity=0, group=PROBE_GROUP, rgba=[0.0, 0.0, 0.0, 0.0])
    f = spec.add_flex()
    f.name = f"element.{e.name}"
    f.dim = 1
    f.radius = r
    f.vertbody = bodies
    f.vert = verts
    f.elem = [k for i in range(n) for k in (i, i + 1)]
    f.contype, f.conaffinity = CHAIN_BITS["contype"], CHAIN_BITS["conaffinity"]
    f.group = NON_GROUND_GROUP
    f.selfcollide = mujoco.mjtFlexSelf.mjFLEXSELF_NONE
    f.rgba = list(BAR_RGBA)
    f.priority = CHAIN_CONTACT["priority"]
    f.solmix = CHAIN_CONTACT["solmix"]
    f.solref = list(CHAIN_CONTACT["solref"])
    f.solimp = list(CHAIN_CONTACT["solimp"])
    f.friction = list(CHAIN_CONTACT["friction"])
    eq = spec.add_equality(name=f"element.{e.name}.edges", type=mujoco.mjtEq.mjEQ_FLEX,
                           objtype=mujoco.mjtObj.mjOBJ_FLEX, name1=f.name)
    eq.solref = list(EDGE_SOLREF)
    eq.solimp = list(EDGE_SOLIMP)


@dataclass
class ChainLayout:
    """A built chain: its flex id, each vertex's body id (-1 for the two ends, on the world), the two ends in the
    field, the link length, and its drawing-only geoms: the clearance probe and the beads (bead i on vertex i)."""
    flex: int
    vertex_bodies: list[int]
    ends: tuple[np.ndarray, np.ndarray]
    link: float
    probe: int = -1
    beads: list[int] = field(default_factory=list)


def chain_layout(model: mujoco.MjModel, e: Chain) -> ChainLayout:
    p = chain_points(e)
    n = len(p) - 1
    _, link = rest_shape(e.span, e.top, e.low)
    return ChainLayout(flex=mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_FLEX, f"element.{e.name}"),
                       vertex_bodies=[-1] + [model.body(f"element.{e.name}.v{i}").id for i in range(1, n)] + [-1],
                       ends=(p[0].copy(), p[n].copy()), link=link,
                       probe=model.geom(f"element.{e.name}.probe").id,
                       beads=[model.geom(f"element.{e.name}.bead{i}").id for i in range(n + 1)])


def vertices(model: mujoco.MjModel, data: mujoco.MjData, flex: int) -> np.ndarray:
    """(n + 1, 3): flex `flex`'s vertices now, in the world."""
    a = int(model.flex_vertadr[flex])
    return data.flexvert_xpos[a:a + int(model.flex_vertnum[flex])]


def lowest_vertex(model: mujoco.MjModel, data: mujoco.MjData, flex: int) -> np.ndarray:
    """The world position of flex `flex`'s lowest vertex now."""
    v = vertices(model, data, flex)
    return v[int(np.argmin(v[:, 2]))].copy()
