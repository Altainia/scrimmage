# Real Driver Station Design (sub-project 28)

**Status:** the owner delegated design questions (decide-and-document, 2026-10-28) and asked for this on 2026-11-04
("I also want to add working with a real Driver Station at some point"). Written 2026-11-04.

## Why

A team drives its real robot with the FRC Driver Station app (Windows-only, from NI's FRC Game Tools) and a
gamepad. Practicing with the same app and the same gamepad, with its real Enable/Disable, mode buttons, Practice
match timer, Game Data box and E-stop, is closer to the real thing than the Drive tab. Today scrimmage is always the
Driver Station (or the WPILib sim GUI is, under `scrimmage serve`).

## What a spike showed (2026-11-04, RobotPy 2026.2.2)

- **The extension:** `robotpy sim --ds-socket` loads WPILib's HALSim DS socket extension. It listens on
  `0.0.0.0` UDP 1110 (DS to robot) and TCP 1740 (game data, match info, joystick descriptors), and answers the DS on
  UDP 1150.
- **What works unchanged:** under `scrimmage serve` (real time, scrimmage sending no DS frames), a fake DS on Linux
  sent UDP and TCP packets. The robot then read enabled teleop, red2, FMS attached and game data "B".
- **What comes back:** through halsim_ws the robot program reports the DS state it got: `>enabled`,
  `>autonomous`, `>test`, `>estop`, `>fms`, `>ds`, `>station` and `>match_time` on `("DriverStation", "")`, and its
  six `Joystick` devices. So scrimmage can show what the real DS is doing.
- **Java:** GradleRIO projects have `wpi.sim.addDriverstation()`. scrimmage's Gradle init script
  (scrimmage/java/scrimmage.init.gradle) currently turns every extension off except the WebSocket client.

## Goals

1. **One switch** makes the real FRC Driver Station drive the robot:
   - in the Drive tab, before Start;
   - `scrimmage serve --real-ds`;
   - MCP `start_session(real_ds=True)`;
   - a config default, `[driver_station] real = true`.
2. **Loading the extension:**
   - **RobotPy:** with the switch on, scrimmage adds `--ds-socket` to a RobotPy command that lacks it.
   - **Java:** the init script also enables the project's DS socket extension, adding it if the project has none.
   - **Other commands:** if the robot program still doesn't listen on UDP 1110 within its start timeout,
     scrimmage says so in plain words.
3. **scrimmage steps back:** it sends no DS frames (no mode, joysticks or match data), and the session runs in real
   time and can't be paused, as a Phoenix 6 session does.
4. **scrimmage follows the real DS:**
   - **What it shows:** mode, enabled, E-stop, station, FMS and match time, read from the robot's own
     DriverStation reports.
   - **Where:** the Drive tab's station bar (read-only: "The FRC Driver Station is driving"), the HUD, `get_state`
     and the CSV log.
   - **Before any connection:** until a DS has connected, the state says "waiting for the Driver Station".
5. **Clear refusals:**
   - **MCP:** `set_mode`, `set_joystick` and `set_match` refuse with "the FRC Driver Station is driving".
   - **The Drive tab:** its mode, enable, joystick and match controls, and Record, are off.
   - **Scenario runs (`scrimmage run`, `run_scenario`):** they refuse the switch, because scenarios are the Driver
     Station there.
6. **Tests on Linux, without the Windows app:**
   - A fake DS test helper sends the real UDP and TCP packets: control byte, station, the game data tag and
     joystick tags. It follows WPILib's `DSCommPacket` (allwpilib simulation/halsim_ds_socket).
   - RobotPy and Java end-to-end tests drive a robot with it, check what scrimmage shows, and check that scrimmage
     sends no DS frames.
7. **Docs** (`docs/driver-station.md`) cover:
   - **Same Windows PC:** install FRC Game Tools, pick "Real Driver Station" in the Drive tab, press Start, and in
     the DS set Team Number to `127.0.0.1`. Try `localhost` if that doesn't connect. Mark these as to be confirmed
     by the owner.
   - **DS on another computer** (the sim on Linux or macOS): use the sim computer's IP address as the Team Number,
     and let UDP 1110/1150 and TCP 1740 through the firewall.
   - **Safety and controls:** Disable is the DS's Enter key, and E-stop is its Space bar.
   - **What doesn't work:** recording, scenarios and pausing.

## Non-goals

- **Speaking the DS protocol from scrimmage itself** (scrimmage pretending to be the robot). The robot program's
  own extension does that.
- **Recording a real-DS session as a scenario.** A later sub-project can map the robot's Joystick reports to
  tracks.
- **The roboRIO's other DS links** (the DS's diagnostics, CAN status, and the robot's battery voltage as shown by
  the DS). The DS shows what the extension sends.

## Constraints

- **Off by default:** with the switch off, nothing changes. Frames, the commands scrimmage launches, and the init
  script's behavior are byte-identical.
- **Wording and tests:** American spellings and plain words, for high-school students. Tests use
  `.venv/bin/python -m pytest`, never `uv run` in the repo root.
- **The ports are fixed by the DS protocol** (UDP 1110 and 1150, TCP 1740). Only one robot program can listen on
  them per machine. A second real-DS session fails with a message naming the port.

## Left to the builder

- Where the real-DS flag lives in the session and bridge (likely next to `ds.active`), and how the Drive tab lays
  out the choice before Start.
- How the "listening on 1110" check is done (a bind attempt or the OS socket list).

## Decisions (from the builder check)

Each decision gives the choice, the reason, and what it costs if it is wrong.

- **D1. `serve --real-ds`:**
  - **With `--launch`:** scrimmage adds the extension (Goal 2).
  - **Without `--launch`:** scrimmage starts no program, so it prints the one line to add, `--ds-socket` for RobotPy
    or the Gradle `wpi.sim.addDriverstation()` for Java.
  - **Either way:** it checks UDP 1110 and shows the real DS's state.
  - **Cost:** a hand-started program without the extension waits until the check's note.
- **D2. Lockstep is released when a real-DS session connects,** on purpose, the way Phoenix 6 sessions release it.
  - **Why:** the extension must answer the DS in wall time, and keeping the extension loaded keeps one launch path.
  - **Cost:** runs aren't repeatable, which is already true with a human at the DS.
- **D3. Which setting wins:**
  - **Explicit settings:** `--real-ds` on `run`, and `real_ds=True` on `run_scenario`, are refused.
  - **A config's `[driver_station] real = true`:** ignored by scenario runs, with a note.
  - **The other choices:** MCP `start_session(real_ds=None)` takes the config's default, and True or False
    overrides it. The Drive tab's choice starts from the config's default.
- **D4. `[match]` and the real DS:**
  - **`[match]`:** a real-DS session ignores it, because the DS sends the station, FMS, game data and the match
    time from its own Practice timer.
  - **The Drive tab's Match section:** read-only, showing the station, FMS and match time from the robot's
    reports. It shows game data only if the robot reports `>game_data`; the e2e test says whether it does, and
    the docs follow.
  - **`get_state()["match"]`:** shows the same reports.
- **D5. When the DS counts as connected:** "waiting for the Driver Station" whenever the robot's latest report
  doesn't have `>ds` true. That covers before any connection, and after the DS stops if the extension then clears
  `>ds`. The e2e test checks a stop, and the docs say what happens.
  - **Cost:** if the extension never clears `>ds`, a stopped DS shows its last state, and the docs say so.
- **D6. Display only:** scrimmage never acts on the real DS's state. Its physics and scoring don't depend on the mode.
- **D7. The choice is fixed per session.** It is made before Start; to change it, stop and start again.
