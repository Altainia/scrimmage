# Robot inspection

`scrimmage inspect` compares the robot you modeled with a season's weight and size rules, the way an inspector does
at an event: weight, frame perimeter, starting height, and how far the robot reaches past its frame. Each line names
the rule and how far you are within or over; the report ends with the manual the numbers came from.

```
scrimmage inspect configs/kitbot.toml --season 2024
```

It exits 0 when every checked limit is met and 1 when one is over, so you can run it in CI. A config that won't build,
a bad `[inspection]` setting or an unknown `--season` exits 2.

**It is not a certification.** The weight is only as good as the masses the parts were given, the size is the
model's, and some rules can't be written as one number (see [What isn't checked](#what-isnt-checked)). Use it to
catch a robot that is 4 in too long before you cut metal, not to promise it passes.

## The limits

Read in each season's final Game Manual (the manual and its link print at the end of every report). Limits given in
feet and inches are converted to inches.

| Season | Weight (no bumpers, battery) | Bumpers | Perimeter | Start height | Match height | Reach |
|---|---|---|---|---|---|---|
| 2013 | 120 lb R05 | 20 lb a set R23 | 112 in R03-A | 84 in R03-C | 60 in G22, unless on the pyramid or in the auto zone (84 in) | a 54 in diameter cylinder (R03-B, G23) |
| 2016 | 120 lb R5 | 20 lb a set R20 | 120 in R3-A | 54 in R3-C | 54 in G17, unless the last 20 s of teleop inside the opponent's courtyard | 15 in G18 |
| 2020 | 125 lb R5 | 15 lb a set R23 | 120 in R3 | 45 in R3 | 45 in G17, unless at the rendezvous point in the endgame | 12 in G18 |
| 2022 | 125 lb R103 | 15 lb a set R407 | 120 in R104 | 52 in R104 | 52 in G106, unless bumpers in the hangar zone (66 in) | 16 in G107 |
| 2023 | 125 lb R103 | 15 lb a set R407 | 120 in R104 | 54 in R104 | 78 in G106 | 48 in G107 |
| 2024 | 125.5 lb R103 (Team Update 21) | 15 lb a set R407 | 120 in R104 | 48 in R104 | 48 in G413-A | 12 in G413-B |
| 2025 | 115 lb R103; 135 lb with bumpers R408 | none | 120 in R104 | 42 in R104 | none | 18 in G415 |
| 2026 | 115 lb R103; 135 lb with bumpers R408 | none | 110 in R104 | 30 in R104 | 30 in R107 | 12 in R105 |

2025 has no match height limit in the manual (it points to limits for parts of the field but gives no number), so
the report says "no limit in 2025". The table lives in `scrimmage/inspection/seasons.toml`, one quoted rule per entry.
In every season the robot must start inside its frame perimeter (minor protrusions aside, and bumpers from 2016):
the no-overhang rule, 2013 R04, 2016 R4, 2020 R2, 2022 to 2026 R102. So the start reach limit is 0, with a 1/4 in
allowance for the minor protrusions each season's perimeter rule allows, and the start reach line cites that rule.

## Choosing a season

The season is, in order: `--season YEAR`, the config's `[inspection] season`, the year a built-in `[range]` file's
name starts with (`2024` or `2013-pyramid`), else the newest season. The first line of the report says which and why.

## The [inspection] section

```toml
[inspection]
season = 2026          # optional: the season's rules to use
battery_mass = 5.8     # kg: your battery, as you weighed it
bumper_mass = 6.8      # kg: one set of bumpers, as you weighed it
```

Having the section (even an empty one) also turns on the inspection notes in a scenario's report and the warnings in
the assembler; without it those stay as they were. `scrimmage inspect` works with or without it.

### Weight

The rules weigh the robot without its bumpers and its battery, but a model includes them: the stock chassis's `mass`
is everything on the chassis that isn't another part (frame, bumpers, battery, electronics). So the weight line is the
model's whole mass minus a battery and a set of bumpers, and shows the sum:

```
weight without bumpers and battery: 36.85 kg (49.5 kg modeled − 5.847 kg battery, estimate − 6.804 kg bumpers, estimate)
```

Unset, the battery is 12.89 lb (AndyMark's listing for the MK ES17-12 most teams use; the rules allow 11.0 to 14.5 lb)
and the bumpers 15 lb (an uncalibrated estimate with no source). Both say "estimate". **Weigh yours and set
`battery_mass` and `bumper_mass`.** If your model leaves the battery or the bumpers out (a CAD model, say), **set them
to 0**, or the report subtracts weight your model never had. An over-limit weight that rests on an estimate still
exits 1, and its line says so; the report never calls an estimated weight legal.

The bumpers are checked too, when you set `bumper_mass`: one set against the per-set limit (2013 to 2024). With the
15 lb estimate the bumper line says it isn't checked (in 2020 to 2024 the estimate equals the limit, so comparing it
would say nothing). In 2025 and 2026 the weight with bumpers is checked against the 135 lb limit, with or without
`bumper_mass`. A line within its limit that rests on an estimate ends "with estimates": it isn't a pass.

## What each measure means in a model

Everything is measured at the model's **start pose**, as the world builds (joints at their start positions, on the
`[range]` start when it has one). That is what the robot must satisfy before the match. A model whose arm starts
raised is out of its starting configuration, and the report says so by what it measures.

- **Frame perimeter**: 2 × (length + width) of the frame, the same outline the [climb runs](climbing.md) use: the
  largest box on the robot's body, which for the stock chassis is its frame plate (so bumpers are out, as R104
  measures). A model without a box uses the outline of all the chassis's geoms, and the line says "no frame plate".
  A frame that isn't rectangular reads as its bounding rectangle, an overestimate, never an underestimate.
- **Height**: the robot's height as if on a flat floor, as the rules measure it: from its lowest point to its highest
  along the chassis's up. Standing level on the carpet that is its top above the carpet; driven onto a bump, tilted
  or hanging from a chain, it stays the robot's own height, so a field element doesn't make it "over". **Every geom
  counts, colliding or not**: an elevator's stages are built `collide = false`, and the rule counts them. Bumpers
  count.
- **Reach**: the largest horizontal distance from the frame outline to a part of a geom that isn't on the chassis's
  body. Round parts and meshes are measured by their own shapes, not their boxes.
- **Start reach**: the same number, which must be within the frame (0, with a 1/4 in allowance). The line names the
  part that sets it, for example `(bl.base)`.
- **Diameter** (2013): the smallest circle holding the robot seen from above, bumpers included.

On the stock example configs the start reach on 2026 reads from 2 mm (swerve) and 6 mm (kitbot), both within the
allowance, through 14 mm (climber_robot), 44 mm (maxswerve), 59 mm (disc_robot) and 75 mm (fastmode_range), to
about 300 mm (fastmode, elevator_arm). The small readings come from parts the model places past the chassis's frame
plate: a swerve module's base plate, a wheel, an intake. That is the model, not a verdict: in the real rule the frame
perimeter wraps around such parts, so **check your real frame**, and move the plate or the part in the model if you
want the report to agree. The large readings are different: a robot whose arm starts extended really is out of its
starting configuration, so start it tucked.

## Scenarios

With an `[inspection]` section, a scenario's report gets inspection notes. They never fail a run: a breach is a note,
as the [climb runs](climbing.md)' G106 and G107 breaches are.

- **At the start**, once: every limit the robot is over at its start pose (weight, perimeter, starting height, start
  reach), the same words as the warnings below.
- **During the run**: the robot's height, its reach beyond the frame and (2013) the smallest circle holding it, against
  the season's match limits, every 20 ms of simulated time. Each breach is one note, with when it first went over, how
  far over at most and when, and how long it was over in all:

```
2026 R105: reach beyond the frame over the 12 in limit from t=0.52 s, by up to 1.78 in (arm) at 0.72 s (1.00 s over in all)
```

A season with no limit for a measure (2025's height) isn't watched for it. A limit with an exception is still noted,
quoting it with "not checked" (a 2022 robot climbing in the hangar zone gets a height note it may dismiss). Sampling
every 20 ms means a flick past the line shorter than that can be missed; the rules' limits are judged over seconds.
A world reset (restarting the robot program, putting the robot back) starts the watch over: a later breach is a new
note.

## The assembler

With an `[inspection]` section, the Robot tab's warnings list each limit the robot is over at its start pose, in the
Units setting:

```
2026 R104: frame perimeter 110.24 in, over the 110 in limit by 0.24 in
```

Within-limit lines aren't warnings. The robot state (`/api/robot/state`) carries `inspection`: the season, why it was
chosen, and each line's key, measured value, limit and how far over (in meters and kilograms), whether it rests on an
estimate, and its words; `null` without the section.

## MCP

The `inspect_robot` tool inspects the running session's robot (its config, built fresh at its start pose, not the
live world), with or without an `[inspection]` section, as `scrimmage inspect` does. `season` picks the year, like
`--season`. It returns the season and why, each line in SI (key, rule, measured, limit, over, estimate, and its words
in metric), the Not checked list and the advice; and, with an `[inspection]` section, the live
session's breach notes since its last reset.

## What isn't checked

Each season's report lists, under "Not checked", the rules a single number can't carry: reach over one side at a
time, zone limits, the plane from the pyramid, and the zone exceptions to the height limits. A limit with an exception
(2022's hangar zone, say) that the robot is over still reports, quoting the exception and "not checked": decide
for yourself whether the exception applies. Also not checked: bumper construction, electrical rules, part legality
and cost.
