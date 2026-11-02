# Feeders, spreads and more balls (sub-project 21)

## Intent

The survey of past FRC games (2026-11-02) found that at least 13 games had a human player station: a slot, chute or
shelf a person feeds game pieces through. Today a Scrimmage field's pieces only start on the floor or in the robot.
This project adds the station, plus the smaller fixes the survey grouped with it.

The owner's description of what a student is trying to find out: **can their mechanism reliably get pieces from the
human player station**, and how does it handle the variation in feeding? Pieces come down a ramp at different angles
and speeds, turned different ways where the rules allow it. Does each one fall into the hopper or intake? Teams that
prefer to pick pieces off the floor ask the same question about awkward pieces there. A piece can land badly from the
station, but it can also be dropped or bumped by another robot. So "how pieces lie on the floor" is its own setting,
for any piece on any field, not a feeder feature.

"Reliable" is the student's call: around 80% may be fine in a high-volume game, but a game where a robot holds one or
two pieces at a time needs every one. The catch rate is also something to compare between two designs. A piece that
misses the hopper and **lodges inside the robot** has to be called out on its own, even when the rate looks fine:
games limit how many pieces a robot controls, and a stuck piece counts toward that limit.

Who: high-school CAD students testing a mechanism in the Test tab (sub-project 19), often with a stand-in robot
instead of their real one. The standing anti-goal applies: **no unnecessary friction**.

## Goals

1. **A human player station on a field.** A range file gets `[[station]]` entries. Each has a release point,
   direction and speed for its pieces, a time between pieces, spreads, and an optional stock.
   - **Time between pieces.** The owner noted that some human players are much faster than others. One team's
     short student worked a foot lever far quicker than taller students, and alliances asked for her. The default
     is 1.0 s. It is an ordinary setting, so a sweep can compare a fast player with a slow one.
   - **Spreads.** Each feed varies within them: approach angle (yaw and pitch, degrees), speed, release position
     (a small box around the release point) and orientation.
     - A tight, walled chute has small spreads; an open ramp has large ones.
     - Orientation is a rotation spread about each axis. It does nothing to a ball, but the format carries it now so
       project 2's non-round pieces use it unchanged (the owner's example: a 2017 gear's teeth face a different way
       on each drop).
   - **Stock.** An optional count, so "feed until the station runs out" can be tested. With none, the station
     never runs out. Restocking over a match is out of scope (see Non-goals).
   - **The station's own walls.** A ramp, chute or shelf is built from the existing element kinds (boxes, ramps)
     around the release point, so a piece really slides down it.
2. **The robot tells the station when to feed.** The owner: a human player waits for the robot to be ready, and for
   a component test it is simplest for the mechanism to say so. New blocks:
   - **start feeding** *station*: "I'm here and lined up." The station sends a piece every interval until it is told
     to stop or runs out.
   - **stop feeding** *station*: "I'm full" or done.
   - **feed one** *station*: one piece. It waits the station's interval if a piece was just fed.
   - New wait/check conditions:
     - **pieces in the hopper** at least / at most N;
     - **station empty**;
     - **at least P% made it in**, a check over the feeds so far. The student sets P.
3. **Every fed piece gets one outcome**, counted in the run's headline sentence, for example "17 of 20 made it in
   (85%); 1 stuck in the robot; 2 missed":
   - **made it in:** the piece's center is inside a body the student marked as the hopper or intake;
   - **stuck in the robot:** it rests on a robot body that is the student's own geometry, outside every hopper body,
     at rest for at least 0.5 s (my number). It is shown even when the test passes. On a stand-in robot (the stock
     chassis "Test it" builds) a piece resting on the stand-in is not "stuck", because the stand-in isn't their
     robot. A piece wedged on the student's own part is still reported.
   - **missed:** everything else (on the floor, off the field, still rolling at the end).

   **Hopper.** The Setup panel gets a "Hopper" picker, like the Hooks picker, preselected from bodies whose names
   contain "hopper", "intake" or "indexer". It is stored in the test file.

   **The replay marks each fed piece's outcome.** A student can jump to the stuck one.
4. **Spreads for pieces on the floor.** A test can try a floor piece at a set of placements:
   - an offset across the robot's path (e.g. −6 to +6 in);
   - a distance;
   - the robot's approach angle;
   - the piece's orientation (no effect on balls, as above).

   Scrimmage runs the test once per placement and reports which placements got picked up: "picked up 7 of 9; missed
   at 6 in left and 6 in right at 30°". It is set in the Setup panel when the field has a floor piece in front of the
   start (the intake field, the new station field). The placements form a grid, not a random scatter (my call: a
   student can see which corner failed and rerun exactly that one).
5. **The same feeds every run, unless asked otherwise.** A station's random variation uses a seed stored in the
   test, so two runs (hopper A against hopper B) get exactly the same feeds and the comparison is fair. "New
   shuffle" in Setup picks a new seed, so a student can check the result wasn't a lucky draw. A sweep can take the
   seed as its value to try several shuffles.
6. **More ball types.** New `[ball.*]` presets, each value carrying a `# source:` or `# estimate:` comment as the
   file requires. Unconfirmed masses and every stiffness stay estimates, and runs stay marked UNCALIBRATED:
   - 2016 Boulder (10 in coated foam);
   - 2017 FUEL (5 in, 2.6 oz);
   - 2019 Cargo (13 in);
   - 2025 Algae (16 in);
   - 2014 game ball (about 25 in);
   - size-5 soccer ball (2002, 2010);
   - 2012 foam basketball (size 4).
7. **A ball picker in Setup** for the fields made to try a mechanism on: the intake field and the new station field.
   The game fields keep their own ball, because their floor layouts are packed for it.
8. **Every built-in field in Setup.** All 11 built-in ranges plus the plain field and the new station field appear
   in the field picker, each by its plain name, not only the four it lists today.
9. **A drive block for swerve robots.** Today `drive` works on tank drives only (sub-project 19's ruling R6). For a
   swerve robot it gets a direction (forward, back, left or right) and a distance or time. A new **turn** block
   turns the robot by an angle. Both are open-loop like the tank drive (steer the modules, then drive at a set
   voltage) and stop at the distance or angle, or time out with a plain message.
10. **Stations on fields.**
    - **The 2026 field's outposts** become stations, with sizes from the 2026 manual or field CAD, as the rest of
      that field was.
    - **A new plain field, "A human player station"**, has one station with a short ramp, a start facing it at a
      sensible distance, and a floor ball for the floor spreads. Its values are our choice, as the intake field's
      are.
11. **A worked example** in `examples/blocks/`: a hand-written hopper part on the stand-in robot at the station
    field. It runs "start feeding, wait until 5 pieces are in the hopper, stop feeding" and checks "at least 80%
    made it in". A floor-spread test on the intake field goes alongside. Docs: docs/test-tab.md and the
    getting-started guide gain a section, and docs/test-range.md documents `[[station]]`.

## Non-goals

- **Non-ball pieces, more than one kind of piece per field, pegs and shelves.** These are project 2 ("pieces beyond
  balls"). Project 2 also adds a check that a piece ends up lying a given way, for a grabber designed to straighten
  a piece (the owner's example: aligning Reefscape coral for placement).
- **Restocking a station over a match.** It matters in a full match simulation, not a component test. A note goes
  in the docs for when match simulation comes up.
- **A human player's judgment** (watching the robot, hand signals). The robot's blocks stand in for it.
- **Robot code driving a station.** Scenarios with robot code don't get a station input in this project.
- **Turret, pneumatics, moving field elements.** These are later survey projects.

## Anti-goals

- Friction:
  - A student should get from "I have an intake" to "17 of 20 made it in" by picking the station field, a ball and
    the hopper in Setup, and using the starter blocks.
  - On the station field, the first test already says *start feeding, wait until 5 in the hopper, stop feeding*.
- A misleading catch rate. Every fed piece is accounted for in exactly one outcome, the counts always add up to the
  feeds, and a run that ends before a piece settles counts it as missed, saying "still moving when the run ended".
- A comparison that isn't fair. The seed is shown in the outcome when it differs from the previous run's.

## Constraints

- Builds on the testrange ball ledger (scrimmage/testrange/balls.py: parked balls are put in play). The station
  spawns pieces from a parked pool sized to its stock, or to a cap when it has none. It builds on the bench's
  `[[inlet]]` design (docs/bench.md) for how a feed is described, and on the Test tab (scrimmage/blocks/,
  assembler test_session.py, test.js, blocks_defs.js).
- Range, robot, part and test file formats change only by adding optional keys and blocks. Existing files load and
  run unchanged.
- The same physics as the whole-robot simulation, with no test-only shortcuts.
- Windows is first-class. Use American spellings and plain words in everything a student reads. CI is unmetered.

## Decided with the owner

- This is project 1 from the survey: feeders and presets, all fields in Setup, and a swerve drive block.
- The station is driven by the mechanism's blocks: "ready, start", "done, stop", "feed one".
- Variation comes from the station's shape (a tight chute versus an open ramp), the human player's speed, and the
  piece's orientation (for pieces where it matters). Restocking is for a full match simulation, not this.
- The student decides what catch rate is reliable. The rate is also a point of comparison.
- A piece stuck inside the robot is called out separately. It can't be detected when the robot's geometry isn't
  there (a stand-in robot).
- Awkward floor pieces get their own spread, for any piece, not as part of the station.
- Reorienting grabbers belong in project 2.
- The survey report stays out of the repo's docs.

## My calls (the owner can overrule any)

- **Settings a student changes live in the test file.** The ball choice, the station overrides (interval, spreads,
  stock), the seed and the floor spread all go in a `[setup]` table of the `.test.toml`. They apply over the range
  file's values for that test only, so two tests on one robot can use different balls. Sweeps gain these keys as
  values. The field choice stays where it is today, in the robot file's `[range]`.
- **When outcomes are decided.** When the blocks finish, the run keeps going until every fed piece has settled (at
  rest for 0.5 s), up to 3 s more (my numbers). An outcome is where the piece is then. A piece that was in the
  hopper and bounced out is "missed (bounced out)". The "at least P% made it in" check uses the counts so far when
  it's a block in the middle of a test, and the final counts when it's the last block.
- **The station field's start puts the robot's bumpers against the station.** The starter blocks can begin feeding
  at once, so the path that needs no setup works for any robot. A student who wants to test driving up adds a drive
  block and moves the start back in Setup.
- **Floor spreads move the ball.** They change its offset across the path and its distance ahead, overriding the
  field's floor ball. They also turn the robot's start heading for the approach angle.
  - The axes the student turns on make a full grid, capped at 25 runs (my number); Setup shows the count before
    Run.
  - It is stored in the test file and its results are kept like a sweep's (the last 3).
  - "Picked up" means made it in, i.e. into a hopper body.
- **"Stuck" is off only on the stand-in robot**, the one "Test it" builds. On the stand-in, a piece resting on a part
  the student added is still stuck. On any other robot, every robot body counts, stock chassis included: a ball on
  top of their real frame counts toward the possession limit.
- **"Pieces in the hopper" counts every ball** whose center is inside a hopper body, fed or not. The existing
  `piece_in` check stays as it is.
- **A station can build its own chute.** Optional keys (width, length, slope) generate the walls from the existing
  element kinds, so a station is written once. A station without them is only a release point, and extra
  `[[element]]` entries can sit around it. `range check` refuses a release point inside a wall.
- The default time between pieces is 1.0 s.
- "Stuck" means at rest for 0.5 s on the robot.
- Floor spreads are a grid, not a random scatter.
- The seed is stored in the test file.
- The station field is a new plain field, not added to the intake field.
- The ball picker is limited to the intake and station fields.
- The new presets are the seven listed above.
- The hopper preselection uses names containing "hopper", "intake" or "indexer".
- A station with no stock uses a cap of 50 pieces (my number) per run, so the parked pool stays small.
- A sweep can take a station's interval, a spread's size, or the seed.

## Left to the builder

- The `[[station]]` keys' exact names and how spreads are written. The bench's `[[inlet]]` is the model to follow
  where it fits.
- How floor spreads run (one world reset per placement, likely reusing the sweep machinery) and how their results
  show in the run column.
- How the swerve drive block steers modules (the existing steer-joint detection in palette.py, and position_volts
  for the steer), and the turn block's control.
- The 2026 outpost geometry, from the manual or field CAD.
- How the replay marks a fed piece's outcome.

## Decisions made during the build
