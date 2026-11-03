# A pneumatic arm, tested with blocks

The mechanism: an arm on the stand-in robot, lifted by an air cylinder. When the cylinder pushes out, the arm swings
up. When it pulls back in, the arm comes down and rests level.

The question the test answers: lift the arm and put it down 10 times in a row. Does the air last, staying above the
60 psi the cylinder works at the whole time?

## The files

| File | What it is |
|---|---|
| `arm-standin.toml` | the stand-in box robot with a stock `arm` and a stock `cylinder` (`lift`) pushing it, and a `[pneumatics]` section with the defaults |
| `cycles.test.toml` | the blocks |

The arm is 0.5 m long. Its pivot is 0.30 m above the chassis's top and points forward. The cylinder has a 3/4 in. bore
and an 8 in. stroke. (The bore is the inside diameter of the cylinder's tube, where the air pushes on the piston; the
stroke is how far the rod moves out; the rod, 1/4 in. here, is the shaft that pushes the arm.) It stands almost straight
up on the chassis, 0.18 m in front of the pivot, and its rod's end is pinned to the arm 0.19 m out from the pivot.
Pulled in, the cylinder holds the arm level (0°). Pushed all the way out, it lifts the arm to about 76°.

The air is the defaults: two 574 mL tanks filled to 120 psi before the match, a VIAIR 90C compressor that turns on
below 95 psi, and a regulator that feeds the cylinder at 60 psi. Under the Test tab the valve needs no device ID and
the compressor is on the whole run.

Open the test with `scrimmage edit examples/blocks/pneumatic/cycles.test.toml`, or run it:

```bash
scrimmage test run examples/blocks/pneumatic/cycles.test.toml
```

## The blocks

1. Expect the air pressure in the tanks above 60 psi, throughout the run.
2. Repeat 10 times:
   1. Set `lift` to extend.
   2. Wait until `arm.pivot` is above 60° (give up after 3 s).
   3. Set `lift` to retract.
   4. Wait until `arm.pivot` is below 5° (give up after 3 s).

## What it does

```
finished
  passed: the air pressure is above 60 psi from 0.3 s on (block 1)
  air: 120 psi at the start, 67 psi at the end, lowest 67 psi at 4.1 s; the compressor ran 2.1 s
  note: Pneumatic numbers are estimates (UNCALIBRATED): see docs/pneumatics.md.
```

Each lift-and-drop cycle takes about 0.4 s, so the 10 are done by 4.1 s. The arm swings fast enough to carry the rod to
the end of its stroke most times. Every cycle uses about 5 psi from the tanks. The lowest pressure, 67 psi, is at the
end, so the air lasts, with 7 psi to spare.

`scrimmage air` gives the air budget for this robot:

```
$ scrimmage air examples/blocks/pneumatic/arm-standin.toml
Working pressure 60 psi; the switch stops the compressor at 115 psi.
Free air for one cycle (extend and retract):
  lift: 0.597 L
Stored air above the working pressure: 4.30 L
Cycles from the tanks alone: 7
Compressor air in a 150 s match: 13.2 L
Cycles per match, tanks and compressor: 29
Estimates (UNCALIBRATED): see docs/pneumatics.md.
```

It says 7 cycles from the tanks, but the test made 10. Two reasons. The tanks start at 120 psi, a little above the
115 psi the budget counts from. And the budget counts each side of the cylinder filled all the way to 60 psi every
time, but here the valve switches as soon as the arm gets past 60° or below 5°, before the cylinder has filled. With a
1 s wait after each switch, so the cylinder fills, each cycle uses about 8 psi, which is what the budget says. All of
these numbers are estimates (see `docs/pneumatics.md`), not measured on a real robot.

## Change it and run it again

**More cycles.** Change the repeat's times to 15. The arm gets up 11 times, then the tanks reach 60 psi at 4.6 s
and the check fails, saying when.

**Slower cycles.** Add a 1 s wait after each set block. Each cycle fills the cylinder and uses more air, but it takes
2 s, and the compressor has time to catch up: the 10 cycles take 19.6 s, the compressor runs 13.8 s, and the lowest
pressure is 70 psi.

## The numbers

The first try had a 2 in. bore. It lifts the arm easily, but each cycle takes about 4 L of air: `scrimmage air` said 1
cycle from the tanks and 4 in a whole match. In the test the arm got up twice, and the tanks fell to 60 psi at 2.0 s.
A 1-1/16 in. bore (the stock cylinder's) did better: `scrimmage air` said 4 cycles from the tanks, and the test got
the arm up 8 times before the tanks fell to 60 psi at 2.7 s.

A 3/4 in. bore still pushes about 118 N at 60 psi, plenty for a 2 kg arm, and uses about 0.6 L a cycle. Pick the
smallest cylinder that does the job: it saves air.
