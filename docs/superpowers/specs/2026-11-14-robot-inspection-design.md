# Robot inspection: a robot against a season's size and weight rules

Status: design. The owner asked "does this sim check whether a full robot is within weight and dimension tolerances
for any given year?" (it didn't), then said "Design and implement" (2026-11-14). Calls are mine (decide-and-document);
each **Call** says what it costs if it's wrong.

## Why

A team builds its robot in scrimmage before cutting metal. Today nothing tells them the robot they modeled is
115 lb when the limit is 115 lb without bumpers and battery, that the frame perimeter is 122 in when the limit is
120, or that the elevator reaches 5 ft when the season allows 4. These limits are what robot inspection checks at an
event, and missing one costs a rebuild. scrimmage already has every number needed (the model's masses, its geoms'
boxes, the robot's pose every step); it only doesn't compare them with a season's rules.

## Goals

- For a chosen season, a robot's **weight** (without bumpers and battery), **frame perimeter**, **starting height**
  and **starting extension** are compared with that season's limits, each with its rule number and the manual it
  came from.
- During a scenario, the robot's **height** and **extension beyond its frame perimeter** are watched against the
  season's match-time limits; a breach is a note in the report (when, by how much at most, for how long), like the
  climb runs' G106/G107 notes.
- In the assembler, a robot over a limit shows a warning while you build it.
- Every limit is sourced (rule number, manual, URL). A rule scrimmage can't check is listed as "not checked", never
  silently passed.

## Non-goals

- Certifying a robot. The weight is only as good as the masses the parts were given; the report says so.
- Bumper construction rules (fabric, backing, corners), electrical rules, part legality, cost.
- Directional or zone-dependent extension rules beyond what a single limit expresses (e.g. "one side at a time",
  "only in the opponent's half"): these are listed as not checked (Call I5).
- Seasons scrimmage has no field for. The table covers the seasons scrimmage has a field or element for
  (2013, 2016, 2020, 2022, 2023, 2024, 2025, 2026); adding one is a table entry.

## Anti-goals

- A green "legal" verdict on a robot whose weight is a guess.
- A limit without a source.
- Failing a scenario run because of an inspection breach (it's a note, not a failed check; Call I6).

## The design

### Call I1: a season rules table, data not code

`scrimmage/inspection/seasons.toml`, one table per season, in the manual's own units (inches, pounds) so a reviewer
can check it against the manual by eye; converted to meters and kilograms on load. Each limit carries its rule
number; a limit with a zone or time exception carries the exception in words (`unless`), which the note quotes
("allowed up to 66 in. with bumpers in the hangar zone: not checked"). A comment above each entry quotes the rule.

| Season | Weight (no bumpers, battery) | Bumpers | Perimeter | Start height | Match height | Reach |
|---|---|---|---|---|---|---|
| 2013 | 120 lb R05 | 20 lb a set R23 | 112 in. R03-A | 84 in. R03-C | 60 in. G22, unless on the pyramid or in the auto zone (84 in.) | a 54 in. diameter cylinder (R03-B, G23) |
| 2016 | 120 lb R5 | 20 lb a set R20 | 120 in. R3-A | 54 in. R3-C | 54 in. G17, unless the last 20 s of teleop inside the opponent's courtyard | 15 in. G18 |
| 2020 | 125 lb R5 | 15 lb a set R23 | 120 in. R3 | 45 in. R3 | 45 in. G17, unless at the rendezvous point in the endgame | 12 in. G18 |
| 2022 | 125 lb R103 | 15 lb a set R407 | 120 in. R104 | 52 in. R104 | 52 in. G106, unless bumpers in the hangar zone (66 in.) | 16 in. G107 |
| 2023 | 125 lb R103 | 15 lb a set R407 | 120 in. R104 | 54 in. R104 | 78 in. G106 | 48 in. G107 |
| 2024 | 125.5 lb R103 (Team Update 21) | 15 lb a set R407 | 120 in. R104 | 48 in. R104 | 48 in. G413-A | 12 in. G413-B |
| 2025 | 115 lb R103; 135 lb with bumpers R408 | none | 120 in. R104 | 42 in. R104 | none | 18 in. G415 |
| 2026 | 115 lb R103; 135 lb with bumpers R408 | none | 110 in. R104 | 30 in. R104 | 30 in. R107 | 12 in. R105 |

Sources (docs/superpowers/specs/2026-11-14-robot-inspection-sources.md): each season's final Game Manual PDF from firstfrc.blob.core.windows.net (URLs in the table file), read in
full for these rules on 2026-11-14, with the season's combined Team Updates searched for changes (2024's TU21 raised
the weight from 125 to 125.5 lb; no other change to these numbers). Inch values for limits given in feet and inches
(2022 to 2025) are conversions. The 2025 manual has no match-time height limit (searched; it refers to area-specific
restrictions but gives no number).

In every season the starting configuration must lie within the frame perimeter (bumpers and minor protrusions
aside): 2013 R03 by its cylinder, the others by an explicit rule (2016 R4, 2020 R2, 2022 to 2026 R104/R101); the start
reach limit is 0.

Not checked (listed in each season's entry, quoted short): 2023 G109 and 2026 R106 (reach over one side at a time),
2023 G108 (no reach in the opponent's zones), 2013 G23-1 (the 54 in. plane from the pyramid), 2022 G108/G109,
2026 R108, 2024 R106, and the zone exceptions above.

*Cost if wrong:* a misread number misleads a team. Each entry quotes its rule; a test pins every value; the file
names the manual version it was read in.

### Call I2: which season

`[inspection] season = 2026` in the robot config. Without one: the season of the config's built-in `[range]` when
it names one (`range = "2024"` → 2024; `2013-pyramid` → 2013), else the newest season in the table. The CLI's
`--season` wins over both. The report always says which season it used and why ("2026: the newest season; set
[inspection] season to choose"). *Cost:* a 2026 robot on the 2013 pyramid range is inspected against 2013 rules by
default; the line says why and how to change it.

### Call I3: the weight, and what it leaves out

Rules weigh the robot without bumpers and the battery. scrimmage's models include both: the stock chassis's `mass`
is "everything on the chassis that isn't another part (frame, battery, electronics)", and its bumpers are geoms on
that body. So:

- weight = the robot's whole mass (its subtree) − battery − bumpers.
- `[inspection] battery_mass` and `bumper_mass` (kg) are what the team weighed. Unset, battery is 12.89 lb (AndyMark's
  listing for am-0844, the MK ES17-12 most teams use; the rules allow 11.0 to 14.5 lb, R601) and bumpers 15 lb
  (an UNCALIBRATED estimate of a bumper set; no source), both labeled "estimate".
- The bumpers are checked too: against the per-set limit (2013 to 2024), or the weight with bumpers against the
  135 lb limit (2025, 2026).
- The weight line always shows the sum: "112.4 lb = 141.0 lb modeled − 13.0 lb battery (estimate) − 15.6 lb
  bumpers (estimate)". With either an estimate, an over-limit weight is a warning, never "legal".
- *Cost if wrong:* a robot whose chassis mass doesn't include the battery reads light. The line names what was
  subtracted, and docs/inspection.md says to set battery_mass = 0 when your model leaves it out.

### Call I4: what "frame perimeter", "height" and "extension" are in a model

- **Frame perimeter**: the outline of the frame, the same one the climb runs use (docs/climbing.md): the largest box
  geom on the robot's body (the stock chassis's frame plate), or, without one, the outline of all its geoms. Its
  perimeter is 2 × (length + width), so a non-rectangular frame reads as its bounding rectangle (an overestimate,
  never an underestimate; said in the docs).
- **Height**: the top of the robot's geoms' bounding boxes above the carpet, **every geom, colliding or not** (an
  elevator's stages are built collide = false, and the rule counts them). Visual-only markers that are part of the
  chassis's look (the heading marker) sit inside the frame and below the top, so counting them is harmless.
- **Cylinder (2013)**: the diameter of the smallest circle holding every geom's box corners, seen from above
  (Welzl's algorithm), bumpers included as the rule says.
- **Extension**: the largest horizontal distance from the frame outline to any geom's box among the geoms not on the
  chassis body (as the climb runs measure it, but every geom, not only colliding ones), in the chassis's own x-y
  plane.
- **Starting configuration**: the robot as the world starts it (joint positions at their start, on its `[range]`
  start when it has one). Its reach must be 0 (within its frame perimeter), with a 1/4 in. allowance for the minor
  protrusions every season's perimeter rule allows.
- *Cost:* bounding boxes overestimate a round or slanted part's reach slightly; measured at most a few cm on stock
  parts. A team with a borderline number checks it in CAD.

### Call I5: rules scrimmage can't express

Each season's `not_checked` lists the size rules a single number can't carry (one-side-at-a-time extension, zone
limits, a cylinder rule), quoted short. The report lists them under "Not checked" so a team reads them.

### Call I6: breaches are notes and warnings, never failures

A scenario run with a breach still passes its checks; the breach is a note (the climb runs do the same with G106 and
G107). `scrimmage inspect` exits 1 when a checked limit is over (so a team can put it in CI), 0 otherwise; an
estimate-only over is still 1 with the line saying it's an estimate.

### Surfaces

- **CLI**: `scrimmage inspect <robot config> [--season YEAR]`: one line per limit (measured, limit, rule, within or
  over by how much), then Not checked, then where the season's numbers came from.
- **Scenario report** (robot configs with `[inspection]`; answer 5): a scenario's robot is watched during the run for height and extension (every 20 ms of
  simulated time, not every physics step: Call I7); breaches are notes. The start check runs once at the start and
  its overs are notes too.
- **Assembler** (robot configs with `[inspection]`; answer 5): the robot state gains `inspection` (the season, each line); over-limit lines are warnings in the
  existing list ("2026 R102: frame perimeter 122.0 in, over the 110 in limit by 12.0 in"). Within-limit lines aren't
  shown as warnings.
- **MCP**: `robot_parts` already carries the config's warnings; the inspection warnings come with them.
- **Units**: lines are shown in the Units setting (imperial shows inches and pounds, metric meters and kg), as every
  other scrimmage report; report.json keeps meters and kg.

### Call I7: sampled, not every step

Height and extension every 20 ms of simulated time (every geom's box is the expensive part, as the climb monitor
notes). A breach shorter than 20 ms can be missed; the rules' inspection is static and refs judge seconds-long
extensions, so 20 ms is fine. *Cost:* a 10 ms flick past the line isn't reported.

## Tests

- Every season entry: the values pinned, the rule numbers present, the URL present.
- Season choice: `[inspection] season`, the `[range]` year, the newest default, `--season`.
- The stock kitbot and swerve robots: measured weight, perimeter, height against hand-computed values.
- Over-limit robots (a long chassis, a tall elevator, a heavy chassis) give the right lines; an elevator built
  collide = false still counts for height.
- A scenario where an arm swings out past the limit for 1 s gives one note with its peak and duration.
- The assembler state carries the warnings; the CLI's exit codes.

## Left to the builder

- Module layout under `scrimmage/inspection/` (spec, measure, monitor).
- Where the monitor hooks into the scenario runner (next to terrain_notes is natural).
- Exact wording of lines, following the climb notes and the American-English style rules.

## Answers to the builder check (2026-11-14; all mine)

1. **Which robots.** Any config that builds a world: a parts robot or a `[model] mjcf` robot. Both have a robot body
   with a subtree mass and geoms. A robot body without a box geom uses the outline of all its geoms for the frame
   (as the climb runs do), and the perimeter line says "frame: the outline of all the chassis's geoms (no frame
   plate)". A config that doesn't build is the usual config error, exit 2.
2. **The stock chassis's mass includes its bumpers and battery.** Its bumpers are massless geoms on the chassis body,
   and `mass` is "everything on the chassis that isn't another part", so they are in it. The parameter's help is
   made explicit: "(frame, bumpers, battery, electronics)". A hand-built or CAD model that leaves them out sets
   `[inspection] battery_mass = 0` / `bumper_mass = 0`; docs/inspection.md says so first. An over-limit weight that rests on
   an estimate still exits 1 (a team running it in CI wants to know), and the line says "with estimates: weigh your
   battery and bumpers and set [inspection] battery_mass and bumper_mass".
3. **The frame.** The frame outline is the largest box on the robot body, by top-view area: for the stock chassis
   its frame plate (L × W; a bumper box is b × (W + 2b), smaller for any chassis the parameter limits allow). So the
   perimeter excludes bumpers, as R104 measures. The 2013 cylinder includes bumpers. Reach (extension) excludes the
   chassis body's own geoms (frame, bumpers, the heading marker).
4. **The start configuration** is the model at its start pose, with no simulation: the world as built and reset
   (joints at their start positions, on the `[range]` start, else the config's start). That is what a robot must
   satisfy before the match: the robot code can't move it while disabled. A model whose arm is up at its start
   position is out of its start configuration, and the line says the measurement is at the model's start pose.
5. **Scenarios and the assembler are opt-in.** The robot config's `[inspection]` section (even an empty one) turns
   on the scenario notes and the assembler warnings, with the season chosen as in Call I2 (so a 2024 range
   inherits 2024). Without it, scenario reports and the assembler are unchanged. Reason: the default stock chassis
   (0.7 m square) has a 110.24 in. frame perimeter, 0.24 in. over 2026's 110 in. limit, so default-on would put
   notes in every existing report. `scrimmage inspect` works on any robot, with or without the section. The
   templates don't gain the section.
6. **"none" and exceptions.** A season with no limit for a measure prints "no limit in <season>" for it and the
   monitor skips it. A breach of a base limit that has an `unless` exception is still reported, with the exception
   quoted and "not checked" (a 2022 robot climbing in the hangar gets a note it may dismiss; the climb runs already
   say the same for G106).
7. **Height counts the whole robot**, bumpers included, at the start and during the match.
