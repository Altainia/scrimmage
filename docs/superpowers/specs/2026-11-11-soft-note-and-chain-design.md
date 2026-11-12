# A soft NOTE and a hanging CHAIN (kickoff fixes K5): design

The 2024 kickoff dry run (docs/superpowers/specs/2026-11-08-kickoff-dry-run-findings.md) left four gaps that this
project closes:

- gap 3: no compliant NOTE. The two rigid stand-ins gave opposite intake answers.
- gap 9: the CHAIN is a rigid bar, and HARMONY can't be expressed.
- gap 15: range pieces have no stiffness key.
- K2's queued pinch exit-speed check: the strict xfail `test_exit_speed_against_the_bench`.

Two throwaway spikes measured the options on MuJoCo 4.0.0. Their reports are kept as this design's evidence:

- docs/superpowers/specs/2026-11-11-soft-note-spike.md (the soft-note spike);
- docs/superpowers/specs/2026-11-11-chain-spike.md (the chain spike).

Every number below that isn't from a source is tagged as an estimate or a choice, as in K2–K4. Under the owner's
standing authorization (2026-11-04: "do all remaining work my way"), every design question here is decided and
recorded with its cost.

## Goal and limits

- **Goal.** On the built-in 2024 range, a student can:
  - check in the Test tab, with no robot code, whether a floor intake picks up a NOTE. The NOTE bends and squashes
    against the roller, so the answer no longer flips between rigid stand-ins.
  - climb a CHAIN that sags under the robot, and score ONSTAGE;
  - hang a partner robot on the same CHAIN, and see the sag, tilt and HARMONY points.

  On the bench, a student's NOTE shooter squeezes a soft NOTE, and the exit speed comes from that squeeze.
- **Builder check (2026-11-11).** A fresh reader's four questions are answered in D1 (drawing), D8 (the ring's
  numbers and drawing), D9 (flex is opt-in) and D10 (the test's geometry).
- **Unchanged.** The world's integrator and timestep, the robot's dynamics, and every ball, disc and rigid piece
  give the same numbers as before. The world ledger golden stays.
- **Non-goals:**
  - a soft NOTE in flight, or real shooter wheels in the world. The world's shooter stays the analytic hand-off
    (`launcher/pinch.py`). The spike's chain pinch exit speed was 1.2–2.3× too fast.
  - robots sliding along a CHAIN toward its low point. The spike found that a thin J hook settles at a link and needs
    about 200–250 N to move (see D4).
  - a partner robot that climbs on its own. The partner hangs where it is put (D6).
  - SPOTLIT, TRAP and the MICROPHONE.
  - making the rigid NOTE cheaper (a capsule torus, or MuJoCo's `sleep`). The spike measured both; they are parked
    with their numbers, because each one changes every NOTE result to date.

## Decisions

Each one gives what was decided, why, and its cost if wrong.

### The CHAIN

- **D1. A `kind = "chain"` range element: a hanging 1D flex.**
  ```toml
  [[element]]
  name = "chain_blue_center"
  kind = "chain"
  center = [5.7441, 4.1051]     # the low point, in the field frame
  yaw_deg = 90.0                # along the chain
  span = 2.42                   # m between its two ends
  top = 1.18                    # m: the ends' height
  low = 0.7176                  # m: the low point's height, unloaded
  diameter_in = 0.5
  level = "ONSTAGE"
  ```
  - **Drawn** (builder check): a 1D flex has no geoms, and the browser view and replays draw only geoms on moving
    bodies. So each vertex body carries a visual-only bead (a sphere of `diameter_in`; contype and conaffinity 0,
    group 1), and the beads show the sag in the live view, the Test tab and replays. *Cost:* a bead chain, not
    drawn links.
  - **The model:** N + 1 point masses joined by N capsule edges (choice: N = 40, the spike's best), as a 1D flex,
    with both ends pinned to the world. A flex equality holds the edge lengths. There is no bending stiffness, so
    it runs on the world's `implicitfast` at 1 ms. The chain's length comes from `span`, `top` and `low` as a
    catenary.
  - **Its parameters** are the spike's (chain spike, "Geometry and parameters"):
    - the chain's mass, as an estimate;
    - the chain's own contact against the hook (priority 1, a large solmix, solref [0.002, 1], solimp
      [0.99, 0.999, 0.001, 0.5, 2], friction 0, so the hook's material sets μ);
    - the edge equality's solimp [0.999, 0.9999, 0.001, 0.5, 2].
  - **Checks:**
    - `low < top`;
    - `span` > 0;
    - a catenary must exist for the three numbers;
    - the ends must lie inside the field;
    - N is fixed, not a key.
  - **2024:** the six `chain_*` elements become chains. Span, top and low come from FIRST's field CAD (K4's
    `GE-24205`, the CHAIN: 2.42 m across, from 1.18 m down to its 0.72 m low point) and the manual (M 5.5). The
    STAGE legs stay obstacles, so a chain's ends are pinned in the air where the truss would hold them.
  - *Why:* MuJoCo 4 removed the cable plugin and the composite cable, and the 1D flex replaces them. Capsule links in
    one tree cost the cube of their depth, and links at N = 40 blew up in the world.
  - *Cost:*
    - Physics on the full 2024 field idles at 315 ms per simulated second instead of 149 (the spike's 6-chain
      number). That is still about 3× faster than real time.
    - The chain's mass and contact softness are estimates.
    - The 2024 world's numbers that involve the chains change. Nothing in the goldens climbs a 2024 chain today.

- **D2. A rigid chain bar that a climber can hold.** The spike found that today's 2024 `bar` (0.5 in) can't be
  climbed by the stock climber: the bar wedges in the J hook's crown, and at about 1.1 kN it slips out.
  - K5 finds the cause and fixes it, so a thin rigid bar can be climbed. The 2022 rungs are thicker and are not
    affected; their goldens stay.
  - If the cause is the hook's crown geometry meeting a thin bar, the fix is in the hook part and is pinned by a test
    on a 0.5 in bar.
  - *Why:* `kind = "bar"` stays the cheap fallback for a chain, and a thin bar is a fair student input.
  - *Cost:* none, if found. If no fix keeps the 2022 goldens, a thin bar under 1 in gets a range-check warning that
    names the problem, and this is recorded as a ruling.
    Superseded: the owner chose the rotor-inertia fix (R16).

- **D3. The climb monitor reads flex contacts and a chain's own line.**
  - A contact with a flex has no geom id on the flex side, so it is read by flex id.
  - A chain's swing axis is its horizontal end-to-end line, not its first edge.
  - Each chain is one *hold*: the climb code's name for "what the robot hangs from". It replaces the bar's rung list.
  - *Why:* the spike needed these two fixes before a hook on a chain counted.
  - *Cost:* none for bars.

- **D4. Robots stay where they hook.** A hook on a chain settles at a link and doesn't slide toward the low point.
  - The docs say so. A HARMONY test therefore puts the partner where it would end up.
  - *Why:* in the spike, neither more links nor less friction made it slide. A real roller chain on a real hook does
    slide some. Modeling that would mean a sliding hook constraint, which is new work with no measurement behind it.
  - *Cost:* two robots that would meet at the low point in a real match stay apart in the sim. The sag and tilt
    each one sees are still right for where it is.

- **D5. ONSTAGE and HARMONY points, and their signals.**
  - `[climb] rule = "off-carpet"` (2024's rule): a robot scores its hold's `level` when it touches a hold and no part
    of it touches the carpet. The old `lowest-rung` stays for 2022.
  - `[climb] harmony = 2` means points for each extra robot on a hold: 2 per robot beyond the first, both off the
    carpet (source: M Table 6-2: HARMONY 2).
  - The signals, named like the existing climb signals:
    - `climb.hold`: which hold the robot hangs from, as a number (signals are numbers): its rank in the range's
      element order, 1 and up, or 0 for none. This is how `climb.level` works already;
    - `climb.hold.<hold>.robots`: how many robots are on that hold and off the carpet (the robot plus a hanging
      partner);
    - `climb.harmony`: HARMONY points so far.
  - The match points (K3) add the HARMONY points in the endgame, as they do ONSTAGE.
  - 2024.toml switches to `off-carpet` with `harmony = 2`, and the header comment drops HARMONY from "not
    expressible".
  - *Cost:* HARMONY is counted for the robot and its partner only, since there are no other robots.

- **D6. A partner that hangs on a chain.**
  - `[partner] hang = { hold = "chain_blue_center", along = 0.5 }` puts the partner robot hanging from that chain
    at `along` meters from the low point (signed, along the chain's yaw). Its frame is level and off the carpet, and
    it is held by a weld to the nearest chain vertex at the partner's top center.
  - In Setup, the Partner robot box gains **Hangs on** (a hold list and "no"), **along** and **across** (an optional
    sideways offset, `across` in the file). It faces along its chain.
  - Measured: its weight pulls the chain's slack into a taut V at its vertex, so the rest of the chain rises (from
    0.72 m to about 0.97 m at the low point, with a partner 0.9 m along). The other robot's hooks meet a higher,
    tauter chain and it hangs about 4 cm higher. A partner starting inside the field (a STAGE leg, the core) is
    refused.
  - Its mass is the partner's own (the stock partner's, about 60 kg).
  - The partner signals stay. `partner.height` reads its lift from where it hangs.
  - *Why:* the kickoff question is "does my climb still work with a partner on our chain, and do we tip?". That
    needs a load on the chain, not a partner that climbs.
  - *Cost:* the partner doesn't swing up from the carpet, and its hook is a weld, not a hook.

### The soft NOTE

- **D7. A piece's squeeze: `stiffness` and `restitution` on `[piece.*]` (gap 15).** `stiffness` already exists in
  `pieces.py` (the builder check found it), so this mostly adds `restitution`. `restitution` is used only by soft
  pieces, so rigid pieces keep their numbers.
  - `stiffness` (N/m) is how hard the piece pushes back per meter of squeeze, measured at 25% of its thickness. It is
    the same number `pinch.py` and the bench use.
  - `restitution` (0–1) is its bounce off the carpet from a 1 m drop.
  - The `2024-note` preset gets:
    - `stiffness = 2000  # estimate: the soft-note spike's "soft" foam`;
    - `restitution = 0.3  # estimate`.

    Both are labeled uncalibrated.
  - `docs/measuring.md` gains a squeeze test (a known weight on a NOTE between two boards, and the squash measured)
    and a drop test (filmed against a ruler).
  - *Why:* soft against firm foam (3×) moves the exit speed 50–75%. That number has to be the student's, and visible.
  - *Cost:* none until measured. Results that rest on it are labeled uncalibrated.

- **D8. In the world: rigid at rest and in flight, soft near the robot.** A piece kind with `soft = true` (the
  `2024-note` preset sets it) is rigid as it is today until it comes near the robot.
  - **Turning soft:** a NOTE is swapped for a soft one when its center is within `SOFT_REACH` (choice: 0.25 m) of a
    colliding robot geom, or is predicted to be within it after the next step (its distance less its closing speed
    times the timestep; Task 6 review, the owner's request: it guards a timestep above today's 1 ms limit).
  - **The soft NOTE:** a ring of 16 capsule segments joined by ball joints. This is the soft-note spike's candidate (b).
  - **Its numbers come from a rule, not a fit at build time** (builder check). The spike fitted the ring once, at its
    "soft" and "firm" foams. The world scales from that fit:
    - the joint bending stiffness and the contact stiffness are in proportion to `stiffness`;
    - the contact damping is the closed form for `restitution` (a damped spring's bounce:
      ζ = −ln e / √(π² + ln² e));
    - a test checks that the rule reproduces both of the spike's fitted foams within 10% on its drop and squeeze.

    Building the world therefore never runs a fit, never needs the flex code or `discrete`, and changing a number in
    Setup doesn't stall. *Cost:* away from the two fitted foams the rule is an extrapolation.
  - **The swap keeps the pose and momentum.** The spike's error was 3e-9 kg·m/s. The soft rings come from a small
    pool (choice: 2) parked under the floor, so the model doesn't change at run time.
  - **Turning rigid again:** it is swapped back when it has been more than `SOFT_REACH` from the robot for 0.25 s
    (choice), or when the shooter launches it.
  - **The shooter stays the analytic hand-off.** `pinch.py` launches a rigid NOTE with the exit velocity it computes
    from `stiffness`.
  - **A full pool:** when a third NOTE comes near, it stays rigid. A note says so, once per run.
  - **Possession and goals (K3)** treat a soft NOTE as the same piece: same name, and same counts.
  - **Drawn as one piece:** a parked ring, and a rigid stand-in parked while its ring is in use, sit under the floor,
    and the view and replays skip bodies parked there. A viewer sees one NOTE that bends, never two.
  - The rigid stand-in shapes stay as they are, so balls, discs and NOTEs far from a robot give today's numbers.
  - *Why:* the spike's flex can't go in the world (10 NOTEs: 8.5 s per simulated second, and even one parked flex
    slows the scene 8×). The capsule ring stays on `implicitfast` at 1 ms, and every soft candidate pulled the NOTE
    in at both axle heights, where the rigid stand-ins flipped.
  - *Cost:*
    - The ring's squash is a MuJoCo soft contact, scaled by mass. It is good for "is it picked up, and where does it
      go", not for forces or exit speeds.
    - The swap is new machinery in `testrange/balls.py`, which assumes every slot is a free joint (M–L).
    - The 0.25 m reach and the 0.25 s are choices.
    - Measured (Task 6 review fixes, the 2024 wing world, wall seconds per simulated second, best of 3 on the
      development machine): idle with 0, 1 and 2 soft NOTEs 0.167, 0.205 and 0.155 (a resting ring sleeps); with the
      robot pushing them at 2 V, 0.172, 0.539 and 0.692, against 0.133 for the two pushed rigid. So a pushed soft NOTE
      adds about 0.3 s per simulated second (the spike's 0.06 was one ring alone, at rest): two in front of a robot
      still run faster than real time here, but a slower computer, or a Phoenix 6 robot in real time, may not.

- **D9. On the bench: a flex NOTE, opt-in, marked experimental.** A bench piece with `flex = true` is a tetrahedral
  flex torus on MuJoCo's `discrete` integrator with Newton, at 0.5 ms. Its mesh is the spike's 16×6.
  - **Opt-in** (builder check): the world's `soft` doesn't change the bench. A bench file that uses the `2024-note`
    preset stays rigid, as today, unless it says `flex = true`. Every existing bench test and example keeps its
    numbers, and `bench_sweep` and `bench_compare` stay fast. The docs say a flex run costs about 100× a rigid one,
    and suggest sweeping on the rigid NOTE first, then confirming on flex.
  - **In `[flight]`:** a flex NOTE flies rigid. When it leaves the last mechanism geom, it is handed off as a rigid
    NOTE with the flex's center-of-mass velocity and spin, as the world's shooter does.
  - Its E is fitted to the piece's `stiffness` by a simulated squeeze test, and its damping to `restitution` by a
    simulated drop. The fit runs on first use and is cached by the two numbers. The shipped preset's fit ships with
    it, so it costs nothing at first.
  - The bench's report labels a flex result "experimental (MuJoCo flex)".
  - Version: `mujoco>=4.0,<5` stays, and a golden flex shot catches a change in `discrete`.
  - *Why:* fed the same squeeze, the flex and `pinch.py` agree within 4–13%, so the bench checks the world's shooter
    numbers.
  - *Cost:*
    - The bench gets a second integrator path. It runs `implicitfast` at 0.1–0.25 ms today.
    - A shot costs 0.26–0.6 s, and an intake pass about 7 s of wall time per simulated second.
    - MuJoCo calls `discrete` "new, under active development, and subject to change".

- **D10. K2's pinch check: the flex against `pinch.py`, on the spike's geometry.**
  - **A new test** (builder check), on a new bench file `examples/bench/note_pinch_flex.toml` with the spike's
    scene:
    - a 1.5 in gap (25% squeeze, where E is fitted);
    - wheels held at speed;
    - 3000 and 5000 rpm.

    A flex NOTE lands within 15% of `pinch.py` (choice; the spike measured 4–13%).
  - **The old strict xfail stays** on the dry run's `note_shooter.toml` (37.5% squeeze, wheels that slow down),
    with its reason updated: a rigid NOTE on the bench takes `stiffness` at each of 8 contact points per wheel. That
    rigid-piece limit is listed under "Known limits" in docs/bench.md.
  - *Why:* the flex is nonlinear past its fit point, and the old file's wheel slowdown is a separate question. A
    test there would become open-ended tuning.
  - *Cost:* a rigid piece on the bench still squeezes harder than its `stiffness` says.

### Docs, examples and the findings

- **D11.**
  - docs/climbing.md:
    - the chain element, the hold, `off-carpet`, HARMONY and the hanging partner;
    - drop the "Chain and rope aren't modeled" line, and keep rope as not modeled;
    - add the D4 limit.
  - docs/test-range.md: the soft piece keys and how D8 works.
  - docs/bench.md: the flex NOTE.
  - docs/measuring.md: the squeeze and drop tests.
  - `examples/blocks/` gains `chain-climb` (ONSTAGE on `chain_blue_center`, and with a hanging partner, HARMONY)
    and an intake example on a soft NOTE (dropped 2026-11-12: see the findings, gap 3's K5 status).
  - `examples/bench/` gains a flex NOTE shot.
  - The findings get a "**K5 status**" note under gaps 3, 9 and 15, and under K2's pinch item.

## Order and tasks

1. **D2:** the thin-bar hook fix, first, since it touches the hook every chain task uses (S–M).
2. **D1 + D3:** the chain element, its beads, and the monitor reading it (M).
3. **D5 + D6:** the points, the signals and the hanging partner, in the config and Setup (M).
4. **D7:** the stiffness and restitution keys, and the measuring docs (S).
5. **D8:** the soft swap in the world, its scaling rule, and parked bodies left out of the view (L).
6. **D9 + D10:** the bench flex NOTE and the pinch test (M–L).
7. **D11:** the 2024 range, the examples, the docs and the findings (M).

## Rules every task follows

- Plain American English. Every user-facing string goes through i18n.t / i18n.tn, with en-GB entries where needed.
- Every new number is tagged `# source:`, `# estimate:` or `# choice:`.
- Run tests with `.venv/bin/python -m pytest`, never `uv run`. In a worktree, set PYTHONPATH to its absolute path.
- Test helpers are imported bare (`from cad_helpers import ...`), never as `from tests.x import ...`.
- Wait on a test run by its PID, never with `pgrep -f`.
- Never commit under `.superpowers/`.
