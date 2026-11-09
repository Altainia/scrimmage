"""Range files: a season's field elements (goals, floor balls, start positions) read from a TOML file.

SI units; angles in degrees (keys end in `_deg`). Field coordinates are WPILib's: the blue alliance wall at x = 0,
z up, on the generated field (scrimmage/parts/field.py): 16.54 x 8.07 m, or the range's own `[range] field = { length,
width }` (2024's is 16.541 x 8.211 m), the walls' inner faces. Floor pieces and stations must lie inside it; a goal
outside it is warned about (`Range.warnings`), as it can't be reached.

Goal frame: origin at the opening's center at rim height, x along `heading_deg`, z up. Opening vertex k lies at
`rotation_deg + k * 360 / sides` from the goal's +x at the circumradius `across_flats / (2 cos(180 / sides))`, so a
hexagon at rotation 0 has corners on +-x and flats facing +-y. Tilt turns the opening about the goal's y axis: its
entry normal (the side a scoring piece comes from) goes from +z (tilt 0: scores going down) to -x (tilt 90: going
sideways, toward +x) to -z (tilt 180: going up). With heading, that is every direction an opening can face. A funnel's walls run inward from the rim at `slope_deg` below
horizontal for `depth` along the normal; the throat is the opening inset by `depth / tan(slope)` per flat (or the
measured `throat` across the flats, when given), `depth` below the rim. The scoring surface is the throat for a
funnelled goal, otherwise the opening.

A `[[goal.plate]]` is a flat rectangle in the goal's frame: at `rpy_deg = [0, 0, 0]` it stands vertical, its width
along the goal's y, its height along z and its face normal along x; `pos` is its center. `rpy_deg` turns it (roll
about x, then pitch about y, then yaw about z, in the goal's frame).

A `rect` opening (`{ kind = "rect", width, height }`, a slot) is a rectangle in the goal's x-y plane: `width` along
the goal's y, `height` along its x, centered on the goal's origin (so `rim_height` is the slot's center height). With
`tilt_deg = 90` the slot stands on a wall: the goal's x is up, its +x heading points into the wall, and a piece scores
coming from the field side. A rect goal can't have a funnel or a body. Its `slot = { depth, frame }` builds the goal
around it: a frame `frame` wide around the opening on the wall face, a box `depth` deep behind the opening and a back
wall closing it (build.py). Without `slot` a rect goal is a rim of bars, as a polygon goal is.

An `[[obstacle]]` is a static box on the field: `pos` its center (field x, y and height z), `size` its full lengths
and `rpy_deg` its turn (as a plate's); or a bar `from` one point `to` another (field x, y, z), `width` square.

A range's game pieces are kinds (scrimmage/testrange/pieces.py). Its default kind is `[range] ball = "<launcher
ball preset>"`, `disc = "<disc preset>"` (scrimmage/disc/presets.toml, with `aero = "<aero set>"`, the disc's
aerodynamics in flight, default pc-long) or `piece = "<ball or piece preset>"`, named after its preset; left out, it
is the first `[piece.<name>]` table. Each `[piece.<name>]` adds a kind (a preset, a ball, a disc or a shape), in file
order after the default. `[[floor_balls]]`, `[[station]]` and `[[goal]]` take `kind = "<name>"` (left out: the
default kind; a goal's, the first ball or disc kind). Goals count any piece kind.

A kind's `color` (in its `[piece.<name>]`, or `[range] color` for the [range] ball, disc or piece) is a named color
(scrimmage/sensors/colors.py: red, blue, yellow, orange, green, purple, white, gray) or [r, g, b], each 0 to 1. The
piece is drawn that color and a color sensor reads it; left out, a ball is yellow, a disc white and a rigid piece
off-white (pieces.piece_color), as they were always drawn.

A ball preset may be a ball measurement file instead (`ball = "fuel.measure.toml"`, relative to the range file, in
`[range]` or a `[piece.<name>]`): its `base` preset with the fields it measured in place (scrimmage/measure/use.py),
the kind named after the preset. A shape's `friction` may be a friction measurement file the same way. The kind keeps
each measured number's provenance (`PieceKind.provenance`).

`Range.ball` and `Range.disc` are the first ball and disc kind's Ball and Disc (None when there is none);
`Range.piece` is the ball, else the disc, else the default kind. A ball-only range (`ball = "..."` and no
`[piece.*]`) is one ball kind, read as it always was.

Floor pieces lie on the carpet, turned by the table's `tip_deg` (0 lying on its side, 90 standing on end; left out,
the piece lies down: 90 for a flat kind such as a disc, lying flat, 0 for any other) and `rotation_deg` (about
vertical from field +x, default 0). With `height` (m, default 0) they are staged on top of something that high
instead (2024's HIGH NOTEs on the AMP): set down as on the carpet, that much higher (`Range.floor_heights`). A staged
piece with no element or obstacle top under it is warned about (it falls), and the "lies on element" check is the
carpet's only. Each is at least its horizontal reach (`PieceKind.reach`) inside the field, and
two are at least their two reaches apart (a ball's reach is its radius: a diameter apart, as always).

`[carpet] rolling_resistance` is the carpet's rolling-resistance coefficient C for the range's ball: a ball rolling on
the carpet feels a torque C m g r against its roll, so it slows at C g / (1 + k) (k its inertia factor) and stops.
Left out, it is DEFAULT_ROLLING_RESISTANCE. A disc lying flat doesn't roll, so a disc range ignores it.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .. import i18n, units
from ..disc.inputs import DEFAULT_AERO, AeroSet, Disc, DiscConfigError, aero_set, disc_preset
from ..disc.inputs import PRESETS as DISC_PRESETS
from ..launcher.inputs import PRESETS, Ball, LauncherConfigError, _build_section
from ..measure.use import is_measurement
from ..teamfolders import TeamFolderError, expand

if TYPE_CHECKING:
    from .elements import ClimbRule, Element
    from .pieces import PieceKind
    from .station import Station

CIRCLE_SIDES = 24  # a circular opening is this regular polygon of the given diameter (corners on the circle)
from ..parts.field import FIELD_LENGTH, FIELD_WIDTH  # noqa: E402  m: the generated field's, when a range gives none
STAGED_TOL = 0.02  # m: a staged piece's support may be this far below its height
FIELD_LIMITS = (2.0, 40.0)  # m: a field's length and width must be in this (a typo's 165.4 is refused)
BUILTIN_RANGES = {name: Path(__file__).with_name("ranges") / f"{name}.toml"
                  for name in ("2026", "2013", "2026-bump", "2023-charge-station", "terrain-library", "2022-hangar",
                               "2020-switch", "2013-pyramid", "ladder", "ladder-square", "intake", "station", "reef",
                               "2026-trench", "2016-defenses", "2024")}
BALL_CHOICE_RANGES = ("intake", "station")   # the ranges whose ball a test's Setup can change (a stand-in's, not a game's)
# estimate: no published figure for an FRC foam ball on FRC carpet. Chosen so a 2026 FUEL (k = 0.4) slows at
# 0.07 * 9.81 / 1.4 = 0.49 m/s^2, stopping from 1 m/s in about 2 s and 1 m: about a golf ball on a putting green
# (a Stimpmeter roll, 1.83 m/s over 10 ft, is 0.55 m/s^2, USGA Stimpmeter instructions), on the reasoning that a
# soft foam ball on FRC's low-pile carpet loses at least that much. Measure your own: roll a ball at a known speed and
# time it to a stop, C = (1 + k) v / (g t).
DEFAULT_ROLLING_RESISTANCE = 0.07

_EPS = 1e-9


class RangeError(ValueError):
    """A range file problem; the message names the file and the key."""


@dataclass(frozen=True)
class Opening:
    sides: int
    across_flats: float
    rotation_deg: float = 0.0

    def vertices(self, across_flats: float | None = None) -> np.ndarray:
        """(sides, 2) corners in the goal's x-y plane, counter-clockwise from `rotation_deg`."""
        flats = self.across_flats if across_flats is None else across_flats
        r = flats / (2.0 * math.cos(math.pi / self.sides))
        a = np.radians(self.rotation_deg) + np.arange(self.sides) * (2.0 * math.pi / self.sides)
        return np.column_stack([r * np.cos(a), r * np.sin(a)])


@dataclass(frozen=True)
class RectOpening:
    width: float    # along the goal's y
    height: float   # along the goal's x (up, for a goal tilted 90 deg onto a wall)

    def vertices(self, across_flats: float | None = None) -> np.ndarray:
        """(4, 2) corners in the goal's x-y plane, counter-clockwise. A rect goal has no funnel, so no throat:
        `across_flats` is Opening.vertices's and must be None."""
        assert across_flats is None, "a rect opening has no throat"
        hx, hy = self.height / 2.0, self.width / 2.0
        return np.array([[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]])


@dataclass(frozen=True)
class Slot:
    depth: float    # the box behind the opening, from the wall face to the back wall
    frame: float    # the frame's width around the opening, on the wall face


@dataclass(frozen=True)
class Obstacle:
    name: str
    pos: tuple[float, float, float]     # center: field x, y and height
    size: tuple[float, float, float]    # full lengths along the box's own axes
    rpy_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def rotation(self) -> np.ndarray:
        """The box's axes in the field (columns): roll about x, then pitch about y, then yaw about z (fixed axes)."""
        r, p, y = (math.radians(a) for a in self.rpy_deg)
        cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
        rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
        ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
        rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
        return rz @ ry @ rx


@dataclass(frozen=True)
class Funnel:
    depth: float
    slope_deg: float
    throat: float | None = None  # measured across flats; None: derived from depth and slope


@dataclass(frozen=True)
class Body:
    size: tuple[float, float]
    height: float


@dataclass(frozen=True)
class Plate:
    name: str
    pos: tuple[float, float, float]
    size: tuple[float, float]  # width, height
    rpy_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class Surface:
    center: np.ndarray  # (3,) world
    normal: np.ndarray  # (3,) unit, pointing to the side a scoring ball comes from
    axes: np.ndarray  # (2, 3) unit in-plane axes; normal = axes[0] x axes[1]
    polygon: np.ndarray  # (n, 2) convex outline in `axes` coordinates about `center`, counter-clockwise

    def crossed(self, prev: np.ndarray, now: np.ndarray) -> np.ndarray:
        """Per row of (m, 3) centers: went from the normal's side to the other side in this step, through the
        polygon (its boundary counts as inside)."""
        prev = np.atleast_2d(np.asarray(prev, dtype=float))
        now = np.atleast_2d(np.asarray(now, dtype=float))
        d0 = (prev - self.center) @ self.normal
        d1 = (now - self.center) @ self.normal
        through = (d0 > 0.0) & (d1 <= 0.0)
        t = np.divide(d0, d0 - d1, out=np.zeros_like(d0), where=through)
        q = (prev + t[:, None] * (now - prev) - self.center) @ self.axes.T  # (m, 2)
        a = self.polygon
        edge = np.roll(a, -1, axis=0) - a  # (n, 2)
        rel = q[:, None, :] - a[None, :, :]  # (m, n, 2)
        cross = edge[None, :, 0] * rel[:, :, 1] - edge[None, :, 1] * rel[:, :, 0]
        return through & np.all(cross >= -_EPS, axis=1)


def _rot_z(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _rot_y(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


@dataclass(frozen=True)
class Goal:
    name: str
    pos: tuple[float, float]  # the opening's center on the field
    heading_deg: float
    rim_height: float
    tilt_deg: float
    opening: Opening | RectOpening
    funnel: Funnel | None
    body: Body | None
    plates: tuple[Plate, ...] = ()
    slot: Slot | None = None        # a rect goal's frame and box (None: a rim of bars)
    # the kind it counts (None: the field's first ball or disc kind, else its first kind)
    kind: str | None = None

    def rotation(self) -> np.ndarray:
        """Goal frame to world: heading about z, after the tilt (about y, entry normal +z toward -x)."""
        return _rot_z(self.heading_deg) @ _rot_y(-self.tilt_deg)

    def origin(self) -> np.ndarray:
        return np.array([self.pos[0], self.pos[1], self.rim_height])

    def to_world(self, local: np.ndarray) -> np.ndarray:
        return self.origin() + np.asarray(local, dtype=float) @ self.rotation().T

    def rim_vertices(self) -> np.ndarray:
        v = self.opening.vertices()
        return self.to_world(np.column_stack([v, np.zeros(len(v))]))

    def throat_across_flats(self) -> float | None:
        if self.funnel is None:
            return None
        if self.funnel.throat is not None:
            return self.funnel.throat
        return self.opening.across_flats - 2.0 * self.funnel.depth / math.tan(math.radians(self.funnel.slope_deg))

    def scoring_surface(self) -> Surface:
        rot = self.rotation()
        depth = 0.0 if self.funnel is None else self.funnel.depth
        return Surface(
            center=self.to_world(np.array([0.0, 0.0, -depth])),
            normal=rot[:, 2].copy(),
            axes=rot[:, :2].T.copy(),
            polygon=self.opening.vertices(self.throat_across_flats()),
        )


@dataclass(frozen=True, eq=False)
class Range:
    name: str
    ball_preset: str | None     # None for a disc range
    ball: Ball | None
    goals: tuple[Goal, ...]
    floor_balls: np.ndarray  # (n, 2) field positions of pieces lying on the carpet
    starts: dict[str, tuple[float, float, float]] = field(default_factory=dict)  # name -> (x, y, heading_deg)
    path: Path = Path()
    rolling_resistance: float = DEFAULT_ROLLING_RESISTANCE  # [carpet]: C, the rolling torque over m g r
    disc_preset: str | None = None  # None for a ball range
    disc: Disc | None = None
    aero: AeroSet | None = None     # the disc's aerodynamics in flight
    obstacles: tuple[Obstacle, ...] = ()
    elements: tuple[Element, ...] = ()  # bumps, ramps, platforms, bars, boxes, switches (testrange/elements.py)
    climb: ClimbRule | None = None  # [climb]: how hanging scores (scrimmage/testrange/elements.py)
    stations: tuple[Station, ...] = ()  # human player stations (scrimmage/testrange/station.py)
    kinds: dict[str, PieceKind] = field(default_factory=dict)  # name -> kind, in file order, the default first
    default_kind: str = ""
    floor_kinds: tuple[str, ...] = ()  # each floor piece's kind
    floor_turns: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))  # (n, 2): tip_deg, rotation_deg
    floor_heights: np.ndarray = field(default_factory=lambda: np.zeros(0))  # (n,): what each rests on, m (0: carpet)
    field_length: float = FIELD_LENGTH  # [range] field: m, the alliance walls' inner faces
    field_width: float = FIELD_WIDTH    # m, the guardrails' inner faces
    zones: tuple = ()                   # [[zone]]: floor areas marked with tape (testrange/zones.py)
    spread_warnings: tuple[str, ...] = ()   # those of warnings that are the stations' spreads' (redone by overrides)
    warnings: tuple[str, ...] = ()      # what is accepted but likely a mistake (checks.Said: its words when shown)

    @property
    def piece(self) -> Ball | Disc | PieceKind:
        """The range's game piece: its (first) Ball, else its Disc, else the default kind."""
        if self.ball is not None:
            return self.ball
        return self.disc if self.disc is not None else self.kinds[self.default_kind]

    @property
    def piece_preset(self) -> str | None:
        """The preset of the kind `piece` is: the first ball kind's, else the first disc kind's, else the default's."""
        if self.ball is not None:
            return self.ball_preset
        return self.disc_preset if self.disc is not None else self.kinds[self.default_kind].preset

    @property
    def piece_kind(self) -> str:
        """The name of the kind `piece` is (the shooter's, and the robot's slots'): the first ball kind, else the
        first disc kind, else the default."""
        for form in ("ball", "disc"):
            name = next((k.name for k in self.kinds.values() if k.form == form), None)
            if name is not None and (self.ball if form == "ball" else self.disc) is not None:
                return name
        return self.default_kind


# ---------------------------------------------------------------------------------------------------- parsing

class _Reader:
    """Typed reads from one table, with errors that name the file and the table."""

    def __init__(self, path: Path, where: str, table, valid: tuple[str, ...]):
        if not isinstance(table, dict):
            raise RangeError(i18n.t("{path}: {where} must be a table, not {table}",
                                    path=path, where=where, table=repr(table)))
        unknown = [k for k in table if k not in valid]
        if unknown:
            raise RangeError(i18n.t("{path}: unknown key '{unknown}' in {where}; valid keys: {valid}",
                                    path=path, unknown=unknown[0], where=where, valid=', '.join(valid)))
        self.path, self.where, self.t = path, where, table

    def err(self, msg: str) -> RangeError:
        return RangeError(f"{self.path}: {self.where} {msg}")

    def has(self, key: str) -> bool:
        return key in self.t

    def raw(self, key: str):
        if key not in self.t:
            raise self.err(i18n.t("is missing required key '{key}'", key=key))
        return self.t[key]

    def text(self, key: str) -> str:
        v = self.raw(key)
        if not isinstance(v, str):
            raise self.err(i18n.t("{key} must be text, not {value}", key=key, value=repr(v)))
        return v

    def num(self, key: str, default: float | None = None, *, positive: bool = False,
            nonnegative: bool = False) -> float:
        if default is not None and key not in self.t:
            return default
        v = self.raw(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (nonnegative and not math.isfinite(v)):
            raise self.err(i18n.t("{key} must be a number, not {value}", key=key, value=repr(v)))
        if positive and not v > 0:
            raise self.err(i18n.t("{key} must be positive, not {value}", key=key, value=repr(v)))
        if nonnegative and v < 0:
            raise self.err(i18n.t("{key} can't be negative, not {value}", key=key, value=repr(v)))
        return float(v)

    def whole(self, key: str) -> int:
        v = self.raw(key)
        if isinstance(v, bool) or not isinstance(v, int):
            raise self.err(i18n.t("{key} must be a whole number, not {value}", key=key, value=repr(v)))
        return v

    def nums(self, key: str, n: int, default: tuple | None = None, *, positive: bool = False) -> tuple:
        if default is not None and key not in self.t:
            return default
        v = self.raw(key)
        if (not isinstance(v, list) or len(v) != n
                or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in v)):
            raise self.err(i18n.t("{key} must be a list of {n} numbers, not {value}", key=key, n=n, value=repr(v)))
        if positive and not all(x > 0 for x in v):
            raise self.err(i18n.t("{key} must be positive, not {value}", key=key, value=repr(v)))
        return tuple(float(x) for x in v)

    def sub(self, key: str, valid: tuple[str, ...]) -> _Reader:
        return _Reader(self.path, f"{self.where} {key}", self.raw(key), valid)


_TOP = ("range", "goal", "floor_balls", "start", "carpet", "obstacle", "element", "climb", "station", "piece",
        "zone")
_RANGE = ("name", "ball", "disc", "aero", "piece", "color", "field")
_FIELD = ("length", "width")
_GOAL = ("name", "pos", "heading_deg", "rim_height", "tilt_deg", "opening", "funnel", "body", "plate", "slot",
         "kind")
_OPENING = ("kind", "sides", "across_flats", "rotation_deg", "diameter", "width", "height")
_RECT = ("kind", "width", "height")
_SLOT = ("depth", "frame")
_OBSTACLE = ("name", "pos", "size", "rpy_deg", "from", "to", "width")
_FUNNEL = ("depth", "slope_deg", "throat")
_BODY = ("size", "height")
_PLATE = ("name", "pos", "size", "rpy_deg")
_FLOOR = ("grid", "at", "kind", "tip_deg", "rotation_deg", "height")
MAX_STAGED = 10.0  # m: a floor piece's `height` at most
_GRID = ("corner", "step", "count")
_START = ("pose",)
_CARPET = ("rolling_resistance",)


def _opening(r: _Reader) -> Opening | RectOpening:
    o = r.sub("opening", _OPENING)
    kind = o.text("kind") if o.has("kind") else "polygon"
    if kind == "rect":
        o = r.sub("opening", _RECT)
        return RectOpening(width=o.num("width", positive=True), height=o.num("height", positive=True))
    if kind != "polygon":
        raise o.err(i18n.t("kind must be \"{polygon}\" (the default) or \"{rect}\", not {kind}", kind=repr(kind),
                           polygon="polygon", rect="rect"))
    if o.has("width") or o.has("height"):
        raise o.err(i18n.t("width and height are a rect opening's; add {kind_setting} or give {sides_across_form}",
                           kind_setting="kind = \"rect\"", sides_across_form="{ sides, across_flats }"))
    polygon, circle = o.has("sides") or o.has("across_flats"), o.has("diameter")
    if polygon and circle:
        raise o.err(i18n.t("has both sides/across_flats and diameter; give one: {sides_across_form} or "
                           "{diameter_form}", sides_across_form="{ sides, across_flats }",
                           diameter_form="{ diameter }"))
    if not polygon and not circle:
        raise o.err(i18n.t("has neither sides nor diameter; give {sides_across_form} or {diameter_form}",
                           sides_across_form="{ sides, across_flats }", diameter_form="{ diameter }"))
    rotation = o.num("rotation_deg", 0.0)
    if circle:
        d = o.num("diameter", positive=True)
        return Opening(sides=CIRCLE_SIDES, across_flats=d * math.cos(math.pi / CIRCLE_SIDES), rotation_deg=rotation)
    sides = o.whole("sides")
    if sides < 3:
        raise o.err(i18n.t("sides must be at least 3, not {sides}", sides=sides))
    return Opening(sides=sides, across_flats=o.num("across_flats", positive=True), rotation_deg=rotation)


def kind_named(path: Path, where: str, name: str, kinds: dict[str, PieceKind]) -> PieceKind:
    """The field's kind `name`, or the plain error that lists its kinds."""
    if name not in kinds:
        raise RangeError(i18n.t("{path}: {where}: kind {name} isn't one of this field's pieces: {kinds}",
                                path=path, where=where, name=repr(name), kinds=', '.join(kinds)))
    return kinds[name]


def _counted(path: Path, where: str, name: str | None, kinds: dict[str, PieceKind]) -> PieceKind:
    """The kind a goal counts: the one it names, else the field's first ball or disc kind, else its first kind."""
    if name is not None:
        return kind_named(path, where, name, kinds)
    return next((k for k in kinds.values() if k.form != "rigid"), next(iter(kinds.values())))


def piece_box(kind: PieceKind) -> tuple[float, float, float]:
    """A rigid kind's bounding box in its body frame: its sides along body x, y and z (m)."""
    from .pieces import piece_bounds  # pieces.py imports this module

    lo, hi = piece_bounds(kind)
    return tuple(float(x) for x in hi - lo)


def piece_sides(kind: PieceKind) -> tuple[float, ...]:
    """The sides of a kind's bounding box in its body frame, smallest first (a ball or disc: its diameter)."""
    if kind.form != "rigid" or not kind.solids:
        d = (kind.ball.diameter if kind.ball is not None else kind.disc.diameter if kind.disc is not None
             else kind.extent)
        return (float(d),)
    return tuple(sorted(piece_box(kind)))


def passing_width(kind: PieceKind) -> float:
    """The narrowest opening a piece can pass, in m: a ball's or disc's diameter, a rigid piece's smallest
    bounding-box side."""
    return piece_sides(kind)[0]


def _goal(path: Path, table, i: int, kinds: dict[str, PieceKind], warnings: list[str] | None = None) -> Goal:
    r = _Reader(path, f"[[goal]] #{i + 1}", table, _GOAL)
    name = r.text("name")
    r.where = f"[[goal]] '{name}'"
    kind_name = r.text("kind") if r.has("kind") else None
    counted = _counted(path, r.where, kind_name, kinds)
    opening = _opening(r)
    rect = isinstance(opening, RectOpening)
    if rect and (r.has("funnel") or r.has("body")):
        raise r.err(i18n.t("has a rect opening with a funnel or a body; a slot goal is built from its {slot_setting}",
                           slot_setting="slot = { depth, frame }"))
    if r.has("slot") and not rect:
        raise r.err(i18n.t("{slot_setting} is a rect opening's; give {opening_setting}",
                           slot_setting="slot = { depth, frame }",
                           opening_setting="opening = { kind = \"rect\", width, height }"))
    slot = None
    if r.has("slot"):
        s = r.sub("slot", _SLOT)
        slot = Slot(depth=s.num("depth", positive=True), frame=s.num("frame", positive=True))
    funnel = None
    if r.has("funnel"):
        f = r.sub("funnel", _FUNNEL)
        slope = f.num("slope_deg")
        if not 0.0 < slope < 90.0:
            raise f.err(i18n.t("slope_deg must be above 0 and below 90, not {slope}", slope=slope))
        throat = f.num("throat", positive=True) if f.has("throat") else None
        if throat is not None and throat >= opening.across_flats:
            raise f.err(i18n.t("throat {throat} must be narrower than the opening's {across_flats} across flats",
                               throat=throat, across_flats=opening.across_flats))
        funnel = Funnel(depth=f.num("depth", positive=True), slope_deg=slope, throat=throat)
    body = None
    if r.has("body"):
        b = r.sub("body", _BODY)
        body = Body(size=b.nums("size", 2, positive=True), height=b.num("height", positive=True))
    plates = []
    raw_plates = r.t.get("plate", [])
    if not isinstance(raw_plates, list):
        raise r.err(i18n.t("plate must be an array of tables ({goal_plate_table})", goal_plate_table="[[goal.plate]]"))
    for j, pt in enumerate(raw_plates):
        p = _Reader(path, f"[[goal]] '{name}' [[goal.plate]] #{j + 1}", pt, _PLATE)
        plates.append(Plate(name=p.text("name"), pos=p.nums("pos", 3), size=p.nums("size", 2, positive=True),
                            rpy_deg=p.nums("rpy_deg", 3, (0.0, 0.0, 0.0))))
    tilt = r.num("tilt_deg", 0.0)
    if not 0.0 <= tilt <= 180.0:       # also refuses nan and inf
        raise r.err(i18n.t("tilt_deg must be from 0 to 180, not {tilt}: 0 scores a piece going down, 90 going "
                           "sideways (into a wall), 180 going up; to face the other way, turn heading_deg instead",
                           tilt=tilt))
    g = Goal(name=name, pos=r.nums("pos", 2), heading_deg=r.num("heading_deg", 0.0),
             rim_height=r.num("rim_height", positive=True), tilt_deg=tilt, opening=opening, funnel=funnel,
             body=body, plates=tuple(plates), slot=slot, kind=kind_name)
    throat = g.throat_across_flats()
    width = passing_width(counted)
    if throat is not None and throat <= width:
        what = i18n.t("the piece's thinnest side") if counted.form == "rigid" else i18n.t("the ball")
        raise RangeError(
            i18n.t("{path}: {goal_table} '{name}' funnel: the throat is {throat} across the flats, no wider than "
                   "{what} ({diameter}); make the funnel shallower or its walls steeper", path=path, name=name,
                   throat=units.fmt(throat, "length"), what=what, diameter=units.fmt(width, "length"),
                   goal_table="[[goal]]"))
    sides = piece_sides(counted)
    if throat is not None and warnings is not None and len(sides) > 1 and throat <= sides[1]:
        warnings.append(
            i18n.t("{path}: {goal_table} '{name}' funnel: the throat is {throat} across the flats, narrower than "
                   "the piece's middle side ({side}); it may pass only on edge", path=path, name=name,
                   throat=units.fmt(throat, "length"), side=units.fmt(sides[1], "length"), goal_table="[[goal]]"))
    return g


def _obstacle(path: Path, table, i: int) -> Obstacle:
    r = _Reader(path, f"[[obstacle]] #{i + 1}", table, _OBSTACLE)
    name = r.text("name")
    r.where = f"[[obstacle]] '{name}'"
    box, bar = r.has("pos") or r.has("size"), r.has("from") or r.has("to")
    if box == bar:
        raise r.err(i18n.t("needs one of a box (pos, size, rpy_deg) or a bar (from, to, width)"))
    if box:
        return Obstacle(name=name, pos=r.nums("pos", 3), size=r.nums("size", 3, positive=True),
                        rpy_deg=r.nums("rpy_deg", 3, (0.0, 0.0, 0.0)))
    a, b = np.array(r.nums("from", 3)), np.array(r.nums("to", 3))
    width = r.num("width", positive=True)
    span = b - a
    length = float(np.linalg.norm(span))
    if not length > 0.0:
        raise r.err(i18n.t("from and to are the same point; a bar needs two ends"))
    # the bar's z along the span: pitch it from vertical, then yaw it about z
    pitch = math.degrees(math.acos(max(-1.0, min(1.0, span[2] / length))))
    yaw = math.degrees(math.atan2(span[1], span[0]))
    return Obstacle(name=name, pos=tuple(float(c) for c in (a + b) / 2.0), size=(width, width, length),
                    rpy_deg=(0.0, pitch, yaw))


def _floor_reach(kind: PieceKind, tip: float, turn: float) -> float:
    """How far a floor piece reaches across the carpet from its center: a ball's radius; a disc's lying flat."""
    if kind.form == "ball" or (kind.form == "disc" and tip == 90.0):
        return kind.radius
    return kind.reach(kind.orient(tip, turn))


def _one_round_kind(kinds: dict[str, PieceKind]) -> bool:
    """Whether the field has one kind, a ball or a disc: its messages read as they always have."""
    return len(kinds) == 1 and next(iter(kinds.values())).form != "rigid"


def _floor_pieces(kinds: dict[str, PieceKind], floor_kinds: list[str]) -> list[str]:
    """The piece each floor piece is, in a message: "ball" on a field of one ball or disc kind (as its names say),
    else the kind's plain name ("coral")."""
    if _one_round_kind(kinds):
        return [i18n.t("ball")] * len(floor_kinds)
    return [kinds[k].label for k in floor_kinds]


def _floor_names(kinds: dict[str, PieceKind], floor_kinds: list[str]) -> list[str]:
    """What a student reads for each floor piece: "ball 3" on a field of one ball or disc kind (as always), else the
    kind's plain name and its number among that kind's floor pieces, from 1 ("coral 1")."""
    if _one_round_kind(kinds):
        return [i18n.t("ball {number}", number=k) for k in range(len(floor_kinds))]
    seen: dict[str, int] = {}
    names = []
    for k in floor_kinds:
        seen[k] = seen.get(k, 0) + 1
        names.append(f"{kinds[k].label} {seen[k]}")
    return names


def _floor_balls(path: Path, tables, kinds: dict[str, PieceKind], default: str,
                 size: tuple[float, float] = (FIELD_LENGTH, FIELD_WIDTH),
                 ) -> tuple[np.ndarray, list[str], np.ndarray, np.ndarray, list[str]]:
    """The floor pieces: their positions, kinds, (tip_deg, rotation_deg) turns, reaches and names, on a field of
    `size` (length, width)."""
    if not isinstance(tables, list):
        raise RangeError(i18n.t("{path}: floor_balls must be an array of tables ({floor_balls_table})", path=path,
                                floor_balls_table="[[floor_balls]]"))
    parts, floor_kinds, turns, reaches, heights = [], [], [], [], []
    for i, t in enumerate(tables):
        r = _Reader(path, f"[[floor_balls]] #{i + 1}", t, _FLOOR)
        if r.has("grid") == r.has("at"):
            raise r.err(i18n.t("needs one of {grid_setting} or {at_setting}",
                               grid_setting="grid = { corner, step, count }", at_setting="at = [[x, y], ...]"))
        name = r.text("kind") if r.has("kind") else default
        kind = kind_named(path, r.where, name, kinds)
        tip = r.num("tip_deg", 90.0 if kind.flat else 0.0)
        if not -90.0 <= tip <= 90.0:
            raise r.err(i18n.t("tip_deg must be from -90 to 90, not {tip}", tip=tip))
        turn = r.num("rotation_deg", 0.0)
        height = r.num("height", 0.0)
        if not 0.0 <= height <= MAX_STAGED:
            raise r.err(i18n.t("height must be from 0 to {most} m (the top of what the pieces are staged on), not "
                               "{height}", most=f"{MAX_STAGED:g}", height=repr(height)))
        if r.has("grid"):
            g = r.sub("grid", _GRID)
            corner, step = g.nums("corner", 2), g.nums("step", 2, positive=True)
            count = g.raw("count")
            if (not isinstance(count, list) or len(count) != 2
                    or any(isinstance(c, bool) or not isinstance(c, int) or c < 1 for c in count)):
                raise g.err(i18n.t("count must be two whole numbers of at least 1, not {n}", n=repr(count)))
            ix, iy = np.meshgrid(np.arange(count[0]), np.arange(count[1]), indexing="ij")  # x outer, y inner
            parts.append(np.column_stack([corner[0] + ix.ravel() * step[0], corner[1] + iy.ravel() * step[1]]))
        else:
            at = r.raw("at")
            if (not isinstance(at, list) or not at or any(
                    not isinstance(p, list) or len(p) != 2
                    or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in p) for p in at)):
                raise r.err(i18n.t("at must be a list of [x, y] pairs, not {at}", at=repr(at)))
            parts.append(np.array(at, dtype=float))
        n = len(parts[-1])
        floor_kinds += [name] * n
        turns += [(tip, turn)] * n
        reaches += [_floor_reach(kind, tip, turn)] * n
        heights += [height] * n
    balls = np.concatenate(parts) if parts else np.zeros((0, 2))
    lift = np.array(heights, dtype=float)
    rad = np.array(reaches, dtype=float)
    names = _floor_names(kinds, floor_kinds)
    length, width = size
    off = (balls[:, 0] < rad) | (balls[:, 0] > length - rad) | (balls[:, 1] < rad) | (balls[:, 1] > width - rad)
    if off.any():
        k = int(np.argmax(off))
        raise RangeError(
            i18n.t("{path}: floor_balls: {ball} at ({x}, {y}) lies off the field (0..{field_length} x "
                   "0..{field_width} m, center at least {inside} m inside)", path=path, ball=names[k],
                   x=f"{balls[k, 0]:.4g}", y=f"{balls[k, 1]:.4g}", field_length=f"{length:g}",
                   field_width=f"{width:g}", inside=f"{rad[k]:.4g}")
        )
    if len(balls) > 1:      # across the carpet, and up: pieces staged higher may sit over others
        d = np.linalg.norm(np.column_stack([balls, lift])[:, None, :] - np.column_stack([balls, lift])[None, :, :],
                           axis=-1)
        d[np.diag_indices(len(balls))] = np.inf
        need = rad[:, None] + rad[None, :]
        if (d - need).min() < -_EPS:
            a, b = np.unravel_index(int(np.argmin(d - need)), d.shape)
            if _one_round_kind(kinds):
                raise RangeError(i18n.t("{path}: floor_balls: {first} and {second} overlap: centers {distance} m "
                                        "apart, closer than the ball's diameter {diameter} m", path=path,
                                        first=names[a], second=names[b], distance=f"{d[a, b]:.4g}",
                                        diameter=kinds[floor_kinds[a]].extent))
            raise RangeError(i18n.t("{path}: floor_balls: {first} and {second} overlap: centers {distance} m apart, "
                                    "closer than their reaches across the carpet allow ({allowed} m)", path=path,
                                    first=names[a], second=names[b], distance=f"{d[a, b]:.4g}",
                                    allowed=f"{need[a, b]:.4g}"))
    return balls, floor_kinds, np.array(turns, dtype=float).reshape(-1, 2), rad, names, lift


def _ball(path: Path, name: str) -> Ball:
    if name not in PRESETS["ball"]:
        raise RangeError(
            i18n.t("{path}: {range_table} ball: unknown ball preset {name}; known ball presets: {presets}", path=path,
                   name=repr(name), presets=', '.join(PRESETS['ball']), range_table="[range]")
        )
    try:
        return _build_section("ball", {"preset": name})
    except LauncherConfigError as e:  # pragma: no cover - the shipped presets are complete
        raise RangeError(f"{path}: [range] ball: {e}") from e


def _disc(path: Path, name: str, aero: str) -> tuple[Disc, AeroSet]:
    if name not in DISC_PRESETS.get("disc", {}):
        raise RangeError(i18n.t("{path}: {range_table} disc: unknown disc preset {name}; known disc presets: "
                                "{presets}", path=path, name=repr(name),
                                presets=', '.join(DISC_PRESETS.get('disc', {})), range_table="[range]"))
    if aero not in DISC_PRESETS.get("aero", {}):
        raise RangeError(i18n.t("{path}: {range_table} aero: unknown aero set {aero}; known aero sets: {sets}",
                                path=path, aero=repr(aero), sets=', '.join(DISC_PRESETS.get('aero', {})),
                                range_table="[range]"))
    try:
        return disc_preset(name), aero_set(aero)
    except DiscConfigError as e:  # pragma: no cover - the shipped presets are complete
        raise RangeError(f"{path}: [range]: {e}") from e


def _range_piece(path: Path, name: str) -> PieceKind:
    """`[range] piece = "<name>"`: a ball preset or a piece preset, as a kind named after it."""
    from .pieces import ball_kind, kind_preset

    if name in PRESETS["ball"]:
        return ball_kind(name, name)
    if name in PRESETS.get("piece", {}):
        return kind_preset(name)
    raise RangeError(i18n.t("{path}: {range_table} piece: unknown preset {name}; known ball presets: {presets}; "
                            "known piece presets: {piece_presets}", path=path, name=repr(name),
                            presets=', '.join(PRESETS['ball']), piece_presets=', '.join(PRESETS.get('piece', {})),
                            range_table="[range]"))


def _kinds(path: Path, raw: dict, rng: _Reader) -> tuple[dict[str, PieceKind], str]:
    """The field's kinds, the default first, and the default's name."""
    from .pieces import ball_kind, disc_kind, measured_ball_kind, parse_kind  # pieces.py imports this module

    given = [k for k in ("ball", "disc", "piece") if rng.has(k)]
    if given == ["ball", "disc"]:
        raise rng.err(i18n.t("has both ball and disc; a range has one game piece: give {ball_setting} or "
                             "{disc_setting}", ball_setting="ball = \"<preset>\"",
                             disc_setting="disc = \"<preset>\""))
    if len(given) > 1:
        a, b = given[:2]
        raise rng.err(i18n.t("has both {a} and {b}; give {a} or {b}, not both", a=a, b=b))
    if rng.has("aero") and not rng.has("disc"):
        raise rng.err(i18n.t("aero is a disc's aerodynamics; a ball range takes its air from the {shooter_table}",
                             shooter_table="[shooter]"))
    tables = raw.get("piece", {})
    if not isinstance(tables, dict) or not all(isinstance(t, dict) for t in tables.values()):
        raise RangeError(i18n.t("{path}: piece must be tables ({piece_name_table})", path=path,
                                piece_name_table="[piece.<name>]"))
    if not given and not tables:
        raise rng.err(i18n.t("is missing its game piece: give {ball_setting}, {piece_setting} or a "
                             "{piece_name_table} table", ball_setting="ball = \"<ball preset>\"",
                             piece_setting="piece = \"<piece preset>\"", piece_name_table="[piece.<name>]"))
    kinds: dict[str, PieceKind] = {}
    if given == ["ball"] and is_measurement(rng.text("ball")):
        kind = measured_ball_kind(None, rng.text("ball"), path.parent, f"{path}: [range]")
        kinds[kind.name] = kind
    elif given == ["ball"]:
        preset = rng.text("ball")
        _ball(path, preset)
        kinds[preset] = ball_kind(preset, preset)
    elif given == ["disc"]:
        preset = rng.text("disc")
        aero = rng.text("aero") if rng.has("aero") else DEFAULT_AERO
        _disc(path, preset, aero)
        kinds[preset] = disc_kind(preset, preset, aero)
    elif given == ["piece"]:
        kind = _range_piece(path, rng.text("piece"))
        kinds[kind.name] = kind
    if rng.has("color"):        # the [range] ball's, disc's or piece's color (pieces.py's piece_color)
        if not given:
            raise rng.err(i18n.t("{key} goes with its ball, disc or piece; give a {piece_name_table} its {key} there",
                                 key="color", piece_name_table="[piece.<name>]"))
        from .pieces import parse_color_key
        default = next(iter(kinds.values()))
        kinds[default.name] = replace(default, color=parse_color_key(f"{path}: [range]", rng.raw("color")))
    for name, t in tables.items():
        if name in kinds:
            raise RangeError(i18n.t("{path}: {piece_name_table} has the same name as {range_table} {given}; give it "
                                    "another name", path=path, given=given[0],
                                    piece_name_table="[piece.{}]".format(name), range_table="[range]"))
        kinds[name] = parse_kind(path, name, t, path.parent)
    return kinds, next(iter(kinds))


def field_size_of(ref: str, base_dir: Path) -> tuple[float, float]:
    """A range's field size (length, width) read quickly from its file's `[range] field`, for the editor, which
    needn't build the whole range: a built-in range's, or the generated field's when the file says none, can't be
    read, or gives a size the range would refuse (that range's own errors are reported when it is built)."""
    try:
        path = BUILTIN_RANGES.get(ref) or Path(base_dir) / expand(ref)
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, TeamFolderError, TypeError, ValueError):
        return FIELD_LENGTH, FIELD_WIDTH
    table = raw.get("range")
    f = table.get("field") if isinstance(table, dict) else None
    if not isinstance(f, dict):
        return FIELD_LENGTH, FIELD_WIDTH
    lo, hi = FIELD_LIMITS
    size = [f.get(k) for k in _FIELD]
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi for v in size):
        return float(size[0]), float(size[1])
    return FIELD_LENGTH, FIELD_WIDTH


def _field_size(rng: _Reader) -> tuple[float, float]:
    """`[range] field = { length, width }`, or the generated field's."""
    if not rng.has("field"):
        return FIELD_LENGTH, FIELD_WIDTH
    f = rng.sub("field", _FIELD)
    lo, hi = FIELD_LIMITS
    out = []
    for key in _FIELD:
        v = f.num(key, positive=True)
        if not lo <= v <= hi:
            raise f.err(i18n.t("{key} must be from {lo} to {hi} m (an FRC field is about 16.5 by 8.1 m), not {value}",
                               key=key, lo=f"{lo:g}", hi=f"{hi:g}", value=repr(v)))
        out.append(v)
    return out[0], out[1]


def _outside_warnings(goals, size: tuple[float, float]) -> tuple[str, ...]:
    """A warning for each goal whose opening's center lies outside the field's walls: nothing can reach it."""
    from ..checks import worded

    length, width = size
    out = []
    for g in goals:
        x, y = g.pos
        if not (0.0 <= x <= length and 0.0 <= y <= width):
            out.append(worded(lambda g=g: i18n.t(
                "goal '{name}' at ({x}, {y}) is outside the field (0..{length} x 0..{width} m), behind its walls: "
                "no robot or piece can reach it. Move it onto the field, or give the range its field's size with "
                "{field_setting}", name=g.name, x=f"{g.pos[0]:.4g}", y=f"{g.pos[1]:.4g}", length=f"{length:g}",
                width=f"{width:g}", field_setting="field = { length, width }")))
    return tuple(out)


def _unsupported(rng: Range, names: list[str]) -> tuple[str, ...]:
    """A warning for each staged floor piece (height above 0) with no element's, obstacle's or goal's top (a goal
    body's wall, its funnel, rim or plates, as build.py builds them) under its center within STAGED_TOL: it would
    fall."""
    from ..checks import worded
    from .station import _element_boxes

    if not len(rng.floor_heights) or not rng.floor_heights.any():
        return ()
    boxes = [b for e in rng.elements for b in _element_boxes(e)]
    boxes += [(np.array(o.pos, float), o.rotation(), np.array(o.size) / 2.0) for o in rng.obstacles]
    from .build import _plates      # build.py imports this module
    for g in rng.goals:             # a goal's walls, funnel, rim and plates, as built
        rot = g.rotation()
        boxes += [(g.to_world(center), rot @ local, np.array(half, float)) for _, center, local, half, _ in _plates(g)]
    out = []
    for k, ((x, y), h) in enumerate(zip(rng.floor_balls, rng.floor_heights)):
        if h <= 0.0:
            continue
        under = [np.array([x, y, h - dz]) for dz in (0.002, STAGED_TOL)]
        if any(np.all(np.abs(rot.T @ (p - c)) <= half + 1e-9) for c, rot, half in boxes for p in under):
            continue
        out.append(worded(lambda k=k, x=x, y=y, h=h: i18n.t(
            "floor_balls: {piece} at ({x}, {y}) is staged {height} m up with nothing under it: no element, "
            "obstacle or goal top is there, so it falls. Check its height and the element's", piece=names[k],
            x=f"{x:.4g}", y=f"{y:.4g}", height=f"{h:g}")))
    return tuple(out)


def parse_range(raw: dict, path: Path) -> Range:
    top = _Reader(path, i18n.t("the file"), raw, _TOP)
    rng = top.sub("range", _RANGE)
    rng.where = "[range]"
    size = _field_size(rng)
    kinds, default = _kinds(path, raw, rng)
    first_ball = next((k for k in kinds.values() if k.form == "ball"), None)
    first_disc = next((k for k in kinds.values() if k.form == "disc"), None)
    goals_raw = raw.get("goal", [])
    if not isinstance(goals_raw, list):
        raise RangeError(i18n.t("{path}: goal must be an array of tables ({goal_table})", path=path,
                                goal_table="[[goal]]"))
    goals = []
    goal_warnings: list[str] = []
    for i, t in enumerate(goals_raw):
        g = _goal(path, t, i, kinds, goal_warnings)
        if any(g.name == other.name for other in goals):
            raise RangeError(i18n.t("{path}: two goals named '{name}'; goal names must be unique",
                                    path=path, name=g.name))
        goals.append(g)
    starts_raw = raw.get("start", {})
    if not isinstance(starts_raw, dict):
        raise RangeError(i18n.t("{path}: start must be tables ({start_name_table})", path=path,
                                start_name_table="[start.<name>]"))
    starts = {}
    for name, t in starts_raw.items():
        starts[name] = _Reader(path, f"[start.{name}]", t, _START).nums("pose", 3)
    carpet = _Reader(path, "[carpet]", raw.get("carpet", {}), _CARPET)
    rolling = carpet.num("rolling_resistance", DEFAULT_ROLLING_RESISTANCE)
    if not rolling >= 0.0:
        raise carpet.err(i18n.t("rolling_resistance must be 0 or more, not {rolling}", rolling=repr(rolling)))
    obstacles_raw = raw.get("obstacle", [])
    if not isinstance(obstacles_raw, list):
        raise RangeError(i18n.t("{path}: obstacle must be an array of tables ({obstacle_table})", path=path,
                                obstacle_table="[[obstacle]]"))
    obstacles = []
    for i, t in enumerate(obstacles_raw):
        o = _obstacle(path, t, i)
        if any(o.name == other for other in [g.name for g in goals] + [x.name for x in obstacles]):
            raise RangeError(i18n.t("{path}: {obstacle_table} '{name}' shares its name with another goal or "
                                    "obstacle; names must be unique", path=path, name=o.name,
                                    obstacle_table="[[obstacle]]"))
        obstacles.append(o)
    from .elements import check_clear, parse_climb, parse_elements  # elements.py imports this module
    from .station import check_release_clear, parse_stations

    elements = parse_elements(path, raw.get("element", []))
    climb, elements = parse_climb(path, raw.get("climb"), elements)
    floor_balls, floor_kinds, floor_turns, reaches, names, heights = _floor_balls(path, raw.get("floor_balls", []),
                                                                                 kinds, default, size)
    on_carpet = heights == 0.0
    check_clear(path, elements, starts, floor_balls[on_carpet], reaches[on_carpet],
                [n for n, c in zip(names, on_carpet) if c],
                [p for p, c in zip(_floor_pieces(kinds, floor_kinds), on_carpet) if c])
    taken = tuple([g.name for g in goals] + [o.name for o in obstacles] + [e.name for e in elements])
    stations = parse_stations(path, raw.get("station", []), kinds, default, taken, size)
    from .zones import parse_zones
    zones = parse_zones(path, raw.get("zone", []), size)
    built = Range(name=rng.text("name"), ball_preset=first_ball.preset if first_ball else None,
                  ball=first_ball.ball if first_ball else None, goals=tuple(goals),
                  floor_balls=floor_balls, starts=starts, path=path, rolling_resistance=rolling,
                  disc_preset=first_disc.preset if first_disc else None, disc=first_disc.disc if first_disc else None,
                  aero=first_disc.aero if first_disc else None, obstacles=tuple(obstacles),
                  elements=elements, climb=climb, stations=stations, kinds=kinds, default_kind=default,
                  floor_kinds=tuple(floor_kinds), floor_turns=floor_turns, floor_heights=heights, zones=zones,
                  field_length=size[0],
                  field_width=size[1],
                  warnings=tuple(w for k in kinds.values() for w in k.warnings) + _outside_warnings(goals, size)
                           + tuple(goal_warnings))
    staged = _unsupported(built, names)
    if staged:
        built = replace(built, warnings=built.warnings + staged)
    spread = check_release_clear(path, built)
    return replace(built, warnings=built.warnings + spread, spread_warnings=spread) if spread else built


def load_range(ref: str, base_dir: Path, ball: str | None = None, piece: str | None = None) -> Range:
    """`ref` is a built-in range's name (BUILTIN_RANGES) or a path relative to `base_dir`. With `ball`, the file is
    read with its [range] ball replaced (all the checks run again; a disc range refuses). With `piece` (a ball or
    piece preset), its default kind is replaced the same way: the [range] ball, disc or piece, or, when the default
    is the first [piece.<name>] table, that table (keeping its name, so what names it still finds it, and its
    `color`, as the [range] table keeps its own). A `ball` that is
    a measurement file (`fuel.measure.toml`) is found from `base_dir`, the folder of the file that names it."""
    if ball is not None and piece is not None:
        raise RangeError(i18n.t("give ball or piece, not both"))
    try:
        path = BUILTIN_RANGES.get(ref) or Path(base_dir) / expand(ref)
    except TeamFolderError as e:
        raise RangeError(str(e)) from None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RangeError(
            i18n.t("range {ref}: cannot read {path} ({error}); built-in ranges: {ranges}",
                   ref=repr(ref), path=path, error=e.strerror, ranges=', '.join(BUILTIN_RANGES))
        ) from e
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:  # also a start (or any table) given twice
        raise RangeError(f"{path}: {e}") from e
    if is_measurement(ball):
        ball = str((Path(base_dir) / ball).resolve())
    if ball is not None:
        table = raw.get("range")
        if isinstance(table, dict) and "disc" in table:
            raise RangeError(i18n.t("{path}: \"{name}\" is a disc field; it can't take a ball",
                                    path=path, name=table.get('name', ref)))
        kept = {k: v for k, v in table.items() if k != "piece"} if isinstance(table, dict) else {}
        raw = {**raw, "range": {**kept, "ball": ball}}
    if piece is not None:
        table = raw.get("range") if isinstance(raw.get("range"), dict) else {}
        tables = raw.get("piece")
        if not any(k in table for k in ("ball", "disc", "piece")) and isinstance(tables, dict) and tables:
            from .pieces import display_name

            first = next(iter(tables))
            key = "ball" if piece in PRESETS["ball"] else "preset"
            # the field's color for it stays (a color sensor reads it), as a [range] piece's does below
            color = {"color": tables[first]["color"]} if isinstance(tables[first], dict) and "color" in tables[first] \
                else {}
            raw = {**raw, "piece": {**tables, first: {key: piece, "label": display_name(piece), **color}}}
        else:
            kept = {k: v for k, v in table.items() if k not in ("ball", "disc", "aero", "piece")}
            raw = {**raw, "range": {**kept, "piece": piece}}
    return parse_range(raw, path)
