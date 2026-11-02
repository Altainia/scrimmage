# A train-wheel ladder climber, tested with blocks

The mechanism: one motor turns a crank, a disc on each side of the robot on one shaft. Each disc has a pin, and the
two pins are half a turn apart. A rod hangs from each pin down to an arm that slides up and down, so as the crank
turns, one arm rises while the other comes down, like the rods on a steam train's wheels. On top of each arm a
bracket reaches forward to a hook: a finger that goes over a rung, a lip at its tip, and a back plate. The hook turns
on a spring hinge, so when it rises into a rung from below it folds down out of the way, then springs back above the
rung and catches it on the way down.

The question the test answers: does that motion climb the ladder rung after rung, and how far does the robot tilt?

## The files

| File | What it is |
|---|---|
| `climber.step` | the CAD (a copy of `tests/data/cad/crank_climber.step`, made by `tests/make_cad_fixtures.py`) |
| `parts/climber.import.toml` | the Part tab's choices: bodies, materials, joints, the two loop pins, the hook springs, the motor |
| `parts/climber/` | the part the Part tab built from it |
| `climber-standin.toml` | the stand-in box robot with the climber where the CAD has it, in front of the `ladder` range, as the Part tab's **Test it** makes it (it puts the crank motor on `PWM:2`, the first PWM channel the drive leaves free) |
| `climb.test.toml` | the blocks |
| `make_climber.py` | makes the three above from `climber.step` with the same edits a student makes in the Part tab |

Open the test with `scrimmage edit examples/blocks/ladder_climber/climb.test.toml`, or run it:

```bash
scrimmage test run examples/blocks/ladder_climber/climb.test.toml
```

## The blocks

1. Expect the robot to tilt less than 30° the whole time (first, so it watches the whole run).
2. Repeat until a hook touches rung1: drive forward for 0.1 s at 3 V. The back plate of the high hook meets rung1 with
   its finger above the rung.
3. Repeat until the robot hangs from rung4: spin the crank at 6 V, wait until a hook touches the next bar, wait 0.3 s.

## What it does

`reached rung1, then waiting for a hook to touch rung2 timed out at 12.1 s (block 2 inside block 3)`.

The robot drives up to the ladder, the high hook comes down onto rung1 and lifts the front of the robot, and the low
hook folds past rung1 on its way up. The robot hangs from rung1 (the hooks carry more than a quarter of its weight),
but it tips up on its back bumper instead of rising. At the top of the stroke the robot is tilted 17° (20° at most
in the run, so the tilt check passes), and the rising hook's pin is 0.81 m up and 0.15 m back from the ladder, while
rung2's top is 0.91 m up. It never reaches rung2, so it comes back down onto rung1, and the crank goes on turning
with the robot rocking on rung1 until the wait gives up.

**Why (physics, not a bug):** the hooks are at the front edge of the robot and its weight is in the middle. A hook
pulling up on the front can't lift the back off the carpet until the robot has tilted far enough that its center of
mass is right under the hook: about 45° for this robot, an estimate from where the hooks sit (about 0.5 m ahead of
the center of mass and 0.45 m above it), not a measurement. Until then the robot pivots on its back bumper, so lifting
the front 0.35 m (one stroke) mostly tilts it. Tilting it back swings everything above the bumper away from the
ladder, the higher hook most of all, so the next rung is out of reach. The crank is strong enough (the motor isn't
stalled), and the hooks fold and catch as designed.

## What was tuned (two rounds, the limit the spec sets)

1. **Gear ratio** 100 to 200. At 100 (a free crank turns 0.47 rev/s at 6 V, the plan's "about 0.5 rev/s") the NEO
   stalled at 110° of the crank with the front of the robot lifted only a few centimeters: lifting about 60 kg on a
   0.2 m crank needs about 120 N·m, a NEO's stall torque at 6 V times 100. At 200 the free crank turns 0.24 rev/s
   and doesn't stall.
2. **Arm angle and mount position**: the arms leaned 20° forward (so they would stand straight up once the robot
   tilts back), the crank moved 60 mm forward and 120 mm up, and the hook's back plate made longer. That was worse:
   a leaning arm carries its hook forward as it rises, so the low hook met rung1 with its hinge instead of its finger
   and jammed, and the crank stalled at 134°. The example ships with straight-up arms (round 1). `CRANK_LEAN` in
   `tests/make_cad_fixtures.py` sets the lean, to try it again.

Not tried, and the next things to try: a climber that holds the robot nearer its middle (hooks reaching over the
robot's top from a mount near its center), or a robot whose weight sits further forward (the stand-in's
`com_offset`), so that the back leaves the carpet before the robot tilts far. A quick try with
`com_offset = [0.3, 0, 0]` still tilted the robot 27° on its back bumper.

A side note from the simulation: the hook's stop (its hinge's range) is soft under a robot's weight, so a loaded hook
turns 40° to 60° past it until its back plate rests on the rung, and the replay shows the hooks tipped up while they
carry the robot. A real hook would sit on its stop. How much this changes the climb hasn't been tested: the Part tab
can't stiffen a hinge's stop yet (the part format's `armature` and `limit_solref` aren't among its joint settings).
