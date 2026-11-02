"""The conditions a test's blocks wait for and check (`wait_until`, `repeat until`, `expect`), watched every physics
step, and the plain words that name them in a run's outcome.

- `joint`: a hinge's angle (degrees) or a slide's travel (inches or meters) above or below a number.
- `tilt`: the angle between the robot body's z axis and straight up, in degrees.
- `touch`: a robot part, body or hook (`hook` for any of them) touching a field element, bar, game piece or any
  named field geom or body; `next bar` for the bar the robot climbs to next.
- `piece_in`: a game piece's center inside a robot body's box: the axis-aligned box, in the body's own frame, around
  its geoms' bounding boxes. `any` for any piece.
- `hanging_from`: the robot hangs from a bar: the hooks touching it carry at least a quarter of the robot's weight
  (its subtree mass times g) for HANG_S. A hook's load is the climb monitor's (`climb.hook.<hook>.load`), on each
  bar it touches.
- `level`: on a range that scores climbs, the climb monitor's scoring level (`climb.level`) is this one.

"The next bar" is the lowest bar whose top is higher than every bar the robot has hung from so far in this run; the
lowest bar before the robot has hung from any.

The Watch reads the world's ClimbMonitor every step and starts its peak-hold over (consume()), so `climb` holds the
last physics step's climb signals; a runner using a Watch reads them there.
"""

from __future__ import annotations

import math

import mujoco
import numpy as np

from ..terrain.climb import hold_geoms, robot_hooks
from .palette import ANY_HOOK, ANY_PIECE, NEXT_BAR

HANG_S = 0.1            # s: the hooks carry a quarter of the robot's weight this long to count as hanging
HANG_SHARE = 0.25       # of the robot's weight
IN = 0.0254


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
        self._hook_geoms = {name: set(gs) for name, gs in robot_hooks(m, self.body).items()}
        for name in program.hooks:
            self._hook_geoms[name] = set(self._body_geoms(name))
        self._balls = [m.body(n).id for n in layout.ball_names] if layout is not None else []
        self._ball_names = list(layout.ball_names) if layout is not None else []
        self._sides: dict[str, set[int]] = {}
        self._boxes: dict[str, tuple[int, np.ndarray, np.ndarray]] = {}

        self.tilt = 0.0                                     # deg
        self.climb: dict = {}                               # the last step's climb signals
        self.hanging: set[str] = set()                      # holds the robot hangs from now
        self.hung: list[str] = []                           # holds hung from so far, in order
        self._held = {name: 0.0 for name in self._holds}    # s each hold has carried a quarter of the weight
        self._read_tilt()

    # ---- every physics step -----------------------------------------------------------------------------------------

    def update(self) -> None:
        """Read the world after a physics step."""
        self._read_tilt()
        if self.mon is not None:
            self.climb = v = self.mon.sample()
            self.mon.consume()
            _, _, hooked, _ = self.mon.contact_state()
            dt = float(self.model.opt.timestep)
            need = HANG_SHARE * self.weight
            for name in self._held:
                load = sum(float(v.get(f"climb.hook.{h}.load", 0.0)) for h in hooked.get(name, ()))
                self._held[name] = self._held[name] + dt if load >= need else 0.0
                if self._held[name] >= HANG_S - 0.5 * dt:
                    self.hanging.add(name)
                    if name not in self.hung:
                        self.hung.append(name)
                else:
                    self.hanging.discard(name)

    def _read_tilt(self) -> None:
        zz = float(self.data.xmat[self.body][8])        # the body's z axis, its world z component
        self.tilt = math.degrees(math.acos(max(-1.0, min(1.0, zz))))

    # ---- the bars ---------------------------------------------------------------------------------------------------

    def _top(self, name: str) -> float:
        """A hold's top now: the highest point of its first geom's bounding box."""
        d, m = self.data, self.model
        g = self._holds[name][0]
        r = d.geom_xmat[g].reshape(3, 3)
        c, h = m.geom_aabb[g][:3], m.geom_aabb[g][3:]
        return float(d.geom_xpos[g][2] + r[2] @ c + np.abs(r[2]) @ h)

    def next_bar(self) -> str | None:
        """The lowest bar whose top is above every bar hung from so far; None when there is none."""
        tops = {name: self._top(name) for name in self._holds}
        above = max((tops[n] for n in self.hung), default=-math.inf)
        higher = [n for n in tops if tops[n] > above]
        return min(higher, key=lambda n: tops[n]) if higher else None

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

    def _field_side(self, name: str) -> set[int]:
        if name not in self._sides:
            m = self.model
            if name in self._holds:
                gs = set(self._holds[name])
            else:
                el = f"element.{name}"
                gs = {g for g in range(m.ngeom)
                      if m.body_rootid[m.geom_bodyid[g]] != self._robot_root
                      and (m.geom(g).name == name or m.geom(g).name == el or m.geom(g).name.startswith(el + ".")
                           or m.body(m.geom_bodyid[g]).name == name)}
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
            for g1, g2 in self.data.contact.geom[:self.data.ncon].tolist():     # the last physics step's contacts
                if (g1 in mine and g2 in other) or (g2 in mine and g1 in other):
                    return True
            return False
        if k == "piece_in":
            b, lo, hi = self._box(a["body"])
            d = self.data
            balls = self._balls if a["piece"] == ANY_PIECE else [self._balls[self._ball_names.index(a["piece"])]]
            if not balls:
                return False
            r = d.xmat[b].reshape(3, 3)
            local = (d.xpos[balls] - d.xpos[b]) @ r          # each center in the body's frame
            return bool(np.any(np.all((local >= lo) & (local <= hi), axis=1)))
        if k == "hanging_from":
            bar = self._bar(a["bar"])
            return bar is not None and bar in self.hanging
        if k == "level":
            rank = int(self.climb.get("climb.level", 0))
            return 0 < rank <= len(self._levels) and self._levels[rank - 1] == a["level"]
        raise ValueError(f"unknown condition {k!r}")


# ---- plain words ----------------------------------------------------------------------------------------------------

def _amount(x: float, unit: str) -> str:
    return f"{x:g}°" if unit == "deg" else f"{x:g} {unit}"


def describe(cond, next_bar: str | None = None) -> str:
    """The condition in plain words, for a run's outcome. `next_bar` names "the next bar" when known."""
    a, k = cond.args, cond.kind

    def bar(name: str) -> str:
        return (next_bar or "the next bar") if name == NEXT_BAR else name

    if k == "joint":
        if "above" in a:
            return f"{a['name']} passes {_amount(a['above'], a['unit'])}"
        return f"{a['name']} goes below {_amount(a['below'], a['unit'])}"
    if k == "tilt":
        if "above" in a:
            return f"the robot tilts more than {_amount(a['above'], 'deg')}"
        return f"the robot tilts less than {_amount(a['below'], 'deg')}"
    if k == "touch":
        who = "a hook" if a["robot"] == ANY_HOOK else a["robot"]
        return f"{who} touches {bar(a['field'])}"
    if k == "piece_in":
        what = "a ball" if a["piece"] == ANY_PIECE else a["piece"]
        return f"{what} reaches {a['body']}"
    if k == "hanging_from":
        return f"the robot hangs from {bar(a['bar'])}"
    if k == "level":
        return f"the robot hangs at {a['level']}"
    raise ValueError(f"unknown condition {k!r}")
