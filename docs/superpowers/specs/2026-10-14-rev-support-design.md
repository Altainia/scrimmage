# REV hardware support (sub-project 6): design

## Context

Scrimmage runs unmodified robot programs against MuJoCo physics through WPILib's HALSim WebSocket, in lockstep
(faster than real time, repeatable) when the robot program allows it. Robot code with CTRE Phoenix 6 devices is
limited to 1x because Phoenix's simulation runs on the wall clock (CTRE confirmed, 2026-10-14). The user's team is
CTRE-only; REV support is for other teams.

A spike (2026-10-14) found:

- REVLib's SPARK simulation is caller-driven: `SparkSim.iterate(velocity, vbus, dt)` (Java, `com.revrobotics.spark`)
  runs REV's simulated controller (closed loop, MAXMotion, filtering) for the caller's `dt`, so it follows sim time
  and doesn't block lockstep.
- It reads and writes a WPILib SimDevice named `SPARK MAX [<id>]` / `SPARK Flex [<id>]` with values including
  `Applied Output`, `Position`, `Velocity`, `Bus Voltage`, `Motor Current`, `Setpoint`, `Control Mode`.
- `SparkSim` must be built from the robot program's own `SparkBase` instance; there is no public device registry.
- `iterate` adds hall-sensor noise from an unseeded `java.util.Random` (`com.revrobotics.sim.NoiseGenerator`).
- `SparkSim` has no follower handling.
- REVLib's Java sources (REVLib-java 2026.0.5, published as a sources jar) are BSD-3-Clause (Copyright REV Robotics):
  reading them and modifying their bytecode at load time is permitted. The native driver libraries are separate and
  out of scope.

Decisions made with the user:

- Java robot code first (RobotPy later).
- Scope: SPARK MAX and SPARK Flex with NEO, NEO 550, NEO Vortex; duty cycle/voltage output and the built-in encoder;
  closed loop (PID, MAXMotion); the absolute encoder on the data port; followers and limit switches; a MAXSwerve stock
  part.
- Approach A: the Scrimmage Java agent registers each SPARK the robot program creates and drives REV's own
  `SparkSim` every robot loop with Scrimmage's physics values. Robot code is unchanged.

## Goals

1. A Java robot with SPARKs runs under Scrimmage with no robot-code changes, in lockstep at any `--speed`, with
   repeatable results.
2. REV's own simulated controller behavior (PID, MAXMotion, filtering) drives the physics.
3. Configs and parts name REV devices as simply as CTRE ones; a MAXSwerve robot can be made from stock parts.
4. Problems (an unbound or missing SPARK, missing REVLib or agent) are reported in plain words.

## Non-goals

- RobotPy (robotpy-rev) robots (later).
- REV devices other than SPARK MAX/Flex (Servo Hub, Color Sensor, MAXSpline encoder on DIO is already plain WPILib).
- Alternate encoder (SPARK MAX) and external encoder (SPARK Flex) ports, analog sensor port, simulated faults.
- Running REV devices without the Scrimmage agent.
- Windows/macOS.

## Design

### What students write and see

- `[[part]]` `ids` short forms: `"SparkMax:<id>"` and `"SparkFlex:<id>"` for motor slots; `"SparkMaxAbs:<id>"` and
  `"SparkFlexAbs:<id>"` for encoder slots (the absolute encoder on that SPARK's data port).
- Table form: `{ rev = "SparkMax:3", neutral = "brake", invert = false, limits = false }`. `neutral` defaults to
  "brake" (REV's factory idle mode); `invert` models a motor mechanically reversed against its joint; `limits = true`
  drives the SPARK's forward/reverse limit-switch inputs from the joint's range limits (pressed at or beyond the
  limit).
- Hand-written `[[motor]]` / `[[encoder]]` bindings take `rev = "SparkMax:3"` / `rev = "SparkMaxAbs:3"` (like
  `ctre = "TalonFX:1"`).
- The motor model comes from the part or binding (`NEO`, `NEO550`, `NeoVortex`); the default is NEO for a SPARK MAX
  and NeoVortex for a SPARK Flex.
- Followers: robot code configures them as on a real robot; Scrimmage mirrors a leader's applied output (inverted
  if configured) onto its followers, so a follower gets its own motor binding on the same gearbox. One binding with
  `count` (implicit followers) also still works.
- Stock part `maxswerve`: a REV MAXSwerve module; drive ratio choices 5.50, 5.08, 4.71 (12T, 13T, 14T pinion; Low,
  Mid, High), steer ratio 9424/203, NEO drive and NEO 550 steer; slots `drive`, `steer`, `encoder`. `Scrimmage
  edit --new swerve-rev` (alongside `swerve` and `differential`) makes a chassis plus four MAXSwerve modules with the
  CAN IDs of REV's MAXSwerve Java template.
- REV robots run in lockstep at any `--speed`; the 1x notice appears only when a Phoenix 6 device is present, and
  names only the Phoenix devices.

### Architecture

Robot side — the Java agent (`scrimmage/java/agent/`), extended:

- ASM hook at the end of `com.revrobotics.spark.SparkBase`'s constructor: `ScrimmageRev.register(this)`. The first
  registration starts the REV helper.
- `ScrimmageRev` reaches REVLib only through reflection (the agent is compiled without REVLib, so it works across
  REVLib versions and robots without REV pay nothing). Per SPARK it creates `SparkMaxSim` / `SparkFlexSim` with the
  matching WPILib `DCMotor` (from the device model: NEO for MAX, Vortex for Flex, replaced by the motor named by
  Scrimmage when it says so), a `SparkAbsoluteEncoderSim` and forward/reverse `SparkLimitSwitchSim`, and a HAL
  SimDevice `ScrimmageREV:SparkMax[<id>]` / `ScrimmageREV:SparkFlex[<id>]` (HALSim type `ScrimmageREV`, device `SparkMax[<id>]` / `SparkFlex[<id>]`) with input values `velocity` (motor shaft RPM), `vbus` (V), `current` (A), `position` (motor rotations since start; sets the built-in encoder each loop),
  `absRotations` (absolute-encoder rotations, before REV's conversion factor), `fwdLimit`, `revLimit` (bool), and
  `motor` (motor model code: 1 NEO, 2 NEO550, 3 NeoVortex, 0 keep the SPARK's default — HALSim WebSocket can't carry
  strings into SimDevice values).
- Each robot loop, from WPILib's `SimulatorJNI.registerSimPeriodicBeforeCallback` (runs with the robot's sim loop,
  so it follows sim time): set limit switches and absolute-encoder position from the inputs; for each SPARK that is
  not a follower, `iterate(velocity, max(vbus, 0.1), dt)` with `dt` the sim-time difference since the previous loop
  (`Timer.getFPGATimestamp()`; the first loop uses 0.02); then set each follower's applied output from its leader's
  (negated if configured inverted); then `setMotorCurrent(current)` so `getOutputCurrent()` matches the physics.
- The agent seeds `NoiseGenerator`'s random source with a fixed seed when the class loads.
- `SparkSim.iterate` called from anywhere but `ScrimmageRev` becomes a no-op, counted in the agent's summary line like
  the Phoenix `*SimState` setters.
- If REV's sim classes aren't found or lack the methods used, the agent prints one line `Scrimmage agent: REV sim
  unavailable: <reason>` and registers nothing more.

Scrimmage side:

- `MotorBinding` and `EncoderBinding` gain `kind = "REV"` (key `rev`), alongside `PWM`, `CANMotor` and `CTRE`.
- Motor: volts = `Applied Output` × `Bus Voltage` (from REV's `SPARK MAX/Flex [<id>]` SimDevice) × sign; writes the
  motor shaft velocity (RPM, joint velocity × gear_ratio × sign) and one motor's current (the total / count) into `ScrimmageREV:SparkMax[<id>]` (or `ScrimmageREV:SparkFlex[<id>]`),
  and the motor model code once.
- Encoder: writes the joint angle as absolute-encoder rotations (joint revolutions × encoder ratio), and the limit
  states when `limits = true`.
- The world's battery voltage goes to every bound SPARK's `vbus`.
- The bridge's device check knows REV's device names (unbound / never created reports); REV devices don't set the
  Phoenix real-time flag.
- `ids.SHORT` gains the four REV short forms; the swerve templates gain `swerve-rev`.

To verify against REVLib's source while planning: the velocity units `iterate` expects, the units of
`SparkAbsoluteEncoderSim.setPosition`, how to read a SPARK's follower leader and inversion from its config, and the
HALSim direction prefixes of REV's SimDevice values.

## Failure handling

| Situation | Behavior |
|---|---|
| Config binds `SparkMax:5`; robot code never creates SPARK 5 | warning (report, Drive tab, MCP), like today's unbound-slot warnings; no torque on that joint |
| Robot code creates a SPARK nothing binds | reported once, naming device and ID; it still runs in REV's sim |
| REVLib sim classes missing or too old | the agent's one line becomes a Scrimmage error naming the REVLib version needed (2026.0.5 or later) and how to update the vendordep |
| Agent not loaded | existing agent-missing handling, worded to include REV devices |
| `iterate` would get vbus 0 | the helper clamps to 0.1 V (Scrimmage never sends 0) |
| Student code calls `iterate` | no-op, counted in the agent summary |
| Follower whose leader isn't created | output 0; warning naming the missing leader ID |
| SPARK and Phoenix 6 devices together | 1x; the notice names the Phoenix devices only |

## Testing

- Unit (Python, no Java): REV short forms and table form; `MotorBinding`/`EncoderBinding` for REV against fake HAL
  values (applied output × vbus → volts with invert; velocity in RPM and position in rotations written back; limit
  states); device-name checks; `maxswerve` geometry, ratios and slots; the `swerve-rev` template.
- Java end-to-end (`java` marker, run by CI's Java job): `tests/rev_probe`, a small GradleRIO robot pinned to REVLib
  2026.0.5 with one SPARK MAX on an arm: duty cycle moves it; a position PID and a MAXMotion move reach their target;
  the absolute encoder reads the joint angle; the forward limit stops forward motion; an inverted follower mirrors
  its leader; a student `iterate` call is neutralized; two runs give identical logs; the run is faster than 1x.
- Example: `examples/java_maxswerve`, REV's MAXSwerve Java template (license confirmed BSD before copying), pinned to
  REVLib 2026.0.5, with a short auto; `scenarios/maxswerve_auto.toml` checks its pose in CI (as the CTRE Java swerve
  scenario does).

## Docs

- `docs/rev.md`: setup, the short forms, followers and limits, limitations, and how REV differs from CTRE (faster
  than real time, REV's own controller logic, deterministic noise).
- `docs/parts.md`: the REV short forms and `maxswerve`.
- README: a "REV robots" section.
