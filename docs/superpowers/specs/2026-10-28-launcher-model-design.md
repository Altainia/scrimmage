# Launcher model and design comparison (sub-project 8): design

## Why

FRC games that score balls reward launchers that fire quickly and land tightly. The game the user's team
(FRC 5098) last played had a large hopper: teams fired balls in rapid succession, and some fired several at
once. Designing such a launcher means choosing things that today are only found by building and testing:
- how much to squeeze ("squish") a given ball between the wheels and the backing plate (hood);
- what wheel speed and torque the shooter needs;
- how heavy a flywheel to use, trading speed lost per shot against time to spin back up;
- how the hood angle changes where balls land and how tightly they group, including an articulated hood that
  changes angle during a match.

The team also can't test shooting well at home: their workshop ceiling is low, so real shots hit it. Last season
they rarely got to test, which kept them from tuning their autonomous shooting. They kept no robot logs of
flywheel speed.

A ball game is unlikely next season, so this is a long-term capability: it should be right rather than quick.

## The larger project and where this fits

Shooting in Scrimmage is four pieces, built in this order. Each gets its own design.

1. **Launcher model** (this design): the physics of a ball passing through a hooded launcher, and its flight.
2. **Design comparison** (this design): a command that runs combinations of launcher settings through the model
   and reports a table and charts.
3. **Test range and robot code** (later): balls, a hopper, a feeder and a scoring goal in the MuJoCo world, with
   the launcher as a robot part that the team's code drives in Phoenix and fast mode. Scenarios could then check
   "8 of 10 scored by 15 s".
4. **Calibration** (later): fit the model's material numbers to measurements, such as slow-motion video of flat
   shots or flywheel-speed logs.

The user also plans a **hopper model** later, whose output is the stream of balls it feeds to the next stage.

Pieces 1 and 2 come first because they answer the design questions on their own, without robot code or a
field, and they prove the physics before pieces 3 and 4 build on it.

## The launcher

The mechanism, as the user described it:
- One set of shooter wheels on a powered shaft, with a curved backing plate (hood) wrapped partway around them.
- A ball fed in at the entry is pinched between the spinning wheels and the hood. It rolls along the hood's
  curve and leaves where the hood ends.
- The hood can be articulated: rotating it changes where the ball leaves, so it sets the exit angle. That allows
  aiming from different places and while the robot moves.

Other launcher types (two wheels facing each other, a top roller, a turret) are not part of this design. The
model must not assume a design where they couldn't be added later.

## Goals

### Piece 1: launcher model

1. **Inputs**, all plain numbers in a TOML file, each with an override:
   - **Ball:** diameter, mass, stiffness (force against compression), energy loss when compressed and released,
     friction against the wheel and against the hood.
   - **Wheels:** diameter, width, hardness (durometer), friction, and their positions along the shaft (or a
     single continuous roller), plus the launcher's inside width. A ball touches the wheels that overlap it, so
     a ball between two wheels may touch both, one or neither, and that sets its grip.
   - **Shooter shaft:** inertia of the wheels and shaft, plus any added flywheel; motor type and count (from
     `scrimmage/motors.py`) and gearing.
   - **Battery:** nominal voltage and internal resistance, using the existing `Battery` sag model, so a burst
     that draws hundreds of amps sags the voltage and slows recovery.
   - **Flywheel control:** a target speed and a velocity controller in the style the team uses on a Talon
     (feedforward kV and kS, kP), with stator and supply current limits.
   - **Hood:** the gap between the hood and the wheels (constant along the arc; with the ball's diameter it
     sets the squish), where the ball enters (fixed on the robot), friction, and its angle. The hood is
     concentric with the wheels and pivots about the shooter shaft (the user's choice, see "Hood articulation").
   - **Robot motion:** an optional robot velocity, added to the ball's velocity at exit, for shooting on the move.
   - **Air:** density, a drag coefficient for the ball and a backspin-lift coefficient, with defaults.
   - **Goal:** a target opening at a height, a distance and a size, used to count balls that score.
2. **Presets** for balls used in past FRC games, from their published size, weight and material. Also presets
   for common shooter-wheel materials by durometer. The source of every preset value is cited in the preset
   file, and values estimated rather than published (such as a ball's stiffness) are marked as estimates.
3. **For each ball the model computes:**
   - the contact: how hard the squish presses the ball against the wheel and hood, and how long they touch;
   - grip and slip at the wheel and at the hood: too little squish slips, and too much spends energy deforming
     the ball;
   - the transfer: the ball's exit speed, exit angle and spin (backspin from rolling along the fixed hood), and
     the speed and energy the shooter shaft loses;
   - the flight: drag and backspin lift, until the ball reaches the goal's height on the way down or the floor;
   - whether it scored.
   Exit speed and spin come from the contact physics, not from a fixed rule such as "half the wheel's surface
   speed".
4. **Rapid and side-by-side fire.** Balls fed one after another meet a shaft that may not have recovered its
   speed. Balls fed side by side across wide wheels are in contact at the same time and slow the shaft together.
   The shaft's speed over the whole burst comes out of the model, under the flywheel controller.
5. **The feed is an input.** The model consumes a stream of fed balls. Each has its position across the
   wheels, its entry speed, any differences from the nominal ball (size, stiffness, mass), and when it is
   available to feed. When a ball actually enters is decided by a feed rule:
   - **fixed schedule:** each ball enters when it is available;
   - **wait for speed:** a ball enters only once the shaft is within a set tolerance of its target speed, as many
     teams' code does. Then burst time shows the flywheel trade-off directly.
   By default the stream is generated (see goal 6). A future hopper model can supply it instead, through the same
   interface.
6. **Spread.** A run fires many virtual balls with small random differences, and the scatter of where they land
   is the grouping. The differences default to sensible values and the user can set each one:
   - ball size, mass and stiffness;
   - the shooter's speed (via the controller's response, and an optional extra amount);
   - feed timing, position and entry speed.
   The random generator is seeded, so the same file gives the same results.
7. **Usable later inside the world.** Piece 3 will run the launcher inside a MuJoCo world stepped by lockstep,
   with the shooter motor's voltage coming from robot code. The model therefore has to advance in time steps
   driven by motor voltage (state in, state out). It must not be a batch function only. Piece 2's runs use that
   same stepping.

### Piece 2: design comparison

1. **Command:** `scrimmage launcher run <file.toml>` runs one launcher setup. `scrimmage launcher sweep
   <file.toml>` runs every combination of the options listed in the file's sweep section. Any input can be swept,
   for example flywheel inertia, hood gap, hood angle, target wheel speed, or goal distance.
2. **Results for each combination:**
   - exit speed and spin (mean and spread);
   - shaft speed lost per ball, and recovery time to within a set tolerance of the target;
   - time to fire a burst of N balls under the feed rule;
   - landing spread at the goal;
   - balls scored out of N.
3. **Output:**
   - a table printed to the terminal, and the same as a CSV file;
   - charts as image files: a chosen result against a chosen input (for example exit speed against squish, one
     line for each flywheel), a landing-spot scatter for each hood angle, and shaft speed over a burst.
4. **Uncalibrated label.** Every printed table and chart says the results are uncalibrated estimates. Calibration
   (piece 4) is what would remove that label.

## Hood articulation

The user's choice: the hood is concentric with the shooter wheels and pivots about the shooter shaft. The ball's
entry point stays fixed on the robot; turning the hood moves its exit end around the wheel, so the hood wraps
more or less of the wheel at a constant gap. The hood angle therefore changes how long the ball is squeezed, and
with it the exit speed and spin, as well as the exit direction. The model must show that coupling: sweeping the
hood angle reports all of them.

A hood that swings as one rigid piece (the ball's path unchanged, only rotated) is not modeled.

## Non-goals

- Robot code, the MuJoCo field, game pieces, a hopper, a feeder or a goal in the world (piece 3).
- Fitting to measurements (piece 4). But the inputs must be the numbers piece 4 will fit; no hidden constants.
- Hood motor dynamics: here the hood angle is fixed for each shot or each combination. The hood's motor comes in
  piece 3.
- Other launcher types (see "The launcher").
- A graphical interface.

## Anti-goals

- **Results that look more precise than they are.** Uncalibrated numbers must say so. The team will make real
  design choices from them.
- **Fudge factors.** A tuning constant that isn't a physical property or a documented modeling choice can't be
  fitted in piece 4 and hides how the model behaves.
- **Physics that breaks at the edges:** zero squish, a gap wider than the ball (the ball never touches), the
  shaft stalling under a burst, or a ball that never reaches the goal. Each should give a clear result, not a
  crash or a nonsense number.

## How we'll know it's right

There's no measured data yet, so correctness is checked against physics the model must obey, and against
sanity ranges:
- **Energy:** the energy taken from the shaft and motor equals the ball's kinetic energy (linear and spin) plus
  the losses the model accounts for.
- **Limits:** with no drag the flight is a parabola. With no slip and no loss, exit speed and spin match the
  rolling-without-slipping kinematics of a ball between a moving wheel and a fixed hood.
- **Trends:**
  - more squish raises exit speed until slip stops, then lowers it as losses grow;
  - a heavier flywheel loses less speed per ball and recovers more slowly at the same motor power;
  - a steeper hood angle raises the exit angle.
- **Sanity ranges:** exit speeds for typical FRC setups fall in the range teams report publicly. These are a
  check, not a target.

## Constraints

- Python, inside the Scrimmage package (`scrimmage/launcher/`), using the existing motor models.
- No new required dependency. Chart output may use an optional dependency (matplotlib is already in the `dev`
  extra). Without it, the commands still write the table and CSV and say how to get charts.
- Fits the existing CLI (`scrimmage/__main__.py` subcommands) and TOML style (configs use `[[part]]`-style
  tables; this file has its own sections).
- Tests in `tests/` run in CI in a few seconds.
- Documentation in `docs/launcher.md`: what the model covers, how accurate it is and isn't, the inputs, and the
  presets with their sources.

## Calls made by Claude (the user can change these)

- The launcher is its own model, not MuJoCo contacts. MuJoCo's soft contact can't represent a foam ball crushed
  between a wheel and a plate in a way that can be calibrated. Piece 3 hands MuJoCo the ball as it leaves.
- The ball is modeled moving through the launcher in the plane of the wheel. Side-by-side balls each have their
  own plane and share the shaft.
- Spread comes from random differences, with seeded runs.
- Output is a CSV plus image charts, with charts optional.
- The battery sags under load (existing `Battery` model), rather than holding a fixed voltage.
- The feed rule is an input, with both fixed-schedule and wait-for-speed rules.
- Wheels are placed along the shaft by position (or as one continuous roller).
- The command is named `scrimmage launcher`.

## Left to the builder

- The exact contact law: how force grows with compression, how energy loss is represented (for example a loss
  factor or hysteresis), and how friction is regularized near zero slip. It must stay within the inputs above.
- The time step and integrator, as long as results converge as the step shrinks.
- The drag and backspin-lift formulas and their default coefficients, with cited sources.
- The TOML layout, provided every input in goal 1 has a place and every input can be swept.
- Chart styling.
