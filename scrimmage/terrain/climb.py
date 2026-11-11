"""The climb signals, read every physics step: is the robot hanging, what does it score, how far does it swing, and
how hard do its hooks work?

What the robot can hang from (its holds) is the range's bars (each bar's pipe, RangeLayout.bars), switches (each
switch's bar, `element.<name>.bar`) and chains (each chain's flex, RangeLayout.chains). Contacts are read by contact
id (flexcontact.contact_ids), so a chain's flex counts as its hold. The ground is the carpet and the ground elements,
the geoms in ELEMENT_GROUP, as the TerrainMonitor's; a truss (a box that isn't ground) is neither.

- `climb.hanging`: 1 while some robot geom touches a hold and none touches the ground.
- `climb.level`: the scoring level's rank in the range's `[climb] levels` (1 for the first, lowest, level), and
  `climb.points` its points, by the `[climb] rule`; both 0 while not hanging or with no rule:
  - "lowest-rung": the lowest level among the bars the robot touches. `[climb] levels` lists them lowest first,
    each worth more than the one before (elements.parse_climb checks it), so lowest is both first listed and fewest
    points.
  - "hang": while the robot touches the rule's switch, the first level (its points), plus `level_bonus` while the
    switch is within its `level_deg` of level.
  - "off-carpet" (2024): the highest level among the holds the robot touches (a robot on two leveled holds scores
    the higher, R8).
  - "lowest-point" (2013): one level above the highest level whose bars are all the way below the robot: 1 plus the
    largest rank among the leveled bars whose top (pipe axis plus radius, where it is now) is below the robot's
    lowest point, capped at the last level; 1 when none is (held off the carpet below the first rung). Chains don't
    count here. A bar's level is the boundary above it: the 2013 PYRAMID's 30 in rungs are tagged LEVEL1, and a
    robot above them is LEVEL2.
    The robot's lowest point is the lowest of its colliding geoms' bounding boxes (MuJoCo's geom_aabb, turned with
    the geom). The manual's sequential-climb rule isn't checked.
- `climb.hold`: while hanging, which hold the robot hangs from (the swing's bar, below), as its number: its rank among
  the range's holds (bars, switches and chains) in element order, 1 for the first; 0 while not hanging (R7).
- `climb.hold.<hold>.robots`: how many robots are on that hold and off the carpet: the robot while it hangs touching
  the hold, plus a partner hanging on it ([partner] hang, parts/partner.py) while none of the partner's geoms touches
  the ground. The partner's contacts are read only when it hangs; a standing partner is never on a hold.
- `climb.harmony`: while hanging, the range's `[climb] harmony` (the "off-carpet" rule's) times the robots on the
  robot's hold (climb.hold's) less one; 0 otherwise (R8).
- `climb.swing` (deg): while hanging, the angle of the robot's CoM (its subtree COM) from straight under the axis of
  the swing's bar, in the plane across that axis; 0 otherwise. Positive when the CoM is on the side of axis x up from
  the axis, where axis is the pipe's own z (a bar's local x): for a bar at yaw 90 (along +y), toward +x. The swing's
  bar is the hold a hook touches, the one a hook touched most recently first when hooks touch two (a traversal's
  hand-over); a hold that only a hook brushed stops being it as soon as no hook touches it. With no hook touching a
  hold (a hand-written robot, or a moment's bounce), it is the previous swing's bar if the robot still touches it,
  else any hold it touches. A chain's axis is its horizontal end-to-end line (R6), through the mean of the geoms of
  the hooks touching it, or with none through its lowest vertex.
- `climb.hook.<inst>.hook<i>.load` (N): the contact force on one whole hook, every geom named `<inst>.hook<i>.<piece>`
  (stem, crown, tip and latch): each contact's world-frame force (mj_contactForce, turned out of the contact frame),
  its sign flipped when the hook's geom is the contact's geom2, summed over the hook's contacts, then its norm.
  `climb.hook_load` is the largest hook's.
- `climb.extension` (m): how far the robot reaches beyond its frame perimeter, horizontally in the chassis's own
  x-y plane (so a tilted robot doesn't read its tilt as reach): the largest distance from the frame's outline to any
  colliding geom's bounding box, among the geoms not on the chassis body itself (its frame and bumpers). The frame's
  outline is the outline of the largest box geom on the robot's body (the chassis), in that body's x-y plane: the
  stock chassis's frame plate. A robot body without a box geom (a hand-written model) uses the outline of all its
  colliding geoms' bounding boxes. G107 (2022) caps it at 16 in.
- `climb.height` (m): the robot's top above the carpet, the highest of its colliding geoms' bounding boxes. G106
  (2022) caps it at 66 in in the HANGAR ZONE.

Only colliding geoms count: a stock mechanism built collide = false (an elevator, an arm) is neither reach nor height
here, the same limit as its passing through a bar. Extension and height cost the most to read (every geom's box, every
step), so the monitor reads them only once a scenario asks for one (track_extent()); until then they read 0.

The World makes a ClimbMonitor on a range with a bar, a chain or a switch, or when a scenario resolves a climb
signal; any other world has none and pays nothing.

Scenario checks tick every lockstep step, several physics steps apart, so `sample()` peak-holds since the last
`consume()`: the largest hook loads, extension and height, and the swing of largest magnitude (sign kept). hanging,
level, points, hold, harmony and each hold's robots are the last physics step's: what the robot scores at the
check.
"""

from __future__ import annotations

import math
import re

import mujoco
import numpy as np

from .. import i18n
from ..flexcontact import contact_ids, flex_id, n_ids
from ..parts.field import ELEMENT_GROUP

CLIMB_SIGNALS = ("hanging", "level", "points", "hold", "harmony", "swing", "hook_load", "extension", "height")
EXTENT_SIGNALS = ("extension", "height")      # these need the robot's geoms' extent every step
HOOK = re.compile(r"^(.+\.hook\d+)\.[^.]+$")  # `<inst>.hook<i>.<piece>`: the stock climber's hook geoms
BRACKET = re.compile(r"^.+\.bracket$")  # `<inst>.bracket`: the plate carrying a climber's hooks


def hold_geoms(model: mujoco.MjModel, layout) -> dict[str, list[int]]:
    """The range's holds, as {element name: contact ids}: each bar's pipe, each chain's flex (flexcontact.flex_id) and
    each switch's bar."""
    if layout is None:
        return {}
    chains = getattr(layout, "chains", {})
    out = {name: [flex_id(model, chains[name].flex)] if name in chains else [model.geom(g).id for g in geoms]
           for name, geoms in layout.bars.items()}
    out.update({name: [model.geom(f"element.{name}.bar").id] for name in layout.switches})
    return out


def robot_hooks(model: mujoco.MjModel, robot_body: int) -> dict[str, list[int]]:
    """The robot's hooks, as {`<inst>.hook<i>`: geom ids}, by name."""
    root = model.body_rootid[robot_body]
    out: dict[str, list[int]] = {}
    for g in range(model.ngeom):
        match = HOOK.match(model.geom(g).name)
        if match and model.body_rootid[model.geom_bodyid[g]] == root:
            out.setdefault(match.group(1), []).append(g)
    return dict(sorted(out.items()))


class ClimbMonitor:
    def __init__(self, world, hook_bodies: list[str] | None = None):
        m = world.model
        self.world, self.model, self.data = world, m, world.data
        self.body = world.robot_body
        root = m.body_rootid[self.body]
        robot = m.body_rootid[m.geom_bodyid] == root
        flexes = [False] * (n_ids(m) - m.ngeom)     # per contact id: a flex is neither the robot nor the ground
        self._robot_l = robot.tolist() + flexes
        self._ground_l = ((m.geom_group == ELEMENT_GROUP) & ~robot).tolist() + flexes
        parts = world.parts
        layout = parts.layout if parts is not None else None
        rng = parts.range if parts is not None else None
        self.rule = rng.climb if rng is not None else None
        levels = list(self.rule.levels.values()) if self.rule is not None else []
        ranks = {name: i + 1 for i, name in enumerate(self.rule.levels)} if self.rule is not None else {}
        elements = {e.name: e for e in (rng.elements if rng is not None else ())}

        # The holds: (name, contact ids, level rank (0 for none), is a switch). _hold_l: per contact id, its hold's
        # index or -1. _chains: hold index -> (flex id, the end-to-end axis), for the swing (R6).
        self.holds = []
        self._hold_l = [-1] * n_ids(m)
        self._chains: dict[int, tuple[int, np.ndarray]] = {}
        for name, geoms in hold_geoms(m, layout).items():
            level = getattr(elements.get(name), "level", None)
            self.holds.append((name, geoms, ranks.get(level, 0), name in layout.switches))
            for g in geoms:
                self._hold_l[g] = len(self.holds) - 1
            if name in getattr(layout, "chains", {}):
                c = layout.chains[name]
                a, b = c.ends
                self._chains[len(self.holds) - 1] = (c.flex, (b - a) / float(np.linalg.norm(b - a)))
        self._levels = levels
        # climb.hold's numbers (R7): each hold's rank among the holds in the range's element order, 1 and up.
        order = [e.name for e in (rng.elements if rng is not None else ()) if e.name in {h[0] for h in self.holds}]
        self._number = [order.index(h[0]) + 1 if h[0] in order else k + 1 for k, h in enumerate(self.holds)]
        self._robots_keys = [f"climb.hold.{h[0]}.robots" for h in self.holds]
        self._last_keys = ("climb.hanging", "climb.level", "climb.points", "climb.hold", "climb.harmony",
                           *self._robots_keys)      # the last step's: what scores at the check
        self.harmony = self.rule.harmony if self.rule is not None else 0.0
        # A partner hanging on a hold ([partner] hang): (its hold's index, per contact id: the partner's geom). A
        # standing partner (or none) is None, and its contacts are never read.
        self._partner: tuple[int, list[bool]] | None = None
        info = parts.partner if parts is not None else None
        hang = getattr(info, "hang", None)
        if hang is not None:
            k = next(i for i, h in enumerate(self.holds) if h[0] == hang.hold)
            proot = m.body_rootid[m.body(info.body).id]
            mine = (m.body_rootid[m.geom_bodyid] == proot).tolist() + flexes
            self._partner = (k, mine)
            self._ground_l = [g and not p for g, p in zip(self._ground_l, mine)]
        self._switch = None             # "hang": (hold index, qpos address, level_deg)
        if self.rule is not None and self.rule.rule == "hang":
            k = next(i for i, h in enumerate(self.holds) if h[0] == self.rule.switch)
            self._switch = (k, layout.switches[self.rule.switch], math.radians(elements[self.rule.switch].level_deg))

        hooks = robot_hooks(m, self.body)
        self.hook_bodies = list(hook_bodies) if hook_bodies else []
        for name in self.hook_bodies:       # a body of the robot counts as a hook: all its colliding geoms
            bodies = [b for b in range(m.nbody) if m.body(b).name == name and m.body_rootid[b] == root]
            if not bodies:
                names = [m.body(b).name for b in range(m.nbody) if m.body_rootid[b] == root and m.body(b).name]
                raise ValueError(i18n.t("hook body '{name}' isn't a body of the robot; its bodies: {names}",
                                        name=name, names=', '.join(names)))
            hooks[name] = [g for g in range(m.ngeom) if m.geom_bodyid[g] == bodies[0]
                           and (m.geom_contype[g] or m.geom_conaffinity[g])]
        self.hooks: list[str] = list(hooks)
        self._hook_geoms = [np.array(g, dtype=np.int64) for g in hooks.values()]
        self._hook_l = [-1] * n_ids(m)
        for i, geoms in enumerate(hooks.values()):
            for g in geoms:
                self._hook_l[g] = i
        # The hook assembly: the hooks and the bracket carrying them, which are meant to touch bars.
        self._assembly = {g for geoms in hooks.values() for g in geoms}
        self._assembly |= {g for g in range(m.ngeom) if BRACKET.match(m.geom(g).name)
                           and m.body_rootid[m.geom_bodyid[g]] == root}
        self._f6 = np.zeros(6)
        self._hook_keys = [f"climb.hook.{name}.load" for name in self.hooks]
        self._max_keys = [*self._hook_keys, "climb.hook_load", "climb.extension", "climb.height"]    # peak-held

        # The robot's colliding geoms, those off the chassis body (which count for the extension) first, and the
        # corners of their bounding boxes in their own frames.
        colliding = [g for g in range(m.ngeom) if robot[g] and (m.geom_contype[g] or m.geom_conaffinity[g])]
        reach = [g for g in colliding if m.geom_bodyid[g] != self.body]
        self._geoms = np.array(reach + [g for g in colliding if g not in reach], dtype=np.int64)
        self._reach = len(reach)        # the first _reach of _geoms count for the extension
        signs = np.array(list(np.ndindex(2, 2, 2)), dtype=float) * 2.0 - 1.0          # (8, 3)
        self._corners = m.geom_aabb[self._geoms, None, :3] + signs * m.geom_aabb[self._geoms, None, 3:]  # (n, 8, 3)
        x0, x1, y0, y1 = self._frame_outline()
        self._frame_lo, self._frame_hi = np.array([x0, y0]), np.array([x1, y1])
        # Extension and height need the extent every step: on once a scenario asks for one (track_extent).
        self.extent = False
        self.reset()

    def _frame_outline(self) -> tuple[float, float, float, float]:
        """(x min, x max, y min, y max) of the frame perimeter in the robot body's frame."""
        m = self.model
        own = [g for g in range(m.ngeom)
               if m.geom_bodyid[g] == self.body and (m.geom_contype[g] or m.geom_conaffinity[g])]
        boxes = [g for g in own if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_BOX]
        if boxes:
            own = [max(boxes, key=lambda g: float(m.geom_size[g][0] * m.geom_size[g][1]))]
        if not own:
            return (0.0, 0.0, 0.0, 0.0)
        lo, hi = np.full(2, np.inf), np.full(2, -np.inf)
        rot = np.zeros(9)
        for g in own:
            mujoco.mju_quat2Mat(rot, m.geom_quat[g])
            r = rot.reshape(3, 3)
            center = m.geom_pos[g] + r @ m.geom_aabb[g][:3]
            half = np.abs(r) @ m.geom_aabb[g][3:]
            lo = np.minimum(lo, (center - half)[:2])
            hi = np.maximum(hi, (center + half)[:2])
        return (float(lo[0]), float(hi[0]), float(lo[1]), float(hi[1]))

    def track_extent(self) -> None:
        """Read the extension and height every step from now on."""
        self.extent = True

    def reset(self) -> None:
        """Clear the swing's bar and the peak-hold (the world was reset)."""
        self._last: int | None = None       # the swing's bar on the last step
        self._hooked_before: set[int] = set()   # the holds a hook touched on the last step
        self._hooked_at: dict[int, int] = {}    # hold -> when (a step count) a hook last started touching it
        self._steps = 0
        self._peak: dict | None = None

    def consume(self) -> None:
        """Start a new peak-hold: the next sample() covers the physics steps from here."""
        self._peak = None

    def sample(self) -> dict:
        """Every climb signal, peak-held since the last consume(); with no physics step since, the current values."""
        if self._peak is None:
            return self._read(update=False)
        return dict(self._peak)

    def after_physics(self) -> None:
        v = self._read(update=True)
        p = self._peak
        if p is None:
            self._peak = v
            return
        for k in self._last_keys:
            p[k] = v[k]
        if abs(v["climb.swing"]) > abs(p["climb.swing"]):
            p["climb.swing"] = v["climb.swing"]
        for k in self._max_keys:
            if v[k] > p[k]:
                p[k] = v[k]

    def _read(self, update: bool) -> dict:
        """This step's values; with `update`, it also keeps the swing's bar and when each hold was hooked."""
        m, d = self.model, self.data
        robot, ground, hold, hook = self._robot_l, self._ground_l, self._hold_l, self._hook_l
        touched: set[int] = set()
        hooked: set[int] = set()        # holds a hook touches
        on_ground = False
        on_hooks = []                   # (contact, hook, the hook is geom1)
        on_chain: dict[int, set[int]] = {}   # a chain's hold -> the hooks touching it
        partner = self._partner[1] if self._partner is not None else None
        partner_down = False            # a hanging partner touches the ground
        for i, (g1, g2) in enumerate(contact_ids(m, d).tolist()):    # plain Python: a handful of contacts
            if partner is not None and not partner_down and (
                    (partner[g1] and ground[g2]) or (partner[g2] and ground[g1])):
                partner_down = True
            r1 = robot[g1]
            if r1 == robot[g2]:
                continue
            mine, other = (g1, g2) if r1 else (g2, g1)
            if ground[other]:
                on_ground = True
            if hold[other] >= 0:
                touched.add(hold[other])
            if hook[mine] >= 0:
                on_hooks.append((i, hook[mine], r1))
                if hold[other] >= 0:
                    hooked.add(hold[other])
                    if hold[other] in self._chains:
                        on_chain.setdefault(hold[other], set()).add(hook[mine])
        hanging = bool(touched) and not on_ground
        if update:
            self._steps += 1
            for h in hooked - self._hooked_before:
                self._hooked_at[h] = self._steps
            self._hooked_before = hooked
        bar = None
        if hooked:              # a hold hooked since the last step (in a sample() between steps) counts as newest
            bar = max(sorted(hooked), key=lambda h: self._hooked_at.get(h, self._steps + 1))
        elif touched:
            bar = self._last if self._last in touched else max(touched)
        if update and hanging:
            self._last = bar
        level, points = self._score(touched) if hanging and self.rule is not None else (0, 0.0)
        robots = [0] * len(self.holds)  # per hold: the robots on it, off the carpet
        if hanging:
            for h in touched:
                robots[h] += 1
        if self._partner is not None and not partner_down:
            robots[self._partner[0]] += 1
        v = {"climb.hanging": int(hanging), "climb.level": level, "climb.points": points,
             "climb.hold": self._number[bar] if hanging else 0,
             "climb.harmony": self.harmony * (robots[bar] - 1) if hanging and self.harmony else 0.0,
             "climb.swing": self._swing(bar, on_chain.get(bar, ())) if hanging else 0.0}
        for key, n in zip(self._robots_keys, robots):
            v[key] = n
        loads = [[0.0, 0.0, 0.0] for _ in self.hooks]
        if on_hooks:
            frames, f6 = d.contact.frame, self._f6
            for i, k, first in on_hooks:
                mujoco.mj_contactForce(m, d, i, f6)
                fn, f1, f2 = f6[:3].tolist()
                nx, ny, nz, ax, ay, az, bx, by, bz = frames[i].tolist()    # rows: the normal, then two tangents
                sign = 1.0 if first else -1.0    # the hook is geom2: flip, so every contact adds the same side's force
                load = loads[k]
                load[0] += sign * (fn * nx + f1 * ax + f2 * bx)
                load[1] += sign * (fn * ny + f1 * ay + f2 * by)
                load[2] += sign * (fn * nz + f1 * az + f2 * bz)
        top = 0.0
        for key, (x, y, z) in zip(self._hook_keys, loads):
            v[key] = n = math.sqrt(x * x + y * y + z * z)
            top = max(top, n)
        v["climb.hook_load"] = top
        if self.extent:
            v["climb.extension"], v["climb.height"] = self._extent()
        else:
            v["climb.extension"] = v["climb.height"] = 0.0
        return v

    def _score(self, touched: set[int]) -> tuple[int, float]:
        rule = self.rule.rule
        rank = 0
        if rule == "lowest-rung":
            ranks = [self.holds[h][2] for h in touched if self.holds[h][2] > 0]
            rank = min(ranks) if ranks else 0
        elif rule == "off-carpet":      # hanging is off the carpet: the highest level touched (R8)
            rank = max((self.holds[h][2] for h in touched), default=0)
        elif rule == "hang":
            k, adr, level = self._switch
            if k not in touched:
                return 0, 0.0
            bonus = self.rule.level_bonus if abs(float(self.data.qpos[adr])) <= level else 0.0
            return 1, float(self._levels[0]) + bonus
        else:                           # lowest-point: one above the highest level all the way below
            low = self._lowest()
            d, m = self.data, self.model
            below = 0
            for k, (_, geoms, r, switch) in enumerate(self.holds):
                if r == 0 or switch or k in self._chains:
                    continue
                g = geoms[0]
                if float(d.geom_xpos[g][2]) + float(m.geom_size[g][0]) < low:
                    below = max(below, r)
            rank = min(below + 1, len(self._levels))
        return rank, float(self._levels[rank - 1]) if rank else 0.0

    def _chain_point(self, bar: int, hooks) -> np.ndarray:
        """A point on chain `bar`'s swing axis (R6): the mean of the touching hooks' geoms, else its lowest vertex."""
        if hooks:
            return self.data.geom_xpos[np.concatenate([self._hook_geoms[k] for k in sorted(hooks)])].mean(axis=0)
        from ..testrange.chain import lowest_vertex
        return lowest_vertex(self.model, self.data, self._chains[bar][0])

    def _swing(self, bar: int, hooks=()) -> float:
        d = self.data
        if bar in self._chains:         # about the chain's horizontal end-to-end line
            ax, ay, az = self._chains[bar][1].tolist()
            px, py, pz = self._chain_point(bar, hooks).tolist()
            cx, cy, cz = d.subtree_com[self.body].tolist()
            rx, ry, rz = cx - px, cy - py, cz - pz
            return math.degrees(math.atan2(rx * ay - ry * ax, -rz))    # across: axis x up = (ay, -ax, 0)
        g = self.holds[bar][1][0]
        _, _, ax, _, _, ay, _, _, az = d.geom_xmat[g].tolist()     # the pipe's axis: its z column
        px, py, pz = d.geom_xpos[g].tolist()
        cx, cy, cz = d.subtree_com[self.body].tolist()
        rx, ry, rz = cx - px, cy - py, cz - pz
        ux, uy, uz = -az * ax, -az * ay, 1.0 - az * az              # up, less its part along the axis
        sx, sy, sz = ay * uz - az * uy, az * ux - ax * uz, ax * uy - ay * ux    # axis x up
        return math.degrees(math.atan2(rx * sx + ry * sy + rz * sz, -(rx * ux + ry * uy + rz * uz)))

    def contact_state(self) -> tuple[bool, set[str], dict[str, set[str]], list[tuple[int, str, float]]]:
        """The robot's contacts now, for `climb run`'s waits and failure checks: (it touches the ground, the holds it
        touches, {hold: the hooks (`<inst>.hook<i>`) touching it}, and (robot geom, hold, normal force in N) for each
        contact of a robot geom that isn't a hook with a hold)."""
        m, d = self.model, self.data
        robot, ground, hold, hook = self._robot_l, self._ground_l, self._hold_l, self._hook_l
        on_ground, touched, hooked, others = False, set(), {}, []
        for i, (g1, g2) in enumerate(contact_ids(m, d).tolist()):
            r1 = robot[g1]
            if r1 == robot[g2]:
                continue
            mine, other = (g1, g2) if r1 else (g2, g1)
            if ground[other]:
                on_ground = True
            h = hold[other]
            if h < 0:
                continue
            name = self.holds[h][0]
            touched.add(name)
            if hook[mine] >= 0:
                hooked.setdefault(name, set()).add(self.hooks[hook[mine]])
            else:
                mujoco.mj_contactForce(m, d, i, self._f6)
                others.append((mine, name, float(self._f6[0])))
        return on_ground, touched, hooked, others

    def is_hook_assembly(self, geom: int) -> bool:
        """Robot geom `geom` is a hook's or the bracket carrying the hooks (`<inst>.bracket`): meant to touch
        bars, never "hit a bar"."""
        return geom in self._assembly

    def swing_direction(self) -> np.ndarray | None:
        """The world direction (horizontal, across the swing's bar) in which a positive climb.swing puts the CoM, as
        of the last physics step; None before the robot has hung from a bar."""
        if self._last is None:
            return None
        if self._last in self._chains:      # the chain's end-to-end line is horizontal: axis x up
            ax, ay, _ = self._chains[self._last][1].tolist()
            return np.array([ay, -ax, 0.0])
        g = self.holds[self._last][1][0]
        ax, ay, az = self.data.geom_xmat[g].reshape(3, 3)[:, 2].tolist()
        ux, uy, uz = -az * ax, -az * ay, 1.0 - az * az
        s = np.array([ay * uz - az * uy, az * ux - ax * uz, ax * uy - ay * ux])
        n = float(np.linalg.norm(s))
        return s / n if n > 0.0 else None

    def own_height(self) -> float:
        """The robot's height in its own frame: its colliding geoms' bounding boxes from lowest to highest along the
        chassis's up. Resting level on a flat floor it is climb.height; hanging or tilted, it is still the height
        the robot would stand (G106 measures a robot "resting normally on a flat floor")."""
        if not len(self._geoms):
            return 0.0
        up = self.data.xmat[self.body].reshape(3, 3)[:, 2]
        z = self._points().reshape(-1, 3) @ up
        return float(z.max() - z.min())

    def _points(self) -> np.ndarray:
        """The corners of each colliding robot geom's bounding box, in the world: (geoms, 8, 3)."""
        d = self.data
        rot = d.geom_xmat[self._geoms].reshape(-1, 3, 3)
        return self._corners @ rot.transpose(0, 2, 1) + d.geom_xpos[self._geoms][:, None, :]

    def _lowest(self) -> float:
        return float(self._points()[..., 2].min()) if len(self._geoms) else 0.0

    def _extent(self) -> tuple[float, float]:
        """(extension, height)."""
        if not len(self._geoms):
            return 0.0, 0.0
        d = self.data
        pts = self._points()
        height = float(pts[..., 2].max())
        if not self._reach:
            return 0.0, height
        rc = d.xmat[self.body].reshape(3, 3)
        local = ((pts[:self._reach].reshape(-1, 3) - d.xpos[self.body]) @ rc[:, :2]).reshape(-1, 8, 2)  # chassis x, y
        out = np.maximum(np.maximum(local.max(axis=1) - self._frame_hi, self._frame_lo - local.min(axis=1)), 0.0)
        return float(np.sqrt((out * out).sum(axis=1)).max()), height
