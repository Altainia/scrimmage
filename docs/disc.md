# Disc launcher model

`scrimmage disc` models a disc (Frisbee) launcher and the disc's flight: the 2013 ULTIMATE ASCENT shooters, where
11 in. discs were fired through slot goals on the alliance wall. A disc is not a ball. It leaves the launcher rolling
along a fence, and in flight it is a wing: at 12 m/s the default coefficients give a lift of about 46 % of its weight
flying level (alpha 0) and more than its weight at 5 degrees, so a ballistic model under-predicts the height at the
wall by about a meter from the auto line, enough to miss the goal.

> **Every number it prints is labeled `UNCALIBRATED`.** The disc's inertia and rim stiffness and the treads'
> friction on the disc are estimates until you measure them (see [Measure these first](#measure-these-first)). Trust
> trends ("more compression, faster exit") before absolute numbers ("10.5 m/s").

- [Quick start](#quick-start)
- [What is modeled](#what-is-modeled)
- [Disc scenario](#disc-scenario)
- [The coefficient sets](#the-coefficient-sets)
- [Known limits](#known-limits)
- [Measure these first](#measure-these-first)
- [The setup file](#the-setup-file)
- [Reading results.csv](#reading-resultscsv)
- [Calls made, and how to change them](#calls-made-and-how-to-change-them)

## Quick start

```bash
scrimmage disc run examples/disc/linear.toml      # four 60A rollers in series, from the auto line at the HIGH goal
scrimmage disc sweep examples/disc/linear.toml    # the same, at five launch angles (its [sweep] table)
scrimmage disc run examples/disc/arc.toml         # one 8 in. pneumatic wheel with a half-turn arc fence
scrimmage disc run examples/disc/pinch.toml       # two wheels at different speeds
scrimmage disc table examples/disc/linear.toml --rpm 2000:6000:1000
```

`run` runs the file's setup once (it ignores any `[sweep]`); `sweep` runs every combination of the `[sweep]` table.
Both print a table and write `results.csv`, `summary.md` and charts (PNG, needing matplotlib) to
`<file's folder>/<file name>-results/`, or to `--out FOLDER`. A bad input prints `scrimmage: <what's wrong>` and exits
with code 2; the message names the bad key and lists the valid ones.

```
$ scrimmage disc run examples/disc/linear.toml
UNCALIBRATED: estimates from preset material numbers, not fitted to measurements (disc inertia, rim stiffness, tread friction).
exit_speed  spin_rpm  advance_ratio  yaw_deg  speed_drop_pct  min_speed_pct  burst_s  flight_time  apex_m  landing_m  wall_height_m  wall_lateral_m  scored  fired  launched  jammed  unfed
     10.51     714.4          0.994   0.7039           11.79           86.8    1.623        2.632   3.235      12.78          2.899         0.09186       4      4         4       0      0
```

Four discs left at 10.5 m/s, 0.44 of the wheels' 23.9 m/s surface speed, spinning counter-clockwise at 714 rpm
(`advance_ratio` 1: rolling along the fence), within 1 degree of the channel's direction. They reached the wall
2.90 m up (the HIGH goal's slot is 2.645-2.950 m), and all four scored. `sweep` over the tilt shows how narrow that band is (`launcher.tilt_deg` is a swept key):

```
$ scrimmage disc sweep examples/disc/linear.toml
launcher.tilt_deg  exit_speed  ...  wall_height_m  wall_lateral_m  scored  fired  launched
               20       10.51  ...          2.165  ...                 0      4         4
               22.5     10.51  ...          2.406  ...                 0      4         4
               25       10.51  ...         2.651  ...                  3      4         4
               27.5     10.51  ...         2.899  ...                  4      4         4
               30       10.51  ...         3.152  ...                 0      4         4
```

`table` fires one disc at each wheel speed and fits the exit speed, the simple numbers to put in your own robot code:

```
$ scrimmage disc table examples/disc/linear.toml --rpm 2000:6000:1000
UNCALIBRATED: estimates from preset material numbers, not fitted to measurements (disc inertia, rim stiffness, tread friction).
 rpm  surface_m_s  exit_speed   ratio  spin_rpm  advance_ratio
2000        10.64       4.829  0.4539     330.3          1.001
3000        15.96       7.234  0.4533     491.2         0.9935
4000        21.28       9.506  0.4467     645.6         0.9936
5000         26.6       11.45  0.4303     777.9         0.9942
6000        31.92       13.13  0.4113     892.1         0.9943
exit speed = 0.429 x wheel surface speed (least squares through the origin)
```

`--rpm` sets the first wheel group's speed; other groups keep their file ratio to it (a pinch keeps its speed
difference). The ratio falls at high rpm as slip grows. The fit line is a rough summary of that curve.

## What is modeled

Two models, each usable alone and tested alone: `scrimmage/disc/launcher.py` (the disc in the channel) and
`scrimmage/disc/flight.py` with `aero.py` (the disc in the air). `scrimmage/disc/run.py` joins them:
`exit_to_flight_state` turns a launcher exit into a flight state (the same function places a disc in the world).

**The launcher.** A planar model: seen from above, the disc is a rigid circle of radius r with three degrees of
freedom, its center (x, y) and its spin Omega (counter-clockwise positive). Wheels (radius R_w) turn on shafts, one
shaft per wheel group, each a `ShaftModel` with the ball launcher's velocity controller and one shared battery.
Wheels touch the disc's rim; so do the fence (straight, or an arc concentric with its wheel) and the guide walls. The
layouts:

- `linear`: wheels in a row on one side, a straight fence on the other.
- `arc`: one wheel and a fence wrapping `arc_deg` round it (up to a half turn).
- `pinch`: wheels on both sides, no fence. Independent wheel speeds set the spin.

Each contact has an overlap delta and a normal force from two springs in series (the wheel's tread, the disc's rim)
with damping:

    N = max(0, k delta + c_d delta'),   c_d = 2 zeta sqrt(k m)
    squeezed between a wheel and the fence:  k_series = 1 / (1/k_wheel + 1/k_rim)

`k_rim` is the disc's diametral stiffness, measured by squeezing the disc between two plates. The tread's stiffness
comes from its durometer (the ball launcher's relation) or from `[layout] wheel_stiffness`. Friction is the ball
launcher's regularized Coulomb law, with s the slip of the other surface's speed past the disc's rim:

    s = u_surface . t - (v . t - r Omega),   F = mu N tanh(s / v_s),   torque on the disc = -r F

Guide rails above and below add sliding friction from the disc's weight. The equations of motion, with J_g the
shaft's inertia and a - b w the motor's torque:

    m v' = sum N n + sum F t + F_rail,    I_zz Omega' = -r sum F,    J_g w_g' = a_g - b_g w_g - sum F sigma R_w

Each substep (1e-4 s unless `dt` is set) is semi-implicit. The model reports each disc's exit speed, direction
(`yaw_deg`), spin and position, each wheel group's speed drop, and an energy account that closes: shaft work plus
entry energy equals exit energy plus slip, damping and rail losses.

**The flight.** A 6-DOF rigid body (position, velocity, orientation quaternion, body angular velocity) integrated by
RK4 at 1 ms, under gravity and Hummel's aerodynamics (Hummel 2003, with Hubbard and Hummel's corrections), in his own
code's forms. With q_inf A = 1/2 rho v^2 A, A = pi d^2 / 4 and the moment arm d, the angle of attack alpha between
the velocity and the disc plane, and r, p, q the angular velocity along the disc's axis, its forward direction and
its lateral direction:

    lift   L = C_L(alpha) q_inf A         drag   D = C_D(alpha) q_inf A
    pitch  M = q_inf A d (C_M(alpha) + C_Mq q)
    roll   R = q_inf A d (C_Rr(alpha, AdvR) + C_Rp p)          AdvR = d r / (2 v)
    spin-down  N = C_Nr r

Euler's equations with the gyroscopic term give the precession that banks a spinning disc: a pitching moment M
turns the angular momentum, I_zz Omega, so the disc rolls rather than pitches, at about M / (I_zz Omega). The bank
tilts the lift sideways and curves the path. The bank direction comes out of the physics: a wheel on the right gives
counter-clockwise spin, and the disc banks left. With the aero set to `none` the flight is an exact parabola.

**The run.** `run` spins the wheels up from rest, then offers discs one at a time (`[feed]`: `count`, `rate`, and the
rule `wait_for_speed` or `schedule`, as the ball launcher's); the channel holds one disc at a time. Each launched disc
is placed in the world by `exit_to_flight_state`: the channel is tilted up by `[launcher] tilt_deg` about its cross
axis, the velocity lies in the tilted plane (rotated by the exit's `yaw_deg`), the spin is about the disc's axis, and
the robot's velocity is added. It flies until it falls to half the disc's height. A disc scores when its center
crosses the goal's rectangle on the wall going downrange.

**In the world.** A range file with `[range] disc = "2013-frisbee"` (the built-in `2013` range,
[docs/test-range.md](test-range.md#the-range-file); for climbing the pyramid use `2013-pyramid`, [docs/climbing.md](climbing.md)) puts discs in the MuJoCo world as free cylinders. Each physics
step an airborne disc gets the same `aero.forces` as the standalone model, applied through `xfrc_applied` (the torque
turned into the world frame). MuJoCo integrates it with the world's own integrator (implicitfast, 1 ms, the forces
held over each step), not RK4, so the two differ: for the FRC shot (12.2 m/s from 0.76 m, 12 degrees, 90 rad/s) they
agree within 1.4 mm over 5 m and 7 mm over 13 m, banking and curving to the same side
(`tests/test_range_discs.py`). A robot shoots them with the stock `disc_shooter` part and `[shooter] kind = "disc"`
([docs/test-range.md](test-range.md#a-disc-shooter)), which runs this launcher model on the robot's battery and
launches each disc from the tilted channel with its orientation and spin.

## Disc scenario

`scenarios/disc_auto_line.toml` puts the model on a whole robot. `configs/disc_robot.toml` is the kitbot chassis on a
PWM differential drive with a stock `disc_shooter` on top (the part's defaults: four 4 in. rollers on one shaft, 2 x
Kraken X60 at 0.75:1, the channel's entry 0.70 m up, tilted 22.5 degrees) and a loader roller, on the built-in `2013`
range at `auto_line_left` with 4 discs preloaded. Its robot program is the test stand-in `tests/fake_robot_main.py
--shoot`: through autonomous it holds the shooter's PWM at a fixed output and runs the loader from a set time in
(`--shooter-pwm 2 --shooter-output 1.0 --feed-pwm 3 --feed-at 1.5`, all flags).

```bash
scrimmage run scenarios/disc_auto_line.toml
```

The four discs leave at 13.25 m/s spinning at 900 rpm, about 1.8, 2.0, 2.2 and 2.3 s into auto, and all four score in
the HIGH goal by 2.8 s. The checks are `by_time score.high >= 3 by t=6` and `at_end fired >= 4`
(`tests/test_disc_scenario.py` runs it, and a copy tilted 8 degrees lower, which fails on the score).

**Choosing the tilt.** At full output, stepping the fixed tilt in the world (the scenario's robot, shooter on from
t = 0, loader from 1.5 s):

| tilt (deg) | high | first hit |
| --- | --- | --- |
| 14.5 | 0 | the alliance wall, under the slot |
| 20 | 0 | the backing plate under the slot |
| 21.25 | 0 | the slot's bottom frame |
| 21.5 | 4 | |
| 22.5 | 4 | |
| 23.75 | 4 | |
| 24 | 0 | the slot's top frame |
| 25 | 0 | the slot's top frame |

All four score from 21.5 to 23.75 degrees; 22.5 sits in that band, 1 degree from the bottom and 1.25 from the top.
`scrimmage disc sweep` on a matching setup (the same motors and gearing, 0.89 m exit height, 5.0 m to the wall)
gives the same exit speed and spin (13.22 m/s, 899 rpm) and scores from 22 to at least 24.5 degrees (the top of its
sweep). The two agree to about half a degree at the bottom of the band; the rest is the rough height and distance,
and the slot's frame, which the standalone model doesn't have. A lower output doesn't open a wider band: at 0.85, 22
degrees still scores and 24 to 34 miss, and at 0.7 every tilt from 26 to 44 flies over. A 12 in. slot from 5 m is a
narrow target, as it was in 2013: a real robot would need its tilt within about a degree, and these numbers are only
as good as the estimates above.

The run is deterministic in lockstep: two runs give the same score, the same fired count and an identical `run.csv`.

## The coefficient sets

The aerodynamic set is chosen by `[air] aero` (default `pc-long`). All are in the forms of Hummel's code
(`scrimmage/disc/aero.py` cites its pages).

| Set | Lift, drag, pitch | Damping | Source |
|---|---|---|---|
| `pc-long` (default) | Potts and Crowther's tables against alpha (spline); C_Rr against alpha and AdvR | C_Mq -0.0144, C_Rp -0.0125, C_Nr -3.41e-5 | PC02; the damping is Hummel's long-flight fit, his recommendation (HUM03 §3.5) |
| `hummel-short` | linear: C_L = 0.3331 + 1.9124 a, C_D = 0.1769 + 0.685 (a - a0)^2, C_M = -0.0821 + 0.4338 a | -0.005, -0.0055, -7.1e-6 | HUM03 Table 3-2, fitted to three 2 m flights |
| `hh02` | linear: C_L0 0.188, C_La 2.37, C_D0 0.15, C_Da 1.24, C_M0 -0.06, C_Ma 0.38 | C_Mq 0.0008, C_Rp -0.013, C_Nr -2.8e-5 | Hummel and Hubbard 2002, as quoted in the addendum |
| `none` | all zero | all zero | a test set: a parabola |

Beyond the tables' range (alpha -10 to 30 degrees) the model extends smoothly to a flat plate at 90 degrees (drag
coefficient 1.17, Hoerner 1965); that extension is an estimate of this model, not of the papers. The disc presets
are `2013-frisbee` and `ultra-star` (Discraft Ultra-Star, the 175 g disc Hummel flew and whose coefficients these
are). Air density defaults to 1.225 kg/m^3.

Citations:

- S. A. Hummel, *Frisbee Flight Simulation and Throw Biomechanics*, MS thesis, UC Davis, 2003 (HUM03), Table 3-1
  (the disc), Table 3-2 (coefficients), App. A and B (the code).
- J. R. Potts and W. J. Crowther, "Frisbee aerodynamics", 20th AIAA Applied Aerodynamics Conf., 2002 (AIAA
  2002-3150): the wind-tunnel tables.
- M. Hubbard and S. A. Hummel, "Addendum to Simulation of Frisbee Flight" (the corrections), and S. A. Hummel and
  M. Hubbard, "Identification of Frisbee aerodynamic coefficients using flight data", ISEA 2002.
- 2013 FRC Game Manual, §2.2.3 (goals) and §2.2.10 (the disc).

The 2013 disc is treated as an Ultra-Star aerodynamically, with perhaps 30 % uncertainty on C_L0, C_M0 and C_Rr. Nobody
has measured the 2013 disc in a wind tunnel.

## Sources for the estimates

Numbers in the presets and tests are tagged `# source:` (a manual, paper or measurement) or `# estimate:` (a planning
value, to be replaced by a measurement). The estimates rest on these.

- **The disc** (source): the 2013 Game Manual section 2.2.10 (rev. 1/8/13) gives Wham-O part 53214, 11 in. diameter,
  1.4 in. height and 180 +/- 5 g. Its rotational inertias and rim stiffness are estimates; the rim (a few kN/m
  diametrally, about 20 N for 5 mm by hand) is unpublished and should be measured with a scale and calipers between two
  flat plates. A roller's inertia is an estimate for a 4 in. urethane roller.
- **Exit speed against wheel speed** (estimate): exit speed is 0.35-0.48 of the wheels' no-load surface speed for
  single-side wheel, belt and arc shooters. The anchor is one Chief Delphi thread, "Disc Lauching Velocity" (Jan 2013,
  chiefdelphi.com/t/disc-lauching-velocity/124785): a CIM belt shooter with a Delrin backing, about 31 m/s no-load,
  measured at 12.2 +/- 1.5 m/s on 60 fps video (ratio about 0.40). Others: the energy model in "Shooting Physics"
  (chiefdelphi.com/t/shooting-physics/125815), about 0.46 for an 8 in. pneumatic wheel; and Team 254's build blog
  (blog.team254.com, January 2013), 6.7-8.9 m/s on a prototype. Exit speed levels off as wheel speed rises: "6 Inch
  Pneumatic Shooting Wheels?" (chiefdelphi.com/t/6-inch-pneumatic-shooting-wheels/126064) saw similar results from
  2500 to 5000 rpm.
- **Compression** (estimate): "Frisbee shooter" (chiefdelphi.com/t/frisbee-shooter/131061) gives about 1/2 in. as
  standard for linear shooters, with 1/8 in. for a 6 in. rubber wheel on an arc (team 1986). "1114 and 2056's shooter
  wheels" (chiefdelphi.com/t/1114-and-2056s-shooter-wheels/129838) gives 1/4 to 1/2 in. on 60A urethane rollers
  (McMaster 2477K36), where the disc buckled more than the urethane compressed. So the disc's rim, not the wheel, is
  the softer spring for stiff treads. Too much compression distorts the disc and hurts accuracy.
- **Tread on polyethylene friction** (estimate): no pair values are published. The planning ranges come from the
  threads above (what teams used: wedgetop, rough-top, "squishy tape", self-fusing tape, Delrin, Teflon tape, bare
  polycarbonate that gave "only a few meters") and from Gallagher Corp.'s table of polyurethane friction (0.2-2.5,
  softer is higher): soft urethane or rubber 0.6-1.0; pneumatic rubber 0.6-0.9; wedgetop or rough-top 0.8-1.2; bare
  polycarbonate or aluminum 0.2-0.35; Delrin or PTFE tape 0.1-0.2. Worn or dirty discs may be about 20 % lower.
- **Tire stiffness** (estimate): a pneumatic 6-8 in. wheel is about 10-40 kN/m, set by pressure and contact patch; a
  60A urethane roller is about 50-100 kN/m for its contact line.
- **Launch angle** (estimate): from the auto line (5.5 m), at 0.76 m launch height and 12 m/s, the model puts the shot
  in the HIGH goal at about 22-25 degrees with lift (about 30 without).
- **Field and rules** (source): the 2013 Game Manual. Section 2 "The Arena": §2.2.1 (alliance wall, 18 ft by 6.5 ft),
  §2.2.2 (AUTO LINES 108 in. either side of the center line), §2.2.3 (goal openings and heights), §2.2.5 (pyramid);
  section 3 "The Game": rule G24 (§3.2.5.4, a robot controls at most four discs); section 6 "Glossary": the AUTO ZONE
  (between the alliance wall and the auto line). Goal depth, middle-goal spacing and pyramid position are estimates.
- **Aerodynamics** (source): Hummel's 2003 thesis (UC Davis) and Potts and Crowther (AIAA 2002-3150), cited by page in
  `scrimmage/disc/aero.py`.

## Known limits

- **A single roller falls short.** One 60A roller at 1/4 in. compression launches at about 0.27 of its surface speed;
  the real range for single-side wheel, belt and multi-wheel shooters is 0.35-0.48. The model says why: the grip,
  mu k_series c, is only about 24 N, and in an arc the disc's centripetal load takes the squeeze off the wheel as it
  speeds up. It is limited by the normal force and the roller's own stored energy, and it rises steeply with the
  rim stiffness (doubling `rim_stiffness` raises the exit speed by 30 %: 8.5 to 11.1 m/s). That is the estimate to
  measure first. Four rollers in series at 1/2 in. (the `linear` example) land at 0.44, inside the real band. Do not
  tune the stiffness to make one roller match: measure it.
- **A slick fence does not kill spin.** A push at the rim alone spins a disc at r Omega / V = m r^2 / I_zz, 1.35 for
  the 2013 disc, above rolling (1). So a slick fence (PTFE, friction 0.15) still gives a rolling disc, and it
  launches slightly slower; with the presets' wheel friction of 0.8, a fence friction of 0.12 is already enough to roll. The reports of "a few
  meters only" with a bare polycarbonate fence are not reproduced as a spin loss. (Slower exit is reproduced.)
- **Equal wheels in a pinch give no spin**, and unequal wheels spin the disc only once the wheels grip: while both slip
  forward past the disc their friction forces are equal (both mu N) and their torques cancel. The `pinch` example
  needs 1 1/4 in. of squeeze and a wide speed difference for that reason; a lightly squeezed pinch gives a spinless
  disc, which does not fly.
- **Bank is small with the Potts and Crowther coefficients.** At alpha 0-5 degrees the pitching moment is tiny
  (|C_M| about 0.006), so a 12 m/s disc banks about 0.6 degrees in 0.4 s and curves a few centimeters to about 0.3 m
  over the flight. `hh02`'s C_M0 is ten times larger and banks visibly. Which is right for your disc is a
  measurement. The lateral miss is the least certain output.
- **The exit direction** (`yaw_deg`) is set by the guide walls. The squeeze stored against the fence throws the disc
  sideways when the last wheel lets go; the model's walls are an exit guide (critically damped, running on until
  the disc's trailing rim is clear), so the recoil dies there and the disc leaves within about 1 degree of the
  channel's direction at any channel length from 0.50 to 0.70 m. A real guide that is short, loose or springy will
  not do as well: the guide's length and slop after the last wheel are yours to measure.
- **Not modeled:** out-of-plane buckling of the rim in the launcher (the disc stays flat and rigid), the
  Robins-Magnus side force, wind, and, in the standalone model, the disc touching the goal's frame or back wall (scoring is the center crossing
  the opening). In the standalone model discs are fed one at a time and there is no hopper or stack. In `disc run`/`sweep`:
  the world does have the goal frame, box, backing plate and hopper. Random spread (the ball launcher's `[spread]`)
  is not included: no figures for it are published, so a run is deterministic.
- **Speed**: each disc's flight takes about a second of wall time, so a sweep of many combinations takes minutes.
  Cut `[feed] count` while exploring.

## Measure these first

Three numbers set most of the answer and are estimates. Each takes an afternoon.

**Disc inertia (`[disc] izz`, `ixx`), by bifilar pendulum.** Hang the disc from two equal, parallel strings of length
L, tied to opposite points of its rim a distance 2a apart, so it hangs level with its center of mass midway between
them. Twist it a few degrees about the vertical axis, let go, and time 20 swings to get the period T. Then
I_zz = m g a^2 T^2 / (4 pi^2 L), with m the disc's weighed mass. For I_xx, hang it on edge from the same strings, tied
to two points of the rim on a horizontal diameter, and twist it the same way: the axis is then a diameter. Keep L long
(1 m or more) and the twist small. Preset: I_zz 0.0026, I_xx 0.00135 kg m^2, the Ultra-Star's scaled by m d^2.

**Rim stiffness (`[disc] rim_stiffness`), by plates and a kitchen scale.** Stand the disc on edge between two flat
plates on a scale. Press the top plate down in steps and read the force at each squeeze (squeeze measured with
callipers or a ruler on the plates). The slope, force over squeeze, is the disc's diametral stiffness `k_rim`
(N/m); use the range of squeeze your shooter uses (6-13 mm), because a thin rim is not linear. Preset: 5000 N/m.
The rim is the softer of the disc and a 60A roller, so this is the number the single-roller result depends on.

**Tread-on-disc friction (`[wheels] friction`, `[layout] fence_friction`), by inclined plane.** Glue a strip of the
tread (or fence) material to a board, lay a piece of the disc's rim (or a whole disc, rim down) on it, and raise the
board until the disc just slides. Friction is the tangent of that angle. Do it a dozen times and take the middle:
polyethylene on rubber varies. Presets: 0.8 for 60A urethane, 0.75 for pneumatic; fences 1.0 (wedgetop), 0.8 (rubber
tape), 0.28 (polycarbonate), 0.15 (PTFE). All estimates.

**Exit speed and spin, by 240 fps video, to check the whole model.** Film the shot at 240 fps (a phone's slow-motion mode) against a ruler or a known length. Exit speed is the
distance the disc moves between frames over the frame time, once it has left the wheel: film it from the side. Spin
needs the disc's top in view, so film from above, or use a second camera above the channel (a side view sees the
marker only edge-on). Draw a marker line across the disc's top; the spin is the marker's turns between frames over the
frame time. Compare with `spin_rpm`. If the speed and spin of the real shot differ from the
model's in the same way after the three measurements above, the next suspect is the squeeze (`compression`), which
is hard to measure on a real shooter. Use `scrimmage disc table` to compare exit speed against wheel rpm.

Replace the estimates in your copy of the setup (`[disc] izz = 0.0027`) as you measure them; the preset's values are
there to be overridden.

## The setup file

Sections and keys (all SI; angles end in `_deg`). The examples in `examples/disc/` mark which numbers are estimates.

| Section | Keys |
|---|---|
| `[disc]` | `preset` (`2013-frisbee`, `ultra-star`), or `diameter`, `mass`, `height`, `izz`, `ixx`, `rim_stiffness`, `rim_damping_ratio` |
| `[wheels]` | `preset` (`urethane-60a-4in`, `pneumatic-8in`) or `diameter`, `width`, `durometer`, `friction` |
| `[layout]` | `kind` = `linear`/`arc`/`pinch`; `wheels` = list of `{ x, side, group }` (x downrange from the entry, side `left`/`right` looking downrange); `fence` (a preset: `wedgetop`, `rubber-tape`, `polycarbonate`, `ptfe`) or `fence_friction`; `compression` (or `fence_radius` for an arc); `channel_length`; `arc_deg` (arc, up to 180); `rail_friction`; `wheel_stiffness` |
| `[shaft]`, `[control]` | the ball launcher's (`wheel_inertia`, `motor`, `gearing`, `target_rps`, `kP`, current limits). One table for all wheel groups, or `[[shaft]]` and `[[control]]` with one table per group, in group order. A group's `wheel_inertia` is all its wheels' together |
| `[launcher]` | `height` (m; the disc's center at the exit), `tilt_deg` (the launch angle). Defaults 0.76 m and 25 degrees, estimates |
| `[goal]` | `distance` (m, launcher to wall), `width`, `height`, `bottom`; defaults to the 2013 HIGH goal (54 x 12 in., bottom 104 1/8 in.) at the auto line, 216 in. |
| `[air]` | `density` (1.225), `aero` (`pc-long`, `hummel-short`, `hh02`, `none`) |
| `[battery]`, `[robot]`, `[feed]` | as the ball launcher's; `[robot] velocity` is added to the disc's. `[feed]` `count`, `rate`, `rule`, `tolerance`, `entry_speed`, `timeout` |
| `[sweep]` | `"section.key" = [values]`: every combination is run by `sweep`. A key in a per-group `[[control]]` sets every group |
| `[[chart]]` | `kind` = `line` (x a swept key, y a column) or `shaft` |

The 2013 goals: HIGH 54 x 12 in., bottom 104 1/8 in.; MIDDLE 54 x 21 in., bottom 88 5/8 in. (two, centers 58 in. either
side of HIGH, an estimate); LOW 29 x 24 in., bottom 19 in. (manual §2.2.3). Discs are held to 4 per robot (rule G24).

## Reading results.csv

One row per setup (per combination for `sweep`), the swept keys first. One value per column, the mean over the discs
launched, unless noted. results.csv is in SI; the table `disc run` and `disc sweep` print, `summary.md` and the
charts show the exit speed, apex, landing and wall heights in the Units setting (or `--units`), the unit in the
column's name (`landing (ft)`), as [settings.md](settings.md) says.

| Column | Meaning |
|---|---|
| `exit_speed` | m/s, along the channel at the exit |
| `spin_rpm` | the disc's spin; positive counter-clockwise seen from above (a right-hand wheel) |
| `advance_ratio` | r Omega / V at the exit: 1 for a disc rolling along its fence, above for a harder rim push, near 0 for no spin |
| `yaw_deg` | the exit direction from the channel axis, positive toward the left |
| `speed_drop_pct` | the largest wheel group's speed lost over the pass, % |
| `min_speed_pct` | the lowest any wheel group's speed fell, % of its target, over the burst |
| `burst_s` | time from the first disc's entry to the last one's exit |
| `flight_time`, `apex_m` | seconds in the air; highest height of the disc's center |
| `landing_m` | downrange distance where the disc reaches the floor |
| `wall_height_m`, `wall_lateral_m` | where the center passes the wall (x = `goal.distance`): height, and sideways (positive left); nan if it lands short. The HIGH goal's slot is 2.645-2.950 m, +-0.686 m |
| `scored` | discs whose center crossed the goal's rectangle |
| `fired`, `launched`, `jammed`, `unfed` | counts: discs fed; left the channel; stuck in it; never fed before `feed.timeout` |

`summary.md` holds the same table. `shaft.png` shows wheel group 0's speed; with a `[sweep]`, the line chart plots
`exit_speed` against the first swept key.

## Calls made, and how to change them

These were decided without asking; each is a one-line change if you disagree.

- The launcher model is planar (x, y, spin). Out-of-plane behavior is not modeled.
- Flight uses Hummel's 6-DOF form with Potts and Crowther and his long-flight damping, his own recommendation. Other
  sets are `[air] aero` presets.
- The 2013 disc is an Ultra-Star aerodynamically, with an inertia scaled by m d^2.
- Goals are slot rectangles; scoring is the center crossing the opening. The manual's "any part crossed and in the
  goal at the end" is approximated, with no revocation if a disc bounces out.
- Goal depth is about 24 in., an estimate: the field drawings were not found. It matters only in the world.
- The disc starts its flight at the exit point (the distance along the channel is not added), with the launcher's
  height as the disc's center height.
- Capacity is 4 discs (rule G24).
- Discs run one at a time through the channel; the feed's `rate` is a ceiling.
