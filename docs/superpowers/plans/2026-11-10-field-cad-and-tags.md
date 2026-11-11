# The field's CAD and its AprilTags (K4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** on kickoff day a student can list the official field STEP's pieces by name, put chosen pieces on a range as
static mesh elements, load the season's AprilTag layout, and check in the Test tab which tags a robot camera sees
from where.

**Architecture:**
- `cad/source.py` maps SolidWorks' `NAUO<n>` instance labels to PRODUCT names. A new `cad/listing.py` backs
  `scrimmage cad list`.
- A new `testrange/mesh.py` parses `kind = "mesh"` elements, cached by content. `build_elements.py` builds them as
  MESH geoms on one welded body.
- A new `testrange/apriltags.py` reads WPILib layout JSON into `Range.apriltags` and draws the tags as visual-only
  geoms.
- A new `sensors/camera.py` runs the `kind = "camera"` geometric check at the camera's fps. Its readings feed signals,
  a Test-tab condition, the replay and one NT topic.

**Tech Stack:** Python 3.11+, MuJoCo 4 (MjSpec mesh assets, mj_ray), cascadio and trimesh (the `cad` extra), coacd
(`convex_split`), numpy, pyntcore and wpimath (NT), pytest with Playwright browser tests, Blockly and three.js on the
pages.

**Spec:** docs/superpowers/specs/2026-11-10-field-cad-and-tags-design.md

## Global Constraints

These are the spec's "Rules every task follows", verbatim:

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

And these for this branch:

- **Commands:**
  - Never `uv run`. In the worktree /home/altainia/code/scrimmage-k4, run tests as
    `PYTHONPATH=/home/altainia/code/scrimmage-k4 ../scrimmage/.venv/bin/python -m pytest`.
  - The full suite (`... -m pytest -q tests/`) takes about 40 minutes. Run it once per task, at the end, in the
    background.
  - Don't run `git checkout`, `git switch` or `git stash`. Don't push. Don't dispatch subagents.
- **Browser tests** wait with Playwright `expect()` (`to_have_text`, `to_be_visible`, `to_have_count`), never with
  sleeps or polling loops.
- **Range file errors** go through `_Reader`'s `err`, naming the file and the table (`element 'stage_blue'`,
  `[range]`).
- **FIRST's CAD isn't committed.** The 88 MB field STEP is used only by local-only tests that skip when
  `FIELD_STEP` is missing:
  `FIELD_STEP = Path("/tmp/claude-1000/-home-altainia-code-scrimmage/3537f469-7b90-4632-aa74-f3b52c0bc63a/scratchpad/kickoff/work/cad/field/FE-2024.STEP")`
  (in `tests/cad_helpers.py`, Task 1).
- **Commits:** one per task, message `K4: <what>`, with the trailer
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Done means:** the task's tests and the full suite pass. The report lists what changed, the decisions made with
  their costs, and the suite's summary line.

## Rulings made while planning

Recorded here so each task can cite them. Each gives the ruling, then its cost.

- **R1, blank instance names (D1):** cascadio names an instance node by the NAUO's name, else by its id. A blank node
  has no label to map, so it keeps `node<idx>`. The real field file has none: all 6122 instance nodes are
  `NAUO<n>`, measured 2026-11-10. *Cost:* none seen.
- **R2, the tolerance is already in millimeters (D4):** measured on 2026-11-10. With the climber fixture in mm, 0.2
  gives a 3 mm bolt a 0.022 mm chord error. Rewritten to meters (r = 3 m), 0.2 gives a 0.1 mm chord error, not
  0.2 m. OpenCASCADE meshes in its session unit, mm, so `TOL_LINEAR = 0.2` already means 0.2 mm in every unit. The fix
  is the comment plus a test that pins it. *Cost:* none.
- **R3, the density check already exists (D4):** `pieces.py` has `DENSITY_RANGE = (1.0, 20000.0)` for every kind.
  It measures volume from the CAD file's own meshes (`own`), which is more accurate than the convex parts. Ruling:
  CAD pieces get `CAD_DENSITY_RANGE = (100.0, 10000.0)`, the spec's 10× of water, with the scale hint. They keep
  `_finish`'s volume, not the convex parts'. Other kinds keep `DENSITY_RANGE`. *Cost:* a CAD shell that isn't closed
  measures by its convex parts, as `_finish` already falls back.
- **R4, split per piece (D3):** each selected piece goes through `convex_split` on its own, in the file's axes.
  The merged selection is what the cache stores. Splitting the selection as one soup would make coacd re-split
  everything on any edit, and `convex_pieces` refuses a mesh that isn't closed. A piece that isn't a closed solid is
  left out, with one warning that counts and names them. *Cost:* more geoms (one or more per piece), and a sheet-body
  piece is missing from the collision shape.
- **R5, a `ground` key on mesh elements (D3):** `ground = true` (default false) puts the geoms in ELEMENT_GROUP, as
  a box's does, so a CAD bump reads as ground for the terrain signals. *Cost:* one key beyond the spec's list.
- **R6, mounted tags (D5, D6):** 2024's tags 4 and 7 sit 3.8 cm inside the `speaker_*_wall` **obstacles**, not the
  perimeter walls. Tags 11, 12, 15 and 16 sit 7.1 to 7.3 cm inside the estimated `stage_*_core` obstacles (measured
  against 2024.toml). Ruling:
  - each tag gets a **face point**: its center pushed out along its normal past any solid it lies at most
    `MOUNT_DEPTH = 0.10 m` (choice) inside, plus 1 mm;
  - the tag is drawn at its face point;
  - the camera's ray ends 2 cm short of the face point, not of the center;
  - `range check` warns only for a tag deeper than MOUNT_DEPTH inside a solid.

  The perimeter walls are still ignored by the ray, as the spec says. The tag's true pose is what the camera's field
  of view, range and angle use. *Cost:* a drawn tag can be up to 10 cm off its true spot, and a tag mounted inside a
  CAD mesh part counts as inside a solid only by the part's bounding box.
- **R7, tag size and range:** `max_range` is the range for 6.5 in tags. With `apriltag_size_in` set, a camera sees
  out to `max_range * size_in / 6.5`, since a tag's pixel count scales with its size. *Cost:* the scaling is linear,
  which a real detector only roughly follows.
- **R8, both 2026 layouts ship:** WPILib v2026.2.2 has `2026-rebuilt-welded.json` (field 16.541 x 8.069) and
  `2026-rebuilt-andymark.json` (16.518 x 8.043), 32 tags each. Both are within 5 cm of 2026.toml's generated
  16.54 x 8.07 field. Ship both under those names. 2026.toml doesn't get a tag layout, since D8 names only 2024.
  *Cost:* two files to keep in step.
- **R9, the condition's name:** `{ sees_tag = { camera = "front_cam", tag = 7 } }`. Leave `tag` out for "any tag".
  *Cost:* none.
- **R10, ID labels:** the web live view (live3d.js) labels each tag with its ID. MuJoCo screenshots (render.py) show
  the squares only. *Cost:* `range view --out` pictures have no numbers.

## Review Focus

1. **A tag inside its mount (R6).** On 2024, a camera in front of the blue SPEAKER sees tag 7, even though its center
   is 3.8 cm inside `speaker_blue_wall`. The drawn square shows in front of that wall's face. Pinned in Task 5
   (`test_2024_mounted_tags_face_the_field`) and Task 6 (`test_a_camera_sees_tag_7_inside_its_wall`).
2. **A camera behind a tag, or past its edge.** A camera behind the tag's plane, or seeing it more edge-on than
   `max_angle_deg`, sees nothing, even in range and in view. Exactly at the limit counts as seen. Pinned in Task 6
   (`test_in_view_edges`).
3. **What blocks the view.** The SUBWOOFER blocks a camera mounted low behind it. The robot's own bumpers, a tag
   square and the perimeter wall don't. A game piece in the way does. Pinned in Task 6 (`test_what_blocks_a_camera`)
   and Task 7 (`test_a_low_camera_is_blocked_by_the_subwoofer`).
4. **A poke between frames.** After `set_pose` or `move_body`, the next reading is of the new pose, not the last
   frame's. `World._read_sensors` forces a camera frame. Pinned in Task 6 (`test_a_poke_reads_the_camera_again`).
5. **A mesh selection with pieces that aren't solid.** A sheet body or a flat piece is left out with a warning. It
   isn't a coacd error or a MuJoCo compile failure. Moving the element (`pos`, `rpy_deg`) uses the cache and doesn't
   re-read the STEP. Pinned in Task 3 (`test_an_open_piece_is_left_out_with_a_warning`,
   `test_moving_the_element_uses_the_cache`).

---

### Task 1: STEP pieces keep their product names (spec D1)

**Files:**
- Modify: `scrimmage/cad/source.py`: `_load_step` (141-201) and `walk` (171-183). Add `_instance_names` beside
  `step_length_unit` (58-78).
- Create:
  - `tests/data/cad/climber_nauo.step`: climber.step with every `NEXT_ASSEMBLY_USAGE_OCCURRENCE('<id>','<name>',...`
    rewritten, in file order, to `('NAUO<n>',' ',' ',...`, as SolidWorks writes it. There are 13 of them.
  - `tests/cad_helpers.py`, holding `FIELD_STEP` (Global Constraints).
  - Add the rewrite as a function at the end of `tests/make_cad_fixtures.py`, after its FreeCAD exports. It must run
    without FreeCAD, so guard the FreeCAD imports or give it its own `if __name__` path, so the fixture can be
    remade.
- Test: `tests/test_cad_source.py`.

**Produces:**
- `NAUO_NAME = re.compile(r"NAUO\d+$")`.
- `_instance_names(raw: bytes) -> dict[str, str]`: each NAUO's id label maps to its child's PRODUCT name. The chain
  is NAUO(id, name, desc, relating PD, **related PD**, ref) → PRODUCT_DEFINITION(id, desc, **formation**, ctx) →
  PRODUCT_DEFINITION_FORMATION or `..._WITH_SPECIFIED_SOURCE`(id, desc, **product**, ...) → PRODUCT(id, **name**,
  ...). Use the name, or the id when the name is blank.
  - Reuse `_ENTITY`, and keep only bodies starting `NEXT_ASSEMBLY_USAGE_OCCURRENCE` or `PRODUCT`. That takes 0.64 s on
    the 88 MB field file, measured.
  - Mind that `PRODUCT_DEFINITION_SHAPE` and the `PRODUCT_DEFINITION...` prefixes overlap: match the entity name up
    to `(` exactly.
- In `_load_step`, after the GLB's nodes are read: when any node name matches `NAUO_NAME`, read the file's bytes once
  and build the map. A FreeCAD file pays nothing. `walk` uses `names.get(name, name)` for a NAUO-named node. Repeated
  names get `#2`, `#3` from `_numbered`, as now.

- [ ] Write `test_solidworks_instance_labels_get_product_names`: `load_source(DATA / "climber_nauo.step")` gives
  exactly the same `[p.key for p in s.pieces]` and `s.groups` as `climber.step`, including `Climber/hook_assy#2/hook`
  and `Climber/bolt#6`. Each piece's `name` is the product's name.
- [ ] Write `test_a_named_label_is_kept`: `_instance_names` is never called for climber.step (monkeypatch it to
  raise).
- [ ] Write `test_instance_names_reads_the_specified_source_formation`: a hand-written STEP text with one NAUO,
  PRODUCT_DEFINITION, `PRODUCT_DEFINITION_FORMATION_WITH_SPECIFIED_SOURCE('ANY','',#3,.NOT_KNOWN.)` and PRODUCT
  ('Boost Area End', ...) maps `{"NAUO1": "Boost Area End"}`. A blank PRODUCT name falls back to its id.
- [ ] Write `test_the_field_step_has_product_names`, local-only (`pytest.mark.skipif(not FIELD_STEP.is_file())`):
  - no piece key segment matches `NAUO_NAME`;
  - some piece is named `Boost Area End`;
  - `len(s.pieces) == 5507`.
- [ ] Run the new tests and see them fail. Implement. Run `tests/test_cad_*.py` and see them pass.
- [ ] Run the full suite and commit: `K4: STEP pieces keep their product names`.

### Task 2: `scrimmage cad list` (spec D2)

**Files:**
- Create: `scrimmage/cad/listing.py`.
- Modify: `scrimmage/__main__.py`. Add a `cad` parser next to `range` (232-243), with `cad list FILE [--match GLOB]
  [--near X,Y,Z --within R] [--units m|mm|in] [--json]`. Add a `_cad(args)` dispatch beside `_range` (356, 840).
- Test: a new `tests/test_cad_list.py`.
- Docs: `docs/test-range.md`. Task 7 writes the how-to; this task adds a short "`scrimmage cad list`" reference
  section next to "Scenarios: signals ..." (1103).

**Produces (Task 3 and Task 7 use these):**
- `Row(key: str, name: str, count: int, center_m: tuple[float, float, float], size_m: tuple[float, float, float],
  triangles: int)`, a frozen dataclass.
- `file_units(src: Source) -> str`: a STEP's `step_length_unit(src.path)` (`"m"` when None), or a mesh source's
  `src.units`.
- `matches(piece_or_row, glob: str) -> bool`: `fnmatch.fnmatchcase` against the name or the key.
- `rows(src: Source) -> list[Row]`: one row per piece, `count = 1`, from its vertices' bounding box (center and
  sides).
- `grouped(rows: list[Row]) -> list[Row]`: rows with the same name, in first-seen order. Each is one row: `count`,
  the combined bounding box, the summed triangles, and the first one's key.
- `near(rows, point: tuple[float, float, float], within: float) -> list[Row]`: rows whose center is within `within`
  m. Applied before grouping.
- `table_lines(rows: list[Row], units: str) -> list[str]`: a header, then one line per row. Each line has the name,
  ×count when over 1, the center and size in `units` and in m (`UNITS`/`STEP_UNITS` factors), and the triangles. The
  header and the "×" and "pieces" words go through `i18n.t`.
- `to_json(rows: list[Row], units: str) -> dict`: `{"file", "units", "pieces": [{key, name, center_m, size_m,
  center_file, size_file, triangles}]}`, ungrouped.
- Errors (exit 2, `scrimmage: <message>`):
  - `--near` without `--within`, or the reverse;
  - `--near` that isn't three numbers;
  - a `CadError` from `load_source`, printed as it is;
  - a `--match` that matches nothing names up to 5 close names (`difflib.get_close_matches` on the glob with `*?[]`
    stripped). Exit 1.

- [ ] Write the tests on climber.step:
  - `test_list_groups_repeated_names`: one `bolt` line with ×6, and one `hook` line with ×2;
  - `test_list_match_filters_by_name_or_key`: `--match "hook*"` gives the hook rows only;
    `--match "Climber/arm_*"` gives arm_A and arm_B;
  - `test_list_near_keeps_pieces_within`: around arm_A's center within 0.05 m, only arm_A;
  - `test_list_json_has_every_piece`: 13 pieces, with the mm and m centers related by 1000;
  - `test_list_shows_file_units_and_meters`: the header says mm, and arm_A's size reads 20 x 20 x 800 mm and
    0.02 x 0.02 x 0.8 m;
  - `test_list_errors`: `--near` alone exits 2 and names `--within`; a match on nothing exits 1 and names `bolt`
    for `--match bolts`;
  - `test_list_an_stl_folder`: `tests/data/cad/stl`, units guessed as mm.
- [ ] Write `test_list_the_field_step_finds_the_stage`, local-only on FIELD_STEP: `--match "TE-24200*"` gives at
  least one row, and the run takes under 60 s.
- [ ] Implement, run the tests, run the full suite, and commit: `K4: scrimmage cad list`.

### Task 3: the `kind = "mesh"` element (spec D3)

**Files:**
- Create:
  - `scrimmage/testrange/mesh.py`: the `Mesh` element, its parse and its cache.
  - `tests/test_range_mesh.py`.
- Modify:
  - `scrimmage/testrange/elements.py`:
    - add `"mesh"` to `_CLASSES` (363), `_OTHER` (347) and `_LENGTHS` (`"mesh": ()`);
    - in `_element` (578-612), a mesh drops `center`, `center_in` and `yaw_deg` as a rod does, and returns
      `parse_mesh(r, name, base_dir, size)`;
    - add `Mesh` to `Element`;
    - `_extent` and `_on_floor` get Mesh branches. `_on_floor` gives `[]` when the mesh's lowest point is above
      `START_STEP`.
    - `parse_elements(path, tables, size=(FIELD_LENGTH, FIELD_WIDTH))` gains `size`. Its base folder is
      `Path(path).parent`.
  - `scrimmage/testrange/spec.py:897`: pass `size` to `parse_elements`. Add each mesh's `warnings` to the range's
    warnings (beside the kinds' warnings at 913).
  - `scrimmage/testrange/build_elements.py`: an `isinstance(e, Mesh)` branch in `add_element` (393), calling a new
    `_mesh(spec, e)`, and the module docstring's mesh paragraph.
  - `scrimmage/testrange/station.py` `_element_boxes` (305): a Mesh gives each convex part's axis-aligned box in the
    field, `(center, np.eye(3), half)`.
  - `scrimmage/testrange/pieces.py` `_cad` (775-832): move the `units`/`scale`/STEP-unit lines (784-808) into
    `cad_factor(r: _Reader, path: Path) -> tuple[float, str | None, list[str]]`, returning factor, units for
    `load_source` and notes. `_cad` and `mesh.py` both use it, and `_cad` behaves exactly as before.
  - `scrimmage/assembler/view.py` `_world_geoms` (158): also draw mesh geoms of `element.*` bodies, not only
    `ball.*`.
- Docs: `docs/terrain.md` "Field elements in range files" (23): the mesh kind's keys, frames and errors.

**Interfaces:**
- Consumes: Task 2's `matches(piece, glob)`; `load_source`; `convex_split` (cad/fit.py); `SHAPED_CACHE`.
- Produces:
  - `MAX_MESH_TRIANGLES = 200_000  # choice: ...`.
  - `MESH_CACHE = SHAPED_CACHE / "mesh-elements"`.
  - `@dataclass(frozen=True, eq=False) class Mesh`, with fields:
    - name, file (Path), select (tuple[str, ...]), units (str | None), scale, pos (tuple3), rpy_deg (tuple3),
      friction, ground (bool);
    - `parts: tuple[tuple[np.ndarray, np.ndarray], ...]` (convex parts, m, in the file's axes after units and scale);
    - `lo` and `hi` (np.ndarray (3,), the field-frame bounding box after the transform);
    - `center: tuple[float, float]` (the box's plan center);
    - `yaw_deg: float = 0.0`;
    - `triangles: int`;
    - `pieces: tuple[str, ...]` (the keys selected);
    - `warnings: tuple[str, ...] = ()`.
  - `parse_mesh(r: _Reader, name: str, base_dir: Path, size: tuple[float, float]) -> Mesh`.
  - `selection(path: Path, select: tuple[str, ...], units: str | None, scale: float, r: _Reader) ->
    list[tuple[str, np.ndarray, np.ndarray]]`, as (key, v, f) in meters, file axes. It is cached in
    `MESH_CACHE / f"{key}.npz"`. The key is the sha256 of the file's bytes plus
    `json.dumps([select, units, scale, 1])`. Write to a temp name, then `os.replace`, as `convex_split` does. A cache
    hit never imports cascadio.
  - `mesh_rotation(e: Mesh) -> np.ndarray`: `pieces._rpy`'s convention (roll x, pitch y, yaw z, fixed axes).
  - Body `element.<name>`, welded at `pos` with `mesh_rotation`'s quat. Each part is a mesh asset
    `element.<name>.<k>` and a geom of the same name. Geoms carry FIELD_BITS, the element's friction, and
    ELEMENT_GROUP with `ground`, else NON_GROUND_GROUP. STRUCTURE_RGBA.

- [ ] Write the parse tests on a range file in tmp_path that points at `tests/data/cad/climber.step` (absolute path)
  or at `tests/data/cad/stl`:
  - `test_a_mesh_element_from_a_step`: with `select = ["Climber/arm_*"]`, `pieces` holds arm_A and arm_B. `lo` and
    `hi` equal the arms' box moved by `pos = [2, 3, 0]`. With `rpy_deg = [0, 0, 90]`, the plan extents swap.
  - `test_select_left_out_takes_every_piece`;
  - `test_a_select_that_matches_nothing_names_close_names`: `select = ["bolts"]` gives a RangeError naming
    `element 'm'` and `bolt`;
  - `test_units_for_an_stl_and_a_steps_own`: STL with `units = "mm"` comes out at 0.3 m. A STEP with `units` goes
    through `cad_factor`, as `tests/test_range_pieces.py:test_a_centimetre_step_takes_units` does.
  - `test_too_big_or_too_many_triangles_says_select_fewer`: `scale = 100` on the climber is larger than the field.
    `MAX_MESH_TRIANGLES` monkeypatched to 10 is too many. Both messages contain "select fewer pieces".
  - `test_mesh_keys_are_checked`: an unknown key, `center` on a mesh, and a missing `file` each name the key.
- [ ] Write the cache tests:
  - `test_moving_the_element_uses_the_cache`: the second parse, with another `pos` and `rpy_deg`, doesn't call
    `load_source` (monkeypatch it to raise).
  - `test_an_edited_file_misses_the_cache`: a copied STEP with one byte appended to a comment line reads again.
  - `test_no_mesh_element_reads_no_cad`: parsing `2024` and `2026` never imports `scrimmage.cad.source` (check
    `sys.modules` in a subprocess).
- [ ] Write `test_an_open_piece_is_left_out_with_a_warning` (Review Focus 5): an STL folder in tmp_path with one
  closed cube and one open square sheet (two triangles). The element has the cube's parts only. Its one warning names
  the sheet and says it isn't a closed solid. The world compiles.
- [ ] Write the build tests:
  - `test_the_mesh_element_builds_and_a_ball_lands_on_it`: a range with a 0.5 m cube STL as a mesh element at
    `pos = [3, 3, 0]`. A ball dropped over it rests on top, within 1 cm of 0.5 m plus its radius.
  - `test_its_footprint_blocks_a_start`: a start inside its plan box gives check_clear's "overlaps element" error.
  - `test_the_view_draws_the_mesh`: `model_view(world, include_world=True)["world_geoms"]` has the element's mesh
    geoms with mesh indexes.
- [ ] Write `test_the_field_stage_is_a_mesh_element`, local-only on FIELD_STEP: `select = ["TE-24200*"]` builds.
  Its plan box is between 2 and 6 m on a side. A second parse takes under 2 s.
- [ ] Implement, run `tests/test_range_*.py tests/test_terrain*.py`, run the full suite, and commit:
  `K4: kind = "mesh" field elements from CAD`.

### Task 4: the tessellation tolerance and the density warning (spec D4)

**Files:**
- Modify:
  - `scrimmage/cad/source.py:24`: `TOL_LINEAR`'s comment. Per R2, it reads "mm, whatever the file's unit:
    OpenCASCADE meshes in its session unit (measured 2026-11-10: a 3 m bolt in a meter STEP at 0.2 has a 0.1 mm
    chord error)".
  - `scrimmage/testrange/pieces.py`:
    - add `CAD_DENSITY_RANGE = (100.0, 10000.0)  # choice: 10x either way of water (spec D4)` next to DENSITY_RANGE
      (71);
    - `_size_warnings` (500-520): for `shape == "cad"`, check the density against CAD_DENSITY_RANGE. The CAD message
      names the density and the range, and suggests `scale` and `mass` (`scale_setting="scale = 40"`). It is a new
      i18n string; add an en-GB entry if it has a listed word.
    - Keep the early size-warning return: the small-piece warning already says `scale`.
- Test:
  - `tests/test_cad_source.py`;
  - `tests/test_range_pieces.py`, beside `test_a_piece_heavy_for_its_size_is_warned_about` (288).
- Docs: `docs/test-range.md` around 663, the density sentence for CAD pieces.

- [ ] Write `test_tessellation_tolerance_is_millimeters_in_any_unit`. Take climber.step, and a tmp copy with
  `SI_UNIT(.MILLI.,.METRE.)` replaced by `SI_UNIT($,.METRE.)`. Measure the `Climber/bolt` piece's chord error,
  `r * (1 - cos(pi / n))`, from its distinct angles around its z axis. Assert:
  - the mm file's is at most 2e-4 m;
  - the meter file's is at most 2e-4 m and above 2e-5 m, so it is neither 0.2 m nor finer than the tolerance asks.

  The test should pass at once: it records R2.
- [ ] Write the density tests on cube STLs (`write_stl`, `cube` from tests/pieces_helpers.py):
  - `test_a_cad_piece_far_from_waters_density_is_warned_about`: a 0.05 m cube at 1.5 kg (12 000 kg/m³) and at
    0.01 kg (80 kg/m³) each give one warning, naming the density, `100`, `10000` and `scale`. At 0.2 kg (1600) there
    is none.
  - The same 1.5 kg as a `shape = "box"` piece gives no warning, since the old range applies.
- [ ] Check that no shipped range or example has a CAD piece (none does: `grep -rl 'shape *= *"cad"' --include=*.toml`).
  Then run `tests/test_range_pieces.py`, the full suite, and commit:
  `K4: CAD tessellation is in mm (measured), and a CAD piece's density is checked against water`.

### Task 5: the AprilTag layout (spec D5)

**Files:**
- Create:
  - `scrimmage/testrange/apriltags.py`;
  - `scrimmage/testrange/apriltags/2024-crescendo.json`, `2026-rebuilt-welded.json` and
    `2026-rebuilt-andymark.json`, copied byte for byte from allwpilib tag **v2026.2.2** (commit
    1fd159938f65ee9d0b22c8a369030b68e0efa82c), path `apriltag/src/main/native/resources/edu/wpi/first/apriltag/<name>.json`.
    2024-crescendo.json there is the same blob as at v2024.3.2 (git sha 8cb837a5023bc3e3b32ad7d79dfb4c7c37405151).
  - `scrimmage/testrange/apriltags/LICENSE.md`: allwpilib's root `LICENSE.md` at v2026.2.2, as is. It begins
    "Copyright (c) 2009-2026 FIRST and other WPILib contributors / All rights reserved. / Redistribution and use in
    source and binary forms ...", the BSD 3-clause text.
  - `scrimmage/testrange/apriltags/README.md`: the source repo, tag, commit, paths and the two sha's, plus how to
    update the files.
  - `tests/test_range_apriltags.py`.
- Modify:
  - `pyproject.toml:39`: add `"testrange/apriltags/*"` to the package data.
  - `scrimmage/testrange/spec.py`:
    - `_RANGE` (385) gains `"apriltags"` and `"apriltag_size_in"`;
    - `Range.apriltags: AprilTagLayout | None = None`;
    - in `parse_range` (851), after `staged`, call `parse_apriltags` and add its warnings;
    - pull the box list out of `_unsupported` (822-848) into `solid_boxes(rng) -> list[tuple[str, np.ndarray,
      np.ndarray, np.ndarray]]`, as (name, center, rot, half), and add `perimeter_boxes(size)`, the four walls as
      `parts/field.py:46-51` builds them. `_unsupported`'s results must not change.
  - `scrimmage/testrange/build.py` `add_range` (330-331): after `add_zones`, `add_apriltags(spec, rng.apriltags)`
    when it isn't None. That is before the balls.
  - `scrimmage/testrange/check.py` `summary`: a part saying `{count} AprilTags ({layout})`, with `i18n.tn`.
  - `scrimmage/blocks/palette.py` `_targets` (98-108): leave out names starting `apriltag.`.
  - `scrimmage/sensors/runtime.py`: per geom, `_tag` (name starts `apriltag.`). `_cast` steps past a tag geom as it
    does past a robot geom, so no sensor reading changes.
  - `scrimmage/assembler/view.py`: `out["apriltags"] = [{id, face, quat, size}]` from the range, with `[]` without
    one.
  - `scrimmage/assembler/static/live3d.js`: a sprite label per `view.apriltags` entry (its ID, the page's theme
    text color), placed 3 cm out from its face. It is disposed in `clear()`.
- Docs: `docs/test-range.md`, a new "AprilTags" section after "Floor zones" (724). It covers the key, the built-in
  names, the frames (WPILib's field frame; +x out of the tag), the checks, the size and R6. It has a two-tag JSON
  example for a hand-made practice field.

**Produces (Task 6 uses these):**
- `TAG_36H11_SIZE_IN = 6.5  # source: FRC 2024 and later 36h11 tags`.
- `FIELD_MATCH = 0.05  # source: spec D5`, `OUTSIDE_REACH = 0.3  # source: spec D5`,
  `MOUNT_DEPTH = 0.10  # choice: R6`.
- `BUILTIN_LAYOUTS: dict[str, Path]`, keyed by the three file stems.
- `Tag(id: int, pos: tuple[float, float, float], quat: tuple[float, float, float, float], face: tuple[float, float,
  float])`, frozen. `quat` is (w, x, y, z), and the property `normal` is the rotated +x.
- `AprilTagLayout(name: str, tags: tuple[Tag, ...], length: float, width: float, size: float)`, with `size` in m.
  It has `ids` (sorted) and `tag(id) -> Tag`.
- `parse_apriltags(r: _Reader, base_dir: Path, field: tuple[float, float], boxes) -> tuple[AprilTagLayout | None,
  tuple[str, ...]]`. Its errors:
  - an unknown built-in name that isn't a file lists the built-ins;
  - bad JSON, or a missing `tags`, `ID`, `pose` or `field`, names the file and the tag;
  - a duplicate ID;
  - a field mismatch over FIELD_MATCH names both sizes;
  - a tag center outside the field by more than OUTSIDE_REACH.

  The warning is a tag deeper than MOUNT_DEPTH inside a box. Faces are set per R6: the depth along the normal is
  `min_i (sign(n_i) h_i - p_i) / n_i` in the box's frame.
- `add_apriltags(spec, layout)`: per tag, two thin boxes facing +normal, at the face point:
  - `apriltag.<id>`, black, the tag size square;
  - `apriltag.<id>.border`, white, `size * 10 / 8`, 1 mm behind the black one.

  Both have `contype=0, conaffinity=0, group=1`, as zone tape does.

- [ ] Add `range_2024_with_tags(tmp_path) -> Path` to `tests/range_fixtures.py`. It copies 2024.toml with
  `apriltags = "2024-crescendo"` added to `[range]`, since the built-in gets its tags only in Task 7. Tasks 5
  and 6 load "2024 with tags" through it.
- [ ] Write `test_2024_layout_reads_and_matches_the_range`: 16 tags. Tag 7 is at (-0.0381, 5.5479, 1.4511) with
  normal +x, and its y is within 0.01 m of `speaker_blue`'s 5.5446. Tag 8's y is 4.9827.
- [ ] Write `test_2024_mounted_tags_face_the_field` (Review Focus 1). Tag 7's face x is in (0, 0.002]: in front of
  `speaker_blue_wall`. Tags 11, 12, 15 and 16 face out of the stage cores. `rng.warnings == ()`.
- [ ] Write `test_the_layout_checks`, each a RangeError naming `[range]`:
  - a layout on a 16.54 x 8.07 field given to a 2024-size range names both sizes;
  - a tag at x = 17.0 on a 16.54 field;
  - an ID given twice;
  - a missing file, and `"2023"`, which lists the built-ins;
  - `apriltag_size_in = 0`.

  A tag 0.2 m outside a wall is fine. Use a two-tag JSON in tmp_path.
- [ ] Write `test_a_tag_deep_in_a_solid_is_warned_about`: a tag 0.2 m inside a box obstacle gives one warning naming
  the tag and the obstacle.
- [ ] Write `test_the_2026_layouts_fit_the_2026_field`: both load against a range with the 2026 default field.
- [ ] Write `test_tags_add_no_contacts_and_no_readings`:
  - a two-tag range builds with the tag geoms at contype and conaffinity 0;
  - `check.summary` lists "2 AprilTags";
  - a distance sensor pointed through a tag reads past it, the same as with no tags;
  - `palette._targets` has no `apriltag.` names.
- [ ] Write `test_the_view_labels_tags`: `model_view(...)["apriltags"]` has both IDs. Add a browser test beside
  the live view's tests: the live view shows one label per tag (`expect(...).to_have_count(2)` on the label hook
  `window.__scrimmage.live3d.tagLabels`).
- [ ] Implement, run `tests/test_range_*.py tests/test_sensors*.py`, run the full suite (the range goldens stay), and
  commit: `K4: AprilTag layouts in a range, drawn and checked`.

### Task 6: the camera sensor, its Test-tab check and robot-code vision (spec D6, D7)

**Files:**
- Create:
  - `scrimmage/sensors/camera.py`;
  - `tests/test_sensors_camera.py`;
  - `tests/test_blocks_camera.py`.
- Modify:
  - `scrimmage/sensors/spec.py`:
    - `KINDS` (44) gains `"camera"`;
    - `KEYS["camera"] = _COMMON + ("at", "at_in", "toward", "hfov_deg", "vfov_deg", "max_range", "max_range_in",
      "max_angle_deg", "fps")`;
    - `DEFAULT_MAX_RANGE["camera"] = 5.0`;
    - `SensorSpec` gains `hfov_deg`, `vfov_deg`, `max_angle_deg` and `fps` (`float | None = None`);
    - the defaults come with the spec's comments: `70  # estimate: an OV9281 with its stock lens`,
      `50  # estimate`, `5.0  # estimate: 6.5 in tags at 1280x800`, `60  # choice`, `30  # choice`;
    - checks: FOVs in (0, 180), `max_angle_deg` in (0, 90], `fps` in (0, 1000]. A `toward` within 1 degree of the
      body's z is an error ("a camera can't look straight up or down: up is the body's z").
  - `scrimmage/sensors/runtime.py`:
    - `Reading` gains `tags: tuple[int, ...] = ()` and `tag_distances: tuple[float, ...] = ()`, in the same order;
    - `SensorRuntime.__init__` (112) keeps camera specs out of its ray loop (`update`, 172). When there are cameras,
      it makes `self.cameras = CameraRuntime(world, cams, layout)`, with `layout = world.parts.range.apriltags`;
    - `update(data, force=False)` calls `self.cameras.update(data, force)`;
    - `state()` adds a camera's `tags`;
    - `sensor_updates` skips cameras.
  - `scrimmage/world.py` `_read_sensors` (319-323): `update(self.data, force=True)`.
  - `scrimmage/checks.py` `_sensor_signal` (228-266):
    - `sensor.<cam>` is 1 while it sees any tag;
    - `sensor.<cam>.tags` is the count;
    - `sensor.<cam>.sees_tag.<id>` is 0 or 1;
    - `sensor.<cam>.tag_distance.<id>` is m, or max_range while not seen.
    - An ID not in the layout is an error listing the layout's IDs. A camera on a range with no tags is an error
      that names `[range] apriltags`. Update `SIGNALS_HELP` (24-30).
  - `scrimmage/blocks/program.py`: the condition kind `sees_tag` (R9) in `KINDS` (69), parsed next to `sensor`
    (299). `tag` is a whole number of 0 or more, or left out. It goes through `_cond_json`, `to_json` and
    `from_json`.
  - `scrimmage/blocks/conditions.py`:
    - `Watch.holds` (424) checks `tag in reading.tags`, or any tag for none;
    - `describe` (660) gives "{camera} sees tag {tag}", "{camera} sees a tag" and their being/to-be forms.
  - `scrimmage/blocks/run_words.py` (250-320): the failure sentence, naming what it saw ("it saw tags 7, 8", or "it saw
    no tags").
  - `scrimmage/blocks/palette.py`:
    - `sensors()` (191) includes cameras;
    - the palette gains `"tags": layout.ids` (or `[]`);
    - `_sensor_problems` (389) refuses a `sensor` condition with `sees` on a camera;
    - a `sees_tag` problem for: no such camera, a sensor that isn't a camera, a range with no tags, and an ID not
      in the layout.
  - `scrimmage/blocks/run_setup.py` `sensor_value` (22): a camera's signal is its tag count.
  - `scrimmage/blocks/run.py`: `RunResult.cameras: dict[str, list[list]]` holds each camera's
    `[t, [ids]]`, recorded only when the set changes; `{}` without cameras.
  - `scrimmage/blocks/store.py`: save and load `cameras`.
  - `scrimmage/assembler/view.py` `_sensors` (111): a camera entry adds `hfov_deg`, `vfov_deg` and `max_range`.
  - `scrimmage/assembler/static/live3d.js`:
    - a camera draws its view cone (the four edges of its frustum, `max_range` long) in place of a line;
    - `showCameraTags(name, ids)` draws a line from the camera to each listed tag's face;
    - `sensorShapes()` adds `tags` for a camera.
  - `scrimmage/assembler/static/test_run.js` (435): at each replay time, `showCameraTags` from
    `rp.run.result.cameras`.
  - `scrimmage/assembler/static/blocks_defs.js`: a `sees_tag` block, "camera %1 sees %2", with a camera dropdown and
    "any tag" plus `tag N` choices from `p.tags`. It sits next to `sensor` (582) and is offered when `p.sensors` has
    a camera and `p.tags` isn't empty (718). Remember the camera (755).
  - `scrimmage/nt_reader.py`: `publish_camera_tags(name: str, ids: list[int]) -> None` publishes an IntegerArray
    `/MuJoCo/Camera/<name>/TagIds`. The topic is made on the first call and closed in `close()` (101-104).
  - `scrimmage/bridge.py` `_log_row` (713-720): with cameras, publish each camera's tags.
  - `scrimmage/locales/en-GB.json`: entries as needed.
- Docs:
  - `docs/sensors.md`:
    - the camera kind, with its key table rows (test_sensors_docs reads them) and a TOML example;
    - each estimate, and how to set it from a team's own camera (lens FOV from its datasheet, max_range from a test
      at the shop);
    - "In the Test tab" and "In scenarios and MCP" rows;
    - "What isn't simulated" (349-358): replace "Cameras and vision ... are not part of Scrimmage" with what the
      camera doesn't model (images, blur, lighting, noise, pose ambiguity, PhotonVision/Limelight NT);
    - a new "Vision in robot code" section: feeding PhotonLib's `VisionSystemSim` the robot pose from
      `/MuJoCo/GroundTruthPose` (Pose2d struct) and the same layout JSON, in Java and Python. Say plainly that the
      snippet isn't run in this repo's tests, since PhotonLib is a network vendordep.
  - `docs/test-tab.md` "Sensors" (467): the camera check.

**Interfaces:**
- Consumes: Task 5's `AprilTagLayout`, `Tag` (`pos`, `normal`, `face`), `TAG_36H11_SIZE_IN` and the `apriltag.`
  geom names.
- Produces:
  - `in_view(cam_pos: np.ndarray, forward: np.ndarray, up: np.ndarray, spec: SensorSpec, tag: Tag, size: float) ->
    float | None`: the distance when the FOV, range (R7) and angle tests pass, else None. It is pure, with no ray.
    - `up` is the body's z with its `forward` part removed, normalized.
    - Horizontal angle `atan2(d·left, d·forward)`, vertical `atan2(d·up, d·forward)`, both need `d·forward > 0`.
    - The angle test is `acos(normal · (cam - tag) / |...|) <= max_angle_deg`.
  - `CameraRuntime(world, specs, layout)`, with:
    - `update(data, force: bool = False)`: a frame runs when `force` is true, or when `data.time` is at least
      `1/fps` past its last frame;
    - `reading(name) -> Reading`;
    - `casts: int`;
    - with `layout is None`, every reading is empty and `update` returns at once.
  - The ray: `mj_ray` from the camera toward the tag's face point, `|face - cam| - 0.02` long, excluding the camera's
    body. It steps past robot geoms, `apriltag.*` geoms and the four `wall_*` geoms, as `SensorRuntime._cast` steps
    past robot geoms (MAX_CASTS). Anything else hit first blocks the view.

- [ ] Write the parse tests in `tests/test_sensors_camera.py`:
  - `test_camera_keys_and_defaults`: the spec example parses with its values. Left out, they default to
    70/50/5.0/60/30.
  - `test_camera_key_errors`: `hfov_deg = 0`, `fps = 0`, `max_angle_deg = 95`, `toward = [0, 0, 1]`, and `dio = 1` on
    a camera each name the key.
- [ ] Write `test_in_view_edges` (Review Focus 2), pure geometry on a tag at the origin facing +x:
  - a camera at (2, 0, 0) facing -x sees it at distance 2;
  - at (-2, 0, 0) facing +x it doesn't (behind);
  - at 60.0 degrees off the normal it does, and at 60.5 it doesn't;
  - at hfov/2 - 0.1 degrees off its own axis it does, and at hfov/2 + 0.1 it doesn't (and the same vertically);
  - at 5.0 m it does, and at 5.01 m it doesn't. With `size = 3.25 in`, 2.6 m doesn't (R7).
- [ ] Write the world tests on a small range: the generated field, a two-tag JSON (one on the blue wall at
  z = 1.45, one free-standing), a box element, and the stand-in robot fixtures from `tests/range_fixtures.py` with a
  camera:
  - `test_a_camera_sees_a_tag_and_reports_it`: the reading's `tags`, the signals, and `world.sensors.state()`;
  - `test_what_blocks_a_camera` (Review Focus 3):
    - the box between the camera and the tag blocks it;
    - a floor ball moved onto the line blocks it;
    - the robot's own geoms and the perimeter wall don't block it;
  - `test_a_camera_sees_tag_7_inside_its_wall` (Review Focus 1): on `range_2024_with_tags`, a robot at (2.5, 5.548, 180°) with a
    camera at z = 1.0 m sees 7 and 8;
  - `test_a_poke_reads_the_camera_again` (Review Focus 4): `set_pose` out of view, then the reading is empty with no
    step taken;
  - `test_a_camera_runs_at_its_fps`: over 1 s at fps 10 there are 10 frames (count `casts` changes), plus or minus
    one;
  - `test_no_camera_no_cost`: a robot with only a beam sensor on a tagged range has no `cameras`. A camera on a range
    with no tags makes no casts in 100 steps.
- [ ] Write `tests/test_blocks_camera.py`:
  - `test_the_sees_tag_condition_round_trips` (dumps, to_json, from_json);
  - `test_a_test_expects_a_tag_and_passes_or_fails`: its failure line names what it saw, with the same machine text
    in every Language (follow `test_blocks_sensors.py:129`);
  - `test_sees_tag_problems`: no camera, not a camera, a range with no tags, and tag 99;
  - `test_the_run_keeps_camera_changes`: `RunResult.cameras` changes only when the set does, and survives store and
    load.
- [ ] Write the NT tests in `tests/test_nt_and_recorder.py`, following `test_nt_publishes_ball_poses` (58):
  - `test_ground_truth_pose_topic_is_a_pose2d_struct`: the topic name and type, read through a struct subscriber
    after `publish_truth`. This is the D7 pin.
  - `test_camera_tag_ids_topic`: `/MuJoCo/Camera/front_cam/TagIds` is an int array with the ids. With no call there
    is no topic.
- [ ] Add a docs test in `tests/test_sensors_docs.py`: "Vision in robot code" names `/MuJoCo/GroundTruthPose`,
  `Pose2d` and `VisionSystemSim`, and says it isn't run here. The camera keys are in the tables.
- [ ] Write the browser tests in `tests/test_test_browser.py`, beside `test_the_sensor_check_*` (2190-2240):
  - the sees_tag block shows only for a robot with a camera on a tagged range, and round-trips;
  - a replay of a run that saw tags shows the camera's tag lines (`sensorShapes()` tags, via `expect` on a test hook);
  - the i18n page tests stay green.
- [ ] Implement, run `tests/test_sensors*.py tests/test_blocks_camera.py tests/test_nt_and_recorder.py
  tests/test_i18n*.py tests/test_units_machine_text.py`, run the full suite, and commit:
  `K4: a camera sensor that sees AprilTags, its Test-tab check, and vision in robot code`.

### Task 7: 2024's tags, the camera example, the how-to and the findings (spec D8)

**Files:**
- Modify:
  - `scrimmage/testrange/ranges/2024.toml`: `[range]` gains
    `apriltags = "2024-crescendo"   # source: WPILib v2026.2.2 2024-crescendo.json`. Update the header comment's
    mention of the tags.
  - `docs/test-range.md`:
    - "The 2024 field" (840) says it has its tags;
    - a new "From the field's CAD to a range" how-to after "The range file" (881). It covers: `scrimmage cad list`
      on the field STEP, finding pieces by `--match` and `--near`, telling a Y-up file from the list's sizes
      (`rpy_deg = [90, 0, 0]`), writing a `kind = "mesh"` element, checking it in `range view`, and that FIRST's
      CAD isn't shipped. It also covers the FreeCAD measuring route 2026.toml used for numbers like a goal's opening.
  - `docs/superpowers/specs/2026-11-08-kickoff-dry-run-findings.md`: a "**K4 status (2026-11-10).**" note under
    gaps 5, 6, 8 and 14. Gap 6 and most of 14 were fixed by the cleanup batch (K1); K4 adds names, the list, mesh
    elements, the tolerance finding (R2), the CAD density check, tags and the camera.
  - `docs/test-tab.md` "The examples" (858): the camera example.
- Create: `examples/blocks/camera/`:
  - a robot config copied from `examples/blocks/speaker/note-shooter-standin.toml` with one camera on the chassis
    (`body` from `scrimmage model`), `at` z about 0.9 m, toward +x pitched up about 10 deg;
  - `sees-speaker-tags.test.toml` that expects `sees_tag` 7 and 8 from the `blue_subwoofer` start;
  - a README for a student, saying why the camera is mounted high.
- Test: `tests/test_range_2024.py`:
  - `test_the_range_checks_clean` (41): its first line now also lists "16 AprilTags (2024-crescendo)", and still no
    warnings;
  - `test_the_camera_example_sees_tags_7_and_8`;
  - `test_a_low_camera_is_blocked_by_the_subwoofer` (Review Focus 3): the same robot with the camera at z = 0.25 m
    doesn't see tag 7 from the subwoofer;
  - every existing test's numbers unchanged.
- [ ] Implement. Run `tests/test_range_2024.py tests/test_range_ledger_golden.py tests/test_range_model.py
  tests/test_language_docs.py`, run the full suite, and commit:
  `K4: 2024's AprilTags, a camera example, and the CAD-to-range how-to`.
