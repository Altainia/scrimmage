# Climbing a 2024 CHAIN, tested with blocks

The robot: a 60 kg stand-in box robot with one stock climber. The climber is a telescope with two hooks on top. The
hooks open toward the front.

The field: the built-in `2024` field. Its CHAINS hang between the STAGE legs, and they sag. The robot starts in front
of the blue STAGE's center CHAIN, `chain_blue_center`, facing it.

The questions the tests answer:

- Can this climber hang from the CHAIN with no part of the robot on the carpet? That is ONSTAGE, 3 points.
- With a partner robot already hanging on the same CHAIN, does it still work? Two robots on one CHAIN score HARMONY,
  2 more points.

No robot program is needed. The blocks drive the motors.

## The files

| File | What it is |
|---|---|
| `climber-standin.toml` | the robot: a stock chassis, a drive, and a two-stage climber on `PWM:2`. It starts at `blue_chain_center`, 0.31 m in front of the CHAIN's low point |
| `onstage.test.toml` | the climb on its own |
| `harmony-climber.toml` | the same robot with a third climber stage, starting 0.45 m to the side (see below) |
| `harmony.test.toml` | the climb in a match, with a partner hanging on the CHAIN |

Open a test with `scrimmage edit examples/blocks/chain-climb/onstage.test.toml`, or run it:

```bash
scrimmage test run examples/blocks/chain-climb/onstage.test.toml
```

## The blocks

1. Move `c.stage1` to 0.14 m. The hooks' tips go up to about 3 cm above the CHAIN.
2. Drive 0.31 m at 1.5 V. The CHAIN goes into the hooks. Drive gently: fast, the hooks can land beside it.
3. Move `c.stage1` to 0. The hooks pull down onto the CHAIN, and the robot comes up off the carpet.
4. Wait until a hook touches `chain_blue_center`.
5. Wait 5 s.
6. Expect ONSTAGE, and expect the robot to hang from `chain_blue_center`.

## What it does

`finished` at 6.9 s, with both checks passed. Watch the CHAIN in the replay: it is drawn as a row of beads. It sags
under the robot, and the robot swings a little before it settles.

## The HARMONY test

`harmony.test.toml` is the same climb, played as a match (`match = true`). In its Setup, a partner robot hangs on the
same CHAIN, 17.7 in (0.45 m) from the low point:

```toml
[setup]
partner = { hang = "chain_blue_center", along_in = 17.7, across_in = -2.0 }
```

It expects 5 or more points in the endgame. The result:

```
5 points: 0 in auto, 0 in teleop, 5 in endgame
  climbed to ONSTAGE: 3 points
  HARMONY with 1 other robot: 2 points
```

The whole match runs to its end, so this test takes about a minute and a half.

**Why the partner hangs where it does.** On a real field, two robots on one CHAIN slide toward its low point and end
up side by side. Here a hook stays at the link where it caught. It doesn't slide. So the test puts both robots where
they would end up: the partner 0.45 m to one side of the low point, and this robot 0.45 m to the other side.

It can't go much further out. The STAGE legs are where FIRST's field drawings put them, at the ends of the CHAIN. A
partner more than about 0.65 m (25.6 in) from the low point starts inside a leg, and the test won't run. Try
`along_in = 35.4` to see the message.

It can't go much closer in either. Your robot is checked as a 0.9 m square at its start. A partner nearer than about
0.86 m to it starts inside it, and the test won't run.

`across_in = -2.0` moves the partner 2 in away from the STAGE's core. Right under the CHAIN, it would touch the core.

**Why this robot has a third stage.** The partner weighs 60 kg. It pulls the CHAIN tight, so the CHAIN is higher where
your robot hooks. The two-stage climber can't reach above it any more. `harmony-climber.toml` has three stages, and the
first block moves `c.stage1` to 0.19 m instead of 0.14 m. Below about 0.18 m the hooks stay under the CHAIN, and the
wait for a hook times out.

## Change it and run it again

- **The reach.** In `onstage.test.toml`, 0.16 m works too. At 0.12 m the hooks go under the CHAIN, and the wait for
  a hook times out.
- **The drive.** 0.29 m and 0.33 m work too. The hooks' throats are wide enough for a little error.
- **The partner.** In the HARMONY test, `along_in = 25.6` works too. At 26.6 the partner starts inside a STAGE leg.

## The numbers

The robot's sizes are our choices. They are the robot from scrimmage's own chain tests. The CHAIN's numbers are from
FIRST: it hangs from 1.18 m, its ends 2.42 m apart, down to 28.25 in at its low point. Its weight and how stiff its
contact is are estimates. HARMONY counts only your robot and the partner, since there are no other robots.
