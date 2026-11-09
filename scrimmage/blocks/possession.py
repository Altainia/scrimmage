"""The game's rules in a match, checked every step and reported, never enforced (spec D5): how many pieces the robot
controls (possession), how far it reaches past its frame (extension) and how tall it stands (height).

`RuleWatch` is made by the runner only for a match whose game has `[game.rules]`; with none, nothing here runs, the
climb monitor's extent stays off and no HeldTracker is made.

A piece is controlled when the ball path holds it (the range ledger's ROBOT state: the hopper, a stage or the
launcher) or the robot holds it by contact (grip_watch.py's HeldTracker rule: touching a robot part, off the floor,
moving with it). A piece pushed along the floor is neither. A piece wedged in the frame without being lifted is missed.
"""

from __future__ import annotations

import numpy as np

from ..testrange.balls import BallState
from .grip_watch import HeldTracker

POSSESSION_GRACE_S = 3.0    # source: G403, MOMENTARY (more pieces than the limit this long is a breach)
ROBOT = "robot"             # HeldTracker's name for the whole robot


class RuleWatch:
    def __init__(self, world, watch, rules):
        self.world, self.watch, self.rules = world, watch, rules
        self.breaches: dict[str, dict] = {}      # rule -> its record {rule, t, value, limit}, once breached
        self._episode: dict[str, tuple[float, float]] = {}    # rule -> (first over, peak) while over now
        self.held: HeldTracker | None = None
        self.mon = None
        tr = world.test_range
        if rules.possession is not None and tr is not None:
            self.balls = tr.balls
            self.held = HeldTracker(world, watch)
            # the whole robot is one holder: every geom of its bodies
            m = world.model
            self.held._geoms[ROBOT] = {g for g in range(m.ngeom)
                                       if m.body_rootid[m.geom_bodyid[g]] == watch._robot_root}
        if rules.extension is not None or rules.height is not None:
            self.mon = world.climb_monitor()
            self.mon.track_extent()
            if self.mon is not watch.mon:
                self.mon.reset()

    def controlled(self) -> int:
        """The pieces the robot controls now: the ball path's, plus the ones held by contact."""
        n = int(np.count_nonzero(self.balls.state == BallState.ROBOT))
        return n + sum(1 for i in self.held.pieces() if self.balls.field[i])

    def see(self, t: float) -> None:
        """After a physics step (and Watch.update) at run time `t`, in the match."""
        r = self.rules
        if self.held is not None:
            self.held.update(t)
            self._see("possession", t, float(self.controlled()), float(r.possession), POSSESSION_GRACE_S)
        if self.mon is not None:
            v = self.watch.climb if self.mon is self.watch.mon else self._sample()
            if r.extension is not None:
                self._see("extension", t, float(v["climb.extension"]), r.extension, 0.0)
            if r.height is not None:
                self._see("height", t, self.mon.own_height(), r.height, 0.0)

    def _sample(self) -> dict:
        v = self.mon.sample()
        self.mon.consume()
        return v

    def _see(self, rule: str, t: float, value: float, limit: float, grace: float) -> None:
        """One reading. Over `limit` for more than `grace` s is a breach, recorded once at the moment it went over;
        a later reading over it raises the record's value, never lowers it."""
        record = self.breaches.get(rule)
        if value <= limit + 1e-9:
            self._episode.pop(rule, None)
            return
        first, peak = self._episode.get(rule, (t, value))
        peak = max(peak, value)
        self._episode[rule] = (first, peak)
        if record is not None:
            record["value"] = max(record["value"], value)
        elif t - first >= grace - 1e-9:
            self.breaches[rule] = {"rule": rule, "t": first, "value": peak, "limit": limit}

    def result(self) -> list[dict]:
        """The breaches, in the order the rules are named."""
        return [self.breaches[k] for k in ("possession", "extension", "height") if k in self.breaches]
