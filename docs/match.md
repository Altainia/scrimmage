# Match data: the station, the match timer and game data

Some years the field tells the robot something that changes what it should do. In 2026 (REBUILT) it is one letter,
`R` or `B`, sent about 3 s after Auto ends: the alliance whose hub goes inactive first. In 2018 it was the sides of the
switch and scale, at the start of Auto. Robot code reads it, together with its alliance and the match time, from the
Driver Station.

When Scrimmage runs your robot (a scenario, the Drive tab or an agent's session), Scrimmage is the Driver Station. A
`[match]` table and each step's `game_data` say what it sends: which alliance and station the robot is at, whether the
field (FMS) is attached, the match time, and the game data. With none of them, it sends what a plain practice session
does: blue1, no FMS, no game data and no match time (-1).

`examples/robotpy_hubshift` is a worked example: a 2026 robot that works out whether its hub is active, tested
through a whole match.

Contents: [The keys](#the-keys), [A match, step by step](#a-match-step-by-step), [The match timer](#the-match-timer),
[Game data](#game-data), [Robot code](#robot-code), [In the Drive tab](#in-the-drive-tab),
[In the scenario editor](#in-the-scenario-editor), [Agents (MCP)](#agents-mcp),
[`scrimmage serve` and a real Driver Station](#scrimmage-serve-and-a-real-driver-station), [Errors](#errors).

## The keys

A `[match]` table can go in a robot config and in a scenario. All of its keys are optional:

| Key | Default | What it does | Robot code reads it with |
|---|---|---|---|
| `station` | `"blue1"` | the alliance and driver station: `red1`, `red2`, `red3`, `blue1`, `blue2` or `blue3` | `getAlliance()`, `getLocation()` |
| `fms` | `false` | whether the field (FMS) is attached | `isFMSAttached()` |
| `timer` | `false` | `true` counts the match time down (see [The match timer](#the-match-timer)); `false` sends -1 | `getMatchTime()` |
| `auto_seconds` | `20` | how long Auto is, in seconds (2026's 20 s) | `getMatchTime()` |
| `teleop_seconds` | `140` | how long Teleop is, in seconds (2026's 140 s) | `getMatchTime()` |

A scenario step can also have:

| Key | Default | What it does | Robot code reads it with |
|---|---|---|---|
| `game_data` | (keep) | the field's game data, sent when the step starts, such as `"R"`; `""` clears it | `getGameSpecificMessage()` |

```toml
config = "hubshift.toml"

[match]
station = "red1"
timer = true

[[step]]
name = "auto"
mode = "auto"
duration = 20.0

[[step]]
name = "teleop"
mode = "teleop"
duration = 140.0
game_data = "R"
```

**Which `[match]` wins.** The config's `[match]` is where every session starts: scenarios, the Drive tab and agents'
sessions. A scenario's `[match]` goes over it key by key, so a scenario that sets only `station` keeps the config's
`timer`.

## A match, step by step

A real 2026 match is Auto (20 s), a short pause while the robot is disabled, then Teleop (140 s). The game data
arrives about 3 s after Auto ends. In a scenario that is three steps, with the game data on the Teleop step (or on a
later Teleop step, if you want the robot to start Teleop without it):

| Step | Mode | Match time (`timer = true`) | Game data |
|---|---|---|---|
| auto | auto, 20 s | counts down from 20 to 0 | none yet |
| the pause | disabled, 3 s | holds at 0 | none yet |
| teleop | teleop, 140 s | starts again at 140 and counts down to 0 | `R`, from the step's start to the end |

The scenario's report shows each step's game data in its heading, such as `## teleop (teleop, t=23.00–163.00 s,
game data "R")`.

## The match timer

With `timer = true`, the match time is the seconds left in the current phase, Auto or Teleop:

- **When it starts again:** every time the robot goes into Auto or Teleop, the time starts again at that phase's
  full length. That is enabling from disabled, changing the mode while enabled, and going into Auto a second time
  after a disable. Choosing the same mode again while it is already in it doesn't restart it.
- **While enabled:** it counts down in simulated time and stops at 0 (it never goes below 0).
- **While disabled:** it holds where it was.
- **When it is -1:** in Test, with the timer off, and before the robot has been in Auto or Teleop.
- **Turning it on part way through a phase** starts it at that phase's full length. A changed `auto_seconds` or
  `teleop_seconds` counts from the next time that phase starts.
- **Its precision:** the value is the phase's length minus the enabled time since the phase started, rounded to
  1 ms. Simulated time makes it repeatable: the same run gives the same match times at any speed.
- **A reconnect carries it on.** When the robot program reconnects, simulated time starts again from 0, and the match
  time carries on from where it was rather than starting the phase again.

Your robot code sees each new value about one robot loop after it is sent, so a check like `match_time > 130` can
change up to 20 ms after the 130 s mark. A scenario that checks a change near a boundary should leave at least one robot loop (20 ms) on
each side. The example gives each change a 0.2 s step of its own, centered on the boundary.

A pause in the middle of Teleop restarts the clock when you enable again: you get a fresh 140 s. That keeps the rule
simple (no memory of earlier phases), and a real match never pauses.

## Game data

- **It stays.** A step's `game_data` is sent when the step starts and stays until a later step changes it, the way
  the field's data stays for the rest of the match. A step without `game_data` keeps what the step before left.
  `game_data = ""` clears it.
- **Each scenario starts empty.** A scenario run starts with no game data, even in an agent's session that had some.
  In attach mode (scrimmage doesn't launch the robot program, so it isn't restarted) the empty game data is sent to
  the robot, so it doesn't keep the old data.
- **A restart keeps it.** A robot program restarted part way through (an agent's `restart_robot`, or the Drive tab
  restarting a running robot on new IDs) gets the game data back on its first frame: a restart is the same match.
- **The size limit:** at most 64 bytes, as the robot's HAL keeps. That is 64 plain letters, digits or punctuation;
  some other characters (such as accented letters) take 2 or more bytes each.

## Robot code

Robot code reads the match data from WPILib's `DriverStation`, the same calls it uses on a real field. Nothing in it
needs to know it is simulated.

Java:

```java
import edu.wpi.first.wpilibj.DriverStation;
import edu.wpi.first.wpilibj.DriverStation.Alliance;

String gameData = DriverStation.getGameSpecificMessage();   // "" until the field sends it
var alliance = DriverStation.getAlliance();                  // Optional<Alliance>: empty before the DS connects
double matchTime = DriverStation.getMatchTime();             // seconds left in the phase, or -1
boolean fms = DriverStation.isFMSAttached();
int station = DriverStation.getLocation().orElse(0);         // 1, 2 or 3

if (alliance.isPresent() && alliance.get() == Alliance.Red && gameData.startsWith("R")) {
  // our hub is inactive first
}
```

Python (RobotPy):

```python
from wpilib import DriverStation

game_data = DriverStation.getGameSpecificMessage()   # "" until the field sends it
alliance = DriverStation.getAlliance()               # DriverStation.Alliance.kRed or kBlue, or None
match_time = DriverStation.getMatchTime()            # seconds left in the phase, or -1
fms = DriverStation.isFMSAttached()
station = DriverStation.getLocation()                # 1, 2 or 3, or None

if alliance == DriverStation.Alliance.kRed and game_data.startswith("R"):
    pass   # our hub is inactive first
```

`examples/robotpy_hubshift/robot.py` has a whole `is_hub_active()`, written from WPILib's 2026 example.

## In the Drive tab

The Driver Station column has a **Match** section: a **Station** menu, a **Game data** box, and **FMS attached** and
**Match timer** checkboxes. They start at the config's `[match]` when you press **Start**, and work while the
session is live.

- **Station, FMS attached and Match timer** reach the robot at once.
- **The Game data box** works like the real Driver Station's: the robot gets it each time it is enabled in Teleop.
  If you change the box while the robot is enabled in Teleop, the new value is sent when you press **Enter** or
  leave the box. Changed while disabled or in Auto, it waits for the next Teleop enable.
- **The HUD** over the live view shows the match time (`match 128.4 s`) while the timer is on.
- **No length fields:** the Drive tab has no fields for the Auto and Teleop lengths. To change them, put
  `auto_seconds` and `teleop_seconds` in the config's `[match]` (or a scenario's), or use the `set_match` tool.
- **A restart keeps the match:** when the robot code panel restarts a running robot on new IDs, the station, FMS,
  timer and game data box stay, and the robot gets its game data back. **Start** (and **Restart** after an error,
  which starts a new session) goes back to the config's `[match]` with an empty box.

**Recording.** A recording saves the match too:

- The saved scenario gets a `[match]` with the station, FMS and timer settings you had when you pressed **Record**
  (every setting that differs from the default or from the config's, so a replay hears the same match).
- Station, FMS and timer changes you make during a recording **aren't saved**. Set them before you press **Record**.
- Each time the robot's game data changes, a new recorded step starts, with that step's `game_data`.

## In the scenario editor

The Scenario tab's header has a **Match** row: Station, FMS attached, Match timer, Auto length and Teleop length.
Each starts at "robot config's", which leaves that key to the config. Each step's editor has a **Game data** box: an
empty box keeps what the step before left (the box shows it, such as `keeps "R"`), and **Clear it here** writes
`game_data = ""`.

## Agents (MCP)

| Tool | What it does |
|---|---|
| `set_match` | Set any of `station`, `fms`, `timer`, `game_data`, `auto_seconds` and `teleop_seconds`; leave out what shouldn't change. Sent on the next advance, in any mode (no wait for Teleop, unlike the Drive tab's box). |
| `get_state` | Its `match` has the station, FMS, timer and lengths, the `game_data`, and `match_time`: the match time last sent. |

An agent's session starts from the config's `[match]`. `set_match(game_data="R")` sends the game data at once, in any
mode: the agent is the field and the Driver Station both. `restart_robot` keeps the game data and settings.
`run_scenario` in a session applies the scenario's `[match]` and starts with no game data; afterwards the session
keeps that station (`get_state` shows it).

## `scrimmage serve` and a real Driver Station

`scrimmage serve` ignores `[match]`: there the WPILib simulation GUI is the Driver Station, so set the alliance,
game data and match time in its window. A session the FRC Driver Station app drives (`[driver_station] real = true`,
or the Drive tab's **The FRC Driver Station app**) ignores `[match]` too, for the same reason: the app is the Driver
Station then. It sends the station, FMS, game data and match time from its own Practice timer. See
[driving with the FRC Driver Station app](driver-station.md).

## Errors

Mistakes stop the run before anything starts, and the message says where:

- **An unknown station:** `[match] station must be one of red1, red2, red3, blue1, blue2, blue3, not 'red4'`.
- **An unknown key:** `[match] has no key 'alliance'; the keys are station, fms, timer, auto_seconds,
  teleop_seconds`.
- **A bad value:** `timer must be true or false`, `auto_seconds must be a positive number of seconds`.
- **Game data that is too long:** `step 2 (shift): game_data must be at most 64 bytes (64 plain letters, digits or
  punctuation)`, or `game_data must be text` for a number.

The Drive tab and `set_match` check the same things and show the same messages.
