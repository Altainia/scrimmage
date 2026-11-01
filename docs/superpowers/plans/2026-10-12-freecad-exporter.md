# FreeCAD Exporter (sub-project 4b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A FreeCAD workbench add-on turns a mechanism Assembly into a Scrimmage part folder (`part.toml` +
meshes), with a panel for motors, sensors, mounts, mass overrides and contact marks, and a headless export used by
tests and a Linux CLI.

**Architecture:** `freecad/Scrimmage/` is a pure-Python FreeCAD workbench. Its export pipeline is plain functions
(`read` → `tree` → `mass`/`mesh` → `export`) that run headless in `freecad.cmd`; the panel only edits a hidden
`ScrimmagePart` settings object and calls them. It writes `part.toml` with a byte-identical copy of Scrimmage's
stdlib-only writer. Scrimmage gains `[[loop]]` and `inertia_rot` (format 1, additive), `scrimmage freecad
install|zip|export`, and tests that drive the add-on through `freecad.cmd`.

**Tech Stack:** FreeCAD 1.1 (Assembly workbench, Part, Mesh), Python 3.12 inside FreeCAD; Scrimmage (Python,
MuJoCo 3.15, pytest).

**Spec:** docs/superpowers/specs/2026-10-12-freecad-exporter-design.md

## Global Constraints

- The add-on imports only FreeCAD modules (`FreeCAD`, `Part`, `Mesh`, `JointObject`, `UtilsAssembly`, `FreeCADGui` in GUI files only) and the Python standard library. Never `scrimmage`, never numpy.
- Units: FreeCAD lengths are mm, masses kg; `part.toml` is meters, kg, degrees. Convert at the boundary only.
- `part.toml` stays `format = 1`; `[[loop]]` and `inertia_rot` are additive keys.
- `freecad/Scrimmage/scrimmage_part/toml_writer.py` is byte-identical to `scrimmage/parts/toml_writer.py`; `freecad/Scrimmage/scrimmage_part/motors.py` lists exactly `scrimmage.motors.MOTORS` names. `freecad/sync.py` regenerates both.
- FreeCAD's "Default" material (density ≈ 1e-9 kg/mm³) counts as no material: use aluminum 2700 kg/m³.
- Settings are keyed by FreeCAD internal object names (`obj.Name`), never labels.
- Every export problem is collected and raised together as `ExportError(problems: list[str])`; nothing is written on error; output is written to a temp folder then renamed; an existing `out_dir` is replaced only if it contains `part.toml`.
- FreeCAD binary lookup order: `$SCRIMMAGE_FREECAD_CMD`, `freecad.cmd` (snap), `FreeCADCmd`, `freecadcmd`. Tests needing it are marked `freecad` and skipped when none is found or `SCRIMMAGE_SKIP_FREECAD=1`.
- Headless FreeCAD under the snap can only read/write inside `~/snap/freecad/` and `/tmp`-like dirs the snap allows; test harness copies inputs to and reads outputs from a directory under `~/snap/freecad/common/scrimmage-test/` (created and cleaned by the harness) — verify writable locations in Task 2 and record them.
- Python is `.venv/bin/python`; tests `SCRIMMAGE_SKIP_JAVA=1 .venv/bin/python -m pytest`.

## Review Focus

1. A student's Assembly whose joint `Placement1/2` frames are expressed relative to the referenced sub-object, not the component origin, must still produce the right joint axis (test with a joint referencing a face of a component that is itself placed and rotated). (Task 4.)
2. A component with FreeCAD's "Default" material must get aluminum density, not ~0 mass. (Task 5.)
3. Re-exporting after deleting a joint must warn and drop its slot, not crash or move it. (Task 6.)
4. A four-bar must export with exactly one `[[loop]]` and the remaining three joints in the tree, and its coupler must move correctly when the crank is driven. (Tasks 4 and 6.)
5. An exported part must load in Scrimmage on a different machine path (meshes relative to the part folder; no absolute paths in `part.toml`). (Task 6.)

---

### Task 1: Scrimmage side: `[[loop]]`, `inertia_rot`, stdlib writer, root offset

**Files:**
- Create: `scrimmage/parts/toml_writer.py`
- Modify: `scrimmage/parts/model.py`, `scrimmage/parts/assemble.py`, `scrimmage/world.py`, `scrimmage/mcp_server.py` (set_pose docstring), `README.md` (set_pose row), `docs/parts.md`
- Test: `tests/test_part_model.py`, `tests/test_assemble.py`, `tests/test_part_ids.py` (or a World test file)

**Interfaces:**
- Produces: `scrimmage/parts/toml_writer.py` with `dumps(d: dict) -> str` (moved `_dumps`/`_toml_value` from model.py, stdlib only; `model.dump_part` uses it); `Loop(body1: str, body2: str, pos: tuple[float,float,float])` dataclass and `Part.loops: list[Loop]`; `Body.inertia_rot: tuple[float,float,float] | None`.

- [ ] **Step 1: Failing tests.** In `tests/test_part_model.py`:

```python
def test_loop_and_inertia_rot_round_trip(tmp_path):
    d = minimal()
    d["body"].append({"name": "link", "parent": "base", "mass": 0.2, "geom": [{"box": [0.1, 0.02, 0.02]}],
                      "joint": {"name": "j2", "hinge": "y"}})
    d["body"][1].update(com=[0.1, 0, 0], inertia=[0.001, 0.002, 0.003], inertia_rot=[0, 0, 30])
    d["loop"] = [{"body1": "arm", "body2": "link", "pos": [0.3, 0, 0]}]
    p = part_from_dict(d, "x.toml", None)
    assert p.loops[0].body1 == "arm" and p.loops[0].pos == (0.3, 0.0, 0.0)
    assert p.bodies[1].inertia_rot == (0.0, 0.0, 30.0)
    dump_part(p, tmp_path / "part.toml")
    assert load_part(tmp_path) == p.__class__(**{**p.__dict__, "base_dir": tmp_path})


@pytest.mark.parametrize("change, message", [
    (lambda d: d.update(loop=[{"body1": "arm", "body2": "arm", "pos": [0, 0, 0]}]), r"loop.*two different bodies"),
    (lambda d: d.update(loop=[{"body1": "arm", "body2": "nope", "pos": [0, 0, 0]}]), r"loop.*no body 'nope'"),
    (lambda d: d["body"][1].update(inertia_rot=[0, 0, 30]), r"inertia_rot needs inertia"),
])
def test_loop_and_inertia_rot_errors(change, message):
    d = minimal()
    change(d)
    with pytest.raises(PartError, match=message):
        part_from_dict(d, "x.toml", None)


def test_writer_is_stdlib_only():
    import ast, pathlib, sys
    src = pathlib.Path("scrimmage/parts/toml_writer.py").read_text()
    mods = {a.name.split(".")[0] for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= set(sys.stdlib_module_names) | {"__future__"}, mods
```

(Adapt `minimal()` to the existing helper; the round-trip assertion may compare with the same `same_part`
helper the kitchen-sink test uses.)

In `tests/test_assemble.py`, a hand-written four-bar `part.toml` (ground, crank, coupler, rocker; hinges about
y at the ground pivots and the crank–coupler pin; a `[[loop]]` pinning coupler and rocker at their shared point):
assert the compiled model has one `mjEQ_CONNECT` equality between `fb.coupler` and `fb.rocker`; drive the crank
joint to +20° by setting qpos and stepping a few hundred steps with gravity off and the crank held (or by
`mj_forward` after solving with a few `mj_step`s); assert the loop's two anchor points coincide within 1 mm and
the rocker angle matches the closed-form four-bar output angle (write a small `fourbar_output(a, b, c, d, theta)`
helper in the test) within 1°. And: `inertia_rot` sets the body's inertial orientation (`model.body_iquat`
matches `quat_from_rpy`).

In a World test: a path part whose root body has `pos = [0.3, 0, 0.1]` and `rot = [0, 0, 45]` attached as the
root with `start_pose = [2, 4, 30]`: `set_robot_pose(2, 4, 30)` reproduces the post-`reset()` root pose
(position within 1e-9, quaternion within 1e-9).

- [ ] **Step 2: Run, see them fail.**

- [ ] **Step 3: Implement.**
  - `toml_writer.py`: move the writer verbatim (keep its escapes, including DEL); `model.py` imports `dumps`.
  - `model.py`: `Loop` dataclass; `LOOP_KEYS = ("body1", "body2", "pos")`; parse `[[loop]]` after bodies
    (both bodies exist, differ; `pos` 3 finite numbers); `inertia_rot` in `BODY_KEYS`, requires `inertia`;
    `part_to_dict` writes both (omit when absent); top-level key list gains `"loop"`.
  - `assemble.py`: in `_fill`, `sb.iquat = quat_from_rpy(b.inertia_rot)` when set (explicit inertial already);
    after attaching each instance, for each loop:
    `spec.add_equality(type=mujoco.mjtEq.mjEQ_CONNECT, name1=f"{inst}.{body1}", name2=f"{inst}.{body2}",
    objtype=mujoco.mjtObj.mjOBJ_BODY, data=[*pos, 0, 0, 0, 0, 0, 0, 0, 0], solref=[0.002, 1])` (data[0:3] is the
    anchor in body1's frame; MuJoCo computes body2's anchor from qpos0 — verify with `dir(mujoco.MjsEquality)`
    and record the exact spelling).
  - Root offset (4a ruling R11): `root_offset` = instance offset ∘ the root body's own `pos`/`rot`
    (compose: `pos = inst.pos + R(inst.rot)·body.pos`, `quat = inst.rot · body.rot`).
  - Docs (4a ruling R12): MCP `set_pose` docstring and README's set_pose row say that for a parts robot the pose
    given is where the start-pose frame goes (`get_state` reports the root body, which may be offset).
  - `docs/parts.md`: `[[loop]]` (keys, meaning: a pin; the tree joints plus the pin keep a planar linkage's
    motion; use for four-bars) and `inertia_rot` (replace the 4a "diagonal in the body frame" wording).

- [ ] **Step 4: Run** the new tests, then `SCRIMMAGE_SKIP_JAVA=1 .venv/bin/python -m pytest -q`.
- [ ] **Step 5: Commit** `"part.toml: [[loop]] pins and inertia_rot; stdlib writer; root-body offset in set_pose"`.

---

### Task 2: Add-on skeleton, sync, FreeCAD runner, install and zip

**Files:**
- Create: `freecad/Scrimmage/package.xml`, `freecad/Scrimmage/Init.py`, `freecad/Scrimmage/InitGui.py`,
  `freecad/Scrimmage/icons/scrimmage.svg`, `freecad/Scrimmage/scrimmage_part/__init__.py`,
  `freecad/Scrimmage/scrimmage_part/toml_writer.py` (generated), `freecad/Scrimmage/scrimmage_part/motors.py`
  (generated), `freecad/sync.py`, `scrimmage/freecad_tools.py`, `tests/freecad_harness.py`,
  `tests/test_freecad_tools.py`
- Modify: `scrimmage/__main__.py` (`freecad` subcommand), `tests/conftest.py` (`freecad` marker, skip),
  `pyproject.toml` (package-data must NOT include the add-on; the add-on ships from the repo checkout — instead
  `freecad_tools` locates it relative to the repo; if Scrimmage is installed non-editable, include
  `freecad/Scrimmage/**` as data under `scrimmage/freecad_addon/` via a build step — pick the simplest that
  works for an editable install and a wheel, and test both paths that you choose).

**Interfaces:**
- Produces: `freecad_tools.addon_dir() -> Path`; `find_freecad() -> str | None` (lookup order in Global
  Constraints); `mod_dirs() -> list[Path]` (snap `~/snap/freecad/common/Mod`, Flatpak
  `~/.var/app/org.freecad.FreeCAD/data/FreeCAD/Mod`, `~/.local/share/FreeCAD/Mod`; first that exists, else the
  last); `install(to: Path | None) -> Path`; `make_zip(out: Path) -> Path`; `run_freecad(script: str, args:
  list[str], timeout: float) -> subprocess.CompletedProcess` (writes the script into a snap-readable work dir,
  sets `sys.argv`-equivalent via an env var or a JSON args file, puts the add-on folder on FreeCAD's path, returns
  stdout with FreeCAD's banner/progress noise stripped); `FreeCADError`.
  `scrimmage freecad install [--to DIR]`, `scrimmage freecad zip [-o FILE]` (export comes in Task 6).
  `tests/freecad_harness.py`: `needs_freecad` (pytest mark + skip) and `run_in_freecad(code: str, files: dict) ->
  dict` (runs code that prints one JSON line prefixed `SCRIMMAGE_JSON:`; returns it parsed; raises with FreeCAD's
  output on failure).

- [ ] **Step 1: Probe and record** where headless `freecad.cmd` (snap) can read and write: run a one-line
  script that writes to `/tmp/x`, `~/snap/freecad/common/x`, and the repo; record results in the report and set
  the harness work dir accordingly (default `~/snap/freecad/common/scrimmage-work/<uuid>`, removed afterwards).
- [ ] **Step 2: Failing tests** (`tests/test_freecad_tools.py`):
  - `test_addon_copies_in_sync`: `freecad/Scrimmage/scrimmage_part/toml_writer.py` bytes == `scrimmage/parts/toml_writer.py` bytes; `motors.py`'s `MOTOR_NAMES` == `sorted(scrimmage.motors.MOTORS)`; the add-on's Python files import nothing outside FreeCAD's modules and the stdlib (AST scan; allowed: stdlib, `FreeCAD`, `FreeCADGui`, `Part`, `Mesh`, `MeshPart`, `JointObject`, `UtilsAssembly`, `Materials`, `PySide`, and relative imports).
  - `test_install_copies_addon(tmp_path)`: `install(tmp_path)` creates `tmp_path/Scrimmage/InitGui.py`; installing twice replaces cleanly.
  - `test_zip_has_workbench(tmp_path)`: the zip's top folder is `Scrimmage/` and contains `package.xml`.
  - `test_cli_install_and_zip(tmp_path, capsys)`: `main(["freecad", "install", "--to", str(tmp_path)])` prints the path; `zip -o` writes the file.
  - `@needs_freecad test_addon_loads_in_freecad`: `run_in_freecad("import scrimmage_part, json; print('SCRIMMAGE_JSON:' + json.dumps({'ok': True}))")` returns `{"ok": True}`.
- [ ] **Step 3: Implement** the files. `package.xml` per FreeCAD's addon format (name Scrimmage, workbench
  classname `ScrimmageWorkbench`, icon, FreeCAD min 1.0). `InitGui.py` defines the workbench with one command
  `Scrimmage_Part` whose `Activated` imports `scrimmage_part.panel` lazily (panel arrives in Task 7; until then show a
  message box "not implemented yet" — it must not import Qt at module import time for headless use).
  `freecad/sync.py` regenerates the two copied files (`toml_writer.py` copied verbatim; `motors.py` as
  `MOTOR_NAMES = (...)` from `scrimmage.motors.MOTORS`).
- [ ] **Step 4: Run** the tests (the `freecad` one runs here), the full non-Java suite.
- [ ] **Step 5: Commit** `"FreeCAD add-on skeleton, sync, install/zip, headless runner"`.

---

### Task 3: Fixture builder

**Files:**
- Create: `tests/freecad/make_fixtures.py` (runs inside FreeCAD), `tests/freecad/__init__.py` (empty) if needed,
  `tests/test_freecad_fixtures.py`

**Interfaces:**
- Produces: `make_fixtures.py` callable as `build(name: str, path: str)` inside FreeCAD, and a pytest session
  fixture `fixtures_dir` (in `tests/freecad_harness.py` or conftest) that builds every fixture once per session
  into the harness work dir and returns `{name: path}`.

Fixtures (all mm, built with `Part::Box`/`Part::Cylinder`, an `Assembly::AssemblyObject`, a
`Assembly::JointGroup`, `JointObject.GroundedJoint(obj, part)` and `JointObject.Joint(obj, JointTypes.index(t))`
with `Reference1/2 = [asm, ["<Comp>.<SubElement>", "<Comp>.<Vertex>"]]` and `Placement1/2` set so the joint is
already satisfied — the probe showed the solver need not move anything if the frames coincide):

| name | contents |
|---|---|
| `arm` | grounded base box 100×40×20; arm box 300×20×20 hinged at the base's +x end about +y (Revolute, limits −30°..120°); a `Part::LocalCoordinateSystem` `mount_top` on the base top; arm component placed and rotated (yaw 15°) so frames aren't axis-aligned with the world |
| `slider` | grounded rail; stage1 and carriage each on a Slider along +z (limits 0..500 mm); a Belt coupling between the two sliders (radii 20/20 → ratio 1) |
| `fourbar` | grounded frame; crank, coupler, rocker with Revolute joints at the four pins (ground–crank, crank–coupler, coupler–rocker, rocker–ground), link lengths a=100 (ground), b=40 (crank), c=120 (coupler), d=80 (rocker) mm |
| `base2w` | grounded plate; two wheel cylinders (r 50.8, width 40) on Revolutes about y at ±250 mm |
| `bad_ungrounded`, `bad_ball`, `bad_slider_loop`, `bad_zero_mass` (a body made only of a zero-volume `Part::Feature` sketch or LCS), `bad_motor_on_loop` (settings added in Task 6) | one per spec error row |

- [ ] **Step 1: Failing test** `@needs_freecad test_fixtures_build(fixtures_dir)`: each path exists; reopening each in FreeCAD (via `run_in_freecad`) lists the expected joint types and a grounded object.
- [ ] **Step 2: Implement** `make_fixtures.py`. Probe every API you use; record deviations.
- [ ] **Step 3: Run**, commit `"Headless FreeCAD fixtures for the exporter tests"`.

---

### Task 4: Reading the Assembly and building the tree

**Files:**
- Create: `freecad/Scrimmage/scrimmage_part/read.py`, `freecad/Scrimmage/scrimmage_part/tree.py`,
  `freecad/Scrimmage/scrimmage_part/errors.py`, `tests/test_freecad_read.py`

**Interfaces:**
- Produces (all plain data; frames are 4×4 row-major lists or `(pos_mm, quat_wxyz)` tuples in world/Assembly coordinates):
  - `errors.ExportError(problems: list[str])`.
  - `read.read_assembly(asm) -> Extracted` with `components: dict[name, Comp(label, shape, placement, material_density_kg_m3 | None)]`, `grounded: list[name]`, `joints: list[J(name, label, type, comp1, comp2, frame1_world, frame2_world, limits: (min,max) | None, radii: (r1, r2) | None, suppressed)]`, `lcs_mounts: list[(name, frame_world, attached_comp | None)]`.
  - `tree.build(extracted) -> Tree` with `bodies: list[TBody(name, comps, parent | None, joint | None, frame_world)]` in parent-before-child order, `loops: list[(joint, body1, body2, pin_world)]`, `couplings: list[(joint, a_joint, b_joint, ratio)]`, `warnings: list[str]`; raises `ExportError` for the spec's rows (ungrounded, unsupported type, slider loop, coupling on non-tree joint or zero radius).

Rules (spec "From an Assembly to a part"):
- Union-find components over Fixed joints → groups. Group containing a grounded component = root; body names from the group's first component's label (sanitized to `[A-Za-z0-9_]`, deduplicated with `_2`…).
- BFS from root over non-suppressed Revolute/Slider joints between groups; the first time a group is reached its joint is its tree joint; an edge to an already-reached group is a loop (Revolute → loop entry; Slider → error).
- Joint frame (world): `comp.Placement * joint.Placement{1,2}` — **verify in a probe** whether FreeCAD 1.1 `Placement1/2` are relative to the component (Review Focus 1); the child body's frame is the joint frame on the child side; record the evidence.
- Limits: `EnableAngleMin/Max` → degrees; `EnableLengthMin/Max` → mm (converted later).
- Couplings: Gears ratio −r₁/r₂, Belt +r₁/r₂, RackPinion r (mm → m per rad later), where the two coupled joints are identified from the coupling's references (the joints acting on those components); zero radius → error.
- Groups not reachable from the root: merged into the root with a warning naming the components.

- [ ] **Step 1: Failing tests** (`@needs_freecad`, via `run_in_freecad` printing `SCRIMMAGE_JSON` of the tree): for `arm`: 2 bodies, the arm's joint axis is world-y rotated by the arm's 15° yaw as built, joint origin at the base +x end (within 0.01 mm), limits (−30, 120), `mount_top` listed; `slider`: 3 bodies chained, one Belt coupling ratio 1; `fourbar`: 4 bodies, 3 tree joints, 1 loop at the coupler–rocker pin; each `bad_*` fixture raises `ExportError` whose message names the joint/component (ungrounded, ball, slider loop).
- [ ] **Step 2–4:** implement, run, commit `"Exporter: read FreeCAD Assemblies into a body tree"`.

---

### Task 5: Mass, inertia, meshes, wheel cylinders

**Files:**
- Create: `freecad/Scrimmage/scrimmage_part/mass.py`, `freecad/Scrimmage/scrimmage_part/mesh.py`, `tests/test_freecad_mass.py`

**Interfaces:**
- `mass.body_inertial(comps, overrides: dict[name, kg], body_frame) -> (mass_kg, com_m_body, principal_moments, rpy_deg_body)` raising `ExportError` for zero mass.
  - Per component: density = material density (kg/m³) unless FreeCAD's "Default" material (or none) → 2700; mass = override or volume(mm³)·1e-9·density; component inertia from `shape.MatrixOfInertia` (FreeCAD's is for density 1 in mm⁵ — verify units in a probe) scaled to its mass; shift each to the body COM (parallel axis), rotate into the body frame, sum; eigen-decompose the 3×3 (implement a small Jacobi eigenvalue routine — no numpy in FreeCAD's add-on) → principal moments and the rotation, expressed as roll/pitch/yaw (degrees) for `inertia_rot`.
- `mesh.write_body_stl(comps, body_frame, path_stl)`: fuse/compound the shapes, transform into the body frame, tessellate (`shape.tessellate(0.5)` or `MeshPart.meshFromShape(LinearDeflection=0.5, AngularDeflection=0.3)`), scale mm→m, write binary STL (write it yourself with `struct` to control units, or use `Mesh` then scale).
- `mesh.wheel_cylinder(comps, body_frame, axis_body) -> (radius_m, width_m, center_m_body)` from the shape's extent about the axis.

- [ ] **Step 1: Failing tests** (`@needs_freecad`):
  - A 100×40×20 mm box with no material: mass 0.08·2.7 = 0.216 kg (±1e-6); with a mass override 1.0 → 1.0; with an assigned aluminum material → its density; with "Default" material → 2700.
  - The `arm` fixture's arm body: COM at the box center in the body frame; principal moments match m(b²+c²)/12 etc. (sorted), and `inertia_rot` reproduces the box's orientation relative to the joint frame.
  - A two-box body (L-shape) matches the parallel-axis sum computed independently in the test.
  - The written STL loads in Scrimmage's MuJoCo (`mujoco.MjModel.from_xml_string` with a mesh asset) and its bounding box in meters matches the shape's in mm / 1000 in the body frame.
  - `wheel_cylinder` on the `base2w` wheel: radius 0.0508, width 0.040 (±1e-4).
- [ ] **Step 2–4:** implement, run, commit `"Exporter: mass, principal inertia, meshes, wheel cylinders"`.

---

### Task 6: Settings, export, and `scrimmage freecad export`

**Files:**
- Create: `freecad/Scrimmage/scrimmage_part/settings.py`, `freecad/Scrimmage/scrimmage_part/export.py`,
  `tests/test_freecad_export.py`
- Modify: `scrimmage/freecad_tools.py`, `scrimmage/__main__.py`

**Interfaces:**
- `settings.get(doc, create=False) -> ScrimmageSettings | None` backed by a hidden `App::FeaturePython`
  `ScrimmagePart` with one `App::PropertyString` `Data` holding JSON `{format: 1, motors: [{slot, joint, motor,
  count, gear_ratio, radius_mm?, invert, efficiency}], encoders: [{slot, joint, on: "joint"|"motor", invert}],
  gyros: [{slot, body_comp}], mounts: [{name, comp, frame_local}], mass_overrides: {comp: kg}, contact: {comp:
  "wheel"|"collide"}, wheel_friction: {comp: f}, export_dir}` keyed by `obj.Name`; `save(doc, settings)`.
- `export.export(doc_or_path, out_dir) -> dict` summary `{part, bodies, joints, loops, couplings, slots, mounts,
  mass_kg, warnings}`; raises `ExportError`. Builds the part dict (bodies with `pos`/`rot` relative to parent
  body frames, `mass`/`com`/`inertia`/`inertia_rot`, mesh geom `meshes/<body>.stl` `collide=false` + wheel
  cylinder for wheels, joints `hinge = "z"`/`slide = "z"` with ranges in deg/m, couplings, loops with `pos` in
  body1's frame in meters, motor/encoder/gyro slots (slider gearing = gear_ratio / (radius_mm/1000) rad per m;
  encoder `on: motor` ratio = the joint's motor gear_ratio), mounts) and writes it with `toml_writer.dumps`
  atomically (temp dir → rename; replace only a folder holding `part.toml`).
- Stale settings (joint/component gone) → warnings, dropped. Motor on a loop joint → error naming the tree
  joint. Duplicate slot/mount names → error.
- `scrimmage freecad export FILE.FCStd OUT_DIR` → `run_freecad` a tiny script calling `export.export` and
  printing the summary JSON; prints a human summary; exit 2 with problems listed, or when FreeCAD isn't found.

- [ ] **Step 1: Failing tests** (`@needs_freecad`; settings are written into fixture copies by a helper running
  in FreeCAD):
  - `arm` with a KrakenX60 motor slot `pivot` (gear ratio 50), an encoder on the joint, the LCS mount and a
    mass override: export, then `load_part(out)` and `assemble` it as `[[part]] path = ...` on a stock chassis;
    assert slots, mount `top` (from `mount_top`), the joint `range`, `inertia_rot` present, and the mesh path is
    relative. Also run `World` with a PWM id and check the stock-arm-style holding voltage boundary (0.9× sinks,
    1.1× rises) using the exported arm's mass and COM.
  - `fourbar`: one `[[loop]]`; driving the crank 0→20° moves the rocker to the closed-form angle within 1°.
  - `slider`: the belt coupling ratio 1; a motor with gear ratio 10 and spool radius 20 mm → gearing 500 rad/m.
  - `base2w` with both wheels marked: each wheel body has a colliding cylinder r 0.0508 and a non-colliding mesh;
    the part sits on its wheels when attached to a chassis.
  - Re-export after deleting the arm's joint from the document: warning names the slot and joint; no slot.
  - `bad_motor_on_loop`, duplicate slot names, existing non-part out dir: `ExportError`/exit 2, nothing written.
  - `scrimmage freecad export` end to end on `arm` prints the summary and exits 0; with no FreeCAD found
    (`SCRIMMAGE_FREECAD_CMD=/nonexistent` and PATH stripped) exits 2 with an install hint.
- [ ] **Step 2–4:** implement, run (focused, then the non-Java suite), commit `"Exporter: settings, export, and scrimmage freecad export"`.

---

### Task 7: The panel

**Files:**
- Create: `freecad/Scrimmage/scrimmage_part/panel.py`
- Modify: `freecad/Scrimmage/InitGui.py`

The task panel (PySide via FreeCAD's `FreeCADGui.Control.showDialog`) with the five sections in the spec,
editing `ScrimmageSettings` and calling `read`/`tree`/`mass`/`export`. Picking mounts uses the current 3D selection
(`FreeCADGui.Selection.getSelectionEx()`): a planar face → center + outward normal; a circular edge or
cylindrical face → center + axis; store the frame in the component's local coordinates. Selecting a Bodies row
selects that group's components. Export shows the summary or the problems list.

- [ ] Steps: implement; keep logic out of the panel (anything testable goes into headless modules with tests);
  write `docs/freecad.md`'s "Panel checklist" (open `arm` fixture, add motor, add mount by picking, override mass,
  export, re-open, re-export) and run through it in the FreeCAD GUI if a display is available (`freecad` snap
  opens on this machine); record what you checked. If no GUI run is possible in your environment, say so in the
  report — the controller will run the checklist.
- [ ] Commit `"Scrimmage workbench task panel"`.

---

### Task 8: CI and docs

**Files:**
- Modify: `.github/workflows/ci.yml`, `README.md`, `docs/parts.md`
- Create: `docs/freecad.md`

- CI: a `freecad` job (ubuntu-latest): checkout, setup-python (repo's pins), install Scrimmage (`pip install
  -e .[dev]`), download a pinned FreeCAD 1.1.x AppImage (check the real release asset name on GitHub; pick one at
  least two weeks old), extract it (`--appimage-extract`, no FUSE on runners), set `SCRIMMAGE_FREECAD_CMD` to the
  extracted `usr/bin/FreeCADCmd` (or the AppRun with a cmd argument — verify), run
  `SCRIMMAGE_SKIP_JAVA=1 pytest -m freecad`. `continue-on-error: true` only if the job can't run FreeCAD at all;
  the Python job sets `SCRIMMAGE_SKIP_FREECAD=1`. Validate the YAML.
- `docs/freecad.md`: install (Linux `scrimmage freecad install`; Windows/macOS zip into the Mod folder from
  Help → About), preparing an Assembly (ground a component; Revolute/Slider/Fixed; Gears/Belt/RackPinion;
  `mount_*` LCS), the panel walkthrough, re-exporting, `scrimmage freecad export`, one troubleshooting entry per
  error message (copy the exact texts from the code), and the panel checklist from Task 7.
- README: "Parts from FreeCAD" section linking `docs/freecad.md` and `docs/parts.md`; layout listing gains
  `freecad/`.
- [ ] Check every documented command and message against the code; commit `"FreeCAD exporter: CI job and docs"`.
