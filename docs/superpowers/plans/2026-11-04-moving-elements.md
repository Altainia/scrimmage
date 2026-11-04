# Moving Elements, Collisions and a Partner Robot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `hinged` and `sliding` field elements, opt-in collisions for stock mechanisms with a clearance check, a
passive partner robot, and the ranges, Test-tab conditions and examples that use them.

**Architecture:**
- **Moving elements:** new element dataclasses in `scrimmage/testrange/elements.py`, built in `build_elements.py` as
  bodies with one hinge or slide joint each.
- **Collisions:** a `collide` parameter on the stock mechanisms switches their geoms to the robot's contact bits.
- **Clearance:** `mj_geomDistance`, only when a test asks for it.
- **Partner:** a second chassis and differential tree, prefixed `partner.`, added by `assemble()` from a
  `[partner]` section.
- **Test tab:** new conditions and signals follow the patterns of sub-project 23's `scored` and `aimed`.

**Tech Stack:** Python 3.11+, MuJoCo 3.17 (MjSpec, mj_geomDistance), Blockly (web app), pytest.

**Spec:** docs/superpowers/specs/2026-11-04-moving-elements-design.md

## Global Constraints

- Robots without `collide = true` or `[partner]`, on ranges without the new kinds, step exactly as before:
  `tests/test_range_ledger_golden.py` and every existing example test pass unchanged.
- **Contact bits:**
  - robot: contype 2, conaffinity 1;
  - field and moving elements: 1 and 2;
  - balls: 4 and 7;
  - partner: 3 and 3, with the partner's own bodies excluded from touching each other.
- **Element style:** dataclasses, `_Reader` checks with plain sentences naming the range file, element and key; every
  length has an `_in` form; angles in degrees in files.
- **Stops:** stiff and critically damped, built like the platform's (`PLATFORM_STOP_TC`).
- **Units:** signals use degrees and inches; files use m, kg, s and degrees (the inch forms as elsewhere).
- **Estimates:** every number not from a manual is marked an estimate in the file that holds it.
- **Wording and tests:** American spellings and plain words. Never run `uv run` in the repo root; use
  `.venv/bin/python -m pytest`, serially.

## Review Focus

1. **A vertical sliding element with no spring** (the portcullis) rests on its lower stop. It neither sinks through
   nor bounces, and lifting it takes its weight (2.27 kg × g ≈ 22.2 N, within 5%). Test: Task 1.
2. **A light hinged flap (0.05 kg) on a stiff spring (5 N·m/deg) at a 1 ms step** stays stable: its angle stays
   within its range ± 1° for 2 s after being knocked. Test: Task 1.
3. **A partner placed overlapping the robot's start or an element** is refused: "[partner] starts inside the robot
   (their outlines overlap by 0.20 m)", or "... inside element portcullis". Test: Task 5.
4. **Clearance when nothing on the robot is near the element** reads ">20 in", and no condition crashes. With every
   stock part `collide = false`, only the chassis and folder parts count. Test: Task 4.
5. **A stock part with `collide = true` that is also a `[shooter]` stage or intake** is an error naming both
   (C5). Test: Task 3.

---

### Task 1: Hinged and sliding elements

**Files:**
- Modify: `scrimmage/testrange/elements.py` (the `Hinged` and `Sliding` dataclasses and parsing),
  `scrimmage/testrange/build_elements.py` (building them)
- Test: `tests/test_moving_elements.py`

**Interfaces:**
- **`Hinged` fields:**
  - placement and size: `name, center, yaw_deg, shape ("box" | "disc"), size (x, y, z) | None, diameter | None,
    thickness | None, base_height`;
  - the hinge: `hinge ("bottom" | "top" | "left" | "right" | "front" | "back" | "center"), range_deg (lo, hi) |
    None, rest_deg = 0.0`;
  - the spring and its numbers: `stiffness (N·m/deg) = 0.0, damping (N·m·s/rad), breakaway (N·m) = 0.0, mass,
    friction = DEFAULT_FRICTION`.
  - **Damping default:** critically damped for the panel's inertia when `stiffness > 0`, else 0.05 N·m·s/rad (an
    estimate).
- **`Sliding` fields:** `name, center, yaw_deg, size, base_height, axis ("x" | "y" | "z"), range (lo, hi), rest =
  0.0, stiffness (N/m) = 0.0, damping (N·s/m), breakaway (N) = 0.0, mass, friction`. The damping default follows the
  same rule.
- **Joints:** the body is `element.<name>`, with joint `element.<name>.hinge` or `element.<name>.slide`.
  - **Zero point:** the joint is 0 at rest. The spring's springref is the rest (0), and the range is given relative
    to the rest.
  - **Stops:** a stop limit with `PLATFORM_STOP_TC`, critically damped.
  - **Breakaway:** the joint's frictionloss.
- **Hinge position:**
  - **Edge hinges:** the hinge axis lies along the named edge of the panel at rest. "bottom" and "top" turn about
    the element's local y, at z = base_height or base_height + size z. "left" and "right" turn about local z at
    y = ±size y/2. "front" and "back" turn about local z at x = ±size x/2.
  - **"center":** turns about the panel's own normal (local x for a disc standing on edge, or local z when it lies
    flat; pick the one the shape's thickness axis gives and document it).
- **Contact and drawing:** the field's bits, and the colors the other elements use.
- **Spec export:** each kind's keys appear in the `[[element]]` kind table the range writer and validator use, so
  `load_range` round-trips them.

- [ ] **Step 1: Write the failing tests:**
  - **`test_hinged_parses_and_builds`:** a door. `hinge = "left"`, `size = [0.05, 0.9, 1.2]`, `range_deg = [0, 90]`,
    `mass = 5`. Joint `element.door.hinge` exists with range [0, π/2].
  - **`test_sliding_portcullis_takes_its_weight`** (Review Focus 1):
    - Set up a vertical slide, `axis = "z"`, `range = [0, 0.6]`, `mass = 2.27`.
    - After 1 s it rests at q ∈ [−0.001, 0.001].
    - A 22.0 N upward force doesn't lift it past 1 mm in 1 s. 23.0 N lifts it above 0.05 m within 1 s.
  - **`test_light_flap_is_stable`** (Review Focus 2): a 0.05 kg flap with `stiffness = 5`, knocked with an impulse.
    Its angle stays within range ± 1° for 2 s.
  - **`test_breakaway_holds_until_pushed`:** a hinged panel with `breakaway = 2` holds under 1.9 N·m and turns
    under 2.1 N·m.
  - **`test_spinning_panel_has_no_stops`:**
    - Set up a disc with `hinge = "center"` and no `range_deg`.
    - Joint limited is false.
    - After driving it with a torque for 3 s the unwrapped angle passes 1080°.
  - **`test_rest_outside_range_errors`:** `rest_deg = 120` with `range_deg = [0, 90]` errors with "rest_deg 120 is
    outside range_deg [0, 90]".
  - **`test_inch_forms`:** `size_in` and `range_in` match the metric forms.
- [ ] **Step 2: Run** them. Expected: FAIL. **Step 3: Implement.** **Step 4: Run** them plus
  `-k "element or terrain or range or golden"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "range: hinged and sliding field elements"`

### Task 2: The element signal and condition

**Files:**
- Modify: the scenario signal resolver (find where `tilt` and the climb signals are resolved: `scrimmage/checks.py`
  or `scenario_runner.py`), `scrimmage/blocks/program.py`, `conditions.py`, `run.py`, `palette.py`
- Test: `tests/test_element_signal.py`

**Interfaces:**
- **Signal:** `element.<name>` for each hinged or sliding element. A hinged element reads degrees, unwrapped from the
  joint's qpos (MuJoCo's hinge qpos is already unwrapped). A sliding element reads inches.
  - It is listed in `palette()["signals"]`, and `palette()` gains `moving_elements: [{name, unit: "deg" | "in"}]`.
  - A scenario check can name it like any other signal.
- **Condition** `"element"`: `{element, above | below: float}`, exactly one of the two, in the element's unit.
  `describe` reads "door is above 45°" or "portcullis is above 12 in".
- **Problems:** an unknown element, or one that doesn't move, reads "<name> isn't a moving element; this field's
  moving elements: ...".
- [ ] **Step 1: Write the failing tests:**
  - **`test_element_condition`:** a stand-in on a test range with a hinged door. A `move_object`-free setup:
    `wait_until element door above 30 timeout 3` passes when the robot drives into the door, or apply the push by
    placing the door in the robot's path. It also fails with the plain message when the door isn't pushed.
  - **`test_element_signal_in_scenario`:** a scenario check on `element.door` reads degrees.
  - **`test_element_names_checked`** and **`test_element_file_shape`.**
- [ ] **Step 2–4: Implement and run** them plus `-k "blocks or scenario or golden"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "blocks: an element condition and element.<name> signals"`

### Task 3: Opt-in collisions for stock mechanisms

**Files:**
- Modify: `scrimmage/parts/stock/mechanisms.py`, `climber.py` (only if its mechanism geoms are non-colliding today),
  `scrimmage/testrange/shooter.py` (the C5 check), `scrimmage/assembler/static/inspector.js` (a bool box, if missing)
- Test: `tests/test_stock_collide.py`

**Interfaces:**
- **Parameter:** `Param("collide", "", False, "its geoms touch the field, game pieces and a partner robot (never the
  robot's own parts)", kind="bool")` on elevator, arm, flywheel, roller, launcher, disc_shooter, turret and
  cylinder.
  - **When true:** every geom that isn't drawing-only gets `collide: True`.
  - **Drawing-only geoms, never colliding:** the launcher's hood arc, a disc shooter's guide plates, and a
    cylinder's drawn barrel in push form.
- **Error (C5):** a stock part with `collide = true` that is a `[shooter]` stage, its intake part or its launcher
  reads "<part> has collide = true but is the [shooter]'s <stage/intake/launcher>; the ball path moves those balls,
  so leave collide off".

- [ ] **Step 1: Write the failing tests:**
  - **`test_collide_default_off`:** a default elevator's geoms have the no-contact bits (unchanged).
  - **`test_collide_on_elevator`:** with `collide = true` its stage geoms have the robot bits (2, 1). A carriage
    raised into a box element above it stops at the box.
  - **`test_collide_on_roller_moves_a_ball`:** a spinning roller with `collide = true` touching a ball pushes it.
  - **`test_collide_shooter_stage_errors`** (Review Focus 5).
  - **Inspector:** a browser test, in the existing inspector browser test file, that the Robot tab shows a checkbox
    for `collide` and saves `collide = true`.
- [ ] **Step 2–4: Implement and run** them plus `-k "stock or parts or golden or browser"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "parts: stock mechanisms can collide (collide = true)"`

### Task 4: Clearance

**Files:**
- Create: `scrimmage/blocks/clearance.py`
- Modify: `program.py`, `conditions.py`, `run.py`, `palette.py`
- Test: `tests/test_clearance.py`

**Interfaces:**
- **`clearance(world, element: str) -> float`:** the smallest distance (m) between any robot geom that collides and
  any geom of `element.<element>` (its bodies, or its static geoms named `element.<element>...`).
  - **Computation:** `mujoco.mj_geomDistance` with distmax 0.5, after pruning pairs whose bounding spheres are more
    than 0.5 m apart.
  - **Result:** `math.inf` when nothing is within 0.5 m. A touch or overlap gives 0.0 (never negative).
  - **Speed:** cache the geom lists per world.
- **Condition** `"clears"`: `{element, at_least_in: float > 0}`. It holds while `clearance ≥ at_least_in`.
  `describe` reads "the robot clears trench by at least 1 in".
- **Signal:** `clearance.<element>` in inches, NaN when over 0.5 m. It is sampled only when the test names it, in the
  condition or the chart.
- **Result:** `RunResult.clearance: dict[element, {"min_in": float | None, "t": float | None}] | None`. The lines
  say "closest to trench: 0.8 in at 3.1 s", or "trench: never within 20 in".
- **Palette:** `palette()["elements"]` already lists the field elements. Use it for the names.

- [ ] **Step 1: Write the failing tests:**
  - **`test_clearance_under_a_box`:** the stand-in chassis is under a box 0.30 m up. The clearance is the box's
    bottom minus the chassis's top, within 1 mm.
  - **`test_clearance_far`** (Review Focus 4): far from it, it's inf, the chart shows NaN, and the line reads
    "never within 20 in".
  - **`test_clears_condition_fails_on_touch`:** a robot driving into the box fails `expect clears box at least 0.5
    throughout`, and the message names the time and the clearance.
  - **`test_clearance_cost`:** a run without `clears` and without a clearance chart signal never calls
    `mj_geomDistance` (monkeypatch it to raise).
- [ ] **Step 2–4: Implement and run** them plus `-k "blocks or golden"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "blocks: clearance to a field element (clears condition, clearance signal)"`

### Task 5: The partner robot

**Files:**
- Create: `scrimmage/parts/partner.py`
- Modify: `scrimmage/parts/assemble.py` (build it), `scrimmage/world.py` (reset it to its start),
  `scrimmage/blocks/program.py` (`[setup] partner`), `scrimmage/blocks/run.py` (`setup_config`), `conditions.py`,
  `palette.py`, and the scenario signal resolver
- Test: `tests/test_partner.py`

**Interfaces:**
- **`[partner]` keys:**
  - shape and weight: `length` (0.7), `width` (0.7), `mass` (60.0, an estimate), `bumper_clearance` (0.02);
  - the wheels: `brake` (true);
  - where it starts: `start = [x, y, heading_deg]` (required in a config; Setup fills it in).
  - Unknown keys error and list the keys.
- **`[setup] partner` in a test file:** a table with `ahead_in` (distance from the robot's start to the partner's
  center, along the robot's heading), `heading_deg` (relative to the robot's) and `brake`, or `false` for none. It
  becomes a `[partner]` with an absolute `start` through `setup_config`.
- **Build:** `partner.py`'s `add_partner(spec, cfg) -> PartnerInfo(body: str, wheels: list[str], start)`.
  - **Parts:** the stock chassis and differential part dicts, with `mass` going to the chassis.
  - **Bits and exclusions:** contype 3, conaffinity 3 on every partner geom, and `spec.add_exclude` for every pair
    of partner bodies.
  - **Joints:** a free joint `partner.free`.
  - **Brake:** wheel joint damping of 20 N·m·s/rad, an estimate of a braked drive's back-EMF; coast leaves it at 0.
  - **Bindings:** none; it has no robot program.
- **Signals:**
  - `partner.height` (in): the lowest of the chassis's four frame-bottom corners, minus its height after the settle;
  - `partner.tilt` (deg): the chassis z axis's angle from vertical;
  - `partner.moved` (in): the horizontal distance from where it settled.
  - All of them also appear in `get_state` under `partner` when present.
- **Conditions:**
  - `"partner_lifted"`: `{at_least_in, level_within_deg: float | None}`. It reads "the partner is lifted at least 4
    in (level within 5°)".
  - `"partner_moved"`: `{at_least_in}`.
- **Start check** (Review Focus 3): the partner's outline (frame plus bumpers) may not overlap the robot's start
  outline (`ROBOT_OUTLINE`) or any element standing on the carpet. The messages are as in Review Focus 3.

- [ ] **Step 1: Write the failing tests:**
  - **`test_partner_builds_and_settles`:** a stand-in robot with `[partner] start = [4, 4, 0]`. After 1 s settling
    the partner's chassis is at rest on its wheels: `partner.height` ≈ 0 and tilt < 1°.
  - **`test_partner_touches_robot_and_field`:** driving the stand-in into the partner pushes it (`partner.moved`
    > 2 in) and doesn't pass through. The partner never falls through the carpet.
  - **`test_brake_resists_more_than_coast`:** the same push for 2 s moves a coasting partner farther than a braked
    one, by at least a factor of 3.
  - **`test_partner_overlap_errors`** (Review Focus 3).
  - **`test_no_partner_unchanged`:** a config without `[partner]` has no `partner.` bodies (the golden test covers
    the stepping).
  - **`test_setup_partner_ahead`:** `[setup] partner = { ahead_in = 60 }` puts the partner 60 in ahead along the
    robot's start heading, within 1 mm.
- [ ] **Step 2–4: Implement and run** them plus `-k "partner or blocks or world or golden"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "a passive partner robot ([partner], Setup's partner, lifted and moved conditions)"`

### Task 6: The trench, the defenses and the control panel

**Files:**
- Create: the built-in ranges `2026-trench.toml` and `2016-defenses.toml` (next to the other built-in range files)
- Modify: `terrain-library.toml` (add the control panel wheel)
- Test: `tests/test_new_ranges.py`

**Interfaces:**
- **`2026-trench`:**
  - **Structure:** one 2026 TRENCH, built from box elements: the arm spanning the opening with its underside 22.25 in
    above the carpet, and the side structures, sized from the 2026 manual §5.6.
  - **Starts:** `[start.approach]`, 1.5 m before the opening, facing through it.
  - **Sources:** cite the manual numbers in comments, and mark every other number as an estimate.
- **`2016-defenses`:** in a row along the field, each with a start before it:
  - the portcullis: sliding, axis z, 2.27 kg (5 lb), sized from the 2016 manual's defense figures;
  - the sally port: hinged "left", breakaway set so it takes 1.5 lb (6.7 N) at its handle's distance to open;
  - the drawbridge: hinged "bottom", leaning up, which a robot pulls or pushes down;
  - the cheval de frise: four hinged plates, "center" on a horizontal axis, tilting under a robot.
  - Numbers not in the manual are marked as estimates.
- **`terrain-library`:** `control_panel`, the 2020 CONTROL PANEL wheel. A disc of 32 in diameter, hinge "center" on a
  vertical axis, no stops, its rim at the manual's height. The manual number is cited in a comment.

- [ ] **Step 1: Write the failing tests:**
  - **`test_trench_clearance`:** `load_range("2026-trench")` builds. The trench arm's underside is 22.25 in (±0.01
    in) above the carpet. The approach start isn't refused.
  - **`test_defenses_build`:** each defense's joint exists, and the portcullis takes 22.2 N ± 5% to lift (as in Task
    1).
  - **`test_control_panel_spins`:** the panel turns three times under a steady torque.
  - **`test_test_setup_lists_them`:** the Test tab's field choices include `2026-trench` and `2016-defenses`.
- [ ] **Step 2–4: Implement and run** them plus `-k "range or terrain or golden"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "ranges: a 2026 trench, the 2016 defenses, the 2020 control panel"`

### Task 7: The block editor and Setup

**Files:**
- Modify: `scrimmage/assembler/static/blocks_defs.js`, `test.js` (Setup's partner), `test_run.js` (the clearance
  lines), `scrimmage/assembler/test_session.py`
- Test: the existing Test tab browser and server tests' files

**Interfaces:**
- Consumes the condition kinds `element`, `clears`, `partner_lifted` and `partner_moved` (Tasks 2, 4 and 5), the
  palette keys `moving_elements` and `elements`, a partner flag in the palette (`partner: bool`), and
  `RunResult.clearance`.
- **Blocks:**
  - *[element] is [above/below] [n] [°/in]*, whose unit follows the element;
  - *robot clears [element] by at least [n] in*;
  - *partner is lifted at least [n] in [level within n°?]*;
  - *partner moved at least [n] in*.
  - Each is hidden when its list is empty or there is no partner.
- **Setup:** a "Partner robot" section with a checkbox, "ahead of my robot by [n] in", "facing [same way / toward
  me / sideways]" and "brakes on". It writes `[setup] partner`.
- **Run panel:** it shows the clearance lines.
- [ ] **Step 1: Write the failing tests:**
  - every new block round-trips to TOML;
  - Setup's partner checkbox writes and clears `[setup] partner`;
  - blocks are hidden for a robot on a field with no moving elements and no partner.
- [ ] **Step 2–4: Implement and run** them plus the Test tab browser tests. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "test tab: blocks for moving elements, clearance and the partner; Setup's partner robot"`

### Task 8: Two worked examples

**Files:**
- Create: `examples/blocks/trench/` (stand-in config, `clear.test.toml`, `README.md`) and
  `examples/blocks/partner/` (stand-in config, `push.test.toml`, `README.md`)
- Test: `tests/test_examples_trench_partner.py`

- **trench:**
  - **Robot:** the kitbot stand-in with a stock elevator and a stock arm, `collide = true`, on `2026-trench`.
  - **Program:** drive under the trench stowed, with `expect clears trench_arm by at least 0.5 in throughout`, and
    record the smallest clearance.
  - **Second test file:** the arm raised drives into the arm, the check fails, and the README tells that story.
- **partner:**
  - **Robot:** the kitbot stand-in, and `[setup] partner = { ahead_in = 50, brake = true }`.
  - **Program:** drive into it at 6 V for 2 s, then `expect partner moved at least N in`, with N measured minus a
    margin.
  - **README:** the coast comparison, measured, as a sentence.
- [ ] **Step 1: Write the tests** to assert what the READMEs state.
- [ ] **Step 2: Build and tune** until they pass, then run them. Expected: PASS.
- [ ] **Step 3: Commit:** `git commit -m "examples: a trench clearance test and a partner push in the Test tab"`

### Task 9: Docs and the spec's decisions

**Files:**
- Modify: `docs/terrain.md` (the new kinds, the defenses), `docs/parts.md` (`collide`), `docs/test-tab.md` (the
  conditions, Setup's partner, the examples, the example count), `docs/test-range.md` (the trench range, and "What
  isn't modeled"), `README.md` (signals, `[partner]`), and the spec's "Decisions made during the build" (from the
  ledger's rulings)
- [ ] **Step 1:** Write them. Every new key goes in a table, and every estimate is called one with its source.
- [ ] **Step 2:** Run the docs tests and the full suite once. Expected: PASS.
- [ ] **Step 3: Commit:** `git commit -m "docs: moving elements, collide, clearance and the partner robot"`
