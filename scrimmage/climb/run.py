"""`climb run`: play a climb script (script.py) on a robot config's climbing range with no robot program, and report
the climb: a timeline, the peak hook load, the swing, the 2022 robot limits and how it ended (docs/climbing.md).

The runner takes the robot program's place: every physics step it writes the motor controllers' outputs, so each
drive goes through the config's own motor, gearing, battery and neutral mode (a motor's speed and torque limits
apply, and a `release` motor in brake mode slows its latch as it would on the robot); every motor bound to a joint
(a lead and its followers) is written, as the Test tab's runs (scrimmage/blocks/run.py) write them. A position drive
is a proportional loop on the joint: KP_SLIDE or KP_HINGE (scrimmage/hal_drive.py) volts per unit of error, clipped to the bus (or the drive's
max_voltage), as a team's position loop on a climber; the motor's own back-EMF damps it.

The run settles the robot on the carpet for SETTLE_S, then plays the steps from t = 0. Every physics step it reads
the climb signals (scrimmage/terrain/climb.py) and checks, in this order:
- "hit a bar": a robot geom that isn't part of a hook assembly (the hooks and their bracket) presses on a bar (or
  switch) harder than the limits' hit_force, its contacts with that bar summed;
- "hook walked off": the robot has hung (HANG_CONFIRM_S of climb.hanging), let go of every bar while a hook carried
  it (more than LOADED_N at some step within CARRY_WINDOW_S before it let go), and then dropped: its CoM more than
  WALK_OFF_DROP below where it let go, or it touched the ground, whatever it lands on;
- "fell": the robot has hung and touches the ground, with no carried hook letting go before it (a descent that
  slides or is lowered down, a deliberate one included);
- "timed out at step N": step N's wait didn't happen within its timeout.
A gap with no real descent (a hook bouncing on its bar, the robot back on a bar within WALK_OFF_DROP) is neither.
Each ends the run. A run that plays every step ends "reached <LEVEL>", the level it scores then, or "not hanging".
G107 (extension beyond the frame perimeter) and G106 (the robot's height in its own frame) breaches are notes.
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
from ..hal_drive import driven_joints, joint_drives, position_volts
from ..protocol import HalState
from .script import Script, ScriptError, Wait

SETTLE_S = 0.3            # s on the carpet, motors at neutral, before step 1
HANG_CONFIRM_S = 0.1      # s of climb.hanging before the robot counts as hung (a margin contact can read 1 a step early)
LOADED_N = 50.0           # N: a hook carrying more than this carries the robot
CARRY_WINDOW_S = 0.2      # s: a hook carried the robot if it did at any step this long before the robot let go
# m: a let-go becomes a walk-off once the CoM has dropped this far (a hook bouncing on its bar, or a robot lowered on
# its telescope, loses contact for up to about 0.1 s and drops a few cm at most)
WALK_OFF_DROP = 0.05
LEVEL_STABLE_S = 0.1      # s a level must hold before the timeline counts it reached
SAMPLE_S = 0.05           # s between the timeline's sample rows
PEAK_DB_DEG = 0.2         # deg: a swing peak is the swing turning back by this much (filters contact jitter)
END_SWING_S = 2.0         # s: the summary's end-of-run swing is the largest |swing| over this last stretch


@dataclass(frozen=True)
class Row:
    t: float
    event: str                # "" for a sample row
    level: str                # the level's name, "" for none
    swing: float              # deg
    hook_load: float          # N, the largest hook's, peak-held since the last row


@dataclass(frozen=True)
class StepTime:
    index: int
    name: str
    start: float
    end: float
    how: str                  # the wait that ended it, or "timed out"


@dataclass(frozen=True)
class Note:
    rule: str                 # "G107" or "G106"
    limit: float              # m
    first: float              # s: when the robot first went over
    peak: float               # m over the limit, the most
    peak_t: float
    over_s: float             # s spent over in all
    label: str = ""           # how the notes name the limit: the rule code, or "the extension limit" for a game's


@dataclass(frozen=True)
class ClimbResult:
    status: str
    goal: str | None
    reached_goal: bool
    level: str                # the final level's name, "" for none
    duration: float           # s from step 1's start to the end
    rows: list[Row] = field(default_factory=list)
    steps: list[StepTime] = field(default_factory=list)
    level_times: dict[str, float] = field(default_factory=dict)     # level -> when first held
    level_swings: dict[str, float] = field(default_factory=dict)    # level -> the largest |swing| hanging there alone
    peak_hook_load: float = 0.0
    peak_load_t: float = 0.0
    max_swing: float = 0.0    # deg, the largest |swing|, sign kept
    max_swing_t: float = 0.0
    end_swing: float = 0.0    # deg: the largest |swing| over the last END_SWING_S
    peak_extension: float = 0.0
    peak_height: float = 0.0  # m: the robot's own height at most
    notes: list[Note] = field(default_factory=list)


class _Breach:
    def __init__(self, rule: str, limit: float, label: str | None = None):
        self.rule, self.limit, self.label = rule, limit, label or rule
        self.first: float | None = None
        self.peak = self.peak_t = self.over_s = 0.0

    def see(self, t: float, value: float, dt: float) -> None:
        over = value - self.limit
        if over <= 0.0:
            return
        if self.first is None:
            self.first = t
        self.over_s += dt
        if over > self.peak:
            self.peak, self.peak_t = over, t

    def note(self) -> Note | None:
        if self.first is None:
            return None
        return Note(self.rule, self.limit, self.first, self.peak, self.peak_t, self.over_s, self.label)


class _Phase:
    """Watches one swing phase from a wait's start: zero crossings by sign, peaks by the swing turning back."""

    def __init__(self, name: str):
        self.sign = 1.0 if name.startswith("forward") else -1.0
        self.zero = name.endswith("zero")
        self.prev: float | None = None
        self.lo: float | None = None
        self.hi: float | None = None

    def see(self, hanging: bool, forward: float | None) -> bool:
        if not hanging or forward is None:      # a hook's bounce off the bar: keep what was seen so far
            return False
        s = self.sign * forward                     # the phase's own direction positive
        prev, self.prev = self.prev, s
        if self.zero:
            return prev is not None and prev < 0.0 <= s
        if self.hi is None:                         # wait to see the swing heading this way
            self.lo = s if self.lo is None else min(self.lo, s)
            if s > self.lo + PEAK_DB_DEG:
                self.hi = s
            return False
        self.hi = max(self.hi, s)
        return self.hi > 0.0 and s < self.hi - PEAK_DB_DEG


class WalkOff:
    """The "hook walked off" and "fell" rules (the module docstring), one physics step at a time. `climb run` and the
    block-test runner (scrimmage/blocks/run.py) both judge a let-go with it."""

    def __init__(self, dt: float):
        self.dt = dt
        self.hung = False                     # the robot has hung (HANG_CONFIRM_S of climb.hanging)
        self.drop = 0.0                       # m the CoM dropped, at a walk-off
        self.left: list[str] = []             # the bars it let go of, at a walk-off
        self._hang_s = 0.0
        self._carried_t = -math.inf           # the last step a hook carried the robot
        self._air = None                      # touching nothing since: (the CoM's height then, a hook carried it)
        self._touched: list[str] = []         # the bars touched at the last step on one

    def see(self, t: float, hanging: bool, load: float, z: float, on_ground: bool, touched) -> str:
        """After a physics step at t, with that step's climb.hanging and climb.hook_load, the robot's CoM height and
        its contact_state() (on the ground, the bars it touches): "hook walked off", "fell" or ""."""
        dt = self.dt
        self._hang_s = self._hang_s + dt if hanging else 0.0
        if not self.hung and self._hang_s >= HANG_CONFIRM_S - 1e-9:
            self.hung = True
        if not self.hung:
            if touched:
                self._touched = sorted(touched)
            return ""
        air = self._air
        if air is not None and air[1]:          # let go while carried: it walks off once it really drops
            drop = air[0] - z
            if on_ground or drop > WALK_OFF_DROP:
                self.drop, self.left = drop, list(self._touched)
                return "hook walked off"
        if on_ground:
            return "fell"
        if touched:
            self._air = None                    # back on a bar (a bounce): neither
            self._touched = sorted(touched)
            if load > LOADED_N:
                self._carried_t = t
        elif air is None:
            self._air = (z, t - dt - self._carried_t <= CARRY_WINDOW_S + 1e-9)
        return ""


def rng_rules(world):
    """The range's `[game.rules]`, or None."""
    rng = world.parts.range if world.parts is not None else None
    return rng.game.rules if rng is not None and rng.game is not None else None


class ClimbRun:
    def __init__(self, world, script: Script):
        self.world, self.script = world, script
        # the range's game rules stand in for the [limits] the script leaves out (a script's own keys win)
        self.limits = script.limits_in(rng_rules(world))
        m = world.model
        self.mon = world.climb_monitor()
        self.mon.track_extent()
        rule = self.mon.rule
        self.levels = list(rule.levels) if rule is not None else []
        names = world.joint_states()
        motors = driven_joints(world)
        self.motors = motors
        self._drive = joint_drives(world, motors)       # every motor on each joint, as the Test tab's runs drive them
        self.latches = sorted(n for n in motors if n.rsplit(".", 1)[-1].startswith("latch"))
        holds = {h[0] for h in self.mon.holds}
        driven = ", ".join(sorted(motors)) or "none"
        for st in script.steps:
            where = f"{script.path} step {st.index}"
            for dr in st.drives:
                if dr.joint not in names:
                    raise ScriptError(i18n.t("{where}: no joint {joint}; the robot's joints: {names}",
                                             where=where, joint=repr(dr.joint), names=', '.join(sorted(names))))
                if dr.joint not in motors:
                    raise ScriptError(i18n.t("{where}: joint {joint} has no motor to drive it (bind its part's "
                                             "slot in the config's ids); joints with a motor: {driven}",
                                             where=where, joint=repr(dr.joint), driven=driven))
            for joint, _ in st.release:
                if not self.latches or (joint is not None and joint not in self.latches):
                    raise ScriptError(i18n.t("{where}: release needs a latch release motor (a climber with "
                                             "{latch_setting} and its release slot bound); latch motors: {motors}",
                                             where=where, motors=', '.join(self.latches) or i18n.t("none"),
                                             latch_setting="latch = \"released\""))
            w = st.wait
            if w is None:
                continue
            if w.kind == "joint_at" and w.joint not in names:
                raise ScriptError(i18n.t("{where}: no joint {joint} to wait on; the robot's joints: {names}",
                                         where=where, joint=repr(w.joint), names=', '.join(sorted(names))))
            if w.kind == "hook_contact" and w.name not in holds:
                raise ScriptError(i18n.t("{where}: no bar {name} in the range; its bars and switches: {holds}",
                                         where=where, name=repr(w.name),
                                         holds=', '.join(sorted(holds)) or i18n.t("none")))
            if w.kind == "hook_contact" and not self.mon.hooks:
                raise ScriptError(i18n.t("{where}: the robot has no hooks (a stock climber's) to wait on",
                                         where=where))
            if w.kind == "hook_contact" and w.hook and not any(_is_hook_of(h, w.hook) for h in self.mon.hooks):
                raise ScriptError(i18n.t("{where}: no hook {hook}; the robot's hooks: {hooks}",
                                         where=where, hook=repr(w.hook), hooks=', '.join(self.mon.hooks)))
            if w.kind == "level" and w.name not in self.levels:
                raise ScriptError(i18n.t("{where}: no level {name}; the range's levels: {levels}", where=where,
                                         name=repr(w.name), levels=', '.join(self.levels) or i18n.t("none")))
        if script.goal is not None and script.goal not in self.levels:
            raise ScriptError(i18n.t("{path}: goal {goal} isn't a level of the range; its levels: {levels}",
                                     path=script.path, goal=repr(script.goal),
                                     levels=', '.join(self.levels) or i18n.t("none (the range has no {climb_table} "
                                                                             "table)", climb_table="[climb]")))
        self.on_step = None     # optional f(t, climb signals): called after every physics step (tracing a run)
        self._qadr = {n: int(m.jnt_qposadr[m.joint(n).id]) for n in names}
        self._hinge = {n: int(m.jnt_type[m.joint(n).id]) == int(mujoco.mjtJoint.mjJNT_HINGE) for n in names}

    # ---- the run ---------------------------------------------------------------------------------------------

    def run(self) -> ClimbResult:
        w, m, d, mon = self.world, self.world.model, self.world.data, self.mon
        dt = float(m.opt.timestep)
        w.external = None
        w.reset()
        for _ in range(round(SETTLE_S / dt)):
            w.step(HalState())
        mon.reset()
        t0 = float(d.time)
        drives: dict[str, tuple] = {}         # joint -> (target or None, volts or None, cap)
        release: dict[str, float] = {}        # latch joint -> output

        rows: list[Row] = []
        steps: list[StepTime] = []
        level_times: dict[str, float] = {}
        level_swings: dict[str, float] = {}
        ruled = self.script.from_rules(rng_rules(self.world))     # limits that came from the range's game
        # The labels are machine text, like every event below: the timeline CSV and summary.md read the same in any
        # Language.
        g107 = _Breach("G107", self.limits.extension, "the extension limit" if "extension" in ruled else None)
        g106 = _Breach("G106", self.limits.height, "the height limit" if "height" in ruled else None)
        state = {"t": 0.0, "level": 0, "swing": 0.0, "load": 0.0, "row_load": 0.0}
        peak = {"load": 0.0, "load_t": 0.0, "swing": 0.0, "swing_t": 0.0, "ext": 0.0, "height": 0.0}
        swings: list[tuple[float, float]] = []
        walk = WalkOff(dt)
        level_seen = (0, 0.0)                 # the level being held, and since when
        next_row = 0.0
        status = ""
        self._held_rank = 0

        def level_name(rank: int) -> str:
            return self.levels[rank - 1] if rank > 0 else ""

        def event(text: str) -> None:
            rows.append(Row(state["t"], text, level_name(state["level"]), state["swing"], state["load"]))

        def tick() -> str:
            """One physics step with the drives as they stand; returns a failure status, or ""."""
            nonlocal level_seen, next_row
            hal = HalState()
            bus = w.vbatt
            for joint, (target, volts, cap) in drives.items():
                if target is not None:
                    q = float(d.qpos[self._qadr[joint]])
                    lim = bus if cap is None else min(cap, bus)
                    volts = position_volts(target, q, self._hinge[joint], lim)
                for drive in self._drive[joint]:
                    drive.write(hal, volts, bus)
            for joint, out in release.items():
                for drive in self._drive[joint]:
                    drive.write(hal, out * bus, bus)
            mon.consume()
            w.step(hal)
            t = float(d.time) - t0
            v = mon.sample()
            if self.on_step is not None:
                self.on_step(t, v)
            hanging = bool(v["climb.hanging"])
            rank = int(v["climb.level"])
            load = float(v["climb.hook_load"])
            state.update(t=t, level=rank, swing=float(v["climb.swing"]), load=load)
            state["row_load"] = max(state["row_load"], load)
            if load > peak["load"]:
                peak["load"], peak["load_t"] = load, t
            if abs(state["swing"]) > abs(peak["swing"]):
                peak["swing"], peak["swing_t"] = state["swing"], t
            swings.append((t, state["swing"]))
            ext = float(v["climb.extension"])
            height = mon.own_height()
            peak["ext"], peak["height"] = max(peak["ext"], ext), max(peak["height"], height)
            for b, value in ((g107, ext), (g106, height)):
                was = b.first
                b.see(t, value, dt)
                if was is None and b.first is not None:
                    event(f"{b.label}: over {b.limit / 0.0254:g} in")
            on_ground, touched, _, others = mon.contact_state()
            # the level: held LEVEL_STABLE_S before it counts (a hook bouncing on a bar flickers it)
            if rank != level_seen[0]:
                level_seen = (rank, t)
            if t - level_seen[1] >= LEVEL_STABLE_S - 1e-9:
                self._held_rank = rank
                if rank > 0 and level_name(rank) not in level_times:
                    level_times[level_name(rank)] = level_seen[1]
                    event(f"level {level_name(rank)}")
                if rank > 0 and len(touched) == 1:      # hanging from that level's bar alone
                    name = level_name(rank)
                    level_swings[name] = max(level_swings.get(name, 0.0), abs(state["swing"]))
            if t >= next_row - 1e-9:
                rows.append(Row(t, "", level_name(rank), state["swing"], state["row_load"]))
                state["row_load"] = 0.0
                next_row += SAMPLE_S
            pressed: dict[tuple[int, str], float] = {}     # every contact of a geom with a bar, summed
            for geom, bar, force in others:
                if not mon.is_hook_assembly(geom):          # the hook bracket is meant to touch bars
                    pressed[(geom, bar)] = pressed.get((geom, bar), 0.0) + force
            for (geom, bar), force in pressed.items():
                if force > self.script.limits.hit_force:
                    event(f"{_geom_label(m, geom)} hit {bar} ({force:.0f} N)")
                    return "hit a bar"
            was_hung = walk.hung
            end = walk.see(t, hanging, load, float(d.subtree_com[w.robot_body][2]), on_ground, touched)
            if walk.hung and not was_hung:
                event("hanging")
            if end == "hook walked off":
                event(f"let go of every bar and dropped {walk.drop * 100:.0f} cm"
                      + (" to the ground" if on_ground else ""))
            elif end == "fell":
                event("touched the ground")
            return end

        for st in self.script.steps:
            start = state["t"]
            for dr in st.drives:
                drives[dr.joint] = (dr.to, dr.voltage, dr.max_voltage)
            for joint, out in st.release:
                for latch in ([joint] if joint is not None else self.latches):
                    release[latch] = out
            event(f"step {st.index} start: {st.name}")
            wt = st.wait
            if wt is None:
                steps.append(StepTime(st.index, st.name, start, start, "no wait"))
                continue
            phase = _Phase(wt.name) if wt.kind == "swing_phase" else None
            done = False
            while not done:
                status = tick()
                if status:
                    break
                elapsed = state["t"] - start
                done = self._waited(wt, elapsed, phase)
                if not done and elapsed >= st.timeout - 1e-9:
                    status = f"timed out at step {st.index}"
                    event(f"step {st.index} timed out after {st.timeout:g} s waiting for {wt.describe()}")
                    break
            if status:
                steps.append(StepTime(st.index, st.name, start, state["t"], "timed out" if "timed" in status
                                      else status))
                break
            steps.append(StepTime(st.index, st.name, start, state["t"], wt.describe()))
            event(f"step {st.index} done: {wt.describe()}")
        w.external = None

        final = level_name(self._held_rank)
        if not status:
            status = f"reached {final}" if final else "not hanging"
        event(f"end: {status}")
        goal = self.script.goal
        reached = status.startswith("reached") and (goal is None or self._held_rank >= self.levels.index(goal) + 1)
        end = state["t"]
        tail = [abs(s) for t, s in swings if t >= end - END_SWING_S]
        notes = [n for n in (g107.note(), g106.note()) if n is not None]
        return ClimbResult(status, goal, reached, final, end, rows, steps, level_times, level_swings, peak["load"],
                           peak["load_t"], peak["swing"], peak["swing_t"], max(tail, default=0.0), peak["ext"],
                           peak["height"], notes)

    def _waited(self, wt: Wait, elapsed: float, phase: _Phase | None) -> bool:
        if wt.kind == "time":
            return elapsed >= wt.time - 1e-9
        if wt.kind == "joint_at":
            return abs(float(self.world.data.qpos[self._qadr[wt.joint]]) - wt.value) <= wt.tol
        if wt.kind == "hook_contact":
            hooks = self.mon.contact_state()[2].get(wt.name, set())
            return any(_is_hook_of(h, wt.hook) for h in hooks) if wt.hook else bool(hooks)
        if wt.kind == "level":
            return self._held_rank >= self.levels.index(wt.name) + 1
        return phase.see(bool(self.mon.sample()["climb.hanging"]), self._forward_swing())

    def _forward_swing(self) -> float | None:
        """climb.swing with forward (the robot's +x) positive, or None while it has no bar."""
        s = self.mon.swing_direction()
        if s is None:
            return None
        x = self.world.data.xmat[self.world.robot_body].reshape(3, 3)[:, 0]
        return float(self.mon.sample()["climb.swing"]) * (1.0 if float(s @ x) >= 0.0 else -1.0)


def _is_hook_of(hook: str, name: str) -> bool:
    """Hook `<inst>.hook<i>` is `name` or one of climber `name`'s hooks."""
    return hook == name or hook.startswith(name + ".")


def _geom_label(m, g: int) -> str:
    """A geom's name, or its body's name for an unnamed geom (a climber's tubes)."""
    return m.geom(g).name or f"{m.body(int(m.geom_bodyid[g])).name} (geom {g})"


def run_climb(config_path, script: Script) -> ClimbResult:
    """Play `script` on the config's robot and range."""
    world = build_world(load_config(config_path))
    if world.parts is None or world.parts.range is None or world.parts.range.climb is None:
        raise ScriptError(i18n.t("{config_path}: climb run needs a {range_table} whose range file has a "
                                 "{climb_table} table (a climbing range, such as 2022-hangar)",
                                 config_path=config_path, range_table="[range]", climb_table="[climb]"))
    return ClimbRun(world, script).run()


# ---- Output ------------------------------------------------------------------------------------------------------

CSV_FIELDS = ("t", "event", "level", "swing", "hook_load")


def write_csv(result: ClimbResult, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_FIELDS)
        for r in result.rows:
            w.writerow([f"{r.t:.3f}", r.event, r.level, f"{r.swing:.2f}", f"{r.hook_load:.1f}"])


def format_markdown(result: ClimbResult, config: str, script: str) -> str:
    r = result
    goal = "" if r.goal is None else f" Goal: {r.goal} ({'reached' if r.reached_goal else 'not reached'})."
    lines = [f"# climb run: {Path(script).name}", "", f"Config: `{config}`. Script: `{script}`.{goal}", "",
             f"**Status: {r.status}** at t = {r.duration:.2f} s.", "",
             "| measure | value |", "|---|---|"]
    for name, t in r.level_times.items():
        lines.append(f"| reached {name} | {t:.2f} s (largest swing hanging there alone {r.level_swings.get(name, 0.0):.1f} deg) |")
    lines += [f"| peak hook load | {r.peak_hook_load:.0f} N at {r.peak_load_t:.2f} s |",
              f"| largest climb.swing | {r.max_swing:.1f} deg at {r.max_swing_t:.2f} s |",
              f"| swing over the last {END_SWING_S:g} s | {r.end_swing:.1f} deg |",
              f"| most extension beyond the frame | {r.peak_extension / 0.0254:.1f} in |",
              f"| tallest (own frame) | {r.peak_height / 0.0254:.1f} in |", ""]
    lines += ["| step | name | start (s) | end (s) | ended by |", "|---|---|---|---|---|"]
    for s in r.steps:
        lines.append(f"| {s.index} | {s.name} | {s.start:.2f} | {s.end:.2f} | {s.how} |")
    lines.append("")
    if r.notes:
        lines.append("Notes:")
        for n in r.notes:
            lines.append(f"- {n.label or n.rule}: over its {n.limit / 0.0254:g} in limit from t = {n.first:.2f} s, by up to "
                         f"{n.peak / 0.0254:.1f} in at {n.peak_t:.2f} s ({n.over_s:.2f} s over in all).")
        lines.append("")
    return "\n".join(lines)
