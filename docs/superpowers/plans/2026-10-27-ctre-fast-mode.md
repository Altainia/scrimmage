# CTRE Fast Mode (sub-project 7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AdvantageKit-style robots with Krakens run under Scrimmage in two modes:
- **fast mode:** Scrimmage's WPILib-only simulated Kraken/CANcoder/gyro, Talon-style control, lockstep,
  repeatable;
- **Phoenix mode:** real Phoenix simulation, on the wall clock.

**Architecture:**
- A Java library (`scrimmage.sim`, built with Gradle against WPILib 2026) provides simulated devices. They talk to
  Scrimmage through HAL SimDevices (`ScrimmageMotor:<name>`, `ScrimmageEncoder:<name>`, `ScrimmageGyro:<name>`), and run
  Talon-style control from CTRE's public docs in a 5 ms HAL notifier.
- Scrimmage binds those devices like its other kinds and sets `SCRIMMAGE_MODE` for the robot.
- Scrimmage publishes the library as a file-based Maven repo plus a vendordep that a robot project commits.
- Robots with Phoenix 6 devices leave lockstep and run in real time.

**Tech Stack:** Java 17, Gradle (wrapper), WPILib 2026.2.2 (`wpilibj`, `hal`, `wpimath`, `wpiutil`, from
frcmaven), JUnit 5; Python 3.11+ (Scrimmage); AdvantageKit 26.0.2, Phoenix 6 26.x and PathPlanner for the sample
robot.

**Spec:** docs/superpowers/specs/2026-10-27-ctre-fast-mode-design.md

## Global Constraints

- **No Phoenix in the library.** The fast-mode library loads no Phoenix class: no CTRE types in its API, and no
  CTRE config objects as inputs. Talon-style behavior comes only from CTRE's public docs (closed-loop overview,
  Motion Magic). Where they're silent, the choice is ours and documented: standard PID plus feedforward, errors
  in mechanism rotations or rotations per second, `kI` integrating error × dt.
- **Inert on the real robot.** Nothing in the library may affect the RoboRIO unless robot code selects it.
  Constructing a device where HAL sim isn't active must not throw.
- **Control timing.** The control loop runs at least every 5 ms, from a HAL `Notifier` (so lockstep steps it
  deterministically).
- **Requests:** voltage, duty cycle, position, velocity, Motion Magic, Motion Magic Velocity, follower
  (aligned/opposed), neutral (brake/coast).
- **Settings:**
  - gains kP, kI, kD, kS, kV, kA, kG;
  - gravity type Elevator/Static or Arm/Cosine;
  - kS sign: Velocity Sign (default) or Closed-Loop Sign;
  - continuous wrap;
  - Motion Magic cruise velocity (rps), acceleration (rps/s) and jerk (rps/s/s); trapezoidal, or S-curve when
    jerk > 0;
  - stator and supply current limits;
  - forward/reverse soft limits;
  - peak forward/reverse voltage and duty cycle;
  - inversion;
  - sensor-to-mechanism and rotor-to-sensor ratios.
- **Non-goals:** TorqueCurrentFOC, Dynamic Motion Magic, Motion Magic Expo, CAN latency, CTRE `SwerveDrivetrain`
  in fast mode, WPILib 2027, RobotPy.
- **Mode signal:** `SCRIMMAGE_MODE=fast` in the robot program's environment (absent or anything else means not
  fast).
- **Repeatability** means Scrimmage's `run.csv`, report and robot console, not AdvantageKit's `.wpilog`.
- **Vendordep:**
  - `mavenUrls` points at a folder committed in the robot project, by relative path. If GradleRIO doesn't resolve
    that, use `implementation files(...)` and say so.
  - `cppDependencies` and `jniDependencies` are empty lists (native-utils rejects null).
  - `frcYear` is `"2026"`.
- **Sample robot:** starts from AdvantageKit v26 `talonfx_swerve` (BSD-style license, keep its notices). Target
  WPILib 2026, Linux.

## Review Focus

1. **Real robot / plain WPILib sim:** constructing `ScrimmageKraken`/`ScrimmageCANcoder`/`ScrimmageGyro` without Scrimmage
   (no `SCRIMMAGE_MODE`, or HAL not in sim) must not throw. `Scrimmage.fastMode()` is false. → test in Task 2.
2. **Fast mode on, but robot code creates Phoenix devices:** Scrimmage should say plainly that fast mode expects
   Scrimmage devices and that Phoenix devices force the run to real time. No silent drift. → test in Task 4.
3. **Two devices with the same name:** the second `ScrimmageKraken("elevator", …)` must throw `IllegalStateException`
   naming the duplicate, not silently share a SimDevice. → test in Task 2.
4. **A config binding `SimMotor:x` that the robot never creates:** the existing "never created" warning names the
   Scrimmage device and mentions fast mode. → test in Task 3.
5. **Agent sessions with Phoenix devices** (now on the wall clock): a held session can't pause Phoenix. The
   session status and the first advance carry a warning, not silence. → test in Task 4.

---

### Task 1: Talon-style control core (pure Java) and the library's Gradle project

**Files:**
- Create the Gradle project `scrimmage/java/fastmode/`:
  - `settings.gradle`, `build.gradle` (`java-library`, `maven-publish`, Java 17; dependencies `edu.wpi.first.wpilibj:wpilibj-java`,
    `edu.wpi.first.hal:hal-java`, `edu.wpi.first.wpimath:wpimath-java`, `edu.wpi.first.wpiutil:wpiutil-java`,
    all `2026.2.2`, from `https://frcmaven.wpi.edu/artifactory/release/`, as `compileOnly`;
    JUnit 5 for tests; group `io.github.altainia.scrimmage`, artifact `scrimmage-fastmode`, version from
    `-PlibVersion`, default `0.1.0`);
  - a Gradle wrapper (copy `gradlew`, `gradlew.bat`, `gradle/wrapper/*` from `examples/java_swerve`).
- Create `scrimmage/java/fastmode/src/main/java/scrimmage/sim/KrakenSettings.java`,
  `ControlRequest.java`, `TalonControl.java`, `MotionMagicProfile.java`, `KrakenModel.java`.
- Test: `scrimmage/java/fastmode/src/test/java/scrimmage/sim/TalonControlTest.java`, `MotionMagicProfileTest.java`.

**Interfaces:**
- Produces:
  - **`KrakenSettings`:** a plain class with public mutable fields and fluent setters:
    - gains: `kP, kI, kD, kS, kV, kA, kG`;
    - modes: `GravityType gravityType` (`ELEVATOR_STATIC` default, `ARM_COSINE`) and
      `StaticFeedforwardSign staticFeedforwardSign` (`VELOCITY_SIGN` default, `CLOSED_LOOP_SIGN`);
    - `boolean continuousWrap`;
    - Motion Magic: `double motionMagicCruiseVelocity, motionMagicAcceleration, motionMagicJerk`;
    - current limits: `double statorCurrentLimit` (`boolean statorCurrentLimitEnable`) and
      `double supplyCurrentLimit` (`boolean supplyCurrentLimitEnable`);
    - soft limits: `double forwardSoftLimitThreshold, reverseSoftLimitThreshold` and
      `boolean forwardSoftLimitEnable, reverseSoftLimitEnable`;
    - peak output: `double peakForwardVoltage = 16, peakReverseVoltage = -16, peakForwardDutyCycle = 1,
      peakReverseDutyCycle = -1`;
    - `boolean inverted` (true = clockwise positive, as `InvertedValue.Clockwise_Positive`), `boolean brake`;
    - ratios: `double sensorToMechanismRatio = 1, rotorToSensorRatio = 1`;
    - `OutputType outputType` (`VOLTAGE` default, `DUTY_CYCLE`): the units of kS/kV/kA/kG/kP… as CTRE documents.
  - **`ControlRequest`:** an immutable value, `Kind kind` (`VOLTAGE, DUTY_CYCLE, POSITION, VELOCITY, MOTION_MAGIC,
    MOTION_MAGIC_VELOCITY, NEUTRAL`), `double value`, `double feedforwardVolts`, with static factories
    `voltage(v)`, `dutyCycle(d)`, `position(rot)`, `velocity(rps)`, `motionMagic(rot)`,
    `motionMagicVelocity(rps)`, `neutral()`, and `withFeedforward(volts)`.
  - **`TalonControl`:** `new TalonControl(KrakenSettings s)`. The core is
    `double update(ControlRequest r, double mechPosition, double mechVelocity, double motorRps, double vbus,
    double dt)`, which returns the voltage to apply across the physical motor. Mechanism units are rotations and
    rps after the ratios; inversion is applied in the device. The method:
    - holds integrator and derivative state;
    - generates Motion Magic profiles;
    - reports `double statorCurrent()` and `double supplyCurrent()` after the last update;
    - offers `void reset()`.
  - **`MotionMagicProfile`:** `static State next(State current, double target, double cruise, double accel,
    double jerk, double dt)` with `record State(double position, double velocity, double acceleration)`
    (position profile), and a velocity variant `nextVelocity(...)`.
  - **`KrakenModel`:** constants from WPILib `DCMotor.getKrakenX60(1)`. It offers `double statorCurrent(double volts,
    double motorRps)` and `double voltsForStatorCurrent(double amps, double motorRps)`.

- [ ] **Step 1: Write failing tests in `TalonControlTest`.** Use exact numbers, with `vbus = 12`, `dt = 0.005`
  and gains in volts unless noted.
  - `voltageRequestPassesThroughWithinPeaks`: `voltage(5)` gives 5. `voltage(20)` gives 16 (the default peak).
    With `peakForwardVoltage = 10`, `voltage(12)` gives 10.
  - `dutyCycleUsesBusVoltage`: `dutyCycle(0.5)` at vbus 11 gives 5.5.
  - `positionPOnly`: with kP = 2 and target 1.0 rot, at 0.25 rot it gives 1.5.
  - `velocityFeedforwardAndKsVelocitySign`: with kV = 0.12, kS = 0.25 and target 10 rps, at rest it gives
    1.45. Target −10 gives −1.45.
  - `ksClosedLoopSignFollowsPositionError`: with CLOSED_LOOP_SIGN, kS = 0.3 and a position target below the
    current position, the output includes −0.3.
  - `elevatorStaticKgAlwaysAdded` and `armCosineKgScalesWithAngle`: with kG = 0.5 and ARM_COSINE, a position of
    0.25 rot gives a kG contribution of 0 (±1e-9), and position 0 gives 0.5.
  - `integralAccumulatesErrorTimesDt`: with kI = 1 and an error of 0.1 held for 100 updates, the output is 0.05
    (±1e-9).
  - `continuousWrapTakesShortestWay`: at 2.1 rot with target 0.8 and wrap on, the error used is −0.3 (as CTRE's
    example: drive to 1.8).
  - `forwardSoftLimitBlocksForwardOutput`: with the forward soft limit at 1.0 and position 1.05, a positive
    request gives 0 and a negative one passes.
  - `statorLimitCapsCurrent`: with the stator limit at 40 A at stall, `voltage(12)` yields a voltage whose
    `KrakenModel.statorCurrent` is ≤ 40 + 0.5.
  - `dutyCycleOutputTypeGainsAreInDutyUnits`: with outputType DUTY_CYCLE, kP = 0.1 and error 1 rot at vbus 10,
    it gives 1.0 V.
  - `neutralGivesZero`.
- [ ] **Step 2: Write failing tests in `MotionMagicProfileTest`.**
  - `trapezoidRespectsCruiseAndAccel`: from 0 to 10 rot with cruise 2 rps and accel 4 rps/s at dt 0.005, the
    velocity never exceeds 2 + 1e-9, |acceleration| ≤ 4 + 1e-9, and it ends at 10 ± 1e-6 with velocity 0.
  - `shortMoveIsTriangle`: 0 to 0.1 rot never reaches cruise.
  - `jerkLimitsAccelerationChange`: with jerk 20, the |Δacceleration| per step is ≤ 20·dt + 1e-9.
  - `velocityProfileRampsAtAccel`: 0 to 5 rps at accel 10 takes 0.5 s ± dt.
- [ ] **Step 3: Run the tests and confirm they fail.** Run `cd scrimmage/java/fastmode && ./gradlew test`.
  Expected: FAIL (classes missing).
- [ ] **Step 4: Implement the five classes.** The output formula, in output units, is
  `kS·sgn + kV·vRef + kA·aRef + kG·(1 or cos(2π·pos)) + kP·e + kI·∫e·dt + kD·de/dt + feedforward`:
  - position requests: vRef = aRef = 0; Motion Magic: vRef and aRef from the profile; velocity requests: e is the
    velocity error;
  - convert duty to volts with vbus;
  - clamp to the peaks, then apply soft limits, then the stator limit (via `KrakenModel`), then the supply limit
    (supply current ≈ stator current × |V|/vbus).
- [ ] **Step 5: Run the tests and confirm they pass.** Run `./gradlew test`. Expected: all pass.
- [ ] **Step 6: Commit** with message `"Fast mode: Talon-style control core and the library's Gradle project"`.

---

### Task 2: Simulated devices, the mode check, and building/publishing the library

**Files:**
- Create `scrimmage/java/fastmode/src/main/java/scrimmage/sim/Scrimmage.java`, `ScrimmageKraken.java`,
  `ScrimmageCANcoder.java` and `ScrimmageGyro.java`.
- Test: `scrimmage/java/fastmode/src/test/java/scrimmage/sim/DevicesTest.java`, using WPILib's sim HAL in tests
  (`HAL.initialize(500, 0)`; add `hal-jni`/`wpiutil-jni` desktop natives as `testRuntimeOnly`, or skip HAL-bound
  tests with a clear message if natives are unavailable).
- Modify `scrimmage/java_tools.py`: add `FASTMODE_VERSION = "0.1.0"`, `build_fastmode(jdk=None) -> Path`, which
  runs `./gradlew publish` into `<cache>/fastmode-maven` and returns that path, and
  `write_vendordep(project: Path) -> Path`.
- Modify `scrimmage/__main__.py`: `scrimmage java build` also builds the library, and a new
  `scrimmage java vendordep <project>` copies `<cache>/fastmode-maven` into `<project>/scrimmage-maven/` and
  writes `<project>/vendordeps/Scrimmage.json`.
- Test: `tests/test_java_tools.py` (vendordep JSON shape, folder copied), plus a `java`-marked test in
  `tests/test_fastmode_vendordep.py` that proves a GradleRIO project resolves the relative `mavenUrls` (see
  Step 5).

**Interfaces:**
- Consumes: Task 1's `KrakenSettings`, `ControlRequest` and `TalonControl`.
- Produces (robot side):
  - **`Scrimmage.fastMode()`:** true iff `System.getenv("SCRIMMAGE_MODE")` equals `"fast"`.
  - **`ScrimmageKraken`:**
    - `new ScrimmageKraken(String name, KrakenSettings s)`;
    - commands: `setControl(ControlRequest r)`, `follow(ScrimmageKraken leader, boolean opposed)`,
      `useRemoteEncoder(ScrimmageCANcoder e)` (the closed loop uses the encoder; `rotorToSensorRatio` applies),
      `setPosition(double mechRot)` (resets the reading);
    - readings: `double getPosition()` and `double getVelocity()` (mechanism units),
      `double getMotorVoltage()`, `double getStatorCurrent()`, `double getSupplyCurrent()`.
  - **`ScrimmageCANcoder`:** `new ScrimmageCANcoder(String name, double magnetOffsetRot, boolean clockwisePositive)`, with
    `double getAbsolutePosition()` in [−0.5, 0.5), `double getPosition()` and `double getVelocity()`.
  - **`ScrimmageGyro`:** `new ScrimmageGyro(String name)`, with `double getYawDegrees()` (CCW+, unwrapped),
    `double getYawRateDegPerSec()` and `void setYaw(double deg)`.
  - **Device protocol** (Scrimmage relies on it in Task 3; the robot creates these SimDevices):
    - `ScrimmageMotor:<name>`: outputs `motorVoltage` (double, V across the physical motor, after inversion) and
      `brake` (boolean). Inputs `position` (rotor rotations, physical frame, since start), `velocity` (rotor rps),
      `vbus` (V).
    - `ScrimmageEncoder:<name>`: inputs `position` (magnet rotations, CCW+ when viewed from the magnet side, unwrapped)
      and `velocity` (rps).
    - `ScrimmageGyro:<name>`: inputs `yaw` (deg, CCW+, unwrapped) and `rate` (deg/s, CCW+).
  - **Control loop:** one shared HAL `Notifier`, every 0.005 s, updates every `ScrimmageKraken` (leaders before
    followers) and writes `motorVoltage`. Robot-thread calls only record the request, under a lock.
  - **Python:** `java_tools.build_fastmode(jdk: Path | None = None) -> Path` and
    `java_tools.write_vendordep(project: Path) -> Path`.
  - **Vendordep:** `Scrimmage.json` has `fileName "Scrimmage.json"`, `name "Scrimmage"`, `version FASTMODE_VERSION`,
    a fixed uuid (`"312a1138-d262-41fe-9cca-bc05120dfa6b"`), `frcYear "2026"`,
    `mavenUrls ["scrimmage-maven"]` and `jsonUrl ""`. Its `javaDependencies` are
    `[{groupId "io.github.altainia.scrimmage", artifactId "scrimmage-fastmode", version FASTMODE_VERSION}]`;
    `jniDependencies` and `cppDependencies` are `[]`.
- [ ] **Step 1: Write failing tests.**
  - `DevicesTest.fastModeFollowsEnvironment`: false without the env var. Test the parsing through a
    package-private `Scrimmage.fastMode(Map<String,String> env)`.
  - `DevicesTest.duplicateNameThrows` (Review Focus 3): message contains `"elevator"`.
  - `DevicesTest.constructsWithoutScrimmage` (Review Focus 1): no throw when HAL isn't in sim mode or the SimDevice
    is null. Readings are 0.
  - `DevicesTest.followerOpposedMirrorsLeader`: leader `voltage(6)` gives follower motorVoltage −6.
  - `DevicesTest.invertedFlipsVoltageAndReadings`: with inverted true, `voltage(3)` writes motorVoltage −3, and
    position input 2.0 reads −2.0 / sensorToMechanismRatio.
  - In `tests/test_java_tools.py`, `test_vendordep_json_and_maven_folder` asserts exact JSON keys and values, and
    that `scrimmage-maven/io/github/altainia/scrimmage/scrimmage-fastmode/0.1.0/*.jar` exists in the project.
- [ ] **Step 2: Run the tests and confirm they fail.** Run `./gradlew test` and
  `SCRIMMAGE_SKIP_JAVA=1 .venv/bin/python -m pytest tests/test_java_tools.py -k vendordep`. Expected: FAIL.
- [ ] **Step 3: Implement the device classes and the Python build/vendordep functions and CLI.**
- [ ] **Step 4: Run the tests and confirm they pass.**
- [ ] **Step 5: Prove a relative `mavenUrls` entry works.** Write `tests/test_fastmode_vendordep.py::test_gradlerio_resolves_committed_vendordep`
  (`java` marker). It copies `examples/java_swerve` to tmp, runs `java_tools.write_vendordep(tmp)`, adds a
  one-line use of `scrimmage.sim.Scrimmage.fastMode()` to `Robot.java`, and checks that `./gradlew build` exits 0.
  - If it fails because the relative URL isn't resolved, switch `write_vendordep` to also print the
    `implementation files(...)` fallback line, document it, and make the test add that line instead.
  - Record which path worked in the commit message.
- [ ] **Step 6: Commit** with message `"Fast mode: simulated Kraken/CANcoder/gyro, SCRIMMAGE_MODE, vendordep"`.

---

### Task 3: Scrimmage binds fast-mode devices and sets the mode

**Files:**
- Modify `scrimmage/bindings.py`: `MotorBinding`, `EncoderBinding` and `GyroBinding` accept `scrimmage = "<name>"`
  (kind `"Scrimmage"`; device keys `("ScrimmageMotor", name)`, `("ScrimmageEncoder", name)` and `("ScrimmageGyro", name)`).
  `REV_DEVICES`/`CTRE_DEVICES`-style detection covers the "created but unbound / bound but never created" reports.
- Modify `scrimmage/parts/ids.py`: add short forms `"SimMotor:<name>"`, `"SimEncoder:<name>"` and
  `"SimGyro:<name>"`. The name is any non-empty string without spaces.
- Modify `scrimmage/config.py`: `[robot_program] sim_mode = "fast" | "phoenix"` (default `"phoenix"`). For `"fast"`
  it adds `SCRIMMAGE_MODE=fast` to the robot's env.
- Modify `scrimmage/__main__.py`: `run` and `serve` accept `--sim-mode fast|phoenix`, which overrides the config.
- Modify `scrimmage/mcp_server.py`: `start_session(..., sim_mode=None)`.
- Modify `scrimmage/bridge.py`: the never-created warning for a Scrimmage device says
  `"fast mode: the robot program never created <Scrimmage device>; does it pick its fast-mode IO when
  Scrimmage.fastMode() is true?"`.
- Tests: `tests/test_bindings.py` (or the existing motor/encoder/gyro binding tests), `tests/test_parts_ids.py`,
  `tests/test_config.py`, `tests/test_bridge.py`.

**Interfaces:**
- Consumes: Task 2's device protocol.
- Produces:
  - **Motor binding:** `MotorBinding.kind == "Scrimmage"`. `apply()` reads `ScrimmageMotor:<name>` `<motorVoltage` (0
    when disabled) and `<brake`. `updates()` writes `>position` (rotor rotations), `>velocity` (rotor rps) and
    `>vbus`, with the sign and gear handling of the REV path.
  - **Encoder binding:** writes `>position`/`>velocity` (magnet rotations).
  - **Gyro binding:** writes `>yaw`/`>rate` (deg, CCW+, unwrapped).
  - **Config:** `Config.robot_program.sim_mode: str`.
- [ ] **Step 1: Write failing tests.**
  - Motor: with the same fake joint state as the REV motor-binding test, motorVoltage 6 V gives the expected torque
    sign, and the written position/velocity is in rotor units with the gear ratio.
  - Encoder: a 90° joint gives `>position` 0.25.
  - Gyro: a body yawed +30° gives `>yaw` 30.
  - ids: `"SimMotor:fl_drive"` maps to `{scrimmage = "fl_drive"}` in a motor slot; `"SimMotor:"` errors.
  - Config: `sim_mode = "fast"` puts `SCRIMMAGE_MODE=fast` into the robot env; `sim_mode = "x"` gives a
    ConfigError naming the choices.
  - CLI: `--sim-mode fast` overrides.
  - Bridge (Review Focus 4): a config binding `SimMotor:x` that the robot never creates logs the fast-mode
    wording.
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests and confirm they pass,** plus the full Python suite (`SCRIMMAGE_SKIP_JAVA=1`).
- [ ] **Step 5: Commit** with message `"scrimmage: bind fast-mode Scrimmage devices; sim_mode config and --sim-mode"`.

---

### Task 4: Phoenix mode runs on the wall clock

**Files:**
- Modify `scrimmage/bridge.py`. When Phoenix 6 devices appear while lockstep is engaged, release lockstep: send
  `>engage false` and end the driver. The bridge then runs its existing real-time loop, so physics keeps pace and
  skips ahead with a warning when behind.
  - Replace `REALTIME_REASON` and the `effective_speed` wording: `"Phoenix 6 simulation runs on the wall clock, so
    this robot runs in real time (not lockstep)"`.
  - `phoenix` status stays.
- Modify `scrimmage/session.py`. `advance` works in real time for Phoenix robots, using the existing real-time
  branch.
  - A held session with Phoenix devices sets a status/warning: `"Phoenix 6 devices keep running between calls:
    this session can't pause them"`. It appears in `session_status` and in the first `advance` result.
  - The `phoenix_speed` measurement moves to the real-time branch as physics lag.
- Modify `scrimmage/scenario_runner.py`. `PHOENIX_NOTE` gets the new wording, and the slow note says physics fell
  behind real time.
- Modify `scrimmage/bridge.py`/`session.py` (Review Focus 2): with `SCRIMMAGE_MODE=fast` and Phoenix devices
  seen, log and report `"fast mode, but the robot program created Phoenix 6 devices (<names>): they force real
  time; use the fast-mode IO for them"`.
- Modify `tests/test_java_e2e.py`: remove the CI skip on `test_java_swerve_auto_scenario`, and keep the
  slow-machine skip.
- Docs: in `README.md`, rewrite the "Phoenix runs at 1× speed" bullet (wall clock, real time, no pause), and update
  the Lockstep section.
- Tests: `tests/test_bridge.py`, `tests/test_session.py`, `tests/test_runner.py` (fake robot with `--phoenix`).

**Interfaces:**
- Produces: `Bridge.realtime_reason` (new text), and Session status key `"phoenix_unpausable": true` when it
  applies.
- [ ] **Step 1: Write failing tests** (fake robot `phoenix=True, lockstep=True`).
  - The bridge sends `>engage: false` after the Phoenix device appears, and `bridge.mode` becomes `"realtime"`.
  - The scenario report has the new note and no lockstep "released" failure. Check what the implicit "lockstep
    stayed engaged" check does for Phoenix robots: it must pass or be skipped with a reason, not fail.
  - The Session status has `phoenix_unpausable` and the first advance result carries the warning.
  - Fast mode plus Phoenix device gives the Review Focus 2 message.
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite including Java (`.venv/bin/python -m pytest`). Java Phoenix tests
  (`test_java_e2e`, `test_java_agent`, the Tuner X test) must pass at real time. Adjust only their wording
  assertions.
- [ ] **Step 5: Commit** with message `"Phoenix mode: robots with Phoenix 6 devices run on the wall clock, not lockstep"`.

---

### Task 5: Sample robot, config, scenarios and docs

**Files:**
- Create `examples/java_fastmode/`:
  - Start from AdvantageKit v26.0.2 `template_projects/sources/talonfx_swerve` (keep its license notices; add
    AdvantageKit's LICENSE beside it).
  - Add `subsystems/elevator/{Elevator, ElevatorIO, ElevatorIOTalonFX, ElevatorIOScrimmage}.java`: two Krakens, the
    second a follower (opposed); Motion Magic; kG Elevator/Static.
  - Add `subsystems/drive/{ModuleIOScrimmage, GyroIOScrimmage}.java`.
  - Add a `ScrimmageConstants` helper that builds both the Phoenix configs and the `KrakenSettings` from one set of
    constants.
  - `RobotContainer` picks the Scrimmage IO when `Scrimmage.fastMode()`, the real IO otherwise.
  - The auto uses PathPlanner: drive the template's example path or a simple 2 m path, then raise the elevator to
    a fixed height.
  - Commit the vendordep folder (`scrimmage-maven/`, `vendordeps/Scrimmage.json`) produced by Task 2's command.
- Create `configs/fastmode.toml`: stock `swerve` modules (Krakens) plus a stock `elevator`. Bind:
  - in fast mode, by `SimMotor`/`SimEncoder`/`SimGyro` names matching the robot code;
  - in Phoenix mode, `TalonFX`/`CANcoder`/`Pigeon2` ids matching the template's TunerConstants.

  A binding list per mode is acceptable: use two configs (`fastmode.toml`, `fastmode_phoenix.toml`) if one
  can't hold both.
- Create `scenarios/fastmode_auto.toml` (fast: auto end pose and elevator height at the step end, tolerances
  measured and recorded in comments) and `scenarios/fastmode_auto_phoenix.toml` (Phoenix mode: the same checks,
  tolerances allowing run-to-run variation).
- Create `docs/fast-mode.md`:
  - the two modes, and how to set up (vendordep command, commit both folders, `Scrimmage.fastMode()`, IO classes);
  - the settings mapping from Phoenix configs to `KrakenSettings`;
  - fidelity limits: 5 ms loop vs 1 kHz, no CAN latency, approximate current limits, no Pro features, the
    documented formula choices;
  - the license position.
- Modify `README.md`: a "Fast mode (CTRE robots)" section linking `docs/fast-mode.md`. Modify `.github/workflows/ci.yml`
  if the library's Gradle build or test needs a step (the Java job runs `./gradlew test` in `scrimmage/java/fastmode`).
- Tests: `tests/test_fastmode_e2e.py` (`java` marker).

**Interfaces:**
- Consumes: Task 2 (library API, vendordep command), Task 3 (`sim_mode`, short forms), Task 4 (Phoenix mode on the
  wall clock).
- [ ] **Step 1: Write failing tests in `tests/test_fastmode_e2e.py`.**
  - `test_fast_mode_auto_scenario_passes`
  - `test_fast_mode_runs_repeat_exactly`: two runs, identical `run.csv`.
  - `test_fast_mode_is_faster_than_real_time`: run with `--speed max`; simulated/wall > 1.5.
  - `test_fast_mode_creates_no_phoenix_devices`: no `(v6)` device in the session's devices.
  - `test_phoenix_mode_auto_scenario_passes`
  - `test_fast_mode_motion_magic_reaches_height_in_time`: elevator within 1 cm of the target by the step end.
- [ ] **Step 2: Run the tests and confirm they fail.**
- [ ] **Step 3: Build the example, configs, scenarios and docs.**
- [ ] **Step 4: Run** `tests/test_fastmode_e2e.py`, then the full suite including Java, then
  `SCRIMMAGE_SKIP_JAVA=1` on Python 3.11 and 3.13 (scratchpad venvs).
- [ ] **Step 5: Commit** with message `"Fast mode: AdvantageKit sample robot, scenarios and docs"`.
