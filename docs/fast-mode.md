# Fast mode (CTRE robots)

Phoenix 6 simulates Talons, CANcoders and Pigeons on the wall clock, so a robot that uses them runs in real time
under Scrimmage: never faster, and never exactly the same twice. CTRE confirmed in October 2026 that this won't
change soon. Fast mode is the way round it for CI and quick iteration. The robot program swaps its Phoenix IO
classes for small stand-ins built on Scrimmage's fast-mode library, which runs Talon-style control on the robot's
simulated clock. The robot then runs in lockstep, at any speed, and repeats exactly.

The same robot code runs both ways:

| | Phoenix mode (the default) | Fast mode |
|---|---|---|
| Robot uses | its real IO classes (Phoenix 6) | its Scrimmage IO classes (the fast-mode library, WPILib only) |
| Device behavior | Phoenix's own simulation: your real Talon configs, CAN timing | Scrimmage's Talon-style control, built from CTRE's public docs, from the same configs |
| Clock | wall clock (real time), never faster | lockstep at any `--speed` (or real time if asked) |
| Repeatable | close, not identical | yes: the same run gives the same `run.csv` and report |
| Use it for | checking against Phoenix's own simulation | CI and fast iteration on autos, mechanisms and geometry |

Nothing changes on the real robot: `Scrimmage.fastMode()` is false on a roboRIO, so the robot always builds its
Phoenix IO there. Fast mode is a simulation of a Talon, not a Talon: read [What fast mode doesn't model](#what-fast-mode-doesnt-model)
before trusting a result that only fast mode has given.

`examples/java_fastmode` is a complete sample: AdvantageKit's TalonFX swerve template (v26.0.2) with a two-Kraken
elevator and an arm on its carriage added. Its auto drives a 2 m PathPlanner path, raises the elevator with Motion
Magic, then swings the arm to 45 degrees.

```sh
scrimmage run scenarios/fastmode_auto.toml            # fast mode: lockstep, about 3x real time here
scrimmage run scenarios/fastmode_auto_phoenix.toml    # Phoenix mode: the same checks, in real time
```

## Setting up a robot project

Fast mode suits robots written AdvantageKit-style, with each subsystem's hardware behind an IO interface: the
real IO class (Phoenix) and a fast-mode IO class (Scrimmage) implement the same interface.

1. **Build the library:** `scrimmage java build` (needs the WPILib JDK) builds the fast-mode library as well as
   the agent. Run it again after upgrading scrimmage.
2. **Add the vendordep:** `scrimmage java vendordep path/to/robot-project` writes two things into the project:
   - `scrimmage-maven/`: the library as a small Maven repository;
   - `vendordeps/Scrimmage.json`: a WPILib vendordep whose `mavenUrls` points at that folder by relative path
     (`"scrimmage-maven"`).

   **Commit both.** Then any computer builds and deploys the robot without Scrimmage installed. WPILib's
   `.gitignore` ignores `*.jar`, so add an exception for the library first:

   ```
   !scrimmage-maven/**/*.jar
   ```

   Re-run the command after upgrading scrimmage: `ScrimmageCANrange` needs library 0.4.0 or later (a build error
   "cannot find symbol ScrimmageCANrange" means the vendored library is older: re-run it). (While Scrimmage is private, the library travels in your
   repository; once it's public, the vendordep will point at a hosted Maven repository instead.)
3. **Choose the IO in `RobotContainer`** with `Scrimmage.fastMode()` (true when Scrimmage sets
   `SCRIMMAGE_MODE=fast`). From the sample:

   ```java
   case SIM:
     if (Scrimmage.fastMode()) {
       drive = new Drive(new GyroIOScrimmage(),
           new ModuleIOScrimmage("fl", TunerConstants.FrontLeft), /* fr, bl, br */ ...);
       elevator = new Elevator(new ElevatorIOScrimmage());
     } else {
       drive = new Drive(new GyroIOPigeon2(),
           new ModuleIOTalonFX(TunerConstants.FrontLeft), /* fr, bl, br */ ...);
       elevator = new Elevator(new ElevatorIOTalonFX());
     }
     break;
   ```

   The sample's simulation always uses an IO pair that Scrimmage can move: AdvantageKit's own `ModuleIOSim` (a
   `DCMotorSim` physics model inside the robot) is gone, since Scrimmage is the physics.
4. **Write the fast-mode IO classes** around the library's devices. Copy them from the sample:
   - `subsystems/drive/ModuleIOScrimmage.java`: a drive Kraken, a steer Kraken closing its loop on a CANcoder;
   - `subsystems/drive/GyroIOScrimmage.java`: the gyro;
   - `subsystems/elevator/ElevatorIOScrimmage.java`: a lead Kraken with Motion Magic and an opposed follower;
   - `subsystems/arm/ArmIOScrimmage.java`: one Kraken with Motion Magic and Arm/Cosine gravity.

   Each is a few lines: build the devices from your configs, read them in `updateInputs`, send requests in the
   setters. Report one odometry sample per loop stamped with `Timer.getFPGATimestamp()` (as `ModuleIOScrimmage`
   does) rather than registering with `PhoenixOdometryThread`: that thread runs on the wall clock, which would make
   runs differ.
5. **Keep one set of constants.** The sample's `ScrimmageConstants` builds each Talon's `TalonFXConfiguration`
   once; the real IO applies it, and `ScrimmageConstants.krakenSettings(config)` turns it into the fast-mode
   settings. Your gains, limits, ratios and inversions then can't drift apart between the two modes. Building a
   Phoenix config object creates no device, so fast mode still has no Phoenix devices.
6. **Write two Scrimmage configs**, because bindings don't change with the mode:
   - one for fast mode, with `[robot_program] sim_mode = "fast"` and the fast-mode device names
     (`configs/fastmode.toml`);
   - one for Phoenix mode, binding Phoenix CAN ids (`configs/fastmode_phoenix.toml`).

   `--sim-mode fast|phoenix` on `run` and `serve` overrides `sim_mode`. It only sets the robot's environment
   variable, so pair it with the config that binds the matching devices.

### Bindings

Fast-mode devices are named by your code, not numbered. In a `[[part]]`'s `ids`:

```toml
ids = { drive = "SimMotor:fl_drive", steer = "SimMotor:fl_steer", encoder = { scrimmage = "fl_encoder", offset = -0.15234375 } }
ids = { gyro = "SimGyro:pigeon" }
ids = { lift = ["SimMotor:elevator_lead", { scrimmage = "elevator_follower", invert = true }] }
```

`"SimMotor:<name>"`, `"SimEncoder:<name>"` and `"SimGyro:<name>"` are short for `{ scrimmage = "<name>" }` (a table
takes the other binding keys). A distance sensor binds its `ScrimmageCANrange` the other way round, from the
`[[sensor]]`: `scrimmage = "<name>"` ([CAN distance sensors](sensors.md#can-distance-sensors)). They follow the same conventions as Phoenix bindings:

- An encoder's `offset` is where the magnet sits, in rotations, when the joint is at zero. For wheels straight
  ahead to read 0, use minus the robot's `MagnetOffset`, the same value in both configs.
- A follower configured *opposed* turns the other way from its leader, so on a real mechanism it's mounted
  mirrored: bind it with `invert = true` in both modes.
- `neutral` comes from the device (`KrakenSettings.brake`), so fast-mode motors don't need it. Phoenix motors do
  (`neutral = "brake"`): Phoenix doesn't tell scrimmage.
- A bound device the robot never creates is reported as a fast-mode problem. With `--sim-mode fast`, any Phoenix
  6 device the robot still creates is reported too, since one is enough to put the run back on the wall clock.

## The library

The library (Java package `scrimmage.sim`, WPILib only) loads no Phoenix class. Its devices are HAL SimDevices that
Scrimmage reads and writes like any other. Where Scrimmage isn't running (a roboRIO, a unit test without the
HAL), they do nothing and read 0.

| Class | What it is |
|---|---|
| `Scrimmage.fastMode()` | true when Scrimmage runs the robot in fast mode |
| `KrakenSettings` | Talon-style settings as plain numbers: public fields and `withX` setters, `copy()` |
| `ControlRequest` | `voltage`, `dutyCycle`, `position`, `velocity`, `motionMagic`, `motionMagicVelocity`, `neutral`; `withFeedforward(volts)` |
| `ScrimmageKraken(name, settings)` | a Kraken X60 with Talon-style control: `setControl`, `follow(leader, opposed)`, `useRemoteEncoder(cancoder, fused)`, `setPosition`, `getPosition`, `getVelocity`, `getMotorVoltage`, `getStatorCurrent`, `getSupplyCurrent` |
| `ScrimmageCANcoder(name, magnetOffsetRot, clockwisePositive)` | `getAbsolutePosition()` in [-0.5, 0.5), `getPosition()`, `getVelocity()` |
| `ScrimmageGyro(name)` | `getYawDegrees()` (counter-clockwise positive), `getYawRateDegPerSec()`, `setYaw(deg)` |
| `ScrimmageCANrange(name)` | `getDistanceMeters()` (whole millimeters), `getIsDetected()`, `setProximityThreshold(meters)`, `setProximityHysteresis(meters)` |

Requests and readings are in mechanism units, after the ratios: rotations and rotations per second. A Kraken
copies its settings when it's built, so changing a `KrakenSettings` afterwards changes nothing.

### Settings: Phoenix config to `KrakenSettings`

Each `KrakenSettings` field means what the `TalonFXConfiguration` field of the same name means in CTRE's
documentation. `ScrimmageConstants.krakenSettings(config)` in the sample copies them:

| `TalonFXConfiguration` | `KrakenSettings` |
|---|---|
| `Slot0.kP`, `kI`, `kD`, `kS`, `kV`, `kA`, `kG` | `kP`, `kI`, `kD`, `kS`, `kV`, `kA`, `kG` (slot 0 only) |
| `Slot0.GravityType` `Elevator_Static` / `Arm_Cosine` | `gravityType` `ELEVATOR_STATIC` / `ARM_COSINE` |
| `Slot0.GravityArmPositionOffset` | `gravityArmPositionOffset` |
| `Slot0.StaticFeedforwardSign` `UseVelocitySign` / `UseClosedLoopSign` | `staticFeedforwardSign` `VELOCITY_SIGN` / `CLOSED_LOOP_SIGN` |
| `ClosedLoopGeneral.ContinuousWrap` | `continuousWrap` |
| `MotionMagic.MotionMagicCruiseVelocity`, `Acceleration`, `Jerk` | `motionMagicCruiseVelocity`, `motionMagicAcceleration`, `motionMagicJerk` |
| `CurrentLimits.StatorCurrentLimit`, `StatorCurrentLimitEnable` | `statorCurrentLimit`, `statorCurrentLimitEnable` |
| `CurrentLimits.SupplyCurrentLimit`, `SupplyCurrentLimitEnable` | `supplyCurrentLimit`, `supplyCurrentLimitEnable` |
| `SoftwareLimitSwitch.Forward/ReverseSoftLimitThreshold`, `...Enable` | `forward/reverseSoftLimitThreshold`, `...Enable` |
| `Voltage.PeakForwardVoltage`, `PeakReverseVoltage` | `peakForwardVoltage`, `peakReverseVoltage` |
| `MotorOutput.PeakForwardDutyCycle`, `PeakReverseDutyCycle` | `peakForwardDutyCycle`, `peakReverseDutyCycle` |
| `MotorOutput.Inverted` `Clockwise_Positive` | `inverted = true` |
| `MotorOutput.NeutralMode` `Brake` | `brake = true` |
| `Feedback.SensorToMechanismRatio`, `RotorToSensorRatio` | `sensorToMechanismRatio`, `rotorToSensorRatio` |
| `Feedback.FeedbackSensorSource` `RemoteCANcoder` / `FusedCANcoder` | `kraken.useRemoteEncoder(cancoder, false)` / `(cancoder, true)` |
| the request type: `...Voltage` / `...DutyCycle` | `outputType` `VOLTAGE` / `DUTY_CYCLE` (the gains' units) |
| `CANcoderConfiguration.MagnetSensor.MagnetOffset`, `SensorDirection` | `ScrimmageCANcoder(name, magnetOffsetRot, clockwisePositive)` |
| `Follower(leaderId, MotorAlignmentValue.Opposed)` | `follower.follow(leader, true)` |

**Defaults differ.** A fresh `KrakenSettings` has its current limits disabled (0 A), while a real Talon ships
with them enabled (and a fresh `TalonFXConfiguration` carries those defaults). Converting from a
`TalonFXConfiguration`, as the sample does, keeps Phoenix's defaults; if you fill in a `KrakenSettings` by hand,
set the limits yourself. The sample sets its elevator's limits explicitly in both modes.

Phoenix settings with no fast-mode equivalent are ignored: slots 1 and 2, Motion Magic Expo gains,
torque-current limits, the supply current lower limit and time, limit switches, and so on.

### Followers

`follow(leader, opposed)` copies the leader's applied voltage every loop: the follower turns the same physical
way as its leader, or the opposite way when `opposed`, whatever its own `inverted` (as CTRE documents for its
`Follower` request). From the moment it follows, a follower's `getPosition` and `getVelocity` read in its
leader's direction (flipped when opposed), not its own `inverted` direction. Any `setPosition` offset it had is
kept as it was, not translated. `setControl` on a follower stops it following.
A follower whose leader is closed gets 0 V (neutral).

### Timing

The library's control loop is one shared HAL notifier every 5 ms on the robot's simulated clock. The sim HAL wakes
every notifier that comes due at the same moment on its own thread, so a 5 ms control tick and the robot's 20 ms
loop (which coincide every fourth tick) could run in either order. The library makes the order not matter:

- Each `setControl`, `follow`, `useRemoteEncoder` and `setPosition` is stamped with the robot's FPGA time (it
  doesn't move while the robot code runs). A tick at time *t* uses the newest call stamped before *t*. A call made
  at the same time as a tick takes effect at the next tick: **a request can wait up to one tick (5 ms)** before the
  Talon-style control sees it.
- What the robot reads back from the control loop (`getMotorVoltage`, `getStatorCurrent`, `getSupplyCurrent`) at
  time *t* comes from the last tick before *t*, never from a tick at *t*. Positions and velocities come from the
  physics, which Scrimmage writes between steps.
- The disabled check reads the enable state Scrimmage set, not the robot loop's refreshed copy of it. So the
  motors can see an enable or disable before robot code does. When Scrimmage enables the robot between two robot
  loops, the motors run their last request (often neutral, from disabled) for up to one robot loop before robot
  code sees the enable and sends new requests. On a disable they go neutral first, as a real Talon does. The window
  is zero when the mode changes on a robot-loop boundary (a config `step` equal to the robot's 20 ms loop makes every
  change land on one); with the default 5 ms step it can be up to 15 ms.

So the same run gives the same result however the threads are scheduled. A real Talon also sees a request a
little after the robot sends it (CAN), so the extra latency is of the same kind, just not the same size.

### Arms

Use Arm/Cosine gravity (`Slot0.GravityType = Arm_Cosine`) and make the mechanism position 0 when the arm is
horizontal, as CTRE requires: then kG holds the arm level and fades to nothing straight up or down. Set
`SensorToMechanismRatio` to the gearing, so positions are arm rotations, and seed the position at boot from where
the arm starts (`setPosition`). If the arm's center of gravity isn't along it (an L-shaped arm and intake), set
`Slot0.GravityArmPositionOffset` to the angle from the arm's zero to its center of gravity. Scrimmage's stock
`arm` part has 0 degrees horizontal forward and positive raising the tip, like the sample's
`subsystems/arm/ArmIOScrimmage.java`.

### Remote and fused CANcoders

`useRemoteEncoder(cancoder, fused)` closes the loop on the CANcoder's position (divided by
`sensorToMechanismRatio`). As CTRE documents the feedback sources:

- **Fused** (`fused` true, for `FusedCANcoder` and `SyncCANcoder`; also what `useRemoteEncoder(cancoder)` does):
  the velocity comes from the rotor, divided by `rotorToSensorRatio` and `sensorToMechanismRatio`. That's how
  `rotorToSensorRatio` matters. Since position comes from the CANcoder and velocity from the rotor, the CANcoder's
  direction (`clockwisePositive`) and the motor's `inverted` must agree, as Phoenix requires for a fused CANcoder:
  if they don't, position and velocity have opposite signs and the loop fights itself.
- **Remote** (`fused` false, for `RemoteCANcoder`): the velocity comes from the CANcoder too (divided by
  `sensorToMechanismRatio`), and the rotor isn't used.

`setPosition` then offsets only the Kraken's reading, not the CANcoder's. The sample's `ModuleIOScrimmage` passes
`fused` from its `TunerConstants` feedback source.

## What fast mode doesn't model

Fast mode is good enough to catch an auto that no longer ends where it should, or a mechanism that no longer
gets there in time. It is not a Talon. The [sample's scenarios](../scenarios/fastmode_auto.toml) agree with
Phoenix mode to within about 1 cm (pose and elevator height) and a fraction of a degree (arm angle), but a robot
tuned close to its limits can pass in one mode and fail in the other. When it matters, check in Phoenix mode.

**Timing**
- The control loop runs every 5 ms (one shared HAL notifier, on the robot's simulated clock). The real Talon
  runs at 1 kHz. Motion Magic profiles step in 1 ms substeps inside each 5 ms update.
- There's no CAN latency or status-frame timing: a request takes effect at the first 5 ms update after it (up to
  one update later when they coincide; see [Timing](#timing)), and position and velocity readings are the latest
  physics values, never stale. Phoenix's update frequencies, `waitForAll` and timestamps don't exist here.

**Current**
- Current comes from the Kraken X60 model in WPILib's published constants (`DCMotor.getKrakenX60`):
  stator current = (V - back-EMF) / R, supply current = stator x |V| / supply voltage. There's no FOC.
- Current limits are approximate. CTRE documents that the limits reduce output, not how. Here the stator limit
  sets the voltage that puts the model's current exactly at the limit, and the supply limit (only while motoring)
  solves for the voltage that puts supply current at its limit. Real current limiting reacts over time; this is
  instantaneous. The supply limit's lower limit and time aren't modeled.
- The stator limit applies after peak output, the supply-voltage cap and soft limits, so while braking hard it
  can push the output past a peak, through a soft limit, or past the supply voltage (when the motor is back-driven
  past its free speed, the voltage that holds the current at the limit can exceed the supply).

**Output**
- Output is clamped to the peak output (default ±16 V), then capped at the supply voltage (±`vbus`, the battery
  voltage Scrimmage simulates): a motor can't be driven harder than its supply. The cap applies before current
  limits, so the reported currents match the voltage actually applied. Duty-cycle requests already scale by the
  supply voltage.
- Soft limits stop output that drives further past the threshold (strictly past it); output back toward the
  range is allowed.
- While the robot is disabled, output is neutral (0 V, brake or coast).

**Formula choices** (CTRE documents what each gain means, but not the formula, the error units or kI handling):
- Output = kS·sign + kV·velocity reference + kA·acceleration reference + kG·(1, or cos(2π·(position + offset)) for
  Arm/Cosine) + kP·error + kI·∫error dt + kD·d(error)/dt + feedforward, in volts (or duty cycle x supply voltage).
- Errors are in mechanism rotations (position) or rotations per second (velocity). Arm/Cosine takes 0 rotations
  of the mechanism as horizontal. CTRE documents `GravityArmPositionOffset` as an offset "applied to the position
  of the arm, within (-0.25, 0.25) rot, before calculating the output of kG", but not its sign: fast mode adds it
  to the position, and holds it to that range.
- The integrator has no anti-windup, and the derivative acts on the error, so a step in the target gives a
  derivative kick. The integrator, derivative and any Motion Magic profile restart when the request's kind
  changes, not when only its target does.
- Motion Magic's profile is Scrimmage's own generator: trapezoidal from cruise velocity and acceleration, an
  S-curve when jerk is above 0. A change of target re-plans from the current setpoint. Cruise velocity or
  acceleration 0 holds the profile where it is.
- Continuous wrap takes the shortest way round one mechanism rotation.

**CANrange.** A distance sensor with `ctre = "CANrange:<id>"` ([sensors.md](sensors.md#can-distance-sensors)) works
in Phoenix mode only; in fast mode the robot builds a `ScrimmageCANrange(name)` and the sensor says
`scrimmage = "<name>"`. `getDistanceMeters()` is the distance in whole millimeters, as Phoenix keeps it.
`getIsDetected()` latches as Phoenix's does (phoenix6 26.3.0): it starts true, turns false when the distance goes
above the threshold plus the hysteresis, and true when it goes below the threshold minus the hysteresis. Defaults
are Phoenix's, 0.4 m and 0.01 m; the IO class copies its `CANrangeConfiguration.ProximityParams` with
`setProximityThreshold` and `setProximityHysteresis`. The latch looks at the distance when `getIsDetected()` is
called, so call it every loop; and where Phoenix switches exactly at the threshold, a reading within 1 cm of it may
differ between the modes.

**Not modeled at all:** Phoenix Pro features (TorqueCurrentFOC requests, Motion Magic Expo, Dynamic Motion Magic,
FOC commutation, fused or synced CANcoder behavior beyond the above), slots 1 and 2, limit switches, the
Pigeon's pitch and roll, CANcoder quantization (1/4096 rotation), CANivore timing, device faults and brownout
behavior. `ModuleIOScrimmage` rejects TorqueCurrentFOC closed-loop output.

**Console:** Gradle's own console passes a task's output on in batches, seconds behind at first, which a fast
run can outrun. So under Scrimmage the Gradle init script writes the robot's console to a file that Scrimmage reads
as it grows, and Scrimmage holds the simulation for 0.25 s of wall time before a step's `console` checks (and before
the "no crash in console" check) so the last lines get through. Run by hand, `./gradlew simulateJava` prints as usual.

## The sample robot

`examples/java_fastmode` is AdvantageKit's `talonfx_swerve` template project (v26.0.2, by Littleton Robotics;
BSD-style license in `AdvantageKit-License.md`, its notices kept in each file). The changes:

- `TunerConstants`: SDS MK4i L2 ratios and wheel and drive gains that suit them (the Scrimmage configs build MK4i
  modules), CAN ids and encoder offsets as the template has them.
- `ScrimmageConstants` (new): the elevator's and the arm's constants and the Phoenix configs for every Talon (the template's
  module config code moved here), plus `krakenSettings(config)`.
- `ModuleIOTalonFX` builds its configs with `ScrimmageConstants`; `ModuleIOSim` is removed.
- New: `ModuleIOScrimmage`, `GyroIOScrimmage`, the elevator subsystem (`Elevator`, `ElevatorIO`, `ElevatorIOTalonFX`,
  `ElevatorIOScrimmage`) and the arm subsystem (`Arm`, `ArmIO`, `ArmIOTalonFX`, `ArmIOScrimmage`).
- `RobotContainer` picks the Scrimmage IO when `Scrimmage.fastMode()`, the Phoenix IO otherwise, and defaults its
  auto chooser to "Drive and Raise": the "Two Meters" path, then the `RaiseElevator` (0.75 m) and `RaiseArm`
  (45 degrees) named commands.
- The vendordep (`scrimmage-maven/`, `vendordeps/Scrimmage.json`) is committed, with the `.gitignore` exception.
- A shooter for Scrimmage's test range (below): `Shooter`, `ShooterIO`, `ShooterIOTalonFX`, `ShooterIOScrimmage`,
  the generated `AimTable` and `ShootCommands`; `Drive.getChassisSpeeds()` is public, for shooting on the move.
- Autos that drive over the 2026 BUMP (below): `DriveCommands.driveStraight` and the bump constants in
  `ScrimmageConstants`.

### Shooting on the test range

The sample also drives a made-up shooting robot on the 2026 test range (not any team's): the same swerve drive with
a two-lane hooded launcher (two Krakens on the wheel shaft, a hood on its own Kraken), a two-stage feeder (hopper
floor, feeder), an intake roller and two beam breaks (DIO 0 halfway along the feeder, DIO 1 at the launcher's
entry). `configs/fastmode_range.toml` binds it in fast mode, `configs/fastmode_range_phoenix.toml` by CAN id
(Talons 11 to 16). The robot code builds the shooter whatever the config, so on the sample's other configs
(`fastmode.toml`, `fastmode_phoenix.toml`) its devices go unbound: Phoenix mode simulates six more Talons with no
physics behind them. Those configs list the shooter's devices in `[robot_program] not_modeled`, so neither mode
warns about them (see the README's robot program section).

`Shooter` looks the wheel speed and hood angle up in `AimTable` by distance to the alliance's hub. On the move it
aims at a virtual target, the hub less the robot's velocity times the table's flight time (looked up twice), and
the drive turns to face it. It feeds only within the table's distances (1.5 to 5.75 m; it holds fire beyond), with the wheels within 2%
of their target (for 0.06 s), the hood within 1 degree and the heading within 2 degrees, and pauses 0.2 s after
each ball enters the launcher while the wheels recover. The environment variable `SCRIMMAGE_AUTO` picks the auto (the
scenarios set it with `robot_env`; without it, "Drive and Raise" as before):

- `Shoot Left`, `Shoot Right`: from the range's `blue_left` / `blue_right` start, the 8 preloaded FUEL, standing.
- `Shoot Moving`: from `blue_center`, along a line 2 m in front of the hub firing the preload on the move, through
  the left TRENCH lane (beside the BUMP, which its 2 cm bumpers can't climb) into the NEUTRAL ZONE FUEL with the
  intake running, then back toward the hub, firing what it picked up from the hub's far side.

```sh
scrimmage run scenarios/fastmode_shoot_left.toml       # also _right, _moving; _speed: Shoot Moving, no floor FUEL
scrimmage run scenarios/fastmode_shoot_phoenix.toml    # Shoot Left in Phoenix mode: fired is checked, the score recorded
```

The scenarios check the firing and the score with `by_time` and read the robot's own console line, and each report
has the per-ball table ([the test range doc](test-range.md#the-report-the-viewer-and-networktables)). Fast mode
repeats exactly, so its thresholds are the measured values: Shoot Left and Right fire all 8 preloaded FUEL by about
2.0 s into auto and score all 8 (the last about 3.2 s in); Shoot Moving fires the 8 preloads on the move at 1.1 to 2
m/s and scores 4 of them by 3.7 s, then collects 16 from the NEUTRAL ZONE and fires them standing, 13 of which score
(17 in all, the last as auto ends). **Phoenix mode** runs on the wall clock, so the order of the robot loop and the
physics differs every run: it fired all 8 every time, but scored 2 to 8 of them (0 to 2 on a busy machine, which
can't keep up in real time), because its wheels leave about 1 rps slower and a lane's ball can land on the funnel's
rim. So that scenario checks that all 8 were fired and records the score without a minimum.

The shooter shares the battery with the drive: the bus voltage is solved within each step from every motor's
current (the drive's and the shooter's), so a heavy combined draw sags it the way a real battery does. A
turn-acceleration limit in `ShootCommands` (`MAX_TURN_ACCEL`) keeps the swerve from slipping its wheels; it's robot
practice, not a workaround for the sim.

`AimTable.java` is made by the launcher model from the robot's config; after changing the launcher, regenerate it
(from the repository root):

```sh
scrimmage launcher aim configs/fastmode_range.toml --goal hub_blue --distances 1.5:6.0:0.25 \
    --java examples/java_fastmode/src/main/java/frc/robot/subsystems/shooter/AimTable.java \
    --java-class frc.robot.subsystems.shooter.AimTable
```

It takes several minutes (see [the aiming-table command](test-range.md#aiming-tables-scrimmage-launcher-aim)).
Distances beyond what the launcher can reach are marked unreachable and left out; the robot code holds fire beyond
the table instead of clamping to its last row. Spotless leaves the file as made (`build.gradle` excludes it), and
`tests/test_range_e2e.py` regenerates its first rows and checks they're the committed ones. The rows stop at the
launcher's reach (6 m is beyond it).

### Over the 2026 BUMP

Two more autos drive the sample's elevator-and-arm robot straight over the 2026 BUMP on Scrimmage's `2026-bump`
range (`configs/fastmode_bump.toml`: the `fastmode.toml` robot with its bumpers 8 cm up, high enough to climb the
bump's 15 degree ramps). They start 1.5 m before the bump and drive 4 m, on past a row of FUEL:

- `Over the Bump`: at `BUMP_SPEED`, 2 m/s.
- `Over the Bump, Elevator Up`: raises the elevator to 0.9 m first, then drives at `BUMP_SPEED_ELEVATOR_UP`, 1 m/s.

The speeds are the limits `scrimmage terrain sweep configs/fastmode_bump.toml --element bump` gives the robot
([docs/terrain.md](terrain.md)). Their scenarios fail if the robot leaves the ground by more than 5 cm or tips over,
and with the elevator up if it tilts more than 30 degrees. In simulation the environment variable
`SCRIMMAGE_BUMP_SPEED` (m/s) replaces the speed, so a scenario can try another with `robot_env`:
`tests/test_terrain_e2e.py` drives it at 3 m/s and checks the ground-gap check fails.

```sh
scrimmage run scenarios/fastmode_bump.toml
scrimmage run scenarios/fastmode_bump_elevator_up.toml
```

## License position

CTRE's support told us (October 2026) that writing new implementations of their C API is a derivative work and
prohibited, that using C functions outside their public headers could count as reverse engineering, and that
modifying their Java bytecode is prohibited. Fast mode stays clear of all three:

- The library loads no Phoenix class, implements no Phoenix API and takes no CTRE type as input: its settings are
  plain numbers. It is not a Phoenix replacement; your robot code chooses it through your own IO classes.
- Its Talon-style behavior is built only from CTRE's public documentation (the
  [closed-loop overview](https://v6.docs.ctr-electronics.com/en/stable/docs/api-reference/device-specific/talonfx/closed-loop-requests.html)
  and [Motion Magic](https://v6.docs.ctr-electronics.com/en/stable/docs/api-reference/device-specific/talonfx/motion-magic.html)
  pages). Where the docs are silent, it makes its own choice and says so (above). Nothing in it comes from
  observing or decompiling Phoenix.
- Converting a `TalonFXConfiguration` to `KrakenSettings` happens in your robot code (the sample's
  `ScrimmageConstants`), which uses Phoenix's public Java API as any robot does.
