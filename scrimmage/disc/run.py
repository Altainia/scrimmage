"""A burst of discs: spin the wheels up from rest, feed discs one at a time by a rule, fly each launched disc to the
wall, and measure it.

Time starts at 0 with every shaft at rest. Feeding starts at `t_ready`, when every wheel group first reaches
`feed.tolerance` of its target (or at `feed.timeout` if one never does). Disc k is offered at t_ready + k / `feed.rate`;
the channel holds one disc at a time, so a disc offered while another is in the channel waits for it to leave. With
`feed.rule = "wait_for_speed"` it also waits for every group to be back within tolerance. A disc not fed by
`feed.timeout` after the first is offered is "unfed". The ball launcher's feed rules, without its random spread (a
disc has no lanes, and no spread figures are published) or its lanes.

Flight. `exit_to_flight_state` places an exit in the world: the launcher's origin is on the floor `mount.height` below
the disc's center, x downrange, y left, z up (the frame of disc/flight.py). The channel is tilted up by `tilt_deg`
about its cross axis (y), so in the channel's frame C
    x_C = (cos t, 0, sin t)   y_C = (0, 1, 0)   z_C = x_C x y_C = (-sin t, 0, cos t)
and the disc lies in C's x-y plane, its axis z_C. The exit velocity is speed (cos yaw, sin yaw) in that plane and the
spin, counter-clockwise seen from above with the channel level, is +z about the disc's axis (omega_body = (0, 0,
spin)). The robot's velocity is added to the disc's, as the ball launcher does. The disc starts at the exit: the
distance it traveled along the channel is not added.
The goal is a rectangle on the wall x = `goal.distance`, centered on the line (y = 0); a disc scores when its center
crosses it, going downrange.
"""

import dataclasses
import math
from dataclasses import dataclass

import numpy as np

from ..launcher.shaft import VelocityController
from ..testrange.spec import Surface
from .flight import DiscFlight, DiscState, fly, quat_from_matrix
from .inputs import DiscSetup, aero_set
from .launcher import DiscEnergy, DiscLauncher, ExitedDisc

IDLE_DT = 1e-3  # s: the shafts' step while no disc is in the channel, a Talon's 1 kHz velocity loop
MAX_TRACE = 2000  # points kept in `RunResult.trace`
GOAL = "goal"

METRIC_KEYS = (
    "exit_speed", "spin_rpm", "advance_ratio", "yaw_deg", "speed_drop_pct", "min_speed_pct", "burst_s", "flight_time",
    "apex_m", "landing_m", "wall_height_m", "wall_lateral_m", "scored", "fired", "launched", "jammed", "unfed",
)


@dataclass
class RunResult:
    shots: list[tuple[ExitedDisc, DiscFlight | None]]  # one per disc fed; a flight only for a launched disc
    trace: list[tuple[float, float]]  # (time s, wheel group 0's speed rps)
    metrics: dict[str, float]


def channel_exit(shot: ExitedDisc, mat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The exit velocity and the disc's orientation (w, x, y, z) for a channel whose frame C (x downrange, y left, z
    the disc's axis, the columns of `mat`) is turned by `mat` in the world: speed (cos yaw, sin yaw) in C's x-y plane,
    and the disc lying in that plane, its body frame C's. The spin is about C's z (its body z). The world's disc
    shooter (testrange/launch.py) uses it with the channel body's pose, the standalone with the tilt alone."""
    yaw = math.radians(shot.yaw_deg)
    vel = shot.speed * (math.cos(yaw) * mat[:, 0] + math.sin(yaw) * mat[:, 1])
    return vel, quat_from_matrix(mat)


def exit_to_flight_state(shot: ExitedDisc, setup: DiscSetup) -> DiscState:
    """The disc's state in the world at its exit (see the module docstring)."""
    tilt = math.radians(setup.mount.tilt_deg)
    st, ct = math.sin(tilt), math.cos(tilt)
    x_c, y_c, z_c = np.array([ct, 0.0, st]), np.array([0.0, 1.0, 0.0]), np.array([-st, 0.0, ct])
    vel, quat = channel_exit(shot, np.column_stack([x_c, y_c, z_c]))
    vel[0] += setup.robot.velocity[0]
    vel[1] += setup.robot.velocity[1]
    return DiscState(
        pos=np.array([0.0, shot.exit_xy[1], setup.mount.height]),
        vel=vel,
        quat=quat,
        omega_body=np.array([0.0, 0.0, shot.spin]),
    )


def goal_surface(setup: DiscSetup) -> Surface:
    """The goal's opening: a rectangle on the wall, normal facing the launcher. In-plane axes (up, left) give
    up x left = -x_hat, the normal."""
    g = setup.goal
    hz, hy = g.height / 2.0, g.width / 2.0
    return Surface(
        center=np.array([g.distance, 0.0, g.bottom + hz]),
        normal=np.array([-1.0, 0.0, 0.0]),
        axes=np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0]]),
        polygon=np.array([[-hz, -hy], [hz, -hy], [hz, hy], [-hz, hy]]),
    )


def _unfed(index: int) -> ExitedDisc:
    n = math.nan
    return ExitedDisc(index, "unfed", n, n, n, n, n, (n, n), DiscEnergy(0.0, 0.0, n, n, 0.0, 0.0, 0.0), 0.0)


def run_setup(setup: DiscSetup) -> RunResult:
    launcher = DiscLauncher(setup)
    shafts = launcher.shafts
    ctrls = [VelocityController(c, s) for c, s in zip(setup.controls, setup.shafts)]
    targets = [2.0 * math.pi * c.target_rps for c in setup.controls]  # rad/s
    feed = setup.feed
    wait = feed.rule == "wait_for_speed"
    dt = launcher.default_dt()
    idle = max(dt, IDLE_DT)

    times, speeds = [0.0], [shafts[0].speed]
    exits: list[ExitedDisc] = []
    low = [1.0] * len(shafts)  # each group's lowest speed / target since the feed began
    feeding = False

    def advance(step: float) -> None:
        exits.extend(launcher.step(step, [c.voltage(s) for c, s in zip(ctrls, shafts)]))
        times.append(launcher.time)
        speeds.append(shafts[0].speed)
        if feeding:
            for g, (s, w) in enumerate(zip(shafts, targets)):
                if w:
                    low[g] = min(low[g], s.speed / w)

    def at_speed() -> bool:
        return all(abs(s.speed - w) <= feed.tolerance * abs(w) for s, w in zip(shafts, targets))

    # Spin up from rest; the cap is the feed timeout, so wheels that can't get there still let the run end.
    while not at_speed() and launcher.time < feed.timeout:
        advance(idle)
    t_ready = launcher.time
    feeding = True
    deadline = t_ready + feed.timeout
    offered = [t_ready + k / feed.rate for k in range(feed.count)]
    pending = list(range(feed.count))
    inserted = 0
    first_in = None
    while pending:
        t = launcher.time
        if t >= deadline:
            break
        if not launcher.in_contact() and t >= offered[pending[0]] - 1e-12 and (not wait or at_speed()):
            launcher.insert(t)
            pending.pop(0)
            inserted += 1
            first_in = t if first_in is None else first_in
        elif launcher.in_contact():
            advance(dt)
        elif wait or offered[pending[0]] <= t:
            advance(idle)
        else:  # schedule rule, nothing in the channel: step up to the next offer
            advance(min(idle, max(offered[pending[0]] - t, 1e-9)))
    while launcher.in_contact():
        advance(dt)
    exits.extend(launcher.step(0.0, [0.0] * len(shafts)))  # a disc that finished at its insert

    aero = aero_set(setup.air.aero)
    surface = goal_surface(setup)
    shots: list[tuple[ExitedDisc, DiscFlight | None]] = []
    for e in sorted(exits, key=lambda e: e.index):
        flight = None
        if e.status == "launched":
            flight = fly(exit_to_flight_state(e, setup), setup.disc, aero, setup.air.density, ((GOAL, surface),))
        shots.append((e, flight))
    shots.extend((_unfed(inserted + j), None) for j in range(len(pending)))
    return RunResult(shots=shots, trace=_decimate(times, speeds),
                     metrics=_metrics(setup, shots, first_in, low))


def _decimate(times: list[float], speeds: list[float]) -> list[tuple[float, float]]:
    n = len(times)
    keep = list(range(0, n, max(1, math.ceil(n / MAX_TRACE))))
    if keep[-1] != n - 1:
        keep[-1] = n - 1
    return [(times[i], speeds[i] / (2.0 * math.pi)) for i in keep]


def _at_wall(flight: DiscFlight, distance: float) -> tuple[float, float]:
    """(height, lateral) of the center where its path first reaches x = distance; NaN if it never does."""
    x = flight.samples[:, 1]
    hit = np.nonzero(x >= distance)[0]
    if not len(hit) or hit[0] == 0:
        return math.nan, math.nan
    i = hit[0]
    f = (distance - x[i - 1]) / (x[i] - x[i - 1])
    a, b = flight.samples[i - 1], flight.samples[i]
    return float(a[3] + f * (b[3] - a[3])), float(a[2] + f * (b[2] - a[2]))


def _metrics(setup: DiscSetup, shots, first_in, low) -> dict[str, float]:
    nan = math.nan
    launched = [(s, f) for s, f in shots if s.status == "launched"]
    status = [s.status for s, _ in shots]
    r = setup.disc.radius

    def mean(values):
        values = [v for v in values if not math.isnan(v)]
        return float(np.mean(values)) if values else nan

    wall = [_at_wall(f, setup.goal.distance) for _, f in launched]
    exited = [s.t_exit for s, _ in launched]
    return {
        "exit_speed": mean([s.speed for s, _ in launched]),
        "spin_rpm": mean([s.spin * 60.0 / (2.0 * math.pi) for s, _ in launched]),
        # r Omega / V at the exit: 1 for a disc rolling along its fence, more when a rim push has spun it harder.
        "advance_ratio": mean([r * s.spin / s.speed for s, _ in launched]),
        "yaw_deg": mean([s.yaw_deg for s, _ in launched]),
        "speed_drop_pct": mean([100.0 * max((d for d in s.droop if not math.isnan(d)), default=nan)
                                for s, _ in launched]),
        "min_speed_pct": 100.0 * min(low) if launched else nan,
        "burst_s": max(exited) - first_in if exited else nan,
        "flight_time": mean([f.flight_time for _, f in launched]),
        "apex_m": mean([f.apex for _, f in launched]),
        "landing_m": mean([float(f.landing[0]) for _, f in launched if f.landing is not None]),
        "wall_height_m": mean([h for h, _ in wall]),
        "wall_lateral_m": mean([y for _, y in wall]),
        "scored": float(sum(f.crossing is not None for _, f in launched)),
        "fired": float(sum(1 for x in status if x != "unfed")),
        "launched": float(len(launched)),
        "jammed": float(status.count("jammed")),
        "unfed": float(status.count("unfed")),
    }


def exit_table(setup: DiscSetup, rpms: list[float]) -> list[dict[str, float]]:
    """One disc at each wheel speed: group 0 at `rpm` wheel rpm, any other group at its file ratio to group 0 (so a
    pinch's speed difference is kept). Rows: rpm, surface (m/s, group 0's wheel surface speed), exit_speed, ratio
    (exit_speed / surface), spin_rpm and advance_ratio."""
    base = setup.controls[0].target_rps
    one = dataclasses.replace(setup, feed=dataclasses.replace(setup.feed, count=1))
    rows = []
    for rpm in rpms:
        controls = tuple(dataclasses.replace(c, target_rps=rpm / 60.0 * (c.target_rps / base if base else 1.0))
                         for c in setup.controls)
        m = run_setup(dataclasses.replace(one, controls=controls)).metrics
        surface = 2.0 * math.pi * rpm / 60.0 * setup.wheels.diameter / 2.0
        rows.append({"rpm": rpm, "surface": surface, "exit_speed": m["exit_speed"],
                     "ratio": m["exit_speed"] / surface if surface else math.nan, "spin_rpm": m["spin_rpm"],
                     "advance_ratio": m["advance_ratio"]})
    return rows
