# A turret shooter, tested with blocks

The mechanism: a launcher on a turret, on top of the stand-in robot. The turret turns the launcher on its own, so the
robot can point any way and still shoot at the hub.

The question the test answers: the robot shoots standing still, turns on the spot, and then shoots while it drives.
Does the turret keep the launcher on the hub the whole time, and how many of the shots score?

## The files

| File | What it is |
|---|---|
| `turret-standin.toml` | the stand-in box robot with a stock `turret` centered on its top and a stock `launcher` on the turret, two feeder stages (`hopper_floor`, `feeder`) and a `[shooter]` model, on the `2026` range with 8 FUEL preloaded |
| `aim.test.toml` | the blocks |

The launcher, the stages and the shooter model are the ones in `configs/fastmode_range.toml`. The robot starts 1.3 m
in front of `hub_blue`'s center, facing it. The turret is at the middle of the robot, so turning on the spot doesn't
move the launcher closer to the hub or farther away.

Open the test with `scrimmage edit examples/blocks/turret/aim.test.toml`, or run it:

```bash
scrimmage test run examples/blocks/turret/aim.test.toml
```

## The blocks

1. Move the hood to 40°. This is the hood joint's angle, measured from the hood's lowest stop: 40° is its top stop,
   where the ball leaves 80° above level (the steepest shot this hood has).
2. Spin the launcher at 11 V.
3. Aim the turret at `hub_blue`. It keeps aiming until the end, whatever the robot does.
4. Wait 1 s for the wheels to spin up.
5. Expect the turret aimed at `hub_blue` within 5°, throughout the rest of the run.
6. and 7. Spin the hopper floor and the feeder at 8 V.
8. Wait 0.4 s.
9. and 10. Stop the feeder and the hopper floor.
11. Wait 2 s, for the shots to land.
12. Expect at least 3 balls scored in `hub_blue`.
13. Turn left 90°.
14. Wait until the turret is aimed within 2° (give up after 2 s).
15. and 16. Spin the hopper floor and the feeder again.
17. Drive forward 1.5 m at 2 V. The balls go out while the robot drives.
18. Wait 2 s.
19. Expect at least 6 balls scored in `hub_blue`.

## What it does

```
finished
  passed: the turret is aimed at hub_blue within 5° from 1.4 s on (block 5)
  passed: 3 or more balls are scored in hub_blue at 3.8 s (block 12)
  passed: 6 or more balls are scored in hub_blue at 9.0 s (block 19)
  scored 7 in hub_blue
```

The stages stop just before the first balls reach the launcher, but the 4 balls already most of the way through
still go out: they leave at 1.8 s, in pairs (the launcher has two lanes), and drop into the hub at about 3.3 s. The
other 4 wait. Then the robot turns. It coasts on past 90° to about 112°, and the turret turns the other way to stay
on the hub. The aim is off by at most 3.4° while the robot is turning.

Then the robot drives. It starts at 4.6 s, and the last 4 balls leave at 4.7 s, while it is still speeding up (0.3 to
0.4 m/s). The turret keeps turning as the robot goes, so the aim is off by at most 1.7° while it drives. Even so, 3 of
the 4 go in (at about 6.2 to 6.7 s) and one misses. Why: the turret points the launcher at the hub, but it doesn't
aim ahead for the robot's motion. A ball shot from a moving robot keeps the robot's speed, so it lands off by about
the robot's speed times the time the ball is in the air: here about 0.4 m/s times 1.5 s, or 0.6 m. Robot code that
shoots on the move aims ahead to make up for it ("leading" the shot); the aim block doesn't.

That's 4 standing and 3 on the move: 7 of 8. The checks ask for 3 and 6, a little less than that, so a small change
somewhere doesn't fail the test. These numbers are estimates from the simulation, not from a real robot.

## Change it and run it again

**The hood and the wheels.** We tried the hood at 30°, 35° and 40° (the hood joint's angles, which send the ball out at
70°, 75° and 80° above level), with the wheels at 9, 10, 11 and 12 V. So close to the hub, only the steepest shot
scores: at 40° the 4 standing shots go in at every voltage we tried, and of the 4 on the move 4 go in at 9 V, 2 at
10 V and 3 at 11 and 12 V. At 35° at most 2 of the standing shots go in, and at 30° none, so the test stops at its
first count. Start the robot farther back (`start_pose` in `turret-standin.toml`) and a lower hood starts to work.

**Drive faster.** Change the drive block's volts to 4. The balls leave with the robot going 0.6 to 0.9 m/s, so they
land about 1 m off, and all 4 miss: the last check fails with 4 scored. Shooting on the move without aiming ahead
only works slowly.

**Without the turret.** Delete the aim block (and the two aimed blocks). The turret stays at 0, so after the turn the
launcher points far away from the hub, and the last 4 shots miss.

**The turn.** Change the turn block's degrees, or its volts. A slower turn (2 V) coasts less: the robot is turned
about 91° instead of 112° when it starts to drive, and 2 of the shots on the move go in.
