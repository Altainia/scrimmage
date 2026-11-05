"""A passive partner robot: a robot config's `[partner]`, a second drive base with no robot program.

    [partner]
    start = [4.0, 4.0, 0.0]     # required: x (m), y (m), heading (deg), field coordinates like [robot] start_pose
    length = 0.7                # m, the frame without bumpers
    width = 0.7                 # m
    mass = 60.0                 # kg, the whole partner (an estimate of a full robot with bumpers and battery)
    bumper_clearance = 0.02     # m, the bumpers' bottom above the carpet
    brake = true                # its drive braked (true) or coasting (false)

It is the stock chassis with the stock differential (6-wheel tank) drive under it, every name prefixed `partner.`
(spec C7): bodies `partner.chassis`, `partner.rails`, `partner.left_1` ..., and a free joint `partner.free`. It is not
the robot: it isn't in Built.instances, its treads aren't in Built.tread_geoms, and it has no motors or bindings, so
the robot's drivetrain, contacts and checks never see it (each finds the robot by its root body). Its geoms touch the
robot, the field and game pieces (contype 3, conaffinity 3); its own bodies are excluded from touching each other.
Its start is built into the model like the robot's, its wheels on the carpet, so World.reset puts it back there.

Its signals (PartnerMonitor), in inches and degrees: `partner.height`, the lowest of the frame's four bottom corners
above where it was once settled (spec C8: from its rested height, so its bumper clearance isn't lift);
`partner.tilt`, the chassis's z axis from vertical; and `partner.moved`, how far its center has gone across the floor
from where it settled. It counts as settled SETTLE_S after a reset (the Test tab's settle, climb/run.py); before that
the signals read from its start.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import mujoco
import numpy as np

from .. import units
from .model import PartError, quat_from_rpy

PARTNER_KEYS = ("length", "width", "mass", "bumper_clearance", "brake", "start")
PREFIX = "partner."
PARTNER_BITS = {"contype": 3, "conaffinity": 3}     # touches the robot (2, 1), the field (1, 2) and balls (4, 7)
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
class PartnerSpec:
    start: tuple[float, float, float]       # x (m), y (m), heading (deg)
    length: float = DEFAULT_LENGTH
    width: float = DEFAULT_WIDTH
    mass: float = DEFAULT_MASS
    bumper_clearance: float = DEFAULT_BUMPER_CLEARANCE
    brake: bool = True


@dataclass
class PartnerInfo:
    body: str                               # the chassis body, "partner.chassis"
    wheels: list[str]                       # the wheel joints, "partner.left_1", ...
    start: tuple[float, float, float]       # x (m), y (m), heading (deg)
    corners: np.ndarray = field(default_factory=lambda: np.zeros((4, 3)))  # the frame's bottom corners, chassis frame
    outline: tuple[float, float] = (0.0, 0.0)   # m: the frame plus bumpers, length and width
    parts: list = field(default_factory=list)   # the two parts (assemble.Instance), for their treads' contacts


def _number(v, key: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise PartError(f"[partner] {key} must be a number, not {v!r}")
    return float(v)


def parse_partner(cfg: dict) -> PartnerSpec | None:
    """The config's [partner], checked (None without one); every mistake a PartError naming the key."""
    sec = cfg.get("partner")
    if sec is None:
        return None
    if not isinstance(sec, dict):
        raise PartError("[partner] must be a table")
    extra = [k for k in sec if k not in PARTNER_KEYS]
    if extra:
        raise PartError(f"[partner]: unknown key {extra[0]!r}; allowed: {', '.join(PARTNER_KEYS)}")
    start = sec.get("start")
    if start is None:
        raise PartError("[partner] needs start = [x, y, heading_deg]: where the partner robot starts")
    if not (isinstance(start, list) and len(start) == 3 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in start)):
        raise PartError(f"[partner] start must be [x, y, heading_deg], 3 finite numbers, not {start!r}")
    kw: dict = {"start": tuple(float(v) for v in start)}
    for key in ("length", "width"):
        if key in sec:
            kw[key] = _number(sec[key], key)
            if kw[key] < MIN_SIZE:
                raise PartError(f"[partner] {key} must be at least {MIN_SIZE:g} m (its drive's wheels fit inside the "
                                f"frame), not {kw[key]:g}")
    for key in ("mass", "bumper_clearance"):
        if key in sec:
            kw[key] = _number(sec[key], key)
            if kw[key] <= 0:
                raise PartError(f"[partner] {key} must be more than 0, not {kw[key]:g}")
    if "brake" in sec:
        if not isinstance(sec["brake"], bool):
            raise PartError(f"[partner] brake must be true (its wheels braked) or false (coasting), "
                            f"not {sec['brake']!r}")
        kw["brake"] = sec["brake"]
    return PartnerSpec(**kw)


def _parts(p: PartnerSpec) -> tuple:
    """The partner's chassis and drive (assemble.Instance), the chassis carrying all of `mass` the drive doesn't."""
    from .assemble import Instance
    from .stock import build_stock, stock_params
    where = "[partner]"
    dparams = {"track_width": p.width - TRACK_INSET, "wheelbase": p.length - WHEELBASE_INSET}
    drive = build_stock("differential", dparams, where)
    drive_mass = sum(b.mass for b in drive.bodies)
    if p.mass <= drive_mass:
        raise PartError(f"[partner] mass must be more than its drive's {drive_mass:g} kg, not {p.mass:g}")
    cparams = {"length": p.length, "width": p.width, "mass": p.mass - drive_mass,
               "bumper_clearance": p.bumper_clearance}
    chassis = build_stock("chassis", cparams, where)
    return (Instance("partner", chassis, "stock chassis", None, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), {}, where,
                     stock_params("chassis", cparams, where)),
            Instance("partner", drive, "stock differential", "partner.center", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), {},
                     where, stock_params("differential", dparams, where)))


def add_partner(spec: mujoco.MjSpec, cfg: dict) -> PartnerInfo | None:
    """Add the config's [partner] to `spec` (None, and nothing added, without one): the stock chassis at its start,
    its wheels on the carpet, with a free joint, and the stock differential under it."""
    from .assemble import _add_couplings, part_spec
    p = parse_partner(cfg)
    if p is None:
        return None
    chassis, drive = _parts(p)
    x, y, heading = p.start
    q = quat_from_rpy([0, 0, heading])
    root = part_spec(chassis.part, chassis.where).body(chassis.part.bodies[0].name)
    spec.worldbody.add_frame(pos=[x, y, 0.0], quat=list(q)).attach_body(root, PREFIX, "")
    body = spec.body(f"{PREFIX}{chassis.part.bodies[0].name}")
    body.add_freejoint(name=f"{PREFIX}free")
    mount = next(m for m in chassis.part.mounts if m.name == "center")
    rails = part_spec(drive.part, drive.where).body(drive.part.bodies[0].name)
    body.add_frame(pos=list(mount.pos), quat=list(quat_from_rpy(mount.rot))).attach_body(rails, PREFIX, "")
    _add_couplings(spec, "partner", drive.part)
    names = [f"{PREFIX}{b.name}" for inst in (chassis, drive) for b in inst.part.bodies]
    for name in names:
        for g in spec.body(name).geoms:
            if g.contype or g.conaffinity:          # a drawing-only geom (the heading marker, the rails) stays so
                g.contype, g.conaffinity = PARTNER_BITS["contype"], PARTNER_BITS["conaffinity"]
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            spec.add_exclude(bodyname1=a, bodyname2=b)
    wheels = [f"{PREFIX}{j}" for j in drive.part.joint_names()]
    if p.brake:
        b = brake_damping(drive.stock_params)
        for j in wheels:
            sj = spec.joint(j)
            sj.damping = [b, *list(sj.damping)[1:]]      # [linear, ...]: MuJoCo's damping terms
    prm = chassis.stock_params
    fh, bumper = prm["frame_height"], prm["bumper"]
    corners = np.array([[sx * p.length / 2, sy * p.width / 2, fh - 0.02] for sx in (1, -1) for sy in (1, -1)])
    return PartnerInfo(body.name, wheels, p.start, corners, (p.length + 2 * bumper, p.width + 2 * bumper),
                       [chassis, drive])


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


STARTS_INSIDE = "[partner] starts inside "    # check_start's refusals begin so; run.world_for words Setup's its own way


def _by(depth: float) -> str:
    """An overlap in words, in the Units setting: "4.72 in" or "120 mm"."""
    return units.fmt(depth, "length", fewer=1)


def check_start(info: PartnerInfo, robot_start: tuple[float, float, float], elements=()) -> None:
    """The partner's outline (frame plus bumpers) may not overlap the robot's start outline (ROBOT_OUTLINE) or any
    element standing on the carpet (testrange/elements.py's _on_floor, as a start's robot outline is checked)."""
    from ..testrange.elements import ROBOT_OUTLINE, _on_floor, _rect
    x, y, heading = info.start
    mine = _rect((x, y), heading, *info.outline)
    rx, ry, rh = robot_start
    depth = _overlap(mine, _rect((rx, ry), rh, ROBOT_OUTLINE, ROBOT_OUTLINE))
    if depth > 1e-9:
        raise PartError(f"{STARTS_INSIDE}the robot (their outlines overlap by {_by(depth)})")
    for e in elements:
        depth = max((_overlap(mine, f) for f in _on_floor(e, start=True)), default=0.0)
        if depth > 1e-9:
            raise PartError(f"{STARTS_INSIDE}element {e.name} (their outlines overlap by {_by(depth)})")


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
        return self._rest if self._rest is not None else self._start

    def height(self) -> float:
        """in: the frame's lowest bottom corner above its rested height."""
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
