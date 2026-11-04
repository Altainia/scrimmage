"""Clearance between the robot and a field element (spec C6): the smallest distance between any robot geom that
collides and any geom of the element.

- **Robot geoms:** every geom under the robot's root body with a nonzero contype or conaffinity: the stand-in
  chassis and its treads, folder parts from CAD, and stock parts with `collide = true` (with it off they touch
  nothing, and don't count).
- **Element geoms:** every geom of the bodies `element.<name>` and `element.<name>.<...>` and the bodies under them
  (a hinged or sliding panel, a box), and any geom so named on the world body, that collides (our call: a geom that
  touches nothing can't be hit, so it doesn't count either).
- **Measure:** mujoco.mj_geomDistance, out to DISTMAX, for each pair whose bounding spheres are within DISTMAX of
  each other (the others are skipped). Nothing within DISTMAX reads math.inf; a touch or an overlap reads 0.0.
- **Cost:** the geom lists are worked out once per model. A run measures an element only when its test names it: a
  `clears` condition, or `clearance.<name>` in the test's chart (named()).
- **Parts it leaves out:** a test that measures an element on a robot with stock parts whose collide is off gets
  uncounted_note, a run note and a Test-tab hint naming them: a raised arm with collide off passes through the
  element unmeasured, and the number read would be the chassis's.
"""

from __future__ import annotations

import math
import weakref

import mujoco
import numpy as np

DISTMAX = 0.5           # m: how far clearance is measured (spec C6)
IN = 0.0254
NEVER_WITHIN_IN = 20    # in: DISTMAX (19.7 in) as the words say it ("never within 20 in", ">20 in")
PREFIX = "clearance."   # a chart signal's name: clearance.<element>, in inches

_GEOMS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()     # model -> {element or None: geom ids}


def _colliding(m) -> np.ndarray:
    return (m.geom_contype != 0) | (m.geom_conaffinity != 0)


def _geoms(world, element: str | None) -> np.ndarray:
    """The robot's colliding geoms (element None) or the element's, cached per model."""
    m = world.model
    per = _GEOMS.setdefault(m, {})
    if element not in per:
        root = int(m.body_rootid[world.robot_body])
        mine = m.body_rootid[m.geom_bodyid] == root
        if element is None:
            per[None] = np.flatnonzero(_colliding(m) & mine)
        else:
            el = f"element.{element}"

            def its(n: str) -> bool:
                return n == el or n.startswith(el + ".")

            bodies: set[int] = set()
            for b in range(1, m.nbody):         # a parent's id is lower than its children's
                if its(m.body(b).name) or int(m.body_parentid[b]) in bodies:
                    bodies.add(b)
            per[element] = np.array([g for g in np.flatnonzero(_colliding(m) & ~mine)
                                     if int(m.geom_bodyid[g]) in bodies or its(m.geom(g).name)], int)
    return per[element]


def clearance(world, element: str) -> float:
    """m: the smallest distance between a colliding robot geom and a geom of `element`; 0.0 for a touch or an
    overlap, math.inf when nothing is within DISTMAX."""
    m, d = world.model, world.data
    robot, field = _geoms(world, None), _geoms(world, element)
    if not len(robot) or not len(field):
        return math.inf
    apart = (np.linalg.norm(d.geom_xpos[robot][:, None, :] - d.geom_xpos[field][None, :, :], axis=2)
             - m.geom_rbound[robot][:, None] - m.geom_rbound[field][None, :])
    best = DISTMAX
    for i, j in zip(*np.nonzero(apart <= DISTMAX)):
        best = min(best, mujoco.mj_geomDistance(m, d, int(robot[i]), int(field[j]), DISTMAX, None))
    return math.inf if best >= DISTMAX else max(0.0, best)


def inches(c: float) -> float:
    """A clearance as its chart signal reads it: inches, NaN when nothing is within DISTMAX."""
    return c / IN if math.isfinite(c) else math.nan


def named(program, elements) -> list[str]:
    """The elements a test measures, sorted: those its `clears` conditions name, and those its chart names as
    `clearance.<element>` that are among `elements` (the field's)."""
    from .conditions import _conds
    out = {c.args["element"] for c in _conds(program.blocks, "clears")}
    known = set(elements)
    out |= {s[len(PREFIX):] for s in program.chart or [] if s.startswith(PREFIX) and s[len(PREFIX):] in known}
    return sorted(out)


def uncounted(world) -> list[str]:
    """The robot's stock parts with collide off, sorted: their geoms touch nothing, so clearance doesn't count them. A
    push cylinder (a cylinder with `push` set) is left out: it is drawn only and never collides, so collide = true
    wouldn't help."""
    parts = world.parts
    if parts is None:
        return []
    return sorted(i.name for i in parts.instances if i.stock_params.get("collide") is False
                  and not (i.source == "stock cylinder" and i.stock_params.get("push")))


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def uncounted_note(world, program) -> str | None:
    """When the test measures an element (named()) and the robot has stock parts with collide off: "clearance counts
    only parts that collide: arm and elevator have collide off, so they aren't measured (set collide = true on
    them)". None otherwise."""
    rng = world.parts.range if world.parts is not None else None
    if not named(program, [e.name for e in rng.elements] if rng is not None else []):
        return None
    off = uncounted(world)
    if not off:
        return None
    if len(off) == 1:
        return (f"clearance counts only parts that collide: {off[0]} has collide off, so it isn't measured "
                "(set collide = true on it)")
    return (f"clearance counts only parts that collide: {_and(off)} have collide off, so they aren't measured "
            "(set collide = true on them)")


class Closest:
    """Each measured element's clearance at the last update, and the smallest so far with when (RunResult.clearance's
    numbers)."""

    def __init__(self, world, elements: list[str]):
        self.world = world
        self.now = {e: math.inf for e in elements}
        self._low = {e: (math.inf, None) for e in elements}

    def update(self, t: float) -> None:
        for e in self.now:
            c = self.now[e] = clearance(self.world, e)
            if c < self._low[e][0]:
                self._low[e] = (c, t)

    def result(self) -> dict | None:
        """{element: {"min_in", "t"}}, both None for an element never within DISTMAX; None when none is measured."""
        if not self.now:
            return None
        return {e: {"min_in": c / IN if math.isfinite(c) else None, "t": t} for e, (c, t) in self._low.items()}

