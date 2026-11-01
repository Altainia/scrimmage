# Uneven Field Elements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** bumps, ramps and tilting platforms on the simulated field. Robot terrain signals (ground gap, wheels down, air
time, tilt, tipped, stranded) that scenario checks can use. A `terrain sweep` command that finds how fast a robot can
take an element in each configuration. A sample auto and scenarios that test a robot over the 2026 BUMP.

**Architecture:**
- Elements are a new `[[element]]` table in range files, parsed in `scrimmage/testrange/elements.py`. They are
  built as box geoms, plus one hinged body for a platform, inside `add_range`.
- Drive wheels are marked by a new geom key `tread`. They get a contact stiffness from new drivetrain parameters.
- A `TerrainMonitor` (`scrimmage/terrain/monitor.py`) runs after every physics step. It ray-casts down from each
  tread wheel, counts contacts and peak-holds the signals between scenario ticks.
- The sweep (`scrimmage/terrain/sweep.py`) drives the robot with a Python servo through a World hook, not a robot
  program.

**Tech Stack:** Python 3.11+, MuJoCo ≥ 3.4 (MjSpec, mj_ray), pytest; Java (WPILib, fast-mode sample) for Task 8.

**Spec:** docs/superpowers/specs/2026-10-29-field-elements-design.md

## Global Constraints

- **Physics.**
  - Timestep ≤ 1 ms. Lockstep runs stay bit-identical run to run.
  - A world without elements, and without a scenario reading a `robot.*` or `element.*` signal, creates no
    TerrainMonitor and does no extra work per step.
- **Measured geometry, in inches as the spec gives them:**
  - 2026 BUMP: 44.40 deep, 73.0 wide, 6.51 tall, two 15.0° HDPE ramps, an 8 mm crest ridge, 0.61 in toe lips. Crest
    on the HUB center line, 182.1 in from the alliance wall. Four bumps at (±3645, ±1522) mm from the field center.
  - 2023 CHARGE STATION: 96 × 48 in platform, 9⅛ in high when level, ±15° tilt. Double hinge with axes 5.30 in apart
    and 6.57 in up. 15⅛ in ramps, LEVEL within 2.5°. 94.8 kg, 24.6 kg·m² about its CoM, which is 7.27 in up.
  - Estimates are marked "estimate" in comments and docs.
- **Thresholds:** tipped is tilt > 60°, latched until reset. Stranded is fewer than 2 tread wheels on the carpet or an
  element for > 0.5 s while not airborne. Both are named module constants.
- **Sweep defaults:** gap limit 0.05 m, tilt limit 30°, never tipped or stranded.
- **Balls never count as ground:** not for the ray, not for `wheels_down`.
- **License:** never transform com.ctre classes, interpose CTRE natives, decompile Phoenix or use non-public C APIs.
- **Commits:** end every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **CI budget:** don't push. All verification is local, including the Java e2e tests (Task 8). The PR opens after
  2026-11-01.

## Review Focus

1. A range `[start.*]` pose that overlaps an element, or floor balls inside an element's footprint. Expected: a
   RangeError at load naming both. (Task 1 test.)
2. `World.reset()` with a platform: the platform comes back level and still. `reset_robot()` leaves it as it is.
   (Task 2 test.)
3. `robot.*` signals on a config with no range (plain carpet) work, with the monitor created on demand. A robot with no
   `tread` geoms (hand-written MJCF) raises a ScenarioError for the wheel-based signals and still gives tilt, pitch,
   roll and z. (Task 4 tests.)
4. A robot upside down or on its side: tilt comes from the chassis up-vector, so it reads 90–180° with no gimbal flip,
   and `tipped` latches. (Task 4 test.)
5. A hop shorter than one 5 ms scenario tick is still seen by an `always` check through the peak-hold. (Task 4 test.)

---

### Task 1: Element spec in range files

**Files:**
- Create: `scrimmage/testrange/elements.py`
- Modify: `scrimmage/testrange/spec.py` (`Range` gains `elements`; `parse_range` reads `[[element]]`; `_TOP` gains `element`)
- Test: `tests/test_range_elements.py`

**Interfaces:**
- Produces:
  - `Bump(name, center: (x, y), yaw_deg, depth, width, height, ramp_deg, crest, lip, friction)`. Lengths are in m and
    `crest` is the flat-top length; frozen dataclass.
  - `Ramp(name, center, yaw_deg, width, height, ramp_deg, deck, two_sided: bool, friction)`.
  - `Platform(name, center, yaw_deg, length, width, height, max_tilt_deg, hinge_height, hinge_spacing, ramp_length,
    mass, inertia, com_height, damping, friction)`. `damping` is in N·m·s/rad and is an estimate.
  - `Element = Bump | Ramp | Platform`.
  - `parse_elements(path, tables) -> tuple[Element, ...]`.
  - `footprint(e) -> np.ndarray`: the (4, 2) corners in field coordinates.
  - `Range.elements: tuple[Element, ...] = ()`.
- TOML keys: `kind = "bump" | "ramp" | "platform"` plus the fields above. Lengths may be given as `<key>_in` in inches;
  give exactly one of `<key>` and `<key>_in`. Defaults: `friction` 0.6, the HDPE estimate; `yaw` 0; `crest` 0.008;
  `lip` 0.0155; `two_sided` true.

- [ ] **Step 1: Write the failing tests**

```python
def test_bump_in_inches_parses_to_meters(tmp_path): ...       # depth_in=44.40 -> depth == approx(1.12776)
def test_unknown_kind_and_missing_keys_name_the_element(tmp_path): ...   # RangeError mentions "element 'b1'"
def test_duplicate_element_names_rejected(tmp_path): ...
def test_start_overlapping_an_element_rejected(tmp_path): ...  # RangeError names the start and the element
def test_floor_ball_inside_an_element_rejected(tmp_path): ...
def test_range_without_elements_unchanged(): ...               # load_range("2026").elements == () before Task 8
```

- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_range_elements.py -q`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** `elements.py` and the `spec.py` changes. For the overlap checks, use the robot's frame plus
  bumpers as an outline at each start pose, and use `footprint(e)`. Model the outline as a 0.9 m square and say so in
  a comment.
- [ ] **Step 4: Run the tests.** Expected: PASS. Then run the whole `tests/test_range_*.py`. Expected: PASS.
- [ ] **Step 5: Commit** "Range files: [[element]] bumps, ramps and platforms".

### Task 2: Building elements, and the range files

**Files:**
- Modify: `scrimmage/testrange/build.py` (`add_range` calls `add_element` for each element)
- Create: `scrimmage/testrange/build_elements.py`
- Create: `scrimmage/testrange/ranges/2026-bump.toml`, `ranges/2023-charge-station.toml`, `ranges/terrain-library.toml`
- Modify: `BUILTIN_RANGES` in `spec.py`
- Test: `tests/test_range_elements_build.py`

**Interfaces:**
- Produces:
  - `add_element(spec: mujoco.MjSpec, e: Element) -> None`.
  - Body naming: a static element is body `element.<name>`, welded. A platform is body `element.<name>` with hinge
    joint `element.<name>.tilt`, or two hinges for the double hinge (the builder chooses, and documents which).
  - All element geoms use FIELD_BITS and the geom group `ELEMENT_GROUP = 4`. The carpet plane in
    `parts/field.py` also moves to group 4, which is how the TerrainMonitor's ray finds ground and only ground.
  - `RangeLayout.platforms: dict[str, int]`: name to the qpos address of its tilt.
- Range files:
  - `2026-bump.toml`: the 2026 ball preset, one bump centered at (4.625, 2.513) with the crest across x. Also a
    `[start.approach]` 1.5 m before the toe facing the bump, and a row of 6 floor balls 0.3–0.6 m past the far toe.
  - `2023-charge-station.toml`: ball preset `2026-fuel`, which isn't used (preload 0), the CHARGE STATION and
    `[start.approach]`.
  - `terrain-library.toml`: a generic two-sided ramp, the 2016 ramparts (two 2 ft × 22 in ramps at 8°) and the 2012
    bridge (48 × 88 in deck, 12 in high, ±15° tilt, an estimate).

- [ ] **Step 1: Write the failing tests**

```python
def test_bump_profile_matches_measurement():
    # mj_ray down at the crest hits z == approx(6.51 * 0.0254, abs=1e-3); halfway up a ramp the slope from two rays
    # is approx(15 deg, abs=0.2); a ray 2 cm past the toe hits the carpet (z 0)
def test_ray_in_group_4_ignores_balls_and_robot(): ...
def test_platform_tilts_to_its_stop_under_a_weight_on_one_end():
    # a 60 kg box dropped 1 m from the center: tilt settles at approx(15, abs=0.5) deg within 2 s
def test_platform_settles_level_unloaded(): ...           # within 2.5 deg (LEVEL) after 3 s from a 10 deg start
def test_reset_levels_the_platform_reset_robot_does_not(): ...
def test_builtin_element_ranges_load_and_compile(): ...   # each new range file builds a World
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
  - A bump is two rotated boxes, one per ramp, each of thickness `0.5 in`, plus a crest box and two lip boxes. Their
    top surfaces form the measured profile.
  - A ramp is a rotated box for each slope plus a deck box.
  - A platform is a body on its hinge(s) at `hinge_height`. It carries the mass and inertia given, a joint range of
    ±`max_tilt_deg` with a stiff limit, and the damping given. Its deck and ramps are boxes; the ramps hang off the
    deck, as on the real station.
- [ ] **Step 4: Run the tests and `tests/test_range_*.py`, `tests/test_part_physics.py`.** Expected: PASS.
- [ ] **Step 5: Commit** "Range elements: built from boxes, platforms on a hinge; bump, charge station and library
  ranges".

### Task 3: Tread wheels

**Files:**
- Modify: `scrimmage/parts/model.py` (geom key `tread: bool`, in `GEOM_KEYS`)
- Modify: `scrimmage/parts/assemble.py` (`Built.tread_geoms: list[str]`; a tread geom's solref)
- Modify: `scrimmage/parts/stock/swerve.py` and `differential.py` (mark wheels `tread`; Params `tread_stiffness` N/m,
  `tread_damping` ratio)
- Test: `tests/test_tread.py`

**Interfaces:**
- Produces:
  - `Built.tread_geoms`: the full names of every geom with `tread = true`.
  - `tread_solref(stiffness: float, damping_ratio: float, mass_share: float) -> tuple[float, float]` in
    `parts/assemble.py`. `mass_share` is the robot's total mass over its number of tread geoms.
- Defaults are the builder's, documented with sources: TPU or Colson tread, roughly 1–3e5 N/m per wheel for a 4 in
  wheel, damping ratio about 1.

- [ ] **Step 1: Write the failing tests**

```python
def test_stock_wheels_are_tread(): ...                 # swerve: 4 tread geoms; differential: one per wheel
def test_static_sag_matches_stiffness():
    # a resting swerve robot's wheel penetration == approx(m_share * g / k, rel=0.25)
def test_landing_does_not_bounce_like_a_ball():
    # robot dropped flat from 0.15 m: the first rebound's peak ground clearance < 0.02 m
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the entire non-Java suite**, `.venv/bin/python -m pytest -q -m "not java"`, or the repo's marker
  (check `pyproject.toml`).
  - For each test whose number moves, re-baseline it. Record `<test>: old → new` in the report file.
  - If a test changes meaning (a different outcome, not a shifted number), stop and report BLOCKED with the case.
- [ ] **Step 5: Commit** "Drive wheels: tread stiffness and damping".

### Task 4: TerrainMonitor and the robot signals

**Files:**
- Create: `scrimmage/terrain/__init__.py`, `scrimmage/terrain/monitor.py`
- Modify: `scrimmage/world.py`:
  - `World.terrain: TerrainMonitor | None`, created when the range has elements or by `World.terrain_monitor()` on
    demand;
  - `World.step` calls `terrain.after_physics()` after `mj_step`, when present;
  - reset clears it.
- Modify: `scrimmage/checks.py` (`resolve_signal` handles `robot.*` and `element.<name>.angle`; `SIGNALS_HELP`)
- Modify: `scrimmage/assembler/signals.py` (the `fixed` list)
- Modify: `checks.py`'s tick path, so each tick reads and then clears the peak-hold
- Test: `tests/test_terrain_signals.py`

**Interfaces:**
- Produces, in `monitor.py`:
  - Constants `TIP_DEG = 60.0`, `STRANDED_S = 0.5`, `STRANDED_WHEELS = 2`.
  - `TerrainMonitor(world)` with:
    - `after_physics()`;
    - `sample() -> dict[str, float]`: the values since the last `consume()`, peak-held (largest gap, air_time, tilt,
      |pitch| and |roll| with sign kept, max z; min wheels_down; latched tipped, stranded);
    - `consume()`;
    - `reset()`;
    - `first_tipped: tuple[float, float, tuple] | None`, as (t, tilt_deg, (x, y, heading));
    - `first_stranded: tuple[float, str] | None`, as (t, what the chassis rests on: a geom or body name);
    - `platform_angle(name) -> float`, in degrees.
  - Signal names: `robot.ground_gap`, `robot.wheels_down`, `robot.air_time`, `robot.pitch`, `robot.roll`,
    `robot.tilt`, `robot.z`, `robot.stranded`, `robot.tipped`, `element.<name>.angle`.
- Rules:
  - Pitch and roll are the Pigeon 2's (pitch nose-down positive, roll left-up positive), in degrees, equal to
    `bindings.pigeon_attitude`.
    Reuse it.
  - Ground gap is the minimum over tread wheels of `max(0, ray hit distance from the wheel's lowest point)`, with the
    ray in `ELEMENT_GROUP` only. It is 0 if any tread wheel touches the carpet or an element.
  - `air_time` counts while no robot geom touches any non-robot geom: the robot is truly in the air. A robot
    stranded on balls is resting, not airborne, so its `air_time` is 0.

- [ ] **Step 1: Write the failing tests**

```python
def test_flat_carpet_reads_zero_gap_four_wheels_level(): ...      # plain config, no range: monitor made on demand
def test_dropped_robot_reports_gap_and_air_time(): ...           # drop from 0.10 m: peak gap approx(0.10, abs=0.01)
def test_robot_on_balls_is_stranded_and_gap_is_height_above_carpet():
    # robot lowered onto 4 FUEL under its frame: wheels_down < 2, stranded == 1 after 0.5 s, gap > 0.03,
    # first_stranded names a ball body
def test_upside_down_robot_tilt_and_tipped_latches(): ...         # rotated 120 deg about x: tilt approx(120), tipped 1
def test_peak_hold_sees_a_hop_shorter_than_a_tick(): ...          # a 3 ms gap spike seen by an always check at 5 ms ticks
def test_mjcf_robot_without_tread_errors_for_wheel_signals_only(): ...
def test_platform_angle_signal(): ...
def test_no_monitor_without_elements_or_signals(): ...            # World(fastmode cfg).terrain is None after 100 steps
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Keep the per-step work vectorized over tread wheels: one `mj_ray` each, plus one pass
  over `data.contact[:ncon]` using geom→body→tree lookups built once.
- [ ] **Step 4: Run the tests, plus `tests/test_checks*.py` and `tests/test_scenario*.py`.** Expected: PASS.
- [ ] **Step 5: Commit** "Terrain signals: ground gap, wheels down, air time, tilt, tipped, stranded".

### Task 5: Report notes for tipping and stranding

**Files:**
- Modify: `scrimmage/scenario_runner.py`: at each step's end, if `world.terrain` has `first_tipped` or
  `first_stranded` set during the step, append a note
- Test: `tests/test_terrain_report.py`

**Interfaces:**
- Note text (exact):
  - `the robot tipped over at t={t:.2f} s (tilt {tilt:.0f}°, at x={x:.2f}, y={y:.2f}){config}`
  - `the robot was stranded at t={t:.2f} s, resting on {what}`
  - `{config}` is `, with ` + the moved joints' positions (`joint.<n>.position = v`) for any non-root joint more than
    0.05 from its rest, else empty.

- [ ] **Step 1: Write the failing test.** Run a two-step scenario on `2026-bump.toml` with a robot placed tipped.
  The report's notes contain the tipped line once, and report.md shows it.
- [ ] **Step 2: Run it.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the test and `tests/test_runner.py`.** Expected: PASS.
- [ ] **Step 5: Commit** "Reports note when the robot tipped or was stranded".

### Task 6: Physics checked against hand calculations

**Files:**
- Test: `tests/test_terrain_physics.py`
- Modify: the defaults in Task 2 (HDPE friction) and Task 3 (tread), if the tests require it. Record any change and why
  in the report.

**Interfaces:**
- Consumes: Tasks 2–4. It uses a test-only helper `push(world, vx)` that sets the robot's free-joint velocity and wheel
  spin consistently. The sweep's driver comes in Task 7.

- [ ] **Step 1: Write the tests**

```python
def test_flight_off_a_ramp_matches_projectile_motion():
    # a ramp (single-sided, 15 deg, deck then drop) at 3 m/s: the chassis CoM's landing x matches
    # x0 + v cos(15) t with t solved from the drop height, within 10 % (the robot is not a point: tolerance stated)
def test_tip_angle_on_a_slope_matches_geometry():
    # a robot parked sideways on ramps of increasing angle (2 deg steps): first tipping angle is within 3 deg of
    # atan(half track / CoM height) computed from the built model's subtree CoM
def test_charge_station_tilt_follows_robot_position(): ...   # robot centered: |angle| < 2.5; robot on one end: 15 +- 0.5
def test_raising_the_elevator_lowers_the_tip_angle(): ...    # fastmode robot, elevator at 0 vs 1.0 m
```

- [ ] **Step 2: Run them.** Fix the defaults or the model until they pass. Never loosen a tolerance stated here
  without recording a ruling in the report.
- [ ] **Step 3: Commit** "Terrain physics checked against projectile and tipping hand calculations".

### Task 7: `terrain sweep`

**Files:**
- Create: `scrimmage/terrain/drive.py`, `scrimmage/terrain/sweep.py`
- Modify: `scrimmage/world.py` (`World.external: Callable[[mujoco.MjData], None] | None`, called after the motors
  drive and before `mj_step`)
- Modify: `scrimmage/__main__.py` (`terrain sweep` subcommand)
- Create: `docs/terrain.md`
- Test: `tests/test_terrain_sweep.py`

**Interfaces:**
- Produces:
  - `StraightDriver(world, speed: float, holds: dict[str, float])`, a callable for `World.external`. It does three
    things:
    - holds every swerve azimuth joint at 0 rad, the robot's +x;
    - servos tread wheel joints to `speed / r`;
    - PD-holds each joint in `holds` at its value.
    For a differential drive, it servos its driven wheel joints.
  - `sweep(config_path, element: str, speeds: list[float], holds: dict[str, float], limits: Limits) -> SweepResult`.
  - `Row(speed, peak_gap, air_time, landing, peak_pitch, peak_roll, tipped, stranded, landed_on_balls)`.
  - `Limits(gap=0.05, tilt=30.0)`.
  - `SweepResult.rows`, and `SweepResult.limit_speed: float | None`: the fastest speed whose row and all slower rows
    pass.
- CLI: `scrimmage terrain sweep CONFIG --element NAME --speeds a:b:step [--set joint=value]... [--gap M]
  [--tilt DEG] [--out DIR]`. It writes `sweep.csv` and `sweep.md`.
- Behavior:
  - The robot starts at the range's `[start.approach]`, or 1.5 m before the element's near toe facing it.
  - The run ends 1.5 s after the robot's CoM passes the far toe, or at 6 s.
  - `landing` is the CoM's x distance past the crest at the first touchdown after air time > 0, or blank.

- [ ] **Step 1: Write the failing tests**

```python
def test_sweep_rows_and_limit_on_the_2026_bump(): ...   # speeds 1:4:1 -> 4 rows, peak_gap non-decreasing, limit set
def test_holding_the_elevator_up_changes_peak_pitch(): ...
def test_cli_writes_csv_and_md(tmp_path): ...
def test_unknown_element_or_joint_errors_clearly(): ...
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement**, and write `docs/terrain.md`. It covers:
  - elements in range files;
  - the signals and their exact meanings;
  - the sweep, with an example table;
  - the estimates: tread, HDPE friction, platform damping and stops.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** "terrain sweep: how fast a robot can take an element, per configuration".

### Task 8: Sample robot, 2026 bumps, scenarios and re-verification

**Files:**
- Modify: `scrimmage/testrange/ranges/2026.toml`, to add the four bumps at (±3645, ±1522) mm from field center,
  converted to field coordinates as the HUB entries are.
- Create: `configs/fastmode_bump.toml`: the `configs/fastmode.toml` robot, with `[range] file = "2026-bump"` and
  `start = "approach"`.
- Modify: `examples/java_fastmode`:
  - an auto "Over the Bump" that drives 4 m straight at `BUMP_SPEED`, a constant in `ScrimmageConstants.java` or the
    drive constants;
  - an auto "Over the Bump, Elevator Up" that raises the elevator to 0.9 m first.
  - Re-vendor if the library changed; it shouldn't.
- Create:
  - `scenarios/fastmode_bump.toml`: auto "Over the Bump", `always robot.ground_gap max 0.05`, `always robot.tipped equals 0`;
  - `scenarios/fastmode_bump_elevator_up.toml`: the same, plus `always robot.tilt max 30`.
- Test: `tests/test_terrain_e2e.py` (Java marker): both scenarios pass at the sample's `BUMP_SPEED`. A copy of the
  first scenario with `robot_env` setting a higher speed (from the sweep) fails on `robot.ground_gap`.
- Re-verify the shooting scenarios against the bumped 2026 range: the five `fastmode_shoot_*` scenarios and
  `tests/test_range_e2e.py`. If a start or check must change, change it and record old → new in the report.
- Docs: `docs/test-range.md` mentions elements and links `docs/terrain.md`. README lists the new scenarios.

- [ ] **Step 1: Pick `BUMP_SPEED`** from `scrimmage terrain sweep configs/fastmode_bump.toml --element bump --speeds
  0.5:4:0.25`. Use the limit speed rounded down to 0.25 m/s, and do the same with `--set` for the elevator up. Put
  the two sweep tables in the report.
- [ ] **Step 2: Write the e2e tests**, then run them. Expected: FAIL (no autos yet).
- [ ] **Step 3: Implement the autos, config and scenarios.**
- [ ] **Step 4: Run** `tests/test_terrain_e2e.py`, `tests/test_range_e2e.py`, `tests/test_fastmode_e2e.py` and the
  non-Java suite. Expected: all PASS.
- [ ] **Step 5: Commit** "Sample: over the 2026 bump, with the ground-gap and tilt scenarios; 2026 range gets its
  bumps".
