# Kickoff dry run: FRC 2024 CRESCENDO in scrimmage — gap log

Date: 2026-11-08, 14:11 to 14:37 CST. Repo `~/code/scrimmage` at main `b2c0d31`. The repo was not changed.

Role played: a team on kickoff day with the game manual (`manual-2024.md`) and the official CAD, using only
scrimmage's documented tools:
- CLI: `bench`, `test run`, `model`, `launcher aim`, `run`, `edit`
- docs: getting-started, test-range, bench, parts, test-tab, terrain, climbing, match, launcher, part-tab

All files are in `kickoff/work/`, laid out so they can be reused.

- [work/note/](work/note/): the NOTE piece trials
- [work/bench/](work/bench/): the shooter and intake bench ideas
- [work/range/](work/range/): the 2024 range files
  - `2024.toml`: the field
  - `2024-ballgoals.toml`: the stand-in goals variant
  - `r_top.png`, `r_blue.png`: renders
- [work/test/](work/test/): Test-tab tests, stand-in robots, the aiming table and the match scenario
- [work/scripts/](work/scripts/): helpers I wrote because scrimmage has no tool for these jobs (not product code)
  - `cad_bbox.py`: lists the pieces in a STEP file with their bounding boxes, using scrimmage's reader
  - `make_torus.py`: writes a torus NOTE mesh
  - `render_top.py`: renders the range
  - `balls.py`: decodes the piece paths from a kept Test run's `motion.bin`
- [work/cadinfo/](work/cadinfo/): bounding-box dumps of the team elements and the whole field STEP

## Time per step

These are approximate wall-clock times; the steps overlapped while background jobs ran.

| Step | Time | Outcome |
|---|---|---|
| 0. Read the docs, unzip the CAD | ~8 min | |
| 1. NOTE as a piece | ~5 min | works as a rigid ring; no compliance |
| 2. 2024 range | ~12 min | Built from the manual and drawing numbers. Loading the field CAD itself was no help. |
| 3. Bench mechanism ideas | ~6 min | Shooter and intake both run. Flight is balls only. |
| 4. Test-tab tests and a match scenario | ~12 min | Pickup passes. The Speaker shot passes only with a 2016 BOULDER stand-in and a reworked goal. The match scenario needs robot code. |
| 5. Rules | ~3 min | Mostly not expressible |

Every step finished well inside its 30–45 min timebox. Where a step was blocked, it was blocked by design limits, not by time.

---

## Step 1: the NOTE as a game piece

**What I did.** I wrote `work/note/note-range.toml` with four kinds of NOTE:
- `preset = "2024-note"`: the existing preset in `scrimmage/launcher/presets.toml:222`, cited to the AndyMark page
- `shape = "ring"`, from the manual's numbers (14/10/2 in, 0.2353 kg, `material = "foam"`)
- `shape = "cad"` from the Stratasys `Game Piece.step`
- `shape = "cad"` from a torus OBJ I generated with trimesh (`scripts/make_torus.py`: R = 6 in, r = 1 in)

I ran `scrimmage test run settle.test.toml` and `scrimmage model standin.toml`, then settled the MJCF in MuJoCo to see how each piece lies.

**What worked.**
- The preset and the ring are identical: 24 convex slices, 0.3556 m across, 0.0508 m thick. They lie flat at z = 0.025.
- The torus OBJ through `shape = "cad"` was split by coacd into 48 convex pieces and also lies flat. So a true torus
  (round cross-section) *can* be represented, but only if the team makes the mesh themselves.

**Findings.**
- The 3D-print "Game Piece.step" is a **1:40-scale flat washer**: 8.89 mm OD, 5.08 mm ID, 1 mm thick, a B-rep in metres. It is not a full-size NOTE.
- scrimmage imported it as an 8.8 mm piece with no warning.
- `units` is ignored for STEP: `"mm"`, `"m"` and `"in"` all gave 0.00441 m.
- There is no `scale` key, and no "this piece is tiny" check (only the "more than 3 m across" check exists).
- Compliance: `[piece.*]` has no stiffness key. Pieces get a "firm contact".
  - The bench refuses a squishy non-ball piece: `[piece] is squishy with shape cylinder, but squishy pieces are balls only (a sphere, or a ball preset); use kind = "rigid" as a rigid stand-in`.
  - The bench does accept a rigid piece with `stiffness`.

## Step 2: the 2024 field (`work/range/2024.toml`)

**How the 2026 range does it.** I read `ranges/2026.toml` against the docs: "measure its CAD or its manual the same way and cite each number".
- The 2026 numbers were measured in FreeCAD with private scripts (`~/snap/freecad/common/sp9/measure.py`).
- No documented tool turns a field STEP into a range.
- Range elements are primitives only: bump, ramp, platform, bar, box, rod, switch, hinged and sliding elements, `[[obstacle]]` boxes and bars, goals, and stations. There is no mesh element.

**Field CAD attempts.**
- `FE-2024.STEP` (88 MB) loaded through scrimmage's own reader (`scrimmage.cad.source.load_source`, which the Part tab uses) in 22 s, using 1.9 GB of RAM. It has 5507 pieces.
  - Every piece is named `NAUO<n>`: the STEP's PRODUCT names (`GE-24128_Blue Amp`, `Amp_Button_Blue`, ...) are lost.
  - So the field can't be navigated in the Part tab.
- Team-element STEPs:
  - SOURCE, AMP and SPEAKER load. Their pieces are also named NAUO#, in local Y-up frames.
  - `TE-24200.STEP` (STAGE) **fails**: `Could not read TE-24200.STEP as a STEP file: re-export it from CAD and try again.`
    - Cause: cascadio converts it fine.
    - trimesh then splits 81 glTF meshes into 84 geometries.
    - `_meshes()` in `scrimmage/cad/source.py` rejects the file when the counts differ.
- `TOL_LINEAR = 0.2` is documented as "in the STEP file's own units", but cascadio works in metres. That would be a 0.2 m tessellation tolerance for metre files. **I did not verify this.**

**What I built from the manual, LMD and tag numbers.** Every line of the range file cites its source or says it is an estimate. The file validated on the first run (`test run`, `model`).
- 11 NOTEs on the spikes and the center line, as `[[floor_balls]]` with `kind = "note"`. They lie flat.
- SUBWOOFERs: a box plus two yawed boxes each.
- The SPEAKER wall below the opening and its hood: `[[obstacle]]` boxes with `rpy_deg`.
- SOURCE corner walls: yawed boxes, because scrimmage's field is a rectangle.
- 2 SOURCE stations: release at the opening, heading out of the wall, `pitch_deg = 50`, no chute.
- 2 STAGEs:
  - the core is a raised box (`base_height` 28.25 in)
  - each leg is an `[[obstacle]]` bar from the foot to the core
  - the 3 chains are rigid `bar` elements at the 28.25 in low point, `level = "ONSTAGE"`, with `[climb] rule = "lowest-rung"`
- Starts: subwoofer, amp side, source side, a wing NOTE, and one at the blue SOURCE in the stand-in goals variant.

**What could not be built:** see gaps 1–4, 6–10 and 18–19.
- Goals that count NOTEs: the Speaker, the Amp and the Trap.
- A field 8.211 m wide.
- The AMP: it is off the field.
- AprilTags.
- Chain physics, HARMONY and SPOTLIT.
- HIGH NOTEs on top of the AMP.

## Step 3: bench mechanisms (`work/bench/`)

- `note_shooter.toml`: top and bottom 4 in flywheels, a 1.25 in gap, and a rigid 14 × 2 in disc stand-in at 2000 N/m.
  - Result: `3 of 3 left through shot at 12.8 m/s, 2° down`. It ran in 1.5 s.
  - Peak contact 1620 N on a 235 g foam ring is a rigid-piece artifact: not credible.
- `note_shooter_torus.toml`: the torus as `shape = "mesh"`. On the bench a mesh collides as its convex hull, so the hole is filled. Result: 12.6 m/s.
- `note_shooter_flight.toml`: `[flight]` with a NOTE.
  - `bench check` says ok.
  - The run's summary says only `[flight] flies balls only; a rigid piece's flight is not modeled yet, so there are no landings.`
  - So there is no answer to "does it reach the Speaker".
- `note_intake.toml`: an under-bumper roller, with the NOTE fed at −2 m/s as if the robot were driving onto it. Result: `1 of 1 left through inside`.
  - My first try jammed: I had the roller axis backwards. The troubleshooting table covers this.
- `note_intake_torus.toml`: the same intake with the torus hull. Result: `1 of 1 escaped the bench`. The NOTE climbed the roller and flew off.
  - So **the choice of rigid stand-in flips the verdict** for an intake. Only a compliant NOTE can settle it.
- `note_squishy.toml`: refused (error above).

## Step 4: Test-tab tests and a scenario (`work/test/`)

**Pickup.**
- `pickup.test.toml` uses `intake-standin.toml` and the hand-written `parts/note_intake` (a floorless box with a roller and a lid), starting at `blue_wing_note`.
- Result: `passed: a piece reaches intake.hopper at 3.3 s`.
- **But** the trace (decoded with `scripts/balls.py`) shows the NOTE moved 3 mm. The robot drove its floorless box over the NOTE, and "in" means "center inside the body's bounding box".

**Speaker shot.**
- `subwoofer-shot.test.toml` uses `shooter-standin.toml`: a stock launcher with hopper capacity 1, from `blue_subwoofer`.
- It **passes only with the 2016 BOULDER (10 in ball) as a stand-in for the NOTE**, and with the Speaker rewritten as a vertical scoring plane inside the hood. Final settings: hood 55°, 7.5 V.
- How I got there:
  1. `kind = "note"` on the goal: `[[goal]] 'speaker_blue': goals count balls and discs; 'note' is a rigid piece`.
  2. The Speaker as a rect goal with `tilt_deg = 14.8`: a ball that clearly went up through the opening (traced at (0.259, 2.039) → (0.061, 2.107)) did not score. Scoring needs the ball to move against the opening's normal, which for tilt 0–90 means going downward.
  3. `tilt_deg = 194.8` / `-165.2`: `tilt_deg must be from 0 to 90, not 194.8`.
  4. Workaround: `heading_deg = 180`, `tilt_deg = 90`, a 0.40 m tall rect 0.02 m off the wall.
- `scrimmage launcher aim ... --goal speaker_blue` took 2 min 24 s for 5 distances. It aims to "cross rim height descending at the goal's center" (apex 2.8 m), which sends the ball onto the hood roof.
- No hood/volts combination from the table scored. I found the working one by a manual grid of about 19 runs, with no ball outcome visible from the CLI.

**Drive, pick up, shoot in one test.** Not expressible. A rigid NOTE picked up by contact can't enter `[shooter]`: the doc says "Shooting stays ball and disc only too".

**Match timing.**
- `match.scenario.toml` uses `[match] auto_seconds = 15`, `teleop_seconds = 135`, `timer = true`, and a 3 s disabled step. The `[match]` keys parse.
- The run fails: `shooter-standin.toml has no [robot_program] section`. On kickoff day there is no robot code, and the Test tab has no match, auto or teleop concept.

**Source feed.** `source.test.toml` runs `feed_one` from `source_blue`. The NOTE falls out of the opening at 50° and lands on the floor-level intake's lid ("missed").
- My first attempt used `spread_position = [0.3, 0.3, 0]`, along the field axes. A feed was pushed into the 30° SOURCE wall and shot across the field at about 13 m/s, with no error or warning.

## Step 5: rules

| Rule | Status in scrimmage |
|---|---|
| 1-NOTE possession, G403/G409 | Not checked. `[shooter.hopper] capacity = 1` limits the ball path only. The Test tab's "stuck" outcome is only a hint. There is no CONTROL or herding signal and no MOMENTARY (3 s) timer. |
| Extension 12 in (R105) and height 48 in | Signals `climb.extension` and `climb.height` exist only on a range with a bar. My chains make that true, so `always = {signal = "climb.extension", max = 0.3048}` would work in a scenario. The limits default to 2022's 16 in / 66 in and are set only in `climb run` scripts (`[limits]`). Stock mechanisms count only with `collide = true`. There is no Test-tab check. |
| Starting frame perimeter ≤ 120 in, weight 125.5 lb, bumper zone | No check. Weight is visible in Test Setup. |
| Points (Speaker 5/2, AMP 2/1, amplified 5, LEAVE 2) | No scoring table. Goals count pieces; the only points are `[climb] levels`. No auto/teleop values, AMPLIFICATION, Coopertition, MELODY or ENSEMBLE. |
| ONSTAGE 3 / SPOTLIT 4 / HARMONY 2 / TRAP 5 | ONSTAGE ≈ `lowest-rung` on a rigid chain bar. This is **untested**: I ran no climber on it. HARMONY (several robots on one chain) and TRAP (a goal for NOTEs) aren't expressible. SPOTLIT is partly expressible: the MICROPHONE as a `rod` plus a ring "seated" check. Not built. |
| Zone fouls (G404/G414 wing, G423 source/amp, G424 stage) | Not expressible: there are no zones or referee. |

---

## Gaps (numbered, ranked roughly by how much they block a kickoff-day team)

Severity: **B** = blocker, **W** = workaround exists, **N** = nice-to-have. Size: S/M/L.

1. **Goals and shooters can't take a NOTE (non-ball, non-disc piece).** — **B**, L
   - Needed: "shoot a NOTE at the Speaker/Amp and count it", the core 2024 question.
   - Happened:
     - `[[goal]] 'speaker_blue': goals count balls and discs; 'note' is a rigid piece`
     - `[shooter]` takes balls only
     - bench `[flight] flies balls only; a rigid piece's flight is not modeled yet`
   - Workaround: a ball stand-in (2016 BOULDER). Wrong size, mass and aerodynamics.
   - Fix:
     - let goals count any kind (the crossing test is geometry-only, so this is S)
     - give `[shooter]` a non-ball launch: exit velocity and spin from a pinch model, then rigid-body flight with drag (L)
     - bench flight for rigid pieces (M)
   - Docs: test-range.md "What isn't modeled" says so, but nothing tells a 2024 team up front.
   - **K2 status (2026-11-09).** What works now:
     - goals count any kind, and the built-in `2024` range's SPEAKERS and AMPS count NOTEs;
     - a stock `pinch_shooter` with `[shooter] kind = "piece"` shoots a NOTE in the world, from an analytic pinch
       model (`scrimmage/launcher/pinch.py`), and the NOTE flies as a rigid body with drag;
     - the bench flies a NOTE in `[flight]` and scores it against a range goal (all of the NOTE must fit through);
     - `examples/blocks/speaker`: a NOTE from the SUBWOOFER scores in the blue SPEAKER, and an AMP shot scores
       (`tests/test_range_2024.py`).
   - What doesn't work yet:
     - a soft NOTE is K5: it is still a rigid stand-in that doesn't squash in the pinch or on the rim (gap 3);
     - lift isn't modeled, so a flat, backspun NOTE that would glide comes up short;
     - the pinch exit speed isn't validated against the bench yet. The bench applies a rigid piece's stiffness at each
       of its MuJoCo contact points (8 per wheel on the ring), so it squeezes harder than its `stiffness` says. That
       fix is queued after K2, and a strict xfail (`tests/test_pinch_launcher.py::test_exit_speed_against_the_bench`)
       pins it;
     - a field with both balls and NOTEs can't shoot NOTEs (the robot holds the ball).

2. **Goals can only score downward entries (tilt 0–90).** — **B** (W with a hack), S
   - Needed: the SPEAKER opening, entered going up (M 5.6, 14° plane).
   - Happened: a rising ball through the tilted rect did not score. `tilt_deg must be from 0 to 90, not 194.8`.
   - Workaround: a vertical scoring plane inside the hood (`heading 180, tilt 90`).
   - Fix: allow `tilt_deg` 0–180, or add an `entry = "up"|"down"` key.
   - Docs: test-range.md "Geometry conventions" never says which way a ball must cross.

3. **No compliant (soft) NOTE anywhere.** — **B** for intake and shooter design questions, L
   - Needed: the squish of a foam ring in a pinch, intake and hood.
   - Happened:
     - the range piece is a "rigid stand-in"
     - bench: `squishy pieces are balls only`
   - The rigid disc and rigid torus-hull stand-ins gave **opposite** intake results (picked up vs escaped).
   - Fix: a soft-ring model, for example a ring of capsule segments joined by springs, or MuJoCo flex. Already noted as "survey project 5".
   - Docs: OK. The limitation is stated.

4. **No match or game-scoring model; scenarios need robot code.** — **B** for "check it scored X points in auto", M–L
   - Needed: the 15 s / 3 s / 135 s match with points, LEAVE, amplification and endgame.
   - Happened: the `[match]` keys parse, but `has no [robot_program] section`. Tests have no auto/teleop or points.
   - Fix:
     - a `[game]` points table per goal/zone/climb level and phase
     - a `match` block or Test-tab "match mode"
     - LEAVE and zone signals
   - **K3 status:** done for the points and the match. A range's `[game]` sets the phase lengths, the points per goal
     and phase, LEAVE and the rules. The Test tab plays a whole match (`[test] match = true`) and scenarios read the
     `points` signals and show a Score section. The built-in 2024 range has its game (SPEAKER 5/2, AMP 2/1, LEAVE 2,
     ONSTAGE 3, 15 s / 3 s / 135 s), and `examples/blocks/match` plays an auto that scores 7 points. Still not
     modeled: AMPLIFICATION, Coopertition, MELODY, ENSEMBLE, the HARMONY, SPOTLIT and TRAP points, and fouls.

5. **Field CAD can't be turned into a range, and piece names are lost.** — **W** (manual numbers), M
   - Needed: "import field elements from the official CAD", as 2026.toml did privately in FreeCAD.
   - Happened: the 88 MB field STEP loads (22 s, 1.9 GB) but its 5507 pieces are all named `NAUO<n>`. There is no range-from-CAD command and no mesh `[[element]]`.
   - Fixes:
     - (a) keep STEP PRODUCT names in `load_source`: S–M
     - (b) a `kind = "mesh"` static element (STEP/STL plus a transform, coacd-split): M
     - (c) document the FreeCAD measuring workflow that 2026.toml used: S
   - Docs: missing. test-range.md says "measure its CAD" with no how-to.
   - **K4 status (2026-11-10).** Done: (a) STEP product names are kept (the field STEP's pieces are named, not `NAUO<n>`); `scrimmage cad list` lists them with `--match`, `--near` and `--sort`; (b) `kind = "mesh"` elements, each piece split on its own and cached; (c) docs/test-range.md has a "From the field's CAD to a range" how-to, with the FreeCAD route. FIRST's CAD still isn't shipped, so the built-in ranges have no mesh elements.

6. **Official STAGE team-element STEP fails to load.** — **W** (none in scrimmage), S
   - Happened: `Could not read TE-24200.STEP as a STEP file: re-export it from CAD and try again.`
   - Cause: `_meshes()` count check, 81 glTF meshes vs 84 trimesh geometries.
   - Fix: map geometries by glTF mesh index instead of by count. The message also blames the file wrongly.
   - **K4 status (2026-11-10).** Fixed by the cleanup batch (K1, b79b1f8): a STEP's meshes are read by glTF mesh index, so the 81-mesh, 84-primitive STAGE STEP loads. The "re-export it from CAD" message remains, now only for a file the reader really can't read.

7. **Field size is fixed at 16.54 × 8.07 m; 2024 is 16.541 × 8.211 m.** — **W**, S–M
   - Happened: with WPILib y kept, the AMP wall (y = 8.204) lies behind scrimmage's guardrail.
   - An `amp_blue` goal at y = 8.21 was **accepted silently**, unreachable, because goals aren't checked against the field.
   - Alternative: shift y by −0.141 and break agreement with robot code, AprilTags and PathPlanner.
   - Fix:
     - `[range] field = { length, width }` driving `scrimmage/parts/field.py` and `FIELD_WIDTH`
     - check goals and elements against the field

8. **No AprilTags or vision.** — **N** on day 1, **B** for auto-alignment work, M
   - sensors.md: "Cameras and vision ... AprilTags are not part of Scrimmage".
   - Fix: `[[apriltag]]` in ranges, or load a WPILib layout JSON, drawn on the field and published as targets.
   - **K4 status (2026-11-10).** Done. A range takes `apriltags = "<built-in or WPILib JSON>"`, drawn on the field. 2024 ships WPILib's 16 tags. A `kind = "camera"` sensor reports which tags it sees by geometry, the Test tab has a `sees_tag` check, and `examples/blocks/camera` shows it from the SUBWOOFER. Robot code gets the layout and the true pose for PhotonLib. Not done: a real detector (blur, noise, pose ambiguity).

9. **Chain is a rigid bar; HARMONY and TRAP can't be expressed.** — **W** (ONSTAGE ≈ lowest-rung), L
   - climbing.md: "Chain and rope aren't modeled (2024's chain ...)".
   - My chain bars hang at the low point and don't sag or swing. HARMONY needs per-chain multi-robot scoring. TRAP needs a NOTE goal (gap 1).
   - Fix: a sagging chain as a chain of capsules, or a `catenary` element, plus a `[climb] rule = "off-carpet"` with per-element counts.

10. **No range viewer or validator; `scrimmage edit 2024.toml` treats a range as a robot config.**
    - **W**, S–M
    - Happened: `Read-only: this config has errors; fix them in a text editor: describe the robot with [[part]] entries ...`
    - I had to validate through a stand-in plus `test run`, and render with my own MuJoCo script.
    - Fix: `scrimmage range check|view FILE` and a Range tab.
    - Docs: `edit --help` doesn't list range files.

11. **The Test CLI result doesn't show where shots and pieces went.** — **W**, S
    - Needed: "why didn't it score?"
    - Happened: `result.json` has `goals: {speaker_blue: 0}` only: no fired count and no ball table. Scenario reports have `## Balls`; tests don't.
    - I decoded `motion.bin` myself.
    - Fix: add the scenario report's per-ball table (fired, exit speed, first touch, end point) to `test run` output and `result.json`.

12. **`launcher aim` assumes a descending-at-center hub entry.** — **W**, M
    - For the SPEAKER it put the apex at 2.8 m, over the hood roof. None of its rows scored.
    - It also took 2 min 24 s for 5 distances.
    - Fix: aim at the goal's scoring surface with its entry direction, and model the rim and plates.
    - **K2 status (2026-11-09).** What works now:
      - `launcher aim` aims at a goal's scoring surface from its entry side: going up through the SPEAKER, under the
        hood, which is an `[[obstacle]]`;
      - a ball's 5 SPEAKER distances take about 10 s (it was 2 min 24 s);
      - a pinch shooter's NOTE table is checked with all of the NOTE through the opening and nothing hit first. Its
        rows must peak at least 0.10 m above the opening's center (`UPWARD_APEX_MARGIN`). Flown again in the world
        with the wheels 2% off, 21 of 21 shots scored with the margin, and 7 of 24 without it (9 of 24 when first
        measured; 7 when measured again on 2026-11-09, with the bounding box and with the outline alike): the lowest arcs
        crossed flat and caught the front lip. The cost: the slowest shots and the farthest distance (2.57 m) are gone.
    - What doesn't work yet:
      - a NOTE table takes about a minute (21 tilts, 8 distances);
      - the rows rest on the unvalidated pinch exit speed and on drag with no lift (gap 1);
      - before the crossing, a NOTE is checked against its goal's own rim bars by its center only, so the margin is
        what keeps its corners off the lip;
      - a ball's SPEAKER rows keep the lowest-apex rule with no margin, and aren't flown in the world.

13. **"Piece in body" = center inside the bounding box: an open-bottom intake "picks up" a NOTE it just drove over.**
    - **N/W**, S
    - pickup.test.toml passed while the NOTE moved 3 mm.
    - Fix: also require contact or support by the body, or report "enclosed but not moved". Document the bounding-box rule more prominently.

14. **CAD piece units and scale.** — **N**, S
    - `units` is ignored for STEP. There is no `scale`, and no lower size check: a 1:40 piece was accepted at 8.8 mm.
    - Fix:
      - warn when a piece is < 2 cm or differs from `mass`'s implied density by more than 10×
      - add `scale`
      - say in the docs that STEP carries its own units
    - The only "official" NOTE CAD was the 1:40 3D-print file.
    - **K4 status (2026-11-10).** The cleanup batch (K1, b3b520a) made a STEP's own units count, added `scale`, and added a warning for a piece under 2 cm or with a density lighter than air or denser than tungsten (the docs say a STEP carries its own units). K4 adds the rest: the tessellation tolerance is measured and pinned (it was already in millimeters, so no code changed, R2), and CAD pieces get a tighter density check, 100 to 10 000 kg/m³ (10 times either side of water), with a hint to try `scale` (R3).

15. **No soft-contact or stiffness key for range pieces.** — **N**, S (until gap 3)
    - The bench has a rigid `stiffness`; `[piece.*]` does not. Add it so the Test tab and bench agree.

16. **Station spread is along the field axes only and isn't checked for overlap.** — **W**, S
    - Happened: `spread_position = [0.3, 0.3, 0]` at a 30° SOURCE wall sometimes spawned the NOTE in the wall, and it shot out at about 13 m/s. No error.
    - Fix: give spread in the station's own frame (along and across the heading), and check the spread box against elements as the release point is.

17. **Rules not checkable.**
    - Needed: G403/G409 one-NOTE CONTROL, R104/R105 perimeter, extension and height, zone fouls, LEAVE.
    - Severity: **N** day 1, **W** for extension (`climb.extension` in scenarios, range must have a bar). Size M.
    - Fix:
      - per-season `[rules]`: extension and height limits used everywhere, not only climb scripts
      - a `controlled` count signal (pieces touching or supported by the robot for > 3 s)
      - zone polygons with "robot in zone" signals
    - **K3 status:** the checks work now. `[game.rules]` holds the limits (2024: one NOTE held, 12 in of extension,
      48 in of height), a match test reports each breach, and a zone's LEAVE is a points item. Still not modeled:
      fouls and penalties (zone fouls, pinning, contact), so a breach is reported but costs no points.

18. **HIGH NOTEs and AMP-top staging can't be placed.** — **N**, S
    - The AMP is off the field (gap 7). Floor pieces must rest on the carpet; there's no "on top of element" placement.

19. **No floor tape or zones** (wing line, starting zone, stage zone). — **N**, S
    - Needed for LEAVE, PARK and G404/G414. Add a `[[zone]]` polygon plus a robot-in-zone signal.

20. **Docs gaps found along the way.**
    - **N**, S
    - test-tab.md documents the file form of `scored` and `aimed` but not `made_it_in` (it's `{ made_it_in = { at_least = 50 } }`; I guessed `at_least_pct` first).
    - The start-outline check is a fixed 0.9 m square, so an intake that reaches a floor piece at the start isn't caught. **Not verified**; I avoided it.
    - bench.md's `[flight]` says "balls only", but `bench check` accepts it with a rigid piece and only the run summary says so: make it a check warning.

## What worked well (keep)

- The range format is expressive enough for a credible 2024 field in about 10 minutes from manual numbers, and it validated first time.
  - Validation errors are precise, for example `goals count balls and discs; 'note' is a rigid piece` and `tilt_deg must be from 0 to 90`.
- The `2024-note` preset already exists and cites its source. `shape = "cad"` plus coacd gives a real torus.
- The bench turned a spoken shooter idea into numbers in under 2 s per run, with clear pictures.
- The Test tab runs with no robot code. Hand-written parts, stand-in robots and custom range files all work from the CLI.
- The rest of the stations work: a NOTE feeds out at the 50° pitch.
