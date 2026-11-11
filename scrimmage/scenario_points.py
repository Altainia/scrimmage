"""A scenario's points: the range's `[game]` scored over the scenario's own steps (spec D6).

A scenario has no match clock. Its phases come from its steps: a piece scored during a step in mode `auto` is an auto
piece, in `teleop` a teleop one, and in a `disabled` step the pause (it scores nothing). A step in any other mode (test)
is neither. LEAVE is judged from the robot's center at the scenario's start and at the end of the last auto step; the
endgame is the climb level held when the scenario ends, and the HARMONY points with it. Scoring is testrange/game.py's
`score`, reading the ball log.

The runner makes one ScenarioMatch per run on a range with a `[game]`, and calls `begin` and `finish` around each step
(per step, not per physics step). The points signals and the report's Score section read it.
"""

from __future__ import annotations

from types import SimpleNamespace

from .testrange.game import Points, leave_zone, level_name, score

INF = float("inf")
_PHASE = {"auto": "auto", "disabled": "pause", "teleop": "teleop"}


class StepPhases:
    """The same `of(t)` as game.Phases, over the scenario's step intervals, [start, end) (the last step's end counts
    too). `auto` and `teleop` give what `score` reads: auto[1] is when auto ended (the end of the last auto step,
    infinity while none is over or planned), teleop[1] when the scenario ends."""

    def __init__(self) -> None:
        self.steps: list[list] = []     # [mode, start, end]
        self.closed = False
        self.last_step = False          # the running step is the scenario's last: its planned end ends the match

    def of(self, t: float) -> str | None:
        for i, (mode, a, b) in enumerate(self.steps):
            last = i == len(self.steps) - 1
            if a <= t < b or (last and t == b):
                return _PHASE.get(mode)
        return None

    def _modes(self, mode: str) -> list[list]:
        return [s for s in self.steps if s[0] == mode]

    @property
    def auto(self) -> tuple[float, float]:
        s = self._modes("auto")
        return (s[0][1], s[-1][2]) if s else (INF, INF)

    @property
    def teleop(self) -> tuple[float, float]:
        s = self._modes("teleop")
        end = self.steps[-1][2] if self.steps and (self.closed or self.last_step) else INF
        return (s[0][1] if s else INF, end)


class ScenarioMatch:
    def __init__(self, world, n_steps: int):
        rng = world.test_range
        self.world, self.n_steps = world, n_steps
        self.game = rng.range.game
        self._zone = leave_zone(self.game, rng.range.zones)
        climb = rng.range.climb
        self._levels = list(climb.levels) if climb is not None else []
        if self._levels:
            world.climb_monitor()       # the level held is read from its signals
        self.phases = StepPhases()
        self._inside0: bool | None = None       # the robot's center in the leave zone when the scenario started
        self._inside_auto: bool | None = None   # ... and when the last auto step ended
        self.log = rng.ball_log

    def _inside(self) -> bool | None:
        if self._zone is None:
            return None
        x, y = self.world.robot_pose()[:2]
        return bool(self._zone.contains(x, y))

    def begin(self, mode: str, start: float, duration: float) -> None:
        if self._inside0 is None:
            self._inside0 = self._inside()
        if mode == "auto":
            self._inside_auto = None
        self.phases.steps.append([mode, start, start + duration])
        self.phases.last_step = len(self.phases.steps) == self.n_steps

    def finish(self, t: float) -> None:
        """The running step ended at run time `t`."""
        if not self.phases.steps:
            return
        step = self.phases.steps[-1]
        step[2] = t
        if step[0] == "auto":
            self._inside_auto = self._inside()

    def close(self) -> None:
        self.phases.closed = True

    def _left(self, now: float) -> bool | None:
        if self._zone is None or self._inside0 is None:
            return None
        a1 = self.phases.auto[1]
        inside = self._inside_auto
        # auto's last step is at its end (within a physics step): read it now
        if inside is None and a1 != INF and now >= a1 - self.world.model.opt.timestep:
            inside = self._inside()
        if inside is None:
            return None
        return bool(self._inside0 and not inside)

    def _level(self) -> str | None:
        return self._climb()[0]

    def _climb(self) -> tuple[str | None, float]:
        """The climb level held now (None: none) and the HARMONY points with it (climb.harmony), from one sample."""
        if not self._levels:
            return None, 0.0
        v = self.world.climb_monitor().sample()
        return level_name(self._levels, int(v["climb.level"])), float(v.get("climb.harmony", 0.0))

    def points(self, at: float | None = None) -> Points:
        """The points at run time `at`, or of the whole scenario."""
        now = self.world.time if at is None else at
        if at is not None:          # a read at a step's end can land a physics step short of it: it is at the end
            dt = self.world.model.opt.timestep
            at = next((b for b in (self.phases.auto[1], self.phases.teleop[1]) if b - dt <= at < b), at)
            now = at
        level, harmony = self._climb()
        return score(self.game, self.log(), self.phases, self._left(now), level, at, harmony=harmony)

    def report(self) -> dict:
        """What the report keeps: the points, and the climb level for the "climbed to" line."""
        return {"points": self.points().to_dict(), "level": self._level()}


def points_lines(report: dict) -> list[str]:
    """The Score section's points lines: the Test tab's words (blocks/run_notes.py points_lines)."""
    from .blocks.run_notes import points_lines as lines
    return lines(SimpleNamespace(points=report["points"], match={"level": report["level"]}))
