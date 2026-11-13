# Soft-NOTE robustness and the intake example (kickoff fixes K6): design

K5 (docs/superpowers/specs/2026-11-11-soft-note-and-chain-design.md) put a soft NOTE in the world: a ring of 16
capsules that the world swaps in near a robot (D8). An intake spike on 2026-11-12 then looked for a floor intake that
picks that NOTE up reliably. It found one, an over-under nip, but most of its time went to three simulator problems
that make realistic intakes impossible:

1. **The squash goes through thin parts.** The NOTE sinks 20–37 mm into a part against a tube radius of 25.4 mm. On a
   1/8 in or 6 mm plate, or after a hard hit, it tunnels, flips and blows up at 50–7000 m/s. Once it tipped a robot
   over.
2. **The ring passes through itself.** A folded ring's segments overlap 30–50 mm and blow up.
3. **The NOTE can be run over.** A robot drives it under a slab with a 2 mm gap.
4. Smaller points: a test with no `hopper` says "stuck in the robot" while `piece_in` passes; the piece table says
   `on_field` for a NOTE in the bin; the bench has no moving carpet, so its intake reads lower than the world's.

This project fixes the three problems at their causes, keeps the NOTE's calibration, ships the intake example K5
dropped, and fixes the smaller points.

**Evidence.** Two sets of throwaway scripts, kept in the session scratchpad (not in the repo). Every number this
design rests on is copied into it:

- the intake spike: `intake-spike/RESULTS.md`, `blowup.py`, `slam.py`, `noself.py`, the grids `F_*.jsonl` and the
  winning design in `winner/`;
- this design's measurements, `intake-spike/k6/`:
  - `scenes.py`: one ring alone on the world's options (implicitfast, Newton, elliptic cone, impratio 10, 1 ms):
    - `slam`: sliding flat into a wall;
    - `press`: a 2 in roller pressing the NOTE onto a thin plate, held or dragged;
    - `slab`: a slab 2 mm over the carpet driving at it;
    - `taco`: the ring folded and pressed face to face;
  - `cfg.py` and `patch.py`: the candidate fixes, applied in-process;
  - `harness.py` and `sweep.py`: the spike's harness with the fix, and true segment speeds;
  - `fix_*.jsonl` and `off_*.jsonl`: the spike's grids rerun with the fix on and off.

Every number below that isn't from a source is tagged as an estimate or a choice, as in K2–K5. Under the owner's
standing authorization (2026-11-04: "do all remaining work my way"; 2026-10-28: decide and document), every question
here is decided, as a ruling with its cost. The rulings are numbered K6-R1 and up, so they don't collide with K5's
R1–R16.

## Goal and limits

- **Goal.**
  - The soft NOTE can't be pushed through a part, through itself, or under a robot by any load an FRC mechanism makes:
    1/8 in and 6 mm plates at roller loads and on hard hits, a folded ring, a 2 mm slab.
  - If it ever blows up anyway, the run says so. It is never silent.
  - Its two numbers keep their meaning. `stiffness` is still the force per meter at a 25% squeeze, and
    `restitution` is still the bounce from a 1 m drop.
  - `examples/blocks/note-intake/` picks a soft NOTE off the 2024 carpet, and it passes the spike's robustness grids.
- **Unchanged.**
  - Balls, discs, rigid pieces and the rigid NOTE (far from a robot, or held) give the same numbers.
  - The world's timestep and integrator, the swap (R11–R13), the pool, the bench flex NOTE's model and fit.
- **Non-goals:**
  - a calibrated foam: the numbers stay estimates until measured (docs/measuring.md);
  - the soft NOTE in flight, or on the bench outside `flex = true`;
  - the rigid pieces' own softness against a part with `priority` (found here, queued: K6-R16).

## What causes each problem

### 1. The squash goes through thin parts

The world's ring contact is set in `scrimmage/testrange/soft.py`:

```python
SOFT_SOLIMP = (0.2, 0.2, 0.001, 0.5, 2.0)  # source: spike: d = 0.2 keeps a static squeeze within 1.25x of an impact
FIT_CONTACT_K = 11211.4  # source: soft-note spike, chain soft fit
contact = {"condim": 3, ..., "solref": [-p.contact_k, -p.contact_b], "solimp": list(SOFT_SOLIMP), "solmix": RIGID_SOLMIX}
```

There are four causes, and three of them are in these lines.

- **A spring that never stiffens.**
  - MuJoCo's impedance d grows from `dmin` to `dmax` over `width` of penetration. Here `dmin = dmax = 0.2`, so d is
    0.2 at every depth and `width` (1 mm) does nothing.
  - With a negative solref, the contact acts as a spring of stiffness `contact_k` × (d / dmax)² per unit of the
    contact's effective mass. Constant d means a linear spring all the way to the tube's center line, and past it.
  - Measured with `tests/soft_helpers.py`'s squeeze, the roller force is exactly `stiffness` × the depth per side:

    | Gap (in) | Depth per side (mm) | Force (N), shipped |
    |---|---|---|
    | 1.5 (25%) | 6.35 | 12.70 |
    | 1.0 (50%) | 12.70 | 25.40 |
    | 0.6 (70%) | 17.78 | 35.56 |
    | 0.4 (80%) | 20.32 | 40.64 |

    So 41 N squashes the 2 in NOTE to 0.4 in. A real foam densifies long before that.
  - `contact_k` = 11211 s⁻² gives an impact frequency of √11211 = 106 rad/s per unit mass. That is the spike's
    "3.7 mm per m/s" into a wall.
- **No margin against the geometry.** The geom margin is MuJoCo's default 0. A capsule's contact normal against a box
  comes from the face nearest its axis. Once the axis passes the plate's middle, at a depth of r + T/2, the normal
  flips and the contact pushes the capsule out the far side:
  - 27.0 mm for a 1/8 in plate;
  - 28.4 mm for a 6 mm plate.

  Measured (`k6/scenes.slam`, priority 0):

  | Hit speed (m/s) | 2 | 4 | 6 | 8 |
  |---|---|---|---|---|
  | Depth (mm), shipped | 3.8 | 14.5 | 22.2 | 26.8 to 28.2 (through both thin plates) |

  A roller load does the same with no hit at all (`k6/scenes.press`, a 2 in roller on the NOTE on a plate):
  - held still, 100 N sinks 29.5 mm and goes through the 1/8 in plate;
  - dragged at 3.4 m/s, 25 N is enough (33 mm, through).
- **A part with `priority` replaces the NOTE's contact.** This cause is not in soft.py. It is in MuJoCo's contact
  mixing rule.
  - At equal priority, the ring's `solmix` of 1e6 makes its solref and solimp govern. A geom with a higher priority
    instead uses its own solref and solimp.
  - A part geom with `priority = 1` and no `solref` therefore meets the NOTE with MuJoCo's default contact: solref
    (0.02, 1), solimp (0.9, 0.95, 0.001, 0.5, 2). That contact is stiff when held still, but on impact it is a
    50 rad/s spring, half as stiff as the NOTE's.
  - The spike's bin, floor slab, lid and guides all set `priority = 1`, to get polycarbonate's 0.4 friction
    (docs/parts.md). So the NOTE's `stiffness` never applied to them at all.
  - Measured against a priority-1 wall: 20.8 mm at 4 m/s (14.5 at priority 0), 34 mm at 6 m/s and 46 mm at 8 m/s into
    a 5 cm box. The spike's "hit the back stop at about 6 m/s and sank 37 mm" was this case.
- **The speeds were over-read.**
  - The spike's harness and `blowup.py` took a segment's speed from `data.cvel[b][3:]`. That is the linear velocity
    at the kinematic tree's center of mass, not at the segment's. For a spinning ring it overstates: the nominal
    intake run read 6.4 m/s, and the ring's own kinematics (`Ring._segments`) give 2.4.
  - So the spike's 20 m/s blow-up flag fired early. Its one blow-up in the corner grid
    (`[-0.5, +1, 11 V, 0.5 m/s, -15°]`, logged at 20.6 m/s) is 16.3 m/s measured truly: a real wedge, but not an
    explosion. The thousands of m/s are real.
- **Then MuJoCo resets everything.** A true blow-up makes MuJoCo warn `mjWARN_BADQACC` and, by default, reset the
  whole simulation. Only the bench reads that warning (`bench/run.py`). The world doesn't, so a world run can jump
  back to its start with a line on stderr and nothing in the report.

### 2. The ring passes through itself

- **What collides.**
  - MuJoCo's `filterparent` (on by default) drops parent–child body pairs, so segment j never touches j ± 1 along
    the tree.
  - `add_ring` excludes s15–s0 by hand, since the closing pair is joined by the equality `<name>.close` and isn't
    parent and child.
  - Every other pair collides with the same soft contact. The exclusion is "neighbors only" because neighbors
    overlap at rest, by construction. Nothing else does.
- **j and j + 2.** They are 2R sin(π/16) − 2r = 59.5 − 50.8 = 8.7 mm apart at rest. The segment between them fixes
  their ends 59.5 mm apart, so they can only touch when the joints between bend past about 90°: a kink, or the fold
  line of a taco. That is a real foam effect: the inside of a tight bend pushes back.
- **Why they go through each other.**
  - A self-contact is two ring capsules against each other. The mass-normalized spring acts on their relative
    motion, whose effective mass is about half a segment's against a part. So the same load sinks it about twice as
    deep, and with no stiffening nothing stops it.
  - Past r of overlap, the capsule–capsule normal flips, as against a plate.
  - Measured in the taco scene (the halves pressed together between plates closing to 60 mm): self-overlap
    18.4–21.3 mm. The spike's roller kinks reached 30–50 mm.
  - The closing equality is not the cause: it opened only 0.41 mm under the same load.
  - The spike's diagnostic of excluding pairs up to 8 apart removed the mode by removing the resistance. The ring
    could then fold through itself freely.

### 3. The NOTE can be run over at a 2 mm gap

- To pass under a slab 2 mm over the carpet, the 50.8 mm tube must squash 24.4 mm from each side, which is nearly
  its radius. The linear contact allows that for about 50 N.
- The spike's floor slab is also `priority = 1`, so it met the NOTE with MuJoCo's soft-on-impact default.
- Measured (`k6/scenes.slab`, the slab driving at 2 m/s, the NOTE pulled toward it by up to 20 N while it moves
  slower than a 3.4 m/s roller surface):
  - a priority-1 slab rides over the NOTE with no pull at all (16 of 16 segments under, 49 mm deep);
  - at priority 0 it pushes the NOTE ahead (0 under, 18–29 mm deep).

  So the run-over is cause 1's priority path. The 2 mm gap itself only matters because the squash is linear.

### 4. The smaller points

- **"Stuck in the robot" while `piece_in` passes.**
  - `blocks/feeds.py`'s `Outcomes` takes its hopper from `watch.hopper`, the test's `hopper` list.
  - With none given, a NOTE at rest on `intake.hopper` sits outside every hopper body, touches a counting robot body,
    and so is counted `stuck`. A `piece_in` condition's `body` never reaches the tracker.
  - The run's `sentence` is called with `hopper=bool(self.program.hopper)` (`blocks/run.py`).
- **`on_field` for a NOTE in the bin.**
  - `blocks/run_notes.py`'s `piece_lines` prints the ledger's outcome (`balls.update_log`). The ledger has no state
    for "in a bin the robot built": a piece in play and not in flight is `on_field`.
  - The feed tracker's rows (`Outcomes.rows`) know it is `in`, but the table doesn't read them.
- **No moving carpet on the bench.**
  - A `[[plate]]` is static. A floor intake can only be fed by an `[[inlet]]`'s velocity, so a NOTE that slows or
    stalls at the front roller fails on the bench where the world's robot would push it through.
  - MuJoCo 4 has a geom surface velocity (`surfacevel`). Measured: a box on a plate with `surfacevel` 1.5 m/s
    reaches 1.5 m/s in under 1 s.

## Decisions

Each one gives what was decided, why, and its cost if wrong.

### The squash

- **K6-R1. The ring's contact stiffens past about 60% of its squash.** The solimp becomes a ramp, and the solref is
  scaled so that a shallow contact is exactly today's:

  ```python
  SOFT_DMIN = 0.2          # source: K5 spike (SOFT_SOLIMP's d): a static squeeze within 1.25x of an impact
  SOFT_DMAX = 0.95         # source: MuJoCo's default dmax (build.BALL_DMAX)
  RAMP_WIDTH = 1.0         # estimate: the ramp's width in tube radii; the foam densifies past about 60 % squeeze
  RAMP_MID, RAMP_POWER = 0.9, 6.0   # choice: flat to about half the width, hard by 0.9 of it
  W_RING = 700.0           # choice: rad/s, the stiffest contact's frequency per unit mass (stable at 700, not 1000)
  ```

  - **The rule.** For a kind:
    - `dmax = min(SOFT_DMAX, SOFT_DMIN · W_RING / √contact_k)`;
    - `f = dmax / SOFT_DMIN`;
    - solref = (−contact_k · f², −contact_b · f);
    - solimp = (SOFT_DMIN, dmax, RAMP_WIDTH · r, RAMP_MID, RAMP_POWER).

    Since the spring is contact_k · (d / dmax)², the new solref at d = dmin gives today's spring and damping exactly.
    At depth, the impact spring grows by up to f² and the static one by f² (1 − dmin) / (1 − dmax). For the preset
    that is 22.6× and 361×.
  - **What it gives.** Measured at the preset, 2000 N/m, with the shipped numbers alongside:

    | Scene | Shipped | K6-R1 |
    |---|---|---|
    | Squeeze at 1.5 in (25%), the calibration point | 12.70 N | 12.7 N |
    | Squeeze at 1.0 in (50%) | 25.4 N | 31.5 N |
    | Squeeze at 0.6 in (70%) | 35.6 N | 133.5 N |
    | Squeeze at 0.4 in (80%) | 40.6 N | 494.3 N |
    | 1 m drop, deepest | 16.2 mm | 15.3 mm |
    | 1 m drop, e (before the refit, K6-R6) | 0.300 | 0.307 |
    | Slam at 8 m/s into a 1/8 in or 6 mm plate | 26.8 and 28.2 mm, through | 20.4 mm, held |
    | Roller on a 1/8 in plate, 200 N held | 46.9 mm, through | 15.5 mm |
    | Roller on a 1/8 in plate, 200 N dragged at 3.4 m/s | 46.9 mm, through | 18.3 mm |
    | 2 mm slab, 20 N pull (priority 0) | 0 under, 29 mm deep | 0 under, 19.9 mm deep |
    | Taco at 60 mm, self-overlap | 18.4–21.3 mm | 16.5–17.3 mm |

    Across the range (`k6/stiffcap.py`):

    | Stiffness (N/m) | dmax | Squeeze at 25% | Drop deepest | Drop e before refit | Slam at 8 m/s, 1/8 in | Squeeze to 0.4 in |
    |---|---|---|---|---|---|---|
    | 1000 | 0.95 | 6.4 N (target 6.35) | 19.3 mm | 0.338 | 23.1 mm, held | stable |
    | 2000 | 0.95 | 12.7 N (12.7) | 15.3 mm | 0.307 | 20.4 mm, held | stable |
    | 6000 | 0.763 | 38.2 N (38.1) | 9.3 mm | 0.300 | 15.5 mm, held | stable |
    | 20000 | 0.418 | 127.1 N (127.0) | 5.1 mm | 0.300 | 4.9 mm, held | stable |

    With W_RING at 1000 rad/s, the squeeze went NaN at 6000 and 20000 N/m past 0.6 in, so 700 is the cap.
  - **Why this width.** Three widths were measured.

    | Width | 1000 N/m slam at 8 m/s (flip at 27.0 mm) | Spike's failing corners passed (of the 5 that fail at 0.8 r) | Lip +1 in |
    |---|---|---|---|
    | 0.8 r | 19.8 mm | 0 | fails |
    | 0.9 r | 21.6 mm | 1 | passes |
    | 1.0 r | 23.1 mm | 3 | passes |

    - The narrower ramp is stiffer earlier, so it jams the example's tight nips (K6-R10). A real foam's onset is
      unknown (owner question 1).
    - 1.0 r is the widest that keeps every case short of a flip: the softest kind's 8 m/s hit stops 3.9 mm short of
      the 1/8 in flip, and the preset's 6.6 mm short. A launched NOTE is rigid, so an 8 m/s hit near a robot is
      already a worst case.
    - A steeper ramp (16 mm wide) blew up once in the taco scene, at 8900 m/s.
    - The 25% calibration point stays on the flat part at every width.
    - *If the example's full grid at 1.0 r misses its target*, the width stays: K6-R10's retune handles that. If an
      acceptance case at 1.0 r leaves its bound, 0.9 r is the fallback, again with K6-R10's retune.
  - **Alternatives weighed:**
    - *A stiffer near-contact solimp* (raising dmin): it changes the static-to-impact ratio from the first
      millimeter, so every fit moves, and it is still linear, so it still goes through under load. Rejected.
    - *A stiffer `stiffness`*: it breaks the key's meaning. Rejected.
    - *A capsule radius or margin change* (K6-R4).
  - *Cost:*
    - The NOTE no longer squashes past about 75% at any force a mechanism makes. A real NOTE probably doesn't either,
      but the ramp's onset is an estimate (owner question 1).
    - Nips tighter than about 0.4 in now jam, and they did not before (K6-R10).
    - The 1 m drop reaches the ramp at 1000–2000 N/m, so DAMPING_SCALES is refit (K6-R6).
    - One more rule, and a test of it.

- **K6-R2. The NOTE's squash applies against every robot geom, even one with `priority`.**
  - **The fix.** When the soft pool is built, every robot body with a colliding geom of priority above 0 gets:
    - an explicit `<pair>` with each ring capsule, for each of its colliding geoms;
    - an `<exclude>` against each ring segment, so MuJoCo's own contact between them is dropped.
  - **Each pair's numbers.**
    - solref and solimp: the ring's (K6-R1).
    - friction and condim: MuJoCo's own rule, unchanged. A priority geom's own; otherwise the larger of the two.
      So a part's `priority = 1` still sets the friction, which is what parts.md promises.
  - **Where.** The pairs are added in `parts/assemble.py`, after the robot, the range and the partner are all in the
    spec and before `spec.compile()`. The partner (added after `add_range`) is covered too. The chains are flexes,
    which can't be paired: their own stiff contact (K5 D1) stays.
  - **Measured** against a priority-1 part:

    | Scene | Without pairs | With pairs |
    |---|---|---|
    | Slam at 4 m/s | 20.8 mm | 13.8 mm |
    | Slam at 8 m/s, 5 cm box | 46.1 mm | 31.4 mm (20.4 with K6-R1) |
    | 2 mm slab, no pull | run over | 0 under |
    | 2 mm slab, 20 N pull | run over | 0 under (2 segments briefly, with K6-R1 at 1.0 r) |

    Rerunning the spike's 45-run grid with the fix cost no time: 168.1 s against 168.4 s.
  - *Why not the alternatives:*
    - giving the ring the highest priority would make the NOTE's 0.6 friction beat every part's, and the carpet's;
    - giving priority parts a stiffer default changes robots' contact with the field. That is K6-R16, queued.
  - *Cost:*
    - pairs ignore contype and conaffinity. That is harmless here: a parked ring sits at PARK_Z, 20 m under the
      field, and a soft ring is the only thing paired;
    - about 32 pairs per priority geom (2 rings × 16 capsules). The spike's intake has 8 priority geoms, so 256 pairs;
    - a model with many priority geoms builds a little slower. A test caps it (K6-R11).

- **K6-R3. Self-contacts stay, with the same ramp.**
  - The ring keeps colliding with itself everywhere except j ± 1 and the closing pair. K6-R1's ramp applies to a
    self-contact as well, since both capsules carry it.
  - The design states the exclusion rule and why: neighbors overlap at rest; j ± 2 touch only past a 90° bend,
    which is a foam pushing back.
  - **Measured** (the taco scene):
    - with the ramp: self-overlap 16.5–17.3 mm at 60 mm plates (0.8 r: 15.5–15.8), finite, and no gain in energy;
    - with j ± 2 also excluded (at 0.8 r): 15.5–16.0 mm, no better;
    - with a 60° ball-joint limit on every joint (at 0.8 r): 15.4–15.5 mm, no better.
  - *Why:* excluding more pairs removes real resistance and lets a ring fold through itself silently (the spike's
    `NSELF=8`). The joint limits measured no gain and would add 15 limit rows per ring.
  - *Cost:* a ring crushed flat in a tight nip still overlaps itself about 17 mm. The acceptance taco test bounds it
    (K6-R11).

- **K6-R4. No capsule radius or margin change.**
  - The radius is the NOTE's tube, from its diameters. A positive margin only starts the contact earlier: it makes
    the NOTE look fatter and doesn't limit how deep it sinks. A smaller collision radius plus a margin gives the same
    surface with the same axis, so it flips at the same depth.
  - *Cost:* none.

- **K6-R5. Rejected: MuJoCo 4's speculative contacts and a flex NOTE in the world.**
  - **Speculative contacts** (`mjENBL_SPECULATIVE`) are a global enable flag, and MuJoCo doesn't document them yet.
    - Measured: the squeeze and the drop were unchanged, but a slam at 8 m/s into a 1/8 in plate stopped at 2–9 mm,
      with contacts reporting 230–380 mm of penetration. That is a different, uncalibrated contact, and it would
      change every ball and robot contact in the world.
    - Static roller loads, the spike's main case, aren't speed tunneling at all.
  - **A flex NOTE in the world:** K5 D8 measured 8.5 s per simulated second for 10 NOTEs, and one parked flex slowed
    the scene 8×. Nothing has changed that.
  - *Cost:* none now. Revisit speculative contacts when MuJoCo documents them per geom.

### The safety net

- **K6-R7. A guard resets a blown-up ring and says so.**
  - **When it fires.** After each step, `SoftPool.shadow()` already computes each in-use ring's segment velocities.
    The guard reads them, at almost no cost. It fires when either happens:
    - any segment is faster than `GUARD_SPEED` (`# choice: 25 m/s`: the spike's rollers never passed 7 m/s, a robot
      drives under 5, and a launched NOTE is rigid);
    - a segment's state is non-finite.
  - **What it does.** It puts the ring back in its rest shape at its center and rotation, moving with its center's
    velocity capped at `GUARD_RESET_SPEED` (`# choice: 7 m/s`, the fastest roller surface the spike saw).
  - **The note.** It records a note, once per ring per reset (i18n, en-GB checked): "soft NOTE note.3 blew up at
    2.13 s (a segment at 140 m/s); it was reset to its rest shape, so results near then aren't trustworthy". The note
    appears where the full-pool note does: the CLI, the Test tab, scenario reports and the MCP results.
  - **MuJoCo's warning.** The world also reads `mjWARN_BADQACC` after each step, as the bench does. A rise adds a
    note: "MuJoCo found the simulation unstable at {t} s and restarted it". MuJoCo's auto reset stays as it is.
  - **A count.** `RunResult` gains `soft_resets`. Every acceptance test asserts that it is 0.
  - *Why:* a last resort that is visible, never a fix. The causes are fixed by K6-R1–R3.
  - *Cost:*
    - a guard hides the shape a blow-up would have had;
    - a 25 m/s threshold would also catch a real 25 m/s segment, which no FRC mechanism makes;
    - one more note string.

- **K6-R8. Segment speeds come from the ring's own kinematics.**
  - `Ring.speeds(data)` returns each segment's center speed from `_segments`. The guard, the acceptance tests and
    `tests/intake_grid.py` use it. Nothing reads `cvel[...,3:]` as a segment speed.
  - *Cost:* none.

### Keeping the calibration

- **K6-R6. `stiffness` and `restitution` keep their meaning; the fit is refit, and nothing else.**
  - **`FIT_CONTACT_K` is not refit.** The 25% squeeze lands within 1% at every stiffness (1000 N/m: +0.8%, 2000:
    0.0%, 6000: +0.3%, 20000: +0.1%), inside the 10% check (`test_the_rule_reproduces_both_fitted_foams`).
  - **`DAMPING_SCALES` is refit** with `tests/fit_soft_damping.py`, all four rows. The ramp catches the 1 m drop at
    1000 and 2000 N/m, so e rose to 0.338 and 0.307 before the refit. 6000 and 20000 moved under 0.5%.
  - **The restitution corners.** `test_the_rule_bounces_within_10_percent_across_the_range` is rerun at every corner
    of 1000–20000 N/m × 0.27–0.45. If a corner leaves 10% after the refit, the table becomes two-dimensional
    (stiffness × restitution, the same log interpolation in each). That rule is chosen now so the result isn't tuned.
  - *Cost:* a refit, and possibly a bigger table.

- **K6-R9. The K5 goldens: checked, and re-recorded only with a reason.**

  | Golden (tests/test_range_soft.py unless named) | Expected change | Reason, if it changes |
  |---|---|---|
  | `test_the_rule_scales_from_the_fit`: contact_k 11211.4, contact_b 64.45 | none: `RingParams` stays unscaled; the geom's solref carries K6-R1's f | — |
  | `test_the_ring_is_built_as_its_kind`: `solref == [-k, -b]`, `solimp == SOFT_SOLIMP` | yes | K6-R1: it now checks the scaled solref and the ramp solimp from the rule |
  | `FITTED`: squeeze 12.70 N and drop 0.295 at 2000; 38.10 N and 0.306 at 6000 | none: the targets stay, within 10% | — |
  | `test_the_shipped_damping_scales_are_the_fit`: the table | yes | K6-R6: the drop reaches the ramp |
  | `test_the_ring_is_stable_at_both_ends_of_its_range`: under 40 mm | tightened (K6-R11) | the bound was looser than the tube |
  | `test_a_swap_back_waits_until_the_note_is_clear_of_a_wall`: overlap 0.008 | none: rigid geometry | — |
  | `test_what_a_soft_note_costs` (SCRIMMAGE_TIMING) | rerun: pairs and the guard | new machinery; the numbers in docs/test-range.md are updated |
  | `tests/data/bench_flex_golden.json` | none: the bench flex model is untouched | — |
  | `tests/data/ledger_golden.json`, `range_ball_golden.npz`, `rangeless_golden.npz` | none: no soft piece | — |
  | 2024 range tests (`tests/test_range_2024.py`) | rerun | any change is from a NOTE turning soft near the robot; each one is listed in the PR with its cause |

  - *Cost:* none beyond the rerun. A golden that moves with no reason in this table is a bug, not a re-record.

### The acceptance suite

- **K6-R11. The soft NOTE's acceptance suite** goes in `tests/test_range_soft_robust.py`. Its scenes come from
  `k6/scenes.py`, moved into `tests/soft_helpers.py` with true speeds (K6-R8). Each scene runs at 1000, 2000 and
  20000 N/m.
  - Every case passes only if:
    - the state stays finite;
    - the ring's kinetic energy never rises above its start, except one documented limit (Task 3's ruling): at
      20000 N/m an 8 m/s slam may gain up to 8 % (`LAUNCH_GAIN_SLACK = 1.08`). It gains 6.8 % on the first step
      after the launch, from the carpet, not the wall: MuJoCo's soft friction (B = contact_b/dmin, impratio 10) lifts
      a contact sliding fast, and a rigid box hops too. Cutting the damping 10 % clears it but moves the drop's e to
      0.335, outside its 10 %;
    - no segment passes 20 m/s (`# choice`, under the guard's 25);
    - the guard never fires.
  - **The cases:**

    | Scene | Cases | Bound |
    |---|---|---|
    | Thin plates at roller loads (`press`) | 1/8 in and 6 mm plates; 25, 50, 100, 200 N; held, and dragged at 3.4 m/s | no segment center below the plate's top; squash under 21 mm (Task 3's ruling, K6-R11's drop bound: the 0.9 r fallback sends the NOTE through both plates at 200 N dragged, 1000 N/m, and 0.95 r sits next to that cliff; measured 18.3 at 2000 N/m and 20.3 at 1000 N/m, 4.7 mm short of the 25 mm tube radius, nothing through) |
    | Thin plates on hard hits (`slam`) | 1/8 in, 6 mm and 5 cm; 2, 4, 6, 8 m/s; priority 0 and 1 | never through; depth under 24 mm (`# choice: 3 mm short of the 1/8 in flip at r + T/2`; measured 23.1 at 1000 N/m, 8 m/s) |
    | A folded ring (`taco`) | plates at 120, 100, 80, 60 mm; lifts 0.3 and 1 N | self-overlap under 20 mm (`# choice`; measured 17.3) |
    | A 2 mm slab (`slab`) | priority 0 and 1; pulls 0, 5, 10, 20 N at 2 m/s | at most 2 segments under the slab at any time, and none at the end (`# choice`; measured 2, briefly, at 20 N with pairs) |
    | The drop (`drop`, the repo's stability test) | both ends of the range | deepest under 21 mm (`# choice`; measured 19.3 at 1000 N/m), down from 40 mm; still within 1 s, as now |
    | The squeeze to 0.4 in | 1000, 2000, 6000, 20000 N/m | finite and rising; the 25% point within 2% of `stiffness` × 6.35 mm |
    | Pairs | the 2024 stand-in plus the example intake | the pair count equals 32 × the priority bodies' colliding geoms; the build is under 1.2× the time without pairs |
    | The guard | a ring given 100 m/s by hand | fires once, resets, and the note and `soft_resets` say so |

  - *Cost:* about 25 s of test time (each scene 0.1–0.6 s).

### The intake example

- **K6-R10. `examples/blocks/note-intake/`, on the spike's winning design, retuned for the fixed NOTE.**
  - **The files** (the chain-climb example's shape):
    - `README.md`: the robot, the field, the question, the blocks, the files, what the grids found, and the design
      rules (thick parts the NOTE is pushed into, a ceiling, an over-under nip);
    - `note-intake-standin.toml`: the spike's stand-in kitbot at `[1.6, 7.0016, 0]`, facing the AMP-side blue WING
      NOTE;
    - `parts/intake/part.toml`: the spike's `winner/parts/intake/part.toml`, hand-written and commented, with its
      "simulator rules" comment updated (K6-R1 and R2 fix the thin-part rule; the thick parts stay as good practice);
    - `intake.test.toml`: spin the roller at 8 V, drive at about 1 m/s, wait until `piece_in intake.hopper`, stop,
      wait 1 s, expect `piece_in`. `hopper = ["intake.hopper"]` stays, though K6-R12 makes it optional.
  - **What the fix did to the spike's design.** The spike's grids were rerun with the fix on the K6 harness
    (`k6/fix_*.jsonl`, at 0.8 r, the first width tried). The fix off (`k6/off_corners.jsonl`) reproduces the spike's
    corners exactly, which checks the harness. The cases that failed at 0.8 r were then rerun at 0.9 r and 1.0 r
    (`k6/hard_w09.jsonl`, `k6/hard_w10.jsonl`):

    | Grid | Spike | Fix off (K6 harness) | Fix on, 0.8 r | Fix on, 1.0 r (failing cases rerun) |
    |---|---|---|---|---|
    | One setting at a time (44 runs, plus the 0 V control) | 44 of 44 | — | 43 of 44 (lip +1 in fails) | lip +1 in passes: 44 of 44 expected |
    | 16 corners | 14 of 16 | 14 of 16 | 11 of 16 | 3 of the 5 pass: 14 of 16 expected |
    | 40 random draws (seed 7) | 36 of 40 in `F_random.jsonl` (the spike's report says 35) | — | 34 of 40 | not rerun |
    | 0 V control | fails | — | fails | — |
    | Fastest segment, true speed | — | 16.3 m/s | 10.3 m/s | 16.3 m/s |

    - "Expected" assumes the cases that passed at 0.8 r still pass at 1.0 r. The full grid at 1.0 r is the first
      step of the retune below.
    - **Every new failure is a tight nip.** The "lip" setting moves the bottom roller toward the front roller, so
      lip +1 in turns the 1.4 in nip into 0.47 in, a 76% squeeze. At axle −0.5 in it is 0.30 in, 85%.
    - The shipped NOTE passed those only because its squash was linear: 41 N flattens it to 0.4 in. With the ramp,
      the NOTE stops on the front roller and stays on the carpet.
    - The two corners still failing at 1.0 r are both 85% squeezes at 5 V (`[-0.5, +1, 5 V, 0.5 m/s, 0°]` and
      `[-0.5, +1, 5 V, 2 m/s, +15°]`). The spike's two failures were other corners. So the count holds, but which
      corners fail moves.
    - The random draws' 6 failures at 0.8 r all have axle below −0.25 in or lip near +1 in, the same cause.
  - **The retune.** The target stays the one given: the one-setting-at-a-time grid all passing, and the corners at
    least 14 of 16. The ranges stay as the spike's: axle ±0.5 in, lip ±1 in, 5–11 V, 0.5–2 m/s, ±15°, ±6 in,
    1.5–10 mm.
    1. Run the three grids on the spike's nominal geometry at K6-R1's 1.0 r. If they meet the target, the example
       ships on that geometry.
    2. Otherwise, retune the nominal so the grid's tightest point stays a nip a NOTE can pass. The candidates, in
       this order:
       - a wider nominal nip. 1.6 in was tried at 0.8 r: it fixed lip +1 in but only 1 of the 5 corners, since the
         ±1 in lip still moves the nip ±0.9 in;
       - the front roller higher over the bottom one, so moving the lip changes the nip less;
       - a sprung front roller arm (`spring` on a hinge), as real over-under intakes have, so a tight nip opens.

       Each is a change in the design generator, then a full grid (about 8 minutes).
    3. The first that meets the target ships, and its grids are recorded in the README and the findings.
    4. If none does, the example ships with the best of them, and the README states the grid it passes. The shortfall
       is recorded as a ruling, with the tight-nip cause. The NOTE is not tuned back toward linear to pass a grid.
  - **The test,** `tests/test_examples_note_intake.py`:
    - the saved `intake.test.toml` passes as saved, with "1 of 1 made it in" and the piece table saying `in`
      (K6-R12, R13);
    - at 0 V it fails, and the NOTE is not in the bin;
    - `soft_resets` is 0;
    - the run takes under 10 s of wall time (`# choice`; the spike measured 4.1 s).
  - **The grids,** `tests/intake_grid.py`. These are the spike's three grids (one setting at a time, 16 corners, 40
    random draws, seed 7) on the example's own files, using K6-R8's speeds.
    - Run from the command line, they print the tables the README carries.
    - `test_the_intake_grids` wraps them, gated by `SCRIMMAGE_INTAKE_GRID=1`, and asserts the target.
    - CI runs it as its own job on every pull request. CI is unmetered, since the repo is public, and the job takes
      about 8 minutes.
  - **The bench file.** `examples/bench/note_intake_flex.toml`, the spike's `winner/bench/nip_flex.toml`, is fed by a
    moving carpet (K6-R14) at the approach speed instead of an inlet's velocity.
  - **Docs:**
    - docs/test-tab.md: the example, beside `chain-climb`;
    - docs/test-range.md "The soft NOTE": the ramp, the pairs, the guard, the new limits, and the cost numbers
      rerun;
    - docs/parts.md: the `priority` row says a soft NOTE's squash still applies;
    - docs/bench.md: `surface_speed`;
    - docs/measuring.md: optional deep-squeeze readings at 0.6 in and 0.4 in gaps, which would check the ramp's onset (owner question 1);
    - the kickoff findings: a "**K6 status**" under gap 3.
  - *Cost:* the retune may take more than one candidate's grid. The example's numbers are uncalibrated, as the
    NOTE's are.

### The smaller points

- **K6-R12. A test with no `hopper` counts its `piece_in` bodies as the hopper.**
  - When `Program.hopper` is None (the file names none), the feed tracker's hopper (`Outcomes._hopper`) is the set
    of bodies that its `piece_in` conditions name. The `sentence` treats the run as having a hopper.
  - `hopper = []` (cleared in Setup) still means none. The file and Setup are unchanged.
  - *Cost:* a test whose `piece_in` names a staging body counts a piece resting there as "in". That is what its
    condition says.

- **K6-R13. The piece table shows the feed outcome.**
  - For a piece that the run's feed tracker followed, the outcome column shows the tracker's outcome: `in`, `placed`,
    `stuck` or `missed`.
  - The `where` column shows where it is: "in intake.hopper"; "stuck on intake.guide"; "rest at (x, y)".
  - Pieces the tracker didn't follow keep the ledger's outcome. The scenario report's ledger table is unchanged: the
    ledger has no robot-built bins.
  - *Cost:* the two tables can differ for a NOTE in a bin. docs/test-tab.md says which one is which.

- **K6-R14. A bench plate can move its surface: `surface_speed`.**
  - `[[plate]] surface_speed = 1.5` (m/s, along the bench's +x; `# choice: the plate's own x, signed`) sets MuJoCo's
    geom `surfacevel`. A NOTE on it is carried as by a robot driving at it.
  - `velocity` on an `[[inlet]]` still sets where it starts.
  - Before the docs, it is checked on the rigid NOTE and the flex NOTE. If a flex contact ignores `surfacevel`, then
    `surface_speed` with `flex = true` is refused, with a message saying so.
  - *Cost:* a bench key; the bench's intake still has no robot pushing from behind.

- **K6-R15. The intake spike's one-off harness doesn't ship.** `tests/intake_grid.py` (K6-R10) replaces it, on the
  example's files.
  - *Cost:* none.

- **K6-R16. Queued, not done: a part geom with `priority` and no `solref` gets MuJoCo's soft default against every
  piece.**
  - Found here: a rigid NOTE or coral against such a part uses solref (0.02, 1), not RIGID_SOLREF.
  - Fixing it (a default of RIGID_SOLREF for a priority geom) changes robots' contacts with the field. So it is its
    own project, with its own goldens check, after K6.
  - *Cost:* rigid pieces keep that softness until then. Soft NOTEs are covered by K6-R2.

## The one question for the owner

1. **How hard is a real NOTE at a deep squeeze?** K6-R1's ramp width (1.0 tube radius) is an estimate: it says the
   foam is linear to about 50% squeeze and hard by about 75%. The intake example's tight-nip corners pass or fail on
   it.
   - The measurement: the docs/measuring.md squeeze test, read at a 0.6 in and a 0.4 in gap as well as at 1.5 in.
     It needs a real NOTE, two 4 in round bars, and a scale.
   - With that, RAMP_WIDTH becomes a `# source:` and the example's grid is rerun. Until then the design goes ahead
     on the estimate.

## Order and tasks

1. **K6-R8 and K6-R11's scenes:** `Ring.speeds`, the scenes moved into `tests/soft_helpers.py`, and the acceptance
   tests written first, failing on today's ring (S).
2. **K6-R1:** the ramp and the stiffness cap; the rule test; the built-ring test (S).
3. **K6-R6 and K6-R9:** the damping refit, the restitution corners, the tightened stability bound, the goldens table
   checked (S–M).
4. **K6-R2:** the priority pairs in `assemble.py`; the pair-count and build-time tests (M).
5. **K6-R7:** the guard, the BADQACC note, `soft_resets` and the notes in every report (M).
6. **K6-R12 and K6-R13:** the hopper default and the piece table (S).
7. **K6-R14:** `surface_speed` on the bench, checked on both NOTEs (S).
8. **K6-R10:** the retune, the example, its test, the grids script and CI job, the bench file, the docs and the
   findings (L).

## Rules every task follows

- Plain American English. Every user-facing string goes through i18n.t / i18n.tn, with en-GB entries where needed.
- Every new number is tagged `# source:`, `# estimate:` or `# choice:`.
- Run tests with `.venv/bin/python -m pytest`, never `uv run`. In a worktree, set PYTHONPATH to its absolute path.
- Test helpers are imported bare (`from soft_helpers import ...`), never as `from tests.x import ...`.
- Wait on a test run by its PID, never with `pgrep -f`.
- Never commit under `.superpowers/`.
