# Climbing: bars, hooks and climbers

A range file can hold **bars to climb** (2022's HANGAR rungs, 2013's PYRAMID, 2020's GENERATOR SWITCH). A robot
carries **stock climbers**: telescopes with J hooks that hold a bar by contact, not by a weld, so a hook can miss,
slip, swing and let go. **Climb signals** tell a scenario whether the robot hangs, what it scores, how far it swings
and how hard its hooks work. **`scrimmage climb run`** plays a scripted climb with no robot program and reports a
timeline, the peak hook load, the swing and how the climb ended: the standalone component model of a climb, with the
same physics the whole-robot simulation runs.

> **The contact numbers are estimates.** Hook friction, the rung's give and the latch spring aren't measured (see
> [Estimates](#estimates)). Trust the trends (a nitrile-lined hook damps the swing faster than UHMW) before the
> absolute numbers (a 1.1 kN re-hook).

- [Elements and ranges](#elements-and-ranges)
- [The climber part](#the-climber-part)
- [The climb signals](#the-climb-signals)
- [`scrimmage climb run`](#scrimmage-climb-run)
- [Scripts](#scripts)
- [The examples](#the-examples)
- [The climb scenario](#the-climb-scenario)
- [Estimates](#estimates)
- [Validation anchors](#validation-anchors)
- [Known limits](#known-limits)

## Elements and ranges

Three `[[element]]` kinds climb; [docs/terrain.md](terrain.md#field-elements-in-range-files) lists their keys:

- **`bar`**: a horizontal pipe at a height, optionally on a vertical sprung slide (`stiffness`, `damping`) that
  stands in for the rung's bending, and tagged with the `level` hanging from it scores;
- **`box`**: a static block, structure (a truss) or, with `ground = true`, floor (floor protection);
- **`switch`**: a bar hanging below a hinge that tilts under a hanging robot.

Bars, switches and boxes that aren't ground aren't floor: a robot hanging from a bar isn't "resting on the floor",
and `robot.stranded` doesn't fire for it. A range's `[climb]` table scores the climb:
- `rule = "lowest-rung"` (2022): the lowest rung the robot touches, and nothing while it touches the ground;
- `rule = "hang"` (2020): hanging from the named `switch` scores the first level, plus `level_bonus` while the
  switch is within its `level_deg` of level;
- `rule = "lowest-point"` (2013): one level above the highest level whose bars are all below the robot's lowest
  point. The manual's sequential-climb rule (a robot must climb level by level) isn't checked.

Library ranges:

| range | what | start |
|---|---|---|
| `2022-hangar` | the four 2022 rungs (LOW, MID, HIGH, TRAVERSAL: 4, 6, 10, 15 points), the trusses and the floor protection, from drawing GE-22100 | `under_mid` |
| `2020-switch` | the 2020 GENERATOR SWITCH: ±14.5°, about 42 kg, LEVEL within 8° | |
| `2013-pyramid` | the 2013 PYRAMID's rungs at 30, 60 and 90 in, as bars (its legs aren't built) | |
| `ladder`, `ladder-square` | a generic ladder (the [Test tab](test-tab.md)'s field choice puts a robot in front of it), not a game's field: rungs `rung1` to `rung4` with tops at 24, 36, 48 and 60 in, round or (`ladder-square`) 1.5 in square, nothing scores | `below` |

To climb the 2013 PYRAMID use `2013-pyramid`. The built-in `2013` is the disc range ([docs/disc.md](disc.md)): its
pyramid is legs and a base lip as static obstacles for shooting past, with no bars to climb.

## The climber part

Stock `climber` ([docs/parts.md](parts.md#climber) lists its parameters):
- a **telescope** (a slide chain like the elevator's, motor slot `extend`, encoder `extend_encoder`). Its travel
  stops are stiff (limit solref 3 ms, overdamped): a 60 kg robot hanging at full extension sags 1.3 mm into them;
- **J hooks** at its tip: a stem, a half-circle crown (the bar's seat) and a tip coming down `hook_depth` below the
  throat. The mouth is underneath, so a hook catches by coming **down** over a bar, and lets go by rising off it and
  moving sideways, out past its tip. The hooks open toward the part's +x; turn the part (`rot`) to open them the
  other way;
- an optional spring **latch** across each hook's mouth (`latch = "passive"`), pushed open by the bar on the way in
  and snapping shut behind it; `latch = "released"` adds a motor slot `release` that turns it open;
- an optional **pivot** at its base (`pivot = "articulated"`, slots `pivot` and `pivot_encoder`) that leans the arm.

**Every climber geom collides** with the field's elements: the tubes, the hooks and the latches. Hook friction is a
material preset (`hook_material`), and the hooks win the contact's friction and softness against a rung.

How the hooks behave, measured (tests/test_climb_physics.py):
- **An open J hook lets go when its load reverses.** A deep open hook holds a robot released from up to 110° out:
  while the CoM is below the bar the hook stays pressed onto it. Released from 120°, above the bar, the robot drops
  through its own hook. A shut latch keeps the bar in through a reversal. The real 2022 walk-offs came from shallow
  hooks and impacts; a small `hook_depth` can still walk off.
- **Latch timing:** with the `release` motor in **coast**, the latch's spring shuts it about **34 ms** after the bar
  passes; in **brake**, the shorted motor's back-EMF slows that to **60–70 ms**. Bind the `release` motor with
  `neutral = "coast"`.
- **Open a latch while hanging:** a latch the bar presses on, or one whose swing the bar is in, won't turn. Hang
  from the crown first (the bar clear of the latch), then run the release motor.

## The climb signals

Read every physics step on a range with a bar or a switch (scrimmage/terrain/climb.py); scenario checks see them
like the terrain signals:

| signal | meaning |
|---|---|
| `climb.hanging` | 1 while some robot geom touches a bar or switch and none touches the ground |
| `climb.level` | the scoring level's **rank** in the range's `[climb] levels`: 1 for the first (lowest) level, 0 for none |
| `climb.points` | that level's points (0 while not hanging) |
| `climb.swing` | deg: the robot's CoM from straight under the bar it swings from, positive toward the bar's local x × up (for a 2022 rung, field +x) |
| `climb.hook_load` | N: the largest whole hook's contact force (every geom of `<inst>.hook<i>`, the latch too), peak-held between checks |
| `climb.hook.<inst>.hook<i>.load` | N: one hook's |
| `climb.hook.<part>.<body>.load` | N: one hook body's, for a body of a CAD part made a hook (see below) |
| `climb.extension` | m: how far the robot reaches beyond its frame perimeter, in the chassis's own plane (G107) |
| `climb.height` | m: the robot's top above the carpet (G106) |

**A body of your own part as a hook.** The stock climber's hooks are found by name. For a climber made in CAD, name
the body that catches the bar: `world.climb_monitor(["climber.hook"])` (a body's name is `<part>.<body>`) counts every
colliding geom of that body as a hook, like the stock ones: it is part of the hook assembly (it may touch a bar
without "hitting" it), and it gets its own `climb.hook.climber.hook.load`. An unknown name is an error that lists the
robot's bodies. Asking again with a different list makes a new monitor.

In the [Test tab](test-tab.md) you tick the hook bodies in Setup instead, and the rungs can be round (`ladder`) or
square (`ladder-square`; a bar's `section = "square"`, whose top is its axis plus half the side). The ladder ranges
reach `rung4` at 60 in.

Notes:
- **The swing's bar** is the one a hook touched most recently while the hooks touch two (a hand-over), so the swing
  jumps when a second bar is hooked: it is then measured about the new bar.
- **A margin contact counts as touched:** MuJoCo reports a contact a little before the surfaces meet, so
  `climb.hanging` can read 1 a step before the last wheel lifts.
- **Extension and height count colliding geoms only.** A stock elevator or arm is built `collide = false` unless
  its part says `collide = true` ([docs/parts.md](parts.md#stock-parts-that-collide)). With it off it shows in
  neither, even when it is what breaks G106, and it passes through bars; with it on it counts for both and can catch
  on a bar.

## `scrimmage climb run`

```
scrimmage climb run configs/climber_robot.toml --script examples/climb/high.toml [--out DIR]
```

It builds the config's robot on its range (which needs a `[climb]` table), lets it settle on the carpet for 0.3 s and
plays the script's steps from t = 0. The runner takes the robot program's place: it writes the motor controllers'
outputs, so every drive goes through the config's own motors, gearing, battery and neutral modes (a motor's speed and
torque limit the climb). A position drive is a proportional loop: 400 V/m on a slide, 60 V/rad on a hinge, clipped
to the bus or the drive's `max_voltage`.

Every physics step it checks how the climb is going, and ends the run on the first of:
- **hit a bar**: a robot geom outside the hook assemblies (a tube, the frame; not the hooks or the bracket carrying
  them, `<part>.bracket`, which are meant to touch bars) presses on a bar harder than `hit_force` (default
  200 N), its contacts with that bar summed. A light brush doesn't count;
- **hook walked off**: the robot has hung (0.1 s of `climb.hanging`), let go of every bar while a hook carried it
  (more than 50 N at some step in the 0.2 s before it let go), and then really dropped: its CoM more than 5 cm below
  where it let go, or onto the ground, whatever it lands on (the carpet, a lower rung). A hook that lets go when its
  load reverses, or a telescope extended faster than the robot falls (the hooks rise off the bar), ends here;
- **fell**: the robot has hung and touches the ground, with no carried hook letting go before it: a robot that
  slides or is let down to the carpet. **A deliberate descent to the carpet after hanging is reported "fell"** too;
- **timed out at step N**: step N's wait didn't happen within its timeout.

A moment off the bar with no real drop is neither: hooks resting on a bar while the robot is let down lose it for a
few milliseconds at a time, and a hook bouncing on a re-hook leaves it briefly; the run goes on.

A run that plays every step ends **reached `<LEVEL>`** (the level it scores then, held for 0.1 s) or **not hanging**.
**G107** (extension beyond the frame perimeter, default 16 in) and **G106** (the robot's height, default 66 in)
breaches are notes: when the robot first went over, by how much at most and for how long. G106 is the robot's
height in its own frame (its colliding geoms from lowest to highest along the chassis's up), as the rule measures a
robot "resting normally on a flat floor"; on the carpet it is `climb.height`, hanging from TRAVERSAL it is not.

Outputs, in `--out` (default `<script's folder>/<script stem>-climb`):
- `timeline.csv`: `t, event, level, swing, hook_load`. Event rows (step starts and ends, `hanging`, `level MID`,
  breaches, a hit, the end) and a sample row every 0.05 s with an empty event, its hook load peak-held since the last
  row;
- `summary.md`: the status, when each level was first held (0.1 s) and the largest swing hanging from it alone, the
  peak hook load, the swing, the reach and height, the step table (when each step's wait fired) and the notes.

The exit code is 0 when the climb reached the script's `goal` level or a higher one (with no goal: when it ended at a
level), 1 when not, 2 on bad input (a script or config error, which says what's wrong).

## Scripts

A script is TOML (scrimmage/climb/script.py): an optional `goal`, optional `[limits]` and `[[step]]`s.

```toml
goal = "HIGH"

[limits]               # optional
extension_in = 16.0    # G107
height_in = 66.0       # G106
hit_force = 200.0      # N

[[step]]
name = "reach above MID"
drive = { joint = "mid.stage1", to = 0.41 }
wait = { joint_at = { joint = "mid.stage1", value = 0.41, tol = 0.003 } }
timeout = 5.0
```

Each step sets its drives, then waits for one condition:
- `drive = { joint, to = <position> }` (a position loop, optionally `max_voltage = <V>`), `{ joint, to_deg }` for a
  hinge in degrees, or `{ joint, voltage = <V> }`; a list of them drives several joints. A drive holds until a later
  step drives the same joint. Joints are named as the world names them (`<part>.<joint>`) and need a motor: drive a
  telescope by its first stage (`mid.stage1`, which the tip follows), a pivot (`high.pivot`), a drive wheel
  (`dt.left_2`).
- `release = <output>` (-1 to 1) sets every latch's release motor, `release = { joint = "<inst>.latch0", output }`
  one; it holds until changed.
- `wait`:
  - `{ time = <s> }`;
  - `{ joint_at = { joint, value, tol } }` (or `value_deg`, `tol_deg`; tol defaults to 0.005);
  - `{ swing_phase = "forward_zero" }`: the CoM passing straight under the bar heading forward (the robot's +x);
    also `"backward_zero"`, and `"forward_peak"` / `"backward_peak"`, the swing's forward and backward ends;
  - `{ hook_contact = "<bar>" }`: any hook touching that bar; `{ hook_contact = { bar, hook = "<part>" } }` one
    climber's hooks (or one hook, `<part>.hook<i>`);
  - `{ level = "<level>" }`: holding that level (or a higher one) for 0.1 s;
  - no wait: the step ends at once.
- `timeout` (default 10 s).

There is no state machine: the waits are the simple conditions a driver or an auto-climb watches. The step table in
`summary.md` records when each fired, which is how a timed setpoint script (the fake robot's `--climb` mode) gets its
times.

## The examples

`configs/climber_robot.toml` is the kitbot with two climbers, a 2022 "telescope + rotating telescope" climber obeying
G106 and G107, on `2022-hangar`, starting under MID:
- `mid`: a fixed two-stage telescope just behind the center, hooks opening backward;
- `high`: a two-stage telescope on a pivot near the front, hooks opening forward.

**`examples/climb/high.toml`** climbs to HIGH, the tested example:

| | |
|---|---|
| status | reached HIGH |
| MID held | 4.2 s (carpet, under MID, to hanging) |
| HIGH held | 7.8 s |
| peak hook load | 1.08 kN (the robot's weight moving onto the high hooks) |
| largest swing on HIGH | 32° (it decays about 2° a cycle on steel hooks) |
| reach / height | 13.7 in beyond the frame, 65.6 in tall: within G107 and G106 |

The sequence: drive forward so the mid hooks pass MID, reach above it, back over it (the rung passes just under the hooks' tips and stops against the stems and the hook bracket) and pull up; raise the high arm
beside HIGH, lean it slowly so the hooks' tips lead over the rung, retract to hook it; extend the mid climber, which
pushes the robot down onto the high hooks and lifts its own hooks off MID; the robot swings under HIGH. Two things
the climb taught (and a script shows): a fast lean swings the robot and runs the arm into the rung, and after the
hand-over the mid climber's hooks catch MID on the back swing unless the high arm's pivot tips them forward, away.
Two more came from timing the climb for a robot program (below): drive the wheels gently (1.5 V; at 3 V they slip as
the loop stops them, and the hooks can land beside MID), and keep extending the mid climber 0.2 s after HIGH is held
(retracting the moment it is, the hooks can catch MID again).

**`examples/climb/traversal.toml`** is the stretch: HIGH, then a hand-over to TRAVERSAL (not gated by a test). It
takes high.toml's 1.5 V wheel drive and "clear of MID" step. As built, **it still does not reach TRAVERSAL: it ends
"hook walked off" at step 25** (t = about 21.5 s): the robot reaches HIGH (about 7.9 s), the mid climber hooks HIGH
beside the high hooks (2.42 kN, the run's peak), the high hooks let go of HIGH, swing forward under it and, extended
at the forward end of a swing, touch TRAVERSAL (step 23, about 20.4 s); but they don't seat and take the robot, so when
the mid hooks lift off HIGH the robot drops. Timing a catch on a swing of about 30–34° (34° at its largest) (the robot hangs from HIGH, so TRAVERSAL
moves against the arm as it swings) is the hard part, as it was for teams; damping the swing first, or a robot built
for the traversal (a taller rotating arm, a reaction arm against the truss), are the next things to try.

**`examples/blocks/ladder_climber/`** is a CAD climber tested with blocks, no robot program: a crank turning two
arms that rise and fall in turn (like a steam train's wheel rods), with spring hooks that fold past a rung and catch
it, on the stand-in box robot in front of the `ladder` range. Its test (`climb.test.toml`, gated by
tests/test_blocks_climber.py) **reaches rung1 and stops there for a physics reason**: the hooks are at the robot's
front edge and its weight is in the middle, so the robot hangs from rung1 but tips up on its back bumper (17° at the
top of a stroke) instead of rising, and that tilt swings the rising hook back from the ladder and below rung2. Its
README has the numbers, what was tuned, and what to try next.

## The climb scenario

```
scrimmage run scenarios/climb_high.toml
```

The whole robot climbs with a robot program: `configs/climber_robot.toml`'s `[robot_program]` is the test suite's
fake robot (tests/fake_robot_main.py) in its `--climb` mode, playing `examples/climb/high_timed.toml`. A robot
program sees only HAL, not contacts, so it can't wait for "the hooks touch HIGH"; it does what a team's endgame code
does: at fixed times it gives each mechanism a new setpoint, and holds each with a **position loop on the
mechanism's own encoder** (the config binds a quadrature encoder to each climber joint: DIO 4, 6 and 8, and the
drive's on 0 and 2). The loops are the runner's (400 V/m, 60 V/rad, in volts over the battery's voltage, clipped
to it or the setpoint's `max_voltage`), once a robot loop (5 ms). The scenario holds the robot disabled for 0.3 s,
then enables **teleop** for 11 s (the climb is an endgame action); the timed file's t = 0 is the first robot loop
enabled in teleop. The config sets `[telemetry] nt = false`: the fake robot serves no NetworkTables, so the run
doesn't wait 5 s for it. Checks:
- `by_time climb.points >= 10 by t = 9` (HIGH is first touched 7.26 s into teleop);
- `at_end climb.points >= 10`: still hanging from HIGH, not a moment's touch;
- `always climb.hook_load max 4000` (it peaks at 1.08 kN).

**Where the timed file comes from.** tests/climb_timed.py runs `examples/climb/high.toml` with `climb run` and writes
each step's drives as `[[setpoint]]`s at the time the step started (when the previous step's wait fired), plus a
`[[joint]]` per driven joint: its PWM motor, its encoder's channel and counts per m or rad, and the gain. It is a few
dozen lines. Regenerate it after changing the climb, the robot or the physics (its header has the command):

```
.venv/bin/python tests/climb_timed.py configs/climber_robot.toml examples/climb/high.toml examples/climb/high_timed.toml
```

**How robust it is.** tests/test_climb_scenario.py moves every setpoint 20 ms later, and 20 ms earlier, and the
climb still reaches HIGH and passes every check; while tuning, ±50 and ±100 ms did too. Removing the high pivot's
setpoints fails on points and nothing else. Two runs give identical reports and run.csv (tests/test_climb_scenario.py compares them).

What came before: replaying the runner's motor outputs open-loop (no encoders) was exact only to the last digit.
Rounding those outputs to 4 decimals turned a HIGH climb into a walk-off, because the high pivot, held under load,
drifts without its loop. With the loops closed, the two places the climb was still sensitive were the wheels and the
hand-over (see [The examples](#the-examples)); high.toml now has margin at both.

## Estimates

| number | value | where | how to measure it |
|---|---|---|---|
| Hook friction on a powder-coated rung | steel 0.5, polycarbonate 0.35, UHMW 0.15, nitrile 1.0 | `HOOK_FRICTION` (scrimmage/parts/stock/climber.py) | Hang the robot (or a weight) from a rung, swing it 25°, count cycles until it stops: the amplitude drops about 4 μ r / d radians a cycle (r the pipe's radius, d pivot to CoM). Or tilt a loaded hook on a rung until it slides. |
| Rung give | 160 kN/m, damping 250 N s/m | `stiffness`, `damping` in ranges/2022-hangar.toml | Hang a known weight at mid-span and measure the deflection with a dial indicator (truss, U-bolts and pipe together); the research's bending estimate is 80–320 kN/m. |
| Hook contact softness | solref 0.01 s (a 60 kg hang sinks 0.6–0.7 mm) | `HOOK_SOLREF` | Not a physical property: chosen so a hang sinks under 2 mm. |
| Latch spring | 0.3 N m/rad, 15° preload | `latch_spring`, `LATCH_PRELOAD_DEG` | Measure the torsion spring's torque at two angles. |
| Latch inertia and stop | armature 2e-4 kg m², stop solref 4 ms | `LATCH_ARMATURE`, `LATCH_STOP_SOLREF` | The armature stands in for the release gearbox; time the latch shutting on video (the model: 34 ms coast). |
| Telescope stops | solref 3 ms, damping ratio 2 | `TELESCOPE_STOP_SOLREF` | Not a physical property: stiff enough that 60 kg sags 1.3 mm; overdamped because a critically damped 4 ms stop kept a 12 mm bounce going against the rung's spring (measured). |
| Hit force | 200 N | `HIT_FORCE` (scrimmage/climb/script.py) | A judgment: about a third of a 60 kg robot's weight. Raise it in `[limits]` for a design that rests a tube on a rung. |
| Position loop gains | 400 V/m, 60 V/rad | `KP_SLIDE`, `KP_HINGE` (scrimmage/climb/run.py) | A team's own kP: replace by driving voltages from a script. |
| Climber masses and gearing | 0.6 kg a stage, 0.8 kg tip, 20:1 on a 15 mm spool (the example: 12:1, two motors) | stock parameters | Weigh the parts; read the gearbox. |

## Validation anchors

From the research notes (Chief Delphi threads, team binders and the 2022 manual; numbers quoted there):

| anchor | value | the model |
|---|---|---|
| Mid-tier full climb (elevator + static hooks) | 11 s (1918), 12–16 s; "to HIGH in 8 s" (one team) | the example holds HIGH at 7.8 s from under MID |
| Elite carpet → TRAVERSAL | 4.8 s (254), 5.1 s (1678) | the example's traversal doesn't reach it |
| Swing period | 1.6–2.1 s (predicted) | the compound pendulum within 5 % (tests/test_climb_physics.py) |
| Swing decay | 2.5–6° a cycle (Coulomb, μ 0.42–1.0) | within 1.5× of 4 μ r / d (tests); the example loses about 2° a cycle on steel |
| Re-hook impact | 2.7–3.8 kN for a 2–5 cm drop; 254's "500+ lbs" | 3 cm onto a compliant rung under 4 kN, a rigid one higher (tests); 1.1 kN in the example |
| Telescope speed | 254: 11 in/s; 4089: about 2 ft/s loaded | the example's telescopes (12:1, two Krakens, 2 stages) lift the robot about 0.7 m in 1.1 s |

## Known limits

- **Other mechanisms collide only when they say so.** A stock elevator, arm or launcher is `collide = false` by
  default: it passes through bars and doesn't count for `climb.extension` or `climb.height`. Set `collide = true` on
  the part ([docs/parts.md](parts.md#stock-parts-that-collide)) to count it. The climber part always collides.
- **Chain and rope aren't modeled** (2024's chain, 2017's rope): they need cable simulation.
- **2013's sequential rule isn't checked**: `"lowest-point"` scores where the robot is, not how it got there.
- **No state machine**: a script waits on simple conditions; an auto-climb that reacts to the swing has to be
  written as a robot program and run in a scenario.
- **The rungs don't spin** (2022's are bolted), and only their vertical bending is modeled (one sprung slide).
- The kitbot's drive is driven by its wheel joints in a script; it isn't a path follower.
