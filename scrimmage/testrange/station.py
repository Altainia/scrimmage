"""A human player station in a range file: `[[station]]` feeds balls onto the field from a release point.

    [[station]]
    name = "feeder"
    release = [1.0, 4.0, 0.5]     # or release_in = [x, y, z]: where a fed ball's center appears (m)
    heading_deg = 0.0             # the way it is fed, about z from field +x
    pitch_deg = 20.0              # down from horizontal, -90 to 90 (default 0)
    speed = 1.0                   # m/s along that direction (default 0: the ball is only set down)
    interval = 1.0                # s between balls (default DEFAULT_INTERVAL)
    stock = 12                    # balls to feed, at most MAX_STOCK (default: none, but at most STATION_CAP a run)
    spread_yaw_deg = 5.0          # each feed varies uniformly within +-this of the heading, and so on
    spread_pitch_deg = 5.0
    spread_speed = 0.2
    spread_position = [0.0, 0.02, 0.02]    # or spread_position_in; a box around the release point, half sizes
    spread_rotation_deg = [0, 0, 0]        # roll, pitch and yaw turning the fed piece (it does nothing to a ball)
    chute = { length = 0.6, slope_deg = 25.0, width = 0.3 }   # a sloped floor and two side walls (optional)
    kind = "coral"                # the field's piece kind it feeds (default: the range's default kind)
    tip_deg = 0.0                 # the fed piece's tip (0 lying on its side, 90 standing on end), -90 to 90;
                                  # left out, it lies down: 90 for a flat kind (lying flat), 0 for any other
    turn_deg = 0.0                # its turn about vertical, from the heading
    spread_tip_deg = 10.0         # and their spreads, as the others
    spread_turn_deg = 30.0

The spreads are limits, not standard deviations (the bench's inlet takes standard deviations): a piece never leaves
a tight chute's limits. Every spread is +- its value on each axis.
Stations feed balls and rigid pieces (a kind, scrimmage/testrange/pieces.py), not discs. A rigid piece is fed
turned by its tip and turn (PieceKind.orient, the turn from the heading), then by the rotation spread; a ball is
fed unturned (its rotation spread turns it, which changes nothing).

The chute's floor top passes under the release point, tangent to the piece resting there: a ball, or a rigid piece
at the lowest of the tips and turns its spreads allow (so no feed starts inside the floor; the rest are set down a
little above it). It runs down along the heading for `length` at `slope_deg` (its high end reaches a little behind
the release point, so the piece lands on the floor's face); its side walls stand one piece extent (the largest
side of its bounding box: a ball's diameter) above the floor. Its `width` is the space between the walls, 1.3
extents when left out (so a piece of any preset fits, whichever way it is turned). The release point's clearance
from everything but its own chute's floor is the piece's bounding radius.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import numpy as np

from .elements import IN, ROBOT_OUTLINE, _rect, _separated
from .spec import FIELD_LENGTH, FIELD_WIDTH, RangeError, _Reader, _rot_z, kind_named

if TYPE_CHECKING:
    from .pieces import PieceKind
    from .spec import Range

DEFAULT_INTERVAL = 1.0      # s between balls (a human player's pace; some are much quicker, so it is a setting)
STATION_CAP = 50            # balls a station with no stock feeds at most in one run (the parked pool's size)
MAX_STOCK = 200             # a station's stock at most (ruling R13: each ball is parked in the world from the start)
CHUTE_WIDTH_BALLS = 1.3     # a chute's default width between its walls, in piece extents (a ball's diameters)
CHUTE_WALL_BALLS = 1.0      # its side walls' height above the floor, in piece extents
SPREAD_SAMPLES = 9          # tips (and turns) tried across a spread for the lowest one under the release point
CHUTE_PLATE = 0.25 * IN     # m: the chute's floor and wall thickness (estimate: a polycarbonate sheet)
DEFAULT_SEED = 1            # the spreads' random seed, as the bench's
OVERRIDE_KEYS = ("interval", "stock", "spread_yaw_deg", "spread_pitch_deg", "spread_speed", "spread_position",
                 "spread_rotation_deg", "tip_deg", "turn_deg", "spread_tip_deg", "spread_turn_deg")
_STATION = ("name", "release", "release_in", "heading_deg", "pitch_deg", "speed", "interval", "stock",
            "spread_yaw_deg", "spread_pitch_deg", "spread_speed", "spread_position", "spread_position_in",
            "spread_rotation_deg", "chute", "kind", "tip_deg", "turn_deg", "spread_tip_deg", "spread_turn_deg")
_CHUTE = ("width", "length", "slope_deg")
_TOUCH = 1e-6   # m: a ball resting on a surface is not inside it
_START_TOUCH = 0.01     # m: a start's bumpers may lie against a chute's low end (its plate's corner reaches past)


def stock_too_big(stock: int) -> str:
    return f"stock can be at most {MAX_STOCK} pieces, not {stock}"


def check_seed(seed) -> int:
    """`seed` if it is a whole number, 0 or more (numpy's seeds); a ValueError otherwise."""
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError(f"seed must be a whole number, 0 or more, not {seed!r}")
    return seed


@dataclass(frozen=True)
class Chute:
    length: float               # along the slope
    slope_deg: float            # below horizontal
    width: float | None = None  # between the walls; None: CHUTE_WIDTH_BALLS piece extents


@dataclass(frozen=True)
class Station:
    name: str
    release: tuple[float, float, float]
    heading_deg: float
    pitch_deg: float = 0.0
    speed: float = 0.0
    interval: float = DEFAULT_INTERVAL
    stock: int | None = None
    spread_yaw_deg: float = 0.0
    spread_pitch_deg: float = 0.0
    spread_speed: float = 0.0
    spread_position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    spread_rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)
    chute: Chute | None = None
    kind: str | None = None     # the kind it feeds; None: the range's default kind
    tip_deg: float = 0.0        # the fed piece's tip: 0 lying on its side, 90 standing on end (a range file's
                                # default is the kind's lying down: 90 for a flat kind)
    turn_deg: float = 0.0       # its turn about vertical, from the heading
    spread_tip_deg: float = 0.0
    spread_turn_deg: float = 0.0

    def kind_of(self, rng: Range) -> PieceKind:
        """The kind this station feeds: Range.piece's own ball for the kind it is (a range with its `ball` replaced
        builds and feeds that ball)."""
        from .pieces import ball_kind_of

        kind = rng.kinds[self.kind if self.kind is not None else rng.default_kind]
        if kind.form == "ball" and kind.name == rng.piece_kind and rng.ball is not None and rng.ball is not kind.ball:
            return ball_kind_of(kind.name, kind.preset, rng.ball)
        return kind

    def pool(self) -> int:
        """Balls the station can feed in one run."""
        return self.stock if self.stock is not None else STATION_CAP

    def direction(self, yaw_deg: float, pitch_deg: float) -> np.ndarray:
        """The unit vector for a feed at `yaw_deg` about z from field +x and `pitch_deg` down from horizontal."""
        y, p = math.radians(yaw_deg), math.radians(pitch_deg)
        return np.array([math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), -math.sin(p)])

    def fed_rotations(self, kind: PieceKind) -> list[np.ndarray]:
        """The rotation matrices a rigid piece of `kind` may be fed at: its tip and turn (PieceKind.orient) across
        their spreads, SPREAD_SAMPLES of each, then the rotation spread's corners and middles. One, unspread."""
        from .pieces import _matrix, _rpy

        def across(v: float, spread: float) -> np.ndarray:
            return np.linspace(v - spread, v + spread, SPREAD_SAMPLES) if spread > 0.0 else np.array([v])

        rpys = {tuple((np.array(c) - 1.0) * self.spread_rotation_deg) for c in np.ndindex(3, 3, 3)}
        base = [_matrix(kind.orient(tip, self.heading_deg + turn)) for tip in across(self.tip_deg, self.spread_tip_deg)
                for turn in across(self.turn_deg, self.spread_turn_deg)]
        return [_rpy(r) @ m for r in rpys for m in base]

    def to_floor(self) -> np.ndarray:
        """The rotation that turns the chute floor's upward normal to +z: about z by -heading, then y by -slope."""
        from .pieces import _axis_angle

        return (_axis_angle((0, 1, 0), -math.radians(self.chute.slope_deg))
                @ _axis_angle((0, 0, 1), -math.radians(self.heading_deg)))

    def clearance(self, kind: PieceKind) -> float:
        """How far the chute floor's top lies below the release point, at right angles to the floor: a ball's
        radius; for a rigid piece, the furthest it reaches toward the floor at any of its `fed_rotations`. Either
        way, plus the furthest the position spread moves the release point toward the floor. Kept on the kind per
        station (a sweep asks again and again)."""
        cache = kind.__dict__.setdefault("_station_clearance", {})
        if self not in cache:
            to_floor = self.to_floor()
            toward = float(np.abs(to_floor[2] * np.array(self.spread_position)).sum())
            if kind.ball is not None:
                reach = kind.ball.diameter / 2.0
            else:
                from .pieces import _quat

                reach = max(kind.lowest(_quat(to_floor @ m)) for m in self.fed_rotations(kind))
            cache[self] = reach + toward if toward else reach
        return cache[self]

    def chute_top(self, kind: PieceKind) -> float:
        """The height of the chute floor's top face under the release point: tangent to the piece resting there."""
        return self.release[2] - self.clearance(kind) / math.cos(math.radians(self.chute.slope_deg))

    def chute_width(self, kind: PieceKind) -> float:
        """The space between the chute's walls for a piece of `kind`."""
        width = self.chute.width if self.chute is not None else None
        return width if width is not None else CHUTE_WIDTH_BALLS * kind.extent


def _low_end_drop(st: Station, kind: PieceKind) -> float:
    """How far the chute's floor underside at its low end lies below the carpet (negative: above it)."""
    c = st.chute
    s = math.radians(c.slope_deg)
    top = st.chute_top(kind) - c.length * math.sin(s)
    return -(top - CHUTE_PLATE * math.cos(s))


def _piece_words(kind: PieceKind) -> str:
    """A fed piece in a message: "a 0.1501 m ball" (as always), or "a coral"."""
    return f"a {kind.ball.diameter} m ball" if kind.ball is not None else f"a {kind.label}"


def _station(path, table, i: int, kinds: dict[str, PieceKind], default: str, taken: list[str]) -> Station:
    r = _Reader(path, f"[[station]] #{i + 1}", table, _STATION)
    name = r.text("name")
    r.where = f"[[station]] '{name}'"
    kind_name = r.text("kind") if r.has("kind") else None
    kind = kind_named(path, r.where, kind_name if kind_name is not None else default, kinds)
    if kind.form == "disc":
        raise RangeError(f"{path}: {r.where}: stations feed balls; this range's piece is a disc")
    if name in taken:
        raise RangeError(f"{path}: [[station]] '{name}' shares its name with another station, goal, obstacle or "
                         "element; names must be unique")
    for key in ("release", "spread_position"):
        if r.has(key) and r.has(key + "_in"):
            raise r.err(f"has both {key} and {key}_in; give one")
    if r.has("release") == r.has("release_in"):
        raise r.err("needs one of release = [x, y, z] (m) or release_in = [x, y, z] (in)")
    release = r.nums("release", 3) if r.has("release") else tuple(v * IN for v in r.nums("release_in", 3))
    if r.has("spread_position_in"):
        spread_position = tuple(v * IN for v in r.nums("spread_position_in", 3))
    else:
        spread_position = r.nums("spread_position", 3, (0.0, 0.0, 0.0))
    pitch = r.num("pitch_deg", 0.0)
    if not -90.0 <= pitch <= 90.0:
        raise r.err(f"pitch_deg must be from -90 to 90, not {pitch}")
    stock = r.whole("stock") if r.has("stock") else None
    if stock is not None and stock < 1:
        raise r.err(f"stock must be at least 1, not {stock}")
    if stock is not None and stock > MAX_STOCK:
        raise r.err(stock_too_big(stock))
    rotation = r.nums("spread_rotation_deg", 3, (0.0, 0.0, 0.0))
    spreads = {"spread_yaw_deg": r.num("spread_yaw_deg", 0.0), "spread_pitch_deg": r.num("spread_pitch_deg", 0.0),
               "spread_speed": r.num("spread_speed", 0.0), "spread_tip_deg": r.num("spread_tip_deg", 0.0),
               "spread_turn_deg": r.num("spread_turn_deg", 0.0)}
    for key, v in (*spreads.items(), ("spread_position", min(spread_position)),
                   ("spread_rotation_deg", min(rotation))):
        if v < 0.0:
            raise r.err(f"{key} must be 0 or more, not {v!r}")
    speed = r.num("speed", 0.0)
    if speed < 0.0:
        raise r.err(f"speed must be 0 or more, not {speed!r}")
    tip = r.num("tip_deg", 90.0 if kind.flat else 0.0)
    if not -90.0 <= tip <= 90.0:
        raise r.err(f"tip_deg must be from -90 to 90, not {tip}")
    rad = kind.radius
    x, y, z = release
    if not (rad <= x <= FIELD_LENGTH - rad and rad <= y <= FIELD_WIDTH - rad):
        raise r.err(f"release point ({x:.4g}, {y:.4g}) lies off the field (0..{FIELD_LENGTH} x 0..{FIELD_WIDTH} m,"
                    f" center at least {rad:.4g} m inside)")
    if z < rad:
        raise r.err(f"release point is {z:.4g} m above the carpet; {_piece_words(kind)}'s center needs at"
                    f" least {rad:.4g} m")
    chute = None
    if r.has("chute"):
        c = r.sub("chute", _CHUTE)
        slope = c.num("slope_deg")
        if not 0.0 <= slope < 90.0:
            raise c.err(f"slope_deg must be from 0 to below 90, not {slope}")
        chute = Chute(length=c.num("length", positive=True), slope_deg=slope,
                      width=c.num("width", positive=True) if c.has("width") else None)
    st = Station(name=name, release=release, heading_deg=r.num("heading_deg"), pitch_deg=pitch, speed=speed,
                 interval=r.num("interval", DEFAULT_INTERVAL, positive=True), stock=stock, **spreads,
                 spread_position=spread_position, spread_rotation_deg=rotation, chute=chute, kind=kind_name,
                 tip_deg=tip, turn_deg=r.num("turn_deg", 0.0))
    if chute is not None:
        if chute.width is not None and chute.width <= kind.extent:
            what = f"the ball ({kind.extent} m)" if kind.ball is not None else f"the {kind.label} ({kind.extent:.4g} m)"
            raise RangeError(f"{path}: {r.where} chute: {chute.width:.4g} m wide, no wider than {what}")
        drop = _low_end_drop(st, kind)
        if drop > 0.0:
            raise RangeError(f"{path}: {r.where} chute: its low end would be {drop:.2g} m below the carpet; make"
                             " it shorter or less steep")
    return st


def parse_stations(path, tables, kinds: dict[str, PieceKind], default: str,
                   taken: tuple[str, ...] = ()) -> tuple[Station, ...]:
    """Read the `[[station]]` tables, feeding the field's `kinds` (`default` when a station names none). `taken` are
    the names of the goals, obstacles and elements, which a station's name may not repeat."""
    if not isinstance(tables, list):
        raise RangeError(f"{path}: station must be an array of tables ([[station]])")
    out: list[Station] = []
    for i, t in enumerate(tables):
        out.append(_station(path, t, i, kinds, default, list(taken) + [s.name for s in out]))
    return tuple(out)


def _world_boxes(center, yaw_deg: float, parts) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """(center, rotation, half sizes) in the field for boxes given in an element's frame (turned `yaw_deg` about z
    at (x, y) on the carpet)."""
    rz = _rot_z(yaw_deg)
    origin = np.array([center[0], center[1], 0.0])
    return [(origin + rz @ np.asarray(pc, float), rz @ np.asarray(pr, float), np.asarray(ph, float))
            for pc, pr, ph in parts]


def _element_boxes(e) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """The boxes an element is built from, level (a platform untilted, a moving panel at rest), in the field."""
    from .build_elements import bump_parts, panel_parts, platform_parts, ramp_parts, rod_parts
    from .elements import Bar, Box, Bump, Hinged, Platform, Ramp, Rod, Sliding

    if isinstance(e, Box):
        sx, sy, sz = e.size
        return _world_boxes(e.center, e.yaw_deg, [((0, 0, e.base_height + sz / 2.0), np.eye(3),
                                                  (sx / 2.0, sy / 2.0, sz / 2.0))])
    if isinstance(e, Rod):
        return [rod_parts(e)]
    if isinstance(e, (Hinged, Sliding)):    # at rest
        return _world_boxes(e.center, e.yaw_deg, [panel_parts(e)])
    if isinstance(e, Bar):
        r = e.diameter / 2.0
        return _world_boxes(e.center, e.yaw_deg, [((0, 0, e.height), np.eye(3), (e.length / 2.0, r, r))])
    if isinstance(e, (Bump, Ramp, Platform)):
        parts = bump_parts(e) if isinstance(e, Bump) else ramp_parts(e) if isinstance(e, Ramp) else platform_parts(e)
        return _world_boxes(e.center, e.yaw_deg, [(c, rot, (hx, e.width / 2.0, hz)) for c, rot, (hx, hz)
                                                  in parts.values()])
    return []   # a switch hangs overhead, on a hinge


def check_release_clear(path, rng: Range) -> None:
    """Refuse a release point whose piece (its bounding sphere) overlaps an element's box, an obstacle, or a station's
    chute (its own chute's walls, not the floor built under it), and a chute that reaches off the field or into an
    element, an obstacle or another station's chute."""
    if not rng.stations:
        return
    from .build_elements import chute_parts

    solids = [(f"element '{e.name}'", _element_boxes(e)) for e in rng.elements]
    solids += [(f"obstacle '{o.name}'", [(np.array(o.pos, float), o.rotation(), np.array(o.size) / 2.0)])
               for o in rng.obstacles]
    chutes = {s.name: chute_parts(s, s.kind_of(rng)) for s in rng.stations if s.chute is not None}
    solids += [(f"the chute of station '{name}'", list(parts.values())) for name, parts in chutes.items()]
    _check_chutes(path, rng, solids, chutes)
    for st in rng.stations:
        kind = st.kind_of(rng)
        r = kind.radius
        p = np.array(st.release)
        own = [b for part, b in chutes[st.name].items() if part != "floor"] if st.name in chutes else []
        for what, boxes in solids:
            if what == f"the chute of station '{st.name}'":
                boxes = own
            for center, rot, half in boxes:
                local = rot.T @ (p - center)
                if np.linalg.norm(local - np.clip(local, -half, half)) < r - _TOUCH:
                    x, y, z = st.release
                    raise RangeError(
                        f"{path}: [[station]] '{st.name}': its release point at ({x:.4g}, {y:.4g}, {z:.4g}) is"
                        f" inside {what}; move it clear (the {'ball' if kind.ball is not None else kind.label}'s"
                        f" center needs {r:.2g} m all round)")


def _corners(center, rot, half) -> np.ndarray:
    """The 8 corners of a box (center, rotation, half sizes)."""
    signs = np.array([[i, j, k] for i in (-1, 1) for j in (-1, 1) for k in (-1, 1)], float)
    return center + (signs * half) @ rot.T


def _overlap(a, b) -> bool:
    """Whether two boxes (center, rotation, half sizes) overlap by more than _TOUCH (the separating axis test)."""
    (ca, ra, ha), (cb, rb, hb) = a, b
    axes = [ra[:, i] for i in range(3)] + [rb[:, i] for i in range(3)]
    axes += [np.cross(ra[:, i], rb[:, j]) for i in range(3) for j in range(3)]
    d = cb - ca
    for u in axes:
        n = float(np.linalg.norm(u))
        if n < 1e-9:            # parallel edges: covered by the face axes
            continue
        u = u / n
        reach = float(np.abs(ra.T @ u) @ ha + np.abs(rb.T @ u) @ hb)
        if abs(float(d @ u)) >= reach - _TOUCH:
            return False
    return True


def _footprint(box) -> np.ndarray:
    """(4, 2) corners of a chute box's base from above: its length along the slope and its width, leaving out how far
    a wall's top leans out over the low end (a start's bumpers may tuck in there)."""
    center, rot, half = box
    base = center[:2] - rot[:2, 2] * half[2]
    along, across = rot[:2, 0] * half[0], rot[:2, 1] * half[1]
    return base + np.array([-along - across, along - across, along + across, -along + across])


def _check_chutes(path, rng: Range, solids, chutes: dict) -> None:
    """Each chute's boxes (`chutes`: station name -> chute_parts) on the field (inside its walls) and clear of the
    elements, obstacles, other chutes and the start poses' robot outlines (seen from above, as the elements are)."""
    for st in rng.stations:
        if st.chute is None:
            continue
        where = f"{path}: [[station]] '{st.name}': its chute"
        for part, box in chutes[st.name].items():
            c = _corners(*box)
            lo, hi = c.min(axis=0), c.max(axis=0)
            if lo[0] < 0.0 or lo[1] < 0.0 or hi[0] > FIELD_LENGTH or hi[1] > FIELD_WIDTH:
                raise RangeError(f"{where} reaches off the field (0..{FIELD_LENGTH} x 0..{FIELD_WIDTH} m: its"
                                 f" {part.replace('_', ' ')} spans x {lo[0]:.3g} to {hi[0]:.3g}, y {lo[1]:.3g} to"
                                 f" {hi[1]:.3g}); move the release point further onto the field")
            for what, boxes in solids:
                if what == f"the chute of station '{st.name}'":
                    continue
                if any(_overlap(box, other) for other in boxes):
                    raise RangeError(f"{where} runs into {what}; move the station or shorten its chute")
            for sname, (sx, sy, heading) in rng.starts.items():
                outline = _rect((sx, sy), heading, ROBOT_OUTLINE - 2 * _START_TOUCH, ROBOT_OUTLINE - 2 * _START_TOUCH)
                if not _separated(outline, _footprint(box)):
                    raise RangeError(f"{where} ({part.replace('_', ' ')}) overlaps start '{sname}' at"
                                     f" ({sx:.4g}, {sy:.4g}); move the start or the station (a {ROBOT_OUTLINE} m"
                                     " square robot outline is assumed)")


def with_overrides(rng: Range, overrides: dict) -> Range:
    """`rng` with the named stations' settings replaced: `{station name: {OVERRIDE_KEYS...}}`. `stock = 0` is no stock
    (the station feeds up to STATION_CAP); a list key takes 3 numbers. The release check runs again."""
    if not isinstance(overrides, dict):
        raise RangeError(f"station settings must be a table of station names, not {overrides!r}")
    names = [s.name for s in rng.stations]
    changed = {}
    for name, table in overrides.items():
        if name not in names:
            raise RangeError(f"the field has no station '{name}'; its stations: {', '.join(names) or 'none'}")
        r = _Reader(rng.path, f"station '{name}'", table, OVERRIDE_KEYS)
        new: dict = {}
        if r.has("interval"):
            new["interval"] = r.num("interval", positive=True)
        if r.has("stock"):
            stock = r.whole("stock")
            if stock < 0:
                raise r.err(f"stock must be 0 (no limit) or more, not {stock}")
            if stock > MAX_STOCK:
                raise r.err(stock_too_big(stock))
            new["stock"] = stock or None
        if r.has("tip_deg"):
            tip = r.num("tip_deg")
            if not -90.0 <= tip <= 90.0:
                raise r.err(f"tip_deg must be from -90 to 90, not {tip:g}")
            new["tip_deg"] = tip
        if r.has("turn_deg"):
            new["turn_deg"] = r.num("turn_deg")
        for key in ("spread_yaw_deg", "spread_pitch_deg", "spread_speed", "spread_position", "spread_rotation_deg",
                    "spread_tip_deg", "spread_turn_deg"):
            if r.has(key):
                v = r.nums(key, 3) if key in ("spread_position", "spread_rotation_deg") else r.num(key)
                if min(v if isinstance(v, tuple) else (v,)) < 0.0:
                    raise r.err(f"{key} must be 0 or more, not {v!r}")
                new[key] = v
        changed[name] = replace(next(s for s in rng.stations if s.name == name), **new)
    out = replace(rng, stations=tuple(changed.get(s.name, s) for s in rng.stations))
    check_release_clear(rng.path, out)
    return out
