# K5 spike: a soft 2024 NOTE in MuJoCo 4.0.0

Date: 2026-11-11. MuJoCo 4.0.0 (Python 3.14, the scrimmage venv), one core per run. The repo was not changed.
All scripts and raw numbers are in this folder; `./run_all.sh` reruns everything (about 7 minutes) and `tables.py`
prints the tables below from the JSON files.

## The answer in short

- **World: a ring of 16 capsules joined by ball joints (candidate b), but only near a mechanism.** NOTEs at rest or in
  flight stay rigid. When one comes near a robot's intake or handler, a pooled soft NOTE is swapped in.
  - The chain runs on the world's own integrator (`implicitfast`, 1 ms) and is stable there, and at 2 ms and 4 ms.
  - It has 51 dofs. One alone costs 0.064 s of wall time per simulated second; 10 resting cost 0.87 s/s.
  - The swap keeps momentum exactly. A parked chain adds about 0.02 s/s.
  - The world's shooter stays analytic (`launcher/pinch.py`), because the chain's pinch exit speed is not
    trustworthy (see the pinch case).
- **Bench: a tetrahedral flex torus (candidate a).** It needs `integrator="discrete"`, a MuJoCo integrator that is
  still experimental.
  - It is the only candidate whose pinch answer agrees with `pinch.py` fed the same foam: 8.3–8.6 m/s against
    7.95 m/s for the soft foam, and 12.7–15.0 against 13.3 for the firm one.
  - A shot costs 0.3–0.6 s of wall time.
  - It is far too slow for the world: about 0.8 s/s per NOTE, and the cost stays even when the NOTE is parked.
- **Rigid stand-ins can't answer intake questions.** Their result flips when the roller moves 0.25 in. The ring is
  stuck at both heights; the disc is stuck, then pulled in; the torus is pulled in, then stuck. Peak contact forces
  reach 6–75 kN. Every soft candidate pulls the NOTE in at both heights, with 19–256 N on the roller.
- **Side finding.** scrimmage's own rigid NOTE (24 mesh slices) is the costliest rigid shape.
  - Ten resting cost 0.53 s/s, and five in a pile cost 2.8 s/s.
  - A 24-capsule torus costs 0.24 s/s for ten and 0.15 s/s for the pile.
  - With MuJoCo's `sleep` flag, ten resting NOTEs cost 0.005 s/s.

## Candidates and how their numbers were chosen

| key | what it is | dofs | integrator |
|---|---|---|---|
| `rigid_ring` | scrimmage's NOTE: `kind_preset("2024-note")`, 24 convex slices through `kind_geoms`, `rigid_contact` (solref 4 ms, solmix 1e6) | 6 | implicitfast |
| `rigid_disc` | a solid cylinder, 14 × 2 in, same contact (the dry run's bench disc) | 6 | implicitfast |
| `rigid_torus` | a round-section torus hull: 24 capsules (r = 1 in) in one body, same contact | 6 | implicitfast |
| `soft_torus` (c) | `rigid_torus` with a soft contact: negative solref (−k, −b), solimp d = 0.2 | 6 | implicitfast |
| `chain` (b) | 16 capsule segments, ball joints with bending/torsion stiffness `kj` and damping `bj`, the loop closed by a `connect` equality, the first/last pair excluded from contact; squash by the same soft contact as (c) | 51 | implicitfast |
| `flex3d` (a) | a direct `flexcomp dim=3 dof=full`: 24 sections × (octagon + center), 216 vertices, 576 tets. Built by splitting prisms with the smallest-index rule so the tets fit together. Flat top and bottom faces 2 in apart. Saint Venant–Kirchhoff `young`, `poisson` 0.3, Rayleigh `damping`; contact solref 2 ms, radius 0.5 mm | 648 | **discrete** (Newton) |

Shared numbers:
- mass 0.235 kg;
- the round tube: R = 6 in, r = 1 in;
- foam friction 0.6 (`[surface.foam]`);
- carpet 1.0 / 0.01 / 0.001 (`parts/field.py`);
- 60A tread 0.7 for the shooter, 35A 0.95 for the intake;
- elliptic cone, impratio 10, Newton, 1 ms (the world's options, `parts/assemble.py`).

**Foam stiffness: a stated estimate.** The NOTE lies flat between two 4 in rollers placed on a line across its
middle (two rim crossings per roller). Squeezed from 2 in to 1.5 in (25 %), it pushes back:
- **soft: 12.7 N per roller.** This is scrimmage's preset `stiffness = 2000 N/m` × half the 0.5 in squeeze, so the
  soft models and `pinch.py` start from the same foam.
- **firm: 3 × that, 38.1 N**, to show how much the answers depend on the foam.

Each soft candidate's stiffness was fitted to that squeeze (`calibrate.py`, `scenes.squeeze`, rollers closing
slowly, no gravity).

**Damping: an estimate too.** Dropped flat from 1 m, the NOTE rebounds with e ≈ 0.3 (about 9 cm). The damping
was bisected to that.

| key | fitted parameters |
|---|---|
| soft_torus soft / firm | k = 1702 / 5106, b = 18.2 / 31.9 (MuJoCo's negative-solref units) |
| chain soft / firm | k = 11 211 / 33 634, b = 64.5 / 113; kj = 0.103 / 0.319 N·m/rad, bj = 0.0008 / 0.0014 |
| flex3d soft / firm | **E = 18.7 kPa / 58 kPa**, β = 0.0025 / 0.0014 s |

`kj = E I / ℓ`, with I = π r⁴/4, ℓ the segment's length, and E from the flex fit. `bj` gives a damping ratio of
0.3 for one segment.

An E of 19–58 kPa is soft polyurethane-foam territory. That is plausible for a "rather squishy" NOTE, but it is
not measured.

**Three things learned while fitting:**
1. **MuJoCo's soft contact is not a spring.**
   - A static load sees d/(1−d) times the stiffness an impact sees.
   - The force also scales with the contact's effective mass, so the chain's light segments need k 6.6 times the
     solid torus's for the same squeeze.
   - With the default d = 0.95 (a 20× gap), a NOTE fitted to the squeeze sank 155–390 mm into the carpet on the
     drop. d = 0.2 (a 1.25× gap) fixed that, and is what (b) and (c) use.
2. **The flex's E depends on the mesh.**
   - At the same E, the squeeze gives 19.4 N on a 16×6 mesh, 12.9 N on 24×8, and 10.4 N on 32×10.
   - Fit E per mesh. The 16×6 mesh refitted to E = 12.2 kPa gives the same pinch within 2 % (table below).
3. **One warning, in an early trial.** At E = 25 kPa, MuJoCo warned that the flex stiffness is "too ill-conditioned
   for the effective-metric block preconditioner" at t = 0 (`MUJOCO_LOG.TXT`). None of the final runs raised a
   warning.

## MuJoCo 4.0 flex and integrator status (from the shipped docs and changelog)

- **Flex elasticity requires `discrete`.** Under `implicitfast` it is an engine error, reproduced here: "flex
  elasticity is no longer integrated implicitly under integrator='implicit' and 'implicitfast': set
  integrator='discrete', or 'Euler' with a small timestep".
- **`discrete` is still experimental.** computation/index.rst says "new (September 2026), under active
  development, and subject to change". modeling.rst says it is being trialled as "a candidate to replace
  `implicitfast` as the recommended integrator". 4.0 changed it further:
  - Newton is 1.4–3× faster on flex;
  - flex attached to articulated bodies is supported under Newton;
  - edge stiffness and damping are rejected under `discrete`;
  - the elastic constants of non-interpolated flexes changed, so they are stiffer.
- **Why not the other flex kinds.**
  - A 2D shell torus would be hollow and buckle, not squash like foam.
  - A `trilinear` or `quadratic` flex can't bend a ring locally.
  - A 1D closed rod (`flexcomp type="circle"`, circle section) bends but its cross-section can't squash, and it also
    needs `discrete`.
  - So the 3D tetrahedral torus is the one to test.
- **Edge equality constraints instead of elasticity.** These do work under `implicitfast`, but the docs call them
  "typically softer than the material they stand for". They weren't tried.
- **Sleep.** MuJoCo 4 has a `sleep` flag. Under `discrete` it needs islands, which are on by default.

## Case 1: pinch shooter

The setup:
- two 4 in wheels (cylinders on hinges), each held at a fixed speed by a stiff velocity servo on a 5 kg·m² armature
  (the droop is under 0.002 %);
- a 1.5 in gap;
- the NOTE fed flat at 2 m/s, with gravity off (as in `pinch.py`);
- the exit read when the NOTE's center is 10 in past the axles.

The spin is about −y, positive for backspin (`pinch.py`'s sign). The `pinch.py` rows use the same wheels, gap,
tread (60A, mu 0.7) and feed, with a 1 kg·m² flywheel on each Kraken so the wheels barely droop.

| candidate | rpm | exit m/s | ÷ surface | spin rad/s | angle ° | peak N, top wheel | deepest contact mm | exit at 0.5 ms | wall s/shot |
|---|---|---|---|---|---|---|---|---|---|
| rigid_ring | 3000 | 16.33 | 1.02 | 6.9 | −1.7 | 101 456 | 8.7 | – | 0.006 |
| rigid_ring | 5000 | 26.83 | 1.01 | −17.1 | −10.4 | 146 530 | 10.8 | – | 0.004 |
| rigid_disc | 3000 | 14.96 | 0.94 | 0 | 0 | 27 330 | 6.3 | – | 0.003 |
| rigid_disc | 5000 | 24.94 | 0.94 | 0 | 0 | 32 586 | 6.3 | – | 0.002 |
| rigid_torus | 3000 | 15.15 | 0.95 | 0 | 0 | 55 578 | 6.4 | – | 0.004 |
| rigid_torus | 5000 | 26.06 | 0.98 | 0 | 0 | 61 108 | 6.4 | – | 0.003 |
| soft_torus soft | 3000 | 13.74 | 0.86 | 0 | 0 | 184 | 6.4 | 13.69 | 0.006 |
| soft_torus soft | 5000 | 19.88 | 0.75 | 0 | 0 | 310 | 6.4 | 19.76 | 0.005 |
| soft_torus firm | 3000 | 14.95 | 0.94 | 0 | 0 | 320 | 6.4 | 14.94 | 0.005 |
| soft_torus firm | 5000 | 23.32 | 0.88 | 0 | 0 | 532 | 6.4 | 23.15 | 0.004 |
| chain soft | 3000 | 13.04 | 0.82 | 0 | 0 | 87 | 6.4 | 13.04 | 0.039 |
| chain soft | 5000 | 17.95 | 0.68 | 3.6 | −0.1 | 139 | 8.2 | 18.06 | 0.034 |
| chain firm | 3000 | 15.00 | 0.94 | 0 | 0 | 129 | 6.4 | 15.02 | 0.033 |
| chain firm | 5000 | 22.11 | 0.83 | 0 | 0 | 216 | 6.4 | 22.21 | 0.027 |
| **flex3d soft** | 3000 | **8.27** | 0.52 | −0.5 | 0.3 | 54 | 2.9 | 7.85 | 0.60 |
| **flex3d soft** | 5000 | **8.55** | 0.32 | −2.2 | 0.1 | 112 | 2.8 | 8.00 | 0.54 |
| **flex3d firm** | 3000 | **12.65** | 0.79 | 0.7 | −0.2 | 111 | 4.2 | 12.31 | 0.46 |
| **flex3d firm** | 5000 | **15.04** | 0.57 | −2.1 | 0.5 | 171 | 4.3 | 13.92 | 0.41 |
| flex3d soft, 16×6 mesh (E refit) | 3000 | 8.38 | 0.53 | 0.6 | 0.3 | 73 | 2.2 | | 0.28 |
| flex3d soft, 16×6 mesh (E refit) | 5000 | 8.53 | 0.32 | 1.3 | −0.1 | 112 | 2.6 | | 0.26 |
| **pinch.py, 2000 N/m** | 3000 | **7.95** | 0.50 | 0 | 0 | | | | |
| **pinch.py, 2000 N/m** | 5000 | **7.95** | 0.30 | 0 | 0 | | | | |
| **pinch.py, 6000 N/m** | 3000 | **13.30** | 0.83 | 0 | 0 | | | | |
| **pinch.py, 6000 N/m** | 5000 | **13.32** | 0.50 | 0 | 0 | | | | |

What this shows:
- **Rigid stand-ins with real wheels are meaningless here.** A 0.25 in interference on each side becomes 27–147 kN.
  The NOTE leaves at the wheels' surface speed, and the sliced ring chatters (−10°, −17 rad/s at 5000 rpm). This is
  why the world uses the analytic pinch.
- **The flex agrees with `pinch.py` given the same foam.**
  - Soft foam: 8.3/8.6 m/s against 7.95 m/s, 4–8 % apart. Firm foam: 12.7/15.0 against 13.3, −5 % and +13 %.
  - Both are grip-limited: exit speed barely changes with rpm.
  - Halving the timestep moves the flex 3–7 % lower, so 1 ms is close but not converged.
- **The soft-contact candidates (b, c) shoot 1.2–2.3× faster than the flex (1.6–2.3× with the soft foam), and faster with rpm.** The cause is the
  contact damping `b`. It was fitted to the 4.4 m/s drop, and in the pinch it adds normal force in proportion to how
  fast the wheel presses in. `chain_vs_flex.py` shows it:

  | chain, soft squeeze | 3000 rpm | 5000 rpm | drop e |
  |---|---|---|---|
  | b = 64.5 (drop fit) | 13.0 | 18.0 | 0.29 |
  | b = 19.3 | 10.5 | 13.6 | 0.64 |
  | b = 6.45 | 8.6 | 10.2 | 0.50 |
  | flex, for comparison | 8.3 | 8.6 | 0.31 |

  A single contact damper can't match both the drop and the flex's pinch. Lowering the impedance to d = 0.05 changed
  nothing (`chain_vs_flex.json`).
- **The flex depends on its damping too, but less.** Its pinch speed is 7.4/7.6, 8.3/8.6 and 10.7/11.8 m/s for
  β × 0.3, × 1 and × 3 (`flex_beta.json`).
- **Spin and angle.** With equal wheels there is no spin, as `pinch.py` says (the flex shows ±2 rad/s noise).
  The exit angle is within ±1° except for the sliced ring.
- **Compared with the October spike.** It found exit speeds of 0.81–0.85 of surface speed, and Chrono FEA gave 0.64.
  Here the flex gives 0.52/0.32 (soft) and 0.79/0.57 (firm). That spike's E is unknown, and the ratio follows E, so
  the numbers can't be compared directly. A shot now costs 0.26–0.6 s, against about 0.8 s then. `discrete` is still
  required and still marked experimental.

## Case 2: floor intake

The setup:
- a 50 kg carriage driven at 1 m/s;
- a 2 in front roller turning at 20 rps (3.2 m/s surface) so its bottom pulls toward the robot;
- a second roller 8 in behind, a ceiling 1.25 in above the axles, and a back stop 20 in behind;
- the NOTE flat, its near edge 3 in ahead of the roller;
- the outcome read after 1.5 s of driving.

Outcomes:
- **in**: held at the stop, moving with the robot;
- **stuck**: under or against the roller but not carried;
- **no grip**: the roller never pressed it (under 1 N).

The brief's "3 in" is the axle height (the roller's bottom at the NOTE's top), and 2.5 in is the dry run's
height (0.5 in squeeze).

| candidate | axle 2.5 in | axle 2.75 in | axle 3.0 in | front roller peak N (2.5 / 2.75) | NOTE center's peak height, in (2.5) | wall s per sim s |
|---|---|---|---|---|---|---|
| rigid_ring | stuck | stuck | in* | 74 891 / 55 388 | 4.56 | 0.21 |
| rigid_disc | stuck | **in** | no grip | 8 194 / 6 452 | 4.65 | 0.07 |
| rigid_torus | **in** | stuck | no grip | 37 071 / 28 227 | 4.17 | 0.13 |
| soft_torus soft | in | in | no grip | 108 / 76 | 2.83 | 0.13 |
| soft_torus firm | in | in | no grip | 256 / 215 | 3.41 | 0.14 |
| chain soft | in | in | no grip | 39 / 22 | 1.69 | 0.73 |
| chain firm | in | in | no grip | 118 / 60 | 2.27 | 0.65 |
| flex3d soft | in | in | no grip | 24 / 19 | 1.51 | 7.0 |
| flex3d firm | in | in | no grip | 63 / 51 | 2.47 | 7.3 |

\* The sliced ring at 3.0 in touches at zero gap and gets a 37 kN contact spike: a contact artifact, not a grip.

What this shows:
- **The rigid stand-ins disagree with each other, and each flips with 0.25 in of roller height.** This is the dry
  run's symptom, reproduced. The cause is kilonewton contact forces from the interference: the NOTE is levered up to
  a center height of 4.2–4.7 in.
- **Every soft candidate pulls the NOTE in at both squeezes.** None grips at zero squeeze.
- **The chain is closest to the flex.** Its roller forces are 22–39 N against the flex's 19–24 N, and the NOTE stays
  as low (1.5–1.7 in), because the chain bends like the flex. The solid soft torus can't bend, so the whole NOTE
  tilts up (2.8–3.4 in) and the roller takes 3–4.5 times the force.

## Case 3: drop and bounce

The NOTE is dropped flat from 1 m (its bottom), onto the carpet.

| candidate | bounce mm | e | settles in s | deepest contact mm | squash mm | stable | bounce at 2 ms | bounce at 4 ms | wall s per sim s |
|---|---|---|---|---|---|---|---|---|---|
| rigid_ring | 14 | 0.12 | 0.14 | 6.2 | 6.2 | yes | | | 0.13 |
| rigid_disc | 14 | 0.12 | 0.14 | 6.3 | 6.3 | yes | | | 0.06 |
| rigid_torus | 14 | 0.12 | 0.14 | 6.2 | 6.2 | yes | | | 0.09 |
| soft_torus soft | 86 | 0.29 | 0.46 | 28.1 | 28.0 | yes | 83 | 77 | 0.09 |
| soft_torus firm | 92 | 0.30 | 0.42 | 16.1 | 16.1 | yes | | | 0.09 |
| chain soft | 87 | 0.29 | 0.40 | 16.0 | 14.0 | yes | 81 | 76 | 0.40 |
| chain firm | 94 | 0.31 | 0.41 | 9.2 | 8.1 | yes | | | 0.40 |
| flex3d soft | 94 | 0.31 | 0.41 | 5.7 | 9.6 | yes | 69 | 43 | 4.9 |
| flex3d firm | 89 | 0.30 | 0.37 | 5.8 | 7.6 | yes | | | 5.0 |

- **Every candidate is stable.** None blew up or settled through the carpet, at 1, 2 and 4 ms.
- **The soft NOTEs' bounce is by construction:** their damping was fitted to e = 0.3. The rigid NOTEs' 4 ms,
  critically damped contact gives e = 0.12.
- **In (b) and (c) the squash shows as penetration.** The soft torus sinks 28 mm into the carpet for an instant;
  the flex's surface stays within 6 mm of it.
- **The flex under `discrete` loses bounce as the step grows** (94, 69, then 43 mm at 1, 2 and 4 ms): that
  integrator's backward-Euler damping.

## Case 4: stability and cost

Stability at the world's step (1 ms):
- all candidates are stable in every case above, and in a pile of five dropped in a leaning stack;
- (b) and (c) run on the world's `implicitfast`;
- (a) needs the whole model on `discrete`. Ten rigid NOTEs under `discrete` cost the same as under `implicitfast`
  (0.55 against 0.57 s/s), but no robot model was tried on it.

Cost (wall s per simulated s; one core; NOTEs resting flat 16 in apart unless noted):

| candidate | dofs per NOTE | 1 resting | 10 resting | 10 resting, `sleep` on | 5 in a pile |
|---|---|---|---|---|---|
| rigid_ring (scrimmage's) | 6 | 0.043 | 0.53 | 0.005 | 2.76 |
| rigid_disc | 6 | 0.003 | 0.023 | 0.002 | 0.03 |
| rigid_torus (capsules) | 6 | 0.022 | 0.24 | 0.005 | 0.15 |
| soft_torus (c) | 6 | 0.022 | 0.25 | 0.005 | 0.16 |
| chain (b) | 51 | 0.064 | 0.87 | 0.017 | 1.19 |
| flex3d (a), 24×8 | 648 | 0.83 | 8.5 | 2.8 | 19.7 |
| flex3d (a), 16×6 | 336 | 0.41 | 4.2 | | |

The world must keep up with real time with a robot in it. The 2024 range has 11 floor NOTEs before stations and
preloads. Ten flex NOTEs run 8.5× slower than real time. Ten chains take 0.87 s/s, nearly all of the budget. Rigid
resting NOTEs (with `sleep`) cost almost nothing.

## Could resting NOTEs stay rigid and turn soft only near a mechanism?

**Yes; mechanically it is easy** (`switch.py`). A rigid NOTE is thrown and, in the air, swapped for a parked soft
one, then swapped back:
- **Rigid to soft:** the soft NOTE gets the same pose and the same rigid-body velocity field.
- **Soft to rigid:** the rigid NOTE gets the soft one's center of mass, a best-fit (Kabsch) rotation, its momentum,
  and its spin from its angular momentum.
- **What changed at each swap:** momentum by under 3e-9 kg·m/s and the center of mass by under 3e-6 mm. The spin
  changed by 0.004 rad/s for the flex and nothing measurable for the chain. Nothing blew up.
- **What the swap back loses:** the deformation and its vibration energy. So swap back once the NOTE has left the
  mechanism's contacts and its internal motion has died down. The NOTE then flies rigid, with `rigid_aero`, as now.

**The cost of carrying the pool:**

| model | wall s per sim s |
|---|---|
| 10 rigid NOTEs, implicitfast | 0.57 |
| 10 rigid NOTEs, discrete | 0.55 |
| 10 rigid + 1 parked chain (contacts off, pose written back each step, as scrimmage parks a slot) | 0.59 |
| 10 rigid + 1 parked flex (discrete) | **8.3** |

A parked flex still costs as much as a live one, and it pushes up the whole contact solve. So the flex can't be a
parked pool member in the world.

**How hard the switch is in scrimmage: M–L.**
- **The ledger.** `testrange/balls.py` is vectorized over free-joint slots (qpos 7, qvel 6 each). It needs per-slot
  spans, or a separate soft pool with its own park and put.
- **The pool.** Its size is fixed when the model is built (the live view needs a fixed body count), for example 2
  soft NOTEs per robot.
- **The trigger.** A NOTE's bounding sphere within a margin of a robot mechanism's geoms: a cheap per-step distance
  check over a few geoms.
- **The swap-back rule.** No robot contact for a few tens of milliseconds and low internal kinetic energy; or at a
  hand-off to the analytic shooter, which already takes a rigid piece.
- **The rest.** The recorder and live view must draw the chain's 16 segments. Tests are needed for momentum at the
  swap and for the golden ledger staying unchanged with no swaps.
- **A cheaper first step, size S.** Replace the 24-slice rigid ring with the 24-capsule torus (a round section, as the
  real NOTE has; 2× cheaper resting, 18× cheaper in a pile), and turn on `sleep` for resting pieces. Both are
  independent of K5.

## Recommendation, with its costs

1. **World:** rigid capsule-torus NOTEs at rest and in flight (with `sleep`), and a pooled 16-segment chain (b) swapped
   in near robot mechanisms.
   - It stays on `implicitfast` at 1 ms, so the robot's dynamics and goldens don't change.
   - Shooting stays the analytic `pinch.py` hand-off, not real wheels: the chain's pinch exit speed is 1.2–2.3× too
     fast, because of its contact damping.
   - *Costs:* the switch above (M–L).
   - *Accuracy:* the chain's squash is a MuJoCo soft contact. It is mass-scaled, a static load and an impact see
     different stiffness (by 1.25× at d = 0.2), and its damping is a compromise. It is good for "is it picked up,
     where does it go", not for exit speeds.
   - *Speed:* about 0.06 s/s per active soft NOTE.
2. **Bench:** the flex torus (a) on `discrete` + Newton, 1 ms for trials and 0.5 ms for the numbers that are kept.
   - Use the 16×6 mesh: half the cost of 24×8, and the same answers within 2 % once E is refitted.
   - E is fitted per mesh to a squeeze test, and β to a drop test.
   - A shot costs 0.26–0.6 s; an intake pass about 7 s of wall time per simulated second.
   - Use it to set `pinch.py`'s `stiffness` and to tune the world chain's contact.
   - *Costs:* the bench gets a second integrator path (it runs `implicitfast` at 0.1–0.25 ms today).
   - *Risk:* `discrete` is "new, under active development, and subject to change", so pin the MuJoCo version and
     keep a golden that catches a change. Per-mesh fitting is a step a student can't do by hand, so ship the fitted
     mesh. Watch for the ill-conditioning warning at low E.
3. **`pinch.py` stays, and is consistent.** Fed the same squeeze, it lands within 4–13 % of the flex. The open
   number is the foam's stiffness, not the model: soft against firm foam moves the exit speed by 50–75 %.
4. **Don't use (c) alone.** It is the cheapest, but a solid soft torus can't bend: at the intake it tilts the whole
   NOTE up and takes 3–4.5 times the roller force of the bending models.

## Open questions only a real NOTE can settle

1. **A squeeze test.** Force against squash between two 4 in rollers, or flat plates, at 25 % and 50 %. This sets E;
   the soft and firm guesses here are 3× apart, and every pinch number scales with it.
2. **A drop test from 1 m, flat (filmed).** The bounce height sets the damping: e = 0.3 here is a guess, and the
   rigid NOTE gives 0.12. It also shows whether foam damping depends on rate (a slow against a fast squeeze), which
   is exactly what separates the chain's pinch from the flex's.
3. **A filmed shot at two wheel speeds** (for example 3000 and 5000 rpm) through a known gap, with exit speed from
   the video.
   - If the exit speed barely rises with rpm, the shot is grip-limited, as the flex and `pinch.py` say.
   - If it rises by 30–40 %, the NOTE behaves like the soft-contact models.
   - One shot with unequal wheels would give the spin.
4. **The tread's friction on the coated foam** (a sled pull). The grip-limited exit speed is roughly proportional to
   it.
5. **A filmed intake at axle heights of 2.5, 2.75 and 3.0 in.** Does a roller with no squeeze ever take the NOTE?
   Every model here says no.
6. **A flat, backspun NOTE's glide** (lift). This is separate from K5, but it is the other half of a SPEAKER shot.

## Files

All in this folder:
- `notes.py`: the candidates (MJCF builders, the tet torus, the state tracker);
- `scenes.py`: squeeze, pinch, intake, drop and resting cost;
- `calibrate.py` writes `params.json`;
- `pinch_ref.py`: `pinch.py`'s prediction;
- `run_cases.py` writes `results.json`;
- `extra.py` and `extra_mesh.py`: the 2.75 in intake and the mesh sensitivity, into `extra.json`;
- `switch.py`: the swap and the parked cost, into `switch.json`;
- `chain_vs_flex.py` and `flex_beta.py`: the damping sweeps;
- `tables.py` writes `tables.md`;
- `run_all.sh` reruns everything;
- `MUJOCO_LOG.TXT`: the one early warning.
