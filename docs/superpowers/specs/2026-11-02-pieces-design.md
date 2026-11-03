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
