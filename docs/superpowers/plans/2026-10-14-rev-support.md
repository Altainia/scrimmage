# REV Hardware Support (sub-project 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Java robots with REV SPARK MAX / SPARK Flex controllers run under Scrimmage with no robot-code changes,
in lockstep (faster than real time, repeatable), with REV's own simulated controller driving the physics.

**Architecture:** The Java agent hooks REV's `SparkBase` constructor to register every SPARK, then each robot loop
(WPILib sim-periodic-before callback) drives REV's public `SparkSim.iterate` with Scrimmage's physics values, passed
through a helper SimDevice per SPARK; Scrimmage's new `rev` motor/encoder bindings read the helper's motor voltage
and write velocity, current, absolute-encoder rotations and limit states back. A MAXSwerve stock part, a `swerve-rev`
template, a REV probe robot and REV's MAXSwerve template as an example robot cover it end to end.

**Tech Stack:** Python 3.11+, MuJoCo, HALSim WebSocket; Java 17 agent (ASM 9, reflection only — no REVLib/WPILib
compile dependency); REVLib 2026.0.5 (BSD-3-Clause Java sources), GradleRIO 2026.1.1; pytest (`java` marker for Java
tests).

**Spec:** docs/superpowers/specs/2026-10-14-rev-support-design.md

## Global Constraints

- Robot code is never edited to work under Scrimmage (examples are copied as published, plus a scenario-friendly auto
  routine only where the example has none).
- REVLib is used only through its public Java API, by reflection; the agent has no compile-time REVLib or WPILib
  dependency. Robots without REV pay nothing (the helper starts on the first SPARK registration).
- Helper SimDevice per SPARK: name `ScrimmageREV:SparkMax[<id>]` or `ScrimmageREV:SparkFlex[<id>]` (HALSim type `ScrimmageREV`,
  device `SparkMax[<id>]`/`SparkFlex[<id>]`). Inputs (written by Scrimmage, HALSim `>` prefix): `velocity` (motor
  shaft, rotations per minute, physical motor frame), `vbus` (V), `current` (A), `position` (motor rotations since start, physical motor frame; sets the built-in encoder — Ruling R10),
  `absRotations` (absolute-encoder magnet rotations, unwrapped), `fwdLimit`, `revLimit` (bool), `motor` (motor model code, a number — WPILib HALSim WS cannot carry strings into SimDevice values: 1 NEO, 2 NEO550, 3 NeoVortex, 0 keep the SPARK model default; Ruling R5). Output (read by
  Scrimmage, `<` prefix): `motorVoltage` (V across the physical motor, after the SPARK's inversion and follower
  mirroring). (Plan refinement of the spec's `scrimmage:SPARK [<id>]`: HALSim splits "Type:Name", and Scrimmage needs
  one uniform output that already includes inversion and following.)
- `iterate(velocity, vbus, dt)` takes velocity in the selected feedback sensor's units after conversion (REVLib
  Javadoc): helper passes motorRPM × encoder velocityConversionFactor, or (closed loop on `kAbsoluteEncoder`)
  absolute-encoder RPM × absoluteEncoder velocityConversionFactor; sign flipped when the SPARK is configured
  inverted (`configAccessor.getInverted()`).
- `SparkAbsoluteEncoderSim.setPosition` takes the position after conversion, computed from `absRotations` with the
  inversion and zero offset as REVLib's `AbsoluteEncoderConfig.zeroOffset` javadoc defines (Ruling R11).
- `dt` = sim-time difference between robot loops (`Timer.getFPGATimestamp()`); first loop 0.02; a loop with `dt <= 0`
  (paused) skips `iterate`. `vbus` passed to REV is `max(vbus, 0.1)`.
- REV's `NoiseGenerator` random source is seeded with 0 at class load. `SparkSim.iterate` called from anywhere but
  the helper is a no-op, counted in the agent summary as `SparkSim.iterate`.
- Short forms: `SparkMax:<id>`, `SparkFlex:<id>` (motor slots), `SparkMaxAbs:<id>`, `SparkFlexAbs:<id>` (encoder
  slots). Table/binding key `rev = "<Kind>:<id>"`. `neutral` default "brake" for REV. Motor default NEO (MAX),
  NeoVortex (Flex).
- REVLib floor: the helper checks for the classes/methods it uses; missing ones print `Scrimmage agent: REV sim
  unavailable: <reason>`, which Scrimmage reports naming REVLib 2026.0.5 or later.
- REV devices never set the Phoenix real-time flag; the 1x notice names Phoenix devices only.
- Python `.venv/bin/python`; tests `SCRIMMAGE_SKIP_JAVA=1 .venv/bin/python -m pytest` (Java tests: drop
  `SCRIMMAGE_SKIP_JAVA`, they need the JDK found by `scrimmage/java_tools.py`). Before pushing, run the suite on Python
  3.11 and 3.13 too (see memory note ci-vs-local-differences: uv venvs in the scratchpad).

## Review Focus

1. A SPARK configured inverted (`.inverted(true)`) must still drive its joint the right way and read a positive
   encoder for the motor's positive direction — the probe test inverts one motor and checks direction. (Task 3.)
2. Pausing (Drive tab) or stepping with a robot loop of `dt = 0` must not divide by zero or jump the controller —
   helper skips `iterate` when `dt <= 0`; test pauses then resumes the probe and checks no jump. (Task 3.)
3. A disabled robot must output 0 V even with a setpoint held (REV gates on `DriverStation.isEnabled()`): probe test
   holds a duty cycle while disabled and checks the joint doesn't move. (Task 3.)
4. Two SPARKs with the same CAN ID, or a SparkMax and a TalonFX sharing an ID, must not cross wires: the REV helper
   device name carries the model and ID, distinct from Phoenix's names; a config binding the same REV device twice is a
   config error (unit test in Task 1).
5. A SPARK created before the robot's first loop (static field, constructor of RobotContainer) and one created later
   (lazy subsystem) must both be registered and driven: probe creates one of each. (Task 3.)

---

### Task 1: Python REV bindings and short forms

**Files:** Modify `scrimmage/bindings.py`, `scrimmage/parts/ids.py`, `scrimmage/config.py` (duplicate-device
check if it lives there); Test `tests/test_rev_bindings.py` (new), `tests/test_part_ids.py`

**Interfaces:**
- `bindings.REV_DEVICES = {"SparkMax": "SparkMax", "SparkFlex": "SparkFlex"}`; `_rev_device(cfg, kind) -> str` returns
  `"SparkMax[<id>]"` / `"SparkFlex[<id>]"` (motor kinds) or the same for encoder kinds `SparkMaxAbs` → `SparkMax[<id>]`
  (the absolute encoder lives on that SPARK's helper device); bad kind → `ValueError` naming the allowed kinds.
- `MotorBinding(kind="REV")`: `device_key = ("ScrimmageREV", "SparkMax[3]")`; `apply`: volts = `<motorVoltage` × sign
  (0 when disabled), torque as for other kinds; `neutral` default "brake"; motor model default NEO (MAX) / NeoVortex
  (Flex) when the config gives none; `updates(data)` returns `("ScrimmageREV", device, {">velocity": rpm, ">current": A,
  ">vbus": V, ">motor": name, (">fwdLimit", ">revLimit" when limits=True)})` where rpm = joint velocity ×
  gear_ratio × sign × 60 / 2π. `limits = true`: pressed when the joint position is at/beyond its upper (fwd) / lower
  (rev) range limit (joint must have a range; else `ValueError`).
- `EncoderBinding(kind="REV")`: `device_key = ("ScrimmageREV", "SparkMax[3]")`; updates `{">absRotations": joint
  revolutions × ratio × sign}`.
- `ids.SHORT` gains `"SparkMax": ("motor", "rev")`, `"SparkFlex": ("motor", "rev")`, `"SparkMaxAbs": ("encoder",
  "rev")`, `"SparkFlexAbs": ("encoder", "rev")`, keeping the full `"<Kind>:<id>"` string as the value (like ctre).
- Config error when two motor bindings name the same REV device, or two encoder bindings do.

- [ ] Steps: failing tests (short forms → binding dicts; table form with neutral/invert/limits; REV motor volts from a
  fake HalState with sign and disabled; velocity rpm/current/vbus/motor written; limits pressed at range ends; encoder
  absRotations with ratio/sign; bad kind messages; duplicate device error); run (FAIL); implement; run (PASS); full
  suite; commit `"REV bindings: rev = SparkMax/SparkFlex motors and absolute encoders"`.

---

### Task 2: Java agent REV helper

**Files:** Create `scrimmage/java/agent/src/scrimmage/agent/ScrimmageRev.java`; Modify
`scrimmage/java/agent/src/scrimmage/agent/ScrimmageAgent.java` (transformer: SparkBase hook, SparkSim.iterate no-op,
NoiseGenerator seed), `scrimmage/java_tools.py` (compile the new file; bump the agent version so stale jars rebuild),
`scrimmage/robot_process.py` (parse `REV sim unavailable` like agent_state), `scrimmage/bridge.py` (REV device names
in the device checks); Test `tests/test_java_agent.py` (agent builds; transformer unit-level checks with a fake class if
practical), `tests/test_robot_process.py`

**Interfaces:**
- `ScrimmageRev.register(Object sparkBase)` (static, called from the hook at the end of every `com/revrobotics/spark/SparkBase`
  constructor, i.e. before each `RETURN` in `<init>`); first call installs the sim-periodic-before callback via
  `edu.wpi.first.hal.simulation.SimulatorJNI.registerSimPeriodicBeforeCallback(Runnable)` (reflection).
- Per SPARK (reflection): model via `getSparkModel()`, id via `getDeviceId()`; `SparkMaxSim(SparkMax, DCMotor)` /
  `SparkFlexSim(SparkFlex, DCMotor)` with `DCMotor.getNEO(1)` / `getNeoVortex(1)` / `getNeo550(1)` chosen from the
  `motor` input once Scrimmage sets it (rebuild the sim object when it changes); `SparkAbsoluteEncoderSim(...)`;
  `SparkLimitSwitchSim(..., true/false)`; helper SimDevice per Global Constraints via `edu.wpi.first.hal.SimDevice.create`.
- Loop (Global Constraints): read inputs; set limits (`setPressed`), absolute position; for non-followers
  (`configAccessor.getFollowerModeLeaderId()` < 0 or follower mode off — check REVLib source for the exact signal)
  `iterate(v, max(vbus,0.1), dt)`; followers: applied output = leader's × (getFollowerModeInverted() ? -1 : 1) via
  `setAppliedOutput`; then `setMotorCurrent(current)`; publish `motorVoltage = appliedOutput × vbus ×
  (inverted ? -1 : 1)`.
- Transformer additions: `com/revrobotics/spark/SparkBase.<init>` hook; `com/revrobotics/spark/SparkSim.iterate(DDD)V`
  prologue: if not inside `ScrimmageRev` (a ThreadLocal flag set around the helper's own calls) → `ScrimmageAgent.blocked
  ("SparkSim.iterate")` and return; `com/revrobotics/sim/NoiseGenerator.<clinit>` epilogue: set `m_rand = new
  java.util.Random(0)`.
- `robot_process`: `rev_state` = "unavailable: <reason>" when the agent prints the REV-unavailable line; Scrimmage
  turns it into an error at session start when the config binds REV devices.

- [ ] Steps: failing tests (agent jar builds and contains ScrimmageRev; robot_process parses the REV line; bridge reports
  `ScrimmageREV` devices the robot created but nothing binds, and bound ones never created, in the same wording as
  existing unbound warnings; a session whose robot creates only ScrimmageREV devices never sets the Phoenix real-time
  flag; the agent-missing message mentions REV devices when the config binds any); run (FAIL); implement; run (PASS); commit `"Java agent: drive REV SPARKs through SparkSim"`.
  (Behavior is verified end to end in Task 3.)

---

### Task 3: REV probe robot and Java end-to-end tests

**Files:** Create `tests/rev_probe/` (GradleRIO 2026.1.1 project copied from the shape of `tests/java_probe/`, vendordep
`REVLib.json` 2026.0.5 from `https://software-metadata.revrobotics.com/REVLib-2026.json`), `tests/rev_probe.toml`
(an arm world, e.g. reuse the elevator_arm config's arm part with `rev` ids), `tests/test_rev_e2e.py` (`java` marker)

**Interfaces:**
- Probe robot (one Robot.java, modes chosen by an NT or env flag the tests set): SPARK MAX 1 on the arm (NEO, one
  configured inverted variant), SPARK MAX 2 following 1 inverted, absolute encoder on SPARK 1's data port, forward
  limit enabled; one SPARK created in a static field and one lazily in `teleopInit`; a student-style
  `simulationPeriodic` that calls `iterate` on its own SparkMaxSim.
- Tests: duty cycle moves the arm the expected way (inverted variant too); position PID and MAXMotion reach targets
  within tolerance; absolute encoder getPosition tracks the joint angle (zero offset honored); forward limit stops
  forward motion; follower current/voltage mirrors inverted; student iterate neutralized (agent summary line); two
  runs give identical run.csv; run reported faster than 1x; disabled holds 0 V (Review Focus 3); pause/resume no jump
  (Review Focus 2); lazily created SPARK driven (Review Focus 5); a follower whose leader ID was never created outputs
  0 V and the agent warns naming the missing leader.

- [ ] Steps: build the probe; failing tests; fix helper issues found; run (PASS, `java` marker); commit `"REV probe
  robot and end-to-end tests"`.

---

### Task 4: MAXSwerve stock part and swerve-rev template

**Files:** Modify `scrimmage/parts/stock/swerve.py` (a `maxswerve` `_Module`), `scrimmage/assembler/templates.py`
(`swerve-rev`), `scrimmage/__main__.py` (`--new swerve-rev`); Test `tests/test_stock.py`, `tests/test_assembler_core.py`

**Interfaces:**
- `maxswerve`: drive ratio choices 5.50, 5.08, 4.71 (default 4.71, the template's 14T pinion: 45·22/(14·15)), wheel
  diameter 3 in (0.0762 m), steer ratio 9424/203, drive motor NEO, steer motor NEO550, slots drive/steer/encoder; the
  encoder slot reads the module angle (encoder ratio 1 on the steer joint), describe() text names the REV MAXSwerve.
- `swerve-rev` template: chassis 26.5 in × 26.5 in module spacing (template kTrackWidth/kWheelBase), four `maxswerve`
  modules with ids from REV's MAXSwerve Java template: front-left drive SparkMax:11 steer SparkMax:10 encoder
  SparkMaxAbs:10; rear-left 13/12/12; front-right 15/14/14; rear-right 17/16/16; a gyro binding for WPILib's
  ADIS16470 (Task 5 adds the keys it needs).

- [ ] Steps: failing tests (part params/slots/geometry, ratios; template builds, assembles, binds the listed ids);
  implement; run; commit `"MAXSwerve stock part and swerve-rev template"`.

---

### Task 5: MAXSwerve example robot and CI scenario

**Files:** Create `examples/java_maxswerve/` (REV's MAXSwerve-Java-Template, BSD-3-Clause, LICENSE kept, REVLib.json
bumped to 2026.0.5), `configs/maxswerve.toml` (swerve-rev parts + `[robot_program]` like configs/swerve.toml),
`scenarios/maxswerve_auto.toml`; Modify `scrimmage/bindings.py` (GyroBinding: optional `angle` / `rate` value names
for WPILib's ADIS16470 SimDevice — read the WPILib source for its exact device and value names), `tests/test_java_e2e.py`
(scenario test), `.github/workflows/ci.yml` (cache key covers the new vendordeps; `scrimmage java build` builds the new
projects if it builds examples)

**Interfaces:** the example keeps REV's code; if it has no autonomous routine usable headless, add the smallest auto
(drive 1 m forward, turn 90°) in its own command file, documented as Scrimmage's addition. Scenario checks the end
pose with tolerances, like `scenarios/swerve_auto.toml`.

- [ ] Steps: build; failing scenario test; fix; pass; full suite incl. Java; commit `"MAXSwerve example robot and
  scenario"`.

---

### Task 6: Docs

**Files:** Create `docs/rev.md`; Modify `docs/parts.md`, `README.md`

- [ ] `docs/rev.md`: setup (vendordep, agent), short forms and table keys, followers and limits, the probe-verified
  behaviors, limitations (non-goals list from the spec), CTRE vs REV (speed, controller logic, deterministic noise).
  `docs/parts.md`: REV short forms, `maxswerve`, `--new swerve-rev`. README: "REV robots" section. Full suite on
  3.11/3.13/3.14; commit `"REV docs"`.
