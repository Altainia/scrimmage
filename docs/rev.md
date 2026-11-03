# REV robots (SPARK MAX and SPARK Flex)

Scrimmage runs Java and RobotPy robot programs that use REV SPARK MAX and SPARK Flex controllers (NEO, NEO 550,
NEO Vortex) with no changes to your robot code. Closed-loop control (position and velocity PID), the built-in
encoder, the absolute encoder on the data port, limit switches and followers all work. You tell Scrimmage which
SPARK moves which joint, and it does the rest.

Contents: [Setup](#setup), [RobotPy](#robotpy), [How it works](#how-it-works), [Binding SPARKs](#binding-sparks),
[Table keys](#table-keys), [Followers](#followers), [The MAXSwerve part](#the-maxswerve-part),
[REV compared with CTRE](#rev-compared-with-ctre), [Errors and warnings](#errors-and-warnings),
[Limitations](#limitations).

## Setup

These steps are for Java robots; RobotPy robots are set up as in [RobotPy](#robotpy).

1. Your robot project needs REVLib **2026.0.5 or later** (`vendordeps/REVLib.json`). In WPILib VS Code:
   Manage Vendor Libraries, Check for Updates.
2. Build the Scrimmage Java agent once: `scrimmage java build`. The agent is versioned, so if you built it
   before REV support existed, run `scrimmage java build` again. This applies to CTRE teams too, because the
   robot program loads the same agent jar.
3. Use the usual Java robot setup (see [Java robots](../README.md#java-robots-phoenix-6): the `[robot_program]`
   section with the Gradle init script).

Try it first on the bundled example: `scrimmage run scenarios/maxswerve_auto.toml` (see
[the example](#the-maxswerve-part)).

## RobotPy

RobotPy robots that use `robotpy-rev` work too, through Scrimmage's RobotPy lockstep extension
(`scrimmage-lockstep`, the `./lockstep` folder), which carries a Python version of the Java agent's REV helper.

**Setup.**

1. Install robotpy-rev 2026.0.4 or later (below 2027): `pip install robotpy-rev`, or install Scrimmage with its
   `robotpy` extra (`pip install -e ".[robotpy]"`), which also brings RobotPy itself and commands2.
2. Install the extension: `pip install ./lockstep`. It must be version 0.6.0 or later; **reinstall it after updating
   Scrimmage** (`pip install ./lockstep` again). A RobotPy config run with `--scrimmage-lockstep` refuses to start with
   an older one and says so. A robot with valves on a REV Pneumatic Hub needs 0.7.0 or later (see
   [pneumatics.md](pneumatics.md#robot-code)).
3. Put `--scrimmage-lockstep` in `[robot_program] command`, the usual RobotPy command:
   `command = ["{python}", "-m", "robotpy", "sim", "--nogui", "--ws-client", "--scrimmage-lockstep"]`. Without it the
   REV helper isn't loaded: the run warns that the SPARKs aren't being driven and tells you to add it.

The robot console then shows `scrimmage rev helper: loaded (python 0.6.0)`. Bindings, parts and checks are the same
as for Java; everything else on this page applies.

**Create SPARKs in `robotInit` or in constructors** (your subsystems, `RobotContainer`), as RobotPy's examples do, not
at module level in `robot.py` or a file it imports. The extension loads after `robot.py` is imported, so a SPARK made
at import time is only picked up if something later builds a `SparkMaxSim` / `SparkFlexSim` for it; otherwise the run
warns "... the robot program created SPARK MAX 3 before Scrimmage's REV helper loaded (for example at import time of
robot.py): create SPARKs in robotInit or a subsystem constructor".

**Your own simulation code.** If your robot calls `SparkMaxSim.iterate` or `SparkFlexSim.iterate` itself (for
example in `_simulationPeriodic`, RobotPy's name for simulationPeriodic), those calls are ignored while Scrimmage
drives the SPARKs, and counted in a console line, `scrimmage rev helper: ignored N robot-side sim writes (...)`,
naming the first caller. Your code needs no changes; it just has no effect under scrimmage.

**Runs repeat.** REV's simulated sensor noise comes from REVLib's C++ library, which seeds it from
`std::random_device`, and robotpy-rev gives Python no way to seed it. So when Scrimmage starts a RobotPy robot with
`--scrimmage-lockstep`, it preloads a small library from the extension (`LD_PRELOAD`) that makes `std::random_device`
return the same numbers every run, so runs repeat exactly, as for Java.
A robot you start yourself (attach mode) doesn't get the library; to get it, start the robot with
`LD_PRELOAD=$(python -c "import scrimmage_lockstep; print(scrimmage_lockstep.seed_library_path())")` in its environment.

**Example.** `examples/robotpy_maxswerve` is robotpy-rev's MAXSwerve example (a RobotPy port of REV's template, at
robotpy-rev tag 2026.0.4), with its license files. It is unmodified apart from a small fix for an enum REVLib 2026
renamed, without which it crashes in `robotInit` on a real robot too. `configs/maxswerve_py.toml` maps it onto the
same stock parts as the Java example, and `scenarios/maxswerve_py_auto.toml` runs its autonomous routine:

```bash
scrimmage run scenarios/maxswerve_py_auto.toml
```

The robot drives only about 1.57 m of the 3 m path: the example's drive feedforward is 12 times too weak under
REVLib 2026 (which reads `velocityFF` as volts). That is what the code does on a real robot; its own odometry agrees
with the simulated position to about 1 cm. The example's README.md explains both points. Its gyro is WPILib's
`ADIS16448_IMU`: `device = "ADIS16448[4]", angle = "gyro_angle_z", rate = "gyro_rate_z", cw_positive = false`.

## How it works

(This section describes the Java agent; the RobotPy helper does the same, except that its runs aren't exactly
repeatable, see [RobotPy](#robotpy).) The Scrimmage Java agent notices each SPARK your robot program creates and,
every robot loop, runs REV's own simulated controller (`SparkSim.iterate`) with the speed, battery voltage and
current that Scrimmage's physics gives it. REV's simulated controller does the PID, filtering and encoder work,
exactly as it does in REV's own simulation. The joint's physics (MuJoCo) takes the SPARK's output as motor voltage.
Your robot code isn't changed.
Because REV's simulation is driven by the agent with the simulated clock, REV robots run in lockstep: faster than
real time at any `--speed`, and repeatable (REV's sensor noise is given a fixed seed; robot code that reads the wall clock can still vary, see
[Lockstep](../README.md#lockstep-repeatable-and-faster-than-real-time-runs)). If your own code calls
`SparkSim.iterate`, those calls are ignored and counted in the agent's summary line, the same as CTRE's sim
setters (unless REV simulation is unavailable, when they run as usual).

## Binding SPARKs

Parts take the short forms in an `ids` table (see [Device IDs](parts.md#device-ids)):

| Form | Slot kind | Becomes |
|---|---|---|
| `"SparkMax:3"` | motor | `[[motor]] rev = "SparkMax:3"` |
| `"SparkFlex:3"` | motor | `[[motor]] rev = "SparkFlex:3"` |
| `"SparkMaxAbs:3"` | encoder | `[[encoder]] rev = "SparkMaxAbs:3"`: the absolute encoder on that SPARK MAX's data port |
| `"SparkFlexAbs:3"` | encoder | `[[encoder]] rev = "SparkFlexAbs:3"` |

The number is the SPARK's CAN ID (0 to 62). The kind must match your robot code: `SparkMax` for a
`SparkMax` object, `SparkFlex` for a `SparkFlex`. A hand-written robot (no `[[part]]`) uses the same value in a
binding:

```toml
[[motor]]
joint = "arm"
rev = "SparkMax:3"
motor = "NEO"
gear_ratio = 60

[[encoder]]
joint = "arm"
rev = "SparkMaxAbs:3"      # the absolute encoder on SPARK 3
```

The motor defaults to a NEO for a SPARK MAX and a NEO Vortex for a SPARK Flex. A NEO 550 needs
`motor = "NEO550"` (a part's `drive_motor` and `steer_motor` parameters do this for you).

## Table keys

When a slot needs more than the ID, give a table: `{ rev = "SparkMax:3", neutral = "brake", invert = false }`.

| Key | Meaning |
|---|---|
| `rev` | The short form above. |
| `neutral` | `"brake"` (default, REV's factory idle mode) or `"coast"`. Scrimmage can't read it from your code, so set it to what your code configures. |
| `invert` | The motor is mechanically reversed against its joint. Not the same as the SPARK's inverted setting, which REV's code handles itself. |
| `limits` | `true` drives the SPARK's limit switch inputs from the joint's range (the joint needs a `range`). The forward limit is pressed at the upper end of the joint's range and the reverse limit at the lower end, regardless of whether the SPARK is inverted. |
| `offset` | Absolute encoders only: where the magnet sits, in rotations, added to the joint's position. Use it when the encoder was zeroed in a different orientation from the one the mechanism is mounted in. REVLib's own inversion and zero offset are applied on top of it. |

The `maxswerve` part's encoder slot is inverted to match REV's MAXSwerve template, which inverts the
through-bore encoder in its SPARK configuration. `configs/maxswerve.toml` shows `offset` in use.

## Followers

Followers are configured in your robot code, as on a real robot (`follow(leader)`). A follower copies its leader's
motor output (negated if the follower is configured inverted). Bind each follower as its own motor on the same
joint and gearbox, so it also adds its torque and current:

```toml
ids = { lift = ["SparkMax:3", "SparkMax:4"] }    # a slot with two motors takes a list, one ID each
```

(One binding with `count = 2` also still works: the SPARK is sent one motor's current, the total divided by the
count, which is what its smart current limit sees; logs show the total.) A follower's encoder sign follows its own inverted setting;
that detail is not tested.

## The MAXSwerve part

`swerve/MAXSwerve` is REV's MAXSwerve module: a 3 in wheel, NEO drive motor and NEO 550 steer motor. The `ratio`
parameter picks the pinion: `12T` = 5.50, `13T` = 5.08, `14T` = 4.71 (the default, and what REV's template
uses). The steer ratio is 9424/203. Its slots are `drive`, `steer` (both motor) and `encoder` (the steer SPARK's
absolute encoder). `scrimmage parts swerve/MAXSwerve` lists everything.

```bash
scrimmage edit my_robot.toml --new swerve-rev     # a chassis and four MAXSwerve modules
```

The new file has the CAN IDs of REV's MAXSwerve Java template (drive 11/13/15/17, steer 10/12/14/16), the
ADIS16470 gyro, and the encoder offsets for modules calibrated as in REV's template.

**Example.** `examples/java_maxswerve` is REV's own MAXSwerve Java template
([MAXSwerve-Java-Template](https://github.com/REVrobotics/MAXSwerve-Java-Template), BSD-3-Clause, its
`LICENSE.txt` is included) run unmodified, apart from the REVLib vendordep bumped to 2026.0.5 and `gradlew` made executable.
`configs/maxswerve.toml` maps it onto stock parts, and `scenarios/maxswerve_auto.toml` runs its autonomous routine:

```bash
scrimmage run scenarios/maxswerve_auto.toml
```

The scenario checks that the robot ends within 0.30 m of 5.0 m: it overshoots by about 0.24 m. The overshoot comes
from the template's weak drive P gain: the robot's own odometry also ends about 0.20 m past, within about 5 cm of
the simulated position.

**Gyros.** The template uses WPILib's `ADIS16470_IMU`. Bind it with a `[[gyro]]` that names the values:
`device = "ADIS16470[0]", angle = "gyro_angle_z", rate = "gyro_rate_z", cw_positive = false`
(see [Gyro bindings](parts.md#gyro-bindings)).

## REV compared with CTRE

| | REV (SPARK) | CTRE (Phoenix 6) |
|---|---|---|
| Speed | Lockstep at any `--speed`, faster than real time | Real time only, not lockstep: Phoenix's simulation runs on the wall clock (CTRE confirmed in October 2026 that Phoenix simulation supports only real time) |
| Controller logic | REV's own simulated controller (`SparkSim`), driven by the agent | Phoenix's own simulation, driven through its sim state |
| Repeatable | Java: yes (except MAXMotion; see [Limitations](#limitations)): REV's simulation follows the simulated clock and its sensor noise is seeded, so the same config gives the same log. RobotPy: yes, through a preloaded library that pins the noise's seed (see [RobotPy](#robotpy)) | No: Phoenix 6 follows the wall clock, so results are close from run to run but not identical |
| Robot code changes | None | None |

A robot with both kinds of device runs in real time, not lockstep (the notice names the Phoenix devices only).

## Errors and warnings

- **REVLib missing or too old.** The run report's "REV simulation available" check fails, and an error notice
  names REVLib 2026.0.5 or later (`vendordeps/REVLib.json`). Update the vendordep and rebuild.
- **Bound but never created.** The config binds `SparkMax:5`, but the robot program never creates it. A warning
  lists the SPARKs the robot does have, and gives a hint if the ID exists as the other kind. That joint gets no
  torque.
- **Created but never bound.** The robot program creates a SPARK nothing in the config binds. You get a warning
  naming it; it still runs in REV's simulation, with no physics behind it.
- **Agent not driving the SPARKs.** If the robot program creates bound SPARKs but the Scrimmage agent makes no
  helper for any SPARK (it isn't loaded, for example `./gradlew simulateJava` without Scrimmage's init script; it
  was disabled with `-Dscrimmage.agent.disable`; or it is too old to know REV), the warning about those SPARKs says so
  instead of pointing at the CAN IDs. Run `scrimmage java build`, and run the robot program through Scrimmage or its Gradle init script
  (`scrimmage java path`).
- **RobotPy: REV helper not loaded or too old.** A RobotPy robot run without `--scrimmage-lockstep` gets the "isn't
  driving the robot's REV SPARKs" warning with the fix: add `--scrimmage-lockstep` to `[robot_program] command`, or
  update scrimmage-lockstep (`pip install ./lockstep`). An extension older than 0.6.0 stops the run at the start
  with "reinstall it with `pip install ./lockstep`". If robotpy-rev lacks something the helper needs, the console
  says `scrimmage rev helper: REV sim unavailable: <reason>` and the "REV simulation available" check fails.

## Limitations

- Java and RobotPy only (no C++).
- RobotPy: a robot you start yourself (attach mode) only repeats with the seed library preloaded (see
  [RobotPy](#robotpy)).
- Only SPARK MAX and SPARK Flex, with the built-in encoder, the absolute encoder on the data port, limit switches,
  closed loop and followers. Not simulated: alternate/external encoder ports, the analog port, faults, the Servo
  Hub and other REV devices.
- **MAXMotion** in REVLib 2026.0.5's own simulation doesn't follow its profile and isn't repeatable. That is
  REV's code and does the same without scrimmage.
- A robot-code `setPosition` on an encoder from another thread (for example a `Notifier`) can occasionally be
  lost.
- Phoenix 6 devices on the same robot still force real time (not lockstep).
- Linux only, like the rest of the lockstep support.
