"""A match in the Test tab: where the run is in the range's `[game]` (auto, the pause, teleop), whether the robot left
the start zone, the climb level it ended on, and the points so far.

The runner makes one MatchState when the test has `match = true`, with the run time the first block starts at, and
calls `see` after every physics step; the Watch reads it for the `match` and `points` conditions. Scoring is
testrange/game.py's `score`, which reads the ball log and never writes to it.
"""

from __future__ import annotations

from ..testrange.game import Game, Points, score


class MatchState:
    def __init__(self, game: Game, zones, levels: list[str], start: float):
        self.game = game
        self.start = start
        self.phases = game.phases(start)
        self.end = self.phases.teleop[1]
        self._zone = next((z for z in zones if game.leave is not None and z.name == game.leave.zone), None)
        self._levels = levels
        self.phase: str | None = None       # the phase at the last step; None before the first
        self.left: bool | None = None       # LEAVE: None until auto ends, then fixed
        self.level: str | None = None       # the climb level held now, fixed once the match has ended
        self._inside0: bool | None = None   # whether the robot's center was in the leave zone at the start
        self._ended = False

    def see(self, t: float, x: float, y: float, rank: int) -> str | None:
        """The state after a physics step at run time `t`: the robot's center (x, y) and the climb monitor's level
        rank (0: none). Returns the new phase when it changed."""
        if self._zone is not None:
            inside = self._zone.contains(x, y)
            if self._inside0 is None:
                self._inside0 = inside
            if self.left is None and t >= self.phases.auto[1]:
                self.left = bool(self._inside0 and not inside)
        if not self._ended:
            self.level = self._levels[rank - 1] if 0 < rank <= len(self._levels) else None
            self._ended = t >= self.end
        phase = self.phases.of(t)
        changed = phase if phase is not None and phase != self.phase else None
        if phase is not None:
            self.phase = phase
        return changed

    def points(self, log: list[dict], at: float | None = None) -> Points:
        """The points so far (with `at`, the run time) or of the whole run, read from the ball log."""
        return score(self.game, log, self.phases, self.left, self.level, at)

    def to_dict(self) -> dict:
        """The phase times, for result.json: run time (s), each phase's [start, end]."""
        a, t = self.phases.auto, self.phases.teleop
        return {"start": self.start, "auto": list(a), "pause": [a[1], t[0]], "teleop": list(t), "left": self.left,
                "level": self.level}
