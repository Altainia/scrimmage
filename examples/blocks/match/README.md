# A whole match: shoot, leave, score points

This example plays a full FRC 2024 CRESCENDO match in the Test tab, with the same robot as the
[speaker example](../speaker/README.md): a box robot with a pinch shooter and a loader. The field is the built-in
`2024` range, which now knows the game: 15 s of auto, a 3 s pause, then 135 s of teleop, and what a NOTE is worth.

The question the test answers: does this auto score at least 7 points?

## What the blocks do

`auto.test.toml` has `match = true` in its `[test]` table. That turns on match mode: the run plays the whole match,
auto first, and keeps the points.

1. Tilt the shooter, spin both wheels, and feed the NOTE in. This is the speaker example's shot. A NOTE in the SPEAKER
   is worth 5 points in auto.
2. Stop the motors and drive back 1.2 m, out of the blue starting zone. A robot that starts in the zone and is out of
   it when auto ends earns LEAVE, 2 points.
3. Wait until the match is in teleop, which is when auto is over and LEAVE is judged.
4. Expect at least 7 points in auto.

## Run it

In the Test tab, open `examples/blocks/match/auto.test.toml` and press Run. The run takes the whole match, 153 s of
game time, so it takes about 40 s on a typical computer. The report shows the points: 7 in auto (5 for the NOTE in
`speaker_blue`, 2 for leaving the zone), and the phases on the chart. From a terminal:

```bash
scrimmage test run examples/blocks/match/auto.test.toml
```

## Try changing it

- Take out the drive block. The test fails: with no LEAVE the auto scores 5, not 7.
- Change the 7 to 8. It fails too, since this auto has no way to score 8.

## What isn't modeled

The points here are the SPEAKER, the AMP, LEAVE and the ONSTAGE climb. These aren't modeled: AMPLIFICATION,
Coopertition, MELODY, ENSEMBLE, the HARMONY, SPOTLIT and TRAP points, and fouls. LEAVE is judged for the blue
alliance, since the range's starts are blue.
