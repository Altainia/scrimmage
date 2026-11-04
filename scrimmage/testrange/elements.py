"""Field elements in a range file: bumps, ramps, tilting platforms, the climbing kinds (bars, boxes and a tilting
switch) and the moving kinds (hinged and sliding panels), as `[[element]]` tables; and the range's `[climb]` scoring
rule.

SI units; angles in degrees. `center` is the element's (x, y) in field coordinates, like goals and starts. `yaw_deg`
turns the element about z from the field's +x; its `depth` or `length` runs along the element's local x and its
`width` along local y. Every length key also has an inch form, `<key>_in` (give one of the two); mass, inertia,
damping, angles and friction have none.

A bump is two ramps meeting at a crest: `crest` is the flat top's length (the 2026 BUMP has only a sharp ridge about
8 mm wide) and `lip` the toe lip where each ramp meets the carpet. A ramp climbs at `ramp_deg` to a flat `deck`, from
one end or from both (`two_sided`). A platform is a deck `height` above the carpet when level, on a double hinge,
with a ramp of `ramp_length` at each end beyond `length`; `damping` (N m s/rad) is an estimate a range file must
state.

A bar is a pipe of outer `diameter` along the element's local x, `length` end to end, its axis `height` above the
carpet, with a post under each end down to the carpet (`posts`, default true). With `stiffness` (N/m) the pipe rides a
vertical sprung slide (its `damping`, N s/m, default 0), standing in for the rung's bending; without, it is rigid.
`level` names one of the `[climb]` levels; the bar's `points` come from there. A box is a static block, `size`
(x, y, z) with its bottom `base_height` up; `ground = true` makes it floor for the terrain signals (a floor
protector, a step), otherwise it is structure (a truss). A rod is a straight round or square rod fixed to the field
between two ends, `base` and `tip` (x, y, z; also `base_in`, `tip_in`), `diameter` across (a square rod's side): a
peg, a pole, a coral branch. It has no `center` or `yaw_deg`: its ends place it. A switch is a bar hung `bar_drop`
below a hinge at `hinge_height` that turns about the element's local y: `mass`, its CoM `com_drop` below the hinge,
stops at +-`stop_deg`, joint `damping` (N m s/rad) and `level_deg`, within which the scoring rule calls it level.

A hinged element is a panel on a hinge (C1): a box of `size` (x, y, z), or (`shape = "disc"`) a disc of `diameter`
and `thickness` lying flat, its bottom `base_height` up. `hinge` names the edge it turns on, the panel as described
(angle 0 of the file): "bottom" or "top" (about local y), "left" (+y) or "right" (-y) or "front" (+x) or "back" (-x)
(about local z), or "center", through its center about its `axis` ("x", "y" or "z"; left out, its thinnest axis:
a disc's own axis, a spinning panel). `ground = true` makes it floor for the terrain signals (a drawbridge).
`range_deg` [lo, hi] are its stops (none without it), `rest_deg` where its spring rests (both angles from the
panel as described); `stiffness` is N m/deg, `breakaway` N m (C2), `damping` N m s/rad (left out, critical for the
panel's inertia about the hinge with a spring, else DEFAULT_MOVING_DAMPING). A sliding element is a box on a slide
along its local `axis` ("x", "y" or "z"): `range` [lo, hi] (m, or `range_in`), `rest` (m, or `rest_in`), stiffness
N/m, breakaway N, damping N s/m (the same default). Each takes `mass`, all of it moving, and `ground`.

`[climb]` picks a scoring rule (CLIMB_RULES) and lists the levels lowest first, each with its points:
`levels = { LOW = 4, MID = 6 }`. The "hang" rule also takes `level_bonus` and the `switch` it applies to.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np

from .spec import RangeError, _Reader

IN = 0.0254  # m per inch
# estimate: HDPE against a rubber-like tread; carpet's is about 1.0
DEFAULT_FRICTION = 0.6
DEFAULT_CREST = 0.008  # m, the 2026 BUMP's ridge (official field STEP)
DEFAULT_LIP = 0.0155  # m, the 2026 BUMP's 0.61 in toe lip
# The robot's frame plus bumpers, as a square outline for the start and element overlap checks. A rough stand-in:
# FRC frame perimeters run to 120 in, so a 0.9 m square is a typical robot with bumpers.
ROBOT_OUTLINE = 0.9  # m
# How far a bump's depth may differ from the one its crest, height, lip and ramp_deg give. The 2026 BUMP's measured
# numbers give 1.2 mm less than its measured 44.40 in.
BUMP_FIT = 0.01  # m
# source: the 2020 GENERATOR SWITCH's rung, 1 1/4 in Sch 40 pipe (2020 manual 3.3.2), a switch's default
DEFAULT_SWITCH_DIAMETER = 1.66 * IN
# A ground box no taller than this may lie under a start: a robot starts on a floor protector, settling the
# difference (the 2022 HANGAR's is 3/8 in, manual 5.4.3).
START_STEP = 0.5 * IN  # m
CLIMB_RULES = ("lowest-rung", "hang", "lowest-point")
# A start's robot may not lie under a rod whose low end is lower than this (it would begin inside the rod); higher
# rods (reef branches, overhead) are allowed over a start.
ROD_START_CLEARANCE = 0.5  # m
POST = 2.0 * IN  # m: a bar's posts' square section (estimate: a typical post; no range here has posts measured)


@dataclass(frozen=True)
class Bump:
    name: str
    center: tuple[float, float]
    yaw_deg: float
    depth: float
    width: float
    height: float
    ramp_deg: float
    crest: float = DEFAULT_CREST
    lip: float = DEFAULT_LIP
    friction: float = DEFAULT_FRICTION


@dataclass(frozen=True)
class Ramp:
    name: str
    center: tuple[float, float]
    yaw_deg: float
    width: float
    height: float
    ramp_deg: float
    deck: float
    two_sided: bool = True
    friction: float = DEFAULT_FRICTION


@dataclass(frozen=True)
class Platform:
    name: str
    center: tuple[float, float]
    yaw_deg: float
    length: float
    width: float
    height: float
    max_tilt_deg: float
    hinge_height: float
    hinge_spacing: float
    ramp_length: float
    mass: float
    inertia: float  # kg m^2 about the center of mass
    com_height: float
    damping: float  # N m s/rad; an estimate
    friction: float = DEFAULT_FRICTION


@dataclass(frozen=True)
class Bar:
    name: str
    center: tuple[float, float]
    yaw_deg: float
    length: float  # end to end, along local x
    diameter: float  # the pipe's outer diameter
    height: float  # the pipe's axis above the carpet
    friction: float = DEFAULT_FRICTION
    posts: bool = True
    stiffness: float | None = None  # N/m: the pipe on a vertical sprung slide; None for a rigid bar
    damping: float = 0.0  # N s/m, the slide's
    level: str | None = None  # a [climb] level's name
    points: float | None = None  # from [climb] levels, for the level
    section: str = "round"  # "round" (a pipe) or "square" (a box of side `diameter`)


@dataclass(frozen=True)
class Box:
    name: str
    center: tuple[float, float]
    yaw_deg: float
    size: tuple[float, float, float]  # along local x, y and z
    base_height: float = 0.0  # its bottom above the carpet
    ground: bool = False  # floor for the terrain signals; otherwise structure
    friction: float = DEFAULT_FRICTION


@dataclass(frozen=True)
class Rod:
    """A straight rod from `base` to `tip` (field coordinates, m), fixed to the field: a peg, a pole, a coral branch.
    `center` (the mean of the two ends, on the carpet plan) and `yaw_deg` (the plan direction from base to tip, 0 for
    an upright rod) are derived, so a rod has a footprint like the other elements."""
    name: str
    base: tuple[float, float, float]
    tip: tuple[float, float, float]
    diameter: float  # a round rod's, or a square rod's side
    section: str = "round"  # "round" or "square"
    friction: float = DEFAULT_FRICTION
    center: tuple[float, float] = field(init=False)
    yaw_deg: float = field(init=False)

    def __post_init__(self) -> None:
        d = np.asarray(self.tip, float) - np.asarray(self.base, float)
        object.__setattr__(self, "center", ((self.base[0] + self.tip[0]) / 2.0, (self.base[1] + self.tip[1]) / 2.0))
        object.__setattr__(self, "yaw_deg", math.degrees(math.atan2(d[1], d[0])) if math.hypot(d[0], d[1]) > 1e-9
                           else 0.0)

    def length(self) -> float:
        return float(np.linalg.norm(np.asarray(self.tip, float) - np.asarray(self.base, float)))

    def axis(self) -> np.ndarray:
        """The unit vector from the base to the tip."""
        return (np.asarray(self.tip, float) - np.asarray(self.base, float)) / self.length()


@dataclass(frozen=True)
class Switch:
    name: str
    center: tuple[float, float]
    yaw_deg: float
    hinge_height: float
    bar_length: float  # end to end, along local x
    bar_drop: float  # from the hinge down to the bar's axis
    mass: float  # everything that turns
    com_drop: float  # its CoM below the hinge
    stop_deg: float
    damping: float  # N m s/rad; an estimate
    level_deg: float  # level within this, for the "hang" rule's bonus
    friction: float = DEFAULT_FRICTION
    diameter: float = DEFAULT_SWITCH_DIAMETER


HINGES = ("bottom", "top", "left", "right", "front", "back", "center")
SHAPES = ("box", "disc")
AXES = ("x", "y", "z")
# N m s/rad or N s/m: a moving element's damping with no spring and none given (estimate: enough to settle a panel
# knocked loose, not enough to feel)
DEFAULT_MOVING_DAMPING = 0.05


@dataclass(frozen=True)
class Hinged:
    """A panel on a hinge. Its pose as described (`size`, `base_height`) is angle 0 of the file; `range_deg` and
    `rest_deg` are angles about the hinge from there. `damping` left out (None) is critical for the panel's inertia
    about the hinge when it has a spring, else DEFAULT_MOVING_DAMPING."""
    name: str
    center: tuple[float, float]
    yaw_deg: float
    hinge: str  # one of HINGES
    mass: float
    axis: str | None = None  # a "center" hinge's, one of AXES; None for the thinnest (a disc's z)
    ground: bool = False  # floor for the terrain signals (a drawbridge); otherwise structure
    shape: str = "box"  # one of SHAPES
    size: tuple[float, float, float] | None = None  # a box's, along local x, y and z
    diameter: float | None = None  # a disc's
    thickness: float | None = None  # a disc's, along local z: a disc lies flat
    base_height: float = 0.0  # its bottom above the carpet
    range_deg: tuple[float, float] | None = None  # (lo, hi); None for no stops
    rest_deg: float = 0.0
    stiffness: float = 0.0  # N m/deg
    damping: float | None = None  # N m s/rad
    breakaway: float = 0.0  # N m
    friction: float = DEFAULT_FRICTION

    def __post_init__(self) -> None:
        if self.damping is None:
            k = math.degrees(self.stiffness)    # N m/rad
            object.__setattr__(self, "damping", 2.0 * math.sqrt(k * hinge_inertia(self)) if k > 0.0
                               else DEFAULT_MOVING_DAMPING)


@dataclass(frozen=True)
class Sliding:
    """A box on a straight slide along its local `axis`. Its pose as described is travel 0 of the file; `range` and
    `rest` are travel along the axis from there (m). `damping` left out (None) is critical for its mass when it has a
    spring, else DEFAULT_MOVING_DAMPING."""
    name: str
    center: tuple[float, float]
    yaw_deg: float
    size: tuple[float, float, float]  # along local x, y and z
    axis: str  # one of AXES, in the element's frame
    range: tuple[float, float]  # (lo, hi), m
    mass: float
    ground: bool = False  # floor for the terrain signals; otherwise structure
    base_height: float = 0.0  # its bottom above the carpet
    rest: float = 0.0  # m
    stiffness: float = 0.0  # N/m
    damping: float | None = None  # N s/m
    breakaway: float = 0.0  # N
    friction: float = DEFAULT_FRICTION

    def __post_init__(self) -> None:
        if self.damping is None:
            object.__setattr__(self, "damping", 2.0 * math.sqrt(self.stiffness * self.mass) if self.stiffness > 0.0
                               else DEFAULT_MOVING_DAMPING)


def panel_size(e: Hinged) -> tuple[float, float, float]:
    """A hinged panel's extent along local x, y and z (a disc's bounding box)."""
    return e.size if e.shape == "box" else (e.diameter, e.diameter, e.thickness)


def panel_center(e: Hinged | Sliding) -> np.ndarray:
    """The panel's center in the element's frame, as described (angle or travel 0)."""
    sz = (panel_size(e) if isinstance(e, Hinged) else e.size)[2]
    return np.array([0.0, 0.0, e.base_height + sz / 2.0])


def hinge_axis(e: Hinged) -> tuple[np.ndarray, np.ndarray]:
    """The hinge's point and unit axis in the element's frame, the panel as described. "bottom" and "top" turn about
    local y along the bottom or top edge; "left" (+y) and "right" (-y) about local z along a side edge; "front" (+x)
    and "back" (-x) about local z along an end edge; "center" through its center about its `axis` or, without one, its
    thinnest axis (a disc's own axis, z: it lies flat; a box's smallest size, x before y before z on a tie)."""
    sx, sy, sz = panel_size(e)
    c = panel_center(e)
    y, z = np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0])
    if e.hinge == "center":
        k = (AXES.index(e.axis) if e.axis is not None else 2 if e.shape == "disc"
             else int(np.argmin([sx, sy, sz])))
        return c, np.eye(3)[k]
    return {"bottom": (c - [0.0, 0.0, sz / 2.0], y), "top": (c + [0.0, 0.0, sz / 2.0], y),
            "left": (c + [0.0, sy / 2.0, 0.0], z), "right": (c - [0.0, sy / 2.0, 0.0], z),
            "front": (c + [sx / 2.0, 0.0, 0.0], z), "back": (c - [sx / 2.0, 0.0, 0.0], z)}[e.hinge]


def panel_inertia(e: Hinged | Sliding) -> np.ndarray:
    """The principal moments about the panel's center along local x, y and z: a uniform box or disc."""
    m = e.mass
    if isinstance(e, Hinged) and e.shape == "disc":
        r, t = e.diameter / 2.0, e.thickness
        across = m * (3.0 * r * r + t * t) / 12.0
        return np.array([across, across, m * r * r / 2.0])
    sx, sy, sz = panel_size(e) if isinstance(e, Hinged) else e.size
    return m / 12.0 * np.array([sy * sy + sz * sz, sx * sx + sz * sz, sx * sx + sy * sy])


def moving_units(elements) -> dict[str, str]:
    """Each hinged or sliding element's name -> the unit its `element.<name>` signal reads in: "deg" for a hinged
    one, "in" for a sliding one; sorted by name."""
    return {e.name: "deg" if isinstance(e, Hinged) else "in"
            for e in sorted(elements, key=lambda e: e.name) if isinstance(e, (Hinged, Sliding))}


def moving_value(q: float, unit: str) -> float:
    """A moving element's signal from its joint's position (radians or m from rest, as qpos holds it): degrees (MuJoCo
    keeps a hinge's angle unwrapped, so three turns read 1080) or inches."""
    return math.degrees(q) if unit == "deg" else q / IN


def hinge_inertia(e: Hinged) -> float:
    """The panel's moment of inertia about its hinge (kg m^2)."""
    point, axis = hinge_axis(e)
    arm = panel_center(e) - point
    off = arm - (arm @ axis) * axis
    return float(panel_inertia(e) @ (axis * axis) + e.mass * (off @ off))


@dataclass(frozen=True)
class ClimbRule:
    """`[climb]`: how hanging scores (scrimmage/terrain/climb.py applies it)."""
    rule: str  # one of CLIMB_RULES
    levels: dict[str, float] = field(default_factory=dict)  # level name -> points, lowest level first
    level_bonus: float = 0.0  # "hang": added while `switch` is within its level_deg
    switch: str | None = None  # "hang": the switch element


Element = Bump | Ramp | Platform | Bar | Box | Switch | Rod | Hinged | Sliding

_COMMON = ("name", "kind", "center", "center_in", "yaw_deg", "friction")
_LENGTHS = {
    "bump": ("depth", "width", "height", "crest", "lip"),
    "ramp": ("width", "height", "deck"),
    "platform": ("length", "width", "height", "hinge_height", "hinge_spacing", "ramp_length", "com_height"),
    "bar": ("length", "diameter", "height"),
    "box": ("base_height",),
    "switch": ("hinge_height", "bar_length", "bar_drop", "com_drop", "diameter"),
    "rod": ("diameter",),
    "hinged": ("base_height", "diameter", "thickness"),
    "sliding": ("base_height", "rest"),
}
_OTHER = {
    "bump": ("ramp_deg",),
    "ramp": ("ramp_deg", "two_sided"),
    "platform": ("max_tilt_deg", "mass", "inertia", "damping"),
    "bar": ("posts", "stiffness", "damping", "level", "section"),
    "box": ("size", "size_in", "ground"),
    "switch": ("mass", "stop_deg", "damping", "level_deg"),
    "rod": ("base", "base_in", "tip", "tip_in", "section"),
    "hinged": ("shape", "size", "size_in", "hinge", "axis", "range_deg", "rest_deg", "stiffness", "damping",
               "breakaway", "mass", "ground"),
    "sliding": ("size", "size_in", "axis", "range", "range_in", "stiffness", "damping", "breakaway", "mass", "ground"),
}
_ALL = tuple(dict.fromkeys(
    _COMMON + tuple(k + s for v in _LENGTHS.values() for k in v for s in ("", "_in"))
    + tuple(k for v in _OTHER.values() for k in v)))
_CLASSES = {"bump": Bump, "ramp": Ramp, "platform": Platform, "bar": Bar, "box": Box, "switch": Switch, "rod": Rod,
            "hinged": Hinged, "sliding": Sliding}
# Length keys that may be 0 or left out, with their defaults.
_LENGTH_DEFAULTS = {"crest": DEFAULT_CREST, "lip": DEFAULT_LIP, "base_height": 0.0}


def _length(r: _Reader, key: str, default: float | None = None, *, positive: bool = True,
            signed: bool = False) -> float:
    """`key` (m) or `<key>_in` (in): positive, or with `positive` false 0 or more, or with `signed` either sign."""
    if r.has(key) and r.has(key + "_in"):
        raise r.err(f"has both {key} and {key}_in; give one")
    if r.has(key + "_in"):
        v = r.num(key + "_in", positive=positive) * IN
    else:
        v = r.num(key, default, positive=positive)
    if not positive and not signed and v < 0.0:
        raise r.err(f"{key} must be 0 or more, not {v!r}")
    return v


def _center(r: _Reader) -> tuple[float, float]:
    if r.has("center") and r.has("center_in"):
        raise r.err("has both center and center_in; give one")
    if r.has("center_in"):
        return tuple(x * IN for x in r.nums("center_in", 2))
    return r.nums("center", 2)


def _bool(r: _Reader, key: str, default: bool) -> bool:
    v = r.t.get(key, default)
    if not isinstance(v, bool):
        raise r.err(f"{key} must be true or false, not {v!r}")
    return v


def _climbing(r: _Reader, kind: str, kw: dict) -> None:
    """A bar's, box's or switch's keys beyond the lengths."""
    if kind == "bar":
        if not kw["length"] > max(kw["diameter"], 2.0 * POST):
            raise r.err(f"length {kw['length']:.4g} m must be more than the pipe's diameter and its two posts")
        kw["posts"] = _bool(r, "posts", True)
        kw["stiffness"] = r.num("stiffness", positive=True) if r.has("stiffness") else None
        if r.has("damping") and kw["stiffness"] is None:
            raise r.err("has damping but no stiffness: damping is the sprung slide's; give stiffness (N/m) too, or "
                        "leave damping out for a rigid bar")
        kw["damping"] = r.num("damping", 0.0)
        if kw["damping"] < 0.0:
            raise r.err(f"damping must be 0 or more, not {kw['damping']!r}")
        kw["level"] = r.text("level") if r.has("level") else None
        kw["section"] = r.text("section") if r.has("section") else "round"
        if kw["section"] not in ("round", "square"):
            raise r.err(f"section must be \"round\" or \"square\", not {kw['section']!r}")
    elif kind == "box":
        kw["size"] = _size(r)
        kw["ground"] = _bool(r, "ground", False)
    else:
        for key in ("mass", "stop_deg", "damping", "level_deg"):
            kw[key] = r.num(key, positive=True)
        if not kw["stop_deg"] < 90.0:
            raise r.err(f"stop_deg must be below 90, not {kw['stop_deg']!r}")
        if not kw["bar_length"] > kw["diameter"]:
            raise r.err(f"bar_length {kw['bar_length']:.4g} m must be more than the pipe's diameter")
        if not kw["bar_drop"] > kw["diameter"] / 2.0:
            raise r.err(f"bar_drop {kw['bar_drop']:.4g} m must be more than the pipe's radius: the bar hangs below "
                        "the hinge")


def _size(r: _Reader) -> tuple[float, float, float]:
    if r.has("size") == r.has("size_in"):
        raise r.err("needs one of size = [x, y, z] (m) or size_in = [x, y, z] (in)")
    return r.nums("size", 3, positive=True) if r.has("size") else tuple(
        v * IN for v in r.nums("size_in", 3, positive=True))


def _not_negative(r: _Reader, key: str, default: float) -> float:
    v = r.num(key, default)
    if v < 0.0:
        raise r.err(f"{key} must be 0 or more, not {v!r}")
    return v


def _span(r: _Reader, key: str, unit: str) -> tuple[float, float]:
    """`range_deg` [lo, hi], or `range` [lo, hi] (m) or `range_in` (in): lo below hi."""
    lo, hi = _lengths(r, key, "lo, hi") if key == "range" else r.nums(key, 2)
    if not lo < hi:
        raise r.err(f"{key} [{lo:g}, {hi:g}] must run from low to high: [lo, hi] in {unit}")
    return lo, hi


def _axis(r: _Reader) -> str:
    v = r.text("axis")
    if v not in AXES:
        raise r.err(f"axis must be \"x\", \"y\" or \"z\" (in the element's frame), not {v!r}")
    return v


def _moving(r: _Reader, kind: str, kw: dict) -> Hinged | Sliding:
    """A hinged or sliding element's keys (C1): its shape and size, the hinge or slide, its stops and rest, and the
    spring, damping and breakaway."""
    kw["base_height"] = _length(r, "base_height", 0.0, positive=False)
    kw["mass"] = r.num("mass", positive=True)
    kw["ground"] = _bool(r, "ground", False)
    for key in ("stiffness", "breakaway"):
        kw[key] = _not_negative(r, key, 0.0)
    if r.has("damping"):
        kw["damping"] = _not_negative(r, "damping", 0.0)
    if kind == "hinged":
        kw["shape"] = r.text("shape") if r.has("shape") else "box"
        if kw["shape"] not in SHAPES:
            raise r.err(f"shape must be \"box\" or \"disc\", not {kw['shape']!r}")
        if kw["shape"] == "box":
            given = [k for k in ("diameter", "diameter_in", "thickness", "thickness_in") if r.has(k)]
            if given:
                raise r.err(f"has {given[0]}, which only a disc takes; a box takes size, or give shape = \"disc\"")
            kw["size"] = _size(r)
        else:
            if r.has("size") or r.has("size_in"):
                raise r.err("has size, which only a box takes; a disc takes diameter and thickness")
            kw["diameter"], kw["thickness"] = _length(r, "diameter"), _length(r, "thickness")
        kw["hinge"] = r.text("hinge")
        if kw["hinge"] not in HINGES:
            raise r.err(f"hinge must be one of {', '.join(HINGES)}, not {kw['hinge']!r}")
        if r.has("axis"):
            if kw["hinge"] != "center":
                raise r.err(f"has axis, which only a \"center\" hinge takes; an edge hinge's axis is its edge "
                            f"(hinge = {kw['hinge']!r})")
            kw["axis"] = _axis(r)
        kw["range_deg"] = _span(r, "range_deg", "degrees") if r.has("range_deg") else None
        kw["rest_deg"] = r.num("rest_deg", 0.0)
        span, rest, key = kw["range_deg"], kw["rest_deg"], "rest_deg"
    else:
        kw["size"] = _size(r)
        kw["axis"] = _axis(r)
        kw["range"] = _span(r, "range", "m")
        kw["rest"] = _length(r, "rest", 0.0, positive=False, signed=True)
        span, rest, key = kw["range"], kw["rest"], "rest"
    if span is not None and not span[0] <= rest <= span[1]:
        rkey = "range_deg" if kind == "hinged" else "range"
        raise r.err(f"{key} {rest:g} is outside {rkey} [{span[0]:g}, {span[1]:g}]; give a {key} within it")
    return (Hinged if kind == "hinged" else Sliding)(**kw)


def _lengths(r: _Reader, key: str, names: str) -> tuple[float, ...]:
    """A list of lengths, `key` in m or `<key>_in` in inches: a rod's `base` or `tip` ("x, y, z"), a slide's `range`
    ("lo, hi")."""
    n = len(names.split(", "))
    if r.has(key) and r.has(key + "_in"):
        raise r.err(f"has both {key} and {key}_in; give one")
    if r.has(key + "_in"):
        return tuple(v * IN for v in r.nums(key + "_in", n))
    if not r.has(key):
        raise r.err(f"needs one of {key} = [{names}] (m) or {key}_in = [{names}] (in)")
    return r.nums(key, n)


def _rod(r: _Reader, name: str) -> Rod:
    """A rod: no center or yaw (its ends say where it is), two different ends, and a diameter shorter than it."""
    kw = dict(name=name, base=_lengths(r, "base", "x, y, z"), tip=_lengths(r, "tip", "x, y, z"),
              diameter=_length(r, "diameter"),
              friction=r.num("friction", DEFAULT_FRICTION, positive=True))
    kw["section"] = r.text("section") if r.has("section") else "round"
    if kw["section"] not in ("round", "square"):
        raise r.err(f"section must be \"round\" or \"square\", not {kw['section']!r}")
    e = Rod(**kw)
    if e.length() < 1e-9:
        raise RangeError(f"{r.path}: {r.where}: base and tip are the same point; a rod needs two ends")
    if e.diameter > e.length():
        raise RangeError(f"{r.path}: {r.where}: diameter {e.diameter:.4g} m is longer than the rod")
    return e


def _element(path, table, i: int) -> Element:
    where = f"[[element]] #{i + 1}"
    name = _Reader(path, where, table, _ALL).text("name")
    where = f"element '{name}'"
    kind = _Reader(path, where, table, _ALL).text("kind")
    if kind not in _CLASSES:
        raise RangeError(f"{path}: {where} has unknown kind '{kind}'; kinds: {', '.join(_CLASSES)}")
    valid = _COMMON + tuple(k + s for k in _LENGTHS[kind] for s in ("", "_in")) + _OTHER[kind]
    if kind == "rod":    # its ends place it
        valid = tuple(k for k in valid if k not in ("center", "center_in", "yaw_deg"))
    r = _Reader(path, where, table, valid)
    if kind == "rod":
        return _rod(r, name)
    kw: dict = dict(name=name, center=_center(r), yaw_deg=r.num("yaw_deg", 0.0),
                    friction=r.num("friction", DEFAULT_FRICTION, positive=True))
    if kind in ("hinged", "sliding"):
        return _moving(r, kind, kw)
    defaults = dict(_LENGTH_DEFAULTS, diameter=DEFAULT_SWITCH_DIAMETER) if kind == "switch" else _LENGTH_DEFAULTS
    for key in _LENGTHS[kind]:
        kw[key] = _length(r, key, defaults.get(key), positive=key not in _LENGTH_DEFAULTS)
    if kind in ("bar", "box", "switch"):
        _climbing(r, kind, kw)
    for key in _OTHER[kind] if kind not in ("bar", "box", "switch") else ():
        if key == "two_sided":
            kw[key] = _bool(r, key, True)
        else:
            kw[key] = r.num(key, positive=True)
    for key in ("ramp_deg", "max_tilt_deg"):
        if key in kw and not kw[key] < 90.0:
            raise r.err(f"{key} must be below 90, not {kw[key]!r}")
    e = _CLASSES[kind](**kw)
    if isinstance(e, Bump):
        _check_bump(r, e)
    return e


def _check_bump(r: _Reader, e: Bump) -> None:
    """The profile is the crest, then each ramp down at ramp_deg to the lip's height, then the lip to the toe: its
    depth has to come out within BUMP_FIT of the given depth."""
    if not e.lip < e.height:
        raise r.err(f"lip {e.lip:.4g} m must be lower than the height {e.height:.4g} m")
    profile = e.crest + 2.0 * (e.height - e.lip) / math.tan(math.radians(e.ramp_deg))
    if abs(profile - e.depth) > BUMP_FIT:
        raise r.err(f"depth {e.depth:.4g} m doesn't fit the profile: crest, height, lip and ramp_deg give "
                    f"{profile:.4g} m from toe to toe; fix one of them")


def parse_elements(path, tables) -> tuple[Element, ...]:
    """Read the `[[element]]` tables; the names must be unique."""
    if not isinstance(tables, list):
        raise RangeError(f"{path}: element must be an array of tables ([[element]])")
    out: list[Element] = []
    for i, t in enumerate(tables):
        e = _element(path, t, i)
        if any(e.name == o.name for o in out):
            raise RangeError(f"{path}: two elements named '{e.name}'; element names must be unique")
        out.append(e)
    return tuple(out)


def parse_climb(path, table, elements: tuple[Element, ...]) -> tuple[ClimbRule | None, tuple[Element, ...]]:
    """`[climb]` (None without one), and the elements with each bar's points filled in from its level."""
    leveled = [e for e in elements if isinstance(e, Bar) and e.level is not None]
    if table is None:
        if leveled:
            raise RangeError(f"{path}: bar '{leveled[0].name}' has level '{leveled[0].level}' but the range has no "
                             f"[climb]; add [climb] with rule and levels, or drop the bar's level")
        return None, elements
    r = _Reader(path, "[climb]", table, ("rule", "levels", "level_bonus", "switch"))
    rule = r.text("rule")
    if rule not in CLIMB_RULES:
        raise r.err(f"has unknown rule '{rule}'; rules: {', '.join(CLIMB_RULES)}")
    raw = r.raw("levels")
    if not isinstance(raw, dict) or not raw:
        raise r.err(f"levels must be a table of level name = points, lowest first, like {{ LOW = 4, MID = 6 }}, "
                    f"not {raw!r}")
    lv = _Reader(path, "[climb] levels", raw, tuple(raw))
    levels = {name: lv.num(name) for name in raw}
    points = list(levels.values())
    if any(p < 0.0 for p in points) or any(b <= a for a, b in zip(points, points[1:])):
        raise r.err(f"levels must be listed lowest first, each worth more points than the one before (and none "
                    f"below 0), not {levels}")
    if rule == "hang":
        name = r.text("switch")
        if not any(e.name == name for e in elements):
            raise r.err(f"switch '{name}' isn't an element of the range; name a kind = \"switch\" element")
        if not any(e.name == name and isinstance(e, Switch) for e in elements):
            raise r.err(f"switch '{name}' isn't a switch; name a kind = \"switch\" element")
        bonus = r.num("level_bonus")
        if bonus < 0.0:
            raise r.err(f"level_bonus must be 0 or more, not {bonus!r}")
        climb = ClimbRule(rule=rule, levels=levels, level_bonus=bonus, switch=name)
    else:
        extra = [k for k in ("switch", "level_bonus") if r.has(k)]
        if extra:
            raise r.err(f"has {extra[0]}, which only the \"hang\" rule takes; drop it or use rule = \"hang\"")
        climb = ClimbRule(rule=rule, levels=levels)
    for e in leveled:
        if e.level not in levels:
            raise RangeError(f"{path}: bar '{e.name}' has level '{e.level}', which isn't one of the [climb] levels: "
                             f"{', '.join(levels)}")
    return climb, tuple(replace(e, points=levels[e.level]) if e in leveled else e for e in elements)


def _extent(e: Element) -> tuple[float, float]:
    """Length along the element's local x, and width along y, outer edge to outer edge."""
    if isinstance(e, Bump):
        return e.depth, e.width
    if isinstance(e, Bar):
        return e.length, max(e.diameter, POST if e.posts else 0.0)
    if isinstance(e, Box):
        return e.size[0], e.size[1]
    if isinstance(e, Switch):
        return e.bar_length, e.diameter
    if isinstance(e, (Hinged, Sliding)):   # as described (angle or travel 0)
        sx, sy, _ = panel_size(e) if isinstance(e, Hinged) else e.size
        return sx, sy
    if isinstance(e, Rod):   # its plan, with the rod's thickness added at both ends (a box of the rod's own width)
        return math.hypot(e.tip[0] - e.base[0], e.tip[1] - e.base[1]) + e.diameter, e.diameter
    if isinstance(e, Ramp):
        run = e.height / math.tan(math.radians(e.ramp_deg))
        return e.deck + run * (2 if e.two_sided else 1), e.width
    return e.length + 2.0 * e.ramp_length, e.width  # ramps stand beyond the deck at both ends


def _rect(center, yaw_deg: float, length: float, width: float) -> np.ndarray:
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    local = np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]], dtype=float) * (length / 2.0, width / 2.0)
    return np.asarray(center, dtype=float) + local @ np.array([[c, s], [-s, c]])


def footprint(e: Element) -> np.ndarray:
    """(4, 2) corners in field coordinates, counter-clockwise, outer edge to outer edge."""
    return _rect(e.center, e.yaw_deg, *_extent(e))


def _separated(a: np.ndarray, b: np.ndarray) -> bool:
    """True when some edge of either convex polygon separates them (separating axis test)."""
    for poly in (a, b):
        for k in range(len(poly)):
            edge = poly[(k + 1) % len(poly)] - poly[k]
            n = np.array([-edge[1], edge[0]])
            pa, pb = a @ n, b @ n
            if pa.max() < pb.min() or pb.max() < pa.min():
                return True
    return False


def _on_floor(e: Element, start: bool) -> list[np.ndarray]:
    """The footprints of the parts of `e` that stand on the carpet, which a start's robot outline (`start`) or a
    floor ball may not overlap. A bar's pipe and a switch hang overhead (a robot may start under them), as do a box
    and a hinged or sliding panel raised off the carpet (a panel as described, at angle or travel 0); a start may lie
    on a thin ground box (a floor protector, START_STEP), a ball may not."""
    if isinstance(e, Switch):
        return []
    if isinstance(e, Rod):   # a ball only rests against a rod that reaches the carpet (a peg); a start also may not
        low = min(e.base[2], e.tip[2]) - e.diameter / 2.0       # lie under any part of a rod lower than the robot
        return [footprint(e)] if low <= (ROD_START_CLEARANCE if start else 0.0) + 1e-9 else []
    if isinstance(e, Bar):
        if not e.posts:
            return []
        c, s = math.cos(math.radians(e.yaw_deg)), math.sin(math.radians(e.yaw_deg))
        reach = e.length / 2.0 - POST / 2.0
        return [_rect((e.center[0] + k * reach * c, e.center[1] + k * reach * s), e.yaw_deg, POST, POST)
                for k in (-1, 1)]
    if isinstance(e, (Hinged, Sliding)) and e.base_height > 0.0:
        return []
    if isinstance(e, Box) and (e.base_height > 0.0 or (start and e.ground and e.size[2] <= START_STEP)):
        return []
    return [footprint(e)]


def check_clear(path, elements, starts: dict, floor_balls: np.ndarray, reaches, names=None) -> None:
    """Reject a start whose outline overlaps an element standing on the carpet, and a floor piece resting inside one
    (see _on_floor). `reaches` are the floor pieces' reaches across the carpet (a ball's radius), one each or one for
    all; `names` what a student reads for each ("ball 3", "coral 1"; default "ball <k>")."""
    reaches = np.broadcast_to(np.asarray(reaches, dtype=float), (len(floor_balls),))
    for sname, (x, y, heading) in starts.items():
        outline = _rect((x, y), heading, ROBOT_OUTLINE, ROBOT_OUTLINE)
        for e in elements:
            if any(not _separated(outline, f) for f in _on_floor(e, start=True)):
                raise RangeError(
                    f"{path}: start '{sname}' at ({x:.4g}, {y:.4g}) overlaps element '{e.name}'; move the start"
                    f" clear of it (a {ROBOT_OUTLINE} m square robot outline is assumed)"
                )
    for k, (x, y) in enumerate(floor_balls):
        for e in elements:
            for f in _on_floor(e, start=False):
                if _gap(f, (x, y)) < reaches[k]:
                    what = names[k] if names is not None else f"ball {k}"
                    raise RangeError(
                        f"{path}: floor_balls: {what} at ({x:.4g}, {y:.4g}) lies on element '{e.name}'; move the"
                        f" {what.rsplit(' ', 1)[0]} off it"
                    )


def _gap(rect: np.ndarray, p) -> float:
    """The distance from point p to the rectangle (its _rect corners), 0 inside."""
    u, v = rect[1] - rect[0], rect[3] - rect[0]
    lu, lv = float(np.linalg.norm(u)), float(np.linalg.norm(v))
    d = np.asarray(p, dtype=float) - rect[0]
    a, b = float(d @ u) / lu, float(d @ v) / lv
    return math.hypot(max(-a, a - lu, 0.0), max(-b, b - lv, 0.0))
