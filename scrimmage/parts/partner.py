"""A passive partner robot: a robot config's `[partner]`, a second drive base with no robot program.

    [partner]
    start = [4.0, 4.0, 0.0]     # required: x (m), y (m), heading (deg), field coordinates like [robot] start_pose
    length = 0.7                # m, the frame without bumpers
    width = 0.7                 # m
    mass = 60.0                 # kg, the whole partner (an estimate of a full robot with bumpers and battery)
    bumper_clearance = 0.02     # m, the bumpers' bottom above the carpet
    brake = true                # its drive braked (true) or coasting (false)

or, in place of `start`, hanging from a chain of the range (K5 spec D6, R9):

    hang = { hold = "chain_blue_center", along = 0.45, across = -0.05 }
    # a chain element; along: m from its low point along its yaw; across: m to the yaw's left (default 0)

A hanging partner faces along the chain (`yaw_deg`), its length along the chain and its narrow side toward the chain's
neighbors. Its top center (its highest colliding geom's top) sits HANG_GAP below the chain's interior vertex nearest
`along`, `across` to the side of it, and a weld holds its chassis to that vertex's body at their pose at the start. A
vertex body has no rotation, so the partner stays level. `along` must leave that vertex interior (|along| at most the
span / 2 less one link) and `across` keep it over the roof (|across| at most half the width); past either is a
HangTooFar. Its 60 kg pulls the chain's slack into a taut V at its vertex, raising the rest of the chain. The chain
doesn't touch it (HANGING_BITS): its weld is its hook. It starts off the carpet: check_start checks only the robot's
outline against it, and check_hanging, once the model is built, refuses it overlapping any field geom it touches
(an element, an obstacle, a floor piece). While none of its geoms touches the ground it counts on its chain for
climb.hold.<chain>.robots and HARMONY (terrain/climb.py).

It is the stock chassis with the stock differential (6-wheel tank) drive under it, every name prefixed `partner.`
(spec C7): bodies `partner.chassis`, `partner.rails`, `partner.left_1` ..., and a free joint `partner.free`. It is not
the robot: it isn't in Built.instances, its treads aren't in Built.tread_geoms, and it has no motors or bindings, so
the robot's drivetrain, contacts and checks never see it (each finds the robot by its root body). Its geoms touch the
robot, the field and game pieces (contype 3, conaffinity 3; a hanging partner's HANGING_BITS (2, 6) leave out the
chain); its own bodies are excluded from touching each other.
Its start is built into the model like the robot's, its wheels on the carpet, so World.reset puts it back there.

Its signals (PartnerMonitor), in inches and degrees: `partner.height`, the lowest of the frame's four bottom corners
above where it was once settled (spec C8: from its rested height, so its bumper clearance isn't lift), a hanging
partner's from its standing height instead (`stand_low`: the lowest corner's height standing on its wheels), so it
reads how far it hangs off the carpet; `partner.tilt`, the chassis's z axis from vertical; and `partner.moved`, how
far its center has gone across the floor from where it settled. It counts as settled SETTLE_S after a reset (the Test tab's settle, climb/run.py); before that
the signals read from its start.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import mujoco
import numpy as np

from .. import i18n
from .model import PartError, quat_from_rpy

PARTNER_KEYS = ("length", "width", "mass", "bumper_clearance", "brake", "start", "hang")
HANG_KEYS = ("hold", "along", "across")
PREFIX = "partner."
PARTNER_BITS = {"contype": 3, "conaffinity": 3}     # touches the robot (2, 1), the field (1, 2) and balls (4, 7)
# choice: a hanging partner touches the robot, the field, balls and launched balls (4, 5), but not its chain
# (testrange/chain.py CHAIN_BITS: 1, 0): its weld is its hook, and the chain can't start in a V over its roof (F12)
HANGING_BITS = {"contype": 2, "conaffinity": 6}
DEFAULT_LENGTH = 0.7            # m (spec C7)
DEFAULT_WIDTH = 0.7             # m
DEFAULT_MASS = 60.0             # kg: an estimate of a full robot with its bumpers and battery
DEFAULT_BUMPER_CLEARANCE = 0.02  # m
MIN_SIZE = 0.4                  # m: the smallest frame its drive's wheels fit inside
# The drive inside the frame: the stock differential's track (0.51 m) and wheelbase (0.52 m) on the stock 0.7 m frame,
# kept that far inside a frame of any size.
TRACK_INSET = 0.19              # m: frame width minus track width
WHEELBASE_INSET = 0.18          # m: frame length minus wheelbase
IN = 0.0254                     # m per inch
HANG_GAP = 0.02  # choice: R9: a hanging partner's top this far below its chain vertex, so it doesn't touch the chain
DEFAULT_ALONG = 0.0  # choice: a hanging partner with no `along` hangs at the chain's low point
DEFAULT_ACROSS = 0.0  # choice: with no `across`, its center is under the chain
HANG_OVERLAP_TOL = 1e-3  # choice: mj_geomDistance's convex-hull slack (m); a deeper overlap at the start is refused


def brake_damping(drive_params: dict) -> float:
    """Each wheel joint's damping (N·m·s/rad) with the drive braked, an estimate: a braked controller shorts the
    motor's windings, so it resists turning by its back-EMF, as world.py's motor bindings model it (bindings.py:
    eff Kt g^2 / (Kv R) for the motors on a side, Ke = 1 / Kv), not a lock. That side's damping is shared by its
    wheels, which are chained together: about 0.29 N·m·s/rad a wheel for the stock differential (2 CIMs a side at
    10.71:1, 90 % efficient, 3 wheels). Coasting leaves the wheels free."""
    from ..motors import get_motor
    m = get_motor(drive_params["motor"], drive_params["motors_per_side"])
    side = drive_params["efficiency"] * m.Kt * drive_params["gearing"] ** 2 / (m.Kv * m.R)
    return side / drive_params["wheels_per_side"]


@dataclass(frozen=True)
class Hang:
    """`[partner] hang`: the chain element it hangs from, and where along it."""
    hold: str
    along: float                            # m, signed along the chain's yaw from its low point
    across: float = DEFAULT_ACROSS          # m, signed across the chain, horizontal: + to the left of its yaw


@dataclass(frozen=True)
class PartnerSpec:
    start: tuple[float, float, float] | None = None     # x (m), y (m), heading (deg); None for a hanging partner
    length: float = DEFAULT_LENGTH
    width: float = DEFAULT_WIDTH
    mass: float = DEFAULT_MASS
    bumper_clearance: float = DEFAULT_BUMPER_CLEARANCE
    brake: bool = True
    hang: Hang | None = None


@dataclass
class PartnerInfo:
    body: str                               # the chassis body, "partner.chassis"
    wheels: list[str]                       # the wheel joints, "partner.left_1", ...
    start: tuple[float, float, float]       # x (m), y (m), heading (deg)
    corners: np.ndarray = field(default_factory=lambda: np.zeros((4, 3)))  # the frame's bottom corners, chassis frame
    outline: tuple[float, float] = (0.0, 0.0)   # m: the frame plus bumpers, length and width
    parts: list = field(default_factory=list)   # the two parts (assemble.Instance), for their treads' contacts
    hang: Hang | None = None                # hanging from a chain ([partner] hang); None standing
    stand_low: float = 0.0                  # m: the lowest bottom corner's z standing on its wheels


def _number(v, key: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise PartError(i18n.t("{partner_table} {key} must be a number, not {value}", key=key, value=repr(v),
                               partner_table="[partner]"))
    return float(v)


def parse_partner(cfg: dict, elements=()) -> PartnerSpec | None:
    """The config's [partner], checked (None without one); every mistake a PartError naming the key. `elements` are
    the range's, which `hang` names a chain of."""
    sec = cfg.get("partner")
    if sec is None:
        return None
    if not isinstance(sec, dict):
        raise PartError(i18n.t("{partner_table} must be a table", partner_table="[partner]"))
    extra = [k for k in sec if k not in PARTNER_KEYS]
    if extra:
        raise PartError(i18n.t("{partner_table}: unknown key {extra}; allowed: {partner_keys}", extra=repr(extra[0]),
                               partner_keys=', '.join(PARTNER_KEYS), partner_table="[partner]"))
    start = sec.get("start")
    if start is not None and "hang" in sec:
        raise PartError(i18n.t("{partner_table} has both start and hang: a partner robot either stands at its start "
                               "or hangs from a chain; drop one", partner_table="[partner]"))
    if start is None and "hang" not in sec:
        raise PartError(i18n.t("{partner_table} needs start or hang: {start_setting}, where the partner robot "
                               "starts, or {hang_setting}, to hang it from a chain", partner_table="[partner]",
                               start_setting="start = [x, y, heading_deg]",
                               hang_setting='hang = { hold = "<chain>", along = <m> }'))
    kw: dict = {}
    if start is not None:
        if not (isinstance(start, list) and len(start) == 3 and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in start)):
            raise PartError(i18n.t("{partner_table} start must be [x, y, heading_deg], 3 finite numbers, not {start}",
                                   start=repr(start), partner_table="[partner]"))
        kw["start"] = tuple(float(v) for v in start)
    for key in ("length", "width"):
        if key in sec:
            kw[key] = _number(sec[key], key)
            if kw[key] < MIN_SIZE:
                raise PartError(i18n.t("{partner_table} {key} must be at least {min_size} m (its drive's wheels fit "
                                       "inside the frame), not {value}", key=key, min_size=f"{MIN_SIZE:g}",
                                       value=f"{kw[key]:g}", partner_table="[partner]"))
    for key in ("mass", "bumper_clearance"):
        if key in sec:
            kw[key] = _number(sec[key], key)
            if kw[key] <= 0:
                raise PartError(i18n.t("{partner_table} {key} must be more than 0, not {value}", key=key,
                                       value=f"{kw[key]:g}", partner_table="[partner]"))
    if "brake" in sec:
        if not isinstance(sec["brake"], bool):
            raise PartError(i18n.t("{partner_table} brake must be true (its wheels braked) or false (coasting), not "
                                   "{brake}", brake=repr(sec['brake']), partner_table="[partner]"))
        kw["brake"] = sec["brake"]
    if start is None:
        kw["hang"] = _hang(sec["hang"], elements, kw.get("length", DEFAULT_LENGTH), kw.get("width", DEFAULT_WIDTH))
    return PartnerSpec(**kw)


class HangTooFar(PartError):
    """`[partner] hang` along or across past its limit, in meters as the robot config writes them. It keeps `key`
    ("along" or "across"), `value` and `limit` (m), so the Test tab words Setup's partner in the Units setting
    (blocks/run_setup.py hang_words) by finding this exception, not its words."""

    def __init__(self, key: str, value: float, limit: float):
        if key == "along":
            text = i18n.t("{where} along must be at most {limit} m either way from the chain's low point (half its "
                          "span less one link, so the partner hangs from a link inside the chain), not {along}",
                          where="[partner] hang", limit=f"{limit:.3f}", along=f"{value:g}")
        else:
            text = i18n.t("{where} across must be at most {limit} m either way from the chain (half the partner's "
                          "frame, so the chain stays over its roof), not {across}", where="[partner] hang",
                          limit=f"{limit:.3f}", across=f"{value:g}")
        super().__init__(text)
        self.key, self.value, self.limit = key, value, limit


def _hang(h, elements, length: float, width: float) -> Hang:
    """`[partner] hang`, checked against the range's chains (R9) and the partner's frame (`length` along the chain,
    `width` across it)."""
    from ..testrange.chain import rest_shape
    from ..testrange.elements import Chain
    where = "[partner] hang"
    if not isinstance(h, dict):
        raise PartError(i18n.t("{where} must be a table like {hang_setting}", where=where,
                               hang_setting='hang = { hold = "<chain>", along = 0.5 }'))
    extra = [k for k in h if k not in HANG_KEYS]
    if extra:
        raise PartError(i18n.t("{where}: unknown key {extra}; allowed: {keys}", where=where, extra=repr(extra[0]),
                               keys=", ".join(HANG_KEYS)))
    chains = {e.name: e for e in elements if isinstance(e, Chain)}
    if not chains:
        raise PartError(i18n.t("{where} needs a chain to hang from, and the range has none (a {chain_setting} "
                               "element)", where=where, chain_setting='kind = "chain"'))
    hold = h.get("hold")
    if not isinstance(hold, str):
        raise PartError(i18n.t("{where} needs hold, the chain it hangs from: one of {chains}", where=where,
                               chains=", ".join(chains)))
    if hold not in chains:
        if any(e.name == hold for e in elements):
            raise PartError(i18n.t("{where} hold '{hold}' isn't a chain: a hanging partner robot needs a chain; the "
                                   "range's chains: {chains}", where=where, hold=hold, chains=", ".join(chains)))
        raise PartError(i18n.t("{where} hold '{hold}' isn't an element of the range; its chains: {chains}",
                               where=where, hold=hold, chains=", ".join(chains)))
    along = _number(h["along"], "hang along") if "along" in h else DEFAULT_ALONG
    e = chains[hold]
    _, link = rest_shape(e.span, e.top, e.low)
    limit = e.span / 2.0 - link
    if abs(along) > limit:
        raise HangTooFar("along", along, limit)
    across = _number(h["across"], "hang across") if "across" in h else DEFAULT_ACROSS
    if abs(across) > width / 2.0:
        raise HangTooFar("across", across, width / 2.0)
    return Hang(hold, along, across)


def _parts(p: PartnerSpec) -> tuple:
    """The partner's chassis and drive (assemble.Instance), the chassis carrying all of `mass` the drive doesn't."""
    from .assemble import Instance
    from .stock import build_stock, stock_params
    where = "[partner]"
    dparams = {"track_width": p.width - TRACK_INSET, "wheelbase": p.length - WHEELBASE_INSET}
    drive = build_stock("differential", dparams, where)
    drive_mass = sum(b.mass for b in drive.bodies)
    if p.mass <= drive_mass:
        raise PartError(i18n.t("{partner_table} mass must be more than its drive's {drive_mass} kg, not {mass}",
                               drive_mass=f"{drive_mass:g}", mass=f"{p.mass:g}", partner_table="[partner]"))
    cparams = {"length": p.length, "width": p.width, "mass": p.mass - drive_mass,
               "bumper_clearance": p.bumper_clearance}
    chassis = build_stock("chassis", cparams, where)
    return (Instance("partner", chassis, "stock chassis", None, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), {}, where,
                     stock_params("chassis", cparams, where)),
            Instance("partner", drive, "stock differential", "partner.center", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), {},
                     where, stock_params("differential", dparams, where)))


def _attach(spec: mujoco.MjSpec, chassis, drive, pos, heading: float):
    """Attach the partner's chassis (with its free joint) at `pos`, turned `heading` (deg), and its drive under it:
    the chassis body."""
    from .assemble import _add_couplings, part_spec
    root = part_spec(chassis.part, chassis.where).body(chassis.part.bodies[0].name)
    spec.worldbody.add_frame(pos=[float(c) for c in pos], quat=list(quat_from_rpy([0, 0, heading]))).attach_body(
        root, PREFIX, "")
    body = spec.body(f"{PREFIX}{chassis.part.bodies[0].name}")
    body.add_freejoint(name=f"{PREFIX}free")
    mount = next(m for m in chassis.part.mounts if m.name == "center")
    rails = part_spec(drive.part, drive.where).body(drive.part.bodies[0].name)
    body.add_frame(pos=list(mount.pos), quat=list(quat_from_rpy(mount.rot))).attach_body(rails, PREFIX, "")
    _add_couplings(spec, "partner", drive.part)
    return body


def _standing(chassis, drive, corners: np.ndarray) -> tuple[float, float]:
    """The partner standing on its wheels at the origin: (its highest colliding geom's top, its frame's lowest bottom
    corner), z in m. Measured on the partner alone, compiled at its start pose."""
    alone = mujoco.MjSpec()
    alone.compiler.degree = False
    _attach(alone, chassis, drive, (0.0, 0.0, 0.0), 0.0)
    m = alone.compile()
    d = mujoco.MjData(m)
    mujoco.mj_kinematics(m, d)
    signs = np.array(list(np.ndindex(2, 2, 2)), dtype=float) * 2.0 - 1.0
    top = -math.inf
    for g in range(m.ngeom):
        if not (m.geom_contype[g] or m.geom_conaffinity[g]):
            continue
        local = m.geom_aabb[g][:3] + signs * m.geom_aabb[g][3:]
        top = max(top, float((d.geom_xpos[g][2] + local @ d.geom_xmat[g].reshape(3, 3)[2]).max()))
    b = m.body(f"{PREFIX}{chassis.part.bodies[0].name}").id
    low = float(np.min(d.xpos[b][2] + corners @ d.xmat[b].reshape(3, 3)[2]))
    return top, low


def add_partner(spec: mujoco.MjSpec, cfg: dict, elements=()) -> PartnerInfo | None:
    """Add the config's [partner] to `spec` (None, and nothing added, without one): the stock chassis at its start,
    its wheels on the carpet, with a free joint, and the stock differential under it; or, with `hang`, under its
    chain's vertex and welded to it (R9). `elements` are the range's, already in `spec`."""
    p = parse_partner(cfg, elements)
    if p is None:
        return None
    chassis, drive = _parts(p)
    prm = chassis.stock_params
    fh, bumper = prm["frame_height"], prm["bumper"]
    corners = np.array([[sx * p.length / 2, sy * p.width / 2, fh - 0.02] for sx in (1, -1) for sy in (1, -1)])
    top, stand_low = _standing(chassis, drive, corners)
    vertex = None
    if p.hang is None:
        x, y, heading = p.start
        z = 0.0
    else:
        from ..testrange.chain import EDGE_SOLIMP, EDGE_SOLREF, chain_points, rest_shape
        e = next(e for e in elements if e.name == p.hang.hold)
        s = rest_shape(e.span, e.top, e.low)[0][:, 0]
        i = 1 + int(np.argmin(np.abs(s[1:-1] - p.hang.along)))      # the nearest interior vertex
        x, y, vz = (float(c) for c in chain_points(e)[i])
        heading = e.yaw_deg                  # its length along the chain (the controller's I4 ruling)
        a = math.radians(e.yaw_deg)
        x, y = x - p.hang.across * math.sin(a), y + p.hang.across * math.cos(a)     # across: + to the yaw's left
        z = vz - HANG_GAP - top
        vertex = f"element.{e.name}.v{i}"
    body = _attach(spec, chassis, drive, (x, y, z), heading)
    names = [f"{PREFIX}{b.name}" for inst in (chassis, drive) for b in inst.part.bodies]
    bits = PARTNER_BITS if vertex is None else HANGING_BITS
    for name in names:
        for g in spec.body(name).geoms:
            if g.contype or g.conaffinity:          # a drawing-only geom (the heading marker, the rails) stays so
                g.contype, g.conaffinity = bits["contype"], bits["conaffinity"]
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            spec.add_exclude(bodyname1=a, bodyname2=b)
    if vertex is not None:
        # held at their pose at the start: anchor 0, and relpose's quaternion 0 so MuJoCo takes it from qpos0
        data = [0.0] * mujoco.mjNEQDATA
        data[10] = data[11] = 1.0       # torquescale 1 (the rotation held as firmly as the position), as MJCF's
        spec.add_equality(type=mujoco.mjtEq.mjEQ_WELD, name=f"{PREFIX}hang", name1=body.name, name2=vertex,
                          objtype=mujoco.mjtObj.mjOBJ_BODY, data=data, solref=list(EDGE_SOLREF),
                          solimp=list(EDGE_SOLIMP))
    wheels = [f"{PREFIX}{j}" for j in drive.part.joint_names()]
    if p.brake:
        b = brake_damping(drive.stock_params)
        for j in wheels:
            sj = spec.joint(j)
            sj.damping = [b, *list(sj.damping)[1:]]      # [linear, ...]: MuJoCo's damping terms
    return PartnerInfo(body.name, wheels, (x, y, heading), corners, (p.length + 2 * bumper, p.width + 2 * bumper),
                       [chassis, drive], hang=p.hang, stand_low=stand_low)


# ---- the start check (Review Focus 3) -------------------------------------------------------------------------------

def _overlap(a: np.ndarray, b: np.ndarray) -> float:
    """How far two convex polygons overlap: the least overlap of their projections over each edge's normal (the
    distance one must move to clear the other), 0 when some edge separates them."""
    depth = math.inf
    for poly in (a, b):
        for k in range(len(poly)):
            edge = poly[(k + 1) % len(poly)] - poly[k]
            n = np.array([-edge[1], edge[0]]) / float(np.linalg.norm(edge))
            pa, pb = a @ n, b @ n
            depth = min(depth, min(pa.max(), pb.max()) - max(pa.min(), pb.min()))
            if depth <= 0.0:
                return 0.0
    return float(depth)


class PartnerOverlap(PartError):
    """check_start's refusal: in meters, as a robot config's [partner] start is written (and as MCP results and
    report.json have always had it). It keeps `what` (the shown words: "the robot", "element <name>") and `depth`
    (m), so the Test tab words Setup's partner in the Units setting (blocks/run_setup.py setup_partner_words) by finding
    this exception, not its words (L3)."""

    def __init__(self, what: str, depth: float):
        super().__init__(i18n.t("{partner_table} starts inside {what} (their outlines overlap by {depth} m)",
                                partner_table="[partner]", what=what, depth=f"{depth:.2f}"))
        self.what, self.depth = what, depth


def _circle_overlap(poly: np.ndarray, center: np.ndarray, r: float) -> float:
    """How far a circle (center, radius r) overlaps a convex polygon: how far it must move to clear it, 0 when apart."""
    edges = [(poly[k], poly[(k + 1) % len(poly)]) for k in range(len(poly))]

    def gap(a, b) -> float:                      # the center's distance to edge a-b
        ab = b - a
        u = min(1.0, max(0.0, float((center - a) @ ab) / float(ab @ ab)))
        return float(np.linalg.norm(center - (a + u * ab)))

    near = min(gap(a, b) for a, b in edges)
    # inside a counterclockwise or clockwise polygon: the center is on the same side of every edge
    sides = [float((b - a)[0] * (center - a)[1] - (b - a)[1] * (center - a)[0]) for a, b in edges]
    inside = all(x >= 0.0 for x in sides) or all(x <= 0.0 for x in sides)
    return near + r if inside else max(0.0, r - near)


def floor_pieces(rng) -> list[tuple[float, float, float, str]]:
    """The range's floor pieces as (x, y, radius (m), what a student calls it) for check_start: a ball's or disc's
    own radius, any other piece's bounding sphere's (so a long piece lying near the partner counts a little early)."""
    kinds = list(rng.floor_kinds) if len(rng.floor_kinds) == len(rng.floor_balls) \
        else [rng.piece_kind] * len(rng.floor_balls)
    out = []
    for (x, y), name in zip(np.asarray(rng.floor_balls, float).reshape(-1, 2), kinds):
        kind = rng.kinds.get(name)
        if kind is not None:
            out.append((float(x), float(y), float(kind.radius), kind.label))
    return out


def check_start(info: PartnerInfo, robot_start: tuple[float, float, float], elements=(), pieces=()) -> None:
    """The partner's outline (frame plus bumpers) may not overlap the robot's start outline (ROBOT_OUTLINE), any
    element standing on the carpet (testrange/elements.py's _on_floor, as a start's robot outline is checked) or any
    floor piece (`pieces`: floor_pieces' (x, y, radius, label)). A hanging partner is off the carpet: only the
    robot's outline is checked (R9)."""
    from ..testrange.elements import ROBOT_OUTLINE, _on_floor, _rect
    x, y, heading = info.start
    mine = _rect((x, y), heading, *info.outline)
    rx, ry, rh = robot_start
    depth = _overlap(mine, _rect((rx, ry), rh, ROBOT_OUTLINE, ROBOT_OUTLINE))
    if depth > 1e-9:
        raise PartnerOverlap(i18n.t("the robot"), depth)
    if info.hang is not None:
        return
    for e in elements:
        depth = max((_overlap(mine, f) for f in _on_floor(e, start=True)), default=0.0)
        if depth > 1e-9:
            raise PartnerOverlap(i18n.t("element {name}", name=e.name), depth)
    for x, y, r, label in pieces:
        depth = _circle_overlap(mine, np.array([x, y]), r)
        if depth > 1e-9:
            raise PartnerOverlap(i18n.t("a floor piece ({piece})", piece=label), depth)


def check_hanging(model: mujoco.MjModel, info: PartnerInfo, rng=None, robot_body: str | None = None) -> None:
    """A hanging partner, at the start (qpos0), may not overlap any geom it touches: an element, an obstacle, a floor
    piece or the robot (its chain it doesn't touch). Each pair whose bounding spheres meet is measured with
    mj_geomDistance; one deeper than HANG_OVERLAP_TOL is a PartnerOverlap naming what it overlaps (I1)."""
    m = model
    d = mujoco.MjData(m)
    mujoco.mj_kinematics(m, d)
    root = m.body_rootid[m.body(info.body).id]
    roots = m.body_rootid[m.geom_bodyid]
    live = (m.geom_contype != 0) | (m.geom_conaffinity != 0)
    mine = [g for g in range(m.ngeom) if live[g] and roots[g] == root]
    flat = (mujoco.mjtGeom.mjGEOM_PLANE, mujoco.mjtGeom.mjGEOM_HFIELD)
    others = [g for g in range(m.ngeom) if live[g] and roots[g] != root and m.geom_type[g] not in flat]
    robot = m.body_rootid[m.body(robot_body).id] if robot_body is not None else -1
    for h in mine:
        ct, ca = int(m.geom_contype[h]), int(m.geom_conaffinity[h])
        for g in others:
            if not ((int(m.geom_contype[g]) & ca) or (ct & int(m.geom_conaffinity[g]))):
                continue
            if float(np.linalg.norm(d.geom_xpos[g] - d.geom_xpos[h])) > m.geom_rbound[g] + m.geom_rbound[h]:
                continue
            dist = mujoco.mj_geomDistance(m, d, h, g, 0.0, None)
            if dist < -HANG_OVERLAP_TOL:
                raise PartnerOverlap(i18n.t("the robot") if roots[g] == robot else _what(m, g, rng), -float(dist))


def _what(m: mujoco.MjModel, g: int, rng) -> str:
    """What a student calls field geom `g` in a start refusal: "element <name>", "obstacle <name>", "a floor piece
    (...)", else its body's (or its own) name."""
    name = m.geom(g).name
    if name.startswith("element."):
        return i18n.t("element {name}", name=name.split(".")[1])
    if name.startswith("ball."):
        pieces = floor_pieces(rng) if rng is not None else []
        i = int(name.split(".")[1]) if name.split(".")[1].isdigit() else -1
        label = pieces[i][3] if 0 <= i < len(pieces) else name
        return i18n.t("a floor piece ({piece})", piece=label)
    body = m.body(m.geom_bodyid[g]).name
    if rng is not None and any(o.name == body for o in getattr(rng, "obstacles", ())):
        return i18n.t("obstacle {name}", name=body)
    return body if body and body != "world" else name


# ---- its signals ----------------------------------------------------------------------------------------------------


class PartnerMonitor:
    """The partner's signals: height (in), tilt (deg) and moved (in), from where it settled (see the module)."""

    def __init__(self, world, info: PartnerInfo):
        from ..climb.run import SETTLE_S
        self.world, self.info = world, info
        self.settle_s = SETTLE_S
        self.body = world.model.body(info.body).id
        self._start: tuple[float, np.ndarray] | None = None     # (lowest corner z, center xy) at the reset
        self._rest: tuple[float, np.ndarray] | None = None      # ... once settled

    def _now(self) -> tuple[float, np.ndarray]:
        d = self.world.data
        r = d.xmat[self.body].reshape(3, 3)
        z = float(np.min(d.xpos[self.body][2] + self.info.corners @ r[2]))
        return z, d.xpos[self.body][:2].copy()

    def reset(self) -> None:
        """After World.reset (its kinematics current): the start is the reference until it settles."""
        self._start, self._rest = self._now(), None

    def after_physics(self) -> None:
        if self._rest is None and float(self.world.data.time) >= self.settle_s - 0.5 * self.world.model.opt.timestep:
            self._rest = self._now()

    def _ref(self) -> tuple[float, np.ndarray]:
        ref = self._rest if self._rest is not None else self._start
        if self.info.hang is not None:      # hanging: its lift is from where it would stand on its wheels
            return self.info.stand_low, ref[1]
        return ref

    def height(self) -> float:
        """in: the frame's lowest bottom corner above its rested height (a hanging partner's: its standing height)."""
        return (self._now()[0] - self._ref()[0]) / IN

    def tilt(self) -> float:
        """deg: the chassis's z axis from vertical."""
        zz = float(self.world.data.xmat[self.body][8])
        return math.degrees(math.acos(max(-1.0, min(1.0, zz))))

    def moved(self) -> float:
        """in: how far its center has gone across the floor from where it settled."""
        return float(np.linalg.norm(self._now()[1] - self._ref()[1])) / IN

    def read(self, name: str) -> float:
        """partner.height, partner.tilt or partner.moved."""
        return {"partner.height": self.height, "partner.tilt": self.tilt, "partner.moved": self.moved}[name]()

    def state(self) -> dict:
        """For get_state: height_in, tilt_deg and moved_in."""
        return {"height_in": round(self.height(), 3), "tilt_deg": round(self.tilt(), 2),
                "moved_in": round(self.moved(), 3)}


SIGNALS = ("partner.height", "partner.tilt", "partner.moved")
