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
from . import i18n, units
from .bindings import binds_rev
from .bridge import REALTIME_REASON
from .devicecheck import check_devices, device_notes
from .checks import (CRASH_PATTERN, Check, CheckResult, ConsoleCheck, Said, build_check, consume_peaks, said, shown,
                     tick_checks, worded)
from .i18n import N_, t, tn, translated
from .config import ConfigError, build_world, load_config
from .render import contact_sheet, to_png
from .scenario import Scenario, ScenarioError, config_for, match_for, no_range_message, range_file, validate
from .session import Session, SessionError
from .testrange.balls import took_part  # here before; the Test tab's runs (blocks/run_notes.py) use it too

log = logging.getLogger("scrimmage")

NT_SETTLE = 0.25   # wall seconds the simulation is held so the robot's NetworkTables can catch up
CONSOLE_SETTLE = 0.25  # wall seconds held so a Java robot's last console lines reach its console file
UNSET = object()
TICK_PERIOD = 0.25  # sim seconds between progress "tick" events
STOPPED = "stopped by the user"
# the Java agent's summary line, or the RobotPy REV helper's (scrimmage_lockstep)
AGENT_IGNORED = re.compile(r"scrimmage (agent|rev helper): ignored (\d+) robot-side sim writes \((.*)\)$")
# (N_(): report.json keeps the text itself; report.md and the Scenario tab show it in the Language setting, _said())
NO_CHECKS_NOTE = N_("this scenario has no checks, so passing only means the robot program ran: add a check (where "
                    "the robot ends up, say) to test something")
# Below this fraction of real time, or skipping ahead at all, a Phoenix 6 run's physics is reported as not keeping up
PHOENIX_SLOW = 0.8
# A Phoenix 6 robot program further behind real time than this (s) is reported as not keeping up: silent that long,
# or relaying values that long superseded (12 robot loops; a 2-core machine with spare CPU stays under 0.11 s)
ROBOT_BEHIND = 0.25
PHOENIX_LOCKSTEP = N_("Phoenix 6 devices: this robot runs in real time, not lockstep")
# D3: a scenario is the Driver Station, so a run refuses the real one, and ignores a config's default
REAL_DS_REFUSED = N_("a scenario run can't use the real Driver Station: the scenario is the Driver Station here (it "
                     "sets the modes and joysticks); run it without the real Driver Station")
CONFIG_REAL_DS_NOTE = N_("the config's {driver_station_table} {real_setting} is ignored here: in a scenario run, "
                         "scrimmage is the Driver Station")
CONFIG_REAL_DS_KEYS = {"driver_station_table": "[driver_station]", "real_setting": "real = true"}
LOCKSTEP_REQUIRED = N_("lockstep is required but the robot program didn't load the scrimmage_lockstep extension "
                       "(RobotPy: add --scrimmage-lockstep to {robot_program_table} command; or set "
                       "{require_lockstep_setting})")
LOCKSTEP_REQUIRED_KEYS = {"robot_program_table": "[robot_program]",
                          "require_lockstep_setting": "require_lockstep = false"}


def _said(text: str, **params) -> Said:
    """A constant marked with N_(): itself in report.json, in the Language setting where it is shown. `params` fill
    its placeholders (what a student types, kept out of the words)."""
    if params:
        return Said(translated(text, lang=i18n.SOURCE, **params), lambda: translated(text, **params))
    return Said(text, lambda: translated(text))


def _phoenix_note() -> Said:
    """The run's note for Phoenix 6's real time: the bridge's REALTIME_REASON (one source for both) and what it
    means for the results."""
    return Said(f"{REALTIME_REASON}; results are close but not byte-identical",
                lambda: t("{reason}; results are close but not byte-identical", reason=translated(REALTIME_REASON)))


def _shown_now(words: Callable[[], str]) -> Callable[[], str]:
    """`words()` in the Language setting now, kept for when it is shown: a session's words (the bridge's, a released
    clock), which can't be built again once the run is over. Called where the run's own text is the machine text
    (inside i18n.machine(), as the web app's runs and MCP's are), `words()` itself is report.json's."""
    with i18n.for_people():
        text = words()
    return lambda: text


def _plain(text):
    """report.json's text: a Said as the plain str it is (None stays None)."""
    return None if text is None else str(text)


def phoenix_slow_note(speed: float | None, skips: int = 0, skipped: float = 0.0) -> str:
    """Physics in a real-time run below PHOENIX_SLOW of real time, or skipping ahead at all (`skips` times, `skipped`
    simulated seconds in all): a run that loses a second to a few skips still averages 0.9x."""
    kept = f"{speed:.1f}x real time" if speed is not None else "part of real time"
    jumps = f" and skipped ahead {skipped:.2f} s ({skips} time{'' if skips == 1 else 's'})" if skips else ""
    text = (f"physics on this computer kept up with only {kept}: physics fell behind real time{jumps}, so results "
            "may differ from a run that keeps up")
    at = {"speed": f"{speed:.1f}" if speed is not None else None, "skipped": f"{skipped:.2f}"}

    def words() -> str:
        if skips and speed is not None:
            return tn("physics on this computer kept up with only {speed}x real time: physics fell behind real time "
                      "and skipped ahead {skipped} s ({count} time), so results may differ from a run that keeps up",
                      "physics on this computer kept up with only {speed}x real time: physics fell behind real time "
                      "and skipped ahead {skipped} s ({count} times), so results may differ from a run that keeps up",
                      skips, speed=at["speed"], skipped=at["skipped"])
        if skips:
            return tn("physics on this computer kept up with only part of real time: physics fell behind real time "
                      "and skipped ahead {skipped} s ({count} time), so results may differ from a run that keeps up",
                      "physics on this computer kept up with only part of real time: physics fell behind real time "
                      "and skipped ahead {skipped} s ({count} times), so results may differ from a run that keeps up",
                      skips, skipped=at["skipped"])
        if speed is not None:
            return t("physics on this computer kept up with only {speed}x real time: physics fell behind real time, "
                     "so results may differ from a run that keeps up", speed=at["speed"])
        return t("physics on this computer kept up with only part of real time: physics fell behind real time, so "
                 "results may differ from a run that keeps up")
    return Said(text, words)


def robot_behind_note(seconds: float) -> str:
    return Said(f"the robot program fell {seconds:.2f} s behind real time (silent, or relaying values that long out of "
                "date): Phoenix 6 runs on the wall clock, so its timing may differ from a run that keeps up",
                lambda: t("the robot program fell {seconds} s behind real time (silent, or relaying values that long "
                          "out of date): Phoenix 6 runs on the wall clock, so its timing may differ from a run that "
                          "keeps up", seconds=f"{seconds:.2f}"))


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

    def to_dict(self, shown: bool = False) -> dict:
        say = _shown if shown else _plain
        return {"name": self.name, "mode": self.mode, "start_t": self.start_t, "end_t": self.end_t,
                "game_data": self.game_data, "passed": self.passed, "interrupted": say(self.interrupted),
                "checks": [c.to_dict(shown) for c in self.checks], "screenshots": self.screenshots,
                "skipped": [say(x) for x in self.skipped]}


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

    def to_dict(self, shown: bool = False) -> dict:
        """report.json: the machine messages (their old text: American English, in meters); with `shown`, in the
        Units and Language settings (the Scenario tab)."""
        say = _shown if shown else _plain
        d = {"scenario": self.scenario, "description": self.description, "passed": self.passed,
             "exit_code": self.exit_code, "error": say(self.error), "lockstep": self.lockstep,
             "steps": [s.to_dict(shown) for s in self.steps], "implicit": [c.to_dict(shown) for c in self.implicit],
             "effective_speed": self.effective_speed, "notes": [say(n) for n in self.notes],
             "console_tail": self.console_tail, "files": self.files}
        if self.range is not None:      # a run on a test range; other reports keep their keys
            d.update(range=self.range, balls=self.balls, ball_states=self.ball_states, ball_summary=self.ball_summary)
        return d

    def summary(self) -> str:
        """The terminal's summary (the CLI prints it in American English, inside i18n.fixed())."""
        if self.error is not None:
            return f"ERROR  {self.scenario}: {_shown(self.error)}"
        lines = [f"{'PASS' if self.passed else 'FAIL'}  {self.scenario}"]
        for s in self.steps:
            for c in s.checks:
                lines.append(f"  {'ok  ' if c.passed else 'FAIL'} [{s.name}] {c.shown()}")
            if s.interrupted:
                lines.append(f"  FAIL [{s.name}] {_shown(s.interrupted)}")
        for c in self.implicit:
            if not c.passed:
                lines.append(f"  FAIL {c.shown()}")
        lines += [f"  note: {_shown(n)}" for n in self.notes]
        return "\n".join(lines)

    def write(self, out_dir: Path) -> None:
        self.files = {k: v for k, v in OUTPUT_FILES.items()
                      if k in ("report", "report_md") or (out_dir / v).exists()}
        (out_dir / "report.json").write_text(json.dumps(self.to_dict(), indent=2, default=str))
        with i18n.for_people():     # report.md is for people: in the Units and Language settings (an MCP run's too)
            (out_dir / "report.md").write_text(self._markdown())

    def _markdown(self) -> str:
        if self.passed:
            head = t("PASS: {scenario}", scenario=self.scenario)
        elif self.error:
            head = t("ERROR: {scenario}", scenario=self.scenario)
        else:
            head = t("FAIL: {scenario}", scenario=self.scenario)
        md = [f"# {head}", ""]
        if self.description:
            md += [self.description, ""]
        if self.error:
            md += [t("**Couldn't run:** {error}", error=_shown(self.error)), ""]
        if self.notes:
            md += ["> " + t("Note: {note}", note=_shown(n)) for n in self.notes] + [""]
        for s in self.steps:
            at = {"name": s.name, "mode": s.mode, "start": f"{s.start_t:.2f}", "end": f"{s.end_t:.2f}"}
            if s.game_data:
                title = t("{name} ({mode}, t={start}–{end} s, game data {data})",
                          data=json.dumps(s.game_data, ensure_ascii=False), **at)
            else:
                title = t("{name} ({mode}, t={start}–{end} s)", **at)
            md += [f"## {title}", ""]
            md += [f"- {'✅' if c.passed else '❌'} {c.shown()}" for c in s.checks]
            md += [f"- ❌ {_shown(s.interrupted)}"] if s.interrupted else []
            md += ["- " + t("skipped: {what}", what=_shown(x)) for x in s.skipped]
            md += [""] + [f"![{p}]({p})" for p in s.screenshots] + [""]
        if self.range is not None:
            md += _score_md(self.range) + _balls_md(self.balls, self.ball_summary or {})
        bad = [c for c in self.implicit if not c.passed]
        if bad:
            md += ["## " + t("Robot program"), ""] + [f"- ❌ {c.shown()}" for c in bad] + [""]
        if self.console_tail:
            md += ["## " + t("Console (last lines)"), "", "```", *self.console_tail, "```", ""]
        return "\n".join(md)


_shown = shown


def _f(v, fmt: str = ".2f") -> str:
    return "–" if v is None else format(v, fmt)


def _fu(v, kind: str, fmt: str = ".2f") -> str:
    """A position or speed in the Units setting, as report.js's ball table shows it."""
    return _f(None if v is None else units.to_display(v, kind), fmt)


def _score_md(c: dict) -> list[str]:
    goals = ", ".join(f"{g} {n}" for g, n in c["goals"].items())
    return ["## " + t("Score"), "", "- " + t("score: {score} ({goals})", score=c["score"], goals=goals),
            "- " + t("fired: {count}", count=c["fired"]), "- " + t("held: {count}", count=c["held"]),
            "- " + t("on the field: {count}", count=c["on_field"]), ""]


def _ball_result(b: dict) -> str:
    """Where a ball ended up: the goal and time it scored, where it left the field or came to rest, or why the
    launcher kept it; what it hit first after its release; and for a ball fired that didn't score, how high it got
    against the rim of the goal nearest its release."""
    out, pos = b["outcome"], b.get("last_pos")
    parts = []
    if out == "scored":
        parts.append(t("{goal} at t={t}", goal=b["goal"], t=_f(b["scored_t"])))
    elif out == "stuck":
        parts.append(t("launcher: {status}", status=b["launcher_status"]))
    elif pos is not None:
        u = units.unit_name("field")
        xyz = f"({_fu(pos[0], 'field')}, {_fu(pos[1], 'field')}, {_fu(pos[2], 'field')}) {u}"
        if out == "out_of_field":
            parts.append(t("left at {where}", where=xyz))
        elif out == "in_flight":
            parts.append(t("flying at {where}", where=xyz))
        elif out == "on_field" and b.get("at_rest"):
            parts.append(t("rest at {where}", where=f"({_fu(pos[0], 'field')}, {_fu(pos[1], 'field')}) {u}"))
        elif out == "on_field":
            parts.append(t("moving at {where}", where=xyz))
        else:
            parts.append(xyz)
    if b.get("first_hit"):
        parts.append(t("first hit {what}", what=b["first_hit"]))
    if out != "scored" and b.get("fired_t") is not None and b.get("apex_z") is not None and b.get("nearest_goal"):
        u = units.unit_name("field")
        parts.append(t("apex {apex} vs rim {rim} ({goal})", apex=f"{_fu(b['apex_z'], 'field')} {u}",
                       rim=f"{_fu(b.get('rim_height'), 'field')} {u}", goal=b["nearest_goal"]))
    return "; ".join(parts)


def _ball_columns() -> list[str]:
    """The ball table's header, in the Units setting (report.js's)."""
    f, v = units.unit_name("field"), units.unit_name("speed")
    return ["#", t("fired t"), f"x {f}", f"y {f}", t("heading"), f"vx {v}", f"vy {v}", "ω °/s", t("hood °"), "rps",
            t("exit {unit}", unit=v), t("spin rpm"), t("slip"), t("outcome"),
            t("goal and time / rest point / first hit")]


def _ball_row(b: dict) -> list[str]:
    pose = b.get("robot_pose") or (None, None, None)
    vel = b.get("robot_velocity") or (None, None, None)
    return [str(b["id"]), _f(b["fired_t"]), _fu(pose[0], "field"), _fu(pose[1], "field"), _f(pose[2], ".1f"),
            _fu(vel[0], "speed"), _fu(vel[1], "speed"), _f(vel[2], ".1f"), _f(b["hood_deg"], ".1f"),
            _f(b["shooter_rps"], ".1f"), _fu(b["exit_speed"], "speed"),
            _f(b["spin_rpm"], ".0f"), _f(b["exit_slip"]), b["outcome"], _ball_result(b)]


def not_listed(summary: dict) -> str:
    """The line under the ball table for the balls it leaves out (report.js says the same)."""
    parts = []
    if summary.get("untouched"):
        parts.append(tn("{count} floor ball untouched (still where the range put it)",
                        "{count} floor balls untouched (still where the range put them)", summary["untouched"]))
    if summary.get("spare"):
        parts.append(tn("{count} spare ball (unused slots, or floor balls turned off)",
                        "{count} spare balls (unused slots, or floor balls turned off)", summary["spare"]))
    return t("Not listed: {balls}.", balls="; ".join(parts)) if parts else ""


def _balls_md(balls: list[dict], summary: dict) -> list[str]:
    """One row per ball that took part; the untouched floor balls and the spares counted under the table."""
    md = ["## " + t("Balls"), ""]
    if balls:
        columns = _ball_columns()
        md += ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
        md += ["| " + " | ".join(_ball_row(b)) + " |" for b in balls]
    else:
        md += [t("No ball took part.")]
    if line := not_listed(summary):
        md += ["", line]
    return md + [""]


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
                       watch: Callable[[Session, float], None] | None = None, sim_mode: str | None = None,
                       real_ds: bool | None = None) -> Report:
    """Runs the scenario. ``progress(event)`` gets step_start / tick / console / done dicts; ``cancel`` is a
    threading.Event checked between advances (when set the session stops and the report's error is
    "stopped by the user"); ``watch(session, t)`` is called after every robot step (a live view's frames).
    ``sim_mode`` ("fast" / "phoenix") overrides the config's, for a session this run starts. ``real_ds=True`` (the
    real Driver Station) is refused: the report's error says why."""
    hooks = _Hooks(progress, cancel, watch)
    done = {"type": "done", "passed": False}
    try:
        report = await _run_scenario(scenario, out_dir, speed, session, hooks, sim_mode, real_ds)
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
                        sim_mode: str | None = None, real_ds: bool | None = None) -> Report:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = Report(scenario.path.name, scenario.description)
    own = session is None
    console_start = 0  # this run's first console line (a passed-in session may have older ones)
    if real_ds or (session is not None and session.real_ds):
        report.error = _said(REAL_DS_REFUSED)
        report.write(out_dir)
        return report
    try:
        if own:
            config = config_for(scenario, load_config(scenario.config_path))
            if config.real_ds:
                report.notes.append(_said(CONFIG_REAL_DS_NOTE, **CONFIG_REAL_DS_KEYS))
            session = Session(config, speed=scenario.speed if speed is UNSET else speed,
                              log_path=out_dir / "run.csv", robot_log=out_dir / "robot.log",
                              picture_sizes=picture_sizes(scenario), sim_mode=sim_mode, real_ds=False)
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
        report.error = _stopped()
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
        report.error = (Said("interrupted", lambda: t("interrupted")) if interrupted else
                        Said(f"unexpected error: {e!r}", lambda why=repr(e): t("unexpected error: {why}", why=why)))
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


def moved_joints(joints: dict[str, tuple[float, float]]) -> Said:
    """", with joint.<n>.position = v, ..." for each robot joint in `joints` ({name: (position, rest)}, the monitor's
    snapshot at the event) more than JOINT_MOVED from its rest, else "": the machine text, and .words() in the
    Language setting (worded())."""
    moved = ", ".join(f"joint.{n}.position = {pos:.3g}" for n, (pos, rest) in joints.items()
                      if abs(pos - rest) > JOINT_MOVED)
    return worded(lambda: t(", with {joints}", joints=moved) if moved else "")


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
        at, tilt, (x, y, _) = tip
        joints = dict(terrain.tipped_joints)
        notes.append(Said(f"the robot tipped over at t={at:.2f} s (tilt {tilt:.0f}°, at x={x:.2f}, y={y:.2f})"
                          f"{moved_joints(joints)}",
                          lambda at=at, tilt=tilt, x=x, y=y, joints=joints:
                          t("the robot tipped over at t={t} s (tilt {tilt}°, at x={x}, y={y})", t=f"{at:.2f}",
                            tilt=f"{tilt:.0f}", x=f"{x:.2f}", y=f"{y:.2f}") + moved_joints(joints).words()))
    if strand is not None and strand != noted.get("stranded"):
        noted["stranded"] = strand
        at, what = strand
        joints = dict(terrain.stranded_joints)
        notes.append(Said(f"the robot was stranded at t={at:.2f} s, resting on {what}{moved_joints(joints)}",
                          lambda at=at, what=what, joints=joints:
                          t("the robot was stranded at t={t} s, resting on {what}", t=f"{at:.2f}", what=what)
                          + moved_joints(joints).words()))
    return notes


def _stopped() -> Said:
    return Said(STOPPED, lambda: t("stopped by the user"))


def _interrupted(at: float) -> Said:
    return Said(f"step interrupted at t={at:.3f}", lambda: t("step interrupted at t={t}", t=f"{at:.3f}"))


async def _run_steps(session: Session, scenario: Scenario, report: Report, out_dir: Path, console_start: int,
                     hooks: _Hooks) -> None:
    report.lockstep = session.lockstep
    if scenario.require_lockstep and not (session.lockstep or session.bridge.clock_freed):
        report.error = _said(LOCKSTEP_REQUIRED, **LOCKSTEP_REQUIRED_KEYS)
        return
    if session.bridge.nt_link is None and any(build_check(c, session.world, s.name).uses_nt
                                              for s in scenario.steps for c in s.checks):
        if not session.config.nt:
            why = "the config sets [telemetry] nt = false, and these checks need NetworkTables"
        elif not session.nt:
            why = "this session was started without NetworkTables (nt=False)"
        else:
            why = "NetworkTables isn't available: install scrimmage[nt] (pyntcore)"
        config_off, session_off = not session.config.nt, not session.nt

        def words() -> str:
            if config_off:
                return t("this scenario checks NetworkTables values (nt: signals or odometry_error), but the config "
                         "sets {telemetry_table} {nt_setting}, and these checks need NetworkTables",
                         telemetry_table="[telemetry]", nt_setting="nt = false")
            if session_off:
                return t("this scenario checks NetworkTables values (nt: signals or odometry_error), but this session "
                         "was started without NetworkTables ({nt_off})", nt_off="nt=False")
            return t("this scenario checks NetworkTables values (nt: signals or odometry_error), but NetworkTables "
                     "isn't available: install {nt_extra} (pyntcore)", nt_extra="scrimmage[nt]")
        report.error = Said(f"this scenario checks NetworkTables values (nt: signals or odometry_error), but {why}",
                            words)
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
            rep.interrupted = _interrupted(session.t)
            report.error = _stopped()
            return
        except SessionError as e:
            rep.end_t = round(session.t, 4)
            rep.interrupted = _interrupted(session.t)
            report.implicit.append(CheckResult("robot program running", False, str(e), first_failure_t=e.t))
            break
        except BaseException:
            rep.end_t = round(session.t, 4)
            rep.interrupted = _interrupted(session.t)
            raise
        finally:    # on every exit of the step: a robot that tipped and then lost its program is still noted
            report.notes.extend(terrain_notes(session.world, noted))
    report.notes.extend(session.world.warnings)
    if session.bridge.clock_freed:
        report.lockstep = False   # Phoenix 6 devices freed the robot's clock
    if session.bridge.realtime_reason:
        report.effective_speed = session.bridge.effective_speed
        report.notes.append(_phoenix_note())
        speed, b = session.phoenix_speed, session.bridge
        if b.physics_skips or (speed is not None and speed < PHOENIX_SLOW):
            report.notes.append(phoenix_slow_note(speed, b.physics_skips, b.physics_skipped))
        if session.bridge.robot_behind > ROBOT_BEHIND:
            report.notes.append(robot_behind_note(session.bridge.robot_behind))
    if (msg := session.bridge.fast_phoenix_message) is not None:
        report.notes.append(Said(msg, _shown_now(lambda: session.bridge.fast_phoenix_message)))
    if session.robot is not None or session.attach:   # a robot program: say which expected devices it never made
        rows = check_devices(session.world, session.bridge.hal, session.t)
        shown = _shown_now(lambda: device_notes(check_devices(session.world, session.bridge.hal, session.t),
                                                final=True))()    # the same notes in the Language setting
        report.notes.extend(Said(note, lambda s=s: s) for note, s in zip(device_notes(rows, final=True), shown))
    if not any(step.checks for step in scenario.steps):
        report.notes.append(_said(NO_CHECKS_NOTE))
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
                    rep.skipped.append(Said(f"screenshot {name}: {e}", lambda name=name, why=str(e):
                                            t("screenshot {name}: {why}", name=name, why=why)))
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
                        rep.skipped.append(Said(f"contact sheet: {e}", lambda why=str(e):
                                                t("contact sheet: {why}", why=why)))
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
    if (scenario.require_lockstep and session.bridge.clock_freed
            and (fast_phoenix := session.bridge.fast_phoenix_message) is not None):
        # In fast mode a Phoenix 6 device is a robot-code mistake, not a choice: the release fails the check.
        report.implicit.append(said("lockstep stayed engaged", False, fast_phoenix,
                                    _shown_now(lambda: session.bridge.fast_phoenix_message)))
    elif scenario.require_lockstep and session.bridge.clock_freed:   # Phoenix mode: released on purpose
        report.implicit.append(said("lockstep stayed engaged", True, PHOENIX_LOCKSTEP,
                                    lambda: translated(PHOENIX_LOCKSTEP)))
    elif scenario.require_lockstep:
        released = session.bridge.lockstep_released_at is not None
        if released:    # (the session's words: report.json's as the run made them, shown in the Language)
            report.implicit.append(said("lockstep stayed engaged", False, session.release_message(),
                                        _shown_now(session.release_message)))
        else:
            report.implicit.append(said("lockstep stayed engaged", True, "the robot program's clock stayed in lockstep",
                                        lambda: t("the robot program's clock stayed in lockstep")))
    if session.robot is not None:
        if any(c.check == "robot program running" for c in report.implicit):
            pass  # a failure is already recorded
        elif session.robot.running:
            report.implicit.append(said("robot program running", True, "the robot program kept running",
                                        lambda: t("the robot program kept running")))
        else:
            code = session.robot.returncode
            report.implicit.append(said("robot program running", False, f"the robot program exited (code {code})",
                                        lambda: t("the robot program exited (code {code})", code=code)))
        crash = next((l for l in session.robot.since(console_start) if CRASH_PATTERN.search(l.text)), None)
        if crash is None:
            report.implicit.append(said("no crash in console", True, "no stack trace in the console",
                                        lambda: t("no stack trace in the console")))
        else:
            report.implicit.append(said("no crash in console", False,
                                        f"stack trace in the console at t={crash.t:.3f}: {crash.text}",
                                        lambda: t("stack trace in the console at t={t}: {line}", t=f"{crash.t:.3f}",
                                                  line=crash.text)))
    if binds_rev(session.config.raw):
        problem = session.rev_problem()
        if problem:     # (the session's words: report.json's as the run made them, shown in the Language)
            report.implicit.append(said("REV simulation available", False, problem, _shown_now(session.rev_problem)))
        elif session.robot_language() == "python":
            report.implicit.append(said("REV simulation available", True,
                                        "the scrimmage REV helper reported no robotpy-rev problem",
                                        lambda: t("the scrimmage REV helper reported no robotpy-rev problem")))
        else:
            report.implicit.append(said("REV simulation available", True,
                                        "the scrimmage agent reported no REVLib problem",
                                        lambda: t("the scrimmage agent reported no REVLib problem")))
    fw = list(session.bridge.foreign_writes.values())
    summary = session.agent_summary()
    if not fw:
        report.implicit.append(said("no foreign sim writes", True,
                                    "robot code left scrimmage's simulated sensor values alone",
                                    lambda: t("robot code left scrimmage's simulated sensor values alone")))
        m = AGENT_IGNORED.match(summary or "")
        if m:
            who, n, what = m.groups()
            # normal on every Tuner X robot (its sim thread): said as expected, not as a problem (usability 14)
            report.notes.append(Said(f"as expected, the scrimmage {who} ignored the robot code's own simulation "
                                     f"({n} sim writes: {what}): scrimmage's physics drives the sensors",
                                     lambda: t("as expected, the scrimmage agent ignored the robot code's own "
                                               "simulation ({n} sim writes: {what}): scrimmage's physics drives the "
                                               "sensors", n=n, what=what) if who == "agent" else
                                     t("as expected, the scrimmage rev helper ignored the robot code's own "
                                       "simulation ({n} sim writes: {what}): scrimmage's physics drives the sensors",
                                       n=n, what=what)))
        return
    agent = session.agent_state()
    language = session.robot_language()

    def explained() -> str:       # the agent's summary is its console's words, as it wrote them
        msg = " ".join(fwn.message(dev, writes, agent, language) for dev, writes in fwn.by_device(fw).items())
        return t("{writes} Agent: {summary}", writes=msg, summary=summary) if summary else msg
    report.implicit.append(said("no foreign sim writes", False, explained(), _shown_now(explained),
                                None, None, min(f["first_t"] for f in fw)))
