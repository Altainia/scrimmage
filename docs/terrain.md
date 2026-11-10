# Terrain: bumps, ramps and tilting platforms

A range file can put **field elements** on the field: bumps, ramps, a tilting platform, and [moving
elements](#moving-elements-hinged-and-sliding) a robot works (a door, a flap, a portcullis, a spinning wheel). The
robot drives over them in the MuJoCo world like any other surface, and **terrain signals** tell a scenario how far it
left the ground, how far it tilted and whether it tipped over or got stuck. **`scrimmage terrain sweep`** drives a
robot straight over one element at a range of speeds, with no robot program, and reports the fastest speed it takes it
safely: the "bump speed limit" a team puts in its auto.

> **The contact numbers are estimates.** The tread stiffness, the HDPE's friction and the platform's damping
> aren't measured (see [Estimates](#estimates)). Trust the trends (raising the elevator lowers the speed limit)
> before the absolute numbers (2 m/s).

- [Field elements in range files](#field-elements-in-range-files)
- [Mesh elements from CAD](#mesh-elements-from-cad)
- [Moving elements: hinged and sliding](#moving-elements-hinged-and-sliding)
- [The 2016 defenses](#the-2016-defenses)
- [The terrain signals](#the-terrain-signals)
- [Approach angle and breakover](#approach-angle-and-breakover)
- [`scrimmage terrain sweep`](#scrimmage-terrain-sweep)
- [Estimates](#estimates)
- [What isn't modeled](#what-isnt-modeled)

## Field elements in range files

A [range file](test-range.md#the-range-file) lists its elements as `[[element]]` tables. SI units, angles in
degrees. `center` is the element's (x, y) in field coordinates; `yaw_deg` turns it about z from the field's +x. Its
`depth` or `length` runs along its own x, the direction robots drive over it, and `width` across. Every length key
has an inch form, `<key>_in` (give one of the two). `friction` (default 0.6) is the surface's sliding friction.

| kind | what it is | keys |
|---|---|---|
| `bump` | two ramps meeting at a crest, with a lip at each toe | `depth`, `width`, `height`, `ramp_deg`, `crest` (the flat top's length, default 8 mm), `lip` (the toe lip's height, default 0.61 in) |
| `ramp` | an incline up to a flat deck, from one end or both | `width`, `height`, `ramp_deg`, `deck`, `two_sided` (default true; one-sided climbs toward +x) |
| `platform` | a deck on a double hinge that tilts under a robot, with a ramp at each end and stops | `length`, `width`, `height`, `max_tilt_deg`, `hinge_height`, `hinge_spacing`, `ramp_length`, `mass`, `inertia`, `com_height`, `damping` |
| `bar` | a fixed horizontal pipe to climb, along its own x, optionally on a vertical sprung slide | `length` (end to end), `diameter` (outer), `height` (of its axis), `posts` (default true: a post under each end), `stiffness` (N/m; leave it out for a rigid bar), `damping` (N s/m, with `stiffness`), `level` (a `[climb]` level), `section` (`"round"`, the default, or `"square"`: a square tube whose side is `diameter`; its top is the axis plus half the side) |
| `box` | a static block: structure (a truss) or, with `ground = true`, floor (a floor protector, a step) | `size` (`[x, y, z]`), `base_height` (its bottom, default 0), `ground` (default false) |
| `rod` | a straight round or square rod fixed to the field between two ends, at any angle: a peg, a pole, a coral branch. A hollow [piece](test-range.md#pieces-that-arent-balls) can slide onto it | `base`, `tip` (x, y, z points, in m; or `base_in`, `tip_in`), `diameter` (across; a square rod's side), `section` (`"round"`, the default, or `"square"`), `friction`. No `center` or `yaw_deg`: its ends place it |
| `switch` | a bar hanging below a hinge about its own y, which tilts under a hanging robot (2020's GENERATOR SWITCH) | `hinge_height`, `bar_length`, `bar_drop` (hinge to the bar's axis), `diameter` (default 1.66 in), `mass`, `com_drop` (its CoM below the hinge), `stop_deg`, `damping`, `level_deg` |
| `hinged` | a panel (a box, or a disc) on a hinge along one of its edges or through its center, with stops, a spring, damping and a breakaway: a door, a flap, a drawbridge, a spinning wheel | see [Moving elements](#moving-elements-hinged-and-sliding) |
| `sliding` | a box on a straight slide, with stops, a spring, damping and a breakaway: a portcullis, a touchpad, a spring peg | see [Moving elements](#moving-elements-hinged-and-sliding) |
| `mesh` | a static shape from a CAD file: a wall, the STAGE's frame, a SUBWOOFER, a bump as it really is | `file`, `select`, `units`, `scale`, `pos`, `rpy_deg`, `ground`, `friction`. No `center` or `yaw_deg`: `pos` and `rpy_deg` place it. See [Mesh elements](#mesh-elements-from-cad) |

A rod's round free end is rounded and reaches exactly to `tip`. Bars, rods, switches and boxes that aren't ground are structure, not floor: they are drawn and collide, but the terrain
signals don't count them as the ground (geom group 1, not the ground's 2), so a robot parked under a bar reads the
carpet, not the bar. A range with climbing elements scores them by its `[climb]` table: `rule` (`"lowest-rung"`,
`"hang"` or `"lowest-point"`), `levels` (each level's points, lowest first, like `{ LOW = 4, MID = 6 }`), and for
`"hang"` the `switch` and its `level_bonus`.

A bump's `depth` must agree with its profile (crest, then each ramp down at `ramp_deg` to the lip, then the lip to
the toe) within 1 cm, so a typo in one number is caught. A start whose robot outline (0.9 m square) overlaps an
element standing on the carpet, or a floor ball resting inside one, is rejected; a start may lie under a bar, a
switch or a raised box, and on a ground box no taller than 1/2 in (a floor protector).

The built-in ranges with elements:

| range | elements |
|---|---|
| `2026` | the 2026 REBUILT field's four BUMPS, `bump_blue_left`, `bump_blue_right`, `bump_red_left` and `bump_red_right`, with its hubs and FUEL ([docs/test-range.md](test-range.md)) |
| `2026-bump` | one 2026 REBUILT BUMP, `bump` (44.40 in deep, 73.0 in wide, 6.51 in tall, 15 deg HDPE ramps, from the field CAD), a row of six FUEL 0.45 m past its far toe, and `[start.approach]` 1.5 m before it |
| `2023-charge-station` | the 2023 CHARGE STATION, `charge_station`, a platform (96 x 48 in, 9 1/8 in up, +-15 deg) |
| `terrain-library` | a generic ramp, the 2016 RAMPARTS, the 2012 bridge and the 2020 CONTROL PANEL wheel (`control_panel`, 32 in across, its bottom 30.25 in up, turning freely), as examples of describing other seasons |
| `2026-trench` | one 2026 REBUILT TRENCH against the guardrail: `trench_arm` (its underside 22.25 in above the carpet) on `trench_side_left` and `trench_side_right` (a 50.34 in opening), and `[start.approach]` 1.5 m before it |
| `2016-defenses` | four 2016 STRONGHOLD DEFENSES on their PLATFORMS, in a line across the field: `portcullis` (a 5 lb door to lift), `sally_port` (a door that takes 1.5 lb at its edge to open), `drawbridge` (a sprung door to pull down and drive over) and `cheval_1` to `cheval_4` (the cheval de frise's tilting plates), each with a start before it |
| `2022-hangar` | the 2022 RAPID REACT HANGAR in the blue corner: rungs `low`, `mid`, `high` and `traversal` (GE-22100's heights, the upper three above 3/8 in floor protection, each on a 160 kN/m slide), the truss as boxes, `floor_protection`, `[start.under_mid]` |
| `2020-switch` | the 2020 GENERATOR SWITCH, `generator_switch` (+-14.5 deg, 42 kg, LEVEL within 8 deg), at the field's center, `[start.approach]` |
| `ladder`, `ladder-square` | a generic ladder to try a climber on (not a game's field): four rungs `rung1` to `rung4`, tops at 24, 36, 48 and 60 in, in one vertical plane 1.0 m in front of `[start.below]`, with a rail at each end; round rungs (1.66 in across) or, in `ladder-square`, square ones (1.5 in). No `[climb]`, so nothing scores; read the climb signals |
| `reef` | one pipe of the 2025 REEF with its `branch_l2`, `branch_l3` and `branch_l4` rods at the manual's heights and angles, a coral and a coral station, to try a grabber on ([docs/test-range.md](test-range.md#the-reef-field)) |
| `2013-pyramid` | the 2013 PYRAMID's twelve rungs at 30, 60 and 90 in (`l1_s` to `l3_e`; its legs aren't built) and floor protection, `[start.approach]`  (for climbing; the built-in `2013` is the disc range, its pyramid only obstacles) |

Everything is built from boxes and capsules (the bars), and a moving disc from a cylinder: the live view and the
renderer draw them with no meshes. A platform is two hinged bodies (the real double hinge: level, the deck rests on
both axes; tilted, it turns about the low one), its stops the joints' limits at `max_tilt_deg`.

### Mesh elements from CAD

```toml
[[element]]
name = "stage_blue"
kind = "mesh"
file = "cad/FE-2024.STEP"          # a .step, .stl or .obj file, or a folder of .stl/.obj files; relative to the range file
select = ["*GE-24200_Blue/GE-24210/*"]   # piece names or keys (globs); all pieces when left out
rpy_deg = [90, 0, 0]               # this file is drawn Y-up
pos = [0, 0, 0]
```

`scrimmage cad list <file>` shows the pieces' names, keys and sizes. `select` takes globs (`*?[]`, case-sensitive) on a
piece's name or its full key, and a glob on an assembly's key takes every piece inside it. A glob that matches nothing
is an error that names the closest piece names. `units` is only for a file that can't say (an STL or OBJ); a STEP
carries its own. `scale` multiplies the file's numbers. `ground = true` makes the shape floor for the
[terrain signals](#the-terrain-signals), as a box's does; without it the shape is structure. `friction` is the
surface's, as for the other kinds.

**Frames.** `scrimmage cad list` shows positions in the file's own axes, in meters. The element turns the file by
`rpy_deg` (roll about x, then pitch about y, then yaw about z, fixed axes, as a plate's) and then puts the file's
origin at `pos`; left out, the file's origin is the field's. A file drawn Y-up (its tallest direction is y, and the
sizes `cad list` shows are tall in y, not z) needs `rpy_deg = [90, 0, 0]`. The element's footprint for the start and
floor-ball checks is its bounding box in the field, and a mesh whose lowest point is above 1/2 in leaves a start
clear beneath it, as a raised box does.

**The shape.** Each selected piece is split into convex parts by itself (coacd, the same split a `cad` piece uses), and
the parts are one welded body, `element.<name>`, with a geom `element.<name>.<k>` each. The read of the CAD file and the
split are cached under the temporary folder (keyed by the file's bytes, `select`, `units` and `scale`), so only the first
build pays for them (an 88 MB STEP takes about 20 s to read), and moving the element (`pos`, `rpy_deg`) reads nothing.
Splitting rounds concave detail, which is coacd's known limit: check the shape in the viewer. A piece that isn't a
closed solid (a sheet body, a flat piece) can't be split: it is left out, with one warning that counts and names them.

**Errors.** A selection larger than the field, or with more than 200 000 triangles, is an error that says to select
fewer pieces. (The whole 2024 STAGE assembly is over a million triangles, most of it bolts and three trusses, so select
its frame and what matters, not all of it.) The file is FIRST's, not scrimmage's: the built-in ranges have no mesh
elements.

## Moving elements: hinged and sliding

A `hinged` or `sliding` element is a panel that a robot pushes, pulls, lifts or spins. It collides with robots, game
pieces and a [partner robot](test-tab.md#a-partner-robot) like any other element, the live view and replays draw it
moving, and its joint shows in `get_state` (`element.<name>.hinge` or `element.<name>.slide`). A robot's stock
elevator or arm only touches it with [`collide = true`](parts.md#stock-parts-that-collide).

Both kinds take the common keys (`name`, `kind`, `center`, `yaw_deg`, `friction`) and these. Every length also has
an `_in` form.

| key | kinds | unit | default | meaning |
|---|---|---|---|---|
| `mass` | both | kg | required | the panel's mass, all of it moving (a uniform box or disc) |
| `base_height` | both | m | 0 | the panel's bottom above the carpet, as described |
| `size` | `sliding`, and a `hinged` box | m | required | `[x, y, z]`, along the element's own x, y and z |
| `shape` | `hinged` | | `"box"` | `"box"` (takes `size`) or `"disc"` (takes `diameter` and `thickness`; it lies flat, its axis up) |
| `diameter`, `thickness` | `hinged` disc | m | required | the disc's |
| `hinge` | `hinged` | | required | the edge it turns on, the panel as described: `"bottom"` or `"top"` (about the element's y), `"left"` (+y side) or `"right"` (-y side), `"front"` (+x end) or `"back"` (-x end) (about z), or `"center"` (through its center) |
| `axis` | `hinged` (`"center"` only) | | the panel's thinnest axis | `"x"`, `"y"` or `"z"`: the axis a center hinge turns about. Left out, the thinnest one: a disc's own axis, so a disc spins flat like a wheel |
| `axis` | `sliding` | | required | `"x"`, `"y"` or `"z"`: the direction it slides, in the element's frame |
| `range_deg` | `hinged` | deg | none (no stops) | `[lo, hi]`, its stops. Leave it out for a panel that turns freely (a spinning wheel) |
| `range` | `sliding` | m | required | `[lo, hi]`, its stops along the slide (or `range_in`) |
| `rest_deg` | `hinged` | deg | 0 | where its spring rests. It must lie within `range_deg` |
| `rest` | `sliding` | m | 0 | where its spring rests (or `rest_in`). It must lie within `range` |
| `stiffness` | both | N·m/deg (`hinged`), N/m (`sliding`) | 0 (no spring) | the spring pulling it back to rest. Per degree on a hinge, so it reads like a door spring's rating |
| `damping` | both | N·m·s/rad (`hinged`), N·s/m (`sliding`) | critical, or 0.05 | with a spring, left out it is critical damping for the panel (it settles at rest without swinging past). With no spring it is 0.05 (an estimate: enough to settle a panel knocked loose, not enough to feel) |
| `breakaway` | both | N·m (`hinged`), N (`sliding`) | 0 | the push it takes to move it (see below) |
| `preload` | both | N·m (`hinged`, or `preload_in_lb` in inch-pounds), N (`sliding`, or `preload_lb` in pounds) | 0 | the spring wound up against a stop: the push it takes to start the panel moving off it (see below). Needs a `stiffness` and the rest at one end of the range |
| `ground` | both | | `false` | `true` makes it floor for the terrain signals, as a box's `ground`: a drawbridge a robot drives over |

All angles and travel are measured from the panel **as described** (its `size` and `base_height`, before it turns).
The element is built at its rest position, and its signal reads 0 there.

**Which way is positive.** Positive is a right-hand turn about the hinge's axis, or travel along the slide's axis,
in the element's frame (turned by `yaw_deg`):

| `hinge` | turns about | positive |
|---|---|---|
| `"bottom"` | its bottom edge, along y | the top tips toward +x |
| `"top"` | its top edge, along y | the bottom swings toward -x |
| `"left"`, `"right"`, `"front"`, `"back"` | that edge, along z | counter-clockwise seen from above |
| `"center"` | its `axis` through its center | a right-hand turn about that axis: counter-clockwise seen from above for a flat disc (z); about y, the +x end goes down |
| (sliding) | its `axis` | along +x, +y or +z |

So a door that should swing only toward -x on its `"left"` hinge has `range_deg = [-90, 0]`.

**Breakaway** is MuJoCo's joint friction (frictionloss). It is the push needed to start the panel moving **and to
keep it moving**: the model has no separate starting and sliding friction. A push under it holds the panel still
(its friction is as stiff as the stops). On a hinge it is a torque: a force F at a distance r from the hinge is
F × r.

**Stops** are stiff (a 5 ms time constant, an estimate, the same as a platform's) and critically damped, so a panel
lands on its stop without a bounce. Gravity acts on every panel: a portcullis on a vertical slide with no spring
rests on its lower stop, and lifting it takes its weight.

**Preload** winds the spring up against the stop the rest is on, so the panel sits pressed into that stop and takes
that much push to start moving off it: a door that takes a set force before it starts to move (the 2016
drawbridge's 2 lb), or a return spring that holds a door shut. Put the rest at one end of the range (`rest_deg` or
`rest` equal to `range_deg`'s or `range`'s low or high end) and give a `stiffness`: the spring then pushes `preload`
at rest and `preload` plus `stiffness` times the travel once moved. On a hinge it is a torque: 2 lb at a door's top
edge 37 in up is `preload_in_lb = 74` (2 × 37).

**Starts and floor balls.** A panel standing on the carpet (`base_height` 0) blocks a start and floor balls, as a
box does; one raised off the carpet doesn't, so a robot may start under it. Both use the panel as described.

A double door is two elements; a panel on more than one joint, or a latch that holds a door open, isn't modeled.

## The 2016 defenses

The built-in range `2016-defenses` has four of the 2016 FIRST STRONGHOLD DEFENSES that move, each on its 3 in
PLATFORM, from the 2016 Game Manual, section 2.2.2.1. They stand in a line across the field (along y), each at
x = 4.0 m and crossed along +x, as on the real OUTER WORKS; the spacing between them is ours. Each has a start 1.5 m
before its near ramp, facing it: `[start.portcullis]`, `[start.sally_port]`, `[start.drawbridge]` and
`[start.cheval_de_frise]`.

| defense | element | how it moves | the numbers |
|---|---|---|---|
| portcullis | `portcullis` (`sliding`, up along z), between two posts | lift it from its bottom edge | 2.27 kg, no spring or breakaway, so it takes its weight to lift: the manual's "approximately 5 lbs." (5 lb = 2.27 kg). A 5 in gap under it (manual). Its 43 x 30 in size and its 24 in of travel are estimates |
| sally port | `sally_port` (`hinged`, `"left"`, `range_deg = [-90, 0]`) | swings only toward -x | a breakaway of 8.135 N·m: the manual's 1.5 lb at its outer edge, 48 in from the hinge. 48 x 24 x 1/4 in (manual); its 6 kg mass is an estimate |
| drawbridge | `drawbridge` (`hinged`, `"bottom"`, `range_deg = [-90, 0]`, `ground = true`) | lowered toward -x, then driven over | a spring wound up against its closed stop (`preload_in_lb = 74`): 2 lb at its top edge to begin to move it, as the manual says, and about 4.9 lb to hold it flat (the manual's at most 5 lb). 37 in tall and 1/4 in thick (manual); its 41 in width, 7.5 kg, spring (0.52 N·m/deg) and damping (3 N·m·s/rad) are estimates |
| cheval de frise | `cheval_1` to `cheval_4` (`hinged`, `"center"`, `axis = "y"`) | each plate tilts about a pipe across the lane | 24 x 12 x 1/2 in plates (manual), resting on alternate sides at ±23.6° (1 and 3 with the far end down). Pressing one level takes 1 lb at its edge (manual). Their spacing, height, 3 kg mass and spring are estimates |

Things to know:

- **The doors open toward the robot.** A robot driving in along +x presses the sally port and the drawbridge against
  their closed stops. They open by being pulled toward the robot (-x), or, for the drawbridge, by pressing its top
  edge down once it has started toward you, as on the real field.
- **The drawbridge takes 2 lb to start.** Its spring is preloaded against the closed stop (see **Preload** under
  [Moving elements](#moving-elements-hinged-and-sliding)). Once moving, the push at its top edge dips to about 1.6 lb
  near 30° (the spring is a little weaker per degree than the door's own weight there) and climbs to about 4.9 lb to
  hold it flat; the manual says only that it increases to at most 5 lb. The sally port's return spring isn't
  modeled: the manual gives only the 1.5 lb to open it, which is its breakaway here.
- **The cheval plates spring back toward rest.** Pressed down, a plate returns to its own side; it doesn't tip over
  to the other side, as a weighted real plate can.
- Every number cites its source on its line in `scrimmage/testrange/ranges/2016-defenses.toml`, or says it is an
  estimate or our choice.

## The terrain signals

The terrain signals are read every physics step on a range with elements (or once a scenario names one). A scenario
check names them like any other signal: `always robot.ground_gap max 0.05`, `always robot.tipped equals 0`.

A range with elements (now including the 2026 range) runs the terrain monitor every physics step, about 10 % on the
shooting configs, because the tipping and stranding notes need it.

**Ground** is the carpet and the elements, nothing else: a ball is never ground. The **tread wheels** are the robot's
drive wheels (every geom with `tread = true`: the stock swerve modules' and differential drive's wheels). A tread
wheel is **down** when it touches the ground, or its bottom is within 5 mm of it (`WHEEL_DOWN_GAP`: a wheel
scrubbing sideways in a turn loses contact on most physics steps while still on the carpet).

| signal | meaning |
|---|---|
| `robot.ground_gap` (m) | 0 while any tread wheel is down; else the smallest distance from a tread wheel's lowest point straight down to the ground. A robot stranded on FUEL reads its real height above the carpet. |
| `robot.wheels_down` | how many tread wheels are down |
| `robot.air_time` (s) | how long no robot geom has touched anything outside the robot; 0 while it touches anything, a ball included |
| `robot.pitch`, `robot.roll` (deg) | the Pigeon 2's convention, so they match a team's gyro logs: **nose down is positive pitch**, left side up is positive roll |
| `robot.tilt` (deg) | the angle between the chassis's up and vertical, 0 to 180 |
| `robot.z` (m) | the chassis origin's height |
| `robot.stranded` | 1 once fewer than 2 tread wheels have been down for more than 0.5 s of physics steps on which the robot touches something (balls, an element's edge). Steps in the air pause the clock; two wheels down reset it, and so does hanging from a bar or a switch (touching one and no ground; see `scrimmage/terrain/climb.py`). |
| `robot.tipped` | 1 once tilt passes 60 deg, latched until the world resets |
| `element.<name>.angle` (deg) | a platform's tilt, positive when its +x end is down |
| `element.<name>` (deg or in) | a hinged element's angle (degrees, unwrapped, so three turns read 1080) or a sliding one's travel (inches), 0 at rest, [positive as above](#moving-elements-hinged-and-sliding) |

The thresholds are named constants in `scrimmage/terrain/monitor.py`: `TIP_DEG` 60, `STRANDED_S` 0.5,
`STRANDED_WHEELS` 2, `WHEEL_DOWN_GAP` 0.005.

Scenario checks tick every lockstep step, several physics steps apart, so each check reads the **peak** since the
last tick: the largest gap, air time, tilt and z, the pitch and roll of largest magnitude (sign kept), the fewest
wheels down, and stranded or tipped if either was at any step. A 3 ms hop can't slip between two ticks.

When the robot tips or is stranded, the scenario report notes when, how, and where its mechanisms were then.

## Approach angle and breakover

A robot can only drive onto a ramp its bumpers clear. The **approach angle** is the steepest slope the front wheels
can start up before the front bumper's bottom edge hits it:

    approach angle = atan(bumper clearance / overhang)

with the **clearance** the bumpers' bottom above the carpet and the **overhang** how far the bumper's front face
reaches past the front wheels' axles. For the stock chassis on corner modules the overhang is `bumper` +
`module_inset`.

| robot | clearance | overhang | approach angle | the 2026 BUMP (15 deg) |
|---|---|---|---|---|
| stock chassis default (0.7 m frame, inset 0.0579 m) | 0.02 m | 0.138 m | 8.2 deg | stops against the ramp |
| configs/fastmode.toml's robot | 0.02 m | 0.176 m | 6.5 deg | stops against the ramp |
| the same, `bumper_clearance = 0.06` | 0.06 m | 0.176 m | 18.8 deg | drives on |
| 0.15 m overhang, `bumper_clearance = 0.06` | 0.06 m | 0.15 m | 21.8 deg | drives on |

Every clearance here is `bumper_clearance`, as built. Resting on its treads the robot sinks about 1 mm, so the stock
bumpers sit 19 mm up and the angles are a few tenths of a degree less.

The stock chassis's `bumper_clearance` defaults to 0.02 m, as the bumpers always were, so existing robots don't
change. **The default can't climb the 2026 BUMP**: crawling at 1 m/s its bumper meets the ramp and it stops,
nose up 7 deg. (Faster, it can bash its way on.) Real 2026 robots were built to cross it: set your chassis's
`bumper_clearance` to your robot's. The 2023 CHARGE STATION's ramps are steeper still, 31 deg as modeled.

**Breakover.** Straddling a crest, front wheels on the far ramp and rear wheels on the near one, the frame and the
side bumpers between the axles must clear the crest. For a bump with ramps at angle a and a wheelbase w, that's
about clearance > (w / 2) tan(a): 0.068 m for the fast-mode robot (w = 0.508 m) on the 2026 BUMP. At 0.06 m its side
bumpers brush the crest at a crawl, which is what a real robot with that clearance does.

## `scrimmage terrain sweep`

    scrimmage terrain sweep CONFIG --element NAME --speeds FROM:TO:STEP [--set JOINT=VALUE]... [--gap M] [--tilt DEG] [--out DIR]

drives the config's robot straight over one element of its range at each speed and writes `sweep.csv` and
`sweep.md` to `DIR` (default `<CONFIG's folder>/<CONFIG stem>-<element>-sweep`). No robot program runs, so no Java
and no Gradle: a sweep of seven speeds takes a few seconds.

- `CONFIG` is a robot config with a `[range]` whose range file has the element. Its `[robot_program]` is ignored.
- `--speeds 1:4:0.5` runs 1, 1.5, ... 4 m/s, both ends included.
- `--set elevator.carriage=0.9` holds that joint at 0.9 (m for a slide, rad for a hinge) for every run. Repeat it for
  more joints (`--set arm.pivot=1.2`). Joint names are the part's name and its joint's (`elevator.carriage`); an unknown
  name lists them all. Joints you don't set are left to their motors at 0 V (coasting, or braking in
  brake mode): an unheld arm sags onto its stop.
- `--gap` (default 0.05 m) and `--tilt` (default 30 deg) are the limits a run must stay within. The tilt limit is on
  `robot.tilt`, the chassis's up from vertical: pitch and roll together, so 25 deg of pitch with 20 deg of roll is
  31.6 deg and fails a 30 deg limit.

### Each run

1. The robot starts at the range's `[start.approach]` when that start faces the element; else 1.5 m (its center)
   before the element's near toe, facing along the element's x.
2. The held joints are set to their values, and the robot settles on its treads for 0.3 s.
3. It is set rolling at the speed (already at speed, so it doesn't spin its wheels up), and the **driver** keeps it
   there: every swerve azimuth held at 0 rad (straight ahead), every tread wheel servoed to `speed / r`, every
   `--set` joint held by a stiff PD. A differential drive's wheels are driven the same way. The driver replaces the
   motors on those joints, so coast and brake mode drive alike.
4. The run ends 1.5 s after the robot's CoM passes the element's far toe (so it lands and settles), or 6 s after it
   started rolling (a robot stopped against the element).

### Each row

| column | meaning |
|---|---|
| `speed` | m/s |
| `peak_gap` | `robot.ground_gap`'s peak over the run (m) |
| `air_time` | the longest time the robot touched nothing (s) |
| `landing` | how far the robot's CoM is past the crest (m, along its path) as the run's longest flight touches down; blank if it never left the ground. The crest is a bump's ridge, a two-sided ramp's or a platform's middle, a one-sided ramp's high edge. The longest flight is the jump: a hop off the toe lip or off the top of the climb lands first, nearer. |
| `peak_pitch`, `peak_roll` | the pitch and roll of largest magnitude (deg, sign kept, the Pigeon 2's convention). Climbing reads about -15, nose down off the crest positive. |
| `peak_tilt` | `robot.tilt`'s peak (deg): the chassis's up from vertical, pitch and roll together, 0 to 180 |
| `tipped`, `stranded` | `robot.tipped` and `robot.stranded` at any time in the run |
| `landed_on_balls` | a ball touched the robot from below (the contact's normal, ball to robot, within 45 deg of up) while the robot was in the air or within 0.3 s of it. A ball the bumper pushes along the carpet touches it side-on and doesn't count. |
| `crossed` | the CoM passed the element's far toe. `no` means the robot stopped against the element. |
| `pass` | crossed, never tipped or stranded, peak gap at most `--gap`, peak tilt at most `--tilt` |

The **speed limit** is the fastest speed whose row, and every slower row, passes: the speed below which every run
was safe. If the slowest speed fails there is none, even if a faster run happens to pass (a robot that bashes over a
ramp it can't crawl up).

### Example: the fast-mode robot on the 2026 BUMP

`configs/fastmode.toml`'s robot (a 0.7 m swerve chassis, 1 m elevator, an arm) with `bumper_clearance = 0.06` on
`2026-bump`, elevator down:

| speed (m/s) | peak gap (m) | air time (s) | landing (m) | peak pitch (deg) | peak roll (deg) | peak tilt (deg) | tipped | stranded | landed on balls | crossed | pass |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.010 | 0.046 | 0.01 | 16.6 | 0.3 | 16.6 | no | no | no | yes | yes |
| 1.5 | 0.010 | 0.022 | 0.44 | 17.6 | 0.0 | 17.6 | no | no | no | yes | yes |
| 2 | 0.036 | 0.128 | 0.57 | 21.9 | -0.8 | 21.9 | no | no | no | yes | yes |
| 2.5 | 0.084 | 0.140 | 0.90 | 18.8 | 0.6 | 18.8 | no | no | no | yes | no |
| 3 | 0.144 | 0.142 | 0.72 | -39.2 | -7.2 | 39.2 | no | no | yes | yes | no |
| 3.5 | 0.177 | 0.165 | 3.08 | 88.1 | 180.0 | 180.0 | yes | yes | yes | yes | no |
| 4 | 0.176 | 0.268 | 2.35 | 88.8 | -180.0 | 180.0 | yes | yes | yes | yes | no |

**speed limit over bump: 2 m/s**. With `--set elevator.carriage=0.9` the limit drops to 1 m/s: at 1.5 m/s the
top-heavy robot pitches further off the crest (27 deg), rides onto the FUEL and is stranded, and at 2 m/s it lands
on the FUEL and tips.

`2026-bump`'s FUEL lies 0.45 m past the far toe, where a robot that leaves the bump too fast lands. The run goes on
1.5 s past the toe, so every robot reaches the row; past about 2.5 m/s, what happens on the FUEL decides the row (as
on the field). To sweep the bump alone, set `floor_balls = false` in the config's `[range]`: then the same robot,
elevator down, lands without tipping up to 4 m/s and the limit is still 2 m/s (the gap limit decides it).

### In a robot program: the fast-mode sample

`configs/fastmode_bump.toml` is the same robot on `2026-bump` with `bumper_clearance = 0.08`, clearing both the
approach angle and breakover. Its sweep gives the same limits, 2 m/s elevator down and 1 m/s up (0.058 m of gap at
2.25 m/s). The fast-mode sample's "Over the Bump" autos ([docs/fast-mode.md](fast-mode.md#over-the-2026-bump)) put
those in `ScrimmageConstants`, and their scenarios check them:

```toml
  [[step.check]]
  always = { signal = "robot.ground_gap", max = 0.05 }
  [[step.check]]
  always = { signal = "robot.tipped", equals = 0 }
```

Driven by its own code, the robot has more margin than the sweep says, as the sweep's driver errs on the safe side
(below): at 2 m/s it leaves the ground by 0.009 m, and told to go 2.5 m/s it slows to about 2 m/s on the climb and
peaks at 0.043 m. At 3 m/s it fails the check (0.082 m). The sweep's limit is the one to put in the auto.

## Estimates

These numbers aren't measured. Each is marked "estimate" where it is set.

- **Tread** (`tread_stiffness`, `tread_damping` on the stock swerve modules and differential drive): 2e5 N/m per
  wheel, critically damped. A 5 mm layer of 70A to 95A urethane under a 1 to 1.5 in wide, 1 cm long contact patch
  gives 1e5 to 3e5 N/m (k = E A / t). Dropped 0.15 m, a 49 kg swerve robot sinks 16 mm into its treads, short of
  the stock bumpers' 19 mm. Checked against hand calculations in `tests/test_terrain_physics.py`: a flight off a
  15 deg ramp lands where projectile motion says, within 2 cm, with no rebound.
- **HDPE friction**: 0.6 for the 2026 BUMP's matte HDPE against a tread. **Known gap:** MuJoCo combines two
  surfaces' friction by taking the larger, so a tread (1.1 to 1.2) grips the bump as well as the carpet, and the 0.6
  never applies to it; it applies to bumpers (0.3) and balls. Climbing 15 deg needs only 0.27, so traction doesn't
  limit the climb, but slides on HDPE are understated.
- **Platform damping**: 30 N m s/rad on each hinge of the 2023 CHARGE STATION. No damper is drawn; the station
  settles in one or two swings after a robot drives off (match video), which 20 to 50 gives.
- **Platform stops**: the hinges' limits at +-`max_tilt_deg`, stiff (a 5 ms time constant) and critically damped, so
  the deck lands on its stop without a bounce. How the real stops work isn't drawn.
- **Moving elements' stops and damping**: their stops are a platform's (above). With no spring and no `damping`
  given, a panel has 0.05 N·m·s/rad (or N·s/m), enough to settle a panel knocked loose.
- **The built-in moving elements**: the 2016 defenses' sizes, masses and springs (above; each marked in the range
  file), the 2020 CONTROL PANEL's 8 kg (its two 1/4 in polycarbonate discs) and its 0.2 N·m breakaway (a lazy susan
  bearing's drag, not published), and the 2026 TRENCH arm's 47 x 18 in section (the manual gives its 22.25 in
  clearance and 50.34 in opening, not the arm's size).
- **The sweep's driver** is ideal: each wheel holds its speed with no motor torque limit (traction limits it), as a
  robot's velocity loop does within its motors' torque. A robot whose drive motors can't hold speed up the ramp is
  slower there than the sweep says. So `crossed`, and a faster run that bashes over an element the robot can't
  crawl up, assume drive torque up to traction: a current-limited drivetrain may stall where the sweep crosses. For
  the gap and air time the sweep errs on the safe side: a robot that can't hold its speed leaves the crest slower
  and jumps less than the sweep says.

## What isn't modeled

- Suspension, or treads deforming beyond a contact stiffness. No stock drivetrain has suspension.
- Mechanisms hitting the field, unless they say so: a stock elevator or arm touches nothing until its part has
  [`collide = true`](parts.md#stock-parts-that-collide); then it catches on a TRENCH arm like a real one.
- A moving element's separate starting and sliding friction, a spring preloaded against a stop, a latch that holds a
  door open, and a panel on more than one joint ([Moving elements](#moving-elements-hinged-and-sliding)).
- FUEL's squash: a ball is a stiff sphere, so a robot rides up on it rather than crushing it.
- The CHARGE STATION's hinged ramps: these turn with the deck rather than sliding on the floor.
