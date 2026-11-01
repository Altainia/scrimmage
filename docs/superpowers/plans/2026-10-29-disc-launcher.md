# Disc Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:**
- A planar disc launcher model, and Hummel 6-DOF disc flight shared by a standalone CLI and the MuJoCo world.
- 2013 slot goals, a disc shooter robot part, and a Python-robot scenario that scores from the auto line.

**Architecture:**
- A new package, `scrimmage/disc/`:
  - `presets.toml` and `inputs.py`: the disc, the setup and the coefficient sets;
  - `aero.py`: forces and moments, the single source of truth;
  - `flight.py`: standalone RK4 on a quaternion rigid body;
  - `launcher.py`: the planar contact model, reusing `launcher.shaft` and `launcher.contact.friction_force`;
  - `run.py`, `sweep.py` and `report.py` glue that reuses `scrimmage/launcher`'s sweep and report.
- The test range gains:
  - a `rect` opening;
  - disc bodies (cylinder geoms) with in-world aero applied as force and torque through `xfrc_applied`, using the
    same `aero.py`;
  - a disc variant of the shooter.

**Tech Stack:** Python 3.11+, numpy, MuJoCo ≥ 3.4, pytest.

**Spec:** docs/superpowers/specs/2026-10-29-disc-launcher-design.md

## Global Constraints

- **Disc `2013-frisbee`:**
  - From the manual: d 0.2794 m, m 0.180 kg, height 0.0356 m.
  - Estimates: Izz 0.0026, Ixx = Iyy 0.00135 kg·m²; rim stiffness k_d 5000 N/m.
  - Every preset value is tagged `# source:` or `# estimate:`.
- **Aerodynamics:**
  - The default coefficient set is `pc-long`: Potts & Crowther tables (C_L, C_D and C_M against α, and C_Rr against
    α and AdvR), with C_Mq −0.0144, C_Rp −0.0125 and C_Nr −3.41e-5 in Hummel's dimensional form.
  - Other sets: `hummel-short` (Table 3-2) and `hh02`.
  - A = πd²/4; moment arm d; ρ default 1.225.
  - The forms are copied from Hummel's thesis App. A/B code
    (/tmp/claude-1000/-home-altainia-code-scrimmage/ac449d14-9b89-46fa-a7f9-a841ce5dbaa6/scratchpad/dl2/HummelThesis.pdf),
    and cited by page in code comments.
- **2013 goals (manual §2.2.3):**
  - HIGH: 54 × 12 in, bottom 104⅛ in, centered on the wall.
  - MIDDLE ×2: 54 × 21 in, bottom 88⅝ in, either side of HIGH (centers ±58 in, an estimate).
  - LOW: 29 × 24 in, bottom 19 in.
  - Goal box depth 24 in (estimate).
- **Capacity:** 4 discs (G24). **Auto line:** 216 in from the wall.
- **Physics:**
  - World timestep ≤ 1 ms, integrator unchanged (implicitfast).
  - Lockstep stays bit-identical.
  - Ball ranges and the ball launcher are unchanged; their existing tests pass untouched.
- **License:** never transform com.ctre classes, interpose CTRE natives, decompile Phoenix or use non-public C APIs.
- **Commits:** end every message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **CI:** never push. Pin floats with tolerances, never exactly.

## Review Focus

1. A disc with zero or tiny spin, or tumbling at α beyond the tables (up to ±90°): the aero must stay finite and
   continuous, with no NaN. (Task 1 test.)
2. A launcher whose wheels are stopped or slower than the disc: no energy creation, and status `no_wheel` or slowing.
   (Task 3 test.)
3. A disc that hits a goal frame or the wall instead of the opening: it collides (cylinder–box), bounces and isn't
   scored. (Task 5 test.)
4. A range with `disc =` and a config with a ball `[shooter]` (or the reverse): a clear config error naming the
   mismatch. (Task 6 test.)
5. A disc resting flat on the carpet: aero applies only to airborne discs, and a disc on the floor doesn't drift.
   (Task 5 test.)

---

### Task 1: Disc presets and aerodynamics

**Files:**
- Create: `scrimmage/disc/__init__.py`, `presets.toml`, `inputs.py` (the Disc and Aero parts only), `aero.py`
- Test: `tests/test_disc_aero.py`

**Interfaces:**
- Produces:
  - `Disc(diameter, mass, height, izz, ixx, rim_stiffness, rim_damping_ratio)`, frozen.
  - `AeroSet(name, ...)`, and `aero_set(name) -> AeroSet`.
  - `coefficients(aero: AeroSet, alpha: float, adv_ratio: float) -> dict` with keys CL, CD, CM and CRr.
  - `forces(disc: Disc, aero: AeroSet, rho: float, vel: np.ndarray, rot: np.ndarray, omega_body: np.ndarray) ->
    tuple[np.ndarray, np.ndarray]`. It returns the world-frame force and the body-frame torque. `rot` is the
    body-to-world 3×3 matrix, and the body z is the disc axis.
  - `PRESETS`.

- [ ] **Step 1: Write the failing tests**

```python
def test_preset_2013_frisbee_values(): ...             # d 0.2794, m 0.180, izz 0.0026 ...
def test_pc_tables_reproduce_tabulated_points(): ...   # CL(10 deg) == approx(0.725), CD(0) == approx(0.100), CM(9 deg) == approx(0, abs=1e-3)
def test_zero_lift_angle_near_minus_4_deg(): ...       # pc-long: CL(alpha0) == 0 at alpha0 in [-4.5, -3.5] deg
def test_forces_finite_and_continuous_over_all_alpha(): ...   # -90..90 deg in 0.5 deg steps, |jump| bounded
def test_lift_is_perpendicular_to_velocity_and_drag_opposes_it(): ...
def test_zero_spin_has_no_division_by_zero(): ...
def test_lift_at_12_mps_alpha0_is_about_half_weight(): ...  # 0.4-0.7 of m g for pc-long
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Copy the forms and tables from the thesis App. A/B code, citing the pages.
  - α is the angle between the velocity and the disc plane, positive when the velocity hits the underside.
  - The aero frame is Hummel's frame F.
  - Beyond the tables, extend lift to stall at 45° and go flat-plate beyond that; document it.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** "Disc: presets and Hummel aerodynamics".

### Task 2: Standalone disc flight

**Files:**
- Create: `scrimmage/disc/flight.py`
- Test: `tests/test_disc_flight.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `DiscState(pos, vel, quat, omega_body)`.
  - `fly(state, disc, aero, rho, goals=(), dt=1e-3, max_time=10.0) -> DiscFlight`.
  - `DiscFlight(samples, landing, crossing: tuple[str, float, np.ndarray] | None, apex, flight_time)`.
  - A goal for `fly` is `(name, Surface)`, using `testrange.spec.Surface` and its `crossed`.
  - Integration is RK4 on position, velocity, quaternion (renormalized) and body ω, with Euler's equations for
    diag(Ixx, Ixx, Izz).

- [ ] **Step 1: Write the failing tests**

```python
def test_no_aero_is_a_parabola(): ...                   # aero set "none" (all zero): matches analytic to 1e-6 m
def test_angular_momentum_conserved_without_aero(): ...  # |H| constant to 1e-9 over 2 s
def test_hummel_f2302_matches_s3():
    # f2302 initial state (thesis Table 3-3, converted from his z-down frame) with the Ultra-Star preset and
    # pc-long: range 15.2 +- 0.5 m, final xdot 8.6 +- 0.5 m/s, lateral curve to the same side as S-3 (left)
def test_frc_shot_reaches_high_goal_band():
    # 2013-frisbee, 12.2 m/s, rolling spin (r Omega / V = 1), from 0.76 m on the auto line (5.486 m from the wall):
    # some launch angle in [20, 28] deg puts the center within the HIGH opening's height band at the wall
def test_no_lift_misses_by_over_a_meter(): ...          # same shot with lift zeroed: > 1 m lower at the wall
def test_bank_side_follows_spin_direction(): ...        # CCW from above banks left at alpha 0-5; CW banks right
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Add a `ultra-star` disc preset (thesis Table 3-1) for the f2302 test. If f2302 can't meet
  the stated tolerances with an exact transcription, record why in the report. Then apply the spec's fallback (range
  within 1 m and side only), and record it as a ruling request.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** "Disc flight: Hummel 6-DOF, checked against f2302 and an FRC shot".

### Task 3: Planar disc launcher model

**Files:**
- Create: `scrimmage/disc/launcher.py`
- Modify: `scrimmage/disc/inputs.py` (`Layout`, `DiscSetup`, `parse_setup` and `load_setup`, reusing
  `launcher.inputs` Wheels, Shaft, Control, BatteryIn, Robot, Feed and Results)
- Test: `tests/test_disc_launcher.py`

**Interfaces:**
- Produces:
  - `Layout(kind: "linear" | "arc" | "pinch", wheels: tuple[WheelSpot, ...], fence_friction, fence_radius,
    channel_length, compression, rail_friction)`.
  - `WheelSpot(x, side: "left" | "right", group: int)`.
  - `DiscLauncher(setup)`, with `.shafts: list[ShaftModel]` (one per group), `insert(t) -> bool`,
    `step(dt, voltages: list[float]) -> list[ExitedDisc]`, `in_contact()` and `default_dt()`.
  - `ExitedDisc(index, status, t_enter, t_exit, speed, spin, yaw_deg, exit_xy, energy, slip_energy)`. `spin` is in
    rad/s, positive counter-clockwise seen from above with the channel horizontal.
- Contact: per wheel and fence contact, the normal force is `k_series * overlap + damping * overlap_rate` with
  `k_series = 1/(1/k_wheel + 1/k_rim)`, where k_wheel is `launcher.contact.wheel_stiffness` from the wheel preset.
  Tangential force: `launcher.contact.friction_force`. The arc's centripetal load comes out of the geometry
  naturally.

- [ ] **Step 1: Write the failing tests**

```python
def test_single_wheel_exit_ratio_in_2013_range(): ...  # 60A 4 in roller at 6000 rpm, 1/4 in compression: V / Vs in [0.35, 0.48]
def test_rolling_launch_advance_ratio_near_one(): ...  # r*spin/V in [0.8, 1.1] with a grippy fence
def test_slick_fence_gives_little_spin(): ...          # fence mu 0.15: advance ratio < 0.4
def test_pinch_equal_speeds_near_zero_spin(): ...      # |advance ratio| < 0.05; unequal gives the predicted sign
def test_wheel_side_sets_spin_sign(): ...              # wheel right -> spin > 0 (CCW from above)
def test_energy_balance(): ...                         # shaft energy lost >= disc KE (translational + spin) + slip
def test_stopped_wheel_does_not_launch(): ...          # status != "launched", no energy created
def test_wheel_droop_reported(): ...                   # 4 in roller droops more than an 8 in pneumatic at equal surface speed
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Use semi-implicit substeps at `default_dt()`, chosen from the contact frequency. If the
  60A test can't reach the band with the presets, report the ratio found and which estimate drives it. Don't tune
  silently.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** "Disc launcher: planar wheel-and-fence contact model".

### Task 4: CLI, examples and docs

**Files:**
- Create: `scrimmage/disc/run.py`, `examples/disc/linear.toml`, `arc.toml`, `pinch.toml`, `docs/disc.md`
- Modify: `scrimmage/__main__.py` (a `disc` subcommand with `run`, `sweep` and `table`)
- Test: `tests/test_disc_cli.py`

**Interfaces:**
- `run_setup(setup) -> RunResult`: discs fed, launched and flown with flight from Task 2.
- `scrimmage disc run FILE [--out DIR]`, `disc sweep FILE [--out DIR]` (`[sweep]` dotted keys through
  `launcher.sweep`) and `disc table FILE --rpm a:b:step`.
- Output: results.csv and summary.md, reusing `launcher.report`.

- [ ] **Step 1: Write the failing tests**

```python
def test_examples_run(tmp_path): ...          # each examples/disc/*.toml runs, writes csv/md, and exits 0
def test_sweep_over_wheel_rpm(tmp_path): ...  # exit speed non-decreasing in rpm until slip saturates
def test_table_prints_rows(tmp_path): ...
def test_bad_setup_exits_2_with_message(tmp_path): ...
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Write docs/disc.md. It covers:
  - the model, with its equations;
  - the coefficient sets and citations;
  - the estimates and how to measure each: inertia by bifilar pendulum, rim stiffness by plates and a scale, tread
    friction by inclined plane, exit speed and spin by 240 fps video;
  - the CLI, with example output.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** "disc run, sweep and table, with examples and docs".

### Task 5: Discs and slot goals in the world

**Files:**
- Modify:
  - `scrimmage/testrange/spec.py`: a `RectOpening(width, height)` opening kind; `[range] disc = "<preset>"` as the
    alternative to `ball`; `Range.disc: Disc | None`.
  - `build.py`: cylinder geom and diagonal inertia for discs; slot goals as a wall frame plus a box.
  - `balls.py`: for discs, the flight force and torque from `disc.aero.forces` into `xfrc_applied[:, 0:6]`, with the
    torque converted to world frame; no rolling resistance for discs.
- Create: `scrimmage/testrange/ranges/2013.toml`
- Test: `tests/test_range_discs.py`

**Interfaces:**
- `Range.piece` is the Ball or the Disc, and `Range.ball` stays for ball ranges.
- Goal TOML: `opening = { kind = "rect", width_in = 54, height_in = 12 }`, with the goal's frame as today. The scoring
  `Surface` is the opening rectangle.

- [ ] **Step 1: Write the failing tests**

```python
def test_2013_range_loads_and_builds(): ...
def test_world_flight_matches_standalone_within_5cm(): ...   # same exit state, 5 m, no collisions; same bank side
def test_disc_through_high_goal_scores(): ...
def test_disc_hitting_the_frame_bounces_and_does_not_score(): ...
def test_disc_resting_flat_does_not_drift(): ...             # 2 s on the carpet: < 1 mm
def test_ball_ranges_unchanged(): ...                        # the existing rangeless and 2026 goldens still pass
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Floor discs aren't needed: the 2013 range places discs only as preloads and spares.
- [ ] **Step 4: Run the tests, plus `tests/test_range_*.py`.** Expected: PASS.
- [ ] **Step 5: Commit** "Discs and 2013 slot goals in the world".

### Task 6: Disc shooter part and in-world launching

**Files:**
- Modify:
  - `scrimmage/parts/stock/mechanisms.py`: stock `disc_shooter`, with a base, wheel bodies with `spin` joints per
    group, a `tilt` joint for the launch angle, and mounts.
  - `scrimmage/testrange/shooter.py`: a `[shooter] kind = "disc"` path running `DiscLauncher` with external shafts
    on the battery.
  - `launch.py`: the disc exit state with orientation (the disc plane tilted to the channel's launch angle, spin about
    the disc axis, plus the robot's motion).
  - `ballpath.py`: disc spacing and a hopper capacity default of 4.
- Test: `tests/test_range_disc_shooter.py`

**Interfaces:** `exit_state_disc(shot: ExitedDisc, ...) -> (pos, vel, quat, ang_vel)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_disc_shooter_part_builds(): ...
def test_preloaded_discs_fire_in_world_and_fly(): ...   # 4 preloaded, the PWM drive spins the wheels, all 4 airborne
def test_in_world_shot_matches_standalone_exit(): ...   # exit speed and spin equal to DiscLauncher's for the same voltages, within 2 %
def test_ball_and_disc_mismatch_is_a_clear_error(): ...
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests, plus `tests/test_range_*.py`.** Expected: PASS.
- [ ] **Step 5: Commit** "Disc shooter: a robot part that launches discs in the world".

### Task 7: Scenario: score from the auto line

**Files:**
- Create: `configs/disc_robot.toml` (kitbot or swerve with `disc_shooter`, `[range] file = "2013"`, preload 4),
  `scenarios/disc_auto_line.toml`
- Create: `tests/test_disc_scenario.py`, using the `range_fixtures`-style Python robot (PWM drive, open-loop shooter
  voltage chosen for the target wheel speed)
- Modify: the README and docs/disc.md (the scenario section)

- [ ] **Step 1: Pick the tilt and voltage** with `scrimmage disc table` and the flight model so the shot lands in the
  HIGH goal from the auto line. Record the numbers.
- [ ] **Step 2: Write the test:** the scenario passes with `by_time score.high >= 3 by t=6` (4 fired). A copy with
  the tilt 8° lower fails on score.
- [ ] **Step 3: Implement the config and scenario.** How the test robot is driven follows `tests/fake_robot.py` and
  range_fixtures.
- [ ] **Step 4: Run** the test, the full non-Java suite, and `tests/test_range_e2e.py` (to show ranges are
  unchanged). Expected: PASS.
- [ ] **Step 5: Commit** "Disc scenario: four discs from the auto line into the high goal".
