"""Where a game piece was placed: whether it rests on a field element (or on another piece), and how far a hollow
piece sits down a rod (its seated depth), the `rests_on` and `seated` conditions.

A piece rests on an element when, all at once and for at least REST_S:
- that element carries at least REST_SHARE of its weight: over each contact between the piece and one of the
  element's geoms (`element.<name>` and `element.<name>.*`), the upward (world z) part of the contact force on the
  piece (mj_contactForce, turned out of the contact frame), against its mass times g. `on = "a piece"` (ON_PIECE)
  counts the contacts with every other piece instead (a tote stacked on a tote);
- it is at rest: its speed under balls.AT_REST_SPEED (sub-project 21's rest speed);
- no robot geom touches it.
A piece MuJoCo has put to sleep (it lay still long enough) has no contacts computed; it keeps what it had the step
before it fell asleep, at rest. The contacts are only read when a test uses rests_on (`rests`), since they cost a
pass over the step's contacts.

The seated depth (seated_depth) needs a piece with a hole (PieceKind.hole) and a rod (base B, tip T, unit axis u from
the base, length L). The hole's two ends are E+- = c +- (l / 2) a, with c its center and a the kind's axis, turned as
the piece is. The rod runs through the hole when the hole's axis is within THROUGH_DEG of the rod's (|a . u| >= cos
THROUGH_DEG), the stretch of the rod the hole spans (s(E) = (E - B) . u, between the ends' s, clipped to 0..L) isn't
empty, and at both ends of that stretch the hole's axis is within the effective inner radius of the rod's axis: an
end of the hole on the rod is checked where it is; an end past the rod's tip or base where the tip or base is inside
the hole (so a coral longer than its branch, its low end against the post, is on it). Then the depth is how far the
hole has passed over the rod, the length of the stretch of rod inside it (min(max s, L) - max(min s, 0): from the
tip, L - min s(E+-), for a piece put on over the tip; a piece threaded on from a free base end, or hanging past it,
counts only the rod it covers), clipped to 0..could, where `could` (how far it could go) is the
smaller of L and the hole's length. Otherwise it has no depth (None: a piece lying across a rod, one resting on the
rod's end without the rod in its hole, a solid piece): the `seated.<rod>` signal is empty then (NaN in a run's
signals, null once read back as JSON), and a seated check says the piece "isn't on" the rod. It is measured whenever
the rod runs through the hole, even while the piece is still in a grabber.
"""

from __future__ import annotations

import math

import mujoco
import numpy as np

from .. import units
from ..testrange.balls import AT_REST_SPEED

REST_SHARE = 0.5        # of the piece's weight, carried by the element (spec)
REST_S = 0.5            # s: carried, at rest and untouched by the robot this long (spec)
THROUGH_DEG = 30.0      # the hole's axis within this of the rod's: the rod runs through it (our call)
KNOCK_OFF_SHARE = 1 / 3  # seated less than this share of how far it could go: it could be knocked off (our call)
ON_PIECE = "a piece"    # a rests_on's `on`: any other game piece
IN = 0.0254


def seated_depth(rod, kind, pos: np.ndarray, rot: np.ndarray) -> tuple[float, float] | None:
    """(depth, could go), in meters, of a piece of `kind` at `pos` turned by `rot` (3x3, its body frame in the world)
    on `rod`; None when the rod doesn't run through its hole (or it has none)."""
    r_in = kind.effective_hole_radius()
    if r_in is None:
        return None
    hole = kind.hole
    rot = np.asarray(rot, float).reshape(3, 3)
    a = rot @ np.asarray(kind.axis, float)
    a /= np.linalg.norm(a)
    u, base, length = rod.axis(), np.asarray(rod.base, float), rod.length()
    along = float(a @ u)
    if abs(along) < math.cos(math.radians(THROUGH_DEG)):
        return None
    c = np.asarray(pos, float) + rot @ np.asarray(hole.center, float)
    s_c = float((c - base) @ u)
    half = 0.5 * hole.length * abs(along)               # how far each end is from the center along the rod
    lo, hi = s_c - half, s_c + half
    if hi < 0.0 or lo > length:
        return None
    for s in (max(lo, 0.0), min(hi, length)):           # the stretch of the rod inside the hole, at each end
        p = c + a * ((s - s_c) / along)                 # the hole's axis there
        off = p - base - s * u
        if float(np.linalg.norm(off)) > r_in:
            return None
    could = min(length, hole.length)
    return float(np.clip(min(hi, length) - max(lo, 0.0), 0.0, could)), float(could)


def seated_words(depth: float, could: float) -> str:
    """A seated depth (m) in plain words, in the Units setting: "seated 3.5 in of 4 in" (or "seated 89 mm of
    102 mm"); under KNOCK_OFF_SHARE of how far it could go, "only 0.8 in on: could be knocked off"."""
    if depth < KNOCK_OFF_SHARE * could:
        return f"only {units.fmt(depth, 'length', fewer=2)} on: could be knocked off"
    return f"seated {units.fmt(depth, 'length', fewer=2)} of {units.fmt(could, 'length', fewer=2)}"


class Placed:
    def __init__(self, world, rests: bool = True):
        """`rests`: follow what each piece rests on (a test uses rests_on); seated depths are read on demand."""
        self.world, self.model, self.data = world, world.model, world.data
        m = self.model
        tr = world.test_range
        self.balls = tr.balls if tr is not None else None
        self.kinds = tr.range.kinds if tr is not None else {}
        layout = world.parts.layout if world.parts is not None else None
        self.rods = dict(layout.rods) if layout is not None else {}
        self.rests = rests and self.balls is not None
        root = m.body_rootid[world.robot_body] if world.robot_body is not None else -1
        self._robot = m.body_rootid[m.geom_bodyid] == root     # geom id -> on the robot
        self._element: list[str | None] = []                   # geom id -> the element it belongs to
        for g in range(m.ngeom):
            name = m.geom(g).name
            self._element.append(name.split(".")[1] if name.startswith("element.") else None)
        self._g = float(np.linalg.norm(m.opt.gravity))
        self._f6 = np.zeros(6)
        self._acc: dict[tuple[int, str], float] = {}           # (piece, on) -> s it has rested there, while it does
        self._last: dict[int, tuple[dict[str, float], bool, bool]] = {}   # piece -> (up force by on, robot, moving)
        self._seen_t = math.nan
        self._depths: dict[str, dict[int, tuple[float, float]]] = {}
        self._hollow = [] if self.balls is None else [
            i for i in range(self.balls.n)
            if str(self.balls.kind[i]) in self.kinds and self.kinds[str(self.balls.kind[i])].hole is not None]

    # ---- every physics step -----------------------------------------------------------------------------------------

    def update(self, t: float) -> None:
        """After a physics step at t: what each piece in play rests on (only with `rests`)."""
        if not self.rests:
            return
        m, d, balls = self.model, self.data, self.balls
        up: dict[int, dict[str, float]] = {}
        robot: set[int] = set()
        n = d.ncon
        if n:
            geoms = d.contact.geom[:n]
            owner = balls.ball_of_geom(geoms)
            for k in np.flatnonzero((owner >= 0).any(axis=1)).tolist():
                g1, g2 = int(geoms[k, 0]), int(geoms[k, 1])
                p1, p2 = int(owner[k, 0]), int(owner[k, 1])
                fz = None
                # the force on geom2 is +f, on geom1 -f
                for me, other, other_piece, sign in ((p1, g2, p2, -1.0), (p2, g1, p1, 1.0)):
                    if me < 0:
                        continue
                    if self._robot[other]:
                        robot.add(me)
                        continue
                    on = ON_PIECE if other_piece >= 0 else self._element[other]
                    if on is None:
                        continue
                    if fz is None:
                        mujoco.mj_contactForce(m, d, k, self._f6)
                        fz = float(self._f6[:3] @ d.contact.frame[k].reshape(3, 3)[:, 2])
                    mine = up.setdefault(me, {})
                    mine[on] = mine.get(on, 0.0) + sign * fz
        dt = float(m.opt.timestep)
        asleep = d.tree_asleep
        for i in np.flatnonzero(balls.field).tolist():
            if asleep[m.body_treeid[balls.bodies[i]]] >= 0 and i in self._last:
                forces, touched, moving = self._last[i]     # asleep: as it was, and still
            else:
                moving = float(np.linalg.norm(balls.velocity(i))) >= AT_REST_SPEED
                forces, touched = up.get(i, {}), i in robot
                self._last[i] = (forces, touched, moving)
            need = REST_SHARE * float(m.body_mass[balls.bodies[i]]) * self._g
            for on in set(forces) | {o for j, o in self._acc if j == i}:
                key = (i, on)
                if forces.get(on, 0.0) >= need and not touched and not moving:
                    self._acc[key] = self._acc.get(key, 0.0) + dt
                else:
                    self._acc.pop(key, None)
        for key in [k for k in self._acc if not balls.field[k[0]]]:
            del self._acc[key]

    # ---- reading ----------------------------------------------------------------------------------------------------

    def _pieces(self, piece: int | None) -> list[int]:
        if self.balls is None:
            return []
        return [i for i in (range(self.balls.n) if piece is None else [piece]) if self.balls.field[i]]

    def rests_on(self, piece: int | None, on: str) -> bool:
        """`piece` (None: any piece in play) rests on `on` (an element's name, or ON_PIECE) now."""
        dt = float(self.model.opt.timestep)
        return any(self._acc.get((i, on), 0.0) >= REST_S - 0.5 * dt for i in self._pieces(piece))

    def why_not(self, piece: int | None, on: str) -> str:
        """Why `piece` (None: any) doesn't rest on `on`, when `on` carries enough of its weight: "a robot body still
        touches it", "it is still moving" or "it has rested only 0.2 s"; "" when nothing carries it there (or it
        rests there)."""
        m, balls = self.model, self.balls
        if self.rests_on(piece, on):
            return ""
        best = None                                          # the piece `on` carries the most of
        for i in self._pieces(piece):
            forces, touched, moving = self._last.get(i, ({}, False, False))
            share = forces.get(on, 0.0) / (float(m.body_mass[balls.bodies[i]]) * self._g)
            if share >= REST_SHARE and (best is None or share > best[0]):
                best = (share, i, touched, moving)
        if best is None:
            return ""
        _, i, touched, moving = best
        if touched:
            return "a robot body still touches it"
        if moving:
            return "it is still moving"
        return f"it has rested only {self._acc.get((i, on), 0.0):.1f} s"

    def _read(self) -> dict[str, dict[int, tuple[float, float]]]:
        """Every rod's seated depths now, by piece. Read once per sim time and kept: a piece moved (Balls.place) without
        a physics step after it still reads its old depth."""
        t = float(self.data.time)
        if t != self._seen_t:
            self._seen_t = t
            d, balls = self.data, self.balls
            self._depths = {}
            for name, rod in self.rods.items():
                got = {}
                for i in self._hollow:
                    if not balls.field[i]:
                        continue
                    b = int(balls.bodies[i])
                    r = seated_depth(rod, self.kinds[str(balls.kind[i])], d.xpos[b], d.xmat[b])
                    if r is not None:
                        got[i] = r
                self._depths[name] = got
        return self._depths

    def seated(self, rod: str, piece: int | None = None) -> tuple[float, float] | None:
        """(depth, could go) in meters of `piece` on `rod` now, or the deepest piece's (None: any); None when the rod
        runs through no such piece's hole."""
        got = self._read().get(rod, {})
        if piece is not None:
            return got.get(piece)
        return max(got.values(), default=None)
