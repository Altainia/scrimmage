# Picking a soft 2024 NOTE off the carpet, tested with blocks

The robot: the stand-in kitbot (a box chassis and a tank drive) with an over-under floor intake in front of its front
bumper. The intake is a part written by hand, `parts/intake/part.toml`.

The field: the built-in `2024` field. The robot starts 1.3 m behind the AMP-side blue WING NOTE, facing it, clear of
the STAGE. Near a robot the NOTE is a soft ring that bends and squashes
([The soft NOTE](../../../docs/test-range.md#the-soft-note)).

The question the test answers: does this intake pick the NOTE up off the carpet and carry it into its bin, and does it
still do that when the build, the driver and the NOTE's position are a little off?

No robot program is needed. The blocks drive the motors.

## The files

| File | What it is |
|---|---|
| `note-intake-standin.toml` | the robot: the kitbot's chassis and tank drive on `PWM:0` and `PWM:1`, and the intake on `PWM:2`, mounted on the carpet at the front bumper's face |
| `parts/intake/part.toml` | the intake: three 2 in rollers on one NEO, a sprung arm for the front roller, side guides, a ceiling and a bin |
| `intake.test.toml` | the test: drive at the NOTE with the rollers on, and expect it in the bin |

Open the test with `scrimmage edit examples/blocks/note-intake/intake.test.toml`, or run it:

```bash
scrimmage test run examples/blocks/note-intake/intake.test.toml
```

## The intake

Seen from the side, the NOTE goes in from the front (right to left on the robot):

- **The front roller** sits out in front, its bottom 1.2 in over the carpet. Its bottom moves back into the robot, so
  it grabs the top of the NOTE and drags it back.
- **The bottom roller** sits 3 mm over the carpet, 3.4 in (between centers) behind and below the front roller. Its top
  moves back into the robot. The NOTE's edge goes into the 1.4 in nip between the two rollers. The 2 in NOTE is
  squeezed 0.6 in there, and the bottom roller lifts it up onto the bin's floor.
- **The floor roller** sits over the front of the bin's floor and carries the NOTE the rest of the way in.
- **The bin** has a floor 2 in up, a back stop, side walls 16 in apart and a lid 2.1 in over the floor.
- One NEO at 3 to 1 drives all three rollers through couplings, at the same surface speed.

## The blocks

1. Spin `intake.roller` at 8 V.
2. Drive at 3.1 V for 2 s. That is about 1 m/s.
3. Wait until a NOTE is in `intake.hopper` (up to 3 s).
4. Stop `intake.roller`.
5. Wait 1 s.
6. Expect a NOTE in `intake.hopper`.

## What it does

It passes at 3.3 s:

```
1 of 1 made it in (100%)
  passed: a note reaches intake.hopper at 3.3 s (block 6)
  pieces:
    #  kind  from   fired s  exit m/s  outcome  where
    2  note  floor  -        -         in       in intake.hopper
```

The NOTE lifts the front roller's arm about 7 degrees on its way through the nip. With the rollers at 0 V, the robot
pushes the NOTE ahead of it and it never gets in: the test fails with `0 of 1 made it in (0%); 1 stuck in the robot`.

## How sure is it? The grids

One run passing doesn't say much. A real intake is built a little off, a driver comes in a little fast or crooked, and
the NOTE isn't always centered. So `tests/intake_grid.py` runs the test many times, each with the intake or the run
changed a little:

- **axle**: the front and floor rollers higher or lower, -0.5 to +0.5 in;
- **lip**: the bottom roller and the bin's floor front further forward or back, -1 to +1 in;
- **volts**: the rollers at 5 to 11 V;
- **speed**: the robot driving at 0.5 to 2 m/s;
- **yaw**: the intake turned on its mount, -15 to +15 degrees;
- **offset**: the NOTE off the robot's center line, -6 to +6 in;
- **clearance**: the bottom roller 1.5 to 10 mm over the carpet (3 mm as built).

There are three grids:

- **one setting at a time**: each setting at several values with the others as built (44 runs), plus the rollers at
  0 V, which must fail;
- **the 16 corners**: axle, lip, volts and speed all at their ends at once, with the yaw at -15, 0 and +15 in turn;
- **40 random draws**: every setting at once, picked at random (seed 7), with the offset within 4 in.

The target is every one-setting run passing (and the 0 V run failing), and at least 14 of the 16 corners.

What they found on these files (2026-11-13):

| Grid | Passed | Failing runs | Fastest NOTE segment |
|---|---|---|---|
| One setting at a time | 44 of 45 | only the 0 V run, as it should | 3.8 m/s |
| The 16 corners | 16 of 16 | none | 5.6 m/s |
| 40 random draws | 40 of 40 | none | 4.5 m/s |

The fastest segment is the fastest any piece of the soft ring moved in any run of the grid. A NOTE that is about to
blow up moves much faster. The soft NOTE's guard steps in above 25 m/s, and it never did in these runs.

Run the grids yourself (about 8 minutes) to see every run's line:

```bash
python tests/intake_grid.py all
```

## Why it is built this way

These rules made the difference between an intake that passes once and one that passes the grids.

- **A ceiling over the rollers.** The lid runs forward over every roller, 2.1 in over the floor. The rollers poke
  through it, which is fine: a robot's own parts don't touch each other. Without it, the NOTE's tail can buckle up
  between the rollers.
- **Thick parts where the NOTE is pushed.** Every surface the NOTE is pushed into is 5 cm thick: the walls, the back
  stop, the lid and the floor slab. The soft NOTE no longer goes through thin plates, since its squash stiffens
  sharply past about 60 % (docs/test-range.md, The soft NOTE). Thick parts are still good practice: they leave room to
  spare.
- **An over-under nip.** The front roller grabs the NOTE's top while the bottom roller lifts its edge. A single roller
  over a lip passed or failed with 1 cm of lip position.
- **The front roller is on a sprung arm.** The arm pivots on a hinge 3 in behind and 1.4 in above the roller. A spring
  holds it down on a stop with about 15 N at the roller, about what the NOTE pushes back with in the 1.4 in nip. A NOTE
  pushed into a tighter nip lifts the roller (up to 25 degrees) instead of jamming. Real over-under intakes have this.

  Why the arm ships: the same intake with a fixed front roller passed only 13 of the 16 corners. Every miss was a
  tight nip: moving the lip forward 1 in and the rollers down 0.5 in turns the 1.4 in nip into 0.3 in, an 85 %
  squeeze, and the NOTE stops on the front roller. Raising the fixed front roller 0.4 in made 14 of 16, right on the
  target, where one small change can flip a corner. With the arm, all 16 pass, and they still do with the spring at
  half or twice its stiffness (7 or 28 N·m/rad).

## The numbers

The intake's sizes, the arm and its spring are our choices. They are the design an intake study found and the arm it
needed. The foam's stiffness (2000 N/m) and bounce (0.3) are estimates until someone measures a real NOTE
([docs/measuring.md](../../../docs/measuring.md#note-squeeze)), and so are the rollers' and the polycarbonate's
friction. The run's notes say the foam numbers are UNCALIBRATED. The tight-nip corners pass or fail on how hard the
foam is at a deep squeeze, which is an estimate too.

The bench has the same nip: [`examples/bench/note_intake_flex.toml`](../../bench/note_intake_flex.toml) feeds the
flex NOTE into it on a moving carpet. Its front roller is fixed, since a bench wheel can't swing on an arm.
