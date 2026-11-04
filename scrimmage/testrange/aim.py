"""An aiming table from the launcher model: for each robot-to-goal distance, the wheel speed and hood angle whose
ball crosses the goal's rim height, descending, at the goal's center.

The geometry comes from the config's assembled robot at its start pose (the launcher shaft's height and its forward
offset from the robot center, the robot facing the goal) and from the range's goal. The launcher is the model's own:
a ball fed at the setup's entry speed into the lane nearest the middle, the shaft held at the wheel speed by a
velocity loop (kP AIM_KP, a stand-in for the robot's own gains: the dip a ball causes is recovered at that rate),
then flown by `fly()`. The robot is taken as stationary and the ball's path unobstructed (the rim's edge and the
funnel are not modeled); a lane off the middle lands that far to the side. An estimate from the launcher model's
preset numbers, so UNCALIBRATED.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np

from ..config import load_config
from ..launcher.flight import Landing, fly
from ..launcher.inputs import Setup
from ..launcher.single import single_pass
from ..parts.assemble import assemble
from .shooter import parse_shooter

AIM_KP = 0.5            # V per wheel rps of error: the velocity loop holding the shaft while a ball passes
RPS_TOLERANCE = 0.01    # the wheel speed is solved to this (rps)
HIT_TOLERANCE = 0.02    # m: the solved shot must cross the goal height this close to the goal center
ROW_FIELDS = ("distance", "wheel_rps", "hood_deg", "flight_s", "apex")
UNREACHABLE = "unreachable"


class AimError(ValueError):
    """A config, goal or distance the table cannot be made for."""


@dataclass(frozen=True)
class AimRow:
    distance: float     # m, robot center to goal center, horizontal
    wheel_rps: float
    hood_deg: float     # exit angle above horizontal
    flight_s: float     # s from the ball's exit to the rim-height crossing
    apex: float         # m, the ball center's highest point
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


def load_model(config_path: Path, goal: str, hood_step_deg: float = 2.0) -> AimModel:
    cfg = load_config(config_path)
    if "shooter" not in cfg.raw:
        raise AimError(f"{config_path}: no [shooter]: the aiming table is made from the robot's launcher")
    try:
        built = assemble(cfg.raw, cfg.dir)
        shooter = parse_shooter(cfg.raw, built, cfg.dir)
    except ValueError as e:     # PartError is one
        raise AimError(f"{config_path}: {e}") from None
    if shooter.kind == "disc":
        raise AimError(f"{config_path}: [shooter] kind = \"disc\": the aiming table is made from a ball launcher; "
                       "for a disc shooter's exit speeds use `scrimmage disc table` and `scrimmage disc run`")
    goals = {g.name: g for g in built.range.goals}
    if goal not in goals:
        raise AimError(f"goal {goal!r} isn't in range {built.range.name!r}; its goals: {', '.join(goals)}")
    g = goals[goal]

    m = built.model
    data = mujoco.MjData(m)
    mujoco.mj_kinematics(m, data)
    robot, base = m.body(built.robot_body).id, m.body(f"{shooter.part}.base").id
    local = data.xmat[robot].reshape(3, 3).T @ (data.xpos[base] - data.xpos[robot])
    facing = data.xmat[robot].reshape(3, 3).T @ data.xmat[base].reshape(3, 3)[:, 0]   # launcher x in the robot frame
    if facing[0] <= 0.0:
        raise AimError(f"the launcher part {shooter.part!r} faces backward (its x axis points {facing[0]:+.2f} along "
                       "the robot's): the table assumes it shoots where the robot faces")

    if shooter.hood == "fixed":
        angles = (shooter.hood_angle,)
    else:
        lo, hi = shooter.hood_range
        angles = tuple(lo + k * hood_step_deg for k in range(int(math.floor((hi - lo) / hood_step_deg + 1e-9)) + 1))
    return AimModel(setup=shooter.setup, lane=min(shooter.lanes, key=abs), rim_height=g.rim_height,
                    forward=float(local[0]), hood_angles=angles)


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
    """The landing of the shot a row prescribes (the goal at the row's distance)."""
    landing = _land(model, row.hood_deg, row.wheel_rps, row.distance)
    if landing is None:
        raise AimError(f"the shot at {row.distance:g} m does not leave the launcher")
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
        raise AimError("no hood angle in range gets a ball to the rim height at any wheel speed in "
                       f"{rps_range[0]:g}..{rps_range[1]:g} rps")
    return (min(lows) if lows else min(highs)), max(highs)


HOOD_PER_RPS = 2.0      # deg of hood counted as 1 rps of wheel speed when a row is matched to the one before it


def choose(found: list[AimRow], previous: AimRow | None) -> AimRow | None:
    """The row to keep among a distance's solutions (one per hood angle that reaches it). The first reachable distance
    takes the highest apex (the steepest entry; ties: the lower wheel speed). Each later one takes the solution nearest
    the previous reachable row's: wheel speed and hood angle together, HOOD_PER_RPS degrees counting as 1 rps (ties:
    the higher apex). Where the launcher is grip-limited the exit speed hardly changes with wheel speed, so the
    highest apex can sit anywhere on that plateau (rows of 57 rps beside 79 and 99); the nearest wheel speed alone
    jumps the hood instead (70 deg beside 40, a flat shot just clearing the rim). Staying near the previous row in
    both keeps the table smooth to interpolate."""
    if not found:
        return None
    if previous is None:
        return max(found, key=lambda r: (r.apex, -r.wheel_rps))
    return min(found, key=lambda r: (abs(r.wheel_rps - previous.wheel_rps)
                                     + abs(r.hood_deg - previous.hood_deg) / HOOD_PER_RPS, -r.apex))


def aim_table(config_path: Path, goal: str, distances: list[float], *, hood_step_deg: float = 2.0,
              rps_range: tuple[float, float] = (10.0, 120.0),
              on_unreachable: Callable[[str], None] | None = None) -> list[AimRow]:
    """A row per distance. A distance nothing reaches gets an UNREACHABLE row (NaN values), and `on_unreachable` is
    called once with a message naming them and the reachable range; AimError only when none is reachable."""
    if not distances:
        raise AimError("no distances")
    if hood_step_deg <= 0.0:
        raise AimError(f"hood_step_deg must be positive, not {hood_step_deg:g}")
    model = load_model(config_path, goal, hood_step_deg)
    rows = []
    previous = None     # the last reachable row
    for d in distances:
        found = [row for a in model.hood_angles if (row := _solve(model, a, d - model.forward, rps_range)) is not None]
        best = choose(found, previous)
        if best is not None:
            previous = best
        nan = math.nan
        rows.append(dataclasses.replace(best, distance=d) if best is not None
                    else AimRow(d, nan, nan, nan, nan, UNREACHABLE))
    missed = [r.distance for r in rows if r.status == UNREACHABLE]
    if missed:
        near, far = reachable(model, rps_range)
        msg = (f"{', '.join(f'{d:g}' for d in missed)} m can't be reached with wheels at {rps_range[0]:g}.."
               f"{rps_range[1]:g} rps and hood angles {model.hood_angles[0]:g}..{model.hood_angles[-1]:g} deg to the "
               f"{goal} rim height: reachable distances are about {near:.2f} to {far:.2f} m")
        if len(missed) == len(rows):
            raise AimError(msg)
        if on_unreachable is not None:
            on_unreachable(msg)
    return rows


# ------------------------------------------------------------------------------------------------ output

def to_csv(rows: list[AimRow]) -> str:
    lines = [",".join((*ROW_FIELDS, "status"))]
    for r in rows:
        values = [f"{getattr(r, f):.4f}" if r.status == "ok" or f == "distance" else "" for f in ROW_FIELDS]
        lines.append(",".join((*values, r.status)))
    return "\n".join(lines) + "\n"


def to_java(rows: list[AimRow], class_name: str, command: str) -> str:
    """A `final` class with `ROWS`, one {distance, wheel_rps, hood_deg, flight_s, apex} row per distance. The header
    gives `command` as the command that made it."""
    from ..launcher.report import UNCALIBRATED
    package, _, name = class_name.rpartition(".")
    body = ",\n".join(
        "        {" + ", ".join(f"{getattr(r, f):.4f}" for f in ROW_FIELDS) + "}" for r in rows if r.status == "ok")
    skipped = [f"        // {r.distance:g} m: unreachable, left out" for r in rows if r.status != "ok"]
    return "\n".join([
        "// Aiming table from scrimmage's launcher model. Each row is",
        "// {distance_m, wheel_rps, hood_deg, flight_s, apex_m}: robot center to goal center, the wheel speed and hood",
        "// angle whose ball crosses the goal's rim height descending at its center, the time from exit to that",
        "// crossing, and the ball's highest point.",
        f"// Made by: {command}",
        f"// {UNCALIBRATED}",
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
