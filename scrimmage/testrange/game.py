"""The game a range file declares: `[game]`, its points, the match's phases and the rules a robot is held to.

    [game]
    name = "2024 CRESCENDO"
    auto_seconds = 15
    pause_seconds = 3
    teleop_seconds = 135
    points = { speaker_blue = { auto = 5, teleop = 2 }, amp_blue = 1 }   # a goal's points: one number or per phase
    leave = { zone = "blue_start", points = 2 }
    [game.rules]
    possession = 1
    extension_in = 12
    height_in = 48

Every key is optional but the three phase lengths. A goal that `points` doesn't list still counts pieces, for 0
points. `climb_points` is a copy of the range's `[climb] levels`, and `harmony` of its `[climb] harmony` (the
"off-carpet" rule's points for each robot beyond the first on the robot's hold), so scoring needs only the game.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .. import i18n

INCH = 0.0254   # m
_GAME = ("name", "auto_seconds", "pause_seconds", "teleop_seconds", "points", "leave", "rules")
_RULES = ("possession", "extension_in", "height_in")
_LEAVE = ("zone", "points")
_POINTS = ("auto", "teleop")


@dataclass(frozen=True)
class GoalPoints:
    auto: float
    teleop: float


@dataclass(frozen=True)
class Leave:
    zone: str
    points: float


@dataclass(frozen=True)
class Rules:
    possession: int | None = None       # pieces held at once, at most
    extension: float | None = None      # m: how far the robot reaches past its frame, at most
    height: float | None = None         # m: how tall the robot stands, at most


@dataclass(frozen=True)
class Game:
    name: str
    auto_s: float
    pause_s: float
    teleop_s: float
    points: dict[str, GoalPoints] = field(default_factory=dict)
    leave: Leave | None = None
    rules: Rules | None = None
    climb_points: dict[str, float] = field(default_factory=dict)
    harmony: float = 0.0            # points for each robot beyond the first on the robot's hold ([climb] harmony)
    goals: tuple[str, ...] = ()     # the range's goal names, in its order: the order a score's items come in

    @property
    def match_s(self) -> float:
        return self.auto_s + self.pause_s + self.teleop_s

    def phases(self, start: float) -> Phases:
        """The phases of a match that starts at run time `start`."""
        return Phases(auto=(start, start + self.auto_s),
                      teleop=(start + self.auto_s + self.pause_s, start + self.match_s))


def _points(r, value, key: str) -> float:
    """One points number from `value` (a plain number, never a bool, not negative) named `key` in reader `r`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise r.err(i18n.t("{key} must be a number, not {value}", key=key, value=repr(value)))
    if not value >= 0:
        raise r.err(i18n.t("{key} can't be negative, not {value}", key=key, value=repr(value)))
    if not math.isfinite(value):
        raise r.err(i18n.t("{key} must be a finite number, not {value}", key=key, value=repr(value)))
    return float(value)


def _seconds(r, key: str, **kw) -> float:
    """A phase length from reader `r`: `r.num`'s checks, and finite."""
    v = r.num(key, **kw)
    if not math.isfinite(v):
        raise r.err(i18n.t("{key} must be a finite number of seconds, not {value}", key=key, value=repr(v)))
    return v


def parse_game(path, table, goals, zones, climb) -> Game:
    """Read `[game]`. `goals` and `zones` are the range's, which `points` and `leave` name; `climb` is its parsed
    `[climb]` rule or None."""
    from .spec import _Reader

    r = _Reader(path, "[game]", table, _GAME)
    name = r.text("name") if r.has("name") else ""
    auto_s = _seconds(r, "auto_seconds", positive=True)
    pause_s = _seconds(r, "pause_seconds", nonnegative=True)
    teleop_s = _seconds(r, "teleop_seconds", positive=True)
    points: dict[str, GoalPoints] = {}
    if r.has("points"):
        raw = r.raw("points")
        p = _Reader(path, "[game] points", raw, tuple(raw))
        names = [g.name for g in goals]
        for goal in raw:
            if goal not in names:
                raise p.err(i18n.t("'{goal}' is not a goal of the range; goals: {goals}", goal=goal,
                                   goals=", ".join(names) or i18n.t("none")))
        for goal, value in raw.items():
            if isinstance(value, dict):
                q = _Reader(path, f"[game] points {goal}", value, _POINTS)
                points[goal] = GoalPoints(_points(q, q.raw("auto"), "auto"), _points(q, q.raw("teleop"), "teleop"))
            else:
                n = _points(p, value, goal)
                points[goal] = GoalPoints(n, n)
    leave = None
    if r.has("leave"):
        q = r.sub("leave", _LEAVE)
        zone = q.text("zone")
        if zone not in [z.name for z in zones]:
            raise q.err(i18n.t("zone '{zone}' is not a zone of the range; zones: {zones}", zone=zone,
                               zones=", ".join(z.name for z in zones) or i18n.t("none")))
        leave = Leave(zone, _points(q, q.raw("points"), "points"))
    rules = None
    if r.has("rules"):
        q = _Reader(path, "[game.rules]", r.raw("rules"), _RULES)
        possession = extension = height = None
        if q.has("possession"):
            possession = q.whole("possession")
            if possession < 1:
                raise q.err(i18n.t("possession must be 1 or more, not {value}", value=repr(possession)))
        if q.has("extension_in"):
            extension = q.num("extension_in", positive=True) * INCH
        if q.has("height_in"):
            height = q.num("height_in", positive=True) * INCH
        rules = Rules(possession, extension, height)
    ordered = {g.name: points[g.name] for g in goals if g.name in points}
    return Game(name=name, auto_s=auto_s, pause_s=pause_s, teleop_s=teleop_s, points=ordered, leave=leave,
                rules=rules, climb_points=dict(climb.levels) if climb is not None else {},
                harmony=climb.harmony if climb is not None else 0.0, goals=tuple(g.name for g in goals))


def leave_zone(game: Game, zones):
    """The range zone `game`'s LEAVE names, or None without a `leave`."""
    return next((z for z in zones if game.leave is not None and z.name == game.leave.zone), None)


def level_name(levels: list[str], rank: int) -> str | None:
    """The climb level a climb monitor's rank names (1: the first of `levels`); None for 0 or out of range."""
    return levels[rank - 1] if 0 < rank <= len(levels) else None


@dataclass(frozen=True)
class Phases:
    """The match's phases in run time (s): auto is [start, end), teleop is [start, end] (the match ends at teleop's
    end, a piece scored then still counts), and the pause is between them."""
    auto: tuple[float, float]
    teleop: tuple[float, float]

    def of(self, t: float) -> str | None:
        """"auto", "pause" or "teleop" for run time `t`; None before auto starts or after teleop ends."""
        if self.auto[0] <= t < self.auto[1]:
            return "auto"
        if self.auto[1] <= t < self.teleop[0]:
            return "pause"
        if self.teleop[0] <= t <= self.teleop[1]:
            return "teleop"
        return None


@dataclass(frozen=True)
class PointsItem:
    source: str     # a goal's name, "leave", "climb" or "harmony"
    phase: str      # "auto", "teleop", "endgame" or "pause"
    count: int
    points: float

    def to_dict(self) -> dict:
        return {"source": self.source, "phase": self.phase, "count": self.count, "points": self.points}


@dataclass(frozen=True)
class Points:
    auto: float
    teleop: float
    endgame: float
    total: float
    items: tuple[PointsItem, ...] = ()

    def to_dict(self) -> dict:
        return {"auto": self.auto, "teleop": self.teleop, "endgame": self.endgame, "total": self.total,
                "items": [i.to_dict() for i in self.items]}


def score(game: Game, log: list[dict], phases: Phases, left: bool | None, level: str | None,
          at: float | None = None, held: bool = False, harmony: float = 0.0) -> Points:
    """The points of a run, read from `ball_log()`'s rows (never written to). A piece scored in the pause scores
    nothing (its item says so); one scored outside the match isn't counted. `left` is whether LEAVE was earned and
    `level` the climb level held when the match ended (None: not earned or not decided, 0 points). With `at`, only
    pieces with `scored_t <= at` count, LEAVE once auto has ended by `at`, and the endgame once the match has ended
    by `at`; with `held` too, the endgame is `level`'s points whenever it is read (the score if the match ended
    then). `harmony` is the HARMONY points held when the match ended (the climb monitor's climb.harmony), scored as
    the level is, as an item after the climb's: ("harmony", "endgame", the other robots on the hold, points), the
    count being `harmony` over the game's points per robot (R8, F4)."""
    counts: dict[tuple[str, str], int] = {}
    for row in log:
        goal, t = row.get("goal"), row.get("scored_t")
        if goal is None or t is None or (at is not None and t > at):
            continue
        phase = phases.of(t)
        if phase is not None:
            counts[goal, phase] = counts.get((goal, phase), 0) + 1
    order = list(game.goals) + [g for g in dict.fromkeys(g for g, _ in counts) if g not in game.goals]
    items: list[PointsItem] = []
    auto = teleop = endgame = 0.0
    for goal in order:
        gp = game.points.get(goal, GoalPoints(0.0, 0.0))
        for phase in ("auto", "pause", "teleop"):
            n = counts.get((goal, phase), 0)
            if not n:
                continue
            each = {"auto": gp.auto, "teleop": gp.teleop, "pause": 0.0}[phase]
            items.append(PointsItem(goal, phase, n, n * each))
            auto += n * each if phase == "auto" else 0.0
            teleop += n * each if phase == "teleop" else 0.0
    if left and game.leave is not None and (at is None or at >= phases.auto[1]):
        items.append(PointsItem("leave", "auto", 1, game.leave.points))
        auto += game.leave.points
    if level is not None and (at is None or held or at >= phases.teleop[1]):
        pts = game.climb_points.get(level, 0.0)
        items.append(PointsItem("climb", "endgame", 1, pts))
        endgame += pts
    if harmony > 0.0 and (at is None or held or at >= phases.teleop[1]):
        others = round(harmony / game.harmony) if game.harmony > 0.0 else 0
        items.append(PointsItem("harmony", "endgame", others, harmony))
        endgame += harmony
    return Points(auto, teleop, endgame, auto + teleop + endgame, tuple(items))
