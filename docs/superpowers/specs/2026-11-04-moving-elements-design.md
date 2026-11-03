# Moving field elements, mechanism collisions and a partner robot (sub-project 24)

## Intent

The survey of past FRC games ranked three related gaps, which become survey project 4:
- **Field elements that move when a robot works them** (8 games): a door, a flap, a portcullis to lift, a touchpad
  to press, a spring peg, a spinning panel. Today a range has only a tilting platform and a tilting switch.
- **Stock mechanisms that collide with the field** (every drive-under game, such as the 2020 and 2026 trenches, plus
  every placing game). Today a stock elevator, arm or launcher passes through a trench arm, so a student can't test
  clearance with them. Folder parts from CAD already collide.
- **A passive partner robot** (5 buddy-lift games, and defense in nearly all) to lift, push or be pushed by. It is
  a stand-in, because a second robot program is a stated non-goal.

The owner asked for this project ("handle the next part of the survey") and earlier told me to answer the design
questions myself. Every call I made is listed under "My calls" for review.

What a student wants to find out:
- **Moving elements:** can my mechanism open the door, lift the portcullis, press the touchpad, or spin the panel
  three turns? How hard does it have to push, and does it get stuck?
- **Collisions:** does my robot fit under the trench with the elevator down and the arm stowed? By how much? Does
  the arm hit the trench if I raise it early?
- **Partner robot:**
  - Can my ramp or forks lift a partner robot, and how high and how level?
  - Can my drivetrain push a robot that has its brakes on?
  - Do I get stuck on a robot parked in my way?

The audience is the same as before: high-school CAD students in the Test tab, often with a stand-in robot, and
teams running robot code in scenarios. The standing anti-goal applies: **no unnecessary friction**.

## Goals

1. **Two moving element kinds** in range files:
   - **`hinged`:** a panel (a box, or a disc for a spinning panel) on a hinge along one of its edges or through its
     center. It has hard stops (or none, for a spinning panel), a spring with a rest angle, damping, and a
     breakaway (the torque that starts it moving). A door, flap, drawbridge, sally port, or spinning wheel.
   - **`sliding`:** a panel on a straight slide. It has stops, a spring with a rest position, damping and a breakaway
     force. Gravity acts on it, so a vertical portcullis takes its own weight to lift. A portcullis, a touchpad, or
     a spring peg.
   - **What they share with the existing elements:** `center`, `yaw_deg` and inch forms of every length.
   - **They appear everywhere elements do:** they collide with robots and pieces, are drawn live and in replays,
     and their joints show in `get_state`.
2. **A signal per moving element:** `element.<name>`, its angle in degrees (unwrapped, so three turns read 1080) or
   its travel in inches. It is available to scenario checks, to the Test tab's chart, and to a new Test-tab
   condition, *element [name] is above/below N*.
3. **Opt-in collisions for stock mechanisms:**
   - **Which parts:** elevator, arm, flywheel, roller, launcher, disc_shooter, turret and cylinder each gain a
     `collide` setting (default false, as today).
   - **When true:** its geoms touch the field, game pieces and the partner robot, like a folder part's geoms. They
     still never touch the robot's own parts.
   - **Where it is set:** the Robot tab's stock form shows it as a checkbox.
4. **Clearance:** a new Test-tab condition, *robot clears [element] by at least N in*, and a chart signal
   `clearance.<element>` in inches, the closest distance between any colliding robot geom and that element. A run
   with that condition says the smallest clearance it saw and when. The existing *touch* condition (with
   *throughout*) already answers "never hits".
5. **A trench to drive under:** a new built-in range, `2026-trench`, with one 2026 TRENCH (22.25 in clearance, from
   the 2026 manual) and a start before it. The full `2026` range is unchanged (see C9).
6. **A passive partner robot:**
   - **Where it is set:** a robot config's `[partner]` section, or the Test tab's Setup.
   - **What it is:** a stock chassis and 6-wheel tank drive with no robot program.
   - **Settings:** its mass, size, bumper height, where it starts, and whether its wheels are braked or coast.
   - **Behavior:** it collides with the robot, the field and game pieces, and it can be pushed, climbed onto or
     lifted.
   - **Signals:** `partner.height` (inches its frame's lowest corner is above where it started), `partner.tilt`
     (degrees from level) and `partner.moved` (inches across the floor from its start).
   - **Conditions:** *partner is lifted at least N in* (optionally *and level within N°*) and *partner moved at
     least N in*.
7. **A defenses range and a worked example:**
   - **`2016-defenses`:** a built-in range with a portcullis (sliding, 5 lb to lift: the 2016 manual), a sally port
     door (hinged, 1.5 lb to open), a drawbridge (hinged, pulled down) and a cheval de frise (four hinged plates
     pressed down). It is described from the 2016 manual's figures, with estimates marked.
   - **Spinning panel:** a 2020 CONTROL PANEL wheel (hinged on its center, no stops) in the `terrain-library`
     range.
   - **Worked examples:**
     - `examples/blocks/trench`: a stand-in robot with a stock elevator and arm, `collide = true`, driving under the
       trench stowed (it clears) and with the arm up (it hits).
     - `examples/blocks/partner`: a stand-in robot pushing a braked partner robot and reading how far it moved.
     - Each example has a README and a test.
8. **Docs:**
   - terrain.md: the new kinds and the defenses range.
   - parts.md: `collide`.
   - test-tab.md: the conditions, Setup's partner, and the examples.
   - test-range.md: the trench range, and its "What isn't modeled" list updated.
   - The README's signal list.

## Non-goals

- **Partner behavior:** a partner robot that drives, defends or runs code, more than one partner, and a partner
  built from the student's own parts.
- **Hinges:** elements on more than one joint (a double door is two elements), and latches that hold a door open
  once opened.
- **Field changes:** adding trenches to the full `2026` range (C9), and the 2020 CONTROL PANEL's color sensing (the
  survey's sensors gap).
- **Speed:** collision for every stock part by default.

## Anti-goals

- **No change to existing results.** Robots without `collide = true` or a `[partner]`, on ranges without the new
  kinds, step exactly as before: the ledger golden test and every example keep their numbers.
- **No new setup a student must do** for the common case. A partner is one checkbox in Setup with good defaults. A
  moving element in a built-in range just works.
- **No unstable elements.** A light flap on a stiff spring at a 1 ms step must not blow up. Stops are stiff and
  critically damped, as the platform's are.

## Constraints

- **Contact bits:** the robot's geoms use contype 2, conaffinity 1; the field uses 1 and 2; balls use 4 and 7.
  - **Moving elements** use the field's bits.
  - **The partner** must touch the robot, the field and balls. Its own bodies never touch each other: use
    contype 3, conaffinity 3, with its own bodies excluded from each other.
- **Field element style:** moving elements follow the existing ones in testrange/elements.py and
  build_elements.py: dataclasses, `_Reader` checks with plain messages, every length with an `_in` form, and
  stops built like the platform's (PLATFORM_STOP_TC, critically damped).
- **Replay:** any body not welded to the world is a mover, so moving elements and the partner replay with no new
  code.
- **Cost:** clearance uses MuJoCo's `mj_geomDistance`, computed only when a test names the element (the condition
  or the chart).
- **Spellings and wording:** American spellings and plain words in every student-facing string.

## My calls (the owner can overrule any)

**Moving elements**
- **C1 Kind names:** `hinged` and `sliding`, because those words describe what the student sees.
  - **`hinged` keys:**
    - `shape` ("box", the default, or "disc"), with `size` [x, y, z] for a box or `diameter` and `thickness` for a
      disc;
    - `base_height`, and `hinge` (which edge it turns on: "bottom", "top", "left", "right", "front", "back", or
      "center" for a spinning panel);
    - `range_deg` [lo, hi] (leave it out for no stops), `rest_deg` (0);
    - `stiffness` (N·m/deg, default 0), `damping` (N·m·s/rad), `breakaway` (N·m), `mass`.
  - **`sliding` keys:**
    - the box keys, plus `axis` ("x", "y" or "z" in the element's frame);
    - `range` [lo, hi] (m, or `range_in`), `rest` (0);
    - `stiffness` (N/m), `damping` (N·s/m), `breakaway` (N), `mass`.
  - **Units:** stiffness is per degree on a hinge, so it reads like a door spring's rating.
- **C2 Breakaway:** this is MuJoCo's joint frictionloss. It's the push needed to start the element moving and to
  keep it moving, because the model has no separate static and kinetic friction. The 2016 portcullis's "5 lb" is
  its weight: a 2.27 kg panel on a vertical slide with no breakaway.
- **C3 Signal sign and zero:** the angle or travel reads 0 at `rest_deg` or `rest`. Positive follows the joint's
  axis, as the right-hand rule gives it, and the docs say which way that is for each `hinge` edge.
- **C4 Defense numbers:** the 2016 forces (portcullis 5 lb lift, sally port 1.5 lb) come from the survey's reading
  of the 2016 manual. Panel sizes are estimates from the manual's figures, marked as estimates in the range file.

**Collisions**
- **C5 One setting per part:** stock parts get `collide = true/false`, not one per geom.
  - **Drawing-only geoms stay non-colliding:** the heading marker, the hood's drawn arc and the side plates. The
    launcher's side plates and wheels collide, but the launcher model still runs the ball path, so a colliding
    launcher touches only field elements and floor pieces.
  - **`[shooter]` stages:** a colliding roller that is also a `[shooter]` stage or intake is an error naming both.
    The ball path and contact would fight over the same balls.
- **C6 Clearance:** measured with `mj_geomDistance` (distmax 0.5 m), between every colliding robot geom and every
  geom of the element.
  - **Pruning:** pairs whose bounding spheres are more than 0.5 m apart are skipped.
  - **Readout:** a clearance over 0.5 m reads ">20 in" and as NaN in the chart. A touch reads 0.

**Partner robot**
- **C7 Partner build:** it is built from the stock chassis and differential parts, prefixed `partner.`.
  - **Defaults:** 0.7 × 0.7 m frame, 60 kg (an estimate of a full robot with bumpers and battery), bumper clearance
    0.02 m, and `brake = true`.
  - **Brake:** heavy wheel damping, the back-EMF a braked drive gives (an estimate). Coast: free wheels.
  - **Start:** `start = [x, y, heading]`, or in Setup *ahead of my robot by N in*, facing a chosen way.
- **C8 Lift reference:** `partner.height` measures the lowest frame corner's rise from its rested height, not from
  the carpet, so bumper clearance doesn't count as lift.

**Ranges and examples**
- **C9 Trench placement:** the trench goes in a new `2026-trench` range, not the full `2026` range. Adding trench
  structures to `2026` would change the ledger golden runs (balls rolling into them) and the existing examples'
  numbers. The full field gets its trenches in a later change that updates the goldens on purpose.
- **C10 Example choice:** the examples are the trench (collisions and clearance) and the partner push. The defenses
  get a range, not an example, because a stand-in grabber that lifts a portcullis would need a custom part.

## Left to the builder

- The internal module layout, and how the clearance and partner signals hook into the Watch and the scenario runner.
- Block shapes and colors in the editor.
- Exact wording, within the plain-words rule.

## Decisions made during the build

(Filled in during the build.)
