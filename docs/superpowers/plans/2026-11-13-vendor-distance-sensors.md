# Vendor Distance Sensors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `kind = "distance"` sensor can name a CTRE CANrange (`ctre = "CANrange:<id>"`) or a Grapple LaserCAN (`grapple = "LaserCAN:<id>"`), and unchanged robot code (Java or RobotPy) reads the physics through the vendor's class.

**Architecture:** The CANrange is fed through the SimDevice Phoenix 6 already registers (`CAN:CANrange (v6)[<id>]`, input `distance`), written by `SensorRuntime.sensor_updates` like the color sensor's. The LaserCAN registers nothing in simulation, so scrimmage's robot-side helpers (the Java agent, the RobotPy lockstep package) register a helper SimDevice `ScrimmageGrapple:LaserCAN[<id>]` per LaserCAN and make `getMeasurement()` / `get_measurement()` return its values, the way `ScrimmageRev` drives SPARKs. Playing With Fusion's sensor is rejected with a clear error for now.

**Tech Stack:** Python 3.11/3.13 (scrimmage), Java 17 agent with ASM (scrimmage/java/agent), RobotPy lockstep package (lockstep/), MuJoCo rays (existing).

**Spec:** docs/superpowers/specs/2026-11-13-vendor-distance-sensors-design.md

## Global Constraints

- Run tests with `/home/altainia/code/scrimmage/.venv/bin/python -m pytest`, never `uv run`; in a worktree set `PYTHONPATH=<absolute worktree path>`.
- American spellings, plain words, in docs and messages; every user-facing string through `i18n.t(...)` as the surrounding code does.
- Never transform Phoenix classes (CTRE's license); the CANrange is reached only through its own SimDevice.
- Robots without libgrapplefrc pay nothing: the agent reaches Grapple by reflection only and starts on the first LaserCAN; the RobotPy helper is a no-op without the package.
- Nothing in a helper raises into the robot program: a problem is printed once (`scrimmage grapple helper: ...`) and the helper stops.
- Timing never gates a test (CI rule): assert values, not wall time.
- Device ids 0 to 62. Units: config meters (or `_in`), `distance_mm` an int.
- SimDevice keys: CANrange `("CAN", "CANrange (v6)[<id>]")` with `{">distance": meters, ">supplyVoltage": 12.0}`; LaserCAN `("ScrimmageGrapple", "LaserCAN[<id>]")` with `{">distance_mm": int, ">status": int}`.

## Review Focus

1. A robot that creates a LaserCAN before the agent/helper has started (at class-load or import time) still gets fed: the hook registers from the constructor, so a LaserCAN made in a static initializer must work (Task 4 test).
2. A sensor named in the config whose device the robot never creates: one warning naming it, not silence (Task 3 test).
3. Two LaserCANs (or two CANranges) on one robot get their own readings, not each other's (Tasks 3 and 4 tests use two devices).
4. Short ranging mode set by robot code after construction: a target between the short reach and `max_range` reads no-target (Task 4 test).
5. A `[[sensor]]` with `ctre` and also `analog`: both outputs written from the same reading (Task 3 test).

---

### Task 1: Check the facts the design leaves to task 1

**Files:**
- Create: `docs/superpowers/specs/2026-11-13-vendor-distance-sensors-checks.md`

**Interfaces:**
- Produces: the numbers Tasks 2-5 use, each with its source: `CANRANGE_MAX_RANGE` (m), `LASERCAN_LONG_REACH` and `LASERCAN_SHORT_REACH` (m), `LASERCAN_STATUS_VALID` and `LASERCAN_STATUS_NO_TARGET` (ints), and yes/no answers with evidence for: RobotPy `phoenix6` registers the same `CAN:CANrange (v6)[<id>]` SimDevice; `libgrapplefrc-py` installs on Python 3.11 and 3.13 on Linux and Windows (by what pip command, from what index) and its LaserCAN class's `get_measurement` can be replaced on the class (or the instance); the Java hook (rewrite `LaserCan.getMeasurement()`'s entry) returns a value in a throwaway probe.

- [ ] **Step 1:** Read Grapple's public sources (GrappleRobotics/libgrapplefrc, GrappleRobotics/LaserCAN) for the status constants, the no-target status, and the short/long reaches; CTRE's CANrange documentation for its maximum range. Record each value with its URL and the file/line or page.
- [ ] **Step 2:** In a scratch venv (not the repo's), `pip install robotpy phoenix6` and run a 3-second `robotpy sim`-free check: `import phoenix6, hal; r = phoenix6.hardware.CANrange(5)`, then list `hal.simulation` SimDevices (`wpilib.simulation.SimDeviceSim.enumerateDevices()`); record the name.
- [ ] **Step 3:** Find how libgrapplefrc-py is installed (its README / depjson); install it on the scratch venv; create a LaserCAN, try replacing `get_measurement` on the class; record what works.
- [ ] **Step 4:** Throwaway Java probe (copy `tests/java_probe`, add the Grapple vendordep `https://storage.googleapis.com/grapple-frc-maven/libgrapplefrc2026.json`, set `JAVA_HOME=~/wpilib/2026/jdk`): an ASM hook that makes `getMeasurement()` return a fixed `Measurement(0, 123, 0, true, 33, null)`; confirm the robot prints 123. Don't commit the probe.
- [ ] **Step 5:** Write the checks file: one section per item, value + source + date. For anything that contradicts the design, write the ruling the design's Call V8 prescribes, with its cost.
- [ ] **Step 6:** Commit: `git add docs/superpowers/specs/2026-11-13-vendor-distance-sensors-checks.md && git commit -m "vendor sensors: checks for the design's open facts"`

### Task 2: Config keys

**Files:**
- Modify: `scrimmage/sensors/spec.py` (KEYS["distance"], SensorSpec, the distance branch of the parser, a whole-config duplicate check)
- Test: `tests/test_sensors_spec.py` (create if no spec test file exists; else add to the existing one — find it with `grep -l "parse_sensor\|SensorSpec" tests/*.py`)

**Interfaces:**
- Consumes: Task 1's `CANRANGE_MAX_RANGE`, `LASERCAN_LONG_REACH`.
- Produces: `SensorSpec.device: str | None` (`"CANrange"` or `"LaserCAN"`, None otherwise) and `SensorSpec.device_id: int | None`; module constants `DEVICE_MAX_RANGE = {"CANrange": ..., "LaserCAN": ...}`; `def device_key(s: SensorSpec) -> tuple[str, str] | None` returning the Global Constraints keys.

- [ ] **Step 1: Failing tests** (names and assertions):
  - `test_ctre_canrange_key`: `ctre = "CANrange:5"` gives `device == "CANrange"`, `device_id == 5`, `length == DEVICE_MAX_RANGE["CANrange"]` with no `max_range`, and `device_key(s) == ("CAN", "CANrange (v6)[5]")`.
  - `test_grapple_lasercan_key`: `grapple = "LaserCAN:7"` gives `device_key(s) == ("ScrimmageGrapple", "LaserCAN[7]")`.
  - `test_device_key_with_analog`: `ctre` and `analog = 0` both kept.
  - `test_explicit_max_range_wins`: `max_range = 1.0` with `ctre` gives `length == 1.0`.
  - `test_bad_device_keys` (parametrized): `ctre = "TalonFX:5"`, `ctre = "CANrange:63"`, `ctre = "CANrange:x"`, `grapple = "LaserCan:7"`, both `ctre` and `grapple` → `SensorError` whose message names the key and the accepted form.
  - `test_device_key_on_other_kind`: `ctre` on a `beam` → the existing unknown-key error.
  - `test_pwf_not_supported_yet`: `pwf = "TimeOfFlight:6"` on a distance sensor → `SensorError` containing `not supported yet` and `analog`.
  - `test_same_device_twice`: two sensors with `ctre = "CANrange:5"` → error naming both sensors.
- [ ] **Step 2:** Run them: `.venv/bin/python -m pytest tests/test_sensors_spec.py -q` → the new tests fail.
- [ ] **Step 3:** Implement in `spec.py`: add `"ctre", "grapple", "pwf"` to `KEYS["distance"]`; parse `"<Kind>:<id>"` with the same error style as `bindings._ctre_device`; per-device default `max_range`; `pwf` raises the not-supported error; duplicate check where the sensor list is parsed (the function that returns `list[SensorSpec]`).
- [ ] **Step 4:** Tests pass; also run `tests/ -k sensor -q`.
- [ ] **Step 5:** Commit `scrimmage/sensors/spec.py tests/...` with message "sensors: ctre CANrange and grapple LaserCAN keys on a distance sensor".

### Task 3: Runtime writes and the not-created warning

**Files:**
- Modify: `scrimmage/sensors/runtime.py` (`sensor_updates`, a `missing_devices` beside `missing_color_sensors`), `scrimmage/bindings.py` / `scrimmage/devicecheck.py` (CANrange as a known, not-modeled CAN kind unless a sensor names it), `scrimmage/bridge.py` (call it where `missing_color_sensors` is called, line ~793)
- Test: `tests/test_sensors_runtime.py` (or the file that tests `sensor_updates`: `grep -l sensor_updates tests/*.py`)

**Interfaces:**
- Consumes: Task 2's `device_key`, `SensorSpec.device`.
- Produces: `SensorRuntime.sensor_updates()` entries per the Global Constraints keys; `SensorRuntime.missing_devices(hal: HalState, after: float) -> list[str]` (the same contract as `missing_color_sensors`: a message once per sensor whose device the robot program hasn't created after `after` seconds; for a LaserCAN the message says the helper may not be loaded, as bridge.py line ~194 words it for REV).

- [ ] **Step 1: Failing tests:**
  - `test_canrange_update_seen`: a piece 0.30 m in front → `("CAN", "CANrange (v6)[5]", {">distance": approx(0.30, abs=1e-3), ">supplyVoltage": 12.0})`.
  - `test_canrange_update_nothing`: nothing in range → `">distance" == s.length`.
  - `test_lasercan_update_seen_and_nothing`: seen → `">distance_mm" == 300`, `">status" == LASERCAN_STATUS_VALID`; nothing → `">distance_mm" == round(s.length * 1000)`, `">status" == LASERCAN_STATUS_NO_TARGET`.
  - `test_two_lasercans_read_their_own`: two LaserCANs at different distances → two updates with their own values.
  - `test_device_and_analog_together`: one sensor gives both the `AI` and the `CAN` update from one reading.
  - `test_unbound_canrange_is_listed_not_modeled`: a robot that creates CANrange 8 that no sensor names → it appears in the device check's "not modeled" list the way an unbound CANcoder does (`devicecheck.py`; `bindings._NOT_MODELED_CAN` gets `"CANrange"`).
  - `test_missing_device_warned_once`: a configured CANrange absent from the HAL after `after` → one message naming the sensor and `CANrange 5`; a second call → none.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement. `LASERCAN_STATUS_VALID`, `LASERCAN_STATUS_NO_TARGET` constants live in `runtime.py` with Task 1's source comment.
- [ ] **Step 4:** Pass; run `tests/ -k "sensor or bridge" -q`.
- [ ] **Step 5:** Commit "sensors: write CANrange and LaserCAN readings; warn when the robot never creates one".

### Task 4: Java agent: ScrimmageGrapple, and the end-to-end test

**Files:**
- Create: `scrimmage/java/agent/src/scrimmage/agent/ScrimmageGrapple.java`
- Modify: `scrimmage/java/agent/src/scrimmage/agent/ScrimmageAgent.java` (transform `au/grapplerobotics/LaserCan`: register at the end of each constructor; at `getMeasurement()`'s entry return `ScrimmageGrapple.measurement(this)` when non-null; record `setRangingMode(RangingMode)` via `ScrimmageGrapple.rangingMode(this, mode)`)
- Modify: `tests/java_probe` (add the Grapple vendordep JSON with its license notice; a CANrange 5, a CANrange 8, a LaserCAN 7 and a LaserCAN 9, the second LaserCAN created in a static initializer; print their readings to NT or the console as the probe does today), `tests/java_probe/scrimmage.toml` (four `[[sensor]]`s)
- Test: `tests/test_java_e2e.py` (new test), `tests/test_java_agent.py` if it unit-tests transforms

**Interfaces:**
- Consumes: Task 3's SimDevice values; Task 1's reaches and statuses.
- Produces: SimDevice `ScrimmageGrapple:LaserCAN[<id>]` inputs `distance_mm` (int), `status` (int); `ScrimmageGrapple.measurement(Object laserCan): Object` (a `LaserCanInterface.Measurement`, or null until scrimmage has written both values); console line `scrimmage grapple helper: loaded` on the first LaserCAN.

- [ ] **Step 1: Failing test** `test_java_reads_can_distance_sensors` (in test_java_e2e.py, the java_probe pattern): place a piece 0.25 m in front of CANrange 5 and 0.40 m in front of LaserCAN 7 (`move_body`), advance; assert the robot reads `0.25 ± 0.001` m from `getDistance()` and `400 ± 1` mm with the valid status from `getMeasurement()`; the second devices read their own pieces; move the pieces away, advance, assert LaserCAN reads the no-target status and CANrange `getIsDetected()` false. Then robot code sets LaserCAN 9 to short mode (the probe does so at 1 s) with a piece at 2.0 m: no-target status.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement ScrimmageGrapple after ScrimmageRev's shape (reflection only, PENDING list, a sim-periodic callback that reads the helper SimDevices; `unavailable` prints one line and stops). Short mode: a `distance_mm` past `LASERCAN_SHORT_REACH` becomes the no-target status in the returned Measurement; `is_long` mirrors the mode.
- [ ] **Step 4:** Pass (`-k java_reads_can_distance`; needs `JAVA_HOME`, as CI's java job sets); run the whole `tests/test_java_e2e.py tests/test_java_agent.py`.
- [ ] **Step 5:** Commit "Java agent: feed Grapple LaserCANs; CANrange and LaserCAN end to end".

### Task 5: RobotPy helper

**Files:**
- Create: `lockstep/scrimmage_lockstep/grapple.py` (a port of ScrimmageGrapple, after rev.py's shape: `install()`, `HELPER_PREFIX = "scrimmage grapple helper: "`)
- Modify: `lockstep/scrimmage_lockstep/__init__.py` (install it beside the REV helper, never raising)
- Test: `tests/test_lockstep_grapple.py`; a RobotPy end-to-end test beside the existing RobotPy REV one (`grep -l "rev helper" tests/*.py`)

**Interfaces:**
- Consumes: Task 1's install/patch findings; Task 3's SimDevice values.
- Produces: the same SimDevice and behavior as Task 4, for RobotPy.

- [ ] **Step 1: Failing tests:** unit: with a fake LaserCAN class, `install()` makes `get_measurement()` return the helper's values once written and the original before; short mode as in Task 4. End to end: the CANrange and LaserCAN readings of Task 4's test, through a RobotPy robot.
- [ ] **Step 2:** Run → fail.
- [ ] **Step 3:** Implement. If Task 1 found the package can't be installed on CI or patched, instead: the end-to-end test is a skip with Task 1's reason, the unit test stays, and docs list RobotPy LaserCAN as not supported (Call V8).
- [ ] **Step 4:** Pass; run `tests/ -k "lockstep or rev" -q`.
- [ ] **Step 5:** Commit "lockstep: feed Grapple LaserCANs for RobotPy robots".

### Task 6: Docs and example

**Files:**
- Modify: `docs/sensors.md` (a "CAN distance sensors" section linked from the contents line; the keys table rows; "What isn't simulated": one ray, not a cone (Call V3), nothing-seen distance (V4), fast mode (V7), PWF (V5)), `docs/fast-mode.md` (one line: no CANrange stand-in yet), `docs/rev.md`-style note on the helper loading (in sensors.md)
- Create or modify: an example config in `examples/` with a CANrange intake sensor (follow `examples/blocks/beam`'s layout); a test that the example parses (`tests/test_examples_*.py` pattern)

- [ ] **Step 1:** Failing test: the example parses and its sensor has `device == "CANrange"`.
- [ ] **Step 2:** Write the example and docs; every config block in the docs marked `<!-- complete -->` must parse (the existing docs test checks this: `grep -l "complete" tests/test_*docs*.py`).
- [ ] **Step 3:** Run `tests/ -k "docs or example or sensor" -q` → pass.
- [ ] **Step 4:** Commit "docs: CAN distance sensors".
