# A coral grabber, tested with blocks

The mechanism is an arm on a hinge, 44 cm up a mast at the front bumper, with two jaws at its end. Each jaw is a pad
on a slide with its own motor. The jaws close on a coral lying on the carpet in line with the robot (along its
heading). The arm then swings the coral forward, up and over the top. At -145° the coral's axis slopes down ahead of
the robot at about 35°, the slope of a 2025 L2 branch. The robot drives onto the branch and the jaws let go.

The question the test answers: does the grabber pick the coral up, hold it the whole way, and leave it seated on the
L2 branch?

This is the Test tab's worked example for pieces that aren't balls. It uses the `reef` field: one pipe of the 2025
reef with its L2, L3 and L4 branches at the 2025 manual's heights and angles, one coral on the carpet in front of the
robot, and a coral station.

## The files

| File | What it is |
|---|---|
| `parts/grabber/part.toml` | the grabber, written by hand: a mast, the arm (joint `arm`, a NEO at 100 to 1) and two jaws (joints `jaw_left` and `jaw_right`, a NEO each), each with a pad |
| `grabber-standin.toml` | the stand-in box robot with the grabber at its front bumper, on the `reef` field, starting at `facing_coral`. The arm and jaws are on `PWM:2`, `PWM:3` and `PWM:4` |
| `place.test.toml` | the blocks: pick the coral up and place it on `branch_l2` |
| `spread.test.toml` | a pickup test run with the coral lying at six different angles (a floor spread) |

The coral moves only by physics. Nothing in the robot or the field carries it: the pads squeeze it, the arm swings it
and the branch takes it.

Open the test with `scrimmage edit examples/blocks/reef/place.test.toml`, or run it:

```bash
scrimmage test run examples/blocks/reef/place.test.toml
```

## The blocks

1. Spin `grabber.jaw_left` and `grabber.jaw_right` at 4 V. Positive volts close a jaw, and they keep squeezing.
2. Wait 1 s for the jaws to close on the coral.
3. Move `grabber.arm` to -30°, lifting the coral off the carpet.
4. Wait until a coral is held by `grabber` (give up after 5 s).
5. Expect a coral held by `grabber`, **throughout**.
6. Move `grabber.arm` to -145°, over the top.
7. Drive forward 2.65 m at 3 V, onto the branch.
8. Wait 0.5 s.
9. Spin both jaws at -2 V to open them, then wait 2 s.
10. Expect a coral seated at least 3 in on `branch_l2`.
11. Expect a coral resting on `branch_l2`.

"Held" means off whatever is under it, touching the grabber and moving with it. "Throughout" means it is checked every
step from then on. A held check normally ends the run the moment the coral is let go. Here the coral is set down on
`branch_l2`, a place the test's own seated and rests-on checks name, so the held check ends there and passes. If the
coral slipped out on the way, or were set down anywhere else, it would fail.

## What it does

The run finishes at 7.4 s:

```
finished
  passed: a coral is held by grabber from 1.4 s until it was placed on branch_l2 at 4.4 s (block 6)
  passed: a coral is seated at least 3 in on branch_l2 at 7.4 s (block 13)
  passed: a coral rests on branch_l2 at 7.4 s (block 14)
```

In the replay:
- The jaws close and the arm lifts the coral by 1.4 s.
- The arm swings it over the top, and the robot drives 2.65 m toward the reef.
- At 4.4 s the branch's tip goes into the coral's low end. That is the moment it was "placed".
- The robot keeps driving a few more centimeters, and the branch turns the coral steeper in the jaws.
- When the jaws open, the coral slides down the branch until its rim meets the post. It ends seated 9.2 in of the
  branch's 11 in, lying along it at 35°.

**Why the robot drives past the first touch.** Driving level moves the coral across the branch's slope as well as
along it. The coral's 4 in hole leaves only about 1.2 in of room around the 1.66 in pipe, so the coral goes about 2 in
on before its wall meets the branch. If the jaws open there, its middle is still short of the branch's tip. It tips
level and hangs about 5 in on, which passes the 3 in check but isn't really placed. Driving on lets the branch turn the
coral in the jaws until its middle is past the tip, and then it slides down. Drives from 2.60 m to 2.70 m at 3 to
4 V all place it at 9.2 in. Below 3 V the robot can't push on, and the drive times out at 2.58 m.

**Why the mast is 44 cm.** At -145° the coral's slope matches the branch, but its height comes from the mast. The
first version had the hinge at 50 cm. That held the coral 14 cm above the branch's line, where no drive could thread
it.

## Change it and run it again

**Would grip tape help?** In Setup, the grabber's pads have a grip picker, and **Try materials…** beside the first one
runs the test once per material on every pad of the grabber. At 4 V on the jaws every material places the coral. So
does the pads' default friction (an uncalibrated estimate, which the run's note says). The squeeze is what matters:

| jaws | grip tape, rubber, nitrile | polycarbonate, aluminum | HDPE / UHMW |
|---|---|---|---|
| 4 V | placed | placed | placed |
| 1 V | placed | placed | the coral slips out as the arm lifts it |
| 0.5 V | placed | slips out as the arm lifts it | never held |

So grip tape doesn't change this test at 4 V. It would let you squeeze softer, about half a volt instead of a volt
with slick pads. To try that, set both jaws' volts lower and use Try materials again. A pad's grip is kept as
`[body.jaw_left] grip = "grip-tape"` in `parts/grabber/scrimmage-edits.toml`, which the picker writes for you.

**Something else to try:**
- The drive's distance (its **Try values…**): from 2.40 m to 2.55 m the coral ends up hanging level on the tip.
- The arm's angle: -140° and -150° place it too, with the 2.65 m drive.

## Lying at an angle: a floor spread

`spread.test.toml` asks whether the grabber can pick up a coral that isn't lying straight. The field's coral sits
between the open jaws, so a turned coral would be inside the grabber. In this test the coral is 40 in ahead of the
robot, and the robot drives 0.386 m up to it before the jaws close. The spread turns the coral to six angles:

```toml
[setup]
floor_spread = { distance_in = [40.0], rotation_deg = [0.0, 10.0, 15.0, 20.0, 45.0, 90.0] }
```

```bash
scrimmage test spread examples/blocks/reef/spread.test.toml
```

What it shows today:

```
picked up 3 of 6; missed lying at 20°, lying at 45° and lying across
  lying in line: picked up (1 of 1 made it in (100%))
  lying at 10°: picked up (1 of 1 made it in (100%))
  lying at 15°: picked up (1 of 1 made it in (100%))
  lying at 20°: missed (waiting for a coral to be held by grabber timed out at 7.2 s (block 6); 0 of 1 made it in (0%); 1 missed)
  lying at 45°: missed (waiting for a coral to be held by grabber timed out at 7.2 s (block 6); 0 of 1 made it in (0%); 1 missed)
  lying across: missed (waiting for a coral to be held by grabber timed out at 7.3 s (block 6); 0 of 1 made it in (0%); 1 missed)
```

The misses are a fact about the jaws, not luck. Open, they are 17.4 cm apart, and each pad is 20 cm long. A coral
4.5 in (11.4 cm) across, turned 20° from the robot's path, covers 20 sin 20° + 11.4 cos 20° = 17.5 cm across the
pads' length. That's wider than the gap, so the pads land on it instead of either side of it. At 15° it covers
16.2 cm and fits. Lying across, a coral can't be picked up this way at all, and couldn't go onto a 35° branch either.
To take a turned coral, the jaws would have to open wider or be shorter, or the robot would have to turn to face the
coral first.

The coral standing on end isn't in this spread. Its turn makes no difference to how it stands, so every turn would
read the same "standing on end". Try it on its own with `floor_spread = { distance_in = [40.0], tip_deg = [90.0] }`:
it is picked up, the jaws closing around it like a post.

## The numbers

- Every size in `parts/grabber/part.toml` is our choice, not a real robot's.
- The field's branch heights, angles and pipe size come from the 2025 game manual. The branches' lengths and the
  post's height are estimates from its figures (see the comments in the `reef` field file).
- The coral is the `2025-coral` preset: 11 7/8 in long, 4.5 in outside and 4 in inside, 0.66 kg.
- The pads use the default friction, an estimate. Measure yours with a sled pull before trusting a squeeze to a volt.
