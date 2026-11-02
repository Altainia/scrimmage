"""Running a test: a student's blocks played against the physics, with no robot program.

The runner takes the robot program's place, as `climb run` does (scrimmage/climb/run.py): every physics step it
writes the motor controllers' outputs through the config's own motor bindings (scrimmage/hal_drive.py), so each block
drives through the robot's own motors, gearing and battery. It resets the world, settles the robot on the carpet for
SETTLE_S with every motor at neutral, then plays the blocks. Every time in a run (the outcome, the checks, the samples)
is on the run's clock, which starts with the settle; `time_limit` ends the run at that time on it.

Blocks hold like a climb script's drives: `spin` holds its volts until a later block changes that motor; `move` holds
its position target (a proportional loop, as `climb run`'s) and waits until the joint is within MOVE_TOL of it, or its
timeout; `drive` (a tank drive's) drives the wheel motors (forward for a positive distance or volts) until the robot
has gone that far across the floor from where the block started (or its timeout), or for its seconds, then stops
them; `stop` lets a motor (or all of them) go to neutral. `wait`, `wait_until` and `repeat` pass time and loop; a
`repeat` goes round at most once a physics step when its body takes no time. `expect` checks its condition once, or
(`throughout`) every step until the run ends; a check that fails ends the run, saying what happened instead. The
Watch reads the world once after the settle, so the first blocks judge the robot as it rests (a hook already on a bar
touches it).

A run ends with one status: "finished" (every block played), "check failed", "timed out" (a wait until, a move or a
drive),
"tipped over" (the robot body's up axis more than TIP_DEG from vertical), "walked off" (the climb runner's "hook walked
off" rule, scrimmage/climb/run.py's WalkOff), "time limit", "repeat limit" (a repeat until reached its max) or
"cancelled", and a one-sentence outcome for the student.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from ..climb.run import SETTLE_S, WalkOff
from ..hal_drive import MotorDrive, driven_joints, position_volts
from ..movers import mover_poses, movers
from ..protocol import HalState
from .conditions import IN, Watch, describe
from .palette import ALL_MOTORS, ANY_HOOK, ANY_PIECE, NEXT_BAR, palette, problems
from .program import (DRIVE_TIMEOUT, DRIVE_VOLTS, MOVE_TIMEOUT, REPEAT_MAX, WAIT_TIMEOUT, Program,
                      TestFileError)

MOTION_HZ = 30            # mover poses a second, for the replay
SIGNAL_HZ = 50            # signal samples a second, for the chart
TIP_DEG = 60.0            # the robot body's up axis this far from vertical: it tipped over (spec, our call)
MOVE_TOL_DEG = 1.0        # a move has reached its target within this (a hinge), our call
MOVE_TOL_IN = 0.25        # ... or this (a slide), our call
EPS = 1e-9

# The verbs describe() uses, as "-ing" (after "without") and "to ..." (after "waiting for").
_VERBS = {"passes": ("passing", "to pass"), "goes": ("going", "to go"), "tilts": ("tilting", "to tilt"),
          "touches": ("touching", "to touch"), "reaches": ("reaching", "to reach"), "hangs": ("hanging", "to hang"),
          "are": ("being", "to be"), "is": ("being", "to be")}


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
    """describe()'s words with its verb as "-ing" (form 0) or "to ..." (form 1)."""
    words = text.split(" ")
    for i, w in enumerate(words):
        if w in _VERBS:
            words[i] = _VERBS[w][form]
            break
    return " ".join(words)


def _amount(x: float, unit: str) -> str:
    return f"{x:g}°" if unit == "deg" else f"{x:g} {unit}"


def setup_config(raw: dict, setup: dict) -> dict:
    """A copy of the config dict with the test's `[setup]` ball, station settings and seed in its `[range]` (a
    station's settings add to, and replace, the config's own). Nothing to set leaves `raw` as it is."""
    values = {k: setup[k] for k in ("ball", "station", "seed") if k in setup}
    if not values:
        return raw
    if not isinstance(raw.get("range"), dict):
        raise TestFileError("Setup's ball and station settings need a field; this robot is on the plain field")
    rng = dict(raw["range"])
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
    from dataclasses import replace
    from ..config import build_world, load_config
    config = load_config(Path(program.path).parent / program.robot)
    return build_world(replace(config, raw=setup_config(config.raw, program.setup)))


class TestRun:
    __test__ = False     # not a pytest class

    def __init__(self, world, program: Program):
        bad = problems(program, world)
        if bad:
            lines = [(f"{_where(tuple(p['place']))}: " if p["place"] else "") + p["message"] for p in bad]
            raise TestFileError("the test can't run on this robot yet:\n" + "\n".join(lines))
        self.world, self.program = world, program
        m = world.model
        self.motors = driven_joints(world)
        self._drive = {n: MotorDrive(b) for n, b in self.motors.items()}
        pal = palette(world, list(program.hooks))
        kinds = {j["name"]: j["kind"] for j in pal["joints"]}
        self.wheels = [n for n in self.motors if kinds.get(n) == "wheel"]
        self.signal_names = list(pal["signals"])
        self._qadr = {n: int(m.jnt_qposadr[m.joint(n).id]) for n in kinds}
        self._slide = {n: k == "slide" for n, k in kinds.items()}

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
        for _ in range(round(SETTLE_S / dt)):
            w.step(HalState())
        watch = self.watch = Watch(w, self.program)
        if watch.mon is not None:
            watch.mon.reset()
        watch.update()                                      # the robot as it rests, for the first blocks
        walk = WalkOff(dt) if watch.mon is not None else None
        view = model_view(w, include_world=True)
        ms = movers(w)
        result = RunResult("", "", 0.0, movers=[mv.name for mv in ms], view=view, started=started)
        result.signals = {"t": [], **{n: [] for n in self.signal_names}}

        self.drives: dict[str, tuple[str, float]] = {}      # joint -> ("volts", V) or ("to", qpos target)
        self.frames = [_Frame(list(self.program.blocks), ())]
        self.active: dict | None = None                     # the waiting block under way
        self.checks: list[dict] = []
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
        if result.signals["t"][-1] < t - EPS:          # the run's last moment, off the sample grid
            next_signal = t
        if result.motion[-1][0] < t - EPS:
            next_motion = t
        sample(t)
        top = watch.highest_hung()
        result.outcome = f"reached {top}, then {ending}" if top else ending
        result.t_end = t
        result.hung = list(watch.hung)
        for check in self.checks:
            check["line"] = self._line(check)
        result.checks = self.checks
        return result

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
                        what = _recast(describe(a["until"], watch.next_bar()), 0)
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
            return True
        if b.do == "stop":
            if a["motor"] == ALL_MOTORS:
                self.drives.clear()
            else:
                self.drives.pop(a["motor"], None)
            return True
        if b.do == "expect":
            cond = a["that"]
            ok = watch.holds(cond)
            check = {"place": list(place), "text": describe(cond, watch.next_bar()),
                     "throughout": bool(a.get("throughout", False)), "passed": ok, "t": t}
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
        elif b.do == "drive":
            self.active["from"] = self.world.data.xpos[self.world.robot_body][:2].copy()
            volts = a.get("volts", DRIVE_VOLTS)
            if "distance" in a:
                volts = math.copysign(abs(volts), a["distance"])
            for j, sign in self._wheel_signs().items():
                self.drives[j] = ("volts", sign * volts)
        return False

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
                what = _recast(describe(a["until"], watch.next_bar()), 1)
                raise _End("timed out", f"waiting for {what} timed out at {t:.1f} s ({_where(place)})")
            return False
        if b.do == "move":
            j = a["joint"]
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
        # drive
        if "distance" in a:
            gone = float(np.linalg.norm(d.xpos[self.world.robot_body][:2] - self.active["from"]))
            done = gone >= abs(a["distance"]) - EPS
            timeout = a.get("timeout", DRIVE_TIMEOUT)
            if not done and elapsed >= timeout - EPS:
                raise _End("timed out", f"drive ({_where(place)}) didn't reach {abs(a['distance']):g} m in "
                                        f"{timeout:g} s (went {gone:.2f} m)")
        else:
            done = elapsed >= a["seconds"] - EPS
        if done:
            for j in self.wheels:
                self.drives.pop(j, None)
        return done

    def _wheel_signs(self) -> dict[str, float]:
        """Each wheel motor's sign that rolls the robot forward (its +x): a wheel turning about the chassis's +y
        rolls it forward, so the sign of the chassis's y . the wheel's axis."""
        d, m = self.world.data, self.world.model
        side = d.xmat[self.world.robot_body].reshape(3, 3)[:, 1]
        return {j: 1.0 if float(side @ d.xaxis[m.joint(j).id]) >= 0.0 else -1.0 for j in self.wheels}

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
                return f"no ball was in {a['body']}"
            return f"{a['piece']} wasn't in {a['body']}"
        if k == "hanging_from":
            return f"the robot wasn't hanging from {bar(a['bar'])}"
        return f"the robot wasn't hanging at {a['level']}"           # level

    def _fail(self, check: dict, cond, t: float) -> None:
        check.update(passed=False, t=t, failure=self._failure(cond))
        raise _End("check failed", f"a check failed ({_where(tuple(check['place']))}): {check['failure']} at {t:.1f} s")

    def _doing(self, b) -> str:
        """A waiting block under way, in words."""
        a = b.args
        if b.do == "wait":
            return f"waiting {a['seconds']:g} s"
        if b.do == "wait_until":
            return f"waiting for {_recast(describe(a['until'], self.watch.next_bar()), 1)}"
        if b.do == "move":
            return f"moving {a['joint']} to {_amount(a['to'], a['unit'])}"
        if "distance" in a:
            return f"driving {a['distance']:g} m"
        return f"driving for {a['seconds']:g} s"

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
        return f"failed ({where}): {c['failure']} at {c['t']:.1f} s"
