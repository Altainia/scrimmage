"""A human player station in a range file: `[[station]]` feeds balls onto the field from a release point.

    [[station]]
    name = "feeder"
    release = [1.0, 4.0, 0.5]     # or release_in = [x, y, z]: where a fed ball's center appears (m)
    heading_deg = 0.0             # the way it is fed, about z from field +x
    pitch_deg = 20.0              # down from horizontal, -90 to 90 (default 0)
    speed = 1.0                   # m/s along that direction (default 0: the ball is only set down)
    interval = 1.0                # s between balls (default DEFAULT_INTERVAL)
    stock = 12                    # balls to feed (default: no limit, but at most STATION_CAP in one run)
    spread_yaw_deg = 5.0          # each feed varies uniformly within +-this of the heading, and so on
    spread_pitch_deg = 5.0
    spread_speed = 0.2
    spread_position = [0.0, 0.02, 0.02]    # or spread_position_in; a box around the release point, half sizes
    spread_rotation_deg = [0, 0, 0]        # the ball's turn; it does nothing to a ball (a later piece's)
    chute = { length = 0.6, slope_deg = 25.0, width = 0.3 }   # a sloped floor and two side walls (optional)

The spreads are limits, not standard deviations (the bench's inlet takes standard deviations): a piece never leaves
a tight chute's limits. The chute's floor top passes under the release point, tangent to a ball resting there, and
runs down along the heading for `length` at `slope_deg` (its high end reaches a little behind the release point, so
the ball lands on the floor's face); its side walls stand one ball diameter above the floor. Its `width` is the space
between the walls, 1.3 ball diameters when left out (so a ball of any preset fits).
Stations feed balls, not discs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from ..launcher.inputs import Ball
from .elements import IN
from .spec import FIELD_LENGTH, FIELD_WIDTH, RangeError, _Reader

if TYPE_CHECKING:
    from .spec import Range

DEFAULT_INTERVAL = 1.0      # s between balls (a human player's pace; some are much quicker, so it is a setting)
STATION_CAP = 50            # balls a station with no stock feeds at most in one run (the parked pool's size)
CHUTE_WIDTH_BALLS = 1.3     # a chute's default width between its walls, in ball diameters
CHUTE_WALL_BALLS = 1.0      # its side walls' height above the floor, in ball diameters
CHUTE_PLATE = 0.25 * IN     # m: the chute's floor and wall thickness (estimate: a polycarbonate sheet)
DEFAULT_SEED = 1            # the spreads' random seed, as the bench's
OVERRIDE_KEYS = ("interval", "stock", "spread_yaw_deg", "spread_pitch_deg", "spread_speed", "spread_position",
                 "spread_rotation_deg")
_STATION = ("name", "release", "release_in", "heading_deg", "pitch_deg", "speed", "interval", "stock",
            "spread_yaw_deg", "spread_pitch_deg", "spread_speed", "spread_position", "spread_position_in",
            "spread_rotation_deg", "chute")
_CHUTE = ("width", "length", "slope_deg")
_TOUCH = 1e-6   # m: a ball resting on a surface is not inside it


def check_seed(seed) -> int:
    """`seed` if it is a whole number, 0 or more (numpy's seeds); a ValueError otherwise."""
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError(f"seed must be a whole number, 0 or more, not {seed!r}")
    return seed


@dataclass(frozen=True)
class Chute:
    length: float               # along the slope
    slope_deg: float            # below horizontal
    width: float | None = None  # between the walls; None: CHUTE_WIDTH_BALLS ball diameters


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

    def pool(self) -> int:
        """Balls the station can feed in one run."""
        return self.stock if self.stock is not None else STATION_CAP

    def direction(self, yaw_deg: float, pitch_deg: float) -> np.ndarray:
        """The unit vector for a feed at `yaw_deg` about z from field +x and `pitch_deg` down from horizontal."""
        y, p = math.radians(yaw_deg), math.radians(pitch_deg)
        return np.array([math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), -math.sin(p)])

    def chute_width(self, ball_d: float) -> float:
        """The space between the chute's walls for a ball of diameter `ball_d`."""
        width = self.chute.width if self.chute is not None else None
        return width if width is not None else CHUTE_WIDTH_BALLS * ball_d


def _low_end_drop(st: Station, ball_d: float) -> float:
    """How far the chute's floor underside at its low end lies below the carpet (negative: above it)."""
    c = st.chute
    s = math.radians(c.slope_deg)
    top = st.release[2] - (ball_d / 2.0) / math.cos(s) - c.length * math.sin(s)
    return -(top - CHUTE_PLATE * math.cos(s))


def _station(path, table, i: int, piece, taken: list[str]) -> Station:
    r = _Reader(path, f"[[station]] #{i + 1}", table, _STATION)
    name = r.text("name")
    r.where = f"[[station]] '{name}'"
    if not isinstance(piece, Ball):
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
    rotation = r.nums("spread_rotation_deg", 3, (0.0, 0.0, 0.0))
    spreads = {"spread_yaw_deg": r.num("spread_yaw_deg", 0.0), "spread_pitch_deg": r.num("spread_pitch_deg", 0.0),
               "spread_speed": r.num("spread_speed", 0.0)}
    for key, v in (*spreads.items(), ("spread_position", min(spread_position)),
                   ("spread_rotation_deg", min(rotation))):
        if v < 0.0:
            raise r.err(f"{key} must be 0 or more, not {v!r}")
    speed = r.num("speed", 0.0)
    if speed < 0.0:
        raise r.err(f"speed must be 0 or more, not {speed!r}")
    rad = piece.diameter / 2.0
    x, y, z = release
    if not (rad <= x <= FIELD_LENGTH - rad and rad <= y <= FIELD_WIDTH - rad):
        raise r.err(f"release point ({x:.4g}, {y:.4g}) lies off the field (0..{FIELD_LENGTH} x 0..{FIELD_WIDTH} m,"
                    f" center at least {rad:.4g} m inside)")
    if z < rad:
        raise r.err(f"release point is {z:.4g} m above the carpet; a {piece.diameter} m ball's center needs at"
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
                 spread_position=spread_position, spread_rotation_deg=rotation, chute=chute)
    if chute is not None:
        if chute.width is not None and chute.width <= piece.diameter:
            raise RangeError(f"{path}: {r.where} chute: {chute.width:.4g} m wide, no wider than the ball"
                             f" ({piece.diameter} m)")
        drop = _low_end_drop(st, piece.diameter)
        if drop > 0.0:
            raise RangeError(f"{path}: {r.where} chute: its low end would be {drop:.2g} m below the carpet; make"
                             " it shorter or less steep")
    return st


def parse_stations(path, tables, piece, taken: tuple[str, ...] = ()) -> tuple[Station, ...]:
    """Read the `[[station]]` tables. `taken` are the names of the goals, obstacles and elements, which a station's
    name may not repeat."""
    if not isinstance(tables, list):
        raise RangeError(f"{path}: station must be an array of tables ([[station]])")
    out: list[Station] = []
    for i, t in enumerate(tables):
        out.append(_station(path, t, i, piece, list(taken) + [s.name for s in out]))
    return tuple(out)


def _world_boxes(center, yaw_deg: float, parts) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """(center, rotation, half sizes) in the field for boxes given in an element's frame (turned `yaw_deg` about z
    at (x, y) on the carpet)."""
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    rz = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    origin = np.array([center[0], center[1], 0.0])
    return [(origin + rz @ np.asarray(pc, float), rz @ np.asarray(pr, float), np.asarray(ph, float))
            for pc, pr, ph in parts]


def _element_boxes(e) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """The boxes an element is built from, level (a platform untilted), in the field."""
    from .build_elements import bump_parts, platform_parts, ramp_parts
    from .elements import Bar, Box, Bump, Platform, Ramp

    if isinstance(e, Box):
        sx, sy, sz = e.size
        return _world_boxes(e.center, e.yaw_deg, [((0, 0, e.base_height + sz / 2.0), np.eye(3),
                                                  (sx / 2.0, sy / 2.0, sz / 2.0))])
    if isinstance(e, Bar):
        r = e.diameter / 2.0
        return _world_boxes(e.center, e.yaw_deg, [((0, 0, e.height), np.eye(3), (e.length / 2.0, r, r))])
    if isinstance(e, (Bump, Ramp, Platform)):
        parts = bump_parts(e) if isinstance(e, Bump) else ramp_parts(e) if isinstance(e, Ramp) else platform_parts(e)
        return _world_boxes(e.center, e.yaw_deg, [(c, rot, (hx, e.width / 2.0, hz)) for c, rot, (hx, hz)
                                                  in parts.values()])
    return []   # a switch hangs overhead, on a hinge


def check_release_clear(path, rng: Range) -> None:
    """Refuse a release point whose ball overlaps an element's box, an obstacle, or a station's chute, and a chute
    that reaches off the field or into an element, an obstacle or another station's chute."""
    if not rng.stations:
        return
    from .build_elements import chute_parts

    r = rng.piece.diameter / 2.0
    solids = [(f"element '{e.name}'", _element_boxes(e)) for e in rng.elements]
    solids += [(f"obstacle '{o.name}'", [(np.array(o.pos, float), o.rotation(), np.array(o.size) / 2.0)])
               for o in rng.obstacles]
    solids += [(f"the chute of station '{s.name}'", list(chute_parts(s, rng.piece.diameter).values()))
               for s in rng.stations if s.chute is not None]
    _check_chutes(path, rng, solids)
    for st in rng.stations:
        p = np.array(st.release)
        for what, boxes in solids:
            for center, rot, half in boxes:
                local = rot.T @ (p - center)
                if np.linalg.norm(local - np.clip(local, -half, half)) < r - _TOUCH:
                    x, y, z = st.release
                    raise RangeError(
                        f"{path}: [[station]] '{st.name}': its release point at ({x:.4g}, {y:.4g}, {z:.4g}) is"
                        f" inside {what}; move it clear (the ball's center needs {r:.2g} m all round)")


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


def _check_chutes(path, rng: Range, solids) -> None:
    """Each chute's boxes on the field (inside its walls) and clear of the elements, obstacles and other chutes."""
    from .build_elements import chute_parts

    for st in rng.stations:
        if st.chute is None:
            continue
        where = f"{path}: [[station]] '{st.name}': its chute"
        for part, box in chute_parts(st, rng.piece.diameter).items():
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
