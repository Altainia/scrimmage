# Testing a Mechanism with Blocks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Test tab where a CAD student snaps Scratch-style blocks into a test of their mechanism on a stand-in
robot, runs it with no robot code, watches an animated replay, compares runs and sweeps numbers.

**Architecture:** A new package `scrimmage/blocks/` holds the test file format (TOML), the conditions, the
interpreter that runs a block tree against a `World` one physics step at a time (writing motor controllers through
`HalState` like `climb run`), the run store (a self-contained replay per run) and sweeps. The assembler opens a
`*.test.toml` in robot mode with a new Test tab: Blockly (vendored) edits the blocks, the server validates and runs
them under the shared SimLock, and the run panel replays the stored motion with the existing three.js scene code.

**Tech Stack:** Python 3.11+, MuJoCo, the assembler's stdlib HTTP server and vanilla JS ES modules, three.js 0.185.1
(vendored), Blockly (vendored, new), Playwright for browser tests, the MCP server.

**Spec:** `docs/superpowers/specs/2026-11-02-blocks-test-design.md` (approved 2026-11-02).

## Global Constraints

- Plain words in everything a student reads (high-school CAD students); American spellings.
- No robot program runs during a test; motors are driven through the config's own motor bindings via `HalState`.
- The same physics as the whole-robot simulation: no test-only shortcuts.
- Robot and part formats don't change incompatibly; new keys are optional (`chassis.com_offset`, bar `section`).
- Front-end libraries are vendored under `scrimmage/assembler/static/vendor/` with a README entry (version, source
  URL, sha256, license), a release at least two weeks old; no CDN at runtime, no build step.
- Windows is first-class: paths via `pathlib`, file replaces via `scrimmage.cad.settings.replace_file`, the Windows CI
  job runs the new non-browser tests.
- Every server or browser test sets `SCRIMMAGE_RECENT` and `SCRIMMAGE_CACHE` to temp paths.
- The app's light/dark contrast test (tests/test_assembler_browser.py) passes with the Test tab shown.
- Python package name is `scrimmage.blocks` (not `scrimmage.test`, which would shadow the stdlib `test`); the CLI
  command is `scrimmage test`.

## Planning decisions

- **The test file** (`<name>.test.toml`) has a top-level `[test]` table (so `classify` can't mistake it for a
  scenario, whose marker is `config` or `[[step]]`) and `[[block]]` arrays, nested through `body` arrays of tables.
  The exact format is Task 3's.
- **Blocks persist across steps like climb drives:** `spin` holds until a later block changes that motor; `move`
  holds its position target and waits until reached; `drive` waits until its distance or time is done, then stops.
- **"Next bar"** is the lowest bar whose top is higher than every bar the robot has hung from so far in this run.
  **Hanging from a bar** is the robot's hooks carrying at least a quarter of its weight from that bar for 0.1 s.
- **Replay** stores, per run, `view.json` (`model_view(world, include_world=True)` at the run's start, meshes
  included) and `motion.bin` (mover poses at 30 Hz). That is self-contained, so a run from before Import again
  replays with its old geometry without keeping MuJoCo models. Signals are sampled at 50 Hz into `signals.csv`.
- **Runs are kept** in `<test stem>-runs/` next to the test file: the newest 10 runs, and the newest 3 sweeps.
- **Tipped over** is the robot body's up axis more than 60° from vertical (spec, my call).
- **A runaway repeat** ends at `max` iterations (default 20) and the whole run at `time_limit` (default 60 s).
- **The climb runner's motor writing** moves into one shared module both runners use (no duplicate).
- **Hooks on CAD parts:** `ClimbMonitor` takes optional hook bodies; the stock climber's hooks still count.

## Review Focus

1. A test written before its robot changed (a part renamed or removed on Import again) names joints or bodies that
   no longer exist: each such block shows a plain problem ("no joint climber.crank on this robot any more") and Run
   is refused until it's fixed; nothing crashes. Test in Task 4.
2. A `repeat until` whose condition never comes true: the run ends at the repeat's `max` or the time limit with a
   sentence that names the repeat, not a hang. Test in Task 5.
3. Run pressed while a drive session or a scenario run holds the simulation: a plain "busy" message, and the test
   runs fine once it's free. Test in Task 8.
4. A hand-edited test file with bad values (an unknown `do`, a negative time, a condition with two kinds, a missing
   `robot` file): one plain error naming the block's position ("block 3, inside repeat 2"). Test in Task 3.
5. The part re-imported while an old run is open in the replay: the old replay keeps working with its old geometry,
   and a new run uses the new geometry. Test in Task 10.

---

### Task 1: Shared motor drive, a stand-in box robot

**Files:**
- Create: `scrimmage/hal_drive.py`
- Modify: `scrimmage/climb/run.py` (use it; remove `_Motor`, `KP_SLIDE`, `KP_HINGE` there),
  `scrimmage/parts/stock/chassis.py` (optional `com_offset`), `scrimmage/assembler/templates.py`
  (`"standin"` template), `docs/parts.md` (chassis `com_offset`)
- Test: `tests/test_hal_drive.py`, additions to `tests/test_parts_stock.py` (or the file that tests chassis), the
  existing `tests/test_climb_*.py` must still pass unchanged

**Interfaces:**
- Produces:
  - `class MotorDrive(binding)` with `write(hal: HalState, volts: float, bus: float) -> None` (the old `_Motor`).
  - `KP_SLIDE = 400.0` (V/m), `KP_HINGE = 60.0` (V/rad), and
    `position_volts(target: float, q: float, hinge: bool, cap: float) -> float` (proportional, clipped to ±cap).
  - `driven_joints(world) -> dict[str, MotorBinding]`: joint name → its motor binding (the mapping `ClimbRun`
    builds today from `world.motors` by qpos address).
  - Chassis param `com_offset` (m, list of 3, default `[0, 0, 0]`): added to the body's `com` (forward, left, up).
  - `templates.new_config("standin")`: a robot named "Stand-in box robot": the kitbot's chassis and differential
    drive from the `differential` template, chassis `mass = 45.0` and `com_offset = [0, 0, 0]`, with a comment
    saying a part attaches to `drive.top` or where its CAD has it. `KINDS` includes `"standin"`.

- [ ] **Step 1: Write the failing tests:** `position_volts(1.0, 0.0, False, 12) == 12` (clipped) and
  `position_volts(0.01, 0.0, False, 12) == pytest.approx(4.0)`; `position_volts(0.1, 0.0, True, 12) ==
  pytest.approx(6.0)`; `driven_joints` of `configs/climber_robot.toml`'s world contains `"mid.stage1"` and
  `"high.pivot"`; a chassis with `com_offset = [0.1, 0, 0.05]` has body com `[0.1, 0, frame_height + 0.11]`; the
  `standin` template parses and assembles (World builds) with no errors.
- [ ] **Step 2:** Run them (FAIL).
- [ ] **Step 3:** Implement; switch `ClimbRun` to `hal_drive`.
- [ ] **Step 4:** Run the new tests and `pytest tests/test_climb_*.py -q` (PASS, unchanged results).
- [ ] **Step 5:** Commit `blocks: shared motor drive, a stand-in box robot`.

---

### Task 2: Hooks on any body, square rungs, a ladder range

**Files:**
- Modify: `scrimmage/terrain/climb.py` (`ClimbMonitor(world, hook_bodies=None)`), `scrimmage/world.py`
  (`climb_monitor(hook_bodies=None)`), `scrimmage/testrange/elements.py` (bar `section`), `docs/terrain.md`,
  `docs/climbing.md`
- Create: `scrimmage/testrange/ranges/ladder.toml`
- Test: `tests/test_climb_hooks_any_body.py`, additions to the elements tests

**Interfaces:**
- Produces:
  - `ClimbMonitor(world, hook_bodies: list[str] | None = None)`: each named robot body becomes a hook named after
    the body (all its colliding geoms), counted with the stock climber's regex hooks, and part of the hook
    assembly (allowed to touch bars). `World.climb_monitor(hook_bodies=None)` rebuilds the monitor when called with
    a different list than the cached one. An unknown body raises `ValueError` naming it and listing robot bodies.
  - Bar key `section = "round" | "square"` (default `"round"`); square is a box of side `diameter`, with the same
    top height rule (axis plus half the side). Holds and climb signals treat it like a round bar.
  - Range `ladder`: name "Generic ladder (a stand-in climb)", four rungs `rung1`..`rung4` (round, 1.66 in OD,
    30 in long), tops at 24, 36, 48, 60 in, in one vertical plane 1.0 m in front of the start, side rails as two
    boxes, no `[climb]` table, no floor balls; and `ladder-square`: the same with `section = "square"` (1.5 in).
    Every value carries a `# choice:` comment (it is not a game's field).

- [ ] **Step 1: Write the failing tests:** a world on `ladder` with a hand-written part whose body `paw` is a box
  pressed onto `rung1` reports `paw` in `ClimbMonitor(world, ["arm.paw"]).hooks` and hook contact on `rung1`; the
  same body without `hook_bodies` hitting the rung isn't a hook; `ladder-square` builds and its rung's top is 24 in;
  an unknown hook body names itself in the error.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS) plus `pytest tests/test_climb_*.py tests/test_terrain*.py -q`.
- [ ] **Step 5:** Commit `climb: hooks on any body, square rungs, a ladder range`.

---

### Task 3: The test file format

**Files:**
- Create: `scrimmage/blocks/__init__.py`, `scrimmage/blocks/program.py`
- Test: `tests/test_blocks_program.py`

**Interfaces:**
- Produces (all in `scrimmage/blocks/program.py`):
  - `class TestFileError(ValueError)`: messages in plain words naming the block's place (`block 3`,
    `block 2 inside block 1 (repeat)`).
  - Dataclasses: `Program(path: Path, robot: Path, hooks: list[str], time_limit: float, chart: list[str] | None,
    blocks: list[Block], setup: dict)` and `Block(do: str, args: dict, body: list[Block])`; `Cond(kind: str,
    args: dict)` lives in `Block.args["until"]` / `["that"]`.
  - `load(path) -> Program`, `loads(text: str, path: Path) -> Program`, `dumps(program) -> str` (round-trips; keeps
    the `[test]` keys first; writes nested `body` as `[[block.body]]` tables), `to_json(program) -> dict`,
    `from_json(d: dict, path: Path) -> Program` (the browser's shape: `{"robot", "hooks", "time_limit", "chart",
    "blocks": [{"do", ...args, "body": [...]}]}`).
  - `DO_KINDS`, `COND_KINDS`, and the defaults below as module constants.
- The format (exact):

```toml
[test]
robot = "climber_box.toml"          # required: a robot config, relative to this file
hooks = ["climber.hook_A"]          # optional: robot bodies that count as hooks
time_limit = 60.0                   # optional, s (default TIME_LIMIT = 60)
chart = ["tilt", "climber.crank"]   # optional: signals shown by default

[[block]]
do = "spin"                         # motor = "<part>.<joint>", volts (-12..12)
motor = "climber.crank"
volts = 6.0
```

  - `do` kinds and their keys: `spin` {motor, volts}; `move` {joint, to, unit = "deg" | "in" | "m" (default deg
    for a hinge, in for a slide is chosen by the editor; the file always says), timeout (default MOVE_TIMEOUT = 5)};
    `drive` {distance (m, negative backs up) or seconds, volts (default DRIVE_VOLTS = 4)}; `stop` {motor, or
    `"all"`}; `wait` {seconds}; `wait_until` {until, timeout (default WAIT_TIMEOUT = 10)}; `repeat` {times or until,
    max (default REPEAT_MAX = 20, only with until), body}; `expect` {that, throughout (default false)}.
  - Condition kinds (exactly one key per condition table): `joint = {name, above | below, unit}`;
    `touch = {robot = "<body>" | "<part>" | "any hook", field = "<element or piece>" | "any bar" | "next bar"}`;
    `tilt = {above | below}` (degrees); `piece_in = {body, piece = "any" | "<piece name>"}`;
    `hanging_from = "<bar>" | "next bar"`; `level = "<level>"`.
  - Validation here is only of the file's shape (kinds, types, ranges: times ≥ 0, `times` a whole number 1..100,
    volts within ±12, nesting at most 4 deep); names are checked against a robot in Task 4.

- [ ] **Step 1: Write the failing tests:** a sample covering every `do` and condition kind round-trips through
  `dumps(loads(...))` and `from_json(to_json(...))` to an equal `Program`; nested repeats write `[[block.body]]`
  and `[[block.body.body]]`; each bad input (Review Focus 4) raises `TestFileError` whose text includes the
  block's place: unknown `do = "fly"` → `block 3: "fly" isn't a block`; `wait = {seconds = -1}` → `can't be
  negative`; `until = {tilt = {above = 10}, joint = {...}}` → `one condition per block`; missing `[test] robot` →
  `says which robot to test`.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS).
- [ ] **Step 5:** Commit `blocks: the test file format`.

---

### Task 4: What a robot offers, and problems per block

**Files:**
- Create: `scrimmage/blocks/palette.py`, `scrimmage/blocks/conditions.py`
- Test: `tests/test_blocks_palette.py`, `tests/test_blocks_conditions.py`

**Interfaces:**
- Consumes: `Program`, `Block`, `Cond` (Task 3); `hal_drive.driven_joints` (Task 1); `ClimbMonitor(world,
  hook_bodies)` (Task 2); `World.joint_states`, `World.robot_contacts`, `World.contact_names`, `World.parts`.
- Produces:
  - `palette(world, hooks: list[str]) -> dict`: `{"motors": [{"name", "unit": "deg"|"in", "kind": "hinge"|"slide"
    |"wheel"}], "joints": [...same...], "bodies": [...], "parts": [...], "hooks": [...], "bars": [...],
    "elements": [...], "pieces": [...], "levels": [...], "drive": bool, "signals": [...]}`, all as the world names
    them, sorted. `drive` is true when the robot has drive-wheel motors (geoms marked `tread`, as assemble.py does).
    `signals`: `"tilt"`, each motor joint, `"<joint>.current"`, and `"hook_load"` when there are hooks.
  - `problems(program, world) -> list[dict]`: `{"place": [i, j, ...] (indices through bodies), "message": str}`,
    plain words, one per bad name: `no joint "climber.crank" on this robot any more`, `"climber.arm" has no motor
    to move it`, `drive needs a drivetrain; this robot has none`, `no bar "rung5" on this field`, `levels need a
    field that scores climbs`. Empty when the program can run.
  - `class Watch(world, program)` in `conditions.py`: per physics step `update()`, then `holds(cond: Cond) -> bool`;
    tracks hang state per bar (hooks carrying ≥ a quarter of the robot's weight for 0.1 s), the bars hung from so
    far (for `next bar`), tilt (degrees between the robot body's z axis and world up), piece inside a body (a game
    piece's center inside the axis-aligned box of that body's geoms, in the body's frame).
  - `describe(cond) -> str`: plain words used in outcomes: `a hook touches rung2`, `the robot tilts more than 20°`,
    `climber.arm passes 40°`, `a ball reaches intake.hopper`, `the robot hangs from rung4`.

- [ ] **Step 1: Write the failing tests:** palette of the `standin` robot plus the climber stock part lists its
  motors with units and `drive = true`; Review Focus 1: a program naming `climber.crank` on a robot without it
  gives exactly one problem at place `[2, 0]` with the "any more" message; `Watch` on a world posed tilted 30°
  (set the free joint's quaternion) reports `tilt above 20` true and `below 20` false; `next bar` resolves to
  `rung1` before any hang and `rung2` after the robot has hung from `rung1` (drive the state by stepping a
  hand-made world hanging a box from `rung1`); `describe` gives the texts above.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS).
- [ ] **Step 5:** Commit `blocks: what a robot offers, and problems per block`.

---

### Task 5: Running a test

**Files:**
- Create: `scrimmage/blocks/run.py`
- Modify: `scrimmage/__main__.py` (`scrimmage test run <file> [--out DIR]`)
- Test: `tests/test_blocks_run.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `class TestRun(world, program)`: refuses (`TestFileError`) when `problems()` isn't empty, listing them;
    `run(on_sample=None, cancel: threading.Event | None = None) -> RunResult`. It settles the robot 0.3 s, then
    each physics step advances the block interpreter (a stack of (block list, index, repeat count) frames), writes
    every held motor command through `MotorDrive`, steps the world, updates the `Watch` and checks `expect ...
    throughout` conditions.
  - `RunResult(status: "finished" | "check failed" | "timed out" | "tipped over" | "walked off" | "time limit" |
    "repeat limit" | "cancelled", outcome: str, t_end: float, checks: list[dict], hung: list[str], signals:
    dict[str, list[float]] (with "t"), motion: list[tuple[float, np.ndarray]], movers: list[str], view: dict)`;
    `view` is `assembler.view.model_view(world, include_world=True)` taken after the settle, before the first
    block, so a replay shows the geometry that ran.
  - `outcome` is one sentence: an optional progress part (`reached rung3` from the highest bar hung from) and the
    ending: `finished`, `a check failed: the robot tilts more than 20° at 6.1 s`, `then waiting for a hook to touch
    rung4 timed out at 8.2 s (block 3 inside block 2)`, `the robot tipped over at 4.0 s`, `a hook walked off rung2 at
    5.5 s` (from the climb monitor's walk-off rule), `ran out of time (60 s)`, `repeat (block 2) stopped after 20
    times without the robot hanging from rung4`.
  - `motion` samples mover poses (`movers.mover_poses`) at `MOTION_HZ = 30`; signals at `SIGNAL_HZ = 50`.
  - CLI prints the outcome and each check's line; writes the run folder (Task 6) to `--out` (default the test's
    `<stem>-runs/`); exit 0 when status is `finished` and every check passed, 1 otherwise, 2 for a file or robot
    error (its plain message).

- [ ] **Step 1: Write the failing tests:** on the `standin` robot with a stock `arm` part: `move arm to 45 deg`
  then `expect joint above 40` finishes; the joint signal ends within 2° of 45; Review Focus 2: `repeat until
  {hanging_from = "rung4"}` with `max = 3` around `wait 0.1` ends `repeat limit` and the outcome names block 1
  and "3 times"; a `wait_until` that can't happen with `timeout = 0.5` ends `timed out` at about 0.8 s (0.3 s
  settle plus the wait); a robot posed tilted 70° ends `tipped over`; `time_limit = 1` ends `time limit`; the CLI
  returns 0, 1 and 2 for those cases.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS).
- [ ] **Step 5:** Commit `blocks: run a test with no robot program`.

---

### Task 6: Kept runs, replay files and sweeps

**Files:**
- Create: `scrimmage/blocks/store.py`, `scrimmage/blocks/sweep.py`
- Test: `tests/test_blocks_store.py`, `tests/test_blocks_sweep.py`

**Interfaces:**
- Consumes: `TestRun`, `RunResult` (Task 5), `assembler.view.model_view`.
- Produces:
  - `save_run(result, program, base: Path, sweep: dict | None = None) -> Path`: a new folder `base/<NNN>/` (3
    digits, increasing) with `result.json` (status, outcome, t_end, checks, hung, started (ISO time), the test as
    TOML text, and `sweep` when part of one), `signals.csv`, `view.json` (`result.view`), and
    `motion.bin`: header `struct "<4sHII"` = `b"SCRM"`, version 1, frames, movers; then per frame `<d` t and
    movers × 7 float32 (x, y, z, qw, qx, qy, qz). Keeps the newest `KEEP_RUNS = 10` plain runs; writes via a temp
    folder and `replace_file`-style rename.
  - `list_runs(base) -> list[dict]` (newest first: id, status, outcome, started, sweep), `load_run(base, id)`,
    `read_motion(path) -> (movers, times, poses)`.
  - `SweepTarget` kinds: `{"block": [place...], "key": "volts" | "seconds" | "to" | "distance"}`,
    `{"robot": "mass" | "com_forward" | "com_left" | "com_up"}` (the stand-in chassis' `mass` / `com_offset`),
    `{"mount": "<part>", "axis": "x" | "y" | "z" | "yaw"}` (the config `[[part]]` `pos` / `rot`).
  - `run_sweep(program, target, values: list[float], base, on_progress=None, cancel=None) -> Path`: at most
    `MAX_SWEEP = 12` values; for each, a modified copy of the config (written to a temp file next to the original so
    relative paths hold) or program, a `TestRun`, `save_run` into `base/sweep-<NNN>/<k>/`; then `table.csv` (value,
    status, outcome, t_end, each check) and `sweep.json` (target, values). Keeps the newest `KEEP_SWEEPS = 3`. A
    value that can't build ("mass must be positive") is a row with that message, not an abort.

- [ ] **Step 1: Write the failing tests:** `save_run` then `load_run` returns the same outcome; `read_motion` returns
  frames at about 30 Hz with the right mover count; the 11th run deletes the oldest; `view.json` holds the
  robot's geoms and the field's; a sweep of chassis `mass` over `[30, 45, 60]` writes three rows and `table.csv`;
  a sweep of a `move` block's `to` writes the right values into each run's saved test text; a value of `-5` for
  mass is a row with a plain message; Review Focus 5 (server half): after the part folder's meshes are replaced,
  `load_run` of an older run still returns its own `view.json` meshes.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS).
- [ ] **Step 5:** Commit `blocks: kept runs, replay files and sweeps`.

---

### Task 7: The MCP tools

**Files:**
- Modify: `scrimmage/mcp_server.py` (tools and the server instructions), `.mcp.json` instructions if they list tools
- Test: additions to the MCP tests (`tests/test_mcp*.py`)

**Interfaces:**
- Produces: `test_run(path: str) -> dict` (status, outcome, checks, run folder, and per signal min / max / final
  value) and `test_sweep(path: str, target: dict, values: list[float]) -> dict` (the rows of `table.csv`). Absolute
  paths, plain errors as the bench tools give them.

- [ ] **Step 1–4:** Failing tests for both tools on the Task 5 sample test, run, implement, run (PASS).
- [ ] **Step 5:** Commit `mcp: test_run and test_sweep`.

---

### Task 8: The server side of the Test tab

**Files:**
- Create: `scrimmage/assembler/test_session.py`
- Modify: `scrimmage/assembler/server.py` (classify "test", robot mode with a test, `/api/test/*` routes, app
  state), `scrimmage/assembler/simlock.py` (owner `"test"`, message "a test run is in progress"),
  `scrimmage/assembler/server.py`'s recent list (kind "test")
- Test: `tests/test_test_server.py`

**Interfaces:**
- Consumes: Tasks 3–6.
- Produces:
  - `classify(path)` returns `"test"` for a file whose TOML has a `[test]` table (checked before the scenario rule;
    for unparseable text, a `[test]` header line).
  - Opening a test opens its `robot` in robot mode with a `TestSession(test_path, app)` attached; `/api/app/state`
    gains `test: "<path>" | null`. Opening another robot or file closes it.
  - `TestSession`: holds the `Program`, a version counter for long polls, the current problems, the run/sweep
    thread. Routes (POST unless noted, JSON, the app's token check):
    - `GET /api/test/state?since=<v>` (long poll): `{version, test: to_json(program), palette, problems, running:
      null | {"kind": "run" | "sweep", "progress": 0..1}, runs: list_runs(), sweeps: [...]}`;
    - `/api/test/save {test, version}`: replace the program (from_json), validate, write the file with
      `replace_file`; a stale version is a 409 with the current state; a shape error is a 400 with its message;
    - `/api/test/run`: refused with 409 and the problems when there are any; else acquires SimLock `"test"` (a busy
      lock gives 409 `{"error": "Wait for the drive session to stop first."}` in the lock's words), runs on a
      thread `"test-run"`, saves the run, bumps the version;
    - `/api/test/sweep {target, values}` likewise on `"test-sweep"`; `/api/test/cancel`;
    - `GET /api/test/run/<id>/view` (view.json) and `GET /api/test/run/<id>/motion` (motion.bin, octet-stream),
      `GET /api/test/run/<id>` (result.json plus signals as JSON);
    - `/api/test/new {robot?}`: write `<robot stem>.test.toml` next to the robot (a free name if taken) with
      starter blocks (Task 11) and open it.
  - The robot config changing on disk (Import again, an assembler edit) re-reads palette and problems on the next
    state.

- [ ] **Step 1: Write the failing tests:** classify a `.test.toml` → "test" and a scenario still → "scenario";
  open a test, `GET state` returns palette and no problems; save a bad shape → 400 with the block's place; save
  with a stale version → 409; Review Focus 3: with SimLock held by `"live"`, run → 409 naming the drive session,
  and after release the run succeeds and `runs` has one entry; `GET .../motion` bytes start with `SCRM`; a test
  whose robot loses a joint (rewrite the config) shows the problem on the next state.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS) plus `pytest tests/test_assembler_server.py -q`.
- [ ] **Step 5:** Commit `edit: Test tab server side`.

---

### Task 9: The block editor

**Files:**
- Create: `scrimmage/assembler/static/vendor/blockly.min.js` (+ its license, README entry),
  `scrimmage/assembler/static/blocks_defs.js` (block definitions, test JSON ↔ workspace),
  `scrimmage/assembler/static/test.js` (the tab: layout, palette, editor, problems, save)
- Modify: `index.html` (Test tab button and panel), `app.js` (`TABS` gains `"test"`, shown when state has a test),
  `app.css` (the three columns, block colors by category in both themes)
- Test: `tests/test_test_browser.py`

**Interfaces:**
- Consumes: Task 8's routes and JSON shapes.
- Produces:
  - Blockly's UMD build loaded as a classic script (`window.Blockly`), the newest release at least two weeks old;
    a Blockly theme per app theme from the app's CSS tokens.
  - One Blockly block type per `do` kind and per condition kind (conditions are value-shaped blocks that plug into
    `wait until`, `repeat until` and `expect`), wording as in the mockup: `spin [motor] at [6] V`, `move [joint] to
    [40]°`, `drive forward [0.3] m`, `wait [1] s`, `wait until [cond]`, `repeat [4] times`, `repeat until [cond]`,
    `expect [cond]` with a "the whole time" checkbox; condition blocks `[hook] touches [rung]`, `tilt above/below
    [20]°`, `[joint] above/below [40]°`, `[piece] in [body]`, `hanging from [bar]`, `holding level [level]`.
    Dropdowns are filled from `palette` (never free text for names). Categories: Move (blue), Wait (orange),
    Repeat (green), Check (purple), matching the mockup.
  - `testToWorkspace(test, ws)` and `workspaceToTest(ws) -> test JSON` in `blocks_defs.js` (round-trip exact).
  - Saving: every change saves (debounced 500 ms) through `/api/test/save` with the version; a 409 reloads the
    workspace from the server state and shows "The test changed on disk; showing the saved version."
  - Problems from the state appear as a warning on the block at `place` (Blockly's warning text) and as a list
    above Run; Run is disabled while there are problems.
  - Window hook for tests: `window.__scrimmageTest = { workspace, state, toJSON: () => workspaceToTest(ws) }`.

- [ ] **Step 1: Write the failing browser tests:** the Test tab opens on a sample test and shows its blocks; adding
  a `wait 2 s` block through the toolbox (drag from the flyout) saves it to the file; the motor dropdown lists the
  palette's motors; a test naming a missing joint shows the warning on that block and Run disabled; the contrast
  test passes in light and dark with the Test tab shown.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS).
- [ ] **Step 5:** Commit `edit: Test tab block editor`. Save screenshots (light, dark) for the review.

---

### Task 10: The run panel: replay, chart, compare

**Files:**
- Create: `scrimmage/assembler/static/test_run.js` (replay player and chart)
- Modify: `test.js`, `app.css`; reuse `scene3d.js` / `live3d.js` drawing (a view JSON plus mover poses) and
  `trackplot.js` for the chart where they fit
- Test: additions to `tests/test_test_browser.py`

**Interfaces:**
- Consumes: Task 8's run routes; Task 6's `motion.bin` layout.
- Produces:
  - Run button; while running, progress and Cancel; when done, the newest run is shown.
  - Replay: the run's own `view.json` drawn in a 3D view; play / pause, speed (¼×, ½×, 1×, 2×), a scrubber with the
    time; the outcome sentence above it.
  - Chart: the test's `chart` signals (default `tilt` and the driven joints), a signal picker, and a "compare with"
    picker of earlier runs (default: the previous run) drawn faded; the scrubber's time marked on the chart.
  - Runs list: the kept runs, newest first, each with its outcome; clicking one shows it.
  - Window hook: `window.__scrimmageTest.replay = { time, playing, runId }`.

- [ ] **Step 1: Write the failing browser tests:** Run on the sample test shows an outcome sentence and a replay
  whose scrubber moves when played; dragging the scrubber to the end moves the robot (a mover's drawn position
  changes); the chart shows two series after a second run; Review Focus 5: replace the part's mesh file and its
  config so the robot changes, run again, then click the first run: it still draws (its own geometry) and the new
  run draws the new geometry.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS).
- [ ] **Step 5:** Commit `edit: Test tab replay, chart and compare`. Save screenshots.

---

### Task 11: Setup, sweeps, starting a test with no friction

**Files:**
- Modify: `test.js`, `test_run.js`, `app.css`, `scrimmage/assembler/test_session.py` (starter blocks),
  `scrimmage/assembler/static/part.js` ("Test it" button), `start.js` ("Test a mechanism")
- Test: additions to `tests/test_test_browser.py`, `tests/test_test_server.py`

**Interfaces:**
- Consumes: Tasks 6, 8, 9, 10; the Part tab's built folders and their `import-origin.toml`.
- Produces:
  - **Setup panel** (collapsed by default, above the blocks): Hooks (a multi-pick of robot bodies, preselected with
    bodies whose names contain "hook" the first time a test is made), the stand-in robot's weight and CG offset
    (when the robot is a `standin` chassis), and each part's mount offset (x, y, z, yaw) — edits write the robot
    config through the assembler's existing edit path, so undo works there.
  - **Sweeps**: "Try values…" on any number in a block (Blockly context menu) or in the Setup panel opens a small
    form: from, to, steps (≤ 12) or a typed list; runs `/api/test/sweep`; shows the table (value, outcome, checks)
    and a chart of the run end time or a chosen signal's final value against the value; clicking a row replays
    that run.
  - **Starter blocks** for a new test: with a drivetrain and bars on the field: `drive forward 0.3 m`, then for the
    first motor `spin [motor] at 6 V`, `wait 2 s`, `stop all`; without: the motor lines only; with a hinge motor
    and no other: `move [joint] to 45°`.
  - **No-friction start:** the Part tab gets "Test it" (after a build): it makes (once) `<part>-standin.toml` from
    the `standin` template next to the import's parts folder with the built part(s) attached where the CAD has them
    (Task 14 of sub-project 18's placement rules), makes `<part>.test.toml` with starter blocks and hooks
    preselected, and opens the Test tab. The start page gets "Test a mechanism" (pick a robot config or a CAD file,
    which goes through the Part tab first).

- [ ] **Step 1: Write the failing tests:** server: `/api/test/new` on the stand-in robot writes starter blocks with
  `drive` first; the Part tab's "Test it" on the climber fixture writes the stand-in config with the part placed
  where its CAD is (compare to `import-origin.toml` origins) and opens the Test tab; browser: a sweep of a block's
  volts over 3 values shows 3 rows and clicking one shows its replay; changing the stand-in weight in Setup changes
  the config file.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS).
- [ ] **Step 5:** Commit `edit: Test tab setup, sweeps, and Test it from the Part tab`. Save screenshots.

---

### Task 12: The train-wheel ladder climber example

**Files:**
- Modify: `tests/make_cad_fixtures.py` (a `crank_climber.step`), commit `tests/data/cad/crank_climber.step`
- Create: `examples/blocks/ladder_climber/` (the import file, the built part folder, `climber-standin.toml`,
  `climb.test.toml`, a README of what it shows), `tests/test_blocks_climber.py`

**Interfaces:**
- Consumes: everything above; sub-project 18's import and build (`scrimmage.cad`).
- The mechanism (millimeters, a choice, tuned in this task): a base plate; one crank body (a disc pair on one
  shaft, horizontal axis across the robot) driven by one motor slot `crank` (a NEO, a gear ratio chosen so 6 V
  turns it about 0.5 rev/s); two crank pins 180° apart; per side a connecting rod from the crank pin to an arm on
  a vertical slide (a slider-crank, so the arms rise and fall in turn); on top of each arm a hook on a spring hinge
  that folds up past a rung and snaps back to catch it. The import file sets bodies, the crank hinge, the slides,
  the rod hinges and loop pins, the hook hinges with springs and stops, and the motor slot, the way a student would
  in the Part tab (through `scrimmage.cad.edits.apply_edit`, not by hand-writing part.toml).
- `climb.test.toml` on `ladder`: hooks are the two hook bodies; `drive forward` until a hook touches `rung1`;
  `repeat until hanging from rung4`: `spin crank at 6 V`, `wait until a hook touches next bar`, `wait 0.3 s`; `expect
  tilt below 30 the whole time`.
- **What the test gates** (spec): the import → build → stand-in → test → run → replay files pipeline; the run
  reaches at least `rung2` (`"rung2" in result.hung`); the outcome is a sentence, not an exception. Tuning (gear
  ratio, spring, hook shape, mount position, crank radius) may take two rounds; if the climb then still stops below
  rung4 for a physics reason, write the reason in the example README and docs and stop tuning (the spec forbids
  open-ended tuning).

- [ ] **Step 1:** Generate the fixture (`freecad.cmd tests/make_cad_fixtures.py`, see its docstring for the snap
  paths) and build the example's part folder through `scrimmage.cad`.
- [ ] **Step 2: Write the test** `test_crank_climber_reaches_rung2` (and the pipeline assertions above); run (FAIL
  until tuned).
- [ ] **Step 3:** Tune within the limit above; run (PASS).
- [ ] **Step 4:** Commit `examples: a train-wheel ladder climber tested with blocks`.

---

### Task 13: The ball intake example

**Files:**
- Create: `examples/blocks/intake/` (`intake-standin.toml`, `intake.test.toml`, README),
  `tests/test_blocks_intake.py`

**Interfaces:**
- Consumes: Tasks 1–6; a range with floor balls (`2026`, or a small new one if the start pose doesn't face a
  ball); the stock `roller` part plus a hopper body (a stock part's body, or a hand-written part folder with a
  bin), whichever gives "a ball reaches the hopper" with the least new code.
- The test: `spin intake.roller at 8 V`, `drive forward` toward a ball for up to 2 s, `wait until a ball is in
  <hopper body>` (timeout 5), `expect` it there at the end.

- [ ] **Step 1: Write the test** `test_intake_takes_a_ball`: status `finished`, the check passed, the ball's final
  position inside the hopper box; run (FAIL).
- [ ] **Step 2:** Build the example; run (PASS).
- [ ] **Step 3:** Commit `examples: a ball intake tested with blocks`.

---

### Task 14: Docs, CI, PR

**Files:**
- Create: `docs/test-tab.md` (style of `docs/part-tab.md`: starting a test, the three columns, every block and
  condition, hooks, replay and compare, sweeps, the stand-in robot, the command line and MCP tools, kept runs and
  where they live (suggest ignoring `*-runs/` in git), troubleshooting), `docs/getting-started/test-a-mechanism.md`
  (linked from that folder's README; covered by `tests/test_getting_started_docs.py`)
- Modify: `README.md` (a Test tab paragraph; Layout lists `scrimmage/blocks/`, `docs/test-tab.md`),
  `docs/climbing.md` (hooks on any body, square rungs, the ladder range), `docs/part-tab.md` ("Test it"),
  `.github/workflows/ci.yml` (the Windows job also runs `tests/test_blocks_*.py tests/test_test_server.py
  tests/test_hal_drive.py tests/test_climb_hooks_any_body.py`), the spec's "Decisions made during the build"
  (every planning decision above and every ruling made during the build, one plain sentence each)
- [ ] **Step 1:** Write the docs and the CI change. Run `pytest tests/test_getting_started_docs.py -v` (PASS), then
  the full suite `SCRIMMAGE_SKIP_JAVA=1 pytest -q` (PASS).
- [ ] **Step 2:** Commit `docs and CI for the Test tab`. Push the branch, open the PR, watch CI to green.
