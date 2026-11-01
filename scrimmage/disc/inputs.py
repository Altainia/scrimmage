"""Disc model inputs: the disc and aerodynamic coefficient-set dataclasses, their presets, and the disc launcher's
setup TOML.

All units are SI. Presets live in presets.toml, each value tagged `# source:` or `# estimate:`.

The launcher setup follows launcher/inputs.py's layout and reuses its sections and coercion:

    [disc]     preset = "2013-frisbee", and any `Disc` field to override it
    [wheels]   preset = "urethane-60a-4in" (this package's [wheels.*] presets), and any `Wheels` field
    [layout]   kind = "linear" | "arc" | "pinch"
               wheels = [{ x = 0.1, side = "right", group = 0 }, ...]
               fence = "<[fence.*] preset>" or fence_friction = 0.8   (linear and arc)
               compression (m), or fence_radius (m) for an arc
               channel_length (m), arc_deg (arc), rail_friction, wheel_stiffness (optional, N/m)
    [shaft]    one table for every wheel group, or [[shaft]] with one table per group, in group order
    [control]  likewise: one table, or [[control]] per group
    [launcher] height (m, the disc's center above the floor at the exit) and tilt_deg (the launch angle: the channel
               tilted up by this much about its cross axis)
    [goal]     distance (m, from the launcher to the wall), width, height and bottom (m, a rectangle on the wall);
               the 2013 HIGH goal by default
    [air]      density (kg/m^3) and aero (a coefficient-set preset, "pc-long" by default)
    [battery], [robot], [feed], [results] and `dt` exactly as the ball launcher's.

Each wheel group is one shaft (its own motor and `ShaftModel`); wheels in a group turn together, so a group's
[shaft] wheel_inertia is all its wheels' (and pulleys' and belts') together.
"""

import copy
import dataclasses
import math
import tomllib
import typing
from dataclasses import dataclass, fields
from pathlib import Path

from ..launcher.inputs import (
    FEED_RULES,
    BatteryIn,
    Control,
    Feed,
    LauncherConfigError,
    Results,
    Robot,
    Shaft,
    Wheels,
    _build_section,
    _coerce,
)
from ..motors import MOTORS

PRESETS: dict[str, dict[str, dict]] = tomllib.loads(
    (Path(__file__).with_name("presets.toml")).read_text(encoding="utf-8")
)

DEFAULT_DISC = "2013-frisbee"
DEFAULT_AERO = "pc-long"

AERO_FORMS = ("potts-crowther", "linear")


class DiscConfigError(LauncherConfigError):
    """A disc setup problem; the message names the bad key or preset and lists the valid ones."""


_frozen = dict(frozen=True, kw_only=True)


@dataclass(**_frozen)
class Disc:
    diameter: float
    mass: float
    height: float  # overall height, top of the dome to the rim's lower edge
    izz: float  # moment of inertia about the spin axis (body z)
    ixx: float  # moment of inertia about a diameter (Iyy is the same)
    rim_stiffness: float  # radial spring of the rim against a wheel (N/m)
    rim_damping_ratio: float  # damping ratio of that spring against the disc's mass

    @property
    def radius(self) -> float:
        return self.diameter / 2

    @property
    def area(self) -> float:
        """Planform area, the reference area of every aerodynamic coefficient."""
        return math.pi * self.diameter**2 / 4


@dataclass(**_frozen)
class AeroSet:
    """A coefficient set in the forms of Hummel's code (see aero.py). The linear fields are unused by the
    potts-crowther form, whose C_L, C_D, C_M and C_Rr come from tables; the rate terms apply to both."""

    name: str
    form: str
    cl0: float = 0.0
    cla: float = 0.0  # per radian
    cd0: float = 0.0
    cda: float = 0.0  # per radian squared
    cm0: float = 0.0
    cma: float = 0.0  # per radian
    crr: float = 0.0  # roll from spin, in the sqrt(d/g) form (linear sets only)
    cmq: float = 0.0  # pitch damping, per rad/s
    crp: float = 0.0  # roll damping, per rad/s
    cnr: float = 0.0  # spin-down, N m per rad/s (no q A d factor)
    flat_plate_cd: float = 0.0  # drag broadside to the flow, the target of the beyond-table extension


def _section(kind: str, name: str) -> dict:
    presets = PRESETS.get(kind, {})
    if name not in presets:
        raise DiscConfigError(f"unknown {kind} preset {name!r}; valid presets: {', '.join(sorted(presets))}")
    return dict(presets[name])


def _check_keys(kind: str, name: str, values: dict, cls) -> None:
    valid = {f.name for f in fields(cls)} - {"name"}
    unknown = sorted(set(values) - valid)
    if unknown:
        raise DiscConfigError(f"[{kind}.{name}] has unknown keys {unknown}; valid keys: {', '.join(sorted(valid))}")


def disc_preset(name: str) -> Disc:
    values = _section("disc", name)
    _check_keys("disc", name, values, Disc)
    disc = Disc(**values)
    for key in ("diameter", "mass", "height", "izz", "ixx", "rim_stiffness"):
        if not getattr(disc, key) > 0.0:
            raise DiscConfigError(f"[disc.{name}] {key} must be positive, got {getattr(disc, key)}")
    if disc.rim_damping_ratio < 0.0:
        raise DiscConfigError(f"[disc.{name}] rim_damping_ratio must be zero or more, got {disc.rim_damping_ratio}")
    return disc


def aero_set(name: str) -> AeroSet:
    values = _section("aero", name)
    _check_keys("aero", name, values, AeroSet)
    aero = AeroSet(name=name, **values)
    if aero.form not in AERO_FORMS:
        raise DiscConfigError(f"[aero.{name}] form {aero.form!r} is not one of {', '.join(AERO_FORMS)}")
    if aero.flat_plate_cd < 0.0:
        raise DiscConfigError(f"[aero.{name}] flat_plate_cd must be zero or more, got {aero.flat_plate_cd}")
    return aero


# --- the disc launcher's setup -------------------------------------------------------------------------------------

LAYOUT_KINDS = ("linear", "arc", "pinch")
SIDES = ("left", "right")

# Guide-rail friction (the plates above and below the disc) when [layout] rail_friction is not given.
# estimate: middle of 0.2-0.3 for polyethylene sliding on aluminum or polycarbonate rails (docs/disc.md, Sources).
DEFAULT_RAIL_FRICTION = 0.25

# The arc's wrap when [layout] arc_deg is not given. estimate: a quarter turn, between themccannman's 60 degree arc
# and the semicircular shooters modeled on 973's (Chief Delphi "Frisbee shooter", CD-FS).
DEFAULT_ARC_DEG = 90.0
MAX_ARC_DEG = 180.0


@dataclass(**_frozen)
class WheelSpot:
    """A wheel in the channel: its center's distance x (m) downrange from the entry, the side of the channel it is
    on (looking downrange), and the wheel group (shaft) that drives it."""

    x: float
    side: str
    group: int


@dataclass(**_frozen)
class Layout:
    """The channel. The disc enters with its center at x = 0, y = 0, moving downrange (+x), and leaves when its
    trailing rim passes x = channel_length. See disc/launcher.py for the geometry of each kind."""

    kind: str
    wheels: tuple[WheelSpot, ...]
    fence_friction: float  # the fence against the disc's rim (0 for a pinch, which has no fence)
    fence_radius: float | None  # the arc fence's radius (m), concentric with its wheel; None unless kind is "arc"
    channel_length: float
    compression: float  # the disc's diameter less the gap between the wheel's surface and the fence (or other wheel)
    rail_friction: float = DEFAULT_RAIL_FRICTION
    arc_deg: float = DEFAULT_ARC_DEG  # how far round its wheel the arc fence wraps (arc only)
    wheel_stiffness: float | None = None  # the tread's contact stiffness (N/m); None: from the durometer

    @property
    def groups(self) -> int:
        return max(w.group for w in self.wheels) + 1


# Where the launcher puts the disc in the world, when [launcher] leaves it out.
# estimate: 0.76 m is the exit height of the measured FRC shot the spec checks flight against (12.2 m/s from 0.76 m);
# 25 degrees sits in the 20-28 degrees that reached the high goal.
DEFAULT_HEIGHT = 0.76
DEFAULT_TILT_DEG = 25.0

INCH = 0.0254
# The 2013 HIGH goal (game manual §2.2.3): 54 x 12 in, its bottom edge 104 1/8 in above the floor, centered on the wall.
# source: MAN-2 §2.2.3. The distance is the auto line's, 216 in from the wall (MAN-2 §2.2; the robot starts on it).
HIGH_WIDTH = 54 * INCH
HIGH_HEIGHT = 12 * INCH
HIGH_BOTTOM = 104.125 * INCH
AUTO_LINE = 216 * INCH
DEFAULT_DENSITY = 1.225  # source: the sea-level standard atmosphere, as HUM03 p. 81 (rho = 1.23)


@dataclass(**_frozen)
class Mount:
    """[launcher]: the disc leaves with its center `height` above the floor, the channel tilted up by `tilt_deg`."""

    height: float = DEFAULT_HEIGHT
    tilt_deg: float = DEFAULT_TILT_DEG


@dataclass(**_frozen)
class DiscGoal:
    """[goal]: a rectangle on a vertical wall `distance` ahead of the launcher, centered on its line."""

    distance: float = AUTO_LINE
    width: float = HIGH_WIDTH
    height: float = HIGH_HEIGHT
    bottom: float = HIGH_BOTTOM


@dataclass(**_frozen)
class DiscAir:
    density: float = DEFAULT_DENSITY
    aero: str = DEFAULT_AERO


@dataclass(**_frozen)
class DiscSetup:
    disc: Disc
    wheels: Wheels
    layout: Layout
    shafts: tuple[Shaft, ...]  # one per wheel group
    controls: tuple[Control, ...]  # one per wheel group
    battery: BatteryIn
    robot: Robot
    feed: Feed
    results: Results
    dt: float | None = None
    mount: Mount = Mount()
    goal: DiscGoal = DiscGoal()
    air: DiscAir = DiscAir()


_SECTIONS = ("disc", "wheels", "layout", "shaft", "control", "battery", "robot", "feed", "results", "launcher", "goal",
             "air")
_TOP_LEVEL = set(_SECTIONS) | {"dt", "sweep", "chart"}
_LAYOUT_KEYS = ("kind", "wheels", "fence", "fence_friction", "fence_radius", "channel_length", "compression",
                "rail_friction", "arc_deg", "wheel_stiffness")


def _table(section: str, value) -> dict:
    if not isinstance(value, dict):
        raise DiscConfigError(f"[{section}] must be a table, not {value!r}")
    return dict(value)


def _preset_table(section: str, cls, table: dict, default: str | None) -> object:
    """A section filled from this package's [section.*] preset, then the table's own keys, coerced as the ball
    launcher's sections are."""
    valid = [f.name for f in fields(cls)]
    for key in table:
        if key not in valid and key != "preset":
            raise DiscConfigError(f"unknown key '{key}' in [{section}]; valid keys: {', '.join(valid + ['preset'])}")
    name = table.pop("preset", default)
    values: dict = {}
    if name is not None:
        known = PRESETS.get(section, {})
        if not isinstance(name, str) or name not in known:
            raise DiscConfigError(f"[{section}] unknown preset {name!r}; known {section} presets: {', '.join(known)}")
        values.update(known[name])
    values.update(table)
    missing = [f.name for f in fields(cls) if f.name not in values and f.default is dataclasses.MISSING]
    if missing:
        raise DiscConfigError(f"[{section}] missing required key(s): {', '.join(missing)}; valid keys: "
                              f"{', '.join(valid + ['preset'])}")
    hints = typing.get_type_hints(cls)
    try:
        return cls(**{k: _coerce(v, hints[k], section, k) for k, v in values.items()})
    except LauncherConfigError as e:
        raise DiscConfigError(str(e)) from None


def _number(table: dict, key: str, default=dataclasses.MISSING) -> float | None:
    if key not in table:
        if default is dataclasses.MISSING:
            raise DiscConfigError(f"[layout] missing required key {key}; valid keys: {', '.join(_LAYOUT_KEYS)}")
        return default
    try:
        return _coerce(table[key], float, "layout", key)
    except LauncherConfigError as e:
        raise DiscConfigError(str(e)) from None


def _spots(value) -> tuple[WheelSpot, ...]:
    if not isinstance(value, list) or not value:
        raise DiscConfigError(f"[layout] wheels must be a non-empty list of {{x, side, group}} tables, not {value!r}")
    out = []
    for i, w in enumerate(value):
        where = f"[layout] wheels[{i}]"
        if not isinstance(w, dict) or set(w) - {"x", "side", "group"} or "x" not in w or "side" not in w:
            raise DiscConfigError(f"{where} must be a table {{x, side, group}} (group defaults to 0), not {w!r}")
        try:
            x = _coerce(w["x"], float, "layout", f"wheels[{i}].x")
            group = _coerce(w.get("group", 0), int, "layout", f"wheels[{i}].group")
        except LauncherConfigError as e:
            raise DiscConfigError(str(e)) from None
        if w["side"] not in SIDES:
            raise DiscConfigError(f"{where} side must be one of {', '.join(SIDES)}, not {w['side']!r}")
        if group < 0:
            raise DiscConfigError(f"{where} group must be 0 or more, not {group}")
        out.append(WheelSpot(x=x, side=w["side"], group=group))
    return tuple(out)


def _layout(table: dict, disc: Disc, wheels: Wheels) -> Layout:
    for key in table:
        if key not in _LAYOUT_KEYS:
            raise DiscConfigError(f"unknown key '{key}' in [layout]; valid keys: {', '.join(_LAYOUT_KEYS)}")
    kind = table.get("kind")
    if kind not in LAYOUT_KINDS:
        raise DiscConfigError(f"[layout] kind must be one of {', '.join(LAYOUT_KINDS)}, not {kind!r}")
    spots = _spots(table.get("wheels"))
    groups = max(w.group for w in spots) + 1
    unused = sorted(set(range(groups)) - {w.group for w in spots})
    if unused:
        raise DiscConfigError(f"[layout] wheel groups must be numbered 0 to {groups - 1} with none skipped; "
                              f"no wheel is in group(s) {unused}")
    sides = {w.side for w in spots}
    if kind == "pinch":
        if sides != set(SIDES):
            raise DiscConfigError("[layout] a pinch has wheels on both sides of the disc; give at least one wheel "
                                  "with side = \"left\" and one with side = \"right\"")
        for key in ("fence", "fence_friction", "fence_radius"):
            if key in table:
                raise DiscConfigError(f"[layout] {key}: a pinch has no fence (the wheels hold both sides)")
        fence = 0.0
    else:
        if len(sides) != 1:
            raise DiscConfigError(f"[layout] a {kind} layout has all its wheels on one side, the fence on the other; "
                                  "use kind = \"pinch\" for wheels on both sides")
        if "fence" in table and "fence_friction" in table:
            raise DiscConfigError("[layout] give fence (a preset) or fence_friction, not both")
        if "fence" in table:
            known = PRESETS.get("fence", {})
            if table["fence"] not in known:
                raise DiscConfigError(f"[layout] unknown fence preset {table['fence']!r}; known fence presets: "
                                      f"{', '.join(known)}")
            fence = float(known[table["fence"]]["friction"])
        else:
            fence = _number(table, "fence_friction")
    r_w = wheels.diameter / 2.0
    fence_radius = None
    if kind == "arc":
        if len(spots) != 1:
            raise DiscConfigError(f"[layout] an arc has one wheel, the fence curved round it; got {len(spots)}")
        if "fence_radius" in table and "compression" in table:
            raise DiscConfigError("[layout] an arc takes compression or fence_radius (the fence is concentric with "
                                  "the wheel, so each sets the other: fence_radius = R_wheel + D - compression), "
                                  "not both")
        if "fence_radius" in table:
            fence_radius = _number(table, "fence_radius")
            compression = r_w + disc.diameter - fence_radius
        else:
            compression = _number(table, "compression")
            fence_radius = r_w + disc.diameter - compression
    else:
        if "fence_radius" in table:
            raise DiscConfigError(f"[layout] fence_radius is for an arc only, not kind = {kind!r}")
        if "arc_deg" in table:
            raise DiscConfigError(f"[layout] arc_deg is for an arc only, not kind = {kind!r}")
        compression = _number(table, "compression")
    layout = Layout(
        kind=kind, wheels=spots, fence_friction=fence, fence_radius=fence_radius,
        channel_length=_number(table, "channel_length"), compression=compression,
        rail_friction=_number(table, "rail_friction", DEFAULT_RAIL_FRICTION),
        arc_deg=_number(table, "arc_deg", DEFAULT_ARC_DEG),
        wheel_stiffness=_number(table, "wheel_stiffness", None),
    )
    _check_layout(layout, disc, wheels)
    return layout


def wheel_reach(layout: Layout, disc: Disc, wheels: Wheels) -> float:
    """How far ahead of a wheel's center (along the channel) the disc first touches it, with the disc on the
    channel's center line: sqrt((R_w + r)^2 - (R_w + r - c)^2), c the squeeze on that wheel (half the compression
    in a pinch, where it is shared with the opposite wheel)."""
    reach = wheels.diameter / 2.0 + disc.radius
    c = layout.compression / 2.0 if layout.kind == "pinch" else layout.compression
    if c <= 0.0:
        return 0.0
    return math.sqrt(max(reach**2 - (reach - c) ** 2, 0.0))


def _check_layout(layout: Layout, disc: Disc, wheels: Wheels) -> None:
    for key in ("fence_friction", "rail_friction"):
        if getattr(layout, key) < 0.0:
            raise DiscConfigError(f"[layout] {key} must be zero or more, not {getattr(layout, key)}")
    if layout.channel_length <= 0.0:
        raise DiscConfigError(f"[layout] channel_length must be positive, not {layout.channel_length}")
    if layout.wheel_stiffness is not None and layout.wheel_stiffness <= 0.0:
        raise DiscConfigError(f"[layout] wheel_stiffness must be positive, not {layout.wheel_stiffness}")
    if not layout.compression < disc.diameter / 2.0:
        raise DiscConfigError(f"[layout] compression {layout.compression} must be below the disc's radius "
                              f"{disc.radius}")
    # The arc wraps at most a half turn. Up to 180 deg the disc stays behind the wheel's center (x <= x_w) until the
    # fence's exit end, so it can't reach the lead-out's walls (which start at x_w) or the channel's end early. A
    # longer wrap would start the disc inside the wheel-side wall.
    if layout.kind == "arc" and not 0.0 < layout.arc_deg <= MAX_ARC_DEG:
        raise DiscConfigError(f"[layout] arc_deg must be above 0 and at most {MAX_ARC_DEG:g} (a half turn round the "
                              f"wheel; the disc would start inside the lead-out's wall past it), not "
                              f"{layout.arc_deg}")
    reach = wheel_reach(layout, disc, wheels)
    for w in layout.wheels:
        if layout.kind != "arc" and w.x < reach:
            raise DiscConfigError(
                f"[layout] a wheel at x = {w.x} touches the disc as it is fed (its center at x = 0): a wheel must be "
                f"at x >= {reach:.4g} (where the disc first touches it) so the disc enters clear of it"
            )
        if w.x > layout.channel_length:
            raise DiscConfigError(f"[layout] a wheel at x = {w.x} is past the channel's end, channel_length "
                                  f"{layout.channel_length}")


def _per_group(raw: dict, section: str, groups: int) -> tuple:
    value = raw.get(section)
    if value is None:
        raise DiscConfigError(f"missing [{section}]: give one [{section}] table for every wheel group, or "
                              f"[[{section}]] with one table per group")
    tables = value if isinstance(value, list) else [value] * groups
    if len(tables) != groups:
        raise DiscConfigError(f"[[{section}]] has {len(tables)} table(s) for {groups} wheel groups; give one per "
                              f"group, in group order, or a single [{section}] for all")
    try:
        return tuple(_build_section(section, _table(section, t)) for t in tables)
    except LauncherConfigError as e:
        raise DiscConfigError(str(e)) from None


def parse_setup(raw: dict) -> DiscSetup:
    """Build a `DiscSetup` from a raw dict (the parsed TOML), applying presets and defaults."""
    for key in raw:
        if key not in _TOP_LEVEL:
            raise DiscConfigError(f"unknown section [{key}]; valid sections: {', '.join(sorted(_TOP_LEVEL))}")
    disc = _preset_table("disc", Disc, _table("disc", raw.get("disc", {})), DEFAULT_DISC)
    for key in ("diameter", "mass", "height", "izz", "ixx", "rim_stiffness"):
        if not getattr(disc, key) > 0.0:
            raise DiscConfigError(f"[disc] {key} must be positive, not {getattr(disc, key)}")
    if "wheels" not in raw:
        raise DiscConfigError("missing [wheels]: give a preset (e.g. preset = \"urethane-60a-4in\") or diameter, "
                              "width, durometer and friction")
    wheels = _preset_table("wheels", Wheels, _table("wheels", raw["wheels"]), None)
    for key in ("diameter", "width"):
        if not getattr(wheels, key) > 0.0:
            raise DiscConfigError(f"[wheels] {key} must be positive, not {getattr(wheels, key)}")
    if not 0.0 < wheels.durometer < 100.0:
        raise DiscConfigError(f"[wheels] durometer must be above 0 and below 100, not {wheels.durometer}")
    if wheels.friction < 0.0:
        raise DiscConfigError(f"[wheels] friction must be zero or more, not {wheels.friction}")
    if "layout" not in raw:
        raise DiscConfigError("missing [layout]: give kind, wheels, compression, channel_length and the fence")
    layout = _layout(_table("layout", raw["layout"]), disc, wheels)
    shafts = _per_group(raw, "shaft", layout.groups)
    controls = _per_group(raw, "control", layout.groups)
    for shaft in shafts:
        if shaft.motor not in MOTORS:
            raise DiscConfigError(f"[shaft] unknown motor {shaft.motor!r}; known motors: {', '.join(sorted(MOTORS))}")
        if shaft.wheel_inertia < 0.0 or shaft.added_inertia < 0.0 or shaft.wheel_inertia + shaft.added_inertia <= 0:
            raise DiscConfigError("[shaft] wheel_inertia and added_inertia must be zero or more, and their sum "
                                  f"positive; got {shaft.wheel_inertia} and {shaft.added_inertia}")
        if shaft.gearing <= 0.0 or shaft.motor_count < 1:
            raise DiscConfigError(f"[shaft] gearing must be positive and motor_count at least 1; got "
                                  f"{shaft.gearing} and {shaft.motor_count}")
    try:
        rest = {name: _build_section(name, _table(name, raw.get(name, {})))
                for name in ("battery", "robot", "feed", "results")}
    except LauncherConfigError as e:
        raise DiscConfigError(str(e)) from None
    feed_keys = ("count", "rate", "rule", "tolerance", "entry_speed", "timeout")
    for key in _table("feed", raw.get("feed", {})):
        if key not in feed_keys:
            hint = " (discs have no lanes)" if key == "lanes" else ""
            raise DiscConfigError(f"[feed] unknown key {key!r}{hint}; valid keys: {', '.join(feed_keys)}")
    if rest["feed"].count < 1:
        raise DiscConfigError(f"[feed] count must be at least 1, not {rest['feed'].count}")
    if rest["feed"].rate <= 0.0:
        raise DiscConfigError(f"[feed] rate must be positive, not {rest['feed'].rate}")
    if rest["feed"].rule not in FEED_RULES:
        raise DiscConfigError(f"[feed] unknown rule {rest['feed'].rule!r}; valid rules: {', '.join(FEED_RULES)}")
    if rest["feed"].entry_speed < 0.0:
        raise DiscConfigError(f"[feed] entry_speed must be zero or more, not {rest['feed'].entry_speed}")
    mount = _preset_table("launcher", Mount, _table("launcher", raw.get("launcher", {})), None)
    goal = _preset_table("goal", DiscGoal, _table("goal", raw.get("goal", {})), None)
    air = _preset_table("air", DiscAir, _table("air", raw.get("air", {})), None)
    for section, cls, obj in (("launcher", Mount, mount), ("goal", DiscGoal, goal), ("air", DiscAir, air)):
        for f in fields(cls):
            v = getattr(obj, f.name)
            if f.name in ("tilt_deg", "bottom"):
                continue
            if isinstance(v, float) and not v > 0.0:
                raise DiscConfigError(f"[{section}] {f.name} must be positive, not {v}")
    if not -90.0 < mount.tilt_deg < 90.0:
        raise DiscConfigError(f"[launcher] tilt_deg must be between -90 and 90, not {mount.tilt_deg}")
    if goal.bottom < 0.0:
        raise DiscConfigError(f"[goal] bottom must be zero or more, not {goal.bottom}")
    aero_set(air.aero)  # a bad name raises, listing the valid ones
    dt = raw.get("dt")
    if dt is not None:
        try:
            dt = _coerce(dt, float, "setup", "dt")
        except LauncherConfigError as e:
            raise DiscConfigError(str(e)) from None
        if dt <= 0:
            raise DiscConfigError(f"dt must be positive, not {dt!r}")
    return DiscSetup(disc=disc, wheels=wheels, layout=layout, shafts=shafts, controls=controls, dt=dt, mount=mount,
                     goal=goal, air=air, **rest)


def load_setup(source: Path | dict) -> tuple[DiscSetup, dict[str, list], list[dict]]:
    """Load a setup from a TOML path or a dict. Returns the setup, the raw `[sweep]` table and the `[[chart]]` list,
    as the ball launcher's `load_setup` does."""
    if isinstance(source, dict):
        raw = copy.deepcopy(source)
    else:
        try:
            raw = tomllib.loads(Path(source).read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise DiscConfigError(f"{source}: {e}") from None
    setup = parse_setup(raw)
    sweep = raw.get("sweep", {})
    if not isinstance(sweep, dict):
        raise DiscConfigError(f"[sweep] must be a table, not {sweep!r}")
    charts = raw.get("chart", [])
    if not isinstance(charts, list) or not all(isinstance(c, dict) for c in charts):
        raise DiscConfigError("[[chart]] must be a list of tables")
    return setup, dict(sweep), list(charts)
