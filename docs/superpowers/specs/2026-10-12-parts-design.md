# Parts and assembler (sub-project 4a): design

## Context

Scrimmage runs an unmodified WPILib robot program (Python or Java; PWM, WPILib CAN or Phoenix 6 devices)
against a MuJoCo world. Today a robot is a hand-written MJCF file (`models/swerve.xml`, `models/kitbot.xml`)
plus a config whose `[[motor]]`, `[[encoder]]` and `[[gyro]]` bindings name joints and bodies in it.
Writing MJCF is the friction this sub-project removes.

The goal of the whole project: an FRC student assembles a robot from mechanisms modeled by teammates (or
stock parts) and tests code before the robot exists, without editing simulator files by hand; agents drive
the same robots through sessions and MCP.

Roadmap position: sub-projects 1 (lockstep), 2 (agent sessions, scenarios, MCP) and 3 (Java + Phoenix 6) are
done. Sub-project 4 was split:

- **4a (this spec):** the part format, the assembler, and a stock library, needing no CAD.
- **4b (next):** a FreeCAD 1.x exporter add-on that reads Assembly joints (Revolute, Slider; RackPinion, Gears,
  Belt as couplings), offers a motor and sensor panel, and writes meshes plus `part.toml` in the format defined
  here. It may also read part placements from a full-robot assembly when a team has one.
- **5 (later):** a GUI assembler and scenario editor that edit the same files.
- **Later, unscheduled:** REV hardware (SPARK MAX/Flex, through-bore encoder) bindings.

Decisions made with the user:

- A part is a mechanism (drivetrain, module, elevator, arm, intake, chassis): geometry, joints, couplings,
  device slots and mount points. Motors and sensors are named stock types with counts and gearing, not
  modeled geometry. Movement comes only from physics inputs (mass, gearing, friction, springs, travel limits),
  never from target speeds.
- Device IDs are assigned when the robot is assembled, not in the part.
- `part.toml` is the single source of truth for a part; Scrimmage generates the MJCF.
- Teams vary in whether they keep a full-robot CAD assembly, so Scrimmage places parts itself.
- Placement: each part attaches to a body of another part, or to a named mount point on it, with an
  optional offset.
- One config file per robot: `[[part]]` entries join the existing run sections.
- Stock parts are Python functions with typed parameters; `part.toml` is static data.
- The first stock library: COTS swerve modules, a parametric chassis, parametric mechanisms, and a
  differential drivetrain. The user's team uses swerve and CTRE only.

## Goals

1. A robot config can describe the robot as `[[part]]` entries: stock or from a part folder, attached and
   offset, with device IDs per slot. No hand-written MJCF and no hand-written bindings.
2. `part.toml` is a documented, versioned format (the contract for the 4b exporter and the GUI).
3. Stock parts: `chassis`, `swerve/MK4i`, `swerve/MK4n`, `swerve/MAXSwerve`, `differential`, `elevator`,
   `arm`, `flywheel`, `roller`.
4. The existing swerve and kitbot robots are rebuilt from parts, and their scenarios pass. `models/swerve.xml`
   is deleted; `models/kitbot.xml` moves to `tests/models/kitbot.xml`, where the `[model]` test fixtures keep
   using it.
5. Mistakes give actionable messages naming the file, part instance and key.
6. Students and agents can inspect what was built: `scrimmage model`, `scrimmage parts`, an MCP tool.

## Non-goals

- The FreeCAD exporter (4b) and any GUI (5).
- Game pieces and their contact with intakes or rollers.
- A full game field (the generator adds today's simplified field, see Field).
- REV hardware bindings. MAXSwerve geometry and ratios ship, driven only by the existing binding kinds.
- Parametric parts written by teams without Python (no expression language in `part.toml`).
- Mecanum drive.
- Self-collision inside a robot.

## Design

### Overview

```
config.toml [[part]] ──► resolve ──► Part (in memory) ──► assembler ──► MjSpec ──► MjModel
                          │  stock: Python function(params) -> Part                │
                          │  path:  load part.toml (+ meshes) -> Part              └─► bindings (generated)
```

New modules (names indicative): `scrimmage/parts/model.py` (the `Part` data model and `part.toml`
load/dump), `scrimmage/parts/stock/` (one module per stock family), `scrimmage/parts/assemble.py`
(placement, naming, bindings, MjSpec generation). `config.py`/`world.py` call the assembler when the config
has `[[part]]`; `World` then runs exactly as today on the generated model and bindings.

The model is built with MuJoCo's spec API (`mujoco.MjSpec`, present in the installed 3.15): each part
becomes a spec, and placing one attaches it to its parent body's frame with the name prefix `<instance>.`.
No MJCF text is stitched together.

### The part model and `part.toml`

Units in files: meters, kilograms, degrees, seconds. Internally radians. Each part has its own frame;
robot-level frames follow WPILib: x forward, y left, z up.

A part has five kinds of thing:

1. **Bodies**, a tree. The first body is the part's root. Fields: `name`, `parent` (omitted for the root),
   `pos`, `rot` (roll, pitch, yaw in degrees), `mass` (required, > 0), optional `inertia` (diagonal) and `com`
   (otherwise computed from geometry with the mass spread over it), and `geom`, a list of shapes:
   `{ box = [x, y, z] }` (full sizes), `{ cylinder = [radius, length], axis = "x"|"y"|"z" }`,
   `{ sphere = radius }`, `{ mesh = "meshes/carriage.stl" }`, each with optional `pos`, `rot`, `collide`
   (default true: touches the floor and world; never other geoms of the same robot, see Collisions),
   `friction` and `color`.
2. **Joints**, on a non-root body, moving it relative to its parent: `{ name, hinge = axis }` or
   `{ name, slide = axis }`, where the axis is `"x"`, `"y"`, `"z"` or a vector; optional `range` (degrees or
   meters), `damping`, `frictionloss`, `armature`, and `spring = { stiffness, rest }`.
3. **Couplings**: `{ joints = ["a", "b"], ratio = r }` meaning b = r·a. Rack and pinion, gear and belt
   pairs, cascade elevator stages. Generated as MuJoCo joint equality constraints.
4. **Device slots**, with no device IDs:
   - `[[motor]]`: `slot`, `joint`, `motor` (a name from `scrimmage/motors.py`), `count` (motors on this
     joint, default 1), `gearing` (motor radians per joint unit: per radian for hinges, per meter for slides),
     optional `invert` (the motor turns the joint backwards when it is positive).
   - `[[encoder]]`: `slot`, `joint`, `ratio` (sensor turns per joint unit, default 1).
   - `[[gyro]]`: `slot`, `body`.
5. **Mount points**, `[[mount]]`: `name`, `body`, `pos`, `rot`. Frames other parts attach to.

Header: `[part] name = "...", format = 1`. A `format` newer than this Scrimmage supports is an error telling
the user to upgrade. Loading then dumping a part gives an equivalent file (round trip).

Example (hand-written):

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
geom = [{ cylinder = [0.025, 0.4], axis = "y" }]
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

A part folder holds `part.toml` and any meshes it names (paths relative to the folder).

### The robot config

`[[part]]` entries replace `[model]` and the hand-written binding lists. A config has either `[model] mjcf`
(the escape hatch, kept for unusual robots and test fixtures) or `[[part]]`, never both; mixing them is a
config error that says so. Hand-written `[[motor]]`/`[[encoder]]`/`[[gyro]]` entries are allowed only with
`[model]`.

```toml
[[part]]
name = "drive"                       # instance name: unique, letters/digits/_ only
stock = "chassis"                    # or path = "parts/elevator"
length = 0.7
width = 0.7
mass = 45
ids = { gyro = "Pigeon2:13" }

[[part]]
name = "fl"
stock = "swerve/MK4i"
ratio = "L2"
attach = "drive.front_left"          # <instance>.<mount point or body>
ids = { drive = "TalonFX:1", steer = "TalonFX:2", encoder = "CANcoder:9" }

[[part]]
name = "arm"
path = "parts/arm"
attach = "elevator.carriage"
pos = [0.05, 0, 0.1]
rot = [0, 0, 90]
ids = { pivot = { ctre = "TalonFX:20", neutral = "brake", invert = true } }
```

Keys of a `[[part]]` other than `name`, `stock`, `path`, `attach`, `pos`, `rot` and `ids` are the stock part's
parameters; unknown parameters are errors listing the valid ones. `path` parts take no parameters.

**Placement.** Exactly one part has no `attach`: the robot's root. It gets the free joint and becomes
`[robot] body` automatically (`[robot] start_pose` stays). `attach` names a mount point or a body of another
instance; mount points are tried first. `pos`/`rot` offset from that frame (default zero). Unknown names and
attachment cycles are errors listing the valid names.

**IDs.** `ids` maps slot names to devices:

- Short form: `"TalonFX:1"`, `"CANcoder:9"`, `"Pigeon2:13"` (Phoenix 6, becoming `ctre = ...`), `"PWM:0"`
  (`pwm = 0`) and `"DIO:0"` for a quadrature encoder's channel A (`channel_a = 0`).
- Table form: today's binding keys for that slot kind, e.g. `{ ctre = "TalonFX:1", neutral = "brake" }`,
  `{ can = "SPARK MAX[3]" }`, `{ channel_a = 0, counts_per_rev = 2048 }`,
  `{ device = "ADXRS450[0]", axis = "x" }`, `{ ctre = "CANcoder:9", offset = 0.25 }`. The part supplies
  `joint`/`body`, `motor`, `count`, `gear_ratio`, `ratio` and `name`; setting those here is an error.
  `invert` here means mounted reversed relative to the part's own direction: it flips the slot's `invert`.
- A motor slot with `count = N` accepts one ID (one controller driving all N, e.g. a Y-split PWM) or a list of
  N IDs (leader and followers). Each controller drives 1/N of the gearbox, so an unconfigured follower shows up
  as missing torque, as on the real robot.
- A slot kind mismatch (`gyro = "TalonFX:3"`) is an error. An unknown slot is an error listing the part's slots.
- A slot with no ID is a warning ("arm.pivot has no device ID; it won't move/report"): parts may be modeled
  before they are wired.
- Duplicate devices are already an error (sub-project 3).

The assembler emits ordinary bindings (the same classes as today), named `<instance>.<slot>`.

**Naming.** Every generated body, joint and geom is `<instance>.<name>` (`fl.drive`, `arm.pivot`).
Scenarios, pokes and status use these names. The root instance's root body is the robot body.

**Collisions.** Robot geoms don't collide with each other; they collide with the floor, walls and any world
geometry. Implemented with contact bits: robot geoms `contype = 2, conaffinity = 1`, field geoms
`contype = 1, conaffinity = 2`. Wheels are the floor contacts for drivetrains; bumpers hit walls.

**Field.** The generator adds today's simplified field, with the same names as `models/kitbot.xml`: the
`carpet` plane, `wall_blue`, `wall_red`, `wall_left`, `wall_right`, the `obstacle` box, a directional light,
and the same visual settings (offscreen size, shadow quality). An optional `[field]` table takes
`obstacle = false` to drop the obstacle.

**Inspection.**
- `scrimmage model <config> [-o robot.xml]` writes the generated MJCF (for MuJoCo's viewer or debugging).
- `scrimmage parts [<stock name>]` lists stock parts, or one part's parameters with units, defaults, ranges
  and docs, its slots and its mount points.
- MCP: a `robot_parts` tool returns the part tree with slots, IDs and mount points. Session `status()` keeps
  listing joints and bodies under the new names.

### Stock library

Python functions, one per part, each with documented parameters (unit, default, allowed range) that are
validated before building. Motors come from `scrimmage/motors.py`.

| Stock | Parameters (defaults in the code) | Slots | Mounts |
|---|---|---|---|
| `chassis` | `length`, `width`, `mass`, `frame_height`, `bumper` thickness, `module_inset` | `gyro` | `front_left`, `front_right`, `back_left`, `back_right`, `center`, `top` |
| `swerve/MK4i` | `ratio` L1 8.14 / L2 6.75 / L3 6.12 or a number, `steer_ratio` 150/7, 4" wheel; `drive_motor`, `steer_motor` (Kraken X60), `drive_invert`, `steer_invert`, `wheel_friction` | `drive`, `steer`, `encoder` | — |
| `swerve/MK4n` | `ratio` L1+ 7.13 / L2+ 5.9 / L3+ 5.36, steer 18.75, 4" wheel; as MK4i | `drive`, `steer`, `encoder` | — |
| `swerve/MAXSwerve` | `pinion` 12 / 13 / 14 (5.50 / 5.08 / 4.71), 3" wheel; as MK4i | `drive`, `steer`, `encoder` | — |
| `differential` | `track_width`, `wheel_diameter`, `wheels_per_side`, `motor`, `motors_per_side`, `gearing`, `wheel_friction` | `left`, `right` | `center`, `top` |
| `elevator` | `travel`, `stages`, `rigging` continuous/cascade, `carriage_mass`, `stage_mass`, `motor`, `count`, `gearing`, `spool_radius`, `friction` | `lift`, `encoder` | `base`, `carriage` |
| `arm` | `length`, `mass`, `com`, `range` (deg), `motor`, `count`, `gearing`, `spring` | `pivot`, `encoder` | `base`, `tip` |
| `flywheel` | `diameter`, `mass` or `moi`, `motor`, `count`, `gearing` | `spin` | `base` |
| `roller` | as flywheel plus `length` | `spin` | `base` |

Swerve modules mirror their hardware's motor directions: `steer_invert` defaults to true for MK4i and MK4n
(the steer gearing reverses the motor, as Tuner X's `kSteerMotorInverted = true` assumes) and `drive_invert`
defaults to false; modules that a team's code inverts as a side (Tuner X's `kInvertRightSide`) set
`drive_invert = true` in the config. So a team's real inversion settings work unchanged. `ratio` takes a named
ratio or a number (drive rotor turns per wheel turn); `steer_ratio` overrides the steer reduction.
`examples/java_swerve` and the Tuner X end-to-end test move to these stock modules and the real inversions.
The drive-steer coupling is not modeled (MuJoCo's joint constraints link only two joints); Phoenix's
coupling compensation then adds a small odometry error while steering, as before this sub-project.

The real MAXSwerve uses REV hardware, which Scrimmage can't bind yet; the stock part ships and its slots take
the existing kinds. The `scrimmage parts` text says so.

The swerve modules and chassis must reproduce today's `models/swerve.xml` physics closely enough that the
swerve scenario passes unchanged in its tolerances.

## Failure handling

| Situation | Behavior |
|---|---|
| Bad TOML, missing or wrong-typed key in `[[part]]` or `part.toml` | exit 2: file, instance (`parts[2] 'arm'`) and key, with the expected form |
| Unknown stock name / parameter / slot / mount / attach target | exit 2, listing the valid names |
| Physical nonsense: mass ≤ 0, `range` min ≥ max, gearing ≤ 0, slot or coupling naming a missing joint, a geom with no size | exit 2 naming the part and field |
| `format` newer than supported | exit 2: "made by a newer Scrimmage; upgrade" |
| Mesh file missing or unreadable | exit 2 with the resolved path |
| Both `[model]` and `[[part]]`, or none of either | exit 2 explaining the two styles |
| Zero or several root parts, attach cycle | exit 2 naming the parts involved |
| Slot without ID; a part with motor slots none of which has an ID | warning on the console and in session status |

## Testing

- **Part model:** load/dump round trip on a checked-in golden `part.toml`; every error row above has a test.
- **Each stock part:** the model compiles; total mass, joint count, ranges and mount poses follow the
  parameters; out-of-range parameters give the documented errors.
- **Physics against closed-form results:**
  - elevator: stall torque against gravity decides whether it holds or falls, at the expected boundary
  - arm: holding voltage matches m·g·r for the motor and gearing
  - flywheel: spin-up time constant matches the DC-motor model
  - swerve module: free speed matches motor free speed / ratio × wheel circumference
  - differential: straight-line speed and turning from equal and opposite voltages
- **Assembler:** attach by mount and by body, offsets, naming, slot to binding generation (single ID and ID
  lists), duplicate and missing ID handling.
- **End to end:** the Java swerve scenario and the Python kitbot scenario on part-built robots; a new small
  elevator and arm example (Python robot program, fast) driven by a scenario.
- `scrimmage model` output loads in MuJoCo; `scrimmage parts` lists every stock part.

## Docs

- `docs/parts.md`: the `part.toml` reference (the 4b exporter's contract) with the stock parts' parameters.
- README: "Building a robot from parts", replacing the hand-written MJCF instructions (which move to a short
  escape-hatch note).
