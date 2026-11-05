# Real Driver Station Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** one switch makes the real FRC Driver Station app drive a simulated robot. The robot program loads WPILib's
DS socket extension, scrimmage stops being the DS, runs in real time, and shows what the real DS is doing.

**Architecture:**
- **The flag:** `real_ds` lives on the Session and the Bridge.
  - The bridge sends no DS frames (`ds.active = False`).
  - It releases lockstep on connect, by the same path Phoenix 6 uses.
  - It reports the real DS's state from the robot's own `("DriverStation", "")` reports in `self.hal`.
- **The launch:** it adds `--ds-socket` (RobotPy) or sets `SCRIMMAGE_DS_SOCKET=1`, which the Gradle init script reads
  to enable the project's DS socket extension (Java).
- **Tests:** a fake DS test helper speaks the DS protocol on Linux.

**Tech Stack:** Python 3.11+, HALSim WebSocket and DS socket extensions (RobotPy 2026.2.2, GradleRIO 2026), the
Drive tab (vanilla JS), pytest.

**Spec:** docs/superpowers/specs/2026-11-04-real-driver-station-design.md (decisions D1–D7 are binding).

## Global Constraints

- **Off by default:** with the switch off, frames, launched commands and the init script's behavior are
  byte-identical to today's.
- **The ports** are UDP 1110 (DS to robot), UDP 1150 (robot to DS) and TCP 1740.
- **Wording:**
  - the refusal text is "the FRC Driver Station is driving";
  - the waiting text is "waiting for the Driver Station";
  - American spellings and plain words, for high-school students.
- **Tests:** never run `uv run` in the repo root. Use `.venv/bin/python -m pytest`, serially.

## Review Focus

1. **Port in use.** A second real-DS session, or anything already on UDP 1110, gives an error naming the port and
   what to stop, before the robot program starts. Test: Task 1.
2. **The DS stops sending.** It is killed mid-session: per D5 the state shows waiting again, or the last state if
   the extension never clears `>ds`. Either way the e2e test records which and the docs say so. Test: Task 1.
3. **A RobotPy command that already has `--ds-socket`** isn't given a second copy. A command that isn't RobotPy or
   Gradle is left alone, and gets the "not listening on UDP 1110" note after the start timeout. Test: Task 1.
4. **The config default under scenarios:** `[driver_station] real = true` runs a scenario normally with a note, and an
   explicit `real_ds=True` on `run_scenario` or `--real-ds` on `run` is refused (D3). Test: Task 1.
5. **The Drive tab during a real-DS session.** Mode, Enable, joystick, Match and Record controls are all off, and the
   bar shows the real DS's mode and enabled as they change. Test: Task 3.

---

### Task 1: The real-DS session (RobotPy), the fake DS, MCP and the config

**Files:**
- Create: `tests/fake_ds.py` (the fake DS helper), `tests/test_real_ds.py` (unit), `tests/test_real_ds_robot_e2e.py`
- Modify: `scrimmage/config.py` (`[driver_station] real`), `scrimmage/session.py`, `scrimmage/bridge.py`,
  `scrimmage/driverstation.py` (`real_ds_report(hal) -> dict`), `scrimmage/robotcode.py` (adding `--ds-socket`),
  `scrimmage/mcp_server.py` (`start_session(real_ds=None)` and the refusals), `scrimmage/scenario_runner.py` (D3)

**Interfaces (produced):**
- **`Config.real_ds: bool`:** `[driver_station] real` (default false). An unknown key in `[driver_station]` is a
  `ConfigError`.
- **`Session(..., real_ds: bool | None = None)`:** None takes the config's value.
  - With real_ds, the session:
    - checks that UDP 1110 and TCP 1740 are free by binding them briefly, before launch, and raises `SessionError`
      naming the port;
    - adds `--ds-socket` to a RobotPy command (detected as the existing `ROBOTPY_ARGS` style
      `-m robotpy sim`) unless it's already there;
    - sets `SCRIMMAGE_DS_SOCKET=1` in the robot's environment;
    - makes `bridge.ds.active = False`;
    - after the start timeout, checks that UDP 1110 is now in use (a failed bind), and otherwise adds the note "the
      robot program isn't listening for the Driver Station (UDP 1110): RobotPy needs --ds-socket; Java needs
      wpi.sim.addDriverstation()".
  - `Session.real_ds: bool` is the result.
- **`Bridge(real_ds: bool = False)`:**
  - **Lockstep:** released when the robot connects, via the same release path as `release_for_phoenix`
    (generalize it, e.g. `release_lockstep(reason)`). Messages say "real Driver Station", not Phoenix.
  - **Between calls:** the session runs in real time and keeps running, like a held Phoenix session. Generalize
    `Session._unpausable()` to cover real_ds.
  - **Mode:** the bridge's mode for logs is `real_ds_report(self.hal)["mode"]`.
- **`real_ds_report(hal) -> dict`:**
  - **Keys:** `connected` (the latest `>ds` is true), `mode` ("disabled" | "auto" | "teleop" | "test", from
    `>enabled`/`>autonomous`/`>test`), `estop`, `station`, `fms`, `match_time`, and `game_data` (only if the robot
    reports it).
  - **Not connected:** `mode` is "waiting".
- **`Session.state()`:** gains `"real_ds": bool`. With real_ds, `"mode"` comes from the report, `"match"` is the
  report, and `"driver_station"` is "waiting for the Driver Station" or "connected".
- **Refusals:** `Session.set_mode`, `set_joystick` and `set_match` raise `SessionError("... the FRC Driver Station is
  driving")` with real_ds. MCP passes it on.
- **Scenarios (D3):**
  - `run_scenario`/`run` with an explicit real-DS request is refused.
  - A config default is ignored with a report note. The session the runner starts uses `real_ds=False`.
- **The fake DS (`tests/fake_ds.py`), following allwpilib `simulation/halsim_ds_socket` `DSCommPacket`:**
  - `FakeDS(host="127.0.0.1")` with `.start()` / `.stop()`, which runs a thread sending UDP to 1110 every 20 ms;
  - `.set(enabled: bool, mode: str, station: str, fms: bool = False, estop: bool = False)`;
  - `.set_joystick(port, axes, buttons)` (the joystick tags in the UDP packet);
  - `.send_game_data(s)` (TCP 1740, tag 0x0E);
  - `.replies() -> int` (how many robot replies on UDP 1150 were received, to prove two-way talk).
  - **The packet:** a 2-byte sequence, version 0x01, a control byte (mode bits 0 teleop, 1 test, 2 auto; 0x04
    enabled; 0x08 FMS; 0x80 E-stop), a request byte of 0, and the station 0–5 (red1..blue3). Then tags as
    [size][id][data]. Read DSCommPacket for the joystick tag layout, and write it down in the helper's docstring.

- [ ] **Step 1: Write the failing unit tests:**
  - the config key, and an unknown key;
  - `real_ds_report` from canned hal dicts (waiting, teleop enabled, auto, test, estop);
  - adding `--ds-socket` once, and leaving a non-RobotPy command alone (Review Focus 3);
  - the port check with a socket the test holds on 1110 (Review Focus 1);
  - the refusals;
  - the scenario rules (Review Focus 4);
  - with the switch off, the launched command and env are identical to today's.
- [ ] **Step 2: Write the failing e2e test** (with `tests/match_probe_py` or a copy that also prints mode and
  joystick axis 0 once a second):
  - **The session:** a held real-DS session, and a FakeDS sending teleop enabled, red2, FMS, and axis 0 = 0.5, then
    game data "B".
  - **The robot prints** teleop, red, 2, FMS true, "B" and 0.5.
  - **`get_state`** shows real_ds, connected, mode teleop and station red2.
  - **The DS frames:** the bridge sent none; spy on `_send`.
  - **`replies() > 0`.**
  - **Then:** `set_mode("auto")` is refused, and after `FakeDS.stop()` and 1 s, the test records what
    `driver_station` shows (Review Focus 2). Assert the D5 rule and write the observed behavior into the report.
  - **If `>game_data` isn't reported back (D4):** assert that it's absent, and note it.
- [ ] **Step 3–4: Implement and run** the new tests plus `-k "session or bridge or lockstep or mcp or scenario or
  config or golden"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "real driver station: the robot listens for the FRC Driver Station; scrimmage
  follows it"`

### Task 2: Java and `scrimmage serve --real-ds`

**Files:**
- Modify: `scrimmage/java/scrimmage.init.gradle`, `scrimmage/__main__.py` (`serve --real-ds`, and `run --real-ds`
  refused)
- Test: `tests/test_java_e2e.py` (a real-DS case with FakeDS; tests/java_probe already has
  `wpi.sim.addDriverstation()`), `tests/test_java_tools.py` or the init-script tests (find them), and a CLI test
  file for serve

**Interfaces:**
- **Consumes:** Task 1's `Session(real_ds=)`, `SCRIMMAGE_DS_SOCKET`, `real_ds_report`, `tests/fake_ds.py` and
  `Bridge(real_ds=)`.
- **The init script:** when `SCRIMMAGE_DS_SOCKET=1`, it keeps the DS socket extension enabled alongside the
  WebSocket client, adding it with `sim.addDriverstation()` if the project has none (find GradleRIO's extension
  name). Without the variable it behaves as today.
- **`serve --real-ds` (D1):**
  - **With `--launch`:** as Task 1's session launch, plus the port check and the listening check.
  - **Without `--launch`:** it prints the line to add for RobotPy and for Java.
  - **Either way:** it logs the real DS's mode changes ("Driver Station: teleop enabled") from `real_ds_report`.
- **`run --real-ds`:** refused, with D3's message.
- [ ] **Step 1: Write the failing tests:**
  - the Java e2e: a FakeDS enables teleop, and the probe's console shows it, in the same style as the existing
    `PROBE_MODE=match` check; set the probe's mode as that test does;
  - the init script with and without the variable, through the existing init-script test pattern;
  - `serve --real-ds` without `--launch` prints both lines;
  - `run --real-ds` is refused.
- [ ] **Step 2–4: Implement and run** them plus `-k "java or serve or cli"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "real driver station: Java projects and scrimmage serve --real-ds"`

### Task 3: The Drive tab

**Files:**
- Modify: `scrimmage/assembler/live.py` (a `real_ds` start option; the commands mode/enable/disable/joystick/match
  refused with the refusal text; state carries `real_ds`, `driver_station` and the report),
  `static/drive.js`, `index.html`, `app.css` (the choice before Start; a read-only bar), and the recorder start path
  (Record off)
- Test: the Drive tab's live and browser test files (add cases)

**Interfaces:**
- **Consumes:** Task 1's `Session(real_ds=)` and `state()` fields.
- **Before Start:** a "Driver Station" choice, "Scrimmage (this page)" or "The FRC Driver Station app". It starts
  from the config's `[driver_station] real` and can't be changed while a session runs (D7).
- **During a real-DS session:**
  - **The bar:** shows "The FRC Driver Station is driving", the mode and enabled (or "waiting for the Driver
    Station"), the station and the match time.
  - **Controls:** Mode, Enable, Disable, the keyboard and gamepad feed, the Match section's inputs, Pause/Step and
    Record are off. Their tooltips say why.
  - **The page:** sends no joystick commands.
- [ ] **Step 1: Write the failing tests:**
  - **Live:** with `real_ds` the mode, joystick and match commands are refused with the text, and the state has
    `real_ds`.
  - **Browser (with a FakeDS and a RobotPy robot, or a stubbed state, whichever the existing browser tests can do;
    say which):** the controls are off, and the bar changes from waiting to teleop enabled (Review Focus 5).
  - **The choice:** it starts from the config.
- [ ] **Step 2–4: Implement and run** them plus `-k "drive or live or browser"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "drive tab: choose the FRC Driver Station app"`

### Task 4: Docs

**Files:**
- Create: `docs/driver-station.md`, `tests/test_driver_station_docs.py`
- Modify: `README.md` (the MCP tool list's `start_session` and the config keys), `docs/drive.md`, `docs/match.md` (a
  real DS ignores `[match]`), and the spec's decisions with anything ruled during the build
- [ ] **Step 1: Write docs/driver-station.md** for high-school students:
  - **What it is and why.**
  - **Same Windows PC:**
    - install FRC Game Tools;
    - choose the app in the Drive tab, or `serve --real-ds --launch`;
    - set the DS Team Number to `127.0.0.1`, or try `localhost`.
    Mark these "to be confirmed on a real DS" until the owner confirms them.
  - **A DS on another computer:** use the sim computer's IP, and the firewall ports.
  - **Your own robot program:** `--ds-socket`, or `wpi.sim.addDriverstation()`.
  - **Safety:** Enter disables and Space is E-stop.
  - **What scrimmage shows.** Say what happens when the DS stops, from Task 1's observed behavior.
  - **What's off:** recording, scenarios, pausing and `[match]`.
  - **Troubleshooting:** port in use, not listening, firewall.
- [ ] **Step 2:** The docs test checks that every switch (Drive tab choice, `--real-ds`, `real_ds`,
  `[driver_station] real`) and every port is documented. It also checks that the refusal and waiting texts match
  the code's constants.
- [ ] **Step 3:** Run the docs tests and the full suite once. Expected: PASS.
- [ ] **Step 4: Commit:** `git commit -m "docs: using the FRC Driver Station app"`
