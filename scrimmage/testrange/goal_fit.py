"""Does a rigid piece's flight score in a goal? Whether all of the piece fits through the opening, and whether it hit
something on the way in.

A caller (the bench's `[flight] goal`, and `launcher aim`) flies the piece with launcher/flight6.py, passing the goal's
scoring surface in flight6's `goals`, and then asks `goal_fit` for the verdict. It passes:
- `res`: the flight6.Flight6Result, flown on the range's field (field coordinates, z up);
- `surface`: the goal's scoring surface (spec.Goal.scoring_surface()), the one flight6 was given;
- `goal`: the goal's name (spec.Goal.name), so its own plates are known by their names, `<goal>.<plate>`;
- `boxes`: the solid things on the field, `field_boxes(rng)`: (name, center, axes as columns, half sizes) each;
- `corners`: the 8 corners of the piece's bounding box in its body frame, `kind_corners(kind)` or `box_corners`.

The rule, since there is no rim for a flight to collide with (the box does the rim's job):
- before the center crosses, at every sample of the flight: the center and the 8 corners (turned as the piece then
  is) must stay out of every obstacle, element and other goal's plates, and the center out of the goal's own plates (a
  tilted piece's leading corners pass the opening's plane before its center does, into a slot's back wall or a
  funnel's walls: that isn't a block);
- at the crossing: the 8 corners, turned as the piece is then (the orientation interpolated between the samples on
  either side), seen along the surface's normal, all fall inside the opening. If one doesn't, the piece didn't fit
  through and caught the rim plate on that edge, `<goal>.rim_<k>`, or `<goal>.edge_<k>` when the goal has none there.

A ball (`launcher aim` at a goal that isn't level) is judged the same way by `fit_ball`, from its center's path:
its ball (the center and radius) must touch nothing before the crossing (its center the goal's own plates), and at
the crossing its center must be a radius inside the opening.

The box is a little bigger than a round piece (a ring's corners stick out past its round), so this is strict for a
turned ring. The points are checked at the flight's samples (1 ms: 1 cm at 10 m/s), not swept between them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..launcher import flight6
from .pieces import PieceKind, _lo_hi
from .spec import Range, Surface


@dataclass(frozen=True)
class Fit:
    """A flight's verdict on a goal: `scored`; else what stopped it, `hit` (something it hit before the crossing,
    by name) or `caught` (the rim plate or edge it overlapped at the crossing: it didn't fit through). Both None for a
    flight that never crossed the opening."""
    scored: bool
    hit: str | None = None
    caught: str | None = None


def field_boxes(rng: Range) -> tuple[tuple[str, np.ndarray, np.ndarray, np.ndarray], ...]:
    """The solid things on a range's field, each (name, center, axes as columns, half sizes) in field coordinates:
    its obstacles (by name), its elements' boxes (by the element's name) and every goal's plates, `<goal>.<plate>`
    (walls, funnel, rim bars, a slot's frame and box, `[[goal.plate]]`s), as build.py builds them."""
    from .build import _plates          # build.py pulls in mujoco
    from .station import _element_boxes

    boxes = [(o.name, np.array(o.pos, float), o.rotation(), np.array(o.size, float) / 2.0) for o in rng.obstacles]
    boxes += [(e.name, np.asarray(c, float), np.asarray(r, float), np.asarray(h, float))
              for e in rng.elements for c, r, h in _element_boxes(e)]
    for g in rng.goals:
        rot = g.rotation()
        boxes += [(f"{g.name}.{n}", g.to_world(c), rot @ local, np.array(half, float))
                  for n, c, local, half, _ in _plates(g)]
    return tuple(boxes)


def box_corners(lo, hi) -> np.ndarray:
    """The 8 corners (8, 3) of the box from `lo` to `hi`."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])], dtype=float)


def kind_corners(kind: PieceKind) -> np.ndarray:
    """A rigid kind's bounding box corners (8, 3) in its body frame: the box spec.piece_box measures."""
    lo = np.min([_lo_hi(s)[0] for s in kind.solids], axis=0)
    hi = np.max([_lo_hi(s)[1] for s in kind.solids], axis=0)
    return box_corners(lo, hi)


def matrices(quats: np.ndarray) -> np.ndarray:
    """(n, 3, 3) rotation matrices of (n, 4) unit quaternions (w, x, y, z)."""
    w, x, y, z = (quats[:, k] for k in range(4))
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], axis=-1),
        np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], axis=-1),
        np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], axis=-1),
    ], axis=1)


def first_hit(points: np.ndarray, boxes) -> tuple[int, str] | None:
    """The first sample of `points` ((n, k, 3): each sample's k points on the field) with a point inside one of
    `boxes` (field_boxes' tuples), as (sample index, the box's name); None when no point enters any box."""
    best = None
    for name, center, axes, half in boxes:
        local = (points - center) @ axes      # local coordinates (the box's axes are columns)
        inside = np.all(np.abs(local) <= half, axis=-1).any(axis=-1)
        hit = np.flatnonzero(inside)
        if len(hit) and (best is None or hit[0] < best[0]):
            best = (int(hit[0]), name)
    return best


def first_touch(centers: np.ndarray, radius: float, boxes) -> tuple[int, str] | None:
    """first_hit for a ball: the first of `centers` ((n, 3) on the field) whose ball of `radius` overlaps one of
    `boxes`, as (sample index, the box's name); None when it touches none."""
    best = None
    for name, center, axes, half in boxes:
        local = (centers - center) @ axes
        gap = np.linalg.norm(np.maximum(np.abs(local) - half, 0.0), axis=-1)    # 0 inside the box
        hit = np.flatnonzero(gap <= radius)
        if len(hit) and (best is None or hit[0] < best[0]):
            best = (int(hit[0]), name)
    return best


def outside_edge(surface: Surface, points: np.ndarray, margin: float = 0.0) -> int | None:
    """Seen along the surface's normal, the edge of its outline (index k: from corner k to k + 1) that `points`
    ((m, 3), on the field) stick out of furthest, or come within `margin` of from inside (a ball's radius); None
    when all are inside, that far from every edge."""
    q = (np.asarray(points, float) - surface.center) @ surface.axes.T  # (m, 2) in the plane
    a = surface.polygon
    edge = np.roll(a, -1, axis=0) - a
    rel = q[:, None, :] - a[None, :, :]
    out = -(edge[None, :, 0] * rel[:, :, 1] - edge[None, :, 1] * rel[:, :, 0]) / np.linalg.norm(edge, axis=1)
    if np.all(out <= 1e-9 - margin):
        return None
    return int(np.unravel_index(np.argmax(out), out.shape)[1])


def _caught(goal: str, edge: int, boxes) -> Fit:
    """The verdict on a piece sticking out of `edge`: caught on its rim plate, or on the edge when it has none."""
    rim = f"{goal}.rim_{edge}"
    return Fit(False, caught=rim if any(b[0] == rim for b in boxes) else f"{goal}.edge_{edge}")


def fit(res: flight6.Flight6Result, surface: Surface, goal: str, boxes, corners: np.ndarray) -> Fit:
    """The verdict on a flight (see the module docstring for what to pass and the rule). `res.crossing` must be the
    crossing of `surface` (flight6's `goals` given it, under any name)."""
    if res.crossing is None:
        return Fit(False)
    _, t_cross, center = res.crossing
    ts = res.samples[:, flight6.T]
    before = res.samples[ts < t_cross]
    centers = before[:, flight6.POS][:, None, :]
    pts = np.concatenate([centers, centers + np.einsum("nij,kj->nki", matrices(before[:, flight6.QUAT]), corners)],
                         axis=1)
    own = f"{goal}."
    others = [b for b in boxes if not b[0].startswith(own)]
    mine = [b for b in boxes if b[0].startswith(own)]
    hits = [h for h in (first_hit(pts, others), first_hit(centers, mine)) if h is not None]
    if hits:
        return Fit(False, hit=min(hits)[1])
    k = max(len(before) - 1, 0)
    span = ts[k + 1] - ts[k] if k + 1 < len(ts) else 0.0
    f = (t_cross - ts[k]) / span if span > 0 else 0.0
    q0, q1 = res.samples[k, flight6.QUAT], res.samples[min(k + 1, len(ts) - 1), flight6.QUAT]
    q = (1 - f) * q0 + f * (q1 if q0 @ q1 >= 0 else -q1)
    at = center + corners @ flight6.matrix_from_quat(q / np.linalg.norm(q)).T
    edge = outside_edge(surface, at)
    if edge is None:
        return Fit(True)
    return _caught(goal, edge, boxes)


def fit_ball(before: np.ndarray, at: np.ndarray, surface: Surface, goal: str, boxes, radius: float) -> Fit:
    """fit's verdict for a ball of `radius`, from its center's path: `before` ((n, 3), the samples before the center
    crossed the surface) and `at` (the crossing point). Before the crossing the ball must touch no obstacle, element or
    other goal's plates, and its center stay out of the goal's own plates (as fit's rule); at the crossing its center
    must be at least `radius` inside the opening, else it caught that edge's rim plate (or edge)."""
    own = f"{goal}."
    others = [b for b in boxes if not b[0].startswith(own)]
    mine = [b for b in boxes if b[0].startswith(own)]
    before = np.asarray(before, float).reshape(-1, 3)
    hits = [h for h in (first_touch(before, radius, others), first_hit(before[:, None, :], mine)) if h is not None]
    if hits:
        return Fit(False, hit=min(hits)[1])
    edge = outside_edge(surface, np.asarray(at, float)[None, :], margin=radius)
    if edge is None:
        return Fit(True)
    return _caught(goal, edge, boxes)
