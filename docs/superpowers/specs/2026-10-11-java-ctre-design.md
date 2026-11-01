# Java robot programs with CTRE Phoenix 6: design

Status: approved in discussion 2026-10-11. This spec is waiting for review.

## Context

Scrimmage's goal is to let FRC students test robot code before the robot is built. The user's team, like
most FRC teams, writes **Java** and uses **CTRE Phoenix 6**: TalonFX/Kraken X60 motors, CANcoders, and a
Pigeon 2, on a **swerve** drivetrain. Until now the Java path was untested and vendor devices needed a
hand-added shim, so this sub-project comes before the part format.

Roadmap, kept in the memory notes:
1. Lockstep (done)
2. Agent sessions, scenarios and MCP (done)
3. **Java + Phoenix 6 (this document)**
4. Part format, assembler and FreeCAD exporter
5. GUI

### Findings from the feasibility probe (2026-10-11, Phoenix 6 26.1.2, WPILib 2026.2.1)

The probe used a throwaway Java project.

- **Phoenix 6 desktop sim registers HAL SimDevices**, and `halsim_ws_client` relays them both ways with no
  robot-code changes:

  | Wire type / device | Values |
  |---|---|
  | `CANMotor` / `Talon FX (v6)[N]` | `<dutyCycle <motorVoltage <supplyCurrent <torqueCurrent >supplyVoltage` |
  | `CANEncoder` / `Talon FX (v6)[N]/Rotor Sensor` | `<position >rawPositionInput >velocity >acceleration` |
  | `CANEncoder` / `CANcoder (v6)[N]` | `<position >rawPositionInput >velocity >supplyVoltage` |
  | `CANGyro` / `Pigeon 2 (v6)[N]` | `<yaw >rawYawInput >pitch >roll >angularVelX/Y/Z >supplyVoltage` |
  | `CANDIO` / `Talon FX (v6)[N]/Fwd Limit`, `/Rev Limit` | `<init <input <>value` |

  Values sent by the probe were read back by `getPosition()`, `getVelocity()` and `getYaw()` in Java.
  `<motorVoltage` reflects the Talon's own control, so onboard PID and Motion Magic run in Phoenix's sim.
  The Talons only output when the HAL DriverStation is enabled.
- **Phoenix 6's sim runs on the wall clock** (status signals refresh about 100 Hz of wall time).
  - Under lockstep faster than real time, Java getters were stale: about 20 steps behind at 50×, about 1
    step at 1×.
  - A CTRE engineer said in 2022 that using the sim clock was "being investigated". The user has emailed
    CTRE to ask about it.
- **Lockstep loads into a Java sim**, through a Gradle hook. `wpi.sim.envVar("HALSIM_EXTENSIONS", …)` doesn't
  work, because GradleRIO overwrites it. Inserting an action before the sim task's exec action that appends
  the extension does work.
- **Timing:** the first build took about 10 s with warm caches; sim startup to WebSocket connect takes 0.7–2.7 s.
- **WebSocket frame format:** the ws client rejects JSON arrays. Send one frame per message, which
  Scrimmage already does.

## Goals

- **An unmodified Java robot project runs under Scrimmage through its `[robot_program]` config:**
  - `scrimmage run`
  - MCP sessions
  - `serve --launch`

  No robot code changes and no `build.gradle` changes.
- **Phoenix 6 TalonFX/Kraken, CANcoder and Pigeon 2 are bound to the physics.** The Talon's onboard control
  drives the joints, and Scrimmage supplies the sensor values.
- **Robot-side simulation models don't fight Scrimmage's physics**, including the Tuner X template's
  `startSimThread()` and hand-written `DCMotorSim` code.
- **Anything that still writes sim inputs behind Scrimmage's back is detected and reported.**
- **Phoenix robots run at 1×,** held between agent calls, and this limitation is stated in status and
  reports.
- **The work is proven on a Java swerve example in the Tuner X template style, and covered in CI.**

## Non-goals

- REV (SPARK MAX/Flex) and Phoenix 5 devices, and specific YAGSL support (the agent may happen to cover it;
  this is recorded, not promised).
- Making Phoenix follow WPILib sim time. That waits on CTRE.
- Faster-than-real-time or byte-repeatable runs for Phoenix robots.
- The part format, assembler and FreeCAD exporter (next sub-project).
- Hand-run setups (VS Code "Simulate Robot Code"). They keep working by connecting to `scrimmage serve`,
  but only get the agent and lockstep if the user adds the init script themselves. The README shows how.

## Design

### Running a Java project

A Java robot config points at the team's repo:

```toml
[robot_program]
command = ["./gradlew", "-I", "{scrimmage_java}/scrimmage.gradle", "simulateJava"]
cwd = "/path/to/robot-repo"
```

`{scrimmage_java}` is a new command placeholder, like `{python}`. It expands to Scrimmage's installed `java/`
directory.

### `java/scrimmage.gradle` (Gradle init script)

For every project that applies GradleRIO, it:
- enables the HALSim WebSocket client and disables the sim GUI and the sim Driver Station extension (in a
  launched session Scrimmage is the DS)
- passes `HALSIMWS_HOST`/`HALSIMWS_PORT` to the simulation
- appends the lockstep extension (located through `SCRIMMAGE_LOCKSTEP_SO`, which Scrimmage sets) to
  `HALSIM_EXTENSIONS`, using the action-insertion hook from the probe
- adds `-javaagent:<scrimmage-agent.jar>` to the simulation JVM's arguments

If the Gradle daemon doesn't pass Scrimmage's environment through to the simulation (to be proven in the
plan's first task), the script reads Gradle properties (`-Pscrimmage.port=…`), which Scrimmage adds to the
command instead.

### `java/agent`: the sim-write guard

A `java.lang.instrument` agent, using ASM. It never transforms Phoenix's own classes: CTRE's license forbids
modifying their software. Instead, in the robot program's own classes, calls to the following setters become
no-ops that give `StatusCode.OK`:
- `com.ctre.phoenix6.sim.TalonFXSimState`: `setRawRotorPosition`, `addRotorPosition`, `setRotorVelocity`,
  `setRotorAcceleration`, `setSupplyVoltage` (not `setForwardLimit`/`setReverseLimit`: Scrimmage has no binding for
  the limit-switch inputs, so robot-side sim code keeps driving them)
- `CANcoderSimState`: `setRawPosition`, `addPosition`, `setVelocity`, `setSupplyVoltage`
- `Pigeon2SimState`: `setRawYaw`, `addYaw`, `setPitch`, `setRoll`, `setAngularVelocityX/Y/Z`,
  `setSupplyVoltage`

The rewrite covers every overload, including the units overloads. Only calls on these three classes are skipped; other
Phoenix devices' robot-side sim models (CANrange, TalonFXS, CANdle, CANdi) keep working. `setMotorType` and
`setMagnetHealth` take enums, so the primitive-argument filter leaves them alone.

It counts blocked calls by method, and by the first non-CTRE caller frame (sampled). Every 5 s, and at exit,
it prints a single summary line to stdout:

```
scrimmage agent: ignored 1204 robot-side sim writes (TalonFXSimState.setRawRotorPosition from frc.robot.CommandSwerveDrivetrain…)
```

Getters (`getMotorVoltage`, etc.) are untouched.

**Building it.** The agent is built with the WPILib JDK
(`~/wpilib/<year>/jdk`, or `JAVA_HOME`) by `scrimmage java build`. That compiles the agent, downloads
and caches ASM from Maven Central (pinned version, checksum-verified), and writes
`java/build/scrimmage-agent.jar`. `scrimmage java path` prints the init script path, for hand-run setups.

### Phoenix 6 bindings (robot config)

```toml
[[motor]]
name = "fl_drive"
ctre = "TalonFX:1"          # Phoenix 6 device and CAN id; bus "" (rio/sim default)
joint = "fl_drive"
motor = "KrakenX60"
gear_ratio = 6.75           # rotor revs per joint rev
invert = false              # physical mounting, as for other motors

[[encoder]]
ctre = "CANcoder:9"
joint = "fl_steer"
ratio = 1.0                 # sensor revs per joint rev
offset = 0.0                # rotations added to the raw reading (the magnet offset is applied by Phoenix itself)

[[gyro]]
ctre = "Pigeon2:13"
body = "chassis"
```

**Motor.** A `ctre` TalonFX binding:
- reads `<motorVoltage` (the voltage the Talon applies, including its onboard control) and runs it through
  Scrimmage's DC motor model to get joint torque and current
- writes `>rawPositionInput` and `>velocity` on `…/Rotor Sensor` (rotor rotations and rps = joint motion ×
  `gear_ratio`, signed for `invert`)
- writes `>supplyVoltage` from the battery model

Brake/coast comes from Phoenix's neutral output: a `<motorVoltage` of 0 is treated as brake unless the
config says `neutral = "coast"`. Phoenix doesn't expose its neutral mode on the wire, so the config states
it.

**CANcoder.** Writes `>rawPositionInput` (rotations) and `>velocity` from the joint.

**Pigeon 2.** Writes these from the chassis body:
- `>rawYawInput` (degrees, CCW positive, unwrapped)
- `>pitch` and `>roll`
- `>angularVelX/Y/Z` (degrees per second)
- `>supplyVoltage`

**Verification.** The exact semantics are pinned by tests against real Phoenix in the plan's spike tasks,
and the binding code is adjusted to match:
- rotor versus mechanism
- the sim state's `Orientation`
- the magnet offset
- sign conventions

### The real-time policy

When the robot program reports any Phoenix 6 device (a `… (v6)[…]` device name):
- `Session` paces `advance` at 1× regardless of the requested speed, including `max`
- `status()` and `state()` include `"realtime_reason": "Phoenix 6 simulation runs on the wall clock"`
- scenario reports note that the speed was forced to 1× and that results are close but not byte-identical
- `serve --speed` other than 1 logs a warning and runs at 1×

Lockstep stays on, and the robot stays held between calls.

### The safety net

Scrimmage knows the last value it sent for every `>` input on the Phoenix devices it binds. The ws client
relays any change the robot side makes to those values. If a relayed value differs from Scrimmage's last
sent value, outside a small tolerance and not explained by an in-flight update, Scrimmage records a
"foreign sim write". It records the device, the key and the first time it happened.

- `status()` lists foreign writes.
- Sessions log one warning per device.
- Scenarios add an implicit check that fails when any foreign write was seen, with this message: "something
  in the robot program writes simulated sensor values for Talon FX (v6)[1] (rotor position), so physics and
  robot code disagree; see the agent summary line in robot.log".

The agent's summary line is also parsed from the console and shown in `status()`.

### Held sessions with Phoenix

Phoenix keeps running on the wall clock while the session is held. The plan measures an onboard closed loop
(Motion Magic to a setpoint) across a 30 s hold.

- **If it misbehaves** (overshoot or windup after resume), Scrimmage reports `>supplyVoltage = 0` to every
  Phoenix device while held, which stops them applying output, and restores the battery voltage before the
  next step.
- **If it's harmless,** that's documented, and the voltage trick isn't added.

### Swerve robot and example

- **`models/swerve.xml`:** a 0.7 m square chassis with four MK4i-style modules. Each module has a steer hinge
  (z axis) and a drive hinge (wheel axis, 4 in wheel), on the field from `kitbot.xml`.
- **`configs/swerve.toml`:**
  - drive Krakens (`gear_ratio` 6.75, L2) and steer Krakens (150/7)
  - CANcoders on the steer joints
  - a Pigeon 2
  - `[robot_program]` running `examples/java_swerve` through the init script
- **`examples/java_swerve/`:** a Java project using CTRE's `SwerveDrivetrain` API in the Tuner X template
  style, including `startSimThread()` in simulation. It does field-centric teleop from joystick 0 and a
  simple autonomous: drive 1 m forward, then rotate 90°. It's based on CTRE's published examples if their
  license allows redistribution; otherwise it's written fresh to the same structure.

## Failure handling

| Situation | Behavior |
|---|---|
| No JDK found (`scrimmage java build`) | An error naming the places searched (`JAVA_HOME`, `~/wpilib/*/jdk`) and how to install WPILib |
| Agent jar not built when a Java config runs | `Session.start` fails, telling the user to run `scrimmage java build` |
| Gradle build fails | `start_timeout` covers it; the error includes the console tail, which shows the Gradle failure |
| Phoenix device in the config never appears | The existing "unknown device" handling: the binding stays idle, and a warning names the expected device |
| Foreign sim writes | Status lists them, scenarios fail an implicit check, and the message names the device and key |

## Testing

- **Unit:**
  - Phoenix bindings against recorded frames: the device names and keys above, and the conversions
  - the real-time policy, triggered by a Phoenix device appearing
  - safety-net detection, with in-flight tolerance
  - the `{scrimmage_java}` placeholder
- **Java spikes, run first in the plan:**
  1. the init script reaches the sim JVM (environment, or the property fallback)
  2. the agent blocks Tuner X `startSimThread()` writes, and the robot reads Scrimmage's values
  3. binding semantics against real Phoenix (rotor/mechanism, orientation, offset, signs)
  4. the hold behavior of onboard closed loops
- **Java end to end, with real Gradle and Phoenix:**
  - the swerve example drives 1 m field-centric and rotates 90° in a scenario, with pose and heading checks
  - the Tuner X sim thread is active, and the robot still follows Scrimmage's physics, with the agent's
    summary line present
  - a deliberately unguarded write (agent disabled) is caught by the safety net
- **CI:** a separate `java` job.
  - It sets up Temurin 17 with `actions/setup-java` and a cache of `~/.gradle` and `examples/java_swerve/.gradle`.
  - It installs Scrimmage plus `./lockstep`, builds the agent, and runs `pytest -m java`.
  - Java tests are marked `java` and skip only when `SCRIMMAGE_SKIP_JAVA=1`. The Python job sets that, and
    the Java job requires the tests to run.
