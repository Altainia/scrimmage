# A hopper fed from the human player station, tested with blocks

The mechanism: an open-topped bin over the whole top of the stand-in robot. The robot waits with its bumpers against
the human player station's chute, and the human player rolls FUEL down the chute into the bin.

The question the test answers: start feeding, wait until 5 balls are in the hopper, stop feeding. Did at least 80% of
the balls the station fed end up in the hopper?

It's the third worked example of the Test tab, and the first with a station. The robot doesn't move, so there's no
robot code to write: the station does the feeding.

## The files

| File | What it is |
|---|---|
| `parts/hopper/part.toml` | the hopper, written by hand: one body `bin`, a floor and four walls |
| `hopper-standin.toml` | the stand-in box robot with the hopper on its top (`drive.top`), on the built-in `station` range, starting `at_station` |
| `feed.test.toml` | the blocks |

The `station` range has one station, `feeder`, at the blue alliance wall. It drops a ball 1.1 m up onto a 0.5 m
chute sloped at 30°, one ball a second. The chute's low end is 0.73 m out from the wall and 0.76 m up, and the
`at_station` start puts the robot's front bumper right there, facing the station.

Open the test with `scrimmage edit examples/blocks/station/feed.test.toml`, or run it:

```bash
scrimmage test run examples/blocks/station/feed.test.toml
```

## The blocks

1. Start feeding from `feeder` (a ball a second, until the stop).
2. Wait until at least 5 balls are in the hopper (give up after 20 s).
3. Stop feeding from `feeder`.
4. Expect at least 80% of the fed balls made it in.

The test file also says which body is the hopper (`hopper = ["hopper.bin"]`) and the seed for the station's random
spread (`[setup] seed = 1`).

"In the hopper" means a ball's center is inside the box around the bin's pieces. The last check is made after the run
has waited for every fed ball to settle, so a ball still bouncing when the blocks end gets its outcome too: in the
hopper, stuck somewhere else on the robot, or missed.

## What it does

```
5 of 5 made it in (100%)
  passed: at least 80% made it in at 6.8 s (block 4)
```

The run's clock starts at 0.3 s (the robot settles on its wheels first), and the first ball leaves the station then.
Each ball takes about 0.6 s to roll down the chute and fly over the bin's front wall, lands about 0.3 m in, and
bounces off the back wall. The fifth is in at about 5 s, the feeding stops, and the run waits for the balls to come to
rest before the last check.

The station on this field feeds every ball the same way (its spreads are 0), so every seed gives the same run. To see
the seed matter, give the station a spread in the Setup column, for example 5° of yaw and pitch and 0.2 m/s of speed
(in the test file: `[setup.station.feeder]` with `spread_yaw_deg = 5.0`, `spread_pitch_deg = 5.0` and
`spread_speed = 0.2`). With that, seeds 1 to 8 all gave 5 of 5.

## Change it and run it again

**How many to wait for.** Change the wait block's `at_least`. The more you wait for, the more balls land on top of
the ones already in, and the more likely one bounces out.

**The check.** Change `at_least = 80.0` in the last block. A ball that bounces out counts against it: `5 of 6 made it
in (83%)` still passes 80%, `5 of 7 (71%)` doesn't.

**The station.** In the Setup column, the station's row has its interval, stock and spreads. **Try values…** on the
seed runs the test once per seed, so you can see how often a spread makes a ball miss.

## The numbers

Every size in `parts/hopper/part.toml` is our choice: the bin is 64 cm long and 60 cm wide, its floor on the chassis's
top, its front and side walls 30 cm high and its back wall 50 cm high.

The first try had 30 cm walls all round, and it made 5 of 6 (83%): the fourth ball landed on the third, bounced about
0.4 m above the bin's floor, over the back wall, and rolled away across the carpet. The check still passed, but only
just, so we raised the back wall to 50 cm, where a ball coming in off another one hits it instead of clearing it. A
ball landing on the balls already in the bin bounces higher than one landing on the floor: a hopper that is fine with
one ball in it can lose balls once it fills.
