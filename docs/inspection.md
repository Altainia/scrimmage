# Robot inspection

`scrimmage inspect` compares the robot you modeled with a season's weight and size rules, the way an inspector does
at an event: weight, frame perimeter, starting height, and how far the robot reaches past its frame. Each line names
the rule, the manual it came from, and how far you are within or over.

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
In every season the robot must start inside its frame perimeter (bumpers and minor protrusions aside), so the start
reach limit is 0, with a 1/4 in allowance for the minor protrusions each season's perimeter rule allows.

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

The bumpers are checked too: one set against the per-set limit (2013 to 2024), or the weight with bumpers against the
135 lb limit (2025, 2026).

## What each measure means in a model

Everything is measured at the model's **start pose**, as the world builds (joints at their start positions, on the
`[range]` start when it has one). That is what the robot must satisfy before the match. A model whose arm starts
raised is out of its starting configuration, and the report says so by what it measures.

- **Frame perimeter**: 2 × (length + width) of the frame, the same outline the [climb runs](climbing.md) use: the
  largest box on the robot's body, which for the stock chassis is its frame plate (so bumpers are out, as R104
  measures). A model without a box uses the outline of all the chassis's geoms, and the line says "no frame plate".
  A frame that isn't rectangular reads as its bounding rectangle, an overestimate, never an underestimate.
- **Height**: the top of the robot's highest geom above the carpet. **Every geom counts, colliding or not**: an
  elevator's stages are built `collide = false`, and the rule counts them. Bumpers count.
- **Reach**: the largest horizontal distance from the frame outline to a part of a geom that isn't on the chassis's
  body. Round parts and meshes are measured by their own shapes, not their boxes.
- **Start reach**: the same number, which must be within the frame (0, with a 1/4 in allowance). The line names the
  part that sets it, for example `(bl.base)`.
- **Diameter** (2013): the smallest circle holding the robot seen from above, bumpers included.

Most stock example robots read 2 to 7 cm of start reach, so `scrimmage inspect` reports the
start reach over on them (a swerve module's base plate, a wheel or an intake sits past the chassis's frame plate).
That is the model, not a verdict: the frame here is the chassis's frame plate, and a module, wheel or bumper-zone part
the model places past the plate reads as reaching. In the real rule the frame perimeter wraps around such parts, so
**check your real frame**, and move the plate or the part in the model if you want the report to agree.

## What isn't checked

Each season's report lists, under "Not checked", the rules a single number can't carry: reach over one side at a
time, zone limits, the plane from the pyramid, and the zone exceptions to the height limits. A limit with an exception
(2022's hangar zone, say) that the robot is over still reports, quoting the exception and "not checked": decide
for yourself whether the exception applies. Also not checked: bumper construction, electrical rules, part legality
and cost.
