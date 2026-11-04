"""Runs a scenario on a Session: each step's DS inputs, checks, screenshots and contact sheets, then a report."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import foreign_writes as fwn
from .bindings import binds_rev
from .bridge import REALTIME_REASON
from .devicecheck import check_devices, device_notes
from .checks import (CRASH_PATTERN, Check, CheckResult, ConsoleCheck, build_check, consume_peaks,
                     tick_checks)
from .config import ConfigError, build_world, load_config
from .render import contact_sheet, to_png
from .scenario import Scenario, ScenarioError, config_for, match_for, no_range_message, range_file, validate
from .session import Session, SessionError

log = logging.getLogger("scrimmage")

NT_SETTLE = 0.25   # wall seconds the simulation is held so the robot's NetworkTables can catch up
CONSOLE_SETTLE = 0.25  # wall seconds held so a Java robot's last console lines reach its console file
UNSET = object()
TICK_PERIOD = 0.25  # sim seconds between progress "tick" events
STOPPED = "stopped by the user"
# the Java agent's summary line, or the RobotPy REV helper's (scrimmage_lockstep)
AGENT_IGNORED = re.compile(r"scrimmage (agent|rev helper): ignored (\d+) robot-side sim writes \((.*)\)$")
PHOENIX_NOTE = REALTIME_REASON + "; results are close but not byte-identical"
NO_CHECKS_NOTE = ("this scenario has no checks, so passing only means the robot program ran: add a check (where the "
                  "robot ends up, say) to test something")
# Below this fraction of real time, or skipping ahead at all, a Phoenix 6 run's physics is reported as not keeping up
PHOENIX_SLOW = 0.8
# A Phoenix 6 robot program further behind real time than this (s) is reported as not keeping up: silent that long,
# or relaying values that long superseded (12 robot loops; a 2-core machine with spare CPU stays under 0.11 s)
ROBOT_BEHIND = 0.25
PHOENIX_LOCKSTEP = "Phoenix 6 devices: this robot runs in real time, not lockstep"


def phoenix_slow_note(speed: float | None, skips: int = 0, skipped: float = 0.0) -> str:
    """Physics in a real-time run below PHOENIX_SLOW of real time, or skipping ahead at all (`skips` times, `skipped`
    simulated seconds in all): a run that loses a second to a few skips still averages 0.9x."""
    kept = f"{speed:.1f}x real time" if speed is not None else "part of real time"
    jumps = f" and skipped ahead {skipped:.2f} s ({skips} time{'' if skips == 1 else 's'})" if skips else ""
    return (f"physics on this computer kept up with only {kept}: physics fell behind real time{jumps}, so results "
            "may differ from a run that keeps up")


def robot_behind_note(seconds: float) -> str:
    return (f"the robot program fell {seconds:.2f} s behind real time (silent, or relaying values that long out of "
            "date): Phoenix 6 runs on the wall clock, so its timing may differ from a run that keeps up")


OUTPUT_FILES = {"csv": "run.csv", "robot_log": "robot.log", "report": "report.json", "report_md": "report.md"}


@dataclass
class StepReport:
    name: str
    mode: str
    start_t: float
    end_t: float = 0.0
    checks: list[CheckResult] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    interrupted: str | None = None   # "step interrupted at t=..." when the robot program failed mid-step
    game_data: str = ""              # the game data the robot had during the step

    @property
    def passed(self) -> bool:
        return self.interrupted is None and all(c.passed for c in self.checks)

    def to_dict(self) -> dict:
        return {"name": self.name, "mode": self.mode, "start_t": self.start_t, "end_t": self.end_t,
                "game_data": self.game_data, "passed": self.passed, "interrupted": self.interrupted,
                "checks": [c.to_dict() for c in self.checks], "screenshots": self.screenshots,
                "skipped": self.skipped}


@dataclass
class Report:
    scenario: str
    description: str
    steps: list[StepReport] = field(default_factory=list)
    implicit: list[CheckResult] = field(default_factory=list)
    error: str | None = None
    lockstep: bool = False
    console_tail: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    effective_speed: float | None = None  # set when the run's speed was forced (Phoenix 6: 1x)
    notes: list[str] = field(default_factory=list)
    range: dict | None = None    # the test range's counts() at the end of the run (None without a range)
    balls: list[dict] = field(default_factory=list)   # ball_log() of the balls that took part (took_part)
    ball_states: list[str] = field(default_factory=list)   # every ball's outcome, by ball id
    ball_summary: dict | None = None    # the balls not in `balls`: {"untouched": floor balls, "spare": spares}

    @property
    def passed(self) -> bool:
        return self.error is None and all(s.passed for s in self.steps) and all(c.passed for c in self.implicit)

    @property
    def exit_code(self) -> int:
        return 2 if self.error is not None else (0 if self.passed else 1)

    def to_dict(self) -> dict:
        d = {"scenario": self.scenario, "description": self.description, "passed": self.passed,
             "exit_code": self.exit_code, "error": self.error, "lockstep": self.lockstep,
             "steps": [s.to_dict() for s in self.steps], "implicit": [c.to_dict() for c in self.implicit],
             "effective_speed": self.effective_speed, "notes": self.notes,
             "console_tail": self.console_tail, "files": self.files}
        if self.range is not None:      # a run on a test range; other reports keep their keys
            d.update(range=self.range, balls=self.balls, ball_states=self.ball_states, ball_summary=self.ball_summary)
        return d

    def summary(self) -> str:
        if self.error is not None:
            return f"ERROR  {self.scenario}: {self.error}"
        lines = [f"{'PASS' if self.passed else 'FAIL'}  {self.scenario}"]
        for s in self.steps:
            for c in s.checks:
                lines.append(f"  {'ok  ' if c.passed else 'FAIL'} [{s.name}] {c.message}")
            if s.interrupted:
                lines.append(f"  FAIL [{s.name}] {s.interrupted}")
        for c in self.implicit:
            if not c.passed:
                lines.append(f"  FAIL {c.message}")
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)

    def write(self, out_dir: Path) -> None:
        self.files = {k: v for k, v in OUTPUT_FILES.items()
                      if k in ("report", "report_md") or (out_dir / v).exists()}
        (out_dir / "report.json").write_text(json.dumps(self.to_dict(), indent=2, default=str))
        md = [f"# {'PASS' if self.passed else ('ERROR' if self.error else 'FAIL')}: {self.scenario}", ""]
        if self.description:
            md += [self.description, ""]
        if self.error:
            md += [f"**Couldn't run:** {self.error}", ""]
        if self.notes:
            md += [f"> Note: {n}" for n in self.notes] + [""]
        for s in self.steps:
            data = f", game data {json.dumps(s.game_data, ensure_ascii=False)}" if s.game_data else ""
            md += [f"## {s.name} ({s.mode}, t={s.start_t:.2f}–{s.end_t:.2f} s{data})", ""]
            md += [f"- {'✅' if c.passed else '❌'} {c.message}" for c in s.checks]
            md += [f"- ❌ {s.interrupted}"] if s.interrupted else []
            md += [f"- skipped: {x}" for x in s.skipped]
            md += [""] + [f"![{p}]({p})" for p in s.screenshots] + [""]
        if self.range is not None:
            md += _score_md(self.range) + _balls_md(self.balls, self.ball_summary or {})
        bad = [c for c in self.implicit if not c.passed]
        if bad:
            md += ["## Robot program", ""] + [f"- ❌ {c.message}" for c in bad] + [""]
        if self.console_tail:
            md += ["## Console (last lines)", "", "```", *self.console_tail, "```", ""]
        (out_dir / "report.md").write_text("\n".join(md))


def _f(v, fmt: str = ".2f") -> str:
    return "–" if v is None else format(v, fmt)


def _score_md(c: dict) -> list[str]:
    goals = ", ".join(f"{g} {n}" for g, n in c["goals"].items())
    return ["## Score", "", f"- score: {c['score']} ({goals})", f"- fired: {c['fired']}", f"- held: {c['held']}",
            f"- on the field: {c['on_field']}", ""]


def _ball_result(b: dict) -> str:
    """Where a ball ended up: the goal and time it scored, where it left the field or came to rest, or why the
    launcher kept it; what it hit first after its release; and for a ball fired that didn't score, how high it got
    against the rim of the goal nearest its release."""
    out, pos = b["outcome"], b.get("last_pos")
    text = ""
    if out == "scored":
        text = f"{b['goal']} at t={_f(b['scored_t'])}"
    elif out == "stuck":
        text = f"launcher: {b['launcher_status']}"
    elif pos is not None:
        xyz = f"({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f})"
        text = {"out_of_field": f"left at {xyz}", "in_flight": f"flying at {xyz}",
                "on_field": f"rest at ({pos[0]:.2f}, {pos[1]:.2f})" if b.get("at_rest") else f"moving at {xyz}"
                }.get(out, xyz)
    if b.get("first_hit"):
        text += f"{'; ' if text else ''}first hit {b['first_hit']}"
    if out != "scored" and b.get("fired_t") is not None and b.get("apex_z") is not None and b.get("nearest_goal"):
        text += (f"{'; ' if text else ''}apex {b['apex_z']:.2f} m vs rim {_f(b.get('rim_height'))} m "
                 f"({b['nearest_goal']})")
    return text


_BALL_COLUMNS = ["#", "fired t", "x", "y", "heading", "vx", "vy", "ω °/s", "hood °", "rps", "exit m/s", "spin rpm", "slip",
                "outcome", "goal and time / rest point / first hit"]


def _ball_row(b: dict) -> list[str]:
    pose = b.get("robot_pose") or (None, None, None)
    vel = b.get("robot_velocity") or (None, None, None)
    return [str(b["id"]), _f(b["fired_t"]), _f(pose[0]), _f(pose[1]), _f(pose[2], ".1f"), _f(vel[0]), _f(vel[1]),
            _f(vel[2], ".1f"), _f(b["hood_deg"], ".1f"), _f(b["shooter_rps"], ".1f"), _f(b["exit_speed"]),
            _f(b["spin_rpm"], ".0f"), _f(b["exit_slip"]), b["outcome"], _ball_result(b)]


def not_listed(summary: dict) -> str:
    """The line under the ball table for the balls it leaves out (report.js says the same)."""
    parts = []
    if summary.get("untouched"):
        n = summary["untouched"]
        parts.append(f"{n} floor ball{'s' if n != 1 else ''} untouched (still where the range put "
                     f"{'them' if n != 1 else 'it'})")
    if summary.get("spare"):
        n = summary["spare"]
        parts.append(f"{n} spare ball{'s' if n != 1 else ''} (unused slots, or floor balls turned off)")
    return "Not listed: " + "; ".join(parts) + "." if parts else ""


def _balls_md(balls: list[dict], summary: dict) -> list[str]:
    """One row per ball that took part; the untouched floor balls and the spares counted under the table."""
    md = ["## Balls", ""]
    if balls:
        md += ["| " + " | ".join(_BALL_COLUMNS) + " |", "|" + "---|" * len(_BALL_COLUMNS)]
        md += ["| " + " | ".join(_ball_row(b)) + " |" for b in balls]
    else:
        md += ["No ball took part."]
    if line := not_listed(summary):
        md += ["", line]
    return md + [""]


def took_part(b: dict, start, r: float) -> bool:
    """Did ball log `b` take part in the run: a preload, or a floor ball that was collected, fired, scored, stuck,
    left the field, is flying, or ended more than a radius `r` from where it started (`start`, its (x, y) on the
    carpet)? Spares never do."""
    if b["outcome"] == "spare":
        return False
    if b["origin"] != "floor" or b["fired_t"] is not None or b["outcome"] != "on_field":
        return True
    p = b["last_pos"]
    return p is None or math.dist(p, (float(start[0]), float(start[1]), r)) > r


class _Stopped(Exception):
    """The caller's cancel event was set."""


class _Hooks:
    """Optional progress callback, cancel event and per-step watch; with none, runs advance exactly as they always
    did."""

    def __init__(self, progress, cancel, watch=None):
        self.progress, self.cancel, self.watch = progress, cancel, watch
        self.last_tick = -TICK_PERIOD
        self.console_next = 0

    def emit(self, event: dict) -> None:
        if self.progress is not None:
            self.progress(event)

    def cancelled(self) -> bool:
        return self.cancel is not None and self.cancel.is_set()

    def check(self) -> None:
        if self.cancelled():
            raise _Stopped

    async def sleep(self, seconds: float, *, raising: bool = True) -> None:
        """asyncio.sleep that a cancel wakes (and, if raising, turns into _Stopped)."""
        if self.cancel is None:
            await asyncio.sleep(seconds)
            return
        await asyncio.to_thread(self.cancel.wait, seconds)
        if raising:
            self.check()

    async def until_cancelled(self, aw):
        """Awaits `aw` (a robot start-up) as a task that a cancel interrupts: the task is then cancelled (its own
        clean-up stops the robot program) and _Stopped is raised."""
        task = asyncio.ensure_future(aw)
        if self.cancel is None:
            return await task
        watcher = asyncio.ensure_future(self._cancelled())
        try:
            await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            watcher.cancel()
            if not task.done():
                task.cancel()
                await asyncio.wait({task})
        if task.cancelled():
            raise _Stopped
        return task.result()

    async def _cancelled(self) -> None:
        while not self.cancel.is_set():
            await asyncio.sleep(0.05)

    def console(self, session) -> None:
        robot = session.robot
        if self.progress is None or robot is None:
            return
        for line in robot.since(self.console_next):
            self.emit({"type": "console", "line": line.text})
            self.console_next = line.index + 1

    def tick(self, session, t: float) -> None:
        if self.progress is not None and t - self.last_tick >= TICK_PERIOD - 1e-9:
            self.last_tick = t
            self.emit({"type": "tick", "t": t})
            self.console(session)


def picture_sizes(scenario: Scenario) -> list[tuple[int, int]]:
    """Every (width, height) the scenario's screenshots and contact sheets use, in first-use order."""
    sizes: list[tuple[int, int]] = []
    for step in scenario.steps:
        wanted = [(sh.width, sh.height) for sh in step.screenshots]
        if step.contact_sheet is not None:
            wanted.append((step.contact_sheet.width, step.contact_sheet.height))
        sizes += [sz for sz in wanted if sz not in sizes]
    return sizes


async def run_scenario(scenario: Scenario, out_dir: Path, *, speed=UNSET, session: Session | None = None,
                       progress: Callable[[dict], None] | None = None, cancel=None,
                       watch: Callable[[Session, float], None] | None = None, sim_mode: str | None = None) -> Report:
    """Runs the scenario. ``progress(event)`` gets step_start / tick / console / done dicts; ``cancel`` is a
    threading.Event checked between advances (when set the session stops and the report's error is
    "stopped by the user"); ``watch(session, t)`` is called after every robot step (a live view's frames).
    ``sim_mode`` ("fast" / "phoenix") overrides the config's, for a session this run starts."""
    hooks = _Hooks(progress, cancel, watch)
    done = {"type": "done", "passed": False}
    try:
        report = await _run_scenario(scenario, out_dir, speed, session, hooks, sim_mode)
        done["passed"] = report.passed
        if report.error is not None:
            done["error"] = report.error
        return report
    except BaseException as e:
        done["error"] = str(e) or type(e).__name__
        raise
    finally:
        hooks.emit(done)


async def _run_scenario(scenario: Scenario, out_dir: Path, speed, session: Session | None, hooks: _Hooks,
                        sim_mode: str | None = None) -> Report:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = Report(scenario.path.name, scenario.description)
    own = session is None
    console_start = 0  # this run's first console line (a passed-in session may have older ones)
    try:
        if own:
            config = config_for(scenario, load_config(scenario.config_path))
            session = Session(config, speed=scenario.speed if speed is UNSET else speed,
                              log_path=out_dir / "run.csv", robot_log=out_dir / "robot.log",
                              picture_sizes=picture_sizes(scenario), sim_mode=sim_mode)
            validate(scenario, build_world(config))
            await hooks.until_cancelled(session.start())
        else:
            if session.config.path != scenario.config_path:
                raise ScenarioError(f"this session runs {session.config.path}, but the scenario uses "
                                    f"{scenario.config_path}; start a session with that config")
            _check_session_range(scenario, session)
            validate(scenario, session.world)
            console_start = session.robot.next_index if session.robot is not None else 0
            if scenario.robot_env and session.attach:
                raise ScenarioError(f"{scenario.path.name}: robot_env can't reach the robot program: this session "
                                    "attaches to a robot program it doesn't launch; set the variables yourself")
            # the scenario's [match] over the config's, with no game data (M4), for the robot's first frame
            ds = session.bridge.ds
            ds.set_match(**dataclasses.asdict(match_for(scenario, session.config)))
            if ds.game_data:
                ds.set_game_data("")
            rng = session.world.test_range
            if rng is not None:     # the bridge resets the world when the robot reconnects: reset() keeps these
                rng.clear_overrides()
                try:
                    rng.reset(**{k: v for k, v in (scenario.range or {}).items() if k != "file"})
                except ValueError as e:
                    raise ScenarioError(f"{scenario.path.name}: range: {e}") from None
            await hooks.until_cancelled(session.restart_robot(env=scenario.robot_env or None))
        hooks.console_next = console_start
        hooks.check()
    except _Stopped:
        _drop_overrides(session, own)
        report.error = STOPPED
        await asyncio.shield(session.stop())
        report.write(out_dir)
        return report
    except (ScenarioError, ConfigError, ValueError) as e:
        _drop_overrides(session, own)
        report.error = str(e)
        report.write(out_dir)
        return report
    except SessionError as e:
        _drop_overrides(session, own)
        report.error = str(e)
        report.console_tail = e.console
        if own and session is not None:
            await session.stop()
        report.write(out_dir)
        return report

    try:
        await _run_steps(session, scenario, report, out_dir, console_start, hooks)
        _capture_range(session, report, own)
        if report.error == STOPPED:
            await asyncio.shield(session.stop())
    except BaseException as e:
        interrupted = isinstance(e, (asyncio.CancelledError, KeyboardInterrupt))
        report.error = "interrupted" if interrupted else f"unexpected error: {e!r}"
        try:
            _capture_range(session, report, own)
        except Exception as e2:  # don't hide the exception that got us here
            log.error("couldn't read the test range's counts: %s", e2)
        if own:
            await asyncio.shield(session.stop())
        try:
            report.write(out_dir)
        except Exception as e:  # don't hide the exception that got us here
            log.error("couldn't write the report to %s: %s", out_dir, e)
        raise
    if own and report.error != STOPPED:
        await session.stop()
    report.write(out_dir)
    return report


def _check_session_range(scenario: Scenario, session: Session) -> None:
    """A passed-in session must already be on the scenario's range: a [range] needs one, and its file must be the
    session's (a range can't change without rebuilding the world)."""
    if not scenario.range:
        return
    rng = session.world.test_range
    if rng is None:
        raise ScenarioError(no_range_message(session.config.path))
    if "file" in scenario.range:
        want, have = range_file(scenario.range["file"]), Path(rng.range.path).resolve()
        if want != have:
            raise ScenarioError(f"the scenario's [range] file is {want}, but this session runs the range {have}; "
                                "start a session with that range")


def _drop_overrides(session: Session | None, own: bool) -> None:
    """A passed-in session's range back to its config's start, preload and floor balls at its next reset."""
    rng = session.world.test_range if not own and session is not None and session.world is not None else None
    if rng is not None:
        rng.clear_overrides()


def _capture_range(session: Session, report: Report, own: bool) -> None:
    """The range's counts and ball log into the report, before the session stops; a passed-in session's range goes
    back to its config's start, preload and floor balls at its next reset."""
    rng = session.world.test_range if session.world is not None else None
    if rng is None:
        return
    report.range = rng.counts()
    logs = rng.ball_log()
    starts, r = rng.range.floor_balls, rng.balls.radius      # each piece's own radius
    took = [took_part(b, starts[b["id"]] if b["origin"] == "floor" else None, float(r[b["id"]])) for b in logs]
    report.balls = [b for b, t in zip(logs, took) if t]
    report.ball_states = [b["outcome"] for b in logs]
    spare = sum(b["outcome"] == "spare" for b in logs)
    report.ball_summary = {"untouched": len(logs) - len(report.balls) - spare, "spare": spare}
    _drop_overrides(session, own)


JOINT_MOVED = 0.05      # rad or m: a joint this far from its rest counts as moved in a tipped/stranded note


def moved_joints(joints: dict[str, tuple[float, float]]) -> str:
    """", with joint.<n>.position = v, ..." for each robot joint in `joints` ({name: (position, rest)}, the monitor's
    snapshot at the event) more than JOINT_MOVED from its rest, else ""."""
    moved = [f"joint.{n}.position = {pos:.3g}" for n, (pos, rest) in joints.items() if abs(pos - rest) > JOINT_MOVED]
    return ", with " + ", ".join(moved) if moved else ""


def terrain_notes(world, noted: dict) -> list[str]:
    """Notes for the tipping and stranding the terrain monitor latched since the last call. Each is noted once per
    latch: `noted` remembers the events already reported, so a world reset (which clears the monitor) lets a later
    tipping or stranding be a new first and be noted again. A scenario with no terrain signal on a range without
    elements has no monitor, so no notes."""
    terrain = getattr(world, "terrain", None)
    notes = []
    if terrain is None:
        return notes
    tip, strand = terrain.first_tipped, terrain.first_stranded
    if tip is not None and tip != noted.get("tipped"):
        noted["tipped"] = tip
        t, tilt, (x, y, _) = tip
        notes.append(f"the robot tipped over at t={t:.2f} s (tilt {tilt:.0f}°, at x={x:.2f}, y={y:.2f})"
                     f"{moved_joints(terrain.tipped_joints)}")
    if strand is not None and strand != noted.get("stranded"):
        noted["stranded"] = strand
        t, what = strand
        notes.append(f"the robot was stranded at t={t:.2f} s, resting on {what}"
                     f"{moved_joints(terrain.stranded_joints)}")
    return notes


async def _run_steps(session: Session, scenario: Scenario, report: Report, out_dir: Path, console_start: int,
                     hooks: _Hooks) -> None:
    report.lockstep = session.lockstep
    if scenario.require_lockstep and not (session.lockstep or session.bridge.phoenix_released):
        report.error = ("lockstep is required but the robot program didn't load the scrimmage_lockstep extension "
                        "(RobotPy: add --scrimmage-lockstep to [robot_program] command; or set "
                        "require_lockstep = false)")
        return
    if session.bridge.nt_link is None and any(build_check(c, session.world, s.name).uses_nt
                                              for s in scenario.steps for c in s.checks):
        if not session.config.nt:
            why = "the config sets [telemetry] nt = false, and these checks need NetworkTables"
        elif not session.nt:
            why = "this session was started without NetworkTables (nt=False)"
        else:
            why = "NetworkTables isn't available: install scrimmage[nt] (pyntcore)"
        report.error = f"this scenario checks NetworkTables values (nt: signals or odometry_error), but {why}"
        return
    noted: dict = {}
    for i, step in enumerate(scenario.steps, 1):
        rep = StepReport(step.name, step.mode, round(session.t, 4))
        report.steps.append(rep)
        try:
            hooks.check()
            hooks.emit({"type": "step_start", "index": i - 1, "name": step.name, "mode": step.mode,
                        "duration": step.duration})
            await _run_step(session, step, i, out_dir, rep, hooks)
        except _Stopped:
            rep.end_t = round(session.t, 4)
            rep.interrupted = f"step interrupted at t={session.t:.3f}"
            report.error = STOPPED
            return
        except SessionError as e:
            rep.end_t = round(session.t, 4)
            rep.interrupted = f"step interrupted at t={session.t:.3f}"
            report.implicit.append(CheckResult("robot program running", False, str(e), first_failure_t=e.t))
            break
        except BaseException:
            rep.end_t = round(session.t, 4)
            rep.interrupted = f"step interrupted at t={session.t:.3f}"
            raise
        finally:    # on every exit of the step: a robot that tipped and then lost its program is still noted
            report.notes.extend(terrain_notes(session.world, noted))
    report.notes.extend(session.world.warnings)
    if session.bridge.phoenix_released:
        report.lockstep = False   # Phoenix 6 devices freed the robot's clock
    if session.bridge.realtime_reason:
        report.effective_speed = session.bridge.effective_speed
        report.notes.append(PHOENIX_NOTE)
        speed, b = session.phoenix_speed, session.bridge
        if b.physics_skips or (speed is not None and speed < PHOENIX_SLOW):
            report.notes.append(phoenix_slow_note(speed, b.physics_skips, b.physics_skipped))
        if session.bridge.robot_behind > ROBOT_BEHIND:
            report.notes.append(robot_behind_note(session.bridge.robot_behind))
    if (msg := session.bridge.fast_phoenix_message) is not None:
        report.notes.append(msg)
    if session.robot is not None or session.attach:   # a robot program: say which expected devices it never made
        report.notes.extend(device_notes(check_devices(session.world, session.bridge.hal, session.t), final=True))
    if not any(step.checks for step in scenario.steps):
        report.notes.append(NO_CHECKS_NOTE)
    await _console_settle(session, hooks)   # for "no crash in console"
    _implicit(session, scenario, report, console_start)
    if session.robot is not None:
        report.console_tail = [line.text for line in session.robot.since(console_start)][-50:]


async def _run_step(session: Session, step, index: int, out_dir: Path, rep: StepReport, hooks: _Hooks) -> None:
    checks: list[Check] = [build_check(c, session.world, step.name) for c in step.checks]
    session.bridge.ds.clear_joysticks()
    for port, axes in step.axes.items():
        session.set_joystick(port, axes=axes)
    for port, buttons in step.buttons.items():
        session.set_joystick(port, buttons=buttons)
    for port, povs in step.povs.items():
        session.set_joystick(port, povs=povs)
    track = step.inputs
    applied = -1

    def apply_track(elapsed: float) -> None:
        nonlocal applied
        k = track.at(elapsed)
        if k != applied:
            applied = k
            if k >= 0:
                for port, pi in track.samples[k].ports.items():
                    session.set_joystick(port, axes=pi.axes, buttons=pi.buttons, povs=pi.povs)

    start = session.t
    if track is not None:
        apply_track(0.0)
    if step.game_data is not None:      # it stays until a later step changes it
        session.bridge.ds.set_game_data(step.game_data)
    rep.game_data = session.bridge.ds.game_data
    session.set_mode(step.mode)
    consume_peaks(session.world)     # the step's checks see the terrain and climb signals from its start
    for c in checks:
        c.begin(session)
    tick_now = [c for c in checks if c.ticks and not c.uses_nt]
    tick_nt = [c for c in checks if c.ticks and c.uses_nt]
    for c in tick_nt:
        c.nt_period = NT_SETTLE

    def on_step(t: float) -> None:
        if track is not None:
            apply_track(t - start)
        tick_checks(tick_now, session, t)
        hooks.tick(session, t)
        if hooks.watch is not None:
            hooks.watch(session, t)

    events: dict[float, list] = {}
    for n, shot in enumerate(step.screenshots, 1):
        at = step.duration if shot.at == "end" else float(shot.at)
        events.setdefault(round(at, 6), []).append(("shot", n, shot))
    sheet_frames = []
    if step.contact_sheet is not None:
        for k in range(step.contact_sheet.frames):
            events.setdefault(round(step.duration * (k + 1) / step.contact_sheet.frames, 6), []).append(("frame",))
    if tick_nt:
        k = 1
        while k * NT_SETTLE < step.duration:
            events.setdefault(round(k * NT_SETTLE, 6), []).append(("nt",))
            k += 1
    events.setdefault(round(step.duration, 6), [])

    for target in sorted(events):
        dt = target - (session.t - start)
        if dt > 1e-9:
            extra = {"stop": hooks.cancelled} if hooks.cancel is not None else {}
            await session.advance(dt, on_step=on_step, **extra)
            if hooks.cancelled() and session.t - start < target - 1e-6:
                raise _Stopped  # the advance ended early
        for ev in events[target]:
            if ev[0] == "nt":
                await hooks.sleep(NT_SETTLE)
                for c in tick_nt:
                    c.tick(session, session.t)
            elif ev[0] == "shot":
                hooks.check()
                _, n, shot = ev
                name = f"step{index}-{n}-{shot.view.replace(':', '-')}.png"
                try:
                    png = await session.screenshot_png(shot.view, width=shot.width, height=shot.height,
                                                       camera=shot.camera)
                except SessionError as e:
                    rep.skipped.append(f"screenshot {name}: {e}")
                else:
                    (out_dir / name).write_bytes(png)
                    rep.screenshots.append(name)
            elif ev[0] == "frame":
                hooks.check()
                cs = step.contact_sheet
                try:
                    img = await session.render_async(cs.view, width=cs.width, height=cs.height)
                    sheet_frames.append((session.t, img))
                except SessionError as e:
                    if not any("contact sheet" in s for s in rep.skipped):
                        rep.skipped.append(f"contact sheet: {e}")
    if any(c.uses_nt for c in checks):
        await hooks.sleep(NT_SETTLE, raising=False)
        for c in tick_nt:
            c.tick(session, session.t)
    if any(isinstance(c, ConsoleCheck) for c in checks):
        await _console_settle(session, hooks)
    hooks.console(session)
    rep.checks = [c.end(session) for c in checks]
    if sheet_frames:
        name = f"step{index}-contact-sheet.png"
        (out_dir / name).write_bytes(await asyncio.to_thread(lambda: to_png(contact_sheet(sheet_frames))))
        rep.screenshots.append(name)
    rep.end_t = round(session.t, 4)


async def _console_settle(session: Session, hooks: _Hooks) -> None:
    """Hold the simulation while a Java robot's last console lines come through Gradle's output pump to its console
    file (see RobotProcess); a fast run's step can end before they do. RobotPy prints straight to scrimmage."""
    if session.robot is not None and session.robot_language() == "java":
        await hooks.sleep(CONSOLE_SETTLE, raising=False)


def _implicit(session: Session, scenario: Scenario, report: Report, console_start: int) -> None:
    session.bridge.settle_foreign_writes()  # judge writes from the last robot loop too
    if (scenario.require_lockstep and session.bridge.phoenix_released
            and (fast_phoenix := session.bridge.fast_phoenix_message) is not None):
        # In fast mode a Phoenix 6 device is a robot-code mistake, not a choice: the release fails the check.
        report.implicit.append(CheckResult("lockstep stayed engaged", False, fast_phoenix))
    elif scenario.require_lockstep and session.bridge.phoenix_released:   # Phoenix mode: released on purpose
        report.implicit.append(CheckResult("lockstep stayed engaged", True, PHOENIX_LOCKSTEP))
    elif scenario.require_lockstep:
        released = session.bridge.lockstep_released_at is not None
        report.implicit.append(CheckResult(
            "lockstep stayed engaged", not released,
            session.release_message() if released else "the robot program's clock stayed in lockstep"))
    if session.robot is not None:
        if any(c.check == "robot program running" for c in report.implicit):
            pass  # a failure is already recorded
        elif session.robot.running:
            report.implicit.append(CheckResult("robot program running", True, "the robot program kept running"))
        else:
            report.implicit.append(CheckResult("robot program running", False,
                                               f"the robot program exited (code {session.robot.returncode})"))
        crash = next((l for l in session.robot.since(console_start) if CRASH_PATTERN.search(l.text)), None)
        report.implicit.append(CheckResult(
            "no crash in console", crash is None,
            "no stack trace in the console" if crash is None else
            f"stack trace in the console at t={crash.t:.3f}: {crash.text}"))
    if binds_rev(session.config.raw):
        problem = session.rev_problem()
        report.implicit.append(CheckResult("REV simulation available", problem is None,
                                           problem or ("the scrimmage REV helper reported no robotpy-rev problem"
                                                   if session.robot_language() == "python" else
                                                   "the scrimmage agent reported no REVLib problem")))
    fw = list(session.bridge.foreign_writes.values())
    summary = session.agent_summary()
    if not fw:
        report.implicit.append(CheckResult("no foreign sim writes", True,
                                           "robot code left scrimmage's simulated sensor values alone"))
        m = AGENT_IGNORED.match(summary or "")
        if m:
            report.notes.append(f"the scrimmage {m.group(1)} neutralized {m.group(2)} robot-side sim writes "
                                f"({m.group(3)})")
        return
    agent = session.agent_state()
    language = session.robot_language()
    msg = " ".join(fwn.message(dev, writes, agent, language) for dev, writes in fwn.by_device(fw).items())
    if summary:
        msg += f" Agent: {summary}"
    report.implicit.append(CheckResult("no foreign sim writes", False, msg,
                                       first_failure_t=min(f["first_t"] for f in fw)))
