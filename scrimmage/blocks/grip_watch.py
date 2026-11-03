"""The `held` condition: whether a robot part or body holds a game piece, watched every physics step.

A piece is held by a part (or a body: every body named `<part>.<body>`, as `touch` reads them) when, all at once and
for at least HELD_S:
- its lowest point is at least HELD_CLEAR above anything that isn't the robot: RAY_CASTS rays cast straight down
  from around its center (skipping the robot's geoms and the piece's own) find what is under it;
- it touches the part (a contact the Watch recorded at the last physics step);
- its speed relative to the part is under HELD_SPEED: how fast its center moves in the touching body's own frame,
  from that position at the last update and now (both from the same forward pass as the contacts, so a whip of the
  arm doesn't read as a slip the way mixing the step's start and end would), the slowest of the bodies it touches. A
  body it has just started touching has no speed yet: a step where no touched body has an earlier sample (the piece
  handed cleanly from one jaw to the other) is no news, and the hold stays as it was.

A held piece may miss its contact for one step (a contact MuJoCo drops for a step); the hold ends when it misses two
in a row, at the first.

The tracker follows every piece in play that touches a part a condition names, so `any` (None) costs no more than a
named piece; and every hopper body, which counts a rigid piece it holds as in (feeds.py), skipping the balls, discs
and pieces in a hopper box. When a held piece stops being held it keeps when, how and what kind it was (`lost`):
- "was set down on <name>": its lowest point came within HELD_CLEAR of something that isn't the robot (named by the
  ray that found it: a field element, a piece, the carpet);
- "slipped out of": it stopped touching the part, or it moves relative to it mostly downward (falling out of the
  grip; our call: a downward part of at least half its relative speed);
- "slid in": still touching and off the floor, it moves relative to the part some other way (turning or sliding in
  the jaws).
The runner names what was commanded in the SLIP_LOOKBACK_S before that moment (run.py).
"""

from __future__ import annotations

import math

import mujoco
import numpy as np

from ..testrange.spec import _one_round_kind as one_round_kind

HELD_CLEAR = 0.01       # m: the piece's lowest point above anything that isn't the robot (spec)
HELD_SPEED = 0.05       # m/s: its speed relative to the part (spec)
HELD_S = 0.1            # s: all three this long, the hold time the climb check uses (spec)
SLIP_LOOKBACK_S = 1.0   # s: a joint or drive commanded this long before a slip was moving (spec: "the second before")
RAY_CASTS = 4           # rays down from around the piece's center, at half its bounding radius (our call)
_RAY_HOPS = 8           # robot geoms a ray may pass through before it gives up
_DOWN = np.array([0.0, 0.0, -1.0])
SET_DOWN = "was set down on "    # a loss's words before what the piece was set down on (run.py reads it)
_UNTOUCHED, _NO_NEWS, _OUT_OF_PLAY = "untouched", "no news", "out of play"     # _judge's other answers


def kind_word(kinds: dict | None, name: str | None) -> str:
    """A kind as a student reads it: "ball" on a field of one ball or disc kind (as always), else its plain name
    ("coral"); "piece" when the kind isn't known."""
    if kinds and one_round_kind(kinds):
        return "ball"
    if kinds and name in kinds:
        return kinds[name].label
    return "piece"


def default_word(kinds: dict | None) -> str:
    """The word for any piece of the field: its kind's on a field of one kind, "piece" on a mixed field."""
    return kind_word(kinds, next(iter(kinds))) if kinds and len(kinds) == 1 else "piece"


class HeldTracker:
    def __init__(self, world, watch):
        self.world, self.watch = world, watch
        self.model, self.data = world.model, world.data
        tr = world.test_range
        self.balls = tr.balls if tr is not None else None
        self.kinds = tr.range.kinds if tr is not None else {}
        self._geoms: dict[str, set[int]] = {}           # by -> its robot geoms
        self._robot = self.model.body_rootid == watch._robot_root   # body id -> part of the robot
        self._acc: dict[tuple[int, str], float] = {}    # (piece, by) -> s all three have held, while they do
        self._held: set[tuple[int, str]] = set()
        self._since: dict[tuple[int, str], float] = {}  # (piece, by) held now -> when that hold began
        self._ever: set[tuple[int, str]] = set()
        self._lost: dict[tuple[int, str], tuple[float, str, str]] = {}
        self._local: dict[tuple[int, int], tuple[float, np.ndarray]] = {}   # (piece, body) -> (t, center in body)
        self._t = 0.0
        self._prev: dict[tuple[int, int], tuple[float, np.ndarray]] = {}
        self._hit = np.zeros(1, dtype=np.int32)
        self._missed: dict[tuple[int, str], float] = {}   # held, not touched at the last update: since when
        self.lost_now: float | None = None                 # when a hold lost at the last update was lost
        self._boxed: set[str] = set()       # tracked only as a hopper body: pieces inside a hopper box are skipped

    def track(self, by: str, hopper: bool = False) -> None:
        """Watch `by` (a part, body or hook name) from the next update on. `hopper`: only because it is a hopper body
        (feeds.py's Outcomes asks whether one holds a piece). A piece inside a hopper body's box is in already, so
        such a name skips it (a bin full of balls would cost rays for every one, every step): a piece that leaves the
        box while touching it is held HELD_S later. A held condition naming it watches every piece."""
        if by not in self._geoms:
            self._geoms[by] = set(self.watch._robot_side(by))
            if hopper:
                self._boxed.add(by)
        elif not hopper:
            self._boxed.discard(by)

    # ---- every physics step -----------------------------------------------------------------------------------------

    def update(self, t: float, boxed=None) -> None:
        """After a physics step at t (after Watch.update recorded its contacts). `boxed`: (balls,) True for each piece
        a hopper-only name skips now (inside a hopper body's box, or a ball or disc), or None."""
        self.lost_now: float | None = None
        if not self._geoms or self.balls is None:
            return
        balls, m = self.balls, self.model
        touching: dict[str, dict[int, set[int]]] = {by: {} for by in self._geoms}
        for g, o in self.watch._pairs:
            i = int(balls.ball_of_geom(o))
            if i < 0 or not balls.field[i]:
                continue
            for by, gs in self._geoms.items():
                if g in gs:
                    touching[by].setdefault(i, set()).add(int(m.geom_bodyid[g]))
        dt = float(m.opt.timestep)
        self._t = t
        seen = {(i, b) for now in touching.values() for i, bs in now.items() for b in bs}
        # each (piece, body)'s last sample, kept over one step without it, so a dropped contact step still has one
        self._prev = self._local
        self._local = {k: v for k, v in self._local.items() if t - v[0] <= 2.5 * dt}
        self._local.update({k: (t, self._in_body(*k)) for k in seen})
        below: dict[int, tuple[str, float]] = {}            # each piece's _below, once this update
        for by in self._geoms:
            now = touching[by]
            skip = boxed is not None and by in self._boxed
            for i in set(now) | {i for i, b in self._acc if b == by}:
                key = (i, by)
                if skip and boxed[i]:                       # in the box: no hold to follow, and none lost
                    self._acc.pop(key, None)
                    self._held.discard(key)
                    self._since.pop(key, None)
                    self._missed.pop(key, None)
                    continue
                why = self._judge(i, now.get(i), below) if balls.field[i] else _OUT_OF_PLAY
                if why is _NO_NEWS:                         # no body it touches has a sample from before
                    continue
                if why is _UNTOUCHED and key in self._held and key not in self._missed:
                    self._missed[key] = t                   # one dropped contact step: the hold goes on
                    continue
                first = self._missed.pop(key, t)
                if why is None:
                    self._acc[key] = self._acc.get(key, 0.0) + dt
                    if self._acc[key] >= HELD_S - 0.5 * dt and key not in self._held:
                        self._held.add(key)
                        self._since[key] = t
                        self._ever.add(key)
                    continue
                self._acc.pop(key, None)
                if key in self._held:
                    self._held.discard(key)
                    self._since.pop(key, None)
                    how = "slipped out of" if why in (_UNTOUCHED, _OUT_OF_PLAY) else why
                    self._lost[key] = (first, how, kind_word(self.kinds, str(balls.kind[i])))
                    self.lost_now = first

    def _judge(self, i: int, bodies: set[int] | None, below: dict) -> str | None:
        """None when piece i meets all three now; _UNTOUCHED when it touches none of the part; _NO_NEWS when no body
        it touches has an earlier sample (it was just handed from one to another); else how it fails, in the words
        `lost` gives."""
        if i not in below:
            below[i] = self._below(i)
        under, gap = below[i]
        if gap < HELD_CLEAR:
            return f"{SET_DOWN}{under}"
        if not bodies:
            return _UNTOUCHED
        rels = [v for v in (self._relative(i, b) for b in bodies) if v is not None]
        if not rels:
            return _NO_NEWS
        rel = min(rels, key=lambda v: float(v @ v))
        speed = float(np.linalg.norm(rel))
        if speed < HELD_SPEED:
            return None
        return "slipped out of" if -float(rel[2]) >= 0.5 * speed else "slid in"

    def _in_body(self, i: int, body: int) -> np.ndarray:
        """Piece i's center in `body`'s frame."""
        d = self.data
        return d.xmat[body].reshape(3, 3).T @ (d.xpos[self.balls.bodies[i]] - d.xpos[body])

    def _relative(self, i: int, body: int) -> np.ndarray | None:
        """Piece i's center's velocity relative to `body`, in the world's axes; None without an earlier sample (a
        body it hasn't touched in the last two updates)."""
        prev = self._prev.get((i, body))
        if prev is None or self._t <= prev[0]:
            return None
        now = self._local[(i, body)][1]
        return self.data.xmat[body].reshape(3, 3) @ (now - prev[1]) / (self._t - prev[0])

    def _below(self, i: int) -> tuple[str, float]:
        """(what is under piece i, how far its lowest point is above that): the highest hit of RAY_CASTS rays cast
        down from around its center, past the robot and the piece itself. Nothing under it: ("", inf)."""
        balls = self.balls
        c = np.array(balls.position(i), dtype=float)
        lowest = balls.clearance(i)                       # the lowest point's height above the carpet's z = 0
        r = 0.5 * float(balls.radius[i])
        top, name = -math.inf, ""
        for k in range(RAY_CASTS):
            a = 2.0 * math.pi * k / RAY_CASTS
            z, g = self._ray(c + np.array([r * math.cos(a), r * math.sin(a), 0.0]), int(balls.bodies[i]))
            if g >= 0 and z > top:
                top, name = z, self._name(g)
        return name, lowest - top

    def _ray(self, start: np.ndarray, piece_body: int) -> tuple[float, int]:
        """(height, geom) of the first thing that isn't the robot straight down from `start` (a geom that touches
        nothing, only drawn, doesn't count); (-inf, -1) for none."""
        m, d = self.model, self.data
        p = start.copy()
        for _ in range(_RAY_HOPS):
            dist = mujoco.mj_ray(m, d, p, _DOWN, None, 1, piece_body, self._hit)
            g = int(self._hit[0])
            if dist < 0 or g < 0:
                return -math.inf, -1
            p[2] -= dist
            if not self._robot[m.geom_bodyid[g]] and (m.geom_contype[g] or m.geom_conaffinity[g]):
                return float(p[2]), g
            p[2] -= 1e-6                    # through a robot geom, or one only drawn, and on down
        return -math.inf, -1

    def _name(self, g: int) -> str:
        """A geom under a piece, as a student knows it."""
        m = self.model
        j = int(self.balls.ball_of_geom(g))
        if j >= 0:
            return self.watch.piece_names.get(self.watch._ball_names[j], self.watch._ball_names[j])
        name = m.geom(g).name
        if name.startswith("element."):
            return name.split(".")[1]
        b = int(m.geom_bodyid[g])
        if b == 0:
            return "the carpet" if not name or name in ("floor", "carpet", "ground") else name
        return name or m.body(b).name

    # ---- reading ----------------------------------------------------------------------------------------------------

    def _keys(self, piece: int | None, by: str, of) -> list:
        return [k for k in of if k[1] == by and (piece is None or k[0] == piece)]

    def held(self, piece: int | None, by: str) -> bool:
        """`piece` (None: any piece in play) is held by `by` now."""
        self.track(by)
        return bool(self._keys(piece, by, self._held))

    def ever(self, piece: int | None, by: str) -> bool:
        """It has been held by `by` at some time in this run."""
        return bool(self._keys(piece, by, self._ever))

    def lost(self, piece: int | None, by: str) -> tuple[float, str, str] | None:
        """(when, how, kind word) it last stopped being held by `by` (any piece: the latest); None if it never did."""
        losses = [self._lost[k] for k in self._keys(piece, by, self._lost)]
        return max(losses) if losses else None

    def lost_piece(self, piece: int | None, by: str) -> int | None:
        """The piece (its index) whose loss lost() gives; None if none was lost."""
        keys = self._keys(piece, by, self._lost)
        return max(keys, key=lambda k: self._lost[k])[0] if keys else None

    def holders(self, piece: int) -> set[str]:
        """The tracked parts and bodies holding `piece` now."""
        return {by for i, by in self._held if i == piece}

    def since(self, piece: int, by: str) -> float:
        """When `by`'s hold on `piece` began (it has held it for HELD_S by then); nan when it doesn't hold it now."""
        return self._since.get((piece, by), math.nan)
