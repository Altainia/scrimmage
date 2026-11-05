# Driving with the FRC Driver Station app

Your team drives its real robot with the FRC Driver Station app and a gamepad. Scrimmage can let that same app
drive the simulated robot: the same Enable and Disable, the same mode buttons, the Practice match timer, the Game
Data box, the E-stop and the same gamepad plugged into the same laptop. That is closer to a real match than driving
from the Drive tab, so it is good practice for the drive team.

Normally Scrimmage is the Driver Station: the Drive tab, a scenario or an agent tells the robot its mode and
joysticks. With the FRC Driver Station app, Scrimmage steps back. Your robot program listens for the app itself,
the way a real roboRIO does, and Scrimmage only shows what the app is doing.

The FRC Driver Station app runs only on Windows. It comes with NI's FRC Game Tools.

Contents: [Turning it on](#turning-it-on), [On the same Windows PC](#on-the-same-windows-pc),
[A Driver Station on another computer](#a-driver-station-on-another-computer),
[Starting the robot program yourself](#starting-the-robot-program-yourself), [Safety](#safety),
[What Scrimmage shows](#what-scrimmage-shows), [It runs in real time](#it-runs-in-real-time),
[What's off](#whats-off), [Troubleshooting](#troubleshooting).

## Turning it on

One choice turns it on. It is fixed for a session: to change it, stop and start again.

| Where | How |
|---|---|
| The Drive tab | The **Driver Station** choice above **Start**: **Scrimmage (this page)** or **The FRC Driver Station app**. Pick it before you press Start. The robot code panel's **Start and check devices** uses the same choice. |
| `scrimmage serve` | `scrimmage serve configs/kitbot.toml --real-ds --launch`. `--no-real-ds` turns it off. |
| Agents (MCP) | `start_session(config=..., real_ds=true)`. `real_ds=false` turns it off. |
| A robot config | `[driver_station] real = true` (below). |

```toml
[driver_station]
real = true          # the FRC Driver Station app drives this robot (default false: Scrimmage is the Driver Station)
```

The config's `[driver_station] real` is the default for the other three. The Drive tab's choice starts at it,
`serve` uses it unless you pass `--real-ds` or `--no-real-ds`, and `start_session` uses it unless you pass
`real_ds`. Leaving out `real_ds` is the same as passing `null`.

**When Scrimmage starts the robot program** (the Drive tab, `serve --launch`, or `start_session` without
`attach`), it tells the program to listen for the app:

- **RobotPy:** Scrimmage adds `--ds-socket` after `sim` in the `[robot_program]` command, if it isn't there.
- **Java:** Scrimmage's Gradle init script turns on GradleRIO's DS socket extension ("Use Real DriverStation", the
  one `wpi.sim.addDriverstation()` adds). It adds it if your `build.gradle` doesn't have it.

You don't change your `[robot_program]` command or your robot code. See
[Starting the robot program yourself](#starting-the-robot-program-yourself) for when you start it by hand.

## On the same Windows PC

**To be confirmed on a real Driver Station.** The steps below follow WPILib's simulation docs and have been tested
with a stand-in for the app, not with the app itself yet.

1. Install NI's **FRC Game Tools** (the FRC Driver Station app comes with it).
2. Start the robot: in the Drive tab, pick **The FRC Driver Station app** and press **Start**, or run
   `scrimmage serve configs/kitbot.toml --real-ds --launch`. The Drive tab's bar says "Waiting for the Driver
   Station".
3. Open the FRC Driver Station app. On its Setup tab (the gear), set **Team Number** to `127.0.0.1`. If it doesn't
   connect, try `localhost`.
4. The app's Communications light turns green, and the Drive tab's bar shows "Disabled". Pick a mode in the app and
   enable.

## A Driver Station on another computer

The simulation can run on one computer (Linux, macOS or Windows) and the app on a Windows laptop on the same
network. This is also **to be confirmed on a real Driver Station.**

1. Find the simulation computer's IP address (for example `192.168.1.20`). On Linux, `hostname -I` shows it; on
   macOS, System Settings > Network; on Windows, `ipconfig`.
2. In the app, set **Team Number** to that IP address.
3. Let the Driver Station's three ports through the firewall:

| Port | Direction | What it carries |
|---|---|---|
| UDP 1110 | Driver Station to robot | the mode, Enable, E-stop, station and joysticks, 50 times a second |
| UDP 1150 | robot to Driver Station | the robot's answers (the app's Communications light) |
| TCP 1740 | Driver Station to robot (the app connects) | the game data, the match info and the joystick names |

The simulation computer must accept UDP 1110 and TCP 1740, and the Driver Station laptop must accept UDP 1150. On
Linux with `ufw`, that is `sudo ufw allow 1110/udp` and `sudo ufw allow 1740/tcp`.

## Starting the robot program yourself

When you start the robot program by hand (`scrimmage serve` without `--launch`, or `start_session(attach=true)`),
Scrimmage can't add anything, so the program must listen for the app on its own.
`scrimmage serve --real-ds` without `--launch` prints the line to add:

- **RobotPy:** add `--ds-socket` after `sim`: `python -m robotpy sim --ds-socket --ws-client`.
- **Java:** add `wpi.sim.addDriverstation()` to `build.gradle`, next to `wpi.sim.addWebsocketsClient()`. Under Scrimmage's init script (`./gradlew -I "$(scrimmage java path)"
  simulateJava`), set `SCRIMMAGE_DS_SOCKET=1` instead:

  ```bash
  SCRIMMAGE_DS_SOCKET=1 ./gradlew -I "$(scrimmage java path)" simulateJava
  ```

  Without `SCRIMMAGE_DS_SOCKET=1`, the init script turns your project's own DS socket off, because Scrimmage is
  then the Driver Station. Scrimmage sets the variable itself when it starts the program.

If the program doesn't listen, Scrimmage says so (see [Troubleshooting](#troubleshooting)).

## Safety

In the FRC Driver Station app, **Enter disables** the robot and the **Space bar is the E-stop**. Keep a hand near
them, as you would with the real robot. The Drive tab's own keys do nothing while the app drives.

After an E-stop, the Drive tab's bar shows "Disabled (E-stopped)".

If the app stops sending (you close it, or the network drops), the robot program disables itself within about
0.1 s, as a real roboRIO does.

## What Scrimmage shows

Scrimmage reads what the app is doing from the robot program's own reports: the mode, whether it is enabled, the
E-stop, the station, whether the FMS is attached, and the match time. It never acts on them: the physics and the
scoring are the same whatever the mode.

- **The Drive tab:** a box at the top of the Driver Station column says "The FRC Driver Station is driving", with
  the mode ("Waiting for the Driver Station", "Teleop enabled", "Auto enabled", "Test enabled", "Disabled" or
  "Disabled (E-stopped)") and a line such as "Station red2 · FMS attached · match time 12.3 s". The Match section
  shows the station and FMS, read-only. Its **Match timer** box is checked whenever the app sends a match time. The
  HUD over the live view shows the mode, Enabled and the match time.
- **Agents:** `get_state` has `real_ds: true` and `driver_station`: "connected", or "waiting for the Driver
  Station". Its `mode` is the app's (`waiting`, `disabled`, `auto`, `teleop` or `test`), and its `match` has
  `connected`, `mode`, `estop`, `station`, `fms` and `match_time`.
- **`scrimmage serve`:** prints a line each time it changes: `Driver Station: teleop enabled` (or `auto enabled`,
  `test enabled`), `Driver Station: disabled`, `Driver Station: disabled (E-stopped)` or `waiting for the Driver
  Station`.
- **The CSV log** (`--log`): its mode column is the app's mode.

Things to know:

- **Before the app connects,** everything says "waiting for the Driver Station".
- **When the app stops sending,** the robot program disables itself within about 0.1 s, but it keeps reporting that
  a Driver Station is connected. So Scrimmage shows "connected" and "Disabled", with the last station and FMS,
  until the app comes back. (Seen with RobotPy 2026.2.2.)
- **Game data:** the robot program reads the app's Game Data box, as on a real field
  (`DriverStation.getGameSpecificMessage()`), but it doesn't report it back, so Scrimmage can't show it. The Drive
  tab's Game data box stays empty.
- **Joysticks:** the gamepads plugged into the Driver Station laptop reach the robot program through the app. The
  Drive tab's joystick ports are off.

## It runs in real time

The app talks to the robot program on the wall clock, so Scrimmage lets go of the robot's clock (lockstep) as soon
as the robot program connects, as it does for Phoenix 6 robots. The robot runs in real time:

- **The speed** is locked at 1x, with the reason shown.
- **It can't be paused.** Pause and Step are off in the Drive tab. An agent's session keeps running between calls,
  and `session_status` says `unpausable: true` ("the real Driver Station keeps the robot running between calls:
  this session can't pause it").
- **Runs aren't repeatable,** which is true anyway with a person at the Driver Station.

## What's off

While the app drives, Scrimmage doesn't send the Driver Station's inputs. These are off, and say why:

- **The Drive tab's** mode buttons, Enable and Disable, the joystick ports and the Match inputs. The page sends
  nothing to the robot. Agents' `set_mode`, `set_joystick` and `set_match` are refused with "this session can't
  change the Driver Station's inputs: the FRC Driver Station is driving".
- **Pause and Step** ("this session can't be paused or stepped: the FRC Driver Station is driving, in real time").
- **Record** ("this session can't be recorded: the FRC Driver Station is driving"), so there is no Save as
  scenario either.
- **`[match]`:** a config's `[match]` table is ignored. The app sends the station, FMS, game data and the match time
  from its own Practice timer. See [match data](match.md).
- **Scenarios:** a scenario is the Driver Station (it sets the modes and the joysticks), so a scenario run can't use
  the app. `scrimmage run --real-ds` and `run_scenario(real_ds=true)` are refused. A config with
  `[driver_station] real = true` still runs its scenarios, with Scrimmage as the Driver Station, and the report
  notes that the setting was ignored.

Poking the robot (dragging it, pushing it, moving game pieces) and **Reset robot** still work.

## Troubleshooting

- **"UDP port 1110 is already in use"** (or TCP port 1740): something else is listening for the Driver Station,
  so the robot program can't. Only one robot program per computer can listen for it. Stop the other real Driver
  Station session or robot program (another `scrimmage edit`, `serve` or agent session, or a robot program started
  by hand) and start again. Scrimmage checks this before it starts the robot program.
- **"The robot program isn't listening for the Driver Station (UDP 1110)":** the robot program started without its
  DS socket. RobotPy needs `--ds-socket`; Java needs `wpi.sim.addDriverstation()`, or `SCRIMMAGE_DS_SOCKET=1`
  under the init script (see [Starting the robot program yourself](#starting-the-robot-program-yourself)). The note
  comes once the start timeout has passed (`[robot_program] start_timeout`: 30 s, or 180 s for a Gradle project).
  For a program you started yourself, it comes that long after the program connects (30 s if the config has no
  `[robot_program]`). `scrimmage edit` and `scrimmage serve` print it in the terminal you started them from, and
  an agent's `session_status` lists it under `warnings`. The Drive tab itself keeps saying "Waiting for the Driver
  Station".
- **Stuck on "Waiting for the Driver Station":** check the app's Team Number (`127.0.0.1` or `localhost` on the same
  PC, the simulation computer's IP otherwise), and the firewall on both computers (the three ports above).
- **The app connects, but the robot doesn't move:** enable it in the app, and check that the gamepad shows in the
  app's USB tab (the joystick order there is the robot's port order).
- **Agents' set_mode is refused:** the session is a real-DS one. Start it again with `real_ds=false` to drive from
  the agent.
