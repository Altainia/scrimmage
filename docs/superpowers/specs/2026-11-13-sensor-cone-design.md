# A distance sensor's field of view (cone)

Status: design (decide-and-document; follow-up from docs/superpowers/specs/2026-11-13-vendor-distance-sensors-design.md
Call V3). Each **Call** has its cost.

## Why
A distance sensor casts one ray today, so a piece beside its axis is invisible. Real time-of-flight sensors see a cone:
CTRE's CANrange has a configurable field of view (up to 27°), Grapple's LaserCAN a region of interest (16x16 SPADs, about
27°, by default). An intake that relies on that cone reads "nothing" in scrimmage when the piece is off-center.

## The design
- **Key `fov_deg`** on `kind = "distance"` sensors: the cone's full angle, in degrees, 0 < fov_deg <= 60. Absent: one
  ray, as today (Call C1 for the default).
- **Call C1 (defaults).** A sensor with a CAN device key (`ctre`, `grapple`, `scrimmage`) defaults to 27°, the vendors'
  defaults (CTRE's CANrange FOV maximum and default; LaserCAN's default 16x16 ROI; sources recorded beside the constant
  as in spec.py's others; if a source says otherwise, use the source). Any other distance sensor stays one ray unless
  `fov_deg` is given. *Cost:* a CAN sensor that relied on the single ray (tests, configs written since PR #43) now sees
  wider; tests that place pieces on the axis are unaffected.
- **Call C2 (the pattern).** 17 rays: the axis, a ring of 8 at a quarter of the full angle off the axis, and a ring
  of 8 at half the full angle (the cone's edge), the rings offset by 22.5°. Each ray is cast as the single ray is now
  (the recast past the robot's own surfaces, `sees`, `max_range`). *Cost:* a piece smaller than the gap between rays at
  that distance can slip through (at 27° and 0.5 m the edge rays are about 9 cm apart): the docs say so.
- **Call C3 (the reading).** The nearest hit of all the rays, measured along its own ray (a time-of-flight sensor
  measures path length). The `on` / `threshold` logic, the analog volts and the CAN writes take that distance unchanged.
  *Cost:* real sensors weight by signal over the zone, so a small near target at the edge reads nearer here than on a
  real sensor.
- **Cost of casting:** 17 times the casts of one ray for that sensor. MAX_CASTS stays per ray (the "robot in its way"
  warning names the sensor once if any ray runs out). Measure a step's time with and without one 27° sensor on a
  stock robot and record it in the docs (a number, not a test).
- **Drawing:** where the sensor's ray is drawn today (check the live view / replay / Test tab), draw the center ray
  as now; drawing the cone is not in scope.
- The color sensor and the beam don't change.

## Tests
- Spec: `fov_deg` accepted on distance, rejected on other kinds (the existing unknown-key error), range checks (0, 61,
  negative, non-number), the 27° default with a CAN key, none without.
- Runtime: a piece off the axis inside the cone is seen (and outside it isn't); the reading is the nearest ray's along
  its own ray; a single-ray sensor is unchanged (an existing golden or a direct comparison).
- Docs: docs/sensors.md (the key, defaults, the pattern and its gaps, the cost number); remove the "one ray, not a cone"
  not-simulated line or turn it into the gap note.
