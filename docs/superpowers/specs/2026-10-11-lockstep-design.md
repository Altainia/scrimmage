# Lockstep stepping: design

Status: approved in discussion 2026-10-11. This spec is waiting for review.

## Context

Scrimmage's goal is to let an FRC student put a robot together from models made by teammates and test
the robot code before the physical robot exists. Agents should be able to run and check those
simulations too. That work is split into four sub-projects, each with its own spec:

1. **Lockstep stepping** (this document)
2. An agent-facing layer to run and verify simulations, with screenshot capture
3. A part format and an assembler API that generates the MJCF model and binding config
4. An interactive GUI assembler built on top of 3

Today the bridge runs physics on the wall clock, and the robot program runs on its own timer. The
HALSim WebSocket protocol has no way to sync the two clocks. As a result, runs can't be reproduced
exactly and can't go faster than real time. Lockstep fixes both, and the agent layer depends on it.

## Goals

- Physics and the robot program take turns at a fixed step, with Scrimmage in control of the clock.
- Robot code stays **unmodified**. Students only load one more HALSim extension, next to the
  WebSocket client they already load.
- Runs can go faster than real time (`--speed max`) or be held to a multiple of real time.
- Two runs with the same config, DS script and robot program produce the same CSV log.
- Setups without the extension keep working in today's real-time mode.

## Non-goals (this sub-project)

- Windows and macOS builds. The first version is Linux only. Other platforms fall back to real time.
- WPILib years before 2026.
- The agent API, scenario assertions and screenshots (sub-project 2).
- Enforcing brownout.

## Design

### The `scrimmage_lockstep` HALSim extension

A small C++ shared library, `libhalsim_scrimmage_lockstep.so`, exporting `HALSIM_InitExtension`. It
calls only the HAL's C API (`HAL_CreateSimDevice`, `HAL_CreateSimValue*`, the SimDevice value
callbacks, `HALSIM_PauseTiming`, `HALSIM_ResumeTiming`, `HALSIM_StepTiming`). That way the same
binary works with RobotPy's and GradleRIO's builds of the same WPILib year, without depending on
the C++ ABI.

It creates a SimDevice named `ScrimmageLockstep`. `halsim_ws_client` relays every SimDevice, so this
one travels over the existing WebSocket connection. It needs no extra port or configuration.

The ws client relays a SimDevice whose name has no `Type:` prefix as
`{"type": "SimDevice", "device": "ScrimmageLockstep"}` (the spike confirms this). In the protocol, `>`
marks values going into robot code (written by Scrimmage) and `<` marks values coming out of it.

| Value | Type | Direction | Meaning |
|---|---|---|---|
| `>engage` | bool | Scrimmage → robot | `true` asks the extension to take its paused clock under Scrimmage's control |
| `>step_us` | long | Scrimmage → robot | microseconds to advance on the next request (whole µs, so no float rounding) |
| `>step_seq` | long | Scrimmage → robot | a new value requests one step |
| `<engaged` | bool | robot → Scrimmage | the extension is paused and accepting steps |
| `<done_seq` | long | robot → Scrimmage | set to `step_seq` once that step's notifiers have run |

**Loading:**

- **RobotPy:** a pip package installed from `lockstep/` provides an entry point in RobotPy's `robotpy_sim.2026` group.
  Run with `python -m robotpy sim --ws-client --scrimmage-lockstep`.
- **Java/C++:** a `HALSIM_EXTENSIONS` entry in `build.gradle` pointing at the same `.so`, next to the
  existing WebSocket-client lines. The exact Gradle lines are worked out during implementation.

**Behavior:**

- The extension **pauses timing as soon as it loads**, before the robot's first loop. The robot
  clock then starts from the same point relative to the step grid on every run, which makes runs
  repeatable. Starting unpaused would let a varying amount of real time pass before Scrimmage takes
  over.
- **Grace period:** if Scrimmage hasn't engaged within 5 s of loading
  (`SCRIMMAGE_LOCKSTEP_GRACE_S` overrides this), the extension resumes real-time timing. A student who
  loads it without running Scrimmage gets a working robot after a 5 s pause, and it says so on
  stderr.
- When `>engage` becomes `true`, it sets `<engaged = true` once the robot program has started
  (`HALSIM_GetProgramStarted`). Timing is already paused. Until the program starts, engage and step
  requests are held. If it hasn't started 30 s after the engage request, the extension resumes
  real-time timing and says why.
- When `>engage` becomes `false` while engaged or waiting to engage, the extension resumes real-time
  timing at once. Scrimmage sends it when it exits.
- Each new `>step_seq` is handed to a dedicated worker thread, never the WebSocket client's event
  loop thread. The worker calls `HALSIM_StepTiming(step_us)`, which advances the clock and waits for
  due notifiers (including TimedRobot's loop) to finish. It then sets `<done_seq`.
- **Watchdog:** if engaged and no step request arrives for 2 s after the last `<done_seq`, the
  extension resumes real-time timing and sets `<engaged = false`. A crashed or disconnected
  Scrimmage therefore never leaves the robot program frozen.
- A step request that arrives while not engaged (after the watchdog fired, or a Scrimmage that
  reconnected) pauses timing again, engages, and steps. Scrimmage always gets its step.
- The extension lives as long as the robot process, so `<done_seq` keeps counting across WebSocket
  reconnects. Scrimmage continues from the `<done_seq` it sees, not from 1.

**Ordering:** motor outputs written during a step and the `<done_seq` that follows go out in order
on one WebSocket connection. Scrimmage applies inbound messages in order, so once it sees
`done_seq >= n`, every output from step n has already been applied to its `HalState`.

### Step cycle

1. The robot program connects. The `ScrimmageLockstep` device appears in `HalState`.
2. Scrimmage writes `>engage = true` and waits for `<engaged = true`. It then resets the world
   (simulation time 0) and continues from the robot's current `<done_seq`.
3. Each cycle:
   1. Scrimmage steps physics by `dt`, using the latest motor outputs.
   2. It runs the per-tick extras (DS script, log, viewer) and sends the sensor updates.
   3. It writes `>step_us` and `>step_seq = n`.
   4. The extension steps WPILib time by `dt` and sets `<done_seq = n`.
   5. Scrimmage waits for `done_seq >= n`, then the next cycle starts.

The default `dt` is **5 ms**. `HALSIM_StepTiming` fires each due notifier at most once per step, so a
step longer than a loop period would drop runs of faster `addPeriodic` callbacks. 5 ms covers the
common 5–10 ms periodic loops for four round trips per 20 ms robot loop. `dt` must be a whole
multiple of the physics timestep. It can be set as `[lockstep] step = 0.005` in the TOML or with
`--step` on the command line.

### Bridge changes

`Bridge._physics` currently mixes three jobs: timekeeping, stepping, and the per-tick extras. It is
split into:

- **`RealtimeDriver`:** today's wall-clock catch-up loop, including `MAX_LAG` skipping.
- **`LockstepDriver`:** the step cycle above.
- **`on_tick(sim_time)`:** the shared extras: sensor pushes, the periodic full refresh, DS script
  frames, CSV logging, NetworkTables and viewer sync. It runs on **simulation time** in both modes.

Other changes:

- The DS script switches from wall time since connection to simulation time since connection. This
  is needed for repeatable runs, and it makes no difference in real-time mode.
- Viewer sync is limited to 60 Hz of **wall** time, so `--speed max` isn't slowed by rendering.
- The bridge starts in `RealtimeDriver`. It switches to `LockstepDriver` when the `ScrimmageLockstep`
  device appears, and back when the robot program disconnects.
- If no device appears within 2 s of connecting, it logs once: "lockstep extension not loaded;
  running in real time".

`World` doesn't change.

### Pacing

`--speed` takes a positive factor (default `1.0`, real time) or `max`.

- In lockstep mode, the driver sleeps as needed so that simulation time doesn't run ahead of wall
  time × factor. With `max` it never sleeps. Pacing doesn't change the results, only how long they
  take.
- In real-time mode, only `1.0` is valid. Any other value is a startup error saying the lockstep
  extension isn't active. Because the mode is only known once the robot connects, this check happens
  when the 2 s detection window closes. The bridge then exits with a clear message.

### Failure handling

| Situation | Behavior |
|---|---|
| Robot program stalls mid-step (breakpoint, slow code) | Scrimmage keeps waiting. After 2 s and every 10 s after that, it logs "waiting on robot program (step n)". |
| Robot program disconnects | Same as today: drop the connection state, idle, and reset on the next connect. |
| Scrimmage dies while engaged | The extension's 2 s watchdog resumes real time. |
| Extension loaded, Scrimmage never runs | The robot is paused for the 5 s grace period, then runs in real time. |
| Extension not loaded | Real-time mode, with one log line. `--speed` other than `1.0` is an error. |

### Repeatability

With the same config, DS script and robot program on the same machine, two lockstep runs produce
identical CSV logs. Physics and robot loops alternate in a fixed order, and MuJoCo is deterministic
for identical inputs. Robot code that reads the wall clock directly, or does timing-dependent work on
its own threads, can still vary. The README will say so. The `odo_*` columns are sampled from
NetworkTables, which isn't synchronized with the step clock, so they can differ between runs. Every
other column repeats exactly.

## Layout

```
lockstep/
  CMakeLists.txt
  pyproject.toml            # scikit-build-core, robotpy_sim.2026 entry point
  src/scrimmage_lockstep.cpp
  scrimmage_lockstep/__init__.py
scrimmage/
  bridge.py                 # split into drivers + on_tick
  lockstep.py               # LockstepDriver and device detection (new)
```

The extension builds against the HAL headers and `libwpiHal.so` that `robotpy-native-wpihal` installs.

## Testing

1. **Spike first.** A minimal extension that creates a SimDevice and echoes a value. Confirm that
   `halsim_ws_client` relays a device created by another extension, both ways, and that outputs set
   during a step arrive before `<done_seq`. The design depends on both. If either fails, stop and
   revisit the approach.
2. **Unit tests**, with no native build: `LockstepDriver` against a fake Python peer that plays the
   `ScrimmageLockstep` device over a real WebSocket. They cover:
   - step order
   - the waiting log after 2 s
   - disconnect and reset
   - fallback when no device appears
   - pacing at speed `1.0` vs `max`
   - rejecting `--speed` in real-time mode.
3. **End to end**, with CI building the extension on Ubuntu:
   - The kitbot autonomous run at `--speed max` meets the existing pose tolerances.
   - It finishes in less wall time than simulated time.
   - Two runs produce identical CSV logs.
   - The existing real-time e2e tests pass unchanged.
4. **Java/Gradle:** the loading instructions get tested if a JDK and GradleRIO are available in the
   development environment. Otherwise they're marked untested, as the current Java path is.

## Risks

- **`halsim_ws_client` may not relay the device as expected, or may not keep messages in order.**
  The spike checks this before anything else is built.
- **`HALSIM_StepTiming` semantics.** If it doesn't wait for notifiers in this WPILib build, the
  extension would need to wait itself (for example via `HALSIM_WaitForProgramStart` /
  notifier-state polling). The spike checks this too.
- **ABI between RobotPy and GradleRIO HAL builds.** Using only the C API reduces the risk, but it
  isn't verified for Java unless a toolchain is available.
- **Round-trip overhead** at 5 ms steps limits `max` speed. It is measured in the e2e test. If it's
  too slow, the default `dt` can be raised to 20 ms for robots without fast periodic loops.
