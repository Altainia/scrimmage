"""A part: a mechanism's bodies, joints, couplings, device slots and mount points, and its part.toml form.

Files use meters, kilograms and degrees; the dataclasses keep file units (degrees for hinge ranges and `rot`)
and the assembler converts. See docs/parts.md for the format."""

from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .. import i18n
from ..motors import MOTORS
from ..pneumatics.model import CylinderSpec
from .toml_writer import dumps

FORMAT = 1
AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
SHAPES = ("box", "cylinder", "sphere", "mesh", "capsule")
GEOM_NAME_RE = re.compile(r"[A-Za-z0-9_.]+$")
ZERO3 = (0.0, 0.0, 0.0)
VALVE_SLOT = "valve"                             # a cylinder's valve slot (a part has at most one cylinder)


class Where(str):
    """A [[part]] entry's place, as messages name it ("parts[2] 'fl'"), knowing the entry's name: the prefix of its
    fields on the Robot tab (`part`, None when the entry has no name that is text)."""
    part: str | None

    def __new__(cls, text: str, part: str | None = None):
        s = super().__new__(cls, text)
        s.part = part
        return s


def fields_at(where, field: str = "part") -> tuple[tuple[str, str], ...]:
    """The Robot tab's (part, field) a mistake at `where` (a Where) is about; () when it has no part name."""
    part = getattr(where, "part", None)
    return ((part, field),) if part is not None else ()


class PartError(ValueError):
    """A mistake in a part or a [[part]] entry. The message names where and which key. `fields` says which of the
    Robot tab's fields it is about, (part name, field) pairs ("ids:<slot>", "ids", "attach", "name", "param:<name>",
    "part"): assembler/validate.py places it by them, not by its words (spec L3), so the message can be translated.
    `where=` (a Where) and `field=` give the one field; a mistake that names no part has none (the status line)."""

    def __init__(self, message: str = "", *, where=None, field: str = "part", fields=()):
        super().__init__(message)
        self.fields = tuple(fields) or fields_at(where, field)


class Noted(str):
    """A warning (text) that knows the Robot tab's fields it is about, as PartError.fields."""
    fields: tuple

    def __new__(cls, text: str, fields=()):
        s = super().__new__(cls, text)
        s.fields = tuple(fields)
        return s


def model_timestep(cfg: dict, default: float | None = None) -> float | None:
    """The config's [model] timestep (s), checked before anything uses it; `default` when it isn't given."""
    if "timestep" not in cfg.get("model", {}):
        return default
    t = cfg["model"]["timestep"]
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t <= 0:
        raise PartError(i18n.t("{model_table} timestep must be a positive number of seconds, not {value}",
                               value=repr(t), model_table="[model]"))
    return float(t)


@dataclass
class Geom:
    shape: str                                   # one of SHAPES
    size: tuple[float, ...] | str                # box: full x, y, z; cylinder, capsule: radius, length; sphere: (r,);
                                                 # mesh: path. A capsule's length is between its end caps' centers,
                                                 # along the geom's z (turned with rot)
    axis: str = "z"                              # cylinders only
    pos: tuple[float, float, float] = ZERO3
    rot: tuple[float, float, float] = ZERO3
    collide: bool = True
    friction: float | None = None
    color: tuple[float, float, float, float] | None = None
    condim: int | None = None
    solref: tuple[float, float] | None = None
    tread: bool = False                          # a drive wheel's tread: its contact is set from the drivetrain's
                                                 # tread stiffness and damping (parts/assemble.py, tread_solref)
    name: str | None = None                      # the geom's name in the model, after the part instance's prefix
    priority: int = 0                            # MuJoCo geom priority: in a contact the higher one's friction,
                                                 # condim and softness are used (field geoms are 0)


@dataclass
class Joint:
    name: str
    kind: str                                    # "hinge" | "slide"
    axis: tuple[float, float, float]
    range: tuple[float, float] | None = None     # degrees (hinge) or meters (slide)
    damping: float = 0.0
    frictionloss: float = 0.0
    armature: float = 0.0
    spring: tuple[float, float] | None = None    # (stiffness, rest) rest in degrees or meters
    limit_solref: tuple[float, float] | None = None   # the range's stops: [timeconst, dampratio] (MuJoCo's default)


@dataclass
class Body:
    name: str
    parent: str | None
    mass: float
    geoms: list[Geom]
    pos: tuple[float, float, float] = ZERO3
    rot: tuple[float, float, float] = ZERO3
    joint: Joint | None = None
    com: tuple[float, float, float] | None = None
    inertia: tuple[float, float, float] | None = None   # diagonal, about com; needs com
    inertia_rot: tuple[float, float, float] | None = None   # roll, pitch, yaw deg of the inertia axes in the body frame; needs inertia


@dataclass
class Coupling:
    joints: tuple[str, str]                      # (a, b): b = ratio * a
    ratio: float


@dataclass
class Loop:
    """A pin closing a kinematic loop: body1 and body2 are held together at `pos`, given in body1's frame."""
    body1: str
    body2: str
    pos: tuple[float, float, float]


@dataclass
class MotorSlot:
    slot: str
    joint: str
    motor: str
    count: int = 1
    gearing: float = 1.0                         # motor radians per joint unit (rad or m)
    invert: bool = False
    efficiency: float = 1.0


@dataclass
class EncoderSlot:
    slot: str
    joint: str
    ratio: float = 1.0                           # sensor turns per joint unit (turn or m)
    invert: bool = False


@dataclass
class GyroSlot:
    slot: str
    body: str


@dataclass
class Mount:
    name: str
    body: str
    pos: tuple[float, float, float] = ZERO3
    rot: tuple[float, float, float] = ZERO3


@dataclass
class Part:
    name: str
    bodies: list[Body]
    couplings: list[Coupling] = field(default_factory=list)
    loops: list[Loop] = field(default_factory=list)
    motors: list[MotorSlot] = field(default_factory=list)
    encoders: list[EncoderSlot] = field(default_factory=list)
    gyros: list[GyroSlot] = field(default_factory=list)
    mounts: list[Mount] = field(default_factory=list)
    base_dir: Path | None = None                 # where mesh paths resolve
    warnings: list[str] = field(default_factory=list)   # notes from loading (the plugin overlay); not in part.toml
    grips: dict = field(default_factory=dict)           # body -> grip material or number (parts/grip.py), from the
    #                                                     overlay or the config; not in part.toml
    cylinders: list[CylinderSpec] = field(default_factory=list)   # the stock cylinder's only (D2); not in part.toml.
    # Its pins name the part's own bodies, or (pin_b, the push form) another part's body as "<instance>.<body>";
    # length None means the pins' distance at the assembled pose. Each cylinder's valve is the slot VALVE_SLOT.
    grip_provenance: dict = field(default_factory=dict)  # body -> the Provenance of a grip from a measurement file
    grip_base: dict = field(default_factory=dict)  # gripped body -> the friction part.toml gave it (grip.part_friction)

    def slot_kinds(self) -> dict[str, str]:
        out = {s.slot: "motor" for s in self.motors}
        out.update({s.slot: "encoder" for s in self.encoders})
        out.update({s.slot: "gyro" for s in self.gyros})
        if self.cylinders:
            out[VALVE_SLOT] = "valve"
        return out

    def joint_names(self) -> list[str]:
        return [b.joint.name for b in self.bodies if b.joint is not None]


# ---------------------------------------------------------------------------------------------- helpers

def quat_from_rpy(rot_deg) -> list[float]:
    """Roll, pitch, yaw in degrees about the fixed x, y, z axes (applied in that order) as a wxyz quaternion."""
    def axis_quat(i: int, a: float) -> list[float]:
        q = [math.cos(a / 2), 0.0, 0.0, 0.0]
        q[1 + i] = math.sin(a / 2)
        return q

    def mul(p, q):
        pw, px, py, pz = p
        qw, qx, qy, qz = q
        return [pw * qw - px * qx - py * qy - pz * qz, pw * qx + px * qw + py * qz - pz * qy,
                pw * qy - px * qz + py * qw + pz * qx, pw * qz + px * qy - py * qx + pz * qw]

    r, p, y = (math.radians(float(v)) for v in rot_deg)
    return mul(axis_quat(2, y), mul(axis_quat(1, p), axis_quat(0, r)))


class _Ctx:
    """Builds error messages: '<source>: <where>: <message>'."""

    def __init__(self, source: str):
        self.source = source

    def err(self, where: str, msg: str) -> PartError:
        return PartError(f"{self.source}: {where}: {msg}" if where else f"{self.source}: {msg}")

    def keys(self, d: dict, allowed: tuple[str, ...], where: str) -> None:
        if not isinstance(d, dict):
            raise self.err(where, i18n.t("must be a table, not {value}", value=repr(d)))
        for k in d:
            if k not in allowed:
                raise self.err(where, i18n.t("unknown key {key}; allowed: {allowed}",
                                             key=repr(k), allowed=', '.join(allowed)))

    def num(self, d: dict, key: str, where: str, default=None, *, positive=False, nonneg=False) -> float:
        if key not in d:
            if default is None:
                raise self.err(where, i18n.t("missing {key}", key=key))
            return default
        v = d[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise self.err(where, i18n.t("{key} must be a number, not {value}", key=key, value=repr(v)))
        if not math.isfinite(v):
            raise self.err(where, i18n.t("{key} must be a finite number, not {value}", key=key, value=v))
        if positive and not v > 0:
            raise self.err(where, i18n.t("{key} must be greater than 0, got {value}", key=key, value=v))
        if nonneg and v < 0:
            raise self.err(where, i18n.t("{key} must be 0 or more, got {value}", key=key, value=v))
        return float(v)

    def vec(self, d: dict, key: str, where: str, n: int = 3, default=None) -> tuple[float, ...]:
        if key not in d:
            if default is None:
                raise self.err(where, i18n.t("missing {key}", key=key))
            return default
        v = d[key]
        if not (isinstance(v, list) and len(v) == n and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)):
            raise self.err(where, i18n.t("{key} must be a list of {n} numbers, not {value}",
                                         key=key, n=n, value=repr(v)))
        if not all(math.isfinite(x) for x in v):
            raise self.err(where, i18n.t("{key} must contain only finite numbers, not {value}", key=key,
                                         value=repr(v)))
        return tuple(float(x) for x in v)

    def flag(self, d: dict, key: str, where: str, default: bool = False) -> bool:
        v = d.get(key, default)
        if not isinstance(v, bool):
            raise self.err(where, i18n.t("{key} must be true or false, not {value}", key=key, value=repr(v)))
        return v

    def name(self, d: dict, key: str, where: str) -> str:
        if key not in d:
            raise self.err(where, i18n.t("missing {key}", key=key))
        v = d[key]
        if not isinstance(v, str) or not v:
            raise self.err(where, i18n.t("{key} must be a non-empty string, not {value}", key=key, value=repr(v)))
        return v

    def axis(self, v, where: str, key: str) -> tuple[float, float, float]:
        """Axis names or a [x, y, z] vector. A vector that is already unit length is kept exactly as written,
        so a saved and reloaded axis doesn't drift by a rounding error."""
        if isinstance(v, str) and v in AXES:
            return AXES[v]
        if isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                       and math.isfinite(x) for x in v):
            n = math.sqrt(sum(float(x) ** 2 for x in v))
            if n > 0:
                if abs(n - 1.0) <= 1e-12:
                    return tuple(float(x) for x in v)
                return tuple(float(x) / n for x in v)
        raise self.err(where, i18n.t("{key} must be \"{x}\", \"{y}\", \"{z}\" or a non-zero [x, y, z], not {value}",
                                     key=key, value=repr(v), x="x", y="y", z="z"))


def _item(c: _Ctx, kind: str, item, i: int, key: str) -> str:
    """Where an entry of a [[list]] section is, for error messages: its name if it has one, else its index."""
    name = item.get(key) if isinstance(item, dict) else None
    return f"{kind} {name!r}" if isinstance(name, str) and name else f"{kind}[{i}]"


GEOM_KEYS = SHAPES + ("axis", "pos", "rot", "collide", "friction", "color", "condim", "solref", "tread", "name",
                      "priority")
JOINT_KEYS = ("name", "hinge", "slide", "range", "damping", "frictionloss", "armature", "spring", "limit_solref")
LOOP_KEYS = ("body1", "body2", "pos")
CYLINDER_KEYS = ("bore", "rod", "stroke", "friction", "conductance", "pin_a", "pin_b", "length")
BODY_KEYS = ("name", "parent", "mass", "pos", "rot", "geom", "joint", "com", "inertia", "inertia_rot")


def _geom(c: _Ctx, g: dict, where: str, base_dir: Path | None) -> Geom:
    c.keys(g, GEOM_KEYS, where)
    shapes = [s for s in SHAPES if s in g]
    if len(shapes) != 1:
        raise c.err(where, i18n.t("a geom needs exactly one of {shapes}", shapes=', '.join(SHAPES)))
    shape = shapes[0]
    if shape == "box":
        size = c.vec(g, "box", where)
        if min(size) <= 0:
            raise c.err(where, i18n.t("box sizes must be greater than 0"))
    elif shape in ("cylinder", "capsule"):
        size = c.vec(g, shape, where, 2)
        if min(size) <= 0:
            raise c.err(where, i18n.t("{shape} [radius, length] must be greater than 0", shape=shape))
    elif shape == "sphere":
        size = (c.num(g, "sphere", where, positive=True),)
    else:
        size = g["mesh"]
        if not isinstance(size, str):
            raise c.err(where, i18n.t("mesh must be a file path, not {size}", size=repr(size)))
        if base_dir is not None and not (base_dir / size).is_file():
            raise c.err(where, i18n.t("mesh file not found: {n}", n=base_dir / size))
    if "axis" in g and shape != "cylinder":
        raise c.err(where, i18n.t("axis is for cylinders only; turn other shapes with rot"))
    axis = g.get("axis", "z")
    if not isinstance(axis, str) or axis not in AXES:
        raise c.err(where, i18n.t("axis must be \"{x}\", \"{y}\" or \"{z}\", not {axis}", axis=repr(axis), x="x",
                                  y="y", z="z"))
    color = c.vec(g, "color", where, 4) if "color" in g else None
    solref = c.vec(g, "solref", where, 2) if "solref" in g else None
    collide = c.flag(g, "collide", where, default=True)
    tread = c.flag(g, "tread", where)
    if tread and not collide:
        raise c.err(where, i18n.t("a tread geom must collide: remove {collide_setting} or {tread_setting}",
                                  collide_setting="collide = false", tread_setting="tread = true"))
    condim = g.get("condim")
    if condim is not None and condim not in (1, 3, 4, 6):
        raise c.err(where, i18n.t("condim must be 1, 3, 4 or 6, not {condim}", condim=repr(condim)))
    name = g.get("name")
    if name is not None and not (isinstance(name, str) and GEOM_NAME_RE.match(name)):
        raise c.err(where, i18n.t("name must be letters, digits, _ and . (it is prefixed with the part's name), "
                                  "not {name}", name=repr(name)))
    if name is not None and tread:
        raise c.err(where, i18n.t("a tread geom is named by its body and index (<body>.tread<i>): remove name"))
    priority = g.get("priority", 0)
    if isinstance(priority, bool) or not isinstance(priority, int) or priority < 0:
        raise c.err(where, i18n.t("priority must be a whole number, 0 or more, not {priority}",
                                  priority=repr(priority)))
    return Geom(shape, size, axis, c.vec(g, "pos", where, default=ZERO3),
                c.vec(g, "rot", where, default=ZERO3), collide,
                c.num(g, "friction", where, positive=True) if "friction" in g else None, color, condim, solref,
                tread, name, priority)


def tread_name(body: str, i: int) -> str:
    """A tread geom's name in its part: its body and its index among the body's geoms."""
    return f"{body}.tread{i}"


def _joint(c: _Ctx, j: dict, where: str) -> Joint:
    c.keys(j, JOINT_KEYS, where)
    name = c.name(j, "name", where)
    kinds = [k for k in ("hinge", "slide") if k in j]
    if len(kinds) != 1:
        raise c.err(where, i18n.t("a joint needs exactly one of {hinge_setting} or {slide_setting}",
                                  hinge_setting="hinge = <axis>", slide_setting="slide = <axis>"))
    kind = kinds[0]
    rng = None
    if "range" in j:
        rng = c.vec(j, "range", where, 2)
        if rng[0] >= rng[1]:
            raise c.err(where, i18n.t("range must be [min, max] with min < max, got {value}", value=list(rng)))
    spring = None
    if "spring" in j:
        s = j["spring"]
        c.keys(s, ("stiffness", "rest"), where + " spring")
        spring = (c.num(s, "stiffness", where + " spring", nonneg=True), c.num(s, "rest", where + " spring", 0.0))
    limit_solref = None
    if "limit_solref" in j:
        if rng is None:
            raise c.err(where, i18n.t("limit_solref sets the range's stops: give the joint a range, or remove "
                                      "limit_solref"))
        limit_solref = c.vec(j, "limit_solref", where, 2)
        if min(limit_solref) <= 0:
            raise c.err(where, i18n.t("limit_solref must be [timeconst, dampratio], both greater than 0, not "
                                      "{value}", value=list(limit_solref)))
    return Joint(name, kind, c.axis(j[kind], where, kind), rng, c.num(j, "damping", where, 0.0, nonneg=True),
                 c.num(j, "frictionloss", where, 0.0, nonneg=True), c.num(j, "armature", where, 0.0, nonneg=True),
                 spring, limit_solref)


def _table_list(c: _Ctx, d: dict, key: str) -> list[dict]:
    v = d.get(key, [])
    if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
        raise c.err("", i18n.t("[[{key}]] must be an array of tables", key=key))
    return v


def part_from_dict(d: dict, source: str, base_dir: Path | None) -> Part:
    """Validate a parsed part.toml (or a stock builder's dict) into a Part."""
    c = _Ctx(source)
    top = ("part", "body", "loop", "coupling", "motor", "encoder", "gyro", "mount")
    if "(stock " in source:     # only a stock builder makes a cylinder (D2): in a part.toml it's an unknown key
        top += ("cylinder",)
    c.keys(d, top, "")
    head = d.get("part")
    if not isinstance(head, dict):
        raise c.err("", i18n.t("missing the {part_table} table ({name_setting}, {format_setting})",
                               part_table="[part]", name_setting="name = \"...\"", format_setting="format = 1"))
    c.keys(head, ("name", "format"), "[part]")
    fmt = head.get("format", FORMAT)
    if not isinstance(fmt, int) or isinstance(fmt, bool):
        raise c.err("[part]", i18n.t("format must be an integer, not {format}", format=repr(fmt)))
    if fmt < 1:
        raise c.err("[part]", i18n.t("format must be 1 or more, not {format}", format=fmt))
    if fmt > FORMAT:
        raise c.err("[part]", i18n.t("format {format} was made by a newer scrimmage (this one reads {newest}); "
                                     "upgrade scrimmage", format=fmt, newest=FORMAT))
    name = c.name(head, "name", "[part]")

    bodies: list[Body] = []
    seen_joints: set[str] = set()
    geom_names: dict[str, str] = {}          # a named geom: where it is defined
    tread_names: dict[str, tuple[str, int]] = {}    # a tread geom's automatic name: its body and index
    for i, b in enumerate(_table_list(c, d, "body")):
        where = _item(c, "body", b, i, "name")
        c.keys(b, BODY_KEYS, where)
        bname = c.name(b, "name", where)
        where = f"body {bname!r}"
        if any(x.name == bname for x in bodies):
            raise c.err(where, i18n.t("defined twice"))
        parent = b.get("parent")
        if not bodies:
            if parent is not None:
                raise c.err(where, i18n.t("the first body is the part's root and has no parent"))
        elif not isinstance(parent, str) or not any(x.name == parent for x in bodies):
            raise c.err(where, i18n.t("parent {parent} isn't an earlier body ({items})",
                                      parent=repr(parent), items=', '.join(x.name for x in bodies)))
        geom_list = b.get("geom", [])
        if not (isinstance(geom_list, list) and all(isinstance(g, dict) for g in geom_list)):
            raise c.err(where, i18n.t("geom must be an array like `{geom_box}`", geom_box="geom = [{ box = [...] }]"))
        geoms = [_geom(c, g, f"{where} geom {k}", base_dir) for k, g in enumerate(geom_list)]
        for k, g in enumerate(geoms):
            if g.name is not None:
                if g.name in geom_names:
                    raise c.err(f"{where} geom {k}", i18n.t("geom name {name} is used twice in the part",
                                                            name=repr(g.name)))
                geom_names[g.name] = f"{where} geom {k}"
            if g.tread:
                tread_names[tread_name(bname, k)] = (bname, k)
        joint = None
        if "joint" in b:
            if not bodies:
                raise c.err(where, i18n.t("the root body {bname} can't have a joint: it moves with what it's "
                                          "attached to", bname=repr(bname)))
            joint = _joint(c, b["joint"], f"{where} joint")
            if joint.name in seen_joints:
                raise c.err(where, i18n.t("joint {name} is defined twice", name=repr(joint.name)))
            seen_joints.add(joint.name)
        com = c.vec(b, "com", where) if "com" in b else None
        inertia = c.vec(b, "inertia", where) if "inertia" in b else None
        if inertia is not None and (com is None or min(inertia) <= 0):
            raise c.err(where, i18n.t("inertia needs com, and its three values must be greater than 0"))
        if inertia is not None and any(2 * v > sum(inertia) for v in inertia):   # what MuJoCo refuses
            raise c.err(where, i18n.t("inertia {inertia} can't be a real body's: none of the three values can be "
                                      "more than the other two added together",
                                      inertia="[" + ", ".join(f"{v:g}" for v in inertia) + "]"))
        inertia_rot = c.vec(b, "inertia_rot", where) if "inertia_rot" in b else None
        if inertia_rot is not None and inertia is None:
            raise c.err(where, i18n.t("inertia_rot needs inertia (and com)"))
        bodies.append(Body(bname, parent, c.num(b, "mass", where, positive=True), geoms,
                           c.vec(b, "pos", where, default=ZERO3), c.vec(b, "rot", where, default=ZERO3),
                           joint, com, inertia, inertia_rot))
    if not bodies:
        raise c.err("", i18n.t("a part needs at least one {body_table}", body_table="[[body]]"))
    for clash in sorted(geom_names.keys() & tread_names.keys()):
        body, k = tread_names[clash]
        raise c.err(geom_names[clash], i18n.t("geom name {clash} is the name of body {body}'s tread geom {key} "
                                              "(treads are named <body>.tread<i>): rename the other geom",
                                              clash=repr(clash), body=repr(body), key=k))
    joints = sorted(seen_joints)
    body_names = [b.name for b in bodies]

    def need_joint(j, where):
        if j not in seen_joints:
            raise c.err(where, i18n.t("no joint {joint}; the part's joints: {joints}",
                                      joint=repr(j), joints=', '.join(joints) or i18n.t("none")))
        return j

    loops = []
    for i, lp in enumerate(_table_list(c, d, "loop")):
        where = f"loop[{i}]"
        c.keys(lp, LOOP_KEYS, where)
        ends = []
        for key in ("body1", "body2"):
            n = c.name(lp, key, where)
            if n not in body_names:
                raise c.err(where, i18n.t("{key}: no body {n}; the part's bodies: {body_names}",
                                          key=key, n=repr(n), body_names=', '.join(body_names)))
            ends.append(n)
        if ends[0] == ends[1]:
            raise c.err(where, i18n.t("a loop pins two different bodies, not {ends} to itself", ends=repr(ends[0])))
        loops.append(Loop(ends[0], ends[1], c.vec(lp, "pos", where)))

    couplings = []
    for i, cp in enumerate(_table_list(c, d, "coupling")):
        c.keys(cp, ("joints", "ratio"), f"coupling[{i}]")
        where = "coupling"
        js = cp.get("joints")
        if not (isinstance(js, list) and len(js) == 2 and all(isinstance(x, str) for x in js)):
            raise c.err(where, i18n.t("joints must be [\"{a}\", \"{b}\"] (b = ratio * a)", a="a", b="b"))
        ratio = c.num(cp, "ratio", where)
        if ratio == 0:
            raise c.err(where, i18n.t("ratio must not be 0"))
        couplings.append(Coupling((need_joint(js[0], where), need_joint(js[1], where)), ratio))

    slots: set[str] = set()

    def slot_name(s: dict, where: str, kind: str) -> str:
        n = c.name(s, "slot", where)
        if n in slots:
            raise c.err(f"{kind} slot {n!r}", i18n.t("slot {n} is defined twice", n=repr(n)))
        slots.add(n)
        return n

    motors = []
    for i, s in enumerate(_table_list(c, d, "motor")):
        where = _item(c, "motor slot", s, i, "slot")
        c.keys(s, ("slot", "joint", "motor", "count", "gearing", "invert", "efficiency"), where)
        n = slot_name(s, where, "motor")
        where = f"motor slot {n!r}"
        motor = c.name(s, "motor", where)
        if motor not in MOTORS:
            raise c.err(where, i18n.t("Unknown motor {motor}. Known motors: {motors}",
                                      motor=repr(motor), motors=', '.join(sorted(MOTORS))))
        count = s.get("count", 1)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise c.err(where, i18n.t("count must be a whole number of motors (1 or more), not {n}", n=repr(count)))
        motors.append(MotorSlot(n, need_joint(c.name(s, "joint", where), where), motor, count,
                                c.num(s, "gearing", where, 1.0, positive=True), c.flag(s, "invert", where),
                                c.num(s, "efficiency", where, 1.0, positive=True)))
    encoders = []
    for i, s in enumerate(_table_list(c, d, "encoder")):
        where = _item(c, "encoder slot", s, i, "slot")
        c.keys(s, ("slot", "joint", "ratio", "invert"), where)
        n = slot_name(s, where, "encoder")
        where = f"encoder slot {n!r}"
        ratio = c.num(s, "ratio", where, 1.0)
        if ratio == 0:
            raise c.err(where, i18n.t("ratio must not be 0"))
        encoders.append(EncoderSlot(n, need_joint(c.name(s, "joint", where), where), ratio,
                                    c.flag(s, "invert", where)))
    gyros = []
    for i, s in enumerate(_table_list(c, d, "gyro")):
        where = _item(c, "gyro slot", s, i, "slot")
        c.keys(s, ("slot", "body"), where)
        n = slot_name(s, where, "gyro")
        where = f"gyro slot {n!r}"
        body = c.name(s, "body", where)
        if body not in body_names:
            raise c.err(where, i18n.t("no body {body}; the part's bodies: {body_names}",
                                      body=repr(body), body_names=', '.join(body_names)))
        gyros.append(GyroSlot(n, body))
    mounts = []
    for i, m in enumerate(_table_list(c, d, "mount")):
        where = _item(c, "mount", m, i, "name")
        c.keys(m, ("name", "body", "pos", "rot"), where)
        n = c.name(m, "name", where)
        where = f"mount {n!r}"
        if any(x.name == n for x in mounts):
            raise c.err(where, i18n.t("defined twice"))
        body = c.name(m, "body", where)
        if body not in body_names:
            raise c.err(where, i18n.t("no body {body}; the part's bodies: {body_names}",
                                      body=repr(body), body_names=', '.join(body_names)))
        mounts.append(Mount(n, body, c.vec(m, "pos", where, default=ZERO3), c.vec(m, "rot", where, default=ZERO3)))
    cylinders = []
    if "cylinder" in d:
        if VALVE_SLOT in slots:
            raise c.err("cylinder", i18n.t("slot {valve_slot} is the cylinder's valve; rename the other slot",
                                           valve_slot=repr(VALVE_SLOT)))
        cylinders.append(_cylinder(c, d["cylinder"], body_names))
    return Part(name, bodies, couplings, loops, motors, encoders, gyros, mounts, base_dir, cylinders=cylinders)


def _cylinder(c: _Ctx, cy: dict, body_names: list[str]) -> CylinderSpec:
    """The cylinder table: sizes in m, friction in N, conductance in m^3/(s Pa), and pin_a, pin_b as
    { body, pos }. pin_a is on one of the part's bodies; pin_b may be on another part's, as "<instance>.<body>"."""
    where = "cylinder"
    c.keys(cy, CYLINDER_KEYS, where)
    bore, rod, stroke = (c.num(cy, k, where, positive=True) for k in ("bore", "rod", "stroke"))
    if rod >= bore:
        raise c.err(where, i18n.t("the rod ({rod} m) must be thinner than the bore ({bore} m)", rod=rod, bore=bore))
    pins = []
    for key in ("pin_a", "pin_b"):
        pin = cy.get(key)
        c.keys(pin, ("body", "pos"), f"{where} {key}")
        body = c.name(pin, "body", f"{where} {key}")
        if body not in body_names and not (key == "pin_b" and "." in body):
            raise c.err(f"{where} {key}", i18n.t("no body {body}; the part's bodies: {body_names}",
                                                 body=repr(body), body_names=', '.join(body_names)))
        pins.append((body, c.vec(pin, "pos", f"{where} {key}", default=ZERO3)))
    length = c.num(cy, "length", where, positive=True) if "length" in cy else None
    return CylinderSpec("cylinder", bore, rod, stroke, c.num(cy, "friction", where, nonneg=True),
                        c.num(cy, "conductance", where, positive=True), pin_a=pins[0], pin_b=pins[1], length=length)


def _axis_out(v: tuple[float, float, float]):
    for k, a in AXES.items():
        if tuple(v) == a:
            return k
    return list(v)


def _without_defaults(pairs, defaults: dict) -> dict:
    """Keep (key, value) pairs except those whose value equals the key's default."""
    return {k: v for k, v in pairs if not (k in defaults and v == defaults[k])}


def part_to_dict(p: Part) -> dict:
    """The part as part.toml data (defaults left out)."""
    def geom(g: Geom) -> dict:
        out: dict = {g.shape: (list(g.size) if g.shape in ("box", "cylinder", "capsule") else
                               g.size[0] if g.shape == "sphere" else g.size)}
        if g.axis != "z":
            out["axis"] = g.axis
        for key, val, dflt in (("pos", g.pos, ZERO3), ("rot", g.rot, ZERO3)):
            if tuple(val) != dflt:
                out[key] = list(val)
        if not g.collide:
            out["collide"] = False
        if g.tread:
            out["tread"] = True
        if g.name is not None:
            out["name"] = g.name
        if g.priority:
            out["priority"] = g.priority
        for key in ("friction", "condim"):
            if getattr(g, key) is not None:
                out[key] = getattr(g, key)
        for key in ("color", "solref"):
            if getattr(g, key) is not None:
                out[key] = list(getattr(g, key))
        return out

    def joint(j: Joint) -> dict:
        out: dict = {"name": j.name, j.kind: _axis_out(j.axis)}
        if j.range is not None:
            out["range"] = list(j.range)
        for key in ("damping", "frictionloss", "armature"):
            if getattr(j, key):
                out[key] = getattr(j, key)
        if j.spring is not None:
            out["spring"] = {"stiffness": j.spring[0], "rest": j.spring[1]}
        if j.limit_solref is not None:
            out["limit_solref"] = list(j.limit_solref)
        return out

    bodies = []
    for b in p.bodies:
        out: dict = {"name": b.name}
        if b.parent is not None:
            out["parent"] = b.parent
        out["mass"] = b.mass
        for key, val in (("pos", b.pos), ("rot", b.rot)):
            if tuple(val) != ZERO3:
                out[key] = list(val)
        if b.com is not None:
            out["com"] = list(b.com)
        if b.inertia is not None:
            out["inertia"] = list(b.inertia)
        if b.inertia_rot is not None:
            out["inertia_rot"] = list(b.inertia_rot)
        out["geom"] = [geom(g) for g in b.geoms]
        if b.joint is not None:
            out["joint"] = joint(b.joint)
        bodies.append(out)
    d: dict = {"part": {"name": p.name, "format": FORMAT}, "body": bodies}
    if p.loops:
        d["loop"] = [{"body1": lp.body1, "body2": lp.body2, "pos": list(lp.pos)} for lp in p.loops]
    if p.couplings:
        d["coupling"] = [{"joints": list(cp.joints), "ratio": cp.ratio} for cp in p.couplings]
    if p.motors:
        d["motor"] = [_without_defaults(
            (("slot", s.slot), ("joint", s.joint), ("motor", s.motor), ("count", s.count),
             ("gearing", s.gearing), ("invert", s.invert), ("efficiency", s.efficiency)),
            {"count": 1, "invert": False, "efficiency": 1.0}) for s in p.motors]
    if p.encoders:
        d["encoder"] = [_without_defaults(
            (("slot", s.slot), ("joint", s.joint), ("ratio", s.ratio), ("invert", s.invert)),
            {"ratio": 1.0, "invert": False}) for s in p.encoders]
    if p.gyros:
        d["gyro"] = [{"slot": s.slot, "body": s.body} for s in p.gyros]
    if p.mounts:
        d["mount"] = [_without_defaults(
            (("name", m.name), ("body", m.body), ("pos", list(m.pos)), ("rot", list(m.rot))),
            {"pos": [0.0, 0.0, 0.0], "rot": [0.0, 0.0, 0.0]}) for m in p.mounts]
    return d


def load_part(path: Path) -> Part:
    """Load a part folder (holding part.toml) or a part.toml file."""
    path = Path(path)
    file = path / "part.toml" if path.is_dir() else path
    try:
        raw = tomllib.loads(file.read_text())
    except FileNotFoundError:
        raise PartError(i18n.t("part file not found: {file}", file=file)) from None
    except (OSError, UnicodeDecodeError) as e:
        raise PartError(i18n.t("can't read part file {file}: {error}", file=file, error=e)) from None
    except tomllib.TOMLDecodeError as e:
        raise PartError(f"{file}: {e}") from None
    part = part_from_dict(raw, str(file), file.parent)
    edits = file.parent / "scrimmage-edits.toml"            # the plugin overlay (parts/overlay.py)
    if edits.is_file():
        from .overlay import apply_overlay
        try:
            text = tomllib.loads(edits.read_text())
        except (OSError, UnicodeDecodeError) as e:
            raise PartError(i18n.t("can't read {edits}: {error}", edits=edits, error=e)) from None
        except tomllib.TOMLDecodeError as e:
            raise PartError(f"{edits}: {e}") from None
        part, notes = apply_overlay(part, text, str(edits))
        part.warnings = notes
    return part


def dump_part(p: Part, path: Path) -> None:
    Path(path).write_text(dumps(part_to_dict(p)))
