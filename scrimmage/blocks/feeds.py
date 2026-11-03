"""One outcome for every fed piece: made it in, placed on the field, stuck in the robot, or missed.

The runner tracks each piece a station feeds (and, later, a floor piece a spread places) and updates the tracker
every physics step. A piece's outcome is where it is when the run ends:

- **in**: its center is inside a hopper body's box (Watch.hopper_mask), the robot's ball path took it in (its
  ledger state is `robot`), or, for a rigid piece (not a ball or disc), a hopper body holds it (grip_watch.py's
  HeldTracker: the Watch has it follow every hopper body). The test's hopper is where a piece should end up, a
  grabber's jaws as much as a bin. A ball keeps the box rule alone (ruling P26): one wedged on a hopper body outside
  its box is stuck, not held.
- **placed**: not in, and on a field element the test names in a `seated` or `rests_on` condition (an expect's or a
  wait until's; the runner passes them as `places`): seated on that rod (any depth: placed.py's Placed.seated) or
  resting on that element (Placed.rests_on), with why the rod's or element's name (controller ruling P33). A test
  that places pieces needs no hopper: a coral seated on a branch is where it should be, not missed.
- **stuck**: it touches a robot body that counts, its center outside every hopper body, and has been at rest for at
  least STUCK_S. At rest on the robot means its speed relative to the robot is below balls.AT_REST_SPEED, where
  v_rel = v_ball - (v_robot + w_robot x (p_ball - p_robot)), so a piece wedged on a moving robot still counts. On a
  stand-in robot (`is_standin`) its own bodies, the stock Chassis and Drivetrain parts' (assembler/library.py
  `group`), don't count: the stand-in isn't the student's robot. A piece resting on a part the student added is
  still stuck. On any other robot every robot body counts, stock chassis included.
- **missed**: everything else, with why: "bounced out" (it was in the hopper once and isn't now), "off the field",
  "still moving when the run ended" (not at rest for STUCK_S yet), "on the floor", or "" (resting somewhere else: on
  the stand-in, on a field element the test doesn't name).

`counts` has fed, in, stuck and missed, and placed when any piece was placed (they add up to fed). `sentence` leads
with the placed pieces when the test has no hopper ("1 of 1 placed on peg"), else adds them after the ones in ("3 of
5 made it in (60%); 2 placed on peg"). `made_it_in` counts the hopper's pieces alone.

After the blocks finish, the runner keeps stepping until every piece has settled (`settled`), up to SETTLE_MAX_S.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..testrange.balls import AT_REST_SPEED, BallState

STUCK_S = 0.5           # s at rest on the robot to be stuck (the spec's number); also how long a piece rests to settle
SETTLE_MAX_S = 3.0      # s the run keeps going after the blocks finish, for the pieces to settle (the spec's number)
ON_FLOOR = 0.01         # m: a piece whose lowest point is within this of the carpet is on the floor
DROPPED_STEPS = 1       # steps a piece at rest keeps the body it rests on with no contact with it (our call)
_OWN_GROUPS = ("Chassis", "Drivetrain")     # the stand-in's own parts: these families of stock parts


def is_standin(config_path) -> bool:
    """The robot config is the stand-in "Test it" builds: its `[robot]` says `standin = true`, or its file name ends
    in `-standin.toml` (what "Test it" has always named it)."""
    p = Path(config_path)
    if p.name.endswith("-standin.toml"):
        return True
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    robot = raw.get("robot")
    return isinstance(robot, dict) and robot.get("standin") is True


def standin_bodies(world) -> set[int]:
    """Body ids of the robot's stock Chassis and Drivetrain parts (every body named `<instance>.<...>`)."""
    from ..assembler.library import group
    from ..parts.stock import REGISTRY
    parts = world.parts
    if parts is None:
        return set()
    names = []
    for inst in parts.instances:
        if inst.source.startswith("stock "):
            stock = REGISTRY.get(inst.source[len("stock "):])
            if stock is not None and group(stock) in _OWN_GROUPS:
                names.append(inst.name)
    m = world.model
    return {b for b in range(m.nbody)
            if any(m.body(b).name == n or m.body(b).name.startswith(n + ".") for n in names)}


@dataclass
class _Piece:
    ball: int
    source: str             # a station's name, or "floor"
    t: float                # when it was fed (s, the run's clock)
    ever_in: bool = False
    now_in: bool = False
    t_in: float = math.nan      # it last went into the hopper
    t_left: float = math.nan    # it last left the hopper
    rest: float = 0.0           # s at rest so far (relative to the robot when it touches the robot)
    rest_since: float = math.nan
    body: int | None = None     # the counting robot body it touches now
    off: int = 0                # steps in a row at rest touching no counting body
    t_gone: float = math.nan    # it left play (off the field, scored, taken in by the ball path)


class Outcomes:
    def __init__(self, world, watch, standin: bool, places: tuple[list[str], list[str]] = ([], [])):
        """`places`: (the rods a `seated` condition names, the elements a `rests_on` condition names)."""
        self.world, self.watch = world, watch
        self._seated_on, self._rests_on = (list(dict.fromkeys(x)) for x in places)
        tr = world.test_range
        self.balls = tr.balls if tr is not None else None
        m = self.model = world.model
        root = m.body_rootid[world.robot_body]
        robot = np.array([m.body_rootid[b] == root for b in range(m.nbody)], bool)
        counts = robot.copy()
        if standin:
            counts[list(standin_bodies(world))] = False
        self._robot_geom = robot[m.geom_bodyid]          # geom id -> on the robot
        self._counting_geom = counts[m.geom_bodyid]      # geom id -> on a robot body that counts
        j = int(m.body_jntadr[world.robot_body])
        self._robot_q = int(m.jnt_qposadr[j]) if j >= 0 else None
        self._robot_v = int(m.jnt_dofadr[j]) if j >= 0 else None
        self._hopper = set(watch.hopper)                 # where a piece should end up: a box, or a hold by one
        self._round = watch.round                        # balls and discs: the box rule only (ruling P26)
        self.pieces: list[_Piece] = []
        self._t: float | None = None

    def track(self, ball: int, source: str, t: float) -> None:
        self.pieces.append(_Piece(int(ball), source, float(t)))

    # ---- every physics step -----------------------------------------------------------------------------------------

    def _robot_motion(self):
        """(p, v, w): the robot body's origin, its velocity and its angular velocity, in the world."""
        d = self.world.data
        if self._robot_q is None:
            return np.zeros(3), np.zeros(3), np.zeros(3)
        q, v = self._robot_q, self._robot_v
        r = d.xmat[self.world.robot_body].reshape(3, 3)
        return d.qpos[q:q + 3].copy(), d.qvel[v:v + 3].copy(), r @ d.qvel[v + 3:v + 6]

    def update(self, t: float) -> None:
        """Read the world after a physics step at time t."""
        dt = 0.0 if self._t is None else t - self._t
        self._t = t
        if not self.pieces:
            return
        b, d = self.balls, self.world.data
        inside = self.watch.inside              # hopper_mask, as the Watch read it after this step
        on_robot: dict[int, bool] = {}          # ball -> it touches the robot
        support: dict[int, int] = {}            # ball -> a counting robot body it touches
        if d.ncon:
            geom = d.contact.geom[:d.ncon]
            ball = b.ball_of_geom(geom)
            for side in (0, 1):
                for c in np.flatnonzero(ball[:, side] >= 0).tolist():
                    other = int(geom[c, 1 - side])
                    i = int(ball[c, side])
                    if self._robot_geom[other]:
                        on_robot[i] = True
                        if self._counting_geom[other] and i not in support:
                            support[i] = int(self.model.geom_bodyid[other])
        p_r, v_r, w_r = self._robot_motion()
        for pc in self.pieces:
            i = pc.ball
            if b.state[i] != BallState.FIELD:
                if math.isnan(pc.t_gone):
                    pc.t_gone = t
                pc.now_in, pc.body = False, None
                continue
            now_in = bool(inside[i])
            if now_in and not pc.now_in:
                pc.t_in = t
            if pc.now_in and not now_in:
                pc.t_left = t
            pc.now_in = now_in
            pc.ever_in |= now_in
            v = b.velocity(i)
            if on_robot.get(i):
                p = b.position(i)
                v = v - (v_r + np.cross(w_r, p - p_r))
            if float(np.linalg.norm(v)) < AT_REST_SPEED:
                if pc.rest == 0.0:
                    pc.rest_since = t - dt          # it came to rest during this step
                pc.rest += dt
                # at rest: the body it rests on stays, though its contact may drop out for a step; off it for longer
                # (slid off slowly onto the floor, say), it rests on no counting body
                if i in support:
                    pc.body, pc.off = support[i], 0
                else:
                    pc.off += 1
                    if pc.off > DROPPED_STEPS:
                        pc.body = None
            else:
                pc.rest, pc.rest_since = 0.0, math.nan
                pc.body, pc.off = support.get(i), 0

    def _settled(self, pc: _Piece) -> bool:
        return not math.isnan(pc.t_gone) or pc.rest >= STUCK_S - 1e-9

    def settled(self) -> bool:
        """Every tracked piece out of play, or at rest (in the hopper or anywhere else) for STUCK_S."""
        return all(self._settled(pc) for pc in self.pieces)

    # ---- outcomes ---------------------------------------------------------------------------------------------------

    def _outcome(self, pc: _Piece) -> tuple[str, str, float]:
        """(outcome, why, when it was decided)."""
        state = self.balls.state[pc.ball]
        end = self._t if self._t is not None else pc.t
        if state == BallState.ROBOT:
            return "in", "", pc.t_gone
        if pc.now_in:
            return "in", "", pc.t_in
        held = (self.watch.held.holders(pc.ball) & self._hopper
                if self._hopper and state == BallState.FIELD and not self._round[pc.ball] else ())
        if held:
            return "in", "", min(self.watch.held.since(pc.ball, by) for by in held)
        on = self._placed_on(pc) if state == BallState.FIELD and (self._seated_on or self._rests_on) else None
        if on is not None:
            return "placed", on, pc.rest_since if not math.isnan(pc.rest_since) else end
        # stuck before bounced out (controller ruling R5): a piece that bounced out of the hopper and came to rest on
        # the student's own part is in the robot, and counts toward a possession limit
        if state == BallState.FIELD and pc.body is not None and pc.rest >= STUCK_S - 1e-9:
            return "stuck", "", pc.rest_since
        if pc.ever_in:
            return "missed", "bounced out", pc.t_left
        if state == BallState.OUT:
            return "missed", "off the field", pc.t_gone
        if state != BallState.FIELD:
            return "missed", "", pc.t_gone
        if pc.rest < STUCK_S - 1e-9:
            return "missed", "still moving when the run ended", end
        if self.balls.clearance(pc.ball) <= ON_FLOOR:
            return "missed", "on the floor", pc.rest_since
        return "missed", "", pc.rest_since

    def _placed_on(self, pc: _Piece) -> str | None:
        """The rod a test's `seated` names that the piece is on, else the element a `rests_on` names that it rests
        on; None."""
        placed = self.watch.placed
        for rod in self._seated_on:
            got = placed.seated(rod, pc.ball)
            if got is not None and got[0] > 0.0:
                return rod
        for on in self._rests_on:
            if placed.rests_on(pc.ball, on):
                return on
        return None

    def so_far(self) -> dict:
        """The counts now: fed, in, stuck, missed (and placed: `counts`)."""
        return counts(self.final())

    def final(self) -> list[dict]:
        """One row per tracked piece, in the order fed: ball, source, t, outcome ("in" | "placed" | "stuck" | "missed"),
        why (a placed piece's: the rod or element), t_outcome, body (the robot body a stuck piece rests on, else None)."""
        m = self.model
        rows = []
        for pc in self.pieces:
            outcome, why, when = self._outcome(pc)
            rows.append({"ball": pc.ball, "source": pc.source, "t": pc.t, "outcome": outcome, "why": why,
                         "t_outcome": None if math.isnan(when) else float(when),
                         "body": m.body(pc.body).name if outcome == "stuck" else None})
        return rows


def made_it_in(c: dict, at_least: float) -> bool:
    """At least `at_least` percent of the pieces fed (counts `c`: fed, in, ...) made it in; false with none fed."""
    return c["fed"] > 0 and 100.0 * c["in"] >= at_least * c["fed"]


def counts(rows: list[dict]) -> dict:
    """fed, in, stuck and missed, and placed when any piece was placed."""
    c = {"fed": len(rows), **{k: sum(r["outcome"] == k for r in rows) for k in ("in", "stuck", "missed")}}
    placed = sum(r["outcome"] == "placed" for r in rows)
    return {**c, "placed": placed} if placed else c


def _placed_words(rows: list[dict]) -> str:
    """Where the placed pieces are: "on peg"; "(2 on branch_l2, 1 on branch_l3)"."""
    on: dict[str, int] = {}
    for r in rows:
        if r["outcome"] == "placed":
            on[r["why"]] = on.get(r["why"], 0) + 1
    if len(on) == 1:
        return f"on {next(iter(on))}"
    return "(" + ", ".join(f"{n} on {where}" for where, n in on.items()) + ")"


def sentence(rows: list[dict], hopper: bool = True) -> str:
    """"17 of 20 made it in (85%); 2 placed on peg; 1 stuck in the robot; 2 missed (1 bounced out, 1 still moving
    when the run ended)"; with no `hopper` (the test names none) and a piece placed, "1 of 1 placed on peg" first;
    "" with no feeds."""
    if not rows:
        return ""
    c = counts(rows)
    placed = c.get("placed", 0)
    if placed and not hopper:
        out = f"{placed} of {c['fed']} placed {_placed_words(rows)}"
        if c["in"]:
            out += f"; {c['in']} made it in"
    else:
        pct = math.floor(100.0 * c["in"] / c["fed"] + 0.5)
        out = f"{c['in']} of {c['fed']} made it in ({pct}%)"
        if placed:
            out += f"; {placed} placed {_placed_words(rows)}"
    if c["stuck"]:
        out += f"; {c['stuck']} stuck in the robot"
    if c["missed"]:
        whys = [f"{n} {why}" for why in ("bounced out", "still moving when the run ended")
                if (n := sum(r["outcome"] == "missed" and r["why"] == why for r in rows))]
        out += f"; {c['missed']} missed" + (f" ({', '.join(whys)})" if whys else "")
    return out
