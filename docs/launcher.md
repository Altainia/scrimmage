# Launcher model

`scrimmage launcher` models a hooded ball launcher: a spinning shooter wheel, a curved hood the ball is squeezed
against, and the ball's flight to the goal. Use it to compare designs before you build them: how much squish, how
heavy a flywheel, what wheel speed, what hood angle.

> **Every number it prints is labeled `UNCALIBRATED`.** The ball and wheel numbers are estimates until you measure
> your own (see [Measure these first](#measure-these-first)). Trust the trends ("more flywheel, smaller dip") before
> the absolute numbers ("7.4 m/s").

- [Quick start](#quick-start)
- [What is modeled](#what-is-modeled)
- [The two regimes: grip and slip](#the-two-regimes-grip-and-slip)
- [Worked example: flywheel weight and squish](#worked-example-flywheel-weight-and-squish)
- [How accurate is this?](#how-accurate-is-this)
- [Measure these first](#measure-these-first)
- [Inputs](#inputs)
- [Presets and their sources](#presets-and-their-sources)
- [Results](#results)
- [Feeding balls from your own hopper model (`FedBall`)](#feeding-balls-from-your-own-hopper-model-fedball)
- [In the world: the test range](#in-the-world-the-test-range)

## Quick start

```bash
scrimmage launcher run examples/launcher/hooded.toml               # one setup, 5 balls
scrimmage launcher sweep examples/launcher/flywheel_squish.toml    # 9 combinations, with charts
scrimmage launcher sweep examples/launcher/hood_angle.toml         # 4 hood angles, with landing charts
```

`run` runs the file's setup once (it ignores any `[sweep]`). `sweep` runs every combination of the values in the
file's `[sweep]` table. Both print a table, write `results.csv` and write charts (PNG) to a results folder,
`<file's folder>/<file name>-results/` unless you pass `--out FOLDER`. Charts need matplotlib; without it the table
and CSV are still written and the command prints `charts need matplotlib: pip install matplotlib`.

A bad input (a typo in a key, a hood with no wrap) prints `scrimmage: <what's wrong>` and exits with code 2. The
message names the bad key and lists the valid ones.

The three examples in `examples/launcher/` are a single-wheel hooded launcher for the 2020 Power Cell: 4 in. 35A
wheels as one 5 in. wide roller, a Kraken X60 direct drive, a 5.5 in. hood gap (0.21 of the ball's diameter) and a
goal 4 m away. Copy one and change it to your design.

## What is modeled

**The launcher.** The wheel spins on a shaft driven by your motor(s) through a gear ratio, under a velocity
controller like a Talon's (feedforward kS + kV plus kP on the error, with stator and supply current limits). The
battery sags under load (a 12.5 V battery with internal resistance). A ball enters between the wheel and the hood, is
squeezed by the gap, and is driven round the hood by friction. The ball's speed and spin are followed step by step:
wheel friction, hood friction, the squeeze's rolling loss, the damping of the foam, the energy stored and released by
the squeeze, gravity, and the load each ball puts back on the shaft. Several balls side by side share the shaft.

**The hood geometry.** Looking at the launcher from the side (the shaft points out of the page, the robot's front
is to the right):

```
                     90 (straight up)
                      |
         exit end  x  |              the hood's exit end is at exit_angle_deg + 90 round the shaft
  (140 for a 50 deg   |              (140 for a 50 degree shot); the ball leaves along the tangent there,
   shot)       .      |              exit_angle_deg above horizontal, forward
            .         |
  180 ----.--------- (+) ---------- 0 (robot's front)
          .   hood    shaft
           .  (ball rides between
             .  hood and wheel)
         entry end  x                the ball enters at entry_deg (default 240: behind and below the shaft)
```

- The hood is concentric with the wheel: the gap between them is the same all the way round.
- The hood pivots about the shaft. The ball's entry is fixed on the robot; turning the hood moves its **exit** end.
- So a larger `exit_angle_deg` launches steeper **and** shortens the wrap (the arc the ball is squeezed over),
  which shortens the contact time and changes exit speed and spin. The wrap is `entry_deg - (exit_angle_deg + 90)`
  degrees; with the defaults and a 50 degree exit angle it is 100 degrees.
- **Squish** is the ball's diameter minus the gap. `hood.gap` sets it. No squish (a gap at least the ball's diameter)
  means no contact: the ball is reported `no_contact`.
- **Entry lead-in.** The hood's entry end is flared out at `hood.flare_deg` (default 30 degrees, an estimate), so
  the squeeze builds up as the ball is drawn in. Between 20 and 45 degrees the exit speed changes by about 10%.
  Too steep and the wheel can't draw the ball in: it is reported `jammed`.
- **Exit.** By default (`exit_flare_deg` not set) the hood simply ends at the end of its arc, and the ball rolls off
  the edge: the squeeze falls away the way a sphere leaves an edge. Set `exit_flare_deg` to model a flared lip
  instead.

**Wheel coverage.** The ball touches the wheel over a patch about `sqrt(D x squish)` wide (D the ball's diameter):
about 3 in. for a Power Cell at 0.2 D squish. Only the part of that patch over wheel material grips. A 2 in. wheel
under a 7 in. ball loses about a third of its grip; a wide wheel, a roller, or wheels spaced across the ball don't.

**The centrifugal squeeze split.** At speed the ball must be held on its curved path, so it presses harder on the
hood and less on the wheel. The wheel's grip falls as the ball speeds up. A soft ball can lift off the wheel
altogether; the model handles that (it isn't an error).

**Flight.** From the exit point the ball flies with gravity, air drag (`1/2 x density x Cd x area x speed^2`) and
Magnus lift from its backspin (lift coefficient `lift_slope x S`, capped at `lift_max`, where S is the spin's surface
speed over the ball's speed). The spin stays constant in flight (no spin decay: a modeling choice). The robot's
velocity is added to the ball's at launch. The goal is a level ring at `goal.height`: a ball scores if its center
drops through the ring, clear of the rim.

**Not modeled:** the ball's path is a circle in the plane of its wheel (no sideways drift inside the launcher);
balls in the same lane don't touch each other; wheel wear, ball wear and temperature; the robot's own motion during
the burst (only its velocity at launch); and the motor's rotor inertia.

## The two regimes: grip and slip

This is the most important thing the model tells you.

- **Below the "knee" wheel speed, the wheel grips the ball.** The ball leaves at about half the wheel's surface
  speed, and more RPM means proportionally more exit speed. This is how most teams think of their shooter.
- **Above the knee, the wheel slips on the ball the whole way through.** Exit speed is limited by grip, so more RPM
  barely helps. What helps is more grip: more squish, wheel material across the whole ball, more wrap, a grippier
  wheel, or a stiffer ball.

The `exit_slip_pct` column tells you which regime you are in: the wheel's slip under the ball at the exit, as a
percentage of the wheel's surface speed. Near 0, the wheel grips: RPM works. Large (10% and up), the shot is
grip-limited: add grip, not RPM.

Between about 1% and 10% exit slip you are near the knee: a little more RPM still helps, but not in proportion.

The two regimes are real friction physics, but where the model puts the knee depends on numbers it estimates (see
below), so the knee's position may be partly a quirk of the model. Teams do meet a grip limit: the
[WCP GreyT shooter FAQ](https://docs.wcproducts.com/welcome/greyt/greyt-shooter-4-6/misc/faq-and-trouble-shooting)'s
advice for a shot falling short is to try more RPM, and to add compression.

**Where the knee falls depends most of all on the ball's stiffness (`stiffness_n`)**, which is why it is the first
thing to measure. For `hooded.toml` with the estimated 60 N Power Cell, the knee is near 50 rps: at 66.7 rps
(4000 rpm) the exit speed is about 7.4 m/s with 26% slip, the same as at 50 rps. With a 120 N ball the same launcher
grips to past 66.7 rps and launches at about 9.7 m/s.

**A known shortfall.** With the estimated 60 N ball, `hooded.toml` launches at about 7.4 m/s. That is below the 8 to
20 m/s sanity range the model is checked against, and likely below what 2020 teams got from similar shooters (we
have no measured figure to cite). The test suite carries this check as a known expected failure. Either the
stiffness estimate is low or the model under-predicts grip; measuring `stiffness_n` (see
[Measure these first](#measure-these-first)) decides which. Until then, read the model's exit speeds as low.

## Worked example: flywheel weight and squish

`examples/launcher/flywheel_squish.toml` is `hooded.toml` with a sweep:

```toml
[sweep]
"shaft.added_inertia" = [0.0, 0.002, 0.004]   # kg m^2 of flywheel on the wheel shaft
"hood.gap" = [0.1511, 0.1245, 0.0889]         # squish 0.15, 0.3 and 0.5 of the 7 in. ball
```

Each run spins the wheel up from rest to 66.7 rps, then feeds 5 balls, 0.2 s apart, each waiting until the wheel is
back within 5% of its target. Run it:

```bash
scrimmage launcher sweep examples/launcher/flywheel_squish.toml
```

Some of the columns it prints (all of them are explained under [Results](#results)):

| added_inertia | gap (squish) | exit_speed (m/s) | exit_slip_pct | speed_drop_pct | min_speed_pct | recovery_s |
|---:|---:|---:|---:|---:|---:|---:|
| 0     | 0.1511 (0.15 D) | 6.15 | 40.2 | 5.3  | 93.7  | 0.043 |
| 0     | 0.1245 (0.3 D)  | 8.30 | 10.4 | 17.1 | 81.2  | 0.049 |
| 0     | 0.0889 (0.5 D)  | 6.68 | 7.1  | 39.6 | 58.3  | 0.089 |
| 0.002 | 0.1511 (0.15 D) | 6.15 | 40.1 | 2.0  | 95.1  | 0.058 |
| 0.002 | 0.1245 (0.3 D)  | 8.32 | 15.9 | 5.2  | 91.4  | 0.116 |
| 0.002 | 0.0889 (0.5 D)  | 7.95 | 13.6 | 10.1 | 84.8  | 0.179 |
| 0.004 | 0.1511 (0.15 D) | 6.15 | 40.2 | 1.0  | 95.1  | 0.062 |
| 0.004 | 0.1245 (0.3 D)  | 8.32 | 16.9 | 2.9  | 92.9  | 0.165 |
| 0.004 | 0.0889 (0.5 D)  | 7.96 | 16.9 | 5.8  | 89.0  | 0.261 |

What it says:

- **Squish.** Going from 0.15 D to 0.3 D squish raises the exit speed from 6.2 to 8.3 m/s: more squeeze, more grip.
  Going on to 0.5 D lowers it again (8.0 m/s, or 6.7 m/s with no flywheel), because squeezing the foam that hard
  costs energy and slows the wheel. The best squish is in the middle. (A smaller `hood.gap` is more squish.)
- **Flywheel.** Each ball takes speed out of the wheel (`speed_drop_pct`). Adding 0.004 kg m^2 of flywheel cuts the
  drop at 0.3 D squish from 17% to 3%, but the wheel then takes longer to spin back up (`recovery_s` from 0.05 to
  0.17 s), because the same motor has more to accelerate. At 0.5 D squish with no flywheel the wheel falls to 58%
  of its target during the burst, and the exit speed suffers.
- **Regime.** `exit_slip_pct` is 10% or more in 8 of the 9 rows: this launcher is grip-limited at 66.7 rps with
  the estimated ball. The 7.1% row (no flywheel, 0.5 D squish) is the one where the wheel sags to 58% of its target,
  so its surface is slower and slips less. Before chasing RPM, measure your ball's stiffness.

The charts in the results folder show the same thing: `line-exit_speed-vs-hood.gap.png` (exit speed against gap,
one line per flywheel), `line-speed_drop_pct-vs-shaft.added_inertia.png`, `line-recovery_s-vs-shaft.added_inertia.png`
and `shaft.png` (the wheel's speed through each burst).

`examples/launcher/hood_angle.toml` sweeps `hood.exit_angle_deg` over 35, 45, 55 and 65 degrees and draws
`landing.png`, where each ball crossed the goal height (or landed on the floor) against the goal ring. The launch
angle follows the hood, and the exit speed falls a little as the wrap shortens (7.6 to 7.0 m/s). With the estimated
ball nothing scores: from 35 to 55 degrees the balls never rise to the 2.5 m goal height, and at 65 degrees four
of the five just do (peaking at 2.5 to 2.7 m) but drop back through it only 1.6 to 2.0 m out, about 2 m short of
the 4 m goal. With the estimated ball, that is the grip limit, not the hood.

## How accurate is this?

**Solid** (standard physics, checked by tests):
- the motor, battery, controller and flywheel: spin-up time, speed drop per ball and recovery, from WPILib's motor
  constants. The torque constant is WPILib's, which is about 2% off the 1/Kv a perfect motor would have; it is kept
  so the model matches WPILib's own simulations;
- the energy bookkeeping: the energy taken from the wheel equals the ball's speed and spin plus the losses;
- the flight: gravity, drag and spin lift (a no-drag flight is checked against the parabola);
- the trends: more flywheel, smaller dip and slower recovery; squish has a best value in the middle; a steeper hood
  launches steeper; more stiffness, coverage or wrap raise the grip limit.

**Uncertain until you calibrate** (estimates, so treat the numbers as trends):
- **the contact numbers**: ball stiffness, bounce (restitution) and wheel friction are estimates, and they set the
  exit speed. Ball stiffness matters most;
- **the release loss**: how much of the squeeze's stored energy the ball gets back as it rolls off the hood's edge.
  The model damps it; with no damping at all the exit speed would be a few % higher (up to about 10% for a stiff
  ball). This is the model's main remaining uncertainty;
- **the wheel's own stiffness**: worked out from its durometer as a thick block of rubber, which ignores a thin
  tread on a hard hub (real thin-tread wheels are stiffer than the model thinks);
- the entry flare angle (default 30 degrees, an estimate);
- **the spread defaults** (`[spread]`): the ball-to-ball and feed variation are estimates, not measured. So are the
  landing spreads that come from them;
- the air: the drag coefficient is a smooth sphere's (a fuzzy or foam ball is a little higher), and the spin lift
  is from studies of other sports balls.

## Measure these first

In order of how much they matter. Put your numbers in your setup file's `[ball]` and `[wheels]` sections; they
override the preset.

1. **Ball stiffness (`ball.stiffness_n`): a squeeze test.** You need a bathroom scale, two flat boards and a ruler.
   Put the ball between the boards on the scale. Press down on the top board until the ball is squeezed to 75% of
   its diameter (a 7 in. Power Cell to 5.25 in.; a 5.9 in. FUEL to 4.4 in.). Read the scale, subtract the top
   board's weight, and convert to newtons (kg x 9.81, or lb x 4.45). That is `stiffness_n`. Measure several balls:
   they vary, and foam balls get firmer as they age.
2. **Bounce (`ball.restitution`): a drop test.** Drop the ball from 0.46 m (18 in., measured to the bottom of the
   ball) onto a hard floor (concrete or a steel plate, not carpet). Film it against a ruler and read the bounce
   height h to the bottom of the ball. `restitution = sqrt(h / 0.46)`. The drop height matters: the model's foam
   bounces differently at different speeds, and it is set up for this one.
3. **Exit speed, later: a flat shot on slow-motion video.** Once the launcher is built, shoot level with a ruler or
   tape marks in view and film at 240 fps. The distance the ball moves between two frames, times the frame rate, is
   the exit speed. Compare it with the model at the same wheel speed, and adjust the least certain input
   (stiffness, then wheel friction) until they agree. Do it at two wheel speeds, one low and one high, to see where
   your knee is.

Wheel friction can be measured with a sled pull: a ball pressed against a slab of your wheel's tread, pulled
sideways with a spring scale. Friction = pull force / pressing force.

## Inputs

A setup file is TOML with these sections. All units are SI: meters, kilograms, seconds, newtons. Angles are in
degrees and their names end in `_deg`. Wheel speed is in rotations per second of the wheel shaft (`target_rps`), as
Phoenix measures velocity. Keys marked **required** have no default. `[ball]`, `[wheels]` and `[air]` can start from
a preset (`preset = "name"`); keys you set yourself override it.

### `[ball]`

| key | unit | default | meaning |
|---|---|---|---|
| `preset` | | | `2020-power-cell`, `2022-cargo`, `2026-fuel`, `2016-boulder`, `2017-fuel`, `2019-cargo`, `2025-algae`, `2014-ball`, `2010-soccer-ball` or `2012-basketball` |
| `diameter` | m | required | |
| `mass` | kg | required | |
| `stiffness_n` | N | required | force to squeeze the ball by 25% of its diameter between flat plates. The most influential input |
| `stiffness_exponent` | | 1.5 | force grows as squeeze^exponent; 1.5 is the Hertz contact law |
| `restitution` | | required | bounce coefficient from a 0.46 m drop on a hard floor |
| `inertia_factor` | | 0.4 | moment of inertia / (mass x radius^2): 0.4 a solid foam ball, 0.667 a thin inflated shell |

### `[wheels]`

| key | unit | default | meaning |
|---|---|---|---|
| `preset` | | | `30A`, `35A`, `40A`, `45A` or `60A` (sets durometer and friction) |
| `diameter` | m | required | |
| `width` | m | required | width of each wheel (or of the roller) |
| `durometer` | Shore A | required | tread hardness |
| `friction` | | required | friction coefficient of the tread against the ball |
| `positions` | m | `[0.0]` | each wheel's center along the shaft |
| `roller` | | `false` | `true`: one continuous roller `width` long, centered at 0 (`positions` is ignored) |

### `[shaft]`

| key | unit | default | meaning |
|---|---|---|---|
| `wheel_inertia` | kg m^2 | required | the wheels, hubs and shaft, about the shaft |
| `added_inertia` | kg m^2 | 0 | flywheel added to the shaft |
| `motor` | | `KrakenX60` | one of `Bag`, `CIM`, `Falcon500`, `Falcon500FOC`, `KrakenX60`, `KrakenX60FOC`, `MiniCIM`, `NEO`, `NEO550`, `NeoVortex`, `Vex775Pro` |
| `motor_count` | | 1 | motors driving the shaft |
| `gearing` | | 1.0 | motor rotations per wheel rotation |

### `[control]`

| key | unit | default | meaning |
|---|---|---|---|
| `target_rps` | rot/s | required | wheel-shaft speed target (4000 rpm = 66.7) |
| `kS` | V | 0 | static feedforward |
| `kV` | V per rot/s | from the motor | velocity feedforward; left out, it is the motor's no-load value, `2 pi x gearing / Kv` |
| `kP` | V per rot/s | required | proportional gain on the speed error |
| `stator_limit` | A | none | stator current limit, per motor, as set on each Talon |
| `supply_limit` | A | none | supply current limit, per motor, as set on each Talon |

### `[battery]`

| key | unit | default | meaning |
|---|---|---|---|
| `nominal_voltage` | V | 12.5 | open-circuit voltage |
| `resistance` | ohm | 0.02 | internal resistance (the battery sags as current is drawn) |

### `[hood]`

| key | unit | default | meaning |
|---|---|---|---|
| `gap` | m | required | wheel surface to hood; squish = ball diameter - gap |
| `entry_deg` | deg | 240 | where the ball enters, round the shaft (0 forward, 90 up) |
| `exit_angle_deg` | deg | required | launch angle above horizontal; the hood's exit end is at `exit_angle_deg + 90` round the shaft |
| `friction` | | required | friction coefficient of the hood against the ball |
| `flare_deg` | deg | 30 | the entry lead-in's angle (an estimate); above 0 and below 90 |
| `exit_flare_deg` | deg | none | none: the hood ends at its arc's end. A number: a flared lip at that angle |

The wrap, `entry_deg - (exit_angle_deg + 90)`, must be positive.

### `[launcher]`

| key | unit | default | meaning |
|---|---|---|---|
| `height` | m | required | shaft height above the floor |
| `width` | m | required | inside width between the side plates. Every `feed.lanes` value, plus and minus half the ball's diameter, must lie within plus or minus half of it |

### `[robot]`

| key | unit | default | meaning |
|---|---|---|---|
| `velocity` | m/s | `[0.0, 0.0]` | robot velocity at launch, `[forward, left]`; added to each ball's |

### `[air]`

| key | unit | default | meaning |
|---|---|---|---|
| `preset` | | `default` | applied even if you leave `[air]` out |
| `density` | kg/m^3 | 1.2 | |
| `drag_coefficient` | | 0.47 | |
| `lift_slope` | | 1.0 | spin lift coefficient per unit of spin ratio S |
| `lift_max` | | 0.3 | the largest spin lift coefficient |

### `[goal]`

| key | unit | default | meaning |
|---|---|---|---|
| `distance` | m | required | straight ahead, from below the shaft to the goal's center |
| `height` | m | required | height of the goal ring |
| `diameter` | m | required | inside diameter of the ring |

### `[feed]`

| key | unit | default | meaning |
|---|---|---|---|
| `count` | | 10 | balls in the burst |
| `rate` | 1/s | 5.0 | how many times a second the hopper offers balls: one per lane each time |
| `rule` | | `wait_for_speed` | `wait_for_speed`: feed only when the wheel is within `tolerance` of its target. `schedule`: feed at the offered times regardless |
| `tolerance` | | 0.05 | "at speed" means within this fraction of the target |
| `lanes` | m | `[0.0]` | ball positions along the shaft; several lanes feed side by side. Each must fit inside `launcher.width` (see above) |
| `entry_speed` | m/s | 1.0 | the ball's speed as it reaches the hood |
| `timeout` | s | 10.0 | balls not fed this long after the first is offered are `unfed` |

Timing: the run starts with the wheel at rest. Feeding starts when the wheel first reaches speed. A ball can't enter
its lane until the one before it has moved a ball's diameter (diameter / entry speed).

### `[spread]`

Random ball-to-ball variation, as standard deviations. **The defaults are estimates.**

| key | unit | default | meaning |
|---|---|---|---|
| `seed` | | 1 | the same seed gives exactly the same results |
| `diameter` | fraction | 0.01 | |
| `mass` | fraction | 0.02 | |
| `stiffness` | fraction | 0.05 | |
| `feed_time` | s | 0.01 | shared by balls fed side by side |
| `lane` | m | 0.005 | |
| `entry_speed` | fraction | 0.1 | |
| `yaw_deg` | deg | 0.5 | sideways launch direction |
| `shooter_speed` | fraction | 0.0 | each ball's wheel-speed target offset. It takes effect when that ball enters, so the wheel has little time to reach it during that ball's own pass: it mostly sets the speed the next ball meets |

### `[results]` and `dt`

| key | unit | default | meaning |
|---|---|---|---|
| `results.recovery_tolerance` | fraction | 0.02 | "recovered" means back within this fraction of the target |
| `dt` (top level) | s | 0.0001 | integration step while balls are in contact |

### `[sweep]` and `[[chart]]`

`[sweep]` lists dotted keys and the values to try: every combination is run, the first key changing slowest.
Every combination, and every `[[chart]]`, is checked before the first run: a bad one stops the command with an
error naming it (for example `hood.exit_angle_deg=160: [hood] wrap must be positive ...`), and nothing is written.

```toml
[sweep]
"hood.gap" = [0.15, 0.13]
"control.target_rps" = [50.0, 66.7, 80.0]
```

Each `[[chart]]` is one PNG. With none, you get a line chart of `exit_speed` against the first swept key (one line
per value of the second), `shaft.png` and `landing.png`. `scrimmage launcher run` sweeps nothing, so it skips
`line` charts.

```toml
[[chart]]
kind = "line"                   # x: a swept key; y: a results column; series (optional): another swept key
x = "control.target_rps"
y = "exit_speed"
series = "hood.gap"

[[chart]]
kind = "shaft"                  # wheel speed against time, every run

[[chart]]
kind = "landing"                # where each ball crossed the goal height (or hit the floor), with the goal ring
```

## Presets and their sources

The presets live in `scrimmage/launcher/presets.toml`; every value there has a `# source:` or `# estimate:`
comment saying where it came from.
The same file also holds the test range's rigid piece presets (`[piece.*]`) and their surface materials
(`[surface.*]`); see docs/test-range.md.

| ball preset | diameter | mass | stiffness_n | restitution | inertia_factor |
|---|---|---|---|---|---|
| `2020-power-cell` | 0.1778 m (7 in., game manual) | 0.1417 kg (AndyMark replica) | 60 N **estimate** | 0.6 **estimate** | 0.4 estimate |
| `2022-cargo` | 0.2413 m (9.5 in., game manual) | 0.2693 kg (game manual) | 500 N **estimate** (from its 3.5 psi) | 0.87 (from the manual's carpet bounce) | 0.667 estimate |
| `2026-fuel` | 0.1501 m (AndyMark) | 0.2164 kg (AndyMark/FIRST whitepaper) | 80 N **estimate** | 0.6 **estimate** | 0.4 estimate |
| `2016-boulder` | 0.254 m (10 in., game manual) | 0.285 kg **estimate** (from Chief Delphi weighings) | 120 N **estimate** | 0.5 **estimate** | 0.4 estimate |
| `2017-fuel` | 0.127 m (5 in. nominal, game manual) | 0.0737 kg (2.6 oz, game manual) | 60 N **estimate** | 0.5 **estimate** | 0.4 estimate |
| `2019-cargo` | 0.3302 m (13 in., game manual) | 0.28 kg **estimate** | 250 N **estimate** | 0.7 **estimate** | 0.667 estimate |
| `2025-algae` | 0.4064 m (16 in., Wikipedia; manual figure not confirmed) | 0.45 kg **estimate** | 200 N **estimate** | 0.7 **estimate** | 0.667 estimate |
| `2014-ball` | 0.635 m (about 25 in., Chief Delphi) | 1.3 kg **estimate** | 300 N **estimate** | 0.8 **estimate** | 0.667 estimate |
| `2010-soccer-ball` | 0.2223 m (27.5 in. circumference, game manual; also the 2002 piece) | 0.425 kg (15 oz, game manual) | 300 N **estimate** | 0.8 **estimate** | 0.667 estimate |
| `2012-basketball` | 0.2021 m (25 in. circumference, 2012 manual section 2.2.11) | 0.3175 kg (11.2 oz, 2012 manual section 2.2.11) | 100 N **estimate** | 0.55 **estimate** | 0.4 estimate |

No published squeeze force was found for any of these balls. The FUEL whitepaper charts a "spring rate" without
units; if it were in lb/in., FUEL would be nearer 130 N (200 N once aged) than 80 N. Measure your own.

| wheel preset | durometer | friction |
|---|---|---|
| `30A` | 30 | 1.0 estimate |
| `35A` | 35 | 0.95 estimate |
| `40A` | 40 | 0.9 estimate |
| `45A` | 45 | 0.85 estimate |
| `60A` | 60 | 0.7 estimate |

Friction against FRC balls isn't published; the presets follow the trend that softer rubber grips more.

`air.default`: density 1.2 kg/m^3 (dry air at 20 C), drag coefficient 0.47 (smooth sphere, NASA Glenn), lift slope
1.0 and lift max 0.3 (estimates from sports-ball wind-tunnel studies, Mehta 1985).

## Results

The table's first line is always the label, the charts' footer too: UNCALIBRATED with what is still an estimate
(the ball's stiffness, its bounce, the wheel's friction), and what was measured with its file, who measured it, when
and from how many trials. With all three measured, and good enough, it reads "Calibrated from measurements: ...". A
swept number is an estimate. Then one row per run (one per sweep combination), with the swept values first.
`results.csv` has the same columns. Averages are over the launched balls.

| column | meaning |
|---|---|
| `exit_speed` | average exit speed (m/s) |
| `exit_speed_sd` | ball-to-ball standard deviation of the exit speed (m/s) |
| `spin_rpm` | average backspin (rpm) |
| `exit_slip_pct` | the wheel's slip under the ball at the exit, % of its surface speed. Near 0: grip, RPM works. Large: grip-limited, add grip |
| `launch_deg` | launch angle (deg) |
| `speed_drop_pct` | how much each ball slows the wheel, over its own pass, % of the target |
| `min_speed_pct` | the wheel's lowest speed in the burst, % of the target |
| `recovery_s` | time from the bottom of a dip back to within `recovery_tolerance` of the target, for dips that recover before the next ball (and the last ball) |
| `burst_s` | first ball in to last ball out (s) |
| `spread_downrange_m`, `spread_lateral_m` | standard deviation of where the balls crossed the goal height on the way down; if any launched ball never reaches that height, of where every ball hit the floor instead |
| `scored` | balls through the goal ring |
| `fired` | balls fed into the launcher |
| `launched` | balls that came out |
| `jammed` | balls that stalled in the launcher (still there 0.5 s after entering) or couldn't be drawn in |
| `unfed` | balls never fed before `feed.timeout` |

A ball can also be `no_contact` (the gap is too big to squeeze it) or `no_wheel` (no wheel under it). These are
results, not errors.

## Feeding balls from your own hopper model (`FedBall`)

`FedBall` is the interface between a hopper and the launcher. From Python you can pass your own list of balls instead
of the `[feed]` section's:

```python
from scrimmage.launcher.inputs import load_setup
from scrimmage.launcher.model import FedBall
from scrimmage.launcher.run import run_setup

setup, _, _ = load_setup("examples/launcher/hooded.toml")
feed = [
    FedBall(available_at=0.0, lane=0.0, entry_speed=1.0, ball=setup.ball),
    FedBall(available_at=0.15, lane=0.0, entry_speed=1.2, ball=setup.ball, yaw_deg=0.3),
]
result = run_setup(setup, feed)
print(result.metrics["exit_speed"], [shot.status for shot, _ in result.shots])
```

- `available_at` (s) is when the hopper offers the ball, counted from the moment the wheel first reaches speed (the
  run's time 0 is the wheel at rest).
- `lane` (m) is where across the shaft the ball runs; `entry_speed` (m/s) is its speed at the hood; `ball` is a
  `Ball` (vary its numbers per ball if you like); `yaw_deg` turns its launch direction sideways.
- `feed.rule`, `feed.tolerance` and `feed.timeout` still apply, and so does the one-ball-at-a-time lane rule.

`result.shots` is a list of `(ExitedBall, Landing or None)`; `result.trace` is the wheel's speed against time.

## In the world: the test range

On the [test range](test-range.md) the same launcher model runs inside the robot sim, with balls that are bodies in
the world. What changes:

- **External bus.** In a standalone run the shaft owns a `[battery]`. In the world the shaft has none
  (`Launcher(setup, external_bus=True)`): the world sets the bus voltage for each step, and the shaft's supply current
  is charged to the robot's one shared battery, so the shooter sags the same bus as the drive. The world solves that
  bus voltage within each step from all the motors' currents (each at its start-of-step speed), so a heavy combined
  draw doesn't make the voltage flip from step to step. A setup file's `[battery]` isn't used there; the config's `[battery]` is the robot's.
- **The shooter's voltage** is your robot program's motor command (with the motor's neutral mode at neutral: brake
  shorts the winding, coast opens it), not a `[control]` loop. A setup file's `[control]`, `[goal]`, `[robot]`, `[feed]`, `[spread]`, `[sweep]` and `[[chart]]` sections are
  ignored there.
- **Run-time hood.** An articulated hood is a joint driven by its own motor, and the model takes its exit angle from
  that joint every step (`Launcher.set_hood_angle`): the hood can move while balls are in the launcher, and a ball
  past the new exit end leaves at the new angle. The joint reads 0 at the low stop.
- **`FedBall` is the hopper interface.** The range's ball path (intake, hopper, feeder stages) hands the launcher a
  `FedBall` when the ball reaches the end of the last stage: its lane, the stage's surface speed as `entry_speed`,
  and the range's ball. It replaces the `[feed]` section.
- **The goal is the range's.** The ball lands where the physics puts it, in a goal with a real opening and a funnel,
  not on a `[goal]` ring. Balls in the world also touch the robot, the field and each other.
- **The launcher's height** is where the part is mounted; a setup file's `[launcher] height` is ignored.

### `scrimmage launcher aim`

An aiming table (wheel speed and hood angle for each distance to a goal) from a parts robot's launcher model:

```sh
scrimmage launcher aim CONFIG --goal hub_blue --distances 1.5:6.0:0.25 --csv aim.csv
```

Unreachable distances are marked in the output and warned about; each row follows the previous row's solution so the
table interpolates smoothly. Options, the Java output and what it assumes are in [the test range
doc](test-range.md#aiming-tables-scrimmage-launcher-aim).
