# Sensors: piece-present, distance and color (sub-project 26)

## Intent

The survey found piece detection in every modern game and color sensing in 2020 and 2022. Robot code can't see a
game piece in Scrimmage today, except through the `[shooter]`'s beam breaks on its own ball path. A team that stops its
intake when a ball arrives, indexes balls to a beam break, or rejects the wrong alliance's ball by color has nothing
to read. The only other sensors are limit switches and potentiometers on joints.

The owner asked me to handle the remaining work in my own order and answer questions myself. This project is the
survey's last unclaimed gap (gap 13). Every call is under "My calls".

What a student wants to find out:
- Does my beam break see the ball where I put it?
- Does the intake stop in time when a piece arrives?
- Does my distance sensor read the piece at the range I expect?
- Can my color sensor tell red from blue at this spot?

The audience is the usual: students in the Test tab with a stand-in robot, and teams running robot code. **No
unnecessary friction.**

## Goals

1. **`[[sensor]]` entries** in a robot config, each mounted on a robot body at a point (in the body's frame), in
   three kinds:
   - **`beam`: a beam break.** A straight beam from `from` to `to` on the body. It reads broken while any game piece
     (a ball, a disc or a rigid piece) crosses the beam.
   - **`distance`: a range finder.** A ray from `at` along `toward`, out to `max_range`. It reads the distance to the
     first thing it hits, either game pieces only or anything except the robot itself.
   - **`color`: a REV Color Sensor V3.** It looks along a ray like `distance`, reads the color of the game piece in
     front of it within its range, and gives a proximity reading.
2. **Robot code reads them** through the devices teams already use:
   - **`beam`:** a `DigitalInput` (`dio = N`), active-low by default as IR beam-break receivers are.
   - **`distance`:** an `AnalogInput` (`analog = N`, volts per meter, as an ultrasonic sensor), or a `DigitalInput`
     that switches at a threshold (an IR proximity switch).
   - **`color`:** REVLib's `ColorSensorV3` on the onboard or MXP I2C port. Scrimmage writes the red, green, blue, IR
     and proximity values of its SimDevice (`REV Color Sensor V3[<port>,82]`, checked against RobotPy's REVLib
     2026).
3. **Test tab:**
   - **A condition:** *sensor [name] is on / off*, where on means broken, near or seeing a piece, with a color sensor
     optionally matching *sees [color]*.
   - **Chart signals:** `sensor.<name>` (0/1 for a beam, inches for a distance, the matched color name or nothing for
     a color sensor), a palette list of the robot's sensors, and editor blocks.
   - **No new action blocks:** *wait until sensor … is on* then *stop* the roller is already possible with the
     existing blocks.
4. **The replay** draws each beam and ray, lit while it sees something.
5. **Piece colors:**
   - **Where they come from:** each piece kind has a color, its drawn color (already in the presets), and a color
     sensor reads that color.
   - **Matching:** colors match by nearest named color from a short list (red, blue, yellow, orange, green, purple,
     white, gray) as well as raw RGB, so a student can write *sees red*.
   - **Fields:** a field may give a piece kind its own color, so a 2022 field can have red and blue cargo as two
     kinds.
6. **An example and docs:**
   - **`examples/blocks/beam`:** a stand-in robot whose intake stops when its beam break sees a ball. It has a test
     and a README.
   - **A RobotPy end-to-end test:** a `DigitalInput` beam break and a `ColorSensorV3` read the right values.
   - **Docs:** a "Sensors" section in parts.md (or its own page), test-tab.md, and the README bindings table.

## Non-goals

- **Cameras and vision** (a stated non-goal), Limelight-style targeting, and AprilTags.
- **Vendor sensors** other than REV's Color Sensor V3: the CTRE CANrange, Playing With Fusion time-of-flight and
  LaserCAN. Each needs its own vendor binding, and they're noted as follow-ups.
- **Sensor noise, latency and mounting tolerances.** The readings are ideal. The docs say so.
- **A Part tab screen for placing sensors.** They live in the config's `[[sensor]]` table. A later project can add a
  picker.

## Anti-goals

- **No change for robots without `[[sensor]]`:** every run is byte-identical, and the ledger golden passes.
- **No cost when unused**, and a bounded cost when used: one `mj_ray` per beam or ray per physics step, against
  piece geoms only when the sensor sees pieces.

## Constraints

- **Rays:** MuJoCo `mj_ray` with a geom-group filter or a body exclusion, so a sensor never sees its own robot.
  Pieces are the `ball.<i>` bodies the range builds (balls, discs and rigid pieces alike).
- **Channels:** sensors' DIO and analog channels must not clash with the robot's other bindings or the shooter's beam
  breaks. Today's duplicate-device check covers them.
- **Spellings and wording:** American spellings and plain words.

## My calls (the owner can overrule any)

- **C1 Config only:** sensors are config-level `[[sensor]]` tables naming a robot body (`body = "intake.base"`), not
  part.toml entries. That works for stock and CAD parts alike, with no format change.
- **C2 Beam break wiring:** a beam break is active-low by default, because the common IR beam-break receivers pull
  low when the beam is broken. `active_low = false` flips it.
- **C3 Distance as analog:** `volts_per_m` defaults to 0.977 V per meter, a MaxBotix MB1013's 5 V over 5.12 m (an
  estimate). `max_range` defaults to 2 m, and a reading past it is the max.
- **C4 Distance as a switch:** with `dio` and `threshold`, the input reads on (low when active-low) while something
  is closer than the threshold.
- **C5 Color sensor values:**
  - **Color:** red, green and blue come from the piece's color scaled by a brightness that falls off with distance,
    with full scale at 2 cm, half at 6 cm and nothing past 10 cm (estimates).
  - **IR and proximity:** IR follows brightness, and proximity is 2047 at contact falling to 0 at 10 cm, as REV's
    datasheet describes (an estimate of the curve).
  - **Nothing in front:** low values (the dark carpet).
- **C6 What "sees" means in the Test tab:**
  - a beam is on while broken;
  - a distance sensor is on while closer than its `threshold` (default half of `max_range`);
  - a color sensor is on while a piece is within 10 cm; *sees red* compares the nearest named color.
- **C7 Field colors:** a field's `[piece.*]` or ball can set `color = "red"` or an RGB list. The default is the
  preset's drawn color.

## Left to the builder

- The module layout (suggested: `scrimmage/sensors/`), and the replay drawing.
- The exact wording, within the plain-words rule.

## Decisions made during the build

- **D1 Leaving the robot out (Task 1):** `mj_ray` excludes one body or whole geom groups, and the robot, the field and
  the pieces share group 0 (groups 1 and 2 mean structure and ground to the terrain and climb monitors; MuJoCo's
  renderer hides 3 to 5). So a sensor excludes its own body and, past a hit on the rest of the robot, casts again
  from just past it. A clear path, or a piece or the field first, is one `mj_ray` a step; each robot surface in the
  way adds one, at most 32 a step. No model setting changes, so renders and the monitors are as before. The field
  blocks every ray, `sees = "pieces"` too: a wall, goal, obstacle, field element or the carpet in front of a piece
  hides it, as it would from a real beam, and a pieces-only sensor that hits the field first reads nothing.
- **D2 When a reading is taken (Task 1):** after `mj_step`, from the kinematics `mj_step` computed at the start of
  the step. The sensor and what it hits come from one pass, so they agree; the reading is 1 ms old. Recomputing the
  kinematics would cost a pass a step and write into the data.
- **D3 A beam's ends (Task 1):** the beam reaches 1 mm past each end, so a piece resting exactly on an end reads
  broken every step.
- **D4 A color sensor's range (Task 1):** `max_range` above 0.10 m is an error, since C5's brightness is nothing past
  10 cm.
- **D5 Piece colors (Task 1):** `color` goes in a `[piece.<name>]` or the range file's `[range]` (for its ball, disc or
  piece). The piece is drawn in that color too. Left out, the color is the one the form was always drawn in: yellow
  balls, white discs, off-white rigid pieces. The presets have no color of their own. A robot config's `[range] ball`
  keeps the range file's `[range] color`.
- **D6 Color sensor values (Task 1):** proximity falls along a straight line from 2047 at contact to 0 at 10 cm,
  whatever it hits. IR follows the brightness of whatever it hits (C5): a piece's in full, anything else's times a
  gray reflectance of 0.3 (an estimate), so IR and proximity agree. Red, green and blue stay at the dark carpet's few
  counts unless a piece is in front. With `sees = "anything"`, something that isn't a piece gives a distance, IR and
  a proximity but no color. Full brightness is 2047 counts in every channel, an estimate: the real sensor's raw color
  counts go far higher, and Task 2 decides what the SimDevice gets.
- **D7 Pieces out of play (Task 1):** a parked piece is 20 m under the field, so no sensor sees it. That includes the
  balls a `[shooter]` holds, since its hopper is a count, not bodies.
- **D8 What the color sensor's SimDevice gets (Task 2):** the counts of D6 as they are, full brightness 2047 in each
  of Red, Green, Blue and IR, and Proximity 0 to 2047. REVLib's `getRawColor()` returns these values and `getColor()`
  divides each by the sum of the three, so `getColor()` reads the piece's color over its sum whatever the full scale:
  a red piece's (0.9, 0.1, 0.1) reads (0.818, 0.091, 0.091), and a `ColorMatch` on `getColor()` works. Robot code
  that compares raw counts with a real sensor's (which go far higher than 2047) needs its own thresholds here; the
  docs (Task 4) say so. Checked live with RobotPy 2026.2.2 and robotpy-rev 2026.0.4: halsim_ws_client relays a SimDevice
  whose name has no `<type>:` prefix as type `SimDevice` with its full name as the device
  (`REV Color Sensor V3[1,82]`), and its values as `>Red`, `>Green`, `>Blue`, `>IR` and `>Proximity`.
- **D9 The robot's inputs (Task 2):** a `dio` reads `on` (a beam broken, a distance closer than its threshold),
  inverted when `active_low`; an `analog` reads `volts_per_m` times the distance, clamped to 0–5 V (past
  `max_range`, the max's voltage). Every sensor channel (DIO, AI and each color sensor's I2C port) joins the
  duplicate-device check with the bindings and the shooter's beam breaks, so two on one channel is an error naming
  both. When the robot program creates the color sensor, scrimmage resends its values at once (a SimDevice sends no
  `<init`, and halsim_ws_client drops values for one that doesn't exist yet); a color sensor the robot program hasn't
  created 2 s after connecting is a warning in the run's notes, naming the port and the other port if the robot used
  that one.
- **D10 How a sensor check reads (Task 3):** the block reads "sensor mouth is on" for a beam break or a distance
  sensor, and "sensor eye is seeing red" or "isn't seeing red" for a color sensor (or "is seeing a piece" for any
  color), both states in plain words. The editor takes the color names from the palette the server sends
  (`sensor_colors`), with no copy of the list in the page's code. Why: the check reads as a plain sentence whichever
  state it names, and a second list in JavaScript could drift from `NAMED_COLORS`. Cost: nothing.
- **D11 Sensors on the chart and in the views (Task 3):** every `sensor.<name>` signal is drawn at first (unless the
  test's `chart` line names others); a distance or color sensor's line is drawn out to its `max_range`; the Drive tab's
  live view draws the lines too, never lit. Why: a sensor is something the student added to look at, and a line to
  `max_range` shows where it can see. The live view has no run signals to light them from. Cost: nothing.
- **D12 A color sensor's check with no color (Task 4):** `describe()` and the failure read "eye is seeing a piece" and
  "expected eye to see a piece, but it saw no piece", as the block reads, not "eye is on". `describe()` takes the
  sensors' kinds (`TestRun._describe` passes them). Why: the run's words should match the block's. Cost: nothing.
- **D13 The example's beam is in the bin (Task 4):** `examples/blocks/beam` puts its beam break across the bin, where
  the ball ends up, not at the mouth. Why: at the mouth the beam breaks when the ball first gets under the roller, and
  stopping there leaves the ball on the carpet in front of the lip (tried: the hopper check fails), which the README
  shows as its first change. Cost: nothing.
- **D14 Java's color sensor name (Task 4):** the docs say Java REVLib's `ColorSensorV3` creates the same SimDevice
  (`REV Color Sensor V3[<port>,82]`, from `SimDevice.create(name, port, address)` read in REVLib-java 2026.0.5's
  class file) but hasn't been run in Scrimmage. Why: only RobotPy was checked live (D8). Cost: a Java team may meet
  a mismatch first; the run's notes name a missing color sensor (D9).
- **D15 An encoder's DIO channels (final review):** a quadrature encoder on `channel_a = n` takes DIO n and n + 1 in
  the duplicate-device check, so a sensor, a `[[digital]]` limit switch or a shooter beam break on either is an error
  naming both, with a note of the encoder's two channels. The config has no `channel_b`: scrimmage matches an encoder
  by its first channel, and the second is WPILib's `Encoder(n, n + 1)`. Why: with sensors, a DIO the drive's encoder
  already takes is easy to pick, and the clash breaks both the sensor and the encoder without saying why. Cost: an
  encoder wired to two channels not side by side can't be described; none can be today either.
- **D16 A color sensor's chart signal (Task 3):** `sensor.<name>` for a color sensor is the index of the color it
  sees in `sensor_colors` (the palette's list of `NAMED_COLORS`: 0 red, 1 blue, ... 7 gray), with a gap while it sees
  none, not the color's name as Goal 3 said. The chart's key names each number's color. Why: every chart signal is a
  number a line can be drawn through, and the index keeps the color sensor on the same chart as the others. Cost: the
  chart's line reads as a number, so the key is needed to read it.
