# K5 spike: a hanging 2024 CHAIN a stock climber can hook

Spike date: 2026-11-11. The setup was scrimmage at `1d95d6b` and MuJoCo 4.0.0. The world settings were 1 ms timestep, implicitfast, elliptic cones, impratio 10 and the Newton solver. Nothing in the repo was changed: every change to scrimmage is a patch applied inside the spike's own process (`k5patch.py`).

## Recommendation

**Model each CHAIN as a 1D flex of N = 40 edges.** That is MuJoCo 4's successor to the cable plugin and the composite cable, both of which MuJoCo 4 removed. Use point masses on slide joints, edge-length equality with a stiff `solimp`, no bending stiffness, and both ends pinned to the world. Give the chain its own contact parameters against the hook. Keep the rigid `bar` as a cheap fallback (`model = "bar"`).

- **Hang shape.** At rest the low point is 0.7176 m (28.25 in). After 10 s it has crept 0.15 mm, with 0.03 mm sd of jitter.
- **One robot climbs.** It hooks with the stock climber and climbs through `scrimmage climb run` unchanged. The run reached ONSTAGE: the hook did not slide, the chain sagged 6 cm under 60 kg, the swing was under 1° and the hook never slipped.
- **Two robots.** Two 60 kg robots on one chain both hang, at different heights (the heavier side sags).
- **Cost.** On a stage-only field with 3 chains, an idle simulated second goes from 42 ms of wall time (bar) to 117 ms (flex 40). On the full 2024 range with 6 chains it goes from 149 to 315 ms. `climb run` takes 207 ms per simulated second, about 5× faster than real time.
- **Why not more edges.** N = 80 is too stretchy for two robots (the chain stretches about 28 cm and they never lift). N = 20 works but bends coarsely.

**Caveat: none of the discrete chains reproduce the HARMONY slide.** A stock J hook is a thin plate, so it settles into the corner the chain makes at one joint and stays there. It only moves when pushed along the chain with about W·tan(φ/2) ≈ 200–250 N for a 60 kg robot, almost independent of N and of hook friction (even μ = 0.02). The frictionless pulley stand-in (candidate c) slides, but freely and without damping. If the slide matters, see "Next steps".

## Geometry and parameters (estimates unless noted)

| quantity | value | source |
|---|---|---|
| chain length L | 120 in = 3.048 m | task: "about 10 ft" |
| end height H | 48 in = 1.2192 m | 2024.toml's comment "hung from 48 in at the legs" (estimate) |
| low point Z0 (the chain's axis) | 0.7176 m (28.25 in) | M 5.5, as the range's `height` |
| end span D | 2.8229–2.8234 m (N = 80 to 20) | **derived**: solved so the exact discrete catenary of N equal links has its lowest joint at Z0 |
| chain radius | 0.25 in (1/2 in across) | the range's `diameter_in = 0.5` |
| chain mass | 0.744 kg/m (2.27 kg) | #35 roller chain, 0.50 lb/ft (estimate; #25 would be 0.37 kg/m) |
| position, axis | `center` and `yaw_deg` of each `chain_*` bar, ends at ±D/2 along the axis | 2024.toml |
| hook | stock climber J: throat 1.66 in + 6 mm, `hook_depth` 0.08, steel μ 0.5, HOOK_SOLREF 0.01 s | climber.py, unchanged |
| chain contact vs hook | priority 1 (= HOOK_PRIORITY), solmix 1e6, solref [0.002, 1], solimp [0.99, 0.999, 0.001, 0.5, 2], friction 0 (so the hook's μ still wins, as the max) | `chainlib.CONTACT` |
| flex edges | flex equality, solref [0.002, 1], solimp [0.999, 0.9999, 0.001, 0.5, 2] | `chainlib.add_flex` |
| links | ball joints, damping 0.002 N m s/rad; connect solref [0.002, 1] | `chainlib.add_links` |
| robot | 60 kg. Standalone: a 0.75 × 0.75 × 0.12 m box with a one-slide telescope servo (4e4 N/m, 2500 N cap). In scrimmage: stock chassis (55.5 kg) + differential + a stock `climber` (2 stages, 2 hooks 0.2 m apart, 2 Krakens, 12:1) | `climbsim.py`, `world/chain_robot.toml` |

The 2024 STAGE's legs in `2024.toml` are estimates. At 48 in they are only 1.7 m apart, so a 10 ft chain cannot hang between them: it would drag on the carpet. The spike therefore puts the chain's ends in the bar's own vertical plane, D apart. A real field model needs the actual attachment points, which are CAD-only.

## Candidates

- **(a) Links**: N capsule bodies on ball joints. Three ways to build them:
  - **tree**: one tree from end A, with a connect pinning the last link to end B;
  - **links**: two trees of N/2 links, one from each end, joined by a connect at the middle;
  - **free**: N free bodies joined by N+1 connects.
- **(b) Flex**: MuJoCo 4 removed `composite` and the `mujoco.elasticity.cable` plugin, and the changelog points to a 1D `flexcomp` with `<elasticity>` instead. Here the flex is raw: N+1 vertices with the two ends on the world body, N capsule edges, and a flex equality holding the edge lengths. Bending stiffness is 0, so no elasticity is needed and implicitfast still works; MuJoCo 4's rod elasticity needs the `discrete` integrator.
- **(c) Tendon**, the kinematic stand-in. A rigid, non-colliding proxy bar marks where the chain is. Once the hook's seat comes down onto the rest curve (within the chain's radius plus 1 cm, |x| < 2 cm), a spatial tendon runs end A → each hook seat → end B, limited to L. This is an exact inextensible, weightless chain whose load points slide without friction: the seat moves on the ellipse with foci A and B.
- **bar**: today's rigid `bar` (1.5 m long at Z0), for reference.

## Case 1: hang shape at rest, one chain, 10 s, no load (`case1_hang.py flex --stiff`, `case1_links.py`)

Every candidate starts in its exact rest shape, so there is no transient.

| model | N | nv | low point after 10 s (m) | creep (mm) | jitter sd / p2p, last 5 s (mm) | far end or joint error (mm) | wall ms per sim s, 1 chain |
|---|---|---|---|---|---|---|---|
| flex (stiff solimp) | 20 | 57 | 0.7176 | −0.03 | 0.006 / 0.025 | 0 (pinned) | 16 |
| flex (stiff solimp) | 40 | 117 | 0.7175 | −0.15 | 0.026 / 0.12 | 0 | 26 |
| flex (stiff solimp) | 80 | 237 | 0.7172 | −0.37 | 0.115 / 0.49 | 0 | 51 |
| flex (default solimp) | 40 / 80 | | 0.7058 / 0.6756 | −11.8 / −42 | 3.7 / 6.4 sd | | 26 / 49 |
| links: tree + pin | 20 / 40 / 80 | 60 / 120 / 240 | 0.7176 / 0.7175 / 0.7174 | −0.04 / −0.11 / −0.20 | ≤ 0.04 sd | 0.03 / 0.07 / 0.13 | 53 / 222 / 1112 |
| links: two trees | 20 / 40 / 80 | 60 / 120 / 240 | 0.7175 / 0.7174 / 0.7173 | −0.07 / −0.15 / −0.27 | ≤ 0.035 sd | 0.07 / 0.13 / 0.24 | 38 / 135 / 599 |
| links: free | 20 / 40 / 80 | 120 / 240 / 480 | 0.7176 / 0.7175 / 0.7172 | −0.02 / −0.12 / −0.43 | ≤ 0.12 sd (vmax 0.24 m/s at N = 80: links spin about their own axes) | ≤ 0.004 | 42 / 82 / 161 |

All of them are stable at the world's timestep. The flex needs the stiff edge `solimp`: with the default one it sags 1–4 cm and keeps bouncing. Links in one tree cost the cube of the depth, because MuJoCo's mass matrix for a body chain is dense: 1.1 s per simulated second at N = 80. Splitting the chain into two trees halves the cost. The flex and the free links grow linearly.

## Case 2: one 60 kg robot climbs

### In scrimmage: `climb run` with the stock climber (`k5climb.py`)

The robot starts 0.25 m short of `chain_blue_center`. The script (`world/chain.toml`, adapted from `examples/climb/high.toml`'s MID steps) has five steps:
1. reach the hooks above the chain;
2. drive 0.31 m at 1.5 V so the chain passes under the hook tips;
3. retract, waiting for `hook_contact`;
4. pull up;
5. hang for 5 s.

| model (3 chains on the stage-only range) | status | hooked at | peak hook load | seat height while hanging | chain low point | slide along the chain | swing (last 3 s) | wall ms per sim s |
|---|---|---|---|---|---|---|---|---|
| bar (rigid, today's) | **not hanging** (the hooks pass the 0.5 in bar; see below) | 3.08 s | 1080 N | — | 0.7176 | — | — | 92 |
| links N = 20 | **reached ONSTAGE** | 3.08 s | 1678 N | 0.650 → 0.655 | 0.667 (sag 5 cm) | 0.0 mm | ±0.8° | 251 |
| links N = 40 | **unstable** (NaN at 3.48 s, 11.6 kN) | 3.08 s | — | — | — | — | — | 910 |
| flex N = 20 | **reached ONSTAGE** | 3.08 s | 1877 N | 0.653 → 0.656 | 0.657 (sag 6 cm) | 0.0 mm | ±0.2° | 166 |
| flex N = 40 | **reached ONSTAGE** | 3.08 s | 1289 N | 0.640 → 0.648 | 0.657 (sag 6 cm) | 0.0 mm | ±0.4° | 207 |
| flex N = 80 | **reached ONSTAGE** | 3.08 s | 754 N | 0.622 → 0.631 | 0.643 (sag 7.5 cm) | 0.0 mm | ±0.5° | 296 |

On every chain model the robot's CoM ends 0.26–0.29 m up, so the bumpers are well off the carpet. No chain run lost a hook. The seat height is the mean of the hook geoms' centers.

**The rigid 0.5 in bar, as the 2024 range has it today, could not be climbed by this script.** The bar wedges into the top of the J's crown, between crown1 and crown2. At about 1.1 kN the robot pitches 1–4°, the hooks slide back off it, and the robot drops to the carpet. A gentler 4 V pull-up (`world/chain_4v.toml`) and a stiffer HOOK_SOLREF (0.002 s, `K5_HOOK_SOLREF`) failed the same way. The same script works on every chain model except links 40. I did not chase this further. It means gap 9's "ONSTAGE ≈ lowest-rung on a rigid chain bar" is untested for a reason: it may not work with a thin bar.

### Standalone: one hook, the same J geometry, and the sequence repeated per candidate (`summarize.py case2 ...`)

| model | N | hooked at 0 m: lifted? bottom / seat z | slide | swing across / along, max | hooked at +0.5 m: slide | seat z | wall ms per sim s |
|---|---|---|---|---|---|---|---|
| bar | — | yes, 0.209 / 0.717 | 0 | 0.1° / 0° | 0 | 0.717 | 9 |
| tendon (c) | — | yes, 0.137 / 0.644 | 0 | 0.1° / 0° | **−0.92 m** (oscillates ±0.43 m, undamped, 19° p2p) | 0.667 | 9 |
| flex | 20 / 40 / 80 | yes, 0.137 / 0.644 · 0.132 / 0.640 · 0.117 / 0.624 | 0 | 0.1° / 0° | −8 / −7 / −4 mm | 0.676 / 0.671 / 0.658 | 26 / 39 / 64 |
| links (two trees) | 20 / 40 / 80 | yes, 0.136 / 0.643 · 0.133 / 0.640 · 0.128 / 0.636 | 0 | 0.1° / 0° | −7 / −6 / −4 mm | 0.675 / 0.673 / 0.670 | 54 / 160 / 653 |
| links (free) | 20 / 40 / 80 | yes · yes · **unstable** (NaN at 1.2 s) | 0 | | −7 mm (N = 40) | 0.671 | 49 / 86 / 157 |

- **Sag check.** For a weightless inextensible chain with the load at its middle, the seat should sit at 0.644 m. The tendon and N = 20 match it exactly. Finer chains sit lower: N = 80 flex reaches 0.624 m because its edges stretch (2 cm lower at 600 N).
- **Light links need the chain's own contact parameters.** Without chainlib.CONTACT, a hook falls through a light chain: flex at N ≥ 40 and links at N = 40 did not hold the robot. MuJoCo scales a contact's softness by the inverse mass it acts on, so a 57 g link under HOOK_SOLREF's 0.01 s is far too soft. With CONTACT (0.002 s, solimp 0.99) every case held.
- **Why a hook 0.5 m off center stays put.** Frictionless statics put the vertex there with tensions of 698 and 790 N and a 47° bend; the hook slides toward the middle only if μ < 0.15, the capstan limit (`breakaway.py`). Even at μ = 0.02 the chain models don't slide (`K5_MU=0.02`). The cause is the notch the chain makes at a joint. The push needed to move a hanging robot 2 cm along the chain:

| model | μ 0.5 (steel) | μ 0.02 |
|---|---|---|
| tendon (c) | 27 N (no friction; that is the pendulum's restoring force) | 27 N |
| flex N = 20 / 40 / 80 | > 400 N (didn't slide) | 255 / 217 / 195 N |
| links N = 20 / 40 / 80 | > 400 N | 255 / 217 / 198 N |

The barrier is W·tan(φ/2) = 600 · tan 22° ≈ 242 N. It is scale-free: both the barrier's energy and its distance shrink with the link, so a finer chain does not remove it.

## Case 3: HARMONY, two 60 kg robots on one chain (`summarize.py case3 ...`, `case3ghost`)

Two layouts were run:
- **case3**: robots at −0.45 m and +0.5 m. Their 0.75 m frames are 0.2 m apart and they collide with each other.
- **case3ghost**: robots at 0 and +0.5 m that don't collide with each other, as scrimmage's robot and partner don't.

| model | layout | both lifted & hooked at the end | final robot bottoms (m) | final seats (y, z) | slide toward the low point | wall ms per sim s |
|---|---|---|---|---|---|---|
| bar | case3 | yes | 0.209 / 0.209 | (−0.45, 0.717) / (0.50, 0.717) | 0 / 0 | 17 |
| tendon (c) | case3 | yes | 0.144 / 0.127 | (−0.335, 0.708) / (0.252, 0.702) | **+0.12 / −0.25 m**, until the frames meet; still swinging 7° p2p | 17 |
| tendon (c) | ghost | yes | 0.082 / 0.097 | both at y ≈ −0.26, z 0.653 | **−0.26 / −0.76 m**, together, swinging ±0.28 m (33° p2p), undamped | 17 |
| flex 20 | case3 | yes | 0.231 / 0.248 | (−0.452, 0.741) / (0.492, 0.757) | −2 / −8 mm | 34 |
| flex 40 | case3 | yes | 0.226 / 0.239 | (−0.450, 0.735) / (0.491, 0.748) | 0 / −9 mm | 46 |
| flex 40 | ghost | yes | 0.165 / 0.205 | (0.014, 0.674) / (0.494, 0.719) | +14 / −6 mm | 46 |
| flex 80 | case3 | **no**: the edges stretch, the chain's low point reaches 0.51 m, and neither robot lifts | 0.000 / 0.002 | | | 115 |
| links 20 | case3 | yes | 0.231 / 0.248 | (−0.451, 0.741) / (0.495, 0.757) | −1 / −5 mm | 59 |
| links 20 | ghost | yes (brief 1–3 sample contact losses) | 0.157 / 0.200 | (0.017, 0.679) / (0.467, 0.714) | +17 / −33 mm | 73 |
| links 40 | case3 | yes | 0.228 / 0.241 | (−0.450, 0.738) / (0.496, 0.751) | 0 / −4 mm | 161 |

- **The chain sags toward the load.** The robot nearer the middle hangs lower: in ghost, the center robot's bottom is at 0.165 m and the off-center one's at 0.205 m.
- **The two chain models agree** to within a few millimeters.
- **Only the tendon slides the robots together,** and with nothing to damp it, it overshoots.

## Case 4: cost, wall time per simulated second (1 ms steps, one core)

| world | bar | links 20 | links 40 | flex 20 | flex 40 | flex 80 |
|---|---|---|---|---|---|---|
| stage-only range, 3 chains, robot parked (idle) | 42 | 142 | 435 | 80 | **117** | 191 |
| same, `climb run` with one robot climbing | 92 | 251 | 910 (unstable) | 166 | **207** | 296 |
| full 2024 range (6 chains, 11 NOTEs, tags), idle | 149 | 403 | — | — | **315** | — |
| standalone, 1 chain, one robot climbing (no monitor) | 9 | 54 | 160 | 26 | 39 | 64 |
| standalone tendon (c), 1 or 2 robots | 9 / 17 | | | | | |

- **The chain's own cost.** In the scrimmage world, flex 40 adds about 25 ms per chain per simulated second. That is on top of the 3-slide-dof vertex bodies (nv 14 → 365 with 3 chains).
- **Where `climb run`'s extra cost goes.** About half of `climb run`'s extra over idle is the monitor's extent tracking (bar: 42 → 92), not the chain.
- **The tendon is as cheap as a rigid bar.**

## What had to change to engage a non-rigid element

**The hook and climber code (`climber.py`) did not change.** The changes were:

1. **Chain building** (`build_elements._bar` for a `chain_*` bar, `k5patch._bar`). The chain is built from the bar's `center`, `yaw_deg` and `height`, plus L and H.
2. **The holds** (`build_elements.fill_layout`). `layout.bars[<chain>]` must list every link geom, so the climb monitor's holds, `hook_contact`, "hit a bar" and scoring treat the chain as one hold. A flex has no geoms, so its list is empty.
3. **`terrain/climb.ClimbMonitor`, for flex chains.** A flex contact has `contact.geom = -1` on the flex's side, and `_read` and `contact_state` index their per-geom Python lists with it. That silently reads the *last geom's* entry: here a robot geom, so the contact counted as robot-on-robot and was ignored. The spike adds a sentinel entry at index −1 for the climbed chain. The real fix maps `contact.flex[k]` to its hold: a `_flex_hold` list beside `_hold_l`, checked when a geom id is −1. `mj_contactForce` works for flex contacts as is.
4. **`ClimbMonitor._swing`.** It measures about the hold's *first geom's* axis. For a chain that is an end link at 40–60°, and for a flex there is no geom. The spike measures about the chain's horizontal axis, through the hooks.
5. **The chain's contact parameters** (`chainlib.CONTACT`). Without them, light links (under 0.1 kg) let the hook fall through: HOOK_SOLREF's 0.01 s is scaled by the inverse inertia the contact sees. CONTACT gives the chain the same priority as the hook, a large solmix so the chain's stiffer solref and solimp win, and friction 0 so `hook_material` still sets μ (MuJoCo takes the max at equal priority). Nothing on the hook changes, so 2022 rungs behave as before.
6. **`climb run`.** No code change: `hook_contact = "chain_blue_center"`, the levels, walk-off and "hit a bar" all worked through change 2. The scripts must treat the chain as compliant: the robot has to drive the hook to the chain itself, and the stem pushes the chain away.

## Scoring ONSTAGE and HARMONY

**Proposed range syntax:**
```toml
[climb]
rule = "off-carpet"            # 2024: a robot touching a hold (a CHAIN) and no ground scores the hold's level
levels = { ONSTAGE = 3 }
harmony = 2                    # points to each robot ONSTAGE on a hold shared with another ONSTAGE robot (M Table 6-2)
```

- **`off-carpet`.** While `climb.hanging` (touching a hold, no ground), the robot scores the level of the holds it touches; with one level that is ONSTAGE. Touching the STAGE's core or truss is allowed, since they are boxes but not ground, exactly as `climb.hanging` already treats them.
- **How it differs from `lowest-rung`.** With several leveled holds it would score the highest, not the lowest. Robots supported by other robots don't count, and SPOTLIT is out of scope.
- **Signals**, named like the existing ones:

| signal | meaning |
|---|---|
| `climb.hanging`, `climb.level`, `climb.points` | as now. Under `off-carpet`, points = the level's points + `harmony` while `climb.harmony` = 1 |
| `climb.hold` | the rank (1-based, range order) of the hold the robot hangs from, 0 for none. Scenario checks need a number; per-hold flags are below |
| `climb.hold.<hold>.robots` | how many robots are ONSTAGE on that hold: the robot (0/1) plus each partner whose geoms touch it and not the ground |
| `climb.hold.<hold>.on` | 1 while this robot hangs from that hold |
| `climb.harmony` | 1 while the robot's hold has `robots >= 2` |
| `climb.hook.<inst>.hook<i>.load`, `climb.swing` | as now (the swing about the chain's horizontal axis, change 4) |

**What HARMONY needs beyond the chain.** It needs a second robot that can hang. scrimmage's `[partner]` (parts/partner.py) is a passive drive base with no climber, so HARMONY can be counted only when a partner is placed hanging, or once a partner can carry a stock climber driven by a script. The spike's case 3 is standalone for this reason. Counting a partner is cheap: `PartnerMonitor` has its geoms, and the same contact walk counts it.

## Next steps (if adopted)

1. **A `chain` element kind** (`kind = "chain"`: `center`, `yaw_deg`, `height` for the low point, `end_height`, `length`, `edges = 40`, `mass_per_m`, `diameter_in`, `level`), built as the flex. Keep `bar` as the cheap stand-in.
2. **Monitor and swing fixes 3 and 4**, with tests:
   - the rest shape (creep < 1 mm in 10 s);
   - a 60 kg hang (sag 5–8 cm, hooked);
   - two robots both hooked;
   - the flex contact → hold mapping.
3. **Decide whether the HARMONY slide matters.** If it does, there are two options:
   - **(i)** a hook with a wide, rounded saddle along the chain, so the chain bends over a radius instead of at a joint (needs a climber `hook_profile` option; not tried);
   - **(ii)** the tendon hybrid as the slide model: engage on contact with the flex, then hand over to the tendon with a damper along the chain (a velocity-proportional force on each seat along the tangent). It gives the slide and costs no more than a bar, but loses the chain's own swing and lets the hook "fall" 7 cm into the ellipse at engagement.
4. **Measure the field.** The real attachment points and height (CAD) and the chain's size (#35 or #25) would replace the estimated H, D and mass. The stage legs in 2024.toml can't hold a 10 ft chain at 48 in.
5. **Look into the rigid 0.5 in bar failing** to hold a stock climber in `climb run`, whatever chain model is chosen: `2024.toml` ships that bar today.

## Files (all in this folder; run with `/home/altainia/code/scrimmage/.venv/bin/python`)

| file | what |
|---|---|
| `chainlib.py` | the chain builders (links in a tree or two trees, free links, flex), the discrete catenary, CONTACT |
| `case1_hang.py [links\|flex] [--stiff]`, `case1_links.py` | case 1 (outputs `case1_flex.txt`, `case1_links.txt`, `case1_free.txt`) |
| `climbsim.py <kind> <n> [case2\|case3\|case3ghost] [--offset m]` | the standalone climb harness; `K5_MU=<μ>` and `K5_HOOK=<material>` set hook friction |
| `summarize.py <case> kind:n[@offset] ...` | one table line per run, plus JSON (`out_*.json`); outputs `case2.txt`, `case3.txt`, `case3ghost.txt` |
| `breakaway.py kind:n ...` | the push needed to slide a hanging robot (`breakaway.txt`) |
| `k5patch.py` | the in-process scrimmage patches (env `K5_CHAIN=bar\|links:N\|flex:N`; diagnosis only: `K5_HOOK_SOLREF`, `K5_BAR_CONTACT`) |
| `k5climb.py [--idle s]` | case 2 through `climb run`, and case 4 idle; env `K5_SCRIPT`, `K5_CONFIG` (outputs in `world/out-*`, `cost_idle*.txt`, `case2_world_more.txt`) |
| `world/make_range.py` → `world/k5-stage.toml` | the 2024 blue STAGE alone, copied from 2024.toml |
| `world/chain_robot.toml`, `world/chain_robot_2024.toml`, `world/chain.toml`, `world/chain_4v.toml` | the robot (stage-only range / full 2024 range) and the climb scripts |
| `dbg_world.py`, `dbg_geom.py` | contact and geometry traces used to diagnose the rigid bar's failure |
