# Test Range and Robot-Code Shooting (sub-project 9) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The team's real robot code (Phoenix or fast mode) drives a robot with a simulated launcher, hood, feeder
and intake on a range with the real 2026 hubs; every ball is a body in the world, the goals count what scores, and
scenario checks on the score pass or fail in CI.

**Architecture:**
- A new package, `scrimmage/testrange/` (not `range`: that would shadow the builtin), one file per concern:
  - **range files:** a per-game TOML (goals, floor balls, start positions) parsed into frozen dataclasses, with
    the goal geometry (outline, scoring surface, crossing test) as pure numpy;
  - **build:** goals as thin box plates and pre-allocated ball bodies added to the `MjSpec` by `assemble()`;
  - **balls:** the runtime ledger of every ball (on the field, in flight, in the robot, scored, out), parking,
    flight forces and scoring, stepped by `World.step` through one hook object;
  - **shooter and ball path:** the launcher part's runtime (sub-project 8's `Launcher` on the world's bus, hood
    read from its joint), lanes, hopper, feeder stages, intake zone and beam breaks;
  - **launch:** a launcher exit turned into a free ball in world coordinates;
  - **aim:** the aiming-table generator.
- `World` holds `test_range: RangeRuntime | None`. With no `[range]`, it is `None` and nothing else changes.
- Signals, a `by_time` check, the report's per-ball section, NetworkTables and the live view read the runtime's
  counts and per-ball log.

**Tech Stack:** Python 3.11+, MuJoCo (`MjSpec`, `xfrc_applied`, sleeping islands if the spike in Task 4 passes),
numpy, `tomllib`; three.js front end (existing); Java 17 / WPILib 2026 / AdvantageKit / PathPlanner for the sample.

**Spec:** docs/superpowers/specs/2026-10-28-test-range-design.md

## Global Constraints

- **Built on what exists:**
  - `[[part]]` stock parts and the assembler;
  - motor bindings and ids (`ctre = "TalonFX:<id>"`, fast-mode devices);
  - the shared `Battery`;
  - lockstep and the real-time Phoenix path;
  - movers and the live-view frame protocol;
  - scenario signals and checks;
  - the NetworkTables link;
  - `scrimmage/launcher` (`Launcher`, `ShaftModel`, `FedBall`, `ExitedBall`, flight air model, presets).
- **Launcher model changes allowed, for running in the world:**
  - the shooter shaft takes the world's bus voltage and reports its supply current to the shared battery,
    instead of owning a battery;
  - the hood angle can change at run time, read from the hood joint;
  - the model advances in substeps inside a world physics step.

  The standalone `scrimmage launcher run|sweep` must keep working and give the same results.
- **Ball count is fixed per run.** The live-view frame protocol drops frames whose body count changes, so ball
  bodies are created when the world is built (preloads, floor balls and spares) and parked out of play when
  inside the robot or scored.
- **License rules unchanged:** no Phoenix internals are touched; fast mode stays WPILib-only.
- **CI and speed:**
  - The full 2026 ball layout must run.
  - The CI speed test (faster than real time in fast mode) may use fewer balls.
  - Balls resting on the floor may use cheaper contact settings, as long as they still behave when disturbed.
  - New tests run in a few minutes.
- **Phoenix mode:** Phoenix-mode runs are real time and not exactly repeatable, so their scenario score thresholds
  are looser than fast mode's.
- **A score you can't explain.** Every ball must be traceable in the report. A missed shot should say why, and
  a ball must never silently vanish.
- **Fudged goal geometry.** The 2026 hub must match the official CAD and manual, and the range file cites them.
- **Breaking repeatability.** Fast-mode runs with the same inputs must give the same scores (lockstep, seeded
  randomness).
- **Robots without balls paying for them.** Existing robots and scenarios, which have no range or launcher, must
  behave exactly as before and not slow down.
- **Tests** run with `.venv/bin/pytest`; Java with the repo's Gradle commands (`./gradlew build` in
  `examples/java_fastmode`, as `tests/test_fastmode_e2e.py` does; `pytest -m java` for the Java e2e tests).
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

### Conventions used by several tasks

- **Frames.** Field coordinates are WPILib's (blue alliance wall at x = 0, the generated field's 16.54 × 8.07 m).
  The launcher's frame is sub-project 8's: x forward, z up, the shaft along y; the ball's path angle φ is measured
  from +x toward +z, and a positive shaft speed launches forward.
- **Ball states** (one per ball body, `BallState` in Task 4): `field` (in play, on the floor or flying), `robot`
  (in the hopper, a stage or the launcher), `stuck` (the launcher returned `no_contact`, `no_wheel` or `jammed`),
  `scored`, `out` (left the field), `spare` (an unused preload slot). Every ball is in exactly one state, and the
  states' counts always sum to the number of ball bodies.
- **Ball bodies** are named `ball.<i>`: floor balls first, in range-file order, then one slot per unit of hopper
  capacity (0 when the robot has no shooter).
- **Collision bits** (MuJoCo: two geoms touch when `(ct1 & ca2) | (ct2 & ca1)`): field `ct 1 / ca 2` and robot
  `ct 2 / ca 1` stay as they are. New: `BALL_BITS = ct 4 / ca 7` (field, robot, balls), `LAUNCHED_BITS =
  ct 4 / ca 5` (field and balls, not the robot), `PARKED_BITS = ct 0 / ca 0`.
- **Parking** puts a ball at `PARK_Z = -20.0` m under its own (x, y) slot, writes its qpos and zero qvel back after
  every `mj_step` (vectorized), and gives it `PARKED_BITS`. The live view hides movers below z = -10.

## Review Focus

1. **A launched ball spawns inside the robot's collision geometry** (a launcher mounted low, its exit point inside
   the bumper volume). Expected: the ball flies out as it would in free air; it neither explodes away nor sticks.
   Test in Task 7.
2. **A ball leaves the field** (over a 0.5 m wall, or through the floor at speed). Expected: state `out`, outcome
   `out_of_field` with its last position in the per-ball log, `on_field` drops by one; never a silent loss. Test in
   Task 4.
3. **The intake meets more balls than it can take** (a full hopper, or a pile wider than the pickup rate allows).
   Expected: the extra balls stay physical on the field and are pushed by the bumper; `held` never exceeds the
   hopper's capacity. Test in Task 6.
4. **A feeder stage reverses, or stops, with balls at a stage boundary or at the launcher entry.** Expected: the
   balls go back the way they came (to the previous stage, or to the hopper), none is duplicated or lost, and the
   ledger still sums to the ball count every step. Test in Task 6.
5. **A scenario uses score signals or `[range]` keys against a robot without a range, or a passed-in session built
   with a different range.** Expected: a `ScenarioError` before the run, naming the problem, not a crash
   mid-run or a check that silently reads 0. Test in Task 8.

---

### Task 1: Launcher model changes for in-world use

**Files:**
- Modify: `scrimmage/launcher/shaft.py`, `scrimmage/launcher/model.py`
- Create: `tests/data/launcher_golden/` (golden outputs written by the unchanged code), `tests/test_launcher_inworld.py`

**Interfaces:**
- Consumes: `ShaftModel`, `Launcher`, `run_setup`, `load_setup`, `parse_setup` (sub-project 8).
- Produces:
  - `ShaftModel.__init__(shaft: Shaft, battery: BatteryIn | None)`. With `None` the shaft owns no battery:
    `bus_voltage` is whatever `set_bus_voltage(v: float) -> None` last set (12.0 until then), and `step` no longer
    updates it. With a `BatteryIn` the behavior is exactly today's.
  - `ShaftModel.angle: float`: the wheel's accumulated rotation (rad), integrated in `step` from the step's start
    and end speeds (trapezoid).
  - `Launcher.__init__(setup: Setup, external_bus: bool = False)`. `external_bus=True` builds the shaft with
    `battery=None`.
  - `Launcher.exit_angle_deg: float` (starts at `setup.hood.exit_angle_deg`) and
    `Launcher.set_hood_angle(exit_angle_deg: float) -> None`. It recomputes the path length, and the end point of
    every ball in contact (their `consts["end"]`, `at_end`, `release`). The entry end is fixed. A wrap ≤ 0 raises
    `LauncherConfigError`. A ball whose arc length is already past the new end finishes `"launched"` in the next
    substep. `ExitedBall.launch_deg` is the hood angle at the moment the ball exits.
  - `Launcher.positions() -> list[tuple[int, float, float]]`: `(index, lane, s)` for each ball in contact (for the
    launcher-entry beam break).

- [ ] **Step 1: Write the golden files with the unchanged code.** Write a short script into the test module's
  docstring (so it can be rerun) and run it once now:
  - `scrimmage launcher run examples/launcher/hooded.toml --out <tmp>` → copy `results.csv` to
    `tests/data/launcher_golden/hooded.csv`;
  - for each combination of `examples/launcher/hood_angle.toml`'s sweep, with `feed.count` overridden to 3, the
    `RunResult.metrics` dict and the decimated `trace` → `tests/data/launcher_golden/hood_angle.json` (floats as
    `repr` strings).
- [ ] **Step 2: Write the failing tests** in `tests/test_launcher_inworld.py`:
  - `test_run_csv_unchanged`: the CLI's `results.csv` for hooded.toml equals the golden bytes.
  - `test_sweep_metrics_unchanged`: every metric and trace point equals the golden `repr` exactly.
  - `test_external_bus_uses_set_voltage`: with `battery=None` and `set_bus_voltage(10.0)`, a step at 12 V command
    is clamped to 10 V (stator current equals that of a `BatteryIn(10.0, 0.0)` shaft, rel 1e-12), and
    `bus_voltage` stays 10.0 after the step.
  - `test_angle_integrates_speed`: at a constant held speed ω for 0.5 s, `angle == ω·0.5` (rel 1e-9).
  - `test_same_hood_angle_is_noop`: calling `set_hood_angle(setup.hood.exit_angle_deg)` before every step gives
    the same `ExitedBall`s as not calling it (exact equality).
  - `test_hood_raised_mid_pass`: with a ball halfway along the path, raising the angle by 10° gives that ball
    `launch_deg` = the new angle, and a shorter contact time than an unmoved run.
  - `test_hood_raised_past_ball_releases_it`: raising the angle so the new end lies behind the ball's arc length
    finishes it `"launched"` within one substep.
  - `test_hood_angle_wrap_rejected`: an angle with `entry_deg − (angle + 90) ≤ 0` raises `LauncherConfigError`.
  - `test_world_sized_steps_match`: one ball through `step(0.001, v)` × 60 matches `step(0.06, v)` (exit speed and
    spin rel 1e-6).
  - `test_positions_reports_balls_in_contact`.
- [ ] **Step 3: Run** `.venv/bin/pytest tests/test_launcher_inworld.py -v`. Expected: the two golden tests PASS,
  the rest FAIL (`set_bus_voltage`, `set_hood_angle`, `positions` missing).
- [ ] **Step 4: Implement** the changes in `shaft.py` and `model.py`.
- [ ] **Step 5: Run** `.venv/bin/pytest tests/test_launcher*.py -v`. Expected: all PASS, golden tests included.
- [ ] **Step 6: Commit** (`Launcher: external bus, run-time hood angle, shaft angle for in-world use`).

### Task 2: Range files and the 2026 range

**Files:**
- Create: `scrimmage/testrange/__init__.py`, `scrimmage/testrange/spec.py`, `scrimmage/testrange/ranges/2026.toml`
- Modify: `pyproject.toml` (packages: add `scrimmage.testrange`; package-data: `testrange/ranges/*.toml`)
- Test: `tests/test_range_file.py`

**Interfaces:**
- Consumes: `PRESETS`, `Ball` and `LauncherConfigError` (`scrimmage.launcher.inputs`).
- Produces:
  - `class RangeError(ValueError)`.
  - The range file layout (SI units, angles in degrees with `_deg`):
    ```toml
    [range]
    name = "FRC 2026 REBUILT"
    ball = "2026-fuel"                      # a launcher ball preset
    [[goal]]
    name = "hub_blue"
    pos = [4.625, 4.035]                    # opening center on the field
    heading_deg = 0.0
    rim_height = 1.829
    tilt_deg = 0.0                          # 0 horizontal, 90 vertical
    opening = { sides = 6, across_flats = 1.060, rotation_deg = 0.0 }   # or { diameter = 0.9 }
    funnel = { depth = 0.395, slope_deg = 60.0 }                        # optional
    body = { size = [1.194, 1.194], height = 1.2 }                      # optional: a hollow body under the opening
    [[goal.plate]]                          # extra plates, in the goal's frame (the 2026 back net)
    name = "net"
    pos = [0.0, 0.0, 0.0]
    size = [1.0, 1.0]                       # width, height
    rpy_deg = [0.0, 0.0, 0.0]
    [[floor_balls]]
    grid = { corner = [7.0, 2.0], step = [0.16, 0.16], count = [6, 10] }   # or at = [[x, y], ...]
    [start.blue_left]
    pose = [3.5, 6.0, 180.0]                # x, y, heading_deg: like [robot] start_pose
    ```
  - Geometry conventions, fixed here:
    - **Goal frame:** origin at the opening's center at rim height, x along `heading_deg`, z up.
    - **Opening vertex k** (k = 0 … sides − 1) lies at angle `rotation_deg + k·360/sides` from the goal's +x, at
      circumradius `across_flats / (2 cos(180°/sides))`. A hexagon at rotation 0 has corners on ±x and flats facing
      ±y. A circle is a 24-sided polygon of the given diameter (`CIRCLE_SIDES = 24`, a documented choice).
    - **Tilt** turns the opening about the goal's y axis; its entry normal goes from +z (tilt 0) to −x (tilt 90).
    - **Funnel:** each wall runs inward from the rim at `slope_deg` below horizontal for `depth` vertically. The
      throat is the opening inset by `depth / tan(slope)` per flat, at `rim_height − depth`.
    - **Scoring surface:** the throat for a funnelled goal, otherwise the opening.
  - Frozen dataclasses: `Opening(sides: int, across_flats: float, rotation_deg: float)`, `Funnel(depth: float,
    slope_deg: float)`, `Body(size: tuple[float, float], height: float)`, `Plate(name: str, pos: tuple[float, float,
    float], size: tuple[float, float], rpy_deg: tuple[float, float, float])`, `Goal(name, pos, heading_deg,
    rim_height, tilt_deg, opening, funnel: Funnel | None, body: Body | None, plates: tuple[Plate, ...])`,
    `Range(name: str, ball_preset: str, ball: Ball, goals: tuple[Goal, ...], floor_balls: np.ndarray  # (n, 2),
    starts: dict[str, tuple[float, float, float]], path: Path)`.
  - `Goal.rim_vertices() -> np.ndarray` (n, 3, world); `Goal.throat_across_flats() -> float | None`;
    `Goal.scoring_surface() -> Surface`.
  - `Surface(center: np.ndarray, normal: np.ndarray, axes: np.ndarray  # (2, 3), polygon: np.ndarray  # (n, 2))`
    with `Surface.crossed(prev: np.ndarray, now: np.ndarray) -> np.ndarray` (bool per row of (m, 3) centers): the
    center went from the normal's side to the other side in this step, and the interpolated crossing point lies
    inside the polygon (boundary counts as inside).
  - `load_range(ref: str, base_dir: Path) -> Range`: `ref` is a built-in name (`BUILTIN_RANGES = {"2026": …}`) or
    a path relative to `base_dir`.
  - Errors (`RangeError`, each naming the file and key): unknown key (listing the valid ones), unknown ball preset
    (listing the presets), a throat no wider than the ball, two goals or starts with one name, floor balls that
    overlap each other (centers closer than a diameter) or lie off the field, an opening with neither `sides` nor
    `diameter` or with both.

- [ ] **Step 1: Source the 2026 numbers.** Everything in goal 3 of the spec is already measured; use those values
  and cite them as the spec does. Still to source:
  - **Neutral-zone FUEL layout** (positions of every ball at the start of the match). First the manual: fetch
    https://firstfrc.blob.core.windows.net/frc2026/Manual/HTML/2026GameManual.htm and find the pre-match FUEL
    placement in the arena/match-setup sections (count, grid, spacing, position relative to the field's center
    line). If the manual gives a count and a figure but no coordinates, measure the layout in the field CAD:
    - the CAD is `/home/altainia/snap/freecad/common/field2026/field.step`; FreeCAD is `/snap/bin/freecad.cmd`;
    - snap confinement: the script and its output must live under `/home/altainia/snap/freecad/common/` (e.g.
      `…/common/sp9/fuel_centers.py` writing `…/common/sp9/fuel_centers.csv`);
    - the script loads the STEP, keeps the solids whose bounding box is a cube about 0.150 m on a side (FUEL),
      and writes their centers;
    - find the CAD-to-field transform by measuring both hub bodies' centers in the same script: they must land at
      x = 4.625 m and 16.541 − 4.625 m on y = 4.035 m (the spec's 182.1 in from each alliance wall);
    - cite the CAD for each grid number, with the count cross-checked against the manual.
  - **Back net** size and position in the goal's frame, and the **hub body's height**: measured in the same
    FreeCAD session.
  - **Start positions:** at least `blue_left`, `blue_center` and `blue_right` on the blue starting line (cite the
    manual's starting-line definition; the y values are our choice, and the comment says so).
- [ ] **Step 2: Write `ranges/2026.toml`.** Every line with a number carries a `# source:` comment (the CAD URL
  `https://firstfrc.blob.core.windows.net/frc2026/FieldAssets/FE-2026-rev-rebuilt-playing-field.step` or the
  manual URL with its section) or a `# choice:` comment for numbers we chose (start positions' y). Both hubs, red
  mirrored about the field's center (heading 180).
- [ ] **Step 3: Write the failing tests:**
  - `test_2026_hub_matches_spec`: across flats 1.060, rim 1.829, funnel depth 0.395 and slope 60, throat across
    flats within 0.015 m of 0.593 at 1.434 m (abs 0.002), blue hub at x = 4.625 and red at 16.541 − 4.625.
  - `test_2026_every_number_cited`: every line of `2026.toml` containing a digit outside a comment has a
    `# source:` or `# choice:` comment.
  - `test_2026_floor_balls_load`: the count equals the manual's (a constant in the test with the manual section
    in its comment), and no two overlap.
  - `test_hexagon_corners_on_x`: at rotation 0, `rim_vertices()` has corners on ±x of the goal and flats at
    ±across_flats/2 on y.
  - `test_crossing_downward_inside_scores`, `test_crossing_upward_does_not`, `test_crossing_outside_outline_does_not`.
  - `test_vertical_goal_crossing_direction`: tilt 90, heading 0: a center moving +x through the opening crosses;
    moving −x does not.
  - `test_throat_narrower_than_ball_rejected`.
  - `test_unknown_key_lists_valid_keys`, `test_unknown_ball_preset_lists_presets`, `test_relative_path_and_builtin`.
- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_range_file.py -v`. Expected: FAIL (module missing).
- [ ] **Step 5: Implement** `spec.py` and the pyproject changes.
- [ ] **Step 6: Run the tests.** Expected: PASS.
- [ ] **Step 7: Commit** (`Test range: range files and the 2026 range`), with the FUEL sourcing route in the message.

### Task 3: The range in the model

**Files:**
- Create: `scrimmage/testrange/build.py`
- Modify: `scrimmage/parts/assemble.py` (`assemble`, `Built`), `scrimmage/parts/field.py` (`add_field`),
  `scrimmage/world.py` (`movable_objects` skips `ball.*`)
- Create: `tests/data/rangeless_golden.npz` (written by the unchanged code), `tests/range_fixtures.py`
- Test: `tests/test_range_model.py`

**Interfaces:**
- Consumes: `load_range`, `Range`, `Goal` (Task 2).
- Produces:
  - Config section (robot config; Task 8 lets a scenario override it):
    ```toml
    [range]
    file = "2026"          # built-in name or a path relative to the config
    start = "blue_left"    # optional: a named start position (instead of [robot] start_pose)
    preload = 8            # balls in the hopper at the start (0 without a [shooter])
    floor_balls = true     # false: the floor balls start parked as spares
    ```
  - `BALL_BITS`, `LAUNCHED_BITS`, `PARKED_BITS`, `PARK_Z` (Conventions) in `build.py`.
  - `ball_contact(ball: Ball, timestep: float) -> dict`: the ball geom's MuJoCo contact keywords. The documented
    mapping: `solref = (2·timestep, ζ)` with ζ = −ln e / √(π² + ln² e) (a damped spring's restitution e), the
    ball's `solmix` far above every other geom's (so its solref governs all its contacts), `condim = 6` (rolling
    friction comes from the carpet's existing rolling coefficient). The drop tests below are the arbiter: if they
    fail, adjust `solimp` (not e) and document why.
  - `RangeLayout(ball_names: list[str], n_floor: int, goal_bodies: list[str])`.
  - `add_range(spec: mujoco.MjSpec, rng: Range, n_slots: int) -> RangeLayout`:
    - each goal is a static body `<goal name>` (welded to the world, so not a mover) holding box plates with
      `FIELD_BITS` and 0.02 m thickness: one funnel wall per side (as long as the rim side, its inner face on the
      wall plane; on a convex funnel the overhang lies outside the opening), or with no funnel one rim strip per
      side; the body's four side walls up to `body.height`; each `[[goal.plate]]`;
    - balls: `ball.<i>` with a free joint, one sphere geom of the preset's diameter and mass, `ball_contact`
      keywords; floor balls at z = radius on their positions with `BALL_BITS`, slots at their park positions with
      `PARKED_BITS`.
  - `add_field(spec, cfg, *, obstacle_default: bool = True)`.
  - `Built.range: Range | None`, `Built.layout: RangeLayout | None`. `n_slots` is `[shooter].hopper.capacity`
    (0 without a `[shooter]`; Task 5 adds the section).
  - Errors (`PartError`): `[range]` with `[model] mjcf` ("a range needs a robot built from [[part]]"); `[range]
    start` together with `[robot] start_pose`; an unknown start name (listing the range's); `[field] obstacle =
    true` with a range ("the range's field elements replace the center obstacle"); `preload` > 0 with no
    `[shooter]`.
  - `tests/range_fixtures.py`: `range_cfg(**overrides) -> dict` (a chassis robot with no shooter on the 2026
    range, floor balls off, `TEST_WORLD` battery as in `tests/test_part_physics.py`) and `drive(w, seconds,
    **pwm)`. Task 5 extends `range_cfg` with the shooter parts.

- [ ] **Step 1: Write the rangeless golden.** With the unchanged code, build `World` for `configs/swerve.toml` and
  `tests/fake_robot.toml`, step each 500 times with a fixed HAL command (all drive PWMs at 0.5), and save `nbody`,
  `ngeom` and the final `qpos` to `tests/data/rangeless_golden.npz`. Keep the generator in the test module.
- [ ] **Step 2: Write the failing tests:**
  - `test_rangeless_world_unchanged`: the same run gives exactly the golden arrays, and `world.test_range is None`
    (the attribute is added in Task 4; until then use `getattr(..., None)`).
  - `test_range_turns_off_obstacle`: no geom named `obstacle`; `test_obstacle_true_with_range_rejected`.
  - `test_hub_plates_are_static_field_geoms`: the plates collide with the robot's bits, and no hub geom is a mover.
  - `test_ball_bodies_fixed_count`: floor balls on → `n_floor + capacity` bodies named `ball.0 …`; floor balls off
    → the same count, all parked.
  - `test_named_start_sets_pose`: `start = "blue_left"` puts the robot at that pose (as `start_pose` would);
    `test_start_and_start_pose_rejected`; `test_unknown_start_lists_names`; `test_range_with_mjcf_rejected`.
  - `test_drop_reproduces_restitution`: a ball dropped from 1.0 m on the carpet rebounds at e · v_impact (abs
    0.05 · v_impact) for e in 0.3, 0.6, 0.8 (pass a modified `Ball`).
  - `test_drop_on_hub_plate`: the same on a horizontal plate built by `add_range` for a test goal.
  - `test_resting_ball_stays_put`: a floor ball left alone for 2 s moves < 1 mm.
- [ ] **Step 3: Run** `.venv/bin/pytest tests/test_range_model.py -v`. Expected: the golden test PASSES, the rest
  FAIL.
- [ ] **Step 4: Implement** `build.py` and the assembler, field and `movable_objects` changes.
- [ ] **Step 5: Run** `.venv/bin/pytest tests/test_range_model.py tests/test_assemble.py tests/test_part_physics.py
  tests/test_world_pokes.py -v`. Expected: PASS.
- [ ] **Step 6: Commit** (`Test range: goals and ball bodies in the model`).

### Task 4: Balls in the world: ledger, parking, flight, scoring

**Files:**
- Create: `scrimmage/testrange/balls.py`, `scrimmage/testrange/runtime.py`
- Modify: `scrimmage/world.py` (`__init__`, `reset`, `step`, `sensor_updates`), `pyproject.toml` (the mujoco floor,
  only if sleeping is used)
- Test: `tests/test_range_balls.py`

**Interfaces:**
- Consumes: `Range`, `Surface` (Task 2); `RangeLayout`, bits, `PARK_Z` (Task 3); `Air` and `Ball` (launcher).
- Produces:
  - `BallState` (a `str` enum): `field`, `robot`, `stuck`, `scored`, `out`, `spare`.
  - `BallLog` (a dataclass, one per ball, filled by Tasks 4, 6 and 7): `id: int`, `origin: str` (`"floor"` or
    `"preload"`), `fired_t`, `robot_pose: tuple[float, float, float] | None` (x, y, heading_deg),
    `robot_velocity: tuple[float, float, float] | None` (vx, vy, ω deg/s), `hood_deg`, `shooter_rps`, `exit_speed`,
    `spin_rpm`, `exit_slip`, `lane`, `launcher_status: str | None`, `outcome: str` (`"held"`, `"on_field"`,
    `"in_flight"`, `"scored"`, `"out_of_field"`, `"stuck"`, `"spare"`), `goal: str | None`, `scored_t`,
    `first_hit: str | None` (the first geom or body it touched after leaving the launcher), `apex_z`,
    `last_pos: tuple[float, float, float] | None`, `at_rest: bool | None`. Floats are NaN until set.
  - `class Balls` (in `balls.py`), all per-ball work vectorized over numpy index arrays:
    - `Balls(model, data, layout: RangeLayout, rng: Range, air: Air)`; `air` is the shooter's (Task 5) when there
      is one, else the launcher presets' `default` air;
    - `state: np.ndarray` (object array of `BallState`), `log: list[BallLog]`;
    - `park(i: int) -> None`, `place(i: int, pos, vel, ang_vel, bits) -> None` (unpark at a pose, with velocity);
    - `apply_flight_forces() -> None`: zeroes `xfrc_applied` rows of every ball body (never the robot's), then for
      balls in `field` state above the floor by more than 1 mm sets drag `−½ρC_dA|v|v` and lift
      `½ρC_LA|v|² (ω̂ × v̂)` with `C_L = min(lift_slope·S, lift_max)`, `S = r|ω|/|v|`, ω the ball's own angular
      velocity from `qvel` (the same law as `launcher/flight.py`);
    - `after_step(prev_pos: np.ndarray) -> list[tuple[int, str]]`: rewrites parked balls' qpos/qvel; scores
      crossings (`Surface.crossed` per goal; scored balls are parked and logged with `goal` and `scored_t`); marks
      balls outside the field (x < −1 or > 17.54, y < −1 or > 9.07, or z < −1) `out` with their last position;
      tracks `first_hit` and `apex_z` for balls fired in the last 10 s; returns the `(ball, event)` pairs.
  - `class RangeRuntime` (in `runtime.py`):
    - `RangeRuntime(world, built: Built, cfg: dict)`;
    - `before_physics(hal: HalState, vbatt: float, dt: float) -> float`: everything that sets forces, returning
      the supply current (A) it adds to the battery (0 until Task 5);
    - `after_physics() -> None`;
    - `sensor_updates(hal: HalState) -> list[Update]` (empty until Task 6);
    - `reset(*, start: str | None = None, preload: int | None = None, floor_balls: bool | None = None) -> None`;
    - `counts() -> dict`: `{"score": int, "goals": {name: int}, "fired": int, "held": int, "on_field": int,
      "ledger": {state: int}}`;
    - `ball_log() -> list[dict]` (each `BallLog` as a dict, outcomes brought up to date: `last_pos` and `at_rest`
      for balls still in play);
    - `ball_poses() -> np.ndarray` (k, 7) of the balls in `field` state (for NetworkTables).
  - `World.test_range: RangeRuntime | None`; `World.step` calls `before_physics` after the motor loop and adds its
    current to the battery total, then `mj_step`, then `after_physics`; `World.reset` calls `test_range.reset()`.
    Nothing else in `step` changes, and with no range there is one `is None` test per step.
  - **Sleeping (cheaper contact for resting balls).** Spike first (Step 1). If it passes: range worlds enable
    `mjENBL_SLEEP`, the robot's tree gets `mjSLEEP_NEVER`, ball trees are allowed to sleep; rangeless worlds are
    untouched. If it fails, no sleeping; record the full-layout speed in the commit message for Task 12's docs.

- [ ] **Step 1: Spike MuJoCo sleeping** (a scratch script, not committed). Check on MuJoCo 3.17: (a) a resting
  ball falls asleep; (b) it wakes when a moving robot box pushes it, and when a ball lands on it; (c) writing the
  qpos/qvel of a sleeping ball (parking, placing) takes effect, or find the documented way to wake a tree first;
  (d) two runs are bit-identical. Use sleeping only if all four hold. If used, raise pyproject's `mujoco>=` floor
  to the first release with `mjENBL_SLEEP` (from MuJoCo's changelog).
- [ ] **Step 2: Write the failing tests** (`tests/range_fixtures.py` worlds):
  - `test_ledger_sums_to_ball_count`: at every step of a 3 s run with floor balls on and the robot pushed through
    them by `set_robot_force`, the ledger's counts sum to the number of ball bodies.
  - `test_flight_matches_launcher_flight`: a ball placed with an `ExitedBall`-like launch (12 m/s, 50°, 200 rad/s
    backspin) and no obstacles lands within 0.05 m of `launcher.flight.fly`'s floor point and its apex within
    0.02 m (the same air).
  - `test_drag_only_on_balls`: after `set_robot_force(50, 0)` and 100 steps with balls flying, the robot body's
    `xfrc_applied` row is still `[50, 0, 0, 0, 0, 0]`.
  - `test_ball_dropped_into_funnel_scores`: a ball released 0.3 m above the blue hub's opening, off center so it
    hits a funnel wall, scores in `hub_blue` once; `counts()["goals"]["hub_blue"] == 1`, and the ball is parked.
  - `test_ball_bouncing_off_rim_does_not_score`: one dropped on the rim edge outward is not counted, and its
    `first_hit` names a `hub_blue` plate.
  - `test_ball_out_of_field` (Review Focus 2): a ball thrown over the wall at 8 m/s ends `out`, outcome
    `out_of_field` with `last_pos` set, and `on_field` drops by one.
  - `test_sleeping_ball_wakes_when_pushed` (only if sleeping is used): the robot pushed into a resting ball moves
    it > 0.1 m.
  - `test_reset_restores_balls`: after a run, `World.reset()` puts every floor ball back and zeroes the counts.
  - `test_same_inputs_same_outcome`: two identical scripted runs give equal `ball_log()` and qpos (exact).
  - `test_full_2026_layout_runs` (CI and speed): the full floor layout and a robot pushed through the neutral zone
    for 2 simulated s: no NaN, no MuJoCo warnings (`data.warning[*].number == 0`), ledger intact; print the
    speed factor; under 60 s of wall time.
- [ ] **Step 3: Run** `.venv/bin/pytest tests/test_range_balls.py -v`. Expected: FAIL.
- [ ] **Step 4: Implement** `balls.py`, `runtime.py` and the `World` hook.
- [ ] **Step 5: Run** `.venv/bin/pytest tests/test_range_balls.py tests/test_range_model.py tests/test_world_pokes.py
  tests/test_lockstep.py -v`. Expected: PASS.
- [ ] **Step 6: Commit** (`Test range: ball ledger, flight forces, scoring`), with the spike's outcome and the
  full-layout speed factor in the message.

### Task 5: The launcher part and the shooter on the shared battery

**Files:**
- Modify: `scrimmage/parts/stock/mechanisms.py` (stock `launcher`), `scrimmage/parts/stock/__init__.py`
  (`stock_params`), `scrimmage/parts/assemble.py` (`Instance.stock_params`), `scrimmage/bindings.py`
  (`MotorBinding` external mode), `scrimmage/testrange/runtime.py`, `tests/range_fixtures.py`
- Create: `scrimmage/testrange/shooter.py`
- Test: `tests/test_range_shooter.py`

**Interfaces:**
- Consumes: Task 1 (`Launcher(setup, external_bus=True)`, `set_hood_angle`, `set_bus_voltage`, `ShaftModel.angle`),
  Tasks 3–4 (`RangeRuntime`, `Built.range`), `parse_setup`, `load_setup`.
- Produces:
  - **Stock part `launcher`** (`register(Stock("launcher", …))`): bodies `base` (its origin on the shaft axis, x
    forward, z up), `wheel` (joint `spin`, hinge y, inertia `wheel_moi`) and, when articulated, `hood` (joint
    `hood`, hinge y about the shaft, like the arm's pivot). All geoms `collide = False`. Params:
    - `wheel_diameter` (m, 0.1016), `width` (m, inside width, 0.3), `wheel_moi` (kg·m², 0.002), `added_moi`
      (kg·m², 0);
    - `motor` (KrakenX60), `count` (1), `gearing` (1.0): the shooter;
    - `hood_gap` (m, 0.12), `hood_entry_deg` (240);
    - `hood` (`"articulated"` or `"fixed"`), `hood_angle` (deg, 50: the fixed hood's exit angle), `hood_range`
      (deg pair, [20, 70]: exit angles at the hood's hard stops), `hood_motor` (KrakenX60), `hood_count` (1),
      `hood_gearing` (50.0), `hood_mass` (kg, 1.0).
    - The hood joint reads 0 at the low stop: exit angle = `hood_range[0]` + joint angle; the joint's range is
      [0, hi − lo].
    - Slots: `shooter` (motor on `spin`), `hood` and `hood_encoder` (only when articulated). Mounts: `base`.
  - `stock_params(name: str, given: dict, where: str) -> dict` (the checked parameters, defaults filled) and
    `Instance.stock_params: dict` (`{}` for path parts).
  - **`MotorBinding.external: bool`** (default False). When True, `apply` reads the command and sets
    `voltage`/`command` as today but applies no torque and no back-EMF damping; `set_shaft_currents(stator: float,
    supply: float) -> None` sets `current`/`supply_current`. `updates` is unchanged: it reads the (mirrored)
    joint, so Phoenix's rotor sensor and the fast-mode encoder see the shaft.
  - **Config section:**
    ```toml
    [shooter]
    part = "launcher"                 # the stock launcher instance it runs in
    setup = "my_launcher.toml"        # optional: a sub-project 8 setup file for wheels, hood and air
    lanes = [-0.08, 0.08]             # positions across the wheels (m, along the shaft)
    [shooter.wheels]                  # sub-project 8 sections, inline; they override the setup file's
    durometer = 35.0
    friction = 1.0
    positions = [-0.08, 0.08]
    width = 0.05
    [shooter.hood]
    friction = 0.3
    [shooter.air]
    preset = "default"
    [shooter.hopper]
    capacity = 30
    ```
    Keys taken from elsewhere are errors that say where they come from: `ball` (from the range), `wheels.diameter`,
    `hood.gap`, `hood.entry_deg`, `hood.exit_angle_deg`, `shaft.*`, `launcher.*` (from the part). A setup file's
    value for one of those must equal the part's (else an error naming both). Its `control`, `goal`, `robot`,
    `feed`, `spread` and `sweep` sections are ignored.
  - `ShooterConfig` and `parse_shooter(cfg: dict, built: Built, cfg_dir: Path) -> ShooterConfig` (in
    `shooter.py`): builds the launcher `Setup` by injecting the part's values and the range's ball, then
    `parse_setup`. Errors (`ValueError` with the `[shooter]` key): no range, unknown part, not a stock launcher,
    `hood_range[1]` giving a wrap ≤ 0.
  - `class Shooter` (in `shooter.py`): `Shooter(world, config: ShooterConfig)` (it sets `external = True` on the
    world's bindings for the launcher's `shooter` slot); `step(dt: float, vbatt: float) ->
    list[ExitedBall]` (sets the hood angle from the hood joint, sets the bus voltage, steps the launcher at the
    mean voltage of the shooter slot's bindings, splits stator and supply current evenly over them); `mirror() ->
    None` (writes the spin joint's qpos = qpos0 + `shaft.angle` and qvel = `shaft.speed` after `mj_step`);
    `hood_deg() -> float`; `rps() -> float`; `launcher: Launcher`.
  - `RangeRuntime.before_physics` steps the `Shooter` and returns the shooter bindings' supply current;
    `after_physics` calls `mirror()`.

- [ ] **Step 1: Write the failing tests** (extend `range_cfg` with a chassis + launcher robot, PWM ids):
  - `test_stock_launcher_builds_both_hoods`: articulated has slots `shooter`, `hood`, `hood_encoder` and joints
    `spin`, `hood`; fixed has only `shooter` and `spin`.
  - `test_shooter_spins_up_like_standalone`: at a fixed 10 V command (stiff battery), the spin joint's velocity
    after 2 s equals a standalone `ShaftModel` at 10 V with the same shaft (rel 1e-6).
  - `test_encoder_reads_shaft`: a `TalonFX` id's rotor sensor `>velocity` = shaft rps × gearing (rel 1e-6).
  - `test_shooter_sags_shared_battery`: with the default 0.02 Ω battery, spinning up drops `world.vbatt`, and the
    drive's motors see the same `vbatt`.
  - `test_hood_angle_follows_joint`: driving the hood motor moves the joint, and `Shooter.hood_deg()` equals
    `hood_range[0]` + the joint angle (rel 1e-9); the launcher's `exit_angle_deg` follows each step.
  - `test_fixed_hood_uses_hood_angle`.
  - `test_shooter_key_from_part_rejected`: `[shooter.wheels] diameter = 0.1` raises naming `wheel_diameter` on the
    launcher part; `test_setup_file_conflict_names_both`; `test_shooter_without_range_rejected`;
    `test_hood_range_wrap_rejected`.
  - `test_external_binding_applies_no_torque`: `qfrc_applied` at the spin dof is 0 after a step at 12 V.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_range_shooter.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the stock part, `stock_params`, the external binding mode, `shooter.py` and the
  runtime wiring.
- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_range_shooter.py tests/test_stock.py tests/test_ctre_bindings.py
  tests/test_scrimmage_bindings.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** (`Test range: launcher part, external shooter shaft, hood joint`).

### Task 6: Lanes, hopper, feeder stages, intake zone, beam breaks

**Files:**
- Create: `scrimmage/testrange/ballpath.py`
- Modify: `scrimmage/testrange/runtime.py`, `scrimmage/testrange/shooter.py` (`ShooterConfig` gains the ball
  path), `scrimmage/world.py` (beam breaks join `_reject_duplicate_devices`), `tests/range_fixtures.py`
- Test: `tests/test_range_ballpath.py`

**Interfaces:**
- Consumes: `Shooter`, `ShooterConfig` (Task 5); `Balls`, `BallState`, `BallLog` (Task 4); `FedBall` (launcher);
  `Instance.stock_params` (Task 5).
- Produces:
  - **Config:**
    ```toml
    [[shooter.stage]]                 # in order, hopper side first; at least one
    name = "floor"
    part = "hopper_floor"             # a stock roller or flywheel: its diameter, motors and gearing
    path_length = 0.40                # m along the ball path
    [[shooter.stage]]
    name = "feeder"
    part = "feeder"
    path_length = 0.25
    [shooter.intake]
    part = "intake"                   # a stock roller; inward is its positive spin
    at = "drive.center"               # a robot mount
    pos = [0.45, 0.0, 0.08]           # zone center in the mount's frame
    size = [0.15, 0.55, 0.20]         # depth, width (the cross-section), height (full sizes)
    rate = 8.0                        # balls per second at most
    min_surface_speed = 0.5           # m/s inward before it collects (a documented default)
    [[shooter.beam_break]]
    at = "stage:feeder"               # "hopper", "stage:<name>" or "launcher"
    fraction = 0.5                    # stage only: where along it
    lane = 0                          # optional: one lane (index into lanes); default any lane
    dio = 0
    active_low = true                 # as today's limit switches
    ```
  - Behavior, fixed here:
    - **Surface speed** of a stage = its joint's qvel × diameter/2 (signed). Feeder loads on the stage motors are
      not modeled (documented).
    - **On a stage,** every ball moves along the path at the surface speed, but never closer than one ball
      diameter to the ball ahead in its lane (it stops behind it).
    - **Forward hand-off:** a ball reaching the end of stage k enters stage k+1 at 0 when that entry is clear;
      otherwise it waits at the end. The hopper releases a lane's next ball into stage 1 when stage 1 runs forward
      and its entry is clear.
    - **Into the launcher:** a ball reaching the end of the last stage is inserted as `FedBall(available_at=now,
      lane=lanes[lane], entry_speed=surface speed, ball=range ball)`, when no launcher ball in that lane has
      s < diameter. Its `BallLog` gets the launcher's ball index.
    - **Reverse:** a ball pushed below 0 on stage k goes to the end of stage k − 1 (or back to the front of its
      hopper lane from stage 1); if that place is blocked it stops at 0.
    - **Lanes:** a collected or preloaded ball goes to the lane holding fewest balls (lowest index on a tie) and
      keeps it. Lanes closer than one ball diameter apart are a config error.
    - **Intake:** a `field` ball whose sphere touches the zone box (center-to-box distance ≤ radius), while the
      intake's inward surface speed ≥ `min_surface_speed`, is collected (parked, state `robot`) if the hopper has
      room and at least 1/`rate` s has passed since the last pickup.
    - **Beam break** occupied: `hopper`: a ball in the hopper (in `lane`, or any); `stage:<name>`: a ball center
      within one radius of `fraction · path_length`; `launcher`: a ball in the launcher with s < diameter (from
      `Launcher.positions()`). Reported as `("DIO", "<dio>") {"<>value": …}` like `DigitalBinding`.
  - `class BallPath` (in `ballpath.py`): `BallPath(config, balls: Balls, shooter: Shooter)`;
    `step(dt: float, t: float) -> None`; `collect(pose) -> None`; `preload(n: int) -> None`; `held() -> int`;
    `lanes_state() -> list[list[tuple[str, float]]]` (per lane, `(where, s)` per ball, for tests);
    `beam_updates() -> list[Update]`; `device_keys() -> list[tuple[str, str]]`.
  - `RangeRuntime` steps `BallPath` in `before_physics` (after the shooter), collects in `after_physics`, and
    returns `beam_updates()` from `sensor_updates`. The stage and intake motors are ordinary `MotorBinding`s, so
    they already draw from the shared battery.

- [ ] **Step 1: Write the failing tests** (two lanes, two stages, an intake; PWM ids):
  - `test_preload_fills_emptiest_lane`: 5 preloaded → lanes hold 3 and 2.
  - `test_stage_moves_at_surface_speed`: a ball on `floor` advances ω·d/2·t (rel 1%) and keeps its spacing.
  - `test_stopped_stage_holds_balls`; `test_chained_stages_hand_off`.
  - `test_ball_enters_launcher_with_stage_speed`: the `FedBall` has the last stage's surface speed and its lane's
    position.
  - `test_reverse_returns_balls` (Review Focus 4): reversing `feeder` with a ball at the stage boundary and one at
    the launcher entry's hand-off sends them back; across the run the ledger sums to the ball count every step
    and no ball id appears twice in the lanes.
  - `test_intake_needs_motor_inward`: touching with the intake stopped or reversed collects nothing.
  - `test_intake_rate_and_capacity` (Review Focus 3): a robot pushed into a pile of 12 balls with capacity 5 holds
    exactly 5, collected no faster than `rate`; the other 7 stay `field` and are displaced by the bumper.
  - `test_beam_breaks`: each location toggles its DIO as a ball passes; `active_low` inverts it.
  - `test_beam_break_dio_conflict_rejected`: the same channel as a `[[digital]]` raises naming both.
  - `test_lanes_too_close_rejected`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_range_ballpath.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `ballpath.py` and the wiring.
- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_range_*.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** (`Test range: lanes, hopper, feeder stages, intake, beam breaks`).

### Task 7: Leaving the launcher, and the per-ball log

**Files:**
- Create: `scrimmage/testrange/launch.py`
- Modify: `scrimmage/testrange/runtime.py`, `scrimmage/testrange/balls.py`
- Test: `tests/test_range_launch.py`

**Interfaces:**
- Consumes: `ExitedBall` (launcher); `Shooter` (Task 5); `BallPath` (Task 6); `Balls.place`, `BallLog` (Task 4).
- Produces:
  - `exit_state(shot: ExitedBall, r_path: float, base_pos: np.ndarray, base_mat: np.ndarray, body_vel: np.ndarray
    # (6,) world angular then linear at base_pos, t_now: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]`:
    position, velocity and angular velocity in the world:
    - exit point in the launcher frame: `(r_path cos φ_exit, lane, r_path sin φ_exit)` with φ_exit =
      `launch_deg + 90°`, rotated and moved by the launcher base body's pose;
    - velocity: `shot.speed` along the launch direction (`launch_deg` above the launcher's x, turned by `yaw_deg`
      about z), rotated to the world, plus the robot's velocity at that point, `v_base + ω × (p − base_pos)`;
    - spin: `shot.spin` about the launcher's −y turned by `yaw_deg` (backspin, as `flight.py`), plus the robot's ω;
    - the position is advanced by `velocity · (t_now − shot.t_exit)` (the exit happened inside the step).
  - Launched balls get `LAUNCHED_BITS` (they ignore the robot) for `LAUNCH_CLEAR_S = 0.15` s of flight, then
    `BALL_BITS`: a documented choice, so a ball whose exit point lies inside the bumper volume flies out.
  - On exit the ball's `BallLog` gets `fired_t`, `robot_pose`, `robot_velocity`, `hood_deg`, `shooter_rps`, `exit_speed`,
    `spin_rpm`, `exit_slip`, `lane`, `launcher_status`. The robot, hood and shooter values are those of the world
    step in which the ball exits. A non-`launched` status puts the ball in `stuck` with that
    outcome.
  - A missed ball's `ball_log()` entry explains itself: `first_hit`, `apex_z` against the nearest goal's rim height,
    and `last_pos`/`at_rest`.

- [ ] **Step 1: Write the failing tests:**
  - `test_exit_point_and_velocity_at_rest`: robot at rest, heading 90°: the ball appears at the transformed exit
    point and its velocity is the launch velocity rotated by 90° (abs 1e-9).
  - `test_robot_velocity_added`: robot moving at (2, 0) m/s: the ball's velocity is the launch velocity + (2, 0, 0).
  - `test_robot_rotation_added`: robot spinning at 3 rad/s: the ball gets ω × r at the exit point.
  - `test_backspin_axis_and_robot_omega`.
  - `test_exit_inside_bumper_flies_out` (Review Focus 1): a launcher mounted so the exit point lies inside the
    chassis bumper's collision box: the ball leaves at its launch speed (rel 5% after 0.1 s) and no MuJoCo warning.
  - `test_fired_ball_log_complete`: a scored shot's `ball_log()` entry has every firing field finite, `outcome
    "scored"`, its `goal` and `scored_t`.
  - `test_missed_ball_says_why`: a shot short of the hub has `outcome "on_field"`, `apex_z` below the rim,
    `first_hit` = `carpet`, and `at_rest` True after 5 s.
  - `test_stuck_ball_logged`: a gap ≥ ball diameter gives `launcher_status "no_contact"`, outcome `stuck`.
  - `test_shoot_and_score_end_to_end`: a robot 3 m from the blue hub, shooter at a speed and hood from a manual
    search in the test (documented in its docstring), feeding 4 preloaded balls, scores ≥ 1 and the ledger is
    intact; two identical runs give identical `ball_log()`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_range_launch.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `launch.py` and the runtime changes.
- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_range_*.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** (`Test range: launched balls in the world, per-ball log`).

### Task 8: Signals, checks, scenarios, report, NetworkTables

**Files:**
- Modify: `scrimmage/checks.py` (signals, `ByTimeCheck`, `CHECK_KINDS`, `SIGNALS_HELP`), `scrimmage/scenario.py`
  (`TOP_KEYS`, the kinds in the error text at ~:190, `by_time` structure checks, `range` and `robot_env` keys),
  `scrimmage/scenario_runner.py` (`Report`, applying the scenario's range and env), `scrimmage/session.py`
  (`state()`), `scrimmage/assembler/signals.py`, `scrimmage/assembler/scenario_validate.py`,
  `scrimmage/assembler/static/step_editor.js` (`CHECK_KINDS`), `scrimmage/assembler/static/scenario.js` (the
  `WHERE` regex), `scrimmage/assembler/static/report.js`, `scrimmage/nt_reader.py`, `scrimmage/bridge.py`
  (`_log_row`)
- Test: `tests/test_range_checks.py`; additions to `tests/test_scenario_browser.py` and `tests/test_nt_and_recorder.py`

**Interfaces:**
- Consumes: `RangeRuntime.counts()`, `ball_log()`, `ball_poses()`, `reset()` (Tasks 4–7).
- Produces:
  - **Signals** (`resolve_signal`): `score`, `score.<goal>`, `fired`, `held`, `on_field`, read from
    `world.test_range.counts()`. Without a range they raise `ScenarioError("<name> needs a robot on a test range:
    add [range] to the config (see docs/test-range.md)")`; an unknown goal lists the range's goals.
  - **`by_time` check:** `by_time = { signal = "score", min = 8, t = 15 }` (keys of `at_end`, plus `t`, seconds from
    the step's start). It ticks; it passes if the condition holds at any tick with step time ≤ t. Messages:
    `score reached 8 at t=12.3 (by 15)` / `score was 6 at t=15; expected >= 8 by t=15`. `t` beyond the step's
    duration is a `ScenarioError` at load. `CHECK_KINDS` gains `by_time` everywhere it is listed.
  - **Scenario keys:**
    - `[range]` with `file`, `start`, `preload`, `floor_balls`: override the config's `[range]` for this run. The
      runner applies them to `config.raw` before building its own session. On a passed-in session, `file` must
      match the session's range (else `ScenarioError` naming both), and `start`, `preload` and `floor_balls` are
      applied by `world.test_range.reset(...)` before the robot restarts.
    - `robot_env = { NAME = "value" }`: merged into the robot program's environment for this run (own session),
      or for the restart (passed-in session; `Session.restart_robot(env: dict | None = None)`). The sample uses it
      to pick its auto.
  - **Report:** `Report.range: dict | None` (`counts()` at the end of the run) and `Report.balls: list[dict]`
    (`ball_log()`), captured before the session stops, in `to_dict()`. `report.md` gains `## Score` (per goal,
    fired, held) and `## Balls` (one row per ball: fired t, x, y, heading, vx, vy, ω, hood°, rps, exit m/s, spin
    rpm, slip, outcome, goal and time, or rest point / first hit). `report.js` renders both sections.
  - `Session.state()["range"]` = `counts()` without `ledger`, when there is a range.
  - **NetworkTables:** `NetworkTablesLink.publish_balls(poses: np.ndarray) -> None`, a `Pose3d` struct array on
    `/MuJoCo/Balls` (set up beside the ground-truth publisher, under the same `wpimath` import guard), called from
    `_log_row` with `ball_poses()` when there is a range.
  - `assembler/signals.signals(world)` gains `"range": ["score", "score.<goal>", …]` when there is a range.

- [ ] **Step 1: Write the failing tests:**
  - `test_score_signals_read_counts`: on a range world with a scripted scored ball, `score`, `score.hub_blue`,
    `fired`, `held` and `on_field` read the runtime's counts.
  - `test_score_signal_without_range_explains` (Review Focus 5): `ScenarioError` whose text contains `[range]`.
  - `test_scenario_range_mismatch_on_passed_session` (Review Focus 5): a passed-in session on range A with a
    scenario naming range B gives a report error naming both files, before any step runs.
  - `test_unknown_goal_lists_goals`.
  - `test_by_time_passes_when_reached_early`, `test_by_time_fails_with_value_at_t`,
    `test_by_time_t_beyond_step_rejected`.
  - `test_scenario_range_overrides_config`: `[range] start = "blue_right"`, `preload = 3` → the robot starts there
    with 3 held.
  - `test_robot_env_reaches_program`: with `tests/fake_robot.py`, a `robot_env` variable is visible to it (it
    prints it; a `console` check finds it).
  - `test_report_has_balls_and_score`: `report.json` has `range` and `balls`; `report.md` has both sections.
  - `test_report_shows_ball_table` (browser, in `tests/test_scenario_browser.py`): the report page lists one row
    per ball with its outcome.
  - `test_nt_publishes_ball_poses` (in `tests/test_nt_and_recorder.py`, skipped without `ntcore`): `/MuJoCo/Balls`
    holds one `Pose3d` per ball on the field.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_range_checks.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the signals, `ByTimeCheck`, scenario keys, report, state and NT changes.
- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_range_checks.py tests/test_scenario*.py tests/test_runner.py
  tests/test_nt_and_recorder.py -v` and `.venv/bin/pytest -m browser tests/test_scenario_browser.py -v`.
  Expected: PASS.
- [ ] **Step 5: Commit** (`Test range: score signals, by_time checks, per-ball report, NT ball poses`).

### Task 9: Live view

**Files:**
- Modify: `scrimmage/assembler/static/live3d.js` (hide parked movers; score overlay),
  `scrimmage/assembler/static/readouts.js` (score readouts), `scrimmage/assembler/server.py` (`_watch_run`
  state gains `range`)
- Test: additions to `tests/test_live_frontend.py`, `tests/test_live_server.py`

**Interfaces:**
- Consumes: `Session.state()["range"]` (Task 8); `PARK_Z` (Task 3); `view.model_view` (existing: hub plates arrive
  in `world_geoms` as static geoms, balls as body movers with a sphere geom).
- Produces:
  - `LiveView.applyFrame`: a mover whose z < −10 is hidden (`group.visible = false`), shown again when it rises.
  - `LiveView.setScore(range: {goals, fired, held} | null)`: an overlay in the view's top-left corner, one line per
    goal (`hub_blue 7`) then `fired 9 · held 3`; hidden for `null`. Called from both live and run-watch state
    messages.
  - Run-watch state messages: `{"source": "run", …, "range": counts or null}`.
  - Ball spheres use 12 × 8 segments (software GL renders hundreds of them).

- [ ] **Step 1: Write the failing tests:**
  - `test_parked_movers_hidden` (browser): a frame with one mover at z = −20 hides its group; a later frame at
    z = 0.075 shows it.
  - `test_score_overlay_ticks` (browser): `setScore({goals: {hub_blue: 1}, fired: 2, held: 3})` then
    `{hub_blue: 2}`; the overlay text updates; `null` hides it.
  - `test_range_world_view_has_hub_and_balls` (`tests/test_live_server.py`): `/api/live/view` for a range config
    has `world_geoms` named `hub_blue…` and one body mover per ball.
  - `test_watch_state_carries_range`: a scenario-run watch state message includes `range`.
- [ ] **Step 2: Run** `.venv/bin/pytest -m browser tests/test_live_frontend.py -v` and
  `.venv/bin/pytest tests/test_live_server.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Check by eye with `scrimmage edit` on `tests/range_fixtures`' config written to a
  file: the hubs, the funnel and the balls show, parked balls don't, and the counter ticks.
- [ ] **Step 4: Run** the tests again. Expected: PASS.
- [ ] **Step 5: Commit** (`Test range: balls, hubs and score counter in the live view`).

### Task 10: Aiming table from the launcher model

**Files:**
- Create: `scrimmage/testrange/aim.py`
- Modify: `scrimmage/__main__.py` (`launcher aim` subcommand)
- Test: `tests/test_launcher_aim.py`

**Interfaces:**
- Consumes: `parse_shooter`, `ShooterConfig` (Task 5); `Range` (Task 2); `Launcher`, `VelocityController`, `fly`,
  `Setup` (launcher).
- Produces:
  - `AimRow(distance: float, wheel_rps: float, hood_deg: float, flight_s: float, apex: float)`.
  - `aim_table(config_path: Path, goal: str, distances: list[float], *, hood_step_deg: float = 2.0,
    rps_range: tuple[float, float] = (10.0, 120.0)) -> list[AimRow]`.
  - CLI: `scrimmage launcher aim CONFIG --goal NAME --distances FROM:TO:STEP [--csv FILE] [--java FILE --java-class
    pkg.Name]`. The CSV has the `AimRow` fields as columns; the Java file is a `final` class with
    `public static final double[][] ROWS` (rows in `AimRow` order) and a header comment giving the command that
    made it and the `UNCALIBRATED` line from `launcher/report.py`. Bad input exits 2 with `scrimmage: <message>`.
  - The algorithm (not determined by the signature):
    ```
    geometry from the config's assembled model at its start pose: the launcher shaft's height h and its forward
      offset f from the robot center (robot facing the goal)
    for each distance d (robot center to goal center, horizontal):
      for each hood angle a from hood_range lo to hi in hood_step_deg steps (the fixed angle only, for a fixed hood):
        solve wheel rps r in rps_range by bisection (to 0.01 rps) so that one ball, fed at the setup's entry speed
          into a Launcher whose shaft is held at r by VelocityController and flown by fly() (goal at distance d − f,
          height = rim height, launcher.height = h), crosses rim height descending at the goal center
          (Landing.at_goal x == d − f); skip a if no r in range brackets it
      keep the a with the highest apex (steepest entry); ties: the lower r
      no a works: the distance is reported in an error listing the reachable range
    ```

- [ ] **Step 1: Write the failing tests** (a small config: two distances, `hood_step_deg = 10`):
  - `test_rows_hit_goal_center`: flying each row with `fly()` crosses rim height within 0.05 m of the goal center.
  - `test_table_repeatable`: two runs give identical CSV bytes.
  - `test_java_output_compiles_shape`: the Java file has the class name, `ROWS` with one row per distance, and the
    `UNCALIBRATED` line.
  - `test_unreachable_distance_explains`: 30 m exits 2 naming the reachable range.
  - `test_fixed_hood_single_angle`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_launcher_aim.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `aim.py` and the CLI.
- [ ] **Step 4: Run** the tests. Expected: PASS, in under 60 s.
- [ ] **Step 5: Commit** (`Launcher: aiming table from the model`).

### Task 11: The fast-mode sample shoots

**Files:**
- Create (in `examples/java_fastmode/src/main/java/frc/robot/`): `subsystems/shooter/Shooter.java`,
  `ShooterIO.java`, `ShooterIOScrimmage.java`, `ShooterIOTalonFX.java`, `AimTable.java` (generated by Task 10's
  command), `commands/ShootCommands.java`
- Modify: `RobotContainer.java` (the shooter, the autos, `SCRIMMAGE_AUTO` picks the default auto),
  `ScrimmageConstants.java` (shooter ids, gearing, configs); PathPlanner paths and autos under
  `src/main/deploy/pathplanner/`
- Create: `configs/fastmode_range.toml`, `configs/fastmode_range_phoenix.toml`,
  `scenarios/fastmode_shoot_left.toml`, `scenarios/fastmode_shoot_right.toml`,
  `scenarios/fastmode_shoot_moving.toml`, `scenarios/fastmode_shoot_phoenix.toml`,
  `scenarios/fastmode_shoot_speed.toml`
- Test: `tests/test_range_e2e.py` (`pytest.mark.java`)

**Interfaces:**
- Consumes: everything above; the fast-mode library as it is (`ScrimmageKraken` velocity, Motion Magic and voltage
  requests; WPILib `DigitalInput` for beam breaks). If the sample turns out to need something new from the
  library, add it there, bump `FASTMODE_VERSION` in `scrimmage/java_tools.py`, re-vendor with `scrimmage java
  vendordep`, and keep `tests/test_fastmode_vendordep.py` passing. Otherwise the library is not touched.
- Produces:
  - **The robot** (made up, not 5098's): the fast-mode sample's drive plus a two-lane hooded launcher (two Krakens
    on the shaft, `hood = "articulated"`), a two-stage feeder (hopper floor, feeder), an intake roller and beam
    breaks at the feeder and the launcher entry. Fast-mode devices `shooter_lead`, `shooter_follower`, `hood`,
    `floor`, `feeder`, `intake`; the Phoenix config binds the same parts to TalonFX ids. Both configs use
    `[range] file = "2026"`.
  - **Aiming:** `Shooter` looks up wheel rps and hood angle from `AimTable` by distance to the alliance hub. On the
    move it aims at a virtual target, the hub minus robot velocity × the table's `flight_s` (iterated twice), and
    the drive rotates to face it. It feeds only with the wheel within 2% of target, the hood within 1° and the
    heading within 2°.
  - **Autos** (chosen by `SCRIMMAGE_AUTO`, default unchanged): `Shoot Left` and `Shoot Right` (start at the range's
    `blue_left` / `blue_right`, shoot the preload standing), `Shoot Moving` (from `blue_center`: fire the preload
    while driving out, intake floor balls in the neutral zone, fire again on the way back).
  - **Regenerating the table:** `scrimmage launcher aim configs/fastmode_range.toml --goal hub_blue --distances
    1.5:6.0:0.25 --java examples/java_fastmode/src/main/java/frc/robot/subsystems/shooter/AimTable.java
    --java-class frc.robot.subsystems.shooter.AimTable`, written in `AimTable.java`'s header and in the docs.
  - **Scenarios:** fast mode: `[range] start` and `robot_env.SCRIMMAGE_AUTO` per auto, a `disabled` step then an
    `auto` step, `by_time` and `at_end` checks on `score.hub_blue` and `fired`, with thresholds equal to the
    measured repeatable values (the measurements in comments, as `fastmode_auto.toml` does). Phoenix mode: `Shoot
    Left`, with thresholds about 60% of fast mode's (the reason in a comment). Speed: `Shoot Moving` with
    `floor_balls = false` and 8 balls preloaded.

- [ ] **Step 1: Write the failing tests** in `tests/test_range_e2e.py` (module fixture builds as
  `tests/test_fastmode_e2e.py` does):
  - `test_shoot_left_scores`, `test_shoot_right_scores`, `test_shoot_moving_scores`: each fast-mode scenario passes.
  - `test_fast_mode_scores_repeat_exactly`: two runs of `fastmode_shoot_moving.toml` give equal `report.json`
    `range` and `balls`, and equal `run.csv`.
  - `test_range_fast_mode_faster_than_real_time`: the speed scenario's config in a `Session` (`sim_mode="fast"`,
    `[range] floor_balls = false`), 2 s warm-up then 4 s timed in auto, faster than real time (as
    `test_fast_mode_is_faster_than_real_time`).
  - `test_phoenix_mode_shoot_scenario_passes`: skips when the machine couldn't keep up (as the existing Phoenix
    test does).
  - `test_aim_table_is_current`: regenerating `AimTable.java` into a temp file gives the committed bytes.
- [ ] **Step 2: Run** `.venv/bin/pytest -m java tests/test_range_e2e.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the Java subsystem, autos, paths, configs and scenarios. Build with
  `cd examples/java_fastmode && ./gradlew build`. Run each scenario with `scrimmage run <scenario>`, watch the
  moving auto in `scrimmage edit`, and set the thresholds from the measured scores.
- [ ] **Step 4: Run** `.venv/bin/pytest -m java tests/test_range_e2e.py tests/test_fastmode_e2e.py -v`.
  Expected: PASS (the existing fast-mode tests unchanged).
- [ ] **Step 5: Commit** (`Fast-mode sample: two-lane hooded shooter, aiming table, shooting autos`), with the
  measured scores and speed factors in the message.

### Task 12: Documentation

**Files:**
- Create: `docs/test-range.md`
- Modify: `docs/launcher.md`, `docs/fast-mode.md`, `README.md`
- Test: `tests/test_range_docs.py`

`docs/test-range.md` covers:
- what the range is, the `[range]` and `[shooter]` sections, every key with units, and a complete example;
- the range file format, the geometry conventions (goal frame, opening, tilt, funnel, scoring surface), and how
  the 2026 numbers were sourced (CAD, manual, FreeCAD route for the FUEL layout);
- the launcher part's parameters, the hood joint's zero, the fixed hood;
- lanes, stages, the hopper, the intake zone, beam breaks, and the hopper/intake hooks (`FedBall`, zone numbers);
- balls in the world: states, parking, the restitution mapping and its drop tests, flight forces, the 0.15 s launch
  clearance, sleeping (or its absence) and the measured full-layout speed;
- signals, `by_time`, the scenario `[range]` and `robot_env` keys, the report's ball table, `/MuJoCo/Balls`;
- the aiming-table command and the sample's autos;
- what isn't modeled: the hub's exits and active/inactive timing, bumps and trenches, feeder loads on motors,
  calibration.

`docs/launcher.md`: the in-world changes (external bus, run-time hood, `FedBall` as the hopper interface in the
world) and the `aim` command. `docs/fast-mode.md`: the sample's shooter and its scenarios. `README.md`: a short
section linking to `docs/test-range.md`.

- [ ] **Step 1: Write the failing test** `test_docs_range_examples_parse`: every ```toml block in
  `docs/test-range.md` that starts with `[range]` or `[shooter]` parses with `tomllib`, and the complete example
  (marked `<!-- complete -->`) builds a `World`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_range_docs.py -v`. Expected: FAIL (no doc).
- [ ] **Step 3: Write** the docs.
- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_range_docs.py -v` and the whole non-Java suite,
  `.venv/bin/pytest -m "not java and not browser"`. Expected: PASS; the new range tests together under a few
  minutes (`--durations=20` to check).
- [ ] **Step 5: Commit** (`Docs: test range and robot-code shooting`).
