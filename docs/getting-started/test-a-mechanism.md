# Test a mechanism with blocks

For students and mentors who want to try a mechanism on a robot: does the roller pick the ball up, does the climber
reach the next bar? You snap blocks together, press **Run**, and watch a replay. No robot code and no programming.

[← All guides](README.md) · Needs: [the install](install.md), the first line of step 3 (`pip install -e .`)

## 1. Open an example

In the `scrimmage` folder, with the venv active:

```bash
scrimmage edit examples/blocks/intake/intake.test.toml
```

A browser tab opens on the **Test** tab. On the left are the blocks. In the middle is the test: five blocks, from
"spin intake.roller at 6 V" down to "expect a ball in intake.hopper". On the right is **Run**.

## 2. Run it

Press the blue **Run**. In a few seconds the right side says what happened in one sentence, here
"Every block played.", then the check: "passed: a ball reaches intake.hopper at 3.3 s (block 5)". Below it are a replay and a chart.

Press **Play** under the picture to watch the robot drive at the ball and the roller pull it into the bin.

## 3. Change a number

Click the number in the **spin** block (6 V) and type 2, then press **Run** again. The roller is too slow to grab the
ball, so the robot just pushes it ahead of it, and the sentence says the wait timed out. Type 6 again to get it back.

To try many values at once, right-click the **spin** block, choose **Try values…**, type From 0, To 12 and Steps 7 (Steps starts at 5),
and press **Try them**. You get one row per voltage and can click any row to replay it.

## 4. Test your own part

Open the Part tab on your CAD ([Bring your CAD in](bring-your-cad-in.md)). When the bottom bar says **Parts up to
date**, press **Test it**. Scrimmage puts your part on a stand-in robot (the kitbot's frame and drive), gives its motors
free IDs, and opens the Test tab with a test to start from. Change the blocks and press **Run**.

You can also start from the start page (`scrimmage edit` with no file) with **Test a mechanism**.

## 5. Feed it from a human player station

Many games have a human player who feeds pieces to the robot through a chute. To see whether a hopper catches them:

```bash
scrimmage edit examples/blocks/station/feed.test.toml
```

Open **Setup**. The **Field** is the human player station, and **Where a piece should end up** has the bin ticked: that
is where a ball counts as "in". A **Piece** picker lets you try a different ball, or a piece that isn't a ball. The test has three blocks: start feeding from the
station, wait until 5 balls are in the hopper, stop feeding, and a check that at least 80% of the balls fed made it in.
Press **Run**. The sentence on the right says how many: "5 of 5 made it in (100%)". A ball that lodged inside the robot
but outside the hopper is counted as "stuck", and one that bounced away is "missed".

The station isn't exactly the same every time, like a person. Press **New shuffle** in Setup and **Run** again: it feeds
a slightly different set of balls, and the count may change. A result that holds over several shuffles is one you can
trust. On your own part, **Test it** picks this field when your part has a bin or an intake body, and **Add the station
blocks** in Setup gives you the same three blocks.

## 6. Test a grabber on a coral

A grabber has a harder question than a bin: not whether it catches a piece, but whether it picks it up however the
piece lies, holds it while the arm swings, and puts it where it belongs. The coral in the 2025 game is a PVC pipe, and
it goes on a branch you slide it onto. Scrimmage has a field for that, `reef`: one pipe of the reef with three branches
(L2, L3 and L4), a coral on the carpet and a coral station.

```bash
scrimmage edit examples/blocks/reef/place.test.toml
```

Press **Run**. The robot is a box with a grabber on its front: an arm and two jaws. The blocks close the jaws on the
coral, lift it, wait until the coral is **held** by the grabber, and check it is held **the whole time** while the arm
swings it over the top and the robot drives onto the L2 branch. At the end the jaws open and the checks are: the coral
is **seated** at least 3 in on `branch_l2`, and **rests on** it. The right side says the coral was held until it was
placed on the branch, and how far onto the rod it sits. If it had slipped, the sentence would say when and what was
moving, and the replay would mark the moment.

On **your own** grabber part, press **Test it** in the Part tab. A part with a claw, grabber, gripper or jaw body is put
on this field, and its test starts with the grab and the held check, ready to run: it closes each motor that closes
the jaws (a joint named jaw, claw, grip or finger) at 4 V. If the jaws close the wrong way, flip
the sign of the volts.

**Try grip tape.** Grip is how well the pads hold, and the squeeze is how hard they push. In Setup, the **Grip** group
lists the pads. Pick a material for a pad (the friction numbers are estimates, labeled UNCALIBRATED), or press **Try
materials…** to run the test once per material. With a firm squeeze every material places the coral, so the surprise
is in a lighter squeeze: grip tape holds where HDPE lets the coral slip. Measure your own pad with a sled pull before you
trust a number ([how](../test-tab.md#grip-material)).

**How the coral lies.** A coral on the floor can be lying in line with the robot, across it, at any angle, or standing on
end. Open `examples/blocks/reef/spread.test.toml` and press **Run the spread** in Setup: it tries six angles, and the
sentence says which were picked up and which missed ("missed lying at 20°, lying at 45° and lying across"). Here the
jaws, open, are only 17.4 cm apart, so a coral turned 20° or more is wider than the gap. That is a fact about the jaws:
open them wider or make the pads shorter and the spread changes.

## What next

- Every block, check and setting is in [The Test tab](../test-tab.md).
- The station example, `examples/blocks/station`, shows how a design is compared over many shuffles.
- The reef example, `examples/blocks/reef`, has the whole grabber story, with its numbers.
- The climber example, `examples/blocks/ladder_climber`, is honest about where it stops: it reaches the first rung and
  tips up on its back bumper instead of rising.
- To run a test with no window, for example from a script: `scrimmage test run examples/blocks/intake/intake.test.toml`.
  It exits 0 when every check passed.
