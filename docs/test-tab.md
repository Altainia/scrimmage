# The Test tab

The Test tab tries a mechanism with blocks, before anyone builds it and with no robot code. You snap blocks together
("spin the crank at 6 volts", "wait until a hook touches the next bar", "expect the robot to tilt less than 30°"),
press **Run**, and watch a replay with a chart. It uses the same physics as the whole-robot simulation, and the motors
are driven through the robot's own motor settings (which motor controller and ID each motor has), so the gearing, the battery and the weight all count. It is the
same page as the [assembler](assembler.md) and the [Part tab](part-tab.md) (`scrimmage edit`), with the same habit:
every change is saved at once, there is no Save button.

Contents: [Starting a test](#starting-a-test), [The three columns](#the-three-columns), [How the editor works](#how-the-editor-works),
[The blocks](#the-blocks), [Feeding from a station](#feeding-from-a-station),
[Floor spreads](#floor-spreads), [The checks](#the-checks), [Holding a piece](#holding-a-piece),
[Placing a piece](#placing-a-piece), [Grip material](#grip-material), [Shooting](#shooting), [Aiming](#aiming),
[Pneumatics](#pneumatics), [Sensors](#sensors), [Moving field elements](#moving-field-elements),
[Clearance to a field element](#clearance-to-a-field-element), [A partner robot](#a-partner-robot),
[Times and limits](#times-and-limits), [Setup](#setup),
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

**A part with a grabber** (a body named claw, grabber, gripper or jaw, and no hook) is put on the **reef** field. Its
starter closes each motor that closes the jaws (a joint named jaw, claw, grip or finger) at 4 V, waits a second, lifts
if there is an arm, waits until a coral is held by the grabber, then expects it held throughout and waits 2 s. If your
jaws close with negative volts, flip the sign.

A new test starts with blocks that do something on its robot, so you can press **Run** right away: it drives 0.3 m
first when the robot can drive and the field has bars, then spins the first mechanism motor (or moves a lone hinge to
45°).

## The three columns

- **Left: the blocks.** Drag a block out into the middle. Blocks are in four groups: **Move**, **Wait**, **Repeat**
  and **Check**. A block only shows up when the robot has something for it (no motors, no **spin** block).
- **Middle: your test.** Above the blocks is **Setup**, folded away until you open it (its summary shows the hooks, the
  robot's weight and the field).
- **Right: the run.** **Fix these first** (when there are problems), **Good to know** (hints that don't stop a
  run), **Run** and **Stop**, then the result: what happened in one sentence, each check, the replay, the chart, and
  the list of kept runs.

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
- **Comments at the top of the file are kept.** Describe the test in `#` lines above `[test]` and a save keeps them.
  The tab writes the rest of the file itself, so comments further down are dropped when it saves.

## The blocks

| Block | What it does |
|---|---|
| **spin** *motor* **at** *V* **V** | Runs a motor at that many volts (-12 to 12). It keeps going until a later block changes that motor. |
| **move** *joint* **to** *number* *unit* | Drives a joint to a position and waits until it gets there. |
| **drive** *direction* *number* **m** or **s** | Drives the robot that far in a direction (**forward**, **back**, **left** or **right**; a minus number with **forward** drives back; **back**, **left** and **right** refuse one), or for that many seconds, then stops. |
| **turn** *left or right* *number* **°** | Turns the robot on the spot that many degrees (up to 720). |
| **start feeding** *station*, **stop feeding** *station*, **feed one from** *station* | A human player station puts balls on the field. See [Feeding from a station](#feeding-from-a-station). |
| **aim** *turret* **at** *goal* | Keeps a turret pointed at a goal's center from now on, while the robot drives and turns. It takes no time. See [Aiming](#aiming). |
| **set** *cylinder* **to** *extend, retract or off* | Switches a cylinder's valve. It takes no time. See [Pneumatics](#pneumatics). |
| **stop** *motor* | Lets a motor go to neutral. **all motors** stops every motor (and every aim). |
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
It slows down as it nears its angle (the block's volts are the most it uses) and pushes back if the robot swings
past, so it ends within 2° of the angle and nearly still, instead of coasting on after the wheels stop.
Any other robot (mecanum, say) has no **drive** or **turn** block: spin the drive motors one by one instead. The
problem text on such a robot is "drive works on a tank or swerve drive; spin the drive motors instead". A robot with
no drivetrain at all gets the problem "drive needs a drivetrain".

**A motor in a block needs a device ID.** A **spin**, **stop**, **move** or **aim** block works through the robot's
motor bindings, so the motor's slot must have an ID in the robot. See [Troubleshooting](#troubleshooting). When
several motors drive one joint (a launcher's lead and follower, say), the block drives every one of them, as robot
code would. A cylinder's valve needs no ID: see [Pneumatics](#pneumatics).

## Feeding from a station

A human player station is a place on a field where balls (or other game pieces, such as a coral) come onto the field, one
at a time, like a human player rolling or dropping them down a chute. Pick a field that has one (the **Station** field, or **2026**, which has both outposts) and
the blocks gain a **Station** group:

| Block | What it does |
|---|---|
| **start feeding** *station* | The station feeds a ball at once, then one every *interval* seconds, until you say stop (or it runs out). It takes no time: the next block starts right away. |
| **stop feeding** *station* | The station stops. |
| **feed one from** *station* | Feeds one ball, waiting out the interval since the last one first, and finishes when the ball is released. On an empty station it does nothing. If a ball is sitting at the release point it waits, and after 10 s the run ends saying the release point stayed blocked. |

Two conditions go with them. **\<station\> is empty** is true when the station has no balls left to feed. **At least
(or at most) n pieces in the hopper** counts the balls inside the hopper right now.

A test with no `hopper = [...]` line treats the bodies its **piece in** checks name as its hopper, so a passing
**piece in intake.hopper** is also counted as "made it in". Write `hopper = []` to turn that off.

**Where a piece should end up.** The test needs to know where a piece counts as "in". In Setup, **Where a piece should
end up**, tick the bodies a piece lands in or is held by: a hopper, an intake, a claw. A piece is in when its center is
inside one of those bodies' boxes (the box around the body's shapes, so the space inside a bin counts) and it is off
the carpet (the robot carries it, as for *piece* **in** *body*), or when one of them holds it as the run ends (a grabber's jaws count too; "held" is
under [Holding a piece](#holding-a-piece)). A new test ticks the bodies named hopper, intake, indexer, claw, grabber,
gripper or jaw for you. If you use **in the hopper** or **made it in** with nothing ticked, the problem says "tick where
a piece should end up in Setup". The test file keeps this under `hopper` in `[test]`.

**A ball and any other piece differ in one rule.** A held *piece that isn't a ball* counts as in, wherever the hand
that holds it is. A *ball* keeps the box rule: it is in only inside a ticked body's box, and a ball resting on a ticked
body without being inside is stuck. (A ball wedged on top of an intake would otherwise read as "in".)

**What happens to each piece.** Every piece the station feeds ends in one of four ways:

- **in**: its center is inside a ticked body and off the carpet, or a ticked body holds it (a piece that isn't a
  ball).
- **placed**: it is seated on a rod, or resting on a field element, that a seated or rests-on check in your test names
  (see [Placing a piece](#placing-a-piece)). A coral on a branch is placed, not missed.
- **stuck**: it is resting on a part of your robot outside the hopper, not moving for half a second. It is called out
  on its own because games limit how many pieces a robot controls, and a stuck piece counts toward that limit. On a
  stand-in robot, the stand-in's own frame and drive don't count (a ball resting on the kitbot's chassis is a miss,
  not stuck); a part you added does. A ball that bounced out of the hopper and then came to rest on your part is
  stuck, not "bounced out".
- **missed**: anything else. The run says why when it can: bounced out (it was in the hopper once and isn't now), off
  the field, on the floor, or still moving when the run ended.

The sentence at the top of the result adds them up: `17 of 20 made it in (85%); 1 stuck in the robot; 2 missed (1
bounced out, 1 still moving when the run ended)`. With placed pieces it reads `3 of 5 made it in (60%); 2 placed on
peg`, and a test with nothing ticked says `1 of 1 placed on peg`. "Made it in" counts only pieces that were in, never
placed ones. The replay marks each piece's outcome on the timeline and in the
picture (a placed piece has its own mark, a square).

**The check.** **At least n% made it in** (under **Check**, plug it into an **expect** block) is true when at least
that share of the fed balls ended in. Put it last. Only the last block of the test, at the top level, is checked after
the balls settle (below); in any other place it uses the counts so far. In the test file it is
`{ made_it_in = { at_least = 80 } }`: `at_least` is a percentage, from 0 to 100 (not `at_least_pct`).

**Settling.** When the blocks finish, the run keeps going for up to 3 s so the balls come to rest, with the motors
holding their last commands and the stations stopped. The wait doesn't count against the time limit. Checks that run
"throughout" (tilt, a tip-over, a walk-off) don't run during the settle: the blocks are done, and the settle only
counts balls. A robot left driving could tip over unreported in that time. If the run ends any other way (a check
failed, it ran out of time, the robot tipped over, you pressed Stop), balls are sorted where they are, and any still
moving read "still moving when the run ended".

**Station settings.** In Setup, each station has a row of boxes for the field's values: time between balls, stock
(empty means "never runs out", at most 50 are fed in one run), and the spreads. **Every spread is plus or minus its
value**: "Left/right angle (± degrees)" of 5 means each ball goes up to 5° either side. The **Release box**'s three
boxes are **along** the way the station feeds, **across** it and **up**, not the field's x, y and z, so a station on a
slanted wall spreads along the wall. A change is saved in the test's
`[setup]`; the box shows the field's own value until you change it. A station that feeds a piece that isn't a ball
(a coral, say) has three more rows, in plain words: **Tipped up** (0 lying on its side, 90 standing on end; for a flat
piece 0 is on its edge and 90 lying flat) and **Tipped up varies by** (± degrees), **Facing** (degrees from the way
the station feeds) and **Facing varies by**, and last **Extra tumble** (± degrees about each axis). Every piece the
station sets down is placed just clear of the chute floor, whichever way it lies.

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
has these axes (five for a piece that isn't a ball). Tick the ones to spread over, and give a from, a to and a number of steps:

| Axis | Moves the ball |
|---|---|
| **Offset across (in)** | left (+) or right (-) of the robot's center line |
| **Distance ahead (in)** | nearer or farther along the robot's heading |
| **Angle (degrees)** | the robot's start heading turns by this, counterclockwise from above, so it approaches at an angle |
| **Turned (degrees)** | the ball turned about the vertical. It does nothing to a round ball. For another piece it reads **Facing**: 0 lies in line with the robot's path, 90 across it |
| **Tipped up (degrees)** | (pieces that aren't balls) the piece's long axis tipped up from lying: 0 lying on its side, 90 standing on end |

The placements are every combination, at most 25 (the count is shown, and a spread over 25 is refused). The robot keeps
its start. The ball that moves is the floor ball nearest in front of the start (within 30° of its heading and 3 m); an
axis you leave out keeps that ball's own offset and distance. A placement that would put the ball inside the robot is
refused with a message, since it can't be a real place. Press **Run the spread**. The result is a table with one row
per placement, "picked up" or not, and each run is kept (the last 3 spreads are kept, apart from sweeps); click a
row to open its replay. It is saved in the test as `floor_spread = { offset_in = [-6, 0, 6] }` under `[setup]`.

For a piece that isn't a ball, such as a coral, **Tipped up** is the fifth axis (`tip_deg`, 0 to 90 in the test file):
`tip_deg = [0, 45, 90]` tries it lying, leaning and standing on end. Its turn is then measured from the robot's
heading, and an axis you leave out keeps how the field has the piece lying. The results name how it lay in plain
words: "picked up 2 of 3; missed standing on end". Two placements that would read the same get what differs added
("lying flat (turned 45°)"). A standing piece looks the same turned any way, so a spread over the turn of a piece
standing on end would give identical rows; try it on its own.

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
| *piece* **in** *body* | A game piece is inside a body of the robot, and the robot carries it: its center is inside the box around the body's shapes, and it has been off the carpet for 0.1 s. A piece the robot drove an open-bottom box over, still on the carpet, isn't in. *any piece* means any of them. |
| *piece* **is held by** *part or body* | A game piece is held by that part or body of the robot. See [Holding a piece](#holding-a-piece). |
| *piece* **rests on** *thing on the field* | The field thing carries the piece's weight. See [Placing a piece](#placing-a-piece). |
| *piece* **is seated at least** *n* **in on** *rod* | A piece with a hole has gone that far onto the rod. See [Placing a piece](#placing-a-piece). |
| **hanging from** *bar* | The robot's hooks hold it up from that bar. |
| **holding level** *level* | The robot holds that climb level (the field's own rule, such as the 2022 hangar's). |
| *n* **scored in** *goal* | At least *n* balls have gone into the goal (**any goal**: into all of them together). On a field whose piece isn't a ball, such as 2024's NOTE, the check says "pieces". See [Shooting](#shooting). |
| **robot is in** / **out of zone** *zone* | The robot's center, seen from above, is inside (or outside) one of the field's zones: an area marked on the carpet, such as 2024's starting zone or wing (a range file's `[[zone]]`, [docs/test-range.md](test-range.md#floor-zones)). Only on a field with zones. In the test file: `{ in_zone = { zone = "blue_start", is = "out" } }` (`is` is `"in"`, the default, or `"out"`): "out of the starting zone" is 2024's LEAVE, by the robot's center. |
| *turret* **aimed at** *goal* **within** *n* **°** | The turret's launcher points within *n*° of the goal's center. See [Aiming](#aiming). |
| *cylinder* **is** *extended or retracted* | The cylinder's rod is all the way out, or all the way in. See [Pneumatics](#pneumatics). |
| *stored or working* **air pressure above / below** *n* **psi** | The air in the tanks (stored), or after the regulator (working), is above or below that. See [Pneumatics](#pneumatics). |
| *element* **is above / below** *n* *unit* | A moving part of the field (a door, a flap, a portcullis) has swung (°) or slid (in) past that from where it rests. See [Moving field elements](#moving-field-elements). |
| **robot clears** *element* **by at least** *n* **in** | Nothing on the robot that collides comes closer to that part of the field. See [Clearance to a field element](#clearance-to-a-field-element). |
| **partner is lifted at least** *n* **in**, optionally **level within** *n* **°** | The partner robot's lowest frame corner is that high above where it rested (and it tilts no more than that). See [A partner robot](#a-partner-robot). |
| **partner moved at least** *n* **in** | The partner robot's center has gone that far across the floor. See [A partner robot](#a-partner-robot). |
| **sensor** *sensor* **is on / off**, or **is seeing / isn't seeing** *color* | One of the robot's `[[sensor]]`s: a beam break is on while broken, a distance sensor while something is closer than its threshold, a color sensor while it sees a piece (of that color). See [Sensors](#sensors). |
| **camera** *camera* **sees** *tag N* / *any tag* | One of the robot's cameras (`kind = "camera"`) sees that AprilTag of the field, or any. Offered on a field with AprilTags. See [Sensors](#sensors). |

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

## Holding a piece

The **held** check, *piece* **is held by** *part or body*, is for grabbers: does the claw keep the coral, however the arm
moves? It is true for a piece when all of these have held for 0.1 s:

- **Lifted.** The piece's lowest point is at least 1 cm above whatever is under it that isn't the robot (the carpet, a
  field element).
- **Slow relative to the part.** It is moving no faster than 0.05 m/s compared with the part it touches, so a piece
  riding along with a swinging arm is held, and one sliding down the jaws isn't.
- **Touching.** It touches a geom of that part or body.

A short gap in the touching is forgiven: a piece passing from one jaw to the other is still held, and one step with the
contact lost doesn't end a hold. A piece you haven't held yet isn't held.

Use it as **wait until** (a grabber closing on a coral) and then as **expect** with **the whole time** ticked, so every
step from there to the end is checked. A **held throughout** check that fails ends the run, and the sentence says when
and how:

- `the coral slipped out of claw at 2.3 s while claw.arm was moving` (the contact was lost)
- `the coral slid in claw at 2.3 s while the robot was driving` (still touching, but moving relative to the part)
- `the coral was set down on branch_l2 at 4.4 s while nothing was moving` (it came to rest on a thing of the field)

What was moving is the last joint a **spin** or **move** block started or was still turning, or a **drive** or **turn**
under way, within the second before: the joints first, then the robot ("claw.arm and claw.jaw were moving and the
robot was driving"); "nothing was moving" says no block was. If the piece was never held by that part: "the coral was
never held by claw at 3.0 s" (when the check failed). In the replay, a cross under the time slider marks the slip;
point at it for the words, or click it to jump there with the piece outlined.

**Placing ends the hold, and that is passing.** A held-throughout check is passed (and ends) when the piece is set down
on a rod or element that a seated or rests-on check in the same test names: the test passes with "held by grabber from
1.4 s until it was set down on branch_l2 at 4.4 s". Setting it down anywhere else, or dropping it, still fails.

## Placing a piece

Two checks say a piece went where it should.

**Rests on.** *piece* **rests on** *thing* is true when the thing (a shelf, a box, a rod, or "a piece": any other
piece) carries at least half the piece's weight, the piece has been at rest for 0.5 s, and no part of the robot touches
it. A piece still in the grabber is not resting. If it fails, the sentence says why ("the coral doesn't rest on
shelf (a robot body still touches it)").

**Seated.** For a piece with a hole that slides onto a rod (a coral on a branch, a hatch panel on a peg), *piece* **is
seated at least** *n* **in on** *rod* is about how far onto the rod it is. The seated depth is how far the rod's tip
has gone into the piece's hole, measured along the rod. It is measured whenever the rod runs through the hole (the
piece's axis within 30° of the rod's), even while the grabber still holds the piece. Its limit, **could go**, is the
smaller of the rod's length and the piece's own length. Examples of what a run says:

- `seated 3.5 in of 4 in on branch_l2`
- `seated only 0.8 in on branch_l2 (could be knocked off)`: less than a third of how far it could go. A piece only
  partly on a rod is held up by almost nothing.
- a piece lying across a rod, or resting on the rod's end without it in the hole, has no depth. A **seated** check then
  fails ("the coral isn't on branch_l2"), and **rests on** still works.

The depth is a signal for the chart, `seated.<rod>`, in inches. The line has a gap while no piece is on the rod, and it
shows the deepest piece when more than one is. After a run, a line under the sentence gives the depth at the end.

A solid piece on a shelf, a tote on a tote, has no depth: use **rests on**.

## Grip material

A grabber's pads are what holds a piece, so how well they grip matters as much as how hard they squeeze. A body of a
part can be given a **grip material**; the picker is in the Part tab (the Inspector, on a body that touches things) and
in Setup (a **Grip** group, for the bodies you ticked under **Where a piece should end up**, or named like a grabber). The
choices, with the friction the simulation uses for each:

| Material | Friction |
|---|---|
| Rubber | 0.8 |
| Nitrile | 0.8 |
| Grip tape | 1.1 |
| Polycarbonate | 0.4 |
| Aluminum | 0.35 |
| HDPE / UHMW | 0.2 |
| **My own number** | what you type (above 0, up to 3) |

(Rubber and nitrile are the same estimate, so they act the same: a sweep over both won't show a difference.) The
picker shows each estimate beside the name. Left alone (**Default — 1.0 (MuJoCo's default; the larger of pad and piece
applies)**), a pad keeps the friction its part gives it: MuJoCo's default 1.0, about grip tape's, unless `part.toml`
sets a `friction` on its geoms, and the larger of the pad's and the piece's friction applies. When `part.toml` sets
one, the picker's first choice says so: **Default — 0.6 (part.toml's; the larger of pad and piece applies)**.

**Every number here is an estimate, marked UNCALIBRATED.** Friction against game pieces isn't published, so these are
typical ranges. A run that depends on a grip says so in a line under the sentence ("claw.pad_left grip: nitrile,
friction 0.8 (UNCALIBRATED estimate; measure yours with a sled pull)"), and a test of held, rests-on or seated with
no grip set says what the pads have instead ("grip: the pads have MuJoCo's default friction 1.0 — about grip tape's;
the larger of pad and piece applies — an UNCALIBRATED estimate; ...", or the friction `part.toml` gives them). Never take
a grip result from a material or the default as measured.

**How to measure your own friction (a sled pull).** Glue your pad material to the bottom of a small weighted sled and
pull it with a luggage scale across the piece's surface (a scrap of the pipe, or the piece itself) lying flat, reading
the scale while it slides steadily, not at the first jerk. **Friction = the pull ÷ the sled's weight** (pull 3.6 N on
a 4.0 N sled: 0.9). [Measuring: sled pull](measuring.md#sled-pull) has the picture, the steps and a form to print.
Then either pick **My own number** and type the average, or write the trials in a friction measurement file and name
it as the body's grip: `[body.pad] grip = "pad.measure.toml"` (relative to the part's folder), or `grip = { pad =
"pad.measure.toml" }` on the robot config's `[[part]]` (relative to the config). With a file, the run's line names it:
"claw.pad grip: measured, friction 0.8 (pad.measure.toml, J. Smith, 2026-11-10, 3 trials)". A file whose pulls aren't
good enough yet (fewer than 3, or disagreeing by more than 15%) is still used, and the line says why and keeps
UNCALIBRATED.

**What a grip does in the simulation.** A grip sets the friction of every geom of that body that collides (a wheel's
tread keeps its own). The pad also gets a firm contact and priority over the piece's, so the pad's friction applies and
not the larger of the two. A piece's own surface (set in its range file) matters too: a rigid piece has a firm contact
by default, so it doesn't creep through a squeeze. The grip is saved in the part's own edits as `[body.<name>] grip =
"nitrile"` (or a number) in `scrimmage-edits.toml`, and Undo in the Part tab takes a grip change back, in both
import and plugin mode. Test files don't set grip; to try it in a robot config, see [parts.md](parts.md).

**Would grip tape help?** Press **Try materials…** in Setup, beside a part's first pad: see
[Trying values](#trying-values-sweeps).

## Shooting

A robot whose config has a `[range]` (a field with goals and balls) and a `[shooter]` (its launcher, stages and
hopper) shoots under the blocks, as it does under robot code: see [the test range](test-range.md). Spin the launcher's
motor to get the wheels up to speed, then spin the stages that carry the balls to it. The robot's preloaded balls, and
any it picks up, go out of the launcher, fly, and score or miss. Nothing about shooting needs a block of its own.

- **The check.** *n* **scored in** *goal* is true once at least *n* balls have gone into that goal. **any goal** counts
  every goal together.
- **The result** has a line per goal with a ball in it: `scored 8 in hub_blue`. To see why a shot missed,
  `scrimmage test run` also prints a table of the pieces that took part (each shot, a fed or preloaded piece, a floor
  piece that moved): its number and kind, where it came from, when it was fired and its exit speed, its outcome, and
  where it went (the goal and time it scored, where it rests or left the field, what it hit first, and how high a
  missed shot got against the goal's rim). The run's `result.json` keeps them as `pieces`, and the MCP `test_run`
  returns them too (in meters and m/s). Two tables, two sets of words: this table's outcome column is the *feed*
  outcome (`in`, `stuck`, `placed`, `missed`) for a piece the run tracked (a fed piece, or the floor piece ahead of the
  robot: always in a spread, and in any run of a test that has a hopper), and its last column then says where (`in intake.hopper`, `in the robot`, `stuck on drive.chassis`). So a
  NOTE lifted into the bin reads `in` here, where the scenario ledger (a `scrimmage run` scenario's ball table) says
  `on_field`, because it is still on the field. Pieces the run did not track keep the ledger's words.
- **The replay** shows the balls in flight.

In the test file:

| Form | Keys |
|---|---|
| `{ scored = { goal = "hub_blue", at_least = 3 } }` | `goal`: a goal of the field, or `"any"` for all of them. `at_least`: a whole number of balls, 1 to 1000. |

## Aiming

A stock `turret` part (see [parts.md](parts.md#turret)) carries a launcher and turns it on its own, so the robot can
face any way and still shoot at the goal.

- **aim** *turret* **at** *goal* points the turret at the goal's center and keeps it there every moment from then on,
  while the robot drives and turns, until a later block spins, moves or stops the turret's motor (or another aim
  takes it over). It takes no time: the next block starts at once. It turns the turret through its own motor, so the
  turret's motor slot needs a device ID.
- *turret* **aimed at** *goal* **within** *n* **°** is true when the launcher points within that many degrees of the
  goal's center, seen from above (2° unless you change it). It measures the launcher on the turret (the `[shooter]`'s
  launcher), or the turret's own front if no launcher rides on it.
- **The chart** gets an `aim_error` signal in a test with an aim block or an aimed check: how far off the turret
  points, in degrees, positive when the goal is to its left.

Things to know:

- **Aiming doesn't lead the shot.** The turret points at where the goal is now, and takes no account of the robot's
  own motion. A ball shot while the robot drives keeps the robot's speed, so it lands off by about the robot's speed
  times the ball's time in the air (at 2 m/s and a 1 s flight, about 2 m). Shoot while turning on the spot, or slow
  down to shoot.
- **The turret's stops.** A turret only turns as far as its `range`. A goal past a stop leaves it at the stop, and a
  failed aimed check says so: `hub_blue is outside the turret's range (it stops at 30°)`. A turret whose range is more
  than a full turn (up to ±360°) picks the way of pointing at the goal that is nearest where it is now, so it doesn't
  unwind for nothing.
- **At a stop, the check reads the launcher.** A goal 1° past a stop, checked within 2°, reads as aimed: the launcher
  really is within 2° of it.

In the test file:

| Form | Keys |
|---|---|
| `do = "aim"` | `turret`: a stock turret part's name. `at`: a goal of the field. |
| `{ aimed = { turret = "turret", at = "hub_blue", within_deg = 2 } }` | `turret`, `at` as for aim. `within_deg`: more than 0, default 2. |

## Pneumatics

A robot with a stock `cylinder` part (see [parts.md](parts.md#cylinder) and [pneumatics.md](pneumatics.md)) has an
air system, and these blocks work it with no robot code.

- **set** *cylinder* **to extend** pushes the rod out, and **to retract** pulls it in. It takes no time: the valve
  switches and the next block starts at once, while the rod moves. Follow it with **wait until** *cylinder* **is
  extended** to wait for the rod.
- **set** *cylinder* **to off** turns the valve's coils off, as robot code does: on a single solenoid (or a valve with
  no device ID) the spring retracts the cylinder, and on a double solenoid it stays where it is. On a single solenoid
  with `invert = true` the spring extends it, as it would on the robot.
- *cylinder* **is extended** (or **retracted**) is true when the rod is within 0.25 in of that end of its stroke.
- *stored or working* **air pressure above / below** *n* **psi** reads the tanks (stored) or the regulator's output
  (working). It is strict: at exactly 120 psi the air is not above 120.

**The valve needs no device ID here.** The blocks switch the valve themselves, so a stand-in robot's cylinder works
with its `valve` slot empty. (Motors still need IDs.) **The compressor runs the whole run**, as if the robot code had
turned its closed loop on, and the pressure switch still starts and stops it.

**The run's air.** A robot with cylinders adds a line to the result, such as `air: 120 psi at the start, 84 psi at the
end, lowest 81 psi at 14.2 s; the compressor ran 9.0 s`. If the tanks fell below the working pressure it warns: `the
tanks fell below the 60 psi working pressure at 2.0 s: the cylinders pushed weaker from then on`. The notes say
`UNCALIBRATED: pneumatic numbers are estimates (lift valve conductance); see docs/pneumatics.md.`, naming each
cylinder's valve (a measured one with its file, who measured it, when and from how many trials).

**The chart** gets `air.psi` (the tanks), `air.working_psi` (after the regulator) and each cylinder's
`<name>.extension` (how far its rod is out, in inches). **The replay** draws each cylinder's barrel and rod between its
two ends.

In the test file:

| Form | Keys |
|---|---|
| `do = "set_cylinder"` | `cylinder`: a cylinder part's name. `to`: `"extend"`, `"retract"` or `"off"`. |
| `{ cylinder = { cylinder = "lift", is = "extended" } }` | `cylinder`: a cylinder part's name. `is`: `"extended"` or `"retracted"`. |
| `{ pressure = { side = "stored", above = 60 } }` | `side`: `"stored"` (the default) or `"working"`. `above` or `below`: psi, 0 or more. |

## Sensors

A robot with `[[sensor]]` tables in its config (beam breaks, distance sensors, color sensors and cameras; see
[sensors.md](sensors.md)) has a **sensor** check for them, and a camera on a field with AprilTags has a **camera**
check too. It needs no robot code and no channel: the Test tab reads
the sensor itself.

- **sensor** *mouth* **is on** (or **is off**), for a beam break or a distance sensor. A beam break is on while a piece
  breaks its beam. A distance sensor is on while what it sees is closer than its `threshold` (half its `max_range`
  unless the config says).
- **sensor** *eye* **is seeing** *red* (or **isn't seeing** *red*), for a color sensor. Pick **a piece** for any color,
  or one of the named colors (red, blue, yellow, orange, green, purple, white, gray). A color sensor sees a piece
  within its `max_range` (10 cm at most), and **is seeing red** is true while the piece it sees is nearest red of those
  colors. **isn't seeing red** is true with nothing in front, or a piece of another color.
- **camera** *front_cam* **sees** *tag 7* (or **any tag**), for a camera on a field whose range file gives
  `[range] apriltags`. It is true while the camera sees that tag: in its view, close enough, not too edge-on and with
  nothing in the way ([sensors.md](sensors.md#a-camera) says exactly when). The tag picker lists the field's tags by
  ID. A **sensor** check on a camera is on while it sees any tag.
- **Wait for it.** **wait until sensor bin is on**, then **stop** the intake's roller, is what robot code does with a
  beam break. `examples/blocks/beam` does just that.
- **A failed check says what the sensor read:** `expected bin on, but it was off (clear)`, `expected range on, but it
  read 78.7 in (it is on closer than 39.4 in)`, `expected eye to see red, but it saw a blue piece`, or `expected eye to
  see a piece, but it saw no piece`. A camera's says what it saw: `expected front_cam to see tag 7, but it saw tags 3,
  4`, or `expected front_cam to see a tag, but it saw no tags`.
- **Problems** before a run: a robot with no sensors (`this robot has no sensors; add a [[sensor]] to its config`), a
  name the robot doesn't have (`hopper isn't a sensor; the robot's sensors: bin, eye`), or a color on a sensor that
  can't see one (`bin is a beam break, which can't see colors; only a color sensor can see red`). A camera check has
  its own: a robot with no camera, a sensor that isn't a camera, a field with no AprilTags (`this field has no
  AprilTags; a range file gives them with [range] apriltags`), or a tag the field doesn't have (`this field has no tag
  99; its tags: 1, 2, ...`).

**The chart** gets `sensor.<name>` for each sensor, drawn at first (unless the test's `chart` line names others): 0
or 1 for a beam break (1 while broken), inches for a distance sensor (its `max_range` with nothing in range), and for
a color sensor the number of the color it sees, with a gap while it sees none. The chart's key says which number is
which color: 0 red, 1 blue, 2 yellow, 3 orange, 4 green, 5 purple, 6 white, 7 gray. A camera's is how many tags it
sees.

**The replay** draws each sensor as a thin line: a beam break from end to end, a distance or color sensor from where
it looks out to its `max_range`. The line is lit while the sensor is on. A camera is drawn as its view cone (four
lines out as far as it sees the field's tags: its `max_range`, scaled by the tags' size, lit while it sees a tag), with a green line from the camera to each tag it sees at the
moment shown. The Drive tab's live view draws the same lines and cones, never lit.

**A blink too short to see.** The chart and the replay take each sensor 50 times a second (every 20 ms), but a wait
or a check reads it every physics step (1 ms at the default step). So a beam broken for less than 20 ms, by a fast
piece, can end a **wait until sensor bin is on** while the chart shows it never broke.

In the test file:

| Form | Keys |
|---|---|
| `{ sensor = { sensor = "bin", is = "on" } }` | `sensor`: one of the robot's `[[sensor]]` names. `is`: `"on"` or `"off"`. |
| `{ sensor = { sensor = "eye", is = "on", sees = "red" } }` | `sees`, a color sensor only: `"red"`, `"blue"`, `"yellow"`, `"orange"`, `"green"`, `"purple"`, `"white"` or `"gray"`. Left out, any piece. `is = "off"` with `sees` is "isn't seeing" that color. |
| `{ sees_tag = { camera = "front_cam", tag = 7 } }` | `camera`: one of the robot's camera `[[sensor]]`s. `tag`: a tag's ID (a whole number) in the field's layout. Left out, any tag. |

## Moving field elements

A field with a `hinged` or `sliding` element (a door, a flap, a portcullis, a spinning wheel; see
[docs/terrain.md](terrain.md#moving-elements-hinged-and-sliding)) has a check for each and a chart signal. The
built-in fields with them are `2016-defenses` (the portcullis, the sally port, the drawbridge and the cheval de
frise) and `terrain-library` (the 2020 CONTROL PANEL wheel, `control_panel`).

- *element* **is above / below** *n* *unit* reads how far the element has moved from where it rests: **°** for a
  hinged one, **in** for a sliding one (the unit switches when you pick the element). Its sign is the element's own,
  as [docs/terrain.md](terrain.md#moving-elements-hinged-and-sliding) gives it for each hinge. The sally port opens
  toward negative angles, so "sally_port is below -60°" means it is more than 60° open.
- **The chart** gets `element.<name>` for each one. A hinged angle keeps counting past a full turn: three turns of
  the CONTROL PANEL read 1080°.
- A **touches** check can name a moving element like any other part of the field.
- Naming an element that doesn't move is a problem: `portcullis_post_left isn't a moving element; this field's
  moving elements: cheval_1, cheval_2, cheval_3, cheval_4, drawbridge, portcullis, sally_port`.

In the test file:

| Form | Keys |
|---|---|
| `{ element = { element = "portcullis", above = 12 } }` | `element`: a hinged or sliding element of the field. `above` or `below`: in degrees for a hinged one, inches for a sliding one, 0 at rest. |

## Clearance to a field element

**robot clears** *element* **by at least** *n* **in** answers "does my robot fit under the trench, and by how much?".
It is the smallest distance between any part of the robot that collides and any part of that element. Put it in an
**expect** block with **the whole time** ticked to check a whole drive. The worked example is
[`examples/blocks/trench`](../examples/blocks/trench/README.md).

- **Only parts that collide count**: the chassis and its bumpers, parts from CAD, and stock parts with
  [`collide = true`](parts.md#stock-parts-that-collide). A stock elevator or arm without it touches nothing, so it
  doesn't count (and passes through the trench). When the robot has stock parts with collide off, the test gets a
  hint under **Good to know** (over **Run**; it doesn't stop the run) and a note on each run that names them:
  `clearance counts only parts that collide: arm and elevator have collide off, so they aren't measured (set collide
  = true on them)`.
- **It is measured out to 0.5 m (19.7 in).** Farther away it reads as clear, so *n* can be at most 19.6 in.
- **The result** gets a line for each element the test measures: `closest to trench_arm: 2.89 in at 2.5 s`, or
  `trench_arm: never within 20 in` when nothing came within 0.5 m. A failed check says the clearance then: `expected
  the robot to clear trench_arm by at least 0.5 in, but it was 0.45 in at 1.7 s`. A touch reads 0.
- **The chart** can show `clearance.<element>` in inches, with a gap while nothing is within 0.5 m. Clearance is
  only measured for the elements a test names (in a **robot clears** check or in its chart), so the chart's picker
  lists each field element's clearance under **From the next run:**. Picking one puts it in the test's chart, and
  the next run measures it.
- **Timing.** It is measured after every physics step, from where the parts were at the start of that step: one
  physics step behind (1 ms at the default step).

In the test file:

| Form | Keys |
|---|---|
| `{ clears = { element = "trench_arm", at_least_in = 0.5 } }` | `element`: any element of the field. `at_least_in`: inches, more than 0 and at most 19.6. |

## A partner robot

A partner robot is a second robot on the field with no robot program: it just sits there, to be pushed, lifted or
driven around (a robot that died on the field, a partner to buddy-lift, a robot in your way). It is a 6-wheel tank
drive on the stock chassis, and it collides with your robot, the field and game pieces. The worked example is
[`examples/blocks/partner`](../examples/blocks/partner/README.md).

**In Setup**, tick **A partner robot** under **Partner robot**:

| Box | Default | What it does |
|---|---|---|
| **Ahead of my robot by** | 60 in | how far its center is ahead of your robot's start, along the way your robot faces |
| **Facing** | the same way | **the same way**, **toward me** (turned 180°) or **sideways** (turned 90°) |
| **Drive: brakes on** | on | on: its drive is in brake mode; off: its wheels coast |
| **Hangs on** | no | on a field with chains only: **no**, or a chain to hang the partner from. Hanging, it shows **along** in place of the three boxes above |
| **along** | 18 in | how far from the chain's low point it hangs, along the chain (signed) |
| **across** | -2 in | how far to the side of the chain its center hangs, to the left of the chain's direction (signed) |

A hanging partner faces along its chain and is held level under the chain's joint nearest **along** (see the
[README](../README.md#a-partner-robot)). One placed overlapping the field (a STAGE leg, the core) is refused before the
run: `the partner robot (Setup) starts inside obstacle stage_blue_leg2 (their outlines overlap by 5.91 in)` (at
**along** 35.4 in, **across** -2 in). One past its limits says so: `the partner robot (Setup) hangs 50 in from the
chain's low point; at most 45.04 in either way`. It counts for HARMONY on an `"off-carpet"` field, and the chart's
picker then offers `climb.harmony` and `climb.hold.<chain>.robots` ([climbing.md](climbing.md#onstage-and-harmony));
they aren't in the default chart.

If the robot file has its own `[partner]` ([README](../README.md#a-partner-robot)), Setup shows it ticked, where the
robot file puts it, until you change it here. Unticking it then keeps the robot file's partner out of this test.

**Brake mode is not a lock.** A braked FRC drive resists being pushed through its motors' back-EMF, so a shoved
braked robot still rolls, only slower. The partner's brake is worked out from its motors: two CIMs a side through
10.71 to 1, as the stock tank drive has, which gives each wheel 0.29 N·m·s/rad of damping (an estimate from the motor
model, not a measurement). In the worked example a braked partner is pushed 63 in in 2 s, a coasting one 112 in,
about 1.8 times as far. A robot that also has friction brakes would be harder to push.

**The signals** are in the chart, all measured from where the partner rested 0.3 s after the start (the
[settle](#times-and-limits)):

| Signal | Meaning |
|---|---|
| `partner.height` (in) | how far the lowest corner of its frame has risen. From where it rested, not from the carpet, so its bumper clearance doesn't count as lift. |
| `partner.tilt` (°) | how far it leans from level |
| `partner.moved` (in) | how far its center has gone across the floor |

**The checks.** **partner is lifted at least** *n* **in** reads `partner.height`; tick **level within** to also want
it tilted no more than that many degrees. **partner moved at least** *n* **in** reads `partner.moved`. On a test with
no partner they are a problem: "there's no partner robot; add one in Setup".

**Where it starts.** A partner that starts overlapping your robot's start (a 0.9 m square), a field element on
the carpet or a game piece lying on the floor is refused before the run:
`the partner robot (Setup) starts inside the robot (their outlines overlap by 7.87 in)`,
`the partner robot (Setup) starts inside element portcullis_platform (their outlines overlap by 4.72 in)`, or
`the partner robot (Setup) starts inside a floor piece (fuel) (their outlines overlap by 19.83 in)`. Move it. A
partner from the robot file's `[partner]` says `[partner] starts inside ...` instead. A piece other than a ball or a
disc counts as its bounding sphere, so a long piece lying near the partner is refused a little early. A
[floor spread](#floor-spreads) whose placement puts its piece under the partner is refused before its first run too:
`the floor spread's placement 66 in ahead puts the ball under the partner robot: move the partner farther away in
Setup, or leave that placement out`.

In the test file:

| Form | Keys |
|---|---|
| `[setup]` `partner = { ahead_in = 50, heading_deg = 0, brake = true }` | `ahead_in`: inches, required. `heading_deg`: degrees, turned from your robot's heading, default 0. `brake`: `true` (the default) or `false`. Or `partner = false` for no partner, even when the robot file has one. |
| `[setup]` `partner = { hang = "chain_blue_center", along_in = 17.7, across_in = -2 }` | `hang`: a chain of the field. `along_in`: inches from its low point along the chain, signed, default 0. `across_in`: inches to the side, signed, default 0. No `ahead_in` or `heading_deg`: the chain places it. |
| `{ partner_lifted = { at_least_in = 2, level_within_deg = 5 } }` | `at_least_in`: inches, more than 0. `level_within_deg`: degrees, more than 0, optional. |
| `{ partner_moved = { at_least_in = 60 } }` | `at_least_in`: inches, more than 0. |

## Times and limits

- **Times include the 0.3 s settle.** Before the first block, the robot rests on the carpet for 0.3 s with every motor
  off. Every time in a run (the outcome, the checks, the chart) counts from the start of that settle, so the first
  block starts at 0.3 s.
- **A move counts as reached** when the joint is within 1° (a hinge) or 0.25 in (a slide) of the target.
- **A move, a wait until or a drive can time out.** A move gives up after 5 s, a **wait until** after 10 s, a drive for
  a distance after 10 s. The run then ends with a sentence that names the block, such as `waiting for a hook to touch
  rung2 timed out at 12.1 s (block 2 inside block 3)`.
- **A repeat until has a max, default 20.** After that many rounds the run ends, saying which repeat gave up.
- **Each test has a time limit, default 60 s.** The run ends there with "ran out of time". A match
  ([Playing a match](#playing-a-match)) has none: the game's phases set its length.
- **"Walked off" ends a climb run.** If the robot has hung from a bar, then lets go of every bar while a hook was
  carrying it, and then drops, the run ends with "a hook walked off". A robot that hung and then touches the floor
  with no hook letting go ends it too.
- **The settle after the blocks.** When the blocks finish and balls were fed, the run goes on for up to 3 s while the
  pieces come to rest. See [Feeding from a station](#feeding-from-a-station).
- **A robot counts as tipped over at 60°.** The run ends with "the robot tipped over" when the robot's body leans more
  than 60° from upright. (The **tilt** check is separate: it only tests what you ask.)

To change a block's own limit, right-click the block (or open its menu) and choose **Block settings…**:

| Block | Settings |
|---|---|
| **move**, **wait until**, **feed one** | **Give up after** (s): its timeout (5 s for a move, 10 s for the others) |
| **drive** | **Drive at** (V, the default 4 V) and, for a distance, **Give up after** (s, 10) |
| **turn** | **Turn at up to** (V, the most it uses, 4 V) and **Give up after** (s, 10) |
| **repeat until** | **Give up after** (rounds, 1 to 100, the default 20) |

An empty box is the default (the file leaves the key out). They are the file's `timeout`, `volts` and `max` keys on
the block, saved like any other change (Undo doesn't take one back: set it again). The whole test's `time_limit` is
in the `[test]` table: edit it in a text editor, and the tab keeps it when it saves. The 3 s settle after the blocks
is fixed, and the file can't change it.

## Playing a match

A range with a `[game]` (see [the test range](test-range.md)) knows a match's phases and what each goal is worth. Put
`match = true` in the test's `[test]` table and the Test tab plays the blocks as a match:

```toml
[test]
robot = "my_robot.toml"
match = true
```

- **The clock.** The match starts with the first block, after the 0.3 s settle, which isn't match time. **Auto** runs
  for the game's `auto_seconds`, then the **pause**, then **teleop**. The run ends when teleop does, even if blocks are
  left (the outcome says so; every **expect** among them fails, "the match ended before this check ran"), and even
  if the blocks finished early: the robot just sits, motors as they were, to the match's end.
- **The buzzer disables the robot.** The 3 s settle after the match runs with the robot disabled, as the field does at
  the buzzer. Pieces in the air land, but a piece that scores then is after the match and scores no points (it still
  counts as in the goal). FRC counts a piece released before the buzzer; this isn't modeled.
- **The pause disables the robot.** Every motor goes to neutral and the air is held, as a real robot held disabled
  would. Blocks keep running: a **drive** for 6 s that starts in auto counts the pause in its 6 s and starts the motors
  again when teleop begins.
- **The length is the game's.** `time_limit` can't be given with `match = true`: the test file is refused.
- **The phase check.** **match is in** *auto*, *pause* or *teleop* works in **wait until** (to wait for teleop) and in
  **expect**. In a file: `{ match = { phase = "teleop" } }`. A **wait until** for a phase has no 10 s limit: it waits
  for as long as the match has left, unless you give it a **Give up after** time of your own.
- **The points check.** **points in** *auto*, *teleop*, *endgame* or *total* **at least** *N* reads the points so far.
  In a file: `{ points = { phase = "auto", at_least = 7 } }` (`phase` is `"total"` when left out). LEAVE counts once
  auto has ended: the robot's center was in the leave zone when the match started and is outside it when auto ends.
  An auto or total check that fails before auto ends says so. The endgame reads the climb level the robot holds now,
  the points it would score if the match ended now (a range with a `[climb]`; none otherwise). So **expect points in
  endgame at least 3** passes while the robot holds ONSTAGE, even if it falls later. The result's points are scored
  at the match's end, with the level held then.
- **Problems.** `match = true` on a field with no `[game]` is listed under the fix-these-first problems, and so is a
  **match** or **points** check in a test that isn't a match.
- **The result** gains a points line first, then a line for each item. The 2024 match example
  ([`examples/blocks/match`](../examples/blocks/match/README.md)) gives `7 points: 7 in auto, 0 in teleop, 0 in
  endgame`, `1 scored in speaker_blue in auto: 5 points` and `left the starting zone: 2 points`. A piece scored in
  the pause scores nothing, and its line says so. `result.json` keeps `match` (the phases' run times, whether LEAVE was earned
  and the climb level held), `points` and `rules` (the rules broken, empty for none), and only for a match. The
  replay's mark strip has a mark at each phase change. A test that isn't a match has none of these.
- **Rules.** A game with a `[game.rules]` table (the test range page lists its keys) is checked every step of the
  match, in auto, the pause and teleop. A broken rule is reported, not enforced: the robot isn't stopped and the run
  carries on, like a referee calling a foul.
  - **Possession** counts the pieces the robot controls: the ones in its hopper, a stage or the launcher, plus the ones
    it holds by contact (touching a robot part, off the floor, moving with it, as a **held** check reads it). A piece
    it only pushes along the floor isn't controlled. More than the limit for longer than 3 s (G403's MOMENTARY) is a
    breach, noted at the moment the count went over.
  - **Extension and height** are the same measures `climb run` uses: how far the colliding parts reach past the frame,
    and the robot's own height. Going over either is a breach at once.
  - Each rule is reported once, at its first breach, with the highest value it reached; a later breach of the same rule
    only raises that value. The result gets a line per breach after the points lines, for example `rule broken at 4.2
    s: held 2 pieces (limit 1)` or `rule broken at 6.1 s: reached 14.1 in past the frame (limit 12 in)`, in your
    Units. `result.json`'s `rules` lists them as `rule`, `t`, `value` and `limit` (pieces, or meters).
  - A game with no `[game.rules]` checks nothing and costs nothing extra. Counting by contact can miss a piece wedged in
    the frame without being lifted.

One block list plays both phases: use `wait until match is in teleop` where the auto part ends and the teleop part
begins.

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
- **Where a piece should end up.** Tick the bodies a piece lands in or is held by: a hopper, an intake, a claw. See
  [Feeding from a station](#feeding-from-a-station) and [Holding a piece](#holding-a-piece).
- **Grip.** For a grabber's pads, a picker of grip materials, and **Try materials…** beside a part's first pad. See
  [Grip material](#grip-material).
- **Piece.** On the intake and station fields, the piece to use: the field's own, or any ball (a 2017 FUEL, a 2019
  CARGO) or other piece (a 2025 coral, a 2023 cone, cube, a 2017 gear, a 2019 hatch panel, a 2015 tote, a 2024 note) from the
  list. A piece that doesn't fit (too wide for the station's chute, or overlapping the field's floor pieces) gives a
  plain problem in Setup and **Run** stays off until you pick another. The test file keeps this as `piece` under
  `[setup]` (older tests with `ball` still work). A measured ball goes in the test file by hand: `ball =
  "fuel.measure.toml"` under `[setup]`, a ball measurement file with a `base` preset, relative to the test file
  ([measuring.md](measuring.md#using-a-measurement)); its measured numbers replace the preset's. The 2023 cube is a
  rigid stand-in: it doesn't squash. The 2024 NOTE turns soft near the robot
  ([test-range.md](test-range.md#soft-pieces)).
- **Station.** One group of boxes for each station: time between balls, stock, and the spreads (each ± its value).
- **Shuffle** and **New shuffle**: which draw of the spreads the stations feed.
- **Floor spread.** Where the floor ball is tried. See [Floor spreads](#floor-spreads).
- **Partner robot.** A second robot with no program, ahead of yours. See [A partner robot](#a-partner-robot).
- **Add the station blocks.** Adds the starter feeding blocks to a test that has none. On a robot with a grabber it adds
  start feeding, wait until a piece is held by the grabber, stop feeding (the hopper starter can't succeed there).
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
  its rod. A field's moving elements (`element.<name>`), a partner robot (`partner.height`, `partner.tilt`,
  `partner.moved`), the robot's sensors (`sensor.<name>`) and the clearances (`clearance.<element>`, listed under
  **From the next run:**) are there too: see [Moving field elements](#moving-field-elements),
  [A partner robot](#a-partner-robot), [Sensors](#sensors) and [Clearance to a field element](#clearance-to-a-field-element).
  A name in the `chart` line that the run has no signal for (a typo, or a joint renamed since) isn't drawn. A note
  under the chart says "This run has no signal called arm.lift", and the hints above **Run** say so before a run.
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

**Every row uses the test's Setup.** A sweep of the weight, a mount or a grip runs each value on the field the test's
Setup describes (its piece, station, shuffle and partner robot), as a single run does.

**Try materials…** (beside a part's first pad in Setup) tries a **grip**: the test runs once for each material you
ticked, on every pad of the part together by default (a pair of pads needs both changed: one slick pad can hide that
the other grips alone). "Which pads?" lists every pad of the part, then each on its own. The rows say how each
material ended. The `test_sweep` tool takes the same target: a part (all its pads) or one `<part>.<body>`, with
material names as the values. Your files don't change.

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
Change them in the Robot tab if you like. A cylinder's valve needs no ID under the Test tab. A part with a hook is
put in front of the ladder field. A part with no body named like a hook or a hopper starts on the plain field (there
is nothing on another field for it to reach), and the tab says so when it opens; Setup's field picks another.

You can use any robot file instead: **Test a mechanism** on the start page, then **Make the test**.

## The command line and MCP tools

```bash
scrimmage test run examples/blocks/intake/intake.test.toml
scrimmage test run my.test.toml --out some/folder    # keep the run somewhere else
scrimmage test spread examples/blocks/intake/spread.test.toml   # one run per floor-spread placement
```

It plays the blocks with no window, prints how it ended, each check and, on a field, a table of the pieces that
took part and where each went ([Shooting](#shooting)), and keeps the run in `<test>-runs/` (or `--out`).
The exit code is **0** when the test finished and every check passed, **1** when it didn't, and **2** when it couldn't
run (a bad test file, a robot that won't build, a folder it can't write).

`scrimmage test spread` runs a test once per placement of its floor spread and says which were picked up. An AI agent
connected through MCP ([install step 5](getting-started/install.md#5-optional-connect-claude-code)) has three
tools. **`test_run`** runs a test file and returns the sentence, the status, the checks, each signal's range and the
folder the run is kept in (the Test tab replays it). **`test_sweep`** tries one number at up to 12 values. Both take an
absolute path. **`test_spread`** runs the floor spread. The agent writes the `.test.toml` with its own file tools.

## The examples

Thirteen worked examples are in `examples/blocks/`. Each has a README with the story, the blocks and how to change them.

- **`examples/blocks/intake`**, a roller and bin that take a ball off the carpet. Run it and it finishes at 3.3 s
  with the ball in the bin. The README has a sweep of the roller's voltage.
- **`examples/blocks/beam`**, the same intake with a beam break across its bin. The roller spins and the robot drives
  at the ball until the beam breaks, then everything stops. The beam breaks at 2.0 s, when the ball rolls into the
  bin, and the run finishes at 3.0 s with the ball resting in the bin. The README moves the beam to the intake's mouth,
  where it breaks too soon and leaves the ball outside, and tries the roller's voltage.
- **`examples/blocks/station`**, a bin over the whole top of a stand-in robot, with its bumpers against the human player
  station's chute. It starts feeding, waits until 5 balls are in, stops, and expects at least 80% of the fed balls to
  have made it in. Its station has a small spread, so the shuffle means something. The README tells of the first try
  that lost balls over a low back wall. (`examples/blocks/intake` also has `spread.test.toml`, a floor spread.)
- **`examples/blocks/reef`**, a grabber on a stand-in robot, on the `reef` field. It picks a coral up off the carpet,
  swings it over the top and drives it onto the L2 branch. It checks the coral is held throughout and seated at least
  3 in on `branch_l2`. A held check that runs throughout ends, passed, when the piece is set down on a rod or element
  that the test's own seated or rests-on checks name: "held by grabber from 1.4 s until it was set down on branch_l2 at
  4.4 s". Any other way of losing the piece still fails it. The README has a grip-material sweep. Its
  `spread.test.toml` turns the coral to six angles and picks up three: past about 15° the coral is wider than the open
  jaws.
- **`examples/blocks/ladder_climber`**, a train-wheel climber made from a CAD file with the same steps a student takes
  in the Part tab. **It reaches `rung1` and no further, and it is honest about why.** The robot hangs from `rung1`
  (the hooks carry more than a quarter of its weight), but then it tips up on its back bumper instead of rising: the
  hooks are at the front edge and the weight is in the middle, so a hook pulling up on the front mostly tilts the robot,
  and tilting swings the next rung out of reach. The motor is not stalled and the hooks work as designed. The README
  says what was tried and what to try next. The test's tilt check passes (the robot leans 20° at most).
- **`examples/blocks/turret`**, a launcher on a turret on a stand-in robot, on the 2026 field with 8 balls preloaded.
  It aims at `hub_blue`, shoots standing still, turns on the spot, then shoots while it drives 1.5 m, and checks the
  turret stays aimed within 5° throughout and that the balls score: 6 of 8 go in, all 4 standing and 2 of the 4 on
  the move (the aim doesn't lead the shot). The README tries the hood, the wheel speed, a faster drive and the same
  run without the turret.
- **`examples/blocks/pneumatic`**, an arm lifted by an air cylinder on a stand-in robot. It lifts the arm and puts it
  down 10 times and checks the air stays above the 60 psi working pressure (the lowest is 67 psi). The README compares
  the run with `scrimmage air`'s budget, and tells of the first try, whose 2 in bore used the air up in about one
  cycle.
- **`examples/blocks/trench`**, a stand-in robot with a stock elevator and an arm on its carriage, both
  `collide = true`, on the `2026-trench` field. It drives under the trench stowed and checks it clears `trench_arm`
  by at least 0.5 in the whole time: the closest it comes is 2.9 in, the elevator's top under the arm. With the arm
  raised first, the check fails at 1.7 s, before the arm hits; without the check the arm runs into the trench and the
  robot stops. The README tries how high the arm can be (up to 50°) and a taller elevator.
- **`examples/blocks/speaker`**, a stand-in robot with a stock `pinch_shooter` on the built-in `2024` field, against
  the blue SUBWOOFER with one NOTE. It tilts the channel to 58°, spins both wheels at 3.05 V (about 21.7 rps, a
  shot `launcher aim` finds there with more room than the table's lowest-arc row) and feeds the NOTE, which scores in
  `speaker_blue`. The README shows the aim table, how much room the shot has and the AMP shot.
- **`examples/blocks/camera`**, the speaker example's robot with a camera on its chassis, 0.90 m up, on the built-in
  `2024` field's AprilTags. From against the blue SUBWOOFER it checks the camera sees tags 7 and 8, the blue SPEAKER's.
  The README says why the camera is mounted high: at 0.25 m the SUBWOOFER hides both tags.
- **`examples/blocks/partner`**, a stand-in robot pushing a partner robot set in Setup. It drives into it at 6 V for
  2 s and checks the partner moved at least 60 in: a braked partner moves 63 in, a coasting one 112 in. The README
  tries other voltages and a partner turned sideways, which hardly moves.
- **`examples/blocks/match`**, the speaker example's robot playing a whole 2024 match (`match = true`). In auto it
  shoots the preloaded NOTE into the SPEAKER and drives out of the starting zone, then expects 7 points in auto: 5 for
  the NOTE and 2 for LEAVE. The README walks through the phases and the points.
- **`examples/blocks/chain-climb`**, a 60 kg stand-in robot with a stock climber in front of the built-in `2024`
  field's `chain_blue_center`. `onstage.test.toml` reaches up, drives the chain into the hooks, pulls down and hangs:
  ONSTAGE at 6.9 s, hanging from the chain. `harmony.test.toml` plays the match with a partner hanging on the same
  chain (Setup's partner, **Hangs on**) and scores 5 in the endgame: ONSTAGE's 3 and HARMONY's 2. The README says where
  the partner hangs and why: the STAGE's legs leave room for it only near the low point, and hooks don't slide along
  a chain.

Open one with `scrimmage edit examples/blocks/intake/intake.test.toml`.

## Troubleshooting

- **"... has no motor to move it".** A block uses a joint or motor whose slot has no device ID in the robot. Open the
  Robot tab and give the slot an ID (a `PWM:n` or a CAN ID). **Test it** does this for you on a stand-in robot.
- **"this robot has no turret to aim", "this field has no goals to aim at".** An aim block or aimed check needs a
  stock `turret` part on the robot and a field with goals (a `[range]`).
- **"hub_blue is outside the turret's range (it stops at 30°)".** The goal is past the turret's stop. Turn the robot
  toward the goal, or widen the turret's `range`.
- **"this robot has no cylinder to set", "this robot has no air system (no cylinder)".** The pneumatics blocks need a
  stock `cylinder` part on the robot.
- **"drive works on a tank or swerve drive; spin the drive motors instead".** The robot's drivetrain is neither (a
  mecanum drive, say). Use **spin** blocks on the drive motors.
- **A station run ends "timed out ... its release point stayed blocked".** A ball is sitting at the station's
  release point (the chute's low end is jammed). Look at the replay for what stopped it.
- **"tick where a piece should end up in Setup".** A block counts pieces in the hopper but no body is ticked under
  **Where a piece should end up**.
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
