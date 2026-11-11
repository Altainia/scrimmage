# Sensors: beam breaks, distance sensors, color sensors and cameras

A robot config's `[[sensor]]` tables put sensors on the robot that see game pieces: a **beam break** that reads broken
while a piece crosses it, a **distance sensor** that reads how far away the first piece (or anything) is, and a **REV
Color Sensor V3** that reads the color of the piece in front of it. Your robot code reads them through the devices
teams already use (`DigitalInput`, `AnalogInput` and REVLib's `ColorSensorV3`), and the Test tab can wait for them,
check them and chart them with no robot code at all.

A **camera** reports which of the field's AprilTags it can see, by geometry, with no image: enough to answer "where
must the camera go to see the tags from where we shoot?". [A camera](#a-camera) says how, and
[Vision in robot code](#vision-in-robot-code) shows how robot code simulates its own vision from scrimmage's true pose.

**The readings are ideal.** There is no noise, no mounting tolerance and no delay beyond one physics step. Sensors see
balls, discs and rigid pieces alike, and never the robot's own parts. The color sensor's numbers are estimates, and
Java's color sensor hasn't been run yet: [Estimated or not yet checked](#estimated-or-not-yet-checked) lists both.

`examples/blocks/beam` is a worked example: an intake that stops when its beam break sees a ball in the bin.

Contents: [Adding a sensor](#adding-a-sensor), [The keys](#the-keys), [What the readings are](#what-the-readings-are),
[A camera](#a-camera), [Piece colors](#piece-colors), [Robot code](#robot-code),
[Vision in robot code](#vision-in-robot-code), [In the Test tab](#in-the-test-tab),
[In scenarios and MCP](#in-scenarios-and-mcp), [Estimated or not yet checked](#estimated-or-not-yet-checked),
[What isn't simulated](#what-isnt-simulated), [Errors](#errors).

## Adding a sensor

Each `[[sensor]]` is mounted on one of the robot's bodies, at points given in that body's frame (meters, or inches
with the `_in` keys). The body is `"<part>.<body>"`, the same names a `[[part]]`'s joints use: `drive.chassis` for the
chassis, `intake.hopper` for the intake example's bin. Naming a body the robot doesn't have is an error that lists the
ones it has.

<!-- complete -->
```toml
[[part]]
name = "drive"
stock = "chassis"

[[sensor]]
name = "bin"                      # what the Test tab and messages call it
kind = "beam"                     # a beam break
body = "drive.chassis"            # the body it is mounted on
from = [0.30, 0.20, 0.10]         # one end of the beam, in the body's frame (m)
to = [0.30, -0.20, 0.10]          # the other end
dio = 4                           # the robot code reads DigitalInput 4

[[sensor]]
name = "range"
kind = "distance"                 # a distance sensor
body = "drive.chassis"
at = [0.33, 0.0, 0.10]            # where it looks from ...
toward = [1, 0, 0]                # ... and which way: straight ahead
max_range = 2.0                   # m
analog = 0                        # the robot code reads AnalogInput 0

[[sensor]]
name = "eye"
kind = "color"                    # a REV Color Sensor V3
body = "drive.chassis"
at = [0.30, 0.0, 0.20]
toward = [0, 0, -1]               # looking down
i2c = "mxp"                       # ColorSensorV3 on the MXP I2C port
```

In a body's frame, x is the body's forward, y its left and z up, as in its part's `part.toml`. For the stock
`chassis`, the origin is on the floor at the robot's center. A stock part's frames are in [parts.md](parts.md#stock-parts).

**The Test tab needs no channel.** A sensor with no `dio`, `analog` or `i2c` still works in the Test tab and the
replay; the channel is only for robot code.

## The keys

Every sensor:

| key | default | meaning |
|---|---|---|
| `name` | required | its name: what the Test tab, the chart (`sensor.<name>`) and messages call it. Letters, digits and `_`, as a part's name; two sensors can't share a name |
| `kind` | required | `"beam"` (a beam break), `"distance"` (a distance sensor) or `"color"` (a REV Color Sensor V3) |
| `body` | required | the robot body it is mounted on, `"<part>.<body>"`. A body the robot doesn't have is an error that lists the robot's bodies |

A beam break (`kind = "beam"`):

| key | default | meaning |
|---|---|---|
| `from` (or `from_in`) | required | one end of the beam: `[x, y, z]` in the body's frame, in meters (or inches) |
| `to` (or `to_in`) | required | the beam's other end. The two ends can't be the same point |
| `dio` | none | a DIO channel: the robot code reads it as a `DigitalInput` |
| `active_low` | `true` | `true`: the input reads `false` while the beam is broken, as IR beam-break receivers do. `false` flips it. It goes with `dio` |

A distance sensor (`kind = "distance"`):

| key | default | meaning |
|---|---|---|
| `at` (or `at_in`) | required | where it looks from: `[x, y, z]` in the body's frame |
| `toward` | required | which way it looks: a direction `[x, y, z]` in the body's frame, any length (not `[0, 0, 0]`) |
| `max_range` (or `max_range_in`) | 2.0 m | how far it sees. With nothing closer, it reads `max_range` |
| `sees` | `"pieces"` | `"pieces"`: game pieces only. `"anything"`: the first thing it hits that isn't the robot (a piece, a wall, the carpet) |
| `analog` | none | an analog input channel: the robot code reads it as an `AnalogInput`, `volts_per_m` volts per meter of distance |
| `volts_per_m` | 0.977 | volts per meter on the `analog` input (an estimate: a MaxBotix MB1013 ultrasonic sensor's 5 V over 5.12 m). The voltage stops at 5 V. It goes with `analog` |
| `dio` | none | a DIO channel that switches at `threshold`, like an IR proximity switch: on while something is closer than `threshold` |
| `active_low` | `true` | `true`: the `dio` reads `false` while it is on. It goes with `dio` |
| `threshold` (or `threshold_in`) | half of `max_range` | the distance (m, or inches) it is on closer than: for its `dio`, and for the Test tab's **is on**. It can't be past `max_range` |

A color sensor (`kind = "color"`):

| key | default | meaning |
|---|---|---|
| `at` (or `at_in`) | required | where it looks from: `[x, y, z]` in the body's frame |
| `toward` | required | which way it looks, as for a distance sensor |
| `max_range` (or `max_range_in`) | 0.10 m | how far it sees. 0.10 m (10 cm) is the most: past that a REV Color Sensor V3 reads nothing ([the readings](#the-color-sensor)) |
| `sees` | `"pieces"` | as for a distance sensor. With `"anything"`, something that isn't a piece gives an IR and a proximity reading but no color |
| `i2c` | none | `"onboard"` or `"mxp"`: the I2C port the robot code gives `ColorSensorV3` |

A camera (`kind = "camera"`):

| key | default | meaning |
|---|---|---|
| `at` (or `at_in`) | required | where the lens is: `[x, y, z]` in the body's frame |
| `toward` | required | which way it looks, a direction in the body's frame. Not straight up or down (within 1° of the body's z): its up is the body's z |
| `hfov_deg` | 70 | its horizontal field of view, degrees (more than 0, less than 180). An estimate: an OV9281 with its stock lens |
| `vfov_deg` | 50 | its vertical field of view, degrees (more than 0, less than 180). An estimate |
| `max_range` (or `max_range_in`) | 5.0 m | the farthest it detects a 6.5 in tag. An estimate: 6.5 in tags at 1280x800 |
| `max_angle_deg` | 60 | the most edge-on it detects a tag: degrees between the tag's face direction and the camera (more than 0, at most 90). Our choice |
| `fps` | 30 | how often it looks, frames per second of sim time (more than 0, at most 1000). Our choice |

A key a sensor's kind doesn't take is an error that lists the kind's keys. Give a length or a point in meters or in
inches, not both (`at` and `at_in` together is an error).

## What the readings are

**When.** Every sensor is read once every physics step (1 ms), from where everything was at the start of that step. So
a reading is 1 ms behind the physics: far less than any real sensor's own response time.

**What a sensor sees.** Each sensor is a straight line: a beam break's from `from` to `to`, a distance or color sensor's
from `at` along `toward`, `max_range` long. On that line:

- **The robot is never in the way.** A sensor looks through its own robot's parts, so a beam drawn through a plate, or
  a sensor mounted inside a bracket, still sees a piece beyond it. It looks past at most 32 of the robot's surfaces
  (a box it passes through is two); a sensor with more in its way reads nothing past them, and scrimmage warns once,
  naming it.
- **The field always is.** The walls, goals, obstacles, the field's moving elements and the carpet block every
  sensor, `sees = "pieces"` too: a piece behind a wall is hidden, as it would be from a real beam. A pieces-only sensor
  that hits the field first reads nothing there.
- **Pieces are the field's balls, discs and rigid pieces** (a coral, a cone, a note), whatever their shape.
- **Not the balls a `[shooter]` holds.** A `[shooter]`'s hopper is a count, not balls in the world: its balls are kept
  out of play, under the field, until they are launched, so no sensor sees them. Use
  [`[[shooter.beam_break]]`](test-range.md#beam-breaks) for a beam break on the shooter's ball path.

**A beam break** reads broken while any piece touches the line between its two ends. The beam reaches 1 mm past each
end, so a piece resting exactly on an end reads broken every step, and doesn't flicker on and off.

**A distance sensor** reads the distance from `at` to the first thing it sees, or `max_range` with nothing closer. On
`analog`, that is `volts_per_m` times the distance, from 0 V up to 5 V at the most: 1.95 V with nothing in range at the
default 2 m. On `dio`, it is on while the distance is less than `threshold`.

### The color sensor

A color sensor reads the color of the first piece in front of it, within `max_range`. Its values are the ones a REV
Color Sensor V3 gives: red, green, blue and IR counts, and a proximity from 0 to 2047. **These numbers are estimates**,
our own choice from REV's datasheet, not measured from a sensor:

| what | how it reads here |
|---|---|
| brightness | full up to 2 cm, half at 6 cm, nothing at 10 cm, along straight lines in between (estimate) |
| red, green, blue | the piece's color (r, g, b, each 0 to 1) times the brightness times 2047 (estimate: 2047 counts at full brightness) |
| IR | the brightness times 2047 for a piece. For something that isn't a piece (with `sees = "anything"`), 0.3 of that (estimate), so IR and proximity agree |
| proximity | 2047 at contact, falling along a straight line to 0 at 10 cm, for whatever it hits (an estimate of REV's curve) |
| nothing in front | red 12, green 14, blue 12, IR 8 (a dark carpet: estimate), proximity 0 |

So a red piece (0.9, 0.1, 0.1) 2 cm away reads red 1842, green 205, blue 205. With `sees = "anything"`, a wall or the
carpet in front reads the dark counts in red, green and blue (`getColor()` reads them as a gray), with an IR and a
proximity reading for how close it is.

**Raw counts top out at 2047 here.** A real Color Sensor V3's raw color counts go much higher. `getColor()` divides
each count by the sum of the three, so it reads the same whatever the scale: the red piece above reads (0.818, 0.091,
0.091). `ColorMatch` works on `getColor()`, so it works unchanged. Robot code that compares `getRawColor()` or
`getIR()` with fixed numbers from a real sensor needs its own numbers here; compare `getColor()` instead.

## A camera

A camera reports which AprilTags of the field it sees. The field needs a layout: the range file's
`[range] apriltags` ([test-range.md](test-range.md)). On a range with no tags, a camera sees nothing and costs nothing.

```toml
[[sensor]]
name = "front_cam"
kind = "camera"
body = "drive.chassis"
at = [0.30, 0.0, 0.25]
toward = [1, 0, 0.17]            # pitched up about 10 deg
hfov_deg = 70                    # estimate: an OV9281 with its stock lens
vfov_deg = 50                    # estimate
max_range = 5.0                  # m; estimate: 6.5 in tags at 1280x800
max_angle_deg = 60               # choice: tags seen more edge-on than this aren't detected
fps = 30                         # how often it checks (choice)
```

**When a tag counts as seen.** At each camera frame (`fps` times a second of sim time), a tag is seen when all of
these hold:

- its center is inside both fields of view, measured in the camera's frame: forward is `toward`, and up is the body's
  z, square to forward. A tag behind the camera isn't seen;
- it is within `max_range`. `max_range` is for 6.5 in tags: with the range's `apriltag_size_in` set, the camera sees
  out to `max_range` times that size over 6.5, since a tag's size in pixels goes with its size;
- its face points toward the camera within `max_angle_deg`. A camera behind the tag, or seeing it more edge-on than
  that, sees nothing, even in range and in view;
- a ray from the camera to 2 cm short of the tag's face hits nothing that blocks the view.

Exactly at a limit counts as seen. A tag mounted a little inside the solid it hangs on (2024's SPEAKER tags are 3.8 cm
inside the speaker wall) is seen at its face, in front of that solid ([test-range.md](test-range.md)).

**What blocks the view:** every solid but the robot's own parts and the field's four perimeter walls (which are where
tags are mounted). The field's elements, obstacles, goals, game pieces and other robots all block it: a STAGE leg, a
piece in the way or a partner robot hides a tag. Something that touches nothing (the tags' squares, zone tape, any
part drawn only for looks) isn't a solid, and never blocks it.

**After a poke**, `set_pose` or `move_body`, the camera looks again at once, so the reading is of the new pose.

**The estimates, and setting them from your own camera.** The defaults are for one common camera, an OV9281
(Arducam, as many PhotonVision coprocessors use) with its stock lens:

- **`hfov_deg` and `vfov_deg`**: from your lens's datasheet, or PhotonVision's calibration (it reports the camera's
  fields of view). A wide-angle lens is 90 or more.
- **`max_range`**: test it at the shop. Mount a 6.5 in tag at its field height, back the robot away until your
  pipeline stops detecting it, and use that distance (in your lighting, at your resolution and exposure).
- **`max_angle_deg`**: the same test, turning the robot (or the tag) instead of backing away.
- **`fps`**: what your pipeline runs at. A camera looks at most once a physics step.

**Cost:** a camera casts one ray per tag in view per frame, and nothing on a range with no tags or a robot with no
camera.

## Piece colors

A color sensor reads a piece kind's color:

- **The kind's `color`**, given in its `[piece.<name>]`, or in a range file's `[range]` for its own `ball`, `disc` or
  `piece` (see [test-range.md](test-range.md#piecename-a-kind-of-piece)). A preset kind takes it too, so a 2022 field
  can have red and blue cargo as two kinds:

  ```toml
  [piece.red_cargo]
  ball = "2022-cargo"
  color = "red"

  [piece.blue_cargo]
  ball = "2022-cargo"
  color = "blue"
  ```

- **Otherwise, the color its form was always drawn in:** a ball yellow, a disc white, a rigid piece off-white.

The piece is drawn in the same color in the viewer and the replay. A **Piece** picked in the Test tab's Setup in place
of a field's first `[piece.<name>]` (the red cargo of a red and blue field) keeps that table's `color` (it stays red),
as one in place of a range file's `[range]` ball keeps its `[range] color`. A table with no `color` of its own leaves
the new piece its form's color.

`color` is one of the named colors below, or `[r, g, b]` with each 0 to 1. The Test tab's **is seeing** names the
nearest of them to the color the sensor reads (the straight-line distance between the two colors' r, g and b), so a
piece of a slightly different red still reads as red:

| name | r, g, b |
|---|---|
| `red` | 0.9, 0.1, 0.1 |
| `blue` | 0.1, 0.25, 0.9 |
| `yellow` | 0.95, 0.85, 0.1 |
| `orange` | 1.0, 0.5, 0.0 |
| `green` | 0.1, 0.7, 0.2 |
| `purple` | 0.55, 0.2, 0.75 |
| `white` | 0.95, 0.95, 0.95 |
| `gray` | 0.5, 0.5, 0.5 |

## Robot code

Robot code reads the sensors with the same classes as on a real robot:

| key | robot code | reads |
|---|---|---|
| a beam break's `dio` | `DigitalInput(dio)` | `false` while the beam is broken (`true` with `active_low = false`) |
| a distance sensor's `analog` | `AnalogInput(analog)` | `getVoltage()`: `volts_per_m` times the distance, 0 to 5 V |
| a distance sensor's `dio` | `DigitalInput(dio)` | `false` while something is closer than `threshold` (`true` with `active_low = false`) |
| a color sensor's `i2c` | REVLib's `ColorSensorV3(I2C.Port.kOnboard)` or `(I2C.Port.kMXP)` | `getColor()`, `getRawColor()`, `getIR()`, `getProximity()` |

**Channels can't clash.** A sensor's DIO, analog input or I2C port can't be one that a `[[digital]]` or `[[analog]]`
entry, a `[[shooter.beam_break]]`, an encoder or another sensor already uses: that is an error naming both. An encoder
on `channel_a = 0` (`"DIO:0"` in a part's `ids`) is `Encoder(0, 1)` and takes DIO 0 and 1, so a sensor on either is an
error. A robot's other `DigitalInput`s use DIO channels too: pick a channel your robot code doesn't use for anything
else.

**The color sensor's port.** The I2C port is `I2C.Port.kOnboard` (0) for `i2c = "onboard"` and `I2C.Port.kMXP` (1) for
`i2c = "mxp"`. Scrimmage writes the values of REVLib's simulated device, `REV Color Sensor V3[1,82]` on the MXP port
(`[0,82]` onboard; 82 is the sensor's I2C address, 0x52), so the color sensor needs no extra setup. If the
robot program hasn't made a `ColorSensorV3` on the sensor's port 2 s after it connects, the run's notes say so, and
name the other port if the program used that one.

Python (RobotPy, with `robotpy-rev`):

```python
import rev
import wpilib

# getColor()'s readings of the field's red and blue pieces (red's (0.9, 0.1, 0.1) over its sum, and blue's)
RED = wpilib.Color(0.82, 0.09, 0.09)
BLUE = wpilib.Color(0.08, 0.2, 0.72)


class MyRobot(wpilib.TimedRobot):
    def robotInit(self):
        self.bin_beam = wpilib.DigitalInput(4)                  # kind = "beam", dio = 4
        self.range = wpilib.AnalogInput(0)                      # kind = "distance", analog = 0
        self.eye = rev.ColorSensorV3(wpilib.I2C.Port.kMXP)      # kind = "color", i2c = "mxp"
        self.matcher = rev.ColorMatch()
        self.matcher.addColorMatch(RED)
        self.matcher.addColorMatch(BLUE)

    def robotPeriodic(self):
        ball_in = not self.bin_beam.get()                       # false while broken
        meters = self.range.getVoltage() / 0.977                # volts_per_m
        color, confidence = self.matcher.matchClosestColor(self.eye.getColor())
        red_piece = self.eye.getProximity() > 400 and color == RED     # 400: within about 8 cm
```

Java (with REVLib):

```java
import com.revrobotics.ColorMatch;
import com.revrobotics.ColorMatchResult;
import com.revrobotics.ColorSensorV3;
import edu.wpi.first.wpilibj.AnalogInput;
import edu.wpi.first.wpilibj.DigitalInput;
import edu.wpi.first.wpilibj.I2C;
import edu.wpi.first.wpilibj.util.Color;

// getColor()'s readings of the field's red and blue pieces
private static final Color RED = new Color(0.82, 0.09, 0.09);
private static final Color BLUE = new Color(0.08, 0.2, 0.72);

private final DigitalInput binBeam = new DigitalInput(4);              // kind = "beam", dio = 4
private final AnalogInput range = new AnalogInput(0);                  // kind = "distance", analog = 0
private final ColorSensorV3 eye = new ColorSensorV3(I2C.Port.kMXP);    // kind = "color", i2c = "mxp"
private final ColorMatch matcher = new ColorMatch();

@Override
public void robotInit() {
  matcher.addColorMatch(RED);
  matcher.addColorMatch(BLUE);
}

@Override
public void robotPeriodic() {
  boolean ballIn = !binBeam.get();                                     // false while broken
  double meters = range.getVoltage() / 0.977;                          // volts_per_m
  ColorMatchResult match = matcher.matchClosestColor(eye.getColor());
  boolean redPiece = eye.getProximity() > 400 && match.color == RED;   // 400: within about 8 cm
}
```

Check `getProximity()` (or that `ColorMatch` found a match) before trusting the color: with nothing in front,
`getColor()` reads the carpet's gray, and the closest match is still one of your colors.

A camera gives robot code nothing directly: scrimmage publishes no PhotonVision or Limelight results. Its IDs are on
NetworkTables at `/MuJoCo/Camera/<name>/TagIds` (an int array) for dashboards and logs; for vision in robot code, see
the next section.

## Vision in robot code

scrimmage publishes the robot's true pose as `/MuJoCo/GroundTruthPose`, a `Pose2d` struct on NetworkTables. PhotonLib's
own simulation, `VisionSystemSim`, takes a robot pose and a tag layout and simulates each camera, its noise and its
latency inside your robot program, so your pose estimator gets results as on the real robot. Feed it scrimmage's true
pose and the same tag layout the range uses. For a layout scrimmage ships, WPILib ships the same file:
`2024-crescendo` is the JSON of WPILib's `AprilTagFields.k2024Crescendo` byte for byte, so load it with
`AprilTagFieldLayout.loadField(AprilTagFields.k2024Crescendo)` in Java or
`AprilTagFieldLayout.loadField(AprilTagField.k2024Crescendo)` in Python (`robotpy_apriltag`), as the snippets do, and
your robot code doesn't point into scrimmage's files. For your own layout JSON, copy the file into your project's
`deploy` folder and give its path there to `AprilTagFieldLayout`'s constructor (the folder is
`Filesystem.getDeployDirectory()` in Java, `wpilib.getDeployDirectory()` in Python).

**This snippet isn't run in this repo's tests**: PhotonLib is a vendordep from the network, so nothing here checks it
against PhotonLib's API. A test pins the topic's name and type. The snippets match **PhotonLib v2026.3.4**, checked by
reading its sources (2026-11-10). PhotonLib 2027 changes `addAprilTags`: it takes a WPILib `Field`, not an
`AprilTagFieldLayout` (and WPILib's Java packages move to `org.wpilib.*`), so check the names against PhotonLib's docs
for your season.

Java (in `Robot.java`):

```java
import edu.wpi.first.apriltag.AprilTagFieldLayout;
import edu.wpi.first.apriltag.AprilTagFields;
import edu.wpi.first.math.geometry.*;
import edu.wpi.first.networktables.NetworkTableInstance;
import edu.wpi.first.networktables.StructSubscriber;
import org.photonvision.PhotonCamera;
import org.photonvision.simulation.PhotonCameraSim;
import org.photonvision.simulation.SimCameraProperties;
import org.photonvision.simulation.VisionSystemSim;

PhotonCamera camera = new PhotonCamera("front_cam");     // the name your robot code reads results from
VisionSystemSim visionSim = new VisionSystemSim("main");
StructSubscriber<Pose2d> truePose = NetworkTableInstance.getDefault()
    .getStructTopic("/MuJoCo/GroundTruthPose", Pose2d.struct).subscribe(new Pose2d());

@Override
public void simulationInit() {
  visionSim.addAprilTags(AprilTagFieldLayout.loadField(AprilTagFields.k2024Crescendo));   // the range's 2024-crescendo
  SimCameraProperties props = new SimCameraProperties();
  props.setCalibration(1280, 800, Rotation2d.fromDegrees(80));   // 80 deg diagonal: about 71 x 48 deg at 1280x800
  props.setFPS(30);
  PhotonCameraSim cameraSim = new PhotonCameraSim(camera, props);
  cameraSim.setMaxSightRange(5.0);   // the [[sensor]]'s max_range (m): PhotonLib has no range limit without it
  // where the camera is on the robot: the [[sensor]]'s at, and its toward pitched up 10 deg (a negative pitch)
  visionSim.addCamera(cameraSim,
      new Transform3d(new Translation3d(0.30, 0.0, 0.25), new Rotation3d(0, Math.toRadians(-10), 0)));
}

@Override
public void simulationPeriodic() {
  visionSim.update(truePose.get());
}
```

Python (in `robot.py`, with `photonlibpy`):

```python
import math

import ntcore
import wpilib
from photonlibpy import PhotonCamera
from photonlibpy.simulation import PhotonCameraSim, SimCameraProperties, VisionSystemSim
from robotpy_apriltag import AprilTagField, AprilTagFieldLayout
from wpimath.geometry import Pose2d, Rotation2d, Rotation3d, Transform3d, Translation3d


class MyRobot(wpilib.TimedRobot):
    def robotInit(self):
        self.camera = PhotonCamera("front_cam")
        self.true_pose = ntcore.NetworkTableInstance.getDefault().getStructTopic(
            "/MuJoCo/GroundTruthPose", Pose2d).subscribe(Pose2d())

    def _simulationInit(self):
        self.vision_sim = VisionSystemSim("main")
        self.vision_sim.addAprilTags(AprilTagFieldLayout.loadField(AprilTagField.k2024Crescendo))   # 2024-crescendo
        props = SimCameraProperties()
        props.setCalibrationFromFOV(1280, 800, Rotation2d.fromDegrees(80))   # 80 deg diagonal: about 71 x 48 deg
        props.setFPS(30)
        camera_sim = PhotonCameraSim(self.camera, props)
        camera_sim.setMaxSightRange(5.0)     # the [[sensor]]'s max_range (m)
        self.vision_sim.addCamera(camera_sim,
                                  Transform3d(Translation3d(0.30, 0.0, 0.25), Rotation3d(0, math.radians(-10), 0)))

    def _simulationPeriodic(self):
        self.vision_sim.update(self.true_pose.get())
```

Keep the camera's place on the robot (`Transform3d`), its fields of view and its range the same as the `[[sensor]]`'s,
so the Test tab's camera check and your robot code's vision see about the same tags. They don't agree exactly:

- **The defaults differ.** PhotonLib's `SimCameraProperties` has defaults of its own, not scrimmage's 70 x 50 deg, so
  set its calibration as above. PhotonLib takes one diagonal field of view and square pixels: 80 deg at 1280x800 is
  about 71 x 48 deg, not exactly the `[[sensor]]`'s 70 x 50.
- **PhotonLib has no edge-on limit** (`max_angle_deg`): it drops a tag by how small it looks in the image instead.
- **PhotonLib has no range limit** unless you call `setMaxSightRange`, as the snippets do with the same `max_range`.
  For tags smaller than 6.5 in (`apriltag_size_in`), scale it as scrimmage does: `max_range` times the size over 6.5.

A team on a Limelight gets no detections in robot code this way: only the Test tab's camera.

## In the Test tab

The Test tab's **sensor** check is on or off: a beam break is on while broken, a distance sensor while something is
closer than its `threshold`, a color sensor while it sees a piece (optionally, **is seeing** one color), and a camera
while it sees any tag. Each sensor's `sensor.<name>` signal is on the chart, and the replay draws each sensor as a
line, lit while it is on.

A camera has its own check too: **camera** *front_cam* **sees** *tag 7* (or **any tag**), offered on a field with
AprilTags. Its chart signal is how many tags it sees. The replay draws it as its view cone, with a line to each tag it
sees at the moment shown. Details are in [test-tab.md](test-tab.md#sensors).

| what | the camera's |
|---|---|
| the check | **camera** *name* **sees** *tag N* or **any tag**; in the file `{ sees_tag = { camera = "front_cam", tag = 7 } }` (no `tag`: any) |
| the chart | `sensor.<name>`: how many tags it sees |
| the replay | its view cone, lit while it sees a tag, and a line to each tag it sees |
| a failed check | `expected front_cam to see tag 7, but it saw tags 3, 4` (or `it saw no tags`) |

## In scenarios and MCP

A scenario step can check a sensor the way the Test tab's **sensor** check does:

```toml
[[step.check]]
sensor = { name = "bin", is = "on" }                        # on at the end of the step

[[step.check]]
sensor = { name = "eye", sees = "red", when = "by", t = 2.0 }   # seeing a red piece by 2 s into the step

[[step.check]]
sensor = { name = "bin", is = "off", when = "always" }      # never broken during the step
```

`is` is `"on"` (the default) or `"off"`. `sees` (a color sensor only) wants it on and seeing that named color. `when`
is `"end"` (the default: at the end of the step), `"always"` (on every tick of the step) or `"by"` (at some tick up
to `t` seconds into the step). The check reports as the `at_end`, `always` and `by_time` checks do, of the signal
`sensor.<name>` (or `sensor.<name>.sees.<color>`) equal to true for on and false for off.

The signals, for `at_end`, `always` and `by_time` too:

- `sensor.<name>`: true while the sensor is on;
- `sensor.<name>.distance`: a distance sensor's reading in meters (its `max_range` with nothing in range), or a color
  sensor's distance to the piece it sees (no value while it sees none). A beam break has none;
- `sensor.<name>.color`: the named color a color sensor sees (no value while it sees none);
- `sensor.<name>.sees.<color>`: true while a color sensor is on and sees that named color.

And a camera's:

| signal | what it is |
|---|---|
| `sensor.<name>` | true while it sees any tag |
| `sensor.<name>.tags` | how many tags it sees |
| `sensor.<name>.sees_tag.<id>` | 1 while it sees tag `<id>`, else 0 |
| `sensor.<name>.tag_distance.<id>` | meters to tag `<id>`'s center while it sees it, else its `max_range` |

An ID that isn't in the range's layout is an error that lists the layout's IDs, and a camera signal on a range with no
tags is an error that names `[range] apriltags`.

The Scenario tab shows a `sensor` check as one it can't edit yet: change it in a text editor.

The MCP tool `get_state` has a `sensors` block on a robot with `[[sensor]]`s: each sensor's `kind`, `on`,
`distance_m` (meters; `null` for a beam break and while a color sensor sees nothing) and `color` (the named color a
color sensor sees, else `null`); a camera's also has `tags`, the IDs it sees. `set_pose` and `move_body` read the
sensors again at once, a camera too, so the reading they return is of the new pose; at any other time a reading is
taken after each physics step (a camera's at its `fps`).

On NetworkTables, each camera's IDs are at `/MuJoCo/Camera/<name>/TagIds`, an int array, beside
`/MuJoCo/GroundTruthPose`.

## Estimated or not yet checked

Three things here rest on our own numbers or on reading code, not on a real sensor or a real run:

- **Estimated: every number the color sensor gives.** The brightness (full up to 2 cm, half at 6 cm, nothing past
  10 cm), the 2047 counts at full brightness for red, green, blue and IR, the IR of something that isn't a piece (0.3
  of a piece's), the proximity's straight line from 2047 at contact to 0 at 10 cm, and the dark carpet's counts
  (red 12, green 14, blue 12, IR 8). They are our choices from REV's datasheet, not measured from a Color Sensor V3.
  `getColor()` and `ColorMatch` don't depend on the scale; code that compares raw counts or IR with a real sensor's
  numbers does.
- **Not yet checked: Java's color sensor.** REVLib's Java `ColorSensorV3` names its simulated device the same way as
  RobotPy's (`REV Color Sensor V3[<port>,82]`, read from REVLib 2026.0.5's classes), but no Java robot program has
  run one in Scrimmage yet. Only RobotPy has been checked running. If a Java robot's color sensor reads nothing,
  this is the first thing to suspect.

- **Estimated: a camera's defaults.** The 70 x 50 degree fields of view (an OV9281 with its stock lens) and the 5.0 m
  range for 6.5 in tags at 1280x800 are estimates; the 60 degree most edge-on angle and 30 frames a second are our
  choices. [A camera](#a-camera) says how to set each from your own camera.

The beam break and the distance sensor are exact lines: they have no numbers of their own to estimate (see below for
what a real one adds).

## What isn't simulated

- **Noise, delay and mounting.** Every reading is exact, 1 ms behind the physics. A real beam is a few millimeters
  wide, a real distance sensor has a cone, not a line, and a real color sensor's numbers depend on its lighting and
  gain. Allow for them on the real robot.
- **Other vendors' sensors.** The CTRE CANrange, Playing With Fusion's time-of-flight sensor and the LaserCAN each
  need their own vendor binding; none is here yet. A distance sensor on an `analog` or `dio` input is the stand-in.
- **A camera's image.** A camera is a yes-or-no rule per tag, not a real detector: no image, no motion blur, no
  lighting, no pixel noise and no pose ambiguity (a tag seen far away or nearly edge-on is as good as one up close).
  And it publishes no PhotonVision or Limelight results on NetworkTables: robot code simulates its own vision from the
  true pose ([Vision in robot code](#vision-in-robot-code)).
- **A screen for placing sensors.** Sensors live in the config's `[[sensor]]` tables, written by hand; there's no
  picker for them in the Robot tab or the Part tab yet.

## Errors

Each mistake names the sensor and the key, for example:

- `[[sensor]] "bin": needs from = [x, y, z]: one end of the beam, in meters in the body's frame (or from_in, inches)`
- `[[sensor]] "bin": body "intake.base" isn't one of the robot's bodies; its bodies: drive.chassis, dt.rails, ...`
- `[[sensor]] "eye": max_range is 0.2 m: a color sensor sees 10 cm at most (max_range = 0.10)`
- `[[sensor]] "range": threshold (3 m) is past max_range (2 m); make it shorter`
- `[[sensor]] "front_cam": toward is [0, 0, 1]: a camera can't look straight up or down: up is the body's z`
- `[[sensor]] 2: name must be letters, digits and _ (like a part's name), not "front beam"`
- `[[sensor]] "bin": unknown key 'sees' for a beam sensor; its keys: name, kind, body, from, from_in, to, to_in, dio, active_low`
- `REV Color Sensor V3[1,82] is bound by [[sensor]] "eye" and [[sensor]] "eye2": give each device one binding`
- `DIO 1 is bound by encoder dt.left_encoder and [[sensor]] "bin": give each device one binding (an encoder on
  channel_a = 0 takes DIO 0 and 1)`
