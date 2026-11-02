# A ball intake, tested with blocks

The mechanism: a roller across the front of the robot, 14 cm off the carpet at its underside, and behind it a bin
with a low round lip along its front edge. A 15 cm ball fits under the roller only by squeezing a little. Spinning
forward, the roller's underside moves back toward the robot, so it grabs the top of the ball, pulls it in under the
roller and rolls it up over the lip into the bin. Pushed by the robot alone (the roller off), the ball just rolls away
from the lip.

The question the test answers: does the roller take a ball off the carpet when the robot drives at it, and does the
ball stay in the bin once the robot stops?

It's the second worked example of the Test tab (the first is `../ladder_climber`, a climber). Nothing here is about
climbing: no hooks, no bars, just a motor, a drive and a ball.

## The files

| File | What it is |
|---|---|
| `parts/intake/part.toml` | the intake, written by hand: a frame, the bin (body `hopper`) and the roller (joint `roller`, a NEO at 3 to 1) |
| `intake-standin.toml` | the stand-in box robot with the intake in front of its front bumper, on the `intake` range (one ball 1.5 m in front of the start). The roller motor is on `PWM:2`, the first PWM channel the drive leaves free |
| `intake.test.toml` | the blocks |

The ball moves only by physics: the robot has no `[shooter]`, so nothing takes balls out of the world, and the ball
gets into the bin because the roller and the lip touch it.

Open the test with `scrimmage edit examples/blocks/intake/intake.test.toml`, or run it:

```bash
scrimmage test run examples/blocks/intake/intake.test.toml
```

## The blocks

1. Spin `intake.roller` at 8 V (it keeps spinning until the end).
2. Drive forward for 2 s at 4 V, at the ball.
3. Wait until a ball is in `intake.hopper` (give up after 5 s).
4. Wait 1 s, so the robot has stopped.
5. Expect a ball in `intake.hopper`.

"In `intake.hopper`" means the ball's center is inside the box around the bin's pieces.

## What it does

`finished` at 3.3 s, with `passed: a ball reaches intake.hopper`. The run's clock starts at 0.3 s (the robot settles
on its wheels first). The roller reaches the ball at about 1.0 s, pulls it in under itself and over the lip by about
1.3 s, while the robot is still driving, and the lid stops it bouncing out. It ends resting on the bin's floor.

## Change it and run it again

**The roller's voltage.** In the Test tab, open the spin block's menu and choose **Try values…** for its volts, for
example from 0 to 12 V in steps of 2. Each value is one run, with a row in the table and a replay. What it shows today
(driving at 4 V):

| roller | result |
|---|---|
| 0 V or 2 V | timed out: the robot pushes the ball ahead of it |
| 3 V to 12 V | finished: the ball is in the bin |

At 3 V the ball is in only after the robot stops (4.3 s instead of 3.3 s). To change the voltage for good, type it
into the block, or edit `volts` in the first block of `intake.test.toml`.

**How high the intake is.** In the Setup column, the intake's row has its position: `z (m)` is how far it is moved
up. Type a number to move it, or press **Try values…** on that row and choose "up (z)". At 8 V on the roller:

| moved up | result |
|---|---|
| -0.01 m to +0.01 m | finished |
| +0.015 m or more | timed out: the roller is now above the ball's top and doesn't grip it |

That's the same as `pos = [0.43, 0.0, 0.0]` in `intake-standin.toml`'s intake part (the last number is up).

**Something else to try.** The drive's voltage (the drive block's **Try values…**): from 2 V to 8 V the ball is
taken (at 8 V on the roller). The roller's position, size or grip (`pos`, `cylinder` and `friction` in `parts/intake/part.toml`), or the lip,
are part changes: edit the file and run the test again.

## The numbers

Every size in `parts/intake/part.toml` is our choice, not a real robot's: the bin is 22 cm deep and 36 cm wide inside
with its floor 3 to 4 cm off the carpet, the lip is a 2 cm rod, the roller is 6 cm across with a friction of 1.2.
Two tries before this one didn't work, and why is worth knowing:

- With the roller 5 cm closer to the bin, a ball got wedged between the roller and the bin's front edge: the roller
  pressed it down onto the carpet instead of pulling it in, and stalled below 8 V. The roller has to be far enough in
  front that the ball is under it before it touches the bin.
- With a flat floor and no lip, a ball that got in rolled back out when the robot slowed down. A square lip was no
  better: the ball came to rest balanced on top of it. A round lip doesn't let a ball rest on it.
