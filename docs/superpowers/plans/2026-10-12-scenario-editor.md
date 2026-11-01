# Scenario Editor (sub-project 5b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `scrimmage edit FILE` opens a two-tab local web app (Robot = 5a's assembler, Scenario = new editor)
where a student edits a scenario through forms and runs it headless with a live progress view and report.

**Architecture:** Extend `scrimmage/assembler/`: shared tomlkit helpers (`tomlutil.py`), scenario edits,
scenario validation via a new `load_scenario_text`, a signals lister, a run manager that drives
`scenario_runner.run_scenario` (gaining `progress` and `cancel` hooks) on a worker thread, and server routes under
`/api/robot/*` and `/api/scenario/*`; front end gains tabs and scenario modules.

**Tech Stack:** Python 3.12, tomlkit, MuJoCo 3.15, asyncio, stdlib http.server; ES modules, three.js (vendored);
pytest, Playwright.

**Spec:** docs/superpowers/specs/2026-10-12-scenario-editor-design.md

## Global Constraints

- Everything from 5a still holds: 127.0.0.1 only; `X-Scrimmage-Token` on every `/api/*` (403 otherwise); Host guard; files always valid on disk (validate before write; atomic writes); edits/undo/redo require `version` (400 missing, 409 stale/external); error bodies `{ok: false, errors: [{field, message}]}`; no `innerHTML` for server strings; no CDN.
- No backward compatibility: 5a's `/api/state|view|library|edit|undo|redo|folder` are removed (404) in favor of `/api/robot/*`; `scrimmage assemble` is removed in favor of `scrimmage edit`.
- Scenario field ids: `scenario/<key>`, `step<i>/<key>`, `step<i>/check<j>/<key>`, `step<i>/shot<j>/<key>`, `step<i>/sheet/<key>`, or `status` (indices 0-based in field ids; messages may say 1-based as the runner does).
- `scrimmage run` behavior and output are unchanged when the new `progress`/`cancel` hooks are not passed.
- One run at a time; a run uses a snapshot of the scenario text taken at Run time.
- Run files are served only from that run's folder (path-guarded).
- Python is `.venv/bin/python`; tests `SCRIMMAGE_SKIP_JAVA=1 .venv/bin/python -m pytest`. Browser tests marked `browser` (5a's harness and Chromium lookup).

## Review Focus

1. Stopping a run must not leave the robot program running (kill by token) — assert no process with the run's token survives. (Task 4.)
2. Editing the scenario while a run is in progress must not change what that run executes. (Task 4.)
3. A check's error from the validator must land on that check's field (`step<i>/check<j>/...`), not only status. (Task 3.)
4. Moving or deleting a step must keep the step's comments with it and not orphan the next step's comments. (Task 2.)
5. A scenario whose robot config breaks while the editor is open becomes read-only with "fix the robot first", not a crash. (Task 5.)

---

### Task 1: `load_scenario_text`, runner hooks, fake-robot scenarios

**Files:** Modify `scrimmage/scenario.py`, `scrimmage/scenario_runner.py`; Create `tests/scenarios/fake_pass.toml`, `tests/scenarios/fake_fail.toml`; Test `tests/test_scenario.py`, `tests/test_runner.py`

**Interfaces:**
- `load_scenario_text(text: str, path: Path) -> Scenario` (all parsing; `load_scenario` reads the file and calls it; messages unchanged).
- `run_scenario(scenario, out_dir, *, speed=UNSET, session=None, progress=None, cancel=None)`: `progress(event: dict)` with events `{"type": "step_start", "index": i (0-based), "name", "mode", "duration"}`, `{"type": "tick", "t": sim_seconds}` at most every 0.25 s of sim time, `{"type": "console", "line": str}`, `{"type": "done", "passed": bool}`; `cancel` is a `threading.Event` checked between advances; when set, the session stops, the report gets `error = "stopped by the user"` and remaining steps are not run.
- Fake scenarios use `config = "../fake_robot.toml"` (relative to `tests/scenarios/`); `fake_pass.toml` has disabled 0.5 s + auto 1.0 s with a `console` check the fake robot satisfies (read `tests/fake_robot_main.py` for its prints) and an `at_end` battery check `min = 5`; `fake_fail.toml` the same with a `pose` check that cannot pass.

- [ ] Steps: failing tests (`load_scenario_text` relative config resolution; progress events order and tick spacing; cancel after the first step_start stops within 2 s and kills the robot process — use `robot_process.kill_by_token` semantics to assert none remain; `scrimmage run tests/scenarios/fake_pass.toml` passes and `fake_fail` fails, output format unchanged); implement; run; full suite; commit `"Scenario text loading; runner progress and cancel hooks"`.

---

### Task 2: Shared TOML helpers and scenario edits

**Files:** Create `scrimmage/assembler/tomlutil.py`, `scrimmage/assembler/scenario_edits.py`, `tests/test_scenario_edits.py`; Modify `scrimmage/assembler/edits.py` (import helpers from `tomlutil`)

**Interfaces (all pure on a tomlkit document; raise `edits.EditError(field, message)`):**
- `tomlutil`: the comment-aware insert/set helpers and comment-block handling from `edits.py` (`insert`, `set_value`, `tail_start`, drop/hand-off of comment blocks), generalized to any array-of-tables name.
- `scenario_edits.set_top(doc, key, value)` for `config`, `description`, `speed`, `require_lockstep` (`None` removes).
- `add_step(doc, after: int | None, name, mode, duration)`, `duplicate_step(doc, i)`, `delete_step(doc, i)`, `move_step(doc, i, j)`, `set_step(doc, i, fields: dict)` (name, mode, duration; `None` removes optional).
- `set_inputs(doc, i, kind, port, values)` (`kind` ∈ axes|buttons|povs; `values` list or `None`/empty removes the port; tables written inline as `axes = { 0 = [...] }` matching the runner's format — read `scenario.py` for the exact accepted shapes).
- `add_check(doc, i, kind, spec)`, `set_check(doc, i, j, spec)`, `delete_check(doc, i, j)` (a check is `[[step.check]]` with one key `kind = { ... }` inline).
- `add_shot / set_shot / delete_shot` (`[[step.screenshot]]`), `set_sheet(doc, i, spec | None)` (`[step.contact_sheet]`).
- Comments: a step's comment block (comments directly above its `[[step]]`) moves with it on `move_step` and goes with it on `delete_step` (Review Focus 4).

- [ ] Steps: failing tests on copies of `scenarios/kitbot_auto.toml`, `swerve_auto.toml`, `elevator_arm.toml` (every op; comment preservation and placement; move keeps comments; delete doesn't orphan; inline formats load with `load_scenario_text`); 5a's edit tests still pass after the helper move; implement; run; full suite; commit `"Scenario edits on shared tomlkit helpers"`.

---

### Task 3: Scenario validation, signals, world geoms in the view

**Files:** Create `scrimmage/assembler/scenario_validate.py`, `scrimmage/assembler/signals.py`, `tests/test_scenario_validate.py`; Modify `scrimmage/assembler/view.py`

**Interfaces:**
- `scenario_validate.check(text, path) -> Result(ok, errors: list[FieldError], warnings, world, scenario, robot_problem: str | None)`: `load_scenario_text` → load and `build_world` the config (failure → `robot_problem` set, `ok=False`, errors `[status: "fix the robot first: …"]`) → `validate(scenario, world)`. Map `ScenarioError` messages to field ids (read real messages in `scenario.py`/`checks.py`: `step N 'name': …`, `check N`, `screenshot N`, top-level keys); unmapped → `status`.
- `signals.signals(world) -> {"fixed": ["battery", "speed"], "motors": [...], "joints": [...], "contacts": [...], "cameras": [...]}` with motor names from `world.motors`, joints from `world.joint_states()`, contacts from `world.contact_names()`, model camera names.
- `view.model_view(world, include_world=True)` adds `world_geoms` (non-robot geoms: planes with their size extents, boxes, cylinders) for the field picker; robot output unchanged.

- [ ] Steps: failing tests (each mapping with a real broken scenario: bad mode, unknown signal, unknown contact, bad screenshot view, bad `at`, missing config file, invalid robot config; a pose check error lands on `step1/check0/pose`; signals for `configs/kitbot.toml` contain `motor.dt.left`… (check the real binding names) and `joint.dt.left_2`; world geoms include `carpet` and `obstacle`); implement; run; commit `"Scenario validation with field mapping, signals, world geoms"`.

---

### Task 4: Run manager

**Files:** Create `scrimmage/assembler/runs.py`, `tests/test_scenario_runs.py`

**Interfaces:**
- `RunManager(on_change: Callable[[], None])`: `.start(scenario_path, text, speed=None) -> run_id` (409-style `RunBusy` if one is running): writes `text` (with `config` rewritten to an absolute path) into a new `runs/<stem>-<time>/scenario.toml` next to the scenario (same base folder as the CLI's default), starts a worker thread with its own event loop running `run_scenario(load_scenario(snapshot), out_dir, speed=..., progress=..., cancel=...)`; `.stop()`; `.runs(since: int) -> {version, runs: [status...]}` (newest first, last 10); `.file(run_id, name) -> Path | None` (only inside that run's folder).
- Status: `{id, state: "running"|"passed"|"failed"|"error"|"stopped", step, step_name, t, total_t, console: [last 200 lines], report: <Report.to_json()> | null, out_dir}`; `on_change()` called on every progress event and state change (the server bumps its long-poll version).

- [ ] Steps: failing tests with `tests/scenarios/fake_pass.toml` / `fake_fail.toml` (pass and fail states; report present; progress steps seen; stop mid-run → `stopped` and no process with the run's token remains; second start while running → `RunBusy`; editing the scenario file during a run doesn't change the run (Review Focus 2); `file()` path guard); implement; run; commit `"Scenario run manager"`.

---

### Task 5: Server, routes and `scrimmage edit`

**Files:** Modify `scrimmage/assembler/server.py`, `scrimmage/__main__.py`, `scrimmage/assembler/templates.py`, `scrimmage/assembler/static/app.js` (robot API paths only), `tests/test_assembler_server.py`, `tests/test_assembler_browser.py` (CLI and paths); Create `tests/test_scenario_server.py`

**Interfaces:**
- `App(path, ...)`: classifies `path` (scenario if top-level `config` or `step` key; else robot config); holds `robot: ConfigDoc` and `scenario: ConfigDoc | None`; switching the scenario's `config` (via `set_top`) reloads the robot doc.
- Routes (all token-guarded, documented in the module docstring): `/api/robot/{state,view,library,edit,undo,redo,folder}` (5a shapes); `GET /api/scenario/state?since=` (`{version, path, display_path, read_only, read_only_reason, robot_problem, top: {...}, steps: [{name, mode, duration, start_t, axes, buttons, povs, checks: [{kind, spec}], shots: [...], sheet}], errors, warnings, can_undo, can_redo}`), `POST /api/scenario/{edit,undo,redo}` (`op` ∈ top, add_step, duplicate_step, delete_step, move_step, set_step, inputs, add_check, set_check, delete_check, add_shot, set_shot, delete_shot, sheet), `GET /api/scenario/signals`, `GET /api/scenario/view`, `POST /api/scenario/run {speed?, version}`, `POST /api/scenario/stop`, `GET /api/scenario/runs?since=`, `GET /api/scenario/run/<id>/<file>`. Old 5a paths → 404.
- A scenario whose robot config breaks (on open or after an outside change) → scenario read-only with `robot_problem` (Review Focus 5).
- CLI: `scrimmage edit FILE [--new-scenario --robot CONFIG] [--new swerve|differential] [--port N] [--no-browser]`; `assemble` removed; `templates.new_scenario(config_rel_path) -> str`.

- [ ] Steps: failing tests (classification; robot routes moved and old ones 404; scenario state shape on the shipped scenarios; scenario edit round trip + field errors; run/stop/runs/run-file through HTTP with `fake_pass.toml`; robot-broken read-only; `--new-scenario` refuses to overwrite and validates); update 5a tests and `app.js` paths; implement; run; full suite (browser tests included); commit `"Two-document server: robot and scenario routes; scrimmage edit"`.

---

### Task 6: Front end: tabs, scenario header, timeline, step editor

**Files:** Modify `static/index.html`, `app.js`, `app.css`; Create `static/scenario.js`, `static/steps.js`, `static/step_editor.js`

- Tabs (`#robot`, `#scenario`; the Scenario tab only when a scenario is open; "Open in Robot tab" switches).
- Header: description, robot config dropdown (configs found next to the scenario and in `configs/` — add `GET /api/scenario/configs` in this task if needed), speed (1× / number / max), require lockstep.
- Timeline: steps with name, mode chip (color per mode), duration, start time, to-scale bar, add/duplicate/delete, drag to reorder (and up/down buttons for keyboard users), check/screenshot icons, last-run dot.
- Step editor: name/mode/duration; inputs per port (axes slider + number, buttons toggles, POV compass or −1; "add port"); checks list with "Add check ▾" and a form per kind (pose: x, y, heading, tol_xy, tol_heading; odometry_error: its keys; at_end/always: signal text box + min/max/equals/approx/tol — the picker comes in Task 7; must/never_touch: text box; console: contains/not contains); screenshots (view, at, width, height, camera fields for free view) and contact sheet.
- Every change posts one edit with `version`; field errors render beside their field; unmapped → status.
- `window.__scrimmage.scenario` hook (state, selected step).
- [ ] Steps: implement; verify in a scratch headless Playwright script (5a's Chromium lookup): open `tests/scenarios/fake_pass.toml` copy, add a step, set mode/duration, set an axis, add a console check, see a field error for a bad mode; screenshots in the scratchpad (record paths in the report); commit `"Scenario tab: header, timeline, step editor"`.

---

### Task 7: Front end: field picker, signal picker, run panel and report

**Files:** Create `static/fieldpick.js`, `static/report.js`; Modify `static/step_editor.js`, `static/scenario.js`, `app.css`

- Field picker: 2D canvas from `/api/scenario/view` (`world_geoms` + robot footprint at start pose); click sets x, y; drag sets heading; tolerance circle and heading wedge; other pose checks drawn as ghosts; OK/Cancel writes one `set_check`.
- Signal picker for at_end/always (groups from `/api/scenario/signals`, plus free text for `hal:`/`nt:`); contact picker for must/never_touch; camera picker for `camera:<name>` views.
- Run panel: Run (speed override), Stop, progress (step i/N + name, sim t / total, console lines streaming), report (per step, check pass/fail with the runner's message; console tail; screenshot thumbnails with click-to-enlarge; contact sheets; report folder path), recent runs list; timeline dots from the latest run.
- [ ] Steps: implement; verify headless (pick a pose on the field; pick a signal; run `fake_fail` copy → failing check shown; fix → re-run → pass; stop a run); screenshots; commit `"Scenario tab: field and signal pickers, run panel, report"`.

---

### Task 8: Browser tests and docs

**Files:** Create `tests/test_scenario_browser.py`, `docs/scenario-editor.md`; Modify `README.md`, `docs/assembler.md`

- Browser tests (spec "Testing"): open a fake-robot scenario; add a step; set mode and duration; set an axis; add a pose check through the field picker; add an `at_end` check via the signal picker; run → progress and a failing check; fix → re-run → pass; stop a run (state `stopped`); open the Robot tab; README screenshot (opt-in like 5a, neutral path, < 300 KB) at `docs/img/scenario-editor.png`.
- Docs: `docs/scenario-editor.md` (walkthrough, pickers, running and reports, read-only cases, constant-per-step inputs); README section; every `scrimmage assemble` mention → `scrimmage edit`.
- [ ] Steps: implement; run (`-m browser` here); full suite; commit `"Scenario editor: browser tests and docs"`.
