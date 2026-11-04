# Pushing a partner robot, tested with blocks

The setup: the stand-in robot and a second robot in front of it, a partner with no robot code that just sits there.
Partners get in the way in a match (a robot that died on the field, say), and sometimes you need to push one.

The question the test answers: driving into the partner at 6 V for 2 s, how far does the robot push it? And how much
does it matter if the partner's drive is in brake mode or coasting?

## The files

| File | What it is |
|---|---|
| `partner-standin.toml` | the stand-in box robot, on the plain field |
| `push.test.toml` | the blocks, with a braked partner |
| `coast.test.toml` | the same blocks, with a coasting partner |

The partner is set up in the test, not in the robot: in Setup, **Partner robot**, which the file keeps as

```toml
[setup]
partner = { ahead_in = 50.0, brake = true }
```

Its center is 50 in ahead of the robot's center, facing the same way. Each robot's bumpers stick out 17 in from its
center, so there is a 16 in gap before they meet. The partner is a 60 kg kitbot-style robot (an estimate of a full
robot with bumpers and battery). `brake = true` means its drive is in brake mode: its motors resist being turned, but
they don't lock the wheels. `brake = false` means coasting: its wheels turn freely.

Open the test with `scrimmage edit examples/blocks/partner/push.test.toml`, or run it:

```bash
scrimmage test run examples/blocks/partner/push.test.toml
```

## The blocks

1. Drive forward for 2 s at 6 V, into the partner.
2. Expect the partner moved at least 60 in.

"Moved" is how far the partner's center has gone across the floor from where it started.

## What it does

```
finished
  passed: the partner moved at least 60 in at 2.3 s (block 2)
```

The robot speeds up over the 16 in gap and hits the partner's bumper at about 0.7 s. Then it pushes it the rest of
the 2 s, at about 1 m/s. By the end the partner has moved 63 in. The check asks for 60, a little less, so a small
change doesn't fail the test.

A coasting partner (`coast.test.toml`) moves 112 in in the same 2 s, about 1.8 times as far: the robot pushes it at
about 2 m/s. Brake mode doesn't stop a partner from being pushed, but it slows the push down a lot. The coasting
partner also keeps rolling after the robot stops; the braked one stops within about 8 in.

These numbers are estimates from the simulation, not from a real robot. The brake is an estimate too: the partner's
drive has two CIM motors a side through 10.71 to 1, and brake mode resists turning the way those motors would.

## Change it and run it again

**The voltage.** Open the drive block's menu and choose **Try values…** for its volts. At 8 V the braked partner
moves 86 in; at 4 V only 40 in, and the check fails.

**Where the partner is.** In Setup, change the partner's distance ahead or its heading (`heading_deg`, turned from
the robot's heading). Turned 90°, the robot pushes it sideways, so its wheels have to slide across the carpet: it
moves only about 3 in and the check fails, braked or coasting. A partner is much harder to push from the side.
