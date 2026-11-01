# Uneven field elements: bumps, ramps and tilting platforms (sub-project 10)

## Why

This season the user's team (FRC 5098, Java, CTRE swerve) turtled a couple of times. The robot hit the 2026 BUMP too
fast, flew off it, landed on FUEL and was stranded on the balls with its wheels off the carpet. They would have liked
to practice how slowly to take the bump. More than that, they'd have liked to *measure* how far the robot leaves the
ground, so they could write tests that fail when an autonomous routine drives over the bump too fast.

Other seasons have different hazards: top-heavy robots, with an elevator up or an arm out, can tip over on a bump or
ramp. A test should be able to check that the robot doesn't tilt past a limit and fall over, in each of its
configurations.

So this sub-project adds:
- uneven field elements to the simulated field: bumps, ramps and a tilting balance platform;
- robot signals for height off the ground, wheel contact, tilt and tipping, which scenario checks can test;
- a standalone sweep that finds how fast a robot can take an element, per configuration.

## What it is

1. **Field elements in range files.** A range file (`scrimmage/testrange`, sub-project 9) gains `[[element]]`
   entries. There are three kinds:
   - **bump**: two ramps meeting at a crest, with an optional flat top and a lip at each toe;
   - **ramp**: an incline up to a flat deck, one-sided or two-sided;
   - **platform**: a deck on a hinge, which tilts under the robot's weight, with ramps, stops and damping.
   All are built from boxes, so the live view and the offscreen renderer draw them with no new rendering code. A
   platform is a hinged body, which the live view already draws as a mover.
2. **The 2026 bumps** in `ranges/2026.toml`: all four, measured from the official field CAD.
3. **New range files:**
   - `ranges/2026-bump.toml`: one bump and some loose FUEL, for focused tests;
   - `ranges/2023-charge-station.toml`: the 2023 CHARGE STATION as a platform;
   - `ranges/terrain-library.toml`: examples of a generic ramp and the 2016 ramparts and 2012 bridge, showing that
     other seasons can be described.
4. **Robot terrain signals**, in `resolve_signal`, the scenario editor's picker and `SIGNALS_HELP`:
   - `robot.ground_gap` (m): how far the robot is off the ground. It is the smallest distance from a drive wheel's
     bottom to the surface beneath it, 0 while any wheel touches. The surface is the carpet or an element; balls
     never count. A robot stranded on FUEL therefore reads its real height above the carpet, and
     `always robot.ground_gap max ...` fails when it turtles.
   - `robot.wheels_down`: how many drive wheels touch the carpet or an element (balls don't count).
   - `robot.air_time` (s): how long the robot has touched nothing at all (truly airborne), 0 otherwise. A robot
     resting on balls isn't airborne.
   - `robot.pitch`, `robot.roll` (deg): the Pigeon 2's convention, so they match a team's gyro logs: nose-down is
     positive pitch, left-side-up is positive roll.
   - `robot.tilt` (deg): the angle between the chassis's up and vertical.
   - `robot.z` (m): the height of the chassis origin above the carpet.
   - `robot.stranded`: 1 while the robot is stuck. That means fewer than 2 drive wheels touch the field or an
     element for more than 0.5 s, while the chassis rests on something (balls, an element edge), not in the air.
   - `robot.tipped`: 1 once tilt passes 60° (latched until reset).
   - `element.<name>.angle` (deg): a platform's tilt.
5. **Reporting:** the report notes the first moment the robot tipped (t, tilt, pose) or was stranded (t, what it rests
   on). That way a failed test reads "tipped over at t=3.2 s with the elevator at 0.9 m", not just a bad pose.
6. **Standalone sweep:** `scrimmage terrain sweep CONFIG --element NAME --speeds 1:5:0.25 [--set joint=value ...]`
   drives the robot straight over one element at each speed, with no robot program. A small built-in driver points
   the swerve modules and holds wheel speed; differential drives are driven directly. One row per speed reports:
   - peak ground gap and air time;
   - landing distance past the crest;
   - peak pitch and roll;
   - whether it tipped or was stranded, and, if there were balls, whether it landed on any.
   `--set` holds mechanism joints at given positions (elevator up, arm out), so one command compares configurations.
   The sweep also reports the fastest speed under the limits the user gives (default: gap ≤ 0.05 m, tilt ≤ 30°, never
   tipped or stranded). That number is the simplified form: the "bump speed limit" a team puts in its auto code.
   Output is CSV plus a short markdown table, like `launcher sweep`.
7. **Sample robot and scenarios:**
   - The fast-mode sample (examples/java_fastmode) gets an auto, "Over the Bump", that drives over a bump under a
     `BUMP_SPEED` constant.
   - Both bump scenarios use a new config, `configs/fastmode_bump.toml`: the sample's elevator-and-arm robot
     (`configs/fastmode.toml`) on `ranges/2026-bump.toml`. The shooting robot's config and tuned scenarios are left
     alone.
   - `scenarios/fastmode_bump.toml` checks `always robot.ground_gap max 0.05` and `always robot.tipped equals 0`.
   - `scenarios/fastmode_bump_elevator_up.toml` runs the same with the elevator raised, and checks tilt.
   - A Python-driven test drives onto the CHARGE STATION and reads the platform's angle.

## Goals

- A team can write a scenario that fails when its auto drives over the bump too fast, using `always` on
  `robot.ground_gap` or `robot.air_time`.
- A team can check a top-heavy configuration doesn't tip, using `always robot.tilt max ...` and `always robot.tipped equals 0`
  in a scenario that raises the elevator before the bump.
- The sweep answers "how slowly do we have to take the bump" in one command, per configuration.
- Turtling on balls happens in the simulation the way it happens on the field: FUEL under the frame can lift the
  wheels off the carpet, and `robot.stranded` reports it.
- The physics is checked against hand calculations:
  - A rigid robot leaving a 15° ramp at speed v lands where projectile motion says, within tolerance. A bumper may
    touch on landing, because a 19 mm bumper clearance gives only a 7° approach angle. The tread stays under bumper
    depth, the robot doesn't rebound, and the bumpers clear the carpet within 0.25 s.
  - A robot parked across a slope tips at the angle its CoM height and track width predict.
  - The CHARGE STATION tilts to its stop under a robot on one end and settles level with a robot centered.
- Ranges without elements are unchanged. `ranges/2026.toml` gains its bumps, because it is the season's field; the five `fastmode_shoot_*` scenarios and their Java e2e tests are re-verified against it. A scenario whose start sits too close to a bump gets its start or check adjusted, and the change is noted in the decisions list.

## Non-goals

- Robot code that auto-balances on the CHARGE STATION. The platform, its angle signal and a Python test are in; a Java
  auto-balance routine in the sample is a later piece.
- Suspension or treads that deform beyond a contact stiffness. No FRC drivetrain in the stock parts has suspension.
- Mechanism collisions with the field (an elevator hitting the TRENCH bar). Mechanisms stay mass-only, as today; the
  TRENCH is a later piece.
- Climbing or hanging (a later sub-project), and the 2016 rough terrain's many blocks (a library example only if
  cheap).
- Driver practice tooling beyond what the live view already gives.

## Anti-goals

- A robot that sails over the bump because the contact is too soft, or bounces like a ball because it's too stiff.
  The landing must look and measure plausibly; the hand-calculation tests are the guard.
- Signals that miss a short hop. Peaks between scenario ticks must not be lost (see Constraints).
- Slowing down every world: worlds without elements pay nothing.

## Constraints

- **Measured geometry.** The 2026 BUMP comes from the official field STEP (FreeCAD) and GE-26100, cross-checked with
  manual §5.5:
  - 44.40 in deep along the field, 73.0 in wide, 6.51 in tall;
  - two ½ in HDPE ramps at 15.0°, with a matte orange-peel finish;
  - no flat top: a sharp ridge about 8 mm wide;
  - each ramp ends at the carpet in a 0.61 in lip;
  - crest lined up with the HUB center, 182.1 in from the alliance wall; four bumps at (±3645, ±1522) mm from the
    field center;
  - across the field: guardrail, TRENCH, BUMP, HUB, BUMP, TRENCH, guardrail.
- **The 2023 CHARGE STATION** (manual §5.4, GE-23300):
  - a 96 × 48 in platform, 9⅛ in high when level, ±15° tilt;
  - a double hinge, axes 5.30 in apart and 6.57 in above the floor;
  - 15⅛ in ramps; LEVEL within 2.5°;
  - CAD mass 209 lb (94.8 kg); inertia 24.6 kg·m² about its CoM, which sits 7.27 in up.
  Its stops and damping aren't published. They are estimates, marked as such. The double hinge's gravity-restoring
  torque (about 63 N·m) comes from the geometry, not a spring.
- **Physics.** Timestep ≤ 1 ms, as everywhere.
  - Wheel contact gets a tread stiffness and damping per drivetrain part (`tread_stiffness`, `tread_damping`). The
    defaults are documented estimates (TPU/Colson-like), converted to MuJoCo solref so the static sag is force / stiffness,
    using MuJoCo's solver mass at the wheel (1 / body_invweight0), a contact impedance of 0.8 and per-shape contact
    counts. Landings behave softer than a bare spring. The chassis frame underside and the bumpers keep colliding, as today.
  - HDPE ramp friction against the wheels is an estimate (documented, default 0.6 against carpet's 1.0).
  - The tread defaults apply to every robot, not only worlds with elements: one robot must behave the same on every
    field. Every existing test is re-run locally. A number that moves (pose, odometry, timing) is re-baselined with the
    old and new value noted in the decisions list; a test that changes meaning is a stop, decided before going on.
- **Signals at physics rate.** Scenario checks tick every lockstep step (5 ms). The terrain signals keep a peak-hold
  over the physics steps between ticks, for `ground_gap`, `air_time`, `tilt`, `pitch` and `roll` (largest
  magnitude). A tick then sees the largest value since the last tick. `wheels_down` is a minimum-hold.
- **Cost.**
  - Elements are a handful of boxes and at most one hinge each.
  - Ground gap uses one downward `mj_ray` per drive wheel per physics step, only in worlds that have elements or a
    scenario that reads the signal. A world with neither pays nothing.
- **Determinism.** Lockstep runs stay bit-identical run to run.
- **License.** As for every sub-project: no transforming com.ctre classes, no interposing CTRE natives, no decompiling
  Phoenix, and no non-public C APIs.
- **CI budget.** The user has fewer than 100 GitHub Actions minutes until it resets on 2026-11-01. Verify everything
  locally, including the Java e2e tests, and open the PR when it's ready, with one CI run.

## Calls made by Claude (the user can change these)

- Elements live in range files, not a new `[field]` syntax: ranges already own the season's field geometry and start
  poses.
- Everything is built from boxes. Ramps are rotated boxes, so they render and collide with no meshes, which the live
  view's world geometry would skip.
- `robot.ground_gap` measures from wheel bottoms to the surface beneath, by ray. The other choice, chassis height,
  mixes in the bump's own height, and that isn't what "how far up off the ground" means.
- Tipped means tilt above 60°, latched. Stranded means fewer than 2 wheels down for 0.5 s while resting on something.
  Both thresholds are named constants, documented.
- The sweep drives with a built-in Python driver, not the robot program, so it's fast and needs no Java. Robot-code
  behavior is what the scenarios test.
- The "simplified form" asked for by the fidelity guideline is the sweep's speed limit per configuration. The full
  MuJoCo model is cheap enough to run inside every scenario, so no separate reduced model is fitted.
- Default sweep limits: gap 0.05 m, tilt 30°, never tipped or stranded.
- A CHARGE STATION auto-balance in Java is left for later.
- From the builder check:
  - The 2026 range file gets its real bumps, and the shooting scenarios are re-verified. A test field without the bumps
    would let a team's auto pass in simulation and hit the bump on the real field.
  - The tread defaults apply everywhere, re-baselined. The same robot gives one answer whatever the range file.
  - The bump scenarios get their own config, so the shooting robot's tuned scenarios don't move.
  - Balls never count as ground, for `ground_gap` and `wheels_down`. Turtling then shows up as a gap and as fewer
    wheels down.

## Left to the builder

- The exact tread stiffness and damping defaults, and the HDPE friction estimate. Pick plausible values, cite what
  they're based on, and show the landing test passes.
- How the platform's stops are modeled: a joint range with a soft limit, or stop geoms.
- How the double hinge is modeled: two hinges, or one hinge with an equivalent restoring torque. Either way, the
  tilt-under-load test must match the geometry.
- The sweep's run length, approach distance and settle time.
- Report wording, in the style of existing notes.

## Decisions made during the build

Each is what was decided, why, and what it costs if wrong.

- **Geom group.** Carpet and element geoms go in group 2 (`ELEMENT_GROUP = 2`, not 4), and the ray filter selects
  group 2 alone. MuJoCo hides groups 3-5 by default, so group 4 would have dropped the carpet and elements from
  screenshots, and nothing else uses groups. Cost if wrong: a later feature that wants group 2 must move.
- **Element names.** `ranges/2026-bump.toml` names its element `bump`; `2026.toml` names its bumps `bump_blue_left`,
  `bump_blue_right`, `bump_red_left` and `bump_red_right`. The sweep command and the e2e tests need fixed names.
  Cost if wrong: a rename.
- **The 2026 range test.** `test_range_without_elements_unchanged` now asserts the four 2026 bumps and still checks that
  a range file without `[[element]]` tables (a tmp file) has `elements == ()`. Cost if wrong: none.
- **Platform footprint.** The footprint is (length + 2 ramp_length) x width, `length` being the deck's depth along the
  tilt direction (the CHARGE STATION: length 48 in, width 96 in, ramps beyond the deck ends, about 76 in overall). It
  matches the GE-23300 layout. Cost if wrong: a footprint check off by a ramp length.
- **Two hinges per platform.** A platform keeps the real double hinge, gravity-restored with no spring, against the
  "at most one hinge each" above. It is the real mechanism and the spec left it to the builder. Cost if wrong: two
  movers per platform in the live view and slightly more work per step.
- **Platform tilt lookup.** `RangeLayout.platforms` maps a name to the (tilt_plus, tilt_minus) qpos addresses, and the
  tilt is their sum. Cost if wrong: none.
- **Tread solref.** `tread_solref(k, zeta, solver_mass, contacts, timestep)` is applied after compile, because only that
  signature matches the static sag (within 0.2 %). Cost if wrong: none.
- **Tread stiffness.** The default stays at 2e5 N/m with contact impedance 0.8; tuning an estimate around a solver
  artifact isn't justified yet. Cost if wrong: landings sink about 16 mm on a 0.15 m drop, close to the bumpers' 19 mm,
  so heavy robots may scrape.
- **A wheel is down** when it has a contact or its ray gap to the carpet or an element is at most
  `WHEEL_DOWN_GAP = 0.005 m`. Wheels lose contact on 70 %+ of steps in a scrubbing turn, so contact alone would flicker
  `wheels_down` and set stranded falsely. Cost if wrong: a wheel hovering under 5 mm counts as down.
- **Re-baselined by the tread change** (Task 3): the rangeless golden swerve heading went from -18.1 deg to -19.7 deg
  (quat z -0.15755 to -0.17139), and the `shoot_moving` check `by_time score.hub_blue >= 18` moved from t 11.8 to 12.0.
  The tread defaults apply to every robot, as the Constraints above require. Cost if wrong: none beyond the new numbers.
- **Pitch and roll signs.** `robot.pitch` and `robot.roll` equal `bindings.pigeon_attitude` exactly (Pigeon 2: pitch
  nose-down positive, roll left-up positive), and the earlier "nose-up is positive pitch" text is corrected. A team
  comparing sim with gyro logs would otherwise see a flipped sign. Cost if wrong: one sign flip.
- **Joints at the event.** The notes' `{config}` joints are captured when the tip or strand happens, not at the step's
  end, since "with the elevator at 0.9 m" is about the configuration that tipped. The positive moved-joint test is not
  skippable. Cost if wrong: a little more monitor state.
- **Landing test.** "While nothing scrapes" became penetration < 19 mm, rebound < 2 mm and bumpers off the carpet
  within 0.25 s, and the goal line is amended. A stock bumper 19 mm up with 0.15 m overhang has a 7.2 deg approach
  angle, so it touches on a 13 deg nose-down landing at every tread stiffness in range. Cost if wrong: a landing check
  that allows a bumper kiss.
- **`bumper_clearance`.** The stock chassis gets this Param (m, bumper bottom above the carpet; default 0.02, so
  existing robots don't change). `docs/terrain.md` explains the approach angle, atan(clearance / overhang). The sample
  must be able to cross the bump and the stock default can't. Cost if wrong: one more chassis parameter.
- **HDPE friction gap parked.** MuJoCo combines friction by max, so the 0.6 element friction never applies to treads
  (1.1-1.2). Making the element win would also replace the tread's solref on elements. It is documented as a known gap;
  climbing 15 deg needs mu > 0.27, so traction isn't the limit. Cost if wrong: slides on HDPE are understated.
- **Flight test.** It compares matched events: the measured takeoff state (speed within 5 %, vz <= v sin15 x 1.1 + 0.05,
  so no spring launch), then the projectile prediction against the CoM x at first touch, at matched height, within
  0.02 m. The idealized crest-takeoff event can't pass as built (the robot leaves 0.28 m later, on the deck, nearly
  flat). Cost if wrong: the test checks flight from the real takeoff, not the idealized one.
- **Sweep keeps the floor balls.** The sweep uses the range file's floor balls (2026-bump's FUEL row past the toe), and
  `docs/terrain.md` says the limit reflects them. Turtling on FUEL is the user's story. Cost if wrong: a stricter limit
  than a bump with clear carpet would give (2 vs 2 m/s elevator down, 1 vs 1.5 elevator up).
- **Sample bumper clearance.** `configs/fastmode_bump.toml` uses `bumper_clearance` 0.08 m, because 0.06 m just fails
  breakover at the crest (needs 0.068). Cost if wrong: none.
- **Sweep fixes.** Reset-independence (sweeping [a, b] and [b] gives the same row for b) is tested, and the docs say
  `crossed` assumes torque up to traction. Cost if wrong: none.
- **Keep the bumps in the sample's test field.** Shoot Moving is rerouted to fire while moving laterally in the alliance
  zone, clear of the bump toes, with no `elements = false` switch; a field without bumps would let an auto pass in
  simulation that strands the real robot, which is what this sub-project exists to catch. The preload fires on the move
  along x 2.6, then the collected FUEL is fired standing. Re-baselined (old to new): fired 8 by 3.0 to 3.05 s; score
  6 to 4 by 3.8 s; score 18 by 12.0 to 16 by 14.8 s; end score 18 to 16; fired 21 to 24; speed scenario score 6 to 4.
  Shoot Left/Right starts moved from x 3.55 to 3.42 (range and `RobotContainer`), the firing deadline 2.05 to 2.15 and
  the score deadline 3.25 to 3.35. The score is sensitive to position (it scores only 1.75-2.05 m from the hub, and
  odometry drifts 0.13-0.2 m through the FUEL), and passes because fast mode repeats exactly; the TRENCH isn't modeled.
  Cost if wrong: weaker shoot-on-move coverage (the user should confirm), and the left and right starts sit behind the
  starting line.
- **Bump speeds.** `BUMP_SPEED` 2.0 and `BUMP_SPEED_ELEVATOR_UP` 1.0 m/s, from sweeps on `configs/fastmode_bump.toml`
  with 0.08 m bumpers. Cost if wrong: none.
- **Failing-speed e2e.** It uses `SCRIMMAGE_BUMP_SPEED` 3.0, not 2.5: the Java robot's own drive slows to about 2 m/s on
  the climb, lifting only 0.043 m at 2.5, while 3.0 lifts 0.082 m and fails on the gap alone. This also shows the sweep's
  ideal driver errs safe. Cost if wrong: none.
- **`SCRIMMAGE_BUMP_SPEED`.** The sample reads this optional env var (m/s), overriding `BUMP_SPEED` in simulation only, as it
  already reads `SCRIMMAGE_AUTO`; the faster-speed e2e sets it through `robot_env`. Cost if wrong: a sim-only knob in
  sample code.
