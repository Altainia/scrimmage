"""Range files: a season's field elements (goals, floor balls, start positions) read from a TOML file.

SI units; angles in degrees (keys end in `_deg`). Field coordinates are WPILib's: the blue alliance wall at x = 0,
z up, on the generated field's 16.54 x 8.07 m (scrimmage/parts/field.py).

Goal frame: origin at the opening's center at rim height, x along `heading_deg`, z up. Opening vertex k lies at
`rotation_deg + k * 360 / sides` from the goal's +x at the circumradius `across_flats / (2 cos(180 / sides))`, so a
hexagon at rotation 0 has corners on +-x and flats facing +-y. Tilt turns the opening about the goal's y axis: its
entry normal goes from +z (tilt 0) to -x (tilt 90). A funnel's walls run inward from the rim at `slope_deg` below
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

A range's game piece is a ball (`[range] ball = "<launcher ball preset>"`) or a disc (`[range] disc = "<disc
preset>"`, scrimmage/disc/presets.toml, with `aero = "<aero set>"`, the disc's aerodynamics in flight, default
pc-long). `Range.piece` is whichever it is; `Range.ball` is None for a disc range, and `Range.disc` for a ball range.
Floor pieces lie on the carpet (a disc flat), spaced at least a diameter apart.

`[carpet] rolling_resistance` is the carpet's rolling-resistance coefficient C for the range's ball: a ball rolling on
the carpet feels a torque C m g r against its roll, so it slows at C g / (1 + k) (k its inertia factor) and stops.
Left out, it is DEFAULT_ROLLING_RESISTANCE. A disc lying flat doesn't roll, so a disc range ignores it.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from ..disc.inputs import DEFAULT_AERO, AeroSet, Disc, DiscConfigError, aero_set, disc_preset
from ..disc.inputs import PRESETS as DISC_PRESETS
from ..launcher.inputs import PRESETS, Ball, LauncherConfigError, _build_section
from ..teamfolders import TeamFolderError, expand

if TYPE_CHECKING:
    from .elements import ClimbRule, Element
    from .station import Station

CIRCLE_SIDES = 24  # a circular opening is this regular polygon of the given diameter (corners on the circle)
FIELD_LENGTH = 16.54  # m, the generated field's alliance walls' inner faces (scrimmage/parts/field.py)
FIELD_WIDTH = 8.07  # m, its guardrails' inner faces
BUILTIN_RANGES = {name: Path(__file__).with_name("ranges") / f"{name}.toml"
                  for name in ("2026", "2013", "2026-bump", "2023-charge-station", "terrain-library", "2022-hangar",
                               "2020-switch", "2013-pyramid", "ladder", "ladder-square", "intake", "station")}
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

    @property
    def piece(self) -> Ball | Disc:
        """The range's game piece: its Ball or its Disc."""
        return self.disc if self.disc is not None else self.ball

    @property
    def piece_preset(self) -> str:
        return self.disc_preset if self.disc is not None else self.ball_preset


# ---------------------------------------------------------------------------------------------------- parsing

class _Reader:
    """Typed reads from one table, with errors that name the file and the table."""

    def __init__(self, path: Path, where: str, table, valid: tuple[str, ...]):
        if not isinstance(table, dict):
            raise RangeError(f"{path}: {where} must be a table, not {table!r}")
        unknown = [k for k in table if k not in valid]
        if unknown:
            raise RangeError(f"{path}: unknown key '{unknown[0]}' in {where}; valid keys: {', '.join(valid)}")
        self.path, self.where, self.t = path, where, table

    def err(self, msg: str) -> RangeError:
        return RangeError(f"{self.path}: {self.where} {msg}")

    def has(self, key: str) -> bool:
        return key in self.t

    def raw(self, key: str):
        if key not in self.t:
            raise self.err(f"is missing required key '{key}'")
        return self.t[key]

    def text(self, key: str) -> str:
        v = self.raw(key)
        if not isinstance(v, str):
            raise self.err(f"{key} must be text, not {v!r}")
        return v

    def num(self, key: str, default: float | None = None, *, positive: bool = False) -> float:
        if default is not None and key not in self.t:
            return default
        v = self.raw(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise self.err(f"{key} must be a number, not {v!r}")
        if positive and not v > 0:
            raise self.err(f"{key} must be positive, not {v!r}")
        return float(v)

    def whole(self, key: str) -> int:
        v = self.raw(key)
        if isinstance(v, bool) or not isinstance(v, int):
            raise self.err(f"{key} must be a whole number, not {v!r}")
        return v

    def nums(self, key: str, n: int, default: tuple | None = None, *, positive: bool = False) -> tuple:
        if default is not None and key not in self.t:
            return default
        v = self.raw(key)
        if (not isinstance(v, list) or len(v) != n
                or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in v)):
            raise self.err(f"{key} must be a list of {n} numbers, not {v!r}")
        if positive and not all(x > 0 for x in v):
            raise self.err(f"{key} must be positive, not {v!r}")
        return tuple(float(x) for x in v)

    def sub(self, key: str, valid: tuple[str, ...]) -> _Reader:
        return _Reader(self.path, f"{self.where} {key}", self.raw(key), valid)


_TOP = ("range", "goal", "floor_balls", "start", "carpet", "obstacle", "element", "climb", "station")
_RANGE = ("name", "ball", "disc", "aero")
_GOAL = ("name", "pos", "heading_deg", "rim_height", "tilt_deg", "opening", "funnel", "body", "plate", "slot")
_OPENING = ("kind", "sides", "across_flats", "rotation_deg", "diameter", "width", "height")
_RECT = ("kind", "width", "height")
_SLOT = ("depth", "frame")
_OBSTACLE = ("name", "pos", "size", "rpy_deg", "from", "to", "width")
_FUNNEL = ("depth", "slope_deg", "throat")
_BODY = ("size", "height")
_PLATE = ("name", "pos", "size", "rpy_deg")
_FLOOR = ("grid", "at")
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
        raise o.err(f"kind must be \"polygon\" (the default) or \"rect\", not {kind!r}")
    if o.has("width") or o.has("height"):
        raise o.err("width and height are a rect opening's; add kind = \"rect\" or give { sides, across_flats }")
    polygon, circle = o.has("sides") or o.has("across_flats"), o.has("diameter")
    if polygon and circle:
        raise o.err("has both sides/across_flats and diameter; give one: { sides, across_flats } or { diameter }")
    if not polygon and not circle:
        raise o.err("has neither sides nor diameter; give { sides, across_flats } or { diameter }")
    rotation = o.num("rotation_deg", 0.0)
    if circle:
        d = o.num("diameter", positive=True)
        return Opening(sides=CIRCLE_SIDES, across_flats=d * math.cos(math.pi / CIRCLE_SIDES), rotation_deg=rotation)
    sides = o.whole("sides")
    if sides < 3:
        raise o.err(f"sides must be at least 3, not {sides}")
    return Opening(sides=sides, across_flats=o.num("across_flats", positive=True), rotation_deg=rotation)


def _goal(path: Path, table, i: int, piece: Ball | Disc) -> Goal:
    r = _Reader(path, f"[[goal]] #{i + 1}", table, _GOAL)
    name = r.text("name")
    r.where = f"[[goal]] '{name}'"
    opening = _opening(r)
    rect = isinstance(opening, RectOpening)
    if rect and (r.has("funnel") or r.has("body")):
        raise r.err("has a rect opening with a funnel or a body; a slot goal is built from its slot = { depth, frame }")
    if r.has("slot") and not rect:
        raise r.err("slot = { depth, frame } is a rect opening's; give opening = { kind = \"rect\", width, height }")
    slot = None
    if r.has("slot"):
        s = r.sub("slot", _SLOT)
        slot = Slot(depth=s.num("depth", positive=True), frame=s.num("frame", positive=True))
    funnel = None
    if r.has("funnel"):
        f = r.sub("funnel", _FUNNEL)
        slope = f.num("slope_deg")
        if not 0.0 < slope < 90.0:
            raise f.err(f"slope_deg must be above 0 and below 90, not {slope}")
        throat = f.num("throat", positive=True) if f.has("throat") else None
        if throat is not None and throat >= opening.across_flats:
            raise f.err(f"throat {throat} must be narrower than the opening's {opening.across_flats} across flats")
        funnel = Funnel(depth=f.num("depth", positive=True), slope_deg=slope, throat=throat)
    body = None
    if r.has("body"):
        b = r.sub("body", _BODY)
        body = Body(size=b.nums("size", 2, positive=True), height=b.num("height", positive=True))
    plates = []
    raw_plates = r.t.get("plate", [])
    if not isinstance(raw_plates, list):
        raise r.err("plate must be an array of tables ([[goal.plate]])")
    for j, pt in enumerate(raw_plates):
        p = _Reader(path, f"[[goal]] '{name}' [[goal.plate]] #{j + 1}", pt, _PLATE)
        plates.append(Plate(name=p.text("name"), pos=p.nums("pos", 3), size=p.nums("size", 2, positive=True),
                            rpy_deg=p.nums("rpy_deg", 3, (0.0, 0.0, 0.0))))
    tilt = r.num("tilt_deg", 0.0)
    if not 0.0 <= tilt <= 90.0:
        raise r.err(f"tilt_deg must be from 0 to 90, not {tilt}")
    g = Goal(name=name, pos=r.nums("pos", 2), heading_deg=r.num("heading_deg", 0.0),
             rim_height=r.num("rim_height", positive=True), tilt_deg=tilt, opening=opening, funnel=funnel,
             body=body, plates=tuple(plates), slot=slot)
    throat = g.throat_across_flats()
    if throat is not None and throat <= piece.diameter:
        raise RangeError(
            f"{path}: [[goal]] '{name}' funnel: the throat is {throat:.4g} m across the flats, no wider than the"
            f" ball ({piece.diameter} m); make the funnel shallower or its walls steeper"
        )
    return g


def _obstacle(path: Path, table, i: int) -> Obstacle:
    r = _Reader(path, f"[[obstacle]] #{i + 1}", table, _OBSTACLE)
    name = r.text("name")
    r.where = f"[[obstacle]] '{name}'"
    box, bar = r.has("pos") or r.has("size"), r.has("from") or r.has("to")
    if box == bar:
        raise r.err("needs one of a box (pos, size, rpy_deg) or a bar (from, to, width)")
    if box:
        return Obstacle(name=name, pos=r.nums("pos", 3), size=r.nums("size", 3, positive=True),
                        rpy_deg=r.nums("rpy_deg", 3, (0.0, 0.0, 0.0)))
    a, b = np.array(r.nums("from", 3)), np.array(r.nums("to", 3))
    width = r.num("width", positive=True)
    span = b - a
    length = float(np.linalg.norm(span))
    if not length > 0.0:
        raise r.err("from and to are the same point; a bar needs two ends")
    # the bar's z along the span: pitch it from vertical, then yaw it about z
    pitch = math.degrees(math.acos(max(-1.0, min(1.0, span[2] / length))))
    yaw = math.degrees(math.atan2(span[1], span[0]))
    return Obstacle(name=name, pos=tuple(float(c) for c in (a + b) / 2.0), size=(width, width, length),
                    rpy_deg=(0.0, pitch, yaw))


def _floor_balls(path: Path, tables, ball: Ball | Disc) -> np.ndarray:
    if not isinstance(tables, list):
        raise RangeError(f"{path}: floor_balls must be an array of tables ([[floor_balls]])")
    parts = []
    for i, t in enumerate(tables):
        r = _Reader(path, f"[[floor_balls]] #{i + 1}", t, _FLOOR)
        if r.has("grid") == r.has("at"):
            raise r.err("needs one of grid = { corner, step, count } or at = [[x, y], ...]")
        if r.has("grid"):
            g = r.sub("grid", _GRID)
            corner, step = g.nums("corner", 2), g.nums("step", 2, positive=True)
            count = g.raw("count")
            if (not isinstance(count, list) or len(count) != 2
                    or any(isinstance(c, bool) or not isinstance(c, int) or c < 1 for c in count)):
                raise g.err(f"count must be two whole numbers of at least 1, not {count!r}")
            ix, iy = np.meshgrid(np.arange(count[0]), np.arange(count[1]), indexing="ij")  # x outer, y inner
            parts.append(np.column_stack([corner[0] + ix.ravel() * step[0], corner[1] + iy.ravel() * step[1]]))
        else:
            at = r.raw("at")
            if (not isinstance(at, list) or not at or any(
                    not isinstance(p, list) or len(p) != 2
                    or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in p) for p in at)):
                raise r.err(f"at must be a list of [x, y] pairs, not {at!r}")
            parts.append(np.array(at, dtype=float))
    balls = np.concatenate(parts) if parts else np.zeros((0, 2))
    rad = ball.diameter / 2.0
    off = (balls[:, 0] < rad) | (balls[:, 0] > FIELD_LENGTH - rad) | (balls[:, 1] < rad) | (balls[:, 1] > FIELD_WIDTH - rad)
    if off.any():
        k = int(np.argmax(off))
        raise RangeError(
            f"{path}: floor_balls: ball {k} at ({balls[k, 0]:.4g}, {balls[k, 1]:.4g}) lies off the field"
            f" (0..{FIELD_LENGTH} x 0..{FIELD_WIDTH} m, center at least {rad:.4g} m inside)"
        )
    if len(balls) > 1:
        d = np.linalg.norm(balls[:, None, :] - balls[None, :, :], axis=-1)
        d[np.diag_indices(len(balls))] = np.inf
        if d.min() < ball.diameter - _EPS:
            a, b = np.unravel_index(int(np.argmin(d)), d.shape)
            raise RangeError(
                f"{path}: floor_balls: balls {a} and {b} overlap: centers {d[a, b]:.4g} m apart, closer than the"
                f" ball's diameter {ball.diameter} m"
            )
    return balls


def _ball(path: Path, name: str) -> Ball:
    if name not in PRESETS["ball"]:
        raise RangeError(
            f"{path}: [range] ball: unknown ball preset {name!r}; known ball presets: {', '.join(PRESETS['ball'])}"
        )
    try:
        return _build_section("ball", {"preset": name})
    except LauncherConfigError as e:  # pragma: no cover - the shipped presets are complete
        raise RangeError(f"{path}: [range] ball: {e}") from e


def _disc(path: Path, name: str, aero: str) -> tuple[Disc, AeroSet]:
    if name not in DISC_PRESETS.get("disc", {}):
        raise RangeError(f"{path}: [range] disc: unknown disc preset {name!r}; known disc presets: "
                         f"{', '.join(DISC_PRESETS.get('disc', {}))}")
    if aero not in DISC_PRESETS.get("aero", {}):
        raise RangeError(f"{path}: [range] aero: unknown aero set {aero!r}; known aero sets: "
                         f"{', '.join(DISC_PRESETS.get('aero', {}))}")
    try:
        return disc_preset(name), aero_set(aero)
    except DiscConfigError as e:  # pragma: no cover - the shipped presets are complete
        raise RangeError(f"{path}: [range]: {e}") from e


def parse_range(raw: dict, path: Path) -> Range:
    top = _Reader(path, "the file", raw, _TOP)
    rng = top.sub("range", _RANGE)
    rng.where = "[range]"
    if rng.has("ball") and rng.has("disc"):
        raise rng.err("has both ball and disc; a range has one game piece: give ball = \"<preset>\" or "
                      "disc = \"<preset>\"")
    if not rng.has("ball") and not rng.has("disc"):
        raise rng.err("is missing its game piece: give ball = \"<ball preset>\" or disc = \"<disc preset>\"")
    if rng.has("aero") and not rng.has("disc"):
        raise rng.err("aero is a disc's aerodynamics; a ball range takes its air from the [shooter]")
    preset = disc_name = ball = disc = aero = None
    if rng.has("ball"):
        preset = rng.text("ball")
        ball = _ball(path, preset)
    else:
        disc_name = rng.text("disc")
        disc, aero = _disc(path, disc_name, rng.text("aero") if rng.has("aero") else DEFAULT_AERO)
    piece = ball if ball is not None else disc
    goals_raw = raw.get("goal", [])
    if not isinstance(goals_raw, list):
        raise RangeError(f"{path}: goal must be an array of tables ([[goal]])")
    goals = []
    for i, t in enumerate(goals_raw):
        g = _goal(path, t, i, piece)
        if any(g.name == other.name for other in goals):
            raise RangeError(f"{path}: two goals named '{g.name}'; goal names must be unique")
        goals.append(g)
    starts_raw = raw.get("start", {})
    if not isinstance(starts_raw, dict):
        raise RangeError(f"{path}: start must be tables ([start.<name>])")
    starts = {}
    for name, t in starts_raw.items():
        starts[name] = _Reader(path, f"[start.{name}]", t, _START).nums("pose", 3)
    carpet = _Reader(path, "[carpet]", raw.get("carpet", {}), _CARPET)
    rolling = carpet.num("rolling_resistance", DEFAULT_ROLLING_RESISTANCE)
    if not rolling >= 0.0:
        raise carpet.err(f"rolling_resistance must be 0 or more, not {rolling!r}")
    obstacles_raw = raw.get("obstacle", [])
    if not isinstance(obstacles_raw, list):
        raise RangeError(f"{path}: obstacle must be an array of tables ([[obstacle]])")
    obstacles = []
    for i, t in enumerate(obstacles_raw):
        o = _obstacle(path, t, i)
        if any(o.name == other for other in [g.name for g in goals] + [x.name for x in obstacles]):
            raise RangeError(f"{path}: [[obstacle]] '{o.name}' shares its name with another goal or obstacle; "
                             "names must be unique")
        obstacles.append(o)
    from .elements import check_clear, parse_climb, parse_elements  # elements.py imports this module
    from .station import check_release_clear, parse_stations

    elements = parse_elements(path, raw.get("element", []))
    climb, elements = parse_climb(path, raw.get("climb"), elements)
    floor_balls = _floor_balls(path, raw.get("floor_balls", []), piece)
    check_clear(path, elements, starts, floor_balls, piece.diameter / 2.0)
    taken = tuple([g.name for g in goals] + [o.name for o in obstacles] + [e.name for e in elements])
    stations = parse_stations(path, raw.get("station", []), piece, taken)
    built = Range(name=rng.text("name"), ball_preset=preset, ball=ball, goals=tuple(goals),
                  floor_balls=floor_balls, starts=starts, path=path, rolling_resistance=rolling,
                  disc_preset=disc_name, disc=disc, aero=aero, obstacles=tuple(obstacles),
                  elements=elements, climb=climb, stations=stations)
    check_release_clear(path, built)
    return built


def load_range(ref: str, base_dir: Path, ball: str | None = None) -> Range:
    """`ref` is a built-in range's name (BUILTIN_RANGES) or a path relative to `base_dir`. With `ball`, the file is
    read with its [range] ball replaced (all the checks run again; a disc range refuses)."""
    try:
        path = BUILTIN_RANGES.get(ref) or Path(base_dir) / expand(ref)
    except TeamFolderError as e:
        raise RangeError(str(e)) from None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RangeError(
            f"range {ref!r}: cannot read {path} ({e.strerror}); built-in ranges: {', '.join(BUILTIN_RANGES)}"
        ) from e
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:  # also a start (or any table) given twice
        raise RangeError(f"{path}: {e}") from e
    if ball is not None:
        table = raw.get("range")
        if isinstance(table, dict) and "disc" in table:
            raise RangeError(f"{path}: \"{table.get('name', ref)}\" is a disc field; it can't take a ball")
        raw = {**raw, "range": {**(table if isinstance(table, dict) else {}), "ball": ball}}
    return parse_range(raw, path)
