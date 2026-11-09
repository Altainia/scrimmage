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
points. `climb_points` is a copy of the range's `[climb] levels`, so scoring needs only the game.
"""

from __future__ import annotations

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

    @property
    def match_s(self) -> float:
        return self.auto_s + self.pause_s + self.teleop_s


def _points(r, value, key: str) -> float:
    """One points number from `value` (a plain number, never a bool, not negative) named `key` in reader `r`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise r.err(i18n.t("{key} must be a number, not {value}", key=key, value=repr(value)))
    if not value >= 0:
        raise r.err(i18n.t("{key} can't be negative, not {value}", key=key, value=repr(value)))
    return float(value)


def parse_game(path, table, goals, zones, climb) -> Game:
    """Read `[game]`. `goals` and `zones` are the range's, which `points` and `leave` name; `climb` is its parsed
    `[climb]` rule or None."""
    from .spec import _Reader

    r = _Reader(path, "[game]", table, _GAME)
    name = r.text("name") if r.has("name") else ""
    auto_s = r.num("auto_seconds", positive=True)
    pause_s = r.num("pause_seconds", nonnegative=True)
    teleop_s = r.num("teleop_seconds", positive=True)
    points: dict[str, GoalPoints] = {}
    if r.has("points"):
        raw = r.raw("points")
        p = _Reader(path, "[game] points", raw, tuple(g.name for g in goals))
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
        q = r.sub("rules", _RULES)
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
    return Game(name=name, auto_s=auto_s, pause_s=pause_s, teleop_s=teleop_s, points=points, leave=leave,
                rules=rules, climb_points=dict(climb.levels) if climb is not None else {})
