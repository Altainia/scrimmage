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
"spin intake.roller at 8 V" down to "expect a ball in intake.hopper". On the right is **Run**.

## 2. Run it

Press the blue **Run**. In a few seconds the right side says what happened in one sentence, here
"Every block played.", then the check: "passed: a ball reaches intake.hopper at 3.3 s (block 5)". Below it are a replay and a chart.

Press **Play** under the picture to watch the robot drive at the ball and the roller pull it into the bin.

## 3. Change a number

Click the number in the **spin** block (8 V) and type 2, then press **Run** again. The roller is too slow to grab the
ball, so the robot just pushes it ahead of it, and the sentence says the wait timed out. Type 8 again to get it back.

To try many values at once, right-click the **spin** block, choose **Try values…**, type From 0, To 12 and Steps 7 (Steps starts at 5),
and press **Try them**. You get one row per voltage and can click any row to replay it.

## 4. Test your own part

Open the Part tab on your CAD ([Bring your CAD in](bring-your-cad-in.md)). When the bottom bar says **Parts up to
date**, press **Test it**. Scrimmage puts your part on a stand-in robot (the kitbot's frame and drive), gives its motors
free IDs, and opens the Test tab with a test to start from. Change the blocks and press **Run**.

You can also start from the start page (`scrimmage edit` with no file) with **Test a mechanism**.

## What next

- Every block, check and setting is in [The Test tab](../test-tab.md).
- The climber example, `examples/blocks/ladder_climber`, is honest about where it stops: it reaches the first rung and
  tips up on its back bumper instead of rising.
- To run a test with no window, for example from a script: `scrimmage test run examples/blocks/intake/intake.test.toml`.
  It exits 0 when every check passed.
