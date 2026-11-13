# Vendor distance sensors: checks for the design's open facts

Date: 2026-11-13. Linux x86-64, Python 3.11.17 and 3.13.16 (uv-made scratch venvs, not the repo's), WPILib 2026 JDK.
Every value below is either **observed** (what I ran, with versions) or **cited** (a public source, with URL and
location). Guesses are marked *guess*. Nothing was run on Windows or macOS.

Summary of rulings (Call V8) where a check went against the design:

| item | design said | found | ruling |
|---|---|---|---|
| CANrange max range | 4.0 m (estimate) | 3 m long range, 1 m short range | `CANRANGE_MAX_RANGE` = 3.0 |
| LaserCAN default mode | helper reports `is_long` true | firmware default is Short (1.3 m) | helper defaults to SHORT |
| `libgrapplefrc-py` | "not on PyPI under that name" | on PyPI under that name | install from PyPI |
| LaserCAN no-target status | "read from sources" | no source gives one value | `LASERCAN_STATUS_NO_TARGET` = 2 (documented meaning; not seen on hardware) |
| RobotPy LaserCAN | patch `get_measurement` | patchable on the class, but the real class's constructor segfaults in plain Python | replace the module's class (see section 4) |

## 1. Constants

```
CANRANGE_MAX_RANGE          = 3.0   # m, long range mode (short range mode: 1.0 m)
LASERCAN_LONG_REACH         = 4.0   # m
LASERCAN_SHORT_REACH        = 1.3   # m
LASERCAN_STATUS_VALID       = 0
LASERCAN_STATUS_NO_TARGET   = 2     # WEAK_SIGNAL; a documented-meaning choice, not observed on hardware
```

### CANrange maximum range: 3 m (long range mode), 1 m (short range mode)

- Source: CTR Electronics, "CANrange User's Guide", dated 11/25/2024, section 1.3 "Electrical Specifications" table,
  https://ctre.download/files/user-manual/CANrange%20User's%20Guide.pdf (downloaded 2026-11-13, 10 pages; text read
  with `pdftotext`). Rows: "Detection Distance, Short range mode, 0 to 1 m"; "Long range mode, 0 to 3 m". Feature
  list (section 1.2): "3M detectable distance in long range mode". "Field of View 6.75 min, 27 typ, 27 max deg".
  Sensor update rate "100 Hz short range mode: 10 ms; user-configured 20 to 200 ms".
- The CTRE web docs page
  (https://v6.docs.ctr-electronics.com/en/stable/docs/hardware-reference/canrange/index.html) does not state a
  range; it links to that guide. The store page returned HTTP 429 and was not read.
- Contradicts the design's 4.0 m estimate (Call V2). Ruling: the default `max_range` for a `ctre = "CANrange:..."`
  sensor is 3.0 m (the long-range maximum). Cost: a robot that sets the CANrange to short range (1 m) sees up to 3 m
  here until the student sets `max_range = 1.0`.
- Phoenix 6 (Python 26.3.0) has `UpdateModeValue` ("short-range and long-range detection at various update
  frequencies"), `ProximityParamsConfigs`, `FovParamsConfigs`, `ToFParamsConfigs`. I did not read the update-mode
  values back through the SimDevice (the SimDevice has no such value; see section 2), so the mode is not visible to
  scrimmage. *Guess:* honoring it would need reading the config, which is not in this task.

### LaserCAN reaches: long 4 m, short 1.3 m

- Source: GrappleRobotics/libgrapplefrc, `src/main/java/au/grapplerobotics/interfaces/LaserCanInterface.java`
  (`RangingMode` enum javadoc; repo HEAD 36fd307, 2026-01-10 "Update to 2026.0.0"):
  LONG "... identify targets at longer distances than the short ranging mode (up to 4m)"; SHORT "... detect targets
  at 1.3m and lower".
  https://github.com/GrappleRobotics/libgrapplefrc/blob/36fd3070c0fe5f36b18b00fc866e8dec34c44afe/src/main/java/au/grapplerobotics/interfaces/LaserCanInterface.java
  The `LaserCan` class javadoc also says "a 0-4m laser ranging sensor".
- **Default mode** (contradicts the design's `is_long` true default): the firmware's default config is Short.
  GrappleRobotics/LaserCAN `lasercan-impl/src/lib.rs` line 39 (`ranging_mode: LaserCanRangingMode::Short`; repo HEAD
  5f444a3), ROI default x=8,y=8,w=16,h=16 (line 40), timing budget 33 ms (line 41). A robot that calls
  `setRangingMode` changes it; GrappleHook can change it persistently on a real unit (not checked). Ruling: the helper
  starts in SHORT and sets `is_long` from `setRangingMode`. Cost: a long-mode sensor configured only in GrappleHook
  (not in code) reads "no target" past 1.3 m here until the student calls `setRangingMode(LONG)` or sets `max_range`.
- The `MockLaserCan` default is `is_long` false, budget 20 ms, ROI (0,0,16,16) (same repo, `simulation/MockLaserCan.java`);
  consistent with short being the baseline.

### LaserCAN statuses

- Source: the same `LaserCanInterface.java` (and C++ `src/main/native/include/grpl/LaserCan.h`, lines 13-42):
  `LASERCAN_STATUS_VALID_MEASUREMENT = 0`, `NOISE_ISSUE = 1`, `WEAK_SIGNAL = 2`, `OUT_OF_BOUNDS = 4`, `WRAPAROUND = 7`.
- These equal the firmware's status codes: GrappleRobotics/LaserCAN `lasercan-firmware/src/main.rs` line 80 sends
  `result.status as u8` from the `vl53l1x-uld` 2.0.0 crate's `RangeStatus` enum (crates.io, `src/lib.rs` lines
  167-200: Valid=0, SigmaFailure=1, SignalFailure=2, OutOfBounds=4, Wraparound=7, ...).
- **No-target status.** No Grapple source says which status the sensor reports with nothing in range. The documented
  meanings are: WEAK_SIGNAL "usually means the target is too far away, not reflective enough, or too small";
  OUT_OF_BOUNDS "an object on the limits of its range ... usually only applies to bright targets". The Grapple example
  (`LaserCAN/docs/example-java.md`) treats any non-zero status as "the target is out of range, or we can't get a
  reliable measurement" and says `distance_mm` may be "a clamped value". I never saw a real LaserCAN.
  Ruling (a choice, *guess* about hardware): `LASERCAN_STATUS_NO_TARGET = 2` (WEAK_SIGNAL), with `distance_mm` =
  the mode's reach in mm. Cost: robot code that tests `status == 4` for "out of range" sees "valid-looking absence"
  differently from hardware; code that tests `status != 0` (Grapple's own example) behaves the same.
- Also from the library: `Measurement` fields (Java) are `status, distance_mm, ambient, is_long, budget_ms, roi`;
  the real native getter returns null until the first frame and again when the last frame is older than 500 ms
  (`grapplefrcdriver/src/lasercan.rs` lines 27-48).

## 2. RobotPy `phoenix6` registers the same CANrange SimDevice: YES (observed)

- `pip install robotpy phoenix6` (uv, PyPI) into scratch venvs on Python 3.11.17 and 3.13.16: installed
  robotpy/wpilib 2026.2.2, **phoenix6 26.3.0** (the Java probe used Phoenix 6 26.1.2; the SimDevice is the same name in both).
- `phoenix6.hardware.CANrange(5)` then `wpilib.simulation.SimDeviceSim.enumerateDevices("")` lists exactly one device,
  `'CAN:CANrange (v6)[5]'`, with values `supplyVoltage`, `distance`, `isDetected` (isDetected flagged as the
  read-only one), on both Pythons. Standalone script, no `robotpy sim`.
- Writing `SimDeviceSim("CAN:CANrange (v6)[5]").getDouble("distance").set(1.234)` then
  `r.get_distance().wait_for_update(1.0).value` read back 1.234, on both Pythons. `get_is_detected()` read False
  before and after (default threshold; I did not set one). In the earlier Java probe `getIsDetected()` was true with
  nothing written; I did not investigate the difference (the Java run had no CAN frame yet vs a standalone Python
  script; *guess*, unchecked).
- Phoenix 6 prints "CAN frame not received/too-stale" warnings in this standalone run; harmless.

## 3. `libgrapplefrc-py` installs on 3.11 and 3.13: YES on Linux (observed), Windows wheel resolves (not run)

- It **is on PyPI** under that name (design said it was not): `pip install libgrapplefrc-py` from the default index
  (https://pypi.org/project/libgrapplefrc-py/), version 2026.0.0, `requires_python >=3.10`. Files on PyPI:
  `cp38-abi3` wheels for `manylinux_2_34_x86_64`, `manylinux_2_34_aarch64`, `win_amd64`, `macosx_11_0_arm64`, and a
  roboRIO `linux_armv6l`. Because they are abi3, the same wheel serves 3.11 and 3.13.
  Source of publishing: libgrapplefrc `.github/workflows/ci.yml` "Publish Python (desktop)" step (twine to PyPI).
  Package repo: `libgrapplefrc-py/` in https://github.com/GrappleRobotics/libgrapplefrc (maturin/pyo3, pyproject.toml).
- Installed and imported (`import libgrapplefrc`) on Linux x86-64, Python **3.11.17** and **3.13.16**: observed.
- `pip download libgrapplefrc-py --no-deps --only-binary=:all: --platform win_amd64 --python-version 3.11` and `3.13`
  both resolved `libgrapplefrc_py-2026.0.0-cp38-abi3-win_amd64.whl` (and `manylinux_2_34_x86_64` likewise). That
  shows pip finds a Windows wheel; **I did not install or run it** (no Windows here). That wheel contains only
  `libgrapplefrc.pyd` and does not bundle a HAL (listed its files). *Guess:* on Windows it binds the `wpiHal.dll`
  that robotpy's hal has already loaded, so it may behave differently from the Linux wheel (section 4); the owner's
  Windows run decides.
- The manylinux_2_34 tag needs glibc 2.34+; this machine has 2.43. CI's ubuntu runners (glibc 2.35+, *guess*) should be fine.

## 4. The Python LaserCAN class: patching, and a construction problem (observed, Python 3.11.17 and 3.13.16, same results)

- The module exposes `LaserCAN`, `LaserCanMeasurement`, `LaserCanRangingMode` (`Long`, `Short`), `LaserCanRoi`
  (`x,y,w,h`), `LaserCanTimingBudget` (`TB20ms/TB33ms/TB50ms/TB100ms`), `MitoCANdria`. `LaserCAN` has
  `get_measurement`, `set_range`, `set_roi`, `set_timing_budget`. Python names differ from Java: the measurement has
  `status, distance_mm, ambient, mode, budget, roi` (no `is_long`, no `budget_ms`), and `set_range` is the mode setter.
- `LaserCanMeasurement` has **no constructor** ("No constructor defined"), so a patched `get_measurement` must return a
  duck-typed object with those six attributes.
- **Replace `get_measurement` on the class: works.** `LaserCAN.get_measurement = lambda self: X` took effect for
  existing and new instances (both returned the fake's 123).
- Replace on an instance: fails (`AttributeError: ... attribute 'get_measurement' is read-only`).
  Subclassing: fails (`TypeError: type 'builtins.LaserCAN' is not an acceptable base type`).
- **Constructing the real class segfaults in plain Python.** `libgrapplefrc.LaserCAN(7)` crashes the interpreter
  (Segmentation fault, with `-X faulthandler` it dies at the constructor line), even after `hal.initialize(500, 0)` on
  robotpy's HAL. Cause (from `ldd`/`nm`, observed): the Linux wheel bundles its own private copy of the WPILib HAL
  (`libgrapplefrc.libs/libwpiHal-*.so`) that is never initialised; the constructor calls `HAL_CAN_OpenStreamSession`
  on it. Calling that bundled copy's `HAL_Initialize(500, 0)` through ctypes first makes construction succeed, and
  then the real `get_measurement()` returned None (no frames), as expected. The cause is the inference from `ldd`;
  the ctypes workaround is observed.
- So the real class cannot be built on desktop Linux by robot code as is; a student's unchanged `LaserCAN(7)` would
  crash a RobotPy simulation with or without scrimmage. Two ways for the lockstep helper (both observed to work):
  (a) set `libgrapplefrc.LaserCAN` (module attribute) to a pure-Python stand-in class before robot code imports it:
  `from libgrapplefrc import LaserCAN` then returned the stand-in; or (b) initialise the bundled HAL via ctypes, then
  patch `get_measurement` on the class. Ruling (V8, cost): choose (a). Its cost: it only works when the helper runs
  before the robot's import (the lockstep helper does), `isinstance(x, libgrapplefrc.LaserCAN)` checks against the
  original class by robot code would fail (unlikely), and `set_range`/`set_roi`/`set_timing_budget` must be
  re-implemented in the stand-in (record the values; return a success result, whose type I did not examine).
  Class patching is not enough on its own, and RobotPy LaserCAN stays supported.

## 5. The Java hook returns a value: YES (observed)

- Probe: a copy of the earlier throwaway robot (WPILib 2026 GradleRIO 2026.2.1, JDK `~/wpilib/2026/jdk`, vendordeps
  Phoenix6 26.1.2, PlayingWithFusion, libgrapplefrc 2026.0.0 from
  https://storage.googleapis.com/grapple-frc-maven/libgrapplefrc2026.json), `simulateJava`, GUI off.
  Not committed; it lives in the scratchpad.
- A throwaway `-javaagent` (ASM 9.7, passed via `JAVA_TOOL_OPTIONS`) rewrote the entry of
  `au.grapplerobotics.LaserCan.getMeasurement()`: call a static helper, and if it returns non-null, cast and return it,
  else fall through to the original body (the same shape as scrimmage's `SparkSim.iterate` guard). The helper
  returned `new Measurement(0, 123, 0, true, 33, null)`.
- Result: the robot printed `LaserCan m=123 status=0` on all 5 samples (ticks 1, 51, 101, 151, 201), and the agent
  printed `hooking LaserCan.getMeasurement()Lau/grapplerobotics/interfaces/LaserCanInterface$Measurement;`. Control
  run, same robot without the agent: `LaserCan m=null`. No VerifyError with `COMPUTE_MAXS` and the two hand-written
  frames (F_SAME1 with one Object on the stack at the skip label, then F_SAME after the POP).
- Setup note: the agent jar's manifest used `Class-Path: asm-9.7.jar` with the ASM jar next to it; I did not test
  any other packaging.

## 6. What this changes in the later tasks

- Task constants: the block at the top of section 1.
- CANrange: default `max_range` 3.0 (not 4.0); docs say the short-range mode (1 m) is not visible to scrimmage.
- LaserCAN Java: the helper starts in SHORT, `is_long` from `setRangingMode`; no-target = status 2 with
  `distance_mm` = the mode's reach in mm.
- LaserCAN RobotPy: supported, by replacing `libgrapplefrc.LaserCAN` with a Python stand-in; duck-typed measurement
  with `status, distance_mm, ambient, mode, budget, roi`; installed by `pip install libgrapplefrc-py` (PyPI). A
  RobotPy end-to-end test can run on Linux CI Pythons 3.11 and 3.13 (the wheel's abi3 tag; CI itself unverified);
  Windows is unverified.
- Not checked: Playing With Fusion (design Call V5), GrappleHook-persisted configuration, any real hardware.
