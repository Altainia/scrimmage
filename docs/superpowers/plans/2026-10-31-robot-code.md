# Point at the Code and Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a programmer point a robot model at the team's robot code and run it from the Robot tab: the project is
detected, a readiness checklist fixes what's missing, the start is picked on a field picture, and a device check
catches CAN ID mistakes. CAD and code folders are set per computer so the same robot file works on every laptop.

**Architecture:** Three new core modules sit beside the existing loaders and are used by the CLI, the agent and the
page alike:
- `teamfolders.py`: the per-computer `{cad}` and `{code}` folders;
- `robotcode.py`: project detection, the checklist and the fixes;
- `devicecheck.py`: expected bindings against the devices the code created.

The editor server gains `/api/robot/code|start|devices` routes and edit ops on top of the existing document handling.
The Robot tab gains a Robot code panel (`code.js`), a start picker and a device table, plus brake/invert and battery
controls.

**Tech Stack:** Python 3.11+, MuJoCo, tomlkit, the editor's stdlib HTTP server and long-polls, ES-module front end,
pytest and Playwright.

**Spec:** `docs/superpowers/specs/2026-10-31-robot-code-design.md`

## Global Constraints

- **Plain words throughout**, for high-school students and mentors who aren't programmers.
- **Readable in light and dark themes**, contrast at least WCAG AA, checked by a browser test. Icons and bold labels
  for status, never pale tinted rows.
- **The CLI keeps working:** hand-written configs, `scrimmage run`, `serve`, the agent, existing paths and
  scenarios. Only starts from the page ask for a start spot; the loader keeps `DEFAULT_START_POSE`.
- **`{cad}` and `{code}` resolve everywhere** (robot config, part `path`, `[robot_program]` command and cwd, `[range]`
  file, a scenario's `config`):
  - from the per-computer settings;
  - overridden by the environment variables `SCRIMMAGE_CAD` / `SCRIMMAGE_CODE`;
  - when neither is set, a plain error naming the folder and how to set it.
- **Never write a robot file that only works on this laptop** when a team folder covers the path. Never start the
  robot from a spot nobody chose. Never hide a device mismatch.
- **Edits keep comments and order** and are undoable (the existing `ConfigDoc`/edit-op path).
- **Linux is tested.** Windows and macOS never blocked: the lockstep check says "not available on this computer: the
  robot runs in real time".
- **Commits** end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Verify locally; push nothing.

## Review Focus

1. **A robot file using `{cad}` opened on a computer that hasn't set the CAD folder.** The page asks for it once, and
   the CLI names the folder and the variable; no traceback, no wrong path. Tests: Task 1
   `test_unset_folder_error_is_plain`, Task 4 `test_open_robot_with_unset_folder_asks`.
2. **A folder picked as robot code that isn't a robot project** (or was moved). The panel says it found nothing it can
   start, and nothing is written. Test: Task 2 `test_detect_not_a_project`.
3. **Devices the code creates late** (in teleopInit). The table shows them as they appear and doesn't call a model
   device "missing" until the robot has run a while (`MISSING_AFTER`). Test: Task 3
   `test_late_device_not_missing_early`.
4. **Applying matches that swap two IDs** (the model has fl.drive 1 and fl.steer 2; the code has them the other way
   round). One write and one Undo; no transient duplicate-ID error. Test: Task 5 `test_apply_swap_is_one_edit`.
5. **A first Gradle build that fails** (no network, wrong JDK). The checklist shows the failure in plain words with the
   last lines of output; Drive isn't started into a 180 s timeout. Test: Task 2 `test_gradle_build_failure_reported`.

---

### Task 1: Team folders

**Files:**
- Create: `scrimmage/teamfolders.py`
- Modify:
  - `scrimmage/config.py`: `[robot_program]` command and cwd.
  - `scrimmage/parts/assemble.py`: part `path`.
  - The `[range]` file reference (where the config's range is loaded).
  - `scrimmage/scenario.py`: `config`.
- Test: `tests/test_teamfolders.py`

**Interfaces:**
- Produces:
  - `FOLDERS = ("cad", "code")`.
  - `class TeamFolderError(ValueError)`. Its message is plain, e.g. "this robot file uses the team's CAD folder, but
    this computer doesn't know where it is: set it on the start page (scrimmage edit), or set SCRIMMAGE_CAD".
  - `folders() -> dict[str, Path | None]`. Environment variables win over the settings file.
  - `set_folder(name: str, path: Path | None) -> None`, an atomic write.
  - `expand(text: str) -> str` replaces `{cad}` and `{code}`. It raises `TeamFolderError` for an unset folder that the
    text uses, and returns text without placeholders unchanged.
  - `relativize(path: Path) -> str`: the path as `{cad}/...` or `{code}/...` when it is under a set folder (the
    longest match wins), else unchanged (as a string).
  - The settings file is `folders.json` next to `recent.json` (`SCRIMMAGE_CACHE` or `~/.cache/scrimmage`), overridden
    by `SCRIMMAGE_FOLDERS` for tests, like `SCRIMMAGE_RECENT`.

- [ ] **Step 1: Write the failing tests**

```python
def test_expand_and_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "f.json"))
    set_folder("cad", tmp_path / "cad")
    assert expand("{cad}/arm") == str(tmp_path / "cad") + "/arm"
    monkeypatch.setenv("SCRIMMAGE_CAD", str(tmp_path / "other"))
    assert expand("{cad}/arm").startswith(str(tmp_path / "other"))
    assert expand("parts/arm") == "parts/arm"

def test_unset_folder_error_is_plain(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "f.json")); monkeypatch.delenv("SCRIMMAGE_CODE", raising=False)
    with pytest.raises(TeamFolderError, match="code folder.*SCRIMMAGE_CODE"): expand("{code}/robot")

def test_relativize_longest_match(tmp_path, monkeypatch): ...   # cad=/x, code=/x/code -> /x/code/r -> "{code}/r"

def test_config_resolves_program_cwd_and_part_path(tmp_path, monkeypatch):
    # robot.toml with path = "{cad}/arm" and [robot_program] cwd = "{code}/bot" loads; cwd resolves to code/bot

def test_scenario_config_placeholder(tmp_path, monkeypatch): ...   # config = "{cad}/robot.toml"

def test_cli_run_with_unset_folder_exits_2_plain(tmp_path, monkeypatch): ...  # message names the folder, no traceback
```

- [ ] **Step 2: Run them; they fail.**
- [ ] **Step 3: Implement** `teamfolders.py`, and call `expand` wherever those keys are read, before paths are
  resolved against the file's folder. Existing relative and absolute paths behave as before.
- [ ] **Step 4: Run** `pytest -q tests/test_teamfolders.py tests/test_config*.py tests/test_parts*.py tests/test_scenario*.py`;
  everything passes.
- [ ] **Step 5: Commit** ("Team folders: {cad} and {code} per computer, everywhere a path is read").

### Task 2: Robot project detection, checklist and fixes

**Files:**
- Create: `scrimmage/robotcode.py`
- Test: `tests/test_robotcode.py`

**Interfaces:**
- Consumes: `java_tools` (JDK lookup, `JAVA_DIR`, the agent build), the lockstep version check (`scrimmage_lockstep`), and
  `teamfolders.relativize`.
- Produces:
  - `@dataclass ProjectInfo(folder: Path, kind: Literal["java", "robotpy"] | None, vendors: list[str], tuner_swerve: bool, python: Path | None, summary: str)`.
    - `summary` is plain, e.g. "Java (GradleRIO) · CTRE Phoenix 6 · Tuner X swerve", or "nothing here Scrimmage can
      start: no build.gradle or robot.py".
    - `vendors` comes from `vendordeps/*.json` names (Phoenix 6, REVLib, ...) or the RobotPy requirements.
  - `detect(folder: Path) -> ProjectInfo`.
  - `program_table(info: ProjectInfo) -> dict`: the `[robot_program]` values. `cwd` is `teamfolders.relativize` of the
    folder. Java gets `["./gradlew", "-I", "{scrimmage_java}/scrimmage.init.gradle", "simulateJava"]`. RobotPy gets
    `[<python>, "-m", "robotpy", "sim", "--nogui", "--ws-client", "--scrimmage-lockstep"]`, where `<python>` is the
    project's `.venv` Python written relative to the folder, else `"{python}"`.
  - `@dataclass Check(id: str, ok: bool | None, message: str, fix: str | None, detail: str | None)`. `ok=None` is
    information only, e.g. the real-time note.
  - `checklist(info: ProjectInfo) -> list[Check]`. Ids: `jdk`, `agent`, `gradle_built`, `lockstep`, `realtime`,
    `robotpy`. On Windows and macOS, `lockstep` has `ok=None` and the message "not available on this computer: the
    robot runs in real time".
  - `run_fix(fix: str, info: ProjectInfo, out: Callable[[str], None]) -> Check`. Fixes: `"build_agent"` (`scrimmage java build`),
    `"gradle_build"` (`./gradlew build` in the folder, with the JDK env), and `"install_lockstep"` (pip install of
    Scrimmage's `lockstep/` into the project's Python). It streams output lines to `out` and returns the re-checked
    item, whose `detail` holds the last 20 lines on failure.

- [ ] **Step 1: Write the failing tests**

```python
def test_detect_java_phoenix_tuner():
    i = detect(EXAMPLES / "java_swerve")
    assert i.kind == "java" and "Phoenix 6" in i.vendors and "Java (GradleRIO)" in i.summary

def test_detect_rev_and_robotpy():
    assert "REVLib" in detect(EXAMPLES / "java_maxswerve").vendors
    assert detect(EXAMPLES / "robotpy_kitbot").kind == "robotpy"

def test_detect_not_a_project(tmp_path):
    i = detect(tmp_path); assert i.kind is None and "nothing here" in i.summary

def test_program_table_uses_code_folder(tmp_path, monkeypatch):
    # code folder set to EXAMPLES -> program_table(detect(EXAMPLES/"java_swerve"))["cwd"] == "{code}/java_swerve"

def test_checklist_ids_and_realtime_note(): ...      # java_swerve: jdk, agent, gradle_built, lockstep, realtime present

def test_gradle_build_failure_reported(tmp_path):
    # a fake project whose gradlew exits 1 printing "Could not resolve" -> run_fix("gradle_build") -> ok False,
    # detail contains "Could not resolve", message plain
```

- [ ] **Step 2: Run them; they fail.** **Step 3: Implement.** **Step 4: Run** `pytest -q tests/test_robotcode.py`;
  they pass.
- [ ] **Step 5: Commit** ("Robot code: detect the project, check it's ready to start, fix what's missing").

### Task 3: Device check

**Files:**
- Create: `scrimmage/devicecheck.py`
- Modify:
  - `scrimmage/scenario_runner.py`: the report gets the device notes as notes, in `report.md`, `report.json` and the
    printed summary.
  - `scrimmage/session.py`: `status()` gains `devices`.
- Test: `tests/test_devicecheck.py`, and a scenario-report case in the existing scenario runner tests

**Interfaces:**
- Consumes: `bridge.phoenix_devices`, `rev_devices`, `scrimmage_devices`, `resolve_device_key`, `PHOENIX_NAME`,
  `MISSING_AFTER`, and `HalState.devices`.
- Produces:
  - `@dataclass DeviceRow(slot: str | None, kind: str, expected: str | None, made: str | None, status: Literal["ok","waiting","missing","extra","other_type","not_checked"], suggest: list[str], note: str)`.
    - `slot` is the binding's name (`fl.drive`).
    - `waiting` means not made yet, before `MISSING_AFTER` s of robot time.
    - `suggest` lists same-type devices the code made that nothing binds, nearest ID first.
    - `other_type` means the code made a device of another type with that ID, and points to the part inspector.
    - `not_checked` covers PWM, DIO, analog and other SimDevice sensors.
    - `note` is plain words.
  - `check_devices(world, hal: HalState, robot_time: float) -> list[DeviceRow]`.
  - `device_notes(rows: list[DeviceRow]) -> list[str]`: the plain lines for reports, e.g. "fr.drive expects TalonFX 3,
    but the robot code made no TalonFX 3; it made TalonFX 13, which nothing uses".

- [ ] **Step 1: Write the failing tests** with a fake `HalState` and a world from `configs/swerve.toml`:

```python
def test_all_match(): ...                              # every row ok
def test_missing_with_suggestion():                    # code made TalonFX 13 instead of 3
    rows = check_devices(world, hal_with(ids_swapped={3: 13}), robot_time=5)
    r = next(r for r in rows if r.slot == "fr.drive")
    assert r.status == "missing" and r.suggest[0].endswith("13")
def test_late_device_not_missing_early():              # robot_time 0.5 -> "waiting"; 5 -> "missing"
def test_extra_and_other_type(): ...                   # SparkMax 3 where TalonFX 3 expected -> other_type
def test_scenario_report_lists_device_notes(tmp_path): ...  # wrong id config -> report.json notes contain the line
```

- [ ] **Step 2: Run them; they fail.** **Step 3: Implement.** Reuse the bridge's matching; don't duplicate the regexes.
- [ ] **Step 4: Run** `pytest -q tests/test_devicecheck.py tests/test_scenario*.py tests/test_bridge*.py`; they pass.
- [ ] **Step 5: Commit** ("Device check: expected devices beside the ones the code made, also in reports").

### Task 4: Server: team folders, robot code panel, start and field

**Files:**
- Modify:
  - `scrimmage/assembler/server.py`: routes and the docstring.
  - `scrimmage/assembler/templates.py`: robot templates no longer write `start_pose`.
  - `scrimmage/assembler/edits.py`: ops `program`, `start` and `battery`.
- Test: `tests/test_robot_code_server.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Routes:

  ```
  GET  /api/app/folders                  -> {cad, code, from_env: {cad: bool, code: bool}}
  POST /api/app/folders {name, path|null}-> {ok}; path must be an existing folder
  GET  /api/robot/code                   -> {folder: shown path|null, info: ProjectInfo as JSON|null, checks: [Check],
                                             start: {picked: bool, pose: [x,y,deg]|null, range: name|null,
                                             named_starts: [names], sentence: plain text}, battery: {...}}
  POST /api/robot/code {folder}          -> detects; writes [robot_program] via the `program` op (undoable);
                                             400 {errors} with the plain summary when nothing can start, nothing written
  POST /api/robot/code/fix {fix}         -> {ok, id}; runs run_fix on a worker (one at a time); progress lines via
  GET  /api/robot/code/fix?since=N       -> {version, running, lines: [...], result: Check|null} (long-poll)
  POST /api/robot/edit op "start" {pose:[x,y,deg] | named: str | null, range: "2026"|null, floor_balls?: bool}
                                         -> writes [robot] start_pose (removing [range] start) or [range] start = named,
                                             and [range] file/none; null pose clears the start (picked false)
  POST /api/robot/edit op "battery" {nominal_voltage?, resistance?}
  ```

- Start gate: `/api/live/start` and `/api/scenario/run` refuse with 200
  `{ok: false, errors: [{field: "start/unpicked", message: "Pick where the robot starts first"}]}` while the robot
  config has neither `[robot] start_pose` nor `[range] start`. The CLI and loader are unchanged.
- `sentence`: plain words such as "Starts on the blue side, facing the red wall", from the pose and the field's
  alliance halves (the plain field: blue on the left half, red on the right).
- Robot files opened or saved through the page store paths through `teamfolders.relativize`.
- Opening a robot file whose placeholders name an unset folder gives `/api/app/state` `needs_folder: "cad"|"code"`,
  and the robot state's read-only reason says which folder to set.

- [ ] **Step 1: Write the failing tests:** `test_folders_roundtrip`, `test_code_detect_writes_program_undoable`,
  `test_code_not_a_project_writes_nothing`, `test_fix_progress_and_failure`, `test_start_gate_refuses_drive_and_run`,
  `test_start_named_and_free_pose_exclusive`, `test_templates_have_no_start_pose`,
  `test_open_robot_with_unset_folder_asks`, `test_battery_edit`. Each asserts the file text and the response fields
  above.
- [ ] **Step 2: Run them; they fail.** **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q tests/test_robot_code_server.py tests/test_assembler_server.py tests/test_scenario_server.py tests/test_live_server.py tests/test_app_open.py`;
  they pass.
- [ ] **Step 5: Commit** ("Editor: robot code panel, readiness fixes, start and field, team folders").

### Task 5: Server: device check and motor settings

**Files:**
- Modify:
  - `scrimmage/assembler/server.py`: routes and the docstring.
  - `scrimmage/assembler/live.py`: devices from the live session.
  - `scrimmage/assembler/edits.py`: ops `match_devices` and `motor`.
- Test: `tests/test_robot_code_server.py`

**Interfaces:**
- Consumes: Task 3, and the Drive tab's live session.
- Routes and ops:

  ```
  POST /api/robot/check                  -> starts the robot like Drive (refused while unpicked or live), runs it
                                             disabled until MISSING_AFTER + 1 s of robot time, keeps it running
  GET  /api/robot/devices?since=N        -> {version, live: bool, robot_time, rows: [DeviceRow as JSON]} (long-poll;
                                             updates while a session is live, also during Drive)
  POST /api/robot/edit op "match_devices" {matches: {slot: device}} -> one write, one undo entry, all ids at once
  POST /api/robot/edit op "motor" {slot, neutral?: "brake"|"coast", invert?: bool}
  ```

  `match_devices` then restarts the live session if one is running.

- [ ] **Step 1: Write the failing tests:**
  - `test_devices_rows_from_live_session` (with the fake robot from `tests/fake_robot_main.py`, or the RobotPy
    kitbot);
  - `test_apply_swap_is_one_edit` (swap two ids: the file has both swapped, one Undo restores both, no
    duplicate-ID error in between);
  - `test_motor_neutral_and_invert_written`;
  - `test_check_refused_while_unpicked`.
- [ ] **Step 2: Run them; they fail.** **Step 3: Implement.** **Step 4: Run** the same command as Task 4; they pass.
- [ ] **Step 5: Commit** ("Editor: device check from the running robot, apply matches, brake/coast and invert").

### Task 6: Robot code panel: folder, checklist, start picture

**Files:**
- Create: `scrimmage/assembler/static/code.js`
- Modify:
  - `app.js`, `index.html`, `app.css`;
  - `fieldpick.js`: reuse its field drawing for the start picker;
  - `start.js`: the team-folder questions.
- Test: `tests/test_robot_code_browser.py`

**Interfaces:**
- Consumes: Task 4 routes.
- Behavior, from the approved mockups:
  - **The panel** has three numbered parts:
    1. **Your robot code**: Browse (through `/api/app/browse`) and the plain summary.
    2. **Ready to start?**: the checks with ✅ / ⚠️ / ℹ️ and fix buttons, live progress lines and the failure detail.
    3. **Where it starts**: "Not picked yet", or the sentence and a field drawing with the robot at its real size and a
       triangle for its heading.
  - **The start picker:** a field choice (2026 or Plain field), and quick choices for named starts. Click to place,
    drag to turn. "Exact position" holds the numbers. There is a "no balls on the floor" switch with its one-line
    reason.
  - **Buttons:** Start and check devices, and Drive. Both open the picker first while the start is unpicked.
  - **The start page** asks "Where is your team's CAD on this computer?" and the same for code, with Browse, when
    `needs_folder` is set, and offers both in a "This computer's team folders" section.

- [ ] **Step 1: Write the failing browser tests:** `test_pick_code_folder_shows_summary_and_checks`,
  `test_drive_asks_for_start_first`, `test_pick_start_shows_sentence_and_triangle` (the file gets `start_pose`), and
  `test_start_page_asks_for_unset_folder`.
- [ ] **Step 2: Run them; they fail.** **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q -m browser tests/test_robot_code_browser.py tests/test_assembler_browser.py tests/test_start_browser.py tests/test_drive_browser.py`;
  they pass.
- [ ] **Step 5: Commit** ("Robot tab: robot code panel, readiness checklist, start picked on the field").

### Task 7: Device table, motor controls, battery, contrast

**Files:**
- Modify: `code.js`, `inspector.js`, `app.css`
- Test: `tests/test_robot_code_browser.py`, `tests/test_assembler_browser.py`

**Interfaces:**
- Consumes: Task 5 routes.
- Behavior:
  - **The device table** has the columns Model part, Model expects, Your code made, and a status. Status shows as
    ✅ matches, ❌ bold "nothing with this ID" with a "Use …" choice, ➕ bold for an extra with "Add as…" (opening the
    part inspector's slot ids), ↔ for another type with a link to the part, and "not checked yet".
  - **Apply the matches and restart.**
  - **Brake/coast and invert** become controls on each motor slot in the inspector, with the reason line (Scrimmage
    can't read brake mode from the code).
  - **The Drive tab** shows a plain line when any device row has ❌ or ↔ ("2 devices don't match the model: see Robot
    code"), linking to the panel.
  - **Battery** fields under "More settings", with help.
  - **A contrast test** checks the panel, the table and the picker in light and dark themes (WCAG AA), by extending
    the existing contrast test's approach.

- [ ] **Step 1: Write the failing browser tests:** `test_device_table_shows_mismatch_and_applies` (a config with a
  wrong id against the RobotPy kitbot or the fake robot), `test_brake_control_writes_neutral`,
  `test_drive_tab_shows_device_mismatch_line`, and `test_panel_contrast_light_and_dark`.
- [ ] **Step 2: Run them; they fail.** **Step 3: Implement.** **Step 4: Run** the Task 6 command; they pass.
- [ ] **Step 5: Commit** ("Robot tab: device check table, brake/coast and invert, battery, readable in both themes").

### Task 8: Docs

**Files:**
- Modify:
  - `docs/getting-started/run-your-robot-code.md`: the page path first, the hand-edit as "Without the page".
  - `docs/getting-started/test-your-robot-code.md`: device notes in reports; the start must be picked on the page.
  - `docs/getting-started/install.md`: team folders.
  - `docs/assembler.md`: the Robot code panel, start picker, device check, motor controls.
  - `docs/drive.md`.
  - `README.md`: the Java robots section and the config keys `{cad}`/`{code}`, `SCRIMMAGE_CAD`/`SCRIMMAGE_CODE`.
  - `docs/usability-future-work.md`: item 2 done; the `--new` template's RobotPy-only `[robot_program]` item done.
- Create: a screenshot `docs/img/robot-code.png`, through a browser test that runs only when `SCRIMMAGE_ROBOT_CODE_SHOT`
  is set.
- Test: `tests/test_getting_started_docs.py` (links, commands, files)

- [ ] **Step 1: Write the docs and the screenshot test.**
- [ ] **Step 2: Run** `pytest -q tests/test_getting_started_docs.py`; it passes.
- [ ] **Step 3: Full suite:** `pytest -q --deselect tests/test_java_e2e.py`, then `pytest -q -m browser`. Report the
  counts. The FreeCAD tests may error while this machine's FreeCAD snap is broken: report them, don't fix them.
- [ ] **Step 4: Commit** ("Docs: point at the code and run from the Robot tab").
