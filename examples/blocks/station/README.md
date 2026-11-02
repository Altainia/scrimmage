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
| `hopper-standin.toml` | the stand-in box robot with the hopper on its top, on the `station` range, at `at_station` |
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

The test file also says which body is the hopper (`hopper = ["hopper.bin"]`), and its `[setup]` makes the station
less exact than the field file has it:

```toml
[setup]
seed = 1
station = { feeder = { spread_yaw_deg = 5.0, spread_pitch_deg = 5.0, spread_speed = 0.2 } }
```

A human player doesn't drop every ball the same way. The field's station just sets each ball down at the top of the
chute. With this spread, about half the balls get a push of up to 0.2 m/s as they're let go, aimed up to 5° off to
either side and up to 5° up or down. The seed picks which of those draws you get.
The same seed gives exactly the same feeds every run, so comparing two hoppers is fair.

"In the hopper" means a ball's center is inside the box around the bin's pieces. The last check is made after the run
has waited for every fed ball to settle, so a ball still bouncing when the blocks end gets its outcome too: in the
hopper, stuck somewhere else on the robot, or missed.

## What it does

```
5 of 5 made it in (100%)
  passed: at least 80% made it in at 8.0 s (block 4)
```

The run's clock starts at 0.3 s (the robot settles on its wheels first), and the first ball leaves the station then.
Each ball takes about 0.6 s to roll down the chute and fly over the bin's front wall, lands about 0.3 m in, and
bounces off the back wall. The fifth is in at about 5 s, the feeding stops, and the run waits for the balls to come to
rest before the last check.

**Is it luck?** One seed is one draw. **New shuffle** in the Setup column picks a new seed (it changes `seed` in
`[setup]`), so you can check the result wasn't a lucky draw. To try many at once, use **Try values…** on the seed:
one run per seed, each with a row in the table. Seeds 1 to 8 all give 5 of 5 today.

## Change it and run it again

**How many to wait for.** Change the wait block's `at_least`. The more you wait for, the more balls land on top of
the ones already in, and the more likely one bounces out.

**The check.** Change `at_least = 80.0` in the last block. A ball that bounces out counts against it: `5 of 6 made it
in (83%)` still passes 80%, `5 of 7 (71%)` doesn't.

**The station.** In the Setup column, the station's row has its interval, stock and spreads. Make the spread wider
(say 10° of yaw) and try a few seeds to see where balls start to miss. Without the spread (delete the `station = ...`
line), every ball is fed the same way and every seed gives the same run.

## The numbers

Every size in `parts/hopper/part.toml` is our choice: the bin is 64 cm long and 60 cm wide, its floor on the chassis's
top, its front and side walls 30 cm high and its back wall 50 cm high.

The first try had 30 cm walls all round. With the same spread, 2 of the 8 seeds lost a ball: seed 3 made 5 of 6 (83%)
and seed 7 made 4 of 5 (80%). Each time, a ball landed on the balls already in the bin, bounced about 0.4 m above the
bin's floor, went over the back wall and rolled away across the carpet. Both still passed 80%, but only just, so we
raised the back wall to 50 cm. Now a ball coming in off another one hits the wall instead of clearing it, and all 8
seeds make 5 of 5. A ball landing on the balls already in the bin bounces higher than one landing on the floor: a
hopper that is fine with one ball in it can lose balls once it fills.
