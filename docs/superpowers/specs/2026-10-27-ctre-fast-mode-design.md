# CTRE fast mode (sub-project 7): design

## Why

Scrimmage can't run robots with CTRE Phoenix 6 devices faster than real time or repeatably. Phoenix's
simulation runs on the wall clock, and CTRE has said (October 2026) that this won't change soon:
- Phoenix's simulation serves non-FRC users too, so it can't depend on WPILib's timing.
- A stepping API is low priority for them.
- Imitating or modifying Phoenix code is prohibited by their license.

The user's team (FRC 5098) uses Krakens throughout: swerve drive and mechanisms such as an elevator. They want
continuous integration that catches a code or CAD change that breaks the robot:
- an autonomous routine that no longer ends where it should;
- a mechanism that no longer reaches its target in time.

Those checks need runs that are fast and repeatable. High fidelity to the real world isn't required for them.

The team will write next season's code (fresh each year) with AdvantageKit, which puts each subsystem's hardware
behind an interface ("IO layer") with a real implementation and a simulated one. CTRE itself suggested that kind
of wrapping as the alternative.

## What it is

Two ways to run the same AdvantageKit-style robot under Scrimmage:

| | Phoenix mode | Fast mode |
|---|---|---|
| Robot uses | its real IO implementations (Phoenix) | Scrimmage's simulated IO implementations (WPILib only) |
| Device behavior | Phoenix's own simulation: the team's real Talon gains and limits, CAN latency | Scrimmage's Talon-style control, built from CTRE's public docs |
| Clock | wall clock (real time), never faster | lockstep at any `--speed`, or real time if asked |
| Repeatable | close, not identical | yes: the same run gives the same log |
| Purpose | checking fidelity, as far as Phoenix's simulation allows | CI and fast iteration on autos, mechanisms and geometry |

Fast mode is opt-in: the Scrimmage config (or a command-line option) chooses it. On the real robot nothing
changes.

## Goals

1. **Fast-mode device library.** A Java (WPILib-only) library of simulated devices that the team's fast-mode IO
   classes wrap (see "Calls made by Claude"), covering:
   - a swerve module: a drive Kraken, a steer Kraken and a CANcoder;
   - a single Kraken with its built-in encoder, for an elevator, arm, intake or flywheel;
   - a gyro standing in for a Pigeon 2.
2. **Talon-style control in fast mode.** The simulated Krakens accept the same kinds of requests the real IO
   sends, with the same gains and limits the team configures on the real Talon, so one set of constants drives
   both modes and fast mode roughly exercises the team's real tuning:
   - control types: voltage, duty cycle, position, velocity, Motion Magic (position) and Motion Magic Velocity;
   - gains: kP, kI, kD, kS, kV, kA and kG (Elevator/Static and Arm/Cosine);
   - static-feedforward sign: Velocity Sign and Closed-Loop Sign;
   - continuous wrap;
   - Motion Magic cruise velocity, acceleration and jerk (trapezoidal, or S-curve with jerk);
   - stator and supply current limits, forward/reverse soft limits, and peak output (voltage and duty cycle):
     these decide whether a mechanism reaches its target in time;
   - a follower Kraken (aligned or opposed), since elevators usually have two motors;
   - brake/coast neutral mode;
   - motor and sensor inversion;
   - the sensor-to-mechanism and rotor-to-sensor ratios.
3. **Phoenix mode runs on the wall clock.** Robots with Phoenix 6 devices run in real time (the robot program's
   clock free-running, physics keeping pace) instead of lockstep held to 1×. Then the robot program and
   Phoenix's simulation can't drift apart on a computer that can't keep up. Today that drift wrecks runs on CI
   runners at 0.7–0.8× real time.
4. **A sample robot** in `examples/` that proves both modes:
   - written the way the team will write next season's code: AdvantageKit, IO layers;
   - a Kraken swerve drive (CANcoders, Pigeon 2) and a Kraken elevator;
   - an autonomous routine that drives a path and raises the elevator.
5. **Scenarios and tests:**
   - the sample robot's auto ends at the expected pose, and its elevator reaches its height in time, in both modes;
   - fast mode repeats exactly and runs faster than real time;
   - each control type behaves as CTRE documents;
   - the Java swerve CI test, skipped on CI since 2026-10-27, comes back once Phoenix mode runs on the wall clock.
6. **Docs:** how to set up fast mode (the vendordep, the mode switch, the IO classes), Phoenix mode versus fast
   mode, and fast mode's fidelity limits.

## Non-goals

- Mechanisms beyond swerve modules and single motors (more can come later).
- WPILib 2027 and AdvantageKit 2027: this is built and proved on 2026, as if rewriting existing code fresh. The
  move to 2027 is a later, holistic change.
- RobotPy (AdvantageKit is Java-only).
- Matching Phoenix's simulation or hardware exactly, CAN latency, CANivore timing, Phoenix Pro features
  (TorqueCurrentFOC, Dynamic Motion Magic, Motion Magic Expo), or fused CANcoder/Pigeon behavior beyond what
  the docs make clear.
- CTRE's own `SwerveDrivetrain` in fast mode. The sample robot's swerve is AdvantageKit-style, with its own
  odometry and kinematics in WPILib.

## Anti-goals (failure even if it works)

- **Anything that imitates, reimplements or modifies Phoenix code, or uses undocumented CTRE behavior.** The
  fast-mode library must not load any Phoenix class: no CTRE types in its API, and no CTRE config objects as
  inputs. Talon-style behavior is built only from CTRE's public documentation. Where the docs are silent, it
  makes a sensible choice and says so.
- **Fast mode that changes how the real robot behaves.** The library's classes may be in the deployed robot
  jar, but nothing in them may run on the RoboRIO unless the robot code selects them.
- **Fast mode that silently passes a run Phoenix mode would fail, without saying so.** The docs must state
  plainly what fast mode doesn't model.

## Constraints

- **License:** CTRE support, 2026-10-27:
  - writing new implementations of their C API is a derivative work and prohibited;
  - using C functions outside the public headers could count as reverse engineering;
  - modifying their Java bytecode is prohibited.

  Scrimmage's agent already respects this: it rewrites only the robot's own calls, never Phoenix classes.
  Fast mode doesn't involve Phoenix at all.
- **CTRE's public docs** for Talon behavior:
  [closed-loop overview](https://v6.docs.ctr-electronics.com/en/stable/docs/api-reference/device-specific/talonfx/closed-loop-requests.html)
  and [Motion Magic](https://v6.docs.ctr-electronics.com/en/stable/docs/api-reference/device-specific/talonfx/motion-magic.html).
  They document what each gain and setting means, gain units "of the control output type", Motion Magic's
  profile shapes and units (rps, rps/s, rps/s/s), and that the Talon's loop runs at 1 kHz. They do *not* give
  an output formula, error units or kI handling. Those are Scrimmage's documented choices: a standard PID plus
  feedforward, with errors in rotations and rotations per second.
- **AdvantageKit** (BSD-style license, Littleton Robotics) v26: the sample robot may start from its
  `talonfx_swerve` template, keeping its license notices.
- **Target:** WPILib 2026 (GradleRIO 2026), Phoenix 6 26.x, Linux (Scrimmage's lockstep is Linux-only).
- **Lockstep:** fast mode keeps it, and its repeatability: everything the robot program does in fast mode follows
  the robot's simulated clock.

## Decided with the user

- **Two modes:** Phoenix mode for fidelity on the wall clock, fast mode for fast and repeatable runs. Fast-mode
  devices can also be used at real time, opt-in.
- **First round:** swerve modules and single motors (plus the gyro swerve needs). More mechanisms later.
- **Same gains:** Talon-style gains and settings in fast mode, the same values as the real Talon configs. This
  is acceptable only as long as no reverse engineering is involved.
- **A sample robot** in Scrimmage's examples (no existing team code to use).
- **2026 now**, 2027 later.
- **Delivery as a vendordep, with the jar committed for now.** The robot project uses the fast-mode library
  through a WPILib vendordep, as other teams will when Scrimmage is public. While the repo is private, the
  vendordep's Maven repository is a small folder committed inside the robot project, so no computer needs
  Scrimmage installed to build or deploy. When Scrimmage goes public, the vendordep switches to a hosted URL and
  the committed folder goes away.

## Calls made by Claude (correct while reviewing)

- **The library takes gains as plain numbers** in its own small settings classes, not CTRE's config objects, so
  fast mode loads no Phoenix code. The sample robot keeps one set of constants and feeds them to both the
  Phoenix configs and the fast-mode settings. A small helper in the sample robot (not the library) may build
  both from the same constants.
- **How the vendordep is produced:** `scrimmage java build` builds the library. A command such as
  `scrimmage java vendordep <robot project>` writes:
  - the jar, as a file-based Maven repository, into a folder of the robot project (e.g. `scrimmage-maven/`);
  - the vendordep JSON into `vendordeps/`, with `mavenUrls` pointing at that folder by relative path.

  The team commits both. Verify early that GradleRIO resolves a relative `mavenUrls` entry against the project
  folder (it registers each URL as a Gradle Maven repository; Gradle resolves relative paths against the project
  directory). If it doesn't, fall back to a plain `implementation files(...)` line and say so.
- **The library ships simulated devices, not the team's IO classes.** AdvantageKit's `ModuleIO`, `GyroIO` and
  their `@AutoLog` inputs live in the team's own project, so a library can't implement them. The library
  provides device-level classes instead: a simulated Kraken (Talon-style control), a simulated CANcoder and a
  simulated gyro. The team writes thin IO classes around them (e.g. `ModuleIOScrimmage`, `ElevatorIOScrimmage`,
  `GyroIOScrimmage`). The sample robot and the docs show these, ready to copy.
- **Mode signal:** Scrimmage tells the robot program the mode through an environment variable (e.g.
  `SCRIMMAGE_MODE=fast`). The library exposes a one-line check, such as `Scrimmage.fastMode()`, for the robot's
  `RobotContainer` to choose IO implementations. AdvantageKit's own `Mode.SIM` stays available for teams' plain
  WPILib simulation.
- **How fast-mode devices reach Scrimmage:** HAL SimDevices relayed by the HALSim WebSocket, as everything else
  is. Where Scrimmage's existing generic devices (`CANMotor`, `CANEncoder`, gyro) fit, reuse them. Otherwise
  add a `ScrimmageMotor`-style device carrying the motor voltage out and rotor position, velocity and current in.
  Config bindings name fast-mode devices simply, in `[[part]]` `ids` like the existing short forms.
- **Current and limits in fast mode:** the simulated Kraken computes motor current from the applied voltage and
  the measured velocity (the Kraken's published motor constants). It then applies the stator and supply limits
  the way CTRE's docs describe them, by reducing output. Where the docs don't define the exact limiting
  behavior, the choice is documented as a fidelity limit.
- **"The same run gives the same log"** means Scrimmage's own outputs: the run recording (`run.csv`), the
  report and the robot console, as the existing repeatability tests check. AdvantageKit's `.wpilog` isn't
  required to repeat, since it records wall-clock loop timings.
- **Phoenix mode when physics falls behind:** on the wall clock, physics catches up the way Scrimmage's
  real-time mode already does: it skips ahead and warns. Physics is cheap (about 0.5 ms of CPU per 20 ms robot
  loop), so CI runners should keep up. Phoenix-mode scenario tolerances allow for run-to-run variation, and the
  report notes any lag.
- **Fast-mode control loop rate:** at least every lockstep step (5 ms), run from the robot program's own HAL
  notifier so lockstep steps it deterministically. The real Talon runs at 1 kHz; 5 ms is close enough for this
  purpose and is documented as a fidelity limit.
- **Phoenix mode on the wall clock:** for a config with Phoenix 6 devices, Scrimmage runs the robot program in
  real time instead of lockstep (the extension released, physics keeping pace, as when the extension is absent).
  The report and docs say so.
  - The 0.8× "kept up with only" note and the `Session.phoenix_speed` measurement stay. In real time they measure
    physics lag instead.
  - Held agent sessions with Phoenix devices can't truly pause the Talons. Today that's silent; the builder must
    make it explicit, with a warning or documented behavior.

## Left to the builder

- **Library API:** class names, package layout and API shape, matching AdvantageKit's IO conventions (e.g.
  `ModuleIOScrimmage`, `MotorIOScrimmage` or similar, `GyroIOScrimmage`).
- **Control details:** the exact control math within the documented choices above, including:
  - Motion Magic profile generation;
  - continuous wrap;
  - gravity feedforward for Arm/Cosine, which needs the mechanism angle;
  - current reporting.
- **Bindings:** the Scrimmage config syntax for fast-mode devices, and how a part's slots bind to them.
- **Sample robot:** its layout and constants, its Tuner X-style constants (if any), its scenarios, and how its
  real IO is exercised in Phoenix mode.
- **Phoenix-mode change:** how the switch to wall-clock runs is implemented and tested, including its effect on
  existing CTRE examples, tests and docs.

## Testing (expected)

- **Unit tests of the fast-mode control:** each control type and gain against expected outputs, including
  Motion Magic profile limits, kS sign modes, kG types, continuous wrap and neutral modes. These are Java tests
  run by CI's Java job.
- **End-to-end tests of the sample robot:**
  - fast mode: auto end pose and elevator height; repeatability (two runs, identical logs); faster than real time;
  - Phoenix mode: the same scenarios at real time, with tolerances that allow for its run-to-run variation.
- **The Java swerve example's CI test** passes again under Phoenix mode on the wall clock.
