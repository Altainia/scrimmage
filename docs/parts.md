# Parts and the assembler

Scrimmage builds your robot from **parts**: a chassis, swerve modules, an elevator, an arm, an intake. Each
part is a small mechanism with joints, motor and encoder slots, and mount points. You list the parts in your
robot config, say where each one attaches and which device ID goes in each slot, and Scrimmage assembles them
into one MuJoCo model and binds your robot program's devices to it. You never write MJCF.

This page is the reference. For a first robot, start with "Building a robot from parts" in the
[README](../README.md). The page is also the contract for anything that writes `part.toml`, such as a CAD
exporter: if a file follows this page, Scrimmage loads it.

There are two kinds of part:

- **Stock parts** are built into Scrimmage and take parameters (`stock = "elevator"`, `travel = 1.2`). The
  list is [below](#stock-parts).
- **Folder parts** are a folder with a `part.toml` (and meshes next to it). You write them, or a tool does
  (`path = "../parts/intake"`).

If you have never used TOML: it is a plain text format of `key = value` lines. Numbers are written as `0.5`,
text in quotes (`"x"`), lists in brackets (`[0, 0, 0.1]`), true/false in lower case. `[[name]]` starts one
entry of a list, and `#` starts a comment. Mistakes are reported with the file, the entry and the key.

A CAD exporter exists: the [FreeCAD add-on](freecad.md) turns an Assembly into a folder part.

Units everywhere in a config and in `part.toml`: **meters, kilograms, seconds, degrees**. Joints rotate about
their axis (degrees in files) or slide along it (meters). Coordinates are x forward, y left, z up.

**From CAD.** You don't have to write a `part.toml` by hand. The Part tab (`scrimmage edit my_robot.step`; see
[the Part tab](part-tab.md)) turns a STEP, STL or OBJ export into part folders in this format, and its choices are
kept in a `<name>.import.toml` next to them. For a part folder made by a CAD plugin, edits made in the Part tab (weight,
joint range, spring, damping, friction, collision, mounts) go in `scrimmage-edits.toml` inside the folder, never in
`part.toml`. Scrimmage applies that file when it loads the part; `part.toml` stays format 1.

**Grip.** A body's pads can be given a grip material for testing a grabber ("would grip tape help?"). The overlay's body
table has a `grip` key, and a config's `[[part]]` has `grip` for the same thing:

```toml
# parts/claw/scrimmage-edits.toml (written by the Part tab's and the Test tab's Grip picker)
[body.pad_left]
grip = "nitrile"         # a material: rubber, nitrile, grip-tape, polycarbonate, aluminum, hdpe; or a number

# the robot config: overrides the folder's grip for the bodies it lists
[[part]]
name = "claw"
path = "parts/claw"
grip = { pad_left = "grip-tape", pad_right = 1.05 }    # 1.05: a friction you measured
```

Every colliding geom of the body (a wheel's tread is left alone) gets the material's sliding friction, a firm contact and
contact priority 1, so the pad's friction, not a game piece's, is the one that applies. That contact also replaces a
ball's own tuned contact (its bounce and rolling) against that pad. A pad with no grip keeps its geoms' `friction`, or
MuJoCo's default 1.0 (about grip tape's) when `part.toml` sets none, and then the larger of the pad's and the piece's
friction applies; a run says which (an UNCALIBRATED estimate). A number must be above 0 and at
most 3. A name that isn't a material, a body the part doesn't have, or a stock part is an error that names the file and the
body; a body with no geom that touches anything is a warning. The friction values (rubber and nitrile 0.8, grip tape
1.1, polycarbonate 0.4, aluminum 0.35, HDPE / UHMW 0.2) are estimates, **UNCALIBRATED**: see
[Grip material](test-tab.md#grip-material) for how to measure your own. Test files don't set grip.

## Putting parts in a robot config

A robot config lists its parts as `[[part]]` entries, next to the usual `[robot]`, `[battery]`,
`[robot_program]` sections. `configs/kitbot.toml`, `configs/swerve.toml` and `configs/elevator_arm.toml` are
complete examples.

```toml
[[part]]
name = "drive"                 # your name for this instance
stock = "chassis"              # a stock part ...
length = 0.66                  # ... and its parameters (see "Stock parts")
ids = { gyro = "Pigeon2:13" }  # a device ID for each slot

[[part]]
name = "intake"
path = "../parts/intake"       # ... or a folder holding part.toml, relative to this config file
attach = "drive.top"           # <instance>.<mount or body>
pos = [0.4, 0, 0]              # offset from that mount, in the mount's frame (meters)
rot = [0, 0, 0]                # roll, pitch, yaw offset (degrees)
ids = { roller = "TalonFX:5" }
```

| Key | Meaning |
|---|---|
| `name` | Required. Letters, digits and `_`, unique in the config. It becomes the prefix of everything the part contributes: bodies and joints are `<name>.<body>` and `<name>.<joint>` (`dt.left_2`, `fl.steer`), and bindings are `<name>.<slot>`. Scenario signals and `get_state` use these names. |
| `stock` / `path` | Exactly one of them. `path` is a folder with a `part.toml`, or the `part.toml` file itself, relative to the config file. |
| other keys | For a `stock` part, its parameters. A `path` part has none: any other key is an error. |
| `attach` | `"<instance>.<mount>"` or `"<instance>.<body>"` of another part (a mount name wins over a body name). Leave it out on exactly one part, the root. |
| `pos`, `rot` | Offset of the part's root body from what it attaches to. `pos` in meters, `rot` as roll, pitch, yaw in degrees (rotations about the fixed x, y, z axes, applied in that order). Both default to zero. |
| `ids` | A table of `slot = device`. See "Device IDs". |
| `grip` | A `path` part only: `grip = { pad_left = "grip-tape", pad_right = 1.05 }`, a grip material or a measured friction for some of the folder's bodies. It overrides the folder's own (`scrimmage-edits.toml`, below). See "Grip". A `stock` part with `grip` is an error. |

**The root part.** The part without `attach` is the robot's root. It gets the free joint (`<name>.free`), and
`[robot] start_pose = [x, y, heading_deg]` places it; without one a parts robot spawns at `(2.0, 4.035, 0)`
(inside the field, facing +x). The root's `pos` is measured in the start-pose frame,
and `rot` is applied below it. Scenario checks (`pose`, `speed`) track the root's first body. Every other
part attaches, directly or through other parts, to the root. A chain like `chassis`, `elevator`, `arm` is
fine. An attach loop, or a part attached to a name that doesn't exist, is an error that names the parts involved.

**Placement.** A part attaches to a *mount* (a named frame on one of the target's bodies, see below) or
directly to a body's origin. Its `pos` and `rot` are expressed in that frame. Parts attach rigidly: the
joints inside a part are the only moving ones.

**Robot geometry.** The stock chassis origin is on the floor (z = 0) at the middle of the frame, and its
corner mounts (`front_left`, `front_right`, `back_left`, `back_right`) are at the module centers, on the
floor. A module attached at a corner mount therefore sits with its wheel on the carpet.

**Collisions.** Robot geoms collide with the field (carpet, walls, the `obstacle`), never with each other.
That is why parts can overlap without fighting. A geom with `collide = false` doesn't touch the field either. The
stock mechanisms (an elevator, an arm, a launcher...) touch nothing unless the part sets `collide = true`: see
[Stock parts that collide](#stock-parts-that-collide).
The field geoms are named `carpet`, `wall_blue`, `wall_red`, `wall_right`, `wall_left` and `obstacle`, which
is what `must_touch` and `never_touch` take. `[field] obstacle = false` removes the obstacle. Under the
hood, robot geoms use MuJoCo contact bits `contype = 2, conaffinity = 1` and the field uses `1` and `2`.

**Physics settings.** The model uses the `implicitfast` integrator, elliptic friction cones and a 1 ms
timestep (`[model] timestep`). Motor back-EMF is integrated implicitly as joint damping, so even
high-gearing mechanisms are stable. When joints are coupled by joint equalities, the back-EMF damping is split
across the coupled joints.

### Device IDs

Each slot in a part (listed by `scrimmage parts <name>` and by the `robot_parts` MCP tool) takes a device.

| Form | Slot kind | Becomes |
|---|---|---|
| `"PWM:3"` | motor | `[[motor]] pwm = 3` |
| `"TalonFX:3"` | motor | `[[motor]] ctre = "TalonFX:3"` (Phoenix 6) |
| `"DIO:4"` | encoder | `[[encoder]] channel_a = 4` (the first DIO channel of a quadrature `Encoder(4, 5)`) |
| `"CANcoder:9"` | encoder | `[[encoder]] ctre = "CANcoder:9"` |
| `"Pigeon2:13"` | gyro | `[[gyro]] ctre = "Pigeon2:13"` |
| `"SparkMax:3"`, `"SparkFlex:3"` | motor | `[[motor]] rev = "SparkMax:3"` (REV; see [rev.md](rev.md)) |
| `"SparkMaxAbs:3"`, `"SparkFlexAbs:3"` | encoder | `[[encoder]] rev = "SparkMaxAbs:3"`, the absolute encoder on that SPARK's data port |
| `{ ... }` | any | a table of binding keys for anything else |
| `["TalonFX:3", "TalonFX:4"]` | motor | a list, one ID per motor, for slots with `count` motors |
| `"PCM:3"`, `"PCM:3,4"` | valve | a single solenoid on channel 3, or a double solenoid on channels 3 and 4, of CTRE PCM 0 (see [Valve IDs](#valve-ids)) |
| `"PH:3"`, `"PH:3,4"` | valve | the same on REV Pneumatic Hub 1 |

A slot's kind must match: `"TalonFX:3"` in an encoder slot is an error.

**REV SPARKs.** A REV motor slot also takes a table: `{ rev = "SparkMax:3", neutral = "brake", invert = false,
limits = false }`. `neutral` defaults to `"brake"` (REV's factory idle mode). `limits = true` presses the SPARK's
forward limit at the upper end of the joint's range and the reverse limit at the lower end, whatever the SPARK's own
inversion. An absolute-encoder table takes `offset` (where the magnet sits, in rotations): `{ rev =
"SparkMaxAbs:3", offset = 0.25 }`. A SPARK MAX motor defaults to a NEO and a SPARK Flex to a NEO Vortex. See
[rev.md](rev.md) for the details. A table holds the binding keys of
`[[motor]]`, `[[encoder]]` or `[[gyro]]` in the README's [Bindings](../README.md#bindings) section, for example
`{ can = "VictorSPX[3]" }`, `{ pwm = 0, neutral = "coast" }`, `{ channel_a = 0, counts_per_rev = 2048 }` or
`{ device = "ADXRS450[0]", axis = "x", cw_positive = true }`, plus extras such as `neutral = "brake"` on a
Phoenix motor. Keys that come from the part (`joint`, `body`, `motor`, `count`, `gear_ratio`, `ratio`, `name`,
`efficiency`) can't be set there. An `invert = true` in the table flips the slot's own invert (two inversions
cancel).

A list gives each motor its own binding (`<name>.<slot>[0]`, `<name>.<slot>[1]`), each driving the same joint
with `count = 1`, so two Kraken X60s on a lift can be two separate CAN devices. The list length must equal the
slot's motor count.

**Warnings.** A slot with no ID is allowed, because you may bind only some devices. It produces a warning,
such as `dt.left has no device ID, so it won't move`. Warnings appear in the `serve`/`run` terminal log
at start, in a session's `status()` (`session_status` over MCP) as `warnings`, and as notes in the scenario
report. An ID for a slot the part doesn't have is an error that lists the part's slots.

### Valve IDs

A cylinder's `valve` slot takes a solenoid on a CTRE PCM or a REV Pneumatic Hub. The short forms use WPILib's
default module numbers: `"PCM:3"` is channel 3 of PCM 0, and `"PH:3"` is channel 3 of Pneumatic Hub 1. Two channels,
`"PCM:3,4"`, make a double solenoid (forward, then reverse). Any other module, or an inverted valve, takes a table:
`{ pcm = 1, channel = 3 }` or `{ ph = 2, forward = 3, reverse = 4, invert = true }`.

| Key | Meaning |
|---|---|
| `pcm` / `ph` | Exactly one of them: the module's CAN number, 0 to 62. |
| `channel` | A single solenoid's channel (a spring-return valve: on extends, off retracts). |
| `forward`, `reverse` | A double solenoid's two channels, in place of `channel`: forward extends, reverse retracts, neither leaves it where it is. They must differ. |
| `invert` | `true` swaps extend and retract (a cylinder plumbed the other way round). Default `false`. |

A PCM's channels are 0 to 7 and a Pneumatic Hub's 0 to 15. All of one robot's valves must be on one kind of module
(all PCM or all PH); a robot with both is an error that names a valve on each. Valves on a Pneumatic Hub need the
lockstep extension 0.7.0 or later. Robot code for each form, and what each valve state does, are in
[pneumatics.md](pneumatics.md#robot-code). Under the Test tab a valve needs no ID.

### The air system: `[pneumatics]`

A robot with cylinders has one air system: tanks, a compressor, a pressure switch and a regulator. A config with
cylinders and no `[pneumatics]` section gets the defaults. Pressures are in psi (gauge), the one exception to
Scrimmage's metric units, since every FRC pneumatic part and rule uses psi. Every default is an estimate
(UNCALIBRATED); [pneumatics.md](pneumatics.md#the-numbers-and-where-they-come-from) gives each one's source.

```toml
[pneumatics]
tanks = 2
tank_ml = 574
compressor = "VIAIR 90C"
working_psi = 60
start_psi = 120
switch_on_psi = 95
switch_off_psi = 115
```

| Key | Default | Meaning |
|---|---|---|
| `tanks` | `2` | Number of tanks on the stored side, 1 or more. |
| `tank_ml` | `574` | Each tank's volume in milliliters (the Clippard AVT-PP-35 FRC tank). |
| `compressor` | `"VIAIR 90C"` | The compressor's flow and current curve, or `"none"` for tanks only. |
| `working_psi` | `60` | The regulator's pressure, what the cylinders get. At most 60 (the FRC rule). |
| `start_psi` | `120` | The tanks' pressure when a run starts (charged before the match). At most 120 (the FRC rule). |
| `switch_on_psi` | `95` | The pressure switch turns the compressor on below this. |
| `switch_off_psi` | `115` | ... and off at this. At most 120, and above `switch_on_psi`. |

An unknown key is an error that lists these. `scrimmage air <config>` gives the robot's air budget: see
[pneumatics.md](pneumatics.md#the-air-budget-scrimmage-air).

### Sensors: `[[sensor]]`

Beam breaks, distance sensors and REV Color Sensor V3s that see game pieces are `[[sensor]]` tables in the robot
config, not part slots. Each is mounted on one of the robot's bodies by name (`body = "intake.hopper"`), so they work
on stock parts and CAD parts alike, with no change to the part:

```toml
[[sensor]]
name = "bin"
kind = "beam"                 # "beam", "distance" or "color"
body = "intake.hopper"        # "<part>.<body>"
from = [0.12, 0.17, 0.08]     # the beam's two ends, m in the body's frame
to = [0.12, -0.17, 0.08]
dio = 4                       # the robot code's DigitalInput
```

Every key, what each kind reads and the robot code are in [sensors.md](sensors.md).

### Inspecting a robot

```bash
scrimmage parts                       # the stock parts, one line each
scrimmage parts elevator              # one stock part: parameters, defaults, slots, mounts
scrimmage model configs/kitbot.toml            # print the MJCF the parts assemble to
scrimmage model configs/kitbot.toml -o kitbot.xml
```

`scrimmage model` prints a snapshot you can open in any MuJoCo viewer. It fails for a hand-written
`[model] mjcf` robot. In an agent session, the `robot_parts` MCP tool returns each instance with its source,
attach point, slots (kind and device ID), mounts and prefixed joint names, and the same warnings are
reported.

## part.toml reference

A part is a tree of **bodies** (rigid pieces, each optionally hanging on a joint), with **couplings** between
joints, **slots** where devices attach, and **mounts** where other parts attach. Unknown keys are errors,
so a typo can't silently do nothing.

### `[part]`

```toml
[part]
name = "intake"
format = 1
```

| Key | Default | Meaning |
|---|---|---|
| `name` | required | Non-empty text. Used in messages. |
| `format` | `1` | An integer, 1 or more. Format version of the file. Files with a newer format than your Scrimmage are rejected with "upgrade Scrimmage"; this is how the format can grow. |

### `[[body]]`

A part needs at least one body. Bodies are listed parents first. The first body is the **root**: it has no `parent` and no `joint`, and it
moves with whatever the part attaches to.

| Key | Default | Meaning |
|---|---|---|
| `name` | required | Unique in the part. |
| `parent` | none | Name of an *earlier* body. Required on every body but the first. |
| `mass` | required | kg, greater than 0. See "Mass" below. |
| `pos` | `[0, 0, 0]` | m, the body's origin in its parent's frame. |
| `rot` | `[0, 0, 0]` | Roll, pitch, yaw in degrees, relative to the parent. |
| `geom` | `[]` | A list of geoms: `geom = [{ box = [0.3, 0.4, 0.05] }, ...]`. |
| `joint` | none | The joint that connects this body to its parent, as an inline table. Not on the root. |
| `com` | none | `[x, y, z]` center of mass in the body's frame, meters. |
| `inertia` | none | `[ixx, iyy, izz]` diagonal inertia about `com`, kg*m^2, each greater than 0. Needs `com`. The diagonal is along the inertia axes, which are the body's axes unless `inertia_rot` turns them. |
| `inertia_rot` | none | `[roll, pitch, yaw]` degrees: the orientation of the inertia axes in the body's frame (same convention as `rot`). Needs `inertia`. An exporter holding a full tensor writes its principal moments as `inertia` and the principal axes here. |

**Mass.** What the assembler does with `mass`:

- With `com`: the mass is placed at `com` with the given `inertia`. If you give `com` but no `inertia`, it uses
  a small solid sphere of 5 cm radius, `0.4 * m * 0.05^2`. Geoms then carry no mass at all, they only draw
  and collide.
- Without `com`: the mass is spread over the body's geoms in proportion to their volume (uniform density,
  including geoms with `collide = false`), and MuJoCo computes the center and inertia from them. Meshes use
  their own volume.
- A body with no geoms (or none with any volume) and no `com`: the whole mass at the body origin with the same
  small inertia.

Inertia matters most for the parts that turn: give a spinning wheel its real moment of inertia with `com` and
`inertia`, as the stock flywheel does.

### Geoms

A geom is an inline table with exactly one shape key:

| Shape | Value |
|---|---|
| `box` | `[x, y, z]` full sizes (not half), meters. |
| `cylinder` | `[radius, length]`, meters. Its axis is `axis`. |
| `sphere` | radius, meters. |
| `capsule` | `[radius, length]`, meters: a cylinder with round ends, `length` between the ends' centers, along the geom's z (turn it with `rot`). MuJoCo's capsule contacts are exact, so use it for hooks and pipes. |
| `mesh` | path to an STL or OBJ file, relative to the folder holding `part.toml`. The file must exist. (Meshes aren't available to stock parts.) Vertex coordinates are meters in the body's frame; there is no scale key, so a CAD exporter converts from mm. |

Other keys:

| Key | Default | Meaning |
|---|---|---|
| `axis` | `"z"` | Cylinders only: `"x"`, `"y"` or `"z"`. A wheel on a `y` axle is `axis = "y"`. On any other shape it is an error ("axis is for cylinders only; turn other shapes with rot"): use `rot`. |
| `pos`, `rot` | zero | The geom's offset in its body's frame (m, degrees). |
| `collide` | `true` | `false` makes a geom visual only: it touches nothing. Use it for everything but the parts that really touch the field (wheels, bumpers). (This is a part.toml geom's key. A stock part has one `collide` setting for the whole part: see [Stock parts that collide](#stock-parts-that-collide).) |
| `friction` | MuJoCo's 1.0 | Sliding friction coefficient, greater than 0. (Torsional and rolling friction are fixed small values.) |
| `color` | gray | `[r, g, b, a]`, each 0 to 1. |
| `condim` | MuJoCo's 3 | Contact dimensions: `1`, `3`, `4` (adds torsional friction, good for wheels) or `6`. |
| `solref` | MuJoCo's default | `[timeconst, dampratio]`, the contact softness. On a `tread` geom it replaces the tread's solref, but the geom keeps solmix 1, so it is still blended 50/50 with the carpet's. |
| `name` | none | The geom's name in the model, after the instance's prefix (`<part>.<name>`): letters, digits, `_` and `.`, unique in the part. The climber names its hook geoms so signals can find them. Not on a `tread` geom (it is named `<body>.tread<i>`). |
| `priority` | `0` | MuJoCo's geom priority, a whole number. In a contact, the higher-priority geom's `friction`, `condim` and `solref` are used instead of mixing the two (MuJoCo takes the larger friction otherwise). Field geoms are 0; the climber's hooks are 1, so a hook's material sets its friction on a bar. |
| `tread` | `false` | `true` marks a drive wheel's tread: the geom touching the ground that drives the robot. Its contact is a spring of the drivetrain's `tread_stiffness` (N/m per wheel) and `tread_damping` (damping ratio), so a wheel carrying a load F rests sunk F / stiffness into the carpet. A part.toml geom uses the stock defaults, 2e5 N/m and 1.0. The stock swerve modules and differential mark their wheels. A tread geom must collide. |

Box sizes and cylinder radius and length must be greater than 0.

Keep collision shapes simple: boxes and cylinders for contact, a CAD mesh with `collide = false` for looks.

### Joints

`joint` is an inline table on a body, `joint = { name = "pivot", hinge = [0, -1, 0], range = [-45, 110] }`:

| Key | Default | Meaning |
|---|---|---|
| `name` | required | Unique in the part. Prefixed with the instance name in the robot. |
| `hinge` or `slide` | exactly one | The motion, and its axis: `"x"`, `"y"`, `"z"` or an `[x, y, z]` vector in the body's frame (normalized). A hinge turns about it (positive is right-handed). A slide moves along it. |
| `range` | unlimited | `[min, max]`, `min < max`. Degrees for a hinge, meters for a slide. Measured from the body's `pos`/`rot` position. For a limit on one side only, write a far value on the other: the FreeCAD exporter writes `-1e6` or `1e6`, meaning no limit on that side. |
| `damping` | `0` | Viscous damping: N*m*s/rad (hinge) or N*s/m (slide). |
| `frictionloss` | `0` | Constant dry friction: N*m or N. |
| `armature` | `0` | Added rotor/drivetrain inertia seen at the joint: kg*m^2 or kg. Stock modules put the geared rotor's inertia here. |
| `spring` | none | `{ stiffness = 5.0, rest = 30 }`: a spring pulling to `rest` (degrees or meters; default 0), with stiffness (0 or more) in N*m/rad or N/m. |
| `limit_solref` | MuJoCo's default | `[timeconst, dampratio]`: the softness of the `range` stops (needs a `range`). MuJoCo softens a stop by the inertia it holds, so a light body on a loaded stop needs a short time constant (and some `armature`) to stay put. The Part tab's **Stops** picker writes `[0.004, 1.0]` (Stiff) or `[0.05, 1.0]` (Soft), or leaves it out (Normal). |

A joint reads zero where the body sits at its `pos` and `rot`, and encoders count from there.

### `[[loop]]`

```toml
[[loop]]
body1 = "coupler"
body2 = "rocker"
pos = [0.3, 0, 0]
```

A pin that closes a kinematic loop, such as a four-bar linkage. Bodies form a tree, so a loop is the one extra
constraint: the point `pos` (meters, in `body1`'s frame) is held together with the same point of `body2`. The
point is the one where the two bodies meet when every joint is at zero, so the part must be built closed at its
zero pose. The tree joints plus the pin keep a planar linkage's motion; use it for four-bars and similar.

| Key | Meaning |
|---|---|
| `body1` | Required: a body of this part. |
| `body2` | Required: a different body of this part. |
| `pos` | Required: `[x, y, z]` meters in `body1`'s frame. |

### `[[coupling]]`

```toml
[[coupling]]
joints = ["stage1", "carriage"]
ratio = 1.0
```

Forces `b = ratio * a` between two joints of the part, where `joints = ["a", "b"]`.

| Key | Meaning |
|---|---|
| `joints` | Required: two joint names of this part, `["a", "b"]`. |
| `ratio` | Required: a number, not 0. |

 The values are in joint
units (radians for hinges, meters for slides), so `ratio` is not affected by whether a joint is in degrees in
the file. `ratio` can't be 0. The stock differential chains its wheels this way, and the stock elevator links
its stages. One of the pair is the driven joint (the one a motor slot names).

### Slots

Slots are the places where devices bind. Slot names are unique across all kinds in a part. An instance gives
each slot a device ID in `ids`.

`[[motor]]`:

| Key | Default | Meaning |
|---|---|---|
| `slot` | required | Name for `ids`. |
| `joint` | required | The joint it drives. |
| `motor` | required | A motor model, e.g. `"KrakenX60"`, `"NEO"`, `"CIM"` (names are in the README's [Bindings](../README.md#bindings) section and `scrimmage/motors.py`). |
| `count` | `1` | Number of motors sharing the slot (a whole number, 1 or more). |
| `gearing` | `1.0` | **Motor radians per joint unit**: radians of motor shaft per radian of a hinge, or per meter of a slide. For a hinge, this is the gear ratio (10:1 reduction = `10`). For a slide on a spool of radius `r`, it is `gear_ratio / r`. Greater than 0. |
| `invert` | `false` | Positive output drives the joint negative. |
| `efficiency` | `1.0` | Drivetrain efficiency, greater than 0. |

`[[encoder]]`:

| Key | Default | Meaning |
|---|---|---|
| `slot` | required | Name for `ids`. |
| `joint` | required | The joint it measures. |
| `ratio` | `1.0` | **Sensor turns per joint unit**: turns of the sensor per turn of a hinge, or per meter of a slide. An encoder on the joint itself is `1.0`; for a slide on a spool of radius `r` it is `1 / (2 * pi * r)`. Can't be 0. |
| `invert` | `false` | Report the opposite sign. |

`[[gyro]]`: `slot` and `body` (both required; the body must exist in the part). The gyro reads the yaw of that body.

#### Gyro bindings

A gyro slot's ID is a table of binding keys. For a SimDevice gyro, `device` is its name and `axis` picks the value
names (`angle_<axis>` and `rate_<axis>`, as WPILib's ADXRS450 uses). Devices with other value names take `angle` and
`rate` to name them outright. WPILib's `ADIS16470_IMU` is
`{ device = "ADIS16470[0]", angle = "gyro_angle_z", rate = "gyro_rate_z", cw_positive = false }`.

### `[[mount]]`

```toml
[[mount]]
name = "top"
body = "frame"
pos = [0, 0, 0.025]
```

A named frame on a body, for other parts to attach to. `name` and `body` (required; the body must exist in the part), `pos` and `rot` (the frame
relative to the body; default zero). Another part's `pos` and `rot` are measured in the mount's frame.
Mount names are unique in the part. A body is also a valid attach target without a mount (at its origin).

### Writing and reading

Scrimmage reads `part.toml` with Python's `tomllib`, so any valid TOML works. Its own writer
(`scrimmage.parts.model.dump_part`) writes a canonical layout: `[part]` first, then one `[[body]]`,
`[[loop]]`, `[[coupling]]`, `[[motor]]`, `[[encoder]]`, `[[gyro]]`, `[[mount]]` table per entry, one key per line, geoms
and joints as inline tables, and **keys at their defaults left out** (except that `[part]` always has `format`, and every `[[body]]` always has `geom`, even if empty). A file written by it and read back is
identical. Floats are written in shortest round-trip form and strings as double-quoted text. Output shaped
like the golden example below is always safe.

### A complete example

`tests/parts/intake/part.toml`, an intake: a frame with a roller on a motor, and a mount on top.

```toml
[part]
name = "intake"
format = 1

[[body]]
name = "frame"
mass = 1.5
geom = [{ box = [0.3, 0.4, 0.05] }]

[[body]]
name = "roller"
parent = "frame"
pos = [0.15, 0, 0]
mass = 0.3
geom = [{ cylinder = [0.025, 0.4], axis = "y", collide = false }]
joint = { name = "roller", hinge = "y", damping = 0.001 }

[[motor]]
slot = "roller"
joint = "roller"
motor = "KrakenX60"
gearing = 3

[[mount]]
name = "top"
body = "frame"
pos = [0, 0, 0.025]
```

Used in a robot config, on the front of a chassis:

```toml
[[part]]
name = "intake"
path = "../parts/intake"         # the folder holding this part.toml
attach = "drive.center"
pos = [0.45, 0, 0.1]             # in the chassis's frame
ids = { roller = "TalonFX:5" }
```

This gives joint `intake.roller` and a motor binding `intake.roller`. A scenario can then check
`joint.intake.roller.velocity` or `motor.intake.roller.current`.

## Mechanism notes

Things worth knowing when you set the stock parts' parameters, or model the same mechanisms yourself:

- **Elevator rigging.** `continuous` moves the carriage at the rope speed, with the full force at the
  carriage. `cascade` moves it at `stages` x the rope speed with 1/`stages` of the force, so in cascade the
  carriage height is spool turns x 2 pi r x `stages`. The encoder counts spool turns. `friction` is in
  newtons felt at the carriage. Stages are joints named `stage1`, `stage2`, ..., `carriage`, coupled so they
  move together.
- **Arm.** Positive raises the tip, and 0 degrees is horizontal pointing forward.
- **Climber.** The one stock mechanism whose geoms always collide: tubes, hooks and latches touch the field's bars
  (the other mechanisms touch nothing unless they set `collide = true`, so by default an elevator passes through a
  bar). The telescope is the elevator's slide
  chain (joints `stage1`, ..., `tip`). Each hook is an open J of capsules opening toward +x, its mouth below: hang
  the robot on the crown, and let go by unloading the hook, lifting its tip clear of the bar and moving it off. A
  latch is a sprung finger across the mouth that a bar entering pushes open and that shuts behind it; with
  `latch = "released"` the `release` motor opens it. Hook friction comes from `hook_material`, and the hooks'
  priority makes it (and the hook's contact softness) govern a hook-bar contact.
- **Swerve modules.** `steer_invert` defaults to true for MK4i and MK4n, matching Tuner X's
  `kSteerMotorInverted`. Set `drive_invert = true` on the right-side modules to match Tuner X's
  `kInvertRightSide`. `ratio` takes a named ratio (`"L2"`) or a number, and `steer_ratio` overrides the
  steer reduction. The drive-steer coupling (the steer rotation turning the drive wheel through the bevel
  gear) isn't modeled.
- **MAXSwerve.** Bind the drive and steer slots with `SparkMax:<id>` (or `SparkFlex:<id>`) and the encoder slot,
  the steer SPARK's absolute encoder, with `SparkMaxAbs:<id>` (or `SparkFlexAbs:<id>`). `ratio` names the pinion
  teeth (12T, 13T, 14T: 45·22/(teeth·15), about 5.50, 5.08 and 4.71); the default is 14T (4.71), which REV's
  MAXSwerve template uses. The encoder slot is inverted whatever it is bound to: it reads the module
  clockwise-positive, like the Through Bore encoder on the steer SPARK, which robot code inverts as REV's template
  does. `scrimmage edit --new swerve-rev` starts a robot with the template's four modules and CAN IDs.
- **Turret.** A platform on a vertical hinge (joint `turn`): 0 faces the part's +x, and positive turns left
  (counter-clockwise seen from above). `range` is where its hard stops are (the wires' limit), each end within ±360°,
  so a turret with a slip ring is a wide range. Attach the launcher to `turret.top`: the shots leave from the launcher,
  so they go where the turret points. The Test tab's **aim** block points it at a goal (see
  [the Test tab](test-tab.md#aiming)). `scrimmage launcher aim` measures its table with the turret at 0, which is
  close enough while the turret is near the robot's middle. The 60:1 gearing on one Kraken X60 is an estimate typical
  of 2022 and 2026 turrets.
- **Cylinder.** A double-acting air cylinder. Its barrel lies along the part's +x and its rod pushes out toward +x.
  It has two forms:
  - **Free** (`push` empty): the cylinder has its own rod, on a slide, with a `rod` mount at its end. Attach a pusher,
    a kicker's plate or an intake's slide to `<name>.rod`.
  - **Push** (`push = "<part>.<body>"`): the rod's end is pinned to another part's body at `push_pos` (in that body's
    frame), and the cylinder drives whatever joints lie between its two ends: a stock arm raised by a piston
    (`push = "arm.arm"`), a CAD claw's jaw, a wrist. Its retracted length is `length`, or by default the distance
    between its ends as assembled; the assembled distance must lie between the retracted and extended lengths, or the
    error gives both. Its own barrel is drawn along the part's +x; the Test tab's replay draws the real cylinder
    between its two pins.

  In both forms the force acts along the line between the two ends, and the rod stops at both ends of its stroke. The
  size, friction, masses and valve flow are estimates (UNCALIBRATED): see [pneumatics.md](pneumatics.md). A cylinder
  is a stock part only: a `part.toml` can't have one, so put a stock cylinder next to your CAD part and pin it with
  `push`.
- **Differential.** The right side is mirrored: its motor and encoder are inverted, as on a real robot, so robot
  code that doesn't call `setInverted` drives the sides in opposite directions.

## Stock parts that collide

A stock elevator, arm, turret, cylinder, flywheel, roller, launcher or disc shooter is built with `collide = false`:
it has mass and joints, but its geoms touch nothing, so it passes through a TRENCH arm or a bar. Set `collide = true`
on the part to make it touch the field, game pieces and a [partner robot](test-tab.md#a-partner-robot), like a folder
part's geoms. It still never touches the robot's own parts. In the Robot tab it is a checkbox in the part's stock
form. A partner hanging from a chain ([README](../README.md#a-partner-robot), `[partner] hang = { hold, along,
across }`) is touched the same way; the chain itself never touches it.

```toml
[[part]]
name = "elevator"
stock = "elevator"
attach = "drive.center"
collide = true          # it can hit the trench
```

| key | default | meaning |
|---|---|---|
| `collide` | `false` | `true`: every geom of the part touches the field, game pieces and a partner robot, except the drawing-only ones below |

Which geoms collide with `collide = true`:

| part | geoms that collide | drawn only |
|---|---|---|
| `elevator` | every stage | |
| `arm` | the pivot block and the arm | |
| `turret` | the base and the platform | |
| `cylinder` | the barrel and the rod (the free form) | a push cylinder's barrel: a cylinder with `push` set touches nothing, even with `collide = true` |
| `flywheel`, `roller` | the frame and the wheel | |
| `launcher` | the side plates and the wheel | the hood's arc: the launcher model, not contact, carries the ball round it |
| `disc_shooter` | the frame, the wheels and the fence | the guide plates above and below the disc |
| `pinch_shooter` | the frame, the side plates and both wheels | |

Things to know:

- **Why it is off by default.** Every colliding geom costs a little each physics step, and an existing robot keeps
  the results it had.
- **A `[shooter]`'s intake can't collide.** A roller with `collide = true` that is also the `[shooter]`'s intake is an
  error naming the part, such as `intake has collide = true but is the [shooter]'s intake; the ball path moves those
  balls, so leave collide off`. Its roller would push the floor balls the intake zone collects. The launcher and the
  stages may collide: the balls they hold are parked and touch nothing, and a launched ball ignores the robot for its
  first 0.15 s ([docs/test-range.md](test-range.md#launching-a-ball)).
- **Clearance and touches.** Only colliding parts count for the Test tab's
  [*robot clears*](test-tab.md#clearance-to-a-field-element) check and its **touches** check.
- **Climbing.** A stock part with `collide = true` counts for `climb.extension` and `climb.height`, which count every
  colliding geom ([docs/climbing.md](climbing.md)), and it can catch on a bar.

## Stock parts

This section is generated from `scrimmage parts <name>` and pasted here. **`scrimmage parts` is the live
source**; if it and this page ever disagree, it is right. Parameters are set as keys of the `[[part]]` entry.
Each shows its default, unit and limits.

```
$ scrimmage parts
arm                  single-jointed arm on a horizontal pivot; 0 degrees points forward, positive raises the tip
chassis              rectangular drive base with bumpers; modules mount at its corners
climber              telescoping climber with J hooks at its tip that hold a bar by contact; optional spring latch (passive or released by a motor) and pivot. Every geom collides
cylinder             a double-acting pneumatic cylinder, driven by air through its valve
differential         tank drive with 2-6 wheels per side; the middle wheel of each side is driven
disc_shooter         planar disc shooter: wheels driving a flat disc along a channel against a fence (or a second row of wheels), the channel tilted to the launch angle, fixed or on a motor; the test range's [shooter] kind = "disc" runs it with the disc launcher model
elevator             linear lift of 1-4 stages driven by a spool; the carriage is the last stage
flywheel             spinning wheel on a horizontal axle (shooter wheel)
launcher             hooded shooter: a wheel on a horizontal shaft and a hood around it, articulated or fixed; the test range's [shooter] runs it with sub-project 8's launcher model
pinch_shooter        face-pinch shooter for a flat piece (the 2024 NOTE): a top and a bottom wheel, each on its own shaft, squeeze the piece's faces and drive it out, the channel tilted to the exit angle, fixed or on a motor; the test range's [shooter] kind = "piece" runs it with the pinch model
roller               spinning roller on a horizontal axle (intake or indexer)
swerve/MAXSwerve     REV MAXSwerve module, 3 in wheel, NEO drive and NEO 550 steer
swerve/MK4i          SDS MK4i swerve module (inverted steer motor), 4 in wheel
swerve/MK4n          SDS MK4n swerve module (narrow), 4 in wheel
turret               a flat platform on a vertical axis; carry a launcher on it and turn it to aim
```

### arm

```
arm: single-jointed arm on a horizontal pivot; 0 degrees points forward, positive raises the tip

Slots: pivot (motor), encoder (arm turns).

parameters:
  length = 0.5 m (greater than 0): pivot to tip
  mass = 2.0 kg (greater than 0): arm mass
  com = 0.0 m (at least 0.0, limited by other parameters): center of mass distance from the pivot; 0 means half the length. Only the center-of-mass point moves: the inertia stays that of a uniform bar of this mass and length
  range = [-90.0, 90.0] deg ([min, max] with min < max): lowest and highest allowed angle
  motor = "KrakenX60" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): motor model
  count = 1 (a whole number, at least 1): motors driving the pivot
  gearing = 50.0 (greater than 0): motor turns per arm turn
  spring_stiffness = 0.0 N*m/rad (at least 0.0): torsion spring (gas spring or counterbalance) stiffness
  spring_rest = 0.0 deg: angle at which the spring is relaxed (a spring is added whenever this or the stiffness is non-zero)
  collide = false: its geoms touch the field, game pieces and a partner robot (never the robot's own parts). Drawing-only geoms never collide: the launcher's hood arc, a disc shooter's guide plates, and a push cylinder's barrel (a push cylinder touches nothing even with collide on)

slots: pivot, encoder
mounts: base, tip
```

### chassis

```
chassis: rectangular drive base with bumpers; modules mount at its corners

parameters:
  length = 0.7 m (greater than 0): frame length (front to back), without bumpers
  width = 0.7 m (greater than 0): frame width (side to side), without bumpers
  mass = 46.0 kg (greater than 0): everything on the chassis that isn't another part (frame, battery, electronics)
  frame_height = 0.12 m (greater than 0): height of the frame's middle above the floor
  bumper = 0.08 m (greater than 0): bumper thickness
  bumper_clearance = 0.02 m (greater than 0): height of the bumpers' bottom above the carpet; with the bumpers' reach past the front wheels it sets the approach angle, the steepest ramp the robot drives onto (docs/terrain.md)
  module_inset = 0.0579 m (at least 0.0, limited by other parameters): distance from each frame edge to a corner mount (module center)
  com_offset = [0.0, 0.0, 0.0] m ([x, y, z]): moves the chassis's center of mass from its usual place [forward, left, up]; use it when a part you attach changes where the robot's weight sits

slots: gyro
mounts: front_left, front_right, back_left, back_right, center, top
```

### climber

```
climber: telescoping climber with J hooks at its tip that hold a bar by contact; optional spring latch (passive or released by a motor) and pivot. Every geom collides

Slots: extend (motor) and extend_encoder (spool turns); release (motor) with latch = "released"; pivot (motor) and pivot_encoder (climber turns) with pivot = "articulated". Hook geoms are named <part>.hook<i>.stem, .crown<k>, .tip and .latch; the bracket carrying them <part>.bracket. The climber stands up from its base mount, retracted; the hooks open toward +x and hold a bar running along y.

parameters:
  stages = 2 (a whole number, at least 1, at most 4): moving tubes including the tip (the tip is stage 'tip', the others stage1, stage2, ...)
  base_length = 0.6 m (greater than 0): length of every tube (estimate)
  travel = 0.6 m (greater than 0, limited by other parameters): total extension of the tip from fully retracted; each tube keeps 0.1 m inside the one below
  rigging = "continuous" (one of continuous, cascade): continuous or cascade, as the elevator's
  tube = 0.0508 m (greater than 0): outer size of the square base tube; each stage is 1/4 in smaller
  stage_mass = 0.6 kg (greater than 0): mass of the base tube and of each intermediate tube (estimate: 2 in 1/16 wall aluminum, 0.86 kg/m, plus hardware)
  tip_mass = 0.8 kg (greater than 0): mass of the tip tube with its hooks and bracket (estimate)
  motor = "KrakenX60" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): extend motor model
  count = 1 (a whole number, at least 1): motors driving the telescope
  gearing = 20.0 (greater than 0): motor turns per spool turn (estimate)
  spool_radius = 0.015 m (greater than 0): radius the rope winds on (estimate)
  friction = 5.0 N (at least 0.0): constant sliding friction, in newtons felt at the tip
  hook_throat = 0.048164 m (greater than 0): the throat's clear width between the stem and the tip; default the 2022 rung's 1.66 in OD plus 6 mm
  hook_depth = 0.1 m (greater than 0): how far the hook's tip reaches below the throat's top (estimate)
  hook_material = "steel" (one of steel, polycarbonate, uhmw, nitrile): sets the hook's friction on the bar: steel 0.5, polycarbonate 0.35, uhmw 0.15, nitrile 1.0 (estimates)
  hook_count = 1 (a whole number, at least 1, at most 2): hooks across the arm (1 or 2)
  hook_spacing = 0.2 m (greater than 0): distance between two hooks along the bar (estimate)
  latch = "passive" (one of none, passive, released): none; passive: a spring latch across each hook's mouth that the bar pushes open and that snaps shut behind it; released: the same, plus a motor slot release that opens it
  latch_spring = 0.3 N*m/rad (greater than 0): the latch's torsion spring (estimate)
  release_motor = "NEO550" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): release motor model
  release_gearing = 5.0 (greater than 0): release motor turns per latch turn (estimate)
  pivot = "none" (one of none, articulated): none, or articulated: a hinge at the base on a motor (slots pivot, pivot_encoder)
  pivot_range = [-30.0, 30.0] deg ([min, max] with min < max): the pivot's limits; 0 is upright, positive leans the tip toward +x, the side the hooks open to (estimate)
  pivot_motor = "KrakenX60" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): pivot motor model
  pivot_count = 1 (a whole number, at least 1): motors driving the pivot
  pivot_gearing = 100.0 (greater than 0): pivot motor turns per climber turn (estimate)

slots: extend, extend_encoder
mounts: base, tip
```

### cylinder

```
cylinder: a double-acting pneumatic cylinder, driven by air through its valve

Slots: valve (a PCM or PH solenoid). The barrel lies along the part's +x and the rod extends toward +x. Free (push empty): mount things on cylinder.rod. Push: the rod's end is pinned to push at push_pos, and the cylinder drives the joints between its two ends; it has only the base mount.

parameters:
  bore = 0.0269875 m (greater than 0): the piston's diameter (1-1/16 in; estimate, UNCALIBRATED)
  rod = 0.0079375 m (greater than 0): the rod's diameter, less than the bore (5/16 in; UNCALIBRATED)
  stroke = 0.1524 m (greater than 0): how far the rod travels (6 in)
  friction = 8.0 N (at least 0.0): seal friction (estimate, UNCALIBRATED)
  conductance = 8e-09 m^3/(s*Pa) (greater than 0): the valve's sonic conductance, how freely air flows (estimate, UNCALIBRATED)
  measured = "": a cylinder measurement file ("deploy.measure.toml", relative to the robot config) whose timed strokes fit the conductance; empty for the conductance above
  mass = 0.25 kg (greater than 0): the barrel's mass (UNCALIBRATED)
  rod_mass = 0.1 kg (greater than 0): the rod's mass (UNCALIBRATED)
  push = "": "<part>.<body>" the rod's end is pinned to (a stock arm's "arm.arm"); empty for a free rod with its own mount
  push_pos = [0.0, 0.0, 0.0] m ([x, y, z]): where on the push body the rod's end is pinned, in that body's frame
  length = 0.0 m (at least 0.0): pin to pin when retracted; 0 takes it from the assembled pose (push) or 0.3 x stroke + 0.05 m (free)
  collide = false: its geoms touch the field, game pieces and a partner robot (never the robot's own parts). Drawing-only geoms never collide: the launcher's hood arc, a disc shooter's guide plates, and a push cylinder's barrel (a push cylinder touches nothing even with collide on)

slots: valve
mounts: base, rod
```

### differential

```
differential: tank drive with 2-6 wheels per side; the middle wheel of each side is driven

The right side is mirrored: its motor and encoder are inverted, so equal controller outputs drive the two sides in opposite directions unless the robot program inverts one.

parameters:
  track_width = 0.51 m (greater than 0): distance between the left and right wheel centers
  wheelbase = 0.52 m (greater than 0): front-to-back spacing of the first and last wheel
  wheels_per_side = 3 (a whole number, at least 2, at most 6): wheels on each side, spread evenly front to back
  wheel_diameter = 0.1524 m (greater than 0): wheel diameter
  drop_center = 0.003 m (at least 0.0): how far the middle wheel of an odd count sits below the others
  motor = "CIM" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): motor model
  motors_per_side = 2 (a whole number, at least 1): motors on each side
  gearing = 10.71 (greater than 0): motor turns per wheel turn
  efficiency = 0.9 (greater than 0): drivetrain efficiency
  wheel_friction = 1.1 (greater than 0): wheel-to-carpet sliding friction coefficient
  tread_stiffness = 200000.0 N/m (greater than 0): each wheel's tread stiffness (estimate: TPU or Colson-like tread on a 4 in wheel, about 1e5 to 3e5)
  tread_damping = 1.0 (greater than 0): the tread contact's damping ratio (1 is critical: no bounce)
  rail_mass = 1.0 kg (greater than 0): mass of the rails the wheels hang from

slots: left, right, left_encoder, right_encoder
mounts: center, top
```

### elevator

```
elevator: linear lift of 1-4 stages driven by a spool; the carriage is the last stage

Slots: lift (motor), encoder (spool turns). The elevator stands up from its base mount; the carriage starts fully lowered.

parameters:
  travel = 1.0 m (greater than 0): total carriage travel from fully lowered to fully raised
  stages = 1 (a whole number, at least 1, at most 4): number of moving stages including the carriage (the carriage is stage 'carriage', the others are stage1, stage2, ...)
  rigging = "continuous" (one of continuous, cascade): continuous: the carriage moves at the rope speed, full force at the carriage, and the first stage moves at 1/stages of it; cascade: the first stage moves at the rope speed and the carriage at stages x rope speed, with 1/stages of the force (carriage height = spool turns x 2 pi r x stages)
  carriage_mass = 3.0 kg (greater than 0): mass of the carriage and what it carries
  stage_mass = 1.5 kg (greater than 0): mass of the base and of each intermediate stage
  motor = "KrakenX60" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): motor model
  count = 2 (a whole number, at least 1): motors driving the lift
  gearing = 9.0 (greater than 0): motor turns per spool turn
  spool_radius = 0.022 m (greater than 0): radius the rope winds on
  friction = 5.0 N (at least 0.0): constant sliding friction, in newtons felt at the carriage
  width = 0.3 m (at least 0.1): side-to-side width of the stages
  collide = false: its geoms touch the field, game pieces and a partner robot (never the robot's own parts). Drawing-only geoms never collide: the launcher's hood arc, a disc shooter's guide plates, and a push cylinder's barrel (a push cylinder touches nothing even with collide on)

slots: lift, encoder
mounts: base, carriage
```

### flywheel

```
flywheel: spinning wheel on a horizontal axle (shooter wheel)

Slot: spin (motor). The axle runs along the y axis.

parameters:
  diameter = 0.1016 m (greater than 0): wheel diameter
  mass = 0.5 kg (greater than 0): wheel mass
  moi = 0.0 kg*m^2 (at least 0.0): moment of inertia about the axle; 0 means a solid disc, 1/2 m r^2
  width = 0.05 m (greater than 0): wheel thickness along the axle
  motor = "KrakenX60" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): motor model
  count = 1 (a whole number, at least 1): motors driving the wheel
  gearing = 1.0 (greater than 0): motor turns per wheel turn
  collide = false: its geoms touch the field, game pieces and a partner robot (never the robot's own parts). Drawing-only geoms never collide: the launcher's hood arc, a disc shooter's guide plates, and a push cylinder's barrel (a push cylinder touches nothing even with collide on)

slots: spin
mounts: base
```

### pinch_shooter

```
pinch_shooter: face-pinch shooter for a flat piece (the 2024 NOTE): a top and a bottom wheel, each on its own shaft, squeeze the piece's faces and drive it out, the channel tilted to the exit angle, fixed or on a motor; the test range's [shooter] kind = "piece" runs it with the pinch model

Slots: bottom and top (motors on joints spin0 and spin1, one per wheel), and tilt (motor) and tilt_encoder (channel turns) with an articulated tilt. The channel body's origin is where the fed piece's middle starts: x downrange, z pointing at the top wheel (the piece's faces lie across it), y along the wheels' axles; it is the base's frame pitched up by the exit angle about y. The tilt joint reads 0 at the low stop: exit angle = tilt_range[0] + the joint's angle. In the world the [shooter] section's pinch model runs the wheel shafts and the spin joints mirror them.

parameters:
  bottom_diameter = 0.1016 m (greater than 0): the bottom wheel's diameter
  top_diameter = 0.1016 m (greater than 0): the top wheel's diameter
  wheel_width = 0.3556 m (greater than 0): the wheels' width along their axles (the contact patch is the narrower of it and the piece)
  gap = 0.03175 m (greater than 0): the gap between the two wheels' surfaces; the piece is squeezed by its thickness less this
  wheel_x = 0.25 m (greater than 0): how far downrange of the fed piece's middle the wheels' axles stand: at least half the piece's length plus the wheels' reach along it
  wheel_moi = 0.001 kg*m^2 (greater than 0): each wheel's (with its hub and shaft) moment of inertia about its axle
  added_moi = 0.0 kg*m^2 (at least 0.0): a flywheel on each wheel's shaft
  motor = "KrakenX60" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): wheel motor model
  count = 1 (a whole number, at least 1): motors driving each wheel
  gearing = 1.0 (greater than 0): motor turns per wheel turn
  channel_mass = 1.0 kg (greater than 0): the channel's mass (its plates and the wheels' frame)
  tilt = "fixed" (one of fixed, articulated): fixed: the channel stays at tilt_angle; articulated: it pivots at its entry on a motor (joint tilt)
  tilt_angle = 45.0 deg (at least -80.0, at most 80.0): the fixed channel's exit angle above horizontal
  tilt_range = [20.0, 60.0] deg ([min, max] with min < max): the articulated channel's exit angles at its low and high hard stops
  tilt_motor = "KrakenX60" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): tilt motor model
  tilt_count = 1 (a whole number, at least 1): motors driving the tilt
  tilt_gearing = 100.0 (greater than 0): tilt motor turns per channel turn
  collide = false: its geoms touch the field, game pieces and a partner robot (never the robot's own parts). Drawing-only geoms never collide: the launcher's hood arc, a disc shooter's guide plates, and a push cylinder's barrel (a push cylinder touches nothing even with collide on)

slots: bottom, top
mounts: base
```

### roller

```
roller: spinning roller on a horizontal axle (intake or indexer)

Slot: spin (motor). The axle runs along the y axis.

parameters:
  diameter = 0.05 m (greater than 0): wheel diameter
  mass = 0.3 kg (greater than 0): wheel mass
  moi = 0.0 kg*m^2 (at least 0.0): moment of inertia about the axle; 0 means a solid disc, 1/2 m r^2
  length = 0.4 m (greater than 0): roller length along the axle
  motor = "KrakenX60" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): motor model
  count = 1 (a whole number, at least 1): motors driving the wheel
  gearing = 1.0 (greater than 0): motor turns per wheel turn
  collide = false: its geoms touch the field, game pieces and a partner robot (never the robot's own parts). Drawing-only geoms never collide: the launcher's hood arc, a disc shooter's guide plates, and a push cylinder's barrel (a push cylinder touches nothing even with collide on)

slots: spin
mounts: base
```

### swerve/MAXSwerve

```
swerve/MAXSwerve: REV MAXSwerve module, 3 in wheel, NEO drive and NEO 550 steer

Real MAXSwerve modules use REV SPARK controllers: bind the drive and steer slots with SparkMax:<id> (or SparkFlex:<id>) and the encoder slot, the steer SPARK's absolute encoder, with SparkMaxAbs:<id> (or SparkFlexAbs:<id>). The ratio choices are the pinion teeth (12T, 13T or 14T); REV's MAXSwerve template uses 14T. The Through Bore encoder reads the module clockwise-positive, so robot code inverts it, as REV's template does.

parameters:
  ratio = "14T" (one of 12T, 13T, 14T or a number greater than 0): drive gear ratio (motor turns per wheel turn): 12T = 5.5, 13T = 5.08, 14T = 4.71, or your own number
  steer_ratio = 46.423645320197046 (greater than 0): steer motor turns per module turn
  drive_motor = "NEO" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): drive motor model
  steer_motor = "NEO550" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): steer motor model
  drive_invert = false: invert the drive motor
  steer_invert = false: invert the steer motor
  wheel_friction = 1.2 (greater than 0): wheel-to-carpet sliding friction coefficient
  wheel_width = 0.04 m (greater than 0): tread width
  tread_stiffness = 200000.0 N/m (greater than 0): each wheel's tread stiffness (estimate: TPU or Colson-like tread on a 4 in wheel, about 1e5 to 3e5)
  tread_damping = 1.0 (greater than 0): the tread contact's damping ratio (1 is critical: no bounce)
  module_mass = 0.75 kg (at least 0.3): azimuth housing plus wheel (0.25 kg of it is the wheel)

slots: drive, steer, encoder
mounts: none
```

### swerve/MK4i

```
swerve/MK4i: SDS MK4i swerve module (inverted steer motor), 4 in wheel

parameters:
  ratio = "L2" (one of L1, L2, L3 or a number greater than 0): drive gear ratio (motor turns per wheel turn): L1 = 8.14, L2 = 6.75, L3 = 6.12, or your own number
  steer_ratio = 21.428571428571427 (greater than 0): steer motor turns per module turn
  drive_motor = "KrakenX60" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): drive motor model
  steer_motor = "KrakenX60" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): steer motor model
  drive_invert = false: invert the drive motor
  steer_invert = true: invert the steer motor
  wheel_friction = 1.2 (greater than 0): wheel-to-carpet sliding friction coefficient
  wheel_width = 0.04 m (greater than 0): tread width
  tread_stiffness = 200000.0 N/m (greater than 0): each wheel's tread stiffness (estimate: TPU or Colson-like tread on a 4 in wheel, about 1e5 to 3e5)
  tread_damping = 1.0 (greater than 0): the tread contact's damping ratio (1 is critical: no bounce)
  module_mass = 0.75 kg (at least 0.3): azimuth housing plus wheel (0.25 kg of it is the wheel)

slots: drive, steer, encoder
mounts: none
```

### swerve/MK4n

```
swerve/MK4n: SDS MK4n swerve module (narrow), 4 in wheel

parameters:
  ratio = "L2+" (one of L1+, L2+, L3+ or a number greater than 0): drive gear ratio (motor turns per wheel turn): L1+ = 7.13, L2+ = 5.9, L3+ = 5.36, or your own number
  steer_ratio = 18.75 (greater than 0): steer motor turns per module turn
  drive_motor = "KrakenX60" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): drive motor model
  steer_motor = "KrakenX60" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): steer motor model
  drive_invert = false: invert the drive motor
  steer_invert = true: invert the steer motor
  wheel_friction = 1.2 (greater than 0): wheel-to-carpet sliding friction coefficient
  wheel_width = 0.04 m (greater than 0): tread width
  tread_stiffness = 200000.0 N/m (greater than 0): each wheel's tread stiffness (estimate: TPU or Colson-like tread on a 4 in wheel, about 1e5 to 3e5)
  tread_damping = 1.0 (greater than 0): the tread contact's damping ratio (1 is critical: no bounce)
  module_mass = 0.75 kg (at least 0.3): azimuth housing plus wheel (0.25 kg of it is the wheel)

slots: drive, steer, encoder
mounts: none
```

### turret

```
turret: a flat platform on a vertical axis; carry a launcher on it and turn it to aim

Slots: turn (motor), encoder (turret turns). 0 faces the part's +x; positive turns left (counter-clockwise from above). Attach the launcher to turret.top.

parameters:
  diameter = 0.3 m (greater than 0): the platform's diameter
  mass = 2.0 kg (greater than 0): platform mass
  height = 0.05 m (at least 0.0): the top mount's height above the base
  range = [-180.0, 180.0] deg ([min, max] with min < max): lowest and highest allowed angle (each end within +-360)
  motor = "KrakenX60" (one of Bag, CIM, Falcon500, Falcon500FOC, KrakenX60, KrakenX60FOC, MiniCIM, NEO, NEO550, NeoVortex, Vex775Pro): motor model
  count = 1 (a whole number, at least 1): motors driving the turret
  gearing = 60.0 (greater than 0): motor turns per turret turn
  friction = 0.2 N*m (at least 0.0): constant friction torque on the turret's joint
  collide = false: its geoms touch the field, game pieces and a partner robot (never the robot's own parts). Drawing-only geoms never collide: the launcher's hood arc, a disc shooter's guide plates, and a push cylinder's barrel (a push cylinder touches nothing even with collide on)

slots: turn, encoder
mounts: base, top
```
