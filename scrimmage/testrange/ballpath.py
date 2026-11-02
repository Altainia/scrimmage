"""The balls' path inside the robot: the intake zone, the hopper's lanes, the feeder stages and the hand-off to the
launcher model, and the beam breaks along it.

Inside the robot a ball is bookkeeping, not a free body: its body is parked (state `robot`) and BallPath tracks where
it is. Each lane is a single file: the hopper (a queue, the front ball next to go), then the stages in order, each
holding balls at an arc length s from 0 (its hopper side) to its path_length, then the launcher model.

- **Lanes.** A collected or preloaded ball goes to the lane holding fewest balls (the lowest index on a tie) and keeps
  it to the launcher.
- **Stages.** A stage's surface speed is its spin joint's speed times its diameter / 2 (signed: positive carries balls
  toward the launcher; slower than STOPPED_SPEED either way, it is stopped). Every ball on a stage moves at that
  speed, but never closer than one ball diameter to the ball ahead of it in its lane (measured along the whole path,
  across stage boundaries); it stops behind it. The feeding load on the stage motors is not modeled: the rollers turn
  as if the balls weren't there.
- **Forward.** A ball reaching the end of stage k enters stage k + 1 at 0 when that entry is clear, else it waits at
  the end; the hopper releases a lane's front ball onto the first stage at 0 while that stage runs forward and its
  entry is clear. A ball reaching the end of the last stage is fed to the launcher model (`FedBall`, at the stage's
  surface speed) when no launcher ball in its lane is still within one diameter of the entry, else it waits.
- **Reverse.** A ball pushed below 0 on stage k goes to the end of stage k - 1, or back to the front of its hopper
  lane from the first stage; if that place is blocked it stops at 0. A ball in the launcher model never comes back.
  A ball resting at a boundary (nothing moving it) stays on the stage it's on.
- **Intake.** A `field` ball whose sphere touches the zone box, while the intake roller's inward (positive) surface
  speed is at least min_surface_speed, is collected (parked, `robot`) if the robot has room (the capacity is the
  whole robot's: hopper, stages and launcher) and at least 1 / rate seconds have passed since the last pickup. One
  ball per pickup; the others touching stay physical and are pushed by the bumper.

A ball's launcher index (`Launcher.insert` numbers the balls it is fed) maps back to its range ball through `take`.
The launcher is reached through the shooter (`busy_lanes`, `feed`, `positions`), so the same path feeds a ball
launcher or a disc shooter.

**Discs** (a disc shooter, `[shooter] kind = "disc"`) take the same path in one lane (the channel holds one disc at
a time): they lie flat in a single-file row on the stages, spaced by their diameter, the simplest choice (the path
already spaces pieces by a diameter; a stacked hopper, spaced by the disc's thickness, would need a stage that lifts
the bottom disc out). The launcher's entry is busy while a disc is in the channel, so the stage pushes the next disc
in only once the last one has left. A disc goes in at the last stage's surface speed. The robot holds DISC_CAPACITY
discs unless [shooter.hopper] capacity says otherwise.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from ..bindings import Update
from ..launcher.model import ExitedBall
from .balls import BallState, Balls

HOPPER, LAUNCHER = "hopper", "launcher"
# Discs the robot holds when [shooter.hopper] capacity is left out: a disc shooter's (kind = "disc").
# source: 2013 FRC Game Manual §3.2.5.4, G24: "ROBOTS may not actively control more than four (4) DISCS at any one
# time" (docs/disc.md, Sources).
DISC_CAPACITY = 4
# m/s: a stage whose surface is slower than this is stopped. A roller at rest isn't exactly still (the robot's motion
# jiggles its joint by ~1e-7 m/s), and that mustn't draw balls from the hopper or creep them along.
STOPPED_SPEED = 1e-3


class BallPath:
    def __init__(self, config, balls: Balls, shooter):
        self.config, self.balls, self.shooter = config, balls, shooter
        m = balls.model
        self.lanes = config.lanes
        self.d = balls.range.piece.diameter     # a ball's, or a disc's: discs lie flat in a row (module notes)
        self.r = self.d / 2.0
        stages = config.stages
        self._names = [st.name for st in stages]
        self._length = [st.path_length for st in stages]
        self._start = [sum(self._length[:k]) for k in range(len(stages))]     # each stage's start on the path
        self._dof = [int(m.jnt_dofadr[m.joint(f"{st.part}.spin").id]) for st in stages]
        self._half_d = [st.diameter / 2.0 for st in stages]
        it = config.intake
        if it is not None:
            self._intake_dof = int(m.jnt_dofadr[m.joint(f"{it.part}.spin").id])
            self._intake_body = m.body(it.body).id
            self._zone_center = np.array(it.center)
            self._zone_axes = np.array(it.axes)
            self._zone_half = np.array(it.size) / 2.0
            self._pickup_dt = 1.0 / it.rate
        self.reset()

    def reset(self) -> None:
        """Every lane empty, the launcher's balls forgotten, no pickup yet."""
        n = len(self.lanes)
        self.hopper: list[deque[int]] = [deque() for _ in range(n)]
        self.path: list[list[list]] = [[] for _ in range(n)]   # per lane [ball, stage, s], the launcher's side first
        self.in_launcher: dict[int, tuple[int, int]] = {}      # launcher index -> (ball, lane)
        self._last_pickup = -np.inf

    def unload_launcher(self) -> None:
        """The launcher model's balls back to the front of their lanes' hoppers (still the robot's), in the order they
        were fed: for a fresh launcher model (World.reset_robot)."""
        for index in sorted(self.in_launcher, reverse=True):
            i, lane = self.in_launcher[index]
            self.hopper[lane].appendleft(i)
        self.in_launcher = {}

    # ------------------------------------------------------------------------------------------------ filling

    def _lane_count(self, lane: int) -> int:
        return (len(self.hopper[lane]) + len(self.path[lane])
                + sum(1 for _, ln in self.in_launcher.values() if ln == lane))

    def _store(self, i: int) -> None:
        """Ball i into the back of the emptiest lane's hopper; it is the robot's (parked)."""
        lane = min(range(len(self.lanes)), key=lambda k: (self._lane_count(k), k))
        self.hopper[lane].append(i)
        self.balls.set_state(i, BallState.ROBOT)
        self.balls.park(i)
        self.balls.log[i].lane = lane

    def preload(self, n: int) -> None:
        """n of the robot's spare ball slots (never a station's pool) into the hopper."""
        b = self.balls
        slots = [i for i in b.layout.robot_slots if b.state[i] == BallState.SPARE]
        if n > len(slots) or self.held() + n > self.config.capacity:
            raise ValueError(f"preload {n}: the robot holds at most {self.config.capacity} balls")
        for i in slots[:n]:
            self._store(i)

    def held(self) -> int:
        """Balls in the robot: the hopper, the stages and the launcher."""
        return sum(len(h) for h in self.hopper) + sum(len(p) for p in self.path) + len(self.in_launcher)

    # ------------------------------------------------------------------------------------------------ each step

    def surface_speeds(self) -> list[float]:
        """Each stage's surface speed (m/s, positive toward the launcher)."""
        qvel = self.balls.data.qvel
        u = [float(qvel[dof]) * h for dof, h in zip(self._dof, self._half_d)]
        return [0.0 if abs(x) < STOPPED_SPEED else x for x in u]

    def step(self, dt: float, t: float) -> list[tuple[int, ExitedBall]]:
        """Move the balls on the stages dt seconds and feed the launcher model at its time t. The balls the launcher
        model turned away at once (no_contact, no_wheel), as (ball, ExitedBall): the caller logs them."""
        u = self.surface_speeds()
        busy = self.shooter.busy_lanes(self.d)        # lane positions with a piece near the launcher's entry
        turned_away: list[tuple[int, ExitedBall]] = []
        for lane in range(len(self.lanes)):
            fed = self._move(lane, u, dt, self.lanes[lane] in busy)
            if fed is not None:
                index, shot = self.shooter.feed(self.lanes[lane], u[-1], t)
                if shot is not None:
                    turned_away.append((fed, shot))
                else:
                    self.in_launcher[index] = (fed, lane)
            self._release(lane, u)
        return turned_away

    def _move(self, lane: int, u: list[float], dt: float, launcher_busy: bool) -> int | None:
        """One lane's balls on the stages; the ball that reached the launcher's entry and goes in, if any.

        Forward movers go front to back, each stopping a diameter behind where the ball ahead ends up (or where it is
        now, if it doesn't move forward); then backward movers, back to front, against the ball behind. Each ball's
        hand-off or clamp is applied before the next ball reads its place, so the spacing holds exactly. Only a ball
        moving forward is handed forward, and only one moving backward is handed back: a ball resting at a boundary
        stays on the stage it's on."""
        balls = self.path[lane]
        if not balls:
            return None
        d, start, length = self.d, self._start, self._length
        last = len(length) - 1
        n = len(balls)
        new = [start[k] + s for _, k, s in balls]       # positions along the whole path, the front ball first
        fed = None
        for j in range(n):
            ball = balls[j]
            k = ball[1]
            step = u[k] * dt
            if step <= 0.0:
                continue
            g = new[j]
            to = g + step if j == 0 else max(g, min(g + step, new[j - 1] - d))
            end = start[k] + length[k]
            if to >= end:
                if k < last:                            # onto the next stage at 0 (the spacing keeps its entry clear)
                    ball[1], to = k + 1, end
                elif j == 0 and not launcher_busy:
                    fed, to = ball[0], np.inf           # into the launcher: no longer in the way of the ball behind
                else:
                    to = end                            # waits at the end
            new[j] = to
        to_hopper = []
        for j in range(n - 1, -1, -1):
            ball = balls[j]
            k = ball[1]
            step = u[k] * dt
            if step >= 0.0 or ball[0] == fed:
                continue
            g = new[j]
            behind = new[j + 1] if j + 1 < n else -np.inf
            to = min(g, max(g + step, behind + d))
            if to < start[k]:
                if k == 0:
                    to_hopper.append(ball[0])
                    to = -np.inf                        # back in the hopper: out of the way of the ball ahead
                elif behind <= start[k] - d + 1e-12:    # to the end of the stage before, if no ball is there
                    ball[1], to = k - 1, start[k]
                else:
                    to = start[k]                       # stops at 0
            new[j] = to
        for j, ball in enumerate(balls):
            if np.isfinite(new[j]):
                ball[2] = new[j] - start[ball[1]]
        if fed is not None:
            balls.pop(0)
        for i in reversed(to_hopper):                   # the rearmost goes back first, so the order is kept
            back = balls.pop()[0]
            if back != i:
                raise RuntimeError(f"ball path: lane {lane}'s rearmost ball {back} isn't ball {i} going to the hopper")
            self.hopper[lane].appendleft(i)
        return fed

    def _release(self, lane: int, u: list[float]) -> None:
        """The hopper's front ball onto the first stage, if it runs forward and its entry is clear."""
        if u[0] > 0.0 and self.hopper[lane]:
            balls = self.path[lane]
            if not balls or self._start[balls[-1][1]] + balls[-1][2] >= self.d - 1e-12:
                balls.append([self.hopper[lane].popleft(), 0, 0.0])

    def take(self, shot: ExitedBall) -> tuple[int, int]:
        """The ball the launcher model has finished with (its index `shot.index`): (ball, lane), no longer held."""
        return self.in_launcher.pop(shot.index)

    # ------------------------------------------------------------------------------------------------ the intake

    def zone_pose(self) -> tuple[np.ndarray, np.ndarray]:
        """The intake zone's center and axes (columns) in the world, from its body's pose."""
        d = self.balls.data
        rot = d.xmat[self._intake_body].reshape(3, 3)
        return d.xpos[self._intake_body] + rot @ self._zone_center, rot @ self._zone_axes

    def collect(self, pose: tuple[np.ndarray, np.ndarray] | None = None) -> None:
        """Collect a field ball touching the intake zone (at `pose`, else where its body is now), if the intake runs
        inward fast enough, the robot has room and the pickup rate allows."""
        it = self.config.intake
        if it is None:
            return
        b = self.balls
        now = float(b.data.time)
        if now - self._last_pickup < self._pickup_dt - 1e-9 or self.held() >= self.config.capacity:
            return
        if float(b.data.qvel[self._intake_dof]) * it.diameter / 2.0 < it.min_surface_speed:
            return
        f = np.flatnonzero(b.field)
        if not len(f):
            return
        center, axes = pose if pose is not None else self.zone_pose()
        local = (b._qpos[f, :3] - center) @ axes                     # each ball's center in the zone's frame
        outside = np.maximum(np.abs(local) - self._zone_half, 0.0)
        dist = np.linalg.norm(outside, axis=1)                       # center to the box (0 inside it)
        touching = np.flatnonzero(dist <= self.r)
        if not len(touching):
            return
        k = touching[np.argmin(np.linalg.norm(local[touching], axis=1))]   # the one nearest the zone's center
        self._store(int(f[k]))
        self._last_pickup = now

    # ------------------------------------------------------------------------------------------------ reports

    def lanes_state(self) -> list[list[tuple[str, float]]]:
        """Per lane, (where, s) for each ball from the launcher's side back: "launcher" (s from its entry), a stage's
        name (s along it) or "hopper" (s 0)."""
        out = []
        pos = {index: s for index, _, s in self.shooter.positions()}
        for lane in range(len(self.lanes)):
            row = [(LAUNCHER, float(pos.get(index, np.nan))) for index, (_, ln) in self.in_launcher.items()
                   if ln == lane]
            row += [(self._names[k], float(s)) for _, k, s in self.path[lane]]
            row += [(HOPPER, 0.0)] * len(self.hopper[lane])
            out.append(row)
        return out

    def lane_balls(self) -> list[list[int]]:
        """Per lane, the ball ids in lanes_state()'s order."""
        return [[i for _, (i, ln) in self.in_launcher.items() if ln == lane] + [b[0] for b in self.path[lane]]
                + list(self.hopper[lane]) for lane in range(len(self.lanes))]

    def _occupied(self, bb) -> bool:
        lanes = range(len(self.lanes)) if bb.lane is None else (bb.lane,)
        if bb.at == HOPPER:
            return any(self.hopper[k] for k in lanes)
        if bb.at == LAUNCHER:
            near = self.shooter.busy_lanes(self.d)
            return any(self.lanes[k] in near for k in lanes)
        k = self._names.index(bb.at.removeprefix("stage:"))
        point = bb.fraction * self._length[k]
        return any(st == k and abs(s - point) <= self.r for lane in lanes for _, st, s in self.path[lane])

    def beam_updates(self) -> list[Update]:
        """Each beam break's DIO, as DigitalBinding reports a limit switch (active-low: False while a ball is there)."""
        out = []
        for bb in self.config.beam_breaks:
            occupied = self._occupied(bb)
            out.append(("DIO", str(bb.dio), {"<>value": (not occupied) if bb.active_low else occupied}))
        return out

    def device_keys(self) -> list[tuple[str, str]]:
        return [bb.device_key for bb in self.config.beam_breaks]
