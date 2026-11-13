"""REV SPARK MAX robots under scrimmage: tests/rev_probe (real Gradle, real REVLib 2026.0.5) on tests/rev_probe.toml.

The arm: SPARK MAX 1 (NEO, 60:1, absolute encoder on its data port, forward limit switch at the top of the range) and
SPARK MAX 2 following it inverted, on a motor that faces the other way across the gearbox. The flywheel: SPARK MAX 3
(the robot creates it in teleopInit) and SPARK MAX 4 (follows CAN id 9, which the robot never creates).

Each session is started once and its checks share the recording (a session start costs a Gradle run)."""

import asyncio
import csv
import logging
import math
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from scrimmage import java_tools
from scrimmage.session import Session

TESTS = Path(__file__).resolve().parent
PROBE = TESTS / "rev_probe"
pytestmark = pytest.mark.java

ZERO_OFFSET = 0.1        # the probe's absoluteEncoder.zeroOffset (rotations)
UPPER_DEG = 110.0        # the arm's range in rev_probe.toml: the forward limit switch is pressed here
REV_LINE = re.compile(r"^rev loop=(\d+) (.*)$")


@pytest.fixture(scope="module", autouse=True)
def built_agent():
    java_tools.build_agent()


@pytest.fixture(scope="module", autouse=True)
def warm_gradle(built_agent):
    """Download dependencies and compile once, so the timed session starts don't race a cold Gradle cache."""
    r = subprocess.run(["./gradlew", "build"], cwd=PROBE, env=dict(os.environ, JAVA_HOME=str(java_tools.find_jdk())),
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, "gradlew build failed:\n" + (r.stdout + r.stderr)[-3000:]


def probe_config(tmp: Path, **env) -> Path:
    """tests/rev_probe.toml, run from `tmp` with extra [robot_program] env."""
    text = (TESTS / "rev_probe.toml").read_text().replace('cwd = "rev_probe"', f'cwd = "{PROBE}"')
    if env:
        table = ", ".join(f'{k} = "{v}"' for k, v in env.items())
        text = text.replace("start_timeout = 180\n", f"start_timeout = 180\nenv = {{ {table} }}\n")
    p = tmp / "rev_probe.toml"
    p.write_text(text)
    return p


# ------------------------------------------------------------------ recording


@dataclass
class Run:
    """What one probe session did: a row per lockstep step (scrimmage's side) and the robot's prints."""
    rows: list[dict] = field(default_factory=list)
    prints: list[dict] = field(default_factory=list)
    marks: dict[str, int] = field(default_factory=dict)   # phase name -> index of its first row
    console: str = ""
    status: dict = field(default_factory=dict)
    wall: float = 0.0       # wall seconds spent advancing (holds excluded)
    warnings: list[str] = field(default_factory=list)  # scrimmage's warnings, where a fixture keeps them
    devices: list = field(default_factory=list)        # the robot's HAL devices at the end, where a fixture keeps them
    simulated: float = 0.0  # simulated seconds advanced

    def phase(self, name: str, until: str | None = None) -> list[dict]:
        end = self.marks[until] if until else len(self.rows)
        return self.rows[self.marks[name]:end]

    def prints_between(self, t0: float, t1: float) -> list[dict]:
        return [p for p in self.prints if t0 <= p["t"] < t1]


class Recorder:
    def __init__(self, s: Session, run: Run):
        self.s, self.run = s, run

    def __call__(self, t: float) -> None:
        joints = self.s.world.joint_states()
        hal = self.s.bridge.hal.devices
        volts = {i: float(hal.get(("ScrimmageREV", f"SparkMax[{i}]"), {}).get("<motorVoltage", 0.0) or 0.0)
                 for i in (1, 2, 3, 4)}
        self.run.rows.append({"t": t, "arm": math.degrees(joints["arm.pivot"][0]),
                              "arm_v": math.degrees(joints["arm.pivot"][1]), "shooter_v": joints["shooter.spin"][1],
                              "vbus": self.s.world.vbatt, **{f"v{i}": v for i, v in volts.items()}})

    def mark(self, name: str) -> None:
        self.run.marks[name] = len(self.run.rows)

    async def advance(self, seconds: float) -> None:
        start = time.monotonic()
        await self.s.advance(seconds, self)
        self.run.wall += time.monotonic() - start
        self.run.simulated += seconds


def parse_prints(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = REV_LINE.match(line)
        if m:
            row = {"loop": int(m.group(1))}
            for item in m.group(2).split():
                k, _, v = item.partition("=")
                row[k] = v == "true" if v in ("true", "false") else float(v)
            out.append(row)
    return out


async def finish(s: Session, run: Run) -> None:
    """Wait for the robot's prints to catch up with the simulation (Gradle forward output a little behind)."""
    last_loop = int(round(s.t / 0.02)) - 5
    for _ in range(150):
        run.console = "\n".join(l["text"] for l in s.console()["lines"])
        run.prints = parse_prints(run.console)
        if run.prints and run.prints[-1]["loop"] >= last_loop:
            break
        await asyncio.sleep(0.1)
    run.status = s.status()


def window(run: Run, tp: float, key, before: float = 0.03, after: float = 0.006) -> list[float]:
    """scrimmage's values of `key` (a row key or function of a row) from shortly before a robot print at robot time
    tp: the robot's loop reads what scrimmage sent during the previous loop period."""
    f = key if callable(key) else (lambda r: r[key])
    return [f(r) for r in run.rows if tp - before <= r["t"] <= tp + after]


def within(value: float, candidates: list[float], tol: float, period: float | None = None) -> bool:
    if not candidates:
        return False
    if period is not None:  # compare on a circle: unwrap the candidates next to the value
        candidates = [value + ((c - value + period / 2) % period - period / 2) for c in candidates]
    return min(candidates) - tol <= value <= max(candidates) + tol


def mismatches(run: Run, prints: list[dict], name: str, expected, tol: float, period: float | None = None) -> list:
    bad = []
    for p in prints:
        cands = window(run, p["t"], expected)
        if not within(p[name], cands, tol, period):
            bad.append((p["loop"], p[name], [round(c, 3) for c in cands]))
    return bad


def abs_reading(arm_deg: float, inverted: bool = False) -> float:
    """What the probe's absolute encoder reads (degrees) for an arm angle, per REVLib's zeroOffset definition: the
    offset is the raw reading (not inverted, factor 1) at the desired zero; inversion turns the offset reading round."""
    x = arm_deg / 360.0 - ZERO_OFFSET
    if inverted:
        x = -x
    return (x % 1.0) * 360.0


# ------------------------------------------------------------------ the main session


@pytest.fixture(scope="module")
def main_run(tmp_path_factory) -> Run:
    """Defaults (SPARK 1 not inverted, closed loop on the built-in encoder). Disabled with a duty cycle held for 1 s;
    teleop duty cycle 0.3 for 2 s with a 2 s hold after the first 0.4 s; zero the encoders at the top (the arm rests
    on the forward limit); then duty cycle -0.3 for 0.5 s and 0 for 0.3 s."""
    cfg = probe_config(tmp_path_factory.mktemp("rev_main"))
    run = Run()

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            rec = Recorder(s, run)
            s.set_joystick(0, axes=[0.3, 0.0])
            rec.mark("disabled")
            await rec.advance(1.0)
            s.set_mode("teleop")
            rec.mark("up")
            await rec.advance(0.4)
            rec.mark("held")
            await asyncio.sleep(2.0)       # held: the robot is paused between steps
            await rec.advance(1.6)
            rec.mark("zero")
            s.set_joystick(0, axes=[-0.3, 0.0], buttons=[False, False, True])
            await rec.advance(0.02)
            s.set_joystick(0, axes=[-0.3, 0.0], buttons=[False, False, False])
            rec.mark("down")
            await rec.advance(0.5)
            s.set_joystick(0, axes=[0.0, 0.0])
            await rec.advance(0.3)
            await finish(s, run)
        finally:
            await s.stop()
    asyncio.run(main())
    return run


def test_disabled_robot_outputs_nothing_with_a_duty_cycle_held(main_run):
    rows = main_run.phase("disabled", "up")
    assert all(r["v1"] == 0.0 and r["v2"] == 0.0 for r in rows), [(r["t"], r["v1"], r["v2"]) for r in rows[::20]]
    assert max(r["arm"] for r in rows) < 0.05                   # the arm only sags under gravity (brake mode)
    assert all(p["app1"] == 0.0 for p in main_run.prints_between(0.05, 1.0))


def test_duty_cycle_raises_the_arm_and_the_inverted_follower_helps(main_run):
    rows = main_run.phase("up", "held")
    last = rows[-1]
    assert last["v1"] == pytest.approx(0.3 * last["vbus"], rel=0.02)
    assert last["v2"] == pytest.approx(-last["v1"], rel=1e-6)    # follow(arm, true): the opposite voltage...
    assert last["arm_v"] > 100.0                                 # ...on a motor facing the other way: both lift
    assert last["arm"] - rows[0]["arm"] > 20.0
    moving = [p for p in main_run.prints_between(rows[5]["t"], last["t"])]
    assert moving and all(p["app2"] == pytest.approx(-p["app1"]) and p["app1"] == pytest.approx(0.3) for p in moving)
    # the follower's getOutputCurrent() is its own motor's current, which matches the leader's on a shared gearbox
    assert all(p["cur1"] > 0.1 and p["cur2"] == pytest.approx(p["cur1"], rel=0.05) for p in moving), moving


def test_hold_and_resume_continue_without_a_jump(main_run):
    i = main_run.marks["held"]
    before, after = main_run.rows[i - 10:i], main_run.rows[i:i + 10]
    v_before = (before[-1]["arm"] - before[0]["arm"]) / (before[-1]["t"] - before[0]["t"])
    v_after = (after[-1]["arm"] - after[0]["arm"]) / (after[-1]["t"] - after[0]["t"])
    assert v_after == pytest.approx(v_before, rel=0.1), (v_before, v_after)
    step = before[-1]["arm"] - before[-2]["arm"]
    assert after[0]["arm"] - before[-1]["arm"] == pytest.approx(step, rel=0.3)


def test_forward_limit_switch_stops_forward_motion(main_run):
    rows = main_run.phase("up", "zero")
    top = [r for r in rows if r["arm"] >= UPPER_DEG - 0.5]
    assert top, max(r["arm"] for r in rows)
    late = [r for r in rows if r["t"] > top[0]["t"] + 0.1]       # a duty cycle of 0.3 is still requested
    assert late and all(r["v1"] == 0.0 and r["v2"] == 0.0 for r in late), [(r["t"], r["v1"]) for r in late[::10]]
    pressed = [p for p in main_run.prints_between(top[0]["t"] + 0.1, rows[-1]["t"])]
    assert pressed and all(p["fwd"] and p["app1"] == 0.0 for p in pressed), pressed


def test_absolute_encoder_reads_the_arm_angle_with_its_zero_offset(main_run):
    prints = main_run.prints_between(0.05, main_run.rows[-1]["t"])
    assert len(prints) > 30
    bad = mismatches(main_run, prints, "abs", lambda r: abs_reading(r["arm"]), tol=0.3, period=360.0)
    assert not bad, bad[:10]


def test_built_in_encoder_counts_the_arm_without_drifting(main_run):
    """Through a hard stop too: it counts the motor's real rotation, not an integral of sampled velocity."""
    t_zero = main_run.rows[main_run.marks["zero"]]["t"]
    prints = main_run.prints_between(0.05, t_zero)
    bad = mismatches(main_run, prints, "rel", "arm", tol=0.3)
    assert not bad, bad[:10]


def test_zeroed_encoders_count_from_zero(main_run):
    i = main_run.marks["zero"]
    arm0 = main_run.rows[i]["arm"]                                 # resting on the forward limit
    assert main_run.rows[i - 1]["arm_v"] == pytest.approx(0.0, abs=0.5)
    assert "zeroed at loop=" in main_run.console
    prints = main_run.prints_between(main_run.rows[i]["t"] + 0.05, main_run.rows[-1]["t"])
    assert prints and min(p["rel"] for p in prints) < -20.0       # it really moved down after zeroing
    bad = mismatches(main_run, prints, "rel", lambda r: r["arm"] - arm0, tol=0.3)
    assert not bad, bad[:10]
    # the follower's own encoder: its motor turns the other way, in motor rotations (no conversion factor)
    bad = mismatches(main_run, prints, "rel2", lambda r: -(r["arm"] - arm0) * 60.0 / 360.0, tol=0.05)
    assert not bad, bad[:10]


def test_lazily_created_spark_is_driven(main_run):
    assert "created SPARK MAX 3" in main_run.console
    last = main_run.phase("up")[-1]
    assert last["v3"] == pytest.approx(0.5 * last["vbus"], rel=0.02)
    assert last["shooter_v"] > 50.0                                # rad/s: the flywheel spins up


def test_follower_of_a_missing_leader_outputs_nothing_and_is_named(main_run):
    assert all(r["v4"] == 0.0 for r in main_run.rows)
    assert all(p["app4"] == 0.0 for p in main_run.prints)
    assert "SPARK MAX 4 follows CAN id 9, but the robot program has no SPARK with that id" in main_run.console


def test_student_iterate_is_neutralized(main_run):
    summary = main_run.status["agent_summary"]
    assert summary is not None and "SparkSim.iterate from frc.robot.Robot.simulationPeriodic" in summary, summary


def test_rev_runs_in_lockstep_faster_than_real_time(main_run):
    st = main_run.status
    assert st["lockstep"] is True and st["realtime_reason"] is None and st["rev_state"] is None
    assert st["foreign_writes"] == []
    assert main_run.wall < main_run.simulated, (main_run.wall, main_run.simulated)


# ------------------------------------------------------------------ an absolute encoder that starts off zero


MAGNET = 0.3             # offset_run's encoder offset: where the magnet sits (rotations) with the arm at 0


@pytest.fixture(scope="module")
def offset_run(tmp_path_factory) -> Run:
    """The arm's absolute encoder bound with offset = 0.3, so its input isn't REV's default of 0 with the arm at rest.
    Disabled for 2.2 s (past the 2 s missing-device check; SPARK MAX 3 is only created in teleop). Keeps the bridge's
    warnings in run.console's "warnings" lines."""
    cfg = probe_config(tmp_path_factory.mktemp("rev_offset"))
    cfg.write_text(cfg.read_text().replace('encoder = "SparkMaxAbs:1"',
                                           f'encoder = {{ rev = "SparkMaxAbs:1", offset = {MAGNET} }}'))
    run = Run()
    warnings: list[str] = []
    handler = logging.Handler(logging.WARNING)
    handler.emit = lambda record: warnings.append(record.getMessage())
    logger = logging.getLogger("scrimmage")

    async def main():
        s = Session(cfg, nt=False)
        logger.addHandler(handler)
        try:
            await s.start()
            await Recorder(s, run).advance(2.2)
            await finish(s, run)
            run.devices = sorted(s.bridge.hal.devices)
        finally:
            logger.removeHandler(handler)
            await s.stop()
    asyncio.run(main())
    run.warnings = warnings
    return run


def test_absolute_encoder_reads_the_arm_from_the_helpers_first_loop(offset_run):
    """The agent creates a SPARK's scrimmage helper in the robot's first loop, so values sent before it existed are
    lost: scrimmage resends them when it appears, and the robot reads the arm from its third loop on (the second
    still has the helper's start-up values), not only after the next full refresh."""
    prints = [p for p in offset_run.prints if p["loop"] >= 3]
    assert [p["loop"] for p in prints[:3]] == [3, 4, 5], offset_run.prints[:6]
    bad = mismatches(offset_run, prints, "abs", lambda r: abs_reading(r["arm"] + MAGNET * 360.0), tol=0.3,
                     period=360.0)
    assert not bad, bad[:10]


def test_a_launched_run_with_a_spark_still_to_come_blames_neither_the_agent_nor_anything_else(offset_run):
    """At the 2 s check SPARK MAX 3 doesn't exist yet (the robot creates it in teleop), and the agent's "loaded" line
    may not have come through Gradle yet: the helpers that are there show the agent drives REV, so the warning is
    about the CAN id, never that the agent isn't driving the SPARKs."""
    assert not [w for w in offset_run.warnings if "agent isn't driving" in w], offset_run.warnings
    # what the agent check relies on when no helper shows up: REVLib's own SimDevice per SPARK, beside its helper
    assert {("SimDevice", "SPARK MAX [1]"), ("ScrimmageREV", "SparkMax[1]")} <= set(offset_run.devices), offset_run.devices
    assert [w for w in offset_run.warnings if w.startswith("config expects SparkMax[3], but the robot program hasn't "
                                                             "created it")], offset_run.warnings


# ------------------------------------------------------------------ a REV Color Sensor V3 (the probe's PROBE_COLOR=1)

CARGO_R = 0.12065           # m: a 2022 cargo's radius
COLOR_PRINT = re.compile(r"^color raw=(\d+),(\d+),(\d+) ir=(\d+) rgb=([\d.]+),([\d.]+),([\d.]+) prox=(\d+)$", re.M)

# A red 2022 cargo on the carpet ahead of the robot (its near side 0.479 m ahead of the center, as in
# test_sensors_robot_e2e.py's RobotPy run), and a color sensor on the MXP I2C port 2.9 cm from it.
RED_CARGO = """
[range]
name = "One red cargo"

[piece.red_cargo]
ball = "2022-cargo"
color = "red"

[[floor_balls]]
at = [[2.6, 4.0]]
kind = "red_cargo"
"""

COLOR_SENSOR = f"""
[range]
file = "one_red.toml"

[[sensor]]
name = "eye"
kind = "color"
body = "drive.chassis"
at = [0.45, 0.0, {CARGO_R}]
toward = [1, 0, 0]
i2c = "mxp"
"""


@pytest.fixture(scope="module")
def color_run(tmp_path_factory) -> dict:
    """PROBE_COLOR=1 with a red cargo ahead of the color sensor. Advance until the robot has printed three readings."""
    tmp = tmp_path_factory.mktemp("rev_color")
    (tmp / "one_red.toml").write_text(RED_CARGO)
    cfg = probe_config(tmp, PROBE_COLOR="1")
    cfg.write_text(cfg.read_text() + COLOR_SENSOR)
    out: dict = {}

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            for _ in range(100):    # values, not wall time: 0.1 s of simulation until the prints have come through
                await s.advance(0.1)
                console = "\n".join(l["text"] for l in s.console()["lines"])
                out["prints"] = COLOR_PRINT.findall(console)
                out["console"] = console
                if len(out["prints"]) >= 3:
                    break
                await asyncio.sleep(0.05)
        finally:
            await s.stop()
    asyncio.run(main())
    return out


def test_the_java_robot_reads_the_red_cargo_on_the_color_sensor(color_run):
    prints = color_run["prints"]
    assert len(prints) >= 3, color_run["console"][-2000:]
    for red, green, blue, ir, r, g, b, prox in prints:
        red, green, blue, ir, prox = map(int, (red, green, blue, ir, prox))
        assert red > 1000 and red > 5 * blue and red > 5 * green, (red, green, blue)
        assert ir > 1000 and prox > 1000, (ir, prox)
        # getColor() is the raw color over its sum: red's (0.9, 0.1, 0.1) over 1.1
        assert (float(r), float(g), float(b)) == pytest.approx((0.9 / 1.1, 0.1 / 1.1, 0.1 / 1.1), abs=0.002)


# ------------------------------------------------------------------ an inverted SPARK


@pytest.fixture(scope="module")
def inverted_run(tmp_path_factory) -> Run:
    """PROBE_INVERT=1: SPARK 1 and its absolute encoder configured inverted. Teleop duty cycle 0.3 for 0.3 s."""
    cfg = probe_config(tmp_path_factory.mktemp("rev_inv"), PROBE_INVERT="1")
    run = Run()

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            rec = Recorder(s, run)
            await rec.advance(0.3)
            s.set_mode("teleop")
            s.set_joystick(0, axes=[0.3, 0.0])
            rec.mark("down")
            await rec.advance(0.3)
            await finish(s, run)
        finally:
            await s.stop()
    asyncio.run(main())
    return run


def test_inverted_spark_drives_its_motor_backwards_and_counts_forwards(inverted_run):
    rows = inverted_run.phase("down")
    last = rows[-1]
    assert last["v1"] == pytest.approx(-0.3 * last["vbus"], rel=0.02)   # the motor turns the other way...
    assert last["v2"] == pytest.approx(-last["v1"], rel=1e-6)           # ...and its follower with it
    assert last["arm"] - rows[0]["arm"] < -10.0 and min(r["arm_v"] for r in rows) < -100.0
    prints = inverted_run.prints_between(rows[0]["t"] + 0.05, last["t"])
    assert prints and prints[-1]["rel"] > 10.0                          # positive duty cycle, positive count
    bad = mismatches(inverted_run, prints, "rel", lambda r: -r["arm"], tol=0.3)
    assert not bad, bad[:10]
    bad = mismatches(inverted_run, prints, "abs", lambda r: abs_reading(r["arm"], inverted=True), tol=0.3,
                     period=360.0)
    assert not bad, bad[:10]


# ------------------------------------------------------------------ closed loop


async def closed_loop_run(cfg: Path, log: Path | None = None, maxmotion: bool = True) -> Run:
    """Disabled 0.5 s; position PID to 45 degrees for 2 s; then MAXMotion to -18 degrees for 3 s."""
    run = Run()
    s = Session(cfg, nt=False, log_path=log)
    try:
        await s.start()
        rec = Recorder(s, run)
        await rec.advance(0.5)
        s.set_mode("teleop")
        s.set_joystick(0, axes=[0.0, 0.5], buttons=[True, False, False])
        rec.mark("pid")
        await rec.advance(2.0)
        if maxmotion:
            s.set_joystick(0, axes=[0.0, -0.2], buttons=[False, True, False])
            rec.mark("maxmotion")
            await rec.advance(3.0)
        await finish(s, run)
    finally:
        await s.stop()
    return run


@pytest.fixture(scope="module")
def closed_runs(tmp_path_factory) -> tuple[Run, Path, Run, Path]:
    """Two identical position-PID runs, logged."""
    tmp = tmp_path_factory.mktemp("rev_closed")
    cfg = probe_config(tmp)
    out = []
    for i in range(2):
        log = tmp / f"run{i}.csv"
        out += [asyncio.run(closed_loop_run(cfg, log, maxmotion=False)), log]
    return tuple(out)


@pytest.fixture(scope="module")
def maxmotion_run(tmp_path_factory) -> Run:
    return asyncio.run(closed_loop_run(probe_config(tmp_path_factory.mktemp("rev_mm"))))


def test_position_pid_reaches_its_target(closed_runs):
    run = closed_runs[0]
    rows = run.phase("pid")
    assert rows[-1]["arm"] == pytest.approx(45.0, abs=1.5)
    assert abs(rows[-1]["arm_v"]) < 1.0


@pytest.mark.xfail(strict=True, reason="REVLib 2026.0.5's simulated MAXMotion doesn't follow its profile (here it "
                   "crawls at a few degrees per second); it does the same in REV's own sim, SparkMaxSim.iterate with "
                   "a WPILib arm model and no scrimmage. See .superpowers/sdd/2026-10-14-rev-support/task-3-report.md")
def test_maxmotion_reaches_its_target(maxmotion_run):
    rows = maxmotion_run.phase("maxmotion")
    assert rows[-1]["arm"] == pytest.approx(-18.0, abs=2.0)


def test_maxmotion_moves_towards_its_target(maxmotion_run):
    rows = maxmotion_run.phase("maxmotion")
    assert rows[-1]["arm"] < rows[0]["arm"] - 5.0


def test_two_runs_give_identical_logs(closed_runs):
    """Position PID runs are bit-identical. (REVLib 2026.0.5's simulated MAXMotion isn't: two runs part a few loops
    after a MAXMotion move starts, with identical inputs, so MAXMotion isn't in these runs.)"""
    a = list(csv.DictReader(open(closed_runs[1])))
    b = list(csv.DictReader(open(closed_runs[3])))
    assert len(a) > 100 and a == b


@pytest.fixture(scope="module")
def abs_run(tmp_path_factory) -> Run:
    """PROBE_FEEDBACK=abs: position PID on the absolute encoder to a reading of 9 degrees (the arm at 45, with the
    probe's zero offset of 36 degrees)."""
    cfg = probe_config(tmp_path_factory.mktemp("rev_abs"), PROBE_FEEDBACK="abs")
    run = Run()

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            rec = Recorder(s, run)
            await rec.advance(0.5)
            s.set_mode("teleop")
            s.set_joystick(0, axes=[0.0, 0.1], buttons=[True, False, False])
            rec.mark("pid")
            await rec.advance(2.0)
            await finish(s, run)
        finally:
            await s.stop()
    asyncio.run(main())
    return run


def test_position_pid_on_the_absolute_encoder_reaches_its_target(abs_run):
    rows = abs_run.phase("pid")
    assert rows[-1]["arm"] == pytest.approx(45.0, abs=1.5)
    assert abs(rows[-1]["arm_v"]) < 1.0


def test_built_in_encoder_still_counts_with_the_loop_on_the_absolute_encoder(abs_run):
    """REV's iterate only updates the selected feedback sensor; a real SPARK's built-in encoder always counts."""
    prints = abs_run.prints_between(0.05, abs_run.rows[-1]["t"])
    assert prints and max(p["rel"] for p in prints) > 30.0
    bad = mismatches(abs_run, prints, "rel", "arm", tol=0.3)
    assert not bad, bad[:10]
    bad = mismatches(abs_run, prints, "rel2", lambda r: -r["arm"] * 60.0 / 360.0, tol=0.05)
    assert not bad, bad[:10]
