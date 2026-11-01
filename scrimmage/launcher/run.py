"""A burst: spin the flywheel up from rest, feed balls by a rule, fly each launched ball, and measure it.

Time starts at 0 with the shaft at rest. Feeding starts at `t_ready`, when the shaft first reaches `feed.tolerance`
of its target (or at `feed.timeout` if it never does). `FedBall.available_at` is relative to `t_ready`, and
`feed.timeout` counts from the first ball's availability.
"""

import dataclasses
import math
from dataclasses import dataclass

import numpy as np

from .flight import Landing, fly
from .inputs import Setup, check_lane
from .model import Energy, ExitedBall, FedBall, Launcher
from .shaft import VelocityController

# While no ball is in contact the shaft is stepped this coarsely (s), the period of a Talon's 1 kHz velocity loop;
# with balls in contact the step is the launcher's own, much finer one.
IDLE_DT = 1e-3
MAX_TRACE = 2000  # points kept in `RunResult.trace`

METRIC_KEYS = (
    "exit_speed", "exit_speed_sd", "spin_rpm", "exit_slip_pct", "launch_deg", "speed_drop_pct", "min_speed_pct",
    "recovery_s", "burst_s", "spread_downrange_m", "spread_lateral_m", "scored", "fired", "launched", "jammed", "unfed",
)


@dataclass
class RunResult:
    shots: list[tuple[ExitedBall, Landing | None]]
    trace: list[tuple[float, float]]  # (time s, shaft rps)
    metrics: dict[str, float]


def generate_feed(setup: Setup, rng) -> list[FedBall]:
    """`feed.count` balls, one availability time per 1 / rate, one ball per lane at each time. The feed-time jitter
    is drawn once per time slot, shared by its lanes, so side-by-side balls stay together. Draw order is fixed (per
    slot: feed time; then per ball: diameter, mass, stiffness, lane, entry speed, yaw), so a seed repeats."""
    feed, spread, base = setup.feed, setup.spread, setup.ball
    lanes = feed.lanes
    out = []
    for group in range(math.ceil(feed.count / len(lanes))):
        available = max(0.0, group / feed.rate + spread.feed_time * rng.standard_normal())
        for lane in lanes[: feed.count - group * len(lanes)]:
            d, m, k, ln, v, yaw = rng.standard_normal(6)
            ball = dataclasses.replace(
                base,
                diameter=base.diameter * (1.0 + spread.diameter * d),
                mass=base.mass * (1.0 + spread.mass * m),
                stiffness_n=base.stiffness_n * (1.0 + spread.stiffness * k),
            )
            out.append(FedBall(
                available_at=available,
                lane=lane + spread.lane * ln,
                entry_speed=max(0.0, feed.entry_speed * (1.0 + spread.entry_speed * v)),
                ball=ball,
                yaw_deg=spread.yaw_deg * yaw,
            ))
    return out


def _unfed(index: int, fed: FedBall) -> ExitedBall:
    n = math.nan
    return ExitedBall(index, "unfed", n, n, n, n, n, fed.yaw_deg, fed.lane, Energy(0.0, 0.0, n, n, 0.0, 0.0, 0.0))


def run_setup(setup: Setup, feed: list[FedBall] | None = None) -> RunResult:
    rng = np.random.default_rng(setup.spread.seed)
    if feed is None:
        feed = generate_feed(setup, rng)
    else:  # a supplied hopper feed is input too: each ball must fit between the side plates
        for i, fb in enumerate(feed):
            check_lane(fb.lane, fb.ball.diameter, setup.launcher.width, f"hopper feed ball {i}: lane")
    feed = sorted(feed, key=lambda f: f.available_at)  # stable: lanes keep their order
    # Per-ball shooter-speed offsets, drawn after the feed so a supplied hopper feed changes nothing else.
    offsets = setup.spread.shooter_speed * rng.standard_normal(len(feed))

    launcher = Launcher(setup)
    shaft = launcher.shaft
    control = setup.control
    nominal = 2.0 * math.pi * control.target_rps  # rad/s
    tol = setup.feed.tolerance
    dt = launcher.default_dt()
    idle = max(dt, IDLE_DT)
    ctrl = VelocityController(control, setup.shaft)

    times = [0.0]
    speeds = [shaft.speed]
    exits: list[ExitedBall] = []

    def advance(step: float) -> None:
        exits.extend(launcher.step(step, ctrl.voltage(shaft)))
        times.append(launcher.time)
        speeds.append(shaft.speed)

    target = nominal  # the controller's current target (rad/s): nominal until a ball brings its own offset

    def at_speed() -> bool:
        return abs(shaft.speed - target) <= tol * abs(target)

    # Spin up from rest. The cap is the feed timeout: a shaft that cannot get there still lets the run end.
    while not at_speed() and launcher.time < setup.feed.timeout:
        advance(idle)
    t_ready = launcher.time

    # An empty supplied feed spins up and ends: no shots, NaN averages, zero counts.
    deadline = t_ready + (feed[0].available_at if feed else 0.0) + setup.feed.timeout
    pending = list(enumerate(feed))
    inserted: list[FedBall] = []
    entries: list[tuple[float, int]] = []  # (time, trace index at the entry) per inserted ball
    targets: list[float] = []  # the target in effect from each inserted ball's entry (rad/s)
    last_entry: list[tuple[float, float]] = []  # (lane, time) of the latest entry per lane
    wait = setup.feed.rule == "wait_for_speed"
    unfed: list[tuple[int, FedBall]] = []

    def lane_free_at(fb: FedBall) -> float:
        # A lane holds one ball at a time: the next enters once the last has moved a ball diameter along it at its
        # entry speed. Entries less than a ball radius apart across the shaft are the same lane (modeling choice).
        times_in_lane = [t0 for lane, t0 in last_entry if abs(lane - fb.lane) < fb.ball.diameter / 2.0]
        if not times_in_lane:
            return -math.inf
        return max(times_in_lane) + fb.ball.diameter / max(fb.entry_speed, 1e-9)

    while True:
        t = launcher.time
        if t >= deadline and pending:
            unfed = pending
            pending = []
        blocked: list[float] = []  # lanes with an earlier ball still waiting: balls keep their order in a lane
        waiting = []
        ready_at = [deadline]  # when the first waiting ball of each lane could next enter
        for idx, fb in pending:
            due = t >= t_ready + fb.available_at - 1e-12 and t >= lane_free_at(fb) - 1e-12
            if due and (not wait or at_speed()) and not any(abs(b - fb.lane) < fb.ball.diameter / 2.0 for b in blocked):
                # The ball's own shooter-speed offset is the target until the next ball enters. It takes effect at
                # this ball's entry, so the wheel barely moves toward it during this pass: it mostly changes the
                # speed the next ball meets (a modeling choice, documented under [spread] in docs/launcher.md).
                target = nominal * (1.0 + offsets[idx])
                ctrl = VelocityController(dataclasses.replace(control, target_rps=target / (2.0 * math.pi)), setup.shaft)
                done = launcher.insert(fb, t)
                if done is not None:  # no squeeze or no wheel: finished at once
                    exits.append(done)
                inserted.append(fb)
                entries.append((t, len(times) - 1))
                targets.append(target)
                last_entry.append((fb.lane, t))
            else:
                if not any(abs(b - fb.lane) < fb.ball.diameter / 2.0 for b in blocked):
                    ready_at.append(max(t_ready + fb.available_at, lane_free_at(fb)))
                blocked.append(fb.lane)
                waiting.append((idx, fb))
        pending = waiting
        if not pending and launcher.in_contact() == 0:
            break
        if launcher.in_contact():
            advance(dt)
        elif wait:
            advance(idle)
        else:  # schedule rule, nothing in contact: step up to the next availability, lane gate or the deadline
            advance(min(idle, max(min(ready_at) - t, 1e-9)))
    # Let the last dip recover (bounded by the feed timeout) so its recovery time can be measured.
    t_end = launcher.time + setup.feed.timeout
    while inserted and launcher.time < t_end and speeds[-1] < (1.0 - setup.results.recovery_tolerance) * target:
        advance(idle)

    shots: list[tuple[ExitedBall, Landing | None]] = []
    for shot in sorted(exits, key=lambda e: e.index):
        landing = fly(shot, setup, inserted[shot.index].ball) if shot.status == "launched" else None
        shots.append((shot, landing))
    for j, (_, fb) in enumerate(unfed):
        shots.append((_unfed(len(inserted) + j, fb), None))

    return RunResult(
        shots=shots,
        trace=_decimate(times, speeds),
        metrics=_metrics(setup, shots, entries, targets, np.array(times), np.array(speeds), nominal),
    )


def _decimate(times: list[float], speeds: list[float]) -> list[tuple[float, float]]:
    n = len(times)
    stride = max(1, math.ceil(n / MAX_TRACE))
    keep = list(range(0, n, stride))
    if keep[-1] != n - 1:
        keep[-1] = n - 1  # the last point is kept; MAX_TRACE is not exceeded
    return [(times[i], speeds[i] / (2.0 * math.pi)) for i in keep]


def _metrics(setup, shots, entries, targets, t, w, nominal) -> dict[str, float]:
    nan = math.nan
    launched = [(s, ld) for s, ld in shots if s.status == "launched"]
    status = [s.status for s, _ in shots]

    def mean(values):
        return float(np.mean(values)) if len(values) else nan

    def sd(values):
        return float(np.std(values)) if len(values) else nan

    # Speed drop, per launched ball: the shaft's speed at its entry less the lowest speed from its entry to the first
    # trace sample at or after its exit, as a percentage of the target then in effect. Recovery: from that lowest
    # point until the shaft is within the recovery tolerance of the target, counted only for balls whose dip recovers
    # before the next ball enters (the last ball always counts). The lowest speed over the whole burst is
    # `min_speed_pct` of the nominal target.
    drops, recoveries = [], []
    entry_times = sorted({e for e, _ in entries})
    low = nominal
    for s_, _ in launched:
        t_in, i = entries[s_.index]
        stop = int(np.searchsorted(t, s_.t_exit, side="left"))  # first sample at or after the exit
        stop = min(stop, len(w) - 1)
        m = i + int(np.argmin(w[i:stop + 1]))
        goal = targets[s_.index]
        drops.append(100.0 * (w[i] - w[m]) / goal)
        low = min(low, w[m])
        back = np.nonzero(w[m:] >= (1.0 - setup.results.recovery_tolerance) * goal)[0]
        next_in = next((e for e in entry_times if e > t_in + 1e-12), math.inf)
        if len(back) and t[m + back[0]] <= next_in:
            recoveries.append(float(t[m + back[0]] - t[m]))

    exited = [s.t_exit for s, _ in launched]
    burst = max(exited) - min(e for e, _ in entries) if exited else nan
    # Landing spread: where the balls cross the goal's height on the way down, if every launched ball does; else
    # where every ball reaches the floor. One set of points either way: mixing the two would measure the distance
    # between the sets, not the spread.
    if all(ld.at_goal is not None for _, ld in launched):
        spots = [ld.at_goal for _, ld in launched]
    else:
        spots = [ld.floor for _, ld in launched]
    xs = [p[0] for p in spots]
    ys = [p[1] for p in spots]
    return {
        "exit_speed": mean([s.speed for s, _ in launched]),
        "exit_speed_sd": sd([s.speed for s, _ in launched]),
        "spin_rpm": mean([s.spin * 60.0 / (2.0 * math.pi) for s, _ in launched]),
        # The wheel's slip under the ball at the exit, % of its surface speed: near 0 the exit speed follows the
        # wheel's; large, the pass is grip-limited (more squeeze, coverage, wrap or wheel grip buys speed, RPM little).
        "exit_slip_pct": mean([100.0 * s.exit_slip for s, _ in launched]),
        "launch_deg": mean([s.launch_deg for s, _ in launched]),
        "speed_drop_pct": mean(drops),
        "min_speed_pct": 100.0 * low / nominal if launched else nan,
        "recovery_s": mean(recoveries),
        "burst_s": burst,
        "spread_downrange_m": sd(xs),
        "spread_lateral_m": sd(ys),
        "scored": float(sum(ld.scored for _, ld in launched)),
        "fired": float(sum(1 for x in status if x != "unfed")),
        "launched": float(len(launched)),
        "jammed": float(status.count("jammed")),
        "unfed": float(status.count("unfed")),
    }
