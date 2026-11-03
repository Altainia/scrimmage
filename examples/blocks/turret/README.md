# A turret shooter, tested with blocks

The mechanism: a launcher on a turret, on top of the stand-in robot. The turret turns the launcher on its own, so the
robot can point any way and still shoot at the hub.

The question the test answers: the robot shoots, turns on the spot, and shoots again. Does the turret keep the
launcher on the hub the whole time, and do the shots from both sides of the turn score?

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
17. Wait 2 s.
18. Expect at least 6 balls scored in `hub_blue`.

## What it does

```
finished
  passed: the turret is aimed at hub_blue within 5° from 1.4 s on (block 5)
  passed: 3 or more balls are scored in hub_blue at 3.8 s (block 12)
  passed: 6 or more balls are scored in hub_blue at 6.6 s (block 18)
  scored 8 in hub_blue
```

The stages stop just before the first balls reach the launcher, but the 4 balls already most of the way through
still go out: they leave at 1.8 s, in pairs (the launcher has two lanes), and drop into the hub at about 3.3 s. The
other 4 wait. Then the robot turns. It coasts on past 90° and ends up turned about 120°, and the turret
turns about 120° the other way to stay on the hub. The aim is off by at most 3.4° while the robot is turning. The
last 4 balls leave at about 4.7 s and are in by 6.3 s.

That's 4 before the turn and 4 after: 8 of 8. The checks ask for 3 and 6, a little less than that, so a small change
somewhere doesn't fail the test. These numbers are estimates from the simulation, not from a real robot.

## Change it and run it again

**The hood and the wheels.** We tried the hood at 30°, 35° and 40° (the hood joint's angles, which send the ball out at
70°, 75° and 80° above level), with the wheels at 9, 10, 11 and 12 V. So close to the hub, only the steepest shot
scores: at 40° all 8 go in at every voltage we tried, at 35° 2 at most, and at 30° none. Start the robot farther back
(`start_pose` in `turret-standin.toml`) and a lower hood starts to work.

**Without the turret.** Delete the aim block (and the two aimed blocks). The turret stays at 0, so after the turn the
launcher points about 120° away from the hub, and the last 4 shots miss.

**The turn.** Change the turn block's degrees, or its volts. A slower turn (2 V) coasts less: the robot ends up turned
about 100° instead of 120°.
