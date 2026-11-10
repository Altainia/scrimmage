"""The conditions a test's blocks wait for and check (`wait_until`, `repeat until`, `expect`), watched every physics
step, and the plain words that name them in a run's outcome.

- `joint`: a hinge's angle (degrees) or a slide's travel (inches or meters) above or below a number.
- `tilt`: the angle between the robot body's z axis and straight up, in degrees.
- `touch`: a robot part, body or hook (`hook` for any of them) touching a field element, bar, game piece or any
  named field geom or body (a body with all of its geoms and the bodies under it); `next bar` for the bar the robot
  climbs to next, `any bar` for any of the field's bars. It reads the contacts update() recorded
  at the last physics step.
- `piece_in`: a game piece's center inside a robot body's box: the axis-aligned box, in the body's own frame, around
  its geoms' bounding boxes, and the robot carrying it: it hasn't touched the carpet for CARPET_GRACE_S (so a piece
  an open-bottom box was driven over, still on the carpet, isn't in it). `any` for any piece.
- `hanging_from`: the robot hangs from a bar: the hooks hold the robot up from that bar with at least a quarter of
  its weight (its subtree mass times g) for HANG_S. The force counted is, over each contact between a hook geom and
  one of that bar's geoms, the upward (world z) part of the contact force on the hook (mj_contactForce, turned out
  of the contact frame): a push sideways, a hook pressed down onto a bar from below, or a hook's contact with
  anything else (a rail, another bar) holds nothing up from this bar.
- `in_hopper`: how many balls (fed or not) have their center inside a hopper body's box, off the carpet as `piece_in`
  reads it, at least or at most N.
- `station_empty`: a station with a stock has fed all of it.
- `made_it_in`: the share of the pieces fed so far that are in the hopper, at least P percent (the runner's
  `outcomes`; false while nothing has been fed).
- `level`: on a range that scores climbs, the climb monitor's scoring level (`climb.level`) is this one.
- `held`: a game piece (`any` for any piece in play) is held by a robot part or body: off whatever is under it,
  touching the part and moving with it, for HELD_S (grip_watch.py's HeldTracker, `Watch.held`).
- `rests_on`: a game piece rests on a field element (or on another piece, `a piece`): the element carries at least
  half its weight, it is at rest and no robot geom touches it, for REST_S (placed.py's Placed, `Watch.placed`).
- `scored`: at least N balls are scored in a range goal, or in all of them together (`any`), as the range's goal
  counts have them (TestRange.counts()'s "goals").
- `seated`: a hollow game piece is on a rod, its hole passed over the rod's tip by at least `at_least_in` inches (the
  depth read to one decimal, as a student reads it; placed.py's seated_depth).
- `aimed`: a stock turret points within `within_deg` of a goal's center, horizontally (aim.py's aim_error: the
  `[shooter]` launcher's forward axis when it rides on the turret, else the turret's own).
- `cylinder`: a pneumatic cylinder's rod is extended (within CYLINDER_TOL_IN of its stroke) or retracted (within it of
  0), as the air system's extension reads it (pneumatics/runtime.py).
- `pressure`: the air system's stored (the tanks') or working (the regulator's output) pressure is above or below N
  psi.
- `element`: a hinged field element's angle (degrees) or a sliding one's travel (inches), 0 at rest, is above or below
  N: its `element.<name>` signal.
- `clears`: the robot keeps at least `at_least_in` inches from a field element: clearance.py's clearance, between
  every robot geom that collides and the element's geoms, measured after every step (`Watch.closest`) for the
  elements the test names; nothing within 0.5 m counts as clear.
- `partner_lifted`: the partner robot's frame is lifted at least `at_least_in` inches (its `partner.height`: the
  lowest frame corner above where it settled), and, with `level_within_deg`, its tilt is within that of level.
- `in_zone`: the robot's center (its root body's, seen from above) is in one of the range's floor zones (`[[zone]]`,
  testrange/zones.py), or out of it with `is = "out"`: 2024's LEAVE and wing zones.
- `partner_moved`: the partner robot has gone at least `at_least_in` inches across the floor from where it settled
  (`partner.moved`).
- `sensor`: one of the robot's [[sensor]]s is on or off, as the world read it at the last physics step
  (sensors/runtime.py, spec C6): a beam break is on while broken, a distance sensor while closer than its threshold,
  a color sensor while a piece is within its range. With `sees`, a color sensor is on only while the piece it sees
  is nearest that named color (colors.nearest_color); `is = "off"` with `sees` holds while it doesn't see that color
  (nothing in range, or a piece of another color).
- `match`: a test with `match = true` is in this phase (auto, the pause or teleop) of the range's `[game]` now
  (match.py's MatchState, set by the runner).
- `points`: the points scored so far in auto, teleop, the endgame or all of them (`total`) are at least N: the game's
  score on the ball log at this moment (testrange/game.py's score with `at`; LEAVE counts once auto has ended, the
  endgame once the match has ended).

The words name pieces as a student knows them: any piece by the field's kind ("a coral"; "a ball" on a field of one
ball or disc kind, as always; "a piece" on a mixed field or when the kinds aren't known), and a named piece by its
kind and number ("coral 1"; on a field of one ball or disc kind its body name, as always).

"The next bar" is the lowest bar whose top is higher than every bar the robot has hung from so far in this run; the
lowest bar before the robot has hung from any.

The Watch reads the world's ClimbMonitor every step and starts its peak-hold over (consume()), so `climb` holds the
last physics step's climb signals; a runner using a Watch reads them there.
"""

from __future__ import annotations

import math

import mujoco
import numpy as np

from .. import i18n, units
from ..terrain.climb import hold_geoms, robot_hooks
from ..testrange.elements import moving_units, moving_value
from ..testrange.spec import _one_round_kind as one_round_kind
from .aim import aim_error
from .clearance import Closest, named
from .feeds import made_it_in
from .grip_watch import HeldTracker, default_word
from .palette import ANY_BAR, ANY_GOAL, ANY_HOOK, ANY_PIECE, NEXT_BAR
from .placed import Placed

HANG_S = 0.1            # s: the hooks carry a quarter of the robot's weight this long to count as hanging
HANG_SHARE = 0.25       # of the robot's weight
IN = 0.0254
CYLINDER_TOL_IN = 0.25  # in: a cylinder this close to an end of its stroke is extended or retracted
# s: a piece in a body's box counts as in only once it has been off the carpet this long (our call: longer than a
# bounce on the carpet in an open box, shorter than any intake's lift into a bin, which leaves the carpet before the
# piece reaches the bin's box, so a carried piece reads as in when it did before)
CARPET_GRACE_S = 0.1
CARPET_TOL = 0.005      # m: a piece whose lowest point is this close to the carpet is on it (it sinks a little)


class Watch:
    def __init__(self, world, program):
        self.world = world
        m = self.model = world.model
        self.data = world.data
        self.body = world.robot_body
        root = m.body_rootid[self.body]
        self._robot_root = root
        self.weight = float(m.body_subtreemass[self.body]) * float(np.linalg.norm(m.opt.gravity))
        parts = world.parts
        layout = parts.layout if parts is not None else None
        self._holds = hold_geoms(m, layout)                 # hold name -> geom ids
        self.mon = None                                     # the climb signals: on a range with holds, or with hooks
        if program.hooks or self._holds:
            self.mon = world.climb_monitor(list(program.hooks))
        rng = parts.range if parts is not None else None
        self._levels = list(rng.climb.levels) if rng is not None and rng.climb is not None else []
        self.units = moving_units(rng.elements) if rng is not None else {}    # moving element -> "deg" or "in"
        self._moving = {name: layout.moving[name] for name in self.units}     # moving element -> its qpos address
        # the clearance to each element a clears condition or the chart names, after every step
        self.closest = Closest(world, named(program, [e.name for e in rng.elements] if rng is not None else []))
        self._hook_geoms = {name: set(gs) for name, gs in robot_hooks(m, self.body).items()}
        for name in program.hooks:
            self._hook_geoms[name] = set(self._body_geoms(name))
        self._balls = [m.body(n).id for n in layout.ball_names] if layout is not None else []
        self._ball_names = list(layout.ball_names) if layout is not None else []
        tr = world.test_range
        self.kinds = dict(tr.range.kinds) if tr is not None else None     # the field's piece kinds, for the words
        self.piece_names: dict[str, str] = {}               # a piece's body name -> what a student reads ("coral 1")
        if tr is not None and not one_round_kind(tr.range.kinds):
            self.piece_names = {n: tr.balls.piece_name(i) for i, n in enumerate(self._ball_names)}
        self.hopper = list(program.hopper or [])             # where a piece should end up (the test's hopper)
        # (balls,) True for each ball or disc: only a rigid piece counts as in when a hopper body holds it (ruling
        # P26: a ball wedged on a hopper body outside its box is stuck, as always)
        self.round = np.array([tr.range.kinds[str(k)].form != "rigid" for k in tr.balls.kind], bool) \
            if tr is not None and self._balls else np.zeros(len(self._balls), bool)
        self.outcomes = None                                # the runner's piece tracker (made_it_in reads it)
        self.match = None                                   # the runner's MatchState in a match (match, points read it)
        self._sides: dict[str, set[int]] = {}
        self._boxes: dict[str, tuple[int, np.ndarray, np.ndarray]] = {}

        self.tilt = 0.0                                     # deg
        self.climb: dict = {}                               # the last step's climb signals
        self.hanging: set[str] = set()                      # holds the robot hangs from now
        self.hung: list[str] = []                           # holds hung from so far, in order
        self._held = {name: 0.0 for name in self._holds}    # s each hold has carried a quarter of the weight
        self.load = {name: 0.0 for name in self._holds}     # N each hold holds the robot up by, through its hooks
        self._hold_of = {g: name for name, gs in self._holds.items() for g in gs}
        self._all_hooks = set().union(*self._hook_geoms.values()) if self._hook_geoms else set()
        self._f6 = np.zeros(6)
        self._pairs: set[tuple[int, int]] = set()           # (robot geom, field geom) touching at the last update
        self.held = HeldTracker(world, self)                # the pieces held, by each part a held condition names
        for c in _conds(program.blocks, "held"):
            self.held.track(c.args["by"])
        # what the pieces rest on, and their seated depths (contacts followed only for a rests_on condition)
        self.placed = Placed(world, rests=bool(_conds(program.blocks, "rests_on")))
        if not self.round.all():                            # and by each hopper body: a rigid piece one holds is in
            for by in self.hopper:
                self.held.track(by, hopper=True)
        # each piece's last time on the carpet (s on the run's clock), followed only for a test that asks what is in a
        # body or the hopper. Read from where its geoms reach down to, not from contacts: a piece at rest sleeps, and
        # a sleeping body has no contacts
        self._carpet_needed = bool(self.hopper) or bool(_conds(program.blocks, "piece_in")) \
            or bool(_conds(program.blocks, "in_hopper"))
        ball_set = {int(b): k for k, b in enumerate(self._balls)}
        owned = [(ball_set[int(m.geom_bodyid[g])], g) for g in range(m.ngeom) if int(m.geom_bodyid[g]) in ball_set]
        owned.sort()
        self._piece_geoms = np.array([g for _, g in owned], dtype=int)
        self._piece_of_geom = np.array([k for k, _ in owned], dtype=int)
        self._on_carpet_t = np.full(len(self._balls), -np.inf)
        self._see_carpet()
        self.inside = self.hopper_mask()                    # hopper_mask at the last update (feeds.py reads it)
        self._read_tilt()

    # ---- every physics step -----------------------------------------------------------------------------------------

    def _see_carpet(self) -> None:
        """Note the pieces on the carpet now: a geom's bounding box reaching within CARPET_TOL of it (not a parked
        piece, far below: one put in play from the park is off the carpet from the moment it is put)."""
        if not self._carpet_needed or not len(self._piece_geoms):
            return
        m, d, g = self.model, self.data, self._piece_geoms
        rz = d.geom_xmat[g][:, 6:9]                                  # each geom's rotation's world-z row
        aabb = m.geom_aabb[g]
        low = d.geom_xpos[g, 2] + np.einsum("ij,ij->i", rz, aabb[:, :3]) - np.einsum("ij,ij->i", np.abs(rz),
                                                                                       aabb[:, 3:])
        on = np.zeros(len(self._balls), bool)
        on[self._piece_of_geom[(low <= CARPET_TOL) & (low > -1.0)]] = True     # not a parked piece, far below
        self._on_carpet_t[on] = float(d.time)

    def carried(self) -> np.ndarray:
        """(balls,) True for each piece off the carpet for at least CARPET_GRACE_S: one the robot may be carrying.
        Off the carpet is all it asks, on purpose: a piece resting on a field element (a bump, a ramp, a piece staged on
        the AMP) inside a body's box counts as in, as it did before. That is fine: a box around a piece up on an
        element is a robot reaching over it, and what this rule is for is the open-bottom box driven over a piece on
        the carpet (the 2024 dry run's intake).
        All of them in a Watch that doesn't follow the carpet (no piece_in, in_hopper or hopper)."""
        if not self._carpet_needed:
            self._carpet_needed = True          # asked after all (a test calling holds() directly): follow it now
            self._see_carpet()
        return float(self.data.time) - self._on_carpet_t >= CARPET_GRACE_S - 1e-9

    def update(self) -> None:
        """Read the world after a physics step."""
        self._read_tilt()
        self._see_carpet()
        if self.mon is not None:
            self.climb = self.mon.sample()
            self.mon.consume()
        m, d = self.model, self.data
        root, hold_of, hooks = self._robot_root, self._hold_of, self._all_hooks
        pairs: set[tuple[int, int]] = set()
        up = {name: 0.0 for name in self._held}
        bodies = m.geom_bodyid
        for i, (g1, g2) in enumerate(d.contact.geom[:d.ncon].tolist()):
            r1 = m.body_rootid[bodies[g1]] == root
            if r1 == (m.body_rootid[bodies[g2]] == root):
                continue
            mine, other = (g1, g2) if r1 else (g2, g1)
            pairs.add((mine, other))
            hold = hold_of.get(other)
            if hold is None or mine not in hooks:
                continue
            mujoco.mj_contactForce(m, d, i, self._f6)
            fz = float(self._f6[:3] @ d.contact.frame[i].reshape(3, 3)[:, 2])   # world z of the force on geom2
            up[hold] += fz if mine == g2 else -fz        # on the hook: geom1 feels the opposite of geom2
        self._pairs = pairs
        self.load = up
        dt = float(m.opt.timestep)
        need = HANG_SHARE * self.weight
        for name, carried in up.items():
            self._held[name] = self._held[name] + dt if carried >= need else 0.0
            if self._held[name] >= HANG_S - 0.5 * dt:
                self.hanging.add(name)
                if name not in self.hung:
                    self.hung.append(name)
            else:
                self.hanging.discard(name)
        self.inside = self.hopper_mask()
        self.held.update(float(d.time), self.inside | self.round if self.hopper else None)
        self.placed.update(float(d.time))
        self.closest.update(float(d.time))

    def _read_tilt(self) -> None:
        zz = float(self.data.xmat[self.body][8])        # the body's z axis, its world z component
        self.tilt = math.degrees(math.acos(max(-1.0, min(1.0, zz))))

    # ---- the bars ---------------------------------------------------------------------------------------------------

    def _top(self, name: str) -> float:
        """A hold's top now: the highest point of its geoms' bounding boxes."""
        d, m = self.data, self.model
        top = -math.inf
        for g in self._holds[name]:
            r = d.geom_xmat[g].reshape(3, 3)
            c, h = m.geom_aabb[g][:3], m.geom_aabb[g][3:]
            top = max(top, float(d.geom_xpos[g][2] + r[2] @ c + np.abs(r[2]) @ h))
        return top

    def next_bar(self) -> str | None:
        """The lowest bar whose top is above every bar hung from so far; None when there is none."""
        tops = {name: self._top(name) for name in self._holds}
        above = max((tops[n] for n in self.hung), default=-math.inf)
        higher = [n for n in tops if tops[n] > above]
        return min(higher, key=lambda n: tops[n]) if higher else None

    def highest_hung(self) -> str | None:
        """The highest bar (by its top now) the robot has hung from so far; None before it has hung from any."""
        return max(self.hung, key=self._top) if self.hung else None

    def _bar(self, name: str) -> str | None:
        return self.next_bar() if name == NEXT_BAR else name

    # ---- names to geoms ---------------------------------------------------------------------------------------------

    def _body_geoms(self, name: str) -> list[int]:
        m = self.model
        return [g for g in range(m.ngeom) if m.body(m.geom_bodyid[g]).name == name]

    def _robot_side(self, name: str) -> set[int]:
        if name not in self._sides:
            m = self.model
            if name == ANY_HOOK:
                gs = set().union(*self._hook_geoms.values()) if self._hook_geoms else set()
            elif name in self._hook_geoms:
                gs = self._hook_geoms[name]
            else:       # a body, or a part: every body named `<part>.<body>`
                bodies = {b for b in range(m.nbody) if m.body_rootid[b] == self._robot_root
                          and (m.body(b).name == name or m.body(b).name.startswith(name + "."))}
                gs = {g for g in range(m.ngeom) if int(m.geom_bodyid[g]) in bodies}
            self._sides[name] = gs
        return self._sides[name]

    def _ancestor(self, b: int, bodies: set[int]) -> bool:
        """b is one of `bodies` or under one of them."""
        m = self.model
        while b > 0:
            if b in bodies:
                return True
            b = int(m.body_parentid[b])
        return False

    def _field_side(self, name: str) -> set[int]:
        if name not in self._sides:
            m = self.model
            if name == ANY_BAR:
                gs = set().union(*self._holds.values()) if self._holds else set()
            elif name in self._holds:
                gs = set(self._holds[name])
            else:
                el = f"element.{name}"
                named = {b for b in range(1, m.nbody) if m.body(b).name == name}
                under = {b for b in range(1, m.nbody) if self._ancestor(b, named)}
                gs = {g for g in range(m.ngeom)
                      if m.body_rootid[m.geom_bodyid[g]] != self._robot_root
                      and (m.geom(g).name == name or m.geom(g).name == el or m.geom(g).name.startswith(el + ".")
                           or int(m.geom_bodyid[g]) in under)}
            self._sides[name] = gs
        return self._sides[name]

    def _box(self, name: str) -> tuple[int, np.ndarray, np.ndarray]:
        """(body id, lo, hi): the axis-aligned box around the body's geoms' bounding boxes, in the body's frame."""
        if name not in self._boxes:
            m = self.model
            b = m.body(name).id
            lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
            rot = np.zeros(9)
            for g in range(m.ngeom):
                if m.geom_bodyid[g] != b:
                    continue
                mujoco.mju_quat2Mat(rot, m.geom_quat[g])
                r = rot.reshape(3, 3)
                c = m.geom_pos[g] + r @ m.geom_aabb[g][:3]
                h = np.abs(r) @ m.geom_aabb[g][3:]
                lo, hi = np.minimum(lo, c - h), np.maximum(hi, c + h)
            self._boxes[name] = (b, lo, hi)
        return self._boxes[name]

    def hopper_mask(self) -> np.ndarray:
        """(balls,) True for each range ball whose center is inside a hopper body's box now, and off the carpet
        (carried), in the range's ball order."""
        d = self.data
        inside = np.zeros(len(self._balls), bool)
        if not self._balls:
            return inside
        for name in self.hopper:
            b, lo, hi = self._box(name)
            local = (d.xpos[self._balls] - d.xpos[b]) @ d.xmat[b].reshape(3, 3)
            inside |= np.all((local >= lo) & (local <= hi), axis=1)
        return inside & self.carried()

    def piece_index(self, name: str) -> int | None:
        """A condition's piece as a ledger index: None for `any`."""
        return None if name == ANY_PIECE else self._ball_names.index(name)

    def scored_count(self, goal: str) -> int:
        """Balls scored so far in `goal`, or in every goal for `any` (0 on a field without a range)."""
        tr = self.world.test_range
        goals = tr.counts()["goals"] if tr is not None else {}
        return sum(goals.values()) if goal == ANY_GOAL else int(goals.get(goal, 0))

    def points_so_far(self, phase: str) -> float:
        """The match's points now in `phase` ("auto", "teleop", "endgame" or "total"); 0 outside a match."""
        if self.match is None:
            return 0.0
        pts = self.match.points(self.world.test_range.ball_log(), at=float(self.data.time))
        return float(getattr(pts, phase))

    def element_value(self, name: str) -> float:
        """Moving element `name`'s signal now: degrees for a hinged one, inches for a sliding one, 0 at rest."""
        return moving_value(float(self.data.qpos[self._moving[name]]), self.units[name])

    def hopper_count(self) -> int:
        """Balls whose center is inside a hopper body's box now, fed or not."""
        return int(self.hopper_mask().sum())

    # ---- conditions -------------------------------------------------------------------------------------------------

    def holds(self, cond) -> bool:
        a, k = cond.args, cond.kind
        if k == "joint":
            m = self.model
            j = m.joint(a["name"]).id
            q = float(self.data.qpos[m.jnt_qposadr[j]])
            v = math.degrees(q) if a["unit"] == "deg" else q / IN if a["unit"] == "in" else q
            return v > a["above"] if "above" in a else v < a["below"]
        if k == "tilt":
            return self.tilt > a["above"] if "above" in a else self.tilt < a["below"]
        if k == "touch":
            field = self._bar(a["field"])
            if field is None:
                return False
            mine, other = self._robot_side(a["robot"]), self._field_side(field)
            return any(g in mine and o in other for g, o in self._pairs)
        if k == "piece_in":
            b, lo, hi = self._box(a["body"])
            d = self.data
            balls = self._balls if a["piece"] == ANY_PIECE else [self._balls[self._ball_names.index(a["piece"])]]
            if not balls:
                return False
            r = d.xmat[b].reshape(3, 3)
            local = (d.xpos[balls] - d.xpos[b]) @ r          # each center in the body's frame
            carried = self.carried() if a["piece"] == ANY_PIECE else \
                self.carried()[[self._ball_names.index(a["piece"])]]
            return bool(np.any(np.all((local >= lo) & (local <= hi), axis=1) & carried))
        if k == "in_hopper":
            n = self.hopper_count()
            return n >= a["at_least"] if "at_least" in a else n <= a["at_most"]
        if k == "scored":
            return self.scored_count(a["goal"]) >= a["at_least"]
        if k == "aimed":
            return abs(math.degrees(aim_error(self.world, a["turret"], a["at"]))) <= a["within_deg"]
        if k == "cylinder":
            return cylinder_at(self.world, a["cylinder"], a["is"])
        if k == "pressure":
            s = self.world.pneumatics.system
            psi = s.stored_psi if a["side"] == "stored" else s.working_psi
            return psi > a["above"] if "above" in a else psi < a["below"]
        if k == "element":
            v = self.element_value(a["element"])
            return v > a["above"] if "above" in a else v < a["below"]
        if k == "clears":
            return self.closest.now[a["element"]] >= a["at_least_in"] * IN
        if k == "sensor":
            return sensor_on(self.world, a["sensor"], a.get("sees")) == (a["is"] == "on")
        if k == "partner_lifted":
            p = self.world.partner
            level = a.get("level_within_deg")
            return p.height() >= a["at_least_in"] and (level is None or p.tilt() <= level)
        if k == "in_zone":
            rng = self.world.parts.range if self.world.parts is not None else None
            zone = next((z for z in (rng.zones if rng is not None else ()) if z.name == a["zone"]), None)
            x, y = (float(c) for c in self.data.xpos[self.body][:2])
            inside = zone is not None and zone.contains(x, y)
            return inside == (a["is"] == "in")
        if k == "partner_moved":
            return self.world.partner.moved() >= a["at_least_in"]
        if k == "station_empty":
            return bool(self.world.test_range.feeder.empty(a["station"]))
        if k == "made_it_in":
            if self.outcomes is None:
                return False
            return made_it_in(self.outcomes.so_far(), a["at_least"])
        if k == "hanging_from":
            bar = self._bar(a["bar"])
            return bar is not None and bar in self.hanging
        if k == "held":
            return self.held.held(self.piece_index(a["piece"]), a["by"])
        if k == "rests_on":
            return self.placed.rests_on(self.piece_index(a["piece"]), a["on"])
        if k == "seated":
            got = self.placed.seated(a["on"], self.piece_index(a["piece"]))
            return got is not None and round(got[0] / IN, 1) >= a["at_least_in"]
        if k == "level":
            rank = int(self.climb.get("climb.level", 0))
            return 0 < rank <= len(self._levels) and self._levels[rank - 1] == a["level"]
        if k == "match":
            return self.match is not None and self.match.phase == a["phase"]
        if k == "points":
            return self.points_so_far(a["phase"]) >= a["at_least"]
        raise ValueError(f"unknown condition {k!r}")


def cylinder_at(world, name: str, end: str) -> bool:
    """The cylinder's rod is within CYLINDER_TOL_IN of its stroke's end ("extended") or start ("retracted")."""
    x = world.pneumatics.extension(name)
    if end == "extended":
        x = next(c.stroke for c in world.parts.cylinders if c.name == name) - x
    return x <= CYLINDER_TOL_IN * IN


def sensor_on(world, name: str, sees: str | None = None) -> bool:
    """The robot's sensor `name` is on now (with `sees`: on, and the piece it sees nearest that named color)."""
    r = world.sensors.reading(name)
    return r.on and (sees is None or r.color_name == sees)


def _conds(blocks, kind: str) -> list:
    """The blocks' conditions of `kind`, through repeat bodies."""
    out = []
    for b in blocks:
        out += [v for v in b.args.values() if getattr(v, "kind", None) == kind]
        out += _conds(b.body, kind)
    return out


# ---- plain words ----------------------------------------------------------------------------------------------------

# a test file's own units (a block's `unit`, an `_in` key, psi) -> (the kind they are shown as, the SI value of one)
KEY_KINDS = {"in": ("length", units.KEY_UNITS["length"]["in"]), "m": ("length", 1.0),
             "psi": ("pressure", units.LBF / units.IN ** 2)}


def _amount(x: float, unit: str, fewer: int = 0) -> str:
    """`x` in a test file's unit ("deg", "in", "m" or "psi") in words, in the Units setting: "90°", "3 in" or
    "76.2 mm", "60 psi" or "413.7 kPa". A joint's "m" is a robot length (in or mm). `fewer`: units.fmt's.
    Machine text (units.machine_text) is the file's own number and unit, as before the setting: "0.5 m"."""
    if unit == "deg":
        return f"{x:g}°"
    if units.is_machine():
        return f"{x:g} {unit}"
    if unit not in KEY_KINDS:
        return f"{x:g} {unit}"
    kind, si = KEY_KINDS[unit]
    return units.fmt(x * si, kind, fewer=fewer)


def phase_word(phase: str) -> str:
    """A match phase or points phase as a sentence says it (the words the phase marks use)."""
    return {"auto": i18n.t("auto", context="match"), "pause": i18n.t("pause", context="match"),
            "teleop": i18n.t("teleop", context="match"),
            "endgame": i18n.t("endgame", context="match")}[phase]


def a_piece(kinds: dict | None) -> str:
    """Any piece of the field, as a sentence names it: "a coral", "a ball", "a piece"."""
    word = default_word(kinds)
    return i18n.t("an {piece}", piece=word) if word[:1] in "aeiou" else i18n.t("a {piece}", piece=word)


def scores_balls(kinds: dict | None) -> bool:
    """Does a scored check say "balls": with no field known, or a field whose kinds are all balls (as it always
    did). Any other field (a NOTE's, a disc's, a mixed one) says "pieces"."""
    return not kinds or all(getattr(k, "form", "ball") == "ball" for k in kinds.values())


def turret_words(name: str) -> str:
    """A turret as a sentence names it: "the turret" for the instance named turret, else "turret <name>"."""
    return i18n.t("the turret") if name == "turret" else i18n.t("turret {name}", name=name)


def describe(cond, next_bar: str | None = None, kinds: dict | None = None, names: dict | None = None,
             units: dict | None = None, sensors: dict | None = None, form: str = "is") -> str:
    """The condition in plain words, for a run's outcome. `next_bar` names "the next bar" when known; `kinds` (the
    field's Range.kinds) names any piece by its kind, `names` a piece by what a student reads (Watch.piece_names),
    `units` a moving element's unit (Watch.units; a number alone when not known), `sensors` each [[sensor]]'s kind
    by name (a color sensor's check with no color reads "is seeing a piece", as its block does).

    `form` is how the sentence around it takes it, each a whole phrase for a translator: "is" on its own ("arm
    passes 40°"), "without" after "without" ("arm passing 40°") and "waiting" after "waiting for" ("arm to pass
    40°")."""
    a, k = cond.args, cond.kind
    names = names or {}
    t = i18n.t

    def say(alone: str, without: str, waiting: str) -> str:
        return {"is": alone, "without": without, "waiting": waiting}[form]

    def piece(name: str) -> str:
        return a_piece(kinds) if name == ANY_PIECE else names.get(name, name)

    def bar(name: str) -> str:
        return (next_bar or t("the next bar")) if name == NEXT_BAR else name

    def who() -> str:
        return t("a hook") if a["robot"] == ANY_HOOK else a["robot"]

    if k == "touch" and a["field"] == ANY_BAR:
        p = {"part": who()}
        return say(t("{part} touches any bar", **p), t("{part} touching any bar", **p),
                   t("{part} to touch any bar", **p))
    if k == "joint":
        if "above" in a:
            p = {"joint": a["name"], "limit": _amount(a["above"], a["unit"])}
            return say(t("{joint} passes {limit}", **p), t("{joint} passing {limit}", **p),
                       t("{joint} to pass {limit}", **p))
        p = {"joint": a["name"], "limit": _amount(a["below"], a["unit"])}
        return say(t("{joint} goes below {limit}", **p), t("{joint} going below {limit}", **p),
                   t("{joint} to go below {limit}", **p))
    if k == "tilt":
        if "above" in a:
            p = {"limit": _amount(a["above"], "deg")}
            return say(t("the robot tilts more than {limit}", **p), t("the robot tilting more than {limit}", **p),
                       t("the robot to tilt more than {limit}", **p))
        p = {"limit": _amount(a["below"], "deg")}
        return say(t("the robot tilts less than {limit}", **p), t("the robot tilting less than {limit}", **p),
                   t("the robot to tilt less than {limit}", **p))
    if k == "touch":
        p = {"part": who(), "target": bar(a["field"])}
        return say(t("{part} touches {target}", **p), t("{part} touching {target}", **p),
                   t("{part} to touch {target}", **p))
    if k == "piece_in":
        p = {"piece": piece(a["piece"]), "body": a["body"]}
        return say(t("{piece} reaches {body}", **p), t("{piece} reaching {body}", **p),
                   t("{piece} to reach {body}", **p))
    if k == "in_hopper":
        if "at_least" in a:
            p = {"count": a["at_least"]}
            return say(t("{count} or more pieces are in the hopper", **p),
                       t("{count} or more pieces being in the hopper", **p),
                       t("{count} or more pieces to be in the hopper", **p))
        p = {"count": a["at_most"]}
        return say(t("at most {count} pieces are in the hopper", **p),
                   t("at most {count} pieces being in the hopper", **p),
                   t("at most {count} pieces to be in the hopper", **p))
    if k == "scored":
        if not scores_balls(kinds):          # a NOTE field (or any piece that isn't a ball): "pieces"
            if a["goal"] == ANY_GOAL:
                p = {"count": a["at_least"]}
                return say(t("{count} or more pieces are scored in any goal", **p),
                           t("{count} or more pieces being scored in any goal", **p),
                           t("{count} or more pieces to be scored in any goal", **p))
            p = {"count": a["at_least"], "goal": a["goal"]}
            return say(t("{count} or more pieces are scored in {goal}", **p),
                       t("{count} or more pieces being scored in {goal}", **p),
                       t("{count} or more pieces to be scored in {goal}", **p))
        if a["goal"] == ANY_GOAL:
            p = {"count": a["at_least"]}
            return say(t("{count} or more balls are scored in any goal", **p),
                       t("{count} or more balls being scored in any goal", **p),
                       t("{count} or more balls to be scored in any goal", **p))
        p = {"count": a["at_least"], "goal": a["goal"]}
        return say(t("{count} or more balls are scored in {goal}", **p),
                   t("{count} or more balls being scored in {goal}", **p),
                   t("{count} or more balls to be scored in {goal}", **p))
    if k == "aimed":
        p = {"turret": turret_words(a["turret"]), "goal": a["at"], "within": _amount(a["within_deg"], "deg")}
        return say(t("{turret} is aimed at {goal} within {within}", **p),
                   t("{turret} being aimed at {goal} within {within}", **p),
                   t("{turret} to be aimed at {goal} within {within}", **p))
    if k == "cylinder":
        p = {"cylinder": a["cylinder"]}
        if a["is"] == "extended":
            return say(t("{cylinder} is extended", **p), t("{cylinder} being extended", **p),
                       t("{cylinder} to be extended", **p))
        return say(t("{cylinder} is retracted", **p), t("{cylinder} being retracted", **p),
                   t("{cylinder} to be retracted", **p))
    if k == "pressure":
        side, psi = ("above", a["above"]) if "above" in a else ("below", a["below"])
        p = {"limit": _amount(psi, "psi")}      # as the block reads: "stored air pressure above 60 psi"
        if (a["side"], side) == ("stored", "above"):
            return say(t("the stored air pressure is above {limit}", **p),
                       t("the stored air pressure being above {limit}", **p),
                       t("the stored air pressure to be above {limit}", **p))
        if (a["side"], side) == ("stored", "below"):
            return say(t("the stored air pressure is below {limit}", **p),
                       t("the stored air pressure being below {limit}", **p),
                       t("the stored air pressure to be below {limit}", **p))
        if side == "above":
            return say(t("the working air pressure is above {limit}", **p),
                       t("the working air pressure being above {limit}", **p),
                       t("the working air pressure to be above {limit}", **p))
        return say(t("the working air pressure is below {limit}", **p),
                   t("the working air pressure being below {limit}", **p),
                   t("the working air pressure to be below {limit}", **p))
    if k == "element":
        side = "above" if "above" in a else "below"
        unit = (units or {}).get(a["element"])
        p = {"element": a["element"], "limit": _amount(a[side], unit) if unit else f"{a[side]:g}"}
        if side == "above":
            return say(t("{element} is above {limit}", **p), t("{element} being above {limit}", **p),
                       t("{element} to be above {limit}", **p))
        return say(t("{element} is below {limit}", **p), t("{element} being below {limit}", **p),
                   t("{element} to be below {limit}", **p))
    if k == "clears":
        p = {"element": a["element"], "limit": _amount(a["at_least_in"], "in")}
        return say(t("the robot clears {element} by at least {limit}", **p),
                   t("the robot clearing {element} by at least {limit}", **p),
                   t("the robot to clear {element} by at least {limit}", **p))
    if k == "sensor":
        if a.get("sees") is not None or (sensors or {}).get(a["sensor"]) == "color":
            p = {"sensor": a["sensor"], "what": a["sees"] or t("a piece")}
            if a["is"] == "on":
                return say(t("{sensor} is seeing {what}", **p), t("{sensor} seeing {what}", **p),
                           t("{sensor} to see {what}", **p))
            return say(t("{sensor} isn't seeing {what}", **p), t("{sensor} losing sight of {what}", **p),
                       t("{sensor} not to see {what}", **p))
        p = {"sensor": a["sensor"]}
        if a["is"] == "on":
            return say(t("{sensor} is on", **p), t("{sensor} being on", **p), t("{sensor} to be on", **p))
        return say(t("{sensor} is off", **p), t("{sensor} being off", **p), t("{sensor} to be off", **p))
    if k == "partner_lifted":
        level = a.get("level_within_deg")
        lift = _amount(a["at_least_in"], "in")
        if level is not None:
            p = {"lift": lift, "level": _amount(level, "deg")}
            return say(t("the partner is lifted at least {lift} (level within {level})", **p),
                       t("the partner being lifted at least {lift} (level within {level})", **p),
                       t("the partner to be lifted at least {lift} (level within {level})", **p))
        p = {"lift": lift}
        return say(t("the partner is lifted at least {lift}", **p), t("the partner being lifted at least {lift}", **p),
                   t("the partner to be lifted at least {lift}", **p))
    if k == "in_zone":
        p = {"zone": a["zone"]}
        if a["is"] == "in":
            return say(t("the robot is in {zone}", **p), t("the robot being in {zone}", **p),
                       t("the robot to be in {zone}", **p))
        return say(t("the robot is out of {zone}", **p), t("the robot being out of {zone}", **p),
                   t("the robot to be out of {zone}", **p))
    if k == "partner_moved":
        p = {"limit": _amount(a["at_least_in"], "in")}
        return say(t("the partner moved at least {limit}", **p), t("the partner having moved at least {limit}", **p),
                   t("the partner to have moved at least {limit}", **p))
    if k == "match":
        p = {"phase": phase_word(a["phase"])}
        return say(t("the match is in {phase}", **p), t("the match being in {phase}", **p),
                   t("the match to be in {phase}", **p))
    if k == "points":
        if a["phase"] == "total":
            p = {"count": f"{a['at_least']:g}"}
            return say(t("{count} or more points are scored", **p), t("{count} or more points being scored", **p),
                       t("{count} or more points to be scored", **p))
        p = {"count": f"{a['at_least']:g}", "phase": phase_word(a["phase"])}
        return say(t("{count} or more points are scored in {phase}", **p),
                   t("{count} or more points being scored in {phase}", **p),
                   t("{count} or more points to be scored in {phase}", **p))
    if k == "station_empty":
        p = {"station": a["station"]}
        return say(t("{station} is empty", **p), t("{station} being empty", **p), t("{station} to be empty", **p))
    if k == "made_it_in":
        p = {"share": f"{a['at_least']:g}"}
        return say(t("at least {share}% made it in", **p), t("at least {share}% having made it in", **p),
                   t("at least {share}% to have made it in", **p))
    if k == "hanging_from":
        p = {"bar": bar(a["bar"])}
        return say(t("the robot hangs from {bar}", **p), t("the robot hanging from {bar}", **p),
                   t("the robot to hang from {bar}", **p))
    if k == "level":
        p = {"level": a["level"]}
        return say(t("the robot hangs at {level}", **p), t("the robot hanging at {level}", **p),
                   t("the robot to hang at {level}", **p))
    if k == "held":
        p = {"piece": piece(a["piece"]), "by": a["by"]}
        return say(t("{piece} is held by {by}", **p), t("{piece} being held by {by}", **p),
                   t("{piece} to be held by {by}", **p))
    if k == "rests_on":
        p = {"piece": piece(a["piece"]), "place": a["on"]}
        return say(t("{piece} rests on {place}", **p), t("{piece} resting on {place}", **p),
                   t("{piece} to rest on {place}", **p))
    if k == "seated":
        at = a["at_least_in"]
        if at:
            p = {"piece": piece(a["piece"]), "depth": _amount(at, "in"), "place": a["on"]}
            return say(t("{piece} is seated at least {depth} on {place}", **p),
                       t("{piece} being seated at least {depth} on {place}", **p),
                       t("{piece} to be seated at least {depth} on {place}", **p))
        p = {"piece": piece(a["piece"]), "place": a["on"]}
        return say(t("{piece} is seated on {place}", **p), t("{piece} being seated on {place}", **p),
                   t("{piece} to be seated on {place}", **p))
    raise ValueError(f"unknown condition {k!r}")
