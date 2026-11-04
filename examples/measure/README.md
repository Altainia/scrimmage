# Measurement examples

Worked examples of [docs/measuring.md](../../docs/measuring.md): one file for each kind of measurement, and two
launcher setups that use them. The trials are made up, but each is what a student with a kitchen scale, a bathroom
scale, a luggage scale and a phone could write down. Run each from the `scrimmage` folder.

| file | kind | tests | what it shows |
|---|---|---|---|
| `fuel.measure.toml` | ball | size, squeeze, drop | a 2026 FUEL ball: good enough to use, with one drop trial flagged |
| `tread-on-fuel.measure.toml` | friction | sled | 40A wheel tread on FUEL: good enough to use |
| `hooded-shots.measure.toml` | launcher | shot | shots from `examples/launcher/hooded.toml`, which fit the Power Cell's stiffness: a good fit |
| `intake-deploy.measure.toml` | cylinder | stroke | an intake's deploy cylinder, filmed: a good fit of its valve |
| `hooded-measured.toml` | a launcher setup | | `hooded.toml` with the stiffness the shots fit: "Measured:" |
| `fuel-shooter.toml` | a launcher setup | | a FUEL launcher with the ball and the tread measured: "Calibrated from measurements" |

## The FUEL ball

```bash
scrimmage measure examples/measure/fuel.measure.toml
```

All three tests are good enough to use, and the file exits 0:

- **Size:** mass 215.6 g and diameter 150.2 mm (5.91 in), spread 1% each over 5 trials.
- **Squeeze:** `stiffness_n` 123.1 N, spread 7% over 5 trials. That is well above the preset's 80 N estimate, and
  near the 130 N docs/launcher.md works out from the FUEL whitepaper's chart.
- **Drop:** `restitution` 0.595, spread 10% over 6 trials, with "warning: trial 6 looks off: check it". Trial 6
  bounced to 5.5 in where the others reached 6.25 to 6.75 in. Its distance from the average (0.042) is more than
  twice the trials' average distance (0.016), so it is flagged. It is not dropped: the spread is still under 15%, so
  the average is good enough to use. Watching that trial's video again (did the ball brush the wall, or land on a
  crack?) and dropping it once more is the right next step.

## The tread's friction

```bash
scrimmage measure examples/measure/tread-on-fuel.measure.toml
```

A sled with 40A tread on its bottom, weighed on a kitchen scale (`sled_g = 2270`), pulled across FUEL foam with a
luggage scale in lb. The units differ, and Scrimmage converts both: friction 0.899, spread 9% over 5 trials.

## The launcher's shots

```bash
scrimmage measure examples/measure/hooded-shots.measure.toml
```

Six shots from the hooded Power Cell launcher, three near 70 rps and three near 80 rps, filmed at 240 fps with marks
24 in apart (each ball crosses in 24 or 25 frames). No ball file is named, so the shots fit the ball's stiffness,
with the wheel friction at the setup's 0.95:

```
  fit: stiffness 91 N, anywhere from 81 to 103 N fits your shots
  the model misses your shots by 1.9% (root mean square)
  warning: Measure the ball's squeeze (the squeeze test) to fit wheel friction too.
  good enough to use
```

It is a good fit: it misses the shots by under 5%, 91 N is far from the edges of what it tries (15 to 240 N), and its
band is within 15% of 91 N.

**Why these shots fit well.** Both wheel speeds are above this launcher's knee, where the wheel slips on the ball and
the ball's grip (its stiffness) sets the exit speed: that is why the model gives the same speed at 70 and 80 rps.
Shots below the knee (40 and 50 rps here) hardly depend on the stiffness, and the fit says "shoot at a higher wheel
speed" ([docs/measuring.md](../../docs/measuring.md#how-the-launcher-fit-works) shows that output). And this hood
launches at 50 degrees, so the ball crosses the marks at under 6 m/s: 24 in at 240 fps gives 25 frames. A faster,
flatter shot (9 m/s across) needs the marks about 30 in apart at 240 fps, or a 960 fps camera.

**Only the stiffness.** The fit can't give the wheel's friction too: the shots can't tell a stiffer ball from a
grippier wheel. To fit the friction instead, squeeze-test the Power Cell (a ball file with `base =
"2020-power-cell"`) and name it in `[shot_setup] ball`.

## The cylinder's strokes

```bash
scrimmage measure examples/measure/intake-deploy.measure.toml
```

The stock-size cylinder (1-1/16 in bore, 6 in stroke) pushing a 2 lb intake out level, filmed at 240 fps and
counted from the Pneumatic Hub channel's light to the rod at full stroke: 59 to 62 frames, an average of 0.252 s.

```
  fit: valve conductance 0.38, anywhere from 0.37 to 0.39 fits your strokes (dm^3/(s bar))
  the model's stroke takes 0.252 s, off by +0.0%
  good enough to use
```

The valve (with its tubing) lets through about half the air the 0.8 estimate does. A stock cylinder in a robot config
uses it with `measured = "intake-deploy.measure.toml"`, relative to the config.

## Using them in a launcher setup

```bash
scrimmage launcher run examples/measure/hooded-measured.toml
scrimmage launcher run examples/measure/fuel-shooter.toml
```

`hooded-measured.toml` is `hooded.toml` with `measured = "hooded-shots.measure.toml"` in its `[ball]`. Its report's
first line names what is measured and what is still an estimate:

```
UNCALIBRATED: still estimates: ball bounce, wheel friction. Measured: ball stiffness (hooded-shots.measure.toml, J. Smith, 2026-11-12, 6 trials).
```

With the 91 N ball in place of the 60 N estimate, the burst's exit speed rises from 7.4 to 9.0 m/s, and one of the
five balls scores where none did.

`fuel-shooter.toml` is a FUEL launcher whose `[ball]` names `fuel.measure.toml` and whose `[wheels]` names
`tread-on-fuel.measure.toml`. Every input that matters is measured and good, so it reads:

```
Calibrated from measurements: ball stiffness (fuel.measure.toml, J. Smith, 2026-11-10, 5 trials); ball bounce (fuel.measure.toml, J. Smith, 2026-11-10, 6 trials); wheel friction (tread-on-fuel.measure.toml, J. Smith, 2026-11-11, 5 trials).
```

The stiffness and the bounce are listed apart because they come from different numbers of trials (5 squeezes, 6
drops). Each run writes its results to a `-results` folder beside the setup (`--out` picks another).

`tests/test_measure_examples.py` checks what this page says.
