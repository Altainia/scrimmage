# The Test tab

The Test tab tries a mechanism with blocks, before anyone builds it and with no robot code. You snap blocks together
("spin the crank at 6 volts", "wait until a hook touches the next bar", "expect the robot to tilt less than 30°"),
press **Run**, and watch a replay with a chart. It uses the same physics as the whole-robot simulation, and the motors
are driven through the robot's own motor settings (which motor controller and ID each motor has), so the gearing, the battery and the weight all count. It is the
same page as the [assembler](assembler.md) and the [Part tab](part-tab.md) (`scrimmage edit`), with the same habit:
every change is saved at once, there is no Save button.

Contents: [Starting a test](#starting-a-test), [The three columns](#the-three-columns), [How the editor works](#how-the-editor-works),
[The blocks](#the-blocks), [The checks](#the-checks), [Times and limits](#times-and-limits), [Setup](#setup),
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
| **drive forward** *number* **m** or **s** | Drives the robot forward that far (a minus number drives back), or for that many seconds, then stops. |
| **stop** *motor* | Lets a motor go to neutral. **all motors** stops every motor. |
| **wait** *s* **s** | Waits that many seconds. |
| **wait until** *check* | Waits until the check is true. |
| **repeat** *n* **times** | Does the blocks inside *n* times (1 to 100). |
| **repeat until** *check* | Does the blocks inside again and again until the check is true. |
| **expect** *check* **the whole time** | Checks something at this point in the test. Tick the box to check it all the way from here to the end of the run. |

**A move block always has its unit.** It is **°** for a joint that turns and **in** for one that slides (the unit
switches for you when you pick the joint). **m** is there too. The file always says the unit, so a number is never
guessed.

**Drive works on a tank drive only.** A tank drive (the kitbot's kind of drivetrain, `differential` in the parts list) is a wheel on each side with its own
motor. A swerve or mecanum robot has no **drive** block: spin the drive motors one by one instead. A robot with no
drivetrain at all gets the problem "drive needs a drivetrain".

**A motor in a block needs a device ID.** A **spin**, **stop** or **move** block works through the robot's motor
bindings, so the motor's slot must have an ID in the robot. See [Troubleshooting](#troubleshooting).

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
- **A robot counts as tipped over at 60°.** The run ends with "the robot tipped over" when the robot's body leans more
  than 60° from upright. (The **tilt** check is separate: it only tests what you ask.)

The tab doesn't have boxes for these settings yet. To change one, edit the test file in a text editor: `timeout` on a
block, `max` on a **repeat until**, `volts` on a **drive**, `time_limit` in the `[test]` table. The tab keeps them when
it saves.

## Setup

Open **Setup** over the blocks. Its summary shows the hooks, the weight and the field at a glance.

- **Hooks.** Tick the bodies that grab a bar. A stock climber's hooks always count. A new test ticks the bodies with
  "hook" in their name for you. The hooks are saved in the test file.
- **The field.** Where the robot starts: **The plain field**, **In front of a ladder (round rungs)**,
  **In front of a ladder (square rungs)** or **In front of a ball (to try an intake)**. In front of a ladder, a climber
  has rungs `rung1` to `rung4` to climb. In front of a ball, an intake has a ball to pick up. This changes the robot
  file, so the Robot tab's **Undo** takes it back.
- **The robot (weight and center of mass).** These appear when the robot's base is the stock chassis, which a stand-in
  robot always is. **Weight** is everything that isn't one of the robot's parts (frame, battery, bumpers). The center of
  mass is forward, left and up, in meters. These also change the robot file and **Undo** takes them back.
- **Where the parts are mounted.** For each part that isn't the base or the drivetrain: how far it is moved from where
  it attaches, forward (x), left (y), up (z) in meters, and turned (yaw) in degrees.

A **Try values…** button sits beside the weight, the center of mass and each mount. See [Trying values](#trying-values-sweeps).
If the robot file can't be changed (it has an error, or it is read-only), Setup says why and the boxes are off.

## Running, replay and chart

Press **Run**. The tab shows the progress, and **Stop** ends the run early. Then:

- **The sentence.** What happened, in one line: `Reached rung1, then waiting for a hook to touch rung2 timed out at
  12.1 s (block 2 inside block 3).`, or, when the test works, "Every block played."
- **The checks.** One line each, passed or failed, with the time. A failed check ends the run, and its line says what
  happened instead.
- **The replay.** A 3D view of the run. **Play**, the time slider and a speed box. If the browser can't draw 3D (WebGL is the browser's 3D drawing) it says
  so and the rest still works.
- **The chart.** Tick the signals to draw (tilt, joint positions, hook load, motor current and more). It starts with
  the signals the test names in its `chart` line, or else the tilt and the joints a block drives. Motor currents, hook
  load and the drive wheels' angles are in the picker but not drawn at first (the wheel angles just keep growing, which
  tells you little about a mechanism).
- **Compare.** Pick another run in **compare with** to draw both on the same chart, for example before and after a
  change.

**Old runs keep their own geometry.** A run saves the robot and field as they were when it ran. If you press **Import
again** in the Part tab later, an old run still replays the old shape, and a new run uses the new one. That makes
comparing before and after honest.

## Trying values (sweeps)

A sweep runs the test once for each value of one number, to see which works best.

1. Right-click a block (or open its menu) and choose **Try values…** for the number it has: a **spin**'s volts, a
   **move**'s target, a **wait**'s seconds or a **drive**'s distance or seconds. Or press **Try values…** beside the
   weight, center of mass or a mount in Setup.
2. Type **From**, **To** and **Steps**, or type the values yourself. **At most 12 values** at a time.
3. Run it. The table has one row per value (how it ended, and a tick or a cross for each check), and a chart of how the
   run ended, or any signal, against the value. Click a row to open that run's replay.

A value that can't run gets a row saying why; the others still run. **Stop** keeps the values done so far.

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
```

It plays the blocks with no window, prints how it ended and each check, and keeps the run in `<test>-runs/` (or `--out`).
The exit code is **0** when the test finished and every check passed, **1** when it didn't, and **2** when it couldn't
run (a bad test file, a robot that won't build, a folder it can't write).

An AI agent connected through MCP ([install step 5](getting-started/install.md#5-optional-connect-claude-code)) has two
tools. **`test_run`** runs a test file and returns the sentence, the status, the checks, each signal's range and the
folder the run is kept in (the Test tab replays it). **`test_sweep`** tries one number at up to 12 values. Both take an
absolute path. The agent writes the `.test.toml` with its own file tools.

## The examples

Two worked examples are in `examples/blocks/`. Each has a README with the story, the blocks and how to change them.

- **`examples/blocks/intake`**, a roller and bin that take a ball off the carpet. Run it and it finishes at 3.3 s
  with the ball in the bin. The README has a sweep of the roller's voltage.
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
- **"drive works on a tank drive; spin the drive motors instead".** The robot's drivetrain isn't a tank drive. Use
  **spin** blocks on the drive motors.
- **Run stays off and nothing is wrong on the blocks.** A save the server refused keeps Run off. Look at the status bar
  and **Fix these first**: it says why ("Not saved: ...").
- **"Busy".** A drive session (the Drive tab), a scenario run or a bench run holds the simulation. Wait for it to finish
  or stop it, then press **Run** again. Only one of these runs at a time.
- **The replay shows the old shape after Import again.** Scrimmage reads a part's mesh files by name and time to the
  second, so a part imported twice within the same second can show the earlier mesh. Wait a moment, press **Import
  again** once more, and run again.
- **A block names a joint that is gone.** After **Import again**, the block says the joint isn't on the robot any
  more. Pick the new name in the block.
- **"The test file can't be read".** The file has a mistake. The message names the block ("block 3, inside repeat 2")
  and what is wrong. Fix the file in a text editor, or delete the block in the tab.
- **The 3D replay says WebGL isn't available.** (WebGL is the browser's 3D drawing.) The result and chart still work. Reload, or turn on hardware
  acceleration in the browser.
