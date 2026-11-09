"""An aiming table from the launcher model: for each robot-to-goal distance, the wheel speed and hood angle whose
ball crosses the goal's rim height, descending, at the goal's center.

The geometry comes from the config's assembled robot at its start pose (the launcher shaft's height and its forward
offset from the robot center, the robot facing the goal) and from the range's goal. The launcher is the model's own:
a ball fed at the setup's entry speed into the lane nearest the middle, the shaft held at the wheel speed by a
velocity loop (kP AIM_KP, a stand-in for the robot's own gains: the dip a ball causes is recovered at that rate),
then flown by `fly()`. The robot is taken as stationary and the ball's path unobstructed (the rim's edge and the
funnel are not modeled); a lane off the middle lands that far to the side. An estimate from the launcher model's
preset numbers, so UNCALIBRATED, unless its ball and wheels are measured (calibration()).

A goal that isn't level (tilt_deg not 0: the 2024 SPEAKER, entered going up under its roof; a wall slot) is aimed at
its scoring surface instead (the "surface" functions below; a level goal, the hub, keeps the path above exactly):
- the robot stands on the goal's approach line, back from the goal's center along the horizontal part of the
  surface's normal (along the goal's heading, backward, when the normal is vertical), facing the goal;
- a row's ball crosses the surface's center from the side it is entered (the normal's), at least its radius inside
  the opening, and touches none of the range's obstacles, elements or other goals' plates, nor (its center) the
  goal's own plates, before it crosses (goal_fit.fit_ball); flight_s is to that crossing, apex the flight's highest
  point as if nothing stopped it (as the hub's, which flies on to the floor);
- the first row of an upward goal (its normal points down) takes the lowest apex that scores, not the highest;
- a path doesn't depend on the distance (the robot stands still; only the goal moves along the approach line), so
  each hood angle's paths on a coarse wheel-speed grid are flown once, with fly_path, each only until it has come
  back down through the center's height. Where a path passes the center's height in the entry direction, going up
  or coming down, is that angle's crossing curve: the distance that path would score from. A distance is bracketed
  on the grid (so a narrow window, or a curve that turns back, is found) and solved by regula falsi.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np

from .. import i18n
from ..config import load_config
from ..launcher.flight import Landing, fly, fly_path, launch_state
from ..launcher.inputs import Setup
from ..launcher.single import single_pass
from ..parts.assemble import assemble
from .goal_fit import field_boxes, fit_ball
from .shooter import parse_shooter
from .spec import Goal

AIM_KP = 0.5            # V per wheel rps of error: the velocity loop holding the shaft while a ball passes
RPS_TOLERANCE = 0.01    # the wheel speed is solved to this (rps)
HIT_TOLERANCE = 0.02    # m: the solved shot must cross the goal height this close to the goal center
ROW_FIELDS = ("distance", "wheel_rps", "hood_deg", "flight_s", "apex")
UNREACHABLE = "unreachable"
GRID_RPS = 10.0         # rps between a surface aim's coarse grid of wheel speeds, the brackets regula falsi starts from
OFFSET_TOLERANCE = 0.002    # m: a surface aim's regula falsi stops this close to the target distance
APEX_TOLERANCE = 1e-4       # m: and this close to the apex that just reaches the surface center's height
NORMAL_SPEED_TOLERANCE = 1e-3   # m/s: and this close to a crossing along the surface (no speed along its normal)


class AimError(ValueError):
    """A config, goal or distance the table cannot be made for."""


@dataclass(frozen=True)
class AimRow:
    distance: float     # m, robot center to goal center, horizontal
    wheel_rps: float
    hood_deg: float     # exit angle above horizontal
    flight_s: float     # s from the ball's exit to the rim-height crossing (a non-level goal: the surface's)
    apex: float         # m, the ball center's highest point (a non-level goal: as if it flew on, unobstructed)
    status: str = "ok"  # "ok", or UNREACHABLE: no wheel speed and hood angle reach it, the other fields NaN


@dataclass(frozen=True)
class AimModel:
    """What a table is made from: the launcher's setup (its height the shaft's), the lane the ball is fed at, the
    goal's rim height and the launcher's forward offset from the robot center."""
    setup: Setup
    lane: float
    rim_height: float
    forward: float
    hood_angles: tuple[float, ...]    # the angles tried: one for a fixed hood
    goal: Goal | None = None          # the goal, aimed at its scoring surface when it isn't level (tilt_deg not 0)
    boxes: tuple = dataclasses.field(default=(), compare=False)   # goal_fit.field_boxes: what a shot can hit

    @property
    def level(self) -> bool:
        """A level goal (the hub, entered going down) is aimed at the old way: its rim height, at its center."""
        return self.goal is None or self.goal.tilt_deg == 0.0


def calibration(model: AimModel) -> str:
    """The table's UNCALIBRATED line: what its setup measured (the range's ball, the shooter's setup file)."""
    from ..launcher import report
    return report.calibration([model.setup])


def load_model(config_path: Path, goal: str, hood_step_deg: float = 2.0) -> AimModel:
    if hood_step_deg <= 0.0:
        raise AimError(i18n.t("hood_step_deg must be positive, not {hood_step_deg}",
                              hood_step_deg=f"{hood_step_deg:g}"))
    cfg = load_config(config_path)
    if "shooter" not in cfg.raw:
        raise AimError(i18n.t("{config_path}: no {shooter_table}: the aiming table is made from the robot's launcher",
                              config_path=config_path, shooter_table="[shooter]"))
    try:
        built = assemble(cfg.raw, cfg.dir)
        shooter = parse_shooter(cfg.raw, built, cfg.dir)
    except ValueError as e:     # PartError is one
        raise AimError(f"{config_path}: {e}") from None
    if shooter.kind == "disc":
        raise AimError(i18n.t("{config_path}: {shooter_table} {kind_setting}: the aiming table is made from a ball "
                              "launcher; for a disc shooter's exit speeds use `{scrimmage_disc_table}` and "
                              "`{scrimmage_disc_run}`", config_path=config_path, shooter_table="[shooter]",
                              kind_setting="kind = \"disc\"", scrimmage_disc_table="scrimmage disc table",
                              scrimmage_disc_run="scrimmage disc run"))
    goals = {g.name: g for g in built.range.goals}
    if goal not in goals:
        raise AimError(i18n.t("goal {goal} isn't in range {name}; its goals: {goals}",
                              goal=repr(goal), name=repr(built.range.name), goals=', '.join(goals)))
    g = goals[goal]

    m = built.model
    data = mujoco.MjData(m)
    mujoco.mj_kinematics(m, data)
    robot, base = m.body(built.robot_body).id, m.body(f"{shooter.part}.base").id
    local = data.xmat[robot].reshape(3, 3).T @ (data.xpos[base] - data.xpos[robot])
    facing = data.xmat[robot].reshape(3, 3).T @ data.xmat[base].reshape(3, 3)[:, 0]   # launcher x in the robot frame
    if facing[0] <= 0.0:
        raise AimError(i18n.t("the launcher part {part} faces backward (its x axis points {facing} along the "
                              "robot's): the table assumes it shoots where the robot faces",
                              part=repr(shooter.part), facing=f"{facing[0]:+.2f}"))

    if shooter.hood == "fixed":
        angles = (shooter.hood_angle,)
    else:
        lo, hi = shooter.hood_range
        angles = tuple(lo + k * hood_step_deg for k in range(int(math.floor((hi - lo) / hood_step_deg + 1e-9)) + 1))
    return AimModel(setup=shooter.setup, lane=min(shooter.lanes, key=abs), rim_height=g.rim_height,
                    forward=float(local[0]), hood_angles=angles, goal=g,
                    boxes=() if g.tilt_deg == 0.0 else field_boxes(built.range))


def _setup(model: AimModel, hood_deg: float, distance: float) -> Setup:
    s = model.setup
    return dataclasses.replace(
        s, hood=dataclasses.replace(s.hood, exit_angle_deg=hood_deg),
        goal=dataclasses.replace(s.goal, distance=distance - model.forward, height=model.rim_height))


def _land(model: AimModel, hood_deg: float, rps: float, distance: float = 0.0) -> Landing | None:
    """One ball through the launcher with the shaft held at rps, then flown; None if it does not come out."""
    setup = _setup(model, hood_deg, distance)
    shot = single_pass(setup, rps, model.lane, kP=AIM_KP)
    if shot is None or shot.status != "launched":
        return None
    return fly(shot, setup, setup.ball)


def _cross(model: AimModel, hood_deg: float, rps: float) -> float | None:
    """Where the ball crosses the rim height, descending, in meters from the shaft (None: it never does)."""
    landing = _land(model, hood_deg, rps)
    return None if landing is None or landing.at_goal is None else landing.at_goal[0]


def fly_row(model: AimModel, row: AimRow) -> Landing:
    """The landing of the shot a row prescribes (the goal at the row's distance). A level goal's: a non-level
    goal's row is flown by surface_path."""
    landing = _land(model, row.hood_deg, row.wheel_rps, row.distance)
    if landing is None:
        raise AimError(i18n.t("the shot at {distance} m does not leave the launcher", distance=f"{row.distance:g}"))
    return landing


def _solve(model: AimModel, hood_deg: float, target: float, rps_range: tuple[float, float]) -> AimRow | None:
    lo, hi = rps_range
    short = lambda rps: (x := _cross(model, hood_deg, rps)) is None or x < target     # too weak to get there
    if not short(lo) or short(hi):
        return None     # the angle's crossings do not bracket the target
    while hi - lo > RPS_TOLERANCE:
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if short(mid) else (lo, mid)
    rps = 0.5 * (lo + hi)
    landing = _land(model, hood_deg, rps, target + model.forward)
    if landing is None or landing.at_goal is None or abs(landing.at_goal[0] - target) > HIT_TOLERANCE:
        return None     # the bracket closed on a jump (the ball just clearing the rim height), not a crossing
    return AimRow(target + model.forward, rps, hood_deg, landing.goal_time, landing.apex)


def reachable(model: AimModel, rps_range: tuple[float, float]) -> tuple[float, float]:
    """The robot-to-goal distances some hood angle reaches (m): from the nearest the slowest wheels cross at to the
    farthest the fastest do. Inside it, a gap between angles' ranges is not looked for."""
    lows, highs = [], []
    for a in model.hood_angles:
        for rps, into in ((rps_range[0], lows), (rps_range[1], highs)):
            x = _cross(model, a, rps)
            if x is not None:
                into.append(x + model.forward)
    if not highs:
        raise AimError(i18n.t("no hood angle in range gets a ball to the rim height at any wheel speed in "
                              "{low}..{high} rps",
                              low=f"{rps_range[0]:g}", high=f"{rps_range[1]:g}"))
    return (min(lows) if lows else min(highs)), max(highs)


HOOD_PER_RPS = 2.0      # deg of hood counted as 1 rps of wheel speed when a row is matched to the one before it


def choose(found: list[AimRow], previous: AimRow | None, lowest: bool = False) -> AimRow | None:
    """The row to keep among a distance's solutions (one per hood angle that reaches it). The first reachable distance
    takes the highest apex (the steepest entry; ties: the lower wheel speed), or with `lowest` (a goal entered going
    up, under a roof) the lowest apex (ties: the lower wheel speed). Each later one takes the solution nearest
    the previous reachable row's: wheel speed and hood angle together, HOOD_PER_RPS degrees counting as 1 rps (ties:
    the higher apex). Where the launcher is grip-limited the exit speed hardly changes with wheel speed, so the
    highest apex can sit anywhere on that plateau (rows of 57 rps beside 79 and 99); the nearest wheel speed alone
    jumps the hood instead (70 deg beside 40, a flat shot just clearing the rim). Staying near the previous row in
    both keeps the table smooth to interpolate."""
    if not found:
        return None
    if previous is None:
        if lowest:
            return min(found, key=lambda r: (r.apex, r.wheel_rps))
        return max(found, key=lambda r: (r.apex, -r.wheel_rps))
    return min(found, key=lambda r: (abs(r.wheel_rps - previous.wheel_rps)
                                     + abs(r.hood_deg - previous.hood_deg) / HOOD_PER_RPS, -r.apex))


def aim_table(config_path: Path, goal: str, distances: list[float], *, hood_step_deg: float = 2.0,
              rps_range: tuple[float, float] = (10.0, 120.0),
              on_unreachable: Callable[[str], None] | None = None,
              model: AimModel | None = None) -> list[AimRow]:
    """A row per distance. A distance nothing reaches gets an UNREACHABLE row (NaN values), and `on_unreachable` is
    called once with a message naming them and the reachable range; AimError only when none is reachable. `model`:
    load_model's, when the caller has loaded it already (for its label)."""
    if not distances:
        raise AimError(i18n.t("no distances"))
    if model is None:
        model = load_model(config_path, goal, hood_step_deg)
    surface = None if model.level else _SurfaceAim(model)
    rows = []
    previous = None     # the last reachable row
    for d in distances:
        if surface is None:
            found = [row for a in model.hood_angles
                     if (row := _solve(model, a, d - model.forward, rps_range)) is not None]
        else:
            found = surface.solutions(d, rps_range)
        best = choose(found, previous, lowest=surface is not None and surface.upward)
        if best is not None:
            previous = best
        nan = math.nan
        rows.append(dataclasses.replace(best, distance=d) if best is not None
                    else AimRow(d, nan, nan, nan, nan, UNREACHABLE))
    missed = [r.distance for r in rows if r.status == UNREACHABLE]
    if missed:
        if surface is None:
            near, far = reachable(model, rps_range)
            msg = i18n.t("{distances} m can't be reached with wheels at {low}..{high} rps and hood angles "
                         "{lowest}..{highest} deg to the {goal} rim height: reachable distances are about {near} to "
                         "{far} m", distances=", ".join(f"{d:g}" for d in missed), low=f"{rps_range[0]:g}",
                         high=f"{rps_range[1]:g}", lowest=f"{model.hood_angles[0]:g}",
                         highest=f"{model.hood_angles[-1]:g}", goal=goal, near=f"{near:.2f}", far=f"{far:.2f}")
        else:
            msg = i18n.t("{distances} m can't be scored with wheels at {low}..{high} rps and hood angles "
                         "{lowest}..{highest} deg: no shot found crosses the center of the {goal} opening from the "
                         "side it is entered without touching something first",
                         distances=", ".join(f"{d:g}" for d in missed), low=f"{rps_range[0]:g}",
                         high=f"{rps_range[1]:g}", lowest=f"{model.hood_angles[0]:g}",
                         highest=f"{model.hood_angles[-1]:g}", goal=goal)
        if len(missed) == len(rows):
            raise AimError(msg)
        if on_unreachable is not None:
            on_unreachable(msg)
    return rows


# ------------------------------------------------------------------------------------------------ surface aim

def _illinois(g: Callable[[float], float | None], a: float, fa: float, b: float, fb: float,
              tolerance: float = OFFSET_TOLERANCE) -> float | None:
    """A root of g between a and b (fa, fb their values, of opposite signs) by regula falsi, Illinois style (the end
    that keeps its place has its value halved, so both ends close in); None if g has no value (None) on the way."""
    side = 0
    c = a
    for _ in range(40):
        c = (a * fb - b * fa) / (fb - fa)
        fc = g(c)
        if fc is None:
            return None
        if abs(fc) <= tolerance or b - a <= RPS_TOLERANCE:
            return c
        if (fc < 0.0) == (fb < 0.0):
            b, fb = c, fc
            if side == -1:
                fa /= 2.0
            side = -1
        else:
            a, fa = c, fc
            if side == 1:
                fb /= 2.0
            side = 1
    return c


class _SurfaceAim:
    """A non-level goal's aim (the module docstring). Paths are in the launcher frame `fly()` uses (x forward, y
    left, z up, the origin on the floor below the shaft); with the robot `d` from the goal's center, that frame's
    origin is on the approach line `d - forward` from it."""

    def __init__(self, model: AimModel):
        g = model.goal
        assert g is not None
        self.model, self.surface = model, g.scoring_surface()
        n = self.surface.normal
        flat = np.array([n[0], n[1], 0.0])
        if np.linalg.norm(flat) > 1e-9:
            self.toward = flat / np.linalg.norm(flat)          # from the goal toward the robot
        else:
            h = math.radians(g.heading_deg)
            self.toward = -np.array([math.cos(h), math.sin(h), 0.0])
        self.forward_axis = -self.toward                        # the launcher's x on the field
        self.left = np.array([self.toward[1], -self.toward[0], 0.0])
        self.normal = np.array([n @ self.forward_axis, n @ self.left, n[2]])    # in the launcher frame
        self.height = float(self.surface.center[2])
        self.ahead = float((self.surface.center[:2] - np.array(g.pos)) @ self.toward[:2])
        self.upward = bool(n[2] < 0.0)
        self.radius = model.setup.ball.diameter / 2.0
        self._paths: dict[tuple[float, float], np.ndarray | None] = {}
        self._crossings: dict[tuple[float, float], dict[str, tuple[float, float]]] = {}
        self._curves: dict[tuple[float, float, float], list[float]] = {}

    def path(self, hood_deg: float, rps: float) -> np.ndarray | None:
        """The ball's path (fly_path's rows, the launcher frame) for this hood angle and wheel speed, flown until it
        is back down a radius below the surface center's height; None if it doesn't leave the launcher."""
        key = (hood_deg, rps)
        if key not in self._paths:
            setup = _setup(self.model, hood_deg, 0.0)
            shot = single_pass(setup, rps, self.model.lane, kP=AIM_KP)
            below = self.height - self.radius
            self._paths[key] = None if shot is None or shot.status != "launched" else fly_path(
                *launch_state(shot, setup), shot.spin, setup.ball, setup.air,
                stop=lambda row: row[6] < 0.0 and row[3] < below)
        return self._paths[key]

    def crossings(self, hood_deg: float, rps: float) -> dict[str, tuple[float, float]]:
        """Where (launcher x) the path first passes the surface center's height going "up" and coming "down", each
        with its speed along the normal there (negative: in the entry direction)."""
        key = (hood_deg, rps)
        if key not in self._crossings:
            out: dict[str, tuple[float, float]] = {}
            p = self.path(hood_deg, rps)
            if p is not None:
                z = p[:, 3] - self.height
                for k in np.flatnonzero((z[:-1] < 0.0) != (z[1:] < 0.0)):
                    branch = "up" if z[k] < 0.0 else "down"
                    f = z[k] / (z[k] - z[k + 1])
                    v = p[k, 4:7] + f * (p[k + 1, 4:7] - p[k, 4:7])
                    out.setdefault(branch, (float(p[k, 1] + f * (p[k + 1, 1] - p[k, 1])), float(v @ self.normal)))
            self._crossings[key] = out
        return self._crossings[key]

    def entry(self, hood_deg: float, rps: float, branch: str) -> float | None:
        """The angle's crossing curve at this wheel speed, on one branch: the launcher x of crossings()'s, when it
        is in the entry direction; None otherwise."""
        c = self.crossings(hood_deg, rps).get(branch)
        return None if c is None or c[1] >= 0.0 else c[0]

    def on_field(self, path: np.ndarray, distance: float) -> np.ndarray:
        """A path's centers (n, 3) on the field, the robot `distance` from the goal's center."""
        g = self.model.goal
        origin = np.array([g.pos[0], g.pos[1], 0.0]) + (distance - self.model.forward) * self.toward
        return (origin + path[:, 1:2] * self.forward_axis + path[:, 2:3] * self.left
                + path[:, 3:4] * np.array([0.0, 0.0, 1.0]))

    def row(self, hood_deg: float, rps: float, distance: float) -> AimRow | None:
        """The row for this shot from `distance`, if it scores: its first entry crossing of the surface's plane is at
        the center (within HIT_TOLERANCE, the lane's offset aside) and goal_fit.fit_ball passes it."""
        p = self.path(hood_deg, rps)
        if p is None:
            return None
        pts = self.on_field(p, distance)
        s = self.surface
        side = (pts - s.center) @ s.normal
        into = np.flatnonzero((side[:-1] > 0.0) & (side[1:] <= 0.0))
        if not len(into):
            return None
        k = int(into[0])
        f = side[k] / (side[k] - side[k + 1])
        at = pts[k] + f * (pts[k + 1] - pts[k])
        miss = at - s.center
        miss -= (miss @ self.left) * self.left
        if np.linalg.norm(miss) > HIT_TOLERANCE:
            return None
        if not fit_ball(pts[:k + 1], at, s, self.model.goal.name, self.model.boxes, self.radius).scored:
            return None
        t = float(p[k, 0] + f * (p[k + 1, 0] - p[k, 0]))
        return AimRow(distance, rps, hood_deg, t, float(p[:, 3].max()))    # the apex as if it flew on, as the hub's

    def curve(self, hood_deg: float, rps_range: tuple[float, float]) -> list[float]:
        """The wheel speeds an angle's crossing curve is sampled at, whatever the distance: the coarse grid, plus
        where a branch starts or stops. A branch starts where the ball's apex just reaches the surface center's
        height (both crossings appear there, together at the apex, and the curve turns back as the square root of the
        speed past it, so the grid alone misses the flattest shots), and stops where its crossing turns from the
        entry direction (a ball coming down onto a goal entered going up does so only just past its apex: a narrow
        window). Each edge is solved for on a smooth quantity, the apex or the speed along the normal, by regula
        falsi, and the curve gets the edge and a point just inside it."""
        key = (hood_deg, *rps_range)
        if key not in self._curves:
            lo, hi = rps_range
            points = [lo + k * GRID_RPS for k in range(int(math.floor((hi - lo) / GRID_RPS + 1e-9)) + 1)]
            if points[-1] < hi:
                points.append(hi)

            def above(rps: float) -> float | None:
                p = self.path(hood_deg, rps)
                return None if p is None else float(p[:, 3].max()) - self.height

            def edges(g: Callable[[float], float | None], tolerance: float, inside_negative: bool) -> list[float]:
                found = []
                ends = [(rps, g(rps)) for rps in sorted(points)]
                for (a, fa), (b, fb) in zip(ends, ends[1:]):
                    if fa is None or fb is None or (fa < 0.0) == (fb < 0.0):
                        continue
                    edge = _illinois(g, a, fa, b, fb, tolerance)
                    if edge is not None:
                        inside = a if (fa < 0.0) == inside_negative else b
                        found += [e for e in (edge, edge + math.copysign(RPS_TOLERANCE, inside - edge)) if a < e < b]
                return found
            points += edges(above, APEX_TOLERANCE, inside_negative=False)
            for branch in ("up", "down"):
                normal_speed = lambda rps, branch=branch: (c[1] if (c := self.crossings(hood_deg, rps).get(branch))
                                                           else None)
                points += edges(normal_speed, NORMAL_SPEED_TOLERANCE, inside_negative=True)
            self._curves[key] = sorted(set(points))
        return self._curves[key]

    def solve(self, hood_deg: float, distance: float, rps_range: tuple[float, float]) -> list[AimRow]:
        """The scoring rows of one hood angle from `distance`: every bracket of its crossing curve (curve()), each
        branch (up, down), solved, and kept if it scores."""
        grid = self.curve(hood_deg, rps_range)
        target = distance - self.model.forward - self.ahead
        rows = []
        for branch in ("up", "down"):
            def miss(rps: float) -> float | None:
                x = self.entry(hood_deg, rps, branch)
                return None if x is None else x - target
            ends = [(rps, miss(rps)) for rps in grid]
            for (a, fa), (b, fb) in zip(ends, ends[1:]):
                if fa is None or fb is None or (fa < 0.0) == (fb < 0.0):
                    continue
                rps = _illinois(miss, a, fa, b, fb)
                if rps is not None and (row := self.row(hood_deg, rps, distance)) is not None:
                    rows.append(row)
        return rows

    def solutions(self, distance: float, rps_range: tuple[float, float]) -> list[AimRow]:
        return [row for a in self.model.hood_angles for row in self.solve(a, distance, rps_range)]


def surface_path(model: AimModel, row: AimRow) -> np.ndarray:
    """A non-level goal's row's shot: rows of (t, x, y, z) on the field, from the exit until the ball is back down a
    radius below the surface center's height (or on the floor)."""
    if model.level:
        raise AimError(i18n.t("a level goal's rows are flown by {fly_row}", fly_row="fly_row"))
    aim = _SurfaceAim(model)
    p = aim.path(row.hood_deg, row.wheel_rps)
    if p is None:
        raise AimError(i18n.t("the shot at {distance} m does not leave the launcher", distance=f"{row.distance:g}"))
    return np.column_stack([p[:, 0], aim.on_field(p, row.distance)])


# ------------------------------------------------------------------------------------------------ output

def to_csv(rows: list[AimRow]) -> str:
    lines = [",".join((*ROW_FIELDS, "status"))]
    for r in rows:
        values = [f"{getattr(r, f):.4f}" if r.status == "ok" or f == "distance" else "" for f in ROW_FIELDS]
        lines.append(",".join((*values, r.status)))
    return "\n".join(lines) + "\n"


def to_java(rows: list[AimRow], class_name: str, command: str, label: str | None = None,
            surface: bool = False) -> str:
    """A `final` class with `ROWS`, one {distance, wheel_rps, hood_deg, flight_s, apex} row per distance. The header
    gives `command` as the command that made it, and `label` (the launcher's UNCALIBRATED line by default; the setup's
    own, calibration(model), says what was measured). `surface`: the rows aim at a non-level goal's opening."""
    from ..launcher.report import uncalibrated
    package, _, name = class_name.rpartition(".")
    body = ",\n".join(
        "        {" + ", ".join(f"{getattr(r, f):.4f}" for f in ROW_FIELDS) + "}" for r in rows if r.status == "ok")
    skipped = [f"        // {r.distance:g} m: unreachable, left out" for r in rows if r.status != "ok"]
    return "\n".join([
        "// Aiming table from scrimmage's launcher model. Each row is",
        "// {distance_m, wheel_rps, hood_deg, flight_s, apex_m}: robot center to goal center, the wheel speed and hood",
        *(["// angle whose ball crosses the goal's opening at its center from the side it is entered (the robot on",
            "// the goal's approach line), the time from exit to that crossing, and the ball's highest point (its",
            "// flight's, unobstructed)."]
          if surface else
          ["// angle whose ball crosses the goal's rim height descending at its center, the time from exit to that",
           "// crossing, and the ball's highest point."]),
        f"// Made by: {command}",
        f"// {uncalibrated() if label is None else label}",
        *([f"package {package};", ""] if package else []),
        f"public final class {name} {{",
        f"    private {name}() {{}}",
        "",
        "    public static final double[][] ROWS = {",
        body,
        *skipped,
        "    };",
        "}",
        ""])
