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
  renderer hides 3 to 5). So a sensor excludes its own body and, past a hit it mustn't see (the rest of the robot, or
  anything but a piece for `sees = "pieces"`), casts again from just past it. A clear path or a piece first is one
  `mj_ray` a step; each unwanted surface in the way adds one, at most 32 a step. Pieces-only casts skip static
  geoms, so the carpet and walls cost nothing. No model setting changes, so renders and the monitors are as before.
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
  whatever it hits. Red, green, blue and IR stay at the dark carpet's few counts unless a piece is in front. With
  `sees = "anything"`, something that isn't a piece gives a distance and a proximity but no color.
- **D7 Pieces out of play (Task 1):** a parked piece is 20 m under the field, so no sensor sees it. That includes the
  balls a `[shooter]` holds, since its hopper is a count, not bodies.
