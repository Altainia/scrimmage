"""REV SPARK MAX RobotPy robots under scrimmage: tests/rev_probe_py (real RobotPy, real robotpy-rev) on
tests/rev_probe_py.toml, through scrimmage's scenario runner. The RobotPy twin of tests/test_rev_e2e.py.

The arm: SPARK MAX 1 (NEO, 60:1, absolute encoder on its data port, forward limit switch at the top of the range) and
SPARK MAX 2 following it inverted, on a motor that faces the other way across the gearbox. The flywheel: SPARK MAX 3
(the robot creates it in teleopInit) and SPARK MAX 4 (follows CAN id 9, which the robot never creates).

REVLib's simulated sensor noise comes from its C++ library, seeded from std::random_device, which robotpy-rev gives no
way to seed; scrimmage preloads scrimmage-lockstep's seed library to pin it, so two runs are identical (checked
below, as test_rev_e2e.py does for Java).

Each scenario is run once and its checks share the recording."""

import asyncio
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

pytest.importorskip("rev")
pytest.importorskip("scrimmage_lockstep")

from scrimmage.scenario import load_scenario_text  # noqa: E402
from scrimmage.scenario_runner import Report, run_scenario  # noqa: E402

TESTS = Path(__file__).resolve().parent
PROBE = TESTS / "rev_probe_py"

ZERO_OFFSET = 0.1        # the probe's absoluteEncoder.zeroOffset (rotations)
UPPER_DEG = 110.0        # the arm's range in rev_probe_py.toml: the forward limit switch is pressed here
REV_LINE = re.compile(r"^\[\s*([\d.]+)\] rev loop=(\d+) (.*)$")  # a robot.log line: [scrimmage t] text


def probe_config(tmp: Path, **env) -> Path:
    """tests/rev_probe_py.toml, run from `tmp` with extra [robot_program] env."""
    text = (TESTS / "rev_probe_py.toml").read_text().replace('cwd = "rev_probe_py"', f'cwd = "{PROBE}"')
    if env:
        table = ", ".join(f'{k} = "{v}"' for k, v in env.items())
        text = text.replace(f'cwd = "{PROBE}"\n', f'cwd = "{PROBE}"\nenv = {{ {table} }}\n')
    p = tmp / "rev_probe_py.toml"
    p.write_text(text)
    return p


def step(name: str, mode: str, duration: float, axes=(0.0, 0.0), buttons=(False, False, False)) -> str:
    fmt = lambda xs: ", ".join(str(x).lower() for x in xs)  # noqa: E731
    return (f'[[step]]\nname = "{name}"\nmode = "{mode}"\nduration = {duration}\n'
            f"axes = {{ 0 = [{fmt(axes)}] }}\nbuttons = {{ 0 = [{fmt(buttons)}] }}\n")


# ------------------------------------------------------------------ recording


@dataclass
class Run:
    """What one probe scenario did: a row per lockstep step (scrimmage's side), the robot's prints and the report."""
    rows: list[dict] = field(default_factory=list)
    prints: list[dict] = field(default_factory=list)
    starts: dict[str, float] = field(default_factory=dict)  # step name -> its start time
    console: str = ""
    report: Report | None = None
    wall: float = 0.0       # wall seconds from the first robot step to the last
    simulated: float = 0.0  # simulated seconds over the same steps

    def phase(self, name: str, until: str | None = None) -> list[dict]:
        t0, t1 = self.starts[name], self.starts[until] if until else math.inf
        return [r for r in self.rows if t0 < r["t"] <= t1]

    def prints_between(self, t0: float, t1: float) -> list[dict]:
        return [p for p in self.prints if t0 <= p["t"] < t1]


def parse_prints(log: str) -> list[dict]:
    """The robot's "rev ..." prints from robot.log, each with its robot-clock t and the scrimmage time it was logged at."""
    out = []
    for line in log.splitlines():
        m = REV_LINE.match(line)
        if m:
            row = {"loop": int(m.group(2)), "logged": float(m.group(1))}
            for item in m.group(3).split():
                k, _, v = item.partition("=")
                row[k] = v == "true" if v in ("true", "false") else float(v)
            out.append(row)
    return out


def run_probe(tmp: Path, steps: list[str], **env) -> Run:
    """Run the probe through `steps` (scenario [[step]] tables) and record it."""
    cfg = probe_config(tmp, **env)
    scenario = load_scenario_text(f'config = "{cfg}"\n' + "".join(steps), tmp / "probe_scenario.toml")
    run = Run()
    clock: list[float] = []

    def watch(s, t: float) -> None:
        if not clock:
            clock[:] = [time.monotonic(), t]
        joints = s.world.joint_states()
        hal = s.bridge.hal.devices
        volts = {i: float(hal.get(("ScrimmageREV", f"SparkMax[{i}]"), {}).get("<motorVoltage", 0.0) or 0.0)
                 for i in (1, 2, 3, 4)}
        run.rows.append({"t": t, "arm": math.degrees(joints["arm.pivot"][0]),
                         "arm_v": math.degrees(joints["arm.pivot"][1]), "shooter_v": joints["shooter.spin"][1],
                         "vbus": s.world.vbatt, **{f"v{i}": v for i, v in volts.items()}})
        run.wall, run.simulated = time.monotonic() - clock[0], t - clock[1]

    out = tmp / "out"
    run.report = asyncio.run(run_scenario(scenario, out, watch=watch))
    run.starts = {st.name: st.start_t for st in run.report.steps}
    run.console = (out / "robot.log").read_text()
    run.prints = parse_prints(run.console)
    return run


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


# ------------------------------------------------------------------ the main run


@pytest.fixture(scope="module")
def main_run(tmp_path_factory) -> Run:
    """Defaults (SPARK 1 not inverted, closed loop on the built-in encoder). Disabled with a duty cycle held for 1 s;
    teleop duty cycle 0.3 for 2 s (the arm reaches the forward limit); zero the encoders at the top; then duty cycle
    -0.3 for 0.5 s and 0 for 0.3 s."""
    return run_probe(tmp_path_factory.mktemp("rev_py_main"), [
        step("disabled", "disabled", 1.0, axes=(0.3, 0.0)),
        step("up", "teleop", 2.0, axes=(0.3, 0.0)),
        step("zero", "teleop", 0.02, axes=(-0.3, 0.0), buttons=(False, False, True)),
        step("down", "teleop", 0.5, axes=(-0.3, 0.0)),
        step("stop", "teleop", 0.3),
    ])


def test_the_scenario_passes_with_rev_available(main_run):
    rep = main_run.report
    assert rep.error is None and rep.passed, rep.to_dict()
    assert any(c.check == "REV simulation available" and c.passed for c in rep.implicit), rep.to_dict()
    assert "scrimmage rev helper: loaded (python " in main_run.console
    assert "cfg arm REVLibError.kOk" in main_run.console
    assert "REV helper failed" not in main_run.console and "no longer simulated" not in main_run.console


def test_robot_clock_matches_scrimmage_time(main_run):
    """The lockstep extension restarts the robot's clock when it loads, so a RobotPy robot's FPGA time is scrimmage's
    time (RobotPy loads extensions after importing robot.py, which used to leave its clock ~0.13 s ahead)."""
    assert main_run.prints
    assert all(abs(p["logged"] - p["t"]) < 1e-3 for p in main_run.prints), [(p["logged"], p["t"]) for p in main_run.prints[:5]]


def test_disabled_robot_outputs_nothing_with_a_duty_cycle_held(main_run):
    rows = main_run.phase("disabled", "up")
    assert all(r["v1"] == 0.0 and r["v2"] == 0.0 for r in rows), [(r["t"], r["v1"], r["v2"]) for r in rows[::20]]
    assert max(r["arm"] for r in rows) < 0.05                   # the arm only sags under gravity (brake mode)
    prints = main_run.prints_between(0.05, main_run.starts["up"])
    assert prints and all(p["app1"] == 0.0 for p in prints)


def test_duty_cycle_raises_the_arm_and_the_inverted_follower_helps(main_run):
    t_up = main_run.starts["up"]
    rows = [r for r in main_run.phase("up") if r["t"] <= t_up + 0.4]   # before the arm reaches the top
    last = rows[-1]
    assert last["v1"] == pytest.approx(0.3 * last["vbus"], rel=0.02)
    assert last["v2"] == pytest.approx(-last["v1"], rel=1e-6)    # follow(arm, True): the opposite voltage...
    assert last["arm_v"] > 100.0                                 # ...on a motor facing the other way: both lift
    assert last["arm"] - rows[0]["arm"] > 20.0
    moving = main_run.prints_between(rows[5]["t"], last["t"])
    assert moving and all(p["app2"] == pytest.approx(-p["app1"]) and p["app1"] == pytest.approx(0.3) for p in moving)
    # the follower's getOutputCurrent() is its own motor's current, which matches the leader's on a shared gearbox
    assert all(p["cur1"] > 0.1 and p["cur2"] == pytest.approx(p["cur1"], rel=0.05) for p in moving), moving


def test_forward_limit_switch_stops_forward_motion(main_run):
    rows = main_run.phase("up", "zero")
    top = [r for r in rows if r["arm"] >= UPPER_DEG - 0.5]
    assert top, max(r["arm"] for r in rows)
    late = [r for r in rows if r["t"] > top[0]["t"] + 0.1]       # a duty cycle of 0.3 is still requested
    assert late and all(r["v1"] == 0.0 and r["v2"] == 0.0 for r in late), [(r["t"], r["v1"]) for r in late[::10]]
    pressed = main_run.prints_between(top[0]["t"] + 0.1, rows[-1]["t"])
    assert pressed and all(p["fwd"] and p["app1"] == 0.0 for p in pressed), pressed


def test_absolute_encoder_reads_the_arm_angle_with_its_zero_offset(main_run):
    prints = main_run.prints_between(0.05, main_run.rows[-1]["t"])
    assert len(prints) > 30
    bad = mismatches(main_run, prints, "abs", lambda r: abs_reading(r["arm"]), tol=0.3, period=360.0)
    assert not bad, bad[:10]


def test_built_in_encoder_counts_the_arm_without_drifting(main_run):
    """Through a hard stop too: it counts the motor's real rotation, not an integral of sampled velocity."""
    prints = main_run.prints_between(0.05, main_run.starts["zero"])
    bad = mismatches(main_run, prints, "rel", "arm", tol=0.3)
    assert not bad, bad[:10]


def test_zeroed_encoders_count_from_zero(main_run):
    t_zero = main_run.starts["zero"]
    at_zero = [r for r in main_run.rows if r["t"] <= t_zero]
    arm0 = at_zero[-1]["arm"]                                     # resting on the forward limit
    assert at_zero[-1]["arm_v"] == pytest.approx(0.0, abs=0.5)
    assert "zeroed at loop=" in main_run.console
    prints = main_run.prints_between(t_zero + 0.05, main_run.rows[-1]["t"])
    assert prints and min(p["rel"] for p in prints) < -20.0       # it really moved down after zeroing
    bad = mismatches(main_run, prints, "rel", lambda r: r["arm"] - arm0, tol=0.3)
    assert not bad, bad[:10]
    # the follower's own encoder: its motor turns the other way, in motor rotations (no conversion factor)
    bad = mismatches(main_run, prints, "rel2", lambda r: -(r["arm"] - arm0) * 60.0 / 360.0, tol=0.05)
    assert not bad, bad[:10]


def test_lazily_created_spark_is_driven(main_run):
    assert "created SPARK MAX 3" in main_run.console
    last = main_run.phase("up", "zero")[-1]
    assert last["v3"] == pytest.approx(0.5 * last["vbus"], rel=0.02)
    assert last["shooter_v"] > 50.0                                # rad/s: the flywheel spins up


def test_follower_of_a_missing_leader_outputs_nothing_and_is_named(main_run):
    assert all(r["v4"] == 0.0 for r in main_run.rows)
    assert all(p["app4"] == 0.0 for p in main_run.prints)
    assert "SPARK MAX 4 follows CAN id 9, but the robot program has no SPARK with that id" in main_run.console


def test_student_iterate_is_neutralized(main_run):
    assert re.search(r"scrimmage rev helper: ignored \d+ robot-side sim writes \(SparkSim\.iterate from "
                     r"\S*MyRobot\._simulationPeriodic\)", main_run.console), main_run.console[-3000:]
    notes = [n for n in main_run.report.notes if "neutralized" in n]
    assert len(notes) == 1 and re.fullmatch(
        r"the scrimmage rev helper neutralized \d+ robot-side sim writes \(SparkSim\.iterate from "
        r"\S*MyRobot\._simulationPeriodic\)", notes[0]), main_run.report.notes


def test_rev_runs_in_lockstep_faster_than_real_time(main_run):
    rep = main_run.report
    assert rep.lockstep is True and rep.effective_speed is None
    assert any(c.check == "no foreign sim writes" and c.passed for c in rep.implicit), rep.to_dict()
    assert main_run.simulated > 3.5
    assert main_run.wall < main_run.simulated, (main_run.wall, main_run.simulated)


# ------------------------------------------------------------------ an inverted SPARK


@pytest.fixture(scope="module")
def inverted_run(tmp_path_factory) -> Run:
    """PROBE_INVERT=1: SPARK 1 and its absolute encoder configured inverted. Teleop duty cycle 0.3 for 0.3 s."""
    return run_probe(tmp_path_factory.mktemp("rev_py_inv"), [
        step("disabled", "disabled", 0.3),
        step("down", "teleop", 0.3, axes=(0.3, 0.0)),
    ], PROBE_INVERT="1")


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


@pytest.fixture(scope="module")
def pid_run(tmp_path_factory) -> Run:
    """Disabled 0.5 s; position PID on the built-in encoder to 45 degrees for 2 s."""
    return run_probe(tmp_path_factory.mktemp("rev_py_pid"), [
        step("disabled", "disabled", 0.5),
        step("pid", "teleop", 2.0, axes=(0.0, 0.5), buttons=(True, False, False)),
    ])


def test_position_pid_reaches_its_target(pid_run):
    rows = pid_run.phase("pid")
    assert rows[-1]["arm"] == pytest.approx(45.0, abs=1.5)
    assert abs(rows[-1]["arm_v"]) < 1.0


@pytest.fixture(scope="module")
def velocity_runs(tmp_path_factory) -> tuple[Run, Run]:
    """Two identical runs with the flywheel on a velocity PID (PROBE_SHOOTER=velocity): REV's controller acts on its
    noisy simulated velocity, so the noise reaches the physics. Its SPARK is created mid-run (teleopInit), which
    also checks that a device created mid-step reaches scrimmage in the same step every run (the extension's
    sim_values count)."""
    steps = [step("disabled", "disabled", 0.3), step("spin", "teleop", 1.5)]
    return tuple(run_probe(tmp_path_factory.mktemp(f"rev_py_velocity{i}"), steps, PROBE_SHOOTER="velocity")
                 for i in range(2))


def test_two_runs_give_identical_physics(velocity_runs):
    """REV's sensor noise repeats (scrimmage preloads the seed library) and the robot's clock starts at exactly 0,
    so velocity control gives bit-identical physics, and the robot reads bit-identical values."""
    a, b = velocity_runs
    spin = a.phase("spin")
    assert spin[-1]["shooter_v"] > 100.0, spin[-1]          # the flywheel is spinning under velocity control
    assert len(a.rows) > 100 and a.rows == b.rows
    assert len(a.prints) > 10 and a.prints == b.prints


@pytest.fixture(scope="module")
def abs_run(tmp_path_factory) -> Run:
    """PROBE_FEEDBACK=abs: position PID on the absolute encoder to a reading of 9 degrees (the arm at 45, with the
    probe's zero offset of 36 degrees)."""
    return run_probe(tmp_path_factory.mktemp("rev_py_abs"), [
        step("disabled", "disabled", 0.5),
        step("pid", "teleop", 2.0, axes=(0.0, 0.1), buttons=(True, False, False)),
    ], PROBE_FEEDBACK="abs")


def test_position_pid_on_the_absolute_encoder_reaches_its_target(abs_run):
    rows = abs_run.phase("pid")
    assert rows[-1]["arm"] == pytest.approx(45.0, abs=1.5)
    assert abs(rows[-1]["arm_v"]) < 1.0
