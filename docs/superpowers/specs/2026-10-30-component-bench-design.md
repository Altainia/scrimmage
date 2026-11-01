# Component bench: try a mechanism idea in minutes (design)

Sub-project 13. Status: design, 2026-10-30.

## Why

At kickoff, the team brainstorms mechanisms for a few days, then the head coach (rightly) wants to start building.
Today the ideas that get heard come from the one or two students who draw and explain well; others wait for Robot in
3 Days and similar teams to show tested ideas, which costs half a week and arrives after the design is mostly
settled. Teams with more people, money, machine time or industry mentors physically prototype many ideas at once, or
are steered to proven mechanisms quickly. This team has lost to that gap, and once built a mechanism that sounded good
on paper but kept breaking however it was patched.

The bench is how the team gets more tries, and more chances to be wrong cheaply, in the first week:

1. **Ideas.** Any student's idea becomes something that runs and can be seen, whatever their drawing or CAD skill.
2. **Dimensions.** The winning idea's numbers (gaps, wheel spacing, squish, the shape of a hood, where a bar goes)
   are narrowed by simulation, and CAD starts from tested values, not guesses. Physical prototyping still happens,
   later and less of it.
3. **Robot** (a later project, not this one). The chosen mechanism goes on a simulated robot so programmers can
   work on autos and drivers can practice strategy before the robot exists.

Success: in the first week after kickoff, the team compares several launcher (or intake, or indexer) ideas from
several students and rules out the weak ones before building any of them.

## Who uses it, and how

**Year one: one operator, a room of students.** Students don't have AI accounts (the mentor can't rely on it; a
school arrangement may come later). The mentor runs Claude Code on a laptop or desktop, voices each student's idea to
the agent, and the room watches the results, likely on a projector. Later years: students with their own access do
the same themselves.

The flow for one idea:
1. A student describes an idea ("two wheels pinch the ball up a curved ramp, then it hits a bar and drops into a
   hopper"). The mentor says it to the agent.
2. The agent writes a **bench file** (plain text), runs it, and shows the room a picture of a pass: the mechanism
   from the side with each piece's path drawn through it, and a few headline numbers in plain words.
3. The room reacts ("tighter gap", "move the bar up"); the agent edits the file and runs it again.
4. Every idea is kept as its own file with the student's name and a one-line description. At the end of the session
   the team has a gallery of every idea.

The bench also runs without an agent: from the command line and the 3D viewer, so students without accounts can
rerun, tweak and watch ideas after the session. The agent is the fast way in, not the only one.

Students mostly use Autodesk Inventor for CAD. FreeCAD is the CAD route here (Inventor parts reach it as STEP); an
Inventor add-in may come later and is not part of this project.

## Goals

- A bench file format an agent writes easily from a spoken idea, and a person can read: wheels, plates, bars,
  rollers, curved plates, inlet and outlet zones, a game piece and a feed. Lengths in meters or inches.
- Physics good enough to compare ideas and choose dimensions, for both rigid pieces (cars, cubes, cones, discs,
  hard balls) and squishy pieces (foam balls), with the existing dedicated models as reference points.
- Results a room can read: a picture of the pass, plain-word headlines, and the numbers behind them (exit speed,
  direction and spin; jams; wheel slowdown and recovery; the load on every part).
- Loads that point at parts worth worrying about ("this bar takes 600 N every shot"), since a mechanism that breaks
  is as bad as one that doesn't work.
- Quick turnaround: a rough idea with about ten pieces runs and shows pictures within about a minute on the mentor's
  machine, so the room doesn't stall.
- Comparison of two or more ideas under the same piece and feed, a sweep of any number in a file, and a gallery of a
  session's ideas.
- CAD in place of rough shapes when the design is chosen: an exported FreeCAD part, or a mesh file straight from
  Inventor for parts that don't move.
- Agent tools (MCP) so the agent runs, shows, sweeps and compares without shell gymnastics.

## Non-goals

- **The robot stage.** Putting a bench mechanism on a robot in the full field, with the robot code driving it, is a
  separate later project (the bench is the standalone component model of the fidelity guideline; the robot stage is
  the whole-robot form). Nothing in this project should make that harder: the bench's mechanism description and
  contact laws should be usable there.
- **A component library** of ready-made blocks ("hooded wheel", "pinch pair") tweaked per game. Later. The file
  format must allow reuse (see `include`) so the library can grow on it.
- **Whether a part bends or breaks.** Parts are rigid. The bench reports loads; stress analysis (FEA) of real CAD
  parts is a separate step.
- **An Inventor add-in.**
- **A click-together editor** for bench files. The browser assembler may learn bench files later.
- **Robot code in the loop.** Bench motors run on built-in control (a speed target or a fixed voltage), as the
  launcher model's do.

## Anti-goals (would make it a failure even if it technically works)

- Getting an idea in needs CAD or drawing skill. That recreates the problem.
- Results that look authoritative when they aren't. Every result is labeled UNCALIBRATED, as the launcher model's
  are, and the known disagreement for squishy pieces (below) is stated in the docs, not hidden.
- A loop slow enough that the room waits on the computer.
- Only usable through an agent.
- Physics that creates energy, or results that change from run to run of the same file.

## What the spike found (2026-10-30)

A throwaway test built the example hooded launcher (`examples/launcher/hooded.toml`) from plain shapes and compared
exit speeds with the dedicated launcher model, and fired a rigid toy-car block between two powered wheels.

- **Rigid piece, MuJoCo's own contacts:** sensible. The car left at 86 to 90% of the wheels' surface speed (the
  wheels slowed 9 to 12% under the load), stayed upright, and gave the same results at 1 ms and 0.1 ms steps. A 5 mm
  off-center entry sent it 8 to 10 degrees off line (so guides matter, and the bench shows that).
- **Foam ball, MuJoCo's own soft contacts:** not good enough. A curved hood must be cut into flat pieces, and the
  ball touched about ten of them at once, each pushing. Low-speed (grip) exits came out 13 to 17% low, launch angles
  up to 9.5 degrees off, the trend with squish was lost, and the result depended on how finely the hood was cut (up
  to 7%).
- **Foam ball, hybrid:** MuJoCo finds where the ball touches each shape; the launcher model's own laws
  (`scrimmage/launcher/contact.py`: the power-law Hunt-Crossley squeeze with the wheel rubber in series, regularized
  Coulomb friction, Tabor rolling loss) give the forces, applied to the ball and the touched bodies; contacts with
  one body whose normals are within 30 degrees are merged, the deepest kept. Within 1% of the launcher model at
  2000 rpm (grip) at 0.15, 0.21 and 0.28 D squish; at 4000 rpm (slip) about 20% higher at 0.15 and 0.21 D and equal
  at 0.28 D; launch angle within 1 to 4 degrees. About 1.3 s per ball in plain Python at a 10 microsecond step.

The 4000 rpm gap is where the two models differ: the launcher model fixes the ball on a circle round the wheel; the
hybrid lets it move outward under its own speed, pressing harder on the hood and less on the wheel. Which is closer
to reality is unknown; the launcher model is itself uncalibrated and believed to read low. No measured shot is
expected soon, so the bench ships with the gap documented.

## The design

### Contact laws by piece

Each game piece is **rigid** or **squishy**; the piece decides the law for every contact it has.

- **Rigid** pieces (and the wheels' treads against them) use MuJoCo's own contacts. Wheel treads are compliant, as
  the stock wheels' treads are (`tread_solref` in `parts/assemble.py`). A rigid piece may also carry its own contact
  stiffness, in series with the tread, so a pinch squeezes both as the disc launcher model does (a disc's rim, preset
  5000 N/m, is far softer than a 50 to 100 kN/m tread and takes most of the squeeze). The series stiffness is set
  per contact pair in MuJoCo; a piece with no stiffness of its own is hard and only the tread gives.
- **Squishy** pieces use the hybrid: MuJoCo geometry, the launcher model's laws, contact merging per touched body.
  Piece-to-piece contacts between squishy pieces use the same law (two soft bodies in series). A squishy piece
  never collides through MuJoCo's own contacts.
- Pieces come from the existing presets: the launcher model's balls (`2020-power-cell`, `2022-cargo`, `2026-fuel`)
  as squishy, and the disc launcher's discs (`2013-frisbee`, `ultra-star`) as rigid with their rim stiffness. A custom
  piece gives a shape (sphere, box, cylinder, disc, or a mesh), mass, inertia (or a default for the shape) and
  `rigid` or `squishy`; a squishy piece also gives the launcher model's ball numbers (stiffness, exponent,
  restitution), defaulting from the nearest preset with a warning.
- **Squishy pieces are balls only, in this project.** The launcher model's laws are written for a sphere. A squishy
  piece of any other shape (a foam ring like 2024's, a fabric cube like 2023's) is an error that says so and
  suggests a rigid stand-in. Squishy non-ball pieces are a later project, started right after kickoff if the 2027
  game piece needs it.
- Contact merging (squishy only) is what makes faceted curves and CAD meshes work: with it, how finely a curve is
  cut must not change a result by more than 2% (an acceptance check, below).

### The bench file

TOML, like every other input here, and written for agents first: shapes are given by what they are, not as raw
geometry, every unknown key is an error that names the valid ones, and every length key has an inch form
(`<key>_in`, give one of the two), as the field elements do, because FRC students think in inches.

The bench frame: x forward (the usual exit direction), z up, y across. A side view looks along y. Angles are in
degrees and end in `_deg`.

Sections (names are the design's; the builder may refine keys but not drop capabilities):

- `[bench]`: `name`, `author` (the student), `idea` (one line, the student's own words), optional `notes`.
- `[piece]`: `preset` or a custom piece (above).
- `[[wheel]]`: a powered or free-spinning wheel or roller: position, axis, diameter, width, tread (the launcher
  model's tread presets, `35A` etc.), and its shaft. Wheels name a `shaft`; wheels on one shaft turn together. A
  shaft has a motor (the `motors.py` names), count, gearing, wheel and added (flywheel) inertia, and control: a speed
  target (the launcher model's velocity controller, with current limits) or a fixed voltage, or none (a free
  roller). Two shafts can be geared or belted together with a ratio. All shafts share one battery.
- `[[plate]]`: a flat plate (box): position, size, rotation, material.
- `[[bar]]`: a round bar or tube (capsule or cylinder): ends or position and axis, diameter, material. Optionally
  springy (a stiffness and travel), for a bounce bar.
- `[[curve]]`: a curved plate: an axis, a center, a radius at each end (equal for an arc, unequal for a flare or a
  spiral), the angle span, width, thickness, material. This covers hoods, curved ramps and funnels. The bench cuts it
  into flat pieces internally; the agent and the student never see that.
- `[[cad]]`: a CAD part in place of rough shapes (see "CAD", below).
- `[[inlet]]`: where pieces appear: a zone (a box), how many, how often (`rate`), their velocity and spin, and a
  spread (position, speed, timing) with a seed. Several inlets are allowed (a two-lane feed).
- `[[outlet]]`: where pieces leave and are taken out of the bench: a named zone. A piece in an outlet is recorded
  (time, position, velocity, spin) and removed. Several outlets are allowed (`shot`, `hopper`, `reject`). A piece
  that leaves the bench volume elsewhere is `escaped`.
- `[flight]` (optional): hand pieces from an outlet to the existing flight models (the launcher model's ball flight,
  the disc flight), with a target (a distance and height, or a ring as in `[goal]`), so the result includes where
  shots land.
- `[run]`: duration limit, timestep (left to the builder, with a sensible default per law), jam rules.
- `[sweep]` and `[[chart]]`: as in the launcher model (dotted keys, every combination).
- `include`: a list of other bench files whose shapes and settings are merged in first (the file's own entries win;
  a clash is an error that names both files). This is the hook for the later component library and for
  "same piece and feed as Maya's".

Materials: a short named list (polycarbonate, aluminum, steel, UHMW, nitrile and the tread presets) giving friction
against foam and against rigid pieces, with sources or "estimate" stated as the presets do. A material name with no
entry is an error that lists them.

### Outcomes for each piece

`out:<outlet name>`, `jammed` (inside the bench, not making progress for a time set in `[run]`), `escaped` (left the
bench volume outside every outlet), `timed out` (still moving when the run ended), and `stuck at the inlet` (never
entered). Each outcome carries the piece's path (sampled), its time inside, and, for `out`, its exit speed,
direction (elevation and yaw) and spin.

### Results

- **Headlines** in plain words, three to six lines, for a room: "8 of 10 left through `shot` at 7.1 m/s ± 0.2,
  52° up", "2 of 10 jammed at the second wheel", "the bounce bar takes 600 N per shot", "the shooter shaft drops 9%
  and recovers in 0.3 s".
- **A path picture**: a side view (and a top view when anything moves across y) of the mechanism with every piece's
  path drawn, colored by outcome, and the outlets marked. It must read on a projector.
- **A contact sheet** of one pass (frames across a piece's time inside), and a short animated GIF of it (Pillow is
  already a dependency).
- **Tables and a CSV**: per piece (outcome, exit numbers, peak force), per wheel and shaft (speed drop, recovery,
  peak current, energy per piece), per part (peak force, peak impulse per pass, where on the part).
- **Loads**: each shape's peak contact force and its per-pass impulse, and the hardest hit, listed in the headlines
  when they stand out. These say which parts to worry about; they don't say whether a part breaks.
- **UNCALIBRATED** on every result, with what to measure (as the launcher model does).
- With `[flight]`: landing points and hits on the target.

### Comparing, sweeping, the gallery

- **Compare**: two or more bench files run with one piece and feed (taken from the first, or a named file) and
  shown as one table and one picture, side by side.
- **Sweep**: every combination of a file's `[sweep]` values, a table, a CSV and charts, as the launcher model does.
- **Gallery**: a folder of bench files becomes one self-contained HTML file (pictures embedded) listing every idea:
  its author, its one-line idea, its path picture and its headlines, plus a comparison table. It opens in a browser
  with no server, so it can be put on the projector or shared.

### Command line

`scrimmage bench run FILE`, `bench sweep FILE`, `bench compare FILE FILE...`, `bench gallery FOLDER`,
`bench view FILE` (the MuJoCo viewer playing a pass, slowed down, for a room that wants to see it move), and
`bench check FILE` (validate only, fast). Bad input prints `scrimmage: <what's wrong>` and exits 2, as the other
commands do. Results go to `<file's folder>/<file name>-results/` unless `--out` is given.

### Agent tools (MCP)

Added to `scrimmage-mcp`, alongside the robot tools: `bench_check`, `bench_run` (returns the headlines, the path
picture and the contact sheet as images), `bench_sweep`, `bench_compare`, `bench_gallery`. The agent writes and edits
bench files with its own file tools. The tools don't need a robot session and must not disturb one that is open.

`docs/bench.md` is written for both the operator and the agent: the format reference, examples, and a section on
turning a spoken idea into a bench file (guess sensible sizes from the game piece, say which numbers were guessed,
start rough, run early).

### CAD

When a design is chosen, any rough shape can be replaced by CAD, so rough and detailed versions run on the same
bench.

- **A part folder from the FreeCAD exporter** (`[[cad]] part = "parts/shooter"`, with a pose): its bodies are rigid
  and fixed to the bench, except joints that carry a motor slot, which become wheels on a shaft the bench file
  configures by slot name. Wheel bodies marked as wheels in the exporter use their collision cylinders. Bodies
  marked `collide` use their meshes.
- **A mesh straight from Inventor** (STL or OBJ, `[[cad]] mesh = "hood.stl"`, `units = "mm"` or `"in"`, with a pose
  and a material) for parts that don't move. Students can skip FreeCAD for hoods, ramps and bars.
- **Concave meshes** (a hood, a funnel) are split into convex pieces automatically when loaded, and the split is
  cached next to the mesh so a rerun is quick. A squishy piece's contact merging covers the seams; for a rigid
  piece, the seams are MuJoCo's own and the docs say so.

### Examples (`examples/bench/`)

- `hooded.toml`: the launcher model's hooded example, rebuilt (the reference check, below).
- `pinch_car.toml`: a toy car fired by two powered wheels (rigid).
- `bounce_bar.toml`: a two-wheel ball shooter with a springy bar at the exit.
- `curved_ramp.toml`: a roller pushing balls up a curved ramp into a hopper outlet (two outlets: `hopper` and
  `spill`).
- `two_stage_disc.toml`: two wheel pairs in a row firing a disc (rigid).
- `hood_shapes/`: three hood curves for the same launcher, ready for `bench compare` and `bench gallery`.

Every example is written as a student-voiced idea (an `author` and `idea` line) so the gallery demonstrates itself.

## Acceptance checks

1. **Reference, squishy:** `examples/bench/hooded.toml` against the launcher model at 0.15, 0.21 and 0.28 D squish:
   at 2000 rpm, exit speed within 3%. Launch angle is reported, not asserted: the launcher model's angle is the hood's
   exit angle by construction, while the bench's comes out of the physics after the ball leaves the hood (the spike
   gave 48.8 to 54.4 degrees against 50). At 4000 rpm the speed difference is reported in `docs/bench.md` with the
   spike's explanation, not asserted.
2. **Reference, rigid disc:** a single-wheel-pair linear disc launcher against the disc launcher model (same disc,
   wheels, gap and speed): exit speed within 5%. (This check is tied to the disc-launcher merge; see Constraints.)
3. **Faceting:** cutting a curve into 2x or 4x as many pieces changes a squishy piece's exit speed by under 2%.
4. **Energy:** for every run, a piece's exit kinetic energy (linear plus spin) never exceeds its entry energy plus
   the work the wheels did on it plus the potential energy it lost. Checked in tests on every example.
5. **Determinism:** the same file gives identical results twice (no wall-clock or thread-order effects).
6. **Turnaround:** `examples/bench/hooded.toml` with ten balls fed at 5 per second (about 3 s simulated, the hood cut
   into its usual pieces, balls able to touch each other) runs, with its pictures, in about a minute on the
   development machine. Timed locally only; never asserted on CI (runners vary too much). Accuracy (check 1) is not
   traded for speed. If plain Python with numpy can't reach the minute, an optional compiled speed-up is acceptable
   as an optional extra, as long as the bench gives the same results, only slower, without it.
7. **Agent path:** an agent given only `docs/bench.md` and a spoken idea produces a valid bench file and a picture
   (checked once by hand during the build, with a fresh subagent, and the docs fixed where it stumbled).

## Constraints

- Python 3.11 or newer; MuJoCo 3.4 or newer. New dependencies only as optional extras (a convex-splitting library,
  for instance), and the bench works without them for files with no concave meshes.
- Reuse the launcher model's contact laws, shaft, controller, battery and flight, and the disc model's presets and
  flight, rather than copying them. Changes to those modules must leave their own tests and results untouched.
- Floats in tests compare with relative tolerances; no wall-clock assertions on CI.
- GitHub Actions minutes are nearly spent until 2026-11-01: verify everything locally; push once per PR.
- **Branching.** The bench needs the launcher model (on main), tread compliance (field-elements) and the disc
  presets and flight (disc-launcher), and those three branches merge from 2026-11-01. The bench branches from
  `climbing` (which contains field-elements); disc pieces, the disc example and acceptance check 2 come in a last
  step after disc-launcher reaches main and the branch is rebased onto it.

## Left to the builder

The module layout (a `scrimmage/bench/` package is the obvious one); how the hybrid is made fast enough
(vectorizing, an implicit treatment of friction, fewer geometry queries); timesteps and jam thresholds; how curves
are cut; the convex-splitting approach; picture styling; exact key names within the sections above.

## Decisions made in the conversation (the user's)

- Agent-first: the main way in is describing an idea to Claude Code. Clicking shapes together is nice to have,
  later.
- Year one runs through the mentor's account; the bench must work without an agent for everyone else.
- Inventor via STEP into FreeCAD for now; an Inventor add-in maybe later.
- Stages 1 and 2 (ideas, dimensions) now; the robot stage as its own later project; a component library after.
- No measured shot is available soon to settle the slip-regime difference.
- Squishy pieces are balls only for now (asked after the builder check); other squishy shapes are a later project.

## Decisions made while writing this (mine; open to change)

- The contact law follows the piece (rigid or squishy), not the mechanism.
- Discs are rigid pieces with compliant treads, not squishy.
- Inch forms for every length key.
- Curves are a built-in shape, so the most common idea (a hood, a ramp) needs no CAD.
- A mesh straight from Inventor is accepted for parts that don't move.
- The gallery is a self-contained HTML file, not a server.
- Loads are reported per shape, with no claim about breaking.
- The bench branches from `climbing`, with disc support last.
- Rigid pieces can carry their own contact stiffness in series with the tread (discs need it to match the disc
  launcher model). Changed after the builder check.
- Launch angle in check 1 is reported, not asserted. Changed after the builder check.
- The turnaround target scene is defined (check 6), and an optional compiled speed-up is allowed.

## Decisions made during the build

- A custom piece may be `shape = "disc"` (a short cylinder); disc presets wait for the disc-launcher merge.
- Mesh pieces need `units` ("m", "mm" or "in"), like `[[cad]]` meshes, and collide as their convex hull.
- Rigid contacts: shapes outrank pieces by geom priority (the shape's material sets friction); a tread is
  2e5 N/m in series with the piece's own stiffness, using MuJoCo's own solver mass (`tread_solref`'s contract).
- The bench's bounds include its inlet and outlet boxes, so an outlet past the shapes still catches pieces.
- Shafts start at rest; pieces are fed once every speed-controlled shaft is within 2% of target (or after 2 s),
  as the launcher model waits for speed.
- Squishy friction and rolling loss are applied semi-implicitly, so the default 1e-4 s step is stable and within
  0.1% of a 1e-5 s step; ten balls through the hooded example run in about 3 s.
- A ball's inlet is checked against its radius exactly (a box grown by the ball refused feeds next to a wheel).
- Measured agreement with the launcher model at 2000 rpm: −1.6%, −1.6% and −1.9% at 0.15, 0.21 and 0.28 D squish;
  at 4000 rpm +20%, +18% and −1.5% (the documented slip-regime difference).
- `[[cad]]` part folders: a joint with a motor slot becomes the named shaft's hinge; other joints are welded with a
  warning; the part's masses aren't used (the `[shaft]` inertia is); a `tread` key sets exported wheels' tread.
- A mesh must be a closed, consistently wound solid (checked after welding repeated vertices) before its
  concavity is judged; concave meshes are split with the optional `coacd`, cached beside the mesh.
- Results carry warnings (such as a squishy ball defaulted from the nearest preset) to the CLI, the agent tools
  and summary.txt; a failed energy check is the first headline; every picture says UNCALIBRATED; path pictures
  draw the piece at true size where it starts each touch and where it ends.
- Not yet done: disc pieces and acceptance check 2 (after disc-launcher reaches main).
