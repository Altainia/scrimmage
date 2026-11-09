# Shooting and scoring a NOTE (kickoff fixes K2): design

The 2024 kickoff dry run (docs/superpowers/specs/2026-11-08-kickoff-dry-run-findings.md) ranked this its top gap:
a team can't shoot a NOTE at the SPEAKER and count it. Four places refuse a piece that isn't a ball or a disc, and
`launcher aim` can't aim at a goal entered going up. This project removes all four. Under the owner's standing
authorization (2026-11-04: "do all remaining work my way"), every design question here is decided and recorded with
its cost.

## Goal and limits

- **Goal.** On a 2024 range, a student can:
  - shoot a rigid NOTE from the robot's shooter;
  - watch it fly with drag that depends on how it faces the air;
  - see it count in the SPEAKER or the AMP;
  - ask `launcher aim` for a SPEAKER table.
  The bench answers "does it reach the SPEAKER" for a NOTE.
- **Rigid only.** The NOTE stays a rigid stand-in. A soft NOTE is K5. Every new number that isn't from a source is
  tagged `# estimate:`. Results that rest on them are labeled uncalibrated, as the disc's are today.
- **Balls and discs unchanged.** Their launch, flight and scoring give the same numbers as before. The world ledger
  golden and `AimTable.java` stay as they are.
- **Non-goals:**
  - lift on a ring (no source for its coefficients);
  - a side-roller (rim-pinch) NOTE shooter. The disc shooter's `pinch` layout already models that mechanism, so it
    isn't new physics. Wiring it to rigid kinds can come later;
  - real MuJoCo wheels in the world;
  - a "whole piece inside" scoring rule.

## Decisions

Each one gives what was decided, why, and its cost if wrong.

- **D1. Goals count any piece kind (survey a1).**
  - Drop the "goals count balls and discs" refusal (`testrange/spec.py` `_counted`). Use the same rule for the
    default kind pick (`balls.py`): the first ball or disc kind, else the field's first kind.
  - Scoring stays a center crossing of the scoring surface (`Surface.crossed`).
  - The funnel throat check uses the piece's passing width: for a rigid kind, the smallest of its bounding-box
    sides. A throat narrower than the next-larger side becomes a warning ("may pass only on edge"), not a refusal.
  - Why: the crossing test is pure geometry and already blind to the kind. *Cost:* a ring can count with its rim over
    the edge of the opening. The rim bars collide, so this is rare. A `counts = "whole"` rule can be added later.
- **D2. Drag on rigid pieces, shared by the world and the bench (survey c1).**
  - Name and place: `scrimmage/launcher/rigid_aero.py`, `forces(kind, rho, vel, rot, omega) -> (force, torque)`.
  - The model is axis-projected drag with no lift.
    - The air sees the piece's projected area along the relative velocity. That area comes from the piece's
      bounding box in its body frame: for each face, its area times |n̂·v̂|.
    - Drag is −½ρ·Cd·A(v̂)·|v|v.
    - `Cd` comes from a new optional `[piece.*]` key `drag_coefficient`. The default is 1.17, the flat-plate value
      (Hoerner) already cited in `disc/presets.toml`, tagged as the estimate that it is.
    - An optional `spin_damping` torque is −c·ω. Its default is 0, so the piece keeps its spin, as balls do now.
  - The world's `Balls.apply_flight_forces` uses it for rigid slots. The bench `[flight]` uses it through one
    6-DOF integrator generalized from `disc/flight.py`: `scrimmage/launcher/flight6.py`, `fly(...)`. The disc keeps
    its Hummel force function inside that integrator.
  - Why: a NOTE's face area and edge area differ by about 5.5×, so a constant area can be off by meters. With no
    lift, the model is honest about what's unknown. *Cost:* a flat, backspun NOTE really glides further. Bench and
    range answers will come up short until someone measures. The docs say so next to the result.
- **D3. The bench takes the range's NOTE.**
  - Bench `[piece]` accepts `preset = "2024-note"` and `shape = "ring"`, with the same piece builder as the range.
  - The dry run's solid-disc stand-in goes away. Bench `[flight]` stops warning for rigid pieces.
  - `[flight]` can name a goal from a range file: `goal = { range = "<file>", name = "speaker_blue" }`, with the
    shot placed by `at = [x, y, z]` and `heading_deg`. A landing then reports "scored" or "missed" through
    `Surface.crossed`.
- **D4. `launcher aim` aims at the goal's scoring surface (survey d1).**
  - The robot stands on the goal's approach line: back from the goal center along the horizontal part of the scoring
    surface's normal. For a horizontal surface (the hub), that is the goal's heading, exactly as now.
  - A shot scores when it crosses the surface in the entry direction, inside the opening (a ball's center at least
    its radius inside; a rigid piece's bounding-box corners all inside, as T3 ruled), without first hitting an
    `[[obstacle]]` or one of the goal's plates. The obstacle check is sampled, not swept: at each of the flight's
    1 ms samples, a ball's sphere (or a rigid piece's center and turned corners) against each oriented box.
  - Upward goals take the lowest apex that scores; downward goals keep today's highest-apex rule.
  - Speed: an angle's crossing curve doesn't depend on distance, so it's computed once per angle. The flight stops
    at the crossing or the floor. The goal is 5 SPEAKER distances in under 15 s, down from 144 s.
  - Rigid kinds aim with `flight6` and D2's drag. Balls keep their own flight.
  - A non-monotone offset (a narrow window under a roof) is handled by bracketing on a coarse rps grid before
    bisecting.
  - *Cost if the hub path changes:* `test_aim_table_is_current` fails. The hub's numbers must come out identical,
    and that test is the pin.
- **D5. A face-pinch shooter for rigid pieces (survey b1).**
  - A new analytic model, `scrimmage/launcher/pinch.py`: a top wheel and a bottom wheel squeeze the piece's two faces.
    - Planar, in the launch plane.
    - The piece is a rigid slab: its thickness is the passing width and its length is the size along the path.
    - The squeeze is the tread's `wheel_stiffness` in series with a new `[piece.*] stiffness` (N/m). The 2024 NOTE
      gets a cited estimate.
    - It reuses `launcher/contact.py` friction and damping, plus `ShaftModel`, and copies the disc launcher's
      substep, energy ledger and jam rule.
    - Its output is exit speed, pitch spin and exit slip.
  - A new stock part, `pinch_shooter`: two shafts, their wheel diameters, the gap, the exit angle, and an optional
    tilt joint, like the launcher's hood.
  - `[shooter] kind = "piece"` with `piece = "<kind name>"`. It is refused unless the part is a `pinch_shooter`.
    It uses a one-lane ball path, as discs do.
  - The exit orientation comes from the part's channel.
  - Check against the bench: on the dry run's `note_shooter.toml` geometry, the analytic exit speed is within 15% of
    the bench's MuJoCo-contact exit speed.
  - Why a new model rather than the NOTE-as-disc stopgap: a disc has no hole, can't be fed by stations, gets pinched
    on the wrong faces, and gets frisbee lift. *Cost:* the size (L) and one more stock part. The foam stiffness is a
    guess until K5's soft NOTE checks it.

## Order and tasks

Each task is one reviewed unit and gets the full suite.

1. **D1** goals count any kind (S).
2. **D2** `rigid_aero` plus `flight6`, used for rigid slots in the world. Goldens unchanged (M).
3. **D3** bench NOTE preset and ring shape, rigid `[flight]`, and a goal from a range (M).
4. **D4** aim at the scoring surface, with the speed-ups. AimTable unchanged (M).
5. **D5** `pinch.py`, the `pinch_shooter` part, and `[shooter] kind = "piece"`, checked against the bench (L).
6. The dry run again: the SPEAKER scored by a NOTE from a pinch shooter, plus an AMP shot and a SPEAKER aim table.
   Then add `ranges/2024.toml` from the dry run's work file. The docs (test-range.md, bench.md, launcher.md) lose
   their "balls and discs only" lines.

## Rules every task follows

- Messages go through `i18n.t`, with table and key names as placeholders. Keep test_i18n_calls, test_i18n_sweep and
  test_en_gb_catalog green, with British spellings in en-GB.
- New report lines use the unit helpers (test_units_sweep). New Python files that print measured values go into its
  `PY` list.
- New preset numbers carry `# source:` or `# estimate:` (test_launcher_inputs).
- American spellings and plain words in docs and messages.
- `.venv/bin/python -m pytest`, never `uv run`.
