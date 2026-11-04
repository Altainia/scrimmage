# Under the trench, tested with blocks

The mechanism: an elevator on the stand-in robot, with an arm on its carriage. In the 2026 game a robot can drive
under a TRENCH, but the space under its arm is only 22.25 in tall.

The question the test answers: with the elevator down and the arm down (stowed), does the robot fit under the trench,
and by how much? And what happens if a driver forgets to put the arm down first?

## The files

| File | What it is |
|---|---|
| `trench-standin.toml` | the stand-in box robot with a stock `elevator` (3 stages, 0.6 m of travel) and a stock `arm` (0.5 m long) on its carriage, both with `collide = true`, on the `2026-trench` range |
| `clear.test.toml` | the blocks: drive under the trench stowed |
| `raised.test.toml` | the same drive with the arm raised straight up first |

`collide = true` makes a part touch the field. A stock part doesn't unless it says so, and the Test tab only measures
the clearance of parts that touch. The robot starts 1.5 m before the trench, centered on the opening, facing through
it. Lowered, the elevator's top is 0.49 m (19.3 in) above the carpet. The arm rests level on its lowest stop, at 0°.

Open the test with `scrimmage edit examples/blocks/trench/clear.test.toml`, or run it:

```bash
scrimmage test run examples/blocks/trench/clear.test.toml
```

## The blocks

`clear.test.toml`:

1. Expect the robot to clear `trench_arm` by at least 0.5 in, throughout the run.
2. Drive forward 4 m at 4 V, under the trench and out the other side.

"Clears by" is the smallest gap between any part of the robot that touches things and the trench's arm.

## What it does

```
finished
  passed: the robot clears trench_arm by at least 0.5 in from 0.3 s on (block 1)
  closest to trench_arm: 2.9 in at 2.5 s
```

The robot fits with 2.9 in to spare. The closest part is the top of the elevator, while it is under the trench's arm.
That's the 22.25 in opening less the elevator's 19.3 in. The check asks for 0.5 in, less than that, so the robot can
bounce a little as it drives and still pass. These numbers are estimates from the simulation, not from a real robot.

## The arm raised: the check fails

`raised.test.toml` adds one block at the start: move `arm.pivot` to 90°, straight up, as if the robot had just scored
and the driver didn't stow the arm. Its tip is then about 0.64 m (25 in) up, higher than the opening.

```
a check failed (block 2): expected the robot to clear trench_arm by at least 0.5 in, but it was 0.45 in at 1.7 s
  failed (block 2): expected the robot to clear trench_arm by at least 0.5 in, but it was 0.45 in at 1.7 s
  closest to trench_arm: 0.4 in at 1.7 s
```

The arm's tip gets within 0.5 in of the front of the trench at 1.7 s, and the check stops the run right there. That
is the check doing its job: it tells you the robot doesn't fit before anything breaks.

Delete the expect block and run it again to see what would happen. The arm runs into the trench at 1.7 s and the
robot stops against it: the drive times out, `didn't reach 4 m in 10 s (went 1.50 m)`. On a real field that is a
bent arm, or a robot stuck in front of the trench.

What a student would change: put a "move `arm.pivot` to 0°" block before the drive, so the arm is down before the
robot reaches the trench. Or keep it lower: at 50° the robot still fits, with 1.3 in to spare; at 55° the check
fails.

## Change it and run it again

**How high the arm can be.** In `raised.test.toml`, open the move block's menu and choose **Try values…** for its
angle, for example from 30° to 60° in steps of 5. Up to 40° the closest part is still the elevator (2.9 in); at 45°
it's the arm's tip (2.5 in), at 50° 1.3 in, and from 55° on the check fails.

**The elevator.** Change the elevator's `travel` in `trench-standin.toml`. Its stages are a third of the travel plus
0.15 m tall, so more travel makes the lowered elevator taller. At 0.75 m it is 0.40 m tall, its top 21.3 in up, and
it would have only about 1 in to spare.
