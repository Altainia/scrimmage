# A soft NOTE and a hanging CHAIN (K5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** on the built-in 2024 range a student can check a floor intake against a NOTE that bends and squashes, climb
a CHAIN that sags and score ONSTAGE, hang a partner on the same CHAIN and see the sag and the HARMONY points; on the
bench a NOTE shooter squeezes a flex NOTE whose exit speed comes from that squeeze.

**Architecture:**
- The CHAIN is a new `kind = "chain"` element (`testrange/chain.py`): a 1D flex of 40 edges with pinned ends, drawn by
  visual beads. A new `scrimmage/flexcontact.py` gives a flex's side of a contact an id past the geoms, so every
  contact reader in the world (the climb monitor, the Test tab's watch, the ledger) reads a chain as one hold.
- `[climb] rule = "off-carpet"` with `harmony` scores ONSTAGE and HARMONY. `[partner] hang` welds the partner under a
  chain vertex.
- The world's soft NOTE (`testrange/soft.py`) is a pooled ring of 16 capsules. Its numbers follow a rule scaled from
  the spike's fit. It is swapped in for a rigid NOTE near the robot and swapped back after.
- The bench's flex NOTE (`bench/flex_note.py`) is a tetrahedral torus on the `discrete` integrator, fitted to the
  piece's `stiffness` and `restitution` and cached. It is opt-in with `flex = true`.

**Tech Stack:** Python 3.11+, MuJoCo 4.0.0 (MjSpec, 1D and 3D flex, `discrete` + Newton), numpy, pytest with
Playwright browser tests, the Test tab's JS (test_setup.js, live3d.js).

**Spec:** docs/superpowers/specs/2026-11-11-soft-note-and-chain-design.md. Its evidence:
docs/superpowers/specs/2026-11-11-chain-spike.md and docs/superpowers/specs/2026-11-11-soft-note-spike.md. The
spike scripts are references only and may be gone:
`/tmp/claude-1000/-home-altainia-code-scrimmage/3537f469-7b90-4632-aa74-f3b52c0bc63a/scratchpad/k5-chain/` (chainlib.py,
k5patch.py, world/) and `.../k5-soft-note/` (notes.py, scenes.py, switch.py, calibrate.py). Every number this plan
needs from them is copied here.

## Global Constraints

These are the spec's "Rules every task follows", verbatim:

- Plain American English. Every user-facing string goes through i18n.t / i18n.tn, with en-GB entries where needed.
- Every new number is tagged `# source:`, `# estimate:` or `# choice:`.
- Run tests with `.venv/bin/python -m pytest`, never `uv run`. In a worktree, set PYTHONPATH to its absolute path.
- Test helpers are imported bare (`from cad_helpers import ...`), never as `from tests.x import ...`.
- Wait on a test run by its PID, never with `pgrep -f`.
- Never commit under `.superpowers/`.

And these for this branch:

- **Commands:** never `uv run`. From the worktree /home/altainia/code/scrimmage-k5 (branch `soft-note-chain`), run
  `PYTHONPATH=/home/altainia/code/scrimmage-k5 /home/altainia/code/scrimmage/.venv/bin/python -m pytest`. The full
  suite (`... -m pytest -q tests/`) takes about 40 minutes: run it once per task, at the end, in the background, and
  wait on its PID. Don't run `git checkout`, `git switch` or `git stash`. Don't push.
- **Goldens stay byte-for-byte:** the world ledger golden (tests/test_range_ledger_golden.py), the range goldens
  (tests/test_range_discs.py's range_ball_golden.npz, tests/test_range_model.py's rangeless_golden.npz,
  tests/test_launcher_inworld.py's launcher_golden) and the 2022 climb tests (tests/test_climb_physics.py,
  test_climb_run.py, test_climb_scenario.py, test_climb_signals.py, test_climb_elements.py, test_climber_part.py,
  test_blocks_climber.py). No range in them has a chain, a soft kind or a flex, so they must pass untouched.
- **The only 2024 tests allowed to change** are those the 2024 chains change (spec D1, D5):
  `tests/test_range_2024.py::test_the_range_has_the_2024_game` gains the HARMONY assertion (Task 9), and
  `::test_the_range_checks_clean` only as Task 1's fallback says. Every other test in tests/test_range_2024.py,
  tests/test_scenario_points.py and tests/test_range_game.py keeps its numbers: none pins a chain number today, and a
  NOTE turns soft only within 0.25 m of a robot. If one of them moves, stop and report it. Don't re-golden it.
- **MuJoCo stays `mujoco>=4.0,<5`** (spec D9). Task 8's golden flex shot catches a change in `discrete`.
- **No new work where nothing uses it:** no flex contact handling for a model with `nflex == 0` (`contact_ids` returns
  MuJoCo's own array), no soft pool on a range with no `soft` kind, and no flex on the bench unless `flex = true`.
- **Browser tests** wait with Playwright `expect()`, never with sleeps or polling loops.
- **Range file errors** go through `_Reader`'s `err`, naming the file and the table (`element 'chain_blue_center'`).
- **Commits:** one per task (two for a split task), message `K5: <what>`, with the trailer
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Done means:** the task's tests and the full suite pass. The report lists what changed, each decision with its
  cost, and the suite's summary line.

## Rulings made while planning

What the spec leaves open, decided here so each task can cite it. Each gives the ruling, why, and its cost.

- **R1, splits.** D8 is two tasks (5: the ring, its rule and the parked pool; 6: the swap). D9 + D10 is two tasks (7:
  the flex model and its fit; 8: the bench wiring and the pinch test). *Why:* each half has its own test cycle, and a
  reviewer could pass the ring's numbers and still reject the swap. *Cost:* two more gates.
- **R2, what "fixed" means for the thin bar (D2).** The stock climber with its default `hook_throat` (1.66 in + 6 mm)
  and the spike's robot and script climbs a 0.5 in rigid bar to "reached ONSTAGE" in `climb run`, and every 2022
  climb test passes unchanged. A fix that needs the student to resize the throat is not a fix: it goes to the
  fallback. *Why:* the spec's "a thin bar is a fair student input". *Cost:* none.
- **R3, the chain's rest shape (D1).** N = 40 equal links whose middle vertex (index 20) is the lowest, at `low`, with
  the ends at `top`, ±`span`/2 along the yaw. Link i's angle is atan(c (i − 19.5)); c is bisected on log scale in
  [1e-6, 1e3] until sag over width equals (top − low)/span; the link length is span over the width at unit length.
  No c in the bracket is the "no catenary" error. *Why:* the spike's `discrete_catenary`, inverted to take the span.
  It starts at rest, so there is no transient. *Cost:* none.
- **R4, chain keys.** `center`, `yaw_deg`, `span`, `top`, `low`, `diameter` (each length also `_in`) are required,
  and `level` is optional. `friction` is refused: the chain's contact has friction 0, so the hook's `hook_material`
  sets μ (MuJoCo takes the max at equal priority). Against the robot's bumpers the chain is frictionless, since its
  priority 1 wins over their 0. *Cost:* a chain slides frictionlessly on a bumper. The docs say so.
- **R5, flex contacts get ids past the geoms.** In a contact, a flex's side has geom −1, and every per-geom Python
  list in the world reads index −1 as the model's last geom (a ball). `flexcontact.contact_ids` puts
  `ngeom + flex id` there. Every world-side contact reader uses it, with its per-geom arrays one entry per flex
  longer. *Why:* the spike found a silent misread in the climb monitor, and balls.py, the terrain monitor, the Test
  tab's watch, feeds, placed and the terrain sweep have the same one. *Cost:* the readers are touched once. A model
  without a flex pays nothing.
- **R6, a chain as a hold.** Each chain is in `layout.bars` with no geom names, so palette, signals, `climb run`'s
  `hook_contact` and the "any bar"/"next bar" targets list it as they list bars. `layout.chains` holds its flex. A
  chain's swing axis is its end-to-end line through the mean of the hooks touching it (the spike's). With no hook, it
  runs through the lowest vertex. Its "top" for "next bar" is its lowest vertex's height plus its radius. Its boxes
  for station and AprilTag checks are its 40 edges at rest. *Cost:* none for bars.
- **R7, `climb.hold` numbering (D5).** The 1-based position among the range's holds (bars, switches and chains, in
  element order), 0 for none. *Cost:* none: adding a box doesn't renumber holds.
- **R8, HARMONY scoring (D5).**
  - `climb.points` stays the level's points.
  - `climb.harmony` = `harmony` × (robots on the robot's hold − 1) while the robot hangs, else 0.
  - The match adds an endgame item `("harmony", "endgame", robots − 1, points)` from the `climb.harmony` held at the
    end, as it does the level.
  - Under `off-carpet` with several leveled holds touched, the highest level scores (the spike's reading).
  - `harmony` is refused under any other rule.

  *Cost:* none.
- **R9, the hanging partner (D6).**
  - Chains only: a bar or a switch gives an error listing the chains.
  - `start` and `hang` are exclusive, and Setup's hanging partner drops `start`.
  - The partner's heading is the chain's `yaw_deg`, so its length runs along the chain (controller ruling, Task 3
    review I4). An optional `across` (m, signed, + to the yaw's left, default 0 `# choice`) offsets it sideways, at
    most half its width; Setup's is `across_in`.
  - Its top center sits `HANG_GAP = 0.02 m  # choice` below the nearest interior vertex, welded to that vertex's body
    with the qpos0 relative pose, so it never touches the chain. Since a vertex body has no rotation, the partner
    stays level (`partner.tilt` reads 0).
  - It counts on its hold while none of its geoms touches the ground.
  - `along` must leave the nearest vertex interior: |along| ≤ span/2 − one link.
  - `partner.height` is measured from its standing height: the lowest bottom corner it would have on its wheels.
  - `check_start` checks only the robot's start outline for a hanging partner, since it is off the floor. After
    compile, `check_hanging` measures it in 3D at qpos0 (`mj_geomDistance`) against every geom it touches and refuses
    an overlap deeper than 1 mm as a PartnerOverlap naming the element or obstacle (review I1).
  - The chain doesn't touch it (CHAIN_BITS (1, 0), HANGING_BITS (2, 6)): with contact on, the start transient pushed
    the chain out under the partner and never resolved. The weld's data is set explicitly (MjSpec's default anchor
    is (0, 1, 0)).
  - Measured (Task 3): one 60 kg partner takes up the chain's 0.22 m of slack and pulls it into a taut V with its
    apex at the partner's vertex. At along 0.9 the apex is at 0.84 m, the low point rises from 0.72 to about 0.97 m,
    and a robot hooking there hangs about 4 cm higher than it does alone. A climber sized for the unloaded chain
    can't get its hooks over it. Near the 2024 STAGE, a stock partner under the center chain reaches 3 mm into the
    core (across 0 is refused, across −0.05 clears), and from about 0.5 m along it overlaps a truss leg.

  *Cost:* it can't hang on a 2022 rung, it never swings, and the robot must hook at least one partner outline away
  (spike case 3 ghost: 0.5 m apart, here 0.9 m in the example).
- **R10, the soft ring's rule (D8).** Scaled from the spike's soft fit at `stiffness` S₀ = 2000 N/m:
  - contact stiffness k = 11211.4 · S/S₀ · M₀/M, with M₀ = 0.235 kg the fit's NOTE (task 5 review): MuJoCo's
    negative solref is per unit of effective mass, so without the mass factor a NOTE twice as heavy squeezes twice
    as hard;
  - joint stiffness kj = 0.10276 · S/S₀ N·m/rad;
  - joint damping bj = 2 · 0.3 · √(kj · I_joint), where I_joint is a segment's inertia about its joint (I_seg +
    m (ℓ/2)²);
  - contact damping b = DAMPING_SCALE · 2 ζ(e) √k, with ζ(e) = −ln e / √(π² + ln² e), the spec's closed form.

  DAMPING_SCALE = 0.85 (`# source: derived: the spike's two fits, b/(2 ζ(0.3) √k) = 0.849 soft and 0.860 firm`).
  *Why:* the spec's closed form alone gives ζ = 0.358 at e = 0.3, where both fits needed 0.304–0.308. MuJoCo's
  impedance d = 0.2 shifts the effective damping, and the scale is one number measured twice. *Cost:* at other e the
  scale is an extrapolation. The test at both fitted foams is the arbiter.

  **Task 5 fix round 2.** One scale for all stiffnesses missed the drop: 0.3 came back as 0.38 at 20000 N/m. So
  DAMPING_SCALE became `DAMPING_SCALES`, a shipped table. Each row is the scale that makes a 1 m drop at e = 0.3 come
  back at 0.3, and the table is read linearly in log(stiffness) and held at its ends:

  | stiffness, N/m | 1000 | 2000 | 6000 | 20000 |
  |---|---|---|---|---|
  | scale | 0.8241 | 0.8536 | 0.9110 | 1.0749 |

  The rows were fitted offline by tests/fit_soft_damping.py on 2026-11-11, and
  `test_the_shipped_damping_scales_are_the_fit` reruns that fit. Building the world never fits anything. R14 bounds
  e to where the drop lands within 10 %.
- **R11, the stand-in shadows its ring (D8).**
  - While a NOTE is soft, its rigid slot stays `field` with PARKED_BITS and feels no ledger forces. After every
    physics step it takes its ring's best-fit pose and velocity, so possession, goals, the hopper, the ball log and
    `ball_of_geom` (the ring's geoms map to the slot) all see one piece, with one name and one count.
  - The live view and replays draw the slot at PARK_Z while its ring is in use (`mover_poses` asks the range).
  - live3d.js hides any mover below `PARKED_BELOW = -10 m`, so parked rings and parked balls are not drawn.

  *Why:* the spec's "same name, and same counts" with no reader of the ledger changed. *Cost:* a few vector writes
  per soft NOTE per step. Physically the stand-in sits at the ring, but it touches nothing.
- **R12, which NOTEs may turn soft.** Only a slot that is in play (`field`), of a `soft` kind, and no longer on
  LAUNCHED_BITS (`Balls._full_bits_at[i]` is infinite). A NOTE held in the robot, or one just launched and still
  clearing its robot, stays rigid. The distance is from its center to each colliding robot geom's oriented box
  (`geom_aabb` in the geom's frame); it turns soft within SOFT_REACH, or predicted within it after the next step (the
  distance less its closing speed times the timestep: the Task 6 review's owner request). A slot that leaves play
  (picked up, scored, out) releases its ring at once.
  *Cost:* none. This keeps every 2024 shot as it is.
- **R13, the swap back lands on the carpet.** The stand-in takes the ring's CoM, its Kabsch rotation, its momentum
  and its spin from angular momentum. If its rigid shape at that pose would reach below the carpet, it is raised to
  rest on it, and the vertical velocity is clipped to ≥ 0. *Why:* a ring squashed on the carpet has its CoM up to
  ~14 mm low (spike drop squash), and a rigid ring placed there would be thrown up. A stand-in that would then
  overlap another colliding geom (a wall, an element, a piece) by more than 1 mm stays soft until it is clear
  (`mj_geomDistance`; Task 6 review). *Cost:* up to 14 mm of lift at the swap, and a NOTE pinned against a wall stays
  soft (and costs its ring) while it is pinned.
- **R14, soft key limits.** `soft = true` is for a `shape = "ring"` kind only. On a soft kind, `stiffness` must lie in
  `SOFT_STIFFNESS_RANGE = (1000.0, 20000.0)  # choice: half to 10x the preset, both ends tested stable at 1 ms`.
  `restitution` must lie in (0, 1). It is refused on a ball or disc kind, whose restitution is its preset's.
  Added in task 5's review:
  - On a soft kind, `SOFT_RESTITUTION_RANGE = (0.27, 0.45)  # choice` is where `restitution` is accurate within
    10 %. Fix round 3: any 0 < e < 1 is accepted, so a student can enter the bounce they measured. Outside the range,
    `range check` warns that the simulated bounce may be off by more.
    - This is where the rule's 1 m drop lands within 10 % of e across the whole range of 1000–20000 N/m, with R10's
      damping table. The worst case is −9.6 %, at 12000 N/m and 0.45.
    - It is narrower than the first round's (0.25, 0.55): 0.25 gives +12.6 % at 20000 N/m, and 0.55 gives −11 % at
      11000 N/m.
    - The weakest stretch is between table rows, near 11000 N/m. No fifth row was added.
    - The test samples the four corners and the middle.
  - The kind needs an `inner_diameter` of more than `SOFT_MIN_HOLE = 0.68` of its diameter. Below
    (1 − sin π/16)/(1 + sin π/16) = 0.674, a segment's capsule overlaps the one two along.
  - When the `soft` came from the preset, an out-of-range override says so, and says that `soft = false` keeps the
    piece rigid.

  *Cost:* a measured NOTE outside the stiffness range needs the range widened, with a test. A bounce under 0.27 or
  over 0.45 runs with a warning, and its simulated bounce may be off by more than 10 %.
- **R15, the bench flex.**
  - The fit runs at the bench's flex timestep, 0.5 ms: squeeze by a secant on log E within 2 %, drop by a log
    bisection of β, 7 steps.
  - It is cached in `SHAPED_CACHE / "flex-note" / <sha256 of (stiffness, restitution, mesh, timestep, 1)>.json`. The
    shipped `scrimmage/bench/flex_fits.json` is read first.
  - At most `FLEX_POOL_MAX = 3  # choice: a parked flex costs as much as a live one` pieces per bench.
  - The golden flex shot is pinned to 2 % (`# choice:` platforms differ in the last digits).

  *Cost:* a first fit for new numbers takes about a minute, and the docs say so.
- **R16, the climber's telescope carries its motors' rotor inertia (Task 1, spec D2; owner's ruling over the
  fallback).**
  - *Cause:* the extend motor's back-EMF damping (57 kN s/m for the stock two Krakens at 1600 rad/m) was implicit dof
    damping on stages of 0.6 and 0.8 kg with no armature (b dt / m = 61 and 26). The constraint solver sees it as an
    explicit force, so the hook's contact pushed too little. Landing, the hook sank 11.2 mm, through a 1/2 in bar.
    Hanging from any rigid bar, the robot bounced on its hooks for good. A 2022 rung's spring hid it.
  - *Fix:* armature count × ROTOR_MOI × (motor rad per m)² (`telescope_armature`, with `ROTOR_MOI` moved to
    motors.py and shared with the stock swerve), split equally over the moving stages: 512 kg on the stock climber,
    256 kg a stage. ROTOR_MOI is an estimate: no published Kraken rotor inertia.
  - *Cost:* the 2022 climbs move.
    - examples/climb/high.toml seats on HIGH 0.8 s instead of 0.5 s (the heavier telescope starts later, and its
      mid hooks caught MID on the back swing). HIGH is held at 8.18 s, was 7.77 s.
    - high_timed.toml was regenerated.
    - The let-down bounce test makes its bounce on purpose (a 50 ms jolt).
    - traversal.toml now ends "hit a bar" at step 21.
    - The first commit's fallback (a thin-bar range warning) is in history and was removed.

## Review Focus

1. **A robot touching a chain anywhere but a hook.** Driving under or into a STAGE, a bumper or tube touches the
   chain's flex. That must read as touching that chain: never as a ball touching the robot (`ball_of_geom`), the
   ground (terrain), or the model's last geom. Pinned in Task 2 by `test_a_bumper_on_the_chain_is_read_as_the_chain`
   and `test_no_world_module_reads_contact_geom_directly`.
2. **A held or just-launched NOTE turning soft.** Every 2024 shot leaves from inside the robot. A preloaded NOTE, or
   one on LAUNCHED_BITS, must stay rigid, and the speaker example must score with its old numbers. Pinned in Task 6
   by `test_a_held_or_launched_note_stays_rigid`.
3. **A NOTE that jumps, doubles or is lost at a swap.** A ring squashed on the carpet swaps back without a hop. A
   World.reset (every Test-tab run) mid-swap leaves no ring out and runs the same again. A third NOTE near the robot
   stays rigid and is noted once. Pinned in Task 6 by `test_the_swap_back_on_the_carpet_doesnt_hop`,
   `test_a_reset_mid_swap_parks_every_ring` and `test_a_full_pool_leaves_the_third_rigid_and_notes_it_once`.
4. **A student's measured stiffness that blows up the ring.** A NOTE measured at 50 000 N/m, or a `restitution` of 0
   or 1, is refused with the range in the message, not run until NaN. Both ends of the range drop and settle. Pinned
   in Task 5 by `test_soft_limits_are_checked` and `test_the_ring_is_stable_at_both_ends_of_its_range`.
5. **A hanging partner where the robot stands or climbs.** A partner hung over the robot's start overlaps it in plan,
   and the build would start with the two interpenetrating. It is refused with Setup's own words, and so is an
   `along` past the chain's end. Pinned in Task 3 by `test_a_hanging_partner_over_the_robot_is_refused` and
   `test_hang_errors`.

---

### Task 1: a thin rigid bar the stock climber can hold (spec D2)

A diagnosis. The cause isn't known: the spike saw a 0.5 in bar wedge "between crown1 and crown2", the robot pitch
1–4° at about 1.1 kN, and the hooks slide off. A gentler 4 V pull-up and HOOK_SOLREF 0.002 s failed the same way.

**Files:**
- Create the fixture `tests/data/thin_bar/` (it stays a bar fixture after Task 9):
  - `range.toml`:
    - `name = "K5 thin bar"`, `ball = "2026-fuel"`, `field = { length = 16.541, width = 8.211 }`;
    - `[climb] rule = "lowest-rung"`, `levels = { ONSTAGE = 3 }`;
    - `[start.chain_center] pose = [6.05, 4.1051, 180.0]`;
    - 2024.toml's blue STAGE as it is today: `stage_blue_core`, the three `stage_blue_leg*` obstacles, and the three
      `chain_blue_*` bars, verbatim.
  - `robot.toml`, the spike's `chain_robot.toml`:
    - `[model] timestep = 0.001`, `[battery]` 12.5 V / 0.02 Ω, `[lockstep] step = 0.005`;
    - `[range] file = "range.toml"`, `start = "chain_center"`, `floor_balls = false`;
    - `drive`: stock chassis, length 0.66, width 0.6, mass 55.5, frame_height 0.1;
    - `dt`: stock differential, `ids = { left = "PWM:0", right = "PWM:1" }`;
    - `c`: stock climber attached at `drive.top`, `pos = [-0.06, 0, 0]`, stages 2, base_length 0.35, travel 0.45,
      tube 0.0508, count 2, gearing 12, hook_count 2, hook_spacing 0.2, hook_depth 0.08, `latch = "none"`,
      `ids = { extend = "PWM:2" }`.
  - `climb.toml`, the spike's `chain.toml`, `goal = "ONSTAGE"`:
    1. drive `c.stage1` to 0.14 and wait `joint_at` 0.14 ± 0.003;
    2. drive `dt.left_2` and `dt.right_2` to 4.05 rad at `max_voltage = 1.5`, and wait 2.5 s;
    3. drive `c.stage1` to 0, waiting `hook_contact = "chain_blue_center"`, with a 5 s timeout;
    4. wait `joint_at` 0.0 ± 0.01, with a 5 s timeout;
    5. wait 5 s.
- Modify, if the cause is the hook: `scrimmage/parts/stock/climber.py`: `_hook_frame` (93-101), `hook_seat` (103-110),
  `_hook` (135-160), the constants (54-58). If it is the bar's contact: `testrange/build_elements.py` `_pipe`
  (259-271).
- Test: a new `tests/test_climb_thin_bar.py`.

**Interfaces:**
- Produces: the fixture folder `tests/data/thin_bar/`. Task 2 copies its robot and script for the chain fixture.

- [ ] **Step 1: write the failing test** `test_the_stock_climber_climbs_a_half_inch_bar`:
  `r = run_climb(THIN / "robot.toml", load_script(THIN / "climb.toml"))`, then:
  - `r.status == "reached ONSTAGE"` and `r.reached_goal`;
  - `r.peak_hook_load < 4000.0`;
  - no row's event contains "walked off" or "fell".

  Run it: it fails "not hanging" (the spike's result).
- [ ] **Step 2: find the cause.** Write a scratch trace (not committed), as the spike's `dbg_world.py` did. Every 5th
  step of `ClimbRun(world, script).run()` with `run.on_step`, print:
  - t, `c.stage1`, and the robot's pitch from `xmat`;
  - each hook-bar contact: geom names, normal force (`mj_contactForce`), `contact.pos` relative to the bar's axis,
    and `contact.frame[:3]` (the normal);
  - each hook geom's center relative to the bar.

  Compare a 1.66 in rung with the same robot and `hook_throat`: it holds (2022). Answer, with numbers:
  - which capsules carry the load and with what normals;
  - whether the bar's center leaves the seat sideways, through the mouth (toward the tip or the stem), or into the
    crown;
  - whether the load jumps when the contact moves from one capsule to the next at a crown vertex;
  - whether the penetration grows past HOOK_RADIUS (5 mm) or the bar's 6.35 mm radius.

  Record the answer in the test module's docstring.
- [ ] **Step 3: fix it where the cause is**, and keep the 2022 rungs' contacts unchanged. Prefer a change that a
  1.66 in bar never touches, such as one only past the seat's capsules. Re-run Step 1, then the 2022 climb tests
  (Global Constraints) and `tests/test_climber_part.py::test_throat_is_bar_plus_6mm_and_seat_is_inside_the_hook`.
  Add `test_the_fix_leaves_the_2022_seat_alone`: `hook_seat(p)` and the hook geoms' sizes and positions for the
  default params equal their values before the fix. Read them from the code before you change it and paste them in as
  literals.
- [ ] **Step 4, only if no fix keeps the 2022 tests: the fallback.**
  - Superseded: the owner chose the rotor-inertia fix (R16).
  - Drop Step 1's test.
  - `testrange/elements.py`: a bar with diameter < `THIN_BAR = 1.0 * IN  # source: spec D2` adds a range warning:
    "bar '{name}' is {diameter} across: a stock climber's hook slips off a bar under 1 in (K5); hang a chain
    ({chain_kind}) or make the bar at least 1 in". It goes through `i18n.t`, through the range's `warnings`, and
    appears in `range check`.
  - `test_a_thin_bar_is_warned_about`: the thin_bar range warns three times, the 2022 ranges not at all.
  - `tests/test_range_2024.py::test_the_range_checks_clean` expects the six chain warnings until Task 9 makes them
    chains.
  - Write the ruling into this plan's Rulings as R16, with the cause and the cost.
- [ ] **Step 5:** run the full suite and commit: `K5: a stock climber holds a 0.5 in bar` (or, on the fallback,
  `K5: thin bars are warned about`).

### Task 2: the chain element, its beads and the climb monitor reading it (spec D1, D3)

**Files:**
- Create:
  - `scrimmage/testrange/chain.py`;
  - `scrimmage/flexcontact.py`;
  - `tests/test_range_chain.py`;
  - `tests/data/chain/`: Task 1's `robot.toml` and `climb.toml` copied, and `range.toml`, Task 1's range with
    `chain_blue_center` as the chain of D1's example (the other two chains stay bars).
- Modify:
  - `scrimmage/testrange/elements.py`:
    - `Chain` in `_CLASSES` (371), `_LENGTHS` and `_OTHER` (342-367), and `Element` (339);
    - `_element` (586-623) drops `friction` for a chain;
    - `_extent` (704): (span, diameter); `_on_floor` (750): `[]`;
    - `parse_climb` (654-701): a chain's `level` like a bar's.
  - `scrimmage/testrange/build_elements.py`: `add_element` (410) calls `add_chain`. `fill_layout` (466-473):
    `layout.bars[name] = []` and `layout.chains[name]` for each chain.
  - `scrimmage/testrange/build.py` `RangeLayout` (123-140): `chains: dict[str, ChainLayout]`.
  - `scrimmage/testrange/station.py` `_element_boxes` (305-336): a chain's edge boxes.
  - `scrimmage/terrain/climb.py`:
    - `hold_geoms` (51-57): a chain's hold is `[flex_id(model, f)]`;
    - `ClimbMonitor.__init__` (74-97): per-id lists of `n_ids(m)`;
    - `_read` (224) and `contact_state` (319) through `contact_ids`;
    - `_swing` (295-305) and `swing_direction` (350-360): R6;
    - `_score`'s lowest-point branch (284) skips chains.
  - Through `contact_ids`, with their per-geom arrays one entry per flex longer:
    - `scrimmage/terrain/monitor.py:184`;
    - `scrimmage/testrange/balls.py` (`_geom_ball` 162, and 440, 468, 607);
    - `scrimmage/blocks/conditions.py` (216, plus `_top` 252 per R6);
    - `scrimmage/blocks/feeds.py:147`;
    - `scrimmage/blocks/placed.py:128`;
    - `scrimmage/terrain/sweep.py:228`.
- Docs: `docs/terrain.md` "Field elements in range files": the chain kind's keys and checks (Task 9 writes
  climbing.md).

**Interfaces:**
- Consumes: Task 1's `tests/data/thin_bar/` robot and script.
- Produces:
  - `flexcontact.n_ids(model) -> int`: `ngeom + nflex`.
  - `flexcontact.flex_id(model, flex: int) -> int`: `ngeom + flex`.
  - `flexcontact.contact_ids(model, data) -> np.ndarray`, shape (ncon, 2): `data.contact.geom[:ncon]` itself when
    `nflex == 0`, else a copy with each −1 replaced by `flex_id(model, data.contact.flex[i, side])`.
  - `flexcontact.id_body(model) -> np.ndarray` (n_ids,): `geom_bodyid` with 0 (the world) for each flex.
  - `@dataclass(frozen=True) class Chain`: name, center, yaw_deg, span, top, low, diameter,
    `level: str | None = None`, `points: float | None = None`.
  - In chain.py:
    - `CHAIN_EDGES = 40  # choice: the chain spike's best (80 stretches 28 cm under two robots; 20 bends coarsely)`;
    - `CHAIN_MASS_PER_M = 0.744  # estimate: #35 roller chain, 0.50 lb/ft`;
    - `CHAIN_CONTACT = {"priority": 1, "solmix": 1e6, "solref": (0.002, 1.0), "solimp": (0.99, 0.999, 0.001, 0.5,
      2.0), "friction": (0.0, 0.005, 0.0001)}  # source: chain spike, chainlib.CONTACT`;
    - `EDGE_SOLREF = (0.002, 1.0)`, `EDGE_SOLIMP = (0.999, 0.9999, 0.001, 0.5, 2.0)  # source: chain spike`;
    - `VERTEX_INERTIA = 1e-6  # choice: a point mass`.
  - `rest_shape(span: float, top: float, low: float, n: int = CHAIN_EDGES) -> tuple[np.ndarray, float]`: (n+1, 2)
    points as (s from the middle, z), and the link length, per R3. ValueError when no catenary fits.
  - `chain_points(e: Chain) -> np.ndarray` (n+1, 3), in the field.
  - `add_chain(spec, e: Chain) -> None`:
    - bodies `element.<name>.v<i>` for i = 1..n−1, each with slides `_x`, `_y`, `_z` and mass
      CHAIN_MASS_PER_M · link;
    - the flex `element.<name>`: dim 1, radius d/2, the ends on the world body, FIELD_BITS, NON_GROUND_GROUP,
      selfcollide none, CHAIN_CONTACT;
    - equality `element.<name>.edges` (mjEQ_FLEX, EDGE_SOLREF, EDGE_SOLIMP);
    - beads `element.<name>.bead<i>` for i = 0..n: spheres of radius d/2, `contype = conaffinity = 0`, group 1,
      BAR_RGBA. The two end beads are on the world body.
  - `@dataclass class ChainLayout`: `flex: int`, `vertex_bodies: list[int]` (index 0 and n are −1, the world),
    `ends: tuple[np.ndarray, np.ndarray]`, `link: float`.
  - `ClimbMonitor.holds` keeps its tuples. A chain's geoms entry is `[flex_id]`.

- [ ] Write the parse tests:
  - `test_a_chain_parses`: D1's example table gives the `Chain` with its values, and `diameter_in = 0.5` gives
    0.0127.
  - `test_chain_checks`, each a RangeError naming `element 'c'`:
    - `low = 1.2` with `top = 1.18`;
    - `span = 0`;
    - `low = 0.005` (below its radius: "above the carpet");
    - `friction = 0.5`, and `edges = 20` (unknown keys);
    - `center = [0.5, 4.1]`, `yaw_deg = 0` (an end at x = −0.71: outside the field);
    - span 0.01 with sag 3 m ("no catenary").
- [ ] Write `test_the_rest_shape`:
  - 41 points; point 20 is the lowest, at z = `low` within 1e-9;
  - the ends at ±1.21 m and 1.18 m within 1e-9;
  - all links equal within 1e-12;
  - the length 40 × link is 2.642 m (the continuous catenary through 2.42, 1.18 and 0.7176) within 1 %.
- [ ] Write the world tests on `tests/data/chain/` (`build_world(load_config(...))`):
  - `test_a_chain_hangs_still`: idle 10 s. The lowest vertex is within 1 mm of 0.7176 (spike: 0.15 mm), its sd
    over the last 5 s under 0.1 mm, and every `data.qpos` finite.
  - `test_the_robot_climbs_the_chain`: `run_climb` with the chain fixture's script gives `"reached ONSTAGE"`, with
    no "walked off". Hanging, the lowest vertex sags 4–8 cm below 0.7176 (spike: 6 cm), and |`climb.swing`| over the
    last 3 s stays under 1.5° (spike: ±0.4°).
  - `test_the_beads_show_the_sag`: `model_view(world, include_world=True)["world_geoms"]` has the chain's 41 bead
    spheres. The 39 interior ones have a `mover`. After the climb, the middle bead's mover pose is more than 4 cm
    below its start.
  - `test_the_swing_axis_is_the_chains_line`: hanging, `swing_direction()` is horizontal and at right angles to
    the end-to-end line within 1e-6.
  - `test_a_bumper_on_the_chain_is_read_as_the_chain` (Review Focus 1): the robot with its hooks down drives its
    bumper into the chain at 3 V for 1 s. At a step with a flex contact:
    - `contact_state()` touches `chain_blue_center`;
    - `climb.hanging` is 0;
    - the terrain monitor's ground contact is false;
    - `balls.ball_of_geom(contact_ids(m, d))` is −1 on the flex side;
    - the model's last geom is a ball geom: assert it, so the test means something.
  - `test_six_chains_cost_at_most_3x_six_bars`: the 2024 range copied to tmp_path twice, once as is and once with
    its six `chain_*` bars as chains with D1's numbers. With the speaker example's robot, idle 2 s each. The wall-time
    ratio is ≤ 3.0 (spike 2.1×: 149 to 315 ms per simulated second).
- [ ] Write `test_no_world_module_reads_contact_geom_directly` (Review Focus 1): no file under `scrimmage/` but
  `flexcontact.py` and `scrimmage/bench/` contains `contact.geom[`.
- [ ] Write `test_no_flex_no_copy`: on the 2026 range, `contact_ids(m, d)` shares memory with `d.contact.geom`.
- [ ] Implement. Run `tests/test_range_chain.py tests/test_climb_*.py tests/test_terrain*.py
  tests/test_range_ledger_golden.py tests/test_blocks_climber.py`, then the full suite. Commit:
  `K5: a hanging chain element, read as one hold`.

### Task 3: ONSTAGE, HARMONY, their signals and a partner hanging on a chain (spec D5, D6)

**Files:**
- Modify:
  - `scrimmage/testrange/elements.py`: `CLIMB_RULES` (77) gains `"off-carpet"`. `ClimbRule` (331) gains
    `harmony: float = 0.0`. `parse_climb`'s reader (663) takes `harmony` (R8).
  - `scrimmage/terrain/climb.py`:
    - `CLIMB_SIGNALS` (63) gains `"hold"` and `"harmony"`;
    - `_score` gains the off-carpet branch (R8);
    - `_read` adds `climb.hold`, `climb.harmony` and `climb.hold.<hold>.robots` for each hold;
    - the partner's ground contacts are read only when it hangs;
    - the module docstring.
  - `scrimmage/checks.py` `_climb_signal` (203-226): `climb.hold.<hold>.robots`, with an unknown hold listing the
    holds. Update `SIGNALS_HELP` (24-30).
  - `scrimmage/assembler/signals.py:50`: add `climb.hold` and `climb.harmony`.
  - `scrimmage/testrange/game.py`:
    - `Game` gains `harmony: float = 0.0`, copied in `parse_game` (142);
    - `score` (198) gains `harmony: float = 0.0`, adding the R8 item after the climb's.
  - `scrimmage/blocks/match.py` `MatchState.see` (28): `harmony` too. `scrimmage/blocks/run.py:556` passes
    `climb.harmony`. `scrimmage/scenario_points.py` `_level` (108) reads `climb.harmony` with it.
  - `scrimmage/blocks/run_notes.py` `points_lines` (96-127): the harmony line, via `i18n.tn`.
  - `scrimmage/parts/partner.py`:
    - `PARTNER_KEYS` (36) gains `hang`;
    - `parse_partner` (90-128);
    - `PartnerSpec` and `PartnerInfo` gain `hang: Hang | None = None`;
    - `add_partner` (151-189) places, turns and welds it (R9);
    - `check_start` (248) checks only the robot for a hanging partner;
    - `PartnerMonitor._ref` (297) measures from the standing height.
  - `scrimmage/blocks/program.py` `_partner_setup` (544-562): `hang` (a hold name) and `along_in` (inches), with no
    `ahead_in` then. Add both to `PARTNER_SETUP_KEYS`.
  - `scrimmage/blocks/run_setup.py` `setup_partner` (45-61): a `hang` writes `{"hang": {"hold": h, "along":
    along_in * IN}}` and drops `start`.
  - `scrimmage/blocks/palette.py` (210-247): `"chains": sorted(layout.chains)`, and the climb signals `climb.harmony`
    and `climb.hold.<chain>.robots` on an off-carpet range.
  - `scrimmage/assembler/static/test_setup.js` `partnerBox` (171-200):
    - a **Hangs on** select ("no", then `palette.chains`) and an **along** length box (`measureBox`, inches in the
      file);
    - while hanging, "Ahead of my robot by", "Facing" and the brake are hidden;
    - the select shows only when `palette.chains` isn't empty.
  - `scrimmage/locales/en-GB.json`: entries as needed.
- Test: a new `tests/test_climb_harmony.py`, plus additions to `tests/test_partner.py`, `tests/test_range_game.py` and
  `tests/test_test_browser.py`.

**Interfaces:**
- Consumes: Task 2's `Chain`, `ChainLayout`, `layout.chains`, `flex_id` and `chain_points`.
- Produces:
  - `@dataclass(frozen=True) class Hang`: `hold: str`, `along: float` (m, signed along the chain's yaw from its low
    point).
  - `HANG_GAP = 0.02  # choice: R9`.
  - `PartnerInfo.hang: Hang | None` and `PartnerInfo.stand_low: float` (the lowest bottom corner's z standing on its
    wheels).
  - `ClimbMonitor.sample()` gains `climb.hold`, `climb.harmony` and `climb.hold.<hold>.robots`.
  - `score(..., harmony: float = 0.0)`, and `MatchState.see(t, x, y, rank, harmony: float = 0.0)`.
  - `[setup] partner = { hang = "<chain>", along_in = <in> }`.

- [ ] Write the rule tests in `tests/test_range_game.py`:
  - `test_off_carpet_and_harmony_parse`: `rule = "off-carpet"` with `harmony = 2` gives `ClimbRule.harmony ==
    2.0`;
  - `harmony` under `lowest-rung` is an error naming `[climb]` and `off-carpet`, and so is `harmony = -1`;
  - `test_score_adds_harmony`: `score(GAME24, log, PH, True, "ONSTAGE", harmony=2.0)` has items ending
    `("climb", "endgame", 1, 3.0), ("harmony", "endgame", 1, 2.0)` and endgame 5.0. `harmony=0` adds no item.
  - `points_lines` gives "HARMONY with 1 other robot: 2 points". Write the words with `i18n.tn`.
- [ ] Write `tests/test_climb_harmony.py` on `tests/data/chain/` with `rule = "off-carpet"` and `harmony = 2` (a
  tmp copy):
  - `test_off_carpet_scores_the_holds_level`: after the chain climb:
    - `climb.level == 1` and `climb.points == 3.0`;
    - `climb.hold` is chain_blue_center's number per R7;
    - `climb.hold.chain_blue_center.robots == 1` and `climb.harmony == 0.0`.

    Touching the carpet again makes all of them 0.
  - `test_a_hanging_partner_hangs` (`[partner] hang = { hold = "chain_blue_center", along = 0.9 }`):
    - at t = 0, its highest colliding geom's top is HANG_GAP ± 0.002 below the nearest interior vertex;
    - its heading is 180 (yaw 90 + 90);
    - after 2 s, `partner.tilt < 0.5` and the chain's lowest vertex is more than 2 cm below 0.7176;
    - `partner.height` equals (its lowest corner z − `stand_low`) / IN within 0.01, and is over 10;
    - no partner geom touches the chain.
  - `test_harmony_counts_the_robot_and_its_partner`: the climb with the partner at along 0.9:
    - `climb.hold.chain_blue_center.robots == 2`, `climb.harmony == 2.0` and `climb.points == 3.0`;
    - the robot's hook seat (the hooks' mean z) hangs at least 1 cm lower than in the climb with no partner.
  - `test_the_match_scores_onstage_and_harmony`: a `MatchState` fed the run's samples ends with endgame 5.0.
- [ ] Write the partner tests in `tests/test_partner.py`:
  - `test_hang_errors`, each a PartError naming `[partner]` (Review Focus 5):
    - both `start` and `hang`;
    - `hold = "nope"`, which lists the chains;
    - `hold = "chain_blue_left"` (a bar), "needs a chain";
    - `along = 1.3` on a 2.42 m chain: past span/2 − one link (1.21 − 0.066);
    - `hang` on a range with no chains.
  - `test_a_hanging_partner_over_the_robot_is_refused` (Review Focus 5): `along = 0.0` with the robot at
    `chain_center` gives the PartnerOverlap "starts inside the robot". Setup's version words it as
    `setup_partner_words` does.
  - `test_setup_partner_hangs`: `setup_partner(raw, {"hang": "chain_blue_center", "along_in": 35.4}, dir)` gives
    `{"hang": {"hold": "chain_blue_center", "along": 0.89916}}` with no `start`. `_partner_setup` refuses `hang`
    with `ahead_in`.
- [ ] Write the browser test in `tests/test_test_browser.py`, beside the partner Setup tests:
  `test_setup_hangs_the_partner_on_a_chain`. On the chain fixture:
  - **Hangs on** lists "no" and `chain_blue_center`;
  - picking the chain and typing 35.4 in along writes `setup.partner` = `{hang: "chain_blue_center", along_in:
    35.4}`;
  - "Ahead of my robot by" is hidden (`expect(...).to_be_hidden()`);
  - picking "no" brings it back.
- [ ] Implement. Run `tests/test_climb_harmony.py tests/test_partner.py tests/test_range_game.py
  tests/test_scenario_points.py tests/test_blocks_match.py tests/test_i18n*.py`, the browser test, then the full
  suite. Commit: `K5: ONSTAGE off the carpet, HARMONY, and a partner hanging on a chain`.

### Task 4: a piece's stiffness and restitution, and the measuring docs (spec D7)

**Files:**
- Modify:
  - `scrimmage/testrange/pieces.py`:
    - `_KIND` (90-92) and `_RIGID_OWN` (96) gain `restitution`;
    - `PieceKind` (353) gains `restitution: float | None = None`;
    - `_air` (970-976) reads it (R14: 0 < e < 1);
    - refused on a ball or disc kind beside the `stiffness` refusal (908-910): "a ball's restitution is its ball
      preset's".
  - `scrimmage/launcher/presets.toml` `[piece.2024-note]` (222-230):
    - `stiffness = 2000.0  # estimate: the soft-note spike's "soft" foam: how hard it pushes back per meter of
      squeeze at 25 % of its 2 in thickness, 12.7 N a side between two 4 in rollers; UNCALIBRATED`;
    - add `restitution = 0.3  # estimate: its bounce off the carpet from a 1 m drop, a dead foam thud (about 9 cm);
      UNCALIBRATED`.
  - `scrimmage/launcher/pinch.py` (24) and the `PieceKind.stiffness` comment (351): the D7 definition, the same
    number.
  - `docs/test-range.md` `[piece.<name>]` (579): the two keys, what each means and how to measure it.
  - `docs/measuring.md`: new sections after "Ball drop" (236):
    - "NOTE squeeze": a known weight on a NOTE lying between two boards, the squash measured, and stiffness =
      weight / (squash / 2) at about a quarter of its thickness;
    - "NOTE drop": dropped flat from 1 m, filmed against a ruler, restitution = √(rebound / 1 m).

    Update the "six tests" count. Both name `[piece.note] stiffness` and `restitution`.
- Test: `tests/test_range_pieces.py`.

**Interfaces:**
- Produces: `PieceKind.restitution: float | None`. The 2024-note preset has `stiffness == 2000.0` and
  `restitution == 0.3`.

- [ ] Write `test_restitution_is_a_rigid_kinds_key`:
  - `restitution = 0.4` on a ring kind reads 0.4;
  - 0, 1 and −0.1 each give a RangeError naming `restitution` and "between 0 and 1";
  - on a ball kind it names the ball preset;
  - `kind_preset("2024-note")` has 2000.0 and 0.3.
- [ ] Write `test_rigid_kinds_keep_their_numbers`: `rigid_contact(kind)` is the same with and without
  `restitution`.
- [ ] Add to the docs tests (`tests/test_range_docs.py` or wherever the `[piece.<name>]` key table is checked): the
  two keys are in the table, and measuring.md has both new sections, each naming its key.
- [ ] Implement. Run `tests/test_range_pieces.py tests/test_pinch_launcher.py tests/test_range_docs.py
  tests/test_language_docs.py`, then the full suite. Commit: `K5: a piece's restitution, and how to measure a NOTE's
  squeeze and bounce`.

### Task 5: the soft ring, its scaling rule and the parked pool (spec D8, first half)

**Files:**
- Create:
  - `scrimmage/testrange/soft.py`;
  - `tests/soft_helpers.py`: the spike's squeeze and drop scenes for a ring alone;
  - `tests/test_range_soft.py`.
- Modify:
  - `scrimmage/testrange/pieces.py`: `_KIND` gains `soft`. `PieceKind` gains `soft: bool = False` (R14: ring only).
    `SOFT_STIFFNESS_RANGE` is checked on a soft kind.
  - `scrimmage/launcher/presets.toml` `[piece.2024-note]`: `soft = true  # choice: spec D8`. Edit the header
    comment's "rigid stand-in".
  - `scrimmage/testrange/build.py` `add_range` (315-376): after the balls, with any slot of a soft kind, add
    `POOL_SIZE` rings parked at `(*slot_xy(n_slots_total + k), PARK_Z)` with PARKED_BITS.
    `RangeLayout.soft: list[str]` holds the ring names.
  - `scrimmage/testrange/runtime.py` (60-63): the ring trees may sleep, as the ball trees do.
  - `scrimmage/world.py` `movable_objects` (330-345): leave out bodies named `soft.*`.

**Interfaces:**
- Consumes: Task 4's `PieceKind.stiffness` and `restitution`.
- Produces, in `soft.py`:
  - constants:
    - `RING_SEGMENTS = 16  # source: soft-note spike, candidate (b)`;
    - `POOL_SIZE = 2  # choice: spec D8`;
    - `SOFT_REACH = 0.25  # choice: spec D8`, `SOFT_LINGER = 0.25  # choice: spec D8`;
    - `SOFT_SOLIMP = (0.2, 0.2, 0.001, 0.5, 2.0)  # source: spike: d = 0.2 keeps a static squeeze within 1.25x of an
      impact`;
    - `FIT_STIFFNESS = 2000.0`, `FIT_CONTACT_K = 11211.4`, `FIT_JOINT_K = 0.10276`, each `# source: soft-note spike,
      chain soft fit`;
    - `JOINT_ZETA = 0.3  # source: spike`, `DAMPING_SCALE = 0.85  # source: derived, R10`;
    - `CLOSE_SOLREF = (0.004, 1.0)  # source: spike's closing connect`;
    - `PARKED_BELOW = -10.0  # choice: R11`.
  - `damping_ratio_for(e: float) -> float`: −ln e / √(π² + ln² e).
  - `@dataclass(frozen=True) class RingParams`: `contact_k`, `contact_b`, `joint_k`, `joint_b`.
    `ring_params(kind: PieceKind) -> RingParams` follows R10.
  - `add_ring(spec, name: str, kind: PieceKind, pos, bits: dict) -> None`:
    - bodies `<name>.s<j>`: s0 has the free joint `<name>.free`, s1..s15 ball joints `<name>.j<j>` at their start,
      with stiffness kj, damping bj, mass kind.mass/16;
    - geoms `<name>.g<j>`: capsules of radius r = (diameter − inner_diameter)/4 and half-length R sin(π/16), where
      R = (diameter + inner_diameter)/4, condim 6, friction (kind.friction, 0.005, 0.0001), solref
      (−contact_k, −contact_b), SOFT_SOLIMP, solmix RIGID_SOLMIX;
    - the connect `<name>.close` from s15's end to s0's start (CLOSE_SOLREF), and an exclude between s15 and s0;
    - the ring lies in the plane normal to `kind.axis` through the kind's body origin, so a rigid pose maps onto it.
  - `ring_rest(model, ring: str) -> np.ndarray` (16, 3): each segment's CoM in the NOTE's own frame.
  - `write_rigid(model, data, ring: str, pos, quat, vel, ang_vel) -> None`: the rigid velocity field into s0's free
    joint, and the ball joints at rest.
  - `read_rigid(model, data, ring: str) -> tuple[pos, quat, vel, ang_vel]`: the CoM, the Kabsch rotation of the
    segment CoMs against `ring_rest`, the momentum over mass, and the spin from the angular momentum about the CoM.
  - `RangeLayout.soft: list[str]` (`soft.0`, `soft.1`), empty without a soft kind.

- [ ] Write `test_the_rule_scales_from_the_fit`:
  - at 2000/0.3, `ring_params` gives contact_k 11211.4;
  - at 6000/0.3, three times it;
  - joint_k in proportion;
  - contact_b equals 0.85 · 2 · `damping_ratio_for(0.3)` · √contact_k;
  - `damping_ratio_for(0.3)` is 0.358 within 0.001.
- [ ] Write `test_the_rule_reproduces_both_fitted_foams`, using `soft_helpers.squeeze(kind)` and
  `soft_helpers.drop(kind)`:
  - squeeze: the ring flat with no gravity, between two 4 in mocap cylinders 18 in wide (friction 0.7) on a line
    across its middle. They close from 2.3 in to 1.5 in apart over 0.6 s and hold 0.4 s. Read the top one's normal
    force, averaged over the last 0.15 s.
  - drop: flat from 1 m (its bottom) onto a carpet plane (friction 1.0, 0.01, 0.001). e = √(rebound apex above rest
    / 1 m).

  At 2000/0.3: squeeze 12.70 N and e 0.295, each within 10 %. At 6000/0.3: squeeze 38.10 N and e 0.306, each within
  10 %. These are the spike's fitted ring's own results.
- [ ] Write `test_soft_limits_are_checked` (Review Focus 4): `soft = true` with `stiffness = 50000`, and with 500,
  each give a RangeError naming `stiffness`, 1000 and 20000. `soft = true` on a `shape = "box"` kind names "ring".
- [ ] Write `test_the_ring_is_stable_at_both_ends_of_its_range` (Review Focus 4): at 1000 and at 20000 N/m, the drop
  stays finite, its deepest contact is under 40 mm, and it is still within 1 s. If an end fails, narrow
  SOFT_STIFFNESS_RANGE, say so in R14, and keep the test at the new ends.
- [ ] Write `test_a_rigid_pose_round_trips`: `write_rigid` then `read_rigid` at a turned pose with velocity and spin
  returns them within 1e-9. The momentum written is mass · vel within 1e-9 kg·m/s.
- [ ] Write the pool tests:
  - `test_a_soft_kind_builds_a_parked_pool`: the 2024 world (the speaker example) has `soft.0.s0` .. `soft.1.s15`
    at z = PARK_Z with PARKED_BITS. After 300 steps they are still there.
  - `test_no_soft_kind_no_pool`: the 2026 world's `nbody` and `nq` equal a build with `soft.py` monkeypatched to
    raise if imported.
  - `test_the_parked_pool_costs_little`: the 2024 world idles 2 s with the pool and with the preset's `soft`
    patched off. The ratio is ≤ 1.3 (spike: a parked ring adds about 0.02 s per simulated second).
  - `test_rings_arent_movable_objects`: `world.movable_objects()` has no `soft.` name.
- [ ] Implement. Run `tests/test_range_soft.py tests/test_range_2024.py tests/test_range_piece_world.py
  tests/test_range_ledger_golden.py tests/test_bench_*.py` (the bench ignores `soft`), then the full suite. Commit:
  `K5: a soft ring NOTE, scaled from its stiffness and restitution, pooled`.

### Task 6: the soft swap in the world, and one NOTE drawn (spec D8, second half)

**Files:**
- Modify:
  - `scrimmage/testrange/soft.py`: `SoftPool`.
  - `scrimmage/testrange/runtime.py`:
    - `RangeRuntime.__init__` makes `self.soft = SoftPool(...)`, or None without a pool;
    - `after_physics` (252-257) runs `soft.shadow()`, then `balls.after_step`, then `soft.update()`;
    - `reset` (100) calls `soft.reset()`;
    - add `soft_notes() -> list[str]`.
  - `scrimmage/testrange/balls.py`: `_in_play` (381) leaves out soft slots, so they get no flight force or rolling
    resistance. `ball_of_geom` and `_geom_names` cover the rings' geoms (the pool keeps them current).
  - `scrimmage/movers.py` `mover_poses` (34-45): the bodies in `world.test_range.soft.drawn_parked()` at
    (x, y, PARK_Z).
  - `scrimmage/assembler/static/live3d.js`: a mover whose z is below `PARKED_BELOW` is `visible = false`. Expose the
    count as `window.__scrimmage.live3d.hiddenMovers()`.
  - `scrimmage/blocks/run_notes.py` `run_notes` (68-87): append `soft_notes()`.
  - `scrimmage/scenario_runner.py` (near 470): the report's notes too.
  - Any contact reader that cached a piece's geom set by name: `blocks/conditions.py` `_field_side` (306) tests
    `ball_of_geom` at read time for a piece name.
- Test: `tests/test_range_soft.py` (more), and a browser test in `tests/test_live_frontend.py` or wherever live3d's
  movers are tested.

**Interfaces:**
- Consumes: Task 5's `add_ring`, `ring_rest`, `write_rigid`, `read_rigid`, `RangeLayout.soft`, SOFT_REACH,
  SOFT_LINGER and PARKED_BELOW. Task 2's `contact_ids`.
- Produces:
  - `SoftPool(model, data, balls: Balls, layout: RangeLayout, robot_root: int)`;
  - `shadow() -> None`: each in-use slot takes its ring's `read_rigid`;
  - `update() -> None`:
    - release the rings of slots that left play;
    - swap back the rings more than SOFT_REACH from every robot geom for SOFT_LINGER (R13);
    - swap in eligible slots within SOFT_REACH (R12) while a ring is free;
    - note a full pool once per reset.
  - `in_use() -> dict[int, str]` (slot to ring), `drawn_parked() -> set[int]` (body ids), `swaps: int`,
    `notes() -> list[str]`, `reset() -> None`.
  - The notes:
    - full pool: "a third NOTE came near the robot while {count} were soft; it stayed rigid (the soft pool holds
      {count})";
    - after any swap: "soft NOTE: its stiffness ({stiffness} N/m) and restitution ({restitution}) are UNCALIBRATED
      estimates; measure them (docs/measuring.md)".

    Both go through `i18n.t`.

- [ ] Set up a test fixture: the 2024 range in tmp_path with `floor_balls` at the blue WING middle spike only
  (2.8956, 5.5534), and the speaker example's robot from `blue_wing_note` with `preload = 0`. Drive at 2 V.
- [ ] Write `test_a_note_near_the_robot_turns_soft_and_back`:
  - at the first step the NOTE's center is within 0.25 m of a robot geom's box, `in_use()` has its slot;
  - the NOTE's momentum (ring) equals the stand-in's just before within 1e-6 kg·m/s, and its CoM within 1e-6 m
    (spike: 3e-9);
  - the robot then reverses at 4 V. 0.25 s after the NOTE is past 0.25 m from every robot geom, it is rigid again:
    `in_use() == {}`, BALL_BITS, state `field`, and momentum kept within 1e-6.
- [ ] Write `test_the_soft_note_is_the_same_piece`:
  - while soft, `piece_name(slot)` and `counts()` are unchanged;
  - `ball_of_geom` of each ring geom is the slot;
  - `ball_log()[slot]["last_pos"]` follows the ring's CoM;
  - a Test-tab `touch` on that NOTE by the chassis holds while only the ring touches it.
- [ ] Write `test_a_held_or_launched_note_stays_rigid` (Review Focus 2):
  - the speaker example with `preload = 1`: no swap while the NOTE is held or as it leaves (`swaps == 0`);
  - `tests/test_range_2024.py::test_the_example_scores_from_the_subwoofer`'s assertions hold as they are
    (`hood_deg` 58.0, `shooter_rps` 21.7, `first_hit` None);
  - a slot whose `_full_bits_at` is finite is never swapped, even at 0.1 m from the robot.
- [ ] Write `test_the_swap_back_on_the_carpet_doesnt_hop` (Review Focus 3): the robot presses the NOTE onto the
  carpet with its bumper, stops, then backs away. At the swap back:
  - the stand-in's lowest point is within 1 mm of the carpet or above it;
  - 50 ms later its vertical speed is under 0.05 m/s;
  - over 0.5 s it moves under 2 cm.
- [ ] Write `test_a_reset_mid_swap_parks_every_ring` (Review Focus 3): `World.reset()` with a ring in use parks every
  ring (z = PARK_Z, PARKED_BITS) and gives `in_use() == {}`. Two runs of 2 s from the reset give the same ball qpos
  within 1e-12.
- [ ] Write `test_a_full_pool_leaves_the_third_rigid_and_notes_it_once` (Review Focus 3): three NOTEs 0.4 m apart
  across the robot's path. Two swap and the third stays rigid. `soft_notes()` has the full-pool note exactly once.
  `run_notes` (Test tab) has it once. After a reset it is empty again.
- [ ] Write `test_one_note_is_drawn`: while soft, `mover_poses` puts the slot's body at z = PARK_Z, the ring's s0 at
  the ring's pose, and the free ring at PARK_Z. Add the browser test: the live view of that world after the swap has
  `hiddenMovers()` equal to the parked ball and ring bodies (`expect` on a test hook text).
- [ ] Implement. Run `tests/test_range_soft.py tests/test_range_2024.py tests/test_range_piece_world.py
  tests/test_blocks_*.py tests/test_scenario_points.py tests/test_range_ledger_golden.py`, then the full suite.
  Commit: `K5: a NOTE turns soft near the robot and back, drawn as one`.

### Task 7: the bench's flex NOTE and its fit (spec D9, first half)

**Files:**
- Create:
  - `scrimmage/bench/flex_note.py`;
  - `scrimmage/bench/flex_fits.json`: the 2024-note's fit, made by Step 4 and checked in;
  - `tests/test_bench_flex_note.py`.
- Modify: `pyproject.toml` package data: `bench/flex_fits.json`.

**Interfaces:**
- Consumes: Task 4's `PieceKind.stiffness` and `restitution`, and `SHAPED_CACHE` (testrange).
- Produces:
  - constants:
    - `MESH = (16, 6)  # source: soft-note spike: 16x6 pinches within 2 % of 24x8 once E is refit`;
    - `FLEX_RADIUS = 0.0005  # source: spike`, `POISSON = 0.3  # source: spike`;
    - `FLEX_TIMESTEP = 5e-4  # source: spec D9`;
    - `FLEX_CONTACT = {"condim": 3, "solref": (0.002, 1.0), "solmix": RIGID_SOLMIX}  # source: spike`;
    - `FIT_VERSION = 1`.
  - `torus_tets(n_phi: int, n_sec: int, radius: float, r_major: float, r_minor: float) -> tuple[np.ndarray,
    np.ndarray]`:
    - each section is a center vertex and n_sec boundary vertices on a polygon of circumradius
      (r_minor − radius) / cos(π/n_sec), its faces centered top and bottom;
    - each section-to-section prism splits into 3 tets by the smallest-global-index rule (code below), and each tet
      is turned to positive volume.
  - `add_flex_note(spec, name: str, pos, kind: PieceKind, fit: FlexFit) -> str`: a `flexcomp`-equivalent direct 3D
    flex. Its options: dof full, mass kind.mass, FLEX_RADIUS, FLEX_CONTACT, friction (kind.friction, 0.005, 0.0001),
    selfcollide none, and elasticity young E, poisson 0.3, damping β.
  - `@dataclass(frozen=True) class FlexFit`: `E: float`, `beta: float`.
  - `squeeze_force(kind, fit) -> float` and `drop_restitution(kind, fit) -> float`: Task 5's scenes, on `discrete`
    with Newton at FLEX_TIMESTEP.
  - `target_force(kind) -> float`: `kind.stiffness * thickness / 8`, each roller's share of a 25 % squeeze (12.70 N
    for the preset).
  - `fit_flex(kind) -> FlexFit`: shipped, else cached, else fitted (R15). The fit:
    - E: a secant on log F against log E from 12 235 Pa (spike, 16×6), until within 2 %;
    - β: a bisection on log scale in [2.5e-4, 2.5e-2] for e within 0.02 of `restitution`, at most 7 steps.

  ```python
  PRISM_I = [[0, 1, 2, 3, 4, 5], [1, 2, 0, 4, 5, 3], [2, 0, 1, 5, 3, 4],
             [3, 5, 4, 0, 2, 1], [4, 3, 5, 1, 0, 2], [5, 4, 3, 2, 1, 0]]

  def _split_prism(v):          # v: bottom v0 v1 v2, top v3 v4 v5 over them (Dompierre et al. 1999)
      p = [v[i] for i in PRISM_I[int(np.argmin(v))]]
      if min(p[1], p[5]) < min(p[2], p[4]):
          return [[p[0], p[1], p[2], p[5]], [p[0], p[1], p[5], p[4]], [p[0], p[4], p[5], p[3]]]
      return [[p[0], p[1], p[2], p[4]], [p[0], p[4], p[2], p[5]], [p[0], p[4], p[5], p[3]]]
  ```

- [ ] Write `test_the_torus_mesh`, for the preset at 16×6:
  - 112 vertices and 288 tets, every tet's volume > 0;
  - the summed volume within 5 % of the round tube's 2π² R r² (R = 6 in, r = 1 in);
  - the vertices' z extent plus 2 · FLEX_RADIUS is 2 in within 1e-9.
- [ ] Write `test_the_shipped_fit_reproduces_its_squeeze_and_drop`: `fit_flex(kind_preset("2024-note"))` loads from
  flex_fits.json without running a scene (monkeypatch `squeeze_force` to raise). Running the scenes on it gives
  12.70 N within 2 %, and e within 0.02 of 0.3.
- [ ] Write `test_a_new_pair_is_fitted_once_and_cached`, with `squeeze_force` and `drop_restitution` monkeypatched
  to cheap monotone stand-ins and `SHAPED_CACHE` in tmp_path: the first call fits and writes one file, and the second
  reads it without calling the scenes.
- [ ] Implement. Then make the shipped fit: run `fit_flex` for the preset with the shipped file absent, and write
  `{"2000_0.3": {"E": ..., "beta": ...}}` with `# source:` text in a sibling README line. Expect E near 12 kPa and
  β near 0.0025 (spike, 1 ms). Report both.
- [ ] Run `tests/test_bench_flex_note.py tests/test_bench_*.py`, then the full suite. Commit:
  `K5: a flex NOTE model for the bench, fitted to stiffness and restitution`.

### Task 8: the flex NOTE on the bench, and K2's pinch check (spec D9 second half, D10)

**Files:**
- Modify:
  - `scrimmage/bench/spec.py`: `_SECTIONS["piece"]` (252) gains `flex`. `Piece` (58-75) gains `flex: bool = False`
    and `restitution: float | None = None`.
  - `scrimmage/bench/spec_piece.py`: `_PRESET_KEYS` (146) gains `flex` and `restitution`. `_kind_piece` (149) reads
    them. `flex = true` on anything but a ring kind is an error naming `flex` and "ring".
  - `scrimmage/bench/build.py` `build_bench` (308-420), with `piece.flex`:
    - the integrator is `discrete` with Newton, timestep FLEX_TIMESTEP unless `[run] timestep`;
    - the pool is `add_flex_note` parked under the bounds, at most FLEX_POOL_MAX (BenchError otherwise);
    - `BenchLayout` gains `flex_pieces: list[int]` (flex ids).
  - `scrimmage/bench/run.py`:
    - a `FlexPieces` adapter that BenchRun uses for `state`, `kinetic`, `spawn` and `settle` when `piece.flex`;
    - `rigid_contacts` (423) maps a contact's flex side to its piece;
    - `spread_stiffness` (253) is skipped for flex.
  - `scrimmage/bench/report.py`: a flex result's headline and table say "experimental (MuJoCo flex)" (via
    `i18n.t`), with one line saying its stiffness and restitution are UNCALIBRATED estimates (spec D7) unless
    measured. `[flight]` (240) flies its exit state as the rigid kind.
  - `tests/test_pinch_launcher.py::test_exit_speed_against_the_bench` (248-257): keep the strict xfail and replace
    its reason: "a rigid NOTE on the bench takes `stiffness` at each of its 8 contact points per wheel, so it squeezes
    harder than its stiffness says (docs/bench.md, Known limits); the flex NOTE is checked on the spike's geometry in
    test_flex_note_matches_pinch_py".
  - `docs/bench.md`:
    - `[piece]` (405): `flex` and `restitution`;
    - a "Flex NOTE (experimental)" section under "Pieces and contact laws" (353): opt-in, about 100× a rigid run,
      sweep rigid first then confirm on flex, the fit and its cache, and MuJoCo's own warning about `discrete`;
    - a "Known limits" subsection under "How accurate is this?" (861) with the rigid-piece stiffness limit.
- Create:
  - `examples/bench/note_pinch_flex.toml`: the spike's pinch:
    - two 4 in wheels, 14 in wide 60A, at z = ±(0.75 in + 2 in);
    - each on a KrakenX60 shaft, `control = "speed"`, `target_rps = 50`, `wheel_inertia = 1.0` (held at speed);
    - `[piece] preset = "2024-note"`, `flex = true`;
    - the inlet 8.5 in before the axles, at 2 m/s along +x, gravity as the bench has it;
    - an outlet 10 in past the axles.
  - `tests/data/bench_flex_golden.json`: the 3000 rpm shot's exit speed, spin and angle.
- Test: `tests/test_pinch_launcher.py` and `tests/test_bench_flex_note.py`.

**Interfaces:**
- Consumes: Task 7's `add_flex_note`, `fit_flex`, `FlexFit`, MESH and FLEX_TIMESTEP.
- Produces: the `[piece] flex` key, and `FLEX_POOL_MAX = 3  # choice: R15`.

- [ ] Write `test_flex_note_matches_pinch_py` (D10), parametrized at 3000 and 5000 rpm (`target_rps` 50 and
  83.333 in a tmp copy of note_pinch_flex.toml). The bench's exit speed is within 15 % of `single_shot` on the same
  geometry: `PinchSetup(piece_of(piece_box(NOTE), NOTE.mass, NOTE.inertia, 2000.0), 4 in, 4 in, gap = 1.5 in,
  wheel_x = 0.25, wheel_width = 14 in, durometer 60, friction 0.7, shafts = (Shaft(wheel_inertia=1.0,
  motor="KrakenX60", motor_count=1, gearing=1.0),) * 2, entry_speed = 2.0)`. The model gives 7.95 m/s, and the spike's
  flex 8.38 and 8.53.
- [ ] Write `test_the_flex_shot_golden`: note_pinch_flex.toml's exit speed, spin and angle match
  bench_flex_golden.json within 2 % (R15). Speed and angle are compared relative, and spin absolute within 0.5 rad/s.
- [ ] Write `test_flex_is_opt_in`: the same file without `flex` builds with `implicitfast` and no flex
  (`model.nflex == 0`). Every file in `examples/bench/` but note_pinch_flex.toml builds with `nflex == 0`.
- [ ] Write `test_flex_errors`: `flex = true` on a sphere names "ring", and 4 pieces in the inlets names 3.
- [ ] Write `test_the_report_marks_flex_experimental`: the headline and the table both say "experimental (MuJoCo
  flex)", and the notes say "UNCALIBRATED". With `[flight]` to the 2024 SPEAKER fixture, the shot is flown as the rigid NOTE from the flex's exit state.
- [ ] Implement. Run `tests/test_pinch_launcher.py tests/test_bench_*.py tests/test_bench_flex_note.py`, then the
  full suite. Commit: `K5: a flex NOTE on the bench, checked against the pinch model`.

### Task 9: the 2024 range, the examples, the docs and the findings (spec D11)

**Files:**
- Modify:
  - `scrimmage/testrange/ranges/2024.toml`:
    - the six `chain_*` elements (285-345) become `kind = "chain"` with their own `center` and `yaw_deg`, and:
      `span = 2.42  # source: field CAD GE-24205, the CHAIN: 2.42 m across`, `top = 1.18  # source: GE-24205: hung
      from 1.18 m`, `low = 0.7176  # source: M 5.5: lowest point 2 ft 4 1/4 in`, `diameter_in = 0.5  # estimate`,
      `level = "ONSTAGE"`;
    - `[climb] rule = "off-carpet"  # source: spec D5, 2024's rule: ONSTAGE is touching the CHAIN and not the carpet` and
      `harmony = 2  # source: M Table 6-2: HARMONY 2`;
    - add `[start.blue_chain_center] pose = [6.05, 4.1051, 180.0]  # choice: 0.25 m short of chain_blue_center,
      facing it`;
    - the header comment: drop HARMONY from "not expressible" and "Not modeled". Say the NOTE turns soft near a
      robot.
  - `tests/test_range_2024.py::test_the_range_has_the_2024_game` (55-71): `assert g.harmony == 2.0`. On Task 1's
    fallback, `test_the_range_checks_clean` returns to `rng.warnings == ()`.
  - `docs/climbing.md`:
    - "Elements and ranges" (25): the chain element, the hold, `off-carpet`, `harmony` and the hanging partner;
    - "Known limits" (319): drop "Chain and rope aren't modeled" (rope stays not modeled), add D4's "a hook on a
      chain settles at a link and doesn't slide toward the low point (about 200–250 N to move a 60 kg robot)", and
      say the chain is frictionless on bumpers (R4).
  - `docs/test-range.md`: "The 2024 field" (896) and "What isn't modeled" (1453), for the chains and HARMONY. A
    "Soft pieces" section after "How the pieces are kept" (869): `soft`, `stiffness`, `restitution`, the reach, the
    linger, the pool of 2, and one NOTE drawn.
  - `docs/test-tab.md` "The examples" (858): the two new examples.
  - `docs/superpowers/specs/2026-11-08-kickoff-dry-run-findings.md`: a "**K5 status (2026-11-11).**" note under gaps
    3, 9 and 15, and under gap 1's pinch-exit-speed item (188-191).
- Create:
  - `examples/blocks/chain-climb/`:
    - `climber-standin.toml`: Task 1's robot on `file = "2024"`, `start = "blue_chain_center"`;
    - `onstage.test.toml`: the climb as blocks (`move c.stage1` to 0.14 m, `drive` 0.31 m, `move` to 0 until
      `touch hook chain_blue_center`, wait 5 s), then expect `level ONSTAGE` and `hanging_from chain_blue_center`;
    - `harmony.test.toml`: the same, `match = true`, `[setup] partner = { hang = "chain_blue_center", along_in =
      35.4 }`, expecting `points = { phase = "endgame", at_least = 5 }`;
    - `README.md` for a student: where the partner hangs and why (D4, R9).
  - `examples/blocks/note-intake/`:
    - a stand-in robot with a front roller intake (copy `examples/blocks/intake/parts/intake`): a 2 in roller with
      its axle 2.75 in up, a ceiling 1.25 in above the axles and a back stop 20 in behind, all `# choice:` from the
      soft-note spike's intake case, and a hopper body;
    - `intake.test.toml`: from `blue_wing_note`, the roller at about 20 rps (its surface 3.2 m/s), drive at the
      middle WING NOTE, expect `piece_in` the hopper;
    - `README.md`, including that the answer no longer flips between rigid stand-ins.
- Test: `tests/test_range_2024.py`, `tests/test_examples_blocks_open_clean.py` (it opens every example).

**Interfaces:**
- Consumes: everything above. `kind = "chain"`, `off-carpet`, `harmony`, `[partner] hang` and Setup's `hang`,
  `soft`, and the flex bench example.

- [ ] Write the tests in `tests/test_range_2024.py`:
  - `test_the_chains_hang_at_their_low_point`: every chain's lowest vertex at rest is 0.7176 within 1 mm after 2 s;
  - `test_the_chain_climb_example_scores_onstage`: onstage.test.toml passes;
  - `test_the_harmony_example_scores_5_in_the_endgame`: harmony.test.toml passes, and its points have the climb 3.0
    and harmony 2.0 items;
  - `test_the_note_intake_takes_the_note_at_both_heights`: intake.test.toml passes as shipped (2.75 in) and with the
    axle at 2.5 in (a tmp copy). Each run's `soft.swaps >= 1` (spec goal: the answer no longer flips; spike: soft
    "in" at both);
  - the existing tests' numbers unchanged but for the Global Constraints' named ones.
- [ ] Add a docs check in `tests/test_range_docs.py`: climbing.md names `kind = "chain"`, `off-carpet`, `harmony` and
  `hang`, and no longer says "Chain and rope aren't modeled".
- [ ] Implement. Run `tests/test_range_2024.py tests/test_range_ledger_golden.py tests/test_range_model.py
  tests/test_language_docs.py tests/test_examples_blocks_open_clean.py tests/test_scenario_points.py`, then the full
  suite. Commit: `K5: 2024's chains and HARMONY, a soft-NOTE intake example, and the docs`.
