# The Test tab

The Test tab tries a mechanism with blocks, before anyone builds it and with no robot code. You snap blocks together
("spin the crank at 6 volts", "wait until a hook touches the next bar", "expect the robot to tilt less than 30°"),
press **Run**, and watch a replay with a chart. It uses the same physics as the whole-robot simulation, and the motors
are driven through the robot's own motor settings (which motor controller and ID each motor has), so the gearing, the battery and the weight all count. It is the
same page as the [assembler](assembler.md) and the [Part tab](part-tab.md) (`scrimmage edit`), with the same habit:
every change is saved at once, there is no Save button.

Contents: [Starting a test](#starting-a-test), [The three columns](#the-three-columns), [How the editor works](#how-the-editor-works),
[The blocks](#the-blocks), [Feeding from a station](#feeding-from-a-station),
[Floor spreads](#floor-spreads), [The checks](#the-checks), [Times and limits](#times-and-limits), [Setup](#setup),
[Running, replay and chart](#running-replay-and-chart), [Trying values (sweeps)](#trying-values-sweeps),
[Kept runs](#kept-runs), [The stand-in robot](#the-stand-in-robot), [The command line and MCP tools](#the-command-line-and-mcp-tools),
[The examples](#the-examples), [Troubleshooting](#troubleshooting).

## Starting a test

A test is a file that ends in `.test.toml`. Three ways to get one:

- **From the Part tab.** When your parts are built (the bottom bar says **Parts up to date**), press **Test it**. See
  [The stand-in robot](#the-stand-in-robot).
- **From the start page.** Run `scrimmage edit` with no file and press **Test a mechanism**. Press **From a CAD file…**
  to go through the Part tab first, or pick a robot file you already have and press **Make the test**.
- **An existing test.** `scrimmage edit examples/blocks/ladder_climber/climb.test.toml`.

A new test starts with blocks that do something on its robot, so you can press **Run** right away: it drives 0.3 m
first when the robot can drive and the field has bars, then spins the first mechanism motor (or moves a lone hinge to
45°).

## The three columns

- **Left: the blocks.** Drag a block out into the middle. Blocks are in four groups: **Move**, **Wait**, **Repeat**
  and **Check**. A block only shows up when the robot has something for it (no motors, no **spin** block).
- **Middle: your test.** Above the blocks is **Setup**, folded away until you open it (its summary shows the hooks, the
  robot's weight and the field).
- **Right: the run.** **Fix these first** (when there are problems), **Run** and **Stop**, then the result: what
  happened in one sentence, each check, the replay, the chart, and the list of kept runs.

The bottom bar says whether the test is saved and how many blocks it has.

## How the editor works

- **The test is the stack that holds the first block.** Blocks run from the top of that stack to the bottom. A block
  can hold other blocks (a **repeat**), and a check plugs into the socket on **wait until**, **repeat until** and
  **expect**.
- **Loose blocks get a warning.** A stack that isn't joined to the test doesn't run and isn't saved in the file. The
  warning on it says so: drag the blocks under the others, or delete them. A check that isn't plugged into anything
  does nothing.
- **Run is off while there are problems.** The problems are listed over **Run** under **Fix these first**, and each
  also shows as a warning on its block. They are plain sentences. For example, if you press **Import again** in the
  Part tab and a joint is renamed, the block that used it says the joint is no longer on the robot, and **Run** stays
  off until you pick another.
- **A block that isn't finished isn't saved.** An empty check socket on **wait until**, **repeat until** or **expect**
  says "Not saved yet: a block needs a check plugged in". Plug one in from **Check**.
- **If the file is changed somewhere else** (you edited it in a text editor), the tab reloads it and says so. A file it
  can't read shows the error and keeps the last good version on screen.

## The blocks

| Block | What it does |
|---|---|
| **spin** *motor* **at** *V* **V** | Runs a motor at that many volts (-12 to 12). It keeps going until a later block changes that motor. |
| **move** *joint* **to** *number* *unit* | Drives a joint to a position and waits until it gets there. |
| **drive** *direction* *number* **m** or **s** | Drives the robot that far in a direction (**forward**, **back**, **left** or **right**; a minus number with **forward** drives back; **back**, **left** and **right** refuse one), or for that many seconds, then stops. |
| **turn** *left or right* *number* **°** | Turns the robot on the spot that many degrees (up to 720). |
| **start feeding** *station*, **stop feeding** *station*, **feed one from** *station* | A human player station puts balls on the field. See [Feeding from a station](#feeding-from-a-station). |
| **stop** *motor* | Lets a motor go to neutral. **all motors** stops every motor. |
| **wait** *s* **s** | Waits that many seconds. |
| **wait until** *check* | Waits until the check is true. |
| **repeat** *n* **times** | Does the blocks inside *n* times (1 to 100). |
| **repeat until** *check* | Does the blocks inside again and again until the check is true. |
| **expect** *check* **the whole time** | Checks something at this point in the test. Tick the box to check it all the way from here to the end of the run. |

**A move block always has its unit.** It is **°** for a joint that turns and **in** for one that slides (the unit
switches for you when you pick the joint). **m** is there too. The file always says the unit, so a number is never
guessed.

**Drive and turn work on a tank drive or a swerve drive.** A tank drive (the kitbot's kind of drivetrain,
`differential` in the parts list) is a wheel on each side with its own motor. A swerve drive has a steer motor and a
drive motor on each module. A tank drive can drive **forward** and **back** only (for left and right it says "a tank
drive can't drive sideways; turn, then drive forward"). A swerve drive can go in all four directions: it points the
modules first (up to a second), then drives the wheels at the block's volts. **turn** spins the robot on the spot:
a tank drive runs its two sides in opposite directions, a swerve drive points its modules around the robot's center.
Any other robot (mecanum, say) has no **drive** or **turn** block: spin the drive motors one by one instead. The
problem text on such a robot is "drive works on a tank or swerve drive; spin the drive motors instead". A robot with
no drivetrain at all gets the problem "drive needs a drivetrain".

**A motor in a block needs a device ID.** A **spin**, **stop** or **move** block works through the robot's motor
bindings, so the motor's slot must have an ID in the robot. See [Troubleshooting](#troubleshooting).

## Feeding from a station

A human player station is a place on a field where balls come onto the field, one at a time, like a human player
rolling them down a chute. Pick a field that has one (the **Station** field, or **2026**, which has both outposts) and
the blocks gain a **Station** group:

| Block | What it does |
|---|---|
| **start feeding** *station* | The station feeds a ball at once, then one every *interval* seconds, until you say stop (or it runs out). It takes no time: the next block starts right away. |
| **stop feeding** *station* | The station stops. |
| **feed one from** *station* | Feeds one ball, waiting out the interval since the last one first, and finishes when the ball is released. On an empty station it does nothing. If a ball is sitting at the release point it waits, and after 10 s the run ends saying the release point stayed blocked. |

Two conditions go with them. **\<station\> is empty** is true when the station has no balls left to feed. **At least
(or at most) n pieces in the hopper** counts the balls inside the hopper right now.

**The hopper.** The test needs to know where a ball counts as "in". In Setup, **Hopper**, tick the bodies a ball lands
in. A ball is in when its center is inside one of those bodies' boxes, or when one of them holds it as the run ends
(a grabber's jaws count too). A new test ticks the bodies named hopper, intake, indexer, claw, grabber, gripper or
jaw for you. If you use **in the hopper** or **made it in** with nothing ticked, the problem says "pick
the hopper in Setup".

**What happens to each ball.** Every ball the station feeds ends in one of three ways:

- **in**: its center is inside a hopper body, or a hopper body holds it.
- **stuck**: it is resting on a part of your robot outside the hopper, not moving for half a second. It is called out
  on its own because games limit how many pieces a robot controls, and a stuck piece counts toward that limit. On a
  stand-in robot, the stand-in's own frame and drive don't count (a ball resting on the kitbot's chassis is a miss,
  not stuck); a part you added does. A ball that bounced out of the hopper and then came to rest on your part is
  stuck, not "bounced out".
- **missed**: anything else. The run says why when it can: bounced out (it was in the hopper once and isn't now), off
  the field, on the floor, or still moving when the run ended.

The sentence at the top of the result adds them up: `17 of 20 made it in (85%); 1 stuck in the robot; 2 missed (1
bounced out, 1 still moving when the run ended)`. The replay marks each ball's outcome on the timeline and in the
picture.

**The check.** **At least n% made it in** (under **Check**, plug it into an **expect** block) is true when at least
that share of the fed balls ended in. Put it last. Only the last block of the test, at the top level, is checked after
the balls settle (below); in any other place it uses the counts so far.

**Settling.** When the blocks finish, the run keeps going for up to 3 s so the balls come to rest, with the motors
holding their last commands and the stations stopped. The wait doesn't count against the time limit. Checks that run
"throughout" (tilt, a tip-over, a walk-off) don't run during the settle: the blocks are done, and the settle only
counts balls. A robot left driving could tip over unreported in that time. If the run ends any other way (a check
failed, it ran out of time, the robot tipped over, you pressed Stop), balls are sorted where they are, and any still
moving read "still moving when the run ended".

**Station settings.** In Setup, each station has a row of boxes for the field's values: time between balls, stock
(empty means "never runs out", at most 50 are fed in one run), and the spreads. **Every spread is plus or minus its
value**: "Left/right angle (± degrees)" of 5 means each ball goes up to 5° either side. A change is saved in the test's
`[setup]`; the box shows the field's own value until you change it. Stations feed balls only.

**The shuffle (the seed).** The spreads are drawn from a seed, the test's *shuffle*. The same shuffle gives the same
balls every run, so comparing two hoppers is fair. **New shuffle** picks another, so you can check that a result wasn't
a lucky draw; the sentence then adds "a new shuffle (seed N)". **Try values…** on the shuffle runs one test for each
seed in a list, which is a quick way to see how often a design drops a ball.

**Add the station blocks.** On a field with a station, if the test has no feeding block, Setup shows **Add the station
blocks**. It adds, at the end and without deleting anything, start feeding, wait until 5 pieces are in the hopper, and
stop feeding. **Test it** on a stand-in with a hopper-like body picks the Station field for you.

## Floor spreads

A ball doesn't always lie where the field puts it: it can be dropped or bumped by another robot, or land badly from a
station. A **floor spread** runs the test several times with the ball in front of the robot in a different place each
time, to see which places the mechanism picks up. In Setup, **Floor spread** (on a field with a ball on the floor)
has four axes. Tick the ones to spread over, and give a from, a to and a number of steps:

| Axis | Moves the ball |
|---|---|
| **Offset across (in)** | left (+) or right (-) of the robot's center line |
| **Distance ahead (in)** | nearer or farther along the robot's heading |
| **Angle (degrees)** | the robot's start heading turns by this, counterclockwise from above, so it approaches at an angle |
| **Turned (degrees)** | the ball turned about the vertical. It does nothing to a round ball |

The placements are every combination, at most 25 (the count is shown, and a spread over 25 is refused). The robot keeps
its start. The ball that moves is the floor ball nearest in front of the start (within 30° of its heading and 3 m); an
axis you leave out keeps that ball's own offset and distance. A placement that would put the ball inside the robot is
refused with a message, since it can't be a real place. Press **Run the spread**. The result is a table with one row
per placement, "picked up" or not, and each run is kept (the last 3 spreads are kept, apart from sweeps); click a
row to open its replay. It is saved in the test as `floor_spread = { offset_in = [-6, 0, 6] }` under `[setup]`.

For a piece that isn't a ball, such as a coral, the test file takes a fifth axis, `tip_deg` (0 to 90): the piece's
long axis pitched up from lying, so `tip_deg = [0, 45, 90]` tries it lying, leaning and standing on end. Its turn is
then measured from the robot's heading, and an axis you leave out keeps how the field has the piece lying. The results
name how it lay: "picked up 2 of 3; missed standing on end".

**Misses near a mechanism's edge can be noise.** A ball just at the edge of what a mechanism catches can bounce out at
scattered points, where moving the ball a tenth of a millimeter flips the result. If one placement misses and its
neighbors don't, try the neighbors of a miss before trusting it. The intake example's README has the story (the "Why 6
V" paragraph).

## The checks

A check is a round-ended block that plugs into **wait until**, **repeat until** or **expect**.

| Check | True when |
|---|---|
| **touches** (*part of the robot*, *thing on the field*) | The robot part touches the thing. |
| **tilt above / below** *n* **°** | The robot leans more (or less) than that from upright. |
| *joint* **above / below** *number* *unit* | A joint is past that position. |
| *piece* **in** *body* | A game piece is inside a body of the robot. *any piece* means any of them. |
| **hanging from** *bar* | The robot's hooks hold it up from that bar. |
| **holding level** *level* | The robot holds that climb level (the field's own rule, such as the 2022 hangar's). |

**What "touches" can name.** On the robot side: **any hook**, one of the hooks, a part, or a body. On the field side:

- **bars** (a ladder's `rung1`, a field's rungs), plus **next bar** and **any bar**;
- the field's other pieces (a bump, a ramp, a box);
- the floor (the carpet) and the walls.

**Next bar** means the lowest bar above every bar the robot has hung from so far in this run. At the start that is the
lowest bar. **Any bar** is any of them.

**Hanging** means the hooks carry at least a quarter of the robot's weight from that bar for 0.1 s. Brushing a bar, or
resting on it with the wheels still on the carpet, is not hanging.

**Hooks.** A climber's hooks are bodies. Tick them in **Setup**; see [Setup](#setup). A stock `climber` part's hooks
always count, and Setup shows them ticked and grayed out. A hook can be any body of a CAD part: see
[Climbing](climbing.md#the-climber-part).

## Times and limits

- **Times include the 0.3 s settle.** Before the first block, the robot rests on the carpet for 0.3 s with every motor
  off. Every time in a run (the outcome, the checks, the chart) counts from the start of that settle, so the first
  block starts at 0.3 s.
- **A move counts as reached** when the joint is within 1° (a hinge) or 0.25 in (a slide) of the target.
- **A move, a wait until or a drive can time out.** A move gives up after 5 s, a **wait until** after 10 s, a drive for
  a distance after 10 s. The run then ends with a sentence that names the block, such as `waiting for a hook to touch
  rung2 timed out at 12.1 s (block 2 inside block 3)`.
- **A repeat until has a max, default 20.** After that many rounds the run ends, saying which repeat gave up.
- **Each test has a time limit, default 60 s.** The run ends there with "ran out of time".
- **"Walked off" ends a climb run.** If the robot has hung from a bar, then lets go of every bar while a hook was
  carrying it, and then drops, the run ends with "a hook walked off". A robot that hung and then touches the floor
  with no hook letting go ends it too.
- **The settle after the blocks.** When the blocks finish and balls were fed, the run goes on for up to 3 s while the
  pieces come to rest. See [Feeding from a station](#feeding-from-a-station).
- **A robot counts as tipped over at 60°.** The run ends with "the robot tipped over" when the robot's body leans more
  than 60° from upright. (The **tilt** check is separate: it only tests what you ask.)

The tab doesn't have boxes for these settings yet. To change one, edit the test file in a text editor: `timeout` on a
block, `max` on a **repeat until**, `volts` on a **drive**, `time_limit` in the `[test]` table. The tab keeps them when
it saves. The 3 s settle after the blocks is fixed, and the file can't change it.

## Setup

Open **Setup** over the blocks. Its summary shows the hooks, the weight and the field at a glance.

- **Hooks.** Tick the bodies that grab a bar. A stock climber's hooks always count. A new test ticks the bodies with
  "hook" in their name for you. The hooks are saved in the test file.
- **The field.** Where the robot starts: **The plain field** or any of the built-in fields, such as a ladder (round or
  square rungs), a ball on the carpet to try an intake, a human player station, or a game's field (2026, 2013 and so
  on). Picking a field moves the robot to that field's start, in front of its rungs, its ball or its station, even
  when the robot had its own start pose (from the robot code panel's **Where it starts**). The Robot tab's **Undo**
  puts the old field and start back.
- **Start.** When the field has more than one start, a picker lists them in words ("At station", "Facing ball"). The
  first one is chosen when you pick the field.
- **Hopper.** Tick the bodies a ball lands in. See [Feeding from a station](#feeding-from-a-station).
- **Ball.** On the intake and station fields, the ball to use: the field's own, or any ball preset (a 2017 FUEL, a
  2019 CARGO and so on). A ball that doesn't fit (too wide for the station's chute, or overlapping the field's floor
  balls) gives a plain problem in Setup and **Run** stays off until you pick another.
- **Station.** One group of boxes for each station: time between balls, stock, and the spreads (each ± its value).
- **Shuffle** and **New shuffle**: which draw of the spreads the stations feed.
- **Floor spread.** Where the floor ball is tried. See [Floor spreads](#floor-spreads).
- **Add the station blocks.** Adds the starter feeding blocks to a test that has none.
- **The robot (weight and center of mass).** These appear when the robot's base is the stock chassis, which a stand-in
  robot always is. **Weight** is everything that isn't one of the robot's parts (frame, battery, bumpers). The center of
  mass is forward, left and up, in meters. These also change the robot file and **Undo** takes them back.
- **Where the parts are mounted.** For each part that isn't the base or the drivetrain: how far it is moved from where
  it attaches, forward (x), left (y), up (z) in meters, and turned (yaw) in degrees.

A **Try values…** button sits beside the weight, the center of mass and each mount. See [Trying values](#trying-values-sweeps).
If the robot file can't be changed (it has an error, or it is read-only), Setup says why and the boxes are off.

## Running, replay and chart

Press **Run**. Until the first run, the Run column says just that. The tab shows the progress, and **Stop** ends the
run early. Then:

- **The sentence.** What happened, in one line: `Reached rung1, then waiting for a hook to touch rung2 timed out at
  12.1 s (block 2 inside block 3).`, or, when the test works, "Every block played."
- **The checks.** One line each, passed or failed, with the time. A failed check ends the run, and its line says what
  happened instead.
- **Seated and grip.** When a piece is on a rod at the end, a line under the sentence says how far: `seated 3.5 in of
  4.0 in on branch_l2`. A test that grips a piece lists what its grip rests on, such as pads with the default
  friction, an UNCALIBRATED estimate.
- **The replay.** A 3D view of the run. **Play**, the time slider and a speed box. If the browser can't draw 3D (WebGL is the browser's 3D drawing) it says
  so and the rest still works. When a piece slipped out of a grabber, a cross under the time slider marks the moment:
  point at it to read what happened, or click it to jump there with the piece outlined.
- **The chart.** Tick the signals to draw (tilt, joint positions, hook load, motor current and more). It starts with
  the signals the test names in its `chart` line, or else the tilt and the joints a block drives. Motor currents, hook
  load and the drive wheels' angles are in the picker but not drawn at first (the wheel angles just keep growing, which
  tells you little about a mechanism). A seated depth (`seated.branch_l2`, in inches) has a gap while no piece is on
  its rod.
- **Compare.** Pick another run in **compare with** to draw both on the same chart, for example before and after a
  change. Under the chart, each run's sentence is shown one above the other.

**Old runs keep their own geometry.** A run saves the robot and field as they were when it ran. If you press **Import
again** in the Part tab later, an old run still replays the old shape, and a new run uses the new one. That makes
comparing before and after honest.

## Trying values (sweeps)

A sweep runs the test once for each value of one number, to see which works best.

1. Right-click a block (or open its menu) and choose **Try values…** for the number it has: a **spin**'s volts, a
   **move**'s target, a **wait**'s seconds or a **drive**'s distance or seconds, or a **turn**'s degrees. Or press **Try values…** beside the
   weight, center of mass or a mount in Setup, or beside a station's boxes (interval, stock, a spread) or the shuffle.
2. Type **From**, **To** and **Steps**, or type the values yourself. **At most 12 values** at a time.
3. Run it. The table has one row per value (how it ended, and a tick or a cross for each check), and a chart of how the
   run ended, or any signal, against the value. Click a row to open that run's replay.

A value that can't run gets a row saying why; the others still run. **Stop** keeps the values done so far.

**A sweep of a spread or the shuffle** shows how often a design misses, not one result. Each row's sentence has the
counts, such as `17 of 20 made it in (85%)`. Try the same number of values with the seed and a spread of 0 and every
row is the same, because nothing varies.

## Kept runs

Every run is kept so you can replay it later, in a folder next to the test file. For `climb.test.toml` it is
`climb-runs/`. Scrimmage keeps **the last 10 runs and the last 3 sweeps**, and deletes older ones. A run you stopped
doesn't count.

The runs hold the robot's shape, so they can be big for a detailed CAD robot. They are results, not source. If your
team keeps its robot in git, add this line to `.gitignore`:

```
*-runs/
```

## The stand-in robot

**Test it** on the Part tab tries one part on its own. It makes (once):

- a **stand-in robot** next to your parts, `<import name>-standin.toml`: the kitbot's frame and tank drive, with your part
  attached where the CAD has it;
- a **starter test**, `<import name>.test.toml`, next to it.

Then it opens the Test tab. If you press **Test it** again after adding parts, new parts are added to the same stand-in
and the tab says which. **Test it also gives the part's motors free device IDs**: the first PWM channels the drive
leaves free (`PWM:2` and up), so a motor you built in the Part tab can spin at once without anyone choosing IDs.
Change them in the Robot tab if you like. A part with a hook is put in front of the ladder field.

You can use any robot file instead: **Test a mechanism** on the start page, then **Make the test**.

## The command line and MCP tools

```bash
scrimmage test run examples/blocks/intake/intake.test.toml
scrimmage test run my.test.toml --out some/folder    # keep the run somewhere else
scrimmage test spread examples/blocks/intake/spread.test.toml   # one run per floor-spread placement
```

It plays the blocks with no window, prints how it ended and each check, and keeps the run in `<test>-runs/` (or `--out`).
The exit code is **0** when the test finished and every check passed, **1** when it didn't, and **2** when it couldn't
run (a bad test file, a robot that won't build, a folder it can't write).

`scrimmage test spread` runs a test once per placement of its floor spread and says which were picked up. An AI agent
connected through MCP ([install step 5](getting-started/install.md#5-optional-connect-claude-code)) has three
tools. **`test_run`** runs a test file and returns the sentence, the status, the checks, each signal's range and the
folder the run is kept in (the Test tab replays it). **`test_sweep`** tries one number at up to 12 values. Both take an
absolute path. **`test_spread`** runs the floor spread. The agent writes the `.test.toml` with its own file tools.

## The examples

Four worked examples are in `examples/blocks/`. Each has a README with the story, the blocks and how to change them.

- **`examples/blocks/intake`**, a roller and bin that take a ball off the carpet. Run it and it finishes at 3.3 s
  with the ball in the bin. The README has a sweep of the roller's voltage.
- **`examples/blocks/station`**, a bin over the whole top of a stand-in robot, with its bumpers against the human player
  station's chute. It starts feeding, waits until 5 balls are in, stops, and expects at least 80% of the fed balls to
  have made it in. Its station has a small spread, so the shuffle means something. The README tells of the first try
  that lost balls over a low back wall. (`examples/blocks/intake` also has `spread.test.toml`, a floor spread.)
- **`examples/blocks/reef`**, a grabber on a stand-in robot, on the `reef` field. It picks a coral up off the carpet,
  swings it over the top and drives it onto the L2 branch. It checks the coral is held throughout and seated at least
  3 in on `branch_l2`. A held check that runs throughout ends, passed, when the piece is set down on a rod or element
  that the test's own seated or rests-on checks name: "held by grabber from 1.4 s until it was placed on branch_l2 at
  4.4 s". Any other way of losing the piece still fails it. The README has a grip-material sweep. Its
  `spread.test.toml` turns the coral to six angles and picks up three: past about 15° the coral is wider than the open
  jaws.
- **`examples/blocks/ladder_climber`**, a train-wheel climber made from a CAD file with the same steps a student takes
  in the Part tab. **It reaches `rung1` and no further, and it is honest about why.** The robot hangs from `rung1`
  (the hooks carry more than a quarter of its weight), but then it tips up on its back bumper instead of rising: the
  hooks are at the front edge and the weight is in the middle, so a hook pulling up on the front mostly tilts the robot,
  and tilting swings the next rung out of reach. The motor is not stalled and the hooks work as designed. The README
  says what was tried and what to try next. The test's tilt check passes (the robot leans 20° at most).

Open one with `scrimmage edit examples/blocks/intake/intake.test.toml`.

## Troubleshooting

- **"... has no motor to move it".** A block uses a joint or motor whose slot has no device ID in the robot. Open the
  Robot tab and give the slot an ID (a `PWM:n` or a CAN ID). **Test it** does this for you on a stand-in robot.
- **"drive works on a tank or swerve drive; spin the drive motors instead".** The robot's drivetrain is neither (a
  mecanum drive, say). Use **spin** blocks on the drive motors.
- **A station run ends "timed out ... its release point stayed blocked".** A ball is sitting at the station's
  release point (the chute's low end is jammed). Look at the replay for what stopped it.
- **"pick the hopper in Setup".** A block counts balls in the hopper but no body is ticked under **Hopper**.
- **Run stays off and nothing is wrong on the blocks.** A save the server refused keeps Run off. Look at the status bar
  and **Fix these first**: it says why ("Not saved: ...").
- **"Busy".** A drive session (the Drive tab), a scenario run or a bench run holds the simulation. Wait for it to finish
  or stop it, then press **Run** again. Only one of these runs at a time.
- **The replay shows the old shape after Import again.** Scrimmage reads a part's mesh files by name and time to the
  second, so a part imported twice within the same second can show the earlier mesh. Wait a moment, press **Import
  again** once more, and run again.
- **A block names a joint that is gone.** After **Import again**, the block says the joint isn't on the robot any
  more. Pick the new name in the block.
- **"The test file can't be read".** The file has a mistake. The message names the block ("block 2 inside block 3
  (repeat)": the second block inside the third, a repeat) and what is wrong. Fix the file in a text editor, or delete
  the block in the tab.
- **The 3D view says "The replay needs WebGL: …".** (WebGL is the browser's 3D drawing; the rest of the message says
  why the browser refused.) The result and chart still work. Reload, or turn on hardware acceleration in the browser.
