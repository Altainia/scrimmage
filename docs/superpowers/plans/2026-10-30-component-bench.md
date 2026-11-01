# Component Bench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `scrimmage bench`: a mechanism idea written as a bench file (wheels, plates, bars, curves, CAD, inlet and
outlet zones, a game piece) runs in MuJoCo and reports headlines, a path picture, loads, comparisons and a gallery,
from the command line and from agent tools.

**Architecture:**
- A new package `scrimmage/bench/`:
  - `spec.py` parses the file;
  - `build.py` makes the MuJoCo model, including a pool of piece bodies;
  - `squishy.py` applies the launcher model's contact laws to squishy balls;
  - `run.py` steps the bench (shafts, inlets, outlets, outcomes, loads, energy);
  - `report.py`, `pictures.py` and `gallery.py` turn a result into words, images and HTML;
  - `cad.py` loads part folders and meshes.
- Rigid pieces use MuJoCo's own contacts. Squishy balls never collide natively: `squishy.py` queries geometry and
  applies forces.
- Shafts reuse `launcher/shaft.py`'s `ShaftModel` and `VelocityController` on one shared `motors.Battery`.

**Tech Stack:** Python 3.11+, MuJoCo ≥ 3.4, numpy, Pillow, pytest; optional `coacd` for concave meshes.

**Spec:** docs/superpowers/specs/2026-10-30-component-bench-design.md

## Global Constraints

- **Contact law follows the piece:**
  - Rigid pieces use MuJoCo's own contacts. Treads are compliant (`tread_solref`), with an optional piece stiffness
    in series, set per contact pair.
  - Squishy pieces are **balls only** and use `launcher/contact.py`'s laws: `contact_force` (with the wheel rubber's
    `wheel_stiffness` on wheels), `friction_force` and `rolling_loss_torque`.
  - Squishy contacts with one body whose normals are within **30°** are merged, the deepest kept.
  - A squishy piece of any other shape is an error suggesting a rigid stand-in.
- **Presets:** balls `2020-power-cell`, `2022-cargo` and `2026-fuel` come from `launcher/presets.toml`. Discs come
  only in Task 9.
- **Inputs:**
  - Every length key also has an inch form `<key>_in` (give one of the two; both is an error).
  - Angles end in `_deg`.
  - Unknown keys and sections are errors naming the valid ones.
  - Bad input prints `scrimmage: <what's wrong>` and exits 2.
- **Bench frame:** x forward, z up, y across; a side view looks along y. Curve angles about +y follow the launcher
  hood convention: 0° along +x, 90° along +z.
- **Results:**
  - Every result says UNCALIBRATED.
  - Results go to `<file's folder>/<file name>-results/` unless `--out` is given.
  - The same file gives identical results twice.
- **Reuse, don't copy:** the launcher model's contact laws, shaft, controller and flight. Changes to `launcher/`
  must keep every existing launcher test passing with unchanged numbers.
- **Dependencies:** no new required dependencies. `coacd` is an optional extra, `bench = ["coacd"]`. Files without
  concave meshes work without it.
- **Tests:**
  - Floats compare with relative tolerances.
  - No wall-clock assertions except under `SCRIMMAGE_TIMING=1` (local only).
  - Each new test takes well under 20 s.
- **Commits:** end every message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **CI:** never push.

## Review Focus

1. **An inlet zone that overlaps a shape, or a piece spawned inside a wheel.** `bench check` rejects it, naming the
   inlet and the shape. It must not explode at run time. (Task 2 test.)
2. **A run that blows up** (NaN, or a piece faster than 100 m/s). It stops with a clear message naming the piece and
   the time, not silently. (Task 4 test.)
3. **A mechanism that holds a piece forever** (a ball trapped between a plate and a stopped wheel). The piece ends
   `jammed`, and the run ends at its duration limit. (Task 4 test.)
4. **Bad curve geometry.**
   - A span over 360°, zero width, or a radius at or below 0 is an error.
   - A radius smaller than the thickness is also an error.
   - (Task 1 test.)
5. **A concave mesh without `coacd` installed.** The error says the mesh is concave and to
   `pip install scrimmage[bench]`; it doesn't run with a hull-filled hood. (Task 8 test.)

---

### Task 1: The bench file

**Files:**
- Create: `scrimmage/bench/__init__.py`, `scrimmage/bench/spec.py`, `scrimmage/bench/presets.toml` (materials)
- Modify: `pyproject.toml` (package `scrimmage.bench`, package-data `bench/*.toml`, extra `bench = ["coacd"]`)
- Test: `tests/test_bench_spec.py`

**Interfaces:**
- Produces (frozen dataclasses in `spec.py`; lengths in meters after parsing):
  - `BenchError(ValueError)`.
  - `load_bench(path: Path) -> Bench`, and `parse_bench(raw: dict, base_dir: Path) -> Bench`.
  - `with_overrides(raw: dict, overrides: dict[str, object]) -> dict`: dotted keys.
    - A list-section entry is addressed by its name: `wheel.top.diameter`, `curve.hood.radius_end`.
  - `Bench(name, author, idea, notes, piece, shafts: dict[str, Shaft], wheels, plates, bars, curves, cads, inlets,
    outlets, flight, run, materials: dict[str, Material], sweep: dict[str, list], charts: list[dict],
    raw: dict, base_dir: Path)`.
  - `Piece(name, kind: "squishy"|"rigid", shape: "sphere"|"box"|"cylinder"|"mesh", size: tuple, mass,
    inertia: tuple[3], ball: launcher.inputs.Ball | None, stiffness: float | None, mesh: Path | None)`.
  - `Shaft(name, motor: str | None, count, gearing, wheel_inertia, added_inertia,
    control: "speed"|"voltage"|"free", target_rps, voltage, kP, kS, kV, stator_limit, supply_limit,
    coupled_to: str | None, ratio)`.
  - `Wheel(name, shaft, center, axis, diameter, width, tread)`.
  - `Plate(name, center, size, rot_deg, material)`.
  - `Bar(name, a, b, diameter, material, spring: float | None, travel, spring_axis)`.
  - `Curve(name, center, axis, zero_dir, radius_start, radius_end, start_deg, end_deg, width, thickness, material)`.
  - `Cad(name, part: Path | None, mesh: Path | None, units, center, rot_deg, material, slots: dict[str, str])`.
  - `Inlet(name, center, size, count, rate, velocity, spin, spread_speed, spread_position, spread_time, seed)`.
  - `Outlet(name, center, size)`.
  - `Flight(outlet, height, target_distance, target_height, ring_diameter, air)`.
  - `RunSettings(duration, timestep, jam_time, jam_distance)`.
  - `Material(name, foam_friction, rigid_friction, source)`.
- Decided:
  - `include = [...]` merges named entries from earlier files first; the file's own entries win by name. Two
    included files defining one name is an error naming both.
  - Defaults:
    - `RunSettings`: duration 5 s, jam_time 1.0 s, jam_distance 0.01 m;
    - `Shaft`: count 1, gearing 1, control `speed` when `target_rps` is given, `voltage` when `voltage` is given,
      `free` with neither and no motor.
  - Materials: polycarbonate, aluminum, steel, UHMW and nitrile. Foam friction for polycarbonate is 0.5, the
    launcher hood default; every other value is an estimate tagged `# estimate:`.
  - Treads come from the launcher's `[wheels.*]` presets.

- [ ] **Step 1: Write the failing tests**
  - `test_minimal_file_parses`: a pinch-car file loads, and its shapes, shafts and outlet are as written.
  - `test_inch_keys`: `diameter_in = 4` gives 0.1016. Giving both forms is an error naming the key.
  - `test_unknown_key_lists_valid_ones`.
  - `test_squishy_non_ball_is_an_error`: the message suggests a rigid stand-in.
  - `test_ball_preset_is_squishy`: `2026-fuel` is squishy with the launcher's Ball.
  - `test_shaft_rules`: a speed target with no motor is an error; a wheel naming a missing shaft is an error.
  - `test_curve_checks` (Review Focus 4).
  - `test_include_merges_and_clashes`.
  - `test_overrides_address_entries_by_name`.
- [ ] **Step 2:** Run `pytest tests/test_bench_spec.py -v`. Expected: FAIL (no module).
- [ ] **Step 3:** Implement `spec.py` and `presets.toml`. Copy the inch handling's behavior from
  `testrange/elements.py`, not its code paths.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit: "Bench file: parse wheels, shapes, zones and pieces".

### Task 2: Building the bench model

**Files:**
- Create: `scrimmage/bench/build.py`
- Test: `tests/test_bench_build.py`

**Interfaces:**
- Consumes: Task 1's `Bench`.
- Produces:
  - `build_bench(bench: Bench) -> Built`, with `Built(model: MjModel, layout: BenchLayout)`.
  - `BenchLayout`:
    - `shape_of_geom: dict[int, str]`;
    - `shaft_dof: dict[str, int]`;
    - `wheel_geoms: dict[str, int]`;
    - `bar_dof: dict[str, int]`;
    - `piece_bodies: list[int]`, with `piece_qpos` and `piece_qvel` addresses;
    - `parked: list[np.ndarray]`;
    - `bounds: (lo, hi)`;
    - `outlet_boxes` and `inlet_boxes`.
  - `cut_curve(curve: Curve, max_deg: float = 5.0) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]`: center,
    quaternion and half-size of each chord box along the inner surface.
  - `check_clear(built: Built) -> None` raises `BenchError` for Review Focus 1.
- Decided:
  - All wheels on one shaft are one body on one hinge.
  - A coupled shaft is an equality JOINT constraint with `ratio`, as `[[coupling]]` does.
  - A springy bar is a slide joint along `spring_axis`, with stiffness `spring` and range ±`travel`.
  - The piece pool is one free body per piece (the sum of inlet counts, at most 200). Pool bodies are parked
    outside `bounds`, spaced apart.
  - Squishy pieces get contype and conaffinity 0.
  - Rigid pieces collide natively, with friction from the material's `rigid_friction` against each shape.
  - A wheel's tread uses `tread_solref` with the piece stiffness in series.
  - `bounds` is the shapes' bounding box grown by 0.5 m.

- [ ] **Step 1: Write the failing tests**
  - `test_shapes_and_shafts_built`: geom counts, the hinge per shaft, and the coupling ratio.
  - `test_cut_curve_lies_on_the_arc`: every chord box's inner face is within 0.5 mm of the arc at `max_deg = 5`.
  - `test_pool_parked_and_squishy_never_collide_natively`: after 100 steps of a ball-pool bench, `d.ncon == 0`
    between pieces and shapes.
  - `test_check_clear` (Review Focus 1).
  - `test_springy_bar_has_a_slide`.
- [ ] **Step 2:** Run `pytest tests/test_bench_build.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `build.py` with `mujoco.MjSpec`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit: "Bench model: shapes, shafts, curves and a piece pool".

### Task 3: Squishy contacts

**Files:**
- Create: `scrimmage/bench/squishy.py`
- Test: `tests/test_bench_squishy.py`

**Interfaces:**
- Consumes: `Built` and `BenchLayout` (Task 2); `launcher.contact`.
- Produces: `SquishyContacts(built: Built, bench: Bench)`.
  - `.apply(d: MjData) -> dict[str, float]`:
    - writes `xfrc_applied` on squishy piece bodies;
    - writes the reactions on shape bodies: `qfrc_applied` on shaft and bar dofs, via `mj_applyFT`;
    - returns this step's normal force per shape name, for the loads.
  - `.work_on_piece: np.ndarray`: joules done on each pool piece by moving bodies this run, for Task 4's energy
    check.
- Decided:
  - Geometry from `mj_geomDistance`, after a bounding-sphere prefilter.
  - Piece-to-piece contacts use the same law with each ball's own numbers in series.
  - The contact point and normal, slip and rolling loss are as the spike's `Hybrid.apply` computes them; the spike
    is at `/tmp/claude-1000/-home-altainia-code-scrimmage/ac449d14-9b89-46fa-a7f9-a841ce5dbaa6/scratchpad/spike/bench.py`.
  - Rolling loss is regularized by `tanh(w / 0.1)`.

- [ ] **Step 1: Write the failing tests**
  - `test_ball_at_rest_on_a_plate`: the sag matches `contact_force`'s static squeeze under the ball's weight, within
    2%.
  - `test_drop_bounce_matches_restitution`: a 3 m/s drop on a plate rebounds at the preset's restitution ± 0.03.
  - `test_seam_is_not_counted_twice`: a ball resting across two coplanar boxes' seam sags as on one box, within 2%.
  - `test_wheel_reaction_is_equal_and_opposite`: the torque on the shaft equals the ball's moment about the axis.
- [ ] **Step 2:** Run `pytest tests/test_bench_squishy.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `squishy.py`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit: "Bench: the launcher model's ball laws on MuJoCo geometry".

### Task 4: Running a bench

**Files:**
- Create: `scrimmage/bench/run.py`
- Test: `tests/test_bench_run.py`

**Interfaces:**
- Consumes: Tasks 1–3; `launcher.shaft.ShaftModel` and `VelocityController`; `motors.Battery`, shared by all shafts.
- Produces: `run_bench(bench: Bench, trace_hz: float = 60.0) -> BenchResult`.
  - `BenchResult(pieces: list[PieceOutcome], shafts: dict[str, ShaftStats], loads: dict[str, LoadStats],
    energy: list[EnergyCheck], trace: list[tuple[float, np.ndarray]], built: Built, bench: Bench)`.
  - `PieceOutcome(index, inlet, status: "out"|"jammed"|"escaped"|"timed_out"|"stuck_at_inlet",
    outlet: str | None, t_in, t_out, path: list[tuple[float, float, float, float]], speed, elevation_deg, yaw_deg,
    spin: tuple[3], peak_force)`.
  - `ShaftStats(name, target_rps, min_rps, drop_pct, recovery_s, peak_current, energy_per_piece_j)`.
  - `LoadStats(shape, peak_force_n, peak_impulse_ns, at: tuple[3])`.
  - `EnergyCheck(index, e_in, work_in, potential_lost, e_out, ok: bool)`.
  - `BenchRunError(RuntimeError)` for Review Focus 2.
- Decided:
  - **Spawning:** inlets spawn on a seeded schedule (`numpy.random.default_rng(seed)`). A spawn waits while its
    spot is occupied. A piece that never gets in by the end is `stuck_at_inlet`.
  - **Leaving:** a piece whose center enters an outlet box is recorded and parked. Outside `bounds`, it is
    `escaped`.
  - **Jams:** a piece that moves less than `jam_distance` in `jam_time` while inside is `jammed`, then parked.
  - **Loads:** peak force per shape, plus impulse per pass (summed while one piece touches the shape).
  - **Energy:** "ok" is `e_out <= e_in + work_in + potential_lost + max(0.01 * e_out, 0.005 J)`. Work from wheels
    and springy bars counts as input. For rigid pieces, use `mj_contactForce` power on moving bodies.
  - **Trace:** qpos is sampled at `trace_hz` for pictures.

- [ ] **Step 1: Write the failing tests**
  - `test_pinch_car_leaves_near_surface_speed`: a rigid block between two 60 mm wheels at 40 rps leaves `out:shot`
    at 80–95% of surface speed, upright.
  - `test_energy_never_created`: run every piece of the pinch car and of a small squishy hooded bench; all checks
    are ok.
  - `test_same_file_same_result`: identical outcomes twice.
  - `test_trapped_ball_jams` (Review Focus 3).
  - `test_blow_up_is_reported` (Review Focus 2): a deliberately absurd stiffness raises `BenchRunError` naming the
    piece.
  - `test_escape_and_stuck_statuses`.
  - `test_shaft_drop_and_recovery`.
- [ ] **Step 2:** Run `pytest tests/test_bench_run.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `run.py`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit: "Bench run: shafts, inlets, outlets, outcomes, loads and energy".

### Task 5: Reference agreement, speed and examples

**Files:**
- Create: `examples/bench/hooded.toml`, `pinch_car.toml`, `bounce_bar.toml`, `curved_ramp.toml`,
  `hood_shapes/{round,flared,long_wrap}.toml`
- Modify: `scrimmage/bench/squishy.py`, `run.py` (speed work as needed)
- Test: `tests/test_bench_reference.py`, `tests/test_bench_examples.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Decided:
  - `hooded.toml` is `examples/launcher/hooded.toml`'s geometry: the hood's flare and arc, entry 240°, exit at
    50°, and the feed entry speed.
  - Every example has `author` and `idea` lines, in a student's voice.
  - The speed approach is the builder's: vectorized analytic sphere-versus-primitive distances, fewer queries,
    implicit friction. An optional compiled speed-up is allowed only as an optional extra, with identical results
    without it.

- [ ] **Step 1: Write the failing tests**
  - `test_reference_grip_regime` (spec check 1): for each of gap 0.15, 0.21 and 0.28 D at 33.3 rps, the bench's
    exit speed is within 3% of `launcher.run.run_setup` for one ball with no spread. Launch angles are printed, not
    asserted.
  - `test_faceting_invariance` (check 3): `max_deg` of 5, 2.5 and 1.25 changes the exit speed under 2%.
  - `test_examples_run`: every example loads, runs, and puts at least one piece `out`.
  - `test_turnaround` (check 6), skipped unless `SCRIMMAGE_TIMING=1`: hooded, 10 balls at 5/s, pictures included,
    under 60 s.
- [ ] **Step 2:** Run them. Expected: FAIL, or slow.
- [ ] **Step 3:** Write the examples and make the hybrid fast enough. Record the 66.7 rps (4000 rpm) numbers (all three gaps:
  the bench, the reference, and the difference) in the task report for Task 7's docs.
- [ ] **Step 4:** Run `pytest tests/test_bench_reference.py tests/test_bench_examples.py -v`, and
  `SCRIMMAGE_TIMING=1 pytest -k turnaround`. Expected: PASS.
- [ ] **Step 5:** Commit: "Bench: agrees with the launcher model; examples".

### Task 6: Results, pictures, flight and the run commands

**Files:**
- Create: `scrimmage/bench/report.py`, `scrimmage/bench/pictures.py`
- Modify: `scrimmage/launcher/flight.py` (extract `fly_state`), `scrimmage/__main__.py` (`bench check|run|view`)
- Test: `tests/test_bench_report.py`, `tests/test_bench_cli.py`, `tests/test_launcher_flight.py` (unchanged,
  must pass)

**Interfaces:**
- Consumes: `BenchResult` (Task 4).
- Produces:
  - `fly_state(pos, vel, spin_axis, spin, ball, air, goal) -> Landing` in `launcher/flight.py`. `fly` calls it, and
    its results are unchanged.
  - `headlines(result: BenchResult) -> list[str]`: three to six lines, as in the spec's examples.
    - The first line counts outcomes.
    - A load line appears when a shape's peak exceeds 3× the median shape peak.
    - A shaft line gives drop and recovery.
    - The last line is `UNCALIBRATED: ...`, naming what to measure.
  - `write_results(result: BenchResult, out_dir: Path) -> Path`: `summary.txt` (the headlines and tables),
    `pieces.csv`, `shafts.csv`, `loads.csv`, and `landings.csv` with `[flight]`.
  - `path_picture(result, view: "side"|"top", width=1280, height=720) -> np.ndarray`:
    - an orthographic MuJoCo render of the shapes;
    - piece paths drawn with Pillow, colored by status (out green, jammed red, escaped orange, timed out gray);
    - outlets outlined.
  - `pass_frames(result, frames=8) -> list[tuple[float, np.ndarray]]`: frames from the first `out` piece's time
    inside, via `render.contact_sheet`.
  - `write_gif(frames, path, fps=15) -> Path`.
- CLI:
  - `scrimmage bench check FILE` prints "ok" and the shape counts.
  - `bench run FILE [--out DIR]` prints the headlines and writes results plus `path_side.png`, `path_top.png`
    (only when anything moves across y), `pass.png` and `pass.gif`.
  - `bench view FILE [--slow 0.25]` plays the run in the MuJoCo viewer.

- [ ] **Step 1: Write the failing tests**
  - `test_fly_state_matches_fly`: identical landings for the launcher's hooded shot.
  - `test_headlines_wording`: pinch car: "N of N left through `shot` at X m/s ± Y, Z° up", plus the UNCALIBRATED
    line.
  - `test_loads_headline_when_a_bar_takes_the_hit`: bounce_bar.
  - `test_path_picture_shapes` (size and non-blank, status colors present). Use the existing render tests'
    `MUJOCO_GL` handling.
  - `test_flight_landing_written`.
  - `test_cli_run_writes_files_and_exit_codes`: 0 on success; 2 with `scrimmage: ...` on a bad file.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the new tests and `pytest tests/test_launcher_*.py`. Expected: PASS.
- [ ] **Step 5:** Commit: "Bench results: headlines, path pictures, a pass GIF, flight; bench check/run/view".

### Task 7: Sweep, compare, gallery, agent tools and docs

**Files:**
- Create: `scrimmage/bench/gallery.py`, `docs/bench.md`
- Modify: `scrimmage/__main__.py` (`bench sweep|compare|gallery`), `scrimmage/mcp_server.py`, `README.md`
  (a "Component bench" section and the MCP table)
- Test: `tests/test_bench_gallery.py`, `tests/test_bench_mcp.py`, `tests/test_bench_docs.py`

**Interfaces:**
- Consumes: Tasks 1–6. The launcher's `sweep.combinations` and its chart writer, where the shapes allow.
- Produces:
  - `sweep_bench(path: Path) -> list[tuple[dict, BenchResult]]`.
  - `compare_benches(paths: list[Path], feed_from: Path | None) -> list[BenchResult]`. "Same piece and feed" copies
    `[piece]` and `[[inlet]]` from `feed_from`, or from the first file.
  - `compare_picture(results) -> np.ndarray`: the path pictures side by side.
  - `write_gallery(folder: Path, out: Path) -> Path`: one self-contained HTML file. Pictures are embedded as base64
    PNG. Each idea shows its author, idea line, side path picture and headlines, then a comparison table of every
    idea.
  - MCP tools: `bench_check(path)`, `bench_run(path, out_dir=None)`, `bench_sweep(path, out_dir=None)`,
    `bench_compare(paths, feed_from=None)` and `bench_gallery(folder, out=None)`.
    - They run in `asyncio.to_thread` and never touch the robot session.
    - `bench_run` returns the headlines as JSON, the side path picture and the pass contact sheet.
- Docs: `docs/bench.md` covers:
  - **The flow.** The year-one room workflow and the student-without-an-agent route.
  - **The format reference.** Every key, its unit and default.
  - **Pieces and laws.** Rigid against squishy, and balls only.
  - **Turning a spoken idea into a bench file.** Guess sizes from the piece, say which numbers were guessed, start
    rough, run early.
  - **Results.**
  - **How accurate is this?**
    - The spike's numbers and Task 5's measured agreement.
    - The 4000 rpm difference, with the spike's explanation.
    - UNCALIBRATED.
  - **CAD.** Filled in by Task 8.
  - **Troubleshooting.**

- [ ] **Step 1: Write the failing tests**
  - `test_sweep_combinations`.
  - `test_compare_uses_one_feed`.
  - `test_gallery_is_self_contained`: no external URLs, one `<img>` per idea, author and idea text present.
  - `test_mcp_bench_run_returns_images` (call the tool function directly, as the existing MCP tests do).
  - `test_mcp_bench_does_not_touch_a_session`.
  - `test_docs_examples_load`: every TOML block in `docs/bench.md` marked `<!-- bench -->` passes `bench check`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement, and write the docs.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit: "Bench sweep, compare and gallery; agent tools; docs".
- [ ] **Step 6 (controller, spec check 7):** dispatch a fresh subagent with only `docs/bench.md` and this idea:
  "two wheels pinch the ball up a curved ramp, then it hits a bar and drops into a hopper". It must produce a file
  that passes `bench check` and a `bench run` picture. Fix the docs where it stumbled; commit
  "Bench docs: fixes from the agent check".

### Task 8: CAD parts and meshes

**Files:**
- Create: `scrimmage/bench/cad.py`
- Modify: `scrimmage/bench/build.py`, `docs/bench.md` (the CAD section)
- Test: `tests/test_bench_cad.py`

**Interfaces:**
- Consumes: `parts.model.load_part(path)`; Task 2's builder.
- Produces: `add_cad(spec: mujoco.MjSpec, cad: Cad, bench: Bench) -> None`, and `convex_pieces(mesh_path: Path,
  units: str) -> list[Path]`.
- Decided:
  - **Part folders:**
    - Bodies are welded to the bench.
    - A joint carrying a motor slot becomes a shaft hinge, configured by `slots = { <slot> = "<shaft>" }`.
    - Exporter-marked wheels use their collision cylinders.
    - Bodies marked `collide` use their meshes.
    - A part joint with no motor slot is welded, with a warning.
  - **Meshes:**
    - `units` is required: `"m"`, `"mm"` or `"in"`.
    - A mesh is concave when its hull's volume exceeds its own volume by more than 2%.
    - Concave meshes are split with `coacd`. The pieces are cached as `<mesh>.convex/<hash>/*.obj`, keyed by the
      file's content and units.
    - Without `coacd`, a concave mesh is the Review Focus 5 error.

- [ ] **Step 1: Write the failing tests**
  - `test_mesh_units_scale`: a 100 mm cube STL in `mm` is 0.1 m.
  - `test_convex_mesh_needs_no_extra`.
  - `test_concave_without_coacd_errors` (Review Focus 5): monkeypatch the import to fail.
  - `test_concave_split_and_cached`: skipped unless `coacd` is importable. A generated hood-arc OBJ gives the
    `hooded.toml` exit speed within 5%.
  - `test_part_folder_slot_becomes_shaft`: a small hand-written part folder fixture with a hinge and a motor slot.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement, and write the CAD docs section: FreeCAD export for moving assemblies, Inventor STL for
  parts that don't move, and the units.
- [ ] **Step 4:** Run the tests, then the full non-Java suite: `pytest -q -k "not java"`. Expected: PASS.
- [ ] **Step 5:** Commit: "Bench CAD: part folders and meshes, concave meshes split".

### Task 9: Disc pieces (after disc-launcher is on main)

**Precondition:** `disc-launcher` has merged to main and this branch has been rebased onto main. Until then this task
is not started; the branch is complete without it.

**Files:**
- Modify: `scrimmage/bench/spec.py`, `build.py`, `run.py` (flight hand-off)
- Create: `examples/bench/two_stage_disc.toml`
- Test: `tests/test_bench_disc.py`

**Interfaces:**
- Consumes: `scrimmage/disc/presets.toml` (`2013-frisbee`, `ultra-star`; rim stiffness `k_rim`) and the disc
  flight in `scrimmage/disc/flight.py`.
- Decided:
  - A disc preset is a rigid cylinder piece with `stiffness = k_rim`.
  - `[flight]` with a disc piece uses the disc flight.

- [ ] **Step 1: Write the failing test** `test_disc_reference` (spec check 2): a one-wheel-pair linear disc bench
  against the disc launcher model, with the same disc, wheels, gap and speed. Exit speed within 5%. Also add
  `two_stage_disc.toml` to `test_examples_run`.
- [ ] **Step 2:** Run it. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the bench tests and `pytest tests/test_disc*.py`. Expected: PASS.
- [ ] **Step 5:** Commit: "Bench: disc pieces".
