# Test range and robot-code shooting (sub-project 9): design

## Why

FRC 5098 couldn't test autonomous shooting last season. Their workshop ceiling is too low for real shots, so
their auto parked at one known spot and fired. They want to test, in simulation, the things they couldn't:
- **Shooting from several known positions.** Their auto can then adapt to where alliance partners' autos need
  room, and each position is known to score.
- **Shooting on the move.** The ball carries the robot's velocity, and the aiming code must allow for it.
- **An articulated hood.** Their code sets the hood angle from distance and robot speed. They didn't have one
  last season and want to find out whether one is worth building.
- **A full cycle.** Fire while driving out for more balls, pick them up, and fire again on the way back.

This is piece 3 of shooting:
- Pieces 1 and 2, the launcher model and design comparison, are done (sub-project 8; `scrimmage/launcher/`,
  `docs/launcher.md`).
- Piece 4, calibration, comes later.

## What it is

The team's real robot code drives a robot whose launcher, hood, feeder and intake are simulated, on a test range
with real field elements:
1. A scenario starts the robot at a named position with preloaded balls.
2. The code runs its auto in Phoenix or fast mode: driving, spinning the flywheel, moving the hood, feeding,
   and intaking.
3. Each ball goes through the launcher model, flies, and bounces off or drops into the goal.
4. The goal counts what scores.
5. Scenario checks pass or fail, as the elevator and arm checks do today, so they can run in CI.

The user watches it in the live 3D view: balls visible everywhere, and a score counter that ticks up for each
ball that goes in.

## Goals

### Test range

1. **A range file per game.** It describes that season's field elements, which are fixed for everyone that
   year. It's written once per game and reused. A robot config or a scenario names the range it uses.
2. **Goals.** Each goal has:
   - an opening: a regular polygon (sides, size across the flats, rotation) or a circle (diameter);
   - a rim height, a tilt (0° horizontal, 90° vertical) and a position and heading on the field;
   - an optional funnel (collar), given by its depth and the slope of its walls, which ends in a throat;
   - a scoring surface: the throat for a funnelled goal, otherwise the opening.

   The goal is solid, so balls bounce off its rim and funnel, and they can roll down the funnel and score.
3. **The 2026 range.** The first range file is last season's (REBUILT). Its hub dimensions come from the official
   2026 field CAD, measured in FreeCAD from `FE-2026-rev-rebuilt-playing-field.step`:
   - **Opening:** a regular hexagon, 41.7 in (1.060 m) across the flats, with its flats facing the field's sides
     (corners toward the alliance walls).
   - **Rim:** level at 72.0 in (1.829 m).
   - **Funnel:** six walls at 60° from horizontal, 15.6 in (0.395 m) deep, down to a throat about 23.3 in
     (0.593 m) across the flats at 56.4 in (1.433 m).
   - **Body:** 47 × 47 in, centered 182.1 in (4.625 m) from its alliance wall.
   - **Back net:** behind the opening, as in the CAD.
   - **Both hubs:** red and blue.
   - **Ball:** FUEL, the `2026-fuel` launcher preset.

   The range file cites, for each number, either the official field CAD
   (https://firstfrc.blob.core.windows.net/frc2026/FieldAssets/FE-2026-rev-rebuilt-playing-field.step) or the manual
   (https://firstfrc.blob.core.windows.net/frc2026/Manual/HTML/2026GameManual.htm, section 5.4). The back net's size is
   measured from the CAD as well.
4. **Floor balls.** Set positions for balls lying on the field, as at the start of auto. The 2026 range includes
   the neutral-zone FUEL layout from the manual or CAD.
5. **Named start positions** for the robot, so scenarios can run the same auto from several of them.

### Robot parts

6. **A launcher part.** Its geometry and physics come from sub-project 8's launcher setup (`[launcher]`-style
   inputs: ball, wheels, shooter shaft, hood, air). It also has:
   - **Shooter motor(s):** Krakens bound by CAN id (Phoenix mode) or as fast-mode devices, like the elevator's.
     The launcher model's shooter shaft runs the flywheel physics. The motor's encoder reads that shaft.
   - **Hood motor:** an articulated hood driven like an arm (a joint with motor, gearing and range). The hood
     angle the launcher model uses each moment is the hood joint's actual position, so moving the hood while
     firing affects each ball.
   - **A fixed hood:** an option (no hood motor) for launchers like last season's.
7. **Lanes.**
   - The hopper, the feeder stages and the launcher carry balls in one or more lanes side by side (the user's
     choice). Each lane has a position across the launcher's wheels.
   - A one-lane configuration gives a single-file shooter.
   - A collected ball goes to the emptiest lane, and it keeps its lane all the way to the launcher.
   - Lanes enter the launcher as the launcher model's lanes (`FedBall.lane`), which also sets the interface the
     user's later hopper model will supply.
8. **Feeder stages.**
   - **What a stage is:** a generic driven stage with one or more motors, gearing, a roller or pulley diameter and
     a path length. Rollers, belts and indexer wheels all reduce to this.
   - **Driving:** running a stage forward moves the front ball in each lane along the path at the stage's surface speed;
     stopping it stops the balls; reversing pushes them back.
   - **Chaining:** stages can be chained, for example a hopper floor and then a final feeder.
   - **Into the launcher:** a ball reaching the end of the last stage enters the launcher, at the stage's surface
     speed as its entry speed (the launcher model's `FedBall`).
9. **Hopper.**
   - A store of balls with a capacity, filled by preloading (the scenario) or by the intake zone.
   - It moves nothing itself; the first feeder stage draws from it.
   - The user plans a detailed hopper model later. It must be able to replace this one through the launcher's
     existing feed interface (`FedBall`), without changing anything else.
10. **Intake zone.**
   - A box at a mount on the robot (for example the front bumper) with a width (the cross-section), a maximum
     pickup rate (balls per second) and an intake motor.
   - A field ball touching the zone is collected into the hopper while the intake motor runs inward, up to the
     rate and the hopper's capacity.
   - A detailed intake model is a separate later piece. It will compare over-the-bumper with a bounce bar against
     a gap in the bumper, and produce the zone's width, rate and how many balls fit at once.
11. **Beam-break sensors (optional).**
    - Placed at points along the ball path: the hopper, a fraction along a feeder stage, or the launcher entry.
    - Wired to DIO channels, true or false as a ball occupies that point, with active-low as an option, like
      today's limit switches.
12. **One battery.** Every simulated motor draws from the robot's shared battery: the shooter, the hood, the
    feeder stages and the intake. A firing burst sags the voltage for the drive and everything else.

### Balls in the world

13. **Balls are physical bodies on the field and in flight.**
    - They bounce off the floor, the robot, the goal and each other.
    - In flight, the launcher model's air drag and backspin lift act on them each step, so a ball's flight
      matches the flight model of sub-project 8 until it touches something.
    - Inside the robot (hopper, feeder, launcher) balls are tracked by the robot model, not as free bodies.
14. **Leaving the launcher.**
    - A launched ball appears at the launcher's exit point, transformed by the robot's actual pose.
    - Its velocity is the launch velocity plus the robot's velocity at the exit point, including rotation.
    - It carries the launcher's backspin.
15. **Scoring.**
    - A ball scores when its center passes downward through a goal's scoring surface inside its outline.
    - It is then removed from play, and counted for that goal.
    - The 2026 hub's four exits returning balls to the neutral zone are not modeled.

### Seeing it and checking it

16. **3D view and run replays.**
    - Balls show everywhere in the live 3D view and when watching a scenario run: in the hopper (as a count),
      on the field, in flight and in the goal.
    - Field elements, including the hub and its funnel, show in the 3D view.
    - An on-screen counter shows each goal's score as it ticks up, plus balls fired and balls held.
17. **Per-ball report.**
    - The run report lists each ball: when it was fired, the robot's position and velocity, the hood angle and
      flywheel speed at the time, exit speed, spin and exit slip.
    - It also gives the outcome: scored in which goal and when, or where it came to rest.
18. **Scenario checks.**
    - New signals usable by the existing checks: score (per goal and in total), balls fired, balls held and balls
      on the field. For example `at_end = {signal = "score", min = 8}`.
    - A "by time T" form, such as "8 scored by 15 s".
19. **AdvantageScope.** Ball positions are published to NetworkTables next to the existing ground-truth pose, as
    a Pose3d array.
20. **A worked example.** The fast-mode sample robot (`examples/java_fastmode`) gains:
    - an AdvantageKit-style shooter subsystem (flywheel, articulated hood, feeder, intake) with real and fast-mode
      IO, using a made-up but plausible two-lane hooded launcher (the user's choice: not 5098's 2026 shooter);
    - an aiming table (wheel speed and hood angle against distance) generated from the sim's own launcher model by a
      documented command or script, so it can be regenerated when the model is calibrated. The robot aims sideways
      by rotating the swerve drive;
    - autos that shoot from two named positions, and on the move;
    - scenarios checking their scores in fast mode (repeatable) and in Phoenix mode (real time).

## Non-goals

- A turret. The robot aims by rotating its swerve drive, which is how 5098 and many teams did it. A turret can be
  added later if a future game needs one.
- Uneven floor (bumps and ramps) and the 2026 trenches. Supporting driving over a bump is a separate later
  sub-project (Claude's call), because it changes the drive physics, not shooting. This range's floor is flat.

- A detailed intake mechanism model (a later piece; the zone's numbers are the hook).
- A detailed hopper model (the user's own later piece; the feed interface is the hook).
- The full game: the 2026 hub's exits, the hub's active/inactive timing, other field elements beyond what
  shooting and pickup need, other robots, and defense.
- Calibration (piece 4).
- Changing the launcher model's physics, except what running inside the world needs (see Constraints).

## Anti-goals

- **A score you can't explain.** Every ball must be traceable in the report. A missed shot should say why, and
  a ball must never silently vanish.
- **Fudged goal geometry.** The 2026 hub must match the official CAD and manual, and the range file cites them.
- **Breaking repeatability.** Fast-mode runs with the same inputs must give the same scores (lockstep, seeded
  randomness).
- **Robots without balls paying for them.** Existing robots and scenarios, which have no range or launcher, must
  behave exactly as before and not slow down.

## Constraints

- **Built on what exists:**
  - `[[part]]` stock parts and the assembler;
  - motor bindings and ids (`ctre = "TalonFX:<id>"`, fast-mode devices);
  - the shared `Battery`;
  - lockstep and the real-time Phoenix path;
  - movers and the live-view frame protocol;
  - scenario signals and checks;
  - the NetworkTables link;
  - `scrimmage/launcher` (`Launcher`, `ShaftModel`, `FedBall`, `ExitedBall`, flight air model, presets).
- **Launcher model changes allowed, for running in the world:**
  - the shooter shaft takes the world's bus voltage and reports its supply current to the shared battery,
    instead of owning a battery;
  - the hood angle can change at run time, read from the hood joint;
  - the model advances in substeps inside a world physics step.

  The standalone `scrimmage launcher run|sweep` must keep working and give the same results.
- **Ball count is fixed per run.** The live-view frame protocol drops frames whose body count changes, so ball
  bodies are created when the world is built (preloads, floor balls and spares) and parked out of play when
  inside the robot or scored.
- **License rules unchanged:** no Phoenix internals are touched; fast mode stays WPILib-only.
- **CI and speed:**
  - The full 2026 ball layout must run.
  - The CI speed test (faster than real time in fast mode) may use fewer balls.
  - Balls resting on the floor may use cheaper contact settings, as long as they still behave when disturbed.
  - New tests run in a few minutes.
- **Phoenix mode:** Phoenix-mode runs are real time and not exactly repeatable, so their scenario score thresholds
  are looser than fast mode's.

## Calls made by Claude (the user can change these)

- **The hood:** it pivots about the shooter shaft, as the user chose for sub-project 8.
- **Robot-owned balls:** a ball inside the robot is a bookkeeping item, not a free body. The launcher model owns
  it from feed to exit. It becomes a free body again at the exit point.
- **Flight in MuJoCo:** balls fly as MuJoCo bodies with the drag and lift forces applied, not by the standalone
  flight calculation. That way rim and funnel bounces are real. Bounce behavior comes from the ball preset's
  restitution, mapped onto MuJoCo's contact parameters; the mapping is a documented choice.
- **No bumps here:** uneven floor goes in its own later sub-project, and the generated field's center obstacle box
  is turned off when a range is used.
- **The intake needs its motor running inward** to collect, not just contact, so intake code gets tested.
- **Goals from plain shapes:** the goal is built from simple shapes (thin plates), not meshes, because the 3D
  view skips meshes on field elements.
- **Score counter content:** it shows score per goal, balls fired and balls held.
- **Two cut-outs:** the 2026 hub's exits and active/inactive timing are left out.

## Left to the builder

- The range file's TOML layout, and how a config or scenario names it.
- The launcher part's config layout, reusing sub-project 8's setup sections where they fit.
- How the shooter shaft's speed is mirrored to the shooter joint for the encoder and the view.
- Parking and un-parking ball bodies, and their contact settings for speed.
- The restitution-to-MuJoCo contact mapping, if it is documented and tested with a drop test like
  `contact.py`'s.
- The counter's look.

## Decisions made during the build (Claude's calls, for review)

Each line: what was decided, why, and what it costs if wrong. P = plan pre-flight scan, R = during the build.

- ## Preflight scan: 41 rows in preflight.md (same dir). Rulings (adopting the scan's proposals; details per finding in preflight.md):
- P1 (Task 6, Task 7) — BallPath.step returns no_contact/no_wheel balls reported by Launcher.insert to the runtime so they're logged — else test_stuck_ball_logged can't pass — cost: none.
- P2 (Task 4, Task 8) — RangeRuntime.reset re-applies its last scenario overrides (Bridge._restart_clock resets the world on robot connect) — cost: none.
- P3 (Task 4, Task 5) — the battery sum in World.step skips external bindings; before_physics returns the shooter supply current which is added once — avoids double count — cost: none.
- P4 (Task 4, Task 7, Task 8, Task 11) — ball_log() returns None for unset values (not NaN) so report.json is valid JSON and == repeatability holds — cost: none.
- P5 (Task 4, Task 7) — free-joint angular qvel is in the body frame: rotate by xmat; test with a ball starting rotated — cost: none.
- P6 (Task 4, Task 5, Task 6, Task 7) — Task 5 and Task 6 extend reset for Launcher/lanes/preload; Task 7 stores launcher-time to world-time offset — cost: none.
- P7 (Task 5, Task 10) — parse_shooter injects documented placeholders for control/goal and takes launcher.height from the mount — the part supplies these — cost: none.
- P8 (Task 10, Task 11) — AimRow.flight_s = time to the rim crossing; add Landing.goal_time — cost: small flight.py change.
- P9 (Task 10, Task 11) — the AimTable header records fixed command text, not the actual temp path — byte comparisons — cost: none.
- P10 (Task 1) — test_world_sized_steps_match is a regression guard (passes before), fix the step text; handle negative frac when the hood moves the exit end behind a ball — cost: none.
- P11 (Task 3, Task 8, Task 11) — a scenario `start` overrides config start_pose for that run; Task 11 configs omit start_pose — cost: none.
- P12 (Task 3, Task 6) — capacity is the whole robot (hopper + stages + launcher); preload > capacity is a config error in Task 3 — cost: none.
- P13 (Task 2) — throat derived from slope/depth is 0.604 m vs CAD 0.593 m: use the CAD value and state the gap in the range file; citations also on name/ball lines — cost: none.
- P14 (Task 6, Task 7) — BallLog.lane is an int lane index — cost: none.
- P15 (Task 3) — golden test drives swerve.toml via CTRE HAL keys and compares with rtol 1e-12 — cost: none.
- P16 (Task 4) — `out` = center outside the field rectangle — cost: none.
- P17 (Task 9) — scored balls are parked/hidden; the score counter satisfies spec 16's "in the goal" — spec's own constraint parks scored balls — cost: a ball isn't seen resting in the hub.
- P18 (Task 8, Task 9) — add tests/fake_robot_main.py and scene3d.js to Files; restart_robot is async — cost: none.
- P19 — no speed test for rangeless robots; the Task 3 golden test proves unchanged behavior — cost: a slowdown could slip in unnoticed (final review to check).
- R1 (Task 2) — the reviewer flagged the throat (±296.3 mm) and rim (529.9 mm) CAD citations as untraceable; they come from Claude's own FreeCAD measurement of the same STEP file (Funnel Base inner edge, Funnel Side top inner edge), so the citations stand — cost: none.
- R2 (Task 3) — accept the implementer's push-only restitution relation (Schwager & Pöschel 2007) instead of the brief's two-way ζ formula, which over-bounces; cite the paper in the docstring — cost: none.
- R3 (Task 3) — fast hits (30–50 m/s) may rebound up to (e + 0.08)·v instead of the requested e + 0.05: the excess is a 1 ms timestep effect (the ball is already up to 4 cm into the plate on the first contact step) and no contact setting met both that and "never passes through"; documented — cost: hard hits bounce up to ~0.07 above the ball's restitution.
- R4 (Task 4) — a bug in sub-project 8's launcher/flight.py: backspin lift used |v| instead of |v|² (the docs said |v|²). Fixed in its own commit; the standalone run/sweep goldens were regenerated on purpose (only landing spreads changed) and docs/launcher.md's hood-angle sentence updated — cost: standalone landing and scoring numbers shift slightly (more lift).
- R5 (Task 5) — the shooter at neutral must follow the binding's neutral mode like every other motor (MotorBinding.brake: coast default for CTRE, brake shorts the winding → back-EMF braking), not always coast — cost: none.
- R6 (Task 5) — per P7 the launcher's height comes from where the part is mounted, so a setup file's [launcher] height is ignored for the part (documented) rather than required to match — reusing a standalone setup file shouldn't need edits — cost: a mismatched height in a setup file goes unnoticed.
- R7 (Task 5) — regenerative braking current into the battery is kept (consistent with motors.Battery and sub-project 8's deferred note) — cost: none.
- R8 (Task 6) — two reviewer "minor" findings were promoted into the fix round because they are correctness bugs: an assert that popped a ball (under python -O balls would duplicate) and ball spacing that could drop 1.6 mm below one diameter — cost: none.
- R9 (Task 7) — balls rolled on carpet for ~10 s; added a carpet rolling resistance ([carpet] rolling_resistance, default 0.07 marked as an estimate, compared with a golf ball on a putting green) so a ball rolled at 0.5 m/s stops in about a second — drifting balls would make pickup behavior wrong — cost: an estimated coefficient that should be measured with real balls; ~5% slower on a resting field.
- R10 (Task 8) — the report's ball table lists only balls that took part (fired, collected, scored, stuck, out, or moved more than a ball radius); untouched floor balls and spares are given as counts — 360 rows of idle balls would bury what matters — cost: an untouched ball's row isn't shown (report.json keeps every ball's state).
- R11 (Task 8) — a scenario's [range] file path resolves relative to the scenario file; built-in names unchanged (consistent with how scenario paths resolve) — cost: none.
- R12 (Task 8) — promoted into the fix round: a scenario robot_env that can't be applied (no [robot_program], or an attach-mode session) is now an error instead of silently running the default auto; report.json only gains the range keys when a range is present; the NetworkTables ball-pose cache is cleared on reconnect — cost: none.
- R13 (Task 10) — `launcher aim` marks unreachable distances (CSV row with status, Java comment) and warns with the reachable range, erroring only if nothing requested is reachable — a team asking for 1–6 m should still get the reachable part — cost: none.
- R14 (Task 11) — the battery voltage flipped between 0 and 12.5 V under heavy combined draw because World set it from the previous step's current (a one-step lag). Fixed in the simulator, not hidden in robot code: the bus voltage is now solved within each step. This changes battery sag slightly for every robot (the existing swerve test used to end at 0 V; it now ends at 10.4 V) — cost: sag numbers differ from before.
- R15 (Task 11) — CI budget: the e2e test must not regenerate the full aim table (~7 min); test regeneration on a small subset of distances against the committed table's rows — cost: none.
- R16 (Task 11) — Phoenix scenario threshold: lower to a level the measured spread never misses (e.g. 3 of 8) so CI doesn't flake; document the measured 4–8 spread — spec: looser Phoenix thresholds — cost: a weaker Phoenix check.
- R17 (Task 11, touches Task 10) — the sample holds fire beyond its aiming table instead of firing at the last row's settings; the moving-shot test uses total speed (hypot of vx, vy); the aiming table picks each row to follow the previous one (wheel speed and hood angle together) so interpolation between rows is smooth — the sample is what teams will copy — cost: one regeneration of the table.
- R18 (Task 11) — the sample's existing configs now also create the shooter's devices (warnings in fast mode, 6 more Talons in Phoenix mode); accepted and documented, since "existing robots unchanged" is about Scrimmage's behavior, not the sample robot's own code — cost: a few warnings on old configs.
- R19 (Task 11) — the first battery fix estimated each motor's end-of-step speed from its own inertia only, which made drive-wheel currents read about half their real value (REV current limits read that number). The bus solve now uses each motor's start-of-step speed (the oscillation came from lagging the voltage, not the speed); regeneration is clamped on the bus total; the no-solution case is documented — cost: currents lag speed by one 1 ms step, as before the fix.
- R20 (Task 11) — Phoenix mode runs in real time and scores 0–2 of 8 on a busy machine (2–6 when idle), so any score minimum would make CI fail randomly: the Phoenix scenario checks that all 8 balls fire (and when), and records the score without a minimum — cost: Phoenix mode no longer gates on scoring.
- R21 — speed guard for the bus solve is an evaluation-count test (≤3 supply evaluations per step at steady load), not wall-clock (CI flake) — cost: an indirect speed check.
- R22 — no upper bound on mujoco in pyproject; scenario timing thresholds get a few ticks of margin with a note of the MuJoCo version measured — cost: a future MuJoCo may still move contact behavior.
- Final fix wave — three small issues left as they are: the faster battery solve can be up to ~20 mV off across rare edge cases (≤0.6 mV in realistic runs); a duty-cycle (PWM) shooter's draw at a 0 V bus is overstated (Talon shooters unaffected; only hit on a fallback path); hand-written MJCF configs don't validate [model] timestep — cost: tiny, documented here.
- CI fixes (PR #13) — GitHub's runners vary about 1.5x over a day (the same code measured 1.44x and 0.91x real time), and comparing the range world's step cost with the same robot's without the range didn't hold either (1.5–1.6 locally, 1.92 on a busy runner, against 2.1 for the regression it was meant to catch). So on CI the range speed test only checks that the run goes in lockstep and fires in the timed part; locally it still asserts faster than real time, and test_ball_bodies_fixed_count guards the floor-ball bodies without a clock — cost: CI doesn't catch a range slowdown; run the speed test locally after performance-sensitive changes.
- CI fixes — real-time physics that fell behind caught up in one pass of up to 250 ms with the robot blind (no sensors), so on a slow machine the robot never fed; it now catches up in 5 ms passes. A starved machine's physics runs at 0.7–0.8x instead of ~0.9x — cost: slower catch-up, but the robot sees its sensors.
- CI fixes — a Phoenix-mode test that fails on a machine too slow for real time is skipped only when every failed check is one timing can explain (the range's fired counts; the fast-mode auto's pose and arm and elevator heights (physics skipping ahead made the robot overshoot 0.63 m on a 0.6x runner); the swerve auto's pose and "auto: done"; Tuner X's drove-straight check), and the skip names the failed checks; foreign writes, crashes, missing devices, interruptions and errors always fail — cost: on slow CI runners those timing checks aren't gated.
- CI fixes — the foreign-write echo window keeps our sent values 10 s back (LATE_ECHO), because a starved robot program relays Phoenix's rewrites of our values seconds late — cost: a slightly weaker detector (a robot-side model writing every loop is still flagged within a few loops).
- Parked after the CI-fix review: (a) with the 10 s window a robot without the agent whose Talons write every loop costs ~18 ms of CPU per loop in foreign-write scans (2000-entry history), which can slow physics in exactly the case the detector reports — thin old history if that bites; (b) the batched sensor send writes to the websocket transport without drain(), so a robot paused in a debugger lets the send buffer grow; (c) the batched send relies on websockets internals and falls back to the normal send when they're absent.
