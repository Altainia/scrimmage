# Scenario editor (sub-project 5b): design

## Context

Scenarios (`scenarios/*.toml`, loaded by `scrimmage/scenario.py`, run by `scrimmage/scenario_runner.py` and
`scrimmage run`) are a robot config plus a list of steps: DS mode, duration, joystick inputs held for the step
(axes, buttons, POVs per port), checks (`pose`, `odometry_error`, `at_end`, `always`, `must_touch`,
`never_touch`, `console`), screenshots and an optional contact sheet. Students write them in a text editor
today.

Sub-project 5a (merged) built `scrimmage assemble`: a local web app (`scrimmage/assembler/`, stdlib HTTP server
with a session token and Host guard, tomlkit documents with validate-before-write and atomic writes, undo/redo,
long-poll state, field-level errors, a no-build JS front end with vendored three.js).

Decisions made with the user:

- The editor also **runs** scenarios headless and shows the report on the page (live 3D watching and joystick
  driving stay for the later run-and-watch sub-project).
- **One app, two tabs:** `scrimmage edit FILE` opens a robot config (Robot tab) or a scenario (Scenario tab, with
  its robot one click away); `scrimmage assemble` is replaced by `edit`.

## Goals

1. A student opens or creates a scenario, edits steps, inputs, checks and screenshots through forms (pose targets
   picked on a top-down field, signals picked from the robot's real signals), with every edit validated by the
   real loader and validator before it is written.
2. Run the scenario from the page, watch progress, read the report (pass/fail with values, console tail,
   screenshots), fix and re-run.
3. Comments and layout survive edits; the file is always valid; outside edits are picked up.
4. The Robot tab and Scenario tab share one server and front-end shell.

## Non-goals

- Live 3D playback, joystick driving, MCP-like interactive control (run-and-watch, later).
- Time-varying inputs within a step (the scenario format holds inputs constant per step).
- Editing scenario files that use a hand-written `[model]` robot is allowed; only the Robot tab is read-only for
  those robots.
- Running more than one scenario at a time.

## Design

### Commands

- `scrimmage edit FILE [--new-scenario --robot CONFIG] [--new swerve|differential] [--port N] [--no-browser]`:
  FILE is classified by content — a top-level `config` key or a `[[step]]` array means a scenario; otherwise a
  robot config. `--new-scenario --robot CONFIG` writes a starter scenario (a 1 s disabled step, a 5 s auto step
  with a placeholder `console` check commented out) to FILE (refusing to overwrite) and opens it.
  `--new swerve|differential` keeps 5a's behavior for configs.
- `scrimmage assemble` is removed in favor of `scrimmage edit` (no backward compatibility needed); docs and
  tests move to `edit`.

### What the student sees (Scenario tab)

- **Header:** description; robot config (dropdown of configs in `configs/` and the scenario's directory, plus
  the current one; "Open in Robot tab"); speed (1×, a number, or `max`); require lockstep.
- **Timeline (left):** steps with name, mode chip (disabled/auto/teleop/test, distinct colors), duration and
  start time; a to-scale bar; add / duplicate / delete / drag-to-reorder; icons for checks and screenshots; a
  pass/fail dot per step from the latest run.
- **Step editor (center):** name, mode, duration; joystick inputs for a chosen port 0–5 (axes as slider + number
  in −1…1, buttons as toggles, POVs as a compass or −1), shown only for ports that have values plus "add port";
  checks list with "Add check ▾" and one form per kind:
  - `pose`: x, y, heading, `tol_xy`, `tol_heading`, with "Pick on field" (see below);
  - `odometry_error`: its keys from `checks.py`;
  - `at_end` / `always`: a signal picker (battery, speed, `motor.<name>.current|command`,
    `joint.<name>.position|velocity`, free text for `hal:` / `nt:`) and min / max / equals / approx+tol;
  - `must_touch` / `never_touch`: a picker of `World.contact_names()`;
  - `console`: contains / not-contains text.
  Screenshots (view: follow, top, free with camera fields, `camera:<name>` from the model's cameras; at: start,
  end or a time; width/height) and the optional contact sheet (frames, view, size).
- **Field picker:** a top-down 2D canvas of the robot's world (field geoms from the model view's world geoms,
  the robot footprint at its start pose); click to set x, y; drag to set heading; tolerances drawn as a circle and
  wedge; existing pose checks drawn as ghosts.
- **Run (right):** Run (optional speed override), Stop, live progress (step i/N with name, sim time / total,
  console lines as they arrive); then the report: per step, each check's pass/fail with the runner's own
  message, the console tail, screenshot thumbnails (click to enlarge) and contact sheets, the report folder path;
  a list of recent runs from this session.
- **Saving:** as in 5a — valid edits autosave; invalid edits are refused at the field with the validator's
  message; undo/redo; outside edits reload with a notice.
- A scenario whose robot config is invalid (or missing) is read-only with "fix the robot first" and a button to
  the Robot tab.

### Architecture

`scrimmage/assembler/` grows:

- `tomlutil.py` — the comment-aware insertion helpers moved out of 5a's `edits.py` (5a imports them back).
- `scenario_edits.py` — pure edits on a scenario tomlkit document: `set_top(key, value)` (config, description,
  speed, require_lockstep); `add_step(after, name, mode, duration)`, `duplicate_step(i)`, `delete_step(i)`,
  `move_step(i, j)`, `set_step(i, fields)`; `set_inputs(i, kind, port, values)` (`kind` ∈ axes, buttons, povs;
  empty removes the port); `add_check(i, kind, spec)`, `set_check(i, j, spec)`, `delete_check(i, j)`;
  `add_shot(i, spec)`, `set_shot(i, j, spec)`, `delete_shot(i, j)`, `set_sheet(i, spec | None)`. Steps are
  addressed by index; `EditError(field, message)` with fields `scenario/<key>`, `step<i>/<key>`,
  `step<i>/check<j>/<key>`, `step<i>/shot<j>/<key>`.
- `scenario_validate.py` — `check(text, path) -> Result(ok, errors, warnings, world, scenario)`: a new
  `load_scenario_text(text, path)` in `scrimmage/scenario.py` (the same split 5a made for configs), then
  `build_world(load_config(scenario.config_path))` and `validate(scenario, world)`. `ScenarioError` messages
  (`step 2 'auto': check 1: …`, top-level keys) are mapped to the field ids above; unmapped → `status`.
- `signals.py` — `signals(world) -> {fixed: [battery, speed], motors: [...], joints: [...], contacts: [...],
  cameras: [...]}` from the World (motor binding names, joint names, `contact_names()`, model camera names).
- `runs.py` — `RunManager`: one run at a time on a worker thread with its own asyncio loop calling
  `scenario_runner.run_scenario(load_scenario(path_snapshot), out_dir, speed=..., progress=cb, cancel=event)`;
  run ids; status `{id, state: running|passed|failed|error|stopped, step, step_name, sim_t, total_t,
  console: [...last N], report: {...} | null, out_dir}`; the scenario text is snapshotted at Run time (written to
  the run folder and run from there, with `config` made absolute).
- `scrimmage/scenario_runner.py` additions: optional `progress(event: dict)` callback (events: step_start,
  tick with sim time every ~0.25 s of sim time, console line, done) and optional `cancel: threading.Event`
  checked between advances; on cancel the session stops cleanly (robot program killed by token) and the report
  marks the run stopped. Defaults keep `scrimmage run` unchanged.
- `server.py` — `App` holds a robot document and optionally a scenario document; routes:
  - Robot (5a shapes) moved under `/api/robot/*` (`state`, `view`, `library`, `edit`, `undo`, `redo`, `folder`);
    the old 5a paths are removed (no backward compatibility needed), and the front end and tests move to the new
    paths;
  - `GET /api/scenario/state?since=N`, `POST /api/scenario/edit|undo|redo` (same version, 409 and error rules
    as 5a), `GET /api/scenario/signals`, `GET /api/scenario/view` (the robot's model view including world geoms
    for the field picker), `POST /api/scenario/run {speed?}`, `POST /api/scenario/stop`,
    `GET /api/scenario/runs?since=N` (long-poll), `GET /api/scenario/run/<id>/<file>` (files only from that run's
    folder, path-guarded).
  - Switching the Scenario's robot (header dropdown) loads that config into the Robot tab too.
- `view.py` — gains world (non-robot) geoms for the field picker (planes as their extents, boxes, cylinders).

Front end (`static/`): `app.js` gains tabs and routing (`#robot`, `#scenario`); new `scenario.js` (state,
header, run panel), `steps.js` (timeline), `step_editor.js` (inputs, checks, screenshots), `fieldpick.js`
(2D canvas), `report.js`. Reuses 5a's `h()`, field-error display, notices and `window.__scrimmage` hooks
(`scenario`, `runs`).

## Failure handling

| Situation | Behavior |
|---|---|
| Edit makes the scenario invalid | not written; field-level message (or status bar) |
| Scenario's robot config invalid or missing | scenario read-only with "fix the robot first" + Robot tab button |
| Unparsable scenario TOML | read-only with the parse error (as 5a) |
| Run while another run is active | 409 "a run is already in progress" |
| Robot program fails to start / disconnects | run ends `error` with the runner's message and console tail |
| Stop pressed | robot program killed, run `stopped`, partial report shown |
| File changed on disk | reload, notice, history cleared (as 5a) |
| Run file request outside the run folder | 404 |

## Testing

- Unit: each scenario edit, with comment and order preservation on the three shipped scenarios; error-to-field
  mapping from real `ScenarioError` messages (bad mode, unknown signal, unknown contact name, bad screenshot
  view, missing config); `signals`; `load_scenario_text`; runner `progress` and `cancel` (with the fast fake robot
  from `tests/fake_robot.toml`).
- Run manager: a passing and a failing fake-robot scenario (new `tests/scenarios/fake_*.toml`), stop mid-run,
  one-at-a-time 409, run files served and path-guarded.
- API tests through the real server (scenario routes, the moved robot routes, old 5a paths returning 404, tab
  switching of the robot).
- Browser tests (Playwright, `browser` marker, as in 5a): open a fake-robot scenario; add a step; set mode and
  duration; set an axis; add a pose check through the field picker; add an `at_end` check from the signal picker;
  run, see progress and a failing check; fix the check; re-run and see it pass; stop a run; open the Robot tab;
  screenshot for the docs.

## Docs

- `docs/scenario-editor.md`: walkthrough, field picker, signal picker, running and reading reports, read-only
  cases.
- README: "Editing and running scenarios in the browser" with a screenshot; `scrimmage edit` documented, and
  every `scrimmage assemble` mention (README, docs/assembler.md) changed to `edit`.
