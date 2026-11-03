# Turret and pneumatics (sub-project 23)

## Intent

The survey of past FRC games found two frequent mechanism kinds with no route in Scrimmage at all: a turret (6
shooter games, including 2026, where turrets were common) and pneumatic cylinders (8 sourced games, spread
2002 to 2019). Students reach for pistons for claws, wrists, brakes, kickers and launchers. Today they have to fake a
piston with a motor slide, which gets the speed, the force and the air all wrong. A turret today can only be faked
by turning the whole robot. Both are self-contained additions that touch no piece or field code.

The owner asked for this project and told me to answer the design questions myself. Every call I made is listed
under "My calls" for review.

What a student wants to find out:
- **Turret:** does it keep the launcher on the goal while the robot drives and turns? How fast must it turn (its
  gearing)? Is its range enough before the wires wrap? Do the shots go in while it tracks?
- **Pneumatics:** does the cylinder push hard enough (a claw's squeeze, lifting an arm, a kicker's shot)? How fast
  does it fire? Does the air last a match, and how many cycles is that? What does the compressor cost the battery?

The audience is the same as before: high-school CAD students using the Test tab, often with a stand-in robot, and
teams whose robot code drives the robot through the HAL. The standing anti-goal applies: **no unnecessary
friction**.

## Goals

1. **A stock `turret` part.** A platform on a vertical hinge (joint `turn`, 0 facing the part's +x, positive
   counter-clockwise seen from above), a motor slot and an encoder slot like the arm's, a range for its hard stops
   (the wires), and a `top` mount for the launcher. A launcher attached to the turret's top shoots where the turret
   points. The range's shots already leave from the launcher's frame in the world, so nothing in the shot needs to
   change.
2. **Shooting in the Test tab.** A config with a `[range]` and a `[shooter]` runs under the blocks. Spinning the
   launcher and stage motors shoots the preloaded and picked-up balls, as robot code would. A new condition,
   *scored*, counts the balls in a goal (or in every goal). The run's result shows the goal counts, and the replay
   shows the shots.
3. **Aiming in the Test tab.**
   - A new block, *aim [turret] at [goal]*, holds the turret pointed at a goal's center every step, until another
     block moves or stops that motor. A student can drive and turn while it aims.
   - A new condition, *aimed*, is true when the launcher points within N° of the goal.
   - A run that aims also gets an `aim_error` signal for the chart: how far off the turret is, in degrees.
4. **A pneumatic system model** (`scrimmage/pneumatics/`), a component model that can be tested on its own:
   - **Stored side:** tanks (574 mL each by default, the FRC Clippard tank) charged by a compressor through a
     pressure switch. The compressor starts at 95 psi and stops at 115 psi, and it runs only while the robot is
     enabled.
   - **Working side:** a regulator holds it at the working pressure, 60 psi by default and never above the stored
     pressure.
   - **Each cylinder:** double-acting, two chambers fed through its valve. Its force comes from the chamber
     pressures on the bore and rod areas, less a seal friction. Its speed comes from how fast air flows through the
     valve, including the choked-flow limit.
   - **Air and battery:** air spent filling a chamber comes out of the tanks. The compressor's current comes from a
     curve against pressure and draws on the battery like a motor.
   - **Estimates:** every number is an estimate with its source, marked UNCALIBRATED.
5. **A stock `cylinder` part** with two forms:
   - **Free form:** the cylinder has its own rod on a slide, with a `rod` mount at the rod's end. For pushers,
     kickers, a pneumatic intake slide, and anything attached to the rod.
   - **Push form:** `push = "<instance>.<body>"` and `push_pos` pin the rod's end to another part's body. The
     cylinder then drives whatever joints lie between its two pins: a stock arm raised by a piston, a CAD claw's
     jaw, a wrist.
   - In both forms the force acts along the line between the two pins, and the cylinder bottoms out at its stroke's
     ends.
6. **Valves and robot code.** A cylinder's `valve` slot takes a single solenoid (spring return) or a double
   solenoid, on a CTRE PCM or a REV Pneumatic Hub:
   - **CTRE PCM:** the robot program's `Solenoid` and `DoubleSolenoid` outputs arrive over HALSim WebSocket.
     Scrimmage sends the PCM its pressure switch, the compressor's current and whether the compressor is on.
   - **REV PH:** this needs the lockstep extension. The extension mirrors the hub's sim data into a SimDevice that
     the socket relays, and writes back the same three values.
   - **Unbound valve:** a valve slot with no ID warns, as an unbound motor does.
7. **A `[pneumatics]` section** in a robot config: tanks, the compressor (or none), the working pressure, the
   starting pressure (120 psi, charged before the match by default) and the switch points. A config with cylinders
   and no section uses the defaults.
8. **Pneumatics in the Test tab:**
   - **Blocks:** a block *set [cylinder] to extend / retract / off*.
   - **Conditions:** *cylinder is extended / retracted*, and *air pressure is above / below N psi*.
   - **Chart signals:** `air.psi` (stored), `air.working_psi`, and each cylinder's `<name>.extension` in inches.
   - **Air line:** the run's result says how the air went, e.g. "air: 120 psi at the start, 84 psi at the end,
     lowest 81 psi at 14.2 s; the compressor ran 9.0 s". It warns if the working side fell below its set pressure:
     the cylinders were weaker from then on.
   - **Replay:** the replay draws each cylinder's barrel and rod between its pins.
9. **`scrimmage air <config>`**, an air budget. It reports each cylinder's air per full cycle (extend and retract),
   the air stored between the switch's top and the working pressure, and roughly how many full cycles of all the
   cylinders that is with no compressor. It also gives how many cycles a 150 s match allows with the compressor
   running. This is the spreadsheet FRC teams make by hand.
10. **Two worked examples:**
    - **examples/blocks/turret:** a stand-in robot with a turret, launcher, hopper and stage on the 2026 field.
      It aims at the hub, shoots while turning in place and driving, and checks how many went in.
    - **examples/blocks/pneumatic:** a stand-in robot whose arm is raised by a push-form cylinder. It cycles the
      arm and checks that the air holds above the working pressure.
    - Each example has a README and a test that runs it.
11. **Docs:**
    - parts.md: the turret, the cylinder, valve IDs and `[pneumatics]`.
    - test-tab.md: shooting, aiming and pneumatics.
    - A new docs/pneumatics.md: the model, its numbers and sources, how to measure your own, and robot-code setup
      for the PCM and the PH.
    - The README's bindings.

## Non-goals

- **Cylinder kinds:** single-acting cylinders, rotary actuators, vacuum, and more than one pneumatic system per
  robot.
- **Valve plumbing:** flow-control valves on each port (a valve's flow is one setting), and air leaks.
- **Thermal effects:** temperature (the model is isothermal).
- **Turret aiming:** leading a shot on the move (aiming at a moving robot's goal from its speed), and continuous
  turrets with slip rings (a range of up to ±360° is allowed, so a slip-ring turret is a wide range).
- **Elsewhere:** pneumatics on the component bench, and a Part tab screen for adding a cylinder. Cylinders are stock
  parts set in a config or in the Robot tab's stock part form.
- **REV PH analog pressure:** HAL has no way to simulate the hub's analog pressure sensor, so `getPressure()`
  reads 0 in sim. The docs say so.

## Anti-goals

- **No new friction for students.** A student adds a cylinder, binds its valve (or doesn't, for blocks), and it
  works with sensible defaults. Every pneumatic number has a default.
- **No unstable physics.** A chamber filling at a 1 ms step must not overshoot or ring. The fill never passes the
  pressure it's filling from.
- **No change to existing results.** Robots without a turret, cylinder or `[pneumatics]` step exactly as before.
  The ledger golden and the existing examples keep their numbers.

## Constraints

- **Pressure rules:** FRC limits the stored side to 120 psi and the working side to 60 psi (the FRC Pneumatics
  Manual). Defaults stay within them, and Scrimmage refuses a working pressure above 60 or a stored pressure above
  120. A refused value names the rule. A team testing beyond the rules isn't a use case.
- **Units:** files take meters and kilograms as elsewhere. Pressures are in **psi**, because every FRC part and
  rule is written in psi. The docs say this is the one exception.
- **CTRE path:** the WebSocket messages, verified 2026-11-03 against RobotPy 2026.2.2's halsim_ws client:
  - a solenoid is `{"type": "Solenoid", "device": "<module>,<channel>", "data": {"<output": bool}}`;
  - the PCM is `{"type": "CTREPCM", "device": "<module>"}` with `<init`, `<closed_loop` (robot to sim) and
    `>on`, `>pressure_switch`, `>current` (sim to robot);
  - a `DoubleSolenoid(4, 5)` set forward turns channel 4 on;
  - a REV PH sends nothing over the socket.
- **REV path:** the extension change must use only the HAL C API, as the rest of the extension does, so one build
  serves RobotPy and GradleRIO.
- **Spellings and wording:** American spellings and plain words in every student-facing string.

## My calls (the owner can overrule any)

**Turret**
- **C1 Joint and range:** joint `turn`, slots `turn` and `encoder`, mounts `base` and `top`. Default range ±180°,
  allowed up to ±360°. Default gearing 60:1 on one Kraken X60 (an estimate typical of 2022 and 2026 turrets).
  Default platform mass 2 kg, with inertia from a 0.3 m disc.
- **C2 What "aimed" measures:** the horizontal angle between the launcher's forward axis and the line from the
  launcher to the goal's center. This assumes the config's `[shooter]` launcher rides on this turret. Otherwise it
  uses the turret's own forward axis. The aim block's target joint angle is the current angle plus that error,
  clamped to the turret's range, and the existing position loop holds it.
- **C3 No lead:** aiming takes no account of the robot's motion. The docs say a moving shot lands off by the
  robot's speed times the flight time.
- **C4 Aiming table:** `scrimmage launcher aim` keeps measuring with the turret at 0. A turret's offset from the
  robot center is small next to the shot distances.

**Shooting in the Test tab**
- **C5 Scored:** `scored` takes `goal` (a name, or "any") and `at_least`. It reads the range's goal counts.

**Pneumatics model**
- **C6 Model form:** an isothermal model of two chambers, with flow through the valve per ISO 6358:
  - **Flow:** sonic conductance C, critical ratio b = 0.3, choked below it. The default
    C = 0.8 dm³/(s·bar), about Cv 0.2, an estimate for FRC's small 5/2 valves (the SMC and Festo kit valves).
  - **Fill step:** each 1 ms step adds the flow and stops at the pressure upstream, so it can't overshoot.
  - **Stability:** every cylinder sub-steps its chambers 10 times per physics step.
  - **Dead volume:** 1 cm³ at each port (5 cm³ after the build: see "Decisions made during the build").
- **C7 Compressor:** the default is the VIAIR 90C, using AndyMark's bench test of it:
  - **Flow:** 0.88 cfm free air at 0 psi and 7 A, falling to 0.22 cfm at 100 psi and 11 A.
  - **Curve:** straight lines between those points, carried on to 120 psi.
  - **Switch:** stops at 115 psi, starts at 95 psi (a Nason SM-2B-115R estimate).
  - `compressor = "none"` is allowed (tanks only).
- **C8 Cylinder defaults:**
  - **Size:** 1-1/16 in bore, 5/16 in rod, 6 in stroke (the common Bimba sizes in FRC).
  - **Seal friction:** 8 N, an estimate.
  - **Mass:** barrel 0.25 kg, rod 0.1 kg.
  - All of these are UNCALIBRATED.
- **C9 Valves:**
  - **Single solenoid** (a spring-return 5/2 valve): on extends, off retracts.
  - **Double solenoid:** forward extends and reverse retracts. Off holds the last position (a detented valve), and
    at the start that is retracted.
  - **Both coils on:** holds the last position, with a warning.
  - **`invert = true`:** swaps extend and retract.
  - **Test tab:** the *set* block's "off" means "retract" on a single solenoid, and on a double solenoid "leave it
    where it is".
- **C10 Device IDs:**
  - `"PCM:3"` and `"PCM:3,4"`: module 0, single and double (forward, reverse).
  - `"PH:3"` and `"PH:3,4"`: REV PH module 1, single and double.
  - A table for anything else: `{ pcm = 1, channel = 3 }`, `{ ph = 2, forward = 3, reverse = 4 }`, with an optional
    `invert`.
  - One robot's valves must all be on one kind of module. Two kinds is an error that names both valves.
- **C11 Compressor in the robot code path:** the compressor runs only while the robot is enabled and the robot
  program has its module's closed loop on (`<closed_loop`, or the PH's config type not "disabled"). If the program
  never creates the module, the compressor never runs, which is correct: the real one wouldn't. Under the Test
  tab, the compressor runs throughout.
- **C12 Cylinder physics:**
  - **Force path:** a cylinder's force acts along a MuJoCo spatial tendon between its two pins, through an actuator
    on that tendon whose control is the force each step. In the free form the pins are the barrel's root and the
    rod's end, and the slide's range is the stroke.
  - **End stops:** the tendon's range is [retracted length, retracted length + stroke], so it bottoms out at both
    ends.
  - **Push form length:** the retracted length is `length` (pin to pin), or by default the pins' distance at the
    assembled pose. A push cylinder whose assembled length is outside its stroke is an error that gives both
    lengths.
  - **Why a tendon:** it adds no degrees of freedom and no soft loop. The web replay draws the cylinder between the
    pins, and MuJoCo's own renderer draws the tendon.
- **C13 Battery load:** the compressor's current joins the bus solve as a load with zero slope. It only draws while
  running.
- **C14 Starting pressure and match length:** a run starts at `[pneumatics] start_psi`, 120 by default. The
  working side starts at the regulator's pressure, with every chamber at its valve's state (retracted, unless a
  valve starts on). `scrimmage air` assumes a 150 s match.

**Examples and docs**
- **C15 Examples:** examples/blocks/turret on the 2026 field with the kitbot stand-in, and
  examples/blocks/pneumatic on the plain field (no range is needed).

## Left to the builder

- File layout inside `scrimmage/pneumatics/`, the names of internal functions and the HAL key names of the PH's
  SimDevice (they're internal to Scrimmage and the extension).
- The Blockly block shapes and colors, following the existing blocks.
- The exact wording of messages, within the plain-words rule.

## Decisions made during the build

Each ruling made while building, in the order it was made within its area: what was decided, why, and what it costs
if it is wrong.

**Turret and aiming**
- **Wrap-around aiming.** On a turret whose range is more than one turn, *aim* picks the reachable angle that points
  at the goal nearest the turret's angle now. Under one turn this is the same as C2's clamp. Why: it avoids unwinding
  the turret for nothing. If wrong: a different choice of which way round to go.
- **Aim with no motor.** An *aim* on a turret whose motor slot has no ID is a problem sentence before the run (it
  crashed before). Why: a plain sentence, like a *move* on an unbound joint. If wrong: nothing.
- **"It stops at …" wording.** The out-of-range sentence ("hub_blue is outside the turret's range (it stops at
  30°)") only fires on a range under a full turn; the review's "(it stops at 180°)" was an illustration, and a default
  ±180° turret covers the circle. If wrong: nothing.
- **What *aimed* reads at a stop.** *aimed* measures where the launcher points (|error| ≤ within), so a goal 1° past a
  stop, checked within 2°, reads as aimed. Why: C2 read literally. If wrong: a lenient pass at the edge of the range.

**Shooting in the Test tab**
- **The replay shows the shots.** Goal 2 says so and no task covered it, so Task 10 checked it (it already worked:
  the launched balls are hidden until launch and then fly). If wrong: a small extra check.
- **Every motor on a joint is driven.** The Test-tab runner now writes every motor binding on a joint, where it wrote
  only one per joint before. Why: it was the root cause of slow shots (a launcher's follower got nothing), and a real
  bug for any joint with several bound motors. If wrong: changed numbers for existing Test-tab runs with multi-motor
  joints (the suite passes unchanged).

**The pneumatics model**
- **Air per stroke.** A stroke's air is the swept volume at absolute pressure (it starts empty, at the atmosphere)
  plus the dead volume at gauge pressure (it starts at one atmosphere), not the plan's gauge-only sum, which
  undercounts by about 20%. `scrimmage air` uses it. If wrong: a 20% shift in the budget.
- **Overshoot and the 120 psi rule in the first fix round.** The review's "low tanks can overshoot" and "a switch
  point above 120 isn't refused" were fixed at once rather than deferred. Why: they broke the plan's constraints (no
  overshoot; stored ≤ 120). If wrong: one fix round's time.
- **The budget's number.** The stock cylinder takes about 0.86 L of free air per cycle at 1 cm³ of dead volume (0.889 L
  at the later 5 cm³); the plan's 0.574 L was an arithmetic slip, and the test computes from the formula. If wrong:
  nothing.
- **A Test-tab hook for the compressor.** `PneumaticsRuntime.always_closed_loop` makes the compressor run throughout a
  Test-tab run (C11). Why: the plan named the behavior but no hook. If wrong: a renamed attribute.
- **Refill after `reset_robot`.** Putting the robot back without a full reset refills each cylinder's chambers for
  where its rod is now, keeping the tanks' air. Why: the old air squeezed into the new volumes made a force spike. If
  wrong: nothing.
- **Dead volume and armature (Task 9a).** The default dead volume became 5 cm³ per port (the port, its fitting and
  about 0.3 m of tube; an estimate, UNCALIBRATED), up from C6's 1 cm³, and each cylinder's tendon gets an armature of
  k_max·dt²/2. Why: a 2 in bore on a light rod went unstable (the rod through its stop, 1700 psi): the air is an
  explicit spring, and the armature bounds k·dt²/m whatever the load. If wrong: about 0.2 kg of extra inertia along a
  2 in cylinder's line (0.02 kg on the default).
- **The extension the air reads.** The air model reads the tendon's length carried forward by this step's motion
  (`ten_length + dt·(ten_J·qvel)`), with the armature kept. Why: mj_step leaves the length from before it integrated,
  and an air spring driven from a one-step-old position is unstable at any stiffness. Exact on the free form, first
  order elsewhere, and cheap. If wrong: a first-order error on curved tendon paths.
- **Stability test bounds.** The free form may go 3 mm past a stop on the first step (the stop's softness). The push
  test bounds the fed side's pressure, while the vented side may cushion up to 3 times the working pressure (real
  compression); its arm is laid out so the cylinder's stroke limits it, within ±1°. The push test asserts that the
  retract stop holds the arm. If wrong: a looser regression net.

**The cylinder part**
- **Stiffer end stops.** The tendon and the slide stop with solref [0.002, 1]. Why: the default let 60 psi push the rod
  2.4 cm past its stroke. If wrong: a slightly stiffer stop than a real cylinder's cushion.
- **The Robot tab's text box.** The inspector's new "text" parameter kind (the cylinder's `push`) showed a number box;
  it was fixed in Task 10, outside Task 4's files. If wrong: a wrong input box until then.
- **The replay's pins.** The model view gives each cylinder its pins as [mover, pos] (mover -1 is the world) and the
  rod's diameter, as the existing geoms do. If wrong: nothing.
- **The vec3 box.** The inspector had no box for a three-number parameter (the cylinder's `push_pos`, and the chassis's
  `com_offset`, which already needed it); Task 10 added it. Why: the push form needs it from the Robot tab. If wrong:
  a small extra change.

**Robot code**
- **A disabled robot's solenoids are off.** A single solenoid's cylinder retracts and a double solenoid's holds. Why:
  that is what FRC hardware does. If wrong: a different hold behavior while disabled.
- **Hub warnings that reach the student.** A missing or unopened Pneumatic Hub warns once per hub, in the run's notes
  and the session's warnings, not only in the log. Why: cheap, and it decides whether the student ever sees it. If
  wrong: one fix round's time.
- **A Java robot with hub valves and no extension is refused.** It stops at the start with the `pip install ./lockstep`
  message. Why: its valves could never move. If wrong: a refusal where a warning would do.

**The Test tab**
- **The air line names the working pressure.** The run's `air` carries `working_psi`, so the warning says the robot's
  own working pressure. If wrong: nothing.
- **"off" on an inverted single solenoid retracts.** C9's "off" is about the cylinder, not the coil, and the docs say
  so. If wrong: a surprise for a student who thinks of "off" as the coil off on an inverted valve.

**Examples**
- **A 3/4 in bore for the pneumatic arm**, not the plan's 2 in. Why: a 2 in bore spends the tanks in about one cycle
  (about 4 L per cycle against 4.3 L stored); the README tells the 2 in and 1-1/16 in tries as the lesson. If wrong: a
  different example cylinder than planned.
- **The example READMEs' small fixes went into the docs task** (Task 12): the UNCALIBRATED lines in the sample output,
  plainer wording, and asserts for the times the READMEs state. Why: the docs pass touched them anyway. If wrong:
  nothing.
