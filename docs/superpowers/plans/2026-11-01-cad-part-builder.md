# CAD to Part, in the Browser: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Part tab in `scrimmage edit` that turns a STEP file, STL/OBJ files or a CAD plugin's part folder into
ordinary Scrimmage part folders with bodies, materials, mass, joints, motors, sensors, mounts and collision, and that
keeps the student's settings when the CAD changes.

**Architecture:** A new pure-Python package `scrimmage/cad/` does the work and knows nothing about the web app:
- read a source into pieces;
- compute mass properties;
- match pieces across re-imports;
- hold the student's settings in an *import file*;
- apply edits to it;
- build part folders from source plus settings.

The web app gets a `PartSession` (like `BenchSession`) behind `/api/part/*`, and a Part tab (`part.js`, `part3d.js`).
Plugin part folders are edited through an overlay file that `load_part` applies, so a plugin re-export never erases
browser edits.

**Tech Stack:**
- Python 3.11+ and numpy;
- `cascadio` (STEP to GLB, OpenCascade) and `trimesh` (GLB scene, mesh utilities) in a new optional extra `cad`;
- the existing vendored three.js;
- pytest and Playwright.

**Spec:** `docs/superpowers/specs/2026-11-01-cad-part-builder-design.md`

## Global Constraints

- Python `>=3.11`. Every new dependency has Windows, macOS and Linux wheels. New extra: `cad = ["cascadio>=0.1.1",
  "trimesh>=4.0"]`. Nothing outside `scrimmage/cad/source.py` imports `cascadio`. `trimesh` is imported only inside
  `scrimmage/cad/`, never by `scrimmage/parts/` or the plugin-overlay code path.
- With the `cad` extra missing, the Part tab and CLI say exactly: `Reading CAD files needs the cad extra: pip install
  "scrimmage[cad]"`.
- `part.toml` stays format 1 as documented in `docs/parts.md`. No new keys in `part.toml`.
- Files use SI units: meters, kilograms, degrees. Paths stored in files are relative, POSIX-style (`/`), or `{cad}/...`
  via `scrimmage.teamfolders`.
- American spellings. Plain words in every user-facing message: "pieces that move together", "turns", "slides". One
  sentence saying what's wrong and what to do.
- Default material: aluminum 6061, 2700 kg/m^3 (same as the FreeCAD exporter's `DEFAULT_DENSITY`).
- Hardware auto-merge never leaves a piece out. Leaving out is only ever the student's choice.
- Every edit is saved at once (no Save button), like the Robot and Bench tabs. Part folders rebuild in the background
  after each edit.
- Match the surrounding code: module docstrings that state the contract, comment density like
  `scrimmage/bench/cad.py`, and API contracts documented in `scrimmage/assembler/server.py`'s module docstring.

## Decisions made while planning (record in the spec's "Decisions made during the build")

- **No reuse of `tree.build()`.** The FreeCAD exporter needs a spanning tree because FreeCAD joints are an undirected
  graph. Here the student says each body's parent directly, so the tree is given, and loops and couplings are explicit
  entries. Only small helpers are shared: `sanitize` (copied to `scrimmage/cad/names.py`) and the
  `principal`/`rpy_deg` math. The math is ported to numpy in `scrimmage/cad/mass.py` and tested against the add-on's
  pure-Python copy.
- **The import file.** `<name>.import.toml`, saved where part folders go (`parts/` next to the robot config by
  default). A top-level `[import]` table makes `classify()` return `"import"`. It holds the source path, units, robot
  frame, per-piece settings (with fingerprints) and per-body settings. Geometric choices (joint anchors, axes, mount
  points) are stored in **CAD coordinates**, so changing the robot frame or re-importing doesn't invalidate them.
- **Output folders.** Each part from an import goes in `parts/<part name>/` with `part.toml`, meshes and an
  `import-origin.toml` marker:
  - `import = "<relative path to the import file>"`;
  - `import_id = "<uuid4 hex>"`;
  - `origins = { <body> = [x, y, z] }`: each body's origin in the robot frame at zero pose.
  The library still finds these folders, and the marker is what goal 10 places parts by.
- **Body frames.** Every body frame is aligned with the robot frame (no rotation). The root body's origin is the robot
  origin (floor, frame center). A child body's origin is its joint anchor. A joint's axis is the student's axis in
  the robot frame. So an import part attached to the stock chassis's root body (`<drive>.chassis`) at zero offset
  sits where the CAD has it.
- **Dropped files** are copied into `parts/<import name>.source/` next to the import file, and the import file points
  there. Files opened with Browse are referenced in place (with `{cad}` when under the team folder). **Import again**
  re-reads the referenced path. Dropping a new file onto an open import replaces its source.
- **Plugin overlay.** `scrimmage-edits.toml` inside a plugin part folder holds per-body overrides: mass, joint range,
  spring, damping, friction, collision, and added slots and mounts. `scrimmage.parts.model.load_part` applies it
  (`scrimmage/parts/overlay.py`, standard library and numpy only), and edits naming bodies that no longer exist come
  back as warnings. The FreeCAD add-on's folder swap keeps the file.
- **Up axis.** Inventor's default is Y-up. The robot frame stores `up` and `front` as one of `+x -x +y -y +z -z`. The
  default guess is `up = "+z"`, `front = "+x"`. The frame step offers one-click "Y is up".
- **"3D printed" density:** `rho_plastic * (0.25 + 0.75 * infill)` (a shell share of 25%), with PLA 1240 and PETG 1270
  kg/m^3, and infill defaulting to 0.4.
- **Background rebuild.** One worker thread per session. The latest edit wins. State reports `build: {state:
  idle|building|failed, message}`.

## Review Focus

1. **A Y-up STEP (Inventor's default):** the default frame puts the robot on its side. Setting `up = "+y"` must give
   a floor-standing part, with the center of gravity height measured along +y. Test in Task 5.
2. **Open or inside-out meshes** (a sheet-metal surface export, an STL with flipped winding): no crash. An inside-out
   closed mesh weighs the same as the correct one. An open piece gets zero volume and a checklist item. A body whose
   only mass is zero builds with a 0.001 kg placeholder and a checklist error, never a `PartError`. Tests in Tasks 1,
   8 and 9.
3. **Re-import where a jointed or slotted piece is gone:** the build still succeeds. The joint or slot whose body
   emptied is dropped with a "no longer in the CAD" message, and nothing else is lost. Test in Task 8.
4. **Paths with spaces, non-ASCII names and Windows separators:** stored as relative POSIX paths, loaded back
   correctly. Piece names like `Hook (Left) Ø8` become valid body names via `sanitize`. Tests in Tasks 2 and 5.
5. **Degenerate clicks for an axis** (a flat face picked for a hinge, a single triangle, zero-area triangles): a
   plain-words error, never a NaN axis written to a file. Test in Task 7.

---

## File Structure

```
scrimmage/cad/__init__.py      CadError; MISSING_EXTRA message
scrimmage/cad/names.py         sanitize(), unique()
scrimmage/cad/mass.py          MATERIALS, density(), mesh_inertial(), combine(), principal(), rpy_deg()
scrimmage/cad/source.py        Piece, Source, load_source(), guess_units()
scrimmage/cad/matching.py      fingerprint(), match()
scrimmage/cad/bodies.py        default_bodies(), is_hardware()
scrimmage/cad/frame.py         RobotFrame, default_frame(), to_robot()
scrimmage/cad/settings.py      ImportFile (read/write/validate the .import.toml)
scrimmage/cad/edits.py         apply_edit(settings, op, args) -> settings  (pure)
scrimmage/cad/axis.py          fit_axis()
scrimmage/cad/build.py         build_parts(), write_parts()
scrimmage/cad/checklist.py     checklist()
scrimmage/parts/overlay.py     apply_overlay() for plugin folders (no trimesh)
scrimmage/assembler/part_session.py   PartSession behind /api/part/*
scrimmage/assembler/static/part.js    Part tab: tree, inspector, checklist, frame step
scrimmage/assembler/static/part3d.js  Part tab 3D view: pieces, picking, CoG markers, try-it
docs/part-tab.md               user docs
tests/data/cad/                fixtures (STEP made once with FreeCAD by tests/make_cad_fixtures.py; STLs)
tests/test_cad_*.py            unit tests per module; tests/test_part_server.py; tests/test_part_browser.py
```

Modified:
- `pyproject.toml` (extra, package list);
- `scrimmage/parts/model.py` (`load_part` applies the overlay);
- `scrimmage/assembler/server.py` (mode `part`, classify `import`, routes, start page "New part from CAD", CoG in
  robot state);
- `scrimmage/assembler/static/index.html`, `app.js`, `start.js`, `library.js`, `view3d.js`, `app.css`;
- `scrimmage/assembler/recent.py` (kind `import`);
- `scrimmage/__main__.py` (`scrimmage edit` accepts `.step/.stp/.stl/.obj`);
- `freecad/Scrimmage/scrimmage_part/export.py` (`_install` keeps `scrimmage-edits.toml`);
- `.github/workflows/ci.yml` (Windows job; the `cad` extra in the test job);
- `README.md`, `docs/parts.md`, `docs/assembler.md`, `docs/getting-started/`.

---

### Task 1: Package, materials and mass properties

**Files:**
- Create: `scrimmage/cad/__init__.py`, `scrimmage/cad/names.py`, `scrimmage/cad/mass.py`
- Modify: `pyproject.toml` (extra `cad`, add `"scrimmage.cad"` to `packages`, add `cad` deps to `dev`)
- Test: `tests/test_cad_mass.py`

**Interfaces:**
- Produces:
  - `class CadError(ValueError)` (its message is user-facing). `MISSING_EXTRA: str` is the exact message in Global
    Constraints.
  - `sanitize(label: str) -> str`: `[A-Za-z0-9_]` only; other runs become `_`; a leading digit gets `p_`; empty
    becomes `piece`.
  - `unique(base: str, used: set[str]) -> str`: `base`, `base_2`, `base_3`…; adds the result to `used`.
  - `MATERIALS: dict[str, Material]`, where `Material(key, label, density, printed: bool)`. Keys:
    - `aluminum` 2700;
    - `steel` 7850;
    - `polycarbonate` 1200;
    - `hdpe` 950;
    - `plywood` 600;
    - `carbon_fiber` 1550;
    - `nylon` 1150;
    - `pla` 1240 (printed);
    - `petg` 1270 (printed).
    The label is the user-facing name, e.g. `"Aluminum 6061"`.
  - `density(material: str, infill: float | None = None, custom: float | None = None) -> float`: `material ==
    "custom"` needs `custom > 0`. Printed materials use `rho * (0.25 + 0.75 * infill)` with infill defaulting to 0.4.
    Unknown material or bad numbers raise `CadError`.
  - `mesh_inertial(v: np.ndarray, f: np.ndarray, density: float) -> Inertial`, where `Inertial(mass: float, com:
    np.ndarray(3), tensor: np.ndarray(3,3))` is about the com, in the mesh's frame, SI units. It uses signed
    tetrahedra from the origin, so an inside-out closed mesh gives the same result: if the signed volume is negative,
    flip it. Open meshes give `mass=0`.
  - `is_closed(f: np.ndarray) -> bool`: every edge shared by exactly two triangles.
  - `combine(items: list[Inertial]) -> Inertial` (parallel-axis).
  - `principal(tensor) -> tuple[np.ndarray(3), np.ndarray(3,3)]`: ascending moments, R proper (det +1).
  - `rpy_deg(R) -> tuple[float, float, float]`: same convention as the FreeCAD add-on's `mass.rpy_deg`.

- [ ] **Step 1: Write the failing tests**

```python
def test_box_volume_com_inertia():          # 0.3 x 0.2 x 0.1 m box centered at (1, 2, 3), aluminum
    inert = mesh_inertial(*box_mesh((0.3, 0.2, 0.1), center=(1, 2, 3)), 2700.0)
    assert inert.mass == pytest.approx(0.006 * 2700)
    assert inert.com == pytest.approx([1, 2, 3])
    assert inert.tensor[0, 0] == pytest.approx(inert.mass * (0.2**2 + 0.1**2) / 12)
    assert abs(inert.tensor[0, 1]) < 1e-12

def test_inside_out_box_weighs_the_same():   # faces reversed
def test_open_mesh_has_no_mass():            # box with one face removed -> mass 0, is_closed False
def test_combine_two_boxes_parallel_axis():  # two 1 kg cubes 1 m apart along x: Iyy = 2*(cube) + 2*0.5**2
def test_principal_and_rpy_match_freecad_addon():
    # import freecad/Scrimmage/scrimmage_part/mass.py by path (it imports FreeCAD lazily) and compare
    # principal()/rpy_deg() on 20 random symmetric positive-definite tensors (seeded)
def test_printed_density():                  # density("pla", 0.4) == 1240 * (0.25 + 0.3)
def test_custom_needs_positive_density():    # density("custom") -> CadError mentioning "density"
def test_sanitize():                         # sanitize("Hook (Left) Ø8") == "Hook_Left_8"; sanitize("8020") == "p_8020"
```

- [ ] **Step 2:** Run `pytest tests/test_cad_mass.py -v`. Expected: FAIL (`ModuleNotFoundError: scrimmage.cad`).
- [ ] **Step 3:** Implement the interfaces above. Use numpy only (no trimesh here).
- [ ] **Step 4:** Run `pytest tests/test_cad_mass.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit: `cad: materials and mesh mass properties`.

---

### Task 2: Reading sources (STEP, STL/OBJ) and fixtures

**Files:**
- Create: `scrimmage/cad/source.py`, `tests/make_cad_fixtures.py`, `tests/data/cad/climber.step`,
  `tests/data/cad/climber_v2.step`, `tests/data/cad/climber_yup.step`, `tests/data/cad/stl/` (STLs in mm)
- Test: `tests/test_cad_source.py`

**Interfaces:**
- Consumes: `CadError`, `MISSING_EXTRA`, `mesh_inertial`, `is_closed`.
- Produces:
  - `Piece(key: str, name: str, group: tuple[str, ...], v: np.ndarray (n,3) float64 meters in CAD coords, f:
    np.ndarray (m,3) int32)`.
    - `key` is the slash-joined path of names from the top (`"Climber/hook_assy/hook_A"`), with `#2`, `#3` added to
      repeated paths in file order.
    - `group` is the ancestors' keys, top first.
  - `Source(kind: "step"|"mesh", path: Path, units: str, pieces: list[Piece], groups: list[str])`.
  - `load_source(path: Path | list[Path], units: str | None = None) -> Source`:
    - `.step`/`.stp` go through cascadio to an in-memory GLB to `trimesh.load(..., force="scene")`. Nodes are walked
      with world transforms. Duplicate vertices are merged (`trimesh` `merge_vertices`) so closed solids are closed.
      Tessellation: `tol_linear=0.2` (in the file's units), `tol_angular=0.5`.
    - A list of `.stl`/`.obj` files (or a folder of them) goes through `scrimmage.bench.cad.read_mesh`, scaled by
      `units`. If `units` is None it uses `guess_units`.
    - `ImportError` of cascadio or trimesh raises `CadError(MISSING_EXTRA)`. An unreadable file raises `CadError`
      naming the file.
  - `guess_units(extent_max: float) -> str`, one of `"m"`, `"mm"`, `"in"`. The input is the largest bounding-box side
    of all pieces together in file units:
    - `< 5` is `"m"`;
    - `5..60` is `"in"`;
    - `> 60` is `"mm"`.
  - `UNITS = {"m": 1.0, "mm": 0.001, "in": 0.0254}`.

**Fixtures** (`tests/make_cad_fixtures.py`, run once with `freecad.cmd`; outputs committed; the script's docstring
says how to re-run):
- **climber.step**: an App::Part `Climber` containing:
  - `base_plate` (300×200×10 mm);
  - `arm_A` and `arm_B` (20×20×800, at x = 50 and 230);
  - two sub-assemblies `hook_assy` with identical `hook` cylinders (r 8, h 60, axis along x) at the arm tops;
  - six `bolt` cylinders (r 3, h 20) on the base.
- **climber_v2.step**: the same, except `arm_B` is 900 long, one bolt is removed, and `hook_assy` #2 is renamed
  `hook_assy_right`.
- **climber_yup.step**: `climber.step` rotated so +Y is up.
- **STLs**: `base_plate.stl`, `arm_A.stl` and `hook.stl` in mm, plus `hook_inside_out.stl` (reversed winding).

- [ ] **Step 1: Write the failing tests**

```python
def test_step_names_nesting_positions():
    s = load_source(DATA / "climber.step")
    keys = [p.key for p in s.pieces]
    assert "Climber/base_plate" in keys and "Climber/hook_assy/hook" in keys and "Climber/hook_assy#2/hook" in keys
    arm = piece(s, "Climber/arm_A")
    assert arm.v.min(axis=0) == pytest.approx([0.05, 0.09, 0.01], abs=1e-6)     # meters, in CAD coords
    assert mesh_inertial(arm.v, arm.f, 1000).mass == pytest.approx(0.02 * 0.02 * 0.8 * 1000, rel=1e-6)
    assert is_closed(arm.f)
def test_repeated_bolts_get_numbered_keys():   # "Climber/bolt", "Climber/bolt#2", ... "#6"
def test_stl_folder_units_guessed_mm():         # load_source(DATA/"stl").units == "mm"; base_plate extent 0.3 m
def test_units_override():                      # units="in" scales by 0.0254
def test_guess_units_thresholds():              # 0.8 -> m, 30 -> in, 800 -> mm
def test_missing_extra_message(monkeypatch):    # setitem(sys.modules, "cascadio", None) -> CadError(MISSING_EXTRA)
def test_unreadable_file_names_it(tmp_path):    # garbage .step -> CadError containing the file name
def test_path_with_spaces_and_unicode(tmp_path):  # copy climber.step to "my parts/Klättrare.step" -> loads
```

- [ ] **Step 2:** Run `pytest tests/test_cad_source.py -v`. Expected: FAIL.
- [ ] **Step 3:** Write `tests/make_cad_fixtures.py` (same snap-path trick as the spike: write under
  `~/snap/freecad/common/`, then copy), generate, and commit the fixtures. Implement `source.py`.
- [ ] **Step 4:** Run `pytest tests/test_cad_source.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit: `cad: read STEP and STL/OBJ sources`.

---

### Task 3: Fingerprints and re-import matching

**Files:** Create `scrimmage/cad/matching.py`. Test: `tests/test_cad_matching.py`.

**Interfaces:**
- Consumes: `Piece`, `mesh_inertial`.
- Produces:
  - `fingerprint(p: Piece) -> dict`: `{"volume": m^3 (6 significant digits), "area": m^2, "extents": sorted
    principal-axis extents [a<=b<=c] in m, "center": centroid [x, y, z] in m}`, all JSON/TOML-safe floats.
  - `match(old: list[dict], new: list[Piece]) -> Match`. `old` is the stored records `{"key", "fp"}`.
    `Match(pairs: dict[old_key, new_key], guessed: set[old_key], missing: list[old_key], added: list[new_key])`. Rules,
    in order:
    1. The exact key.
    2. Shape and position: the same name tail (last path segment, without `#n`), and volume within 2%, extents within
       2%, and the center within 5 mm. The closest center wins.
    3. Shape only: volume and extents within 2%, taking the nearest unmatched center. These go in `guessed`.
    A new piece is used once. Ties go by file order.

- [ ] **Step 1: Write the failing tests**

```python
def test_same_file_matches_every_key():
def test_v2_renamed_subassembly_matched_by_shape_and_position():
    # old "Climber/hook_assy#2/hook" -> new "Climber/hook_assy_right/hook"; not in guessed (name tail "hook" same)
def test_removed_bolt_is_missing_and_lengthened_arm_still_matches_by_key():
def test_identical_hooks_keep_their_sides():     # swapped file order in a synthetic list -> pairs by position
def test_shape_only_match_is_guessed():          # renamed AND moved 50 mm -> in guessed
```

- [ ] **Steps 2–5:** run (FAIL), implement, run (PASS), commit `cad: match pieces across re-imports`.

---

### Task 4: Default bodies and hardware

**Files:** Create `scrimmage/cad/bodies.py`. Test: `tests/test_cad_bodies.py`.

**Interfaces:**
- Consumes: `Source`, `Piece`, `sanitize`, `unique`.
- Produces:
  - `is_hardware(p: Piece, assembly_diag: float) -> bool`. True if either:
    - the name matches `(?i)\b(bolt|screw|nut|washer|rivet|standoff|spacer|shcs|bhcs|fhcs|nylock|pem|insert)\b`, or
      the McMaster-style `^\d{4,5}[A-Z]\d{1,4}`;
    - the largest extent is `< 0.01 * assembly_diag`.
  - `default_bodies(src: Source) -> dict[str, list[str]]`: body name to piece keys, in source order.
    - STEP: each child of the single top-level group is one body. If there are several top-level groups, each is one
      body. A loose top-level piece is its own body.
    - Mesh: each file is one body.
    - Hardware pieces then join the body whose pieces' bounding box contains the hardware centroid, or else the body
      with the nearest piece centroid.
    - Body names: `sanitize` of the group or piece name, made unique.

- [ ] **Step 1: Write the failing tests**

```python
def test_climber_default_bodies():
    b = default_bodies(load_source(DATA / "climber.step"))
    assert set(b) == {"base_plate", "arm_A", "arm_B", "hook_assy", "hook_assy_2"}
    assert all(k in b["base_plate"] for k in bolt_keys)        # bolts merged into the plate they sit on
def test_hardware_by_name_and_by_size():
def test_hardware_never_left_out():                             # every piece key appears in exactly one body
def test_stl_each_file_a_body():
```

- [ ] **Steps 2–5:** run (FAIL), implement, run (PASS), commit `cad: default bodies and hardware merge`.

---

### Task 5: Robot frame and the import file

**Files:** Create `scrimmage/cad/frame.py`, `scrimmage/cad/settings.py`. Test: `tests/test_cad_settings.py`.

**Interfaces:**
- Consumes: `Source`, `default_bodies`, `fingerprint`, `scrimmage.teamfolders` (portable paths).
- Produces:
  - `RobotFrame(up: str, front: str, floor: float, center: tuple[float, float] | None, skip: bool, confirmed:
    bool)`. `confirmed` starts False and `set_frame` sets it. `up` and
    `front` are each one of `+x -x +y -y +z -z`, and they must be perpendicular. `floor` is the CAD coordinate along
    `up`. `center` is the footprint center in the two remaining CAD coordinates.
    - `default_frame(src) -> RobotFrame`: `up="+z"`, `front="+x"`, floor = the lowest point along up, center = the
      middle of the bounding box across the other two axes.
    - `to_robot(frame) -> (R: 3x3, t: 3)` such that `robot = R @ cad + t`. `skip=True` means the identity.
  - `ImportFile`, a dataclass with `to_dict()`/`from_dict()` and these fields:
    - `name`, `import_id`, `source` (a portable path string), `units`, `frame: RobotFrame`;
    - `pieces: dict[key, PieceSettings]`;
    - `bodies: dict[name, BodySettings]`;
    - `couplings`, `loops`, `parts: dict[part name, PartSettings]`.
  - `PieceSettings(body: str, material="aluminum", infill=None, custom_density=None, mass=None, left_out=False,
    fp: dict)`.
  - `BodySettings(parent: str | None, joint: JointSettings | None, mass=None, collision="none" | "wheel" | "shaped" |
    "simple", part: str | None, motor: MotorSettings | None, encoder: EncoderSettings | None, gyro: bool, mounts:
    list[MountSettings])`.
    - `JointSettings(kind: "hinge" | "slide", anchor: [x, y, z] CAD m, axis: [x, y, z] CAD, range=None, spring=None
      (stiffness, rest), damping=0, frictionloss=0)`.
    - `MotorSettings(motor: str, count=1, ratio=1.0, spool_diameter=None m, invert=False, efficiency=1.0)`.
    - `EncoderSettings(on: "joint" | "motor", invert=False)`.
    - `MountSettings(name, point: [x, y, z] CAD, normal: [x, y, z] CAD)`.
  - `PartSettings(folder: str (portable path), total_mass=None)`.
  - `new_import(src: Source, name: str, source_path: Path, base: Path) -> ImportFile`: sets the defaults. All bodies
    hang on the first body with no joint (welded). That first body is the root of part `name`.
  - `read_import(path) -> ImportFile` and `write_import(imp, path)`. TOML is written with
    `scrimmage.parts.toml_writer._toml_value` style lines (a small `dumps_import` beside it). Read validates and
    raises `CadError` with the key path. Unknown keys are errors.
  - Reconciling with a new source: `reimport(imp, src) -> tuple[ImportFile, list[str]]`.
    - It uses `match()` to re-key `pieces`; new pieces go to their default body.
    - Bodies left with no pieces are removed, together with their joint, slots and mounts. Their children re-parent
      to the removed body's parent.
    - Every drop is a message like `"hook_B is no longer in the CAD; its joint and motor were dropped"`. Guessed
      matches give `"hook_B: matched by shape; check it"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_default_frame_z_up():                  # floor == 0.0 for climber.step, center == (0.15, 0.10)
def test_y_up_frame_puts_floor_on_y():          # climber_yup.step with up="+y": to_robot maps base plate to z in [0, 0.01]
def test_round_trip_import_file(tmp_path):      # write -> read == original (dataclass equality)
def test_unknown_key_is_an_error(tmp_path):     # extra "colour" key -> CadError naming "bodies.arm_A.colour"
def test_source_path_portable(tmp_path):        # source under a {cad} team folder written as "{cad}/..."; windows-style
                                                # relative input "sub\\climber.step" stored as "sub/climber.step"
def test_reimport_v2_messages():                # one bolt missing message; hook renamed -> no "check it" (name tail same)
def test_reimport_drops_joint_of_emptied_body():
    # imp with a hinge on body hook_assy_2, then a source without that body's piece -> joint gone, message names it,
    # every other body's settings unchanged
```

- [ ] **Steps 2–5:** run (FAIL), implement, run (PASS), commit `cad: robot frame and the import file`.

---

### Task 6: Edit operations

**Files:** Create `scrimmage/cad/edits.py`. Test: `tests/test_cad_edits.py`.

**Interfaces:**
- Consumes: `ImportFile` and its parts, `MATERIALS`, `scrimmage.motors.MOTORS`.
- Produces: `apply_edit(imp: ImportFile, op: str, args: dict) -> ImportFile`. Pure: it returns a new object. It
  raises `EditError(field: str, message: str)`. Ops and their `args`:

| op | args | rules |
|---|---|---|
| `set_material` | `pieces: [key]`, `material`, `infill?`, `custom_density?` | clears `mass` on those pieces |
| `set_mass` | `piece` or `body`, `kg: float \| None` | `kg > 0`, or None to clear |
| `move_pieces` | `pieces`, `body` | an emptied body is removed (as in reimport) |
| `new_body` | `pieces`, `name` | the parent is the pieces' old body |
| `merge_bodies` | `into`, `other` | `other`'s joint and slots are dropped; children re-parent |
| `rename_body` | `old`, `new` | name rules from `sanitize`; renames every reference |
| `leave_out` | `pieces`, `out: bool` | |
| `set_joint` | `body`, `parent`, `joint: JointSettings fields` or None | parent isn't a descendant; root can't have one |
| `set_motor` / `set_encoder` | `body`, settings or None | needs a joint; motor in `MOTORS`; `ratio > 0`; a slide needs `spool_diameter > 0` |
| `set_gyro` | `body`, `on: bool` | |
| `add_mount` / `remove_mount` | `body`, `name`, `point`, `normal` | names unique in the part |
| `set_collision` | `body`, mode | |
| `set_part` | `body`, `part: str \| None` | makes `body` the root of a new part, folder `parts/<part>` by default |
| `add_coupling` / `remove_coupling` | `a`, `b` (bodies with joints), `ratio != 0` | |
| `add_loop` / `remove_loop` | `body1`, `body2`, `point` | |
| `set_frame` | RobotFrame fields | up ⟂ front |
| `set_total` | `part`, `kg \| None` | |
| `set_units` | `units` | mesh sources only |

  Messages are plain words, e.g. `EditError("parent", "arm_A can't hang on hook_assy: hook_assy already hangs on
  arm_A")`.

- [ ] **Step 1: Write the failing tests:** one per row, covering its rule (for example
  `test_set_joint_refuses_a_cycle`, `test_slide_motor_needs_spool`, `test_rename_body_updates_couplings_and_parent`,
  `test_merge_drops_other_joint_and_reparents_children`, `test_set_part_makes_second_part`).
- [ ] **Steps 2–5:** run (FAIL), implement, run (PASS), commit `cad: edit operations on the import file`.

---

### Task 7: Fitting an axis from a click

**Files:** Create `scrimmage/cad/axis.py`. Test: `tests/test_cad_axis.py`.

**Interfaces:**
- Consumes: `Piece`.
- Produces: `fit_axis(p: Piece, triangle: int, kind: "round" | "flat") -> tuple[np.ndarray point, np.ndarray
  unit_dir]`, in CAD coordinates.
  - **`round`:** grow a region from the clicked triangle across edges whose two face normals differ by less than
    20°. The axis direction is the eigenvector of the smallest eigenvalue of `Σ n nᵀ` over the region's normals,
    weighted by area. The point is a least-squares circle fit (Kåsa) of the region's vertices projected on the
    plane ⟂ to the axis, then moved to the region's mid-height along the axis.
  - **`flat`:** grow coplanar triangles (within 2°). The direction is the area-weighted normal, and the point is the
    region's centroid.
  - Errors (`CadError`), all in plain words:
    - a region whose normals don't wrap a cylinder (smallest eigenvalue > 0.05 × largest) for `round`:
      `"that face isn't round; click inside a hole or on a round face"`;
    - zero-area triangles only;
    - a triangle index out of range.

- [ ] **Step 1: Write the failing tests:**
  - a cylinder mesh (r 8 mm along x) clicked on its side gives the x axis and a center within 0.1 mm;
  - a box with a through-hole clicked inside the hole gives the hole axis;
  - a flat face clicked for `round` gives the error;
  - a degenerate triangle gives the error;
  - the result is never NaN.
- [ ] **Steps 2–5:** run (FAIL), implement, run (PASS), commit `cad: fit an axis from a clicked face`.

---

### Task 8: Building and writing part folders

**Files:** Create `scrimmage/cad/build.py`. Test: `tests/test_cad_build.py`.

**Interfaces:**
- Consumes: everything above, plus `scrimmage.parts.model` (`Part`, `Body`, `Geom`, `Joint`, `MotorSlot`,
  `EncoderSlot`, `GyroSlot`, `Mount`, `Coupling`, `Loop`, `dump_part`), `scrimmage.bench.cad.convex_pieces` and
  `write_obj`.
- Produces:
  - `build_parts(imp: ImportFile, src: Source) -> BuildResult`. `BuildResult(parts: dict[part name, BuiltPart],
    messages: list[str])`. `BuiltPart(part: Part, meshes: dict[filename, (v, f)], origins: dict[body, [x, y, z]],
    com: [x, y, z] robot frame, mass: float)`. Rules:
    - **Frames:** a body's origin is its joint anchor (root: the robot origin), mapped by `to_robot`. The body's pos
      is the origin minus the parent's origin. `rot` is zero.
    - **Mass:** each body's pieces (not left out) give a mass from their density, or a typed piece mass (which keeps
      the piece's com and scales its inertia). A typed body mass scales the whole body's computed inertial. Then
      `total_mass` scales every computed (non-typed) mass in the part so the part totals `total_mass`. If typed masses
      already exceed it, that's a message and no scaling. A body with zero mass gets `mass=0.001` and a message.
      Write `com`, `inertia` (principal moments) and `inertia_rot`.
    - **Geoms:** one visual mesh per body, `collide=false`, at `meshes/<body>.obj`, in the body frame.
    - **Collision:**
      - `wheel`: a cylinder about the joint axis (the radius is the largest radial distance, the length is the extent
        along the axis), friction 1.2, condim 4. It needs a hinge.
      - `shaped`: `convex_pieces()` of the body mesh written as OBJ (meters), each piece a mesh geom with
        `collide=true`. A `BenchError` saying coacd is missing becomes a message, and the body falls back to `simple`.
      - `simple`: a box fitted to the body's oriented bounding box (a box geom with `rot` from its axes).
    - **Joints:** `Joint(name=body, kind, axis=R @ axis, range, spring, damping, frictionloss)`. A hinge range is in
      degrees and a slide range in meters, as stored.
    - **Slots:** motor slot `<body>_motor`. `gearing = ratio` for a hinge, `ratio / (spool_diameter / 2)` for a slide.
      Encoder slot `<body>_encoder`, `ratio = 1.0` on the joint, or for `on = "motor"`: `ratio` (hinge) or `ratio /
      (pi * spool_diameter)` (slide). Gyro slot `<body>_gyro`.
    - **Mounts:** the point maps to the body frame, and the normal becomes the mount's z (rot from the shortest-arc
      rotation of +z to the normal, as rpy).
    - **Couplings and loops** map one to one. A loop point maps to `body1`'s frame.
    - **Parts:** each `BodySettings.part` root starts a part made of its subtree. Bodies under a part root belong to
      that part, not the parent part. A joint whose parent is in another part becomes that part's root attachment and
      is dropped, with the message `"arm_A is its own part; attach it to <parent> in the robot"`.
  - `write_parts(result: BuildResult, imp: ImportFile, import_path: Path) -> list[Path]`: each part is written to
    `<folder>.tmp`, then swapped in (as `freecad export._install` does), with `import-origin.toml`. It refuses a target
    folder that holds files but no `import-origin.toml` naming this `import_id` (`CadError` "parts/arm already holds
    a part that didn't come from this import; pick another name").

- [ ] **Step 1: Write the failing tests**

```python
def test_default_import_builds_one_welded_part(tmp_path):
    # new_import(climber): every body welded (no joint) -> welded bodies merge into their parent at build time, so
    # one part with one Body; load_part(folder) works and part_spec(part) compiles
def test_mass_matches_aluminum_volume(): # total mass == 2700 * sum(volumes); com within 1e-6 of trimesh's combined com
def test_hinge_hook_moves_about_axis():
    # set_joint(hook_assy, parent arm_A, hinge, anchor/axis from fit_axis) -> compile with part_spec, set qpos 30 deg,
    # hook geom center rotates about the axis (check one vertex)
def test_slide_motor_gearing_and_encoder_ratio():   # ratio 25, spool 0.0254 m -> gearing == 25/0.0127
def test_total_mass_scales_computed_not_typed():
def test_y_up_part_stands_on_floor():               # climber_yup with up="+y": lowest geom z == 0 in the assembled model
def test_attach_to_stock_chassis_lands_where_cad_says(tmp_path):
    # robot config: chassis + this part attach="drive.chassis" -> arm_A's body world pos == its CAD position mapped
def test_wheel_collision_cylinder_dimensions():
def test_shaped_collision_uses_convex_pieces():     # pytest.importorskip("coacd"); hook body -> several collide meshes
def test_shaped_without_coacd_falls_back_with_message(monkeypatch):
def test_zero_volume_body_gets_placeholder_and_message():
def test_second_part_and_message():                 # set_part(arm_B, "right_arm") -> two folders
def test_refuses_foreign_folder(tmp_path):
def test_reimport_after_body_vanishes_still_builds():
```

- [ ] **Steps 2–5:** run (FAIL), implement, run (PASS), commit `cad: build and write part folders`.

---

### Task 9: The checklist

**Files:** Create `scrimmage/cad/checklist.py`. Test: `tests/test_cad_checklist.py`.

**Interfaces:**
- Consumes: `ImportFile`, `Source`, `BuildResult`.
- Produces: `checklist(imp, src, result) -> list[Item]`. `Item(level: "todo" | "check" | "error", text: str, target:
  {"kind": "piece" | "body" | "frame", "keys": [..]})`, in this order:
  - pieces still on the default material, one item per body listing the count: "12 pieces in arm_A are still
    guessed as aluminum";
  - pieces with no volume: "error, type a weight or leave it out";
  - guessed re-import matches ("check");
  - bodies welded to their parent (no joint), as a single "check" item listing them;
  - jointed bodies with no motor (each one "todo");
  - a frame that hasn't been confirmed (`frame.confirmed` false, set by `set_frame`);
  - the build's messages.

- [ ] **Steps 1–5:** tests that assert each item's text and target on the climber fixture after specific edits; run
  (FAIL), implement, run (PASS), commit `cad: what's-left checklist`.

---

### Task 10: Plugin overlay and the FreeCAD add-on

**Files:**
- Create: `scrimmage/parts/overlay.py`
- Modify: `scrimmage/parts/model.py` (`load_part`), `freecad/Scrimmage/scrimmage_part/export.py` (`_install`)
- Test: `tests/test_part_overlay.py`, an addition to `tests/test_freecad_tools.py` (FreeCAD-marked)

**Interfaces:**
- Produces:
  - `OVERLAY = "scrimmage-edits.toml"`.
  - `apply_overlay(part: Part, edits: dict, source: str) -> tuple[Part, list[str]]`. Keys:
    - `[body.<name>]`:
      - `mass`: scales com-preserving inertia by `mass / old`;
      - `collision`: one of `none`, `wheel`, `shaped`, `simple`, using the same rules as Task 8, with wheel and
        simple computed from the body's mesh geoms' vertices;
      - `[body.<name>.joint]`: `range`, `spring`, `damping`, `frictionloss`.
    - `[[motor]]`, `[[encoder]]`, `[[gyro]]`, `[[mount]]` are added, in the same shape as `part.toml`'s.
    - `total_mass` scales bodies whose mass isn't set in the overlay.
    - A missing body gives a warning: `"scrimmage-edits.toml: body hook_B is no longer in the part; its edits were
      dropped"`.
  - `load_part(path)` applies the overlay when the file exists. Warnings go through `warnings.warn` (shown by
    validation like other part warnings).
  - `freecad export._install`: when the old folder holds `scrimmage-edits.toml`, copy it into the new folder before
    the swap.
- Tests:
  - an overlay changes a FreeCAD fixture part's body mass and a joint range;
  - a missing body gives a warning, not an error;
  - `load_part` without an overlay is unchanged (byte-identical `part_to_dict`);
  - FreeCAD-marked: re-exporting into a folder holding `scrimmage-edits.toml` keeps it.
- [ ] **Steps 1–5:** run (FAIL), implement, run (PASS), commit `parts: plugin overlay that survives re-export`.

---

### Task 11: Server: PartSession and routes

**Files:**
- Create: `scrimmage/assembler/part_session.py`
- Modify: `scrimmage/assembler/server.py`, `scrimmage/assembler/recent.py`, `scrimmage/__main__.py`
- Test: `tests/test_part_server.py` (use the `Client` from `test_assembler_server`)

**Interfaces:**
- Consumes: all of `scrimmage/cad/`, plus `apply_overlay` for plugin folders.
- Produces:
  - **Opening.** `classify()` returns `"import"` for a `.toml` with a top-level `[import]` table, and for
    `.step/.stp/.stl/.obj` files and folders holding a `part.toml`. A folder with no `part.toml` stays `"session"`.
    The app mode is `"part"`.
    - Opening a source file asks for nothing. It creates `parts/<stem>.import.toml` next to it (or in the robot's
      parts folder when given), builds, and opens it.
    - Opening a part folder with `import-origin.toml` opens its import file.
    - Opening a part folder without one is plugin mode: the overlay is edited and only body-level ops are allowed.
  - **Routes**, documented in the server docstring in the existing style:
    - `GET /api/part/state[?since=N]`: `{version, mode: "import" | "plugin", name, source, units, frame, pieces: [{key,
      name, group, body, material, infill, mass, left_out, volume, hardware}], bodies: [{name, parent, joint, motor,
      encoder, gyro, mounts, collision, part, mass, com}], parts: [{name, folder, mass, com, total_mass}], checklist:
      [...], messages, build: {state, message}, can_undo, can_redo, read_only_reason}`. It long-polls like
      `/api/bench/state`.
    - `GET /api/part/view`: `{pieces: [{key, vert, face}]}`, base64 `<f4`/`<i4` as in `view.py._mesh`, in robot
      coordinates. Cached per source mtime.
    - `GET /api/part/materials`: `MATERIALS` and the motor names.
    - `POST /api/part/edit {version, op, args}` runs `apply_edit`. It returns 200 with state, 409 for a stale version,
      or 400 `{field, message}`.
    - `POST /api/part/undo`, `/redo`: a 50-step history of import files.
    - `POST /api/part/axis {piece, triangle, kind}` runs `fit_axis` and returns `{point, axis}` in CAD coordinates,
      or 400.
    - `POST /api/part/reimport` re-reads the source (same path).
    - `POST /api/part/upload` (raw body, header `X-Filename`): saves into `parts/<name>.source/` and re-imports. When
      sent from the start page with no import open, it creates the import there.
    - `POST /api/app/new {kind: "import", path}`: the start page's "New part from CAD" with a Browse-picked source.
  - **The build worker** is one thread per session. After each edit it runs `build_parts` and `write_parts` (latest
    wins) and bumps the version when done. `close()` joins it.
  - **Outside edits** of the import file show within 0.25 s, as the bench session does.
  - **Recent and browse:** `recent.py` gains kind `"import"`. Browse lists `.step/.stp/.stl/.obj` as kind `"cad"`.
  - **`scrimmage edit file.step`** opens the Part tab.

- [ ] **Step 1: Write the failing tests**

```python
def test_open_step_creates_import_and_builds(part_app):   # GET state: mode import, 5 bodies; build idle;
                                                          # parts/climber/part.toml exists and load_part works
def test_edit_material_saves_and_rebuilds(part_app):
def test_stale_version_409(part_app):
def test_bad_edit_400_with_field(part_app):
def test_axis_route(part_app):
def test_undo_redo(part_app):
def test_reimport_keeps_settings_and_reports(part_app):   # swap climber.step for climber_v2.step at the source path
def test_upload_creates_source_copy(part_app):
def test_plugin_folder_mode_edits_overlay(part_app):     # body ops only; piece ops -> 400 "per-piece materials need
                                                         # the STEP: import it instead"
def test_missing_extra_reported_not_crash(part_app, monkeypatch):
def test_part_threads_stop():                            # no "part-" thread outlives App.stop()
def test_view_is_robot_coordinates_after_frame_change(part_app):
def test_large_assembly_stays_quick(tmp_path):    # 400 small STL pieces (generated): open + state + view < 5 s locally
                                                  # (timed only when not on CI, per the CI-timing lesson)
```

- [ ] **Steps 2–5:** run (FAIL), implement, run (PASS), commit `edit: Part tab server side`.

---

### Task 12: Part tab front end: view, tree, materials

**Files:**
- Create: `scrimmage/assembler/static/part.js`, `scrimmage/assembler/static/part3d.js`
- Modify: `index.html` (tab button `tab-btn-part`, panel `tab-part`), `app.js` (mode `part`), `app.css`, `start.js`
  ("New part from CAD" with Browse and a drop zone)
- Test: `tests/test_part_browser.py` (fixtures from `test_assembler_browser`)

**Behavior:**
- **The layout** follows the approved mockup `part-builder.html`:
  - left, the tree (bodies, then their pieces; hardware folded under "+N hardware") and the checklist;
  - middle, the 3D view;
  - right, the inspector.
- **3D view.** Orbit, pan and zoom like `view3d.js`. Click selects a piece, and Shift-click adds to the selection. A
  body's pieces share a color. Left-out pieces are hidden, and a "Show left out" toggle shows them faint. The
  centers of gravity are orange dots: one per body, and a larger one for the part. Large views stay smooth: one merged
  `BufferGeometry` per body for drawing, with picking by a triangle-to-piece index.
- **Tree drag-and-drop** moves pieces between bodies. "New body from selection" and "Leave out" are buttons that act
  on the selection.
- **Inspector for pieces:** the material select (the label with density) with an infill field for printed materials,
  and "or type a weight". It applies to the whole selection.
- **Inspector for a body:** its name (rename), total mass, center of gravity and "Its own part".
- **The status line** shows "Parts up to date" or "Updating parts…" from `build.state`, with a failure message when
  there is one.
- **Drop a file anywhere** on the tab to replace the source. "Import again" re-reads it.
- **Test hook:** `window.__scrimmagePart` exposes the store and `screenPos(pieceKey)`.

- [ ] **Step 1: Write the failing browser tests:**
  - opening `climber.step` shows 5 bodies in the tree;
  - clicking the `arm_A` piece in 3D selects its tree row;
  - setting the selected pieces to steel shows a higher mass, and the import file on disk has `material = "steel"`;
  - dragging a bolt row onto `arm_A` moves it;
  - "Leave out" hides the piece;
  - the checklist shows "still guessed as aluminum" until the materials are set;
  - the page passes the existing all-screen contrast test in light and dark themes (add the Part tab to that test's
    list).
- [ ] **Steps 2–5:** run (FAIL), implement, run (PASS), commit `edit: Part tab view, tree and materials`.

---

### Task 13: Part tab front end: frame, joints, slots, mounts, collision, try it

**Files:** Modify `part.js`, `part3d.js`, `app.css`. Test: additions to `tests/test_part_browser.py`.

**Behavior:**
- **"Where's the robot?" step**, shown as a banner until confirmed. The view draws the floor grid at `floor` and a
  "front" arrow, with buttons "Y is up", "Z is up", "Turn front 90°", "Click the floor" (picks a flat face; its
  height becomes `floor`), "Click the frame's center" and "Looks right" (confirms). "This isn't a whole robot" sets
  `skip`.
- **Joint editor** for the selected body: "Moves: no (welded) / turns / slides" and "relative to [parent select]".
  - "Pick the axis" puts the 3D view in pick mode: a hint, then a click calls `/api/part/axis` with `round` for turns
    or `flat` for slides. "Snap to X / Y / Z" is available.
  - The axis is drawn as an arrow. The range (from/to), spring, damping and friction are under "More".
- **Try it:** a slider per joint moves the body (and its descendants) in the view only, from the joint data,
  client-side. When there's no range it covers ±180° or ±0.5 m.
- **Motor:** the motor select, count, gear ratio, and spool diameter with an in/mm toggle (stored in m). **Encoder:**
  none / on the joint / on the motor. **Gyro** checkbox.
- **Mounts:** "Add mount" puts the view in pick mode (flat face, then a name).
- **Collision:** a select with plain words: "Only shows (doesn't touch anything)", "A wheel", "Touches things, as
  shaped", "Touches things, as a simple box". A body named like a hook (`hook`, `latch`, `claw`) or a wheel
  (`wheel`, `tread`, `roller`) shows a one-click suggestion ("This looks like a hook: make it touch things as
  shaped?"), which is never applied on its own.
- **Couplings and loops** are under the body's "More" ("turns with another joint", "pinned to another body").

- [ ] **Step 1: Write the failing browser tests:**
  - "Y is up" on `climber_yup.step` gives a floor grid under the base plate (check via the store's frame), and "Looks
    right" removes the banner;
  - making `hook_assy` turn on `arm_A`, picking the axis by clicking the hook's side, and moving the Try-it slider
    changes the hook's screen position;
  - adding a NEO to `arm_A` at ratio 25 with a 1 in spool writes gearing 1968.5 (approx) to `part.toml` after the
    build;
  - setting collision to wheel writes a cylinder geom.
- [ ] **Steps 2–5:** run (FAIL), implement, run (PASS), commit `edit: Part tab joints, slots, mounts and frame`.

---

### Task 14: Assembler integration: Import CAD, auto-placement, whole-robot center of gravity

**Files:**
- Modify: `scrimmage/assembler/server.py` (robot state gets `com`), `scrimmage/assembler/edits.py` (add-part offset),
  `static/library.js` ("Import CAD..." button), `static/app.js` (bottom bar), `static/view3d.js` (CoG marker toggle)
- Test: `tests/test_assembler_server.py`, `tests/test_assembler_browser.py` additions

**Interfaces and behavior:**
- **Robot state** gains `com: {height, forward, left} | null`, in meters. It is `subtree_com` of the robot body,
  expressed in the robot body's frame (the chassis origin is on the floor at the frame center). The bottom bar shows
  `CG: 0.31 m up, 0.02 m forward`. A "CG" toggle draws an orange marker in the 3D view.
- **Adding a folder part that has `import-origin.toml`:**
  - when the attach target is the stock chassis's root body, the offset is zero (that's already right by the
    body-frame convention; the dialog preselects that body);
  - when the target is body B of another part Q with the same `import_id`, the offset is `-origins[B]`, read from
    Q's marker. This assumes Q itself sits at the CAD position;
  - the Placement panel shows "Placed where the CAD has it", with a "Reset to CAD" button.
- **Library:** "Import CAD..." opens the start page's "New part from CAD" flow with the robot config's `parts/` as the
  target. After the import, "Back to the robot" reopens the robot config.

- [ ] **Step 1: Write the failing tests:**
  - state `com.height` for the kitbot is between 0.05 and 0.4;
  - adding two parts from one import (climber split into `base` and `right_arm`), the second attached to the first's
    `arm_B` body, lands each body at its CAD position (compare world positions after `assemble`);
  - a browser test that the CG text appears and the toggle adds the marker.
- [ ] **Steps 2–5:** run (FAIL), implement, run (PASS), commit `edit: import CAD from the library, place by CAD, show
  the robot's CG`.

---

### Task 15: Docs, getting started, CI

**Files:**
- Create: `docs/part-tab.md` (style of `docs/assembler.md`: starting it, the three columns, where's the robot, bodies,
  materials and weight, joints, motors and sensors, mounts, collision, the checklist, importing again, plugin parts,
  troubleshooting including the missing extra and Y-up)
- Modify:
  - `docs/getting-started/` (a new short guide `bring-your-cad-in.md`, linked from `README.md` there; covered by
    `tests/test_getting_started_docs.py`);
  - `README.md` ("Assembling a robot in the browser" gains a "From CAD" paragraph; Layout lists `scrimmage/cad/` and
    `docs/part-tab.md`);
  - `docs/parts.md` (a "From CAD" note pointing at the Part tab and `scrimmage-edits.toml`);
  - `docs/freecad.md` (Re-exporting: the edits file is kept);
  - `.github/workflows/ci.yml`:
    - the `test` job installs `.[dev,nt,mcp,cad,bench]`;
    - a new `windows` job (`windows-latest`, Python 3.11 and 3.13) runs `pip install -e ".[dev,cad,bench]"` and
      `pytest tests/test_cad_*.py tests/test_part_overlay.py tests/test_part_server.py -v` with
      `SCRIMMAGE_SKIP_BROWSER=1`.
  - The spec: record every planning decision above and any made during the build in "Decisions made during the
    build".
- [ ] **Step 1:** Write the docs and the CI job. Run `pytest tests/test_getting_started_docs.py -v` (PASS), then the
  full suite locally: `SCRIMMAGE_SKIP_JAVA=1 pytest -q` (PASS).
- [ ] **Step 2:** Commit `docs and CI for the Part tab`. Push the branch, open the PR, and watch CI (including the new
  Windows job) until green.
