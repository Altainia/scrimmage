# Pieces beyond balls (sub-project 22)

## Intent

The survey of past FRC games found that 6 of the last 11 games centered on a piece that isn't a ball (a cube, a cone,
a pipe, a gear, a panel) and that ten games mixed piece kinds. Scrimmage's fields hold one kind of piece today, and it
must be a ball or a Frisbee. Non-ball shapes exist only on the component bench. This project puts non-ball pieces on
the field, lets a field hold several kinds, and adds the checks that matter for them: held, placed, and how well
placed.

The owner's example is the 2025 coral, a PVC pipe. A student designing a grabber wants to know:
- whether it can pick up a coral however the coral lies: standing on end, lying across the robot's path, lying in
  line with it, or at any angle in between;
- how far down it has to reach to close on the coral's curved side, and whether curved jaws would help;
- whether the grip holds. A coral can fall out when the grabber has too little friction, or wedge out of the jaws
  because of its curve when the squeeze is too hard. Moving the grabber while holding makes both worse, so the
  student wants to test the squeeze force and the grip material;
- when placing a coral on a branch, that it worked, and how well it sits. A coral only partly on a branch can be
  knocked off by accident.

Coral is hollow and is scored by sliding it onto rod-like branches, so its hollowness has to be modeled. A future
game's tube might be solid, so a tube's hollowness is a setting.

At kickoff the owner's agent builds the new game's pieces. It works from the piece's CAD and from the manual's
dimensions, using both when both are available.

The audience is high-school CAD students using the Test tab (sub-projects 19 and 21), often with a stand-in robot.
The standing anti-goal applies: **no unnecessary friction**.

## Goals

1. **Piece kinds on a field.** A range file can declare several piece kinds. Each has a name and is either a preset
   or defined in the file:
   - **From simple shapes, using the manual's numbers:** a box, a cylinder, a tube (an inner diameter makes it
     hollow; leaving it out makes it solid), a cone, a ring (a short tube), a sphere, or a few of these combined.
     Each kind has a mass and a surface material.
   - **From CAD:** a STEP or STL file through the importer the Part tab already uses (scrimmage/cad), for an exact
     shape. The manual's weight and material go on top when the CAD lacks them.
   - Each floor piece, station and goal names the kind it uses. Today's single `ball = "<preset>"` keeps working and
     means one ball kind.
2. **Real contact.** Non-ball pieces are ordinary rigid bodies in MuJoCo, with the same physics as everything else.
   A hollow piece is built so a rod really passes through it (a ring of convex slices, or MuJoCo's own convex
   decomposition). A pipe rolls on its curved side and stands on its end.
3. **Stations and floor spreads work with any kind.** Sub-project 21's rotation spread, which does nothing for a
   ball, now turns the piece: a coral comes down a chute at varied angles, or lies on the floor turned every way. A
   floor spread reports orientation in plain words, e.g. "picked up lying across and at 45°; missed standing on
   end".
4. **Held.** A new condition and check: *the piece is held by* a robot part or body. It holds when the piece is off
   the floor, moving with that part, and touching it. A run can check "held throughout" while the arm swings or the
   robot drives and stops hard (existing blocks). When it fails, the outcome says when and how, e.g. "the coral
   slipped out of the claw at 2.3 s while the arm was moving", and the replay marks the moment.
5. **Grip you can change and sweep.**
   - **Squeeze:** set and swept as today, through the motor's voltage or a joint's push force (sub-project 19's
     blocks and sweeps).
   - **Pad material:** a grip material on a part's bodies, set in the Part tab or the Test tab's Setup. Choices:
     rubber, nitrile, grip tape, polycarbonate, aluminum, HDPE/UHMW, plus "my own number" for a measured friction.
     Each piece kind has its own surface material too. Friction values are estimates marked UNCALIBRATED, the same
     way ball stiffness is today, and the docs say how to measure one (a sled pull).
   - **A sweep over the material** answers "would grip tape help".
6. **Placed, and how well.**
   - A new condition and check: *the piece rests on* a field element. It holds when the element holds the piece
     up, the piece is at rest, and no robot body touches it.
   - For an element a piece slides onto (a rod, peg, pole or branch), a **seated depth**: how far the piece sits
     down it, against how far it could go. It is shown in plain words ("seated 3.5 in of 4.0 in"; "only 0.8 in on:
     could be knocked off"), charted like other signals, comparable between runs, and usable in a check ("seated
     at least 3 in").
   - The same "rests on" works for shelves, platforms and stacks (2015 totes, 2023 cubes on shelves).
7. **New field elements for scoring.** Rods, pegs, poles and branches (a straight round or square rod at any angle,
   fixed to the field). Shelves come from the existing box element. These can be written in range files and are
   used by the presets' sample fields.
8. **Pieces from past games, ready to use.** Each is sized from its manual, with every value carrying a
   `# source:` or `# estimate:` comment:
   - 2025 coral (hollow tube) and 2025 algae (ball, from sub-project 21)
   - 2023 cone and 2023 cube (the cube as a rigid stand-in)
   - 2017 gear
   - 2019 hatch panel (with its center hole)
   - 2015 tote
   - 2024 note (a foam ring, as a rigid stand-in)
9. **Setup's picker becomes a piece picker.** On the intake and station fields, the Ball picker lists every piece
   kind. On fields that hold several kinds, each station and floor piece shows its kind.
10. **A reef test field and a worked example.**
    - The field: a few branches at the 2025 manual's heights and angles, a coral on the floor, and a coral station.
    - The example: a grabber on the stand-in that picks the coral up off the floor, swings it, and places it on a
      branch. It checks the coral is held throughout the swing and seated on the branch, and comes with a floor
      spread over the coral's orientation.
    - Docs: docs/test-range.md (piece kinds, rods), docs/test-tab.md (held, rests on, seated, grip material), and a
      getting-started section.

## Non-goals

- **Squishy non-ball pieces** (the fabric cube, the foam note, inflated tubes). These are rigid stand-ins of the
  right size and weight here. Soft pieces are survey project 5.
- **Shooting non-ball pieces.** The `[shooter]` ball path, the launcher model and goals that count pieces passing
  an opening stay ball and disc only. Non-ball pieces move by real contact with the student's mechanism.
- **A "bump the piece" block.** A student tests knock-offs by driving or swinging into the piece. Left out unless the
  owner asks.
- **Grabbing from inside a tube.** Possible with a hollow piece, but nothing is built specially for it.
- **Turret, pneumatics, moving field elements.** These are later survey projects.

## Anti-goals

- Friction: a student with a grabber part gets from "Test it" to a run that says "held" or "slipped out at …"
  without writing a range file. Grip material is a picker, not a number to look up.
- A misleading grip result. Friction and squeeze are estimates and are labeled UNCALIBRATED; a run never presents a
  grip as measured.
- Breaking what works. Every existing range, test, scenario and example loads and runs unchanged, with the same
  results.

## Constraints

- Builds on what exists:
  - the range ledger and stations from sub-project 21 (scrimmage/testrange: balls.py, feeder.py, station.py,
    spec.py);
  - the bench's rigid pieces (scrimmage/bench, docs/bench.md `[piece]`);
  - the CAD importer (scrimmage/cad);
  - part geoms' `friction` and `priority` (docs/parts.md);
  - the blocks runner, conditions, Setup and replay (scrimmage/blocks, the assembler's Test tab).
- The same physics as the whole-robot simulation, with no test-only shortcuts.
- Formats change only by adding optional keys. Old files load and run unchanged.
- Windows is first-class. Use American spellings and plain words. CI is unmetered.

## Decided with the owner

- The main use: grabbers on pieces lying any way, the grip under motion (friction, squeeze, material), and placing
  onto rods, including how well the piece sits.
- Coral stays hollow because of scoring on branches. Hollowness is a per-piece setting, since future tubes may be
  solid.
- Grip force and pad material are things to test and sweep.
- "Placed" answers whether it worked and how well it sits (the seated depth).
- At kickoff, pieces come from CAD and the manual, both when available.
- Soft pieces stay rigid stand-ins. Shooting stays ball-only. Sample pieces from past games and a reef test field
  ship with this project.

## My calls (the owner can overrule any)

- **Piece kinds go in a range file's `[piece.<name>]` tables.** Presets live beside the ball presets, in a new
  `[piece.*]` section of a presets file. `ball = "..."` stays as shorthand for one ball kind.
- **"Held by" thresholds:**
  - the piece's lowest point is at least 1 cm above anything that isn't the robot;
  - its speed relative to the part is under 0.05 m/s;
  - it touches the part;
  - all three hold for at least 0.1 s, the same hold time the climb check uses.
- **"Rests on" thresholds:** the element carries at least half the piece's weight, the piece is at rest (the
  sub-project 21 rest speed) for 0.5 s, and no robot contact.
- **Seated depth:** measured along the rod's axis, from the rod's tip to where the piece's hole starts. For a solid
  piece on a shelf there is no depth, only "rests on".
- **Grip materials:** friction values are estimates from published rubber-on-plastic ranges, marked as such. Pads
  get a higher contact priority than pieces (as the climber's hooks do), so the pad's friction applies.
- **The note and the hatch panel's holes are modeled.** A note on the floor and a hatch on a hook both behave
  better with their holes.
- **The reef test field** uses three branches (the 2025 L2, L3 and L4 heights and angles) on one post, rather than
  a whole reef.
- **"Made it in" and "picked up" include holding.**
  - Setup's Hopper picker becomes "Where a piece should end up". The student ticks hopper, intake or grabber bodies.
  - A fed or floor piece counts as "in" when its center is inside one of those bodies' boxes, as today, or when it is
    held by one of them when the run ends.
  - So a grabber holding a coral at the end reads "picked up", in floor spreads and at stations alike.
  - Preselection adds "claw", "grabber", "gripper" and "jaw" to the hopper words (rule R9 from sub-project 21).
- **Orientation has two parts: tip and turn.**
  - Each piece kind declares its long axis: a tube's or cone's axis, the normal of a gear's or hatch panel's flat
    face, and a box's longest edge, or a CAD piece's choice.
  - A floor spread gains a `tip_deg` axis: 0 means lying on its side, 90 means standing on end, and anything in
    between is a lean the piece settles from. `rotation_deg` stays the turn about vertical.
  - The words come from both. Standing is "standing on end". Lying is "lying in line" at a turn of 0° (the long
    axis along the robot's path), "lying across" at 90°, and "lying at 45°" in between.
  - A piece is dropped from just above its resting height and settles, so a lean ends wherever physics puts it.
  - Stations use the same tip and turn spreads.
- **Seated depth.**
  - A rod has a length from its free tip to its base or stop.
  - The depth is how far along the rod's axis the piece's hole has passed over the tip. It is measured whenever the
    rod runs through the hole, even while the piece is still in the grabber.
  - "Could go" is the smaller of the rod's length and the piece's own length along its axis.
  - A solid piece, or one lying across a rod, has no depth (the signal is empty) and only gets "rests on".
- **Pad material lives in the part.**
  - A body's grip material is an optional key in the part's own edits (scrimmage-edits.toml), set in the Part tab.
    Setup shows it and edits the same key, as Setup's mount boxes edit the robot today.
  - A sweep over materials is a new sweep target, a body with a list of material names. It works by copying the
    config, as robot sweeps do.
  - Test files don't override it.
- **Stations and fields fit the piece.**
  - A station's chute without a set width is 1.3 times the piece's largest cross-section, where today it is 1.3
    times the ball's diameter.
  - The floor piece rests at the piece's own resting height.
  - A piece that can't fit a chute or start is the existing plain refusal, and a mismatch otherwise plays out
    physically.
- **Goals count balls and discs only.** A goal that names a non-ball kind is a plain error. On a mixed field, a goal
  counts only the ball or disc kind it names (the first ball kind when it names none).
- **The held outcome sentence** names the part, the time, and what was moving: the joint or drive that was
  commanded in the second before the slip.

## Left to the builder

- How hollow shapes are built in MuJoCo (convex slices or decomposition), and how CAD pieces are cut into convex
  parts. Coacd is already an option on the bench.
- How piece kinds share the ball ledger: one ledger over every free body, or one per kind.
- How existing ball-only code paths (the shooter ball path, goals, NT publication) ignore non-ball kinds cleanly.
- The exact grip material list and values, and whether pad material lives on bodies or geoms.
- Sizes and limits for rods (diameter, square or round, end caps).
- How the replay marks a slip, and how seated depth shows in the chart.

## Decisions made during the build

These are the calls made while planning and building, each with what it costs if it was wrong. The first group were
made in the plan; the rulings after them (P1 to P36) were made as the build met a case the plan didn't cover. None
changes what the sections above promise.

### Planning decisions

- **One ledger for every piece.** Every piece of every kind is a `ball.<i>` body, with its kind recorded per slot, so
  replay, NetworkTables and older tests keep working; cost if wrong: a name that says "ball" for a coral, which no
  student sees (they read "coral 3").
- **A kind's frame has its origin at its center of mass,** with a full inertia tensor, so a piece's position is where
  it balances; cost if wrong: a CAD piece's body origin differs from its file's origin by a fixed offset.
- **Shapes and the hollow tube.** A kind is a sphere, box, cylinder, tube, ring, cone, compound or CAD, and a hollow tube
  is 24 convex slices (a solid round a 32-sided prism); cost if wrong: a coral's hole is 0.9% narrower than its number
  and its outside a polygon, a hair off a true circle.
- **Mass is required** for a written-out kind, with its surface as a material or a number, because CAD files carry no
  mass; cost if wrong: one more key to type per piece.
- **Each kind has a long axis,** a flat kind being shorter along it than across, which fixes the words for how it lies;
  cost if wrong: a CAD piece with an odd axis reads "lying across" when it lies the other way (it can be set with `axis`).
- **Orientation is a tip then a turn,** the turn of a floor piece about the field's +x and of a spread or station
  relative to the robot or station heading; cost if wrong: a range file's turn has to be thought of in field terms.
- **A rigid piece rests at its lowest point plus 5 mm,** so it settles under physics; cost if wrong: a 5 mm drop at the
  start of every run.
- **Old size words come from the piece's extent and bounding radius** (a ball's are today's numbers), so chutes and
  release checks fit any piece and old balls are unchanged; cost if wrong: a chute is a little wide for an odd piece.
- **A field's default kind is `[range] ball | disc | piece`, else the first `[piece.*]`;** floor pieces, stations and goals
  take `kind`; cost if wrong: a file that wants a different default has to list it first.
- **`[setup] piece` and config `[range] piece` replace the field's one kind** on the intake and station fields only,
  and `ball` stays the old spelling, both together being an error; cost if wrong: a game's field can't have its piece
  swapped from Setup.
- **Stations gain tip, turn and their spreads, and a non-ball station draws 11 numbers a feed** (a ball's nine are
  unchanged); cost if wrong: none for balls; a non-ball station's feeds differ if the order of draws is changed later.
- **Rods are capsule or box geoms** welded to the field, with the free end exactly at `tip`; cost if wrong: a round rod's
  tip is rounded, as a real branch's is, and a square rod's is flat.
- **Held is a set of tests every step** (touching, 1 cm off anything that isn't the robot, 0.05 m/s relative to the part, 0.1
  s, with how it ended read from what happened last); cost if wrong: a borderline grip reads held or slipped by one
  step's difference.
- **Seated depth is how far the rod's tip has gone into the hole,** measured while the rod runs through it (within 30
  degrees), empty otherwise, and "only N in on: could be knocked off" under a third of the way; cost if wrong: a
  piece in a grabber reads a depth before it is placed, and a one-third line is a judgment call.
- **Rests on is half the weight carried, at rest 0.5 s, no robot contact;** cost if wrong: a piece balanced on an edge
  can read rests-on or not by a hair.
- **"Where a piece should end up" keeps the `hopper` key** and gains the grabber words, a piece being in when its center
  is in a ticked body's box or it is held by one; cost if wrong: an old file's key doesn't match its new name.
- **Grip lives in the part folder's own edits** (`[body.<name>] grip`), set in the Part tab and in Setup, with a robot
  config's `grip` overriding it for sweeps, and tests don't set it; cost if wrong: two places to look for a grip.
- **The grabber starter is a short run of blocks** on the reef field for a part with a grabber word and no hook; cost if wrong: a
  grabber that closes the other way reads "never held" until the sign is flipped.
- **A slip is marked in the replay and the run keeps notes** (a list of marks, and the UNCALIBRATED grip lines); cost if
  wrong: none beyond the extra data in `result.json`.

### Rulings during the build

- **P1: the reef range and the grabber part (17a) came right after Task 8,** because later tasks needed them; cost if
  wrong: none beyond the order.
- **P2: pieces that are not balls are drawn in the replay,** because corals invisible in a replay defeat the point;
  cost if wrong: none.
- **P3: the ball-only golden reuses the kitbot run and adds the intake example,** not a Java run that is skipped in
  normal runs; cost if wrong: none (a golden that never runs proves nothing).
- **P4: the held sentence names the part the check named, not the jaw touched last,** and "is held" and "rests" are verbs
  the runner knows; cost if wrong: wording only.
- **P5: a spread's new tip column and orientation labels without "at"** ("missed standing on end"), the floor spread's default
  tip and turn coming from the piece's own pose; cost if wrong: wording only.
- **P6: the chute tests use the new signature, the sweeps take lists of material names,** and the grab-run helper is shared;
  cost if wrong: none.
- **P7: one grip friction table,** so the bench and the test range can't disagree; cost if wrong: bench numbers shift
  slightly (UNCALIBRATED either way).
- **P8: the example's coral lies in line, and its spread has no repeated placement,** because a coral lying across can't
  thread a 35 degree branch; cost if wrong: none.
- **P9: students read a piece by its kind's plain name and number ("coral 3"),** never "2025-coral" or "ball.12"; cost if
  wrong: wording only.
- **P10: the small findings in the pre-build review were settled by the task that owned them;** cost if wrong: a small
  wording or edge case differs from what the review suggested.
- **P11: per-kind numbers start at 1,** while the old one-kind "ball k" wording stays; cost if wrong: wording only.
- **P12: `piece_preset` returns the preset of the kind taken first (ball, then disc, then default);** cost if wrong: none
  for existing ranges.
- **P13: a station's default tip for a flat kind is lying flat,** matching the floor; cost if wrong: a default only.
- **P14: a start under a rod lower than 0.5 m is refused,** and higher rods are allowed, because the range doesn't know
  the robot's height; cost if wrong: a tall robot started under a rod between 0.5 and 1.4 m isn't caught.
- **P15: the chute floor's clearance is measured at right angles to the floor, worst case over the spreads,** not the
  brief's ball-only formula, which would start a coral inside the floor; cost if wrong: a chute floor sits a little lower
  than a hand calculation expects.
- **P16: each fed piece is set down just clear of the chute floor from its own orientation;** cost if wrong: a feed
  starts a little lower than the release point a file names.
- **P17: when spread labels collide the label adds what differs** ("lying flat, turned 45°"); cost if wrong: wording only.
- **P18: the inside-the-robot start check tests each piece solid's box against each robot box** (a separating-axis test);
  cost if wrong: a refusal near a thin part is stricter than it needs to be.
- **P19: rigid pieces have a firm contact by default,** so a held coral doesn't creep out of a squeeze and read as a slip
  that isn't there; cost if wrong: piece contacts are a little stiffer than real foam or PVC (UNCALIBRATED anyway).
- **P20: a jaw-to-jaw handoff is no new evidence and one dropped step doesn't end a hold;** cost if wrong: a real slip is
  reported one step later.
- **P21: "what was moving" counts a stopped block and any joint still turning;** cost if wrong: wording only.
- **P22: a grip on a pad sets its friction, a firm contact and priority 1,** so the pad's friction and a firm contact
  both apply against any piece; cost if wrong: none beyond P19's.
- **P23: the reef's branch lengths and post height are labeled estimates scaled from the manual's figure,** and the
  manual's heights win over Wikipedia's; cost if wrong: branch lengths off by an inch or two, which changes seated
  depths slightly.
- **P24: the grabber starter closes the jaws,** each jaw motor at 4 V first, so the starter works on the shipped example;
  cost if wrong: one sign to flip on some grabbers.
- **P25: the starter waits a second and lifts with the arm,** because "held" needs the piece off the floor; cost if wrong:
  on an arm where up is positive the starter swings down and reads "never held" until the angle is flipped.
- **P26: "held counts as in" applies to non-ball pieces only,** a ball keeping the box rule so the shipped intake spread
  keeps its results; cost if wrong: a ball held in a claw (not in a box) reads stuck, not in.
- **P27: the grip picker shows each friction estimate beside its name,** so two materials with the same number read the
  same; cost if wrong: a slightly longer label.
- **P28: a grip sweep can target a whole part,** and that is Setup's default, because sweeping one pad of two shows a false
  "no difference"; cost if wrong: none.
- **P29: a grip change goes on the Part tab's undo history in import mode too,** matching plugin mode; cost if wrong: none.
- **P30: station boxes read in pairs and plain words** ("Tipped up", "Facing", "Extra tumble"); cost if wrong: wording only.
- **P31: on a field with a grabber the station starter waits until a piece is held,** because the hopper starter can never
  succeed there; cost if wrong: none.
- **P32: one "Try materials" button per part,** because one per pad suggested a one-pad sweep; cost if wrong: none.
- **P33: "placed" is a fourth feed outcome** for a piece seated on, or resting on, something a check names, because a seated
  coral read "missed" over a passed placement check; cost if wrong: none.
- **P34: a held-throughout check ends, passed, when the piece is set down where a seated or rests-on check names,** or
  "held throughout the swing, then placed" couldn't be written; cost if wrong: a piece dropped on the named branch from
  too high still reads as held until it touched.
- **P35: the example's hinge is at 0.44 m and its spread distance 40 in,** because at 0.50 m the coral can never thread and
  at 24.8 in a turned coral starts inside the robot; cost if wrong: none.
- **P36: the plain place run's outcome stays "finished" with its check lines,** since a floor piece is tracked only in a
  spread; cost if wrong: a student reads three lines instead of one headline, and the example's README says why.
