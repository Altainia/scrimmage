# An intake that stops on a beam break, tested with blocks

The mechanism: the ball intake of `../intake` (a roller across the front of the robot that rolls a ball up over a low
lip into a bin), with a beam break across the bin. A beam break is a small light and a receiver facing each other: a
ball between them breaks the beam. Here the beam runs from one side wall of the bin to the other, so it breaks when a
ball is in the bin, and not before.

The question the test answers: drive at a ball with the roller on, and stop everything as soon as the beam breaks.
Does the beam break when the ball is in, and is the ball still in the bin once the robot has stopped?

It's the worked example for [sensors](../../../docs/sensors.md). The intake itself, and why its numbers are what they
are, is in `../intake/README.md`.

## The files

| File | What it is |
|---|---|
| `parts/intake/part.toml` | the intake, the same as `../intake`'s: a frame, the bin (body `hopper`) and the roller (joint `roller`, a NEO at 3 to 1) |
| `beam-standin.toml` | the stand-in box robot with the intake in front of its front bumper, on the `intake` range (one ball 1.5 m in front of the start), and a `[[sensor]]`: the beam break `bin` |
| `stop.test.toml` | the blocks |

The beam break, from `beam-standin.toml`:

```toml
[[sensor]]
name = "bin"
kind = "beam"
body = "intake.hopper"
from = [0.12, 0.17, 0.08]
to = [0.12, -0.17, 0.08]
dio = 4
```

It is mounted on the bin (`intake.hopper`), and its two ends are in the intake's coordinates: x forward from the
bumper's face, y left, z up, in meters. So the beam crosses the bin 12 cm in front of the bumper and 8 cm off the
carpet. A ball resting on the bin's floor (its center 11.5 cm up) breaks it. A ball on the carpet in front of the
robot, or still on the lip, doesn't.

Open the test with `scrimmage edit examples/blocks/beam/stop.test.toml`, or run it:

```bash
scrimmage test run examples/blocks/beam/stop.test.toml
```

## The blocks

1. Spin `intake.roller` at 6 V.
2. and 3. Spin both sides of the drive (`dt.left_2` and `dt.right_2`) at 4 V, so the robot drives at the ball. They
   keep going until a later block stops them. (A **drive** block would finish its drive before the next block starts,
   and the robot couldn't stop the moment the beam breaks.)
4. Wait until sensor `bin` is on, that is, until the beam is broken (give up after 5 s).
5. Stop all motors: the roller and the drive.
6. Wait 1 s, so the robot and the ball have stopped.
7. Expect sensor `bin` still on.
8. Expect a ball in `intake.hopper`.

Blocks 4 and 5 are what robot code does with a beam break: read it every loop, and stop the intake when it reads
broken. The test does it with no robot code.

## What it does

`finished` at 3.0 s, with both checks passed:

```
finished
  passed: bin is on at 3.0 s (block 7)
  passed: a ball reaches intake.hopper at 3.0 s (block 8)
```

The run's clock starts at 0.3 s (the robot settles on its wheels first). The roller reaches the ball at about 1.0 s.
For most of a second the ball rides on the lip, squeezed under the roller, while the robot carries it along. Then it
rolls down into the bin, and **the beam breaks at 2.0 s**. The blocks stop the roller and the drive at once. The
robot rolls on about 19 cm before it stops, and the ball comes to rest on the bin's floor, **its center 13 cm in front
of the bumper's face**, 1 cm in front of the beam, so the beam stays broken. Once broken, it reads broken to the end
of the run: it never flickers clear.

The chart shows `sensor.bin` (the test's `chart` line names it): 0 until 2.0 s, and 1 from then on. In the replay, the
beam is drawn as a line across the bin, lit from 2.0 s.

The robot code that does the same, with the beam break on DIO 4 and the roller on PWM 2 (as `beam-standin.toml`
binds them). The beam break reads `false` while the beam is broken, as an IR beam-break receiver does
([sensors.md](../../../docs/sensors.md#robot-code) has more):

```python
import wpilib


class MyRobot(wpilib.TimedRobot):
    def robotInit(self):
        self.bin_beam = wpilib.DigitalInput(4)
        self.roller = wpilib.PWMSparkMax(2)

    def teleopPeriodic(self):
        ball_in = not self.bin_beam.get()           # false while broken: a ball is in the bin
        self.roller.set(0.0 if ball_in else 0.5)    # 0.5 is about 6 V on a full battery
```

## Change it and run it again

**Where the beam is.** A beam break at the intake's mouth, between the lip and the roller, sees the ball sooner. In
`beam-standin.toml`, change the beam to:

```toml
from = [0.32, 0.17, 0.05]
to = [0.32, -0.17, 0.05]
```

and run the test again. It fails:

```
a check failed (block 8): no ball was in intake.hopper at 2.0 s
```

The beam breaks at 1.0 s, when the ball first gets under the roller. The blocks stop everything there, and the ball is
left on the carpet against the lip, in front of the bin (its center 30 cm in front of the bumper's face). Block 7
passes: the ball is still on the beam. A beam break tells you that something is where the beam is, so put it where the
ball should end up.

**The roller's voltage.** In the Test tab, open the first spin block's menu and choose **Try values…** for its volts,
from 0 to 12 V in steps of 2. Each value is one run, with a row in the table and a replay. What it shows today:

| roller | result |
|---|---|
| 0 V | timed out at 5.3 s: the robot pushes the ball ahead of it on the carpet, and the beam never breaks |
| 2 V | timed out at 5.3 s: the ball gets onto the lip but the roller is too weak to roll it in |
| 4 V to 12 V | finished: the beam breaks between 1.3 s and 2.0 s, and the ball is in the bin |

How long the ball rides on the lip before it rolls in changes from one voltage to the next without a pattern (the beam
breaks at 1.7 s at 4 V, 2.0 s at 6 V, 1.3 s at 8 V): it rattles between the roller, the lip and the lid. The
`../intake` README says more about that.

**Something else to try.** Take block 5 (stop all) out. The run still passes, since the ball is in the bin by then,
but the robot drives on and the roller keeps spinning to the end. Stopping at the beam is what keeps a real intake
from pulling in a second ball on top of the first.

## The numbers

The intake's sizes are our choice, from `../intake`. The beam is where we put it: across the middle of the bin, low
enough that a ball resting on the floor crosses it. The beam break is ideal: it reads broken whenever a ball touches
the line between its two ends (1 ms behind the physics, the length of one step), with no noise, and it sees only game
pieces, never the robot's own parts. See [sensors.md](../../../docs/sensors.md#what-the-readings-are) for how every
sensor is read.
