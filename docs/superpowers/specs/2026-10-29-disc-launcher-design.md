# Disc launcher and disc flight (sub-project 11)

## Why

The user asked for mechanisms that past FRC games used, and named a disc (Frisbee) launcher first. 2013's ULTIMATE
ASCENT was the disc game: teams shot 11 in discs into slot goals on the alliance wall. A disc behaves very differently
from a ball:
- It leaves the launcher rolling along a fence. With a single wheel, its exit speed is about half the wheel's surface
  speed, and its spin comes from the fence's grip.
- In flight it is a wing. Lift carries about half its weight at 12 m/s (46 % with the default coefficients, 60 % with
  HH02), so a ballistic model badly under-predicts
  its height.
- A pitching moment, through gyroscopic precession, banks the disc and curves its path. So spin matters: a disc with
  too little spin wobbles and falls.

The user's fidelity guideline applies (from sub-project 8 on): each mechanism gets a standalone component model that can
be tested on its own. It also gets a simplified form for the whole-robot simulation, possibly fitted from the
higher-fidelity component model.

This was designed without the user (decide-and-document, authorized 2026-10-29). Every call is listed under "Calls made
by Claude".

## What it is

1. **A disc launcher component model,** `scrimmage/disc/launcher.py`.
   - It is a planar model: the disc lies flat in the shooter's channel and moves in its own plane. Its state is x, y
     and spin. Wheels and a fixed fence (straight or arc) touch its rim.
   - Each contact is two springs in series (the wheel tread k_w, the disc rim k_d) with damping, and regularized
     Coulomb friction (the existing `contact.friction_force`).
   - Wheels are driven by the existing `ShaftModel` with its velocity controller and the battery, one shaft per wheel
     group.
   - The supported layouts:
     - one or more wheels in series on one side against a fence (linear);
     - one wheel with an arc fence;
     - two-wheel pinch, where independent wheel speeds set the spin.
   - Guide rails above and below add sliding friction from the disc's weight.
   - Output per disc: exit speed, direction, spin (sign from the contact physics), exit position, wheel speed droop,
     slip energy and contact time. The status strings are the same as the ball launcher's (launched, jammed,
     no_contact).
2. **A disc flight model,** `scrimmage/disc/flight.py` and `aero.py`.
   - It is a 6-DOF rigid body (position, velocity, orientation quaternion, body angular velocity), integrated with RK4
     at 1 ms in the standalone model.
   - Aerodynamics follow Hummel (2003), as Hubbard and Hummel corrected them:
     - lift, drag, pitching moment, roll moment and spin-down;
     - α measured between the velocity and the disc plane;
     - reference area πd²/4, moment arm d.
   - Coefficients come from a single `aero.py` function used by both the standalone and the in-world model:
     - lift, drag and pitching moment are Potts & Crowther's (2002) tables against α (spline or linear), with a linear
       extension beyond the table;
     - damping uses Hummel's long-flight values (C_Mq −0.0144, C_Rp −0.0125, C_Nr −3.41e-5) in his dimensional form;
     - C_Rr comes from the P&C table against α and advance ratio.
   - Coefficient sets are presets, so a user can pick P&C plus the long-flight set (the default), Hummel's
     short-flight set or HH02.
   - Scoring: the disc's center path crosses a goal's opening rectangle.
3. **Disc presets, in `scrimmage/disc/presets.toml`.**
   - `2013-frisbee`: 0.2794 m, 0.180 kg, 35.6 mm tall, from the manual.
   - Inertia is the Ultra-Star's, scaled by m·d²: Izz 0.0026 and Ixx 0.00135 kg·m², an estimate.
   - Rim stiffness k_d is an estimate.
   - Every value carries `# source:` or `# estimate:`, as in `launcher/presets.toml`.
   - Tread and fence friction presets cover urethane 60A, pneumatic, wedgetop, bare polycarbonate and PTFE.
4. **A `scrimmage disc run|sweep` CLI,** the same shape as `launcher run|sweep`.
   - It reads a setup TOML (disc, launcher layout, wheels, shaft, control, battery, air, goal, feed, robot) and reports
     each disc: exit, flight, landing or goal crossing, and whether it scored.
   - It uses the existing sweep, CSV and report plumbing.
5. **Discs in the simulated world,** `scrimmage/testrange`.
   - A range file can name a disc preset in place of a ball preset (`[range] disc = "2013-frisbee"`).
   - Discs are free bodies with cylinder geoms and the disc's diagonal inertia.
   - In flight they get the Hummel force and torque each physics step through `xfrc_applied`, the same `aero.py`, so
     a disc launched in the world banks and fades like the standalone model.
   - Parking, ledger states and scoring reuse the ball machinery.
6. **2013 goals,** `ranges/2013.toml`.
   - Rectangular slot goals on the wall, from manual §2.2.3:
     - HIGH: 54 × 12 in, bottom at 104⅛ in;
     - two MIDDLE: 54 × 21 in, bottom at 88⅝ in;
     - LOW: 29 × 24 in, bottom at 19 in.
   - Each goal is a box behind its opening, about 24 in deep (an estimate; the GE drawings weren't found), with a back
     wall.
   - A new opening kind `rect` sits beside the regular-polygon `Opening`. The scoring surface is the opening rectangle
     on the wall plane.
   - Also in the file: starts on the auto line, the alliance wall, and the pyramid as a simple obstacle (four legs and
     its footprint, for driving around; not climbable here).
7. **A disc shooter robot part,** `scrimmage/parts/stock/mechanisms.py` `disc_shooter`.
   - A linear channel with wheels on one side, a fence, and a tilt (launch angle) joint like the ball launcher's hood.
   - It plugs into the world like the ball launcher (sub-project 9's Shooter, with an external shaft on the battery).
   - In the world, the disc launcher's output starts the disc's free body with its orientation (the disc plane tilted
     to the channel) and spin.
   - A hopper of up to 4 discs (rule G24) feeds a stage that pushes the next disc in.
8. **The simplified form for the whole robot.** The in-world shooter runs the planar component model directly, since
   it is cheap: one disc, a few contacts, a 1e-4 s substep. So no fitted reduced model is needed.
   `scrimmage disc table SETUP` fits and prints an exit table (exit speed and spin against wheel rpm, per layout) for
   teams that want the simple numbers for their own code.
9. **Scenarios.**
   - A Python-driven test robot (the `range_fixtures` PWM drive pattern, no Java) on `ranges/2013.toml` shoots 4 discs
     from the auto line.
   - Scenario checks use the existing signals `fired`, `score` and `score.<goal>`.
   - A Java sample robot for discs is a later piece; see Non-goals.

## Goals

- `scrimmage disc run` on the reported 2013 multi-wheel layout (four 60A rollers, 1/2 in compression) gives an exit
  speed of 0.35–0.48 × the wheel surface speed, the measured range from 2013 belt and multi-wheel shooters. A single
  60A roller at 1/4 in falls short (0.26): it is limited by normal force and the roller's stored energy. That case
  stays an expected failure until the disc's rim stiffness is measured.
  - A rolling launch gives an advance ratio rΩ/V near 1.
  - A slick fence does not kill spin. A rim push alone spins the disc at rΩ/V = m r²/Izz ≈ 1.35, and fence friction
    brings it to rolling (1).
  - A two-wheel pinch at equal speeds gives near-zero spin.
- **Flight agrees with physics and the literature:**
  - With the aero switched off, flight is exactly a parabola.
  - Hummel's flight f2302, replayed with the P&C plus long-flight set, matches his simulation S-3: range about 15.2 m
    within 0.5 m, final ẋ 8.6 m/s within 0.5, and lateral curve to the side his S-3 gives.
  - The measured FRC shot holds: 12.2 m/s from 0.76 m on the auto line reaches the high goal's height band at a
    launch of 20–28°, as the research reproduced with lift at 22–25°.
- **Spin direction comes out of the contact physics:** a wheel on the right of the channel gives counter-clockwise spin
  seen from above, and the disc banks the way precession says.
- **Determinism:** the in-world disc and the standalone model agree. With the same exit state and no collisions, the
  two flight paths match within 5 cm over 5 m with the same bank side (MuJoCo integrates the same forces at 1 ms with implicitfast; the standalone uses RK4).
- **Robot test:** the test robot can score from the auto line into the high goal in a scenario, with a `by_time` check
  on score.
- Ball ranges and the ball launcher are unchanged.

## Non-goals

- A Java fast-mode sample robot for discs, with AimTable-style tables. That is a later piece if the user wants it.
- Disc–disc stacking physics in a hopper beyond a simple single-file queue (as `BallPath`).
- The pyramid goal and climbing the pyramid. Climbing is its own sub-project.
- A rotating-arm or belt shooter. Belts are close to many wheels in series; the arm is a different mechanism.
- Out-of-plane disc buckling in the launcher. It is listed as a known accuracy effect, not modeled.
- Robins–Magnus side force: an optional C_Y term defaulting to 0.

## Anti-goals

- A disc that flies like a ball. Flight without lift under-predicts its height at the wall by about a meter from
  the auto line (0.96–0.99 m measured), enough to miss the goal, and the tests must catch that.
- Spin or bank direction hard-coded instead of coming from the contacts and precession.
- Numbers presented as measured that are estimates. The disc's rim stiffness, inertia and tread-on-polyethylene
  friction are all estimates until measured, and the docs give a measurement recipe for each.

## Constraints

- **Physics.** Timestep ≤ 1 ms in the world. The launcher substep is its own (as `launcher.Launcher.default_dt`).
  Lockstep stays bit-identical run to run.
- **Sources.** Every number in presets, ranges and docs is tagged as source or estimate, with the manual section,
  paper table or Chief Delphi thread. The research notes:
  /tmp/claude-1000/-home-altainia-code-scrimmage/ac449d14-9b89-46fa-a7f9-a841ce5dbaa6/scratchpad/sp11-disc-research.md.
  Copy what's needed into `docs/disc.md`; the notes themselves are not committed.
- **Reuse.** ShaftModel, VelocityController, the battery, `contact.friction_force`, the sweep and report plumbing, the
  BallState ledger, parking and `Surface.crossed`. Don't fork them.
- **License:** never transform com.ctre classes, interpose CTRE natives, decompile Phoenix or use non-public C APIs.
- **CI budget.** All verification is local, and the PR opens on or after 2026-11-01 with one CI run. Pin floats with
  tolerances, never exact (the Python 3.11 lesson).

## Calls made by Claude (the user can change these)

- The launcher model is planar, with the disc's x, y and spin and spring-damper contacts with friction. A MuJoCo
  sub-simulation would be slower, and the planar model captures rolling against the fence, slip and droop.
- Flight uses the Hummel 6-DOF form with Potts & Crowther coefficients and Hummel's long-flight damping, Hummel's own
  recommendation. Other sets are presets.
- The 2013 disc is treated as an Ultra-Star aerodynamically, with ±30 % uncertainty on C_L0, C_M0 and C_Rr. Nobody has
  measured the 2013 disc.
- Goals are slot rectangles, and scoring is the disc's center crossing the opening. The manual's "any part crossed and
  in the goal at the end" is approximated by the center crossing plus the goal box holding it.
- The goal depth is about 24 in, an estimate; the field drawings weren't found.
- The whole-robot form runs the component model directly, because it is cheap.
- The robot test uses a Python test robot, not Java.
- Disc capacity is 4, from rule G24.
- From the builder check:
  - **Hummel's exact forms.** The flight model copies Hummel's own code (thesis App. A/B, simulate_flight.m and
    discfltEOM.m): the P&C tables including the full C_Rr(α, AdvR) table, and his dimensional rate terms (roll
    √(d/g)-scaled, spin-down without q∞Ad). The long-flight damping values were identified in that form. The thesis
    PDF is at /tmp/claude-1000/-home-altainia-code-scrimmage/ac449d14-9b89-46fa-a7f9-a841ce5dbaa6/scratchpad/dl2/HummelThesis.pdf.
    The forms are cited in `aero.py` and docs/disc.md, and the PDF is not committed. If a table can't be read
    exactly, the f2302/S-3 goal becomes range within 1 m and the curve's side only, recorded as a decision.
  - **World against standalone.** The world keeps its integrator (implicitfast, 1 ms): changing it would touch every
    range and lockstep. The agreement goal is 5 cm over 5 m, with the same bank side, not 1 cm. The integrator
    difference is documented.
  - **The FRC shot.** The default coefficient set stays P&C plus long-flight. The measured-shot check accepts a launch
    angle of 20–28° into the high-goal band. If the default set misses even that, the builder reports the angle it
    needs rather than tuning coefficients: calibration waits for measurements.
  - **Scoring counts on the crossing,** as for balls, with no revocation if a disc bounces out. Bounce-outs off the
    back wall are rare with a 24 in box; a known approximation.
  - **The test robot drives the disc shooter open loop,** at a fixed voltage chosen for the target wheel speed, as the
    PWM fixtures do. A closed-loop Python controller isn't needed for the scenario.

## Left to the builder

- Spline or linear interpolation of the P&C tables, and the extension beyond them (stall near 45°).
- The launcher's contact discretization, substep and jam rule.
- The fence geometry representation for an arc.
- How the exit orientation is set: the disc plane follows the channel tilt, plus any pitch rate from uneven release.
- The pyramid obstacle's exact shapes.

## Decisions made during the build

Each is what was decided, why, and what it costs if wrong.

- **Extra presets.** Task 1 also defines the `ultra-star` disc preset (thesis Table 3-1) and an aero set `none` (all coefficients zero), because Task 2's tests need them. No cost if wrong.
- **External bus.** `DiscLauncher` takes `external_bus: bool = False`, meaning what it does for `launcher.model.Launcher` (the shafts read the world's bus voltage, and the world counts their current), because Task 6 depends on it. No cost if wrong.
- **Fake robot shooter mode.** Task 7 extends `tests/fake_robot_main.py` with a shooter mode (a fixed shooter voltage and feeding preloads at a set time), like `--drive`, because scenarios need a robot program and the fake robot is the project's Python stand-in. The cost if wrong is test-only code.
- **Aerodynamic details (Task 1).** Follow the sources, or avoid a non-physical spline artifact: (1) zero-lift tests are per set, since the P&C table has C_L = 0 at -3.0 degrees exactly (thesis p. 82) and HH02 in [-4.6, -4.4] degrees, where the brief's [-4.5, -3.5] mixed the linear fit with the table; (2) the C_Rr spline is kept where well-behaved (alpha -5 to 15 degrees, AdvR 0 to 1.04) and interpolated linearly across its overshooting gaps; (3) for reversed spin, the spin-induced part of C_Rr is mirrored; (4) hummel-short C_Nr is -7.1e-6 (Table 3-2), not the code comment's positive value; (5) the linear sets get the bounded post-30-degree treatment; (6) aero is zero below 0.01 m/s. If wrong, roll moments for FRC discs past alpha 15 degrees differ from Hummel's spline (it swung to +0.2 where the table maxes at 0.019).
- **No-lift test (Task 2).** The check is "over 0.9 m lower at the wall and misses the opening" (measured 0.96-0.99 m at the scoring angles), and the spec's anti-goal "more than a meter" is amended to "about a meter, enough to miss the goal", because the 1 m figure was the designer's estimate and the test still catches a disc that flies like a ball. No cost if wrong.
- **Single-roller exit ratio (Task 3).** The 2013 band (0.35-0.48) is tested on the reported multi-wheel layout (four 60A rollers at 1/2 in: 0.417 at the time, 0.408 after the exit-guide fix). The single-roller 1/4 in case (0.255 at the time; 0.266 after the exit-guide fix) stays a strict xfail pending a measured disc rim stiffness (k_rim 5000 N/m is an estimate), as sub-project 8 did for ball stiffness, and the spec goal is amended. The 2013 measurements came from belt or multi-wheel shooters, and one roller is normal-force and energy limited. If k_rim is really stiffer, the single-roller case undersells.
- **Slick fence (Task 3).** The slick-fence goal was wrong physics: a rim push alone gives AR = m r^2/Izz = 1.35, and fence friction brings it to 1 (rolling), so a slick fence doesn't kill spin. The test is now `test_slick_fence_does_not_reduce_spin` and the spec is amended (the research's reading of the "bare polycarbonate" report was mistaken). No cost if wrong; the physics is derived.
- **Two minors promoted (Task 3).** Remove the wall-clock < 1 s assert (CI runners vary, and a failure costs scarce Actions minutes) and fix the `insert(t)` clock consistency. No cost if wrong.
- **Clock rule carried to Task 6.** The world must step the `DiscLauncher` every physics tick with the world dt, or feed with `launcher.time`; `insert(t)` raises unless t is within 1e-9 relative of `launcher.time`. If wrong, world feeding hits a ValueError.
- **Exit yaw (Task 4).** The exit yaw artifact is fixed in the launcher model: guide-wall contacts are at least critically damped, so the recoil dies in the guide, with a test that |yaw| < 1 degree for channel lengths 0.50-0.70 m. A real exit rail sends the disc along the channel; the yaw was the phase of a lightly damped wall oscillation whose sign flipped with channel length, and Task 6 consumes `yaw_deg`. If wrong, the model can't show real exit-guide misalignment (none is modeled anyway).
- **Commit trailers (Task 4).** They name the model that wrote the commit (the subagent's own attribution reminder), with no amend. No cost if wrong.
- **Sweep plumbing (Task 4).** Un-fork it by parameterizing `launcher.sweep` (load, parse, overrides, run, error, defaulting to the ball's), because the constraint is "reuse, don't fork". No cost if wrong.
- **2013 field size (Task 5).** The world keeps its 8.07 x 16.54 m field for 2013 (the real 27 x 54 ft is 8.23 x 16.46 m), with the range measured from the goal wall, because the field size isn't per-range today. If wrong, field walls are about 8 cm off for 2013.
- **Three minors carried into Task 6 (Task 5).** (1) A backing plate in the alliance-wall plane over the goal band, with the openings as holes, so misses rebound instead of leaving; (2) in-world disc aero uses `self.air.density`, not the constant RHO; (6) starts moved inside the auto zone (between the wall and the auto line). (6) matters for Task 7's auto-line shot, (1) for rebound and spare accounting, and (2) for consistency with the ball path. No cost if wrong.
- **Center auto-line start (Task 6).** `auto_line_center` maps to pyramid_back (13.94, 4.035, 0), and `auto_line_left`/`right` sit inside the auto zone at 11.65, +/-1.79 m off center, because the pyramid base fills the middle of the zone just behind the auto line. If wrong, there is no center auto-line start.
- **Tilt joint (Task 6).** The `tilt` joint exists only with `tilt = "articulated"`; the fixed default sets the angle by a param, as the ball launcher's hood does, for a consistent convention. No cost if wrong.
- **Narrow scoring band (Task 7).** Accept the band of 21.5-23.75 degrees (tilt 22.5, full PWM output). It's physics: a 12 in slot less 1.4 in of disc at about 13 m/s over 5.2 m is about 0.1 m per degree. If the disc or wheel estimates change, the test fails loudly and the tilt is re-centered from docs/disc.md's table. The cost if wrong is re-centring after measurements.
