# Component bench

`scrimmage bench` tries a mechanism idea with game pieces in a minute or so: wheels, plates, bars, rollers and
curved hoods described in a short text file, game pieces fed in, and a picture of where every piece went, with a few
plain-word numbers. It is for the first week after kickoff, when a team has more ideas than time to build them: every
student's idea becomes something the room can see run, the weak ones are ruled out before anyone cuts metal, and the
winner's dimensions (gaps, wheel spacing, squeeze, hood shape, where a bar goes) are narrowed before CAD starts.

> **Every result is labeled `UNCALIBRATED`**, pictures included. The ball, tread and material numbers are estimates until you measure
> your own ([measuring.md](measuring.md)). Use the bench to compare ideas with each other and to see trends ("a
> tighter gap, a faster shot"); trust the differences before the absolute numbers. See
> [How accurate is this?](#how-accurate-is-this)

- [The flow](#the-flow)
- [A first bench file](#a-first-bench-file)
- [Turning a spoken idea into a bench file](#turning-a-spoken-idea-into-a-bench-file)
- [Pieces and contact laws](#pieces-and-contact-laws)
- [The format reference](#the-format-reference)
- [Results](#results)
- [Sweeps, comparisons and the gallery](#sweeps-comparisons-and-the-gallery)
- [How accurate is this?](#how-accurate-is-this)
- [CAD](#cad)
- [Troubleshooting](#troubleshooting)

## The flow

**In the room (year one).** Students don't need accounts. The mentor runs Claude Code on a laptop wired to the
projector, with the Scrimmage MCP server added (see the README's "Agents (MCP)"), and `scrimmage edit <the session's folder>`
open in a browser beside it: the [Bench tab](bench-tab.md) shows each idea, its picture and headlines as the agent
writes and runs it, and a student can change a number there and run it too. For each idea:

1. A student says the idea out loud: "two wheels squeeze the ball and fire it straight out". The mentor repeats it to
   the agent, with the student's name.
2. The agent writes a bench file in the session's folder (one file per idea, named after the student or the idea),
   checks it (`bench_check`) and runs it (`bench_run`), and shows the room the side picture and the headlines.
3. The room reacts: "tighter gap", "move the bar up", "what if the hood went further round?". The agent edits the
   file and runs it again. A question about a number ("which gap is best?") is a sweep (`bench_sweep`). The agent can
   pin the numbers being discussed on the page (`bench_focus`).
4. At the end, `bench_gallery` turns the folder into one HTML page with every idea, its author, its picture and its
   numbers, and a table comparing them all. It opens in any browser with no server: put it on the projector, or send
   it to the team.

**Without an agent.** The files are plain text and the commands need nothing else, so a student can rerun, tweak and
watch any idea after the session:

```bash
pip install -e .                                         # once, in the scrimmage folder
scrimmage bench check examples/bench/pinch_car.toml     # is the file right? (fast)
scrimmage bench run examples/bench/pinch_car.toml       # run it: headlines, tables and pictures
scrimmage bench view examples/bench/pinch_car.toml      # watch it move in the 3D viewer, four times slower
```

Open the file in any text editor (or in [the Bench tab](bench-tab.md)), change a number, run it again. The folder of results is printed at the end:
`path_side.png` is the picture, `pass.gif` the animation. **A rerun overwrites that folder**; to keep a run's pictures
to compare with the next, give each run its own folder: `scrimmage bench run idea.toml --out idea-tight-gap`.

`bench check` lists the first problem in each part of the file in one go (one per entry: each wheel, bar, hood or
table; each names its key), not just the first in the file, and prints warnings that don't stop a run, such as a moving part overlapping another shape. Fixing them top to bottom works.

**In the browser.** `scrimmage edit <folder or bench file>` opens the same ideas on [the Bench tab](bench-tab.md):
run, change numbers, edit the shape in 3D, sweep, compare and gallery, all writing these same files. It keeps a small
hidden `.scrimmage-bench/` folder in the session folder (the run log, the pinned numbers and who is running now), which
is how the page, the agent and the command line see each other's runs; add it to `.gitignore`. A run from the
command line in that folder waits, and says so, while the page or the agent is running.

The examples in `examples/bench/` are each written as a student's idea:

| File | Who | The idea |
|---|---|---|
| `hooded.toml` | | the launcher model's hooded shooter, rebuilt on the bench (the reference check) |
| `pinch_car.toml` | Maya | two wheels pinch a toy car down a track (a rigid piece) |
| `bounce_bar.toml` | Priya | shoot flat between two wheels, a springy bar bounces the ball up |
| `curved_ramp.toml` | Diego | a roller pushes balls up a curved ramp, over the top into a hopper |
| `hood_shapes/*.toml` | Sam, Aisha, Leo | three hood shapes on one launcher, for `bench compare` and `bench gallery` |

## A first bench file

Two wheels, one above the other, squeeze a 2026 FUEL ball and fire it forward. Save this as `pinch.toml` and run
`scrimmage bench run pinch.toml`:

<!-- bench -->
```toml
[bench]
name = "pinch shooter"
author = "Ava"
idea = "two wheels squeeze the ball and fire it straight out"
notes = "guessed: 4 in. 40A wheels, a 0.2 D squeeze, 1800 rpm, Kraken X60s direct"

[piece]
preset = "2026-fuel"             # 0.15 m foam ball

[shaft.bottom]
motor = "KrakenX60"
target_rps = 30                  # 1800 rpm
kP = 0.3
wheel_inertia = 0.001            # kg m^2: wheels, hubs and shaft

[shaft.top]
motor = "KrakenX60"
target_rps = 30
kP = 0.3
wheel_inertia = 0.001

[[wheel]]
name = "bottom"
shaft = "bottom"
center = [0.0, 0.0, -0.1108]     # 4 in. wheels with 0.12 m between treads: a 0.03 m (0.2 D) squeeze
diameter_in = 4
width = 0.1
tread = "40A"

[[wheel]]
name = "top"
shaft = "top"
center = [0.0, 0.0, 0.1108]
axis = [0.0, -1.0, 0.0]          # turned the other way, so both treads push the ball forward
diameter_in = 4
width = 0.1
tread = "40A"

[[plate]]
name = "feeder"
center = [-0.22, 0.0, -0.08]     # the ball rolls along it into the wheels
size = [0.26, 0.2, 0.01]

[[inlet]]
name = "feed"
center = [-0.25, 0.0, 0.001]     # the ball's center, 1 mm above the feeder
size = [0.001, 0.001, 0.001]
count = 5
rate = 3                         # balls per second
velocity = [2.0, 0.0, 0.0]

[[outlet]]
name = "shot"
center = [0.6, 0.0, 0.0]         # a 0.4 m box just past the wheels
size = [0.4, 0.4, 0.4]

[run]
duration = 3.0
```

It prints, in about three seconds (in Metric here, as an agent's `bench_run` returns its headlines whatever the
setting; `scrimmage bench run` and the Bench tab print them in the [Units setting](settings.md), feet per second and
pounds-force unless you pick Metric):

```
5 of 5 left through `shot` at 7.4 m/s ± 0.0, 6° down
the `top` shaft drops 14% and recovers in 0.05 s (the worse of 2 shafts)
a piece takes 0.19 s from the inlet to the outlet
UNCALIBRATED: estimates from preset numbers, not measurements (ball stiffness, tread friction); measure the ball's
stiffness (a squeeze test) and its bounce (a drop test) first; see docs/launcher.md, "Measure these first".
```

**The bench frame.** x is forward (the usual way pieces leave), z is up, y is across. Lengths are meters, or inches
with the key's `_in` form (`diameter_in = 4`). The side picture looks along y: forward is to the right, up is up.

**Which way a wheel turns.** A wheel spins about its `axis` (default `[0, 1, 0]`, across the bench) by the right-hand
rule, with a positive `target_rps`. With the default axis the wheel's **top moves forward (+x)** and its bottom moves
back. A wheel *below* a piece pushes it forward with the default axis; a wheel *above* a piece needs
`axis = [0, -1, 0]` (or a negative speed) to push it forward. Turned the wrong way, the piece is thrown back out of
the inlet: the picture shows it.

## Turning a spoken idea into a bench file

This section is for the agent (and anyone writing a file by hand).

1. **Name the idea and its author.** `[bench] name` is a short name, `author` the student's name, and `idea` the
   student's own words, in one line. These go on the picture and in the gallery: keep the student's words.
2. **Pick the piece.** Use the game's ball preset (`2020-power-cell`, `2022-cargo`, `2026-fuel`, or a past game's ball: see the preset table in [docs/launcher.md](launcher.md#presets-and-their-sources)) for a foam ball. For
   anything else, a rigid custom piece (a box, a cylinder or a disc) with a rough mass. A squishy piece must be a
   ball (see [Pieces and contact laws](#pieces-and-contact-laws)).
3. **Guess sizes from the piece, and say which numbers were guessed.** Students say "two wheels", not "4 in. 40A at
   0.2 D squeeze". Good first guesses:
   - Shooter and intake wheels: 4 in. diameter, 40A tread (35A for a shooter flywheel), as wide as the piece.
   - Squeeze (how much less the gap is than the piece): 0.15 to 0.25 of a foam ball's diameter; 3 to 5 mm a side
     for a rigid piece.
   - Speeds: shooter wheels 30 to 70 rps (1800 to 4200 rpm); intake rollers and conveyors 5 to 15 rps at the roller,
     about 3 m/s at the surface. A Kraken X60 (`KrakenX60`) direct for shooters, a `NEO` geared 3:1 to 5:1 for
     rollers.
   - `wheel_inertia`: 0.0005 to 0.001 kg m² for a pair of 4 in. wheels on a shaft; add `added_inertia` for a
     flywheel (0.002 is a heavy one).
   - Plates and hoods: polycarbonate, 1/8 in. thick (the defaults); guides and ramps UHMW; bars aluminum tube.
   - Feed: the piece enters 1 mm clear of everything, moving 1 to 3 m/s toward the mechanism, a few per second.

   Put the guesses in `[bench] notes` and tell the room which they were ("I guessed 4 in. wheels and a 0.2 D
   squeeze"), so they can correct them.
4. **Start rough, run early.** A first file with one wheel and one plate that runs in five minutes is worth more than
   a complete one in twenty. Run `bench_check` after every edit (it is instant), then `bench_run`, show the picture,
   and add the next part.
5. **Place the inlet and outlets so the run can be read.**
   - The inlet is where a piece's *center* appears. Make it a tiny box (1 mm) for a repeatable feed; it must keep the
     whole piece clear of every shape, or `bench check` says which shape it overlaps and by how much.
   - Outlets are boxes; a piece whose center enters one is counted out there and its exit speed, direction and spin
     are read at that moment. Put the main outlet first (the tables' exit numbers are for the first outlet), its
     face just past where the piece leaves the mechanism. Add a second outlet for "where it shouldn't go" (`spill`,
     `reject`, "missed the hopper"), as in [the quarter pipe](#curved-ramps-concave-up-and-round-a-roller). A piece
     that leaves the bench elsewhere is `escaped`.
   - **An outlet must be somewhere a piece can only reach after the part you care about.** A piece is counted out
     the moment its center enters the box, wherever it came from. A hopper box that overlaps the climb up to a bar
     catches the balls before they reach the bar, and the headline says "4 of 4 left through `hopper`" for a design
     that never touched the bar. Put the box past and below the last part (a hopper the ball must *fall* into), not
     around the path leading to it.
   - A plate under the inlet (a feeder) is only the piece's rolling support on the way in: keep the inlet a piece's
     length or more from the wheels, so the piece is moving before it meets them. An inlet right at the nip feeds the
     wheels directly and the feeder does nothing.
6. **Read the picture before the numbers.** Green paths left through an outlet, red jammed, orange escaped, gray were
   still inside when the run ended. Check that the paths actually go through the parts the idea is about. To be sure a
   part was hit, look at `loads.csv` in the results folder: every shape's peak contact force; a part at 0 N was never
   touched. If nothing gets out, look at where the paths stop (see [Troubleshooting](#troubleshooting)).
7. **Speeds, heights and lengths are coupled.** Two rules of thumb for a first guess:
   - A pinched or rolled ball leaves at about **80 to 90% of the wheels' surface speed** (surface speed = pi x
     diameter x rps) while the treads grip: the pinch shooter below gives 87% at 20 rps, Maya's car 92%. Once the ball
     slips, more speed buys little: the same shooter gives 77% at 30 rps and no faster above that.
   - To rise a height h (m) after leaving the wheels, a piece needs a speed v (m/s) with **v² > 2 g h**, about
     v > 4.4 sqrt(h): 1.4 m/s for 0.1 m, 2.0 m/s for 0.2 m, 3.1 m/s for 0.5 m, more against friction on a ramp.

   So the wheel speed, where a bar sits and where a ramp ends are one decision: raise the bar or lengthen the climb
   and the wheels must turn faster; speed them up and the piece may fly over the hopper. Change one, and check the
   others in the picture.

### Worked example: "a roller over a ramp pulls the ball up into the hopper"

Noah's idea, as the agent might first write it. The guesses: a 20 degree polycarbonate ramp 0.6 m long, a 4 in.
40A roller above it with the ball squeezed 0.15 D, a NEO geared 3:1 at 10 rps, FUEL rolling up the ramp at 1.5 m/s.
The ramp is a plate turned about y: a **negative** `rot_deg` about y lifts its +x end (right-hand rule about +y turns
+x toward -z). The roller sits above the ramp's middle, 0.1833 m out from the ramp's center line along the ramp's
normal (half the plate's thickness, the ball's diameter less the squeeze, and the roller's radius); the inlet is on
the ramp 0.14 m before the roller.

<!-- bench -->
```toml
[bench]
name = "roller ramp"
author = "Noah"
idea = "a roller over a ramp pulls the ball up into the hopper"
notes = "guessed: 4 in. roller, 0.15 D squeeze, 20 degree ramp, 10 rps at the roller"

[piece]
preset = "2026-fuel"

[shaft.roller]
motor = "NEO"
gearing = 3.0                    # 3:1 down
target_rps = 10                  # at the roller: about 3.2 m/s at its surface
kP = 0.5
wheel_inertia = 0.0005

[[wheel]]
name = "roller"
shaft = "roller"
center = [-0.0627, 0.0, 0.1722]  # above the ramp's middle
axis = [0.0, -1.0, 0.0]          # its bottom moves forward, up the ramp
diameter = 0.1016
width = 0.25
tread = "40A"

[[plate]]
name = "ramp"
center = [0.0, 0.0, 0.0]
size = [0.6, 0.3, 0.01]
rot_deg = [0.0, -20.0, 0.0]      # rising 20 degrees toward +x
material = "polycarbonate"

[[inlet]]
name = "floor"
center = [-0.1593, 0.0, 0.0283]  # on the ramp, 0.14 m before the roller, 1 mm clear
size = [0.001, 0.001, 0.001]
count = 3
rate = 2
velocity = [1.41, 0.0, 0.513]    # 1.5 m/s up the ramp

[[outlet]]
name = "hopper"
center = [0.5, 0.0, 0.25]        # past the top of the ramp
size = [0.3, 0.4, 0.4]

[run]
duration = 3.0
```

It prints ``3 of 3 left through `hopper` at 1.2 m/s ± 0.0, 31° down`` and that the roller's shaft drops 8%. The
first try had the inlet 0.2 m down the ramp at 1 m/s: the balls stopped short of the roller, rolled back and escaped
off the bottom (three orange paths). Moving the inlet up and feeding faster fixed it, which is the loop: run, look,
change one thing.

### Curved ramps: concave up, and round a roller

A `[[curve]]` is a ramp as well as a hood. Two ways round:

- **Concave up (a quarter pipe, a scoop).** Put the curve's `center` *above* the ramp and run the angles through the
  bottom (270 degrees is straight down from the center). The inner face is at `radius` from the center, so a ball's
  center rides at `radius` less the ball's radius: that is the distance to use for the inlet. Mia's idea, with a
  `spill` outlet beside the `hopper` for balls that fly over it, and a spread of feed speeds so the run shows both:

<!-- bench -->
```toml
[bench]
name = "quarter pipe"
author = "Mia"
idea = "roll the ball into a curved ramp so it swoops up into the hopper"
notes = "guessed: 0.4 m radius, polycarbonate, ball fed at 4 m/s"

[piece]
preset = "2026-fuel"             # 0.15 m: radius 0.075 m

# Concave up: the curve's axis point is ABOVE the ramp. Angles about +y: 270 is straight down from the center, 360
# straight forward. The inner face is at `radius` from the center, so the ball's center rides at radius - 0.075.
[[curve]]
name = "ramp"
center = [0.0, 0.0, 0.4]
radius = 0.4
start_deg = 250.0                # a little behind the bottom
end_deg = 330.0                  # the lip: the ball leaves 60 degrees up, going forward
width = 0.25

[[inlet]]
name = "feed"
center = [-0.0282, 0.0, 0.0772]  # at 265 degrees, center 0.324 m from the axis point: 1 mm off the ramp
size = [0.001, 0.001, 0.001]
count = 3
rate = 2
spread_speed = 0.6              # m/s: some balls faster, some slower
seed = 3
velocity = [3.985, 0.0, 0.3487]  # 4 m/s along the ramp

[[outlet]]
name = "hopper"                  # first: the main outlet. Low, past the lip: a ball only gets here falling
center = [0.65, 0.0, 0.15]       # after it has left the ramp, not on its way up
size = [0.4, 0.4, 0.3]

[[outlet]]
name = "spill"                   # flew over the hopper
center = [1.25, 0.0, 0.15]
size = [0.8, 0.4, 0.3]

[run]
duration = 3.0
```

  It prints two outlet lines: ``2 of 3 left through `hopper` at 1.7 m/s ± 0.0, 73° down`` and ``1 of 3 left through
  `spill` at 3.4 m/s, 46° down``; the fast ball flew over. The hopper is low and past the lip, so a ball only gets
  there by leaving the ramp and falling. Fed at 3 m/s, the balls don't clear the lip: they roll back and escape.
- **Round a roller (concave toward the roller).** The curve's `center` is the roller's center, and the curve's
  `radius` is the roller's radius plus the gap: the ball is squeezed between them and carried round, as on a shooter
  hood. `examples/bench/curved_ramp.toml` (Diego) does this: a 4 in. roller at the origin turning with
  `axis = [0, -1, 0]` (its front surface moves up), a UHMW `lead` curve from -100 to -75.52 degrees narrowing from
  0.2067 m to the 0.1784 m ramp radius (a 30 degree flare, the lead-in), then the `ramp` curve at 0.1784 m from
  -75.52 round the front to 100 degrees. The ball enters at the bottom (-103 degrees), rides up round the front, and
  leaves at the top heading backward into a `hopper` outlet behind the roller, with a `spill` outlet further back.
  Its headline is ``4 of 4 left through `hopper` at 2.5 m/s ± 0.0, 45° down, going backwards``.

## Pieces and contact laws

The piece decides how every contact it has is worked out.

- **Squishy** pieces are foam balls: the launcher model's laws (a power-law squeeze, in series with the tread's own
  rubber on a wheel; friction; rolling loss) on contact points MuJoCo finds. Contacts with one shape whose directions
  are within 30 degrees are merged, so a curve cut into flat pieces, or a CAD mesh, acts as one smooth surface. Balls
  touch each other with the same law. **Squishy pieces are balls only, for now.** A squishy box, cylinder or mesh (a
  foam ring like 2024's, a fabric cube like 2023's) is an error: use a rigid stand-in (`kind = "rigid"`, with its own
  `stiffness`). Squishy non-ball pieces are a later project.
- **Rigid** pieces (toy cars, cubes, cones, discs, hard balls) use MuJoCo's own contacts. Wheel treads are compliant
  (the stock wheels' tread stiffness), and a rigid piece may carry its own contact `stiffness` (N/m) in series with
  the tread, so a pinch squeezes both: a soft plastic body at 3000 N/m takes most of the squeeze.
- **A range's piece** (`preset = "2024-note"`, or any `[piece.*]` preset in `scrimmage/launcher/presets.toml`: the
  2025 CORAL, the 2019 HATCH PANEL...) is rigid, and the bench builds it with the test range's own piece builder: the
  same mass, size, inertia and solids as on the range. A ring's hole is open (a rod passes through it), because it is
  built from 24 slices, as on the range. `shape = "ring"` with `size = [diameter, inner_diameter, length]` and a
  `mass` builds your own ring the same way. On the bench the 2024 NOTE is a rigid stand-in unless the piece says
  `flex = true` (below): the real one is soft foam and squashes. It brings its own contact `stiffness` (2000 N/m, an
  estimate), as on the range. A ring touches a wheel at many MuJoCo contact points (8 per wheel for the NOTE's face),
  and the bench spreads the stiffness over them, so held between wheels the NOTE is squeezed as hard as its
  `stiffness` says, no harder. Going through a pinch it still doesn't behave like foam (see [Known
  limits](#known-limits)); `flex = true` makes it a soft body instead (below).

### Flex NOTE (experimental)

`flex = true` in the `[piece]` makes a ring (the 2024 NOTE, or your own `shape = "ring"`) a soft body that bends and
squashes: MuJoCo's flex, a ring-shaped mesh of 288 small solid pieces (tetrahedra) joined at 112 points. Results
say **experimental (MuJoCo flex)** in the headlines and the pieces table.

<!-- bench -->
```toml
[bench]
name = "soft note"
author = "Jordan"
idea = "feed the soft NOTE between two wheels"

[piece]
preset = "2024-note"
flex = true

[[inlet]]
name = "feed"
center = [0.0, 0.0, 0.0]
size = [0.001, 0.001, 0.001]
count = 1
velocity = [2.0, 0.0, 0.0]

[[outlet]]
name = "out"
center = [1.0, 0.0, 0.0]
size = [0.2, 0.8, 0.8]
```

`examples/bench/note_pinch_flex.toml` is a whole shooter: the NOTE squeezed a quarter between two 4 in wheels.

- **It is opt-in.** Without `flex = true` nothing changes: the NOTE is the rigid stand-in, as fast as before. The
  test range's own soft NOTE (near a robot) doesn't change the bench.
- **It is slow: about 100 times a rigid run.** One shot through a pinch takes about half a second of computer time.
  Sweep and compare on the rigid NOTE first, then confirm the design you picked with `flex = true`: a sweep or
  compare over a flex file runs every combination at that cost, so 12 combinations take 12 flex runs. A bench holds
  at most 3 flex pieces (each parked one costs as much as a moving one), so `count` in the inlets adds up to 3 or
  fewer. Flex NOTEs piled on each other are slower still ([Known limits](#known-limits)).
- **Feed it on a floor.** A flex NOTE fed through the air sags: in the pinch example with no floor under the feed,
  its 12 in tail drooped while its front was in the wheels, and it left tilted 28 degrees and spinning at 25 rad/s.
  A plate under the feed, level with the NOTE's underside and ending just before the wheel, keeps it flat (as
  `note_pinch_flex.toml` does): it then left tilted 4 degrees, spinning at about 3 rad/s.
- **Its stiffness and bounce are fitted.** The flex's material numbers are worked out so that it answers the
  piece's `stiffness` (a squeeze between two rollers, docs/measuring.md) and `restitution` (a 1 m drop) the way the
  test range's soft NOTE does. The 2024 NOTE's fit ships with scrimmage. Other numbers (your own `stiffness` or
  `restitution`) are fitted the first time they are used, which takes up to about 20 seconds, and kept in a cache
  folder after that. Both numbers are estimates for the 2024 NOTE, **UNCALIBRATED** until you measure them.
- **In `[flight]`** it flies as the rigid NOTE, from the flex's exit: its center, velocity and orientation as it
  left, and the spin it had, as the test range's shooter hands a NOTE off. The hand-off happens where the NOTE's
  center crosses into the outlet, so put the outlet past the wheels, more than the NOTE's half-width (7 in) beyond
  them, or its speed and spin are read while it is still squeezed; `bench check` warns about an outlet that close.
- **The wheels' treads take the flex's contact.** On a flex bench every shape (a wheel's tread included) meets the
  NOTE with the flex's own contact settings, the ones its fit was made with, so the tread's own stiffness doesn't
  apply, and contacts have no rolling or twisting friction. The squeeze is the flex's: its `stiffness` alone.
- **It runs on a different integrator.** A flex needs MuJoCo's `discrete` integrator (with the Newton solver), at a
  0.5 ms timestep unless `[run] timestep` says otherwise; the rest of the bench uses `implicitfast`. The flex is
  fitted at the run's own timestep, since its answer moves a few percent with the step: the shipped fit is for
  0.5 ms, so another `timestep` fits it again on first use (up to about 20 s at 0.5 ms, longer at a smaller step)
  and caches that fit too. MuJoCo itself
  describes `discrete` as new, under active development and subject to change, so a MuJoCo update may move these
  numbers (a test pins one shot, so a change is caught).
- On the soft-note spike's pinch (1.5 in gap, 3000 and 5000 rpm), the flex leaves within 4% of the launcher
  model's pinch (`launcher/pinch.py`) fed the same squeeze.

Discs (the 2013 Frisbee and an Ultra-Star preset, with their own flight) come in a later step, once the disc launcher
model is merged. A custom rigid `shape = "disc"` (a short cylinder) works now.

Ball presets come from `scrimmage/launcher/presets.toml`; their sources are in
[docs/launcher.md](launcher.md#presets-and-their-sources), and how to measure your own ball in
[docs/measuring.md](measuring.md). A squishy custom ball (a sphere with no preset) gives
`stiffness_n` and `restitution`; left out, the nearest preset's are used, with a warning.

## The format reference

TOML. Every unknown key or section is an error that names the valid ones. **Every length key also has an inch form**,
`<key>_in` (give one of the two, not both): `diameter_in = 4`, `center_in = [0, 0, 4.36]`. Angles are degrees and end
in `_deg`. Lists of shapes (`[[wheel]]`, `[[plate]]`, ...) need a `name` on each entry (letters, digits, `_`, `-`),
unique in its section; it is how the pictures label it and how a sweep reaches it. So a bench file has many `name =`
lines: the idea's own is the one under `[bench]`. To rename the idea, change only that one (the Bench tab's title box
does just that). A find-and-replace on `name` renames the shapes too, and other lines (a wheel's `shaft`, a sweep's
target) still name the old ones, so the file stops loading.

Units: lengths m (or in.), mass kg, inertia kg m², speeds m/s, spin rad/s, shaft speeds rps (turns per second),
stiffness N/m, voltage V, current A, time s.

### `[bench]`

| Key | Meaning | Default |
|---|---|---|
| `name` | the idea's short name (pictures, tables, the gallery) | required |
| `author` | the student whose idea it is | "" |
| `idea` | the student's own words, one line | "" |
| `notes` | anything else: which numbers were guessed | "" |

### `[piece]`

Either `preset = "<ball>"` (a squishy ball; any key below overrides the preset's), `preset = "<piece preset>"` (a
range's rigid piece, such as `2024-note`; only `name`, `stiffness`, `drag_coefficient`, `spin_damping`, `flex` and
(with `flex`) `restitution` go with it, the rest is the preset's), or a custom piece.

| Key | Meaning | Default |
|---|---|---|
| `preset` | a ball: `2020-power-cell`, `2022-cargo`, `2026-fuel`, `2016-boulder`, `2017-fuel`, `2019-cargo`, `2025-algae`, `2014-ball`, `2010-soccer-ball` or `2012-basketball`; a range's piece: `2025-coral`, `2023-cone`, `2023-cube`, `2017-gear`, `2019-hatch-panel`, `2015-tote` or `2024-note` | none |
| `name` | the piece's name | the preset, or `piece` |
| `kind` | `squishy` or `rigid` | `squishy` with a preset, else `rigid` |
| `shape` | `sphere`, `box`, `cylinder`, `disc` (a cylinder), `ring` (hollow, built as the range builds it) or `mesh` | `sphere` with a ball preset, a piece preset's own, else required |
| `size` | sphere `[diameter]`; box `[x, y, z]`; cylinder or disc `[diameter, length]`; ring `[diameter, inner_diameter, length]` (axis along z) | the preset's |
| `mass` | kg | the preset's, else required |
| `inertia` | `[Ixx, Iyy, Izz]` about its own axes, kg m² | from the shape (required for a mesh) |
| `stiffness` | rigid only: its own contact stiffness, N/m, in series with the tread | a piece preset's own (the 2024 NOTE's 2000), else none: hard |
| `drag_coefficient`, `spin_damping` | rigid only, in `[flight]`: the drag coefficient on its shadow, and how fast the air slows its spin, N m s/rad (0 or more) | 1.17 (an estimate), 0; a piece preset's own |
| `stiffness_n`, `stiffness_exponent`, `restitution`, `inertia_factor` | squishy only: the launcher model's ball numbers | the preset's, or the nearest preset's |
| `flex` | `true`: a ring is a soft flex body, experimental and about 100 times slower ([Flex NOTE](#flex-note-experimental)); a ring whose length is its wall's width only, as the 2024 NOTE | `false` |
| `restitution` | with `flex = true`: the ring's bounce off the carpet (above 0, below 1), which its flex is fitted to | a piece preset's own (the 2024 NOTE's 0.3) |
| `mesh`, `units` | `shape = "mesh"`: an STL or OBJ file (relative to the bench file) and its units, `m`, `mm` or `in`; it collides as its convex hull | none |

### `[shaft.<name>]`

A shaft carries wheels (each `[[wheel]]` names its `shaft`); wheels on one shaft turn together, on one axis. All
driven shafts share one battery (12.5 V, 0.02 ohm), so a dip on one sags the others.

| Key | Meaning | Default |
|---|---|---|
| `motor` | `KrakenX60`, `KrakenX60FOC`, `Falcon500`, `Falcon500FOC`, `NEO`, `NeoVortex`, `NEO550`, `CIM`, `MiniCIM`, `Bag`, `Vex775Pro` | none: a free roller |
| `count` | motors on the shaft | 1 |
| `gearing` | motor turns per shaft turn (3.0 is 3:1 down) | 1.0 |
| `wheel_inertia` | the wheels, hubs and shaft, kg m² | required |
| `added_inertia` | a flywheel, kg m² | 0 |
| `control` | `speed`, `voltage` or `free` | from the keys below |
| `target_rps` | speed control: the shaft's target, rps | none |
| `voltage` | a fixed voltage instead | none |
| `kP` | speed control: volts per rps of error (required with `target_rps`) | — |
| `kS`, `kV` | feedforward: static volts, volts per rps | 0; `kV` from the motor and gearing |
| `stator_limit`, `supply_limit` | speed control current limits, A | none |
| `coupled_to`, `ratio` | belted or geared to another shaft: this shaft turns `ratio` times its speed (no motor of its own) | none; 1.0 |

Pieces start feeding once every speed-controlled shaft is within 2% of its target (or after 2 s).

### `[[wheel]]`

| Key | Meaning | Default |
|---|---|---|
| `name`, `shaft` | its name, and the shaft that turns it | required |
| `center` | `[x, y, z]`, m | required |
| `axis` | the direction it spins about (right-hand rule): `[0, 1, 0]` moves its top forward | `[0, 1, 0]` |
| `diameter`, `width` | m | required |
| `tread` | `30A`, `35A`, `40A`, `45A`, `60A` (the launcher model's treads; also its friction) | `35A` |

### `[[plate]]`

A flat box, fixed. With `surface_speed` its surface moves while the box stays put, as a conveyor belt does, so a
piece lying on it is carried along: a moving carpet, which stands in for a robot driving a piece along. The bench's
checks and summary say so for each moving plate.

| Key | Meaning | Default |
|---|---|---|
| `name`, `center` | | required |
| `size` | `[x, y, z]` full lengths, m | required |
| `rot_deg` | turns about x, then y, then z, degrees (right-hand; `[0, -20, 0]` lifts the +x end) | `[0, 0, 0]` |
| `material` | see [Materials](#materials) | `polycarbonate` |
| `surface_speed` | how fast its surface moves, m/s, along the plate's own x (turned with `rot_deg`, so a ramp carries along its slope, and a plate turned onto its edge carries a piece up or down); negative runs back. A piece on it comes up to this speed through friction. Works with `flex = true` too | `0` |

An `[[inlet]]`'s `velocity` sets how a piece starts; a moving plate keeps it moving. Every face of the box moves
(MuJoCo's surface velocity covers the whole box), so a piece touching its side or underside is pushed along x too.

### `[[bar]]`

A round bar or tube (a capsule): give its two ends `a` and `b`, or `center`, `axis` and `length`.

| Key | Meaning | Default |
|---|---|---|
| `name`, `diameter` | | required |
| `a`, `b` | the ends, m | |
| `center`, `axis`, `length` | instead of the ends | |
| `material` | | `polycarbonate` |
| `spring` | springy: N/m along `spring_axis` (a bounce bar) | none: fixed |
| `travel` | with `spring`: how far it can give each way, m | required with `spring` |
| `spring_axis` | | `[0, 0, 1]` |

### `[[curve]]`

A curved plate: hoods, curved ramps, funnels. It is a strip of the given `width` round an axis, at a radius that runs
from `radius_start` at `start_deg` to `radius_end` at `end_deg` (equal for an arc, unequal for a flare or a spiral).
Angles go round from `zero_dir`: about the default axis `[0, 1, 0]`, **0 degrees is forward (+x), 90 is up (+z), 180 is
back, 270 is down** (the launcher model's hood convention). The curve's inner face is at the radius; its thickness
is outside. The bench cuts it into flat pieces 5 degrees apart internally.

| Key | Meaning | Default |
|---|---|---|
| `name`, `center` | the axis passes through `center` | required |
| `radius`, or `radius_start` and `radius_end` | m | required |
| `start_deg`, `end_deg` | the span (at most 360 degrees, not zero) | required |
| `width` | along the axis, m | required |
| `axis` | | `[0, 1, 0]` |
| `zero_dir` | where 0 degrees points (made square to the axis) | `[1, 0, 0]` |
| `thickness` | m; no more than the smaller radius | 1/8 in. |
| `material` | | `polycarbonate` |

A hood round a wheel at the origin, a 5.5 in. gap for a 7 in. ball (examples/bench/hood_shapes/round.toml, whole):

<!-- bench -->
```toml
[bench]
name = "round hood"
author = "Sam"
idea = "a plain round hood, the same gap all the way round"

[piece]
preset = "2020-power-cell"

[shaft.shooter]
motor = "KrakenX60"
wheel_inertia = 0.0006
added_inertia = 0.002            # a flywheel
target_rps = 66.7                # 4000 rpm
kP = 0.2
stator_limit = 80.0
supply_limit = 60.0

[[wheel]]
name = "wheel"
shaft = "shooter"
center = [0.0, 0.0, 0.0]
diameter_in = 4
width_in = 5
tread = "35A"

# The entry: a 30 degree flare from no squeeze down to the gap, starting a little early as a lead-in.
[[curve]]
name = "flare"
center = [0.0, 0.0, 0.0]
radius_start = 0.234679
radius_end = 0.1905              # the wheel's radius plus the 5.5 in. gap
start_deg = 245.0                # behind and below the wheel
end_deg = 208.6613
width = 0.2

[[curve]]
name = "hood"
center = [0.0, 0.0, 0.0]
radius = 0.1905
start_deg = 208.6613
end_deg = 140.0                  # the exit: the ball leaves 140 - 90 = 50 degrees up
width = 0.2

[[inlet]]
name = "feed"
center = [-0.0504, 0.0, -0.1314] # at 249 degrees, 1 mm off the wheel, just clear of the flare
size = [0.001, 0.001, 0.001]
count = 3
velocity = [-0.9336, 0.0, 0.3584] # 1 m/s along the path

[[outlet]]
name = "shot"
center = [0.2, 0.0, 0.635]       # everything above 0.135 m
size = [1.0, 1.0, 1.0]

[run]
duration = 2.0
```

### `[[cad]]`

A CAD part in place of rough shapes; see [CAD](#cad) for how to get one out of Inventor. Give `mesh` or `part`.

| Key | Meaning | Default |
|---|---|---|
| `name` | its name (a part's shapes are named `<name>.<body>`) | required |
| `mesh` | an STL or OBJ file (path relative to this file), fixed to the bench | |
| `units` | with `mesh`: the file's length unit, `"m"`, `"mm"` or `"in"` (Inventor writes mm or in.) | required with `mesh` |
| `part` | instead of `mesh`: a part folder from the FreeCAD exporter (holding `part.toml`) | |
| `center` | where the file's origin (or the part's root) goes, `[x, y, z]`, m | `[0, 0, 0]` |
| `rot_deg` | turns about x, then y, then z, degrees, as a plate's | `[0, 0, 0]` |
| `material` | every shape but a part's wheels; see [Materials](#materials) | `polycarbonate` |
| `slots` | with `part`: `{ <motor slot> = "<shaft>" }`, which of the part's motor slots a bench shaft turns | none: all welded |
| `tread` | with `part`: its wheels' tread, as a `[[wheel]]`'s | `35A` |

### `[[inlet]]`

Where pieces appear. Several inlets are allowed (a two-lane feed), each on its own schedule.

| Key | Meaning | Default |
|---|---|---|
| `name`, `center` | the zone's center, where a piece's center appears | required |
| `size` | the zone, `[x, y, z]`, m; it must keep the whole piece clear of every shape | required |
| `count` | how many pieces | 1 |
| `rate` | pieces per second | 5 |
| `velocity` | `[vx, vy, vz]`, m/s | `[0, 0, 0]` |
| `spin` | `[wx, wy, wz]`, rad/s | `[0, 0, 0]` |
| `spread_speed`, `spread_position`, `spread_time` | random spread (standard deviations: m/s along the velocity, m, s) | 0 |
| `seed` | the spread's random seed (the same seed, the same run) | 1 |

A piece waits while another still overlaps its spawn point. At most 200 pieces in a bench.

### `[[outlet]]`

| Key | Meaning | Default |
|---|---|---|
| `name`, `center`, `size` | a box; a piece whose center enters it is out there, and removed | required |

### `[flight]` (optional)

Flies each piece out through `outlet` from where and how it left, so the results say where shots land. A ball flies
with the launcher model's ball flight (drag and spin lift). A rigid piece (a NOTE, a box...) flies with its drag only:
the drag of its shadow as it faces the air, turning as it tumbles, from its orientation and spin as it left
(`scrimmage/launcher/rigid_aero.py`). There is no lift for a rigid piece, so a flat, backspun NOTE that really glides
comes up short here: the drag numbers are estimates, and the result is UNCALIBRATED.

| Key | Meaning | Default |
|---|---|---|
| `outlet` | which outlet's pieces fly | required |
| `height` | the bench origin's height above the floor, m | 0 |
| `target_distance`, `target_height` | the target, forward of the origin and above the floor, m | required, unless there is a `goal` |
| `ring_diameter` | a ring at the target: shots through it count (a rigid piece's: all of its outline inside the ring as it comes down through it, the rule a `goal` uses) | none |
| `air` | an air preset | `default` |
| `goal` | rigid pieces: `{ range = "<range file>", name = "<goal>" }`, a goal from a range file (relative to the bench file, or a built-in range's name) | none |
| `at` | with `goal`: where the bench origin stands on that field, `[x, y, height]`, m (its height replaces `height`) | required with `goal` |
| `heading_deg` | with `goal`: which way the bench's +x faces on that field, from the field's +x | required with `goal` |

With a `goal`, the bench stands on the range's field and each shot says `scored` or `missed` (a `goal` column in the
landings table, and a headline like ``1 of 1 shots score in `speaker_blue` ``). A shot scores when all of the piece
fits through: its center goes through the goal's opening from the side a scoring piece comes from, and the points of
the piece's outline (the corners of its solids' hull, at most 64 of them: a NOTE's rim), turned as the piece is then,
all fall inside the opening. On the way in, neither the center nor any outline point may touch one of the range's
obstacles or elements or a goal's plates. A shot through the SPEAKER's hood, or a NOTE overlapping the opening's
edge, doesn't count, and the headline names what it hit (a rim plate, or `<goal>.edge_<k>`). The points are checked
every 1 ms of the flight, not swept between, and a ring's hole isn't checked. The positions in the landings table are still the bench's own: forward of the
bench origin and above the floor.

The built-in `2024` range (the FRC 2024 CRESCENDO field, `scrimmage/testrange/ranges/2024.toml`) has both SPEAKERS
(`speaker_blue`, `speaker_red`) and both AMPS (`amp_blue`, `amp_red`), so `range = "2024"` needs no file of your own:

<!-- bench -->
```toml
[bench]
name = "note lob"
author = "Sam"
idea = "lob a NOTE into the blue SPEAKER from 2.5 m out"
notes = "the NOTE is thrown by hand (an inlet's velocity); a shooter goes where the hand is"

[piece]
preset = "2024-note"

[[inlet]]
name = "hand"
center = [0.0, 0.0, 0.0]
size = [0.001, 0.001, 0.001]
count = 1
velocity = [5.281, 0.0, 6.075]   # 8.05 m/s at 49 degrees up

[[outlet]]
name = "shot"
center = [0.45, 0.0, 0.0]
size = [0.1, 1.0, 3.0]

[flight]
outlet = "shot"
goal = { range = "2024", name = "speaker_blue" }   # the built-in 2024 range, or your own range file
at = [2.5, 5.5446, 0.5]          # 2.5 m out from the blue alliance wall, in line with the SPEAKER, 0.5 m up
heading_deg = 180.0              # facing the wall
```

### `[run]`

| Key | Meaning | Default |
|---|---|---|
| `duration` | the longest the run goes on, spin-up included, s (it ends sooner once every piece is settled) | 5.0 |
| `timestep` | s; with `flex = true`, the flex is fitted at this step (a step other than 5e-4 fits it again on first use) | 1e-4 squishy, 2.5e-4 rigid, 5e-4 flex |
| `jam_time`, `jam_distance` | a piece that stays within `jam_distance` (m) of one point for `jam_time` (s) is jammed | 1.0, 0.01 |

### `include`

`include = ["shooter.toml"]` at the top merges another bench file's shafts, shapes and sections in first (paths
relative to this file). The including file's own entries win by name, and its own `[bench]`, `[sweep]` and single
sections replace the included ones; the same name defined in two included files is an error naming both.

### Materials

`material` on a plate, bar, curve or CAD part: `polycarbonate`, `aluminum`, `steel`, `UHMW`, `nitrile`, or a tread name. Each
gives a friction against foam and against rigid pieces; all are estimates
(`scrimmage/bench/presets.toml` says how each was estimated). Replace them with a sled pull of your own part and
piece ([measuring.md](measuring.md#sled-pull)).

### `[sweep]` and `[[chart]]`

`[sweep]` lists values for dotted keys; `bench sweep` runs every combination (the first key varies slowest). List
entries and shafts are reached by name: `"wheel.top.center"`, `"shaft.top.target_rps"`, `"curve.hood.end_deg"`,
`"inlet.feed.rate"`, `"run.duration"`. Quote the key. Setting `diameter` replaces a stored `diameter_in` and the other
way round.

<!-- bench -->
```toml
[bench]
name = "pinch shooter, speed sweep"
author = "Ava"
idea = "two wheels squeeze the ball and fire it straight out: how fast should they spin?"

[piece]
preset = "2026-fuel"

[shaft.bottom]
motor = "KrakenX60"
target_rps = 30
kP = 0.3
wheel_inertia = 0.001

[shaft.top]
motor = "KrakenX60"
target_rps = 30
kP = 0.3
wheel_inertia = 0.001

[[wheel]]
name = "bottom"
shaft = "bottom"
center = [0.0, 0.0, -0.1108]
diameter_in = 4
width = 0.1
tread = "40A"

[[wheel]]
name = "top"
shaft = "top"
center = [0.0, 0.0, 0.1108]
axis = [0.0, -1.0, 0.0]
diameter_in = 4
width = 0.1
tread = "40A"

[[plate]]
name = "feeder"
center = [-0.22, 0.0, -0.08]
size = [0.26, 0.2, 0.01]

[[inlet]]
name = "feed"
center = [-0.25, 0.0, 0.001]
size = [0.001, 0.001, 0.001]
count = 3
rate = 3
velocity = [2.0, 0.0, 0.0]

[[outlet]]
name = "shot"
center = [0.6, 0.0, 0.0]
size = [0.4, 0.4, 0.4]

[sweep]
"shaft.top.target_rps" = [20, 30, 40]
"shaft.bottom.target_rps" = [20, 30, 40]

[[chart]]
kind = "line"
x = "shaft.top.target_rps"
y = "exit_speed"
series = "shaft.bottom.target_rps"
```

Its table shows something worth knowing before building: from 30 rps on both wheels, the exit speed stays at
7.4 m/s whatever the speed. The ball slips on the treads, and slipping friction doesn't grow with wheel speed (the
launcher model's [grip and slip](launcher.md#the-two-regimes-grip-and-slip)), so more rpm buys nothing there; a
tighter squeeze or a grippier tread might.

A `[[chart]]` is a `line` chart of one table column (`y`, from the list in [Results](#results)) against a swept key
(`x`), one line per value of another (`series`). With no `[[chart]]`, `bench sweep` draws `exit_speed` against the
first swept key. Charts need matplotlib; without it the table and CSV are still written, with the note
`charts need matplotlib: pip install matplotlib`.

## Results

`bench run` prints three to six **headlines**, then writes to `<file's folder>/<file name>-results/` (or `--out`):

| File | What |
|---|---|
| `summary.txt` | the headlines and every table below |
| `summary.json` | no words: how many lines the title block of `summary.txt` takes, how many headline lines follow it, that the last is the UNCALIBRATED line, and whether the energy check failed (the Bench tab reads the headlines from `summary.txt` by position; a results folder from before `summary.json` shows its headlines, but no failed energy check) |
| `pieces.csv` | each piece: outcome, outlet, times, exit speed, elevation, yaw, spin, tilt, exit slip, peak force, the energy check |
| `shafts.csv` | each shaft: target, lowest speed, worst drop (%), recovery time, back to speed before the next piece, peak current, energy per piece |
| `loads.csv` | each shape: peak contact force (N), peak impulse in one pass (N s), and where |
| `landings.csv` | with `[flight]`: where each shot crosses the target height and lands |
| `path_side.png` | the mechanism from the side, every piece's path drawn through it (`path_top.png` too when pieces move across) |
| `pass.png`, `pass.gif` | frames of one piece's pass, as a sheet and an animation (thin parts drawn thick enough to see) |

**Outcomes.** `out` (through a named outlet), `jammed` (inside, not moving, for `jam_time`), `escaped` (left the
bench outside every outlet), `timed_out` (still inside when the run ended), `stuck_at_inlet` (never got in), spelled
as in `pieces.csv`. Path colors: green out (through any outlet: the headlines say which), red jammed, orange escaped,
gray timed out or stuck. A path is the piece's center; its true-size outline is drawn where it first touched each
part and where it ended, so a path that seems to pass clear of a part can still show the piece hitting it.

**Headlines**, in order: a failed energy check, if any (see below); what went out where, how fast and which way (``8 of 10 left through `shot` at 7.1 m/s ±
0.2, 52° up``); whether the balls were still slipping on the wheels as they left; what didn't get out and where it jammed; with `[flight]`, where shots come down; the part that takes
the hardest hits, when it stands out (over 3 times the other touched parts' typical peak); the shaft that slows most
and how fast it gets back to the speed it had (or that it isn't back before the next piece arrives: feed slower,
or add a flywheel); the time through. The last line is always UNCALIBRATED, with what to measure first.

- **Several outlets:** one line per outlet that pieces left through, the busiest first (``2 of 3 left through
  `hopper` ...``, then ``1 of 3 left through `spill` ...``).
- **Direction:** `N° up`, `N° down` or `level` is the angle above or below horizontal. A sideways angle is added when
  it is over 2 degrees (`5° to the left`, toward +y). **`going backwards`** means the pieces were moving toward -x
  (back, against the bench's forward) when they entered the outlet: expected for a hopper behind a roller, as in
  `curved_ramp.toml`; a sign of trouble for a shooter (a wheel turning the wrong way, or a bounce back).

**Loads** are the normal contact forces each part takes, to point at parts worth worrying about ("the kicker takes
600 N every shot"). Parts are rigid: the bench says nothing about whether a part bends or breaks.

**Energy check.** Every piece's exit energy is checked against its entry energy, the work the wheels did on it and
the height it lost. A failure is the first headline (``the energy check failed for 2 of 10 pieces: ...``), and is in
`summary.txt` and the `energy_ok` column: the simulation is wrong there, not the design; try a smaller
`[run] timestep`.

**Tilt** (`tilt_deg` in `pieces.csv`) is how far a rigid piece's own up axis is from up when it leaves: is a car still
upright? It is blank for a ball, which has no up.

**Exit slip** (`exit_slip_pct` in `pieces.csv`, balls only) is how much the wheels were still sliding under the ball at
its last wheel contact, as a percentage of the wheel's surface speed: the launcher model's `exit_slip_pct`. It counts
only when the ball left the wheels just before it left the bench (within 0.15 s): a ball an intake roller hands to a
long chute wasn't launched by that roller, so it gets no exit slip. Near 0, the
ball grips and leaves with the wheel: more wheel speed means a faster shot. At 10% or more the ball is still slipping
as it leaves: a headline says so, because more wheel speed then buys little, and more grip (more squeeze, more or
wider wheels, a longer wrap) is what helps. This is also the least certain part of the model (see
[How accurate is this?](#how-accurate-is-this)).

**Warnings** that don't stop a run (a custom ball with no `stiffness_n` or `restitution` borrows the nearest ball
preset's) print as `scrimmage: warning: ...`, go in `summary.txt`, and come back in the agent tools' `warnings`.

**Table columns** (sweep, compare and the gallery): `out` (through the first outlet), `out_other` (through any other),
`jammed`, `escaped`, `exit_speed` and `exit_speed_sd` (m/s, the first outlet's pieces), `elevation_deg`, `spin_rps`,
`speed_drop_pct` and `recovery_s` (the shaft that dropped most), `peak_load_n` (the hardest-hit part), `time_inside_s`
(median, inlet to outlet). `nan` means there was nothing to measure (no piece got out, or one piece has no spread).

## Sweeps, comparisons and the gallery

```bash
scrimmage bench sweep pinch_sweep.toml                            # every [sweep] combination: table, CSV, charts
scrimmage bench compare examples/bench/hood_shapes/*.toml         # the same ball and feed: table and one picture
scrimmage bench compare a.toml b.toml --feed-from maya.toml       # piece and feed from another file
scrimmage bench gallery examples/bench/hood_shapes                # one HTML page of every idea in a folder
```

- **sweep** (the speed sweep above, saved as `pinch_sweep.toml`) writes `results.csv` and the charts to the results folder. Every combination is checked before the first
  one runs, so a bad value fails at once, named (`wheel.top.diameter=-1: ...`). `results.csv` is SI; the printed
  table and the charts show exit speeds and the peak load in the Units setting (`exit_speed (ft/s)`), and a chart's
  swept length, weight or speed key too (`wheel.top.diameter (in)`, for a `diameter_in` key as well).
- **compare** runs two or more files with **one piece and feed**: `[piece]` and `[[inlet]]` from `--feed-from`, or
  from the first file; everything else (shapes, shafts, outlets, `[run]`) is each file's own. So the inlet must fit
  every design. It prints each idea's headlines and one table, and writes `compare.csv` and `compare.png` (the side
  pictures side by side, three to a row) to `<first file's folder>/compare-results/` or `--out`. Each picture keeps
  its own scale bar.
- **gallery** runs every `*.toml` in the folder once (not subfolders; `[sweep]` is ignored) and writes
  `<folder>/gallery.html` (or `--out`): each idea's author, idea line, picture and headlines, then a table of them
  all. The pictures are inside the file, so it opens with no server and can be emailed on its own. A file that
  doesn't run is listed with its problem instead of stopping the gallery.

**Agent tools.** The Scrimmage MCP server has the same five: `bench_check(path)`, `bench_run(path, out_dir)` (the
headlines, the side picture and the pass sheet as images), `bench_sweep(path, out_dir)`, `bench_compare(paths,
feed_from, out_dir)` and `bench_gallery(folder, out)`. Give them absolute paths. They don't need a robot session and
leave an open one alone. The agent writes and edits bench files with its own file tools.

## How accurate is this?

The bench was checked against the launcher model, the dedicated hooded-shooter model (itself uncalibrated, and
believed to read low), with `examples/bench/hooded.toml`: the launcher model's hooded example rebuilt from a wheel and
two curves, fed one 2020 Power Cell from rest.

**Grip (2000 rpm, 33.3 rps): within 2%.** Exit speed at three squeezes:

| squeeze | bench | launcher model | difference |
|---|---|---|---|
| 0.15 D | 4.95 m/s | 5.03 m/s | −1.6% |
| 0.21 D | 4.90 m/s | 4.98 m/s | −1.6% |
| 0.28 D | 4.81 m/s | 4.90 m/s | −1.9% |

**Slip (4000 rpm, 66.7 rps): the two models disagree, by up to 20%.**

| squeeze | bench | launcher model | difference |
|---|---|---|---|
| 0.15 D | 7.48 m/s | 6.23 m/s | +20.0% |
| 0.21 D | 8.82 m/s | 7.46 m/s | +18.1% |
| 0.28 D | 8.26 m/s | 8.38 m/s | −1.5% |

This is where the two models differ in what they assume, not a bug in either. The launcher model holds the ball on a
circle round the wheel; the bench lets it move outward under its own speed, so at high speed it presses harder on the
hood and less on the wheel. Which is closer to a real shot is not known: no measured shot is available to settle it.
At high wheel speeds and light squeezes, read the bench's speeds as possibly high, and compare designs at the same
speed rather than across speeds.

**Launch angle** comes out of the physics after the ball leaves the hood: 48 to 55 degrees on the bench for a hood
built for 50 (the launcher model gives exactly 50 by construction).

**Other checks**, run in the tests:

- Cutting a curve into 2 or 4 times as many flat pieces changes the exit speed by at most 0.44%.
- No piece leaves with more energy than it was given (every example).
- The same file gives the same results every time.

A one-off measurement while building the bench (not repeated by the tests): a ten times smaller timestep changed
the exit speed by 0.1%.

**The design spike** (a throwaway test before the bench was built, 2026-10-30) found the same picture: within 1% of the
launcher model at 2000 rpm, about 20% higher at 4000 rpm at 0.15 and 0.21 D and equal at 0.28 D, launch angles within
1 to 4 degrees. It also fired a rigid toy car between two wheels: it left at 86 to 90% of the wheels' surface speed
(the wheels slowed 9 to 12% under the load), stayed upright, and gave the same results at 1 ms and 0.1 ms steps; a
5 mm off-center entry sent it 8 to 10 degrees off line, so guides matter. MuJoCo's own soft contacts were tried for
foam balls and rejected: a curved hood cut into flat pieces gave exits 13 to 17% low and depended on how finely it was
cut, which is why squishy pieces use the launcher model's laws with contacts merged.

**Speed.** A foam ball takes about 0.3 s of computer time per ball (0.1 s of it the contact itself) on the
development machine; `hooded.toml` with ten balls runs in about 3 s, with its pictures in about 4. Rigid pieces are
faster.

**What is not modeled.** Parts are rigid (no bending, no breaking); the bench reports loads only. Squishy pieces are
balls only. Air drag inside the bench is ignored (it matters only in `[flight]`), and a rigid piece in `[flight]` has
drag but no lift. Ball, tread and material numbers are estimates: **UNCALIBRATED**. Measure the ball's stiffness (a
squeeze test) and bounce (a drop test) first, then the
tread's friction (a sled pull): [measuring.md](measuring.md) has each test. The bench doesn't take a measurement file
yet: type a measured `stiffness_n` or `restitution` into the `[piece]` (the averages `scrimmage measure` prints). Its
last line still says UNCALIBRATED, since it can't tell a typed number from an estimate.

### Known limits

- **A rigid piece in a pinch.** A rigid NOTE squeezed between two wheels isn't foam. Held still, it is squeezed as
  hard as its `stiffness` says: the bench spreads the stiffness over the 16 contact points it makes with each wheel.
  Going through, though, the flat sides of its 24 slices strike the wheels (several hundred N, where the launcher
  model's pinch has 19 N) and it rocks about 25 mm, so the wheels slow far more (46% against the model's 4% on the
  dry run's 1.25 in pinch) and it leaves slower (7.1 m/s against the model's 9.6). Compare rigid-NOTE pinches with
  each other, not with the launcher model, or use `flex = true` ([Flex NOTE](#flex-note-experimental)), which agrees
  with the model within 4% on a quarter squeeze. (A test keeps the rigid case as a known failure.)
- **Flex NOTEs touching each other are slow.** A flex NOTE resting flat on another makes about 900 contacts a step,
  and the bench reads each one in Python: one simulated second of two stacked NOTEs took about 20 s of computer time
  on the development machine, where one NOTE landing on another's rim and sliding off took about 3 s. A pinch shot
  is unaffected (few contacts). Keep `count` low and space the feed out so flex NOTEs don't pile up; the contact
  reader will be made faster if a flex intake ever needs it.
- **The flex NOTE is experimental**: fitted to estimates, on an integrator MuJoCo still calls new.
- **Wheels are drawn with flat sides.** With a rigid piece other than a ball, each wheel (and each CAD wheel on a
  shaft) touches it as a 64-sided prism, not a round cylinder, so the picture shows the flats. MuJoCo 4.0.1 gives a
  piece's edge only one contact on a round wheel's tread, off center, which tips the piece; the prism gives the two
  the edge should have. The flats change a shot's speed by about half a percent. Balls still meet round wheels.

## CAD

When the room has picked an idea, its rough shapes can be swapped for CAD one at a time, so the rough and the
detailed versions run on the same bench with the same piece and feed (`bench compare` them). Students mostly draw in
Autodesk Inventor; there are two ways in.

**A part that doesn't move** (a hood, a ramp, a guide, a bracket): save a copy from Inventor as STL (Save Copy As,
type STL; its Options set the units and how finely curves are cut) or OBJ, and name it in a `[[cad]]` entry with its
units. Inventor writes millimeters or inches; the file doesn't say which, so `units` is required. A wrong unit makes
the part 25 or 1000 times too big or small, which the picture shows at once.

```toml
[[cad]]
name = "hood"
mesh = "cad/hood.stl"      # relative to the bench file
units = "mm"               # "m", "mm" or "in": what the file's numbers mean
center = [0.0, 0.0, 0.0]   # where the file's origin goes on the bench
rot_deg = [0.0, 0.0, 0.0]  # turn it into the bench frame (x forward, z up, y across)
material = "polycarbonate"
```

Easiest: draw the part in Inventor with its origin where the bench's is (the main shaft's center, say) and its axes
the bench's (x forward, z up), then `center` and `rot_deg` stay zero. Otherwise place it with them as you would a
plate. Remove the rough shape it replaces (a `[[curve]]` hood, say) or the piece meets both.

**A moving assembly** (a shooter with its rollers, an intake): go through FreeCAD. Save the Inventor assembly as STEP,
open it in FreeCAD, join the components with the Assembly workbench, mark the rollers as wheels and give each roller's
joint a motor slot (the panel's Slots table), and export a part folder with the Scrimmage exporter ([docs/freecad.md](freecad.md)). Then:

```toml
[shaft.shooter]
motor = "KrakenX60"
target_rps = 50
kP = 0.2
wheel_inertia = 0.0006     # the part's own masses aren't used: give the shaft's inertia here

[[cad]]
name = "shooter"
part = "parts/shooter"     # the exported folder (holding part.toml)
center = [0.0, 0.0, 0.0]
material = "aluminum"
tread = "35A"
slots = { flywheel = "shooter" }   # the part's motor slot "flywheel" is turned by shaft "shooter"
```

- Every body is fixed to the bench, except one whose joint carries a motor slot named in `slots`: it turns with that
  bench shaft, about the joint's axis (positive turns are right-handed about the joint's axis, flipped when the slot
  has `invert`). The shaft's motor, gearing, control and inertia are the bench's `[shaft]` table's; the part's motor
  slot only says which joint it is. A shaft that also has `[[wheel]]`s must share their axis.
- A joint left without a shaft is welded, with a warning naming it (`joint 'hood_pivot' is welded`). The part's
  couplings and loops aren't used (couple bench shafts with `coupled_to`).
- Only what collides goes on the bench. The exporter's **wheel** components get a collision cylinder, which is a
  wheel with the entry's `tread` (its friction, and against a foam ball the tread's softness). Components marked
  **collide** collide through their meshes. Everything else is look-only and is left off, so mark every component a
  piece can touch.
- Loads are reported per body: `shooter.hood`, `shooter.flywheel`.

**Concave meshes.** MuJoCo collides a mesh as its convex hull, the shape a sheet of cling film would make round it: a
curved hood's hull fills in the hollow the ball runs through. So the bench checks every mesh (a part's colliding
meshes too): when its hull is more than 2% bigger than the mesh itself, it is concave and is split into convex pieces
with [CoACD](https://github.com/SarahWeiii/CoACD), the optional extra:

```sh
pip install scrimmage[bench]
```

To tell, the mesh must be a **closed solid**: every edge shared by exactly two faces, all facing outward. An open
surface, a few loose faces, or a mesh with some faces turned inside out holds no definite volume, so it is an error
(`mesh cad/hood.stl isn't a closed solid (12 open edges): export it as a closed solid`), never run as its hull.
Export the part's solid body (Save Copy As STL from the part file), not a surface or sketch; corners repeated or
rounded by the export are merged first.

Without coacd a concave mesh is an error (`mesh cad/hood.stl is concave ... pip install scrimmage[bench]`); it never
runs with the hollow filled in. A convex mesh (a flat plate, a block) needs nothing extra. The split takes about half
a second for a hood and is saved beside the mesh in `hood.stl.convex/<key>/`, keyed by the file's contents and units,
so a rerun is quick and an edited file is split again. Delete the folder whenever you like; it is remade. When the
mesh's folder can't be written (read-only), the split is saved under the system's temp folder instead
(`scrimmage-convex/`), with a warning.

The seams between pieces: for a foam ball, contacts with one part whose directions are within 30 degrees are merged
(as for a curve's flat pieces), so the seams don't show. A rigid piece meets the pieces as MuJoCo's own contacts and
can feel a seam as a small step where two pieces meet at an angle. Each piece's inside face is flat, so a curved
surface is cut into facets about 7 degrees apart (0.4 mm inside a 0.19 m hood's curve). Check: the hooded example's
flare and hood, written as one OBJ in millimeters and split, gives the curves' exit speed within 0.5%
(`tests/test_bench_cad.py`).

## Troubleshooting

| What you see | What to do |
|---|---|
| `scrimmage: inlet 'feed' overlaps shape 'hood' by 4.2 mm` | The inlet zone's piece would start inside a shape. Move the inlet away from that shape by at least the overlap, or make the zone smaller. |
| `unknown key 'color' in [[outlet]] 'shot'; valid keys: ...` | A typo or a key that doesn't exist; the message lists the valid ones. |
| `[piece] is squishy with shape box, but squishy pieces are balls only` | Use `kind = "rigid"` with a `stiffness` as a stand-in. |
| Every piece is `escaped`, its path going backwards | A wheel turns the wrong way: flip its `axis` (`[0, -1, 0]`), see [which way a wheel turns](#a-first-bench-file). |
| Pieces bounce back off a bar, plate or hood into the wheels, or escape out of the bottom of the mechanism | Not the same as a wrong-way wheel (check the wheel first: are the paths going *forward* at all?). The piece reaches the obstacle too fast or too square on: angle the bar or plate so it deflects rather than stops the piece, move it further from the wheels, lower the wheel speed, or add a plate under the path to catch it. `loads.csv` shows how hard it was hit. |
| Pieces fall away before reaching the mechanism | The feed is too slow or too far away (a ball rolling uphill stops). Move the inlet closer or give it more `velocity`. |
| ``0 of 3 got out through `shot` `` but the paths reach it | The outlet box is in the wrong place, or too small: a piece is out only when its *center* enters the box. |
| Pieces end `timed out` | `[run] duration` is too short, or pieces are rolling about slowly; a longer duration or a steeper path. |
| Pieces end `jammed` | That is a result: the picture shows where (the headline names the part). If they jam on a slow but moving path, lengthen `jam_time`. |
| `the run blew up at t = 0.4123 s: ...` | A contact is far too stiff for the timestep: a gap much smaller than the piece, or a rigid piece's `stiffness` too high. Check the squeeze, sizes and `stiffness`; a smaller `[run] timestep` can help. |
| `scrimmage: no pictures: ...` | No OpenGL: on a machine without a GPU, `sudo apt-get install libosmesa6` and set `MUJOCO_GL=osmesa`. The tables are still written. |
| A run takes minutes | Many foam balls touching many curve pieces is the slow case; fewer pieces (`count`) while trying ideas, more for the final numbers. |
| `bench view` shows nothing | It needs a display; use `bench run` and `pass.gif` instead. |
