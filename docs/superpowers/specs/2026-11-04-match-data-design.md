# Match Data Design (sub-project 27)

**Status:** the owner delegated design questions (decide-and-document, 2026-10-28). Written 2026-11-04.

## Why

Some years the field sends the robot data that changes what it should do. In 2026 (REBUILT) it is one letter, `R` or
`B`, about 3 s after Auto ends: the alliance whose hub goes inactive first. Robot code combines it with the match time
and the alliance color to know whether its hub is active (WPILib's "2026 Game Data Details" page). In 2018 it was
the switch and scale sides at the start of Auto, and in 2020 the Stage 3 color.

Today scrimmage, acting as the Driver Station, sends `>station: "blue1"` and `>match_time: -1` every time and never
sends `>game_data`. So code like WPILib's `isHubActive()` example can't be tested: the game data is always empty,
the alliance is always blue, and the match time is always unknown.

## Goals

1. **Game data reaches the robot:** robot code reads it with `DriverStation.getGameSpecificMessage()` (2026)
   in Java and Python, from a scenario, the Drive tab and the MCP tools.
2. **When it arrives:** a scenario sets it per step, so it can arrive at any point (empty in Auto, `R` from Teleop).
   The Drive tab sends it the way the real Driver Station's Settings box does, when the robot is enabled in Teleop.
3. **The station:** the alliance and station (red1–3, blue1–3) can be chosen, and `DriverStation.getAlliance()`
   follows it.
4. **A match timer:** when on, the match time counts down in sim time: from the Auto length at the start of Auto,
   and from the Teleop length at the start of Teleop. The defaults are 2026's 20 s and 140 s, and a scenario can
   change them.
5. **FMS attached** can be set, so code that checks `isFMSAttached()` can be tested.
6. **An example and a live check:** a RobotPy robot reads all of it in an end-to-end test, and an example scenario
   tests a 2026-style hub-shift check.

## Non-goals

- Using a real NI Driver Station. That is sub-project 28, next. This one keeps scrimmage as the DS.
- Event name, match number and match type. halsim_ws has no fields for them.
- The Test tab. Its blocks play with no robot program, so no robot code reads game data there.
- Scoring the hub shifts in the simulator. The field's goals don't turn off.

## Constraints

- **Defaults keep today's frames:** station blue1, FMS off, game data empty and the timer off (match time -1). A
  scenario or config without `[match]` sends exactly what it sends now.
- **The halsim_ws fields:** `>game_data` (a string; the HAL keeps up to 64 bytes), `>station` ("red1".."blue3"),
  `>fms` (bool) and `>match_time` (seconds).
- **Wording and tests:** American spellings and plain words. Tests run with `.venv/bin/python -m pytest`, never
  `uv run` in the repo root.

## The design

### Scenario files

- **A top-level `[match]` table:** `station` (default "blue1"), `fms` (default false), `timer` (default false), and
  `auto_seconds` (20) and `teleop_seconds` (140).
- **Per step, `game_data = "R"`:** sent when the step starts. It stays until a later step changes it (`""` clears
  it), the way the field's data stays for the match.
- **The scenario editor:** shows and edits `[match]` and a step's game data.
- **Errors:** an unknown station names the six, and game data over 64 characters says so.

### The match timer

- **The countdown:** with `timer = true`, entering Auto sets the match time to `auto_seconds` and entering Teleop
  sets it to `teleop_seconds`. It counts down in sim time while enabled and stops at 0.
- **Disabled and Test:** the time holds while disabled, and Test sends -1.
- **Why sim time:** runs are repeatable at any speed.

### The Drive tab

- **The station bar:** gains a station menu, an FMS box, a timer box and a game data box.
- **Game data:** sent when the robot is enabled in Teleop, as the real DS does. A Record turns it into the
  recorded step's `game_data`, and the station, FMS and timer into the saved scenario's `[match]`.

### The MCP tools

- **`set_match(station?, fms?, timer?, game_data?, auto_seconds?, teleop_seconds?)`:** sets any of these, sent on
  the next DS frame.
- **`get_state`:** reports them.

### Robot configs

- **A config's `[match]`:** has the same keys as a scenario's and is used by `serve`, the Drive tab and MCP sessions.
- **A scenario's `[match]`:** wins over the config's.

## Left to the builder

- Where the match state lives (likely `DriverStationState`) and how the Drive tab lays out the new controls,
  within the station bar's current style.

## Decisions (from the builder check)

Each decision gives the choice, the reason, and what it costs if it is wrong.

- **M1. When the timer restarts:**
  - **The rule:** every entry into Auto or Teleop restarts the countdown at that phase's length. That includes
    enabling from disabled, a mode change while enabled, and a second `set_mode("auto")` after a disable. The time
    counts down only while enabled, holds while disabled, and stays at 0 once it runs out.
  - **The edges:** before any phase it is -1, and in Test it is -1. Turning the timer on part way through a phase
    starts it at that phase's full length. A changed `auto_seconds` takes effect at the next entry.
  - **Why:** a real match is Auto, a short disable, then a fresh Teleop, and every scenario and Drive tab session
    goes through the same steps. One rule with no memory of earlier phases is easy to predict.
  - **Cost:** re-enabling within Teleop after a pause restarts the clock, so a student who pauses part way through a
    pretend match gets a fresh 140 s. Fixing it means remembering the phase.
- **M2. Game data is state, sent in every DS frame:**
  - **How it is sent:** like the mode. `>game_data` and `>fms` join the frame only once they have been set in the
    session, so default frames are unchanged. Once in, they stay, so clearing to `""` is sent too.
  - **Why:** a restarted robot program gets it back on the next frame.
  - **Cost:** about 20 bytes a frame.
- **M3. What a reset clears:**
  - **What clears:** a session reset (`clear()`) clears the mode, joysticks, timer value and game data.
  - **What stays:** the station, FMS and timer settings stay at the session's `[match]`.
  - **Why:** those are settings, and the others are the match's progress.
- **M4. Which `[match]` wins:**
  - **Merging:** key by key, so a scenario's keys win over the config's for the keys it sets.
  - **Each scenario:** `run_scenario` applies its merged `[match]` when it starts, with the game data empty. It does
    not restore what was there before, which matches what it does with joysticks.
  - **The Drive tab and MCP:** both start from the config's `[match]`, and `set_match` and the Drive tab controls
    change the live session.
  - **`serve`:** the WPILib sim GUI (or, in sub-project 28, the real DS) is the DS there, so `[match]` doesn't apply
    and the docs say so.
  - **Cost:** a scenario run inside a live MCP session leaves its station behind. `get_state` shows it.
- **M5. The Drive tab's game data box:**
  - **When it is sent:** on every enable in Teleop, and at once if edited while enabled in Teleop.
  - **The timer:** follows sim time, like everything in a session.
  - **Record:** a recorded step carries `game_data` only when the data the robot had at the step's start differs
    from the step before (or, for the first step, from empty).
- **M6. The match time's precision:**
  - **The value:** the remaining time is the phase length minus the enabled sim time since entry, rounded to 1 ms.
  - **The example:** its boundary checks keep at least one robot period (20 ms) of margin.
  - **Cost:** a robot that tests `== 0` exactly still works, because time holds at 0.
- **M7. Checked live first:** Task 1's end-to-end test proves that RobotPy 2026 reads `>game_data`, `>station` and
  `>fms` through halsim_ws (`getGameSpecificMessage`, `getAlliance`, `getLocation`, `isFMSAttached`,
  `getMatchTime`). Java is checked through the existing Java end-to-end harness. If a field doesn't arrive, the
  ruling and its workaround go here.
