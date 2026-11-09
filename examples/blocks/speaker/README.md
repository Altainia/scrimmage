# A NOTE into the SPEAKER, tested with blocks

The mechanism: a stand-in robot with a NOTE shooter, a stock `pinch_shooter`. A top and a bottom wheel squeeze the
NOTE's two faces and drive it out along a channel that tilts from 30° to 70° on a motor. A loader roller behind it
pushes the NOTE in. The field is the built-in `2024` range, the FRC 2024 CRESCENDO field
([docs/test-range.md](../../../docs/test-range.md#the-2024-field)).

The question the test answers: from against the blue SUBWOOFER, at what tilt and wheel speed does the NOTE go into
the blue SPEAKER? The SPEAKER's opening is entered going up, under its hood, and it is only 4.7 in longer than the
NOTE is wide.

## The files

| File | What it is |
|---|---|
| `note-shooter-standin.toml` | the stand-in box robot with the `pinch_shooter` (4 in wheels, a 1.25 in gap, a KrakenX60 on each wheel, the tilt on PWM 4) and the loader, `[shooter] kind = "piece"`, on the `2024` range at `blue_subwoofer` with one NOTE preloaded |
| `subwoofer.test.toml` | the blocks: tilt the channel, spin both wheels, feed the NOTE, expect it in `speaker_blue` |

Open the test with `scrimmage edit examples/blocks/speaker/subwoofer.test.toml`, or run it:

```bash
scrimmage test run examples/blocks/speaker/subwoofer.test.toml
```

## Where the shot comes from: the aim table

`scrimmage launcher aim` makes a table of tilts and wheel speeds for the SPEAKER from this robot:

```bash
scrimmage launcher aim examples/blocks/speaker/note-shooter-standin.toml --goal speaker_blue --distances 1.17:2.57:0.2
```

```
distance  wheel_rps  hood_deg  flight_s  apex  status
1.1700  19.8747  60.0000  0.3545  2.1609  ok
1.3700  21.2350  56.0000  0.3481  2.1785  ok
1.5700  23.0659  52.0000  0.3361  2.2119  ok
1.7700  25.6885  48.0000  0.3166  2.2763  ok
1.9700  26.1037  46.0000  0.3392  2.2113  ok
2.1700  26.9391  44.0000  0.3551  2.1751  ok
2.3700  28.1158  42.0000  0.3656  2.1551  ok
2.5700          unreachable
```

`hood_deg` is the channel's tilt. The distance is from the robot's center to the SPEAKER opening's center; against
the SUBWOOFER it is 1.17 m. It takes about a minute (21 tilts). The numbers are UNCALIBRATED: the NOTE's stiffness,
the tread's friction and the NOTE's drag coefficient are estimates, lift isn't modeled, and the pinch model's exit
speed isn't yet checked against the bench
([docs/launcher.md](../../../docs/launcher.md#a-face-pinch-shooter-for-a-flat-piece)).

Every row peaks at least 0.10 m above the opening's center (2.05 m up), so the NOTE is still rising as it goes
through. Each row was flown again in the world, with the robot at its distance and the wheels held at the row's speed
and 2% either side of it: all 21 shots scored. Without that margin, the table took the lowest shot that just reaches
the opening, which crosses it flat at the top of its arc. Those rows caught the opening's front lip
(`speaker_blue.rim_1`) or its back edge, and only 9 of their 24 shots scored. `tests/test_range_2024.py` flies a
4-row table's rows again on every run.

## Why the example doesn't use the table's first row

The table's first row, 60° at 19.87 rps, is the lowest arc that scores from the SUBWOOFER (with the 0.10 m margin).
The lowest arc sits at one edge of what scores. At 2.8 V (about 20 rps) a 60° channel scores, but only from 56° to
61°: 1° more tilt and the NOTE's top edge catches the front lip under the hood. A real robot's tilt is rarely
better than a degree.

So the example takes another shot `launcher aim` finds from the same place: 58° at about 21.9 rps, a higher arc
(it peaks 0.27 m above the opening's center, against the row's 0.11 m). On this robot 3.05 V holds the wheels at
about 21.7 rps. It scores from 53° to 60° (5° to spare below, 2° above), and from 2.7 V to past 4.4 V. Above 3.8 V
the NOTE touches the hood on its way in and still drops through. The table keeps the lowest arc because that keeps
its rows smooth from distance to distance. Take a higher arc when you want room, and fly it in the world to check.

## The blocks

`subwoofer.test.toml`:

1. Move `shooter.tilt` to 28°. The tilt joint reads 0 at its low stop, 30°, so this is a 58° channel.
2. Spin `shooter.spin0` (the bottom wheel) at 3.05 V.
3. Spin `shooter.spin1` (the top wheel) at 3.05 V. On this robot 3.05 V holds the wheels at about 21.7 rps.
4. Wait 1 s for the wheels to get up to speed.
5. Spin the loader at 8 V. It pushes the NOTE into the wheels.
6. Wait 1.5 s.
7. Expect 1 or more scored in `speaker_blue`.

## What it does

```
finished
  passed: 1 or more pieces are scored in speaker_blue at 2.9 s (block 7)
  scored 1 in speaker_blue
  pieces:
    #  kind  from     fired s  exit m/s  outcome  where
    0  note  preload  1.84     6.86      scored   speaker_blue at t=2.14
```

The NOTE leaves at 6.9 m/s and is in the SPEAKER 0.3 s later without touching the rim or the hood. These numbers
are estimates from the simulation, not from a real robot.

## How much room there is

**The wheel speed.** Open the spin blocks' menu and choose **Try values…** for the volts (both wheels together). At
58°, 2.7 V scores and 2.6 V falls short: the NOTE catches the opening's back edge (`speaker_blue.rim_3`). Up to
3.8 V it goes in clean; from 4.0 V to at least 4.4 V it touches the hood and still drops in.

**The tilt.** Try values for the move block. At 3.05 V, 23° to 30° on the joint scores (53° to 60° channels). At 22°
the NOTE hits the wall under the opening, and at 31° its top edge catches the front lip (`speaker_blue.rim_1`).

**The table's row instead.** Move to 30° and spin at 2.8 V: it scores from 2.6 V to 3.4 V, but only from 26° to 31°
on the joint.

## The AMP

The range has the AMPS too (`amp_blue`, `amp_red`), and a start `blue_amp`, 1 m back from the blue AMP and facing it.
To try it, change the robot's start to `blue_amp` (in the Robot tab, or `start = "blue_amp"` in
`note-shooter-standin.toml`), then in the test move `shooter.tilt` to 0° (a 30° channel), spin both wheels at 1.8 V
(about 13.3 rps, the AMP table's 1 m row) and expect it in `amp_blue`. It scores from 1.4 V to 2.8 V
(`tests/test_range_2024.py` runs it). The AMP isn't a second test file here because a test can't move the robot's
start, and driving from the SUBWOOFER to the AMP with drive and turn blocks ended up to 0.36 m off the AMP's center:
the NOTE has only 0.13 m to spare each side.

## What isn't modeled

- The NOTE is rigid: a real one is foam and squashes in the pinch and against the rim.
- The NOTE has drag in the air but no lift.
- The pinch model's exit speed isn't validated against the bench yet.
- A NOTE counts when it goes through the opening; what it does inside the SPEAKER isn't modeled.
