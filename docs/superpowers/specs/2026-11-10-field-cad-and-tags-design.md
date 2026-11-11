# The field's CAD and its AprilTags (kickoff fixes K4): design

The 2024 kickoff dry run (docs/superpowers/specs/2026-11-08-kickoff-dry-run-findings.md) found:
- **gap 5:** a team can't turn the official field CAD into a range;
- **gap 6:** the STAGE team-element STEP wouldn't load;
- **gap 8:** there are no AprilTags;
- **gap 14:** CAD piece units and scale were mishandled.

Since then the cleanup batch fixed gap 6 and most of gap 14:
- `_meshes()` reads meshes by glTF index;
- STEP units are applied;
- `scale` exists;
- size checks exist.

What's left:
- **Names are lost.** The 88 MB field STEP loads, but its 5507 pieces are named `NAUO<n>`. The STEP was written by SolidWorks, which labels every assembly instance `NAUO1`, `NAUO2` and so on. The readable name ("Boost Area End", "FE-00006-06") is on the PRODUCT each instance points to. cascadio uses the instance label.
- **No CAD on the field.** There is no `[[element]]` that takes its shape from CAD, so a part of the field can't be dropped in as it is.
- **No list.** There is no way to list a big STEP's pieces, to find the ones a team wants.
- **No tags.** There are no AprilTags. docs/sensors.md says "Cameras and vision (Limelight-style targeting, AprilTags) are not part of Scrimmage."
- **Gap 14's last two items:** whether `TOL_LINEAR = 0.2` is in meters or the file's units has never been checked, and there is no density check (a 1:40 model passes with the full-size mass).

Under the owner's standing authorization (2026-11-04: "do all remaining work my way"), every design question here is decided and recorded with its cost.

## Goal and limits

- **Goal.** On kickoff day a student can:
  - list the official field STEP's pieces by name, with their sizes and positions;
  - put chosen pieces of it on a range as static field elements that robots and game pieces collide with;
  - load the season's AprilTag layout and see the tags on the field;
  - mount a camera on the robot and check in the Test tab, with no robot code, which tags it can see from where (e.g. "from the SUBWOOFER, the camera sees tag 7").
  Robot code gets the tag layout and the robot's true pose, which is what PhotonLib's own vision simulation needs.
- **Non-goals:**
  - **Image-based vision:** rendering camera frames and detecting tags in pixels, or running PhotonVision or a Limelight pipeline.
  - **Speaking PhotonVision's or Limelight's NetworkTables protocol,** so that an unmodified `PhotonCamera` sees targets. Both protocols are versioned binary formats, owned by those projects.
  - **Turning a whole field STEP into a range automatically.** Which pieces are goals, walls or stations is a game decision. Turning a whole field into a range is the team's job, using the list command and the docs.
  - **Moving CAD parts:** a hinged or sliding element shaped from CAD. Mesh elements are static.

## Decisions

Each one gives what was decided, why, and what it costs if wrong.

- **D1. STEP pieces keep their product names.** `scrimmage/cad/source.py` reads the STEP's `NEXT_ASSEMBLY_USAGE_OCCURRENCE`, `PRODUCT_DEFINITION`, `PRODUCT_DEFINITION_FORMATION` and `PRODUCT` entities with the module's existing `_ENTITY` regex. It maps each instance label to its child's PRODUCT name.
  - `walk()` uses the product name whenever a node's name is `NAUO<digits>` or blank.
  - Repeated names get `#2`, `#3`.
  - A label that already carries a name, as FreeCAD's exports do, is kept.
  - *Why:* cascadio has no option for this. The regex pass is the same one `step_length_unit` already makes.
  - *Cost:* one more pass over the file's text: about a second for the 88 MB field. A STEP whose instances name products some other way keeps cascadio's names.
- **D2. `scrimmage cad list <file>` lists a CAD file's pieces.**
  - **Columns:** one row per piece: its name, its bounding box's center and size (in the file's units and in meters), and its triangle count.
  - **Pieces with the same name** are grouped into one line with a count.
  - **`--match <glob>`** filters by name.
  - **`--near x,y,z --within r`** keeps pieces whose center lies within r meters of the point.
  - **`--json`** writes the full table for scripts.
  - *Why:* the dry run needed a private script (`cad_bbox.py`) to find anything in 5507 pieces.
  - *Cost:* a new command to maintain. It reuses `load_source`, so there is no second reader.
- **D3. A static `kind = "mesh"` `[[element]]`.**
  ```toml
  [[element]]
  name = "stage_blue"
  kind = "mesh"
  file = "cad/FE-2024.STEP"          # or .stl / .obj / .glb; relative to the range file
  select = ["*GE-24200_Blue/GE-24210/*"]   # piece names or keys (globs), from `scrimmage cad list`; all when left out
  units = "in"                       # only when the file can't say (STL/OBJ); a STEP carries its own
  scale = 1.0
  pos = [8.2705, 4.1055, 0]          # where the file's origin goes on the field (m): FE-2024's is the field's middle
  rpy_deg = [90, 0, 180]             # the field STEP is drawn Y-up, with its +x toward red
  ```
  - **The shape.** The selected pieces are merged, transformed, and split into convex parts by the existing `convex_split` (coacd, content-hashed cache). They become MESH geoms on one welded body, `element.<name>`, with the field's collision bits.
  - **The cache.** The merged selection is cached under `SHAPED_CACHE`, keyed by the file's sha256 plus `select`, `units` and `scale`. Only the first build pays the 22 s load of an 88 MB STEP.
  - **Errors:**
    - a `select` glob that matches nothing names the closest piece names;
    - a selection larger than the field, or with more than `MAX_MESH_TRIANGLES` (choice: 200 000) triangles, says to select fewer pieces.
  - **Frames:** `cad list` shows positions in the file's own axes, in meters. A mesh element places the file's
    origin at `pos` after rotating it by `rpy_deg`. A file drawn Y-up needs a 90° roll; FE-2024 also has its origin at the field's middle and
    its +x toward red, so it needs `rpy_deg = [90, 0, 180]` and `pos = [8.2705, 4.1055, 0]` (measured in task 7). The
    how-to says how to find a file's up axis, origin and +x from the list.
  - **Footprint:** the element's footprint for the range checks is its transformed bounding box.
  - *Why:* "import field elements from the official CAD" was the dry run's top ask for building a range. A static mesh covers walls, the STAGE frame, the SUBWOOFER and bumps as they really are.
  - *Cost:*
    - convex splitting rounds concave detail, which is coacd's known limit. The docs say to check the shape in the viewer.
    - The range file points at a CAD file that scrimmage doesn't ship. FIRST's field CAD isn't ours to redistribute, so the built-in ranges get no mesh elements.
- **D4. Gap 14's remainder.**
  - **Tessellation tolerance.** Measure what cascadio's `tol_linear` is in: tessellate a known cylinder at 0.2 and at 0.002, and compare the chord error. Then make `TOL_LINEAR` mean 0.2 mm whatever the file's unit, and record the measurement in a comment.
  - **Density check.** A CAD piece whose mass makes a density more than 10× away from the range water (100 to 10 000 kg/m³, using the convex parts' volume) gets a warning that names both numbers and suggests `scale`. A 1:40 NOTE with the full NOTE's mass comes out near 10⁷ kg/m³.
  - *Why:* both were open findings.
  - *Cost:* the warning can fire on a legitimately dense or hollow piece. It is a warning, not an error.
- **D5. A range carries its AprilTag layout, in WPILib's JSON format.**
  - **The key:** `[range] apriltags = "2024-crescendo"`, a layout scrimmage ships, or a path to a WPILib `AprilTagFieldLayout` JSON file.
  - **Built-in layouts:** each is copied from WPILib at a pinned tag, with the source and WPILib's BSD license note beside it in `scrimmage/testrange/apriltags/`. 2024 ships, and 2026 ships if WPILib has its layout at that tag.
  - **Checks:** the layout's field length and width must match the range's field within 5 cm, or it is an error that names both. Every tag's center must lie inside the field, or slightly outside it, within 0.3 m (a wall-mounted tag).
  - **Frames:** the layout is used as it is. scrimmage's field frame is WPILib's: the origin at the blue alliance
    wall's right corner, x toward red, z up, with the season's real field size. Each tag's Pose3d puts its outward
    normal along the tag's +x. A test pins tag 7 against the SPEAKER's position in `2024.toml`.
  - **Tag size:** `apriltag_size_in` (default 6.5, the 2024 and later 36h11 tags). It sets how big the tags are drawn and the camera's view check.
  - **Drawing:** tags are visual-only geoms, a black square with a white border facing along the tag's +x, and the viewer labels each one with its ID. The real tag36h11 pattern isn't drawn.
  - **Reporting:** `range check` lists the tag count.
  - *Why:* teams already have this file, and their robot code loads the same layout. One format means one truth.
  - *Cost:* a hand-made practice field needs a small JSON file rather than TOML. The docs show a two-tag example. Screenshots show plain squares, not real tags.
- **D6. A `kind = "camera"` sensor that reports which tags it sees.**
  ```toml
  [[sensor]]
  name = "front_cam"
  kind = "camera"
  body = "drive.chassis"
  at = [0.30, 0.0, 0.25]
  toward = [1, 0, 0.17]            # pitched up about 10 deg
  hfov_deg = 70                    # estimate: an OV9281 with its stock lens
  vfov_deg = 50                    # estimate
  max_range = 5.0                  # m; estimate: 6.5 in tags at 1280x800
  max_angle_deg = 60               # choice: tags seen more edge-on than this aren't detected
  fps = 30                         # how often it checks (choice)
  ```
  - **When a tag counts as seen.** At each camera frame (1/fps of sim time), a tag is seen when all of these hold:
    - its center is inside both fields of view, measured in the camera's frame. Up is the body's z, projected.
    - it is within `max_range`.
    - its face points toward the camera within `max_angle_deg`.
    - a ray from the camera to 2 cm short of the tag's center hits nothing, ignoring the robot's own geoms. That makes the STAGE legs, pieces and other robots block the view.
  - **What blocks the view:** the same ray filter the beam and distance sensors use, which ignores the robot's own
    geoms. The ray also ignores the field's perimeter walls, which are where tags are mounted: 2024's SPEAKER tags sit
    at x = -0.038 m, behind the wall's face. Every other solid blocks it: elements, obstacles, goal plates, pieces and
    other robots. `range check` warns when a tag's center is inside a solid other than a perimeter wall, since a
    camera could never see that tag.
  - **Signals:**
    - `sensor.<name>.tags`: how many tags it sees;
    - `sensor.<name>.sees_tag.<id>`: 0 or 1;
    - `sensor.<name>.tag_distance.<id>`: m. When the tag isn't seen it reads what the distance sensor reads with
      nothing in range.
    - These extend the existing sensor signal parser in `checks.py`. An ID that isn't in the range's layout is an
      error that lists the layout's IDs.
  - **Test tab:** the new condition is **camera <name> sees tag N / any tag**. The replay draws the camera's view cone and lines to the tags it sees.
  - **NetworkTables:** the visible IDs are published at `/MuJoCo/Camera/<name>/TagIds` as an int array.
  - **Cost of the work:** the camera runs only when the robot has a camera and the range has tags. It costs one ray per tag in view per frame, and nothing otherwise.
  - *Why:* the kickoff-day question is "where must the camera go to see the tags from where we shoot?". A geometric check answers it with no image pipeline.
  - *Cost:*
    - Detection is a yes/no rule, not a real detector: no motion blur, lighting, pixel noise or pose ambiguity.
    - The defaults are estimates for one common camera. The docs list each one and say how to set them from a team's own camera.
- **D7. Robot code: the layout and the true pose, for PhotonLib's own simulation.**
  - scrimmage already publishes the robot's true pose as `/MuJoCo/GroundTruthPose` (Pose2d struct). The docs gain a section, "Vision in robot code", that shows how to feed PhotonLib's `VisionSystemSim` from that topic, with the same layout JSON. PhotonLib then simulates the camera, its noise and its latency inside the robot program.
  - scrimmage publishes no fake PhotonVision or Limelight results.
  - *Why:* PhotonLib's simulation is maintained by the people who know the camera. A scrimmage copy of their protocol would break each season.
  - *Cost:*
    - A team on Limelight gets no detections in robot code, only the Test-tab camera.
    - The PhotonLib snippet isn't run in this repo's tests: PhotonLib is a network vendordep. The docs say it isn't checked here, and a test pins the topic's name and type.
- **D8. 2024 gets its tags. The docs get a CAD-to-range how-to.**
  - **The range:** `ranges/2024.toml` gains `apriltags = "2024-crescendo"`.
  - **The example:** `examples/blocks/camera` mounts a camera on the speaker example's robot and expects it to see the SPEAKER's tags (7 and 8 on blue) from the SUBWOOFER.
  - **The how-to, in docs/test-range.md:** list a field STEP's pieces, pick names, write a `kind = "mesh"` element, check it in the viewer. It also covers the FreeCAD measuring route that 2026.toml used, for numbers like a goal's opening.
  - **docs/sensors.md:** the camera kind, and an updated "What isn't simulated" section.
  - **The findings doc:** a "K4 status" note under gaps 5, 6, 8 and 14, saying which were already fixed by the cleanup batch.
  - *Cost:* none beyond the work.

## Order and tasks

Each task is one reviewed unit and runs the full suite.

1. **D1:** product names in `load_source`. The test fixture is `tests/data/cad/climber.step` with its instance labels rewritten to `NAUO<n>`, so CI needs no FIRST file (S).
2. **D2:** `scrimmage cad list` (S).
3. **D3:** the `kind = "mesh"` element: parse, cache, build, footprint and errors (M).
4. **D4:** the tolerance measurement and fix, and the density warning (S).
5. **D5:** the AprilTag layout: parse, check, draw, `range check`, and the built-in 2024 (and 2026) JSON (M).
6. **D6 + D7:**
   - the camera sensor: spec, visibility, signals and the NT topic;
   - the Test-tab condition and block, and the replay drawing;
   - the "Vision in robot code" docs (L).
7. **D8:** 2024's tags, the camera example, the CAD-to-range how-to, and the findings' K4 status (S).

## Rules every task follows

- **Messages:**
  - go through `i18n.t` (or `N_` at import), called directly, with table and key names as placeholders;
  - run sentences use run_words.py's `_w` words, so machine text stays the same in every Language and Units;
  - keep these tests green: test_i18n_calls, test_i18n_sweep, test_i18n_typed, test_en_gb_catalog, test_units_machine_text and test_language_docs.
- **No new work where nothing uses it:**
  - no per-step work for a robot with no camera, or a range with no tags;
  - no new reads of a CAD file that has no mesh element.
- **Goldens are unchanged:** the ledger golden, the range goldens and tests/test_range_2024.py's numbers stay byte-for-byte. Tags are visual-only and add no contacts.
- **Sources:** new preset numbers carry `# source:`, `# estimate:` or `# choice:`.
- **Writing:** American spellings and plain words.
- **Running tests:** `.venv/bin/python -m pytest`, never `uv run`. In a worktree: `PYTHONPATH=$PWD ../scrimmage/.venv/bin/python -m pytest` (absolute PYTHONPATH).
