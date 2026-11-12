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
them; `turn` drives them until the robot's heading has turned that far (unwrapped every step, so past 180°) and
it has nearly stopped turning, or its timeout: its volts follow the angle still to go and the turn rate (_steer_turn),
so it slows into its angle rather than coasting past it. On a swerve drive both point the modules first (drive.py's
steer targets, held by the position loop, wheels at
0 V) and start the wheels once every module is pointed, or after STEER_TIMEOUT (each wheel then signed again every
step, as its module turns, until they all are); a drive's seconds count from then. The steer targets stay held after
the block, until a later block changes them. `stop` lets a motor (or all of them) go to neutral. `wait`,
`wait_until` and `repeat` pass time and loop; a `repeat` goes round at most once a physics step when its body takes
no time. `expect` checks its condition once, or (`throughout`) every step until the run ends; a check that fails ends
the run, saying what happened instead. A held throughout check ends, passed, when the piece is set down on a rod or
element that a `seated` or `rests_on` condition of the test names ("... until it was set down on branch_l2 at 4.4 s",
controller ruling P34): placing a piece lets go of it. Any other loss still fails it. The Watch reads the world once
after the settle, so the first blocks judge the robot as it rests (a hook already on a bar touches it).

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
is seated 2 in of 4 in on branch_l2, less than 3 in"). Each `seated.<rod>` signal is the deepest piece's depth on
that rod in inches, NaN while none is on it; the run's `seated` says each rod's depth at the end in words.

Units: the signals keep fixed units (the ones named here: inches, psi, degrees, newtons), as signals.csv and the MCP
tools have them, whatever the Units setting; the Test tab converts them for its chart. The words (the outcome, the
check failures, `seated`, `lines`) are written in the Units setting when the run is made (units.fmt; the examples
here are Imperial's), and a run already made keeps the units it was written in. Machine text (MCP test_run, and a
sweep's or spread's table.csv) is written inside units.machine_text(): the words as they were before the setting
existed, whatever it is (inches and psi with one decimal, and meters for a drive, a "m" joint and the partner).

Shooting: on a range with a `[shooter]` the range's launcher model runs as it does under robot code, so spinning
the launcher and its stages shoots the balls the robot holds. Every motor of a joint is written (a launcher's
lead and follower, say), as robot code drives each one. The run's `goals` are the range's goal counts at the end
(None without a `[shooter]`); `scored` checks them.

Aiming: `aim` takes no time. From then on, every step, it sets the turret joint's position target to aim.py's
aim_target (the goal's center, clamped to the turret's range), held by the same position loop as a `move`, until a
later spin, move, stop or aim on that joint. A run with an aim block or an aimed condition has an `aim_error` signal
(degrees, positive with the goal to the left): the latest aim block's turret and goal once one has started, the
test's first aim's or aimed condition's before. A failed `aimed` check whose goal is beyond the turret's stop says so
("hub_blue is outside the turret's range (it stops at 30°)").

Pneumatics: `set_cylinder` takes no time. It sets the cylinder's valve through the air system
(PneumaticsRuntime.command, plan decision D3: no device ID needed) to extend or retract until a later set_cylinder on
it; "off" is the valve's spring position, as with the coils off in robot code: a single solenoid's spring
retracts the cylinder (extends it when the valve is inverted; one with no device ID counts as single, not inverted)
and a double solenoid's stays where it is (spec C9).
The compressor runs throughout a run, as if the robot code's closed loop were on (C11): the pressure switch still
starts and stops it. A robot with cylinders has the signals `air.psi` (the tanks), `air.working_psi` (the regulator's
output) and each cylinder's `<name>.extension` (in), and the run's `air`: the tanks' pressure at the start and end,
the lowest and when, how long the compressor ran and when the tanks first fell below the working pressure
(air_lines words it).

A field with hinged or sliding elements has each one's `element.<name>` signal: a hinged element's angle (deg) or a
sliding one's travel (in), 0 at rest; the `element` condition reads it.

A robot with a partner robot (the config's `[partner]`, or Setup's partner: setup_config) has the signals
`partner.height` (in), `partner.tilt` (deg) and `partner.moved` (in), read from where the partner settled
(parts/partner.py); the `partner_lifted` and `partner_moved` conditions read them. On an "off-carpet" range (2024's
ONSTAGE) the run has `climb.harmony` (HARMONY points) and `climb.hold.<chain>.robots` for each chain (how many robots
hang on it, a hanging partner counted), from the climb monitor (terrain/climb.py), and a match scores the HARMONY
points held at its end.

A robot with [[sensor]]s has each one's `sensor.<name>` signal: 0 or 1 for a beam break (1 while broken), the
distance in inches for a distance sensor (its max_range with nothing in range), and for a color sensor the index in
NAMED_COLORS of the color it sees (NaN while it sees none), and for a camera how many tags it sees. The `sensor`
condition reads the same readings, and the replay lights each sensor's line from its signal. A camera's tags are kept
in the run's `cameras`: each camera's [t, [IDs it sees]], from the start and then each time the set changes, which
the replay draws as lines from the camera to those tags.

Clearance (clearance.py): a test that names a field element in a `clears` condition or as `clearance.<element>` in
its chart measures the robot's clearance to it after every step (Watch.closest), and only then. The chart's
`clearance.<element>` is in inches, NaN while nothing is within 0.5 m; the run's `clearance` is each element's
smallest clearance and when (clearance_lines words it: "closest to trench: 0.82 in at 3.1 s", "trench: never within
20 in"). A failed `clears` check says the clearance then ("but it was 0.42 in").

A match (`match = true` in `[test]`, on a range with a `[game]`; match.py): the match starts at the first block's run
time (after the settle) and the run ends at its end, `auto_s + pause_s + teleop_s` later, with status "finished" even
when blocks are left (the outcome says so) or finished early (the robot sits, motors as they were); `time_limit` does
not apply. During the pause every step's HalState gets a DriverStation frame with `>enabled` false, so World.step
neutralizes the motors and holds the pneumatics; the blocks play on, and a drive's seconds count the pause. The
settle after the match's end runs with the same frame (the robot is disabled at the buzzer); a piece that lands then
is a goal piece but scores no points. A mark (kind "phase") goes into `marks` at each phase change. The result gains
`match`, `points` (the score at the match's end; a points condition reads the endgame as the climb level held now)
and `rules` (the rules broken, possession.py: [] for none).

A run ends with one status: "finished" (every block played), "check failed", "timed out" (a wait until, a move, a
drive or a turn), "tipped over" (the robot body's up axis more than TIP_DEG from vertical), "walked off" (the climb
runner's "hook walked off" rule, scrimmage/climb/run.py's WalkOff), "time limit", "repeat limit" (a repeat until
reached its max) or "cancelled", and a one-sentence outcome for the student.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import mujoco
import numpy as np

from .. import i18n, units
from ..climb.run import SETTLE_S, WalkOff
from ..hal_drive import driven_joints, joint_drives, position_volts
from ..movers import mover_poses, movers
from ..parts.partner import SIGNALS as PARTNER_SIGNALS
from ..protocol import HalState, frame
from ..testrange.build import BALL_BITS
from .aim import TURN, aim_error, aim_target
from .clearance import PREFIX, inches, named
from .conditions import IN, Watch, _amount, _conds, phase_word
from .drive import STEER_TIMEOUT, drivetrain
from .feeds import SETTLE_MAX_S, Outcomes, counts, is_standin, made_it_in, sentence
from .grip_watch import SLIP_LOOKBACK_S
from .match import MatchState
from .palette import ALL_MOTORS, palette, problems
from .placed import ON_PIECE, seated_words
from .possession import RuleWatch
from .program import DRIVE_TIMEOUT, DRIVE_VOLTS, MOVE_TIMEOUT, REPEAT_MAX, WAIT_TIMEOUT, Program, TestFileError
# run_notes', run_setup's and run_words' names, used here and re-exported (other modules and the tests import them
# from here)
from .run_notes import (DEFAULT_GRIP_NOTE, GRIP_CONDITIONS, PNEUMATIC_ESTIMATES, air_lines,  # noqa: F401
                        clearance_lines, default_grip_note, goal_lines, piece_lines, piece_log, piece_notes,
                        pneumatic_note, points_lines, rules_lines, run_notes)
from .run_setup import (COLOR_NAMES, hang_words, partner_overlap, sensor_value, setup_config,  # noqa: F401
                        setup_partner, setup_partner_words, world_for)
from .run_words import _FailureWords, _keep_words, _recast, _v, _w, _where, _ws  # noqa: F401
from .spread import no_ball, rest_of, spread_ball, start_pose

MOTION_HZ = 30            # mover poses a second, for the replay
SIGNAL_HZ = 50            # signal samples a second, for the chart
TIP_DEG = 60.0            # the robot body's up axis this far from vertical: it tipped over (spec, our call)
MOVE_TOL_DEG = 1.0        # a move has reached its target within this (a hinge), our call
MOVE_TOL_IN = 0.25        # ... or this (a slide), our call
TURN_TOL_DEG = 2.0        # a turn has reached its angle within this, our call
TURN_TOL_MIN_DEG = 0.25   # ... a small turn's tolerance (a quarter of its angle) is at least this, our call
TURN_RATE_TOL = 10.0      # deg/s: ... and turns slower than this, our call
TURN_KP = 0.25            # V per degree still to turn, our call (tuned on the stand-in and the swerve template)
TURN_KD = 0.02            # V per deg/s of turning, our call
TURN_MIN_VOLTS = 1.0      # V: a turn short of its angle pushes at least this hard (the wheels' static friction)
EPS = 1e-9
MOVING_DEG_S = 5.0        # a joint turning faster than this (a hinge), our call, was moving at a slip
MOVING_IN_S = 0.5         # ... or sliding faster than this (a slide), our call
DISABLED = frame("DriverStation", "", {">enabled": False})     # the pause's frame: the robot is disabled
MOVING_SPEED = 0.1        # m/s: the robot going across the floor faster than this was driving at a slip, our call


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
    goals: dict[str, int] | None = None     # the range's goal counts at the end; None without a [shooter]
    # the air, on a robot with cylinders (None without): start_psi, end_psi, low_psi, low_t (s), compressor_s and
    # below_working_t (s; None while the tanks stayed at or above the working pressure), the tanks' gauge psi; and
    # working_psi, the regulator's set pressure, which air_lines' warning names
    air: dict | None = None
    # each element the test measures (a clears condition or a clearance.<element> chart signal): {min_in, t}, the
    # smallest clearance (in) and when, both None when it was never within 0.5 m; None when the test measures none
    clearance: dict[str, dict] | None = None
    # each game piece that took part (testrange/balls.py's took_part): its ball log (balls.py log_dict, SI, None
    # for unset) and its kind; empty off a range. Where each shot or piece went, scored or not (piece_lines)
    pieces: list[dict] = field(default_factory=list)
    # a run made inside machine text (a sweep's, a spread's, MCP test_run's): its words in every Language and Units
    # setting, {"en-GB|metric": {outcome, checks, marks, seated, notes}} (_keep_words), which the Test tab shows in
    # the viewer's; None for any other run (its words are in the setting it was made in)
    words: dict | None = None
    # a match (`match = true`; None for any other run): the phase times in run time (match.py's to_dict), the points
    # (game.py's Points.to_dict) at the end or where the run stopped, and the rules broken (possession.py)
    match: dict | None = None
    points: dict | None = None
    rules: list | None = None
    # each camera [[sensor]]'s tags: [t, [IDs]] at the start and whenever the set it sees changes; {} without cameras
    cameras: dict[str, list[list]] = field(default_factory=dict)

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


class _Air:
    """The air through a run, seen after every physics step: RunResult.air's numbers."""

    def __init__(self, pneumatics, t: float):
        self.system = pneumatics.system
        psi = self.system.stored_psi
        self.out = {"start_psi": psi, "end_psi": psi, "low_psi": psi, "low_t": t, "compressor_s": 0.0,
                    "below_working_t": None, "working_psi": self.system.config.working_psi}

    def see(self, t: float, dt: float) -> None:
        s, out = self.system, self.out
        psi = s.stored_psi
        out["end_psi"] = psi
        if psi < out["low_psi"]:
            out["low_psi"], out["low_t"] = psi, t
        if s.compressor_on:
            out["compressor_s"] += dt
        if out["below_working_t"] is None and psi < out["working_psi"]:
            out["below_working_t"] = t


def _aims(blocks) -> list[tuple[str, str]]:
    """(turret, goal) of each aim block and aimed condition, in the test's order, through repeat bodies."""
    out = []
    for b in blocks:
        if b.do == "aim":
            out.append((b.args["turret"], b.args["at"]))
        out += [(c.args["turret"], c.args["at"]) for c in b.args.values() if getattr(c, "kind", None) == "aimed"]
        out += _aims(b.body)
    return out


def check_problems(program: Program, world) -> None:
    """Raise TestFileError listing the test's problems on this robot (palette.problems), one line each."""
    bad = problems(program, world)
    if bad:
        lines = [(f"{_where(tuple(p['place']))}: " if p["place"] else "") + p["message"] for p in bad]
        raise TestFileError(i18n.t("the test can't run on this robot yet:") + "\n" + "\n".join(lines))


def turn_tolerance(degrees: float) -> float:
    """How close (deg) a turn of `degrees` must come to its angle: TURN_TOL_DEG, or a quarter of a small turn's
    angle (at least TURN_TOL_MIN_DEG), so a 2° turn turns rather than counting as there at the start."""
    return min(TURN_TOL_DEG, max(TURN_TOL_MIN_DEG, abs(degrees) / 4.0))


class TestRun(_FailureWords):
    __test__ = False     # not a pytest class

    def __init__(self, world, program: Program, placement: dict | None = None):
        """`placement`: a floor spread's {offset_in, distance_in, angle_deg, rotation_deg, tip_deg}
        (spread.placements)."""
        check_problems(program, world)
        self.world, self.program = world, program
        self.match: MatchState | None = None        # the match's state, made when the run starts (match = true)
        self._blocks_done = False                   # a match: every block has been played, the match goes on
        self._over = False                          # a match: it has ended, the robot is disabled for the settle
        self.rule_watch: RuleWatch | None = None    # a match on a game with [game.rules]: the rules checked each step
        m = world.model
        self.motors = driven_joints(world)
        self._drive = joint_drives(world, self.motors)     # every motor on each joint
        pal = palette(world, list(program.hooks), program.setup)
        kinds = {j["name"]: j["kind"] for j in pal["joints"]}
        self.drivetrain = drivetrain(world, self.motors)
        self.signal_names = list(pal["signals"])
        aims = _aims(program.blocks)
        self._first_aim = aims[0] if aims else None      # (turret, goal) the aim_error signal reads at the start
        if aims:
            self.signal_names.append("aim_error")
        self._seated = {f"seated.{r}": r for r in pal["rods"]}     # a seated signal -> its rod
        self._double = {c["name"]: c["double"] for c in pal["cylinders"]}
        self._extension = {f"{c}.extension": c for c in self._double}     # an extension signal -> its cylinder
        self._elements = {f"element.{e['name']}": e["name"] for e in pal["moving_elements"]}   # signal -> element
        # a clearance signal -> its element: only the elements the test names (Watch.closest measures the same)
        self._clearance = {f"{PREFIX}{e}": e for e in named(program, pal["elements"])}
        self.signal_names += list(self._clearance)
        self._sensors = {f"sensor.{s['name']}": s["name"] for s in pal["sensors"]}     # a sensor signal -> its sensor
        self._qadr = {n: int(m.jnt_qposadr[m.joint(n).id]) for n in kinds}
        self._slide = {n: k == "slide" for n, k in kinds.items()}
        self.feeder = world.test_range.feeder if world.test_range is not None else None
        self.standin = is_standin(Path(program.path).parent / program.robot)
        self.placement = placement
        self._floor = spread_ball(world) if placement is not None else None
        if placement is not None and self._floor is None:
            raise TestFileError(no_ball())

    # ---- the run ----------------------------------------------------------------------------------------------------

    def run(self, on_sample=None, cancel: threading.Event | None = None) -> RunResult:
        """Play the blocks. `on_sample(t, row)` is called at each signal sample (row: signal -> value); setting
        `cancel` ends the run at the next physics step. The compressor runs throughout a run (C11); afterward the
        world's closed loop is back to what it was."""
        air = self.world.pneumatics
        if air is None:
            return self._play(on_sample, cancel)
        before = air.always_closed_loop
        air.always_closed_loop = True
        try:
            return self._play(on_sample, cancel)
        finally:
            air.always_closed_loop = before

    def _play(self, on_sample, cancel: threading.Event | None) -> RunResult:
        from ..assembler.view import model_view
        started = datetime.now().astimezone().isoformat(timespec="milliseconds")
        w, m, d = self.world, self.world.model, self.world.data
        dt = float(m.opt.timestep)
        w.external = None
        w.reset()
        self._over = False
        self.air = None
        if w.pneumatics is not None:
            self.air = _Air(w.pneumatics, float(d.time))
        if self.placement is not None:
            self._place(self.placement)
        for _ in range(round(SETTLE_S / dt)):
            w.step(HalState())
            self._see_air()
        watch = self.watch = Watch(w, self.program)
        if watch.mon is not None:
            watch.mon.reset()
        watch.update()                                      # the robot as it rests, for the first blocks
        places = ([c.args["on"] for c in _conds(self.program.blocks, "seated")],
                  [c.args["on"] for c in _conds(self.program.blocks, "rests_on") if c.args["on"] != ON_PIECE])
        self._places = set(places[0]) | set(places[1])     # where a check expects a piece: a held check ends there
        outcomes = self.outcomes = watch.outcomes = Outcomes(w, watch, self.standin, places)
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
        self.aims: dict[str, tuple[str, str]] = {}          # turret joint -> (turret, goal) its target follows
        self._aim_signal = self._first_aim                  # the latest aim block's, once one has started
        self.frames = [_Frame(list(self.program.blocks), ())]
        self.active: dict | None = None                     # the waiting block under way
        self.checks: list[dict] = []
        self.marks: list[dict] = []
        self._commanded: dict[str, float] = {}             # joint, "drive" or "turn" -> the last time it was commanded
        self._turning: dict[float, set[str]] = {}          # the moment a hold was lost -> what was turning
        #                                                     (seen at the update that ended it)
        self.throughout: list[tuple[dict, object]] = []
        t = float(d.time)
        if self.program.match:             # the match starts with the first block, on the run's clock
            rng = w.parts.range
            self.match = watch.match = MatchState(rng.game, rng.zones, watch._levels, t)
            self._see_match(t)
            if rng.game.rules is not None:
                self.rule_watch = RuleWatch(w, watch, rng.game.rules)
        next_signal = next_motion = t

        cams = [s.name for s in w.sensors.specs if s.kind == "camera"] if w.sensors is not None else []
        result.cameras = {c: [] for c in cams}

        def sample(t: float) -> None:
            nonlocal next_signal, next_motion
            for c in cams:          # after every step: only a change is kept
                seen, kept = list(w.sensors.reading(c).tags), result.cameras[c]
                if not kept or kept[-1][1] != seen:
                    kept.append([t, seen])
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
                    raise _End("cancelled", _w(lambda: i18n.t("you stopped the run at {t} s", t=f"{t:.1f}")))
                if self.match is not None:
                    if t >= self.match.end - EPS:       # the match plays to its end, whatever the blocks did
                        if not self._blocks_done:
                            self._unreached(t)
                        raise _End("finished", "finished" if self._blocks_done else _w(lambda: i18n.t(
                            "the match ended before the last block")))
                elif t >= self.program.time_limit - EPS:
                    raise _End("time limit", _w(lambda: i18n.t(
                        "ran out of time ({limit} s){under_way}", limit=f"{self.program.time_limit:g}",
                        under_way=self._under_way())))
                self._step()
                t = float(d.time)
                watch.update()
                self._track(t)
                if self.match is not None:
                    self._see_match(t)
                    if self.rule_watch is not None and t <= self.match.end + EPS:
                        self.rule_watch.see(t)
                sample(t)
                for check, cond in list(self.throughout):
                    if not watch.holds(cond):
                        placed = self._placed_hold(cond)
                        if placed is None:
                            self._fail(check, cond, t)
                        check["until"] = placed                 # the hold ended where a check expects the piece
                        self.throughout.remove((check, cond))
                if watch.tilt > TIP_DEG:
                    raise _End("tipped over", _w(lambda: i18n.t("the robot tipped over at {t} s", t=f"{t:.1f}")))
                if walk is not None:
                    on_ground, touched, _, _ = watch.mon.contact_state()
                    z = float(d.subtree_com[w.robot_body][2])
                    if walk.see(t, bool(watch.climb.get("climb.hanging", 0)), float(watch.climb.get(
                            "climb.hook_load", 0.0)), z, on_ground, touched) == "hook walked off":
                        def walked() -> str:
                            bars = i18n.t(" and ", context="list separator").join(walk.left) or i18n.t("its bar")
                            return i18n.t("a hook walked off {bars} at {t} s", bars=bars, t=f"{t:.1f}")
                        raise _End("walked off", _w(walked))
        except _End as e:
            result.status, ending = e.status, e.ending
        if result.status == "finished":
            t0 = t
            self._over = self.match is not None     # the buzzer: the settle after a match runs disabled
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
        top, finished = watch.highest_hung(), result.status == "finished"

        def outcome() -> str:
            said = _v(ending)
            if rows:
                told = sentence(rows, hopper=bool(self.program.hopper))
                said = told if finished else i18n.t("{ending}; {counts}", ending=said, counts=told)
            return i18n.t("reached {bar}, then {ending}", bar=top, ending=said) if top else said
        result.outcome = _w(outcome)
        result.t_end = t
        result.hung = list(watch.hung)
        for check in self.checks:
            check["line"] = _w(lambda: self._line(check))
        result.checks = self.checks
        result.marks = self.marks
        for rod in self._seated.values():
            got = watch.placed.seated(rod)
            if got is not None:
                result.seated[rod] = _w(lambda: seated_words(*got))
        result.notes = _ws(lambda: run_notes(self.world, self.program))
        tr = self.world.test_range
        if tr is not None and tr.shooter is not None:
            result.goals = dict(tr.counts()["goals"])
        if tr is not None:
            result.pieces = piece_log(tr)
        if self.air is not None:
            result.air = dict(self.air.out)
        result.clearance = watch.closest.result()
        if self.match is not None:      # the points so far at the run's end (a pause or teleop piece after it is out)
            # a run that reached the match's end is scored at the end: a last step a float error short still counts it
            at = max(t, self.match.end) if t >= self.match.end - EPS else t
            result.match = self.match.to_dict()
            result.points = self.match.points(tr.ball_log(), at=at).to_dict()
            result.rules = self.rule_watch.result() if self.rule_watch is not None else []
        _keep_words(result)
        return result

    def _unreached(self, t: float) -> None:
        """The match ended with blocks left: each `expect` that never ran fails ("the match ended before it"). That is
        the current block (unless it is under way), the rest of every open block list, and the bodies of the repeats
        among them."""
        left: list[tuple[tuple, object]] = []

        def walk(blocks, trail, start) -> None:
            for i in range(start, len(blocks)):
                b = blocks[i]
                if b.do == "expect":
                    left.append((trail + (i,), b))
                walk(b.body, trail + (i,), 0)

        last = len(self.frames) - 1
        for k, f in enumerate(self.frames):
            # the innermost list's current block is under way when `active`; an outer list's is the open repeat
            walk(f.blocks, f.trail, f.index + (1 if k < last or self.active is not None else 0))
        for place, b in sorted(left, key=lambda x: x[0]):
            cond = b.args["that"]
            self.checks.append({
                "place": list(place), "text": _w(lambda: self._describe(cond)),
                "throughout": bool(b.args.get("throughout", False)), "passed": False, "t": t, "timed": True,
                "failure": _w(lambda: i18n.t("the match ended before this check ran"))})

    def _see_match(self, t: float) -> None:
        """After a physics step (or at the start) at run time t: the match's state, and a mark when its phase
        changed."""
        w = self.world
        x, y = (float(c) for c in w.data.xpos[w.robot_body][:2])
        climb = self.watch.climb
        phase = self.match.see(t, x, y, int(climb.get("climb.level", 0)), float(climb.get("climb.harmony", 0.0)))
        if phase is not None:
            self.marks.append({"t": t, "kind": "phase", "phase": phase, "text": _w(lambda: phase_word(phase))})

    def _see_air(self) -> None:
        if self.air is not None:
            d = self.world.data
            self.air.see(float(d.time), float(self.world.model.opt.timestep))

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
        (motors as they were; after a match, disabled); the time it ends. Setting `cancel` ends it, and the run, at
        the next step."""
        if self.feeder is not None:
            self.feeder.stop_all()
        d = self.world.data
        t0 = t
        while not self.outcomes.settled() and t < t0 + SETTLE_MAX_S - EPS:
            if cancel is not None and cancel.is_set():
                raise _End("cancelled", _w(lambda: i18n.t("you stopped the run at {t} s", t=f"{t:.1f}")))
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
        if self.match is not None and (self.match.phase == "pause" or self._over):
            hal.apply(DISABLED)         # held disabled: World.step neutralizes the motors and holds the pneumatics
        bus = w.vbatt
        for joint, (turret, goal) in self.aims.items():
            self.drives[joint] = ("to", aim_target(w, turret, goal)[0])
        for joint, (kind, value) in self.drives.items():
            if kind == "to":
                value = position_volts(value, float(d.qpos[self._qadr[joint]]), not self._slide[joint], bus)
            for drive in self._drive[joint]:
                drive.write(hal, value, bus)
        w.step(hal)
        self._see_air()

    def _row(self, watch) -> dict[str, float]:
        d = self.world.data
        row = {}
        for name in self.signal_names:
            if name == "tilt":
                row[name] = watch.tilt
            elif name == "hook_load":
                row[name] = float(watch.climb.get("climb.hook_load", 0.0))
            elif name.startswith("climb."):             # climb.harmony (points), climb.hold.<chain>.robots
                row[name] = float(watch.climb.get(name, 0.0))
            elif name in self._seated:                  # inches; NaN while no piece is on the rod
                got = watch.placed.seated(self._seated[name])
                row[name] = got[0] / IN if got is not None else math.nan
            elif name == "aim_error":
                row[name] = math.degrees(aim_error(self.world, *self._aim_signal))
            elif name == "air.psi":
                row[name] = self.world.pneumatics.system.stored_psi
            elif name == "air.working_psi":
                row[name] = self.world.pneumatics.system.working_psi
            elif name in self._extension:
                row[name] = self.world.pneumatics.extension(self._extension[name]) / IN
            elif name in self._elements:                # deg or in
                row[name] = watch.element_value(self._elements[name])
            elif name in self._clearance:               # in; NaN while nothing is within 0.5 m
                row[name] = inches(watch.closest.now[self._clearance[name]])
            elif name in PARTNER_SIGNALS:               # in, deg, in
                row[name] = self.world.partner.read(name)
            elif name in self._sensors:                 # 0/1, in, or a color's index (NaN: none)
                row[name] = sensor_value(self.world, self._sensors[name])
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
                    if self.match is not None:      # a match plays to its end even when the blocks are done
                        self._blocks_done = True
                        return
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
                        raise _End("repeat limit", _w(lambda: i18n.tn(
                            "repeat ({where}) stopped after {count} time without {what}",
                            "repeat ({where}) stopped after {count} times without {what}",
                            f.rounds, where=_where(f.trail), what=self._describe(a["until"], "without"))))
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
        if b.do in ("spin", "stop"):
            self.aims.pop(a["motor"], None)
        elif b.do == "move":
            self.aims.pop(a["joint"], None)
        if b.do == "aim":
            j = f"{a['turret']}.{TURN}"
            self.aims[j] = self._aim_signal = (a["turret"], a["at"])
            self.drives[j] = ("to", aim_target(self.world, a["turret"], a["at"])[0])
            self._commanded[j] = t
            return True
        if b.do == "set_cylinder":
            to = a["to"]
            if to == "off":         # C9: the valve's spring position (an inverted single's extends)
                to = self.world.pneumatics.spring_state(a["cylinder"])
            self.world.pneumatics.command(a["cylinder"], to)
            return True
        if b.do == "spin":
            self.drives[a["motor"]] = ("volts", a["volts"])
            self._commanded[a["motor"]] = t
            return True
        if b.do == "stop":
            if a["motor"] == ALL_MOTORS:
                self._commanded.update({j: t for j in self.drives})
                self.drives.clear()
                self.aims.clear()
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
                check = {"place": list(place), "text": _w(lambda: self._describe(cond)), "throughout": False, "passed": True,
                         "t": t}
                self.checks.append(check)
                self._deferred = (check, cond)       # decided after the settle, on the final counts
                return True
            ok = watch.holds(cond)
            check = {"place": list(place), "text": _w(lambda: self._describe(cond)),
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
            timeout = a.get("timeout", WAIT_TIMEOUT)
            if self.match is not None and "timeout" not in a and a["until"].kind == "match":
                timeout = self.match.end - self.active["t0"] + 2 * EPS     # a wait for a phase lasts to the match's end
            if elapsed >= timeout - EPS:
                raise _End("timed out", _w(lambda: i18n.t("waiting for {what} timed out at {t} s ({where})",
                                                          what=self._describe(a["until"], "waiting"), t=f"{t:.1f}",
                                                          where=_where(place))))
            return False
        if b.do == "feed_one":
            f, name = self.feeder, a["station"]
            if f.count(name) > self.active["count"] or f.capped(name):
                return True
            if elapsed >= a.get("timeout", WAIT_TIMEOUT) - EPS:
                st = next(s for s in f.stations if s.name == name)
                blocked = f.blocked(name)

                def timed_out() -> str:
                    why = (i18n.t("its release point stayed blocked") if blocked
                           else i18n.t("its {interval} s between pieces hadn't passed", interval=f"{st.interval:g}"))
                    return i18n.t("feed one from {station} timed out at {t} s: {why} ({where})",
                                  station=name, t=f"{t:.1f}", why=why, where=_where(place))
                raise _End("timed out", _w(timed_out))
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
                def stopped() -> str:
                    p = dict(joint=j, at=self._value(j, a["unit"]), to=_amount(a["to"], a["unit"]),
                             where=_where(place))
                    return (i18n.t("{joint} stopped at {at} short of {to} ({where})", **p) if short
                            else i18n.t("{joint} stopped at {at} past {to} ({where})", **p))
                raise _End("timed out", _w(stopped))
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
            done = self._steer_turn(b, t)
            if not done and elapsed >= timeout - EPS:
                p = dict(where=_where(place), degrees=f"{a['degrees']:g}", timeout=f"{timeout:g}",
                         turned=f"{math.degrees(act['turned']):.0f}")
                # at its angle but still turning faster than TURN_RATE_TOL (a swing it hadn't damped yet)
                there = abs(a["degrees"] - math.degrees(act["turned"])) <= turn_tolerance(a["degrees"])
                raise _End("timed out", _w(lambda: i18n.t(
                    "turn ({where}) reached {degrees}° but was still turning when its {timeout} s ran out", **p)
                    if there else i18n.t("turn ({where}) didn't turn {degrees}° in {timeout} s (turned {turned}°)",
                                         **p)))
        elif "distance" in a:
            gone = float(np.linalg.norm(d.xpos[self.world.robot_body][:2] - act["from"]))
            done = gone >= abs(a["distance"]) - EPS
            if not done and elapsed >= timeout - EPS:
                def short() -> str:
                    if units.is_machine():          # the old words, kept for machine text (units.machine_text)
                        far, went = f"{abs(a['distance']):g} m", f"{gone:.2f} m"
                    else:
                        far, went = units.fmt(abs(a["distance"]), "field"), units.fmt(gone, "field", fewer=1)
                    p = dict(where=_where(place), far=far, timeout=f"{timeout:g}", went=went)
                    return (i18n.t("drive {direction} ({where}) didn't reach {far} in {timeout} s (went {went})",
                                   direction=a["direction"], **p) if "direction" in a
                            else i18n.t("drive ({where}) didn't reach {far} in {timeout} s (went {went})", **p))
                raise _End("timed out", _w(short))
        else:
            done = act["rolling"] is not None and t - act["rolling"] >= a["seconds"] - EPS
        if done:
            for j in self.drivetrain.wheels:
                self.drives.pop(j, None)
        return done

    def _steer_turn(self, b, t: float) -> bool:
        """A turn block's wheels this step, once they roll, and whether it is done. The volts follow the heading still
        to go and the turn rate (TURN_KP, TURN_KD, up to the block's volts, at least TURN_MIN_VOLTS while it is off by
        more than turn_tolerance), so the robot slows into its angle and pushes back when it coasts past, rather than
        coasting past it after the wheels stop (it went about 120° for 90). Done, once its wheels have started, within
        turn_tolerance and turning slower than TURN_RATE_TOL."""
        act, a = self.active, b.args
        prev = act.get("rate_at")
        rate = 0.0 if prev is None or t <= prev[0] + EPS else (act["turned"] - prev[1]) / (t - prev[0])
        act["rate_at"] = (t, act["turned"])
        err = math.degrees(math.radians(a["degrees"]) - act["turned"])
        rate = math.degrees(rate)
        tol = turn_tolerance(a["degrees"])
        if act["rolling"] is not None and abs(err) <= tol and abs(rate) <= TURN_RATE_TOL:
            return True
        if act["rolling"] is not None:
            cap = abs(a.get("volts", DRIVE_VOLTS))
            v = max(-cap, min(cap, TURN_KP * err - TURN_KD * rate))
            if abs(err) > tol and abs(v) < TURN_MIN_VOLTS:
                v = math.copysign(min(TURN_MIN_VOLTS, cap), err)
            for j, volts in self.drivetrain.wheel_volts(None, math.copysign(1.0, v), abs(v)).items():
                self.drives[j] = ("volts", volts)
        return False

    def _moving(self, t: float) -> str:
        """What was commanded (moved, spun or stopped) in the SLIP_LOOKBACK_S before t, most recent first, then
        anything else still turning at t: "claw.arm was moving", "claw.arm and claw.jaw were moving", the joints first,
        then "and the robot was driving"; "nothing was moving"."""
        recent = [n for _, n in sorted(((c, n) for n, c in self._commanded.items()
                                        if t - SLIP_LOOKBACK_S - EPS <= c <= t + EPS), reverse=True)]
        recent += sorted(n for n in self._turning.get(t, ()) if n not in recent)
        joints = [n for n in recent if n not in ("drive", "turn")]
        words = [i18n.t("{joint} was moving", joint=joints[0]) if len(joints) == 1 else
                 i18n.t("{joints} and {last} were moving", joints=", ".join(joints[:-1]), last=joints[-1])] \
            if joints else []
        words += [i18n.t("the robot was driving") if n == "drive" else i18n.t("the robot was turning")
                  for n in recent if n not in joints]
        return i18n.t(" and ", context="list separator").join(words) if words else i18n.t("nothing was moving")

    def _fail(self, check: dict, cond, t: float) -> None:
        check.update(passed=False, t=t, failure=_w(lambda: self._failure(cond)))
        if cond.kind == "held":
            i, by = self.watch.piece_index(cond.args["piece"]), cond.args["by"]
            loss = self.watch.held.lost(i, by)
            if loss is not None and self.watch.held.ever(i, by):
                check["timed"] = True              # its failure says its own time (the moment it was lost)
                self.marks.append({"t": loss[0], "kind": "slip", "text": check["failure"],
                                   "ball": self.watch.held.lost_piece(i, by)})
        def failed() -> str:
            p = dict(where=_where(tuple(check["place"])), failure=_v(check["failure"]))
            return (i18n.t("a check failed ({where}): {failure}", **p) if check.get("timed")
                    else i18n.t("a check failed ({where}): {failure} at {t} s", t=f"{t:.1f}", **p))
        raise _End("check failed", _w(failed))
