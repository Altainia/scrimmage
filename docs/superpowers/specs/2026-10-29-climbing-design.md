# Climbing and swinging: bars, hooks and climbers (sub-project 12)

## Why

The user asked for endgame mechanics from past games and named climbing what were essentially monkey bars, which is
2022's RAPID REACT HANGAR. A climb is where robots get damaged and matches are lost:
- a hook that misses or walks off the rung;
- a robot that swings into the next bar;
- a climb that is too slow because it waits for the swing to die out (a full swing takes 1.6–2.1 s, and hook friction
  removes only 2.5–6° per cycle).

Teams can't practice this safely or often. A simulation that shows the swing, the hook loads and the timing lets them
test a climb sequence before risking the robot.

The user's fidelity guideline applies: a standalone component model that can be tested on its own, and a form the
whole-robot simulation uses.

This was designed without the user (decide-and-document, authorized 2026-10-29); every call is listed below. The
research notes with sources are at
/tmp/claude-1000/-home-altainia-code-scrimmage/ac449d14-9b89-46fa-a7f9-a841ce5dbaa6/scratchpad/sp12-climb-research.md.
What's needed is copied into `docs/climbing.md` with citations; the notes themselves are not committed.

## What it is

1. **Field elements for climbing**, as `[[element]]` kinds in range files (the field-elements machinery):
   - **`bar`**: a fixed horizontal pipe at a height (center, yaw, length, outer diameter, height of its axis, and
     friction), between two posts.
     - An optional vertical compliance (`stiffness`, `damping`) puts the pipe on a sprung slide. The research gives
       80–320 kN/m for 2022's rungs. It makes re-hooking impacts realistic; leave it out for a rigid bar.
     - Optional `level` and `points` say what hanging from it is worth.
   - **`box`**: a generic static box (a truss, a floor protector, a HAB step), with `ground = true|false`. Only ground
     boxes count as floor for the terrain signals.
   - **`switch`**: a tilting hanging bar like 2020's GENERATOR SWITCH. It reuses the platform's hinge, stops and
     damping: hinge height, bar length and height below the hinge, mass, CoM depth, ±stop, damping and a level
     threshold.
   - Bars and non-ground boxes go in a geom group that isn't ground (the terrain monitor's ground is group 2), so a
     robot hanging from a bar isn't "resting on the floor".
2. **Range files:**
   - `ranges/2022-hangar.toml`: the four rungs from drawing GE-22100 (1.66 in OD Sch 40 pipe), the truss and the
     floor protection, with their levels and points (4, 6, 10, 15). Only the hangar, not the whole 2022 field.
   - `ranges/2020-switch.toml`: the GENERATOR SWITCH (±14.5°, about 42 kg, CoM 0.66 m below the shaft, level within
     8°).
   - `ranges/2013-pyramid.toml`: the three pyramid rungs at 30, 60 and 90 in, as bars on the pyramid's legs.
3. **A climber robot part:** stock `climber`.
   - A telescoping arm: a slide chain like the elevator, with motor slot `extend` and an encoder.
   - Colliding hooks at its tip. A hook is a J of capsules whose throat fits the bar, plus a spring-loaded latch on a
     passive hinge: the bar pushes it open on the way in, and it closes behind the bar. An optional `release` motor
     slot opens the latch.
   - An optional pivot (hinge, motor slot `pivot`) tilts the arm for reaching the next rung, as rotating-arm
     traversal climbers do.
   - Hook friction is a material preset: steel 0.5, polycarbonate 0.35, UHMW 0.15, nitrile tread 1.0. These are
     estimates; the research tabulates their sources.
   - A robot can carry two climbers (fixed and rotating) for a traversal.
   - **The robot is held by contact, not by welds.** The hook carries the robot's weight through MuJoCo contacts,
     and their friction about the bar damps the swing. A missed or walking hook is therefore physical, not scripted.
4. **Climb signals** for scenario checks:
   - `climb.hanging`: 1 while the robot touches no ground and is held by a bar or switch.
   - `climb.level` and `climb.points`: the level and points under the range's `[climb] rule`, 0 when not hanging.
   - `climb.swing`: the angle of the robot's CoM about the bar it hangs from, in degrees from vertical.
   - `climb.hook_load`: the total contact force on one whole hook, taking the largest hook, in N, peak-held between
     ticks like the terrain signals. Per-hook signals are `climb.hook.<name>.load`.
   - `element.<switch>.angle`.
   - The terrain monitor's `stranded` no longer fires for a robot held by a bar.
5. **A standalone climb run:** `scrimmage climb run CONFIG --script SCRIPT [--out DIR]`.
   - It runs a robot config on a climbing range with a climb script: a TOML list of steps. Each step drives a
     climber joint to a position or holds a voltage, then waits for a time, a joint position, a swing phase
     ("swing angle crosses 0 heading forward") or a hook contact. No robot program is needed.
   - It reports a timeline (levels reached and when), peak hook load, swing amplitude and decay, and success or
     failure (fell, hook walked off, hit a bar), as CSV and markdown.
   - This is the component model. The same physics runs in the world for whole-robot scenarios, so no reduced model
     is fitted: the full one is cheap enough.
6. **Sample and scenarios:**
   - `configs/climber_robot.toml`: the kitbot with two climbers (a fixed-hook telescope and a rotating telescope),
     on 2022-hangar, starting under MID.
   - `examples/climb/high.toml`: a climb script to HIGH (MID, then a swing to HIGH).
   - `examples/climb/traversal.toml`: a stretch script to TRAVERSAL.
   - `scenarios/climb_high.toml` runs the fake robot (tests/fake_robot_main.py, a new `--climb` mode playing a timed
     open-loop script) and checks `by_time climb.points >= 10 by t=...` and `always climb.hook_load max 4000`.
   - A Java sample climber is a later piece.

## Goals

- **The swing is physical:**
  - hanging free, the swing period matches the compound pendulum T = 2π√((d² + k²)/(g d)) within 5 %, with d and k
    from the built model;
  - with hook friction μ, the amplitude falls about linearly at Δθ ≈ 4 μ r/d per cycle (Coulomb), within a factor
    of 1.5.
- **A robot hangs and holds.** A 60 kg robot hung by two steel hooks stays put for 30 s (creep < 1 cm). An unlatched
  open J hook holds a swing until its load reverses (measured: up to 110°), then lets go; a closed latch keeps the bar
  in through a reversal.
- **Re-hooking impacts are plausible:** under 4 kN with the rung compliance on, and visibly higher with a rigid bar.
- **Scoring follows the 2022 rule:** the lowest rung touched counts, and touching the floor scores nothing.
- **The example climb reaches HIGH** in a time in the range teams reported (mid-tier 9–15 s, carpet to HIGH or
  TRAVERSAL). The time is reported; there is no hard target beyond "reaches it with hook load under 4 kN".
- **Existing ranges and robots are unchanged.** No climbing elements and no climber part means no change.

## Non-goals

- Flexible elements: 2024's chain and 2017's rope (they need cable simulation).
- 2019's HAB as a climbing target. Its steps can be `box` elements, but HAB-specific climbers are out of scope.
- A Java climber sample.
- Automatic climb sequencing (state machines that detect hooks): the script waits on simple conditions only.
- The rung's rotation: 2022's rungs are bolted and don't spin.

## Anti-goals

- Hooks that hold by welding the robot to the bar. A weld can't miss, slip or walk off, which are the failures teams
  need to see.
- A swing that dies too fast because contact softness hides friction, or a period that doesn't match the pendulum.
- Ground signals confused by bars: a hanging robot isn't "stranded", and a robot on the floor under a bar doesn't
  read the bar as ground.

## Constraints

- **Geometry** from the research notes (manual and drawing sections cited there):
  - 2022 rung tops at 48.72, 60.26, 75.59 and 90.94 in; the upper three are measured from floor protection about
    ⅜ in high.
  - MID→HIGH→TRAVERSAL steps of 24.00 in horizontally and 15.34 in up.
  - 1.66 in OD pipe; truss 1 ft square, the structure 9 ft ¼ in × 10 ft ¾ in × 6 ft 2 in.
  - Every number is tagged as a source or an estimate in the range files.
- **Physics:**
  - timestep ≤ 1 ms;
  - lockstep stays bit-identical;
  - hook contacts use condim 3 (sliding friction; no torsional or rolling term, which would double-count the friction
    torque about the pipe), with the pipe's real radius;
  - contact stiffness for hooks is set so a 60 kg hang sinks under 2 mm.
- **License:** never transform com.ctre classes, interpose CTRE natives, decompile Phoenix or use non-public C APIs.
- **CI budget.** Verify locally; open the PR after 2026-11-01. The branch is based on field-elements and is rebased
  onto main after that merges, before its PR. Pin floats with tolerances. Keep the new tests fast, each well under
  20 s.

## Calls made by Claude (the user can change these)

- 2022's HANGAR is the main target. 2020's switch and 2013's pyramid are library ranges; the chain and rope are out.
- Hooks hold by contact and friction, with a passive spring latch, not welds.
- The climber is a stock part: a telescope, hooks, a latch and an optional pivot. Two climbers on one robot make a
  traversal climber.
- Bars aren't ground. Floor protection is ground.
- The climb run uses a script with simple waits, including a swing-phase wait; no state machine.
- The example targets HIGH, with TRAVERSAL as a stretch example that isn't gated by a test.
- Rung compliance is optional, and on in 2022-hangar (an estimate of 160 kN/m, the middle of the research range).
- The scenario uses the Python fake robot, not Java.

### From the builder check

- **How a hook lets go.** Hooks are open J-shapes, seated by the robot's weight. They release by geometry: the climber
  unloads the hook (it extends up, or the other climber takes the weight) and moves it off the bar by extending or
  pivoting away, as most 2022 passive-hook climbers did.
  - The latch is optional. When present, it blocks the bar from leaving through the mouth, and it has an optional
    motor slot `release` that opens it. The script gets a "set the release output" step.
  - No contact or joint is switched off by the script, so release stays physical.
- **What collides.** Every geom of the `climber` part collides with field elements: tubes, hooks and latch. The
  chassis frame and bumpers already do. Existing stock mechanisms (elevator, arm, launcher) stay collide = false, so
  existing robots don't change. This is a documented limit: an elevator can pass through a bar.
- **Scoring is per range.** A `[climb] rule` table in the range file selects one of:
  - "lowest-rung" (2022): the lowest rung level touched, and nothing while touching ground;
  - "hang" (2020): hanging scores its points, plus `level_bonus` while the named switch is within its level threshold
    at the check;
  - "lowest-point" (2013): the level is the highest rung the robot's lowest point is above, while hanging. The
    manual's sequential-climb rule isn't checked; that's documented.
  The signals use the range's rule.
- **2022 robot limits.** The sample climber and scripts obey G107 (at most 16 in beyond the frame perimeter) and G106
  (at most 66 in tall in the HANGAR ZONE). `climb run` reports breaches as notes (when, and by how much), and the climb
  signals gain `climb.extension` (m beyond the frame perimeter) and `climb.height` (the robot's top above the carpet).
- **`climb.hook_load`** is the total contact force on one whole hook (the sum over all its geoms, including the
  latch), taking the largest hook. Per-hook signals are `climb.hook.<name>.load`. The 4 kN limits apply to this total.

## Left to the builder

- The hook's exact geometry (capsule count and throat shape) and the latch spring.
- How the swing angle is computed for a robot hanging from two bars at once: use the bar it was most recently hung
  from, and document it.
- The script file format details, within the step kinds above.
- How the truss is split into boxes.

## Decisions made during the build

Rulings and accepted deviations from the build, in the order they came up. Each line says what was decided, why, and
what it costs if it turns out wrong.

- **The fake robot's `--climb` mode plays a timed script** (its own small TOML of times and setpoints), with its
  timings taken from a `climb run` of the same climb. A robot program sees only HAL, not world contacts, and timed
  playback in lockstep is deterministic. If wrong: the timed file must be regenerated when the physics changes.
- **`NON_GROUND_GROUP = 1`**: bars and other non-ground geoms are drawn by default but aren't ground. MuJoCo hides
  groups 3 to 5 by default. If wrong: nothing.
- **Hook geoms get MuJoCo geom priority above the field's**, so the hook's friction preset and contact softness apply.
  MuJoCo combines friction by the larger value, so a 0.5 rung would override UHMW 0.15 or polycarbonate 0.35. If wrong:
  nothing.
- **A compliant rung's flex range is +-5 cm.** At 160 kN/m a 4 kN impact needs about 25 mm, and a +-2 cm stop would
  bottom out and hide the real peak. If wrong: nothing.
- **Accepted deviations in the elements work:** `Bar.level` names a `[climb]` level (points come from `[climb]`);
  `Switch.diameter` is optional (1.66 in); `layout.bars` holds pipe geoms only; `check_clear` lets starts stand under
  bars and switches and on thin ground boxes; the hangar sits in the blue corner against y = 0 (an estimate); the
  pyramid's legs aren't built.
- **Existing ranges are guarded by a tolerance-based fingerprint pin**, and the switch error says what to do. The
  constraint "existing ranges unchanged" deserves a real test. If wrong: nothing.
- **The `release` latch motor should run in coast neutral.** A shorted (brake) motor's back-EMF slows the latch's
  spring closing from about 30 ms to about 0.3 s, so docs/climbing.md says so. If wrong: latches close slowly in
  brake-mode configs. Later measurement refined this: coast 34 ms, brake 60 to 70 ms.
- **Accepted additions in the climber part:** the `capsule` shape in the schema; geom `name` and `priority`; joint
  `limit_solref` (keeps the 30 g latch from sagging); the elevator telescope factored out as `slide_chain`
  (byte-identical); hook solref [0.01, 1] (sink 0.64 to 0.73 mm at 60 kg); hooks open toward +x and catch by coming
  down over the bar; only `extend` and `extend_encoder` are listed as slots.
- **The latch and release tests drive the real `release` slot through its motor binding** (coast neutral), with two
  coupled hooks, and check the latch shuts in time in a real catch. A `PartError` lower bound on `hook_throat` (a sqrt
  crash below about 2 mm) and a fix for geom names that clash with auto `<body>.tread<i>` names came with it. If
  wrong: nothing.
- **The walk-off goal was amended to the physics.** A deep open J hook holds an unlatched swing up to 110 degrees and
  lets go only when its load reverses (released from 120 degrees, or an impact). The spec's "a latch that doesn't close
  lets the hook walk off under a swing" became "an unlatched hook lets go when its load reverses; a latch keeps the bar
  in through a reversal". This is the measured result; real 2022 walk-offs came from shallow hooks and impacts. If
  wrong: nothing; a shallow `hook_depth` can still show walk-offs.
- **The climber telescope's travel stops get a stiff `limit_solref`**, like the latch's, so 60 kg hanging at a stop
  sags under 2 mm; the runner holds extension in a position loop. The default stops sag 1.5 cm, and a full-duty motor
  pushes 10 cm through them. If wrong: nothing. Accepted with it: the final `limit_solref` is [0.003, 2.0]
  (overdamped), because critically damped kept up a 12 mm bounce with the rung spring.
- **No contact or tolerance changes were needed** for the hooks; the nitrile decay (0.72 times Coulomb) is noted, not
  gated.
- **`climb.hook_load` sums each contact's world-frame force, with the sign flipped when the hook geom is geom2**, then
  takes the norm per hook. That is correct for any contact ordering. If wrong: nothing.
- **The 2013 "lowest-point" level is 1 + the number of bars below the robot's lowest point, while hanging**, so a
  robot held by the pyramid below the 30 in rung is LEVEL 1 (LEVEL1 10, LEVEL2 20, LEVEL3 30). It matches the 2013
  manual's levels; the first build was one level low. If wrong: nothing.
- **`climb run` calls `ClimbMonitor.track_extent()`** so the G106 and G107 notes are computed. Extension and height
  count colliding geoms only (a `collide = false` elevator doesn't show in G106), and `climb.level` is a rank
  (1 = lowest level, 0 = none). If wrong: nothing.
- **The 2013 rule counts distinct levels** (rank = 1 + the highest rank below the lowest point, capped), not bar
  elements, because the pyramid has four bars per height. If wrong: nothing.
- **The swing's bar comes from hook contacts** (the most recently hooked hold still touched), falling back to any
  touched hold while hanging, so a brief brush of another hold doesn't move the swing's axis. If wrong: nothing.
- **traversal.toml may not reach TRAVERSAL.** It is a stretch example, not gated; the high hooks touch TRAVERSAL
  without taking load, and the docs say so. If wrong: there is no traversal demo until it is reworked.
- **Accepted in the climb runner:** G106 is evaluated on the robot's own-frame height (a height above the carpet would
  flag every HIGH climb); drives go through the motor bindings, so the example drives its wheels and depends on carpet
  traction; the hit threshold is 200 N; the climb run tests were written after the runner, not strictly test-first.
- **Walk-off and "fell" classification uses the windowed peak hook load** (0.2 s, or since hung) and what follows.
  Airborne from a carried hold plus a real descent (more than 5 cm of CoM drop, or a landing) is "hook walked off"; a
  descent without a carried let-go is "fell"; a brief gap that re-hooks the same hold with no descent is neither. A
  single-step label mislabeled both ways (a 108 ms bounce read as a walk-off; a load-reversal let-go read as "fell").
  If wrong: nothing.
- **The hook bracket counts as part of the hook, not a "hit a bar" geom**, and high.toml's MID approach went back to
  3 V at the time. Summing hit force made the bracket trip at 215 N with a 2 mm margin, and the bracket is meant to
  touch bars. If wrong: a bracket crash isn't flagged as a hit (the hook load signal still shows it).
- **The scenario replays timed setpoints held by position loops on HAL encoders, not the runner's motor outputs.** The
  open-loop replay was chaotic (4-decimal rounding missed HIGH) and a byte-exact regenerated 100 KB script is a CI
  hazard. The byte-exact test was dropped and +-20 ms setpoint-shift tests added. This is robust and realistic and
  avoids cross-platform float sensitivity. If wrong: re-implementation time.
- **high.toml was retuned for the closed-loop replay:** wheels 3 V to 1.5 V under MID (3 V slipped about 2 cm), and a
  0.2 s "clear of MID" step. The climb run still reaches HIGH (MID 4.20 s, HIGH 7.77 s, peak 1224 N falling to
  1077 N). It is robust to +-20 ms (tested) and +-100 ms (probed). If wrong: the scenario's timing margin shrinks.
- **A new `[telemetry] nt = false` config key skips the 5 s NetworkTables wait**, for robot programs that serve none.
  Climber encoders are bound on DIO 4, 6 and 8. If wrong: nothing.
- **Measured ledger numbers for the shipped examples** (Task 5): high.toml MID 4.21 s, HIGH 7.77 s, peak 1224 N, swing
  29.8 degrees, 13.9 in reach, 65.6 in tall.
