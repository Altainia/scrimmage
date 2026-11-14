"""Watching a robot's height, reach and diameter during a run against the season's match limits (design Calls I6, I7
and answer 6). Made by the World for a robot config with an [inspection] section (world.inspection); None without one.

The monitor samples every SAMPLE_S of simulated time, not every physics step: turning every geom's points is the
expensive part, and the rules' limits are judged over seconds, so a flick past the line shorter than 20 ms can be
missed. A measure the season has no limit for is skipped (2025 has no height limit; only 2013 has a diameter).

Each breach is one note per world episode (a reset starts a new one), worded like the climb runs' G106 and G107 notes:
when it first went over, how far over at most and when, and how long it was over in all. A limit with an exception
(2022's hangar zone) is still noted, quoting the exception and "not checked". The notes are Said: the str is the
machine text (SI, American English), `words()` the Units and Language settings."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from .. import units
from ..checks import Said
from ..i18n import t
from .measure import Extent, geom_label, smallest_circle
from .report import Inspection, warnings
from .spec import Limit, Season

SAMPLE_S = 0.02         # s of simulated time between samples (Call I7)
WATCHED = ("height", "reach", "diameter")     # the measures sampled during a run; the start check skips them
KEEP = 60               # breaches kept from earlier episodes (a long session's resets don't grow it without end)


def _what(key: str) -> str:
    if key == "height":
        return t("height")
    if key == "reach":
        return t("reach beyond the frame")
    return t("diameter of the smallest circle holding the robot")


def _length(v: float, fewer: int = 1) -> str:
    return units.fmt(v, "length", fewer=fewer)


@dataclass
class Breach:
    """One measure over its limit in one world episode."""
    key: str
    rule: str
    limit: float            # m
    unless: str
    year: int
    episode: int
    first: float            # s: when the robot first went over
    peak: float = 0.0       # m over the limit, the most
    peak_t: float = 0.0
    peak_part: str = ""     # the reach's geom at the peak ("" for height and diameter)
    over_s: float = 0.0     # s spent over in all

    def note(self) -> Said:
        """The breach as it stands now (a copy: the breach goes on growing while it lasts)."""
        b = dataclasses.replace(self)

        def words() -> str:
            part = f" ({b.peak_part})" if b.peak_part else ""
            s = t("{year} {rule}: {what} over the {limit} limit from t={first} s, by up to {peak}{part} at {peak_t} s "
                  "({over} s over in all)", year=b.year, rule=b.rule, what=_what(b.key),
                  limit=_length(b.limit, 0), first=f"{b.first:.2f}", peak=_length(b.peak),
                  peak_t=f"{b.peak_t:.2f}", part=part, over=f"{b.over_s:.2f}")
            if b.unless:
                s += t("; {exception}: not checked", exception=b.unless)
            return s
        with units.fixed("metric"):
            machine = words()
        return Said(machine, words)


class InspectionMonitor:
    """The robot's height, reach and (2013) diameter, every SAMPLE_S, against `season`'s match limits. `start` is the
    inspection at the start pose (inspect_world), for the start notes."""

    def __init__(self, world, season: Season, start: Inspection | None = None):
        self.world, self.season, self.start = world, season, start
        self.watched: dict[str, Limit] = {k: getattr(season, k) for k in WATCHED
                                          if getattr(season, k).value is not None}
        self._extent = Extent(world) if self.watched else None
        self.episode = 0
        self.samples = 0                    # samples taken this episode
        self._current: dict[str, Breach] = {}
        self._past: list[Breach] = []
        self._last = self._next = 0.0
        self._sample(first=True)

    def reset(self) -> None:
        """A world reset: a new episode, sampled at once (its start pose)."""
        self._past = (self._past + list(self._current.values()))[-KEEP:]
        self._current = {}
        self.episode += 1
        self.samples = 0
        self._sample(first=True)

    def after_physics(self) -> None:
        if self.world.data.time + 1e-9 >= self._next:
            self._sample()

    def _sample(self, first: bool = False) -> None:
        now = float(self.world.data.time)
        dt = SAMPLE_S if first else now - self._last     # the time this sample stands for
        self._last, self._next = now, now + SAMPLE_S
        self.samples += 1
        if self._extent is None:
            return
        height = reach = 0.0
        where = -1
        if "height" in self.watched or "reach" in self.watched:
            height, reach, where = self._extent.read_where()
        for key, limit in self.watched.items():
            value = {"height": height, "reach": reach}.get(key)
            if value is None:
                value = self._diameter(limit.value)
            over = value - limit.value
            if over <= 0:
                continue
            b = self._current.get(key)
            if b is None:
                b = self._current[key] = Breach(key, limit.rule, limit.value, limit.unless, self.season.year,
                                                self.episode, now)
            b.over_s += dt
            if over > b.peak:
                b.peak, b.peak_t = over, now
                b.peak_part = geom_label(self.world.model, where) if key == "reach" and where >= 0 else ""

    def _diameter(self, limit: float) -> float:
        """The smallest circle's diameter, or, when the points' bounding box's diagonal is within `limit` (the circle
        is no larger), that diagonal: the circle is only solved when it may be over."""
        pts = self._extent.points()[:, :2]
        if not len(pts):
            return 0.0
        span = pts.max(axis=0) - pts.min(axis=0)
        diagonal = float(np.hypot(*span))
        return diagonal if diagonal <= limit else smallest_circle(pts)

    def breaches(self, since: int = 0) -> list[Breach]:
        """The breaches of episode `since` and later, in the order they began."""
        return [b for b in self._past + list(self._current.values()) if b.episode >= since]

    def notes(self, since: int = 0) -> list[Said]:
        return [b.note() for b in self.breaches(since)]

    def start_notes(self) -> list[Said]:
        """The start check's warnings but height, reach and diameter (the first sample covers those)."""
        if self.start is None:
            return []
        return warnings(self.start, skip=WATCHED)
