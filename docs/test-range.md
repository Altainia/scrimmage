# Test range: shooting at last season's field elements

The test range puts your robot on a field with **goals** (last season's hub), **balls that are real bodies**, and a
**launcher, hopper, feeder and intake** that your own robot code drives. You write the robot code once; the sim
decides where each ball goes and whether it scores. It builds on the [launcher model](launcher.md): the wheel, the
hood and the ball's squeeze are that model, and the range adds a world around it.

> **The numbers are estimates until you measure your own balls and wheels** ([measuring.md](measuring.md)). The
> launcher model's results are labeled `UNCALIBRATED` for that reason, and so are the aiming tables made from it.
> Trust the trends (a bigger flywheel recovers faster) before the absolute numbers (7.4 m/s).

- [What you get](#what-you-get)
- [Putting a robot on the range](#putting-a-robot-on-the-range)
- [A complete example](#a-complete-example)
- [`[range]`](#range)
- [`[shooter]`](#shooter)
- [The launcher part](#the-launcher-part)
- [A disc shooter](#a-disc-shooter)
- [A piece shooter (a NOTE)](#a-piece-shooter-a-note)
- [Lanes, stages, the hopper and the intake](#lanes-stages-the-hopper-and-the-intake)
- [Beam breaks](#beam-breaks)
- [Balls in the world](#balls-in-the-world)
- [Pieces that aren't balls](#pieces-that-arent-balls)
- [The trench field](#the-trench-field)
- [The 2024 field](#the-2024-field)
- [The range file](#the-range-file)
- [Scenarios: signals, `by_time`, `[range]`, `robot_env`](#scenarios-signals-by_time-range-robot_env)
- [The report, the viewer and NetworkTables](#the-report-the-viewer-and-networktables)
- [Aiming tables: `scrimmage launcher aim`](#aiming-tables-scrimmage-launcher-aim)
- [The fast-mode sample's autos](#the-fast-mode-samples-autos)
- [What isn't modeled](#what-isnt-modeled)

## What you get

- Goals with a real opening, tilt and funnel. A ball **scores** when its path crosses the scoring surface going
  in. The 2026 range ships with both hubs, the four bumps and the 360 FUEL in the neutral zone, all measured from the
  field CAD. Robots drive over the bumps; see [docs/terrain.md](terrain.md) for elements and the terrain signals.
  The 2024 range ships with both SPEAKERS and AMPS, scored by a NOTE from a pinch shooter
  ([The 2024 field](#the-2024-field)).
- Balls as MuJoCo bodies: they roll, bounce off the carpet, the hub and your robot, get pushed by your bumper and
  fly with drag and spin lift.
- A robot with a hooded shooter, a hopper, one or more feeder stages and an intake. Your robot program drives their
  motors (PWM, CAN or fast-mode IO, exactly like any other motor) and reads their sensors, including beam breaks as
  DIO inputs.
- Scenario checks on `score`, `fired`, `held` and `on_field`, a per-ball table in the report, the balls in the
  viewer and on NetworkTables.
- An aiming-table generator, so the robot code can look the wheel speed and hood angle up by distance.

It needs a robot built from `[[part]]` entries ([docs/parts.md](parts.md)): a `[range]` on an MJCF robot is an
error. Robots with no `[range]` behave exactly as before.

## Putting a robot on the range

1. Add a **launcher** part (stock `launcher`) and, for the ball path, **roller** parts for the hopper floor,
   feeder and intake.
2. Add `[range]` (which range file, which start, how many balls preloaded).
3. Add `[shooter]` naming the launcher part, its lanes, the stages and the intake.
4. Set `[model] timestep = 0.001` or less (see [the contact model](#the-contact-model)).
5. Run a scenario, or `scrimmage serve` and open the viewer.

## A complete example

A differential-drive robot with a two-lane launcher, a hopper floor, a feeder, an intake and two beam breaks, on the
2026 range, driven by CAN TalonFXs. It is built by a test (`tests/test_range_docs.py`), so it stays in step with the
code.

<!-- complete -->
```toml
[range]
file = "2026"             # a built-in range's name, or a path relative to this file
start = "blue_center"     # one of the range's [start.*]; leave out to use [robot] start_pose
preload = 4               # balls already in the hopper, at most the shooter's hopper capacity
floor_balls = true        # the range's floor balls (the 360 in the neutral zone); false: none

[model]
timestep = 0.001          # at most 1 ms with a range

[battery]
nominal_voltage = 12.5
resistance = 0.02

[[part]]
name = "drive"
stock = "chassis"
length = 0.66
width = 0.6
mass = 47.0
frame_height = 0.1

[[part]]
name = "dt"
stock = "differential"
attach = "drive.center"
ids = { left = "TalonFX:1", right = "TalonFX:2" }

[[part]]
name = "launcher"
stock = "launcher"
attach = "drive.top"
pos = [-0.1, 0.0, 0.35]   # the shaft's position: its height is the launcher's height in the model
width = 0.36              # inside the side plates: both lanes' balls fit (see lanes below)
hood = "articulated"
hood_range = [40.0, 80.0] # exit angles at the hood joint's low and high stops
ids = { shooter = "TalonFX:3", hood = "TalonFX:4" }

[[part]]
name = "hopper_floor"
stock = "roller"
attach = "drive.top"
pos = [0.1, 0.0, 0.1]
gearing = 10.0
ids = { spin = "TalonFX:5" }

[[part]]
name = "feeder"
stock = "roller"
attach = "drive.top"
pos = [-0.05, 0.0, 0.2]
gearing = 10.0
ids = { spin = "TalonFX:6" }

[[part]]
name = "intake"
stock = "roller"
attach = "drive.center"
pos = [0.36, 0.0, 0.2]
gearing = 10.0
ids = { spin = "TalonFX:7" }

[shooter]
part = "launcher"
lanes = [-0.08, 0.08]     # ball positions across the wheels (m); 0.16 apart, each fits the 0.36 m width
[shooter.wheels]
preset = "35A"
positions = [-0.08, 0.08] # a wheel under each lane
width = 0.05
[shooter.hood]
friction = 0.3
[shooter.air]
preset = "default"
[shooter.hopper]
capacity = 8
[[shooter.stage]]
name = "floor"
part = "hopper_floor"
path_length = 0.40
[[shooter.stage]]
name = "feeder"
part = "feeder"
path_length = 0.25
[shooter.intake]
part = "intake"
at = "drive.center"
pos = [0.45, 0.0, 0.08]   # the zone's center in that mount's frame
size = [0.15, 0.55, 0.20] # depth, width, height
rate = 8.0                # balls per second at most
[[shooter.beam_break]]
at = "stage:feeder"
fraction = 0.5
dio = 0
[[shooter.beam_break]]
at = "launcher"
dio = 1
```

[`configs/fastmode_range.toml`](../configs/fastmode_range.toml) is a larger worked example: a swerve robot with the
fast-mode sample's shooter.

## `[range]`

| key | unit | default | meaning |
|---|---|---|---|
| `file` | | required | a built-in range (`"2026"`, `"2013"`, `"2026-bump"`, `"2023-charge-station"`, `"terrain-library"`, `"2022-hangar"`, `"2020-switch"`, `"2013-pyramid"`, `"ladder"`, `"ladder-square"`, `"intake"` (one ball on the carpet in front of `[start.facing_ball]`, to try an intake on), `"station"` (a human player station at the blue alliance wall that feeds balls down a short chute, `[start.at_station]` against it, to try a hopper or an intake on), `"2026-trench"` (one 2026 TRENCH, its arm 22.25 in above the carpet, `[start.approach]` before it, to drive under), `"2016-defenses"` (the 2016 portcullis, sally port, drawbridge and cheval de frise, each with a start before it; [docs/terrain.md](terrain.md)), `"2024"` (the 2024 CRESCENDO field: both SPEAKERS and AMPS, which a NOTE scores in, the SUBWOOFERS, the STAGES, the SOURCES and the 11 staged NOTES, on its own 16.541 by 8.211 m field; [The 2024 field](#the-2024-field)) or `"reef"` (one pipe of the 2025 reef with its L2, L3 and L4 branches, a coral on the carpet in front of `[start.facing_coral]` and a coral station with `[start.at_coral_station]` against it, to try a grabber on)) or a path to a [range file](#the-range-file), relative to the config. For 2013, `"2013"` is the disc range (shooting discs at the slot goals, the pyramid an obstacle) and `"2013-pyramid"` the climbing range (the pyramid to climb, no goals) |
| `start` | | none | a named start from the range file. The robot starts there. Give either `start` or `[robot] start_pose`, not both |
| `preload` | balls | 0 | balls already in the hopper at the start of a run. More than the robot holds (the hopper capacity) is an error |
| `floor_balls` | | `true` | `false`: no floor balls (the range's `[[floor_balls]]` are left out of the world entirely, so the world can't put them out later). Faster runs, and the right choice when the auto never touches them |
| `ball` | | the range's | a ball preset to use in place of the range's own (`"2017-fuel"`, `"2019-cargo"`, and so on: the names in [docs/launcher.md](launcher.md)). Or a ball measurement file with a `base` preset (`"fuel.measure.toml"`, relative to the config): the base preset's ball with the measured numbers in place ([measuring.md](measuring.md#using-a-measurement)). Only a range with a ball takes it (not a disc range). A range with a station refuses a ball that doesn't fit the station's chute (a chute with a set `width` must be wider than the ball). The Test tab's Setup sets this for a stand-in's `intake` and `station` fields |
| `piece` | | the range's | a ball preset or a piece preset to use in place of the range's own kind (`"2025-coral"`, `"2023-cube"`, `"2017-fuel"`, and so on: see [Pieces that aren't balls](#pieces-that-arent-balls)). Give `piece` or `ball`, not both: `ball` is the older spelling and takes ball presets only. The Test tab's Setup writes `piece`. It replaces the field's one kind, and only the built-in `intake` and `station` fields take it (a game's field and the `reef` field keep their own pieces) |
| `station` | | none | changes a station's settings for this run: `station = { feeder = { interval = 0.5, stock = 8, spread_yaw_deg = 5.0 } }`. The keys are `interval`, `stock`, `spread_yaw_deg`, `spread_pitch_deg`, `spread_speed`, `spread_position`, `spread_rotation_deg`, `tip_deg`, `turn_deg`, `spread_tip_deg` and `spread_turn_deg`; any other key, or a station the range doesn't have, is an error. `stock = 0` means "never runs out" (at most 50 balls are fed in one run); a stock can be at most 200 |
| `seed` | | `1` | the whole number (0 or more) that picks which draw of the spreads the stations feed. The same seed gives the same feeds every run |

A `[range]` replaces the field's center obstacle with the range's own elements, so `[field] obstacle = true` with a
range is an error. `[range] file` problems list what is valid (the built-in names, a range's starts).

## `[shooter]`

`[shooter]` says how a stock `launcher` part works as a shooter on the range. It gives the **launcher model's
inputs** that the part doesn't already fix.

| key | meaning |
|---|---|
| `part` | the stock launcher part's name (required) |
| `setup` | optional path (relative to the config) to a [launcher setup file](launcher.md#inputs) to take `[wheels]`, `[hood]` and `[air]` from. Inline sections below win over it |
| `lanes` | ball positions across the wheels, meters from the launcher's center; default `[0.0]` (one lane) |
| `[wheels]` | the tread: `preset` (or `durometer` and `friction`), `positions`, `width` (see [`[wheels]`](launcher.md#wheels)). The wheel diameter comes from the part |
| `[hood]` | the hood's friction and flares (see [`[hood]`](launcher.md#hood)). The gap and the entry angle come from the part, and so does the exit angle |
| `[air]` | the air for the balls in flight (see [`[air]`](launcher.md#air)); defaults to the `default` preset |
| `[hopper]` | `capacity`: balls the **whole robot** holds (hopper, stages and launcher together). Default 0 |
| `[[stage]]` | feeder stages, at least one: `name`, `part` (a stock roller or flywheel), `path_length` (m) |
| `[intake]` | the intake zone: `part`, `at`, `pos`, `size`, `rate`, `min_surface_speed` |
| `[[beam_break]]` | optional beam breaks: `at`, `fraction`, `lane`, `dio`, `active_low` |

What `[shooter]` does **not** take, because the range or the part already fixes it: `ball` (the range's ball),
`shaft` and `launcher` (the part's `wheel_moi`, `added_moi`, `motor`, `count`, `gearing`, `width` and its mount),
`[wheels] diameter`, `[hood] gap`, `entry_deg` and `exit_angle_deg`. Giving one is an error that says which part
parameter to change. A setup file's values for these must agree with the part's, with two exceptions: its
`[launcher] height` is ignored (the launcher's height is where the part is mounted, so you can reuse a standalone
setup file without editing it), and an articulated hood's `exit_angle_deg` only has to lie within `hood_range`.
A setup file's `[control]`, `[goal]`, `[robot]`, `[feed]`, `[spread]`, `[sweep]` and `[[chart]]` sections are
ignored: your robot code controls the shaft, the range has the goals, and the hopper feeds the balls.

### The shooter shaft in the world

Your robot program's motor commands drive the shooter motors like any other motor, and the launcher model runs the
shaft. The motors take part in the world's battery: **the bus voltage is solved within each step** from all the
motors' currents (each at its start-of-step speed), so a spinning-up shooter sags the same battery your drive and
your robot code's voltage readings see, and a fast shot visibly dips it. Regenerative braking current goes back to
the battery, as for every other motor, but the battery isn't charged beyond zero net draw.

At neutral the shooter **follows its motor's neutral mode**, like every other motor: brake shorts the winding (the
shaft slows on its own back-EMF through the motor's resistance); coast (and a shooter with no binding) is an open
circuit and spins down only as the shaft's losses slow it. Set the neutral mode in your robot code (or the binding's
`brake`) the way you would for the real motors.

The spin joint, the fast-mode encoder and the rotor sensors report the launcher model's shaft, so what your robot
code reads is the wheel the balls are squeezed against.

## The launcher part

The stock `launcher` part ([docs/parts.md](parts.md), `scrimmage parts launcher -v`) is a wheel on a horizontal
shaft with a hood around it. Its parameters:

| parameter | unit | default | meaning |
|---|---|---|---|
| `wheel_diameter` | m | 0.1016 | shooter wheel diameter |
| `width` | m | 0.3 | inside width between the side plates. Every lane's ball must fit: `abs(lane) + ball diameter / 2 <= width / 2` |
| `wheel_moi`, `added_moi` | kg m^2 | 0.002, 0 | the wheels' inertia, and a flywheel on the same shaft |
| `motor`, `count`, `gearing` | | | the shooter motor model, how many, and motor turns per wheel turn |
| `hood_gap` | m | 0.12 | gap between the wheel and the hood: the ball is squeezed by its diameter less this |
| `hood_entry_deg` | deg | 240 | where the ball enters, round the shaft from forward toward up |
| `hood` | | `articulated` | `articulated`: the hood turns on a motor; `fixed`: it stays at `hood_angle` |
| `hood_angle` | deg | 50 | the fixed hood's exit angle |
| `hood_range` | deg | [20, 70] | the articulated hood's exit angles at its low and high hard stops |
| `hood_motor`, `hood_count`, `hood_gearing`, `hood_mass` | | | the hood's motor, count, gearing and mass |

The part's slots: `shooter` (the shooter motor, joint `spin`), and for an articulated hood `hood` (the hood motor)
and `hood_encoder` (hood turns). The base's origin is on the shaft: x forward, z up, the shaft along y.

**The hood joint's zero.** The joint reads 0 at the **low** stop, so the exit angle is
`hood_range[0] + the joint's angle`. A robot whose hood encoder reads zero at its lowest position matches. Each step
the launcher model takes its exit angle from the joint (clamped to the stops), so a hood that moves while a ball is
in the launcher changes that ball's exit. A **fixed** hood has no hood joint, motor or encoder; its angle is
`hood_angle`.

**An unbalanced hood drifts while disabled.** Its weight backdrives the motor; in brake mode the shorted winding
only slows it, so a hood left between its stops creeps until it rests on one. The sample robot's hood
(configs/fastmode_range.toml) creeps toward its high stop at about 2.3 degrees a second, its motor reading about
-1.2 A. Hold it with your own code if it matters, as on a real robot.

**The launcher's height comes from the mount.** The height used for the ball's exit point and for the flight is the
shaft's height above the carpet in the start pose (the part's position on the robot), never a `height` key.

The hood needs room to wrap the ball: `hood_entry_deg` must be more than the exit angle + 90 degrees. The check names
the numbers.

## A disc shooter

On a disc range (the built-in `2013`), `[shooter] kind = "disc"` runs a stock `disc_shooter` part with the planar disc
launcher model of [docs/disc.md](disc.md). A ball shooter on a disc range, or a disc shooter on a ball range, is an
error naming the range, its piece and the `kind`.

```toml
[[part]]
name = "loader"
stock = "roller"
attach = "drive.top"
pos = [-0.35, 0.0, 0.5]
gearing = 10.0
ids = { spin = "PWM:3" }

[[part]]
name = "shooter"
stock = "disc_shooter"        # defaults: four 4 in. rollers on the right, a fence on the left, 1/2 in. squeeze,
attach = "drive.top"          # a 0.5 m channel tilted up 25 degrees (examples/disc/linear.toml's channel)
pos = [-0.25, 0.0, 0.25]
ids = { shooter0 = "PWM:2" }

[shooter]
kind = "disc"
part = "shooter"
wheels = { preset = "urethane-60a-4in" }   # the tread: durometer and friction (diameter and width are the part's)
layout = { fence = "wedgetop" }            # the fence's friction, and optionally rail_friction, wheel_stiffness
[[shooter.stage]]                          # at least one stage pushes the discs in
name = "loader"
part = "loader"
path_length = 0.30
```

`[shooter]` for a disc shooter takes `part`, `setup` (a [disc setup file](disc.md), whose values for what the part
or the range gives must agree with them), `[wheels]`, `[layout]` (`fence` or `fence_friction`, `rail_friction`,
`wheel_stiffness`), `[air]` (`density`), and the same `[hopper]`, `[[stage]]`, `[intake]` and `[[beam_break]]` as a
ball shooter. The part gives the channel: `layout` (`linear`, `arc` or `pinch`), the wheels (`wheel_count` per side
from `wheel_start` every `wheel_pitch`, on `wheel_side`), `groups` (`single`, `per_side` or `per_wheel`: one shaft and
motor slot `shooter<g>` each, with each wheel's `wheel_moi`, a flywheel `added_moi`, `motor`, `count`, `gearing`),
`wheel_diameter`, `wheel_width`, `compression`, `channel_length`, `arc_deg`, and `channel_width` (the disc's
diameter: it must be the range's disc's). The range gives the disc and its aerodynamics; there are no `lanes`.

- **Tilt.** The `channel` body pivots on the base at the disc's entry. `tilt = "fixed"` (the default) holds it at
  `tilt_angle`; `tilt = "articulated"` puts it on a motor (slot `tilt`, encoder `tilt_encoder`), reading 0 at the
  low stop of `tilt_range`, as the launcher's hood does.
- **The hopper.** A robot holds 4 discs unless `[hopper] capacity` says otherwise (G24, 2013 manual §3.2.5.4). The
  discs are single file, lying flat in a row on the stages, spaced by their diameter; the stage pushes the next one
  into the channel when the channel is clear (it holds one disc at a time), at the stage's surface speed.
- **In the world.** Each wheel group's shaft is run by the model on the shared battery, as the ball shooter's shaft,
  and its `spin<g>` joint mirrors it. The model is stepped every physics step with the world's timestep (it takes
  its own 0.1 ms substeps while a disc is in the channel). A launched disc leaves from its center's exit point in the
  channel's frame, lying in the channel's plane, moving at the exit speed and yaw, spinning about its axis
  (counter-clockwise from above for wheels on the right), plus the robot's motion; it ignores its robot for 0.15 s
  as a ball does. Its log's `hood_deg` is the tilt and `spin_rpm` is signed. The same voltages give the standalone
  model's exit speed and spin (`tests/test_range_disc_shooter.py`).
- `scrimmage launcher aim` makes tables for ball launchers and piece shooters; for a disc shooter use
  `scrimmage disc table`.

## A piece shooter (a NOTE)

On a range whose piece is a flat rigid piece, such as the 2024 NOTE, `[shooter] kind = "piece"` runs a stock
`pinch_shooter` part with the pinch model of [docs/launcher.md](launcher.md#a-face-pinch-shooter-for-a-flat-piece):
a top and a bottom wheel squeeze the piece's two faces and drive it out. `piece` names the range's kind it shoots.

```toml
# defaults: 4 in. top and bottom wheels, 14 in. wide, a 1.25 in. gap, a fixed 45 degree channel
[[part]]
name = "shooter"
stock = "pinch_shooter"
attach = "drive.top"
pos = [-0.1, 0.0, 0.3]
ids = { bottom = "PWM:2", top = "PWM:3" }

# the loader: a roller behind the shooter that pushes the held NOTE into the wheels
[[part]]
name = "loader"
stock = "roller"
attach = "drive.top"
pos = [-0.35, 0.0, 0.2]
gearing = 10.0
ids = { spin = "PWM:5" }

[shooter]
kind = "piece"
part = "shooter"
piece = "note"                # the range's [piece.note]
wheels = { preset = "60A" }   # the tread: a [wheels.*] preset, or durometer and friction
[[shooter.stage]]             # at least one stage pushes the piece in
name = "loader"
part = "loader"
path_length = 0.30
```

`[shooter]` for a piece shooter takes `part`, `piece`, `[wheels]` (`preset`, `durometer`, `friction`), `[air]` (the
launcher's, for the flight) and the same `[hopper]`, `[[stage]]`, `[intake]` and `[[beam_break]]` as a ball shooter.
There are no `lanes` and no `setup` file. The part gives the wheels (`bottom_diameter`, `top_diameter`,
`wheel_width`, the `gap` between their surfaces, `wheel_x`: how far downrange of the fed piece's middle their axles
stand), each wheel's shaft (slots `bottom` and `top`, each with `wheel_moi`, a flywheel `added_moi`, `motor`, `count`
and `gearing`) and the channel's `tilt` (as the disc shooter's: `tilt_angle`, or `tilt_range` on a motor). The range
gives the piece: its thinnest side is the thickness the wheels squeeze, its next side its length along the path, and
its own `stiffness` (N/m, at one contact; the 2024 NOTE preset's 2000 is an estimate) is in series with the tread.

These are refused, each in words: `kind = "piece"` in a part that isn't a `pinch_shooter`; a `piece` the range
doesn't have, or one that is a ball or a disc (they have their own shooters); a piece the robot can't hold (on a
range with a ball, the robot holds the ball); a `gap` no narrower than the piece's thickness (the wheels can't grip
it); and a `wheel_x` that puts the piece between the wheels before it is fed.

- **The hopper.** A robot holds 1 piece unless `[hopper] capacity` says otherwise (an estimate: the pinch holds one,
  and the 2024 game lets a robot control one NOTE at a time). The piece takes the disc's one-lane path, spaced by its
  bounding sphere's diameter, and is pushed into the pinch when it is clear, at the stage's surface speed.
- **In the world.** Each wheel's shaft is run by the model on the shared battery, as the disc shooter's groups, and
  its `spin0` (bottom) or `spin1` (top) joint mirrors it. A launched piece leaves from its center's exit point along
  the channel's x, at the model's exit speed, lying face-parallel to the channel (its faces across the gap, as it
  lay between the wheels), with its pitch spin as backspin about the channel's -y (none for equal wheels at equal
  speeds), plus the robot's motion; it ignores its robot for 0.15 s and flies with its drag
  (*A rigid piece in the air*, below). Its log's `hood_deg` is the tilt, `spin_rpm` its backspin
  and `exit_slip` the wheels' slip under it as it left (`tests/test_range_piece_shooter.py`).
- **Aiming.** `scrimmage launcher aim` aims a piece shooter at a goal that isn't level (a SPEAKER, an AMP), with the
  tilt in place of the hood angle and both wheels at the row's wheel speed: its exit is the pinch model's, its flight
  the rigid piece's, and a row scores only if all of the piece fits through the opening without touching anything
  first, and at a goal entered going up it must peak at least 0.10 m above the opening's center
  ([Aiming tables](#aiming-tables-scrimmage-launcher-aim)). A level goal is refused (its table is a ball
  launcher's). It is slower than a ball's table: about 15 s for 3 SPEAKER distances at 7 tilts, 35 s for 5 at 16.
- **A worked example** is [`examples/blocks/speaker`](../examples/blocks/speaker/README.md), on the built-in
  [2024 field](#the-2024-field): a NOTE from the SUBWOOFER into the blue SPEAKER, the SPEAKER table, its rows flown
  again in the world, and why the example takes a higher arc than the table's first row.

## Lanes, stages, the hopper and the intake

The ball path inside the robot is bookkeeping, not bodies: a ball inside the robot is parked out of the way and the
sim tracks how far along it has got.

```
intake zone --> hopper (a queue per lane) --> stage 1 --> stage 2 --> ... --> launcher model --> out
```

- **Lanes.** The lanes are single files across the wheels. A ball picked up or preloaded goes to the lane holding
  the fewest balls (the lowest index on a tie) and **keeps that lane** to the launcher. Lanes must be at least a
  ball diameter apart, and each ball must fit between the side plates.

  With the default launcher width of 0.3 m and the 0.150 m FUEL, a lane can be at most 0.075 m from the center, and
  two lanes a ball diameter apart sit at exactly +-0.075: no room to spare. For two lanes, widen the part (the
  examples use `width = 0.36` with lanes at +-0.08, which reach 0.155 m from the center).
- **Capacity.** `[shooter.hopper] capacity` counts every ball the robot holds, wherever it is on the path. A
  preload above it is a config error. The intake collects nothing when the robot is full.
- **Stages.** Each `[[shooter.stage]]` is a stock `roller` (or `flywheel`) part. Its surface speed is its spin
  joint's speed times its radius: positive carries balls toward the launcher. Every ball on a stage moves at that
  speed but never closer than one ball diameter to the ball ahead of it, and stops behind it. `path_length` is how
  far a ball travels along the stage. Stages are listed from the hopper's side. A stage slower than 1 mm/s is
  stopped.
- **Forward.** A ball at the end of stage k enters stage k+1 when that entry is clear, else waits at the end. The
  hopper releases a lane's front ball onto the first stage while that stage runs forward. A ball at the end of the
  last stage is handed to the launcher model when no earlier ball in its lane is still within one diameter of the
  entry.
- **Reverse.** A ball pushed back below the start of a stage goes to the end of the stage before (or to the front of
  its hopper lane); a ball already in the launcher model never comes back.
- **No feed load.** The load of pushing balls along is not modeled: the rollers turn as if the balls weren't there.

**The hand-off (`FedBall`).** What reaches the launcher model is a `FedBall` (see [Feeding balls from your own
hopper model](launcher.md#feeding-balls-from-your-own-hopper-model-fedball)): the lane's position across the wheels,
the entry speed (the last stage's surface speed) and the range's ball. In the world the simulator builds it for you;
in standalone Python you build it yourself. A ball the launcher turns away at the entry (`no_contact`: the gap is too
big to squeeze it, or `no_wheel`: no wheel under its lane) or that jams is taken out of play as `stuck`, and the
report says why.

**The intake zone.** `[shooter.intake]` is a box that rides on a robot body:

| key | unit | default | meaning |
|---|---|---|---|
| `part` | | required | the stock roller that collects (its spin joint is `<part>.spin`) |
| `at` | | required | the robot mount (or body) the zone rides on, e.g. `drive.center` |
| `pos` | m | required | the zone's center in the mount's frame |
| `size` | m | required | `[depth, width, height]` of the box |
| `rate` | balls/s | required | at most this many balls picked up per second |
| `min_surface_speed` | m/s | 0.5 | the roller's inward (positive) surface speed needed before it collects |

A floor ball whose sphere touches the box while the roller turns inward at `min_surface_speed` or faster is
collected, one at a time, if the robot has room. The balls not collected stay physical and are pushed by the bumper.
Put the box where your real intake reaches: this is the number that decides whether a ball is picked up.

## Beam breaks

`[[shooter.beam_break]]` makes a ball sensor on the ball path that your robot code reads as a **DIO** input,
exactly like a limit switch.

| key | default | meaning |
|---|---|---|
| `at` | required | `"hopper"`, `"launcher"`, or `"stage:<name>"` of a stage |
| `fraction` | | for a stage only: where along it, 0 (hopper side) to 1 |
| `lane` | any | one lane's index in `lanes`, or leave out for any lane |
| `dio` | required | the DIO channel |
| `active_low` | `true` | `true`: the channel reads low while a ball breaks the beam (the usual wiring) |

Two beam breaks can't share a DIO, and a beam break can't use a channel a `[[digital]]` entry already has.

These beam breaks are the only way to sense a ball the `[shooter]` holds: its hopper is a count, not balls in the
world, so a [`[[sensor]]`](sensors.md) can't see them. A `[[sensor]]` sees the balls in the world (on the carpet, in
your own intake), and can't share a DIO with a `[[shooter.beam_break]]` either.

## Balls in the world

Every ball is a MuJoCo free body: `ball.0`, `ball.1` and so on, one for each floor ball and one for each hopper slot
(the `capacity`). Each ball is in one **state**:

| state | meaning |
|---|---|
| `field` | in play: lying on the floor or flying |
| `robot` | held in the hopper, a stage or the launcher (parked; the path tracks it) |
| `stuck` | the launcher turned it away (`no_contact`, `no_wheel`) or jammed |
| `scored` | went through a goal's scoring surface, going in |
| `out` | its center left the field rectangle |
| `spare` | an unused slot of the robot's |

**Parking.** Only `field` balls are in play. Every other ball is parked 20 m below the carpet, under its own x and y,
with all its collisions off, and put back there after every physics step. **A scored ball is parked and hidden**: it
isn't seen resting in the hub, but the goal's score counter has it.

### The contact model

The balls' bounce is set from the ball's `restitution` (the launcher presets; `scrimmage` ships 2020, 2022 and
2026 balls). A MuJoCo contact only pushes, so a spring-damper's bounce isn't its damping ratio's: the sim solves the
push-only relation of Schwager and Poeschel (Granular Matter 9, 2007, 465-469) to get the damping that gives the
requested restitution. Measured by dropping a ball from 1 m on carpet, at the 1 ms step:

| ball restitution | rebound height ratio, measured |
|---|---|
| 0.3 | 0.33 |
| 0.6 | 0.64 |
| 0.87 | 0.88 |
| 0.95 | 0.96 |

(The tests allow +-0.05 on the bounce ratio.) The same holds on the hub's plates.

**Limits, which the sim enforces with an error:**

- The timestep must be at most **1 ms** (`[model] timestep = 0.001`). A longer step lets a fast ball pass through a
  2 cm plate.
- The restitution must be at most **0.95**. A bouncier ball can't be made stable.

**What to expect.** The contact is softer than a real foam ball in the other direction: the balls sink into a plate
a little on a fast hit, about 1 cm at drop speed and, at a 1 ms step, **up to about 9 cm on a 50 m/s hit**, and a
fast hit rebounds up to about **0.07 above** the requested restitution (the ball is already a few centimeters into
the plate on the first step that sees the contact). Both are step artifacts, not part of the model. Shots at
shooter speeds (5 to 15 m/s) are well inside them.

### Flight, rolling and sleeping

- **Flight.** A ball in the air feels drag and spin lift from the `[air]` section (the same equations as the
  [launcher model](launcher.md#what-is-modeled): drag 1/2 rho A Cd v^2, lift 1/2 rho A C_L |v|^2 with the lift
  coefficient from the spin). Backspin from the wheel gives a flatter, longer shot.
- **Rolling on carpet.** `[carpet] rolling_resistance` in the range file is the carpet's rolling-resistance
  coefficient C: a rolling ball feels a torque `C m g r` against its roll, so it slows at `C g / (1 + k)` (k is the
  ball's inertia factor) and **stops**. The default is **0.07**, an *estimate* chosen so a FUEL slows at about
  0.5 m/s^2, like a golf ball on a putting green; nobody has published a figure for an FRC ball on FRC carpet.
  **Measure yours:** roll a real ball at a known speed, time it to a stop, and C = (1 + k) v / (g t). It decides how
  far missed shots and bumped balls drift. Without it a ball rolls on for tens of seconds.
- **Sleeping.** The sim turns on MuJoCo's body sleeping (MuJoCo 3.4 added it; the package requires MuJoCo 4), so the
  hundreds of balls resting on the carpet cost almost nothing until something touches them. Only ball bodies may
  sleep; a sleeping ball wakes when an awake body touches it, when it is moved or when a force is applied to it. Two
  runs stay bit-identical.
- **Speed.** Measured on a development machine (yours will differ): the full 360-ball layout resting runs about
  7 times faster than real time; the fast-mode sample's "Shoot Moving" auto (all 360 balls, a swerve robot, the
  shooter and intake) runs at about 1.0 to 1.8 times real time in fast mode with lockstep. Turn `floor_balls` off
  for an auto that doesn't use them.

### Launching a ball

When the launcher model says a ball has left, the sim places it at the launcher's exit point with the launch velocity
plus the robot's own velocity at that point (and the robot's spin), and the backspin from the wheel. The exit happens
inside a physics step, so the ball is placed where it would have been at the step's start and flies on from there.

**Launch clearance.** The launcher's own geometry doesn't collide (unless its part has
[`collide = true`](parts.md#stock-parts-that-collide)), but the robot's frame and bumpers do, and an exit
point can sit inside them. A launched ball therefore **ignores its own robot for 0.15 s** (`LAUNCH_CLEAR_S` in
`scrimmage/testrange/launch.py`), then collides with everything. 0.15 s is about 1 m of flight at a slow 7 m/s shot,
past the frame of any robot. It still collides with the field, the hub and other balls from the start.

> **Known limitation:** a very slow shot from deep inside the robot can still be overlapping the robot when the
> 0.15 s are up. It is then thrown out by the contact solver.

## Pieces that aren't balls

A field can hold game pieces that aren't balls: a pipe, a cone, a cube, a gear, a hatch panel, a tote. Each kind of
piece is a real rigid body in the same physics as everything else: a pipe rolls on its curved side and stands on its
end, and a rod really goes through a hollow piece's hole. A field can hold several kinds at once (2023 had cones and cubes). The pieces are named by their kind in everything a student reads ("coral
3", not an internal body name).

**What isn't a ball stays a stand-in.** Soft pieces (the 2023 cube, the 2024 note) are rigid stand-ins of the right
size and weight: they don't squash. See [What isn't modeled](#what-isnt-modeled).

### `[range] piece` and the piece presets

`[range] piece = "<preset>"` makes the field's default kind that preset (a ball preset or a piece preset). The kind is
named after its preset. `ball = "<preset>"` and `disc = "<preset>"` stay as they were. A range file with none of the
three uses the first `[piece.<name>]` table as its default kind.

The piece presets are in `scrimmage/launcher/presets.toml`, beside the ball presets. Each number there carries a
`# source:` (the game manual's section) or an `# estimate:` saying how it was estimated.

| preset | what it is | shape |
|---|---|---|
| `2025-coral` | the 2025 CORAL, a PVC pipe: 4.5 in outside, 4 in inside, 11 7/8 in long, 0.658 kg | hollow tube |
| `2023-cone` | the 2023 CONE: an 8 3/8 in square base and a cone 12 13/16 in tall, 0.652 kg | a slab and a cone |
| `2023-cube` | the 2023 CUBE, 9 1/2 in, 71 g. A rigid stand-in for the fabric cube | box |
| `2017-gear` | the 2017 GEAR, an 11 in disc 2 in thick, 0.522 kg, without teeth or hole | cylinder |
| `2019-hatch-panel` | the 2019 HATCH PANEL, 19 in across with a 6 in hole, 3/16 in thick | ring |
| `2015-tote` | the 2015 TOTE, 26.9 by 16.9 by 12.1 in, 3.54 kg, a solid box | box |
| `2024-note` | the 2024 NOTE, a ring 14 in across with a 10 in hole, 2 in thick, 235 g. A rigid stand-in for the foam ring | ring |

The coral's mass is the middle of the manual's 1.1 to 1.8 lb; the hatch panel's is from polycarbonate's density; the
tote's inertia is a solid box's. The file says each one.

### `[piece.<name>]`: a kind of piece

A `[piece.<name>]` table adds a kind to the field. It is one of:

```toml
[piece.coral]
preset = "2025-coral"             # a piece preset, or ball = "2026-fuel" (a ball preset, or a ball file), or disc = "2013-frisbee"

[piece.post]                      # or a kind of your own, from shapes and the manual's numbers
shape = "tube"
diameter_in = 4.5
inner_diameter_in = 4.0           # leave it out for a solid tube; a number makes it hollow
length_in = 11.875
mass = 0.66                       # kg: required
material = "pvc"                  # the surface: a [surface.*] preset ...
# friction = 0.45                 # ... or your own measured number, or a friction file; give one of the two
label = "post"                    # the plain name students read; left out, the table's name
color = "white"                   # what a color sensor reads, and how it is drawn: a named color or [r, g, b]
```

A preset kind is its preset: it takes no shape keys. A kind written out takes a `shape`, a `mass` and a surface
(`material` or `friction`), and every length has an `_in` form (give one of the two). Giving both a preset and a shape,
a shape with a key it doesn't use, no mass, or no surface is an error that names the table and the key.

| `shape` | keys | notes |
|---|---|---|
| `sphere` | `diameter` | |
| `box` | `size` (x, y, z) | the long axis is the box's longest edge |
| `cylinder` | `diameter`, `length` | a solid round piece |
| `tube` | `diameter`, `length`, `inner_diameter` | with `inner_diameter` it is hollow (a pipe); without, solid |
| `ring` | `diameter`, `length`, `inner_diameter` | a tube read as short: a hatch panel, a note |
| `cone` | `diameter` (base), `tip_diameter` (default 0, a point), `length` | |
| `compound` | `[[piece.<name>.shapes]]`, `axis`, `hole` | several of the shapes above, each with its `pos` and `rpy_deg` |
| `cad` | `cad` (a STEP, STL or OBJ file, relative to the range file), `units` (`"m"`, `"mm"` or `"in"`), `scale` (default 1), `axis`, `hole` | an exact shape from CAD |

Every kind also takes `mass` (kg, required), `material` or `friction`, `inertia = [Ixx, Iyy, Izz]` (kg m², left out:
worked out from the shape at the same density throughout), `label` and `color`.

**A rigid piece in the air.** A flying rigid piece (a shape or a piece preset, not a ball or a disc) feels air drag
against its velocity: ½ρ·Cd·A·v². A is the area of the piece's shadow as the air sees it, turned as it is now: its
shadow seen along each of its three axes, worked out from its shape, each counted by how squarely the air meets it.
A NOTE flying flat in its own plane shows its 0.018 m² edge; face first, its 0.049 m² face (the ring, its hole
open), 2.7 times more. Two keys set it, each a number that can't be negative:
- `drag_coefficient` (default 1.17, a flat plate's, an estimate used at every angle; 0 is no drag);
- `spin_damping` (N·m·s/rad, default 0: the piece keeps its spin, as a ball does).

There is no lift, so a flat, backspun piece that really glides comes up short, and the air through a ring's hole is
taken to cost nothing. Results that rest on these numbers are uncalibrated until someone measures. A piece touching
anything (lying on the carpet, leaning on a goal) feels no air. A preset kind takes the two keys too:
`preset = "2024-note"` with `drag_coefficient = 0.9`.

**A kind's color.** `color` is one of the named colors (`"red"`, `"blue"`, `"yellow"`, `"orange"`, `"green"`,
`"purple"`, `"white"`, `"gray"`) or `[r, g, b]`, each 0 to 1. The piece is drawn in it, and a
[color sensor](sensors.md#piece-colors) reads it. A preset kind takes it too, so a 2022 field can have red and blue
cargo as two kinds (`ball = "2022-cargo"` and `color = "red"` in one, `"blue"` in the other). Left out, a ball is
yellow, a disc white and a rigid piece off-white, as they were always drawn. A range file's `[range]` takes `color` for
its own `ball`, `disc` or `piece`.

**Hollow pieces.** A hollow tube or ring is built from 24 convex slices so a rod goes through the hole (the hole is
very slightly smaller than the number you give: the effective radius is cos(7.5 degrees), about 0.9914 times it). A
compound or CAD piece with a hole says so with `hole = { diameter = 0.1, length = 0.3 }` (`center` too if it isn't in
the middle); this is only used to tell if a rod runs through it, for a [seated](test-tab.md#placing-a-piece) check. For a CAD
piece the hole is the piece as it is read, after `units` and `scale`: its `diameter`, `length` and `center` are in
meters (or the `_in` keys) of the full-size piece, not the CAD file's own numbers. A 1:40 model at `scale = 40` takes
the real NOTE's 10 in hole. A `center` read off the CAD tool is in the tool's units and the model's size: turn it
into meters and multiply it by the scale first.

**CAD pieces.** `shape = "cad"` reads the file with the same reader as the Part tab, so a piece can come straight from
the game's CAD. The CAD files carry no mass, so `mass` is required (the manual's weight), and `material` or `friction`
for the surface. A piece that is not convex is cut into convex parts with `coacd`; without it installed, that is a
plain error saying so. The piece's size is checked: one more than 3 m across is refused with "the piece is 301 m
across; check units (m, mm or in)", the usual sign of millimeters read as meters or inches as millimeters.

- **Units.** An STL or OBJ file has no units, so `units` says what its numbers are (guessed from the size when left
  out). A **STEP file says its own units** (the length unit of its parts' unit contexts: meters, millimeters,
  centimeters, inches or feet), and they are used when `units` is left out. Give `units` for a STEP only when the file
  is wrong about them: its numbers are then read in `units` instead. When the file's own unit can't be told (none, or
  its parts disagree), `units` is ignored with a warning and the file loads as it is.
  - *A change for old range files:* before, `units` was ignored on a STEP. A range file that gave a STEP piece a
    `units` different from the file's own (harmless then) now rescales the piece; `scrimmage range check` shows the
    size warning or the "check units" refusal when that is far off. Remove `units` to keep the old size.
- **Scale.** `scale = 40` makes the piece 40 times bigger, for a scale model. The 2024 "Game Piece.step" in the
  3D-print field files is a 1:40 NOTE, 8.9 mm across; the full-size NOTE is `scale = 40`, or better the `2024-note`
  preset.
- **A warning for a piece far from its expected size.** A piece (CAD or not) smaller than 2 cm across, or whose
  `mass` makes it lighter than air or denser than tungsten for its size, is accepted with a warning: `scrimmage range
  check` prints it, and a test run's notes say it. It is usually a scale model, a units mistake, or a mass in pounds.

**The long axis, and flat pieces.** Each kind has a long axis: a tube's, ring's, cylinder's or cone's own, a box's
longest edge, and for a compound or CAD piece `axis = "x"`, `"y"` or `"z"` (default `"z"`). A kind is **flat** when it
is shorter along that axis than it is wide across it (a gear, a hatch panel, a note). Its words are "lying flat" and
"standing on edge", not "standing on end".

**Surfaces.** `material` names a `[surface.*]` preset in `presets.toml`: `pvc` 0.4, `polycarbonate` 0.4, `rubber`
0.8, `foam` 0.6, `hdpe` 0.25, `polypropylene` 0.3 and `fabric` 0.5. Each is a sliding friction against a hard robot
part, and **each is an estimate** (the file says how each was chosen): friction against FRC pieces isn't published.
Replace one with your own number, measured with a sled pull ([how](measuring.md#sled-pull)): `friction = 0.45`, or
`friction = "pvc.measure.toml"`, a friction measurement file relative to the range file, so the piece's surface is
named as measured. The `[surface.*]` presets themselves can't be measured files. A ball kind takes a ball measurement
file the same way: `ball = "fuel.measure.toml"` (with a `base` preset).

**Contact.** A piece has a firm contact (a stiff spring, so a squeezed piece doesn't creep into the part that holds
it). The `friction` is the piece's own; a robot part's pad with a grip material has priority, so its friction applies
([Grip material](test-tab.md#grip-material)).

### Putting kinds on the field

`kind = "<name>"` on a `[[floor_balls]]`, a `[[station]]` or a `[[goal]]` says which kind it uses. Left out, it is the
default kind (a goal's, the first ball or disc kind).

- **`[[floor_balls]]`** (its pieces, whatever the kind) also takes `tip_deg` and `rotation_deg`, one number each for
  the whole table. `tip_deg` is how far the piece's long axis is tipped up from lying: 0 lying on its side (the
  default), 90 standing on end, from -90 to 90. A flat kind with no `tip_deg` lies flat (90). `rotation_deg` turns it
  about the vertical from the field's +x: 0 is lying in line with +x, 90 across. A piece is set down just above its
  resting height and settles, so a leaning piece ends wherever physics puts it. Each floor piece must be inside
  the field by its reach and not overlap another: two are at least the sum of their horizontal reaches apart.
- **Staged on top of something.** `height = 1.2` (meters, 0 to 10, default 0: the carpet) sets that table's pieces
  down on a surface that high instead, such as 2024's HIGH NOTEs on top of the AMP: give the AMP as an `[[element]]`
  box or an `[[obstacle]]`, and the pieces' `height` as its top. A staged piece is not refused for lying over an
  element (that check is the carpet's), and pieces at different heights may share an x and y. One with no element,
  obstacle or goal top under its center (within 2 cm) is accepted with a warning: it would fall. A goal's `body` is
  four walls, not a solid block, so a piece over the middle of one falls in; stage it over a wall, or give the top
  as an element.
- **`[[station]]`** feeds any ball or rigid piece (not a disc). The keys `tip_deg` and `turn_deg` give the fed piece's
  base orientation (the turn is about the vertical from `heading_deg`), and `spread_tip_deg` and `spread_turn_deg`
  spread each feed by up to ± that much, as `spread_yaw_deg` does. They default to 0 (a flat kind's tip to 90). A
  ball station is not changed: a ball is fed unturned and draws the same random numbers as before, so old feeds are
  identical. A non-ball station draws two more numbers per feed (the tip and turn), after the nine it already draws.
  `spread_rotation_deg` then turns a piece a little more about each axis (the Test tab calls it "extra tumble").
  Each fed piece is set down just clear of the chute's floor (or at the release point if there is no chute), whatever
  way it was drawn to lie, so a long piece doesn't start inside the floor. A chute with no `width` is 1.3 times the
  piece's largest side wide, and its side walls are as tall as that side.
- **`[[goal]]`** counts any piece kind: the one it names with `kind`, else the field's first ball or disc kind, else
  (a field of rigid pieces only) its first kind. A piece scores when its center crosses the scoring surface, as a ball
  does, so a ring can count with its rim over the edge of the opening; the rim bars collide, so this is rare. A funnel
  throat no wider than a rigid piece's thinnest side is an error; one narrower than its middle side is a warning, as
  the piece may pass only on edge. The `[shooter]` shoots balls, discs and, with a `pinch_shooter`, a flat rigid piece
  such as the NOTE ([a piece shooter](#a-piece-shooter-a-note)); other pieces are moved only by contact with the
  student's mechanism.

**Words for how a piece lies.** The Test tab and the floor spread's results name how a piece lies from its tip and
turn. Tipped up 80 degrees or more: "standing on end" (for a flat kind, "lying flat"). Tipped up 10 or less: "lying
in line" at a turn of 0, "lying across" at 90 (either way), "lying in line, turned around" at 180, "lying at 45°"
between; a flat kind at tip 0 is "standing on edge". In between: "leaning 40°, lying across". When a spread would give
two placements the same words, the words add what differs ("lying flat (turned 45°)").

### Floor zones

A range can mark areas of the carpet as zones, the way a game's tape lines do (2024's starting zone, wing and stage
zone):

```toml
[[zone]]
name = "blue_start"          # source: the manual's starting line
corners = [[0.0, 0.0], [1.93, 0.0], [1.93, 8.21], [0.0, 8.21]]   # field x, y (m), in order around the zone
color = "blue"               # the tape's color; default white
```

A zone is any polygon whose edges don't cross, with at least 3 corners, each on the field; two zones can't share a
name. It is drawn as 2 in tape along its edges, which touches nothing. The Test tab's **robot is in** / **out of zone**
condition reads it: the robot's center, seen from above, inside or outside the zone (an edge counts as inside). That is
enough for "did it leave the starting zone" (2024's LEAVE) and "is it in the wing"; a rule about the whole robot or
its bumpers (2024's PARK) isn't modeled yet.

### Rods

A rod is a field element: a straight round or square rod fixed to the field, such as a peg, a pole, or a coral branch.
It is an `[[element]]` with `kind = "rod"`; its keys are in [docs/terrain.md](terrain.md#field-elements-in-range-files).

```toml
[[element]]
name = "branch_l2"                # the name a test's checks use
kind = "rod"
base = [5.2529, 4.0, 0.6404]      # x, y, z (m) of the end that is fixed (or base_in, in inches)
tip = [5.0240, 4.0, 0.8006]       # x, y, z (m) of the free end the piece slides on from (or tip_in)
diameter_in = 1.66                # across; a square rod's side
```

The rod runs from `base` to `tip` at any angle; its length is the distance between them. A round rod's free end is
rounded and reaches exactly to the tip; a square rod (`section = "square"`) has a flat end. `friction` defaults to 0.6.

**Starts are kept clear of rods.** A rod is a solid thing the robot would begin inside. A start whose robot outline
lies under a rod that is lower than 0.5 m anywhere is refused, with a message naming the rod. A rod higher than that
over a start is allowed, since the range doesn't know the robot's height (a branch at reef heights is far above).

### The reef field

The built-in `reef` field is a test field to try a grabber on, not the whole 2025 reef: one pipe, its three branches
`branch_l2`, `branch_l3` and `branch_l4` at the manual's heights and angles (the two lower ones rise at 35 degrees
toward the robot; L4 is vertical), a coral on the carpet in front of the robot, and a coral station.

- Starts: `facing_coral` (2.0, 4.0 m facing +x, the coral lying in line 0.63 m ahead) and `at_coral_station` (against
  the coral station's chute).
- The heights and angles, and the pipe's size, are from the 2025 manual (section 5.3); the branches' lengths and the
  pipe's height are estimates from its figures, and the file says so. The post is drawn square and the bends aren't
  built.
- The worked example is [`examples/blocks/reef`](../examples/blocks/reef/README.md); the walk-through is
  [Test a grabber on a coral](getting-started/test-a-mechanism.md#6-test-a-grabber-on-a-coral).

### How the pieces are kept

Every piece, whatever its kind, is a numbered free body in the same list as the balls, `ball.<i>` (the name stays so
that replay, NetworkTables and older tests keep working). The world knows each slot's kind, and the ball-only parts
skip the rest: flight, the carpet's rolling resistance, scoring, the launcher's pickup and preload. A piece's body
origin is its center of mass, so its position is where it balances.

## The trench field

The built-in `2026-trench` field is one 2026 REBUILT TRENCH, to find out whether a robot fits under it: does it clear
the arm with its elevator down and its arm stowed, and by how much? It has no hub, bump or FUEL around it.

- **The elements.** `trench_arm`, a box across the top whose underside is 22.25 in above the carpet, standing on two
  side structures, `trench_side_left` (+y) and `trench_side_right` (-y), with the 50.34 in opening between them. The
  TRENCH stands against the guardrail (y = 0), at the same x as `2026-bump`'s BUMP (on the hub's line).
- **The start.** `[start.approach]` faces the opening (heading 0 drives through it along +x), centered on it, 1.5 m
  before its near face.
- **The numbers.** The 22.25 in clearance, the 50.34 in opening and the TRENCH's 47.0 in depth, 65.65 in width and
  40.25 in height are the 2026 manual's (section 5.6). The arm's 47 x 18 in section is an estimate: the manual gives
  no arm size, so the arm is taken to fill the whole depth and the height above the opening. The two sides are taken
  to be equal. The pivoting arm of the far-side TRENCHES isn't modeled: this arm is fixed.
- **Your robot must collide to hit it.** A stock elevator or arm passes through the arm unless its part has
  [`collide = true`](parts.md#stock-parts-that-collide). The Test tab's
  [*robot clears*](test-tab.md#clearance-to-a-field-element) check measures how close the robot comes.
- The worked example is [`examples/blocks/trench`](../examples/blocks/trench/README.md): a stand-in robot with an
  elevator and an arm that clears the arm by 2.9 in stowed, and hits it with the arm raised.

## The 2024 field

The built-in `2024` field is the FRC 2024 CRESCENDO field, written on a kickoff dry run from the game manual, the
field drawings, the WPILib 2024 AprilTag layout and Team 6328's field constants
([`scrimmage/testrange/ranges/2024.toml`](../scrimmage/testrange/ranges/2024.toml); every number cites its source on
its line, or says it is an estimate or our choice).

- **The field** is 2024's own, 16.541 by 8.211 m, so WPILib coordinates and AprilTag positions agree with it.
- **The piece** is `note`, the `2024-note` preset: a rigid stand-in for the foam ring (it doesn't squash). The 11
  staged NOTES lie on their marks (`floor_balls = false` leaves them out), and the two SOURCES (`source_blue`,
  `source_red`) feed NOTES down their chutes.
- **The goals** count NOTES. `speaker_blue` and `speaker_red` are the SPEAKER openings, entered going up under the
  hood; the hood and the wall under the opening are `[[obstacle]]` boxes (`speaker_blue_hood`, `speaker_blue_wall`),
  not plates of the goal, so a NOTE's corners can't pass through the hood on the way in. `amp_blue` and `amp_red` are
  the AMP pockets, entered sideways.
- **The elements.** The SUBWOOFERS (a box under each SPEAKER and its two angled sides), the SOURCE walls, and the
  STAGES: a core box, three legs and three CHAINS as rigid bars (`[climb]` scores a robot off the carpet touching a
  chain, as ONSTAGE). HARMONY, the TRAP and the MICROPHONES aren't modeled.
- **The zones** `blue_start`, `blue_wing`, `red_start` and `red_wing` are drawn as tape.
- **The starts.** `blue_subwoofer` (against the blue SUBWOOFER, facing the SPEAKER), `blue_amp` (1 m back from the
  blue AMP, facing it), `blue_amp_side`, `blue_source_side`, `blue_wing_note` and `at_blue_source`.
- **What isn't there.** The rest of the AMP and the SPEAKER's inside (a NOTE counts when it crosses the opening; where
  it goes after isn't modeled), the AMPLIFICATION and the game's points.
- The worked example is [`examples/blocks/speaker`](../examples/blocks/speaker/README.md): a stand-in robot with a
  pinch shooter scores a NOTE in the blue SPEAKER from the SUBWOOFER, with a shot `launcher aim` finds there.

## The range file

A range file describes a season's field elements: the ball, the goals, the floor balls, the starting positions,
the carpet and the elements robots drive over or work (bumps, ramps, a tilting platform, doors and other moving
panels: `[[element]]` tables, described in
[docs/terrain.md](terrain.md#field-elements-in-range-files)). SI units, angles in degrees (keys end in `_deg`), WPILib field coordinates (blue alliance wall at
x = 0, z up) on the generated field of 16.54 by 8.07 m. The built-in `2026` is
[`scrimmage/testrange/ranges/2026.toml`](../scrimmage/testrange/ranges/2026.toml); copy it to make your own and
point `[range] file` at the copy.

**The field's size.** A season whose field isn't 16.54 by 8.07 m gives its own in the range file's `[range]` table:
`field = { length = 16.541, width = 8.211 }` (2024's, from the field drawings), each 2 to 40 m, the inside faces of
the alliance walls (length, along x) and the guardrails (width, along y). The generated field's carpet and walls
follow it, so WPILib coordinates, AprilTag positions and robot code agree with the range. Floor pieces and stations
must lie inside it. A goal whose center is outside it is accepted with a warning: it is behind a wall, so nothing
can reach it (2024's AMP at y = 8.204 m needs the 8.211 m field).

**Checking and seeing a range file.** A range file isn't a robot config, so `scrimmage edit` doesn't open it (it says
so, and what to do instead). Check one on its own, with no robot:

```bash
scrimmage range check ranges/2024.toml          # "ok", the field's size and what it holds, then any warnings
scrimmage range check 2026 ranges/2024.toml     # several at once; a built-in range by its name
scrimmage range view ranges/2024.toml           # the field in the MuJoCo 3D viewer, no robot
scrimmage range view ranges/2024.toml --out top.png    # a picture from above instead
```

`range check` exits 1 when a file has a problem, and prints the problem as the robot's config would (the file, the
table and the key). A warning (a goal behind the walls) doesn't fail it. `range view` shows the goals, obstacles,
elements, stations' chutes and floor pieces on the carpet at the range's field size.

```toml
# range file: one hexagonal goal, an 8-ball line on the floor, a start in front of the goal
[range]
name = "My practice goal"
ball = "2026-fuel"        # a launcher ball preset: 2020-power-cell, 2022-cargo, 2026-fuel or one from a past game (docs/launcher.md)

[carpet]
rolling_resistance = 0.07 # optional; see "Rolling on carpet"

[[goal]]
name = "goal"
pos = [4.6, 4.035]         # field x, y of the opening's center
heading_deg = 0.0          # goal +x points away from the alliance wall
rim_height = 1.829         # opening height above the carpet
tilt_deg = 0.0             # 0 faces up, 90 faces -x (a side opening), 180 faces down (scores going up)
opening = { sides = 6, across_flats = 1.06, rotation_deg = 0.0 }
funnel = { depth = 0.395, slope_deg = 60.0 }
body = { size = [1.194, 1.194], height = 1.51 }

[[floor_balls]]
grid = { corner = [7.0, 4.0], step = [0.2, 0.2], count = [1, 8] }

[start.front]
pose = [3.0, 4.035, 0.0]   # x, y, heading in degrees
```

### `[[station]]`: a human player station

A station feeds balls, or any other kind of rigid [piece](#pieces-that-arent-balls), onto the field from a release
point, as a human player would: one every `interval` seconds, each varying a little. It is built like the bench's
`[[inlet]]` ([docs/bench.md](bench.md)), with limits where the inlet has standard deviations. A `[[station]]` in a disc
range is an error.

```toml
[[station]]
name = "feeder"
release = [0.3, 4.0, 1.1]     # where a fed ball's center appears (m); or release_in, in inches
heading_deg = 0.0             # the way it is fed, from the field's +x
pitch_deg = 30.0              # down from horizontal
speed = 0.0                   # m/s along that direction (0: the ball is only set down)
interval = 1.0                # s between balls
stock = 12                    # balls to feed; leave it out for "never runs out"
spread_yaw_deg = 5.0
chute = { length = 0.5, slope_deg = 30.0 }
```

| key | unit | default | meaning |
|---|---|---|---|
| `name` | | required | the name a test's feed blocks and the `[range] station` table use. It can't repeat another station, goal, obstacle or element's name |
| `release` or `release_in` | m or in | one is required | where a fed ball's center appears: field x, y and height. It must be on the field, at least a ball radius above the carpet, and clear of every element, obstacle and chute |
| `heading_deg` | degrees | required | the way a ball is fed, turned from the field's +x about the vertical (z) |
| `pitch_deg` | degrees | 0 | down from horizontal, -90 to 90 |
| `speed` | m/s | 0 | the ball's speed along the heading and pitch. 0 sets it down with no push |
| `interval` | s | 1.0 | time between balls. A human player's pace; some are much quicker, so it is a setting to try |
| `stock` | balls | none | how many balls the station feeds, 1 to 200. Left out, it never runs out, but feeds at most 50 in one run. In a test's Setup (and the `[range] station` table), `stock = 0` also means "never runs out" |
| `spread_yaw_deg`, `spread_pitch_deg` | degrees | 0 | each feed's heading and pitch vary by up to this much either way |
| `spread_speed` | m/s | 0 | each feed's speed varies by up to this much either way |
| `spread_position` or `spread_position_in` | m or in | 0, 0, 0 | a box around the release point, three numbers in the station's own frame (along the heading, across it to the left, up): each feed's release point moves by up to this much either way on each axis |
| `spread_rotation_deg` | degrees | 0, 0, 0 | how much each feed is turned about each axis (roll, pitch, yaw). A round ball doesn't show it; for a piece that isn't round it tumbles it a little |
| `kind` | | the range's default | the kind of piece it feeds, the name of a `[piece.<name>]` table (or the default kind) |
| `tip_deg` | degrees | 0 (flat kinds: 90) | the fed piece's tip: 0 lying on its side, 90 standing on end, -90 to 90. A ball ignores it |
| `turn_deg` | degrees | 0 | the fed piece's turn about the vertical, from `heading_deg` |
| `spread_tip_deg`, `spread_turn_deg` | degrees | 0 | each feed's tip and turn vary by up to this much either way |
| `chute` | | none | a sloped floor and two side walls (below) |

**Every spread is plus or minus its value.** `spread_yaw_deg = 5.0` means each ball goes up to 5 degrees left or 5
degrees right of the heading, and `spread_position = [0, 0.02, 0.02]` is 2 cm either way across and up. The draws are
uniform between those limits, not standard deviations, so a ball never leaves a tight chute's limits. A spread of 0 is
exact.

**The position spread follows the station.** `spread_position` is along the heading, across it and up, not along the
field's x and y, so a station on a slanted wall (2024's SOURCE, at 30 degrees) spreads along the wall with
`spread_position = [0, 0.3, 0]` whatever its heading. (Before, it was along the field's axes; a station heading 0
reads the same either way.) Every corner of the spread box is checked like the release point: a feed whose center would
start inside an element, an obstacle, a chute or beyond the field's walls is refused, since the physics would throw
it out fast (the 2024 dry run saw a NOTE leave a wall at 13 m/s); one whose piece would only touch something is
accepted with a warning (`scrimmage range check` shows it).

**The seed.** The draws come from `[range] seed` (default 1), one stream per station, so the same seed gives the same
feeds whatever the timing. A different seed is a different shuffle of the same spreads: the Test tab's **New shuffle**
changes it.

**The chute** is an optional sloped plate with a wall on each side, built into the world as the body
`element.<station>.chute`. `length` runs along the slope, `slope_deg` is below horizontal, and `width` is the space
between the walls (leave it out for 1.3 times the piece's largest side, a ball's diameter, so a piece of any preset
fits, however it is turned). The floor's top starts beneath the release point and runs down along the heading; its
walls stand one largest side tall. The chute is
refused if its low end would go below the carpet, or it reaches off the field or into an element, an obstacle,
another station's chute or a start's robot outline (the check uses each chute part's base, so the wall tops leaning out about
a ball diameter times the sine of the slope over the low end are not counted).

**A feed waits** while another piece on the field is within one ball diameter (for a piece that isn't a ball, its
bounding radius) of the release point, so pieces don't land on each other. **A fed piece starts just clear of the chute
floor**: it is turned as drawn from its tip and turn, then set down just above the floor at the height that orientation
needs, so a long piece does not start inside the floor. If the release point stays blocked, a test's **feed one** block times out and says so.

**How many balls a station feeds.** Each station has its own parked pool of balls (`stock` of them, or 50 when `stock`
is left out), added to the world when it is built. Because the model is built once, a station can't be restocked
during a run.

The built-in `station` range is one station at the blue alliance wall with a short chute and two starts: `at_station`
(bumpers against the chute's low end) and `facing_ball` (a ball on the carpet in front). The built-in `2026` range
has its two outposts as stations: see [Where the 2026 numbers come from](#where-the-2026-numbers-come-from).

### Geometry conventions

- **The goal's frame** has its origin at the center of the opening at rim height. x runs along `heading_deg` (from
  the field's +x, counterclockwise), z is up.
- **The opening** is a regular polygon (`sides`, `across_flats`; or `{ diameter = d }` for a circle approximated by a
  24-gon), turned by `rotation_deg`. Vertex k lies at `rotation_deg + k * 360 / sides` from the goal's +x, so a
  hexagon at 0 has its corners on +-x and flats facing +-y.
- **Tilt.** `tilt_deg` (0 to 180) turns the opening about the goal's y axis. The entry normal, the side a scoring
  piece comes from, goes from +z (0: the opening faces up, a piece scores going down) to -x (90: it faces back
  toward the field, a piece scores going toward +x, into the wall) to -z (180: it faces down, a piece scores going
  up). A piece crossing the other way never scores. So the 2024 SPEAKER, entered going up and into the wall through
  an opening whose front lip is 14.8 deg higher than its edge at the wall, is `heading_deg` pointing into the wall
  (180 at the blue wall) and `tilt_deg = 165.2` (180 - 14.8). With
  `heading_deg` this covers every way an opening can face, so a tilt outside 0 to 180 is refused: turn the heading.
- **The funnel.** Walls run inward from each rim side at `slope_deg` below horizontal for `depth` along the entry
  normal. The **throat** is where they end: the opening inset by `depth / tan(slope)` on each flat, or the measured
  `throat` (across flats), when you give one. Walls are 2 cm thick plates.
- **The scoring surface** is the throat for a funnelled goal and the opening for a goal with none. A ball **scores**
  when its path crosses that surface going in, inside the polygon (a ball that lands exactly on the plane or an edge
  counts). A goal with no funnel gets a thin bar outside each rim side, flush with the rim.
- **The body and plates.** `body` is the goal's housing: four side walls from the carpet to `height`. `[[goal.plate]]`
  adds any flat rectangle, in the goal's frame: at `rpy_deg = [0, 0, 0]` it stands vertical, `size` is its width
  (goal y) and height (z), its face normal is along x, and `pos` is its center. `rpy_deg` turns it (roll about x,
  then pitch about y, then yaw about z). The 2026 range has the hub's back net as a plate.
- **Floor balls** are either `grid = { corner, step, count }` (x outer, y inner) or `at = [[x, y], ...]`. Balls lying
  too close (overlapping), or off the field, are errors. A table may name a `kind`, and gives its pieces a `tip_deg` and
  `rotation_deg` ([how a piece lies](#putting-kinds-on-the-field)).
- **Starts** are `[start.<name>]` with `pose = [x, y, heading_deg]`.
- **Slot goals.** `opening = { kind = "rect", width = w, height = h }` is a rectangle: `width` along the goal's y,
  `height` along its x, centered on the goal's origin (so `rim_height` is the slot's center height). With
  `tilt_deg = 90` it stands on a wall: the goal's x is up and `heading_deg` points into the wall. `slot = { depth,
  frame }` builds it as a frame `frame` wide around the opening on the wall face, a box `depth` deep behind it and a
  back wall. A rect goal has no funnel or body; without `slot` it gets the rim bars.
- **Obstacles.** `[[obstacle]]` is a static box: `name`, `pos` (field x, y and the center's height), `size` (full
  lengths) and `rpy_deg`; or a bar, `from = [x, y, z]`, `to = [x, y, z]` and a square `width`.
- **Discs.** `[range] disc = "2013-frisbee"` (a disc preset, `scrimmage/disc/presets.toml`) in place of `ball`
  makes a disc range, with `aero = "<set>"` for its flight (default `pc-long`). Discs are free cylinders with the
  disc's inertia; in flight they get the same Hummel force and torque as `scrimmage disc run`'s flight model
  ([docs/disc.md](disc.md)) each physics step. Floor pieces lie flat. A disc range ignores `[carpet]
  rolling_resistance` (a flat disc doesn't roll).
- **Elements** are `[[element]]` tables: see [docs/terrain.md](terrain.md#field-elements-in-range-files), which has
  the `rod` kind ([Rods](#rods)). A start whose robot outline overlaps an element, or a floor ball resting inside one,
  is an error; so is a start under a rod lower than 0.5 m. The start check uses a fixed 0.9 m square for the robot,
  not your robot's own outline: an intake or arm that reaches past it, into an element or onto a floor piece, isn't
  caught there (the robot is placed as the start says and the physics sorts out the overlap at the first step).

The built-in `2013` is the ULTIMATE ASCENT field's goal end:
[`scrimmage/testrange/ranges/2013.toml`](../scrimmage/testrange/ranges/2013.toml). The blue alliance's four slot
goals (high, two middle and low, from manual section 2.2.3) on the red alliance wall, the wall itself, the blue
pyramid's four legs and base lip as obstacles, and three starts behind the auto line. Only that one goal wall is
modeled, and the goal depth (24 in) and the middle goals' and pyramid's positions are estimates; the file cites
each number. The generated field is 8.07 by 16.54 m, the 2013 carpet 8.23 by 16.46 m, so the range measures from the
goal wall: the shot's distances are the manual's. It is the range for shooting discs; to climb the pyramid, use
`2013-pyramid` ([docs/climbing.md](climbing.md)), which has the pyramid's bars and no goals.

Every key is checked: unknown keys, a ball preset that doesn't exist, a throat wider than the opening, two goals
with one name and the like are errors that name the file and the table.

### Where the 2026 numbers come from

Every number in `2026.toml` cites its source on its line, or says it is our choice.

- **Manual:** the 2026 game manual for the field's size (5.2), the zones (5.3), the hub's 47 by 47 in body, 41.7 in
  hexagon and 72 in rim (5.4), the bumps and trenches (5.5, 5.6), and the FUEL count: 360 to 408 in the neutral zone
  (6.3.4).
- **Field CAD:** `FE-2026-rev-rebuilt-playing-field.step`, measured in FreeCAD. This is where the hub's position and
  rim, the funnel (walls down to 0.395 m, 60 degrees), the throat (0.593 m across the flats) and the net come from,
  and where the **FUEL layout** comes from: 456 FUEL solids as 150 mm cubes, of which 360 form an exact 12 by 30
  grid on a 6 in pitch (two blocks of 15 rows, split by the manual's 2 in divider). The CAD's frame is millimeters
  from the field center; the range file states the transform.
- **The throat gap.** Depth and slope alone give a throat of 0.604 m; the CAD's funnel base narrows the opening to
  0.593 m. The range uses the CAD's value and says so in the file.
- **The four BUMPS** (`bump_blue_left`, `bump_blue_right`, `bump_red_left`, `bump_red_right`) are from the CAD, as in
  `2026-bump.toml` (one of them alone): see [docs/terrain.md](terrain.md).
- **The two OUTPOSTS** (`outpost_blue`, `outpost_red`) are stations: 24 FUEL each (manual 6.3.4), fed one every 0.25 s
  down a 15 degree tunnel out of a notch in the alliance wall (manual 5.9.2; geometry from the CAD). The release point
  is just clear of the wall, so **the outposts have no chute** (the tunnel is behind the wall, off the field), and **the
  chute door**, which holds the staged FUEL in, is left out: a station feeds as if it were open. The feed pace and the
  spreads are estimates and the file says so. `blue_outpost` is a start below the blue opening.
- **Starts** (`blue_left`, `blue_center`, `blue_right`) are our choice, in front of the left bump, the hub and the
  right bump. `blue_center` is behind the starting line so a bumper overlaps it. `blue_left` and `blue_right` are
  0.13 m further back: the shooting autos turn there to face the hub, and from the line the robot's bumper corner hit
  the bump turning.
- **Carpet rolling resistance** is an estimate (above).

To make a range for another field, measure its CAD or its manual the same way and cite each number on its line.

## Scenarios: signals, `by_time`, `[range]`, `robot_env`

On a range, scenario checks (`at_end`, `always`, `by_time`, ...) can read these signals:

| signal | meaning |
|---|---|
| `score` | balls scored, all goals |
| `score.<goal>` | balls scored in one goal (a name from the range file; an unknown name lists the goals) |
| `fired` | balls the launcher has launched |
| `held` | balls in the robot |
| `on_field` | balls in play |

Without a `[range]` on the config these signals are an error that says so.

**`by_time`** is a check that a condition holds by some time rather than at the end of the step:

```toml
[[step.check]]
by_time = { signal = "score.hub_blue", min = 8, t = 15 }    # 8 scored by 15 s into the step
```

It takes the keys of `at_end` (`signal` and `min`, `max`, `equals` or `approx` with `tol`) plus `t`, seconds into the
step. It passes at the first tick the condition is met up to `t`; otherwise it fails with the value at `t`. `t` can't
be beyond the step.

**A scenario's `[range]`** overlays the config's, with the keys `file`, `start`, `preload` and `floor_balls`. A
scenario's `file` that isn't a built-in range's name is **relative to the scenario file**. A scenario's `start`
replaces the config's `start` or `[robot] start_pose` for that run. A bad `start` or a `preload` the robot can't hold
is an error before the run starts. A scenario with a `[range]` needs a config with one. A scenario can't set `ball`,
`station` or `seed`: those are the Test tab's settings, and robot code driving a station is not part of scrimmage yet.

**`robot_env`** sets environment variables for the robot program for that run:

```toml
config = "../configs/fastmode_range.toml"
robot_env = { SCRIMMAGE_AUTO = "Shoot Left" }

[range]
start = "blue_left"
preload = 8
```

The robot program reads them the usual way (`System.getenv`); the fast-mode sample picks its auto this way. A
`robot_env` needs a `[robot_program]` in the config to give it to, and it can't be used with a session that attached
to an already-running robot: in both cases the scenario is an error rather than silently running the wrong auto.

## The report, the viewer and NetworkTables

When the world has a range, the run's `report.json` and `report.md` add:

- `## Score`: per goal, balls scored, fired, held and on the field;
- `## Balls`: **a table of the balls that took part**: one row each for the balls fired, collected, scored, stuck,
  out, or moved more than a ball radius from where they started. For each: when it was fired, the robot's pose and
  speed, the hood angle, wheel speed, exit speed, spin and the wheel's slip at the exit, its outcome, and where it
  ended up (the goal and time it scored in, the point it came to rest or left the field, or the launcher's status),
  and what it touched first. The balls that never took part are **counted** under the table (untouched floor balls,
  unused spare slots), not listed: 360 untouched rows would bury the ones that matter.

A run with no range has none of these.

In the viewer (the live and drive views), the balls in play are drawn and the hub is part of the field; a score
overlay shows each goal's score and `fired` / `held`. Scored and held balls aren't drawn.

**NetworkTables.** When `pyntcore` is available, the balls in play are published as a `Pose3d[]` on
`/MuJoCo/Balls` (for AdvantageScope's 3D field).

## Aiming tables: `scrimmage launcher aim`

An aiming table gives the wheel speed and hood angle that put a ball through a goal from each distance, so your
robot code can look them up:

```sh
scrimmage launcher aim configs/fastmode_range.toml --goal hub_blue --distances 1.5:6.0:0.25 --csv aim.csv \
    --java src/main/java/frc/robot/AimTable.java --java-class frc.robot.AimTable
```

| option | meaning |
|---|---|
| `config` | a robot config with a `[range]` and a `[shooter]` |
| `--goal` | the range's goal to aim at (its name in the range file) |
| `--distances FROM:TO:STEP` | robot center to goal center, meters, both ends included (500 at most) |
| `--hood-step DEG` | hood angles tried every DEG (default 2) |
| `--csv FILE` | also write the table as CSV |
| `--java FILE --java-class pkg.Name` | also write it as a Java class (the two go together) |

For each distance it finds a wheel speed and hood angle whose ball, fed at the entry, crosses the goal's rim height
**descending at the goal's center**, using the launcher model's own wheel and the flight model. It uses the
assembled robot's launcher height and forward offset from its center; the robot is taken as stationary and the path
unobstructed (the rim and funnel are not modeled), so it is an estimate and is marked `UNCALIBRATED`. The table is a
fixed-size set of rows `{distance, wheel_rps, hood_deg, flight_s, apex}`.

- **Unreachable distances** are marked: a CSV row with `status` = `unreachable` and no values, a comment in the Java
  table (the row is left out), and a warning (for a level goal, with the reachable range). If none of the requested
  distances is reachable the command is an error. Ask for 1 to 6 m and you get the reachable part.
- **Rows follow the previous row.** Several wheel speeds and hood angles can reach one distance. The first row takes
  the steepest solution; each later row takes the solution **closest to the row before it** (wheel speed and hood angle
  together), so the table is smooth to interpolate between.
- `flight_s` is the time from the exit to the rim-height crossing; the robot code can use it to lead a moving shot.
- **A launcher on a turret** (the stock `turret` part, [parts.md](parts.md#turret)) is measured with the turret at 0.
  A turret near the robot's middle moves the launcher only a little as it turns, which is small next to the shot's
  distance.
- A table takes several minutes for a long list of distances.

**A goal that isn't level** (its `tilt_deg` isn't 0: the 2024 SPEAKER, entered going up under its hood; a slot in a
wall) is aimed at its **scoring surface** instead. A level goal such as the hub is aimed as above, unchanged.

- The robot stands on the goal's **approach line**: back from the goal's center along the level part of the scoring
  surface's normal (the side a piece comes from), facing the goal. `--distances` is measured along it.
- A row's ball crosses the opening's center **from the side it is entered**, at least its radius inside the opening,
  and touches none of the range's `[[obstacle]]` boxes, elements or other goals' plates (nor, with its center, the
  goal's own plates) on the way in. A shot that would go through a hood roof, or clip its front edge, isn't offered.
- The first row of a goal entered going up takes the **lowest apex** that scores (the steepest, as for the hub, would
  go up into a roof). Later rows follow the row before, as above. These flattest shots peak at the opening, so some
  are just past their apex, still moving up through a tilted opening.
- `flight_s` is the time to that crossing; `apex` is the flight's highest point as if nothing stopped it.
- A table of 5 distances takes about 10 s: a path doesn't depend on the distance, so each hood angle's paths are
  flown once, on a coarse wheel-speed grid, and only until they're back down past the opening's height.
- The ball still flies unobstructed after it crosses: what it hits inside the goal isn't modeled.

**A piece shooter** (`[shooter] kind = "piece"`, [above](#a-piece-shooter-a-note)) is aimed the same way at a goal
that isn't level, its channel's tilt in place of the hood angle (`--hood-step` steps the tilt) and both wheels at
`wheel_rps`. Its shot is the pinch model's exit, flown as a rigid piece with its drag and its orientation, and a row
scores only if all of the piece fits through the opening at the crossing (its outline's points, turned as it
flies) and nothing touches an obstacle first. At a goal entered going up, a piece's row must also **peak at least
0.10 m above the opening's center** (`UPWARD_APEX_MARGIN` in `scrimmage/testrange/aim.py`), so the piece is still
rising as it crosses: flown again in the world, rows that crossed flat at their apex caught the SPEAKER's front lip
(17 of their 24 shots missed: all 16 at the row's speed or 2% above it, and one of the 8 at 2% below), while every
row with the margin scored at 2% either side (21 of 21). It costs the flattest, slowest shots and the farthest
distances (the example robot's table stops at 2.37 m with it, and reached 2.57 m without it). A ball's rows keep the rule above (they aren't flown again in the world). Its
UNCALIBRATED line names the piece's stiffness, the tread's friction and the drag coefficient as the estimates, and
says lift isn't modeled. A level goal (the hub) is refused for it. It takes longer than a ball's: about 15 s for 3
SPEAKER distances with 7 tilts, 35 s for 5 with 16, and about a minute for the example's 8 with 21.

Robot code should **hold fire beyond the table's reach** rather than clamp to its last row; the sample does.

## The fast-mode sample's autos

[`examples/java_fastmode`](../examples/java_fastmode) has a shooter, a generated `AimTable.java` and autos picked
by `SCRIMMAGE_AUTO`; they run on `configs/fastmode_range.toml` (fast mode) and `configs/fastmode_range_phoenix.toml`
(Phoenix mode). See [Shooting on the test range](fast-mode.md#shooting-on-the-test-range). The scenario files
`scenarios/fastmode_shoot_*.toml` are working examples of `robot_env`, `[range]`, `by_time` and the report.

## What isn't modeled

- **The hub's exits and the active/inactive timing.** A scored ball is counted and then parked and hidden; the hub's
  funnel and its internal exits and the game's hub-activation rules are not modeled. Every goal always scores.
- **Trenches on the full field.** The `2026` range has its four bumps ([docs/terrain.md](terrain.md)) but not the
  TRENCH structures: a robot drives through where a trench arm is. One TRENCH is in its own range, `2026-trench`,
  to drive under (below). Putting the trenches in `2026` would change every existing run on it (balls roll into
  them), so that waits for a change that updates those results on purpose.
- **Restocking over a match.** A station feeds the balls it started with (its `stock`, or at most 50) and then stops.
  A human player who gets balls back from the field and feeds them again isn't modeled; it comes up with match
  simulation.
- **Feeder loads on motors.** The rollers turn as if the balls weren't there.
- **Ball-to-ball compression.** Balls are rigid spheres with a soft contact; a ball does not squash in the hopper.
- **Squishy pieces.** The 2023 cube, the 2024 note and other soft pieces (inflated tubes, foam) are rigid stand-ins of
  the right size and weight: they don't squash or grip back. Soft pieces are a later project (survey project 5).
- **Shooting pieces that aren't balls.** The `[shooter]` shoots balls, discs and flat rigid pieces (a pinch shooter,
  the 2024 NOTE). A coral, a cone or a tote moves only by contact with the mechanism you test.
- **Calibration.** Nothing here is calibrated: the ball's stiffness, the wheel's friction, a piece's surface and a pad's grip, and the carpet's rolling
  resistance are estimates until you measure your own. See [measuring.md](measuring.md) for the ball, the wheel, a
  piece's surface and a pad (the carpet's rolling resistance has no test there yet).
