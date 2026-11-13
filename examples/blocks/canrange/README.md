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

It looks across the bin from one side wall to the other, 34 cm. A ball in the bin reads closer than 25 cm (`threshold`:
the sensor is on). An empty bin reads 40 cm (`max_range`: it sees no piece).

Run the test with no robot code:

```bash
scrimmage test run examples/blocks/canrange/stop.test.toml
```

It finishes with the ball in the hopper and `bin` on, as `../beam`'s does.

## In robot code

A CANrange is a Phoenix 6 device, so a robot that makes one runs in real time. The robot makes the device with the
sensor's id, sets its proximity threshold to the config's `threshold` (25 cm), and reads it as usual:

```java
CANrange bin = new CANrange(2);
CANrangeConfiguration cfg = new CANrangeConfiguration();
cfg.ProximityParams.ProximityThreshold = 0.25;        // meters: the config's threshold
bin.getConfigurator().apply(cfg);

boolean ballIn = bin.getIsDetected().getValue();      // closer than the CANrange's threshold
double meters = bin.getDistance().getValueAsDouble(); // whole millimeters, as Phoenix keeps it
```

Set the threshold in robot code: Phoenix's default is 40 cm, and this sensor's `max_range` is 40 cm, so without it an
empty bin reads detected. scrimmage's `threshold` is only for the Test tab and a `dio`; keep the two the same.
