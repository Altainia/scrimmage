# Vendor distance sensors: CANrange, LaserCAN, Playing With Fusion Time of Flight

Status: design. The owner delegated the decisions (standing order: decide and document); every call below is marked
**Call** with what it costs if it's wrong.

## Why

`[[sensor]] kind = "distance"` reaches robot code today only as an `AnalogInput` or a `DigitalInput`. Most teams'
distance sensors are CAN time-of-flight sensors instead: CTRE's **CANrange** (Phoenix 6), Grapple's **LaserCAN** and
Playing With Fusion's **Time of Flight** (SEN-36005). A student who wires one of these on the real robot writes robot
code against the vendor class (`CANrange.getDistance()`, `LaserCan.getMeasurement()`, `TimeOfFlight.getRange()`), and
that code reads nothing in scrimmage today. This was deferred from the sensors sub-project (PR #9) as "vendor sensors".

Success: a student adds one key to a distance sensor they already have, runs their unchanged robot code (Java or
RobotPy), and the vendor call returns what the physics sees.

## What I found (probe, 2026-11-13)

A throwaway Java robot (`./gradlew simulateJava`, WPILib 2026, Linux) made one of each and listed every SimDevice once
a second:

| device | library | in simulation |
|---|---|---|
| CANrange 5 | Phoenix 6 26.1.2 | registers SimDevice `CAN:CANrange (v6)[5]` with inputs `distance` and `supplyVoltage` and output `isDetected`. `getDistance()` read 0.0 and `getIsDetected()` true with nothing written |
| LaserCAN 7 | libgrapplefrc 2026.0.0 | registers no SimDevice. `getMeasurement()` returns null. The library ships `interfaces.LaserCanInterface` and `simulation.MockLaserCan` (no SimDevice either; robot code calls `setMeasurementFullSim`) |
| TimeOfFlight 6 | PlayingWithFusion 2026.5.08 | registers no SimDevice. `getRange()` 0.0, `isRangeValid()` false, status Invalid |

CTRE documents `CANrangeSimState.setDistance(meters)`; the SimDevice input is what it writes. Licenses: libgrapplefrc
is a "Grapple Modified MIT License" (modification permitted). Playing With Fusion's Java library has no license I
could find. CTRE told us (2026-10-27) not to change Phoenix classes' bytecode; nothing here needs that.

RobotPy: `phoenix6` (the same native library, so the same SimDevice is expected), `robotpy-playingwithfusion`
2026.2.13.1 on PyPI, and Grapple's own `libgrapplefrc-py` (in the libgrapplefrc repo; not on PyPI under that name).
None of the Python sides was run yet: task 1 checks them.

## Goals

- **CANrange**, Java and RobotPy: scrimmage writes the SimDevice's `distance` (and `supplyVoltage`) from the sensor's
  ray. Phoenix computes `isDetected` from its own configured threshold, as on the real sensor.
- **LaserCAN**, Java and RobotPy: robot code's `LaserCan(id).getMeasurement()` returns scrimmage's reading, with the
  status the real sensor gives (valid, or the no-target status), through scrimmage's existing robot-side helpers (the
  Java agent, the RobotPy lockstep helper), the way the REV helper drives SPARKs.
- The Test tab, scenarios, MCP `get_state` and the docs treat these like any other `distance` sensor.

## Non-goals

- **Playing With Fusion Time of Flight, for now.** See Call V5.
- A field of view (cone). See Call V3.
- Sensor noise, ambient light, signal strength, timing budgets, regions of interest. Readings stay ideal, as every
  scrimmage sensor's are (docs/sensors.md, "The readings are ideal").
- Other CAN sensors (CANdi, CANcoder-as-proximity, Pigeon): out of scope.

## Anti-goals

- Robot code that has to change for scrimmage. A student's code that works on the real robot must read the sensor in
  scrimmage unchanged (beyond what the vendor itself requires for simulation).
- Changing a vendor's classes against their wishes (CTRE's answer).

## The design

### Config

A distance sensor names its device with one key, mirroring the motor bindings' `ctre = "<Kind>:<id>"` and
`rev = "<Kind>:<id>"`:

```toml
[[sensor]]
name = "note"
kind = "distance"
body = "intake.roller"
at = [0.10, 0.0, 0.05]
toward = [0, 0, -1]
ctre = "CANrange:5"        # or grapple = "LaserCAN:7"
```

- **Call V1.** Keys `ctre = "CANrange:<id>"` and `grapple = "LaserCAN:<id>"`, at most one device key per sensor, and
  they may sit beside `analog` / `dio` (a sensor can feed both, as `analog` and `dio` already can). An id is 0 to 62.
  A device key alone is enough (no `analog` or `dio` needed). The keys belong to `kind = "distance"` only: on any other
  kind they are the existing "unknown key" error. `threshold` keeps its meaning (when the sensor reads `on` for the
  Test tab and a `dio`), device key or not. Two sensors naming the same device is an error naming both.
  *Cost if wrong:* a key rename later, with an error naming the new key.
- **Call V2.** `max_range` defaults per device when left out: CANrange 4.0 m (CTRE's datasheet maximum is to be
  checked in task 1; the value is an estimate until then), LaserCAN 4.0 m in long mode. Beyond it the sensor sees
  nothing. *Cost:* a student with a short-mode LaserCAN sees farther than the real one until they set `max_range`.
- **Call V3.** One ray, like today's `distance` sensor. The real sensors see a cone (CANrange's field of view is
  configurable, LaserCAN's region of interest up to 27°), so a piece off the axis is seen in life and not here. A
  `fov_deg` is a follow-up. *Cost:* an intake that relies on a wide cone reads "nothing" when the piece is off-center;
  docs/sensors.md's "What isn't simulated" says so.

### CANrange

- `SensorRuntime.sensor_updates` adds, per `ctre` sensor, the CANrange SimDevice with `>distance` = the reading in
  meters and `>supplyVoltage` = 12.0, keyed the way the CANcoder binding keys `CANcoder (v6)[id]` (bindings.py).
- **Call V4.** Nothing seen: write `max_range` (the runtime's `Reading.distance` already is `max_range` on a miss, so
  `sensor_updates` doesn't branch). CTRE gives no "no target" input, and its `isDetected` compares the
  distance with the configured proximity threshold, so the maximum reads "not detected" for any sane threshold.
  *Cost:* code that reads `getDistance()` with nothing there gets `max_range` where the real sensor's value is
  unspecified (often the last or a noisy value). Docs say so.
- A CANrange is a Phoenix 6 device, so a robot with one runs in real time like every Phoenix robot (the bridge's
  existing rule). The docs' Phoenix section says so.
- `devicecheck.py` / the device list: a CANrange the config names and the robot never creates gets the same "not
  created" warning a CANcoder gets; one the robot creates and the config doesn't name is listed as not modeled.

### LaserCAN

- Java agent: a hook at the end of `au.grapplerobotics.LaserCan`'s constructors registers each LaserCAN with a new
  helper, `ScrimmageGrapple`, built like `ScrimmageRev` (reflection only; robots without libgrapplefrc pay nothing).
  It registers a SimDevice `ScrimmageGrapple:LaserCAN[<id>]` with inputs `distance_mm` (int) and `status` (int), and
  `LaserCan.getMeasurement()` returns a `Measurement` built from them (ambient 0, `is_long` true, `budget_ms` 33, the
  ROI the robot code last set or the default) once scrimmage has written them, and null before, as the real one does
  until its first frame.
- RobotPy: the lockstep helper does the same to libgrapplefrc-py's `LaserCAN` (patching `get_measurement`), if
  task 1 finds the Python class can be patched; else RobotPy LaserCAN is listed as not supported, with the reason.
- `MockLaserCan`: robot code that builds one in simulation feeds it itself; scrimmage leaves it alone and the docs
  say to construct the real `LaserCan` instead (scrimmage feeds it).
- Status values: the valid status, and for nothing seen, the status the real sensor reports with no target, with
  `distance_mm` = `max_range` in mm (Call V6). Task 1 reads the statuses from Grapple's sources (the firmware and
  library are public) and records the source next to the constants.
- **Call V6.** The helper honors the ranging mode: it records `setRangingMode`, sets `is_long` from it, and reports
  the no-target status for a distance past the mode's reach (short mode's reach from Grapple's docs, checked in
  task 1; 1.3 m expected). *Cost:* none beyond a recorded value; without it a short-mode robot would see too far.
- **The hook** (checked by `javap` on 2026.0.0): `LaserCan` is a public, non-final class; `getMeasurement()` is plain
  Java over a native `getMeasurementInternal()`; `Measurement`'s constructor is public; constructing a `LaserCan(7)` in
  simulation ran without error in the probe. The agent rewrites `getMeasurement()`'s entry (as it patches REV's
  `SparkSim.iterate`): when the helper has a value for that LaserCAN it returns it, else the original runs. Task 1
  proves this hook end to end before anything else is built on it.
- `sensor_updates` writes the helper SimDevice the way it writes the color sensor's.
- When the helper isn't loaded (Java without scrimmage's init script, RobotPy without `--scrimmage-lockstep`), the run
  warns once, as the REV helper's missing-helper warning does.

### Playing With Fusion Time of Flight

- **Call V5.** Not built now. Its library registers nothing in simulation, so supporting it means changing
  `TimeOfFlight`'s methods at run time, and its license doesn't say whether that's allowed. I'll draft a short
  question to Playing With Fusion (would they object, and would they consider registering a SimDevice, as CTRE's
  CANrange does) for the owner to review and post. Until there's an answer, a `pwf` key is an error that says it
  isn't supported yet and points to `analog`. *Cost:* PWF teams keep wiring an analog stand-in in scrimmage.

### Everything else

- **Call V7. Fast mode is out of scope.** A fast-mode robot (docs/fast-mode.md) never creates the real `CANrange`,
  so the SimDevice write reaches nothing. The docs say so; a `ScrimmageCANrange` stand-in in the fast-mode library is a
  follow-up. *Cost:* a fast-mode robot with a CANrange reads nothing until then; the run's existing "not created"
  warning names the CANrange.
- **Call V8. When a check in task 1 goes against this design** (a Python class can't be patched, a package won't
  install on CI's Pythons, a datasheet number differs), the builder takes the fallback written here, records it as a
  ruling with its cost, and goes on: RobotPy LaserCAN listed as not supported with the reason; a RobotPy end-to-end
  test skipped with the reason; the datasheet's number used. Only the Java LaserCAN hook failing stops the LaserCAN
  half for a redesign; CANrange goes ahead either way.
- Test tab, scenarios, MCP: no changes beyond showing the device (`CANrange 5`) where a sensor's wiring is listed.
- Docs: docs/sensors.md gets a "CAN distance sensors" section (the keys, Phoenix's real-time rule, LaserCAN's
  helper, what isn't simulated, PWF's status), plus an example config.

## Tests (what proves it)

- Spec: the keys, the id range, one device key at most, a device named twice, the keys on another kind, the
  per-device `max_range`, the PWF error.
- Runtime: `sensor_updates` writes the CANrange and LaserCAN values (seen, and nothing seen).
- Java end to end (the java_probe pattern; CI's java job): a probe robot with a CANrange and a LaserCAN, a piece placed
  in front of each, reads the distance through `getDistance()` and `getMeasurement()` within 1 mm, and "nothing"
  when the piece is moved away.
- RobotPy end to end, if task 1 finds the packages install on CI's Python versions; else a recorded skip with the
  reason.

## Left to the builder

- The helper SimDevice's exact value names and int/double types; where the Grapple hook sits in ScrimmageAgent.
- How the vendordeps for the probe are fetched in CI (the Phoenix one is committed already; the Grapple JSON can be
  committed the same way: its license allows redistribution with the notice).
