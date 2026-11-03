# Test range: shooting at last season's field elements

The test range puts your robot on a field with **goals** (last season's hub), **balls that are real bodies**, and a
**launcher, hopper, feeder and intake** that your own robot code drives. You write the robot code once; the sim
decides where each ball goes and whether it scores. It builds on the [launcher model](launcher.md): the wheel, the
hood and the ball's squeeze are that model, and the range adds a world around it.

> **The numbers are estimates until you measure your own balls and wheels.** The launcher model's results are
> labeled `UNCALIBRATED` for that reason, and so are the aiming tables made from it. Trust the trends (a bigger
> flywheel recovers faster) before the absolute numbers (7.4 m/s).

- [What you get](#what-you-get)
- [Putting a robot on the range](#putting-a-robot-on-the-range)
- [A complete example](#a-complete-example)
- [`[range]`](#range)
- [`[shooter]`](#shooter)
- [The launcher part](#the-launcher-part)
- [A disc shooter](#a-disc-shooter)
- [Lanes, stages, the hopper and the intake](#lanes-stages-the-hopper-and-the-intake)
- [Beam breaks](#beam-breaks)
- [Balls in the world](#balls-in-the-world)
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
| `file` | | required | a built-in range (`"2026"`, `"2013"`, `"2026-bump"`, `"2023-charge-station"`, `"terrain-library"`, `"2022-hangar"`, `"2020-switch"`, `"2013-pyramid"`, `"ladder"`, `"ladder-square"`, `"intake"` (one ball on the carpet in front of `[start.facing_ball]`, to try an intake on) or `"station"` (a human player station at the blue alliance wall that feeds balls down a short chute, `[start.at_station]` against it, to try a hopper or an intake on)) or a path to a [range file](#the-range-file), relative to the config. For 2013, `"2013"` is the disc range (shooting discs at the slot goals, the pyramid an obstacle) and `"2013-pyramid"` the climbing range (the pyramid to climb, no goals) |
| `start` | | none | a named start from the range file. The robot starts there. Give either `start` or `[robot] start_pose`, not both |
| `preload` | balls | 0 | balls already in the hopper at the start of a run. More than the robot holds (the hopper capacity) is an error |
| `floor_balls` | | `true` | `false`: no floor balls (the range's `[[floor_balls]]` are left out of the world entirely, so the world can't put them out later). Faster runs, and the right choice when the auto never touches them |
| `ball` | | the range's | a ball preset to use in place of the range's own (`"2017-fuel"`, `"2019-cargo"`, and so on: the names in [docs/launcher.md](launcher.md)). Only a range with a ball takes it (not a disc range). A range with a station refuses a ball that doesn't fit the station's chute (a chute with a set `width` must be wider than the ball). The Test tab's Setup sets this for a stand-in's `intake` and `station` fields |
| `station` | | none | changes a station's settings for this run: `station = { feeder = { interval = 0.5, stock = 8, spread_yaw_deg = 5.0 } }`. The keys are `interval`, `stock`, `spread_yaw_deg`, `spread_pitch_deg`, `spread_speed`, `spread_position` and `spread_rotation_deg`; any other key, or a station the range doesn't have, is an error. `stock = 0` means "never runs out" (at most 50 balls are fed in one run); a stock can be at most 200 |
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
- `scrimmage launcher aim` makes tables for ball launchers only; for a disc shooter use `scrimmage disc table`.

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
- **Sleeping.** The sim turns on MuJoCo's body sleeping (needs `mujoco>=3.4`, which the package requires), so the
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

**Launch clearance.** The launcher's own geometry doesn't collide, but the robot's frame and bumpers do, and an exit
point can sit inside them. A launched ball therefore **ignores its own robot for 0.15 s** (`LAUNCH_CLEAR_S` in
`scrimmage/testrange/launch.py`), then collides with everything. 0.15 s is about 1 m of flight at a slow 7 m/s shot,
past the frame of any robot. It still collides with the field, the hub and other balls from the start.

> **Known limitation:** a very slow shot from deep inside the robot can still be overlapping the robot when the
> 0.15 s are up. It is then thrown out by the contact solver.

## The range file

A range file describes a season's field elements: the ball, the goals, the floor balls, the starting positions,
the carpet and the elements robots drive over (bumps, ramps, a tilting platform: `[[element]]` tables, described in
[docs/terrain.md](terrain.md#field-elements-in-range-files)). SI units, angles in degrees (keys end in `_deg`), WPILib field coordinates (blue alliance wall at
x = 0, z up) on the generated field of 16.54 by 8.07 m. The built-in `2026` is
[`scrimmage/testrange/ranges/2026.toml`](../scrimmage/testrange/ranges/2026.toml); copy it to make your own and
point `[range] file` at the copy.

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
tilt_deg = 0.0             # 0 faces up, 90 faces -x (a side opening)
opening = { sides = 6, across_flats = 1.06, rotation_deg = 0.0 }
funnel = { depth = 0.395, slope_deg = 60.0 }
body = { size = [1.194, 1.194], height = 1.51 }

[[floor_balls]]
grid = { corner = [7.0, 4.0], step = [0.2, 0.2], count = [1, 8] }

[start.front]
pose = [3.0, 4.035, 0.0]   # x, y, heading in degrees
```

### `[[station]]`: a human player station

A station feeds balls onto the field from a release point, as a human player would: one every `interval` seconds, each
varying a little. It is built like the bench's `[[inlet]]` ([docs/bench.md](bench.md)), with limits where the inlet
has standard deviations. Stations feed balls only; a `[[station]]` in a disc range is an error.

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
| `spread_position` or `spread_position_in` | m or in | 0, 0, 0 | a box around the release point, three numbers (along x, y, z): each feed's release point moves by up to this much either way on each axis |
| `spread_rotation_deg` | degrees | 0, 0, 0 | how much each feed is turned about each axis. A round ball doesn't show it; the key is there for pieces that are not round |
| `chute` | | none | a sloped floor and two side walls (below) |

**Every spread is plus or minus its value.** `spread_yaw_deg = 5.0` means each ball goes up to 5 degrees left or 5
degrees right of the heading, and `spread_position = [0, 0.02, 0.02]` is 2 cm either way across and up. The draws are
uniform between those limits, not standard deviations, so a ball never leaves a tight chute's limits. A spread of 0 is
exact.

**The seed.** The draws come from `[range] seed` (default 1), one stream per station, so the same seed gives the same
feeds whatever the timing. A different seed is a different shuffle of the same spreads: the Test tab's **New shuffle**
changes it.

**The chute** is an optional sloped plate with a wall on each side, built into the world as the body
`element.<station>.chute`. `length` runs along the slope, `slope_deg` is below horizontal, and `width` is the space
between the walls (leave it out for 1.3 ball diameters, so a ball of any preset fits). The floor's top starts
beneath the release point and runs down along the heading; its walls stand one ball diameter tall. The chute is
refused if its low end would go below the carpet, or it reaches off the field or into an element, an obstacle,
another station's chute or a start's robot outline (the check uses each chute part's base, so the wall tops leaning out about
a ball diameter times the sine of the slope over the low end are not counted).

**A feed waits** while another ball on the field is within one ball diameter of the release point, so balls don't
land on each other. If the release point stays blocked, a test's **feed one** block times out and says so.

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
- **Tilt.** `tilt_deg` turns the opening about the goal's y axis: the entry normal goes from +z (0, facing up) to -x
  (90, facing back toward the goal's own side).
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
  too close (overlapping), or off the field, are errors.
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
- **Elements** are `[[element]]` tables: see [docs/terrain.md](terrain.md#field-elements-in-range-files). A start
  whose robot outline overlaps an element, or a floor ball resting inside one, is an error.

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
  table (the row is left out), and a warning that gives the reachable range. If none of the requested distances is
  reachable the command is an error. Ask for 1 to 6 m and you get the reachable part.
- **Rows follow the previous row.** Several wheel speeds and hood angles can reach one distance. The first row takes
  the steepest solution; each later row takes the solution **closest to the row before it** (wheel speed and hood angle
  together), so the table is smooth to interpolate between.
- `flight_s` is the time from the exit to the rim-height crossing; the robot code can use it to lead a moving shot.
- A table takes several minutes for a long list of distances.

Robot code should **hold fire beyond the table's reach** rather than clamp to its last row; the sample does.

## The fast-mode sample's autos

[`examples/java_fastmode`](../examples/java_fastmode) has a shooter, a generated `AimTable.java` and autos picked
by `SCRIMMAGE_AUTO`; they run on `configs/fastmode_range.toml` (fast mode) and `configs/fastmode_range_phoenix.toml`
(Phoenix mode). See [Shooting on the test range](fast-mode.md#shooting-on-the-test-range). The scenario files
`scenarios/fastmode_shoot_*.toml` are working examples of `robot_env`, `[range]`, `by_time` and the report.

## What isn't modeled

- **No turret.** A swerve robot aims by rotating the whole robot; the launcher shoots along the robot's x.
- **The hub's exits and the active/inactive timing.** A scored ball is counted and then parked and hidden; the hub's
  funnel and its internal exits and the game's hub-activation rules are not modeled. Every goal always scores.
- **Trenches.** The 2026 range has its four bumps ([docs/terrain.md](terrain.md)) but not the TRENCH structures:
  a robot drives through where a trench arm is.
- **Restocking over a match.** A station feeds the balls it started with (its `stock`, or at most 50) and then stops.
  A human player who gets balls back from the field and feeds them again isn't modeled; it comes up with match
  simulation.
- **Feeder loads on motors.** The rollers turn as if the balls weren't there.
- **Ball-to-ball compression.** Balls are rigid spheres with a soft contact; a ball does not squash in the hopper.
- **Calibration.** Nothing here is calibrated: the ball's stiffness, the wheel's friction and the carpet's rolling
  resistance are estimates until you measure your own. See [Measure these first](launcher.md#measure-these-first).
