"""The range's human player stations at run time: each feeds pieces from its parked pool (RangeLayout.station_slots)
when it is told to (start, stop, request one).

A feed puts the station's next pool ball in play at the release point, moving along the station's direction, before
the physics step (RangeRuntime.before_physics), so its first step already moves. Each feed varies within the
station's spreads, drawn uniformly from -1 to 1:

- position: release + U * spread_position on each axis;
- speed: speed + U * spread_speed, at least 0;
- direction: Station.direction(heading_deg + U * spread_yaw_deg, pitch_deg + U * spread_pitch_deg);
- turn: roll, pitch and yaw of U * spread_rotation_deg (it does nothing to a ball); a rigid piece is first laid at
  PieceKind.orient(tip_deg + U * spread_tip_deg, heading_deg + turn_deg + U * spread_turn_deg). No spin.

The draws come from numpy.random.default_rng([seed, k]) for station k, nine per feed in a fixed order (yaw, pitch,
speed, dx, dy, dz, roll, pitch, yaw) and, for a rigid piece, two more (tip, turn), so a seed gives the same feeds
whatever the timing, and one station's feeds don't change another's. A fed piece has BALL_BITS: it can touch the
robot at once (a human player feeds into it).

A station feeds when it is feeding (start) or has pieces requested (request), its last feed was at least its
interval ago (the first is at once), its pool isn't used up, and no piece in play has its center within its own
bounding diameter of the release point (the last one hasn't cleared it yet: the station waits, as the bench's inlet
does).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .balls import Balls
from .build import BALL_BITS, RangeLayout, _quat, _rpy
from .pieces import _matrix
from .spec import Range
from .station import DEFAULT_SEED, check_seed

EPS = 1e-6      # s: a feed due within this of now is due now (the step clock's rounding)


@dataclass
class Fed:
    """One fed piece."""
    ball: int                                   # its ball index
    station: str
    t: float                                    # sim time it was released
    release: tuple[float, float, float]         # where it was put (m)
    velocity: tuple[float, float, float]        # its velocity then (m/s)


class Feeder:
    def __init__(self, balls: Balls, layout: RangeLayout, rng: Range, seed: int = DEFAULT_SEED):
        self.balls, self.layout = balls, layout
        self.stations = rng.stations
        self._kinds = [st.kind_of(rng) for st in self.stations]
        self._k = {st.name: k for k, st in enumerate(self.stations)}
        self.seed = seed
        self.fed: list[Fed] = []
        self.reset(seed)

    def reset(self, seed: int | None = None) -> None:
        """The draws restarted (from `seed` when given, else the current seed), every pool unused, nothing feeding
        or requested. It leaves the balls alone: call it right after Balls.clear() (as RangeRuntime.reset does),
        which parks the pools and gives their logs back."""
        if seed is not None:
            self.seed = check_seed(seed)
        n = len(self.stations)
        self._gen = [np.random.default_rng([self.seed, k]) for k in range(n)]
        self._next = [0] * n                    # the next unused pool ball, per station
        self._last = [-math.inf] * n            # the last feed's time
        self._requested = [0] * n
        self._feeding = [False] * n
        self.fed = []

    def _index(self, name: str) -> int:
        k = self._k.get(name)
        if k is None:
            raise ValueError(f"no station {name!r}; this range's stations: {', '.join(self._k) or 'none'}")
        return k

    # ------------------------------------------------------------------------------------------------ commands

    def start(self, name: str) -> None:
        """Feed a piece every interval from now on (the first at once), until stop or the pool runs out."""
        self._feeding[self._index(name)] = True

    def stop(self, name: str) -> None:
        """No more feeding (pieces asked for with request still come)."""
        self._feeding[self._index(name)] = False

    def stop_all(self) -> None:
        """No station feeds again until told to: every one stopped, and pieces asked for and not yet fed dropped."""
        self._feeding = [False] * len(self.stations)
        self._requested = [0] * len(self.stations)

    def request(self, name: str) -> None:
        """One more piece, as soon as the interval since the last feed has passed."""
        self._requested[self._index(name)] += 1

    # ------------------------------------------------------------------------------------------------ each step

    def step(self, t: float) -> list[Fed]:
        """Before the physics step at sim time `t`: release every piece that is due. The pieces fed now."""
        out: list[Fed] = []
        for k, st in enumerate(self.stations):
            want = self._feeding[k] or self._requested[k] > 0
            if not want or self._next[k] >= st.pool() or t < self._last[k] + st.interval - EPS or self._blocked(k):
                continue
            i = self.layout.station_slots[st.name][self._next[k]]
            yaw, pitch, speed, dx, dy, dz, rx, ry, rz = self._gen[k].uniform(-1.0, 1.0, 9).tolist()
            pos = np.array(st.release) + np.array([dx, dy, dz]) * np.array(st.spread_position)
            v = max(0.0, st.speed + speed * st.spread_speed) * st.direction(
                st.heading_deg + yaw * st.spread_yaw_deg, st.pitch_deg + pitch * st.spread_pitch_deg)
            turn = _rpy(np.array([rx, ry, rz]) * np.array(st.spread_rotation_deg))
            kind = self._kinds[k]
            if kind.form != "ball":
                tip, about = self._gen[k].uniform(-1.0, 1.0, 2).tolist()
                turn = turn @ _matrix(kind.orient(st.tip_deg + tip * st.spread_tip_deg,
                                                  st.heading_deg + st.turn_deg + about * st.spread_turn_deg))
            self.balls.place(i, pos, v, (0.0, 0.0, 0.0), BALL_BITS, quat=_quat(turn))
            self._next[k] += 1
            self._last[k] = t
            self._requested[k] = max(0, self._requested[k] - 1)
            fed = Fed(ball=int(i), station=st.name, t=float(t), release=tuple(float(c) for c in pos),
                      velocity=tuple(float(c) for c in v))
            self.fed.append(fed)
            out.append(fed)
        return out

    def _blocked(self, k: int) -> bool:
        b = self.balls
        f = np.flatnonzero(b.field)
        if not len(f):
            return False
        gap = np.linalg.norm(b._qpos[f, :3] - np.array(self.stations[k].release), axis=1)
        return bool((gap < 2.0 * b.radius[f]).any())

    # ------------------------------------------------------------------------------------------------ reports

    def count(self, name: str) -> int:
        """Pieces the station has fed since the reset."""
        return self._next[self._index(name)]

    def left(self, name: str) -> int | None:
        """Pieces left in its stock; None for a station without one."""
        st = self.stations[self._index(name)]
        return None if st.stock is None else st.stock - self.count(name)

    def empty(self, name: str) -> bool:
        """A station with a stock that has fed all of it."""
        return self.left(name) == 0

    def capped(self, name: str) -> bool:
        """Its pool is used up (its stock, or STATION_CAP without one): it feeds no more this run."""
        k = self._index(name)
        return self._next[k] >= self.stations[k].pool()

    def blocked(self, name: str) -> bool:
        """A piece in play is at the release point now (its center within its bounding diameter of it)."""
        return self._blocked(self._index(name))
