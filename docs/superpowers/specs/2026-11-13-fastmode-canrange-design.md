# Fast mode: a CANrange stand-in

Status: design (decide-and-document; follow-up from docs/superpowers/specs/2026-11-13-vendor-distance-sensors-design.md
Call V7). Each **Call** has its cost.

## Why
A fast-mode robot (docs/fast-mode.md) never creates Phoenix's `CANrange`, so a `ctre = "CANrange:<id>"` sensor
reaches nothing and its IO class reads 0. Fast mode needs a CANrange stand-in like its `ScrimmageCANcoder` and
`ScrimmageGyro`.

## The design
- **Library class** `scrimmage.sim.ScrimmageCANrange` (scrimmage/java/fastmode), built like `ScrimmageGyro`:
  `Scrimmage.claim`, a HAL SimDevice `ScrimmageCANrange:<name>` with one input `distance` (meters), reads 0 where
  scrimmage isn't running, `AutoCloseable`.
  - `getDistanceMeters()`: the written distance floored to whole millimeters (Phoenix keeps whole millimeters: PR #43
    observed 0.24996 written, 0.249 read).
  - `getIsDetected()`: Phoenix-style proximity with hysteresis. **Call F1:** defaults are Phoenix's: threshold 0.4 m,
    hysteresis 0.01 m, and the latch observed with phoenix6 26.3.0 (PR #43 final review): it starts detected (distance
    0 at boot), turns off when the distance goes above threshold + hysteresis, turns on when it goes below
    threshold - hysteresis. *Cost:* if Phoenix's exact edge differs (on/off at the threshold itself), a reading within
    1 cm of the threshold differs between the modes; docs say so.
  - `setProximityThreshold(meters)`, `setProximityHysteresis(meters)`: so the IO class copies its
    `CANrangeConfiguration.ProximityParams` (the sample-style `ScrimmageConstants` pattern).
- **Binding (Call F2):** a `kind = "distance"` sensor takes `scrimmage = "<name>"` (any non-empty name without spaces,
  as fast-mode motor names), a third device key beside `ctre` and `grapple` (at most one). `device_key` is
  `("ScrimmageCANrange", "<name>")`, written `{">distance": meters}`; default `max_range` and `threshold` as a CANrange
  (3.0 m; 0.4 m capped at max_range). As with motors, the fast-mode config and the Phoenix config differ (docs/fast-mode.md
  "Write two Scrimmage configs"). *Cost:* none beyond the existing two-config rule.
- A bound `scrimmage` sensor the robot never creates gets the existing missing-device warning (naming
  `ScrimmageCANrange "<name>"` and that the robot builds it in its fast-mode IO); duplicates are an error, as for the
  other device keys. The device check lists it like the other fast-mode devices.

## Tests
- Java library: DevicesTest-style tests: the SimDevice name and input; distance floored to mm; the latch (starts on;
  off above 0.41; stays off at 0.40; on below 0.39); custom threshold/hysteresis; duplicate names throw; reads 0 with
  no device.
- Python: the spec key (valid, empty, with spaces, beside ctre is an error, duplicate), `device_key`, the runtime write,
  the missing warning.
- Docs: docs/fast-mode.md's library table and bindings; docs/sensors.md's CAN distance section and its "fast mode"
  line (now supported); the java_fastmode sample is not changed (it has no CANrange).
