# Match Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** when scrimmage is the Driver Station, it sends the robot game data, the station, FMS attached and a
counting-down match time. These come from a config or scenario `[match]`, a step's `game_data`, the Drive tab and
the MCP tools.

**Architecture:**
- **One place for match state:** `DriverStationState` (scrimmage/driverstation.py) holds the match settings and the
  game data.
- **The timer:** it computes the match time in `frames(now)` from the sim time of the last phase entry, so the bridge
  passes `now` and nothing else needs a clock.
- **Sources of settings:** the config, the scenario, MCP and the Drive tab only set that state.

**Tech Stack:** Python 3.11+, HALSim WebSocket (halsim_ws), RobotPy 2026.2.2, the Drive tab (vanilla JS), pytest.

**Spec:** docs/superpowers/specs/2026-11-04-match-data-design.md (decisions M1–M7 are binding).

## Global Constraints

- **Default frames:** with no `[match]`, no `set_match` and no game data, DS frames are byte-identical to today's:
  `>station: "blue1"`, `>match_time: -1`, and no `>game_data` or `>fms` key.
- **Settings and defaults:** station is one of `red1 red2 red3 blue1 blue2 blue3`. Game data is a string of at most
  64 characters. `auto_seconds` defaults to 20 and `teleop_seconds` to 140, both positive numbers.
- **`[match]` keys:** `station`, `fms`, `timer`, `auto_seconds` and `teleop_seconds`, in both configs and scenarios.
  A step's key is `game_data`.
- **Wording and tests:** American spellings and plain words. Never run `uv run` in the repo root; use
  `.venv/bin/python -m pytest`, serially.

## Review Focus

1. **A robot program restarted part way through a session** (`restart_robot`) gets the game data back within one DS
   period (M2). Test: Task 1.
2. **Two scenarios in a row in one MCP session:** the second starts with empty game data and its own `[match]`
   (M4). Test: Task 2.
3. **The timer at a phase boundary:** Auto, then disabled (holds), then Teleop, which starts again at
   `teleop_seconds`. It never goes below 0, and Test sends -1 (M1). Test: Task 1.
4. **A bad `[match]`** (station "red4", game data of 65 characters, `auto_seconds = 0`, an unknown key): an error
   naming the key and the allowed values, in configs and scenarios, and in the scenario editor's problems. Test:
   Tasks 1 and 2.
5. **The Drive tab edited while enabled in Teleop:** the new game data reaches the robot at once. While enabled in
   Auto it waits until Teleop (M5). Test: Task 3.

---

### Task 1: Match state, configs, MCP, and a live RobotPy check

**Files:**
- Modify: `scrimmage/driverstation.py`, `scrimmage/bridge.py` (pass `now` to `frames`), `scrimmage/config.py`
  (`Config.match`), `scrimmage/session.py` (apply the config's match at start; `set_match`; `state()` reports it),
  `scrimmage/mcp_server.py` (the `set_match` tool)
- Test: `tests/test_match_data.py`, `tests/test_match_robot_e2e.py` (model on `tests/test_sensors_robot_e2e.py`)

**Interfaces (produced, used by Tasks 2–4):**
- **`MatchSettings` (a dataclass in driverstation.py):** `station: str = "blue1"`, `fms: bool = False`,
  `timer: bool = False`, `auto_seconds: float = 20.0`, `teleop_seconds: float = 140.0`.
  - `MatchSettings.merged(table: dict, where: str) -> MatchSettings` is a copy with a `[match]` table's keys applied
    over these values (the key-by-key merge, M4). It raises `ValueError` naming `where` and the key.
- **Constants:** `STATIONS = ("red1", "red2", "red3", "blue1", "blue2", "blue3")`, `MATCH_KEYS`, `GAME_DATA_MAX = 64`.
  - `check_game_data(value, where) -> str` raises `ValueError` for anything but a string of at most 64 characters.
- **`DriverStationState`:**
  - **State:** gains `match: MatchSettings` and `game_data: str = ""`.
  - **Setters:** `set_match(**changes)` validates the same way and sets `changed`. Turning `timer` on starts the
    current phase now (M1). `set_game_data(s)` sets `changed`.
  - **`frames(now: float)`:** the existing `match_time` field goes away. The match time is computed (M1, M6):
    - it is -1 with the timer off, before any phase, and in Test;
    - otherwise it is the phase length minus `now` − entry, rounded to 3 decimals, never below 0;
    - it holds while disabled.
    A phase entry is a mode change into auto or teleop seen by `frames`, `set_mode` or a timer turn-on. Record the
    entry at the next `frames(now)` after the change.
  - **Which keys are sent:** `>game_data` and `>fms` are sent once either has been set (M2).
  - **`clear()`:** follows M3. It resets the timer value and the game data, and keeps `match`.
- **`Config.match: MatchSettings`:** from the config's `[match]`, with errors as `ConfigError("[match] ...")`.
- **`Session`:**
  - `set_match(station=None, fms=None, timer=None, game_data=None, auto_seconds=None, teleop_seconds=None) -> dict`
    returns `self.state()`.
  - `state()` gains `"match": {station, fms, timer, auto_seconds, teleop_seconds, game_data, match_time}`.
- **MCP:** the tool `set_match(...)` has the same keyword arguments and documents each one in plain words.

- [ ] **Step 1: Write the failing unit tests** in `tests/test_match_data.py`:
  - default frames equal today's, with an exact dict compare of the DriverStation frame's data;
  - after `set_game_data("R")`, frames carry `>game_data: "R"` every period, and after `set_game_data("")` they carry
    `""`;
  - station and FMS are sent;
  - **the timer:** auto entry at t=1.0 gives 20.0 at t=1.0 and 15.0 at t=6.0. Disabled at t=8.0 holds 13.0 at t=9.0.
    Teleop at t=10.0 gives 140.0, then 0 after 141 s and never negative. Test gives -1. With the timer off it is
    always -1 (Review Focus 3);
  - turning the timer on part way through Teleop starts at 140;
  - `clear()` keeps the station and timer setting, and drops the game data;
  - `MatchSettings.merged` rejects red4, 65 characters, `auto_seconds = 0` and an unknown key, each naming the key
    (Review Focus 4);
  - a config `[match]` loads, and a bad one raises `ConfigError`;
  - `Session.set_match` changes `state()["match"]`.
- [ ] **Step 2: Write the failing e2e test** in `tests/test_match_robot_e2e.py`: a tiny RobotPy robot prints, once a
  second in teleopPeriodic, `getGameSpecificMessage()`, `getAlliance()`, `getLocation()`, `isFMSAttached()` and
  `getMatchTime()`.
  - **The session:** a held session with `[match] station = "red2"`, `fms = true` and `timer = true`; teleop, then
    `set_match(game_data="R")`, then advance 3 s.
  - **The console shows:** "R", Red, 2, True, and a match time between 130 and 139.
  - **Review Focus 1:** `restart_robot`, advance 1 s, and the new process prints "R" again.
  - **If a field doesn't arrive (M7):** stop and report it with the HAL dump (`get_hal`). Don't work around it.
- [ ] **Step 3: Implement** to the interfaces above.
- [ ] **Step 4: Run** the new tests plus `-k "driverstation or bridge or session or mcp or golden"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "match data: station, FMS, game data and a match timer reach the robot"`

### Task 2: Scenarios

**Files:**
- Modify: `scrimmage/scenario.py` (a top-level `match`, the step key `game_data`, `Scenario.match`,
  `Step.game_data: str | None`), `scrimmage/scenario_runner.py`, the scenario editor (`scrimmage/assembler/scenario_edits.py`,
  `scenario_validate.py`, the server routes beside `add_step`, `static/scenario.js`)
- Test: `tests/test_scenario_match.py`, plus the scenario editor's existing server and browser test files (add cases there)

**Interfaces:**
- **Consumes:** Task 1's `MatchSettings.merged` and `check_game_data`, and `DriverStationState.set_match` and
  `set_game_data`.
- **`Scenario.match`:** the scenario's own `[match]` table (a dict, validated at load). The runner merges it over
  the config's `Config.match` when the run starts. `Step.game_data` is None (unchanged) or a string.
- **The runner:** at scenario start it sets `ds.match` to the merge and the game data to `""` (M4). At each step's
  start, a step's `game_data` that isn't None is sent before `set_mode`.
- **The report:** each step's report gains `game_data` (what the robot had during the step). The Markdown step
  heading shows it when it isn't empty.
- **Editor edits:**
  - `set_match(doc, key, value)` (None removes the key, and an empty table is removed);
  - `set_step_game_data(doc, i, value)` (None removes it), placed after `duration`.
  - In the UI: a Match section (station menu, FMS, timer, the two lengths) and a "Game data" field on each step.
- [ ] **Step 1: Write the failing tests:**
  - **Loading:** a scenario with `[match] station = "red1"` and steps auto (no game data), teleop `game_data = "B"`,
    teleop (none) loads, and a bad `[match]` or game data is an error naming the step (Review Focus 4).
  - **A runner run with a stub session or the existing fake robot:** the DS frames have game data "" in step 1 and
    "B" in steps 2 and 3, and the station is red1.
  - **Review Focus 2:** two scenarios run back to back in one session; the second starts with "" and its own
    station.
  - **Editor:** the edits round-trip, keeping comments and order, and the validator reports bad values as
    problems.
  - **Browser:** setting the station and a step's game data in the editor writes the file.
- [ ] **Step 2–4: Implement and run** them plus `-k "scenario or editor or report"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "scenarios: [match] and per-step game data"`

### Task 3: The Drive tab

**Files:**
- Modify: `scrimmage/assembler/live.py` (a `match` command with the keys of `Session.set_match`, plus `game_data`
  held by the tab and sent per M5; state reports `match`), `static/drive.js` and its HTML/CSS (station bar
  controls), `scrimmage/assembler/recorder.py` (`[match]` and per-step `game_data` in a saved recording, M5)
- Test: the Drive tab's existing live and browser test files, and the recorder test file (add cases there)

**Interfaces:**
- **Consumes:** `Session.set_match` and `state()["match"]` (Task 1), and the scenario keys (Task 2).
- **The tab's game data box:** holds a pending value.
  - The live session sends it with `set_match(game_data=...)` on each enable whose effective mode is teleop, and at
    once when the box changes while effectively teleop (M5).
  - While enabled in Auto, a changed box waits for Teleop (Review Focus 5).
- **Initial values:** the controls start from `state()["match"]`, which is the config's `[match]`.
- **Recording:** the recording saves `[match]` with the keys that differ from the defaults. A step gets `game_data`
  only when the data the robot had at the step's start differs from the step before (from "" for the first).
- [ ] **Step 1: Write the failing tests:**
  - **Live:** a live-session test that teleop plus enable with the box at "R" makes the session's game data "R".
    Changing the box to "B" while enabled changes it at once, and in Auto it doesn't change until Teleop is
    enabled (Review Focus 5).
  - **Browser:** the station menu sets the station, and the controls show the config's `[match]`.
  - **Recorder:** auto, then teleop with "R", then teleop gives steps with no game data, `"R"` and none, and
    `[match]` holds the changed station.
- [ ] **Step 2–4: Implement and run** them plus `-k "drive or live or recorder or browser"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "drive tab: station, FMS, match timer and game data controls"`

### Task 4: An example, Java, and docs

**Files:**
- Create:
  - `examples/robotpy_hubshift/` (a RobotPy robot whose `is_hub_active()` follows WPILib's 2026 example, using
    `getGameSpecificMessage()`, `getMatchTime()` and `getAlliance()`, and prints `hub active` / `hub inactive` when
    it changes);
  - a scenario `hubshift.scenario.toml` (`[match] station = "red1"`, `timer = true`; Auto 20 s, then teleop with
    `game_data = "R"`), with console checks that the hub reads inactive and active in the right Teleop windows;
  - a README stating what the run shows.
- Create: `tests/test_examples_hubshift.py`, and a Java check in `tests/test_java_e2e.py`: `tests/java_probe` (or a
  copy) prints `DriverStation.getGameSpecificMessage()` once in teleop, and the session's `set_match(game_data="R")`
  shows up in the console. If the probe can't change without breaking other Java tests, use a copy.
- Modify:
  - docs: a new `docs/match.md` covering every key, the timeline, the timer rules M1/M6, robot code in Java and
    Python, and that `serve` and a real DS (next sub-project) ignore `[match]`;
  - `docs/drive.md` (the new controls) and the scenario docs (`[match]` and `game_data`);
  - the README (the MCP tool list and the scenario key list);
  - the spec's decisions with anything ruled during the build.
- [ ] **Step 1:** Build the example. Its checks keep at least 20 ms of margin at each boundary (M6). The test runs
  it and asserts that it passes.
- [ ] **Step 2:** Add the Java check.
- [ ] **Step 3:** Write the docs, with a docs test (like `tests/test_sensors_docs.py`) that every `[match]` key and
  the `set_match` tool are documented.
- [ ] **Step 4:** Run the new tests and then the full suite once. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "docs and an example: match data"`
