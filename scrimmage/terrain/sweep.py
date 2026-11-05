"""`terrain sweep`: drive a robot straight over one field element at each of several speeds, with no robot program,
and find the fastest speed it takes the element safely: the speed limit a team puts in its auto (docs/terrain.md).

Each run:
- puts the robot at the range's [start.approach] when that start faces the element, else APPROACH m before the
  element's near toe, facing it along the element's x;
- holds the `holds` joints at their values (an elevator up), lets the robot settle for SETTLE_S, then sets it
  rolling at the speed, which StraightDriver (drive.py) keeps;
- ends AFTER_S after the robot's CoM passes the element's far toe, or RUN_S after it started rolling.

A row reports the run's peak ground gap, longest air time, landing, the pitch and roll of largest magnitude (sign
kept, the Pigeon 2's convention), whether the robot tipped or was stranded (scrimmage/terrain/monitor.py), whether
it landed on balls, and whether it crossed the element at all. A row passes when the robot crossed, never tipped or
was stranded, its peak gap is at most the gap limit and its peak tilt (robot.tilt: the chassis's up from vertical,
pitch and roll together) at most the tilt limit.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

import mujoco
import numpy as np

from .. import i18n
from ..config import build_world, load_config
from ..protocol import HalState
from ..testrange.elements import Ramp, footprint
from .drive import StraightDriver

APPROACH = 1.5         # m: the start's robot center before the element's near toe, without a [start.approach]
APPROACH_START = "approach"
SETTLE_S = 0.3         # s at rest on the treads before the run starts rolling
RUN_S = 6.0            # s: a run ends this long after it starts rolling, if not before
AFTER_S = 1.5          # s: or this long after the robot's CoM passes the element's far toe
BALL_LANDING_S = 0.3   # s: a ball beneath the robot this soon after it was airborne is a landing on balls
BALL_UNDER_DEG = 45.0  # deg: a ball touching the robot with the contact's normal this close to vertical is beneath it
BALL_UNDER_COS = math.cos(math.radians(BALL_UNDER_DEG))


class SweepError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    gap: float = 0.05      # m: the peak ground gap allowed
    tilt: float = 30.0     # deg: the peak robot.tilt allowed (the chassis's up from vertical)


@dataclass(frozen=True)
class Row:
    speed: float               # m/s
    peak_gap: float            # m: robot.ground_gap's peak
    air_time: float            # s: the longest time touching nothing
    landing: float | None      # m: the CoM past the crest as the run's longest flight touches down; None: no air
    peak_pitch: float          # deg, the largest magnitude, its sign kept (nose down positive)
    peak_roll: float           # deg, likewise (left side up positive)
    peak_tilt: float           # deg: robot.tilt's peak, the chassis's up from vertical (pitch and roll together)
    tipped: bool
    stranded: bool
    landed_on_balls: bool
    crossed: bool = True       # the CoM passed the far toe: False when the robot stopped against the element

    def passes(self, limits: Limits) -> bool:
        return (self.crossed and not self.tipped and not self.stranded and self.peak_gap <= limits.gap
                and self.peak_tilt <= limits.tilt)


@dataclass(frozen=True)
class SweepResult:
    element: str
    holds: dict[str, float]
    limits: Limits
    rows: list[Row] = field(default_factory=list)
    limit_speed: float | None = None    # the fastest speed whose row, and every slower row, passes


def limit_speed(rows: list[Row], limits: Limits) -> float | None:
    best = None
    for r in sorted(rows, key=lambda r: r.speed):
        if not r.passes(limits):
            break
        best = r.speed
    return best


def sweep(config_path, element: str, speeds: list[float], holds: dict[str, float], limits: Limits) -> SweepResult:
    """Drive the config's robot over `element` of its range at each speed (m/s), the `holds` joints held."""
    world = build_world(load_config(config_path))
    rng = world.parts.range if world.parts is not None else None
    if rng is None or not rng.elements:
        raise SweepError(i18n.t("{config_path}: the sweep needs a [range] whose range file has field elements "
                                "([[element]])", config_path=config_path))
    by_name = {e.name: e for e in rng.elements}
    if element not in by_name:
        raise SweepError(i18n.t("no element {element} in range {name}; its elements: {elements}",
                                element=repr(element), name=repr(rng.name), elements=', '.join(by_name)))
    names = world.joint_states()
    for n in holds:
        if n not in names:
            raise SweepError(i18n.t("no joint {joint} to hold with --set; the robot's joints: {names}",
                                    joint=repr(n), names=', '.join(sorted(names))))
    try:
        driver = StraightDriver(world, 0.0, holds)
    except ValueError as e:
        raise SweepError(str(e)) from None
    course = _Course(world, by_name[element], rng.starts.get(APPROACH_START))
    rows = [_run(world, driver, course, float(s), holds) for s in speeds]
    return SweepResult(element, dict(holds), limits, rows, limit_speed(rows, limits))


class _Course:
    """The run's start, and the element along the robot's path: distances along the start's heading from the
    element's center to its near and far toes and its crest."""

    def __init__(self, world, e, approach):
        c = np.asarray(e.center, dtype=float)
        u = np.array([math.cos(math.radians(e.yaw_deg)), math.sin(math.radians(e.yaw_deg))])
        corners = footprint(e)
        if approach is not None and _faces(approach, c, corners):
            self.start = tuple(approach)
        else:
            depth = float(np.max((corners - c) @ u))
            x, y = c - (depth + APPROACH) * u
            self.start = (float(x), float(y), e.yaw_deg)
        h = math.radians(self.start[2])
        self.dir = np.array([math.cos(h), math.sin(h)])
        along = (corners - c) @ self.dir
        self.center = c
        self.far = float(np.max(along))
        # The crest: a bump's ridge, a two-sided ramp's or a platform's middle; a one-sided ramp's high edge (its
        # local +x end, where its deck drops off).
        self.crest = 0.0
        if isinstance(e, Ramp) and not e.two_sided:
            self.crest = float(np.max((corners - c) @ u) * (u @ self.dir))

    def along(self, p) -> float:
        return float((np.asarray(p[:2]) - self.center) @ self.dir)


def _faces(start, c, corners) -> bool:
    """The start's heading line passes through the element, ahead of it."""
    h = math.radians(start[2])
    d = np.array([math.cos(h), math.sin(h)])
    rel = corners - np.asarray(start[:2])
    ahead = rel @ d
    side = rel @ np.array([-d[1], d[0]])
    return bool(np.all(ahead > 0.0) and side.min() < 0.0 < side.max())


def _run(world, driver: StraightDriver, course: _Course, speed: float, holds: dict[str, float]) -> Row:
    m, d = world.model, world.data
    hal = HalState()
    world.external = None
    world.reset()
    world.set_robot_pose(*course.start)
    for name, value in holds.items():
        d.qpos[m.jnt_qposadr[m.joint(name).id]] = value
    mujoco.mj_forward(m, d)
    driver.speed = 0.0
    world.external = driver
    dt = float(m.opt.timestep)
    for _ in range(round(SETTLE_S / dt)):
        world.step(hal)
    driver.speed = speed
    driver.launch(d)
    mon = world.terrain_monitor()
    mon.reset()
    root = int(m.body_rootid[world.robot_body])
    robot = (m.body_rootid[m.geom_bodyid] == root).tolist()
    balls = np.zeros(m.ngeom, dtype=bool)
    layout = world.parts.layout
    for name in (layout.ball_names if layout is not None else []):
        b = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, name)
        if b >= 0:
            balls[m.geom_bodyid == b] = True
    balls = balls.tolist()

    gap = air = pitch = roll = tilt = 0.0
    tipped = stranded = on_balls = False
    landing = None
    last_air = -math.inf
    flight = longest = 0.0
    t_far = None
    t0 = float(d.time)
    while True:
        mon.consume()
        world.step(hal)
        t = float(d.time) - t0
        v = mon.sample()
        gap = max(gap, v["robot.ground_gap"])
        a = v["robot.air_time"]
        air = max(air, a)
        if abs(v["robot.pitch"]) > abs(pitch):
            pitch = v["robot.pitch"]
        if abs(v["robot.roll"]) > abs(roll):
            roll = v["robot.roll"]
        tilt = max(tilt, v["robot.tilt"])
        tipped |= bool(v["robot.tipped"])
        stranded |= bool(v["robot.stranded"])
        s = course.along(d.subtree_com[root])
        if a > 0.0:
            flight, last_air = a, t
        elif flight > 0.0:
            if flight > longest:        # the jump, not a hop at the toe lip or off the climb before the crest
                longest, landing = flight, s - course.crest
            flight = 0.0
        if not on_balls and t - last_air <= BALL_LANDING_S and d.ncon:
            on_balls = _ball_beneath(d, balls, robot)
        if t_far is None and s > course.far:
            t_far = t
        if t >= RUN_S - 1e-9 or (t_far is not None and t - t_far >= AFTER_S - 1e-9):
            break
    world.external = None
    return Row(speed=speed, peak_gap=gap, air_time=air, landing=landing, peak_pitch=pitch, peak_roll=roll,
               peak_tilt=tilt, tipped=tipped, stranded=stranded, landed_on_balls=on_balls, crossed=t_far is not None)


def _ball_beneath(d, balls: list[bool], robot: list[bool]) -> bool:
    """A ball touches the robot from below: the contact's normal, ball to robot, within BALL_UNDER_DEG of up. A ball
    the bumper pushes along the carpet touches it side-on, and doesn't count."""
    n = d.contact.frame[:d.ncon, 2].tolist()            # the normal's z, from geom1 to geom2
    for k, (g1, g2) in enumerate(d.contact.geom[:d.ncon].tolist()):
        if balls[g1] and robot[g2] and n[k] >= BALL_UNDER_COS:
            return True
        if balls[g2] and robot[g1] and -n[k] >= BALL_UNDER_COS:
            return True
    return False


# ---- Output ------------------------------------------------------------------------------------------------------

CSV_FIELDS = ("speed", "peak_gap", "air_time", "landing", "peak_pitch", "peak_roll", "peak_tilt", "tipped", "stranded",
              "landed_on_balls", "crossed", "pass")


def _cells(r: Row, limits: Limits) -> dict[str, str]:
    def yes(b: bool) -> str:
        return "yes" if b else "no"
    return {"speed": f"{r.speed:g}", "peak_gap": f"{r.peak_gap:.3f}", "air_time": f"{r.air_time:.3f}",
            "landing": "" if r.landing is None else f"{r.landing:.2f}", "peak_pitch": f"{r.peak_pitch:.1f}",
            "peak_roll": f"{r.peak_roll:.1f}", "peak_tilt": f"{r.peak_tilt:.1f}", "tipped": yes(r.tipped), "stranded": yes(r.stranded),
            "landed_on_balls": yes(r.landed_on_balls), "crossed": yes(r.crossed), "pass": yes(r.passes(limits))}


def write_csv(result: SweepResult, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in result.rows:
            w.writerow(_cells(r, result.limits))


def limit_line(result: SweepResult) -> str:
    lim = result.limits
    rule = f"gap <= {lim.gap:g} m, tilt <= {lim.tilt:g} deg, crossed, never tipped or stranded"
    if result.limit_speed is None:
        return f"speed limit over {result.element}: none, the slowest speed fails ({rule})"
    return f"speed limit over {result.element}: {result.limit_speed:g} m/s ({rule})"


def format_markdown(result: SweepResult, config: str) -> str:
    held = ", ".join(f"{k} = {v:g}" for k, v in result.holds.items()) or "nothing"
    head = ("| speed (m/s) | peak gap (m) | air time (s) | landing (m) | peak pitch (deg) | peak roll (deg) "
            "| peak tilt (deg) "
            "| tipped | stranded | landed on balls | crossed | pass |")
    lines = [f"# terrain sweep: {result.element}", "", f"Config: `{config}`. Held: {held}.", "", head,
             "|" + "---|" * len(CSV_FIELDS)]
    for r in result.rows:
        lines.append("| " + " | ".join(_cells(r, result.limits).values()) + " |")
    lines += ["", f"**{limit_line(result)}**", ""]
    return "\n".join(lines)
