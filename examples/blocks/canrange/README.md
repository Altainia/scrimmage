# An intake sensor on a CTRE CANrange

The same intake and the same test as `../beam`, but the sensor in the bin is a distance sensor that robot code reads
as a **CTRE CANrange** on CAN id 2, not as a beam break on a DIO. It's the worked example for
[CAN distance sensors](../../../docs/sensors.md#can-distance-sensors).

## The files

| File | What it is |
|---|---|
| `parts/intake/part.toml` | the intake, the same as `../beam`'s |
| `canrange-standin.toml` | the stand-in box robot with the intake, and a `[[sensor]]`: the distance sensor `bin` with `ctre = "CANrange:2"` |
| `stop.test.toml` | the blocks, the same as `../beam`'s: roll at the ball, wait until `bin` is on, stop everything |

The sensor, from `canrange-standin.toml`:

```toml
[[sensor]]
name = "bin"
kind = "distance"
body = "intake.hopper"
at = [0.12, 0.17, 0.08]
toward = [0, -1, 0]
max_range = 0.40
threshold = 0.25
ctre = "CANrange:2"
```

It looks across the bin from its right-hand wall. A ball in the bin is closer than the 34 cm between the walls, so the
sensor is on (closer than `threshold`); with nothing in the bin it reads `max_range`.

Run the test with no robot code:

```bash
scrimmage test run examples/blocks/canrange/stop.test.toml
```

It finishes with the ball in the hopper and `bin` on, as `../beam`'s does.

## In robot code

A CANrange is a Phoenix 6 device, so a robot that makes one runs in real time. The robot makes the device with the
sensor's id, and reads it as usual:

```java
CANrange bin = new CANrange(2);
boolean ballIn = bin.getIsDetected().getValue();      // from the CANrange's own configured threshold
double meters = bin.getDistance().getValueAsDouble(); // whole millimeters, as Phoenix keeps it
```

Set the CANrange's proximity threshold in its configuration (`ProximityParams.ProximityThreshold`); scrimmage's
`threshold` is only for the Test tab and a `dio`.
