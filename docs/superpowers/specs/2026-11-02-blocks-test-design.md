# Testing a mechanism with blocks (sub-project 19)

## Intent

A CAD student has just brought a mechanism into Scrimmage (sub-project 18, the Part tab) and wants to know if it
works, before there is robot code and often before there is a robot. The owner's example: a ladder climber whose
two arms ride on vertical discs like a train's wheel cranks, offset so one arm rises while the other lowers, one
motor spinning both, with hooks on the arms. The questions: does the oscillating motion climb rung after rung, and
does the robot's tilt (it hangs off one side) need the arms angled, the mechanism moved, a wait before climbing, or
a different center of gravity?

The student answers them by snapping together Scratch-style blocks ("spin the crank at 6 V, wait until a hook
touches the next rung, repeat until rung 4"), pressing Run, and watching an animated replay of the robot on the
field. Then they change something, in the CAD (Import again) or in Scrimmage (a number, a weight, where the part is
mounted), run again, and compare. The loop is the product: **change, run, compare**.

Who: high-school CAD students (mostly Windows; Inventor, and other CAD programs through STEP/STL), technically
minded but still learning. The owner's standing anti-goal applies with full force: **any unnecessary friction**.
The more annoying the tool, the less it is used.

## Goals

1. **A Test tab** with three columns, as in the agreed mockup: the block palette, the student's test, and the run
   (an animated replay with a scrubber, a chart over time comparing this run with the last, and the run's outcome
   in one plain sentence: "reached rung 3, then a hook missed rung 4 at 8.2 s").
2. **Blocks that aren't tied to one mechanism.** The same editor works for a climber, an intake, a catapult, a bar
   that knocks something off a ledge:
   - **Move:** spin a motor at a voltage, move a joint to a position (degrees for a hinge, inches or meters for a
     slide), drive forward/back a distance or for a time (when the robot has a drivetrain), stop.
   - **Wait:** a time; a joint reaching a position; a named robot part touching a named field element or game
     piece ("either hook touches the next rung"); the robot's tilt above/below an angle; a game piece reaching a
     place (inside a named body of the robot, such as a hopper, or past a line); and, on a range that scores climbs,
     holding a level.
   - **Repeat:** N times; until one of the wait conditions.
   - **Check:** "expect" a condition at that point, or throughout the rest of the run ("tilt stays under 20°"). A
     failed check ends the run and says so.
   - The bold slots are typed numbers or picked from lists built from the robot (its parts, joints, motors) and the
     field (its elements and game pieces), so a student never types a name that doesn't exist.
3. **Runs a student can watch.** Every run records the robot's and the game pieces' motion and plays back in the
   browser's 3D view at any speed, with a scrubber. The chart shows signals the student picks, with sensible
   defaults per test (tilt; the driven joints' positions; motor current; hook load on a climbing range).
4. **Compare.** The last few runs are kept. Any two can be overlaid on the chart, with their outcomes side by side.
   A run made after Import again lands next to the one before it.
5. **Sweeps** over numbers Scrimmage controls: a value in a block (a voltage, a wait), the stand-in robot's weight
   and center of gravity, and where a part is mounted on the robot (its offset and rotation). The result is a table
   and a chart, one row per value; clicking a row replays that run. Geometry the CAD owns (an arm's length, a hook's
   shape) is changed in CAD and brought in with Import again; it is not swept.
6. **A stand-in robot.** A robot is needed before a mechanism has one to live on: a box with a weight and center of
   gravity the student sets, optionally on a drivetrain so "drive forward" can bring it to the ladder. A real robot
   from the assembler works the same way.
7. **Two proving cases**, shipped as examples and tested end to end, so the design isn't shaped around one mechanism:
   - the owner's train-wheel ladder climber on the stand-in box, climbing a sample ladder range;
   - a ball intake on the stand-in box, driving into balls on the floor of an existing range until one reaches the
     hopper.
8. **Agents can use it too.** The owner works with AI agents, so a test file can be run from the command line and
   through the MCP server, returning the outcome, the checks and the chart data as text.

## Non-goals

- **Generating robot code.** The blocks are for the CAD student's testing. Programmers can read the blocks to see
  the sequence (owner: no separate "steps for the programmers" list is needed).
- **Surveying past FRC games** for mechanisms and game pieces the sim doesn't support yet. That is the next,
  separate project (agreed with the owner); this one is general enough that the survey's gaps become new blocks,
  elements and pieces, not a new editor.
- **Building this season's field elements.** The owner will have an agent build them from the game manual and field
  CAD at kickoff. This project ships one sample ladder range for its own example and tests.
- **Sweeping CAD geometry.**
- **Replacing `scrimmage climb run`.** It keeps working as it is; the builder may share code with it.
- **Driving by hand** in the Test tab; the Drive tab already does that.

## Anti-goals

- Friction: a student who has never seen the tab should get from "I imported my part" to watching a run in a few
  minutes, without reading the docs. Empty states say what to do next; the first test a student opens already has
  sensible starter blocks for the robot it's on.
- Feeling like programming. No variables, no types, no syntax; a block either fits where it's dropped or doesn't.
- A silently wrong run: a block that can't do what it says (a joint with no motor, a piece that isn't in the field)
  is marked on the block before Run, in plain words, and Run is refused until it's fixed or removed.

## Constraints

- Builds on what exists: the robot config and assembler (robot mode, SimLock, the 3D scene code), the range files'
  elements and game pieces (docs/terrain.md, docs/test-range.md), the Part tab's import-origin placement, the climb
  runner's way of driving motors (it writes the config's motor controllers through `HalState`, so a test goes through
  the config's own motors, gearing, battery and neutral modes). No robot program runs during a test.
- The robot and part formats don't change incompatibly. The stand-in box's settable center of gravity is a new,
  optional key.
- The same physics as the whole-robot simulation: no special test-only shortcuts that would make a test pass that
  the real robot wouldn't.
- Windows is a first-class platform (CI has a Windows job since sub-project 18).
- American spellings; plain words in everything a student reads.
- CI is unmetered (public repo).

## Decided with the owner

- Blocks, Scratch-style, for testing components without code (sub-project 18's conversation).
- The run is seen as an animation, preferably, and checked against a scenario's checks when the student has them.
- The agreed screen: palette, test, run with replay, scrubber and a comparison chart. No "steps for the
  programmers" section; the blocks are readable enough.
- Sweeps cover block values, the stand-in robot's weight and center of gravity, and mount position; CAD geometry
  changes go through edit CAD, Import again, Run.
- General blocks proven on two mechanisms now; a survey of past games is a separate project after this one.
- Field elements for a season come from the owner's agent working from the manual and field CAD.

## My calls (the owner can overrule any)

- **A test is a file**, `<name>.test.toml`, next to the robot config it names (`robot = "climber_box.toml"`), holding
  the blocks as readable TOML (steps, repeats, checks), not as an editor's internal format, so it diffs well in git
  and an agent can write one by hand. Opening a test file opens its robot in robot mode with the Test tab showing,
  the way a scenario file shows the Scenario tab.
- **The block editor is Blockly** (Google's open-source library behind most Scratch-style editors), vendored into the
  app like its other front-end libraries. It is the proven way to get drag-and-drop, snapping, undo and keyboard
  support right. The cost: it is large (several hundred KB) and has its own look, which gets themed to match the
  app's light and dark themes. The TOML file is the source of truth; the editor round-trips it.
- **The stand-in robot** is the stock `chassis` with an optional center-of-gravity offset (forward, left, up from
  its default) plus a template config, "Stand-in box robot", on the kitbot's drivetrain, offered by the Test tab
  when the student has a part but no robot. A part is added to it through the assembler as today (and lands where
  the CAD has it).
- **Run outcome wording** is generated from what ended the run: all steps done, a check failed (which, when), a wait
  timed out (which step), the robot tipped over (its tilt passed 60°: my number) or, on a climbing range, the climb
  outcomes `climb run` already reports ("hook walked off", "reached HIGH").
- **Recording** is a new, compact motion log per run (each moving body's pose at the display's frame rate, about
  30 per second, plus the chart signals), kept for the last 10 runs per test like scenario runs. The replay uses the
  app's existing 3D scene code.
- **The sample ladder** is a new library range, `ladder`, with round rungs at even spacing between two side rails
  (bars and boxes, the existing element kinds), sized like a generic FRC climb. A square-rung variant is one key
  away if the bar element supports a square section; if it doesn't, adding it is in scope (the owner's square-hook
  example).
- **The intake example** uses an existing range with floor balls and a roller intake from stock or the CAD
  fixtures; the builder picks whichever proves "a piece reached the hopper" with the least new code.
- **The train-wheel climber example is designed by the builder**, since the owner's example is hypothetical and
  there's no CAD for it yet. It is a STEP fixture made the way sub-project 18's fixtures are
  (tests/make_cad_fixtures.py): a base, two vertical discs on one shaft driven by one motor, cranks 180° apart, an
  arm on each crank pin guided by a pivot so it rises and falls, and a spring-hinged hook on each arm that folds
  past a rung on the way up and catches it on the way down. It goes through the Part tab (import, bodies, joints,
  the loop closures for the cranks) as part of the end-to-end test, so the example proves the whole path a student
  takes. **What the test gates:** the pipeline (import, test file, run, replay, compare, sweep) and an honest outcome.
  Climbing every rung is the goal, and the shipped example should climb at least two, but tuning contact physics
  until it climbs all four is not allowed to grow without limit: if it stops at a rung for a physics reason, the
  example ships saying so (as `traversal.toml` does today) and the reason goes in the docs.
- **Hooks on a CAD part:** the climb outcomes ("hook walked off", hook load, a hook being allowed to touch a bar while
  any other part hitting one ends the run) need to know which bodies are hooks. The test file says so, in a short
  "Setup" panel of the Test tab: "Hooks: hook_A, hook_B", preselected from bodies whose names contain "hook" and
  editable. The stock climber's hooks count automatically, as today. The part format and the Part tab don't change.
- **Scenarios and checks:** the owner's "run it by a scenario if they have one" is already possible: a whole-robot
  scenario with robot code reruns after Import again in the Scenario tab. In this project, Check blocks use the same
  conditions as Wait blocks; a test doesn't import a scenario's checks.
- **Replays show what ran.** A run keeps the model it ran (the compiled robot and field and the meshes it used,
  meshes shared between runs when they're identical), so a run from before Import again replays with the old
  geometry, and comparing old and new is honest.
- **Command line and MCP:** `scrimmage test run <file>` (exit code 0 when every check passed) and an MCP tool that
  runs a test file and returns the outcome and chart data.

## Left to the builder

- Module layout (a general test runner package, how much of `scrimmage/climb/run.py` it shares), the motion log's
  file format, the API routes, and how the Test tab's session fits robot mode and the shared SimLock.
- How position moves are controlled (the climb runner's fixed proportional gains are a starting point; a block that
  can't reach its position should say so in its wait's timeout, not hang).
- The default chart signals per test, and the exact list of contact targets ("either hook", "any part of the arm").
- Sweep limits (how many values, run time) and how a sweep runs in the background without freezing the tab.
- Accessibility of the block editor (keyboard use, contrast in both themes; the app has a contrast test).
