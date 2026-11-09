# NOTE shooting and scoring (K2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a rigid NOTE can be shot from the robot, fly with orientation-dependent drag, count in a goal, be aimed at an upward-entry goal, and be flown on the bench.

**Architecture:**
- One drag model for rigid pieces (`launcher/rigid_aero.py`) and one 6-DOF integrator (`launcher/flight6.py`),
  shared by the world, the bench and `launcher aim`.
- A new analytic face-pinch launcher (`launcher/pinch.py`) behind a new `pinch_shooter` stock part.
- Goals and aim work from the goal's real scoring surface.

**Tech Stack:** Python 3.11+, MuJoCo 4, numpy, pytest (with Playwright browser tests in the full suite).

**Spec:** docs/superpowers/specs/2026-11-08-note-shooting-design.md

**Background:**
- The code survey with file:line for every restriction is
  `/tmp/claude-1000/-home-altainia-code-scrimmage/3537f469-7b90-4632-aa74-f3b52c0bc63a/scratchpad/kickoff/k2-survey.md`.
  Read the section for your task.
- The dry run's 2024 work files (range, bench, test) are under
  `/tmp/claude-1000/-home-altainia-code-scrimmage/3537f469-7b90-4632-aa74-f3b52c0bc63a/scratchpad/kickoff/k1/work/`.

## Global Constraints

- **Balls and discs unchanged:** their launch, flight and scoring numbers are identical. These tests stay
  untouched and green:
  - `tests/test_range_ledger_golden.py`
  - `tests/test_range_e2e.py::test_aim_table_is_current` (the `AimTable.java` pin)
  - `tests/test_range_discs.py`
  - `tests/test_launcher_*.py`
- **Commands:**
  - Never `uv run`. Use `.venv/bin/python -m pytest`.
  - Don't run `git checkout`, `git switch` or `git stash`.
  - Don't push.
  - Don't dispatch subagents.
- **Messages:**
  - Every user-facing message goes through `i18n.t`, with table and key names as placeholders.
  - en-GB entries are needed wherever a string contains a word on the en-GB spelling list.
  - Call `i18n.t` directly, not through a local alias, because `i18n template` skips aliased calls.
- **Units:** printed measured values use the unit helpers. Add new modules that print values to the
  test_units_sweep `PY` list.
- **Preset numbers:** every new preset number carries a `# source:` or `# estimate:` comment.
- **Wording:** American spellings and plain words in docs and messages.
- **Commits:** one per task, message `K2: <what>` with the trailer
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Done means:** the task's tests and the full suite (`.venv/bin/python -m pytest -q tests/`) pass, and the report
  lists what changed, the decisions made with their costs, and the suite summary line.

## Review Focus

1. **A ball or disc number drifts.** Rigid-slot code paths leak into balls (for example, `apply_flight_forces`
   ordering, or the default-kind pick on a mixed field). The goldens must be byte-identical.
2. **A ring "scores" through a solid part.** It crosses the scoring surface behind the hood roof or through a plate.
   Aim and the bench goal check must reject paths that hit obstacles or plates first.
3. **A tumbling piece's drag.** The projected area must be right at every orientation (face-on, edge-on, at 45°),
   and the drag must oppose the air-relative velocity.
4. **A rigid-only field.** No ball or disc kind: the goal's default kind, the shooter's piece choice and the throat
   check must not crash on `diameter = None`.
5. **The aim speed-up changes hub rows.** Caching per angle must give the same rows as before for a hub.

---

### Task 1: goals count any piece kind (spec D1)

**Files:**
- Modify: `scrimmage/testrange/spec.py` (`_counted`, the throat check), `scrimmage/testrange/balls.py` (default kind)
- Test: `tests/test_range_kinds.py`, `tests/test_range_piece_world.py`, a new `tests/test_range_rigid_goals.py`
- Docs: `docs/test-range.md` (the "goals count balls and discs" lines)

**Produces:** `passing_width(kind) -> float` in `scrimmage/testrange/spec.py`. It is the ball or disc diameter, or
for a rigid kind the smallest side of its body-frame bounding box. Tasks 3 and 4 use it.

- [ ] Replace the two refusal tests with tests that a goal naming a rigid kind builds and counts it.
  - A NOTE dropped through a horizontal goal counts 1.
  - A NOTE that misses counts 0.
  - On a rigid-only field, a goal with no `kind` counts the field's first kind.
- [ ] Test the throat. A rigid kind through a throat narrower than its middle side gives a warning containing
  "edge", not an error. A rigid kind with no diameter doesn't crash.
- [ ] Implement it, update the docs, run the full suite, and commit.

### Task 2: rigid-piece drag and a shared 6-DOF flight (spec D2)

**Files:**
- Create: `scrimmage/launcher/rigid_aero.py`, `scrimmage/launcher/flight6.py`
- Modify: `scrimmage/disc/flight.py` (becomes a thin caller of flight6 with its Hummel force function),
  `scrimmage/testrange/balls.py` (`apply_flight_forces` for rigid slots), `scrimmage/testrange/pieces.py` (the new
  optional keys `drag_coefficient`, default 1.17, and `spin_damping`, default 0)
- Test: a new `tests/test_rigid_aero.py`; `tests/test_range_discs.py` and the disc flight tests unchanged

**Produces:**
- `rigid_aero.forces(box_half_extents, cd, spin_damping, rho, vel_world, rot_world, omega_world) -> (force3, torque3)`
- `flight6.fly(pos, vel, quat, omega, mass, inertia, force_fn, *, dt, until) -> Flight6Result`
  - `force_fn(vel, rot, omega) -> (force, torque)`
  - `until(pos, prev_pos)` returns True to stop
  - `Flight6Result` has `samples` (t, pos, quat) and the end state

- [ ] Test the projected area:
  - face-on, a 0.3556 × 0.3556 × 0.0508 box gives A = 0.3556²;
  - edge-on gives 0.3556 × 0.0508;
  - at 45°, it is between the two and equals the |n̂·v̂| sum.
- [ ] Test the force: it is exactly opposite the velocity, and |F| = ½ρ·Cd·A·v².
- [ ] Test spin damping: zero by default, and −c·ω when set.
- [ ] Test the world:
  - a rigid NOTE thrown at 10 m/s in the world lands shorter than with gravity only;
  - thrown flat and moving in its own plane (the small area faces the air), it lands further than one thrown
    face-first into the air (the large area).
- [ ] Test the discs: the disc flight's existing tests and goldens are unchanged.
- [ ] Implement it, run the full suite, and commit.

### Task 3: the bench flies a NOTE and scores it against a range goal (spec D3)

**Files:**
- Modify: `scrimmage/bench/spec.py` (`[piece]` takes `preset = "<piece preset>"` and `shape = "ring"`; `[flight]`
  takes `goal = { range, name }` with `at` and `heading_deg`), `scrimmage/bench/report.py` (rigid landings use
  flight6 + rigid_aero; the "flies balls only" text and check warning are removed), `scrimmage/bench/build.py`
  (ring geometry, through the range's piece builder)
- Test: `tests/test_bench_flight_warning.py` (now asserts no warning), a new `tests/test_bench_rigid_flight.py`
- Docs: `docs/bench.md`
- en-GB: remove the now-unused entries for the removed strings

- [ ] Test that a bench file with `preset = "2024-note"` builds a ring piece with the same mass and size as the
  range's.
- [ ] Test that `[flight]` with a rigid piece reports landings: a distance and a time.
- [ ] Test the goal: with `goal = { range = <2024 test range>, name = "speaker_blue" }`, a shot aimed into it reports
  scored, and a short shot reports missed.
- [ ] Implement it, update the docs, run the full suite, and commit.

### Task 4: `launcher aim` aims at the scoring surface (spec D4)

**Files:**
- Modify: `scrimmage/testrange/aim.py` (and its CLI in `scrimmage/__main__.py` only if a flag is needed)
- Test: `tests/test_range_aim*.py` (existing tests unchanged), a new `tests/test_aim_upward.py`
- Docs: `docs/test-range.md` (the aim section)

- [ ] Test the hub regression: today's hub aim rows are recorded (from main, before any change) and are identical
  after.
- [ ] Test an upward goal: on a small range with a SPEAKER-like goal (`heading 180`, `tilt 165`, a roof obstacle
  above its opening), aim gives at least 3 scoring rows for distances from 1.5 to 4 m. Each row's simulated shot
  crosses the opening going up, inside it, and touches no obstacle before the crossing.
- [ ] Test that a path through the roof is rejected: a row whose straight shot would cross the surface only after
  passing through the roof box isn't offered.
- [ ] Test speed: 5 SPEAKER distances in under 15 s on this machine. Mark it as a timing test with generous slack,
  following how the repo marks such tests.
- [ ] Implement it, run the full suite, and commit.

### Task 5: a face-pinch shooter for rigid pieces (spec D5)

**Files:**
- Create: `scrimmage/launcher/pinch.py` and the `pinch_shooter` stock part, following how
  `scrimmage/parts/stock/mechanisms.py` defines `disc_shooter`
- Modify: `scrimmage/testrange/shooter.py` (`SHOOTER_KINDS` gets `"piece"`; `piece = "<kind>"`; refused unless the
  part is a pinch_shooter; one-lane path), `scrimmage/testrange/launch.py` (`exit_state_piece`, with the orientation
  from the part's channel), `scrimmage/testrange/runtime.py`, `scrimmage/testrange/pieces.py` (the optional
  `stiffness` key, N/m), `scrimmage/launcher/presets.toml` (the 2024 NOTE's `stiffness`, tagged estimate with its
  reasoning)
- Test: a new `tests/test_pinch_launcher.py` and `tests/test_range_piece_shooter.py`
- Docs: `docs/launcher.md`, `docs/test-range.md`, the parts docs that list stock parts

- [ ] Test the model on its own:
  - exit speed rises with wheel speed;
  - equal top and bottom surface speeds give no pitch spin, and a faster top wheel gives backspin of the right sign;
  - the energy ledger closes to 1%;
  - a gap wider than the piece's thickness jams (or doesn't grip) with a message.
- [ ] Test against the bench: with the dry run's `note_shooter.toml` geometry (top and bottom 4 in wheels, 1.25 in
  gap, 2000 N/m), the analytic exit speed is within 15% of a bench run's.
- [ ] Test it in the world: a robot with a `pinch_shooter` and `[shooter] kind = "piece"`, `piece = "note"` on a
  2024-like range shoots a NOTE that leaves the robot at the model's exit speed (±5%) in the part's channel
  direction.
- [ ] Test the refusals: `kind = "piece"` without a pinch_shooter part, an unknown piece name, and a piece name that
  is a ball are each refused in words.
- [ ] Implement it, update the docs, run the full suite, and commit.

### Task 6: the 2024 dry run again, and a 2024 range in the repo

**Files:**
- Create: `scrimmage/testrange/ranges/2024.toml` (or wherever built-in ranges live), from the dry run's
  `work/range/2024.toml`
- Create: an example test under `examples/` that shoots a NOTE into the blue SPEAKER from the subwoofer, with a
  pinch_shooter robot config
- Test: `tests/test_range_2024.py`
  - the range checks clean;
  - the example test passes;
  - a SPEAKER aim table has at least 3 rows.
- Docs: remove the remaining "balls and discs only" lines; the dry-run findings doc gets a "K2 status" note under
  gaps 1 and 12.

- [ ] Write the tests, add the range and the example, run the full suite, and commit.
