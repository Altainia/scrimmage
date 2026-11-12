# Soft-NOTE robustness and the intake example (K6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the world's soft NOTE can't be pushed through a thin part, through itself or under a robot by any FRC
load; if it blows up anyway the run says so; its `stiffness` and `restitution` keep their meaning; and
`examples/blocks/note-intake/` picks it off the 2024 carpet and passes the spike's robustness grids.

**Architecture:**
- The ring's contact (`testrange/soft.py`) becomes a ramp: the solimp stiffens past about 60 % of the squash, and
  the solref is scaled so a shallow contact is exactly today's (K6-R1). The damping table is refit (K6-R6).
- Before `spec.compile()`, `parts/assemble.py` pairs each ring capsule with every colliding geom of each robot or
  partner body that has a priority geom, using the ring's solref and solimp (K6-R2).
- `SoftPool.shadow()` gains a guard that resets a blown-up ring and notes it. `RangeRuntime` reads MuJoCo's
  BADQACC warning. The blocks `RunResult` counts `soft_resets` (K6-R7).
- Smaller fixes: a hopper taken from `piece_in` (K6-R12), the piece table reading the feed outcome (K6-R13), and
  a bench plate's `surface_speed` (K6-R14).

**Tech Stack:** Python 3.11+, MuJoCo 4.0.1 (MjSpec pairs and excludes, geom `surfacevel`), numpy, tomlkit, pytest,
GitHub Actions.

**Spec:** docs/superpowers/specs/2026-11-12-soft-note-robustness-design.md (rulings K6-R1 to R16). It is binding;
where this plan narrows it, the ruling below says so. The spike evidence is a reference only and may be gone:
`/tmp/claude-1000/-home-altainia-code-scrimmage/3537f469-7b90-4632-aa74-f3b52c0bc63a/scratchpad/intake-spike/`
(`RESULTS.md`, `winner/`, `k6/`: `scenes.py`, `patch.py`, `harness.py`, `sweep.py`, `*_cases.json`, `*.jsonl`).
`$SPIKE` below means that folder. Only Task 1 runs anything from it, and no committed file imports from it.

## Global Constraints

The spec's "Rules every task follows", and the owner's project rules:

- Plain American English. Every user-facing string goes through i18n.t / i18n.tn. Add an en-GB entry only where
  British English differs, and run `scrimmage i18n check en-GB` (exit 0).
- Every new number is tagged `# source:`, `# estimate:` or `# choice:`.
- Run tests with `.venv/bin/python -m pytest`, never `uv run`. From the worktree /home/altainia/code/scrimmage-k6
  (branch `soft-note-robust`), that is
  `PYTHONPATH=/home/altainia/code/scrimmage-k6 /home/altainia/code/scrimmage/.venv/bin/python -m pytest`.
- Test helpers are imported bare (`from soft_helpers import ...`), never as `from tests.x import ...`.
- Wait on a run by its PID (`while kill -0 $PID 2>/dev/null; do sleep 20; done`), never with `pgrep -f`.
- Timing never gates CI. A wall-time assertion runs only under `SCRIMMAGE_TIMING=1`, as `test_what_a_soft_note_costs`
  does today.
- Never commit under `.superpowers/`.
- Re-record a golden only for the reason K6-R9's table gives for it. A golden that moves with no reason there is a
  bug: stop and report it.
- **Branch:** the branch already has origin/main merged (PR #39, bridge.py). Don't run `git checkout`, `git switch`
  or `git stash`, and don't push.
- **The full suite** (`... -m pytest -q tests/`, about 40 minutes) runs once per task, at the end, in the background.
  Write it to a log in the scratchpad and wait on its PID.
- **Unchanged (spec "Goal and limits"):** balls, discs, rigid pieces and the rigid NOTE give the same numbers. These
  pass untouched: `tests/data/ledger_golden.json`, `range_ball_golden.npz`, `rangeless_golden.npz` and
  `tests/data/bench_flex_golden.json`. The world's timestep and integrator, the swap (K5 R11–R13), the pool, and the
  bench flex NOTE's model and fit don't change.
- **Tests written ahead of their fix** are marked `pytest.mark.xfail(strict=True, reason="K6 Task N")`. The task
  that fixes them removes the mark. A task may xfail only tests that a later task's ruling owns.
- **Commits:** one per task, message `K6: <what>`, ending with the trailer
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Done means:** the task's tests and the full suite pass. The report lists what changed, each decision with its
  cost, and the suite's summary line.

## Rulings made while planning

These cover what the spec leaves open, or where it can't be applied as written. Each gives the ruling, why, and
its cost.

- **K6-P1, order.** Task 1 runs the intake grid at 1.0 r first, through the spike's in-process patch on the
  example's own files, so the geometry later tasks document is settled. The other tasks follow the spec's order:
  2 = scenes (R8, R11), 3 = R1, 4 = R6 and R9, 5 = R2, 6 = R7, 7 = R12 and R13, 8 = R14, 9 = R10's remainder.
  *Cost:* the grid is run twice: here with the patch, and in Task 9 with the shipped code.
- **K6-P2, each grid setting is one body move in the example's files.** The spike varied its geometry through a
  generator, which doesn't ship (K6-R15). The example's `part.toml` is the spike's `winner/parts/intake/part.toml`
  with one change: the floor's front is a separate body, so that moving the lip is one `pos` edit.
  **Narrowed by Task 1 (see its record):** there is no `lip` body. The lip setting moves `bottom` and the bin's front
  ends as the spike's generator did, and the front roller hangs on a sprung `arm` (K6-P3 (c)).
  - The body `lip` (parent `frame`, pos 0) holds the front floor block: `box = [0.0608, 0.4064, 0.0498]` at
    `[0.4412, 0.0, 0.0269]`, friction 0.4, priority 1. It spans 0.4108–0.4716 m, which is 2 in plus 1 cm.
    `# derived:` it still meets the main slab with the lip at +1 in.
  - The `hopper` floor slab is shortened to end at 0.4412: `box = [0.4285, 0.4064, 0.0498]` at `[0.22695, 0, 0.0269]`.
  - The body `bottom` (the bottom roller) moves under `lip`, at the same position.
  - The settings, as `tests/intake_grid.py` applies them:
    - axle `a` in: `roller` and `top0` z += a·IN;
    - lip `l` in: `lip` x += l·IN;
    - clearance `c` mm:
      - Δ = (c − 3)/1000;
      - the standin's intake mount z += Δ;
      - `roller` z −= Δ;
      - `roller` x += √(cd² − (h0 − Δ)²) − √(cd² − h0²), with cd = 0.08636 m and h0 = 0.02748 m
        (`# source: the spike's designs.nip: the front roller placed from the bottom one, 3.4 in between centers`);
    - yaw: the mount's `rot` z;
    - offset: the NOTE's placement.

  *Cost:* the spike's generator also moved the walls' and lid's front ends with the lip, which this doesn't. Task 1's
  fix-off corner run checks that the two are the same design.
- **K6-P3, the retune candidates' values** (K6-R10 step 2, in that order). These are choices. Each one is an edit to
  the example's `part.toml`:
  - (a) A 1.6 in nip: `roller` x += 5.34 mm (`# derived: centers 3.6 in apart`).
  - (b) The front roller 0.4 in higher (its bottom at 1.6 in), at the 1.4 in nip: `roller` z += 10.16 mm and
    x −= 4.15 mm.
  - (c) A sprung arm:
    - a body `arm` (parent `frame`, mass 0.1 `# choice`), hinged about y at the roller's center
      + (−0.0762, 0, +0.035) m;
    - `roller` moves under it;
    - joint range [0, 25]°, and `spring = { stiffness = 14.0, rest = -5 }`
      (`# choice: about 15 N of preload at the roller, the 30 % squeeze's force, so the nominal nip still grips`).
    - If (c) misses the target, its stiffness is tried at 7.0 and then at 28.0, and no further.
- **K6-P4, "kinetic energy never rises above its start" (K6-R11).**
  - In a scene that nothing drives (slam, drop), the ring's mechanical energy (kinetic plus m g z) never rises above
    its start by more than 0.1 % (`# choice: integrator noise`).
  - In a driven scene (press, taco, slab, squeeze), the ring starts at rest and the load does work, so the energy
    check is the finite state and the 20 m/s bound.
  - *Cost:* a driven scene that gains energy below 20 m/s isn't caught.
- **K6-P5, "the guard never fires" in a scene.** The scenes are a ring alone, with no SoftPool. The guard fires only
  on a segment over GUARD_SPEED (25 m/s) or a non-finite state. Each scene asserts a finite state and every segment
  under 20 m/s, which is stricter. *Cost:* none.
- **K6-P6, timing opt-in.**
  - The pair build-time bound (under 1.2×) and the example's wall-time bound (under 10 s) run only under
    `SCRIMMAGE_TIMING=1`.
  - CI asserts the pair count and the example's pass, not their times.
- **K6-P7, the BADQACC note lives in `RangeRuntime`.**
  - Every world on a range reads it, soft pool or not.
  - `RangeRuntime.notes(lang)` returns the soft pool's notes plus this one. Its two callers move to it:
    `blocks/run_notes.py:88` and `scenario_runner.py:707`.
  - *Cost:* a world with no range isn't covered. Every soft NOTE is on a range.
- **K6-P8, `soft_resets` is on the blocks `RunResult`** (`scrimmage/blocks/run.py:180`), not on the launcher's or
  the disc runner's. It is also in MCP `test_run`'s result dict (`mcp_server.py:723`). *Cost:* none.
- **K6-P9, if the damping table must become two-dimensional (K6-R6).**
  - Rows `(stiffness, restitution, scale)` for stiffness {1000, 2000, 6000, 20000} × restitution {0.27, 0.30, 0.45}
    (`# choice: the restitution range's ends and the fit's point`).
  - Interpolated bilinearly in (log stiffness, log restitution), held at the ends.
  - `damping_scale(stiffness, restitution)`.
- **K6-P10, `surface_speed` runs along the plate's own x** (K6-R14 gives both "the bench's +x" and "the plate's own
  x"). For an unturned plate the two are the same. A turned plate, such as a ramp, carries along its own surface.
  *Cost:* none.
- **K6-P11, naming the geoms that a pair needs.**
  - MuJoCo pairs take geom names. A paired geom with no name gets `"<body name>#soft-pair-<k>"`.
  - `flexcontact.id_name` drops a trailing `#soft-pair-<k>`, so contact names in reports (`first_hit`, stuck on
    ...) stay the body's, as today.
  - *Cost:* one suffix rule in `id_name`.
- **K6-P12, where a guard reset puts the ring.**
  - A finite ring goes to its read center and Kabsch rotation, at its center's velocity capped at
    GUARD_RESET_SPEED, with no spin.
  - A non-finite ring goes to its stand-in's last shadow pose, at rest.
  - Its dofs' `qacc_warmstart` is zeroed.

## Review Focus

1. **A reset after a guard reset.** Every Test-tab run calls `World.reset`. A run after one that blew up must start
   with `soft_resets` 0 and no "blew up" note. Pinned in Task 6 by `test_a_reset_clears_the_guards_note_and_count`.
2. **Rings that are parked, asleep or free never trip the guard.** A parked ring sits 20 m under the field and can
   sleep. The guard must read only in-use, awake rings. Pinned in Task 6 by
   `test_parked_and_sleeping_rings_never_trip_the_guard`.
3. **A partner's priority geoms, and a robot with none.** The partner must be paired, as the spec says. A robot
   with no priority geom gets no pairs and builds exactly as today. Pinned in Task 5 by
   `test_a_partners_priority_geoms_are_paired` and `test_no_priority_geom_no_pairs`.
4. **A test with no hopper and no `piece_in`, and one with `hopper = []`.** Both keep today's words ("stuck in the
   robot"). Only a `piece_in` body becomes the hopper. Pinned in Task 7 by
   `test_no_hopper_and_no_piece_in_is_unchanged` and `test_a_cleared_hopper_stays_none`.
5. **`surface_speed` on a ramp, and a negative one.** A turned plate carries along its own face (K6-P10), and a
   negative speed carries back. Pinned in Task 8 by `test_a_turned_plate_carries_along_its_face` and
   `test_a_negative_surface_speed_carries_back`.

---

### Task 1: the intake grid at 1.0 r, on the example's own files (K6-R8's speeds, K6-R10 retune, K6-R15)

**Files:**
- Modify: `scrimmage/testrange/soft.py`: `Ring.speeds` after `Ring.momentum` (~line 297).
- Create:
  - `examples/blocks/note-intake/note-intake-standin.toml`: `$SPIKE/winner/note-intake-standin.toml` as is;
  - `examples/blocks/note-intake/parts/intake/part.toml`: `$SPIKE/winner/parts/intake/part.toml` with K6-P2's
    `lip` body;
  - `examples/blocks/note-intake/intake.test.toml`: `$SPIKE/winner/note-intake.test.toml`, with
    `robot = "note-intake-standin.toml"`.
- Create: `tests/intake_grid.py`.
- Test: `tests/test_range_soft.py` (speeds), `tests/test_examples_note_intake.py` (new).
- Modify: this plan's "Task 1's record", at the end.

**Interfaces:**
- Produces:
  - `Ring.speeds(data: mujoco.MjData) -> np.ndarray`: shape (16,), each segment's center speed (m/s), from
    `_segments` (never `cvel`).
  - In `tests/intake_grid.py`:
    - `EXAMPLE = <repo>/examples/blocks/note-intake`, `IN = 0.0254`;
    - `SPEED_PER_V = 0.32  # source: intake spike, speed.py`;
    - `REACH, GAP_IN, TRAVEL = 0.6, 20.0, 1.4  # source: intake spike harness (run_case)`.
  - `@dataclass(frozen=True) class Case`: `grid: str`, `label: str`, `x: object`, `axle_in: float = 0.0`,
    `lip_in: float = 0.0`, `clear_mm: float = 3.0`, `yaw_deg: float = 0.0`, `volts: float = 8.0`,
    `speed: float = 1.0`, `offset_in: float = 0.0`.
  - `oat_cases() -> list[Case]`: 45 cases, `$SPIKE/oat2.py`'s list in its order: nominal, control 0 V, then axle,
    lip, volts, speed, yaw, offset and clearance (1.5, 6, 10 mm).
  - `corner_cases() -> list[Case]`: 16 cases, `$SPIKE/combo.py`'s product of (axle ±0.5) × (lip ±1) × (5, 11 V) ×
    (0.5, 2 m/s), with yaw (−15, 0, 15)[k % 3].
  - `random_cases(n: int = 40, seed: int = 7) -> list[Case]`: combo.py's draws with `random.Random(seed)`, in its
    order: axle, lip, volts, speed, yaw, offset ±4 in.
  - `variant(case: Case, into: Path, example: Path = EXAMPLE) -> Path`: copies the example folder into `into`, then
    applies K6-P2's moves with tomlkit, keeping comments.
  - `run_case(case: Case, example: Path = EXAMPLE) -> dict`:
    - keys `grid`, `label`, `x`, `passed`, `status`, `outcome`, `vmax` (m/s, the fastest `Ring.speeds` of any
      in-use ring at any step) and `wall`;
    - the test's spin volts, drive volts (speed / SPEED_PER_V) and drive seconds
      ((GAP_IN·IN + TRAVEL)/speed + 0.4) are set per case;
    - the NOTE is placed as the spike's harness did (`spread_ball`'s lie, distance 0.43 + REACH + GAP_IN·IN +
      0.1778 m);
    - the world is reused while the variant folder is the same.
  - `run_grid(name: str, example: Path = EXAMPLE, out: Path | None = None) -> list[dict]`: `name` is one of `oat`,
    `corners`, `random`. With `out`, one JSON line per case.
  - `table(rows: list[dict]) -> list[str]`: one line per case, then the count.
  - `meets_target(oat: list[dict], corners: list[dict]) -> bool`: every OAT case but the control passes, the
    control fails, and at least 14 of 16 corners pass.
  - CLI: `python tests/intake_grid.py {oat,corners,random,all} [--out DIR]`.

- [ ] **Step 1: write the failing test** `test_segment_speeds_are_the_rings_own` in `tests/test_range_soft.py`:
  - build the preset ring alone (as `test_the_ring_is_built_as_its_kind` does);
  - `soft.write_rigid(m, d, "n", c, q, vel=[1.0, 0, 0], ang_vel=[0, 0, 10.0])`, then `mj_forward`;
  - assert `ring.speeds(d)` equals ‖v + ω × (com_j − c)‖ for each segment within 1e-9, with com_j and c from
    `ring._segments` at `d.qpos`.

  Run it and see it fail with AttributeError.
- [ ] **Step 2: implement `Ring.speeds`.** Run Step 1: PASS.
- [ ] **Step 3: write the example's three files** (K6-P2).
  - The part file's header comment keeps the spike's "simulator rules" for now. Task 9 updates it.
  - Run `scrimmage test run examples/blocks/note-intake/intake.test.toml`. It passes, on today's NOTE, as the
    spike's nominal did.
- [ ] **Step 4: write the failing grid tests** in `tests/test_examples_note_intake.py` (it does
  `sys.path.insert(0, str(Path(__file__).parent))`, then `import intake_grid`):
  - `test_the_grids_are_the_spikes`:
    - the lengths are 45, 16 and 40;
    - `corner_cases()[0]` has axle −0.5, lip −1, 5 V, 0.5 m/s and −15°;
    - `[c.x for c in random_cases()][0] == [-0.176, -0.698, 8.91, 0.61, 1.1, -1.1]`;
    - every case's settings equal `$SPIKE/k6/{oat,corner,random}_cases.json`, read once by hand and pasted in as
      the first and last case of each list.
  - `test_a_variant_moves_the_settings_bodies`, through `tomllib` on the variant's files:
    - lip +1 moves `lip` x by +0.0254;
    - axle −0.5 moves `roller` and `top0` z by −0.0127;
    - 10 mm clearance raises the mount z by 0.007 and lowers `roller` z by 0.007, and moves `roller` x by K6-P2's
      formula (+2.03 mm, within 0.01 mm);
    - yaw 15 makes the mount's `rot` [0, 0, 15];
    - nothing else changes.
  - `test_the_example_passes_as_saved`: `TestRun(world_for(load(TEST)), load(TEST)).run().passed`.

  Run them and see them fail with ImportError.
- [ ] **Step 5: implement `tests/intake_grid.py`.** Run Step 4: PASS. Then run
  `tests/test_examples_blocks_open_clean.py`: the new test file opens with no problems and no hints.
- [ ] **Step 6: check the files against the spike, fix off.** Write a scratch wrapper `$SCRATCH/k6grid.py`, not
  committed:
  - it puts `$SPIKE/k6` on `sys.path` and does `import patch`, which reads K6_WIDTH, K6_RAMP and K6_PAIRS;
  - then it runs `runpy.run_path("tests/intake_grid.py", run_name="__main__")`.

  Run it in the background with `K6_RAMP=0 K6_PAIRS=0` on `corners`, and wait on its PID. Expected: 14 of 16, with
  `[-0.5, +1, 11 V, 0.5 m/s, −15°]` and `[-0.5, +1, 11 V, 2.0 m/s, 0°]` failing (the spike's `k6/off_corners.jsonl`).
  If it differs, the files are not the spike's design: compare each body's pose with `winner/` at the failing
  corner and fix the files before going on.
- [ ] **Step 7: run the three grids with the fix at 1.0 r.** Run `K6_WIDTH=1.0 ... all --out $SCRATCH/k6grid-w10/`,
  in the background (about 8 minutes), waiting on its PID.
  - If `meets_target`: the example ships on this geometry.
  - Otherwise, take the retune ladder (K6-R10 step 2, values in K6-P3): edit the example's `part.toml` for (a), run
    all three grids, and so on through (b) and (c).
  - The first candidate that meets the target is the example's geometry. If none does, keep the best one. Write the
    shortfall into the record as ruling K6-P13, with the tight-nip cause. Don't loosen the NOTE.
  - Rerun Step 4's variant test against the final file.
- [ ] **Step 8: fill in "Task 1's record"** below: for each grid that ran, the count, the failing cases, the fastest
  segment, and the geometry that ships.
- [ ] **Step 9:** run the full suite and commit: `K6: the intake example's files and its grids at the 1.0 r ramp`.

### Task 2: the acceptance scenes, written first (K6-R8, K6-R11)

**Files:**
- Modify: `tests/soft_helpers.py`. Port `$SPIKE/k6/scenes.py`'s slam, press, slab and taco, with `Ring.speeds` for
  segment speeds. `squeeze` gains `gap`.
- Create: `tests/test_range_soft_robust.py`.

**Interfaces:**
- Consumes: `Ring.speeds` (Task 1).
- Produces, in `soft_helpers`:
  - `PLATE_FRICTION = [0.4, 0.005, 0.0001]  # source: k6 scenes: polycarbonate on foam (docs/parts.md)`.
  - `squeeze(kind, gap: float = sc.SQUEEZE_GAP) -> float`.
  - `energy(m, d, ring="n") -> float`: kinetic (translation and spin, from `_segments`) plus Σ m g z.
  - `slam(kind, v, thickness, priority=0, pairs=False) -> dict`: `deepest` (m, contact depth on the wall), `through`,
    `vmax`, `finite` and `energy_gain` (max energy / start). The scene is 600 steps.
  - `press(kind, force, thickness, slide=0.0) -> dict`: `squash` (m, the lowest capsule's bottom below the plate's
    top), `center_below_top`, `vmax` and `finite`.
  - `taco(kind, gap, lift) -> dict`: `self_overlap` (m), `vmax` and `finite`.
  - `slab(kind, pull, priority=0, pairs=False, v=2.0, gap=0.002) -> dict`: `under_max`, `under_end`, `vmax` and
    `finite`.
  - With `pairs=True`, the wall or slab is on its own body (`wallb`, `slabb`, as `$SPIKE/k6/scenes.py`'s
    `slam_pair` and `slab_pair` are), since excluding the world body would also drop the carpet. The scene then
    calls `soft.pair_rings(spec, ["n"], [spec.body("wallb")])` (Task 5).
  - `drop` gains `energy_gain`, the same measure as slam's.
  - K6-R3, R4 and R5 change no code: self-contacts stay, with no radius or margin change, no speculative contacts and
    no flex in the world. The taco test pins R3.

- [ ] **Step 1: port the scenes.**
- [ ] **Step 2: write `tests/test_range_soft_robust.py`**, parametrized over `STIFFNESSES = (1000.0, 2000.0,
  20000.0)` (K6-R11). Every test asserts `finite` and `vmax < 20.0`
  (`# choice: under the guard's 25 m/s; K6-P5`).
  - `test_a_thin_plate_holds_a_roller_load[T, F, slide, s]`:
    - T is 0.003175 or 0.006, F is 25, 50, 100 or 200, slide is 0.0 or 3.4;
    - assert `not center_below_top` and `squash < 0.020`.
  - `test_a_hard_hit_never_goes_through[T, v, priority, s]`:
    - T is 0.003175, 0.006 or 0.05, v is 2, 4, 6 or 8, priority is 0 or 1 (`pairs=True` at 1);
    - assert `not through`, `deepest < 0.024` and `energy_gain <= 1.001` (K6-P4).
  - `test_a_folded_ring_doesnt_pass_through_itself[gap, lift, s]`:
    - gap is 0.12, 0.10, 0.08 or 0.06, lift is 0.3 or 1.0;
    - assert `self_overlap < 0.020`.
  - `test_a_2mm_slab_doesnt_run_the_note_over[priority, pull, s]`:
    - priority is 0 or 1 (`pairs=True` at 1), pull is 0, 5, 10 or 20;
    - assert `under_max <= 2` and `under_end == 0`.
  - `test_the_squeeze_stiffens_and_keeps_its_calibration[s]`:
    - s is 1000, 2000, 6000 or 20000; gaps are 1.5, 1.0, 0.6 and 0.4 in;
    - assert every force is finite and strictly rising, and the 1.5 in force is within 2 % of s × 0.00635.
- [ ] **Step 3: run it on today's ring.** Record every failing case's id in
  `FIXED_BY: dict[str, str] = {case id: "K6 Task 3" | "K6 Task 5"}`. Build each parametrization as
  `pytest.param(..., id=..., marks=pytest.mark.xfail(strict=True, reason=FIXED_BY[id]) if id in FIXED_BY else ())`.
  - Task 5 owns priority 1 with `pairs=True` (the call fails until `pair_rings` exists).
  - Task 3 owns any other failure.
  - Expected failures: thin-plate slams at 6 and 8 m/s; press at 100 and 200 N; the squeeze's "rising" check at
    0.4 in, if it plateaus. Expected to pass: the 1.5 in calibration point.
  - A failure no ruling owns (for example a case that is non-finite at 20000 N/m with today's ring) is reported, not
    xfailed.
- [ ] **Step 4:** run the file (about 25 s): every test passes or xfails. Run the full suite, and commit:
  `K6: the soft NOTE's acceptance scenes, written first`.

### Task 3: the ring's contact stiffens past about 60 % of its squash (K6-R1)

**Files:**
- Modify: `scrimmage/testrange/soft.py`:
  - the constants (lines 47–62);
  - a new `ring_contact`;
  - `add_ring`'s `contact` (line 146);
  - the module docstring's squash paragraph (lines 12–13, 25–28).
- Modify: `tests/test_range_soft.py`: `test_the_ring_is_built_as_its_kind` (268–289), and new rule tests.
- Modify: `tests/test_range_soft_robust.py`: remove the "K6 Task 3" entries from `FIXED_BY`.

**Interfaces:**
- Produces:
  - `SOFT_DMIN = 0.2  # source: K5 spike (SOFT_SOLIMP's d): a static squeeze within 1.25x of an impact`;
  - `SOFT_DMAX = 0.95  # source: MuJoCo's default dmax (build.BALL_DMAX)`;
  - `RAMP_WIDTH = 1.0  # estimate: the ramp's width in tube radii; the foam densifies past about 60 % squeeze`;
  - `RAMP_MID, RAMP_POWER = 0.9, 6.0  # choice: flat to about half the width, hard by 0.9 of it`;
  - `W_RING = 700.0  # choice: rad/s, the stiffest contact's frequency per unit mass (stable at 700, not 1000)`;
  - `SOFT_SOLIMP` is removed;
  - `ring_contact(kind: PieceKind) -> tuple[list[float], list[float]]`: the (solref, solimp) of K6-R1's rule:
    - `dmax = min(SOFT_DMAX, SOFT_DMIN · W_RING / √contact_k)` and `f = dmax / SOFT_DMIN`;
    - solref `[−contact_k·f², −contact_b·f]`;
    - solimp `[SOFT_DMIN, dmax, RAMP_WIDTH·r, RAMP_MID, RAMP_POWER]`;
    - `RingParams` stays unscaled (K6-R9's first row).

- [ ] **Step 1: write the failing tests:**
  - `test_the_ramp_rule`:
    - at 2000, 6000 and 20000 N/m, solimp[1] is 0.95, 0.763 and 0.418 within 1e-3, and solimp[2] == 0.0254;
    - at 2000, solref == [−11211.4·22.5625, −p.contact_b·4.75] within 1e-6 relative;
    - at every stiffness, solref[0]·(SOFT_DMIN/dmax)² == −contact_k: a shallow contact is today's.
  - Update `test_the_ring_is_built_as_its_kind` to assert each capsule's solref and solimp equal `ring_contact(NOTE)`.
    Its docstring names the reason: K6-R1, per K6-R9.

  Run them and see them fail.
- [ ] **Step 2: implement.** Run Step 1: PASS. Remove the "K6 Task 3" `FIXED_BY` entries and run
  `tests/test_range_soft_robust.py`: every Task 3 case passes. Also add the spec's measured values at 2000 to
  `test_the_squeeze_stiffens...`: 0.6 in within 10 % of 133.5 N and 0.4 in within 10 % of 494.3 N
  (`# choice: pins the rule`).
- [ ] **Step 3: the fallback.** If any acceptance case at 1.0 r leaves its bound:
  - set `RAMP_WIDTH = 0.9` (`# estimate:`, as K6-R1's fallback);
  - rerun the acceptance file, and Task 1 Steps 7–8 with `K6_WIDTH=0.9` (the retune again);
  - record both in Task 1's record and in this task's report.

  If 0.9 r also fails a case, stop and report it.
- [ ] **Step 4: run `tests/test_range_soft.py`.** Only Task 4's tests may fail:
  - `test_the_shipped_damping_scales_are_the_fit`;
  - `test_the_rule_bounces_within_10_percent_across_the_range` corners;
  - `test_the_rule_reproduces_both_fitted_foams`'s drop.

  Mark each one that fails xfail strict "K6 Task 4". Any other failure: stop and report it. Run the full suite,
  and commit: `K6: the soft NOTE's contact stiffens past about 60 % of its squash`.

### Task 4: the damping refit and the goldens (K6-R6, K6-R9)

**Files:**
- Modify: `scrimmage/testrange/soft.py`: `DAMPING_SCALES` (59–60), and `damping_scale` only on K6-P9's path.
- Modify: `tests/fit_soft_damping.py`: only on K6-P9's path (a loop over restitution).
- Modify: `tests/test_range_soft.py`:
  - Task 3's xfail marks removed;
  - `test_the_ring_is_stable_at_both_ends_of_its_range` (191–197);
  - `test_the_damping_scale_table` (173–179) on K6-P9's path.
- Modify: `docs/test-range.md` "The soft NOTE" (line 558): the table's rows.

**Interfaces:**
- Produces: `DAMPING_SCALES`, refit, with the tag `# source: fitted offline by tests/fit_soft_damping.py,
  2026-11-12 (K6-R1's ramp)`. Or, on K6-P9's path, `damping_scale(stiffness: float, restitution: float) -> float`;
  `ring_params` then passes `kind.restitution`.

- [ ] **Step 1:** run `tests/fit_soft_damping.py` (about 11 s a row) and paste its four rows into `DAMPING_SCALES`.
  The 6000 and 20000 rows should move less than 0.5 % (K6-R6).
- [ ] **Step 2:** remove Task 3's xfail marks. Run `test_the_shipped_damping_scales_are_the_fit`,
  `test_the_rule_reproduces_both_fitted_foams` (FITTED stays: 12.70 N and 0.295; 38.10 N and 0.306, within 10 %)
  and `test_the_rule_bounces_within_10_percent_across_the_range`, all five points.
- [ ] **Step 3, only if a bounce point is still off by more than 10 %: take K6-P9's path.**
  - Make the table two-dimensional, and the fit script loop over K6-P9's restitutions.
  - Refit the twelve rows.
  - `test_the_damping_scale_table` checks the bilinear log interpolation at the middle of a cell, and the ends.
  - Rerun Step 2.
- [ ] **Step 4: tighten the stability test.** It asserts:
  - `r["deepest"] < 0.021` (`# choice: measured 19.3 mm at 1000 N/m; was 40 mm, more than the tube`);
  - `r["energy_gain"] <= 1.001` (K6-P4);
  - `settle_s < 1.0`, as now.

  Update its docstring.
- [ ] **Step 5: check K6-R9's goldens table row by row.**
  - Run `test_the_rule_scales_from_the_fit` (unchanged: 11211.4 and 64.45),
    `test_a_swap_back_waits_until_the_note_is_clear_of_a_wall` (overlap 0.008), the ledger, range ball, rangeless
    and bench flex goldens, and `tests/test_range_2024.py`.
  - Each must give the table's "Expected change".
  - A 2024 test that moves is listed in the report with its cause: a NOTE turning soft near the robot. Any other
    move: stop and report it.
- [ ] **Step 6:** update docs/test-range.md's damping rows. Run the full suite, and commit:
  `K6: the soft NOTE's damping refit for the ramp`.

### Task 5: a priority part meets the NOTE's squash (K6-R2)

**Files:**
- Modify: `scrimmage/testrange/soft.py`: a new `pair_rings` and `priority_bodies`.
- Modify: `scrimmage/parts/assemble.py`: between the partner and `spec.compile()` (lines 597–611).
- Modify: `scrimmage/flexcontact.py` `id_name` (47–54), per K6-P11.
- Modify: `tests/test_range_soft_robust.py`: remove the "K6 Task 5" `FIXED_BY` entries, and add the pair tests.

**Interfaces:**
- Consumes: `ring_contact` (Task 3).
- Produces:
  - `PAIR_SUFFIX = "#soft-pair-"`.
  - `priority_bodies(roots: list[mujoco.MjsBody]) -> list[mujoco.MjsBody]`: the bodies in each root's subtree,
    the root included, with a colliding geom (contype or conaffinity nonzero) of priority above 0.
  - `pair_rings(spec, rings: list[str], bodies: list[mujoco.MjsBody]) -> int`:
    - for each ring segment and each body, `spec.add_exclude(<ring>.s<j>, body)`;
    - for each colliding geom of the body and each capsule, `spec.add_pair` with the capsule's solref and solimp;
    - the pair's friction is `[μ0, μ0, μ1, μ2, μ2]` and its condim is the geom's own if the geom has priority,
      otherwise the elementwise max of the two (`$SPIKE/k6/patch.py`'s rule);
    - an unnamed geom is named `<body>#soft-pair-<k>` first;
    - returns the pairs added.
  - In `assemble`: when `layout is not None and layout.soft`, call
    `pair_rings(spec, layout.soft, priority_bodies([robot root spec body] + ([partner root] if partner else [])))`.
    The robot root is `spec.body(f"{root.name}.{root.part.bodies[0].name}")`; the partner root is
    `spec.body(partner.body)`.

- [ ] **Step 1: write the failing tests** in `tests/test_range_soft_robust.py`:
  - `test_the_pairs_cover_every_priority_body[config]`, on the speaker standin
    (`examples/blocks/speaker/note-shooter-standin.toml`) and `examples/blocks/note-intake/note-intake-standin.toml`:
    - with n_g the colliding geoms of priority bodies and n_b the priority bodies, both counted from the compiled
      model, and n_r = 16 × `len(layout.soft)`:
      - `m.npair == n_r × n_g`;
      - `m.nexclude` grows by n_r × n_b (against `pair_rings` patched to a no-op);
    - every `m.pair_solref` row equals its ring capsule's `geom_solref`;
    - on the intake, every pair's friction[0] is 0.4.
  - `test_a_partners_priority_geoms_are_paired` (Review Focus 3): the intake standin with a `[partner]` whose part
    has one priority geom (a tmp config). That geom appears in `m.pair_geom2`.
  - `test_no_priority_geom_no_pairs` (Review Focus 3): a standin with no priority geom has `m.npair == 0`, and its
    model's nbody, ngeom and nexclude equal the build with `pair_rings` patched out.
  - `test_a_paired_geom_keeps_its_contact_name`: `id_name` of a renamed geom is its body's name.
  - `test_pairs_cost_little_to_build` (`SCRIMMAGE_TIMING=1` only, K6-P6): the best of 3 intake builds with pairs is
    under 1.2× the best of 3 without.

  Run them and see them fail.
- [ ] **Step 2: implement.** Run Step 1, then the whole file with the Task 5 `FIXED_BY` entries removed: every
  priority-1 slam and slab case passes.
- [ ] **Step 3:** rerun `tests/test_range_2024.py` and `tests/test_range_soft.py`, and list any 2024 change with
  its cause, as in Task 4 Step 5. Run the full suite, and commit:
  `K6: a priority part meets the soft NOTE's squash through explicit pairs`.

### Task 6: the guard and the instability note (K6-R7)

**Files:**
- Modify: `scrimmage/testrange/soft.py`: the constants, and `SoftPool.reset` (455–467), `.notes` (478–492) and
  `.shadow` (494–515).
- Modify: `scrimmage/testrange/runtime.py`: `after_physics` (263–278), `reset` (~136), `soft_notes` (296–299), and a
  new `notes`.
- Modify: `scrimmage/blocks/run_notes.py:88` and `scrimmage/scenario_runner.py:707`, to `RangeRuntime.notes`.
- Modify: `scrimmage/blocks/run.py`: `RunResult` (180) gains `soft_resets: int = 0`, set where `result.pieces` is
  (517).
- Modify: `scrimmage/mcp_server.py:723`: `"soft_resets"`.
- Modify: `scrimmage/locales/en-GB.json`, only if a string differs.
- Test: `tests/test_range_soft_robust.py`.

**Interfaces:**
- Consumes: `Ring.speeds` (Task 1).
- Produces:
  - `GUARD_SPEED = 25.0  # choice: m/s; the spike's rollers never passed 7 m/s, a robot drives under 5, a launched
    NOTE is rigid`;
  - `GUARD_RESET_SPEED = 7.0  # choice: m/s, the fastest roller surface the spike saw`.
  - `SoftPool.resets: int`. `reset()` clears it and the blow-up notes.
  - In `shadow()`, each in-use, awake ring is checked after its read. It fires when a segment's speed exceeds
    GUARD_SPEED or any of its qpos or qvel rows is not finite. It then resets the ring per K6-P12, increments
    `resets`, and records `(slot name, t, speed or None)`.
  - Its notes are worded through units (speed as `piece_lines` words it):
    - `"soft NOTE {name} blew up at {t} s (a segment at {speed}); it was reset to its rest shape, so results near
      then aren't trustworthy"`;
    - for a non-finite ring, `"soft NOTE {name} blew up at {t} s (its state was not finite); it was reset to its
      rest shape, so results near then aren't trustworthy"`.

    `{name}` is `balls.piece_name(slot)`.
  - `RangeRuntime.unstable: list[float]`: each step-start time at which `data.warning[mjWARN_BADQACC].number` rose.
    The baseline is set at construction and in `reset()`. The note is
    `"MuJoCo found the simulation unstable at {t} s and restarted it"`.
  - `RangeRuntime.notes(lang=None) -> list[str]`: `soft_notes(lang)` plus the instability notes.

- [ ] **Step 1: write the failing tests:**
  - `test_the_guard_resets_a_blown_up_ring`: on `wing_world` (from `test_range_soft`; import it bare), get a ring in
    use with `_soft_now`. Set its free joint's linear qvel (`d.qvel[ring.free_v:ring.free_v + 3]`) to
    [100, 0, 0] m/s and step once. Then:
    - `pool.resets == 1`;
    - every `ring.speeds` ≤ 7.0 + 1e-6;
    - the ring's shape is its rest shape (the segment offsets about the center equal `ring.rest` turned by its
      rotation, within 1e-6);
    - `w.test_range.notes()` has the "blew up" note once.
  - A `TestRun` on the same world with the kick done in `on_sample` gives `result.soft_resets == 1`, and its
    `result.notes` has the note.
  - `test_a_non_finite_ring_is_reset_where_its_stand_in_was`: a NaN in one qpos row. The ring is reset to the slot's
    last shadow pose, at rest, and the non-finite note appears.
  - `test_a_reset_clears_the_guards_note_and_count` (Review Focus 1): after the guard fires, `World.reset`. Then
    `resets == 0`, no "blew up" note, and a quiet second run gives `soft_resets == 0`.
  - `test_parked_and_sleeping_rings_never_trip_the_guard` (Review Focus 2):
    - the wing world idles 2 s with no NOTE near: `resets == 0`;
    - with a parked ring's qvel set to 100 m/s, still 0 (parked rings aren't checked);
    - with an in-use ring asleep (`d.tree_asleep[ring.tree] >= 0`), still 0.
  - `test_a_badqacc_rise_is_noted`: set `data.warning[mjWARN_BADQACC].number += 1` by hand after a step at known t,
    then call `after_physics`. `notes()` has "MuJoCo found the simulation unstable at {t} s" once.

  Run them and see them fail.
- [ ] **Step 2: implement.** Run Step 1: PASS. Run `scrimmage i18n check en-GB`: exit 0.
- [ ] **Step 3: assert the guard never fires** in the existing soft world tests that take a `TestRun` or scenario
  result (`test_a_scenario_report_notes_the_soft_note` and the swap tests that run): `soft_resets == 0`, or
  `pool.resets == 0` where they step a `World` by hand.
- [ ] **Step 4:** run the full suite and commit: `K6: a guard resets a blown-up soft NOTE and says so`.

### Task 7: the hopper from `piece_in`, and the piece table's outcome (K6-R12, K6-R13)

**Files:**
- Modify: `scrimmage/blocks/conditions.py:138`: `self.hopper`.
- Modify: `scrimmage/blocks/run.py:498`: `hopper=bool(watch.hopper)`.
- Modify: `scrimmage/blocks/feeds.py`: `_outcome` (202–231) and `final` (250–260) give the body for "in" too.
- Modify: `scrimmage/blocks/conditions.py`: a new `Watch.hopper_body(ball: int) -> str | None`, the first hopper body
  whose box (`_box`) holds the piece's center now.
- Modify: `scrimmage/blocks/run.py:517`: join the feed rows onto `result.pieces`.
- Modify: `scrimmage/blocks/run_notes.py` `piece_lines` (184–202).
- Modify: `docs/test-tab.md`: which table is which (K6-R13's cost).
- Test: a new `tests/test_blocks_note_outcomes.py`.

**Interfaces:**
- Produces:
  - `Watch.hopper`:
    - `list(program.hopper)` when the file names one, `[]` included;
    - else the `body` of each `piece_in` condition (`_conds(program.blocks, "piece_in")`), in order, without
      duplicates.
  - A feed row's `body`:
    - for "in", the holder (held) or `hopper_body` (box), else None (the ball path took it);
    - for "stuck", as today.
  - A `result.pieces` row gains `feed: {"outcome": str, "body": str | None, "why": str} | None`, matched on
    `row["ball"] == piece["id"]`.
  - `piece_lines`, for a row with `feed`:
    - the outcome column is `feed["outcome"]`;
    - the `where` column is `"in {body}"`, or `"in the robot"` when body is None, or `"stuck on {body}"`, or
      `"placed on {why}"`;
    - a missed piece keeps `_ball_result`'s words ("rest at (x, y)").

    All of these go through `i18n.t`. Rows without `feed` are unchanged.

- [ ] **Step 1: write the failing tests** on a tmp copy of `examples/blocks/note-intake/`:
  - `test_a_piece_in_body_is_the_hopper`: with the `hopper` line removed, the run passes, `result.outcome` contains
    "1 of 1 made it in", and its piece row's outcome is `in` with where `in intake.hopper`.
  - `test_no_hopper_and_no_piece_in_is_unchanged` (Review Focus 4): the same copy, with the `wait_until` and
    `expect` blocks replaced by a 4 s wait. The outcome says "stuck in the robot", as today.
  - `test_a_cleared_hopper_stays_none` (Review Focus 4): `hopper = []` with `piece_in` kept. `watch.hopper == []`,
    and the sentence has no "made it in".
  - `test_the_piece_table_says_in_for_a_note_in_the_bin`: the saved example's `piece_lines(result)` has `in` and
    `in intake.hopper` on the NOTE's line, not `on_field`.
  - `test_the_scenario_ledger_table_is_unchanged`: `scenario_runner._ball_result` for that piece still words it from
    the ledger (`on_field`).

  Run them and see them fail.
- [ ] **Step 2: implement.** Run Step 1: PASS. Then run `tests/test_blocks_*.py` and `tests/test_feeds*.py`: every
  test that names a `hopper` is unchanged.
- [ ] **Step 3:** add the docs/test-tab.md lines. Run the full suite, and commit:
  `K6: a test's piece_in bodies are its hopper; the piece table shows the feed outcome`.

### Task 8: a bench plate can move its surface (K6-R14)

**Files:**
- Modify: `scrimmage/bench/spec.py`: `Plate` (115–120) gains `surface_speed: float = 0.0`, and the plate key list
  (262) gains `surface_speed`.
- Modify: `scrimmage/bench/spec_shapes.py` `_parse_plate` (82–85).
- Modify: `scrimmage/bench/build.py` `_add_static` (266–270): the plate geom's `surfacevel = [surface_speed, 0, 0]`
  (its own frame, K6-P10).
- Modify: `docs/bench.md` `[[plate]]` (526): the key, in its table.
- Test: `tests/test_bench_surface_speed.py` (new).

**Interfaces:**
- Produces:
  - `Plate.surface_speed` (m/s, signed, along the plate's own x), parsed as a number like the `[[inlet]]` velocity's
    components.
  - With `flex = true`, a nonzero `surface_speed` is refused, but only if Step 2 finds that a flex contact ignores
    `surfacevel`. The refusal is a `BenchError` naming the plate:
    `"[[plate]] '{name}': surface_speed doesn't move a flex NOTE (MuJoCo ignores a surface velocity on a flex
    contact); feed it with an [[inlet]] velocity"`.

- [ ] **Step 1: write the failing tests:**
  - `test_a_moving_plate_carries_a_rigid_note`: a bench with one plate (`surface_speed = 1.5`) and the rigid NOTE
    dropped on it. Its x speed reaches 1.5 ± 0.1 m/s within 1 s (spec: under 1 s).
  - `test_a_negative_surface_speed_carries_back` (Review Focus 5): −1.5 gives −1.5 ± 0.1.
  - `test_a_turned_plate_carries_along_its_face` (Review Focus 5): a plate with `rot_deg = [0, -10, 0]`. The piece's
    velocity is along the plate's x within 3°.
  - `test_surface_speed_with_a_flex_note`: the same plate with `flex = true`. Either the flex NOTE reaches 1.5 ± 0.15
    m/s within 1.5 s, or the refusal is raised. Step 2 decides which branch the test keeps.

  Run them and see them fail.
- [ ] **Step 2: implement the key, then measure the flex case.** If the flex NOTE moves, keep that branch. If it
  doesn't, add the refusal and keep that branch. Record which one in the report.
- [ ] **Step 3:** run `tests/test_bench*.py`: every bench golden passes, including `bench_flex_golden.json`. Write the
  docs/bench.md row. Run the full suite, and commit: `K6: a bench plate can move its surface (surface_speed)`.

### Task 9: the intake example, its grids in CI, the bench file and the docs (K6-R10)

**Files:**
- Modify: `examples/blocks/note-intake/parts/intake/part.toml`: the "simulator rules" comment. K6-R1 and R2 fix the
  thin-part rule; the thick parts stay as good practice.
- Create: `examples/blocks/note-intake/README.md`, in the chain-climb example's shape: the robot, the field, the
  question, the blocks, the files, what the grids found (Step 3's tables), and the design rules (thick parts the
  NOTE is pushed into, a ceiling, an over-under nip whose front roller is on a sprung arm: Task 1's record says why
  the arm ships and what the grids gave without it).
- Modify: `tests/test_examples_note_intake.py`.
- Modify: `tests/intake_grid.py`: rows gain `soft_resets`.
- Modify: `.github/workflows/ci.yml`: a new `intake-grid` job.
- Create: `examples/bench/note_intake_flex.toml`, from `$SPIKE/winner/bench/nip_flex.toml`.
- Modify, the docs:
  - docs/test-tab.md: the example, beside `chain-climb`;
  - docs/test-range.md "The soft NOTE" (558): the ramp, the pairs, the guard, the new limits, and the cost numbers
    rerun;
  - docs/parts.md's `priority` row (318): a soft NOTE's squash still applies;
  - docs/measuring.md: optional deep-squeeze readings at 0.6 in and 0.4 in gaps (owner question 1);
  - `docs/superpowers/specs/2026-11-08-kickoff-dry-run-findings.md`: a "**K6 status (2026-11-12).**" under gap 3.

**Interfaces:**
- Consumes: everything above. Specifically `run_grid`, `meets_target` and `table` (Task 1), `soft_resets`
  (Task 6), the feed outcome (Task 7) and `surface_speed` (Task 8).

- [ ] **Step 1: write the example's tests** in `tests/test_examples_note_intake.py`:
  - `test_the_example_passes_as_saved` (extended):
    - passed;
    - `"1 of 1 made it in"` in `result.outcome`;
    - the NOTE's `piece_lines` row says `in`;
    - `result.soft_resets == 0`.
  - `test_the_example_fails_at_0_volts`: the spin block's volts set to 0 (`loads` of the edited text). It fails, and
    the NOTE is not in `intake.hopper` (`watch.hopper_mask()` false at the end).
  - `test_the_example_runs_in_under_10_s` (`SCRIMMAGE_TIMING=1` only, K6-P6): the CLI run's wall time is under 10 s
    (`# choice; the spike measured 4.1 s`).
  - `test_the_intake_grids` (`SCRIMMAGE_INTAKE_GRID=1` only): runs the three grids. It asserts
    `meets_target(oat, corners)`, that every row has `soft_resets == 0`, and that the counts equal Task 1's record
    (or Task 3's 0.9 r rerun).
- [ ] **Step 2: run the grids on the shipped code** (`python tests/intake_grid.py all --out $SCRATCH/k6grid-final/`,
  in the background, waiting on its PID).
  - They should match Task 1's record case for case, since the patch and the shipped rule are the same rule. A case
    that differs is reported with its row.
  - `test_the_intake_grids` asserts the shipped numbers.
- [ ] **Step 3: write the README**, with Step 2's tables in the spec's format: the grid, the count, the failing
  cases, and the fastest segment. Update the part's "simulator rules" comment. The `arm` body's comments (the
  hinge, the stop, the spring's preload) stay as Task 1 wrote them.
- [ ] **Step 4: add the CI job.**
  - `intake-grid` runs on ubuntu-latest with Python 3.13.
  - It installs `pip install -e ".[dev]"` and OSMesa, as the `test` job does.
  - It sets env `SCRIMMAGE_INTAKE_GRID: "1"` and `MUJOCO_GL: osmesa`.
  - It runs `pytest -v tests/test_examples_note_intake.py::test_the_intake_grids`.
  - It runs on every event the workflow runs on, pull requests included (K6-R10; CI is unmetered).
  - It has no timeout assertion.
- [ ] **Step 5: write the bench file.**
  - `examples/bench/note_intake_flex.toml` is nip_flex.toml with:
    - the `carpet` plate given `surface_speed = 1.0` (`# choice: the example's approach speed`);
    - the inlet's `velocity` set to `[1.0, 0.0, 0.0]`, so the NOTE starts at the carpet's speed;
    - an `idea` line naming the example.
  - If Task 8 refused `surface_speed` with `flex = true`, the file keeps the inlet's velocity at 1.5 m/s, and its
    comment says why.
  - Its nip stays nip_flex.toml's fixed front roller. The example's sprung arm (Task 1) isn't modeled on the bench,
    and the file's comment says so: the bench shows the nip at its nominal gap, not how the arm opens.
  - Add `test_the_bench_flex_intake_passes`: `run_bench(load_bench(path))` reports 1 of 1 through `bin`.
- [ ] **Step 6: write the docs:**
  - docs/test-range.md: the ramp (K6-R1's tables), the pairs and their cost, the guard and its notes, the new
    limits (the acceptance bounds), and the cost numbers.
    - Rerun `SCRIMMAGE_TIMING=1 ... -s tests/test_range_soft.py::test_what_a_soft_note_costs` for the cost numbers.
  - docs/parts.md: the priority row.
  - docs/measuring.md: the 0.6 in and 0.4 in squeeze readings, as optional.
  - docs/test-tab.md: the example.
  - The findings' K6 status: what works (the three causes fixed, the example and its grids, the guard) and what
    doesn't (the foam's deep squeeze is an estimate, owner question 1; K6-R16 queued).
- [ ] **Step 7:** run the full suite, and commit:
  `K6: the note-intake example, its grids in CI, the bench intake and the docs`.

---

## Task 1's record (filled in by Task 1)

Run with the spike's in-process patch (`$SPIKE/k6/patch.py`) through `tests/intake_grid.py` on the example's files.
"Fastest segment" is the fastest `Ring.speeds` of any in-use ring at any step of any case in the grid.

- **The lip setting is the spike generator's, not a lip body (narrows K6-P2).** With K6-P2's `lip` body, the fix-off
  corners came out 15 of 16 (`[-0.5, +1, 11 V, 0.5 m/s, -15°]` passed). The rollers matched `designs.nip` exactly; the
  bin did not. The lip block overlapped the main floor slab by 3 cm at nominal and 5.6 cm at lip -1 in, and a NOTE on
  two overlapping boxes gets two contacts, right under the nip. A rigid lip body can't avoid that (at +1 in it leaves
  an overlap or a hole). So the part file is `$SPIKE/winner/parts/intake/part.toml` verbatim (no `lip` body), and the
  lip setting moves `bottom` forward and keeps the floor's, walls' and lid's front ends and the ceiling's back end at
  the moved front, as `designs.nip` did. At all 72 axle and lip settings the variant's geoms match `designs.nip`'s to
  under 1 micron. The clearance setting stays K6-P2's mount raise, with the front roller's 3.4 in spacing read from
  the saved file.
- Fix off, corners, on the example's files: 14 of 16, failing: `[-0.5, +1, 11 V, 0.5 m/s, -15°]` and
  `[-0.5, +1, 11 V, 2.0 m/s, 0°]` (the spike's: 14, the two 11 V corners). Every case's pass and fastest segment match
  `$SPIKE/k6/off_corners.jsonl` to its rounding (16.33 against 16.3 m/s).
- Fix on at 1.0 r, the nominal geometry (misses the target: 13 of 16 corners):

  | Grid | Passed | Failing cases | Fastest segment (m/s) |
  |---|---|---|---|
  | One setting at a time (44 + the 0 V control) | 44 of 45 | the 0 V control only | 5.0 |
  | 16 corners | 13 of 16 | `[-0.5, -1, 5, 2.0, 0]`, `[-0.5, +1, 5, 0.5, 0]`, `[-0.5, +1, 5, 2.0, +15]` | 16.3 (at `[-0.5, +1, 11, 0.5, -15]`, which passed) |
  | 40 random draws (seed 7) | 38 of 40 | `[-0.399, -0.273, 5.15, 1.81, 3.4, -2.8]`, `[-0.303, -0.591, 8.74, 1.85, 10.2, -0.2]` | 5.8 |

- Retune candidates run (K6-P3), each from the nominal file. All five were run at once, so (c) and its stiffness
  steps were measured even though (b) met the target first:

  | Candidate | One setting at a time | 16 corners | 40 random draws | Fastest segment (m/s) | Target |
  |---|---|---|---|---|---|
  | (a) 1.6 in nip | 43 of 45: the control and offset -6 in | 15 of 16: `[-0.5, -1, 5, 2.0, 0]` | 35 of 40 | 4.0 / 6.0 / 9.8 | no |
  | (b) front roller 0.4 in higher | 44 of 45: the control only | 14 of 16: `[-0.5, +1, 5, 0.5, 0]`, `[-0.5, +1, 5, 2.0, +15]` | 40 of 40 | 3.2 / 6.0 / 4.4 | **yes** |
  | (c) sprung arm, 14.0 N·m/rad | 44 of 45: the control only | 16 of 16 | 39 of 40: `[-0.399, -0.273, 5.15, 1.81, 3.4, -2.8]` | 4.5 / 5.3 / 5.2 | yes |
  | (c) at 7.0 | 44 of 45: the control only | 16 of 16 | 39 of 40: `[-0.303, -0.591, 8.74, 1.85, 10.2, -0.2]` | 3.4 / 6.9 / 4.7 | yes |
  | (c) at 28.0 | 44 of 45: the control only | 16 of 16 | 38 of 40 | 4.5 / 4.8 / 16.1 | yes |

  The fastest-segment column is OAT / corners / random.
- The geometry that ships: **(c), the sprung arm at 14.0 N·m/rad**, by the coordinator's ruling in Task 1's
  review. Ladder order (K6-R10 step 3) would have shipped (b), the first candidate to meet the target. But (b) sits
  exactly on the 14 of 16 corner target, and these cases flip near the margin (the bin's geom split alone flipped a
  corner, above). Task 9's rerun on the shipped code could then miss the target by chance. (c) passes all 16 corners
  and holds from 7 to 28 N·m/rad.
  - The part: an `arm` body (parent `frame`, at `[0.505671, 0, 0.09088]`, the roller's center + (−0.0762, 0, +0.035)
    m, mass 0.1), `joint = { name = "arm", hinge = [0, -1, 0], range = [0, 25], spring = { stiffness = 14.0,
    rest = -5 } }`, a drawn-only box. `roller` hangs under it at `[0.0762, 0, -0.035]`, which is the spike's place
    `[0.581871, 0, 0.05588]` while the arm is on its stop. Everything else is the spike's winner.
  - `tests/intake_grid.py` applies the settings to it unchanged. Axle and clearance edit `roller`'s pos in the arm's
    frame (at the stop that is the part's frame), and the clearance formula reads the roller's place through the arm.
  - The rerun on the shipped files at 1.0 r matches the (c) row above case for case (pass and fastest segment):

    | Grid | Passed | Failing cases | Fastest segment (m/s) |
    |---|---|---|---|
    | One setting at a time (44 + the 0 V control) | 44 of 45 | the 0 V control only | 4.5 |
    | 16 corners | 16 of 16 | none | 5.3 |
    | 40 random draws (seed 7) | 39 of 40 | `[-0.399, -0.273, 5.15, 1.81, 3.4, -2.8]` (missed: still moving at the end) | 5.2 |

  - Fix off (ramp and pairs off) on the shipped files: corners 16 of 16, fastest segment 5.3 m/s. No segment in any
    of these runs went over 10 m/s.
- K6-P13, written only on a shortfall: none (no shortfall).
