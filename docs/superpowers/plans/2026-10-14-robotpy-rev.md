# RobotPy REV Support (sub-project 6b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** RobotPy robots using `robotpy-rev` SPARK MAX / SPARK Flex run under Scrimmage with no robot-code changes,
in lockstep and faster than real time, driven by REV's own simulated controller.

**Architecture:** A Python port of the Java `ScrimmageRev` helper lives in the `scrimmage-lockstep` package
(`scrimmage_lockstep/rev.py`), installed by its `loadExtension()` (already loaded by `--scrimmage-lockstep`). It wraps the
SPARK constructors, and every robot loop drives `robotpy-rev`'s `SparkMaxSim`/`SparkFlexSim` through the existing
`ScrimmageREV:SparkMax[<id>]` helper SimDevice protocol, so Scrimmage's bindings are unchanged; Scrimmage learns the
helper's console lines, RobotPy wording, and a lockstep-package version check.

**Tech Stack:** Python 3.11+, robotpy 2026.2 (`hal`, `wpilib`, `wpimath`), `robotpy-rev` 2026.0.4 (RobotPy pieces
Apache-2.0, REV pieces BSD-3-Clause), the `scrimmage-lockstep` package (scikit-build, `lockstep/`), pytest.

**Spec:** docs/superpowers/specs/2026-10-14-robotpy-rev-design.md

## Global Constraints

- Robot code is never edited to run under Scrimmage (the example is copied as published, license notices kept).
- Protocol identical to the Java helper's (scrimmage/java/agent/src/scrimmage/agent/ScrimmageRev.java is the reference):
  helper SimDevice `ScrimmageREV:SparkMax[<id>]` / `ScrimmageREV:SparkFlex[<id>]`; inputs `velocity` (motor RPM, physical
  frame), `position` (motor rotations since start), `vbus`, `current`, `absRotations`, `fwdLimit`, `revLimit`, `motor`
  (code: 1 NEO, 2 NEO550, 3 NeoVortex, 0 default); output `motorVoltage`. Same unit, sign, zero-offset, follower,
  position-preset/`setPosition`, rebuild-only-on-motor-change, `dt` (`wpilib.Timer.getFPGATimestamp()` difference;
  first loop 0.02; `dt <= 0` skips `iterate`) and vbus floor (0.1) rules as the Java helper.
- `install()` is a no-op when `rev` isn't importable; nothing it does may raise into the robot program (log once and
  stop the helper instead).
- Console lines exactly: `scrimmage rev helper: loaded (python <scrimmage-lockstep version>)`; `Scrimmage rev
  helper: REV sim unavailable: <reason>`; `Scrimmage rev helper: ignored N robot-side sim writes (<top 3 with first
  caller>)`.
- No noise seeding; RobotPy REV runs are close, not identical (spec option A).
- `scrimmage-lockstep` version 0.2.0; Scrimmage requires >= 0.2.0 only when a RobotPy config binds REV devices;
  message names `pip install ./lockstep`.
- `robotpy-rev>=2026.0.4,<2027` in the `robotpy` and `dev` extras.
- Python `.venv/bin/python`; tests `SCRIMMAGE_SKIP_JAVA=1 .venv/bin/python -m pytest`; reinstall the lockstep package
  into the venvs after changing it (`.venv/bin/pip install ./lockstep`, and the scratchpad venv311/venv313 the
  controller uses). Before pushing, run the suite on Python 3.11 and 3.13.

## Review Focus

1. A robot subclassing `SparkMax` (`class MySpark(rev.SparkMax)` calling `super().__init__`) and code doing `from rev
   import SparkMax` must both register — test both with fake classes. (Task 1.)
2. pybind11 constructor overloads (`SparkMax(id, MotorType)`, keyword args) must pass through the wrapper unchanged —
   test positional and keyword construction. (Task 1.)
3. An exception inside the per-loop helper (one bad SPARK) must not raise into the robot loop and must not stop the
   other SPARKs — test with a fake SPARK whose sim throws. (Task 1.)
4. A RobotPy robot without REV (e.g. examples/robotpy_kitbot) must behave exactly as before, and a config binding no
   REV devices must not trigger the lockstep version check — test. (Tasks 1-2.)
5. A SPARK constructed at import time of robot.py, before `loadExtension()` ran, can't be registered — the helper must
   say so plainly rather than silently leave it undriven: the existing "created but not driven" bridge path covers it
   (REV's own device without a helper); test that the warning wording fits RobotPy. (Task 2.)

---

### Task 1: The RobotPy REV helper (`scrimmage_lockstep/rev.py`)

**Files:** Create `lockstep/scrimmage_lockstep/rev.py`, `tests/test_rev_py_helper.py`; Modify
`lockstep/scrimmage_lockstep/__init__.py` (`loadExtension()` calls `rev.install()` in a try/except that logs), 
`lockstep/pyproject.toml` (version 0.2.0)

**Interfaces:**
- `rev.install() -> None`; `rev.HELPER_PREFIX = "scrimmage rev helper: "`; module-level registry and the
  per-loop callback; `rev.loop_once(now: float) -> None` (the callback body, testable without HAL timing).
- Uses `robotpy-rev` public API only: `SparkMaxSim(spark, DCMotor)`/`SparkFlexSim`, `iterate`, `setPosition`,
  `setMotorCurrent`, `setAppliedOutput`, `getAppliedOutput`, `getAbsoluteEncoderSim`, `getForward/ReverseLimitSwitchSim`,
  `getRelativeEncoderSim`, `configAccessor.*` (inverted, follower leader/inverted, encoder/absoluteEncoder conversion
  factors and inversion/zero offset, closedLoop feedback sensor), `getDeviceId`; `hal.SimDevice` for the helper device;
  `hal.simulation.registerSimPeriodicBeforeCallback`; `wpimath.system.plant.DCMotor` (NEO, NEO550, neoVortex).
- `SparkMaxSim.iterate`/`SparkFlexSim.iterate` replaced by a guard (ignore + count unless the helper's own context flag
  is set, or the helper is unavailable).

- [ ] Steps: failing unit tests with fake `rev`/`hal`/`wpimath` modules injected into `sys.modules` (wrapping incl.
  `from rev import SparkMax`, subclassing, positional/keyword construction; loop math: velocity conversion for
  primary and absolute feedback, absolute position with offset+inversion, inverted SPARK sign, follower mirroring
  (inverted leader + follow inverted), position preset and a robot setPosition preserved, dt<=0 skip, vbus floor,
  rebuild only when the motor code changes the DCMotor; iterate guard + summary line; a throwing SPARK is dropped and
  others continue; install() no-op without rev; console lines exact); run (FAIL); implement by porting
  ScrimmageRev.java; run (PASS); reinstall ./lockstep into .venv; full suite; commit `"RobotPy REV helper in
  scrimmage-lockstep"`.

---

### Task 2: Scrimmage side for RobotPy REV

**Files:** Modify `scrimmage/robot_process.py` (parse `scrimmage rev helper:` lines into the same `agent_status` /
`agent_summary` / `rev_state` fields as the Java agent's), `scrimmage/bridge.py` (`rev_agent_reason`,
`rev_unavailable_message` worded per language: RobotPy → "add `--scrimmage-lockstep` to `[robot_program] command`, or
update scrimmage-lockstep (`pip install ./lockstep`)" and "update robotpy-rev (`pip install -U robotpy-rev`)"),
`scrimmage/session.py` (version check at start: RobotPy config binding REV devices and `scrimmage-lockstep` < 0.2.0
or missing → SessionError naming `pip install ./lockstep`), `pyproject.toml` (extras); Test `tests/test_robot_process.py`,
`tests/test_bridge.py`, `tests/test_session.py`

**Interfaces:** `bridge.robot_language(command: list[str]) -> "python" | "java" | None` (python when the command runs
`robotpy`, java when it runs gradle/gradlew); the bridge/session pass it to the wording helpers.

- [ ] Steps: failing tests (helper lines parsed; RobotPy wording for agent-missing and REV-unavailable; Java wording
  unchanged; version check refuses 0.1.0/missing only for RobotPy+REV configs, not for kitbot or Java); implement; run;
  full suite; commit `"scrimmage: RobotPy REV helper lines, wording and version check"`.

---

### Task 3: RobotPy REV probe robot and end-to-end tests

**Files:** Create `tests/rev_probe_py/robot.py` (mirrors tests/rev_probe's Java Robot: SPARK MAX 1 on an arm (NEO),
SPARK 2 following 1 inverted, absolute encoder on SPARK 1 with a zero offset, forward limit enabled, a SPARK created
lazily in teleopInit, a student-style simulationPeriodic calling its own SparkMaxSim.iterate, modes selected by the same
env flag mechanism as the Java probe), `tests/rev_probe_py.toml` (like tests/rev_probe.toml with a RobotPy
`[robot_program]` using `--scrimmage-lockstep`), `tests/test_rev_py_e2e.py`

- [ ] Steps: failing e2e tests (skip only when robotpy-rev or scrimmage_lockstep isn't importable, like test_e2e.py; CI's
  test jobs must run them): duty cycle moves the arm the right way (and the inverted variant); position PID reaches its
  target; absolute encoder reads the joint angle with the offset; forward limit stops forward motion; inverted follower
  mirrors; disabled holds 0 V; lazily created SPARK driven; faster than 1x; student iterate ignored (summary line); no
  repeatability assertion (comment: REV's C++ noise is unseedable from Python). Fix helper issues found (in Task 1's
  module). Commit `"RobotPy REV probe robot and end-to-end tests"`.

---

### Task 4: RobotPy MAXSwerve example, scenario, CI and docs

**Files:** Create `examples/robotpy_maxswerve/` (robotpy-rev's `examples/maxswerve`, Apache-2.0/BSD notices kept: copy
the repo's LICENSE text alongside), `configs/maxswerve_py.toml` (the `swerve-rev` parts + RobotPy `[robot_program]`
with `--scrimmage-lockstep`), `scenarios/maxswerve_py_auto.toml` (pose check, tolerance allowing for noise and the example's
own behavior — measure the robot's odometry vs truth as the Java task did and record it in a comment); Modify
`tests/test_e2e.py` (scenario test), `.github/workflows/ci.yml` (if anything beyond the dev extra is needed),
`docs/rev.md` ("RobotPy" section: setup with robotpy-rev and `--scrimmage-lockstep`, reinstalling ./lockstep, why runs
are close but not identical, the example), `README.md` (REV section mentions RobotPy)

- [ ] Steps: failing scenario test; copy example; config + scenario; pass; full suite on .venv, 3.11, 3.13; commit
  `"RobotPy MAXSwerve example, scenario and docs"`.
