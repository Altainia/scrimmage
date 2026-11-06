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
(parts/partner.py); the `partner_lifted` and `partner_moved` conditions read them.

A robot with [[sensor]]s has each one's `sensor.<name>` signal: 0 or 1 for a beam break (1 while broken), the
distance in inches for a distance sensor (its max_range with nothing in range), and for a color sensor the index in
NAMED_COLORS of the color it sees (NaN while it sees none). The `sensor` condition reads the same readings, and the
replay lights each sensor's line from its signal.

Clearance (clearance.py): a test that names a field element in a `clears` condition or as `clearance.<element>` in
its chart measures the robot's clearance to it after every step (Watch.closest), and only then. The chart's
`clearance.<element>` is in inches, NaN while nothing is within 0.5 m; the run's `clearance` is each element's
smallest clearance and when (clearance_lines words it: "closest to trench: 0.82 in at 3.1 s", "trench: never within
20 in"). A failed `clears` check says the clearance then ("but it was 0.42 in").

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

from .. import i18n, units
from ..climb.run import SETTLE_S, WalkOff
from ..hal_drive import MotorDrive, driven_joints, position_volts
from ..movers import mover_poses, movers
from ..parts.partner import SIGNALS as PARTNER_SIGNALS, PartnerOverlap
from ..protocol import HalState
from ..sensors.colors import NAMED_COLORS
from ..testrange.build import BALL_BITS
from .aim import TURN, aim_error, aim_target
from .clearance import NEVER_WITHIN_IN, PREFIX, inches, named, uncounted_note
from .conditions import IN, Watch, _amount, _conds, describe, turret_words
from .drive import STEER_TIMEOUT, drivetrain
from .feeds import SETTLE_MAX_S, Outcomes, counts, is_standin, made_it_in, sentence
from .grip_watch import SET_DOWN, SLIP_LOOKBACK_S, default_word
from .palette import ALL_MOTORS, ANY_GOAL, ANY_HOOK, ANY_PIECE, NEXT_BAR, palette, problems
from .placed import KNOCK_OFF_SHARE, ON_PIECE, seated_words
from .program import (DRIVE_TIMEOUT, DRIVE_VOLTS, MOVE_TIMEOUT, REPEAT_MAX, WAIT_TIMEOUT, Program,
                      TestFileError, found_setup)
from .spread import no_ball, rest_of, spread_ball, start_pose

MOTION_HZ = 30            # mover poses a second, for the replay
SIGNAL_HZ = 50            # signal samples a second, for the chart
TIP_DEG = 60.0            # the robot body's up axis this far from vertical: it tipped over (spec, our call)
MOVE_TOL_DEG = 1.0        # a move has reached its target within this (a hinge), our call
MOVE_TOL_IN = 0.25        # ... or this (a slide), our call
EPS = 1e-9
MOVING_DEG_S = 5.0        # a joint turning faster than this (a hinge), our call, was moving at a slip
MOVING_IN_S = 0.5         # ... or sliding faster than this (a slide), our call
MOVING_SPEED = 0.1        # m/s: the robot going across the floor faster than this was driving at a slip, our call
COLOR_NAMES = list(NAMED_COLORS)     # a color sensor's signal is its color's index here (palette's sensor_colors)

# The verbs describe() uses, as "-ing" (after "without") and "to ..." (after "waiting for"): _recast's. A run says
# those forms with describe(form="without"/"waiting"), a whole phrase per form for a translator; _recast stays as
# the English rule they follow (tests/test_i18n_test_tab.py checks every condition's forms against it).
_VERBS = {"passes": ("passing", "to pass"), "goes": ("going", "to go"), "tilts": ("tilting", "to tilt"),
          "touches": ("touching", "to touch"), "reaches": ("reaching", "to reach"), "hangs": ("hanging", "to hang"),
          "are": ("being", "to be"), "is": ("being", "to be"), "made": ("having made", "to have made"),
          "is held": ("being held", "to be held"), "rests": ("resting", "to rest"),
          "clears": ("clearing", "to clear"), "moved": ("having moved", "to have moved"),
          # a color sensor's check: "without eye seeing red", "waiting for eye not to see red"
          "is seeing": ("seeing", "to see"), "isn't seeing": ("losing sight of", "not to see")}


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
    # a run made inside machine text (a sweep's, a spread's, MCP test_run's): its words in every Language and Units
    # setting, {"en-GB|metric": {outcome, checks, marks, seated, notes}} (_keep_words), which the Test tab shows in
    # the viewer's; None for any other run (its words are in the setting it was made in)
    words: dict | None = None

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


class _Words(str):
    """Machine text with its words in every Language and Units setting (units.each_setting), for a run made inside
    units.machine_text() (a sweep's, a spread's or MCP test_run's): the str itself is the machine text, `each` the
    words by setting. The run's end files them in RunResult.words and leaves plain text (_keep_words)."""

    def __new__(cls, text: str, each: dict):
        s = super().__new__(cls, text)
        s.each = each
        return s

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


def _w(fn) -> str:
    """fn()'s text; inside machine text, as _Words with fn() in every setting too, while the run's numbers are at
    hand (a kept run is shown in the viewer's setting)."""
    text = fn()
    return _Words(text, units.each_setting(fn)) if units.is_machine() else text


def _v(text):
    """Text made earlier (by _w) as it reads in the setting being worded now; itself in machine text."""
    if isinstance(text, _Words) and not units.is_machine():
        return text.each.get(units.setting_key(), str(text))
    return text


def _ws(fn) -> list:
    """_w() for a list of lines (each line worded in every setting)."""
    lines = fn()
    if not units.is_machine():
        return lines
    each = units.each_setting(fn)
    return [_Words(line, {k: v[i] if len(v) == len(lines) else line for k, v in each.items()})
            for i, line in enumerate(lines)]


WORDED_CHECK = ("text", "failure", "line", "until")     # a check's fields in words


def _keep_words(result: RunResult) -> None:
    """Inside machine text: file the run's words in every setting in result.words ({setting_key: {outcome, checks
    (each check's WORDED_CHECK fields), marks (each mark's text), seated, notes}}), and leave the machine text in
    the result as plain str (MCP results and result.json's other fields don't change)."""
    if not units.is_machine():
        return

    def pick(x, key):
        return x.each.get(key, str(x)) if isinstance(x, _Words) else x

    keys = list(units.each_setting(lambda: None))
    result.words = {key: {
        "outcome": pick(result.outcome, key),
        "checks": [{f: pick(c[f], key) for f in WORDED_CHECK if isinstance(c.get(f), str)} for c in result.checks],
        "marks": [pick(m.get("text"), key) for m in result.marks],
        "seated": {rod: pick(v, key) for rod, v in result.seated.items()},
        "notes": [pick(n, key) for n in result.notes]} for key in keys}
    result.outcome = str(result.outcome)
    for c in result.checks:
        for f in WORDED_CHECK:
            if isinstance(c.get(f), str):
                c[f] = str(c[f])
    for m in result.marks:
        if isinstance(m.get("text"), str):
            m["text"] = str(m["text"])
    result.seated = {rod: str(v) for rod, v in result.seated.items()}
    result.notes = [str(n) for n in result.notes]


def _where(place: tuple) -> str:
    """0-based place -> "block 3 inside block 2"."""
    words = i18n.t("block {n}", n=place[-1] + 1)
    for i in reversed(place[:-1]):
        words = i18n.t("{block} inside block {n}", block=words, n=i + 1)
    return words


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


def sensor_value(world, name: str) -> float:
    """Sensor `name`'s chart signal now: 0 or 1 for a beam break (1 broken), inches for a distance sensor, and for a
    color sensor the index in NAMED_COLORS of the color it sees (NaN: none)."""
    kind = next(s.kind for s in world.sensors.specs if s.name == name)
    r = world.sensors.reading(name)
    if kind == "beam":
        return 1.0 if r.on else 0.0
    if kind == "distance":
        return r.distance / IN
    return float(COLOR_NAMES.index(r.color_name)) if r.on else math.nan


def setup_config(raw: dict, setup: dict, cfg_dir: Path | None = None) -> dict:
    """A copy of the config dict with the test's `[setup]` ball or piece, station settings and seed in its `[range]`
    (a station's settings add to, and replace, the config's own; a ball or piece replaces the config's own, whichever
    it gave), and its partner as a `[partner]` (setup_partner). Nothing to set leaves `raw` as it is. On the plain
    field (no `[range]`) the seed is dropped: it only picks the stations' draws, and there are none. `cfg_dir` is the
    config's folder, where a partner's robot start reads the config's own range file from."""
    raw = _range_setup(raw, setup)
    if "partner" in setup:
        raw = setup_partner(raw, setup["partner"], cfg_dir)
    return raw


def setup_partner(raw: dict, partner, cfg_dir: Path | None) -> dict:
    """A copy of the config dict with Setup's partner: false drops the config's `[partner]`; a table puts the
    partner's center `ahead_in` ahead of the robot's start along its heading, turned `heading_deg` from it, its brake
    as given (the config's `[partner]` keeps its other settings)."""
    if partner is False:
        return {k: v for k, v in raw.items() if k != "partner"}
    from ..parts.assemble import robot_start
    try:
        x, y, heading = robot_start(raw, cfg_dir)
    except ValueError as e:     # the config's own mistake (its [range], its start_pose), which building it says too
        raise TestFileError(i18n.t("Setup's partner needs the robot's start: {why}", why=e)) from None
    h, ahead = math.radians(heading), partner["ahead_in"] * IN
    have = raw.get("partner")
    out = {**(have if isinstance(have, dict) else {}),
           "start": [x + ahead * math.cos(h), y + ahead * math.sin(h), heading + partner.get("heading_deg", 0.0)]}
    if "brake" in partner:
        out["brake"] = partner["brake"]
    return {**raw, "partner": out}


def _range_setup(raw: dict, setup: dict) -> dict:
    """setup_config's `[range]` part: the ball or piece, the station settings and the seed."""
    values = {k: setup[k] for k in ("ball", "piece", "station", "seed") if k in setup}
    if not isinstance(raw.get("range"), dict):
        values.pop("seed", None)
        if values:          # palette.problems says so too, so Setup shows it before Run
            raise TestFileError(i18n.t("Setup's ball, piece and station settings need a field; this robot is on the "
                                       "plain field"))
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
    """The World of the test's robot config (its `robot`, relative to the test file), with the test's setup applied.
    When Setup places the partner and it starts inside the robot or an element, the TestFileError says Setup's partner
    ("the partner robot (Setup) starts inside the robot (their outlines overlap by 7.87 in)", in the Units setting;
    machine text keeps the old 0.20 m), not the config's [partner]: the student only ticked Setup."""
    from ..config import ConfigError, build_world, load_config
    config = load_config(Path(program.path).parent / program.robot)
    try:
        return build_world(replace(config, raw=setup_config(config.raw, found_setup(program), config.dir)))
    except ConfigError as e:
        overlap = partner_overlap(e)
        if not isinstance(program.setup.get("partner"), dict) or overlap is None:
            raise
        raise TestFileError(setup_partner_words(overlap)) from None


def partner_overlap(e: BaseException) -> PartnerOverlap | None:
    """The PartnerOverlap a ConfigError came from (build_world re-raises partner.check_start's `from None`, so it is
    the error's context), found by its type, never by its words (L3); None for any other error."""
    seen = 0
    cause = e
    while cause is not None and seen < 8:
        if isinstance(cause, PartnerOverlap):
            return cause
        cause, seen = cause.__cause__ or cause.__context__, seen + 1
    return None


def setup_partner_words(overlap: PartnerOverlap) -> str:
    """Setup's partner refusal from the config's PartnerOverlap: in the Units setting ("... by 7.87 in)"), or in
    machine text in meters, as the config's own refusal was before the setting ("... by 0.20 m)")."""
    by = f"{overlap.depth:.2f} m" if units.is_machine() else units.fmt(overlap.depth, "length", fewer=1)
    return i18n.t("the partner robot (Setup) starts inside {what} (their outlines overlap by {by})",
                  what=overlap.what, by=by)


GRIP_CONDITIONS = ("held", "rests_on", "seated")     # a test of these rests on the grip's friction
DEFAULT_GRIP_NOTE = i18n.N_("grip: the pads have MuJoCo's default friction 1.0 — about grip tape's; the larger of "
                            "pad and piece applies — an UNCALIBRATED estimate; pick a grip material for them in the "
                            "Part tab, or measure yours with a sled pull (docs/test-tab.md)")


def default_grip_note(world) -> str:
    """The note for a test of held, rests on or seated with no grip anywhere: DEFAULT_GRIP_NOTE, or, when
    part.toml sets a pad's friction, the pads' own values (MuJoCo's default 1.0 for a pad it leaves alone)."""
    from ..parts.grip import pad_frictions
    pads = pad_frictions(world)
    if all(mu is None for mu in pads.values()):
        return i18n.translated(DEFAULT_GRIP_NOTE)
    mus = set(pads.values())
    if len(mus) == 1:
        return i18n.t("grip: the pads have friction {friction}, which part.toml gives them; the larger of pad and "
                      "piece applies — an UNCALIBRATED estimate; pick a grip material for them in the Part tab, or "
                      "measure yours with a sled pull (docs/test-tab.md)", friction=f"{mus.pop():g}")
    each = ", ".join(i18n.t("{body} {friction}", body=b, friction=f"{mu:g}") if mu is not None
                     else i18n.t("{body} MuJoCo's default 1.0", body=b) for b, mu in pads.items())
    return i18n.t("grip: the pads have the friction part.toml gives them ({pads}); the larger of pad and piece "
                  "applies — an UNCALIBRATED estimate; pick a grip material for them in the Part tab, or measure "
                  "yours with a sled pull (docs/test-tab.md)", pads=each)


PNEUMATIC_ESTIMATES = i18n.N_("pneumatic numbers are estimates")      # after measure/labels.py's mark


def pneumatic_note(cylinders) -> str:
    """What a robot's air rests on: each cylinder's valve conductance, measured (its CylinderSpec's provenance, from a
    stock cylinder's `measured` file) or still an estimate."""
    from ..measure.labels import line
    keys = tuple(f"{c.name} valve conductance" for c in cylinders)
    inputs = {k: c.provenance for k, c in zip(keys, cylinders)}
    return i18n.t("{line}; see docs/pneumatics.md.", line=line(inputs, keys, estimates=PNEUMATIC_ESTIMATES))


def piece_notes(world) -> list[str]:
    """One line per field piece whose friction a measurement file gives ([piece.<name>] friction = "<file>"), worded
    as a measured grip's: "crate piece: measured, friction 0.8 (tread.measure.toml, J. Smith, 2026-11-10, 3
    trials)"."""
    from ..parts.grip import measured_label
    tr = getattr(world, "test_range", None)
    kinds = getattr(getattr(tr, "range", None), "kinds", None) or {}
    return [f"{kind.name} piece: {measured_label(kind.friction, kind.provenance['friction'])}"
            for kind in kinds.values() if "friction" in kind.provenance]


def run_notes(world, program: Program) -> list[str]:
    """What the run's numbers rest on: a line per gripped body (parts/grip.py's grip_notes); a test of held, rests on
    or seated with no grip anywhere gets default_grip_note, so its grip isn't read as measured either. A field piece
    whose friction is measured gets piece_notes' line. A robot with cylinders gets pneumatic_note. A test that
    measures an element's clearance, on a robot with stock parts whose collide is off, gets clearance.uncounted_note:
    those parts aren't measured."""
    from ..parts.grip import grip_notes
    notes = grip_notes(world)
    if not notes and any(_conds(program.blocks, k) for k in GRIP_CONDITIONS):
        notes = [default_grip_note(world)]
    notes += piece_notes(world)
    if getattr(world, "pneumatics", None) is not None:
        notes.append(pneumatic_note(world.parts.cylinders))
    uncounted = uncounted_note(world, program)
    if uncounted is not None:
        notes.append(uncounted)
    return notes


def goal_lines(result: RunResult) -> list[str]:
    """The run's goal counts as lines, "scored 5 in hub_blue", one per goal with a ball in it (none without a
    [shooter])."""
    return [i18n.t("scored {count} in {goal}", count=n, goal=name) for name, n in (result.goals or {}).items() if n]


def clearance_lines(result: RunResult) -> list[str]:
    """The run's closest approaches as lines, one per element it measured: "closest to trench: 0.82 in at 3.1 s", or
    "trench: never within 20 in" (none for a run that measured none)."""
    if units.is_machine():                  # the old words, kept for machine text (units.machine_text)
        return [f"{e}: never within {NEVER_WITHIN_IN} in" if c["min_in"] is None else
                f"closest to {e}: {c['min_in']:.2f} in at {c['t']:.1f} s" for e, c in (result.clearance or {}).items()]
    return [i18n.t("{element}: never within {distance}", element=e, distance=_amount(NEVER_WITHIN_IN, "in"))
            if c["min_in"] is None else
            i18n.t("closest to {element}: {distance} at {t} s", element=e, distance=_amount(c["min_in"], "in", fewer=1),
                   t=f"{c['t']:.1f}")
            for e, c in (result.clearance or {}).items()]


def air_lines(result: RunResult) -> list[str]:
    """The run's air as lines (none without cylinders): "air: 120 psi at the start, 84 psi at the end, lowest 81 psi
    at 14.2 s; the compressor ran 9.0 s", and when the tanks fell below the working pressure, a warning (Review
    Focus 5)."""
    a = result.air
    if a is None:
        return []
    def psi(x: float) -> str:
        return f"{x:.0f} psi" if units.is_machine() else _amount(x, "psi", fewer=1)

    lines = [i18n.t("air: {start} at the start, {end} at the end, lowest {low} at {t} s; the compressor ran {ran} s",
                    start=psi(a["start_psi"]), end=psi(a["end_psi"]), low=psi(a["low_psi"]), t=f"{a['low_t']:.1f}",
                    ran=f"{a['compressor_s']:.1f}")]
    if a["below_working_t"] is not None:
        lines.append(i18n.t("the tanks fell below the {working} working pressure at {t} s: the cylinders pushed weaker "
                            "from then on", working=_amount(a["working_psi"], "psi"), t=f"{a['below_working_t']:.1f}"))
    return lines


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


class TestRun:
    __test__ = False     # not a pytest class

    def __init__(self, world, program: Program, placement: dict | None = None):
        """`placement`: a floor spread's {offset_in, distance_in, angle_deg, rotation_deg, tip_deg}
        (spread.placements)."""
        check_problems(program, world)
        self.world, self.program = world, program
        m = world.model
        self.motors = driven_joints(world)
        # every motor on each joint (a slot of several motors, a lead and its followers, shares one joint), each
        # written as robot code writes it, through its own binding
        self._drive = {n: [MotorDrive(mb) for mb in world.motors if mb.qadr == b.qadr]
                       for n, b in self.motors.items()}
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
                    raise _End("cancelled", _w(lambda: i18n.t("you stopped the run at {t} s", t=f"{t:.1f}")))
                if t >= self.program.time_limit - EPS:
                    raise _End("time limit", _w(lambda: i18n.t(
                        "ran out of time ({limit} s){under_way}", limit=f"{self.program.time_limit:g}",
                        under_way=self._under_way())))
                self._step()
                t = float(d.time)
                watch.update()
                self._track(t)
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
        if self.air is not None:
            result.air = dict(self.air.out)
        result.clearance = watch.closest.result()
        _keep_words(result)
        return result

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
        (motors as they were); the time it ends. Setting `cancel` ends it, and the run, at the next step."""
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
            if elapsed >= a.get("timeout", WAIT_TIMEOUT) - EPS:
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
            done = act["turned"] * math.copysign(1.0, a["degrees"]) >= math.radians(abs(a["degrees"])) - EPS
            if not done and elapsed >= timeout - EPS:
                raise _End("timed out", _w(lambda: i18n.t(
                    "turn ({where}) didn't turn {degrees}° in {timeout} s (turned {turned}°)", where=_where(place),
                    degrees=f"{a['degrees']:g}", timeout=f"{timeout:g}", turned=f"{math.degrees(act['turned']):.0f}")))
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

    def _value(self, joint: str, unit: str) -> str:
        """A joint's position now in words (`38.2°`, `1.5 in` or `38 mm`): degrees for a `unit` of "deg", else
        a length in the Units setting."""
        q = float(self.world.data.qpos[self._qadr[joint]])
        if unit == "deg":
            return _amount(round(math.degrees(q), 1) + 0.0, unit)
        if units.is_machine():                  # the old words: the file's unit, to 0.1
            return _amount(round(q / IN if unit == "in" else q, 1), unit)
        return units.fmt(q, "length", fewer=2)

    def _failure(self, cond) -> str:
        """What happened instead of `cond`, in words."""
        a, k, watch = cond.args, cond.kind, self.watch
        t = i18n.t

        def bar(name: str) -> str:
            return (watch.next_bar() or t("the next bar")) if name == NEXT_BAR else name

        if k == "joint":
            p = dict(joint=a["name"], at=self._value(a["name"], a["unit"]))
            if "above" in a:
                return t("expected {joint} above {limit}, but it was {at}", limit=_amount(a["above"], a["unit"]), **p)
            return t("expected {joint} below {limit}, but it was {at}", limit=_amount(a["below"], a["unit"]), **p)
        if k == "tilt":
            tilted = _amount(round(watch.tilt, 1), "deg")
            if "above" in a:
                return t("expected the robot to tilt more than {limit}, but it tilted {tilted}",
                         limit=_amount(a["above"], "deg"), tilted=tilted)
            return t("expected the robot to tilt less than {limit}, but it tilted {tilted}",
                     limit=_amount(a["below"], "deg"), tilted=tilted)
        if k == "touch":
            if a["robot"] == ANY_HOOK:
                return t("no hook touched {target}", target=bar(a["field"]))
            return t("{part} didn't touch {target}", part=a["robot"], target=bar(a["field"]))
        if k == "piece_in":
            if a["piece"] == ANY_PIECE:
                return t("no {piece} was in {body}", piece=default_word(watch.kinds), body=a["body"])
            return t("{piece} wasn't in {body}", piece=watch.piece_names.get(a["piece"], a["piece"]), body=a["body"])
        if k == "in_hopper":
            n = watch.hopper_count()
            if "at_least" in a:
                return i18n.tn("expected {want} or more pieces in the hopper, but {count} piece was in it",
                               "expected {want} or more pieces in the hopper, but {count} pieces were in it",
                               n, want=a["at_least"])
            return i18n.tn("expected at most {want} pieces in the hopper, but {count} piece was in it",
                           "expected at most {want} pieces in the hopper, but {count} pieces were in it",
                           n, want=a["at_most"])
        if k == "scored":
            n = watch.scored_count(a["goal"])
            if a["goal"] == ANY_GOAL:
                return i18n.tn("expected {want} or more balls scored in any goal, but {count} was",
                               "expected {want} or more balls scored in any goal, but {count} were", n,
                               want=a["at_least"])
            return i18n.tn("expected {want} or more balls scored in {goal}, but {count} was",
                           "expected {want} or more balls scored in {goal}, but {count} were", n,
                           want=a["at_least"], goal=a["goal"])
        if k == "aimed":
            target, ok = aim_target(self.world, a["turret"], a["at"])
            if not ok:
                return t("{goal} is outside the turret's range (it stops at {stop}°)", goal=a["at"],
                         stop=f"{round(math.degrees(target), 1):g}")
            off = math.degrees(aim_error(self.world, a["turret"], a["at"]))
            return t("expected {turret} aimed at {goal} within {within}, but it was {off}° off",
                     turret=turret_words(a["turret"]), goal=a["at"], within=_amount(a["within_deg"], "deg"),
                     off=f"{abs(off):.1f}")
        if k == "cylinder":
            out = self.world.pneumatics.extension(a["cylinder"]) / IN
            stroke = next(c.stroke for c in self.world.parts.cylinders if c.name == a["cylinder"]) / IN
            if units.is_machine():
                p = dict(cylinder=a["cylinder"], out=f"{out:.1f} in", stroke=f"{round(stroke, 1):g} in")
            else:
                p = dict(cylinder=a["cylinder"], out=_amount(out, "in", fewer=2), stroke=_amount(stroke, "in", fewer=2))
            if a["is"] == "extended":
                return t("expected {cylinder} extended, but its rod was out {out} of {stroke}", **p)
            return t("expected {cylinder} retracted, but its rod was out {out} of {stroke}", **p)
        if k == "pressure":
            side, psi = ("above", a["above"]) if "above" in a else ("below", a["below"])
            s = self.world.pneumatics.system
            now = s.stored_psi if a["side"] == "stored" else s.working_psi
            # as the block reads: "stored air pressure above 60 psi"
            if units.is_machine():
                p = dict(limit=f"{psi:g} psi", now=f"{now:.1f} psi")
            else:
                p = dict(limit=_amount(psi, "psi"), now=_amount(now, "psi"))
            return {("stored", "above"): t("expected the stored air pressure above {limit}, but it was {now}", **p),
                    ("stored", "below"): t("expected the stored air pressure below {limit}, but it was {now}", **p),
                    ("working", "above"): t("expected the working air pressure above {limit}, but it was {now}", **p),
                    ("working", "below"): t("expected the working air pressure below {limit}, but it was {now}", **p),
                    }[a["side"], side]
        if k == "element":
            side = "above" if "above" in a else "below"
            unit = watch.units[a["element"]]
            now = watch.element_value(a["element"])
            if unit == "deg" or units.is_machine():                    # machine text: the old words, to 0.1
                now = round(now, 1) + 0.0                                 # + 0.0: a panel at rest never reads "-0"
            p = dict(element=a["element"], limit=_amount(a[side], unit), now=_amount(now, unit, fewer=2))
            if side == "above":
                return t("expected {element} above {limit}, but it was {now}", **p)
            return t("expected {element} below {limit}, but it was {now}", **p)
        if k == "clears":
            c = watch.closest.now[a["element"]]
            if units.is_machine():
                now = f"{c / IN:.2f} in" if math.isfinite(c) else f">{NEVER_WITHIN_IN} in"
            else:
                now = units.fmt(c, "length", fewer=1) if math.isfinite(c) else f">{_amount(NEVER_WITHIN_IN, 'in')}"
            return t("expected the robot to clear {element} by at least {limit}, but it was {now}",
                     element=a["element"], limit=_amount(a["at_least_in"], "in"), now=now)
        if k == "sensor":
            return self._sensor_failure(a)
        if k == "partner_lifted":
            p, level = self.world.partner, a.get("level_within_deg")
            want = (t("at least {lift} (level within {level})", lift=_amount(a["at_least_in"], "in"),
                      level=_amount(level, "deg")) if level is not None
                    else t("at least {lift}", lift=_amount(a["at_least_in"], "in")))
            if units.is_machine():
                up = round(p.height(), 1) + 0.0             # + 0.0: a partner at rest never reads "-0"
                up = f"{up:g} in"
            else:
                up = _amount(p.height(), "in", fewer=2)
            return t("expected the partner lifted {want}, but it was {up} up, {tilt}° from level", want=want, up=up,
                     tilt=f"{p.tilt():.1f}")
        if k == "partner_moved":
            moved = (f"{self.world.partner.moved():.1f} in" if units.is_machine()
                     else _amount(self.world.partner.moved(), 'in', fewer=2))
            return t("expected the partner to move at least {limit}, but it moved {moved}",
                     limit=_amount(a["at_least_in"], "in"), moved=moved)
        if k == "station_empty":
            left = self.feeder.left(a["station"]) if self.feeder is not None else None
            if left:
                return t("{station} wasn't empty ({left} left)", station=a["station"], left=left)
            return t("{station} wasn't empty", station=a["station"])
        if k == "made_it_in":
            c = self._final if self._final is not None else self.outcomes.so_far()
            return t("expected at least {share}% to make it in, but {made} of {fed} did", share=f"{a['at_least']:g}",
                     made=c["in"], fed=c["fed"])
        if k == "hanging_from":
            return t("the robot wasn't hanging from {bar}", bar=bar(a["bar"]))
        if k == "held":
            return self._not_held(a)
        if k in ("rests_on", "seated"):
            return self._not_placed(k, a)
        return t("the robot wasn't hanging at {level}", level=a["level"])           # level

    def _sensor_failure(self, a: dict) -> str:
        """What a sensor read instead: a beam broken or clear, a distance, the color seen."""
        t = i18n.t
        name, want = a["sensor"], a["is"]
        spec = next(s for s in self.world.sensors.specs if s.name == name)
        r = self.world.sensors.reading(name)
        if spec.kind in ("beam", "distance"):
            wanted = t("on", context="sensor") if want == "on" else t("off", context="sensor")
        if spec.kind == "beam":
            return t("expected {sensor} {want}, but it was {reading}", sensor=name, want=wanted,
                     reading=t("on (broken)") if r.on else t("off (clear)"))
        if spec.kind == "distance":
            if units.is_machine():
                p = dict(reading=f"{r.distance / IN:.1f} in", threshold=f"{spec.threshold / IN:.1f} in")
            else:
                p = dict(reading=units.fmt(r.distance, "length", fewer=2),
                         threshold=units.fmt(spec.threshold, "length", fewer=2))
            return t("expected {sensor} {want}, but it read {reading} (it is on closer than {threshold})", sensor=name,
                     want=wanted, **p)
        if not r.on:
            saw = t("no piece")
        elif r.color_name[0] in "aeiou":
            saw = t("an {color} piece", color=r.color_name)
        else:
            saw = t("a {color} piece", color=r.color_name)
        what = a["sees"] or t("a piece")
        if want == "on":
            return t("expected {sensor} to see {what}, but it saw {saw}", sensor=name, what=what, saw=saw)
        return t("expected {sensor} not to see {what}, but it saw {saw}", sensor=name, what=what, saw=saw)

    def _not_held(self, a: dict) -> str:
        """How a held check failed: when and how the piece was lost, and what was moving in the second before; or
        that it was never held. It carries its own time (the moment it was lost). How it was lost is grip_watch.py's
        words, in English."""
        watch = self.watch
        i, by = watch.piece_index(a["piece"]), a["by"]
        loss = watch.held.lost(i, by) if watch.held.ever(i, by) else None
        named = watch.piece_names.get(a["piece"], a["piece"]) if i is not None else None
        if loss is None:
            if named:
                return i18n.t("{piece} was never held by {by}", piece=named, by=by)
            return i18n.t("the {piece} was never held by {by}", piece=default_word(watch.kinds), by=by)
        t, how, kind = loss
        lost = f"{how} {by}" if how in ("slipped out of", "slid in") else how
        p = dict(lost=lost, t=f"{t:.1f}", moving=self._moving(t))
        if named:
            return i18n.t("{piece} {lost} at {t} s while {moving}", piece=named, **p)
        return i18n.t("the {piece} {lost} at {t} s while {moving}", piece=kind, **p)

    def _placed_hold(self, cond) -> str | None:
        """A held throughout check that stopped holding because the piece was set down on a rod or element a `seated`
        or `rests_on` condition of the test names (controller ruling P34): "until it was set down on branch_l2 at
        4.4 s" (set down, not placed: the touch may not stay). None for any other loss, and for any other condition."""
        if cond.kind != "held":
            return None
        held = self.watch.held
        i, by = self.watch.piece_index(cond.args["piece"]), cond.args["by"]
        loss = held.lost(i, by) if held.ever(i, by) else None
        if loss is None or not loss[1].startswith(SET_DOWN):
            return None
        on = loss[1].removeprefix(SET_DOWN)
        if on not in self._places:
            return None
        return _w(lambda: i18n.t("until it was set down on {place} at {t} s", place=on, t=f"{loss[0]:.1f}"))

    def _not_placed(self, k: str, a: dict) -> str:
        """How a rests_on or seated check failed: "the coral doesn't rest on shelf (a robot body still touches it)";
        "the coral isn't on branch_l2", "the coral is seated only 0.8 in on branch_l2 (could be knocked off)", or,
        on but short of what was asked, "the coral is seated 2 in of 4 in on branch_l2, less than 3 in"."""
        watch = self.watch
        i, on = watch.piece_index(a["piece"]), a["on"]
        what = (watch.piece_names.get(a["piece"], a["piece"]) if i is not None
                else i18n.t("the {piece}", piece=default_word(watch.kinds)))
        if k == "rests_on":
            why = watch.placed.why_not(i, on)
            if why:
                return i18n.t("{piece} doesn't rest on {place} ({why})", piece=what, place=on, why=why)
            return i18n.t("{piece} doesn't rest on {place}", piece=what, place=on)
        got = watch.placed.seated(on, i)
        if got is None:
            return i18n.t("{piece} isn't on {place}", piece=what, place=on)
        depth, could = got
        if depth < KNOCK_OFF_SHARE * could:
            shown = f"{depth / IN:.1f} in" if units.is_machine() else units.fmt(depth, "length", fewer=2)
            return i18n.t("{piece} is seated only {depth} on {place} (could be knocked off)", piece=what, depth=shown,
                          place=on)
        return i18n.t("{piece} is {seated} on {place}, less than {want}", piece=what, seated=seated_words(depth, could),
                      place=on, want=_amount(a["at_least_in"], "in"))

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

    def _describe(self, cond, form: str = "is") -> str:
        """describe() with this run's next bar, the field's piece words, its moving elements' units and the robot's
        sensors' kinds; `form` as describe()'s."""
        w, sensors = self.watch, getattr(self.world, "sensors", None)
        kinds = {s.name: s.kind for s in sensors.specs} if sensors is not None else None
        return describe(cond, w.next_bar(), w.kinds, w.piece_names, w.units, kinds, form=form)

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

    def _doing(self, b) -> str:
        """A waiting block under way, in words."""
        a = b.args
        if b.do == "wait":
            return i18n.t("waiting {seconds} s", seconds=f"{a['seconds']:g}")
        if b.do == "wait_until":
            return i18n.t("waiting for {what}", what=self._describe(a["until"], "waiting"))
        if b.do == "move":
            return i18n.t("moving {joint} to {to}", joint=a["joint"], to=_amount(a["to"], a["unit"]))
        if b.do == "feed_one":
            return i18n.t("feeding one from {station}", station=a["station"])
        if b.do == "turn":
            return i18n.t("turning {degrees}°", degrees=f"{a['degrees']:g}")
        if "distance" in a:
            far = f"{a['distance']:g} m" if units.is_machine() else units.fmt(a["distance"], "field")
            return (i18n.t("driving {direction} {distance}", direction=a["direction"], distance=far)
                    if "direction" in a else i18n.t("driving {distance}", distance=far))
        s = f"{a['seconds']:g}"
        return (i18n.t("driving {direction} for {seconds} s", direction=a["direction"], seconds=s)
                if "direction" in a else i18n.t("driving for {seconds} s", seconds=s))

    def _under_way(self) -> str:
        """The innermost repeat and the block under way, for the time-limit sentence ("" for neither)."""
        f = self.frames[-1]
        doing = None
        if self.active is not None and f.index < len(f.blocks):
            doing = self._doing(f.blocks[f.index]), f.trail + (f.index,)
        if len(self.frames) > 1:
            return (i18n.t(" during repeat ({where}), {doing}", where=_where(f.trail), doing=doing[0]) if doing
                    else i18n.t(" during repeat ({where})", where=_where(f.trail)))
        return i18n.t(", {doing} ({where})", doing=doing[0], where=_where(doing[1])) if doing else ""

    def _line(self, c: dict) -> str:
        p = dict(where=_where(tuple(c["place"])), t=f"{c['t']:.1f}")
        if c["passed"]:
            if c.get("until"):
                return i18n.t("passed: {check} from {t} s {until} ({where})", check=_v(c["text"]),
                              until=_v(c["until"]), **p)
            if c["throughout"]:
                return i18n.t("passed: {check} from {t} s on ({where})", check=_v(c["text"]), **p)
            return i18n.t("passed: {check} at {t} s ({where})", check=_v(c["text"]), **p)
        if c.get("timed"):
            return i18n.t("failed ({where}): {failure}", failure=_v(c["failure"]), where=p["where"])
        return i18n.t("failed ({where}): {failure} at {t} s", failure=_v(c["failure"]), **p)
