# Launcher Model and Design Comparison (sub-project 8) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A physics model of a hooded single-shaft ball launcher (contact, flywheel, flight), plus
`scrimmage launcher run` and `scrimmage launcher sweep`, which report a table, a CSV file and charts.

**Architecture:**
- A new package, `scrimmage/launcher/`, with one file per concern:
  - **inputs and presets:** the setup TOML parsed into frozen dataclasses;
  - **contact laws;**
  - **shooter shaft:** motor, controller and battery;
  - **ball-in-launcher dynamics:** a time-stepped `Launcher` with `insert` and `step`;
  - **flight;**
  - **run orchestration:** feed generation, feed rules and metrics;
  - **sweep and report.**
- `Launcher.step(dt, voltage)` is the interface piece 3 will drive from MuJoCo and lockstep. Pieces 1 and 2 drive
  it from a Talon-style velocity controller.

**Tech Stack:** Python 3.11+, numpy (already required), `tomllib`, matplotlib (optional, charts only).

**Spec:** docs/superpowers/specs/2026-10-28-launcher-model-design.md

## Global Constraints

- **No new required dependency.** matplotlib is imported lazily, only for charts. Without it, the commands
  still write the table and CSV and print: `charts need matplotlib: pip install matplotlib`.
- **Units:**
  - the setup TOML is SI (meters, kilograms, seconds, newtons);
  - angles are in degrees with a `_deg` suffix;
  - shooter speed is rotations per second of the wheel shaft (`target_rps`), as Phoenix's velocity units are.
- **Existing models:**
  - motors come from `scrimmage.motors.get_motor(name, count)`;
  - battery sag uses `scrimmage.motors.Battery`, never a fixed voltage.
- **Uncalibrated label.** This exact text is the first line of every terminal table and appears on every chart:
  `UNCALIBRATED: estimates from preset material numbers, not fitted to measurements.`
- **No fudge factors.** Every constant in the physics is either:
  - a setup input; or
  - a documented modeling choice, with a code comment citing its source.
- **Repeatable.** All randomness comes from `numpy.random.default_rng(spread.seed)`. The same file gives
  byte-identical CSV output.
- **Speed.** `pytest tests/test_launcher*.py` runs in under 30 s locally.
- **Errors.** Bad input raises `LauncherConfigError(message)`, and the CLI prints `scrimmage: <message>` and
  exits 2. Physics outcomes (no contact, jammed) are results, never exceptions.
- **Hood geometry (the user's choice):**
  - the hood is concentric with the wheels and pivots about the shaft, at a constant gap;
  - the ball's entry is fixed on the robot;
  - the hood angle moves the exit end, so it changes the wrap.
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

### Geometry convention (used by tasks 2, 4 and 5)

The launcher's plane is the robot's x (forward) and z (up) plane, and the shaft runs along y.

The ball's position on its path is the angle φ of its center around the shaft, measured from +x toward +z.
- The ball enters at `hood.entry_deg` (default 240: behind the shaft and below it).
- It travels with φ decreasing, so it rides up the back of the wheel.
- It leaves at φ_exit = `hood.exit_angle_deg` + 90. Its velocity is then tangent to the path, pointing
  `exit_angle_deg` above horizontal and forward.
- The wrap is entry_deg − φ_exit. A setup where the wrap ≤ 0 is a `LauncherConfigError`.
- The center's path radius is R_c = R_wheel + gap/2.
- Squish δ = ball diameter − gap. Contact begins only when δ > 0.

## Review Focus

1. **The gap is at least the ball's diameter, or the squish is exactly zero.** Expected: the shot's status is
   `no_contact`, its exit fields are NaN, it is reported in the table, and nothing crashes. Test in Task 4.
2. **A ball is fed in a lane where no wheel lies under it.** Expected: status `no_wheel`, and the shaft is
   unaffected. Test in Task 4.
3. **A burst the shaft can't recover from** (a tiny motor, `wait_for_speed`). Expected: the run ends at
   `feed.timeout` and the balls not fed are reported as `unfed`. It must never loop forever. Test in Task 6.
4. **A typo in a setup or sweep key** (`hood.gpa`). Expected: `LauncherConfigError` naming the key and listing the
   valid keys of that section. Test in Task 1 (setup) and Task 7 (sweep).
5. **A shot that never reaches the goal's height, or one fired backwards by robot motion.** Expected: `scored`
   False, with the floor landing point reported. Test in Task 5.

---

### Task 1: Setup inputs and presets

**Files:**
- Create: `scrimmage/launcher/__init__.py`, `scrimmage/launcher/inputs.py`, `scrimmage/launcher/presets.toml`
- Modify: `pyproject.toml` (packages: add `scrimmage.launcher`; package-data: add `launcher/*.toml`)
- Test: `tests/test_launcher_inputs.py`

**Interfaces:**
- Produces:
  - `class LauncherConfigError(ValueError)`.
  - Frozen dataclasses, with these fields and defaults:
    - `Ball(diameter, mass, stiffness_n, stiffness_exponent=1.5, restitution)`. `stiffness_n` is the force in
      newtons that compresses the ball by 25% of its diameter between flat plates. `restitution` is the bounce
      coefficient from a drop on a hard floor. Both can be measured at home.
    - `Wheels(diameter, width, durometer, friction, positions=(0.0,), roller=False)`. `positions` are the
      centers along the shaft, in meters. `roller=True` means one continuous roller of length `width` centered
      at 0.
    - `Shaft(wheel_inertia, added_inertia=0.0, motor="KrakenX60", motor_count=1, gearing=1.0)`. `gearing` is
      motor rotations per wheel rotation.
    - `Control(target_rps, kS=0.0, kV, kP, stator_limit=None, supply_limit=None)`. Volts are per wheel
      rotation per second. `kV` defaults to the motor's 1/Kv scaled by gearing.
    - `BatteryIn(nominal_voltage=12.5, resistance=0.02)`.
    - `Hood(gap, entry_deg=240.0, exit_angle_deg, friction)`.
    - `Launcher(height, width)`. `height` is the shaft height above the floor; `width` is the inside width.
    - `Robot(velocity=(0.0, 0.0))`: the robot's vx and vy in m/s.
    - `Air(density=1.2, drag_coefficient, lift_slope, lift_max)`, with defaults cited in `presets.toml`.
    - `Goal(distance, height, diameter)`.
    - `Feed(count=10, rate=5.0, rule="wait_for_speed", tolerance=0.05, lanes=(0.0,), entry_speed=1.0,
      timeout=10.0)`.
    - `Spread(seed=1, diameter=0.01, mass=0.02, stiffness=0.05, feed_time=0.01, lane=0.005, entry_speed=0.1,
      yaw_deg=0.5, shooter_speed=0.0)`. Fractions are standard deviations relative to nominal; the others are in
      their units. These defaults are estimates, and `docs/launcher.md` says so.
    - `Results(recovery_tolerance=0.02)`.
    - `Setup(ball, wheels, shaft, control, battery, hood, launcher, robot, air, goal, feed, spread, results,
      dt=None)`.
  - `load_setup(source: Path | dict) -> tuple[Setup, dict[str, list], list[dict]]`. It returns the setup, the
    raw `[sweep]` table (dotted key to a list of values), and the raw `[[chart]]` list.
  - `apply_overrides(raw: dict, overrides: dict[str, object]) -> dict`. It sets the dotted keys on a deep copy,
    and raises `LauncherConfigError` for an unknown section or key.
  - `parse_setup(raw: dict) -> Setup`.
  - `PRESETS`: the parsed `presets.toml`.
- A section may name `preset = "<name>"`. That preset's values fill the section, and explicit keys override them.

- [ ] **Step 1: Write `presets.toml`.**
  - **Balls:** at least the FRC 2020/2021 Power Cell and the FRC 2022 Cargo, plus last season's game ball if its
    manual is findable. Size and mass come from the game manuals.
  - **Wheels:** shooter-wheel materials at 30A, 35A, 40A, 45A and 60A.
  - **Air:** drag and lift defaults.
  - **Sources:** every value carries a `# source:` comment (a URL or citation). Values that aren't published
    (ball stiffness and restitution, wheel friction) are marked `# estimate:`, with how they were estimated.
- [ ] **Step 2: Write the failing tests:**
  - `test_preset_fills_and_override_wins`: `ball = {preset="2020-power-cell", mass=0.2}` gives the preset's
    diameter and mass 0.2.
  - `test_unknown_preset_names_known_ones`.
  - `test_unknown_key_lists_valid_keys`: `hood.gpa` raises, and the message contains `gpa` and `gap`.
  - `test_missing_required_names_field`.
  - `test_wrap_must_be_positive`: entry_deg 120 with exit_angle_deg 60 (φ_exit 150) raises.
  - `test_apply_overrides_deep_copies`.
  - `test_every_input_is_sweepable`: every dataclass field path accepts an override.
- [ ] **Step 3: Run** `pytest tests/test_launcher_inputs.py -v`. Expected: FAIL (module missing).
- [ ] **Step 4: Implement** `inputs.py` and the pyproject changes.
- [ ] **Step 5: Run the tests.** Expected: PASS.
- [ ] **Step 6: Commit** (`Launcher: setup inputs and presets`).

### Task 2: Contact laws

**Files:**
- Create: `scrimmage/launcher/contact.py`
- Test: `tests/test_launcher_contact.py`

**Interfaces:**
- Consumes: `Ball`, `Wheels` (Task 1).
- Produces:
  - `normal_force(delta: float, delta_rate: float, ball: Ball, wheel_stiffness: float | None) -> float`:
    - a power law, `F = stiffness_n · (δ_ball / (0.25·D))^exponent`;
    - the ball in series with the wheel's compliance (when `wheel_stiffness` is given);
    - Hunt–Crossley damping, derived from `restitution`;
    - never negative.
  - `wheel_stiffness(wheels: Wheels, contact_width: float) -> float`. Shore A is converted to Young's modulus by
    a published relation (Gent 1958), and the source is cited in a comment.
  - `friction_force(slip: float, normal: float, mu: float) -> float`. Coulomb friction, regularized near zero
    slip by a tanh over a slip speed of 0.01 m/s (a documented choice).
  - `wheel_contact_fraction(lane_y: float, ball_d: float, delta: float, wheels: Wheels) -> float`. This is the
    fraction (0 to 1) of the ball's contact-patch width, about `sqrt(D·δ)`, that lies over wheel material.
  - `rolling_loss_torque(normal: float, ball: Ball, delta: float) -> float`. The hysteresis loss while rolling,
    from the same loss that `restitution` sets. The derivation is in the docstring.

- [ ] **Step 1: Write the failing tests:**
  - `test_force_at_reference_compression`: δ = 0.25·D at zero rate gives `stiffness_n` (rel 1e-9).
  - `test_drop_reproduces_restitution`: a ball integrated against a rigid plate at 3 m/s with the law rebounds
    at `restitution · 3` (abs 0.03), for restitution 0.3, 0.5 and 0.8.
  - `test_no_tension`: separating contact never pulls.
  - `test_wheel_compliance_softens`: a 30A wheel gives less force than a 60A wheel at the same total δ.
  - `test_friction_saturates_and_is_odd`.
  - `test_contact_fraction`: 1.0 for a centered ball on a wider wheel; 0.0 in a gap between wheels; between 0 and
    1 for a ball straddling a wheel edge; 1.0 anywhere on a roller.
- [ ] **Step 2: Run** `pytest tests/test_launcher_contact.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `contact.py`.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** (`Launcher: contact laws`).

### Task 3: Shooter shaft, controller and battery

**Files:**
- Create: `scrimmage/launcher/shaft.py`
- Test: `tests/test_launcher_shaft.py`

**Interfaces:**
- Consumes: `Shaft`, `Control`, `BatteryIn` (Task 1); `get_motor` and `Battery` (`scrimmage.motors`).
- Produces:
  - `class ShaftModel`:
    - `__init__(shaft: Shaft, battery: BatteryIn)`;
    - attributes `.speed` (wheel rad/s), `.inertia`, `.stator_current`, `.supply_current`, `.bus_voltage`;
    - `step(dt, voltage, load_torque) -> None`. The motor torque comes through the gearing; the load torque
      (from the balls) opposes it. The bus voltage is the battery's sag from the previous step's supply current.
  - `class VelocityController`:
    - `__init__(control: Control, shaft: Shaft)`;
    - `voltage(shaft_model) -> float`. It computes `kS·sign + kV·target + kP·error` in wheel rotations per
      second, then clamps:
      - to ±bus voltage;
      - to the voltage at which the stator current equals `stator_limit`;
      - to the duty that keeps supply current ≤ `supply_limit`.

- [ ] **Step 1: Write the failing tests:**
  - `test_spins_up_to_target`: within 2% of target by 3 s, for a Kraken at 1:1 with 0.002 kg·m².
  - `test_heavier_spins_up_slower`.
  - `test_stator_limit_never_exceeded`: limit 40 A, so the maximum stator current is ≤ 40.5 A over the
    spin-up.
  - `test_supply_limit_never_exceeded`.
  - `test_battery_sags_under_load`: the bus voltage during spin-up is below nominal, and it recovers at steady
    speed.
  - `test_free_speed_cap`: an impossible target settles at the free speed for the bus voltage (rel 2%).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `shaft.py`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** (`Launcher: shooter shaft, controller, battery`).

### Task 4: Ball-in-launcher dynamics

**Files:**
- Create: `scrimmage/launcher/model.py`
- Test: `tests/test_launcher_model.py`

**Interfaces:**
- Consumes: Tasks 1–3, and the geometry convention above.
- Produces:
  - `FedBall(available_at: float, lane: float, entry_speed: float, ball: Ball, yaw_deg: float = 0.0)`: the feed
    interface a future hopper supplies.
  - `ExitedBall(index, status, t_enter, t_exit, speed, spin, launch_deg, yaw_deg, lane, energy: Energy)`:
    - `status` is one of `"launched"`, `"no_contact"`, `"no_wheel"` or `"jammed"`;
    - `spin` is in rad/s, positive for backspin;
    - the exit fields are NaN unless the ball was launched.
  - `Energy(from_shaft, entry_kinetic, exit_linear, exit_spin, slip_loss, hysteresis_loss)`, in joules.
  - `class Launcher`:
    - `__init__(setup: Setup)`;
    - `.shaft: ShaftModel`;
    - `insert(fed: FedBall, t: float) -> None`;
    - `step(dt: float, voltage: float) -> list[ExitedBall]`;
    - `in_contact() -> int`;
    - `default_dt() -> float`.
  - Each ball has two degrees of freedom: its position φ along the path (its speed is v) and its spin ω. Its
    radial position is held by the hood at the constant gap.
  - The normal force at the hood equals the force at the wheel plus m·v²/R_c (curvature).
  - Friction acts at both contacts, from their slip speeds. The rolling loss comes from Task 2.
  - The balls' reaction torque goes into `ShaftModel.step`.
  - A launched ball's status is final when φ reaches φ_exit.
  - A ball still in contact after 0.5 s is `"jammed"` (a documented choice).

- [ ] **Step 1: Write the failing tests.** Use a fixed shaft speed where noted, via a helper that holds
  `shaft.speed`.
  - `test_energy_balances`: for each launched ball, from_shaft + entry_kinetic equals exit_linear + exit_spin +
    slip_loss + hysteresis_loss (rel 1%).
  - `test_no_slip_no_loss_limit`: with friction 5.0, restitution 0.99 and a held surface speed V, the exit speed
    is V/2 (rel 5%) and the spin is V/(gap) (rel 5%).
  - `test_squish_trend`: as δ/D runs over [0.02, 0.10, 0.20, 0.40], exit speed rises from 0.02 to the peak, and
    the 0.40 value is below the peak.
  - `test_hood_angle_trend`: a larger exit_angle_deg gives a larger launch_deg and a longer contact time.
  - `test_gap_at_least_diameter_is_no_contact`: gap = D and gap = 1.2·D both give status `no_contact`, NaN exits
    and no exception.
  - `test_lane_without_wheel`: wheels at ±0.1 m with a width of 0.05 m, and a ball in lane 0, gives `no_wheel`
    with the shaft speed unchanged.
  - `test_side_by_side_dips_more`: two balls inserted together in lanes under two wheels drop the shaft speed
    more than one ball does (open loop, voltage 0).
  - `test_step_converges`: halving dt changes the exit speed by < 1%.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `model.py`. The integrator is your choice; it must pass the convergence test.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** (`Launcher: ball-in-launcher dynamics`).

### Task 5: Flight and scoring

**Files:**
- Create: `scrimmage/launcher/flight.py`
- Test: `tests/test_launcher_flight.py`

**Interfaces:**
- Consumes: `ExitedBall` (Task 4); `Air`, `Goal`, `Robot`, `Launcher` and `Ball` (Task 1).
- Produces:
  - `Landing(scored: bool, at_goal: tuple[float, float] | None, floor: tuple[float, float], apex: float,
    flight_time: float)`:
    - `at_goal` is the (x, y) point where the ball crosses the goal's height while descending;
    - `floor` is where it reaches the floor.
  - `fly(shot: ExitedBall, setup: Setup, ball: Ball) -> Landing`:
    - the ball starts at the exit point (the shaft height plus R_c times the sine of φ_exit; x likewise);
    - its velocity is the exit speed along the launch and yaw angles, plus the robot's velocity;
    - it flies under drag `½ρC_dAv²` and lift `½ρC_LAv²`, where `C_L = min(lift_slope·S, lift_max)` and
      `S = rω/v` (the lift is perpendicular to the velocity, in the plane given by the spin axis).
  - Scored: the ball crosses the goal's height descending, with its center within (goal.diameter − ball
    diameter)/2 of (goal.distance, 0).

- [ ] **Step 1: Write the failing tests:**
  - `test_vacuum_is_parabola`: with drag 0 and lift 0, the apex and range match the closed form (abs 1 mm).
  - `test_drag_shortens`.
  - `test_backspin_lengthens`.
  - `test_robot_velocity_adds`: a forward robot velocity of 2 m/s lands further downrange; a sideways velocity
    moves the landing point sideways.
  - `test_short_shot_not_scored`: a shot whose apex is below the goal's height gives `scored` False,
    `at_goal` None and a floor point.
  - `test_backwards_robot_motion`: a robot velocity of -15 m/s gives `scored` False and no exception.
  - `test_center_hit_scores`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `flight.py`, with RK4 at 1 ms and the crossing interpolated linearly.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** (`Launcher: flight and scoring`).

### Task 6: Runs, feed rules and metrics

**Files:**
- Create: `scrimmage/launcher/run.py`
- Test: `tests/test_launcher_run.py`

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces:
  - `generate_feed(setup: Setup, rng) -> list[FedBall]`:
    - `feed.count` balls, made available at intervals of 1/`feed.rate`;
    - when there are several `feed.lanes`, each availability time delivers one ball per lane (side by side), and
      `count` is the total;
    - spread is applied per ball from `Spread`.
  - `run_setup(setup: Setup, feed: list[FedBall] | None = None) -> RunResult`:
    - it first spins up from rest until the shaft is within `feed.tolerance` of target (or the timeout);
    - then it feeds by `feed.rule`:
      - `"schedule"`: each ball enters at its availability time;
      - `"wait_for_speed"`: a ball also waits until the shaft is within tolerance;
    - it stops at `feed.timeout` after the first availability, and marks any remaining balls `"unfed"`;
    - it flies each launched ball;
    - `spread.shooter_speed` adds a per-ball random offset to the controller's target.
  - `RunResult` has:
    - `shots: list[tuple[ExitedBall, Landing | None]]`;
    - `trace: list[tuple[float, float]]` (time, shaft rps, decimated to at most 2000 points);
    - `metrics: dict[str, float]`. Its keys, in this order: `exit_speed`, `exit_speed_sd`, `spin_rpm`,
      `launch_deg`, `speed_drop_pct`, `recovery_s`, `burst_s`, `spread_downrange_m`, `spread_lateral_m`,
      `scored`, `fired`, `launched`, `jammed`, `unfed`.
  - The metrics are measured as follows:
    - **speed drop:** the mean, per ball, of the dip from its entry speed to the minimum before the next entry,
      as a percentage of target;
    - **recovery:** the mean time from each dip's minimum back to within `results.recovery_tolerance`;
    - **burst:** from the first entry to the last exit;
    - **landing spread:** standard deviations at the goal crossing (or the floor point if it never crosses).

- [ ] **Step 1: Write the failing tests:**
  - `test_same_seed_same_result` and `test_different_seed_differs`.
  - `test_heavier_flywheel_tradeoff`: with added inertia 0.004 against 0, the heavier wheel has a smaller
    `speed_drop_pct` and a larger `recovery_s`.
  - `test_wait_for_speed_slower_than_schedule`: with a light flywheel at rate 20, `burst_s` is larger under wait
    than under schedule.
  - `test_unrecoverable_burst_terminates`: a NEO550 at gearing 4 with added inertia 0.05, wait_for_speed and
    timeout 2 s, returns within 30 s of wall time with `unfed` > 0.
  - `test_side_by_side_lanes`: lanes (−0.08, 0.08) with count 4 enter two at a time.
  - `test_hopper_feed_is_used`: an explicit `feed` list overrides the generated one.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `run.py`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** (`Launcher: runs, feed rules, metrics`).

### Task 7: Sweep, report and CLI

**Files:**
- Create: `scrimmage/launcher/sweep.py`, `scrimmage/launcher/report.py`
- Modify: `scrimmage/__main__.py` (a `launcher` subparser with `run` and `sweep`; a `_launcher(args)` handler
  in the existing style)
- Test: `tests/test_launcher_cli.py`

**Interfaces:**
- Consumes: `load_setup`, `apply_overrides`, `parse_setup` (Task 1); `run_setup`, `RunResult` (Task 6).
- Produces:
  - `combinations(sweep: dict[str, list]) -> list[dict[str, object]]`: the cartesian product, in file order.
  - `run_sweep(path: Path) -> list[tuple[dict, RunResult]]`.
  - `UNCALIBRATED` (the exact text from Global Constraints), in `report.py`.
  - `format_table(rows) -> str`.
  - `write_csv(rows, path)`: the columns are the swept keys, then the metric keys in `RunResult.metrics`
    order, with floats at 4 significant figures.
  - `write_charts(rows, charts: list[dict], out_dir) -> list[Path] | None`. It returns None when matplotlib is
    missing. Chart kinds:
    - `{kind="line", x=<swept key>, y=<metric>, series=<swept key, optional>}`;
    - `{kind="landing"}`: a scatter of the goal-crossing points, one panel per combination, with the goal
      circle drawn;
    - `{kind="shaft"}`: shaft rps over time for each combination.
    With no `[[chart]]` section, the default is a line of `exit_speed` against the first swept key (the second
    swept key, if any, as the series), plus `shaft` and `landing`. Every chart's footer is `UNCALIBRATED`.
- CLI:
  - `scrimmage launcher run FILE [--out DIR]` and `scrimmage launcher sweep FILE [--out DIR]`;
  - `--out` defaults to `<FILE's directory>/<FILE stem>-results/`;
  - `run` ignores `[sweep]`, and `sweep` with no `[sweep]` section exits 2 with
    `no [sweep] section: use 'scrimmage launcher run'`;
  - output: `results.csv`, the chart PNGs and the table on stdout.

- [ ] **Step 1: Write the failing tests:**
  - `test_combinations_product_in_order`.
  - `test_sweep_typo_names_key`: `[sweep] "hood.gpa" = [...]` exits 2, and stderr contains `gpa` and `gap`.
  - `test_run_prints_uncalibrated_first`.
  - `test_csv_columns_and_repeatable`: running twice gives identical bytes.
  - `test_charts_written` (skipped if matplotlib is missing).
  - `test_no_matplotlib_message`: monkeypatch the import to fail; the CSV is still written, and the message is
    printed.
  - `test_sweep_without_section_exits_2`.
  Use a small setup (count 3) so that the tests run in seconds.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** `sweep.py`, `report.py` and the CLI.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** (`Launcher: sweep, report, CLI`).

### Task 8: Examples, documentation and sanity check

**Files:**
- Create: `examples/launcher/hooded.toml`, a single setup (Power Cell, 4 in 35A wheels, Kraken 1:1, goal at
  4 m).
- Create: `examples/launcher/flywheel_squish.toml`, which sweeps `shaft.added_inertia` over [0, 0.002, 0.004]
  and `hood.gap` over three squishes, with charts.
- Create: `examples/launcher/hood_angle.toml`, which sweeps `hood.exit_angle_deg`, with landing charts.
- Create: `docs/launcher.md`.
- Modify: `README.md` (a short section that links to the docs).
- Test: `tests/test_launcher_examples.py`

`docs/launcher.md` covers:
- what is modeled, and the hood geometry;
- every input, with units;
- the presets and their sources;
- the stiffness and restitution measurements a team can make at home: a squeeze between plates on a bathroom
  scale, and a drop test;
- what is accurate (the flywheel, energy and flight physics) and what is only a trend until calibration (the
  contact numbers);
- the spread defaults are estimates;
- `FedBall` as the hopper interface;
- the commands and their output.

- [ ] **Step 1: Write the failing tests:**
  - `test_examples_load_and_run`: each example runs through `run_setup`, or through `run_sweep` with feed.count
    overridden to 2.
  - `test_hooded_sanity_range`: with hooded.toml at `target_rps = 66.7` (4000 rpm), a launched exit speed lies between 8 and 20 m/s.
    This is a sanity range only, and the test's docstring says so.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Write the examples and docs.** Run `scrimmage launcher sweep
  examples/launcher/flywheel_squish.toml` and check by eye that the trends match the spec's "How we'll know it's
  right". Note the actual results in the commit message.
- [ ] **Step 4: Run** `pytest tests/test_launcher*.py -v`. Expected: PASS, in under 30 s.
- [ ] **Step 5: Commit** (`Launcher: examples and docs`).
