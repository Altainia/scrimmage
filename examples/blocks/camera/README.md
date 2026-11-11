# Can the camera see the SPEAKER's tags?

A camera helps a robot line up on a goal. The SPEAKER has two AprilTags, 7 and 8 on the blue side. Can a camera on
our shooter robot see them from the SUBWOOFER, where we shoot?

The robot is the one from [the speaker example](../speaker/README.md), with one camera added. The field is the built-in
`2024` field, which has WPILib's 16 AprilTags.

## The files

| File | What it is |
|---|---|
| `note-shooter-standin.toml` | the speaker example's robot, plus a `[[sensor]]` camera named `front_cam` |
| `sees-speaker-tags.test.toml` | the blocks: wait half a second, then expect the camera sees tag 7 and tag 8 |

Run it:

```bash
scrimmage test run examples/blocks/camera/sees-speaker-tags.test.toml
```

Or open it with `scrimmage edit examples/blocks/camera/sees-speaker-tags.test.toml`.

## Why the camera is mounted high

The camera is 0.90 m up on the chassis, pitched up about 10 degrees.

That is high on purpose. The SUBWOOFER is 37 inches (0.94 m) tall, and the robot starts about an inch from it. A camera
mounted low looks straight at the SUBWOOFER's face. The SUBWOOFER is in the way, so the camera can't see the tags
behind it. A camera sees a tag only if nothing solid is between them.

We tried different heights, measured from the chassis. At 0.80 m and below, the camera sees neither tag. At 0.85 m and
above, it sees both. We used 0.90 m to leave some room.

The pitch tilts the view up toward the tags. They are 1.45 m above the carpet, higher than the camera.

## Change it and see

Lower the camera. Change `at = [0.25, 0.0, 0.90]` to `at = [0.25, 0.0, 0.25]` in the robot file and run the test
again. It fails: "front_cam sees tag 7". `tests/test_range_2024.py` checks this on every run.

Other things to try:

- Raise it to 1.1 m. It still sees both.
- Narrow the view with `hfov_deg = 40`. Tag 8 sits to one side of the SPEAKER, so it drops out and only tag 7 is seen.
- Start from `blue_amp` (set `start = "blue_amp"` in the robot file's `[range]`) and expect any tag. The robot faces
  the AMP there, and the camera sees the AMP's tag 6. From `blue_amp_side` it sees none: the robot faces the
  SPEAKER wall, but tags 7 and 8 are more than 35 degrees off to the side, outside the camera's 70 degree view.

The camera's keys are in [docs/sensors.md](../../../docs/sensors.md#a-camera).

## What isn't modeled

- The camera has no image. It answers yes or no, by geometry: is the tag in view, close enough and facing the
  camera, with nothing in the way?
- There is no blur, bad light, noise or pose error. A real camera sees fewer tags than this one.
- The camera's field of view and range are estimates for one common camera. Set them from your own camera.
