# Turret and Pneumatics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stock turret part with aiming and shooting in the Test tab, and pneumatic cylinders (a stock `cylinder`
part, a pneumatic system model, valves bound to robot code on a CTRE PCM or REV PH, Test-tab blocks and an air
budget).

**Architecture:**
- **Turret:** a plain stock part (a hinge on z). The range's shots already leave from the launcher's world frame,
  so the work is in the Test tab: run the `[shooter]` under blocks, plus the `scored` and `aimed` conditions and an
  `aim` block.
- **Pneumatics model:** a self-contained, unit-testable model in `scrimmage/pneumatics/` covering tanks, the
  compressor, the regulator, the valves, and two chambers per cylinder.
- **Pneumatics in MuJoCo:** a cylinder pushes along a MuJoCo spatial tendon between two pins, through an actuator
  whose control is the force. The world steps the model each physics step, reads valve states from the HAL (or
  from the Test tab's runner), and reports the PCM's or PH's inputs back.

**Tech Stack:** Python 3.11+, MuJoCo (MjSpec), HALSim WebSocket, the C++ HALSim lockstep extension (HAL C API),
Blockly (web app), pytest.

**Spec:** docs/superpowers/specs/2026-11-03-turret-pneumatics-design.md

## Global Constraints

- Robots with no turret, cylinder or `[pneumatics]` step exactly as before: `tests/test_range_ledger_golden.py` and
  every existing example test pass unchanged.
- Pressures are in psi in files, messages and signals (the one unit exception; everything else is m, kg, s, deg).
  1 psi = 6894.757 Pa, atmosphere = 101325 Pa.
- Working pressure ≤ 60 psi and stored pressure ≤ 120 psi. A refused value names the FRC rule ("FRC allows at most
  60 psi on the working side").
- Every pneumatic number is an estimate marked UNCALIBRATED in the docs and in the run's notes.
- No chamber may overshoot the pressure it fills from or exhausts to, at any timestep.
- American spellings and plain words in every student-facing string. Never run `uv run` in the repo root. Use
  `.venv/bin/python -m pytest`.
- The extension change uses only the HAL C API (`hal/...` headers), as `lockstep/src/scrimmage_lockstep.cpp` does.

## Planning decisions

- **D1 Model file:** the pneumatics model lives in `scrimmage/pneumatics/model.py`. It has no MuJoCo, so the
  component model can be tested on its own (the fidelity guideline). `scrimmage/pneumatics/runtime.py` binds it to a
  World.
- **D2 Cylinders are stock only:**
  - `Part` gains `cylinders: list[CylinderSpec]`, filled only by the stock `cylinder` builder.
  - The dict key `cylinder` is accepted by `part_from_dict` only when `source` is a stock source (it contains
    "(stock "). In a part.toml the key stays an unknown-key error, as today.
- **D3 Test-tab valves need no device ID.** The runner sets each valve's state through
  `PneumaticsRuntime.command(name, state)`, not through the HAL, so a stand-in robot with an unbound valve works
  under blocks.
  - This differs from motors, which need an ID. The reason: a valve is not a speed controller with gearing to
    honor, and the anti-goal is no friction. It costs nothing if wrong: a valve with an ID behaves the same.
- **D4 Free text in stock parameters:** stock `Param` gains kind `"text"`, a free string that may be empty. `push`
  uses it.
- **D5 The PH's SimDevice:** the extension creates `ScrimmagePH:<module>`, which the socket relays as type
  `ScrimmagePH`, device `"<module>"`.
  - **Outputs** (robot to sim): `solenoids`, an int bitmask of channels 0–15, and `closed_loop`, a bool that is
    true when the compressor config type isn't disabled.
  - **Inputs** (sim to robot): `pressure_switch` (bool), `compressor_on` (bool) and `compressor_current` (double).
    The extension copies each one into `HALSIM_SetREVPH*`.
  - Each relayed value's name gets halsim_ws's `<` or `>` prefix by its direction.
- **D6 Pressure switch value:** it is true while the compressor's switch calls for air, which is the hysteresis
  state. WPILib reads true as "not full" (verified in RobotPy 2026.2.2's `Compressor.getPressureSwitchValue`).

## Review Focus

1. **An unbound valve in the robot code path.** The cylinder stays retracted (at a single solenoid's off state) and
   there's one warning, `<inst>.valve has no device ID, so it won't move`. No crash. Test: Task 5.
2. **Both coils of a double solenoid on.** The cylinder holds its last position, and one warning names the valve.
   Test: Task 5.
3. **A push cylinder whose two pins can't move relative to each other** (no joint between the bodies). This is a
   PartError: "<inst>: the cylinder's two ends are on bodies that can't move relative to each other (nothing
   between lift.barrel and drive.chassis turns or slides)", naming the two pin bodies. Test: Task 4.
4. **Aiming at a goal outside the turret's range.** The target is clamped to the stop, `aimed` stays false, and a
   failed aimed check says "<goal> is outside the turret's range (it stops at 180°)". Test: Task 8.
5. **A run where the tanks fall below the working pressure.** The air line warns "the tanks fell below the 60 psi
   working pressure at 14.2 s: the cylinders pushed weaker from then on", and the regulator's output follows the
   tanks down. Test: Task 9.

---

### Task 1: The pneumatic system model

**Files:**
- Create: `scrimmage/pneumatics/__init__.py`, `scrimmage/pneumatics/model.py`
- Test: `tests/test_pneumatics_model.py`

**Interfaces:**
- Produces:
  - Constants: `PSI = 6894.757`, `ATM = 101325.0`, `T_AIR = 293.15`, `R_AIR = 287.05`, `RHO_ANR = 1.185`,
    `CFM = 4.719474e-4` (m³/s), `MAX_WORKING_PSI = 60.0`, `MAX_STORED_PSI = 120.0`.
  - `@dataclass(frozen=True) class Compressor: name: str; points: tuple[tuple[float, float, float], ...]`, where
    each point is (gauge psi, free-air cfm, amps). `COMPRESSORS = {"VIAIR 90C": Compressor("VIAIR 90C",
    ((0.0, 0.88, 7.0), (100.0, 0.22, 11.0)))}`, with methods `flow_cfm(psi) -> float` and `amps(psi) -> float`.
    Both are linear through the points, extrapolated past the last one, and the flow is clamped at ≥ 0.
  - `@dataclass(frozen=True) class AirConfig: tanks: int = 2; tank_ml: float = 574.0; compressor: str = "VIAIR
    90C"` (or `"none"`), with `working_psi: float = 60.0; start_psi: float = 120.0; switch_off_psi: float = 115.0;
    switch_on_psi: float = 95.0`.
  - `@dataclass(frozen=True) class CylinderSpec`:
    - fields `name: str; bore: float; rod: float; stroke: float; friction: float; conductance: float;
      dead_volume: float = 1e-6`, with `bore`, `rod` and `stroke` in m and `conductance` in m³/(s·Pa);
    - the pins: `pin_a: tuple[str, tuple]`, `pin_b: tuple[str, tuple]` and `length: float | None` (body name,
      local pos). Task 4 fills them.
    - **Default conductance:** `DEFAULT_CONDUCTANCE = 8e-9` (0.8 dm³/(s·bar)), with `CRITICAL_RATIO = 0.3`.
  - `class AirSystem(config: AirConfig, cylinders: list[CylinderSpec])`:
    - `reset()`;
    - `set_valve(name, state)`, where state is "extend", "retract" or "hold";
    - `step(dt, extension: dict[str, float], enabled: bool, closed_loop: bool) -> dict[str, float]`, which returns
      each cylinder's force in N (positive extends) and carries no friction (MuJoCo's tendon frictionloss handles
      friction);
    - properties `stored_psi`, `working_psi`, `compressor_on`, `compressor_amps`, `switch_calls_for_air`;
    - `chamber_psi(name) -> tuple[float, float]` (cap side, rod side, gauge).

- [ ] **Step 1: Write the failing tests** in `tests/test_pneumatics_model.py`:
  - **`test_compressor_curve`:**
    - `COMPRESSORS["VIAIR 90C"].flow_cfm(0) == 0.88` and `.amps(100) == 11.0`;
    - `flow_cfm(120)` is ≈ 0.088 (abs 1e-9);
    - the flow is never negative at 400 psi.
  - **`test_stall_force_is_pressure_times_area`:** one default cylinder (1-1/16 in bore = 0.0269875 m, 5/16 in
    rod = 0.0079375 m, 6 in stroke = 0.1524 m), held at extension 0 with the valve on "extend". After 1 s of
    `step(0.001, {"c": 0.0}, True, True)`, the force is within 1% of `60*PSI*pi*bore²/4` (about 221 N).
  - **`test_retract_force_uses_the_annulus`:** the same with "retract" at full extension gives within 1% of
    `-60*PSI*pi*(bore²-rod²)/4`.
  - **`test_no_overshoot`:** stepping at dt 0.001 and at dt 0.01 from an empty chamber, the cap-side pressure never
    exceeds the working pressure plus 1e-6 psi in any step.
  - **`test_air_used_per_stroke`:** start at 120 psi with no compressor. After one extend (extension stepped 0 to
    stroke over 0.5 s, then held 1 s), the tanks' drop in free-air volume is within 5% of
    `(pi*bore²/4*stroke + dead) * 60*PSI/ATM`.
  - **`test_switch_hysteresis`:**
    - start at 100 psi: the switch calls for air (true) and the compressor runs while enabled with the closed loop
      on;
    - it stops at 115;
    - it stays off until below 95;
    - with `enabled=False` it never runs.
  - **`test_regulator_follows_low_tanks`:** with the tanks at 40 psi, `working_psi == 40`.
  - **`test_double_hold_keeps_state`:** "hold" after "extend" keeps the cap side at the working pressure.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/test_pneumatics_model.py -q`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** `scrimmage/pneumatics/model.py`.
  - **State:** isothermal. Each chamber's state is its air mass m, with p = m·R·T/V. The cap volume is
    `dead + A_cap·x` and the rod volume is `dead + (A_cap − A_rod)·(stroke − x)`, with x clamped to [0, stroke].
  - **Flow:** ISO 6358 mass flow `q = C · p_up · RHO_ANR · φ(p_down/p_up)`, where φ = 1 for r ≤ b, else
    `sqrt(1 − ((r − b)/(1 − b))²)`.
  - **Sub-steps:** 10 per call. Each sub-step clamps the new mass so the pressure doesn't pass p_up when filling, or
    p_down when exhausting.
  - **Supply and exhaust:** the supply is the regulator, at `min(working_psi, stored)` absolute pressure. Mass drawn
    from it comes out of the tanks, whose volume is `tanks·tank_ml·1e-6`. The exhaust is the atmosphere.
  - **Compressor:** it adds `flow_cfm(stored)·CFM·RHO_ANR` kg/s to the tanks while on. It is on when
    `enabled and closed_loop and switch_calls_for_air`. The switch turns on below `switch_on_psi`, turns off at
    `switch_off_psi`, and starts in the "calling" state iff start_psi < switch_off_psi.
  - **Validation:** `AirConfig` rejects a working pressure above 60, a start pressure above 120, `switch_on >=
    switch_off`, and fewer than one tank (`ValueError` with a plain sentence).
  - **Starting state:** reset puts every chamber at the regulator pressure on the side its valve's start state
    feeds ("retract"), and at the atmosphere on the other side.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "pneumatics: an air system model (tanks, compressor, regulator, two-chamber cylinders)"`

### Task 2: The stock turret

**Files:**
- Modify: `scrimmage/parts/stock/mechanisms.py`
- Test: `tests/test_stock_turret.py`

**Interfaces:**
- Produces: stock `turret`.
  - **Parameters:**
    - `diameter` (m, 0.3) and `mass` (kg, 2.0);
    - `height` (m, 0.05): the `top` mount's height above the base;
    - `range` (pair deg, [-180, 180]): each end within ±360;
    - `motor` (KrakenX60), `count` (1) and `gearing` (60.0);
    - `friction` (N·m, 0.2): the joint's frictionloss.
  - **Bodies:** `base` (0.5 kg), and `platform` on hinge `turn` with axis [0, 0, 1] and the range in radians as
    elsewhere.
  - **Inertia:** a disc, `m·r²/2` about z.
  - **Slots and mounts:** slots `turn` (motor) and `encoder` (ratio 1.0); mounts `base`, and `top` on the platform
    at [0, 0, height].
  - **Drawing:** a cylinder of the diameter, 0.02 m tall, that doesn't collide.

- [ ] **Step 1: Write the failing tests:**
  - **`test_turret_builds`:** a config with a chassis and `turret` attached at `drive.top` assembles. Joint
    `turret.turn` exists with type hinge, axis z and range ±π. Slots read `{"turn": "motor", "encoder":
    "encoder"}`.
  - **`test_turret_turns_the_launcher`:** a launcher attached to `turret.top`. With `turret.turn` qpos set to π/2
    and `mj_kinematics`, the launcher base's x axis points along world +y (within 1e-9) at heading 0.
  - **`test_turret_range_limit`:** `range = [-400, 0]` raises a PartError mentioning "360".
- [ ] **Step 2: Run** them. Expected: FAIL (unknown stock part).
- [ ] **Step 3: Implement** `_turret` and `register(Stock("turret", ...))` next to `_arm`. The note reads: "Slots:
  turn (motor), encoder (turret turns). 0 faces the part's +x; positive turns left (counter-clockwise from above).
  Attach the launcher to turret.top."
- [ ] **Step 4: Run** them. Expected: PASS. Also run `.venv/bin/python -m pytest tests -q -k "stock or parts"`.
- [ ] **Step 5: Commit:** `git commit -m "parts: a stock turret"`

### Task 3: Shooting and `scored` in the Test tab

**Files:**
- Modify: `scrimmage/blocks/program.py`, `scrimmage/blocks/conditions.py`, `scrimmage/blocks/run.py`,
  `scrimmage/blocks/palette.py`
- Test: `tests/test_blocks_shooting.py`

**Interfaces:**
- Produces:
  - **The `scored` condition:** `COND_KINDS` gains `"scored"`, with args `{"goal": str, "at_least": int}`. The goal
    is a range goal name or `"any"` (`ANY_GOAL = "any"` in palette.py), and `at_least` ≥ 1.
  - **Palette:** `palette()` gains `goals: list[str]` (Tasks 8 and 9 add `turrets` and `cylinders`), sorted, from `world.parts.range.goals`.
  - **Result:** `RunResult` gains `goals: dict[str, int] | None`, the range's goal counts at the end, or None
    without a `[shooter]`. The run's lines include "scored 5 in blue_hub" for each goal with a count.
  - **Wording:** `describe` reads "5 or more balls are scored in blue_hub" ("in any goal" for any).

- [ ] **Step 1: Write the failing tests** using `configs/fastmode_range.toml`'s robot. Copy it into tmp_path,
  drop `[robot_program]`, and set `[range] start = "blue_center"`. The program spins `launcher.shooter` at 11 V,
  `hopper_floor.spin` and `feeder.spin` at 8 V, waits 1 s and runs `expect scored any at_least 1`.
  - Find a `move launcher.hood` angle (deg) that scores, in Step 3, with a quick sweep. Pin it in the test with a
    comment saying it was found that way.
  - **`test_spinning_the_launcher_scores`:** the check passes and `result.goals` has a total ≥ 1.
  - **`test_scored_names_a_goal`:** `problems()` flags `goal = "nowhere"` with "no goal 'nowhere' on this field;
    its goals: ...".
  - **`test_scored_file_shape`:** `at_least = 0` raises TestFileError.
  - **`test_no_shooter_no_goals`:** an existing station example's result has `goals is None`.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement.** The Watch's `holds` reads `world.test_range.counts()["goals"]`.
  - If the shooter doesn't run under the runner, find out why with systematic debugging. Likely causes: the
    runner's `HalState` lacks the beam-break DIO, or `enabled` is missing. Fix it in the runner, not by special
    cases in the shooter.
- [ ] **Step 4: Run** the new tests and `tests -q -k "blocks or test_tab"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "blocks: shooting runs under the Test tab; a scored condition and goal counts"`

### Task 4: The stock cylinder in the model

**Files:**
- Modify: `scrimmage/parts/stock/__init__.py` (kind `"text"`), `scrimmage/parts/stock/mechanisms.py`,
  `scrimmage/parts/model.py` (`Part.cylinders`, D2), `scrimmage/parts/assemble.py` (tendons and actuators),
  `scrimmage/parts/ids.py` (the valve slot kind)
- Test: `tests/test_stock_cylinder.py`

**Interfaces:**
- Consumes: `CylinderSpec` and `DEFAULT_CONDUCTANCE` (Task 1).
- Produces: stock `cylinder`.
  - **Parameters:**
    - `bore` (m, 0.0269875), `rod` (m, 0.0079375; less than bore) and `stroke` (m, 0.1524);
    - `friction` (N, 8.0) and `conductance` (m³/(s·Pa), 8e-9);
    - `mass` (kg, 0.25) and `rod_mass` (kg, 0.1);
    - `push` (text, ""), `push_pos` (vec3, [0, 0, 0]) and `length` (m, 0: from the assembled pose).
  - **The barrel:** it lies along the part's +x, and the rod extends toward +x.
  - **Free form** (`push == ""`):
    - bodies `barrel` and `rod`, with `rod` on slide `extend` along x, range [0, stroke];
    - pin_a is the barrel's origin and pin_b is the rod's origin; the retracted length is `length or 0.3·stroke +
      0.05`, drawn that way;
    - mounts `base` and `rod`.
  - **Push form:**
    - one body `barrel` (mass + rod_mass); pin_a is its origin and pin_b is `(push body full name, push_pos)`;
    - mount `base`.
  - **Slot** `valve` (kind "valve").
  - **In the model:** each cylinder makes a spatial tendon `"<inst>.cylinder"` between two sites, `"<inst>.pin_a"`
    and `"<inst>.pin_b"`:
    - the tendon has `limited = True`, `range = [L0, L0 + stroke]`, and `frictionloss = friction`;
    - an actuator `"<inst>.cylinder"` on it has `gainprm[0] = 1`, no bias and no ctrl limits.
  - **Built:** `Built.cylinders: list[CylinderSpec]`, with full body names and `length` resolved.
  - **Valve bindings:** `built.bindings["valve"]` holds one dict per bound valve: `{"name": "<inst>.valve",
    "cylinder": "<inst>", "module": "PCM" | "PH", "id": int, "forward": int, "reverse": int | None, "invert":
    bool}`.
  - **IDs:** `SHORT` gains "PCM" and "PH" for kind valve. The default ids are module 0 for PCM and 1 for PH. A
    channel is 0–7 on a PCM and 0–15 on a PH. The table forms are `{pcm = 1, channel = 3}` and `{ph = 2, forward =
    3, reverse = 4}`, plus `invert`.

- [ ] **Step 1: Write the failing tests:**
  - **`test_free_cylinder`:** it assembles. The tendon range is [L0, L0 + 0.1524] and the actuator count goes up by
    1. Setting `extend` qpos to 0.1 changes `ten_length` by 0.1 (1e-9) after `mj_forward`.
  - **`test_push_cylinder_on_an_arm`:** a stock arm on drive.top, and a cylinder on drive.top at [0, 0, -0.1] with
    `push = "arm.arm"` and `push_pos = [0.2, 0, 0]`. It assembles. Applying ctrl = 100 N on the actuator raises the
    arm's pivot qacc above 0 (the cylinder pushes the tip up). Choose positions so that holds.
  - **`test_push_length_outside_stroke`:** `length = 0.01` raises a PartError naming both lengths in meters.
  - **`test_push_pins_rigid`** (Review Focus 3): push onto `drive.chassis` itself raises the exact message.
  - **`test_push_unknown_body`:** the error lists the instance's bodies.
  - **`test_valve_ids`:**
    - `"PCM:3"` gives `{"module": "PCM", "id": 0, "forward": 3, "reverse": None}`;
    - `"PH:3,4"` gives `forward 3, reverse 4, id 1`;
    - `"PCM:9"` raises "0 to 7";
    - mixing a PCM and a PH valve across two cylinders raises an error naming both;
    - `"TalonFX:3"` in a valve slot raises the existing kind-mismatch error.
  - **`test_part_toml_rejects_cylinder`:** a folder part.toml with `[[cylinder]]` still errors as an unknown key.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **Building:** add the sites, tendon and actuator in `assemble()` after every instance is attached and before
    `spec.compile()`, using MjSpec (`spec.add_tendon`, `wrap_site`, `spec.add_actuator` with `trntype` tendon).
  - **Retracted length:** compute L0 for the push form from a kinematics pass on a first compile. Alternatively,
    compile once and add after, whichever MjSpec allows; document the choice in a comment.
  - **Rigid check:** the pins' bodies have no joint between them when they share their nearest moving ancestor and
    no joint lies on either path to it.
- [ ] **Step 4: Run** the tests, the golden test and `tests -q -k "parts or assemble or stock"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "parts: a stock pneumatic cylinder (free or pushing another part) and valve slots"`

### Task 5: Pneumatics in the world and the CTRE PCM

**Files:**
- Create: `scrimmage/pneumatics/runtime.py`
- Modify: `scrimmage/world.py` (construct, step, bus load, sensor_updates, reset), `scrimmage/config.py` or the
  place that validates top-level sections (`[pneumatics]`)
- Test: `tests/test_pneumatics_world.py`

**Interfaces:**
- Consumes: Tasks 1 and 4.
- Produces: `class PneumaticsRuntime(world, built, cfg_section: dict | None)`.
  - **Attributes:** `system: AirSystem` and `names: list[str]` (the cylinder instances).
  - **Methods:**
    - `command(name, state)`, for the Test tab (D3): it overrides the HAL for that cylinder until `reset()`;
    - `before_physics(hal: HalState, enabled: bool, dt)`, which sets `data.ctrl` for each actuator;
    - `extension(name) -> float` (m);
    - `supply_and_slope(bus) -> (amps, 0.0)`;
    - `sensor_updates() -> list[Update]`;
    - `reset()` and `warnings: list[str]`.
  - **On the World:** `world.pneumatics: PneumaticsRuntime | None`, None without cylinders.
- **`[pneumatics]` keys:** `tanks`, `tank_ml`, `compressor` ("VIAIR 90C" or "none"), `working_psi`, `start_psi`,
  `switch_on_psi`, `switch_off_psi`. An unknown key errors and lists the keys.
- **HAL:**
  - **Valves read:** `hal.get("Solenoid", f"{id},{ch}", "<output", False)`.
  - **Closed loop:** `hal.get("CTREPCM", str(id), "<closed_loop", False)`.
  - **Inputs sent:** `("CTREPCM", str(id), {">pressure_switch": calls, ">current": amps, ">on": on})` for every
    module a valve names.

- [ ] **Step 1: Write the failing tests:**
  - **`test_solenoid_extends`:** a world from a config with a chassis and a free cylinder with `valve = "PCM:3"`.
    `HalState` has DriverStation enabled and `("Solenoid", "0,3")` `<output True`. After stepping 1 s, the
    cylinder's extension is ≥ 0.15 m and `stored_psi` is below 120.
  - **`test_unbound_valve_warns`** (Review Focus 1): it stays at extension < 0.001 after 1 s, and the world's
    warnings include the exact text.
  - **`test_double_both_on`** (Review Focus 2): it holds, with one warning naming the valve.
  - **`test_pcm_inputs`:** the sensor updates include CTREPCM "0" with `>pressure_switch` and `>current`.
  - **`test_compressor_loads_battery`:** with closed loop on and the tanks at 90 psi, `world.vbatt` is lower than
    nominal by about the battery resistance times 7–11 A.
  - **`test_no_cylinders_no_runtime`:** `configs/kitbot.toml` gives `world.pneumatics is None`.
  - **`test_pneumatics_section_checked`:** `working_psi = 80` raises with "60 psi".
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **Step order:** `World.step` calls `pneumatics.before_physics` after the motors drive and before `mj_step`. The
    bus solve adds the runtime as a load when the compressor is on.
  - **`reset()`:** resets the system and clears the overrides.
  - **Gotcha:** `data.ctrl` must be written after `mj_step` resets nothing. MuJoCo keeps ctrl, so write it every
    step.
- [ ] **Step 4: Run** the tests, the golden test and the full `tests -q -x -k "world or range or blocks"`. Expected:
  PASS.
- [ ] **Step 5: Commit:** `git commit -m "pneumatics: cylinders in the world, valves from a CTRE PCM, the compressor on the battery"`

### Task 6: The REV Pneumatic Hub through the lockstep extension

**Files:**
- Modify: `lockstep/src/scrimmage_lockstep.cpp`, `lockstep/pyproject.toml` (version 0.7.0),
  `scrimmage/pneumatics/runtime.py`, `scrimmage/bridge.py` (if a ScrimmagePH device needs listing)
- Test: `tests/test_pneumatics_robot_e2e.py`

**Interfaces:**
- Consumes: Task 5's runtime. D5's device and value names.
- Produces:
  - **Valves read:** a PH valve reads bit `ch` of `hal.get("ScrimmagePH", str(id), "<solenoids", 0)`, and
    `<closed_loop` the same way.
  - **Inputs sent:** `("ScrimmagePH", str(id), {">pressure_switch", ">compressor_on", ">compressor_current"})`.
  - **No extension warning:** "<inst>.valve is on a REV Pneumatic Hub, which needs the lockstep extension
    (`--scrimmage-lockstep` for RobotPy, the Gradle init script for Java)". It is added when, 2 s after the robot
    connects, the HAL holds no ScrimmagePH device, the same way `missing_rev_warnings` works.

- [ ] **Step 1: Write the failing e2e tests**, modeled on `tests/test_rev_py_e2e.py` (read it for the harness):
  - The RobotPy program creates `Solenoid(CTREPCM, 3)`, `DoubleSolenoid(REVPH, 4, 5)` and a `Compressor(CTREPCM)`.
  - It sets the first on and the double forward in `teleopInit`, and prints `getPressureSwitchValue()` after 1 s.
  - **`test_pcm_solenoid_extends_a_cylinder`** and **`test_ph_double_solenoid_extends_a_cylinder`:** both
    cylinders extend and the program prints the switch value.
  - The PH test is skipped with a reason if the installed `scrimmage_lockstep` is older than 0.7.0.
- [ ] **Step 2: Rebuild the extension** with `.venv/bin/pip install ./lockstep` and run the tests. Expected: the PH
  test FAILs.
- [ ] **Step 3: Implement the extension.**
  - **At startup:** loop over `HAL_GetNumREVPHModules()` and register `HALSIM_RegisterREVPHInitializedCallback`.
  - **On init:** create the SimDevice `ScrimmagePH:<module>`, with `solenoids` (int, output), `closed_loop`
    (bool, output) and the three inputs.
  - **Callbacks:** register the solenoid-output and compressor-config-type callbacks to update the outputs, and
    `HALSIM_RegisterSimValueChangedCallback` on each input to call `HALSIM_SetREVPHPressureSwitch`,
    `HALSIM_SetREVPHCompressorOn` and `HALSIM_SetREVPHCompressorCurrent`.
  - **Lockstep:** count the device's values in `gValueNames` like any other SimDevice, so lockstep waits for them.
  - **Then** the runtime and the warning.
- [ ] **Step 4: Run** the e2e tests and `tests -q -k "lockstep or rev"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "lockstep: relay the REV Pneumatic Hub; pneumatics: PH valves and inputs"`

### Task 7: `scrimmage air`

**Files:**
- Create: `scrimmage/pneumatics/budget.py`
- Modify: `scrimmage/__main__.py`
- Test: `tests/test_air_budget.py`

**Interfaces:**
- Consumes: `AirConfig`, `CylinderSpec` and `COMPRESSORS` (Task 1); `assemble` (Task 4).
- Produces:
  - `budget(config: AirConfig, cylinders: list[CylinderSpec], match_s: float = 150.0) -> Budget`, where `Budget`
    is a dataclass holding:
    - `per_cycle_l: dict[str, float]`: free-air liters for an extend plus a retract, at the working pressure,
      including the dead volume;
    - `stored_l: float`: free air between the switch's top and the working pressure;
    - `cycles_no_compressor: float`;
    - `compressor_l_per_match: float`: the mean flow between the switch points, times match_s;
    - `cycles_per_match: float`.
  - `format_budget(b) -> str`, plain lines ending with "Estimates (UNCALIBRATED): see docs/pneumatics.md."
  - **CLI:** `scrimmage air <config.toml> [--match-seconds 150]`.
- **Formulas:**
  - per stroke side = `V_swept_side · working_psi·PSI / ATM`;
  - stored = `tanks·tank_ml/1000 · (switch_off_psi − working_psi)·PSI/ATM`;
  - no-compressor cycles = stored / sum(per_cycle).
- [ ] **Step 1: Write the failing tests:**
  - **`test_budget_one_default_cylinder`:** per cycle is about 0.574 L within 2%. Compute the exact expected value
    in the test from the formulas. Two default tanks give stored ≈ 2·0.574·55·PSI/ATM.
  - **`test_air_cli`:** run on a tmp config with one cylinder. It prints "cycles" and "UNCALIBRATED", exit 0.
  - **`test_air_cli_no_cylinders`:** prints "has no pneumatic cylinders", exit 1.
- [ ] **Step 2: Run** them. Expected: FAIL. **Step 3: Implement.** **Step 4: Run** them. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "pneumatics: scrimmage air, an air budget"`

### Task 8: Aiming in the Test tab

**Files:**
- Create: `scrimmage/blocks/aim.py`
- Modify: `program.py`, `conditions.py`, `run.py`, `palette.py`
- Test: `tests/test_blocks_aim.py`

**Interfaces:**
- Consumes: Task 2's turret and Task 3's `goals`.
- Produces:
  - **Block `aim`:** `DO_KINDS` gains `"aim"`, with args `{"turret": "<inst>", "at": "<goal>"}`. It takes no time.
    Every step the runner sets `drives[f"{turret}.turn"] = ("to", target)`, with the target from `aim_target()`,
    until a later spin, move, stop or aim on that joint.
  - **Condition `aimed`:** `COND_KINDS` gains `"aimed"`, with args `{"turret", "at", "within_deg": float = 2.0}`.
  - **Functions in aim.py:**
    - `aim_error(world, turret, goal) -> float`: rad, signed, positive means the goal is to the left. It uses the
      `[shooter]` launcher's base when that launcher rides on this turret (its body chain passes through
      `<turret>.platform`), else the platform's x axis. It measures horizontally, from the launcher base to the
      goal's center.
    - `aim_target(world, turret, goal) -> tuple[float, bool]`: the joint target, clamped, and whether it was in
      range.
  - **Signal:** `aim_error` (deg) whenever an aim block or aimed condition exists.
  - **Palette:** `palette()` gains `turrets: list[str]`, the instances whose joint `<inst>.turn` comes from a stock
    turret.
  - **Wording:** `describe` reads "the turret is aimed at blue_hub within 2°".
- [ ] **Step 1: Write the failing tests:**
  - **Robot:** Task 3's robot, with the launcher moved onto a turret (`attach = "turret.top"`, the turret on
    `drive.top` at the launcher's old pos), turret `turn = "SimMotor:turret"`.
  - **`test_aim_holds_while_turning`:** aim at the start pose's hub, wait 1 s, `expect aimed within 3`, turn the
    robot 90°, then `expect aimed within 5`. Both pass.
  - **`test_aim_out_of_range`** (Review Focus 4): with turret range [-30, 30] and the robot turned 120° away first,
    `expect aimed` fails with "outside the turret's range (it stops at 30°)".
  - **`test_aim_names_checked`:** `problems()` for `turret = "drive"` reads "drive isn't a turret; the robot's
    turrets: turret". An unknown goal gets the Task 3 message.
  - **`test_aim_error_uses_launcher`:** with the launcher mounted with `rot = [0, 0, 90]` on the turret top, aiming
    turns the turret so the launcher, not the platform, faces the goal.
- [ ] **Step 2: Run** them. Expected: FAIL. **Step 3: Implement.** **Step 4: Run** them and `-k blocks`.
  Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "blocks: aim a turret at a goal; an aimed condition and aim_error signal"`

### Task 9: Pneumatics in the Test tab

**Files:**
- Modify: `program.py`, `conditions.py`, `run.py`, `palette.py`
- Test: `tests/test_blocks_pneumatics.py`

**Interfaces:**
- Consumes: Task 5's `world.pneumatics` and `command()`.
- Produces:
  - **Block `set_cylinder`:** `DO_KINDS` gains `"set_cylinder"`, with args `{"cylinder": "<inst>", "to":
    "extend" | "retract" | "off"}`. It takes no time. "off" maps to "retract" on a single solenoid and to "hold" on a
    double (C9). Under the Test tab, a cylinder with no bound valve counts as single.
  - **Conditions:**
    - `"cylinder"`: `{"cylinder", "is": "extended" | "retracted"}`, within 0.25 in of the end;
    - `"pressure"`: `{"side": "stored" | "working" = "stored", "above" | "below": psi}`, one of the two.
  - **Signals:** `air.psi`, `air.working_psi` and `<inst>.extension` (in).
  - **Palette:** `palette()` gains `cylinders: list[{"name", "double": bool}]`.
  - **Result:** `RunResult` gains `air: dict | None`, with keys `start_psi`, `end_psi`, `low_psi`, `low_t`,
    `compressor_s` and `below_working_t` (float | None). The run's lines include the air sentence, exactly
    "air: 120 psi at the start, 84 psi at the end, lowest 81 psi at 14.2 s; the compressor ran 9.0 s". When
    `below_working_t` is set, add the Review Focus 5 warning.
  - **Notes:** `run_notes` adds "Pneumatic numbers are estimates (UNCALIBRATED): see docs/pneumatics.md." when the
    robot has cylinders.
  - **During a run:** the compressor runs throughout (enabled, closed loop on: C11).
- [ ] **Step 1: Write the failing tests:**
  - **Robot:** a stand-in robot (copy `examples/blocks/intake/intake-standin.toml`'s first sections) with a free
    cylinder on `drive.top`.
  - **`test_set_cylinder_extends`:** `set_cylinder extend`, `wait_until cylinder extended timeout 2`, `expect
    pressure below 120`. It passes.
  - **`test_air_runs_out`** (Review Focus 5): `[pneumatics] compressor = "none", start_psi = 70`, tanks 1, a 2 in
    bore cylinder, then `repeat 6 times` of extend, wait 0.5, retract, wait 0.5. The result's air has
    `below_working_t` set, and the lines contain "fell below the 60 psi working pressure".
  - **`test_set_cylinder_names_checked`:** an unknown cylinder lists the robot's cylinders. "off" on a single
    solenoid retracts.
  - **`test_file_shape`:** `pressure` with both `above` and `below` raises TestFileError, and so does `to =
    "open"`.
- [ ] **Step 2: Run** them. Expected: FAIL. **Step 3: Implement.** **Step 4: Run** them and `-k blocks`.
  Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "blocks: set a cylinder; cylinder and pressure conditions; the air line"`

### Task 10: The block editor, the run panel and the replay

**Files:**
- Modify: `scrimmage/assembler/static/blocks_defs.js`, `test.js`, `test_run.js`, the replay drawing in
  `view3d.js` (or wherever `test_run.js` draws movers), `scrimmage/assembler/test_session.py`, and the model view
  that feeds the replay (find `model_view`)
- Test: the existing browser test pattern for the Test tab (find the tests that drive `blocks_defs.js` with
  Playwright, e.g. `tests/test_test_tab_browser*.py`) and a server test

**Interfaces:**
- Consumes: Tasks 3, 8 and 9's block kinds, condition kinds and palette keys (`goals`, `turrets`, `cylinders`).
- Produces:
  - **Blocks:**
    - *aim [turret] at [goal]*, and *set [cylinder] to [extend/retract/off]*;
    - conditions *[n] scored in [goal]*, *[turret] aimed at [goal] within [n]°*, *[cylinder] is
      [extended/retracted]* and *air pressure ([stored/working]) [above/below] [n] psi*.
  - **Hiding:** each block is hidden from the toolbox when its palette list is empty, the way the existing piece
    blocks are hidden.
  - **Run panel:** it shows `result.goals` and the `air` sentence.
  - **Replay:**
    - `model_view` gains `cylinders: [{"name", "a": [body, pos], "b": [body, pos], "bore", "retracted", "stroke"}]`;
    - the replay draws a barrel (bore-diameter cylinder, length = retracted) from pin a toward pin b, and a rod
      (rod diameter) the rest of the way, every frame, from the two bodies' poses;
    - a pin on a non-moving body uses its fixed pose.
- [ ] **Step 1: Write the failing tests:**
  - **Server test:** `model_view` for a push-cylinder config lists the cylinder with both pins.
  - **Browser test:** the toolbox for a turret-and-cylinder robot has the aim and set-cylinder blocks. A program
    built from them saves to TOML that `program.load` reads back with the same args.
- [ ] **Step 2: Run** them. Expected: FAIL. **Step 3: Implement.** **Step 4: Run** them, plus the Test tab browser
  tests. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "test tab: blocks for aiming, cylinders, scoring and air; cylinders in the replay"`

### Task 11: Two worked examples

**Files:**
- Create: `examples/blocks/turret/` (`turret-standin.toml`, `aim.test.toml`, `README.md`),
  `examples/blocks/pneumatic/` (`arm-standin.toml`, `cycles.test.toml`, `README.md`)
- Test: `tests/test_examples_turret_pneumatic.py`

**Interfaces:**
- Consumes: everything above.
- **turret:**
  - **Robot:** the kitbot stand-in with a turret on `drive.top`, a launcher on `turret.top`, the fastmode_range
    feeder stages and hopper, and `[range] file = "2026"` with 8 balls preloaded.
  - **Program:** spin up, aim at the near hub, feed, wait 2 s, turn the robot 90° while still aiming, feed again,
    then `expect scored at least N`.
  - **N:** what the run actually gives, minus margin. Record the measured count in the README.
- **pneumatic:**
  - **Robot:** the stand-in with a stock arm on `drive.top`, lifted by a push cylinder (2 in bore, 8 in stroke),
    and `[pneumatics]` defaults.
  - **Program:** a repeat of 10 cycles (extend, wait until `arm.pivot` above 60°, retract, wait until below 5°),
    with `expect pressure above 60 throughout`.
  - **README:** it reports the measured lowest pressure and what `scrimmage air` says for this robot.
- [ ] **Step 1: Write the tests.** Each example's test runs its program and asserts the check results the README
  states.
- [ ] **Step 2: Build the examples,** tune geometry until each behaves as the README claims, and run the tests.
  Expected: PASS.
- [ ] **Step 3: Commit:** `git commit -m "examples: a turret shooter and a pneumatic arm in the Test tab"`

### Task 12: Docs, MCP state and the spec's decisions

**Files:**
- Create: `docs/pneumatics.md`
- Modify: `docs/parts.md` (turret, cylinder, valve IDs, `[pneumatics]`), `docs/test-tab.md` (Shooting, Aiming,
  Pneumatics sections and the block list), `README.md` (bindings table: PCM/PH), `scrimmage/mcp_server.py` or
  `World` state (get_state gains `pneumatics: {stored_psi, working_psi, compressor_on, cylinders: {name:
  extension_in}}` when present), `.github/workflows/*.yml` only if the e2e test needs the rebuilt extension (it
  already does `pip install ./lockstep`)
- Test: `tests/test_docs_links.py` if it exists (run it). Add a get_state test in `tests/test_pneumatics_world.py`.

- [ ] **Step 1:** Write `docs/pneumatics.md`:
  - the model in plain words and every default with its source: the FRC Pneumatics Manual (120/60 psi), AndyMark's
    VIAIR 90C bench test (0.88 cfm at 7 A at 0 psi, 0.22 cfm at 11 A at 100 psi), the Clippard AVT-PP-35 tank (574
    mL), the Bimba sizes, and the ISO 6358 conductance estimate;
  - UNCALIBRATED, and how to measure your own: time a stroke with a phone video, and watch a gauge drop over N
    cycles;
  - robot code for the PCM and the PH (the PH needs the extension), and that `getPressure()` reads 0 in sim.
- [ ] **Step 2:** Update the other docs. Every new key appears in a table, as the existing ones do.
- [ ] **Step 3:** Add the get_state pneumatics block and its test.
- [ ] **Step 4:** Run the full suite: `.venv/bin/python -m pytest tests -q -x`. Expected: PASS.
- [ ] **Step 5:** Fill in the spec's "Decisions made during the build" from the ledger's rulings.
- [ ] **Step 6: Commit:** `git commit -m "docs: turret, pneumatics, the air budget; get_state reports the air"`
