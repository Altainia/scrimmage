"""Running a test: a student's blocks played against the physics, with no robot program.

The runner takes the robot program's place, as `climb run` does (scrimmage/climb/run.py): every physics step it
writes the motor controllers' outputs through the config's own motor bindings (scrimmage/hal_drive.py), so each block
drives through the robot's own motors, gearing and battery. It resets the world, settles the robot on the carpet for
SETTLE_S with every motor at neutral, then plays the blocks. Every time in a run (the outcome, the checks, the samples)
is on the run's clock, which starts with the settle; `time_limit` ends the run at that time on it.

Blocks hold like a climb script's drives: `spin` holds its volts until a later block changes that motor; `move` holds
its position target (a proportional loop, as `climb run`'s) and waits until the joint is within MOVE_TOL of it, or its
timeout; `drive` drives the wheel motors (forward for a positive distance or volts, or in its direction) until the
robot has gone that far across the floor from where the block started (or its timeout), or for its seconds, then stops
them; `turn` drives them until the robot's heading has turned that far (unwrapped every step, so past 180°), or its
timeout. On a swerve drive both point the modules first (drive.py's steer targets, held by the position loop, wheels at
0 V) and start the wheels once every module is pointed, or after STEER_TIMEOUT (each wheel then signed again every
step, as its module turns, until they all are); a drive's seconds count from then. The steer targets stay held after
the block, until a later block changes them. `stop` lets a motor (or all of them) go to neutral. `wait`,
`wait_until` and `repeat` pass time and loop; a `repeat` goes round at most once a physics step when its body takes
no time. `expect` checks its condition once, or (`throughout`) every step until the run ends; a check that fails ends
the run, saying what happened instead. The Watch reads the world once after the settle, so the first blocks judge the
robot as it rests (a hook already on a bar touches it).

Feeding: `start_feeding` and `stop_feeding` take no time (the station feeds every interval from the next step on, or
stops). `feed_one` asks for one piece and waits until the station's count rises, or until it is empty or used up (it
does nothing then; the counts show it); past its timeout it ends the run "timed out", saying whether the release point
stayed blocked. Every piece a station feeds is tracked (feeds.py's Outcomes). When the blocks finish, the stations
stop and the run keeps stepping, motors as they were, until every piece has settled or for SETTLE_MAX_S (the settle
doesn't count against `time_limit`); any other ending decides the pieces where they are. A last top-level `expect
made_it_in` (not `throughout`) is checked after the settle, against the final counts; any other `made_it_in` reads
the counts so far. With pieces tracked, a finished run's outcome is the counts sentence (feeds.sentence) and any other
ending is "<ending>; <counts>".

A floor spread's placement (spread.py) moves the floor ball and turns the robot: after the reset the robot keeps its
start position with its heading turned by `angle_deg`, and the spread's ball is put `distance_in` ahead along that
heading and `offset_in` to its left, turned by `rotation_deg` about the vertical (any piece but a ball from the
robot's heading, its axis tipped up by `tip_deg`, at its own resting height: spread.rest_of). It is tracked as "floor"
from the first block on, so the run's counts and outcome include it.

A failed `held` check says when the piece was lost and how (grip_watch.py), and what was commanded in the
SLIP_LOOKBACK_S before: each joint a `spin` started or a `move` was moving (until it got there), and a `drive` or
`turn` while under way ("the coral slipped out of claw at 2.3 s while claw.arm was moving"), or that the piece was
never held. That sentence carries its own time, and a lost piece's moment is kept in `marks` for the replay. A failed
`rests_on` check says why when the element carries the piece ("the coral doesn't rest on shelf (a robot body still
touches it)"); a failed `seated` check says the piece isn't on the rod, or how far it is ("the coral is seated only
0.8 in on branch_l2 (could be knocked off)" under placed.py's KNOCK_OFF_SHARE of how far it could go, else "the coral
is seated 2.0 in of 4.0 in on branch_l2, less than 3 in"). Each `seated.<rod>` signal is the deepest piece's depth on
that rod in inches, NaN while none is on it; the run's `seated` says each rod's depth at the end in words.

A run ends with one status: "finished" (every block played), "check failed", "timed out" (a wait until, a move, a
drive or a turn), "tipped over" (the robot body's up axis more than TIP_DEG from vertical), "walked off" (the climb
runner's "hook walked off" rule, scrimmage/climb/run.py's WalkOff), "time limit", "repeat limit" (a repeat until
reached its max) or "cancelled", and a one-sentence outcome for the student.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

import mujoco
import numpy as np

from ..climb.run import SETTLE_S, WalkOff
from ..hal_drive import MotorDrive, driven_joints, position_volts
from ..movers import mover_poses, movers
from ..protocol import HalState
from ..testrange.build import BALL_BITS
from .conditions import IN, Watch, _conds, describe
from .drive import STEER_TIMEOUT, drivetrain
from .feeds import SETTLE_MAX_S, Outcomes, counts, is_standin, made_it_in, sentence
from .grip_watch import SLIP_LOOKBACK_S, default_word
from .palette import ALL_MOTORS, ANY_HOOK, ANY_PIECE, NEXT_BAR, palette, problems
from .placed import KNOCK_OFF_SHARE, seated_words
from .program import (DRIVE_TIMEOUT, DRIVE_VOLTS, MOVE_TIMEOUT, REPEAT_MAX, WAIT_TIMEOUT, Program,
                      TestFileError)
from .spread import NO_BALL, rest_of, spread_ball, start_pose

MOTION_HZ = 30            # mover poses a second, for the replay
SIGNAL_HZ = 50            # signal samples a second, for the chart
TIP_DEG = 60.0            # the robot body's up axis this far from vertical: it tipped over (spec, our call)
MOVE_TOL_DEG = 1.0        # a move has reached its target within this (a hinge), our call
MOVE_TOL_IN = 0.25        # ... or this (a slide), our call
EPS = 1e-9
MOVING_DEG_S = 5.0        # a joint turning faster than this (a hinge), our call, was moving at a slip
MOVING_IN_S = 0.5         # ... or sliding faster than this (a slide), our call
MOVING_SPEED = 0.1        # m/s: the robot going across the floor faster than this was driving at a slip, our call

# The verbs describe() uses, as "-ing" (after "without") and "to ..." (after "waiting for").
_VERBS = {"passes": ("passing", "to pass"), "goes": ("going", "to go"), "tilts": ("tilting", "to tilt"),
          "touches": ("touching", "to touch"), "reaches": ("reaching", "to reach"), "hangs": ("hanging", "to hang"),
          "are": ("being", "to be"), "is": ("being", "to be"), "made": ("having made", "to have made"),
          "is held": ("being held", "to be held"), "rests": ("resting", "to rest")}


@dataclass
class RunResult:
    status: str               # "finished" | "check failed" | "timed out" | "tipped over" | "walked off" |
    #                           "time limit" | "repeat limit" | "cancelled"
    outcome: str              # one sentence for the student
    t_end: float              # s on the run's clock
    checks: list[dict] = field(default_factory=list)    # each expect: place, text, throughout, passed, t, line
    hung: list[str] = field(default_factory=list)       # the bars hung from, in order
    signals: dict[str, list[float]] = field(default_factory=dict)   # "t" and each signal, SIGNAL_HZ
    motion: list[tuple[float, np.ndarray]] = field(default_factory=list)  # (t, (movers, 7) poses), MOTION_HZ
    movers: list[str] = field(default_factory=list)
    view: dict = field(default_factory=dict)            # model_view(world, include_world=True) at the start
    started: str = ""         # when the run started, ISO time with its UTC offset
    feeds: list[dict] = field(default_factory=list)     # one row per fed piece (feeds.Outcomes.final)
    counts: dict | None = None    # fed, in, stuck, missed; None on a field without stations
    seed: int | None = None       # the stations' seed; None on a field without stations
    settle_s: float = 0.0         # s the run went on after the blocks finished, for the pieces to settle
    marks: list[dict] = field(default_factory=list)     # moments the replay marks: {t, kind ("slip"), text, ball}
    seated: dict[str, str] = field(default_factory=dict)  # rod -> its piece's depth at the end in words (placed.py)
    notes: list[str] = field(default_factory=list)      # what the run's numbers rest on (run_notes): UNCALIBRATED grips

    @property
    def passed(self) -> bool:
        return self.status == "finished" and all(c["passed"] for c in self.checks)


@dataclass
class _Frame:
    blocks: list
    trail: tuple              # 0-based indices of the repeats this list is inside, outermost first
    index: int = 0
    rounds: int = 0           # rounds of a repeat's body done
    round_t: float | None = 0.0   # when this round started; None: it starts at the next step


class _End(Exception):
    def __init__(self, status: str, ending: str):
        self.status, self.ending = status, ending


def _where(place: tuple) -> str:
    """0-based place -> "block 3 inside block 2"."""
    return " inside ".join(f"block {i + 1}" for i in reversed(place))


def _recast(text: str, form: int) -> str:
    """describe()'s words with its verb (one word, or two: "is held") as "-ing" (form 0) or "to ..." (form 1)."""
    words = text.split(" ")
    for i, w in enumerate(words):
        two = " ".join(words[i:i + 2])
        if two in _VERBS:
            return " ".join([*words[:i], _VERBS[two][form], *words[i + 2:]])
        if w in _VERBS:
            words[i] = _VERBS[w][form]
            break
    return " ".join(words)


def _amount(x: float, unit: str) -> str:
    return f"{x:g}°" if unit == "deg" else f"{x:g} {unit}"


def setup_config(raw: dict, setup: dict) -> dict:
    """A copy of the config dict with the test's `[setup]` ball or piece, station settings and seed in its `[range]`
    (a station's settings add to, and replace, the config's own; a ball or piece replaces the config's own, whichever
    it gave). Nothing to set leaves `raw` as it is. On the plain field (no `[range]`) the seed is dropped: it only
    picks the stations' draws, and there are none."""
    values = {k: setup[k] for k in ("ball", "piece", "station", "seed") if k in setup}
    if not isinstance(raw.get("range"), dict):
        values.pop("seed", None)
        if values:          # palette.problems says so too, so Setup shows it before Run
            raise TestFileError("Setup's ball, piece and station settings need a field; this robot is on the plain "
                                "field")
    if not values:
        return raw
    rng = dict(raw["range"])
    if "ball" in values or "piece" in values:      # the test's one choice, never both (load_range refuses both)
        rng.pop("ball", None)
        rng.pop("piece", None)
    for key, v in values.items():
        if key == "station":
            have = rng.get("station")
            have = have if isinstance(have, dict) else {}
            rng[key] = {**have, **{n: {**(have[n] if isinstance(have.get(n), dict) else {}), **t}
                                   for n, t in v.items()}}
        else:
            rng[key] = v
    return {**raw, "range": rng}


def world_for(program: Program):
    """The World of the test's robot config (its `robot`, relative to the test file), with the test's setup applied."""
    from ..config import build_world, load_config
    config = load_config(Path(program.path).parent / program.robot)
    return build_world(replace(config, raw=setup_config(config.raw, program.setup)))


GRIP_CONDITIONS = ("held", "rests_on", "seated")     # a test of these rests on the grip's friction
DEFAULT_GRIP_NOTE = ("grip: the pads have the default friction, an UNCALIBRATED estimate; pick a grip material for "
                     "them in the Part tab, or measure yours with a sled pull (docs/test-tab.md)")


def run_notes(world, program: Program) -> list[str]:
    """What the run's numbers rest on: a line per gripped body (parts/grip.py's grip_notes); a test of held, rests on
    or seated with no grip anywhere gets DEFAULT_GRIP_NOTE, so its grip isn't read as measured either."""
    from ..parts.grip import grip_notes
    notes = grip_notes(world)
    if not notes and any(_conds(program.blocks, k) for k in GRIP_CONDITIONS):
        notes = [DEFAULT_GRIP_NOTE]
    return notes


def check_problems(program: Program, world) -> None:
    """Raise TestFileError listing the test's problems on this robot (palette.problems), one line each."""
    bad = problems(program, world)
    if bad:
        lines = [(f"{_where(tuple(p['place']))}: " if p["place"] else "") + p["message"] for p in bad]
        raise TestFileError("the test can't run on this robot yet:\n" + "\n".join(lines))


class TestRun:
    __test__ = False     # not a pytest class

    def __init__(self, world, program: Program, placement: dict | None = None):
        """`placement`: a floor spread's {offset_in, distance_in, angle_deg, rotation_deg, tip_deg}
        (spread.placements)."""
        check_problems(program, world)
        self.world, self.program = world, program
        m = world.model
        self.motors = driven_joints(world)
        self._drive = {n: MotorDrive(b) for n, b in self.motors.items()}
        pal = palette(world, list(program.hooks))
        kinds = {j["name"]: j["kind"] for j in pal["joints"]}
        self.drivetrain = drivetrain(world, self.motors)
        self.signal_names = list(pal["signals"])
        self._seated = {f"seated.{r}": r for r in pal["rods"]}     # a seated signal -> its rod
        self._qadr = {n: int(m.jnt_qposadr[m.joint(n).id]) for n in kinds}
        self._slide = {n: k == "slide" for n, k in kinds.items()}
        self.feeder = world.test_range.feeder if world.test_range is not None else None
        self.standin = is_standin(Path(program.path).parent / program.robot)
        self.placement = placement
        self._floor = spread_ball(world) if placement is not None else None
        if placement is not None and self._floor is None:
            raise TestFileError(NO_BALL)

    # ---- the run ----------------------------------------------------------------------------------------------------

    def run(self, on_sample=None, cancel: threading.Event | None = None) -> RunResult:
        """Play the blocks. `on_sample(t, row)` is called at each signal sample (row: signal -> value); setting
        `cancel` ends the run at the next physics step."""
        from ..assembler.view import model_view
        started = datetime.now().astimezone().isoformat(timespec="milliseconds")
        w, m, d = self.world, self.world.model, self.world.data
        dt = float(m.opt.timestep)
        w.external = None
        w.reset()
        if self.placement is not None:
            self._place(self.placement)
        for _ in range(round(SETTLE_S / dt)):
            w.step(HalState())
        watch = self.watch = Watch(w, self.program)
        if watch.mon is not None:
            watch.mon.reset()
        watch.update()                                      # the robot as it rests, for the first blocks
        outcomes = self.outcomes = watch.outcomes = Outcomes(w, watch, self.standin)
        self._deferred: tuple[dict, object] | None = None  # the last made_it_in check, decided after the settle
        self._final: dict | None = None                     # the final counts, once the run has ended
        self._fed_seen = 0                                  # the feeder's Feds tracked so far
        if self._floor is not None:
            outcomes.track(self._floor[0], "floor", float(d.time))
        walk = WalkOff(dt) if watch.mon is not None else None
        view = model_view(w, include_world=True)
        ms = movers(w)
        result = RunResult("", "", 0.0, movers=[mv.name for mv in ms], view=view, started=started)
        result.signals = {"t": [], **{n: [] for n in self.signal_names}}

        self.drives: dict[str, tuple[str, float]] = {}      # joint -> ("volts", V) or ("to", qpos target)
        self.frames = [_Frame(list(self.program.blocks), ())]
        self.active: dict | None = None                     # the waiting block under way
        self.checks: list[dict] = []
        self.marks: list[dict] = []
        self._commanded: dict[str, float] = {}             # joint, "drive" or "turn" -> the last time it was commanded
        self._turning: dict[float, set[str]] = {}          # the moment a hold was lost -> what was turning
        #                                                     (seen at the update that ended it)
        self.throughout: list[tuple[dict, object]] = []
        t = float(d.time)
        next_signal = next_motion = t

        def sample(t: float) -> None:
            nonlocal next_signal, next_motion
            if t >= next_signal - EPS:
                row = self._row(watch)
                result.signals["t"].append(t)
                for k, v in row.items():
                    result.signals[k].append(v)
                if on_sample is not None:
                    on_sample(t, row)
                next_signal += 1.0 / SIGNAL_HZ
            if t >= next_motion - EPS:
                result.motion.append((t, mover_poses(w, ms)))
                next_motion += 1.0 / MOTION_HZ

        sample(t)
        outcomes.update(t)
        try:
            while True:
                self._advance(t)
                if cancel is not None and cancel.is_set():
                    raise _End("cancelled", f"you stopped the run at {t:.1f} s")
                if t >= self.program.time_limit - EPS:
                    raise _End("time limit", f"ran out of time ({self.program.time_limit:g} s){self._under_way()}")
                self._step()
                t = float(d.time)
                watch.update()
                self._track(t)
                sample(t)
                for check, cond in self.throughout:
                    if not watch.holds(cond):
                        self._fail(check, cond, t)
                if watch.tilt > TIP_DEG:
                    raise _End("tipped over", f"the robot tipped over at {t:.1f} s")
                if walk is not None:
                    on_ground, touched, _, _ = watch.mon.contact_state()
                    z = float(d.subtree_com[w.robot_body][2])
                    if walk.see(t, bool(watch.climb.get("climb.hanging", 0)), float(watch.climb.get(
                            "climb.hook_load", 0.0)), z, on_ground, touched) == "hook walked off":
                        bars = " and ".join(walk.left) or "its bar"
                        raise _End("walked off", f"a hook walked off {bars} at {t:.1f} s")
        except _End as e:
            result.status, ending = e.status, e.ending
        if result.status == "finished":
            t0 = t
            try:
                t = self._settle(t, sample, cancel)
            except _End as e:
                result.status, ending = e.status, e.ending
                t = float(d.time)
            result.settle_s = t - t0
        rows = outcomes.final()
        self._final = counts(rows)
        if self._deferred is not None:           # reached: the run finished, or was stopped while settling
            check, cond = self._deferred
            check["t"] = t
            if not made_it_in(self._final, cond.args["at_least"]):
                try:
                    self._fail(check, cond, t)
                except _End as e:
                    if result.status == "finished":
                        result.status, ending = e.status, e.ending
        if result.signals["t"][-1] < t - EPS:          # the run's last moment, off the sample grid
            next_signal = t
        if result.motion[-1][0] < t - EPS:
            next_motion = t
        sample(t)
        if self.feeder is not None or rows:
            result.feeds, result.counts = rows, counts(rows)
            result.seed = self.feeder.seed if self.feeder is not None else None
        if rows:
            told = sentence(rows)
            ending = told if result.status == "finished" else f"{ending}; {told}"
        top = watch.highest_hung()
        result.outcome = f"reached {top}, then {ending}" if top else ending
        result.t_end = t
        result.hung = list(watch.hung)
        for check in self.checks:
            check["line"] = self._line(check)
        result.checks = self.checks
        result.marks = self.marks
        for rod in self._seated.values():
            got = watch.placed.seated(rod)
            if got is not None:
                result.seated[rod] = seated_words(*got)
        result.notes = run_notes(self.world, self.program)
        return result

    def _place(self, p: dict) -> None:
        """A floor spread's placement, after the reset: the robot turned at its start, the spread's ball put in front
        of it."""
        w = self.world
        x, y, heading = start_pose(w)
        heading += p["angle_deg"]
        w.set_robot_pose(x, y, heading)
        h = math.radians(heading)
        ahead, left = p["distance_in"] * IN, p["offset_in"] * IN
        i = self._floor[0]
        z, quat = rest_of(w, i, p, heading)          # a ball at its radius; any other piece at its rest height
        w.test_range.balls.put(i, (x + ahead * math.cos(h) - left * math.sin(h),
                                   y + ahead * math.sin(h) + left * math.cos(h), z), BALL_BITS, quat=quat)
        mujoco.mj_forward(w.model, w.data)

    def _track(self, t: float) -> None:
        """After a physics step at t: track the pieces the stations fed in it, and update every tracked piece."""
        if self.feeder is not None:
            for fed in self.feeder.fed[self._fed_seen:]:
                self.outcomes.track(fed.ball, fed.station, fed.t)
            self._fed_seen = len(self.feeder.fed)
        self.outcomes.update(t)
        lost = self.watch.held.lost_now
        if lost is not None:
            self._turning[lost] = self._turning_now()

    def _turning_now(self) -> set[str]:
        """What is moving now: each driven joint (not a drive wheel) turning faster than MOVING_DEG_S, or sliding
        faster than MOVING_IN_S, by its name; "drive" when the robot goes across the floor faster than
        MOVING_SPEED (its wheels turn a little as it rocks)."""
        m, d, w = self.world.model, self.world.data, self.world
        wheels = set(self.drivetrain.wheels) if self.drivetrain is not None else set()
        out = set()
        for j in self.motors:
            if j in wheels:
                continue
            v = abs(float(d.qvel[m.jnt_dofadr[m.joint(j).id]]))
            if v > (MOVING_IN_S * IN if self._slide.get(j) else math.radians(MOVING_DEG_S)):
                out.add(j)
        vel = np.zeros(6)
        mujoco.mj_objectVelocity(m, d, mujoco.mjtObj.mjOBJ_BODY, w.robot_body, vel, 0)
        if float(np.linalg.norm(vel[3:5])) > MOVING_SPEED:
            out.add("drive")
        return out

    def _settle(self, t: float, sample, cancel) -> float:
        """After the blocks finish: the stations stopped, step until every piece has settled, or for SETTLE_MAX_S
        (motors as they were); the time it ends. Setting `cancel` ends it, and the run, at the next step."""
        if self.feeder is not None:
            self.feeder.stop_all()
        d = self.world.data
        t0 = t
        while not self.outcomes.settled() and t < t0 + SETTLE_MAX_S - EPS:
            if cancel is not None and cancel.is_set():
                raise _End("cancelled", f"you stopped the run at {t:.1f} s")
            self._step()
            t = float(d.time)
            self.watch.update()
            self._track(t)
            sample(t)
        return t

    def _step(self) -> None:
        """One physics step with the motor commands as they stand."""
        w, d = self.world, self.world.data
        hal = HalState()
        bus = w.vbatt
        for joint, (kind, value) in self.drives.items():
            if kind == "to":
                value = position_volts(value, float(d.qpos[self._qadr[joint]]), not self._slide[joint], bus)
            self._drive[joint].write(hal, value, bus)
        w.step(hal)

    def _row(self, watch) -> dict[str, float]:
        d = self.world.data
        row = {}
        for name in self.signal_names:
            if name == "tilt":
                row[name] = watch.tilt
            elif name == "hook_load":
                row[name] = float(watch.climb.get("climb.hook_load", 0.0))
            elif name in self._seated:                  # inches; NaN while no piece is on the rod
                got = watch.placed.seated(self._seated[name])
                row[name] = got[0] / IN if got is not None else math.nan
            elif name.endswith(".current") and name[:-8] in self.motors:
                row[name] = float(self.motors[name[:-8]].current)
            else:
                q = float(d.qpos[self._qadr[name]])
                row[name] = q / IN if self._slide[name] else math.degrees(q)
        return row

    # ---- the blocks -------------------------------------------------------------------------------------------------

    def _advance(self, t: float) -> None:
        """Play blocks at time t until one has to wait for time to pass; raises _End when the run ends."""
        watch = self.watch
        while True:
            f = self.frames[-1]
            if f.round_t is None:
                f.round_t = t
            if f.index >= len(f.blocks):
                if len(self.frames) == 1:
                    raise _End("finished", "finished")
                parent = self.frames[-2]
                rep = parent.blocks[parent.index]
                f.rounds += 1
                a = rep.args
                if "times" in a:
                    done = f.rounds >= a["times"]
                else:
                    done = watch.holds(a["until"])
                    if not done and f.rounds >= a.get("max", REPEAT_MAX):
                        what = _recast(self._describe(a["until"]), 0)
                        times = "1 time" if f.rounds == 1 else f"{f.rounds} times"
                        raise _End("repeat limit", f"repeat ({_where(f.trail)}) stopped after {times} without {what}")
                if done:
                    self.frames.pop()
                    parent.index += 1
                    continue
                f.index = 0
                if t <= f.round_t + EPS:       # the round took no time: go round again at the next step
                    f.round_t = None
                    return
                f.round_t = t
                continue
            b = f.blocks[f.index]
            place = f.trail + (f.index,)
            if self.active is None:
                if self._start(b, place, t):
                    f.index += 1
                    continue
                if b.do == "repeat":            # its body's frame is on top now
                    continue
            if not self._waited(b, place, t):
                return
            self.active = None
            f.index += 1

    def _start(self, b, place: tuple, t: float) -> bool:
        """Start block b; True when it is done already (it takes no time)."""
        a, watch = b.args, self.watch
        if b.do == "spin":
            self.drives[a["motor"]] = ("volts", a["volts"])
            self._commanded[a["motor"]] = t
            return True
        if b.do == "stop":
            if a["motor"] == ALL_MOTORS:
                self._commanded.update({j: t for j in self.drives})
                self.drives.clear()
            else:
                self._commanded[a["motor"]] = t
                self.drives.pop(a["motor"], None)
            return True
        if b.do in ("start_feeding", "stop_feeding"):
            (self.feeder.start if b.do == "start_feeding" else self.feeder.stop)(a["station"])
            return True
        if b.do == "expect":
            cond = a["that"]
            throughout = bool(a.get("throughout", False))
            if cond.kind == "made_it_in" and not throughout and place == (len(self.program.blocks) - 1,):
                check = {"place": list(place), "text": self._describe(cond), "throughout": False, "passed": True,
                         "t": t}
                self.checks.append(check)
                self._deferred = (check, cond)       # decided after the settle, on the final counts
                return True
            ok = watch.holds(cond)
            check = {"place": list(place), "text": self._describe(cond),
                     "throughout": throughout, "passed": ok, "t": t}
            self.checks.append(check)
            if not ok:
                self._fail(check, cond, t)
            if check["throughout"]:
                self.throughout.append((check, cond))
            return True
        if b.do == "repeat":
            if "until" in a and watch.holds(a["until"]):
                return True
            self.frames.append(_Frame(list(b.body), place, round_t=t))
            return False
        self.active = {"t0": t}
        if b.do == "move":
            j = a["joint"]
            to = math.radians(a["to"]) if a["unit"] == "deg" else a["to"] * IN if a["unit"] == "in" else a["to"]
            self.active["target"] = to
            self.active["q0"] = float(self.world.data.qpos[self._qadr[j]])
            self.drives[j] = ("to", to)
            self._commanded[j] = t
        elif b.do == "feed_one":
            f = self.feeder
            self.active["count"] = f.count(a["station"])
            if not f.capped(a["station"]):
                f.request(a["station"])
        elif b.do in ("drive", "turn"):
            self._commanded[b.do] = t
            dt, d = self.drivetrain, self.world.data
            self.active["from"] = d.xpos[self.world.robot_body][:2].copy()
            self.active["yaw"], self.active["turned"] = self._yaw(), 0.0
            self.active["rolling"] = None              # when the wheels started
            for j, q in dt.steer_targets(a.get("direction"), self._turn(b)).items():
                self.drives[j] = ("to", q)
            for j in dt.wheels:
                self.drives[j] = ("volts", 0.0)
            if dt.steered(d):
                self._roll(b, t)
        return False

    def _turn(self, b) -> float:
        """A turn block's sign (+1 left, -1 right); 0 for a drive."""
        return math.copysign(1.0, b.args["degrees"]) if b.do == "turn" else 0.0

    def _roll(self, b, t: float) -> None:
        """Start the drive or turn block's wheels at t, signed by where each rolls now (again every step until every
        module is pointed, when they start after STEER_TIMEOUT). A module stuck near 90 degrees from its target may
        flip its wheel's sign from step to step; harmless, as such a wheel adds almost nothing."""
        a = b.args
        volts = a.get("volts", DRIVE_VOLTS)
        if b.do == "turn":
            volts = abs(volts)
        elif "distance" in a:
            volts = math.copysign(abs(volts), a["distance"])
        for j, v in self.drivetrain.wheel_volts(a.get("direction"), self._turn(b), volts).items():
            self.drives[j] = ("volts", v)
        self.active["rolling"] = t
        self.active["steered"] = self.drivetrain.steered(self.world.data)

    def _yaw(self) -> float:
        """The robot body's heading, rad (its x axis seen from above)."""
        r = self.world.data.xmat[self.world.robot_body].reshape(3, 3)
        return math.atan2(float(r[1, 0]), float(r[0, 0]))

    def _waited(self, b, place: tuple, t: float) -> bool:
        """Block b, under way since self.active["t0"], is done at t; raises _End when it gives up."""
        a, watch, d = b.args, self.watch, self.world.data
        elapsed = t - self.active["t0"]
        if b.do == "wait":
            return elapsed >= a["seconds"] - EPS
        if b.do == "wait_until":
            if watch.holds(a["until"]):
                return True
            if elapsed >= a.get("timeout", WAIT_TIMEOUT) - EPS:
                what = _recast(self._describe(a["until"]), 1)
                raise _End("timed out", f"waiting for {what} timed out at {t:.1f} s ({_where(place)})")
            return False
        if b.do == "feed_one":
            f, name = self.feeder, a["station"]
            if f.count(name) > self.active["count"] or f.capped(name):
                return True
            if elapsed >= a.get("timeout", WAIT_TIMEOUT) - EPS:
                st = next(s for s in f.stations if s.name == name)
                why = ("its release point stayed blocked" if f.blocked(name)
                       else f"its {st.interval:g} s between pieces hadn't passed")
                raise _End("timed out", f"feed one from {name} timed out at {t:.1f} s: {why} ({_where(place)})")
            return False
        if b.do == "move":
            j = a["joint"]
            self._commanded[j] = t                      # moving until it gets there
            q = float(d.qpos[self._qadr[j]])
            tol = MOVE_TOL_IN * IN if self._slide[j] else math.radians(MOVE_TOL_DEG)
            if abs(q - self.active["target"]) <= tol:
                return True
            if elapsed >= a.get("timeout", MOVE_TIMEOUT) - EPS:
                target = self.active["target"]
                short = (target - q) * (target - self.active["q0"]) > 0.0     # still on the start's side
                raise _End("timed out", f"{j} stopped at {self._value(j, a['unit'])} "
                                        f"{'short of' if short else 'past'} {_amount(a['to'], a['unit'])} "
                                        f"({_where(place)})")
            return False
        # drive or turn
        self._commanded[b.do] = t
        act = self.active
        yaw = self._yaw()
        act["turned"] += (yaw - act["yaw"] + math.pi) % (2.0 * math.pi) - math.pi
        act["yaw"] = yaw
        if act["rolling"] is None:
            if self.drivetrain.steered(d) or elapsed >= STEER_TIMEOUT - EPS:
                self._roll(b, t)
        elif not act["steered"]:
            self._roll(b, act["rolling"])       # a module still turning: its wheel signed by where it rolls now
        timeout = a.get("timeout", DRIVE_TIMEOUT)
        if b.do == "turn":
            done = act["turned"] * math.copysign(1.0, a["degrees"]) >= math.radians(abs(a["degrees"])) - EPS
            if not done and elapsed >= timeout - EPS:
                raise _End("timed out", f"turn ({_where(place)}) didn't turn {a['degrees']:g}° in {timeout:g} s "
                                        f"(turned {math.degrees(act['turned']):.0f}°)")
        elif "distance" in a:
            gone = float(np.linalg.norm(d.xpos[self.world.robot_body][:2] - act["from"]))
            done = gone >= abs(a["distance"]) - EPS
            if not done and elapsed >= timeout - EPS:
                raise _End("timed out", f"{self._drive_name(a)} ({_where(place)}) didn't reach "
                                        f"{abs(a['distance']):g} m in {timeout:g} s (went {gone:.2f} m)")
        else:
            done = act["rolling"] is not None and t - act["rolling"] >= a["seconds"] - EPS
        if done:
            for j in self.drivetrain.wheels:
                self.drives.pop(j, None)
        return done

    @staticmethod
    def _drive_name(a: dict) -> str:
        """`drive`, or `drive left` with its direction."""
        return f"drive {a['direction']}" if "direction" in a else "drive"

    def _value(self, joint: str, unit: str) -> str:
        """A joint's position now in `unit`, in words (`38.2°`)."""
        q = float(self.world.data.qpos[self._qadr[joint]])
        v = math.degrees(q) if unit == "deg" else q / IN if unit == "in" else q
        return _amount(round(v, 1), unit)

    def _failure(self, cond) -> str:
        """What happened instead of `cond`, in words."""
        a, k, watch = cond.args, cond.kind, self.watch

        def bar(name: str) -> str:
            return (watch.next_bar() or "the next bar") if name == NEXT_BAR else name

        if k == "joint":
            side = "above" if "above" in a else "below"
            return (f"expected {a['name']} {side} {_amount(a[side], a['unit'])}, "
                    f"but it was {self._value(a['name'], a['unit'])}")
        if k == "tilt":
            side, limit = ("more", a["above"]) if "above" in a else ("less", a["below"])
            return (f"expected the robot to tilt {side} than {_amount(limit, 'deg')}, "
                    f"but it tilted {_amount(round(watch.tilt, 1), 'deg')}")
        if k == "touch":
            if a["robot"] == ANY_HOOK:
                return f"no hook touched {bar(a['field'])}"
            return f"{a['robot']} didn't touch {bar(a['field'])}"
        if k == "piece_in":
            if a["piece"] == ANY_PIECE:
                return f"no {default_word(watch.kinds)} was in {a['body']}"
            return f"{watch.piece_names.get(a['piece'], a['piece'])} wasn't in {a['body']}"
        if k == "in_hopper":
            n = watch.hopper_count()
            pieces = "1 piece was" if n == 1 else f"{n} pieces were"
            if "at_least" in a:
                return f"expected {a['at_least']} or more pieces in the hopper, but {pieces} in it"
            return f"expected at most {a['at_most']} pieces in the hopper, but {pieces} in it"
        if k == "station_empty":
            left = self.feeder.left(a["station"]) if self.feeder is not None else None
            return f"{a['station']} wasn't empty" + (f" ({left} left)" if left else "")
        if k == "made_it_in":
            c = self._final if self._final is not None else self.outcomes.so_far()
            return f"expected at least {a['at_least']:g}% to make it in, but {c['in']} of {c['fed']} did"
        if k == "hanging_from":
            return f"the robot wasn't hanging from {bar(a['bar'])}"
        if k == "held":
            return self._not_held(a)
        if k in ("rests_on", "seated"):
            return self._not_placed(k, a)
        return f"the robot wasn't hanging at {a['level']}"           # level

    def _not_held(self, a: dict) -> str:
        """How a held check failed: when and how the piece was lost, and what was moving in the second before; or
        that it was never held. It carries its own time (the moment it was lost)."""
        watch = self.watch
        i, by = watch.piece_index(a["piece"]), a["by"]
        loss = watch.held.lost(i, by) if watch.held.ever(i, by) else None
        named = watch.piece_names.get(a["piece"], a["piece"]) if i is not None else None
        if loss is None:
            return f"{named or 'the ' + default_word(watch.kinds)} was never held by {by}"
        t, how, kind = loss
        what = named or f"the {kind}"
        lost = f"{how} {by}" if how in ("slipped out of", "slid in") else how
        return f"{what} {lost} at {t:.1f} s while {self._moving(t)}"

    def _not_placed(self, k: str, a: dict) -> str:
        """How a rests_on or seated check failed: "the coral doesn't rest on shelf (a robot body still touches it)";
        "the coral isn't on branch_l2", "the coral is seated only 0.8 in on branch_l2 (could be knocked off)", or,
        on but short of what was asked, "the coral is seated 2.0 in of 4.0 in on branch_l2, less than 3 in"."""
        watch = self.watch
        i, on = watch.piece_index(a["piece"]), a["on"]
        what = watch.piece_names.get(a["piece"], a["piece"]) if i is not None else f"the {default_word(watch.kinds)}"
        if k == "rests_on":
            why = watch.placed.why_not(i, on)
            return f"{what} doesn't rest on {on}" + (f" ({why})" if why else "")
        got = watch.placed.seated(on, i)
        if got is None:
            return f"{what} isn't on {on}"
        depth, could = got
        if depth < KNOCK_OFF_SHARE * could:
            return f"{what} is seated only {depth / IN:.1f} in on {on} (could be knocked off)"
        return f"{what} is {seated_words(depth, could)} on {on}, less than {a['at_least_in']:g} in"

    def _moving(self, t: float) -> str:
        """What was commanded (moved, spun or stopped) in the SLIP_LOOKBACK_S before t, most recent first, then
        anything else still turning at t: "claw.arm was moving", "the robot was driving" (several joined by "and");
        "nothing was moving"."""
        recent = [n for _, n in sorted(((c, n) for n, c in self._commanded.items()
                                        if t - SLIP_LOOKBACK_S - EPS <= c <= t + EPS), reverse=True)]
        recent += sorted(n for n in self._turning.get(t, ()) if n not in recent)
        words = [("the robot was driving" if n == "drive" else "the robot was turning" if n == "turn"
                  else f"{n} was moving") for n in recent]
        return " and ".join(words) if words else "nothing was moving"

    def _describe(self, cond) -> str:
        """describe() with this run's next bar and the field's piece words."""
        w = self.watch
        return describe(cond, w.next_bar(), w.kinds, w.piece_names)

    def _fail(self, check: dict, cond, t: float) -> None:
        check.update(passed=False, t=t, failure=self._failure(cond))
        if cond.kind == "held":
            check["timed"] = True                  # its failure says its own time
            i, by = self.watch.piece_index(cond.args["piece"]), cond.args["by"]
            loss = self.watch.held.lost(i, by)
            if loss is not None and self.watch.held.ever(i, by):
                self.marks.append({"t": loss[0], "kind": "slip", "text": check["failure"],
                                   "ball": self.watch.held.lost_piece(i, by)})
        when = "" if check.get("timed") else f" at {t:.1f} s"
        raise _End("check failed", f"a check failed ({_where(tuple(check['place']))}): {check['failure']}{when}")

    def _doing(self, b) -> str:
        """A waiting block under way, in words."""
        a = b.args
        if b.do == "wait":
            return f"waiting {a['seconds']:g} s"
        if b.do == "wait_until":
            return f"waiting for {_recast(self._describe(a['until']), 1)}"
        if b.do == "move":
            return f"moving {a['joint']} to {_amount(a['to'], a['unit'])}"
        if b.do == "feed_one":
            return f"feeding one from {a['station']}"
        if b.do == "turn":
            return f"turning {a['degrees']:g}°"
        way = f" {a['direction']}" if "direction" in a else ""
        if "distance" in a:
            return f"driving{way} {a['distance']:g} m"
        return f"driving{way} for {a['seconds']:g} s"

    def _under_way(self) -> str:
        """The innermost repeat and the block under way, for the time-limit sentence ("" for neither)."""
        f = self.frames[-1]
        doing = None
        if self.active is not None and f.index < len(f.blocks):
            doing = self._doing(f.blocks[f.index]), f.trail + (f.index,)
        if len(self.frames) > 1:
            return f" during repeat ({_where(f.trail)})" + (f", {doing[0]}" if doing else "")
        return f", {doing[0]} ({_where(doing[1])})" if doing else ""

    def _line(self, c: dict) -> str:
        where = _where(tuple(c["place"]))
        if c["passed"]:
            when = f"from {c['t']:.1f} s on" if c["throughout"] else f"at {c['t']:.1f} s"
            return f"passed: {c['text']} {when} ({where})"
        return f"failed ({where}): {c['failure']}" + ("" if c.get("timed") else f" at {c['t']:.1f} s")
