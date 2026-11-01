# Run and watch (sub-project 5c): design

## Context

Sub-projects 5a and 5b built `scrimmage edit FILE`: a local web app (`scrimmage/assembler/`, stdlib
`ThreadingHTTPServer` on 127.0.0.1 with an `X-Scrimmage-Token` header and a Host guard, tomlkit documents with
validate-before-write, a no-build JS front end with vendored three.js 0.185.1) with a Robot tab (assembler) and a
Scenario tab (edit and run scenarios headless with a report; `RunManager` runs one scenario at a time).

`Session` (`scrimmage/session.py`) already drives a robot program: `start`, `advance(seconds, on_step, stop)`,
`set_mode`, `set_joystick`, `set_pose`, `push`, `move_body`, `state`, `console`. A held session keeps the robot
program paused between `advance` calls with keep-alives (so pausing has no 2 s watchdog problem). Robots with
Phoenix 6 devices are paced at 1x.

Decisions made with the user:

- Run-and-watch covers **both** a Driver Station style live session driven from the browser (gamepad or
  keyboard) and watching scenario runs live, in one 3D view.
- Extras: pause / step / speed, poking the world, live readouts, and **recording a drive to a scenario**.
- Recordings use **input tracks**: a scenario step may hold time-stamped joystick samples in a CSV.
- Approach A: an in-process live session in the edit server, streaming over a token-guarded WebSocket; the page
  animates the existing three.js geometry by body poses.

## Goals

1. A student opens the Drive tab, starts their robot program, picks a mode, enables, and drives with a gamepad or
   the keyboard while watching the robot move in 3D in real time.
2. Pause, single-step and change speed; set the robot pose, push it, move game pieces, reset.
3. See live readouts (pose, speed, battery, motors, joints, up to 4 plotted signals, console).
4. Record a drive and save it as a scenario that replays the same inputs step for step and can be edited and
   re-run in the Scenario tab.
5. Watch a scenario run live from the Scenario tab.

## Non-goals

- Remote (non-localhost) driving, multiple viewers controlling one session, more than one simulation at a time.
- Replaying pokes (set pose, push, move) from recordings.
- Server-side video streaming.
- Gamepads without the browser's "standard" mapping get raw axes/buttons in browser order; no remapping UI.
- Windows/macOS.

## Design

### What the student sees (Drive tab)

- A third tab, **Drive** (`#drive`), for the open robot; the Scenario tab has "Drive this robot".
  **Start** launches the robot program from `[robot_program]` (as `scrimmage run` does) with start-up
  progress; **Stop** ends it.
- **Center:** the live 3D view: the same geometry as the Robot tab plus field elements and game pieces, moved
  every frame. Cameras: follow, top, driver station (behind the blue alliance wall), free orbit. HUD: sim time,
  effective mode, an Enabled badge, real-time factor.
- **Driver Station bar (top):** mode (Teleop / Auto / Test) and Enable / Disable; the effective scenario mode is
  `disabled` when not enabled, otherwise the chosen mode. Enter enables, Space disables. A joystick list for
  ports 0–5: each browser gamepad (Gamepad API "standard" mapping, mapped to WPILib `XboxController` axis and
  button indices) or the keyboard can be assigned to a port, with live axis and button bars. Keyboard preset:
  W/S and A/D for the left stick (axes 1 and 0, W = −1 on axis 1 as on a real stick), I/K and J/L for the right
  stick (axes 5 and 4), keys 1–8 for buttons 1–8. No deadband is applied (robot code does its own).
- **Sim controls:** Pause / Resume, Step (one robot step: the config's `[lockstep] step`, 20 ms by default), speed 0.25×, 0.5×, 1×, 2×, max. Phoenix 6
  robots lock speed at 1× with the reason shown; Pause still works. While paused, inputs are held.
- **Poke tools:** set pose by dragging on a top-down mini-field (the 5b field picker component); Push (direction,
  force, duration); move a game piece or free body by dragging on the mini-field; Reset robot to its start pose.
- **Readouts (right):** pose, speed, battery; motor command and current bars; joint positions; up to 4 signals
  chosen with the 5b signal picker (including `nt:` / `hal:`) plotted over the last 10 s; console tail.
- **Record:** a Record toggle (recording starts at the next step); then "Save as scenario…" (file name; default
  folder `scenarios/` next to the robot config, created if needed), which opens the saved scenario in the Scenario
  tab.
- **Watch:** while a scenario run is going, the Scenario tab's run panel has a **Watch** toggle that shows the
  same live view with the Drive controls hidden.

### Architecture

Python (`scrimmage/assembler/`):

- `simlock.py` — `SimLock`: one simulation at a time across `LiveManager` and `RunManager`; `acquire(owner)`
  raises `SimBusy(message)` with "a scenario run is in progress" or "a drive session is live".
- `live.py` — `LiveManager`: owns one `Session` on a worker thread with its own asyncio loop (the `RunManager`
  pattern). States `idle → starting → running | paused → stopped | error`. While running it loops
  `session.advance(0.1, on_step=publish, stop=pending)` where `pending()` is true when paused or a command is
  queued. Commands from the page are queued (thread-safe) and applied on the session's loop between steps:
  `mode`, `enable`, `disable`, `joystick(port, axes, buttons, povs)`, `pause`, `resume`, `step(n)`, `speed(x)`,
  `set_pose(x, y, heading_deg)`, `push(force_n, direction_deg, duration_s)`, `move_body(name, x, y, yaw_deg)`,
  `reset`, `record_start`, `record_stop`, `signals(list)`. Joystick input is latest-wins per port; an input
  watchdog zeroes a port that hears nothing for 0.5 s of wall time and disables the robot when every input
  source has gone quiet (page closed). Speed changes go through the session's pacer (`Session.set_speed`, new,
  validated like `--speed`).
- `stream.py` — `StreamHub`: a `websockets` server on 127.0.0.1, random port, started with the app; the page gets
  the port from `GET /api/live/info`. Auth: the first message must be `{"token": ...}` matching the session
  token, the `Origin` header must equal the app's own origin and the Host is checked as in 5a; otherwise the
  connection is closed with no data sent. Outbound: binary **frames** at most every 33 ms of wall time
  (little-endian: `u32 seq`, `u8 source` (0 live, 1 run), `f64 t`, `u16 nbodies`, then per body `f32 x, y, z, qw, qx, qy, qz`, in the order
  of the view's body list) and JSON **state** messages about every 100 ms (`source: "live" | "run"`, mode,
  enabled, paused, speed, real-time factor, the `Session.state()` readouts, picked signal values, new console
  lines, recording status and poke count). Inbound JSON: `{"type": "input", "ports": {...}}` (50 Hz while the
  page has focus) and `{"type": "cmd", ...}`.
- `recorder.py` — `DriveRecorder`: at each robot step while recording, samples the effective mode and every
  port's inputs; cuts a step at every effective-mode change; counts pokes; `save(path)` writes the scenario and
  its tracks (format below).
- `view.py` — the model view is regrouped by body: a `bodies` list (robot bodies and free world bodies such as
  game pieces, with name, instance and start pose) and, per geom, its body index and pose relative to the body.
  The Robot tab composes start poses so it looks unchanged; the Drive view applies frames to body groups.
- `runs.py` — `RunManager` takes the `SimLock` and publishes frames and state (`source: "run"`) to the hub from
  its progress hook.
- `server.py` routes: `POST /api/live/start` (the Robot tab's config, or `{config}`), `POST /api/live/stop`,
  `GET /api/live/info` (`{port, state, error?}`), `POST /api/live/save_recording {path, overwrite?}`.
  Everything else goes over the socket.

Core:

- `Session.body_poses() -> (names, float32 array (n, 7))` for the bodies in the view's list, and
  `Session.set_speed(speed)`.
- Scenario input tracks (below) in `scenario.py` and `scenario_runner.py`.

Front end (`static/`): `drive.js` (tab, DS bar, sim controls, poke tools, record), `livestream.js` (socket, auth,
reconnect, frame decode), `gamepad.js` (Gamepad API and keyboard to WPILib ports), `readouts.js` (bars and a
small canvas plotter), `trackplot.js` (Scenario tab track plot); `view3d.js` gains body groups, frame
application and camera modes. Test hook: `window.__scrimmage.live` (latest state, frame count, body poses).

### Input tracks (scenario format)

- New step key `inputs = "<path>.csv"`, relative to the scenario file. A step with `inputs` may not also have
  `axes`, `buttons` or `povs` (validation error naming the step).
- CSV: a header row, then one row per sample. Columns: `t` (seconds from step start, first ≥ 0, strictly
  increasing), then any of `p<port>.axis<i>` (−1…1), `p<port>.button<i>` (0 or 1, buttons numbered from 1 as in
  WPILib), `p<port>.pov<i>` (−1 or 0–359), ports 0–5. Only used ports and indices appear.
- Validation in `load_scenario_text`: file exists and parses, well-formed headers, values in range, `t`
  increasing, last `t` ≤ the step's `duration`. Errors carry the CSV line number and map to `step<i>/inputs`.
- Runner: at step start apply the latest row with `t ≤ 0` (all inputs zero if none), and in `on_step` apply the
  latest row with `t ≤` elapsed step time (zero-order hold; after the last row the inputs are held). Recorded
  rows lie on robot-step boundaries, so a lockstep replay sends the same inputs at the same steps as the drive.
  In real-time mode (Phoenix 6) replay is as faithful as the wall clock allows.
- Recording output: `NAME.toml` plus `NAME/step<k>.csv`; one step per effective-mode segment named
  `<mode> <k>`, duration = segment length (a multiple of the robot step); disabled segments are kept with their inputs; a
  row is written only when some input changed (plus the `t = 0` row); `config` points at the driven robot
  (relative path); a comment records the start pose. If pokes happened while recording, the save response and
  dialog say "N pokes happened while recording and are not in the scenario".
- Scenario tab (5b): a step with `inputs` shows a read-only plot (axis traces, button lanes) with "Trim start /
  end" (rewrites the CSV and the duration), "Replace with constant inputs" (removes the key; the CSV stays on
  disk) and "Choose file…"; constant-input editing is hidden for that step. New `scenario_edits` ops:
  `set_inputs_file(i, path | None)`, `trim_track(i, start, end)`.
- `scrimmage run` and MCP replay tracks unchanged otherwise.

## Failure handling

| Situation | Behavior |
|---|---|
| Robot config invalid or without `[robot_program]` | Start disabled with the reason and a Robot tab link |
| Robot program fails to start / agent missing | state `error` with the session's message and console tail |
| Robot program crashes or disconnects mid-drive | `error` at sim time t, last frame kept, console tail, Restart; a recording in progress can still be saved up to that point |
| Start while a scenario run is active (or Run while live) | 409 with the `SimBusy` message |
| Page closed or socket dropped | after 0.5 s inputs zeroed and robot disabled; session stays, paused; the page shows it again on reconnect; Stop or ending `scrimmage edit` kills it |
| No gamepad, or unplugged mid-drive | keyboard offered; on unplug the port's inputs zero at once with a notice |
| Phoenix 6 robot | speed locked at 1× with the reason; pause works |
| Bad socket token, Origin or Host | connection closed, no data sent |
| Bad or missing track CSV | scenario validation error at `step<i>/inputs` with the line number (read-only as in 5b) |
| Save recording over an existing file | refused unless `overwrite` is confirmed in the dialog; paths outside both the folder `scrimmage edit` was started in and the robot config's folder are refused |
| Command arrives mid-advance | queued, applied at the next step boundary |

## Testing

- Unit: track CSV parse/validation (each error with line number); runner track replay (fake robot logs received
  inputs per step, compared with the CSV); recorder segments, durations, CSV contents, poke count; `view.py`
  by-body grouping (geom world poses composed from body and local poses equal the old output); `body_poses`;
  `set_speed`; scenario edits `set_inputs_file`, `trim_track`.
- `LiveManager` with the fake robot: start/stop; pause holds sim time; `step(n)` advances exactly n robot steps;
  speed change; set_pose, push, move_body, reset; input watchdog; `SimLock` exclusion both ways; crash → `error`;
  **round trip**: record, save, run the saved scenario with `scenario_runner`, and the inputs received per step
  match the recording.
- Stream (real server, `websockets` client): auth rejection (token, Origin, Host); frames decode to the body count
  with increasing sim time; state messages; commands applied; a scenario run publishes `source: "run"`.
- Browser (Playwright, `browser` marker): open the Drive tab on a fake-robot config; Start; enable teleop; hold W
  and see the pose in `__scrimmage.live` change (keyboard); a virtual gamepad via an injected
  `navigator.getGamepads` stub; Pause (sim time constant) and Step one robot step; set pose on the mini-field; record,
  save as scenario, see the track plot in the Scenario tab, Run and pass; Watch a scenario run; screenshot for
  the docs.

## Docs

- `docs/drive.md`: walkthrough, keyboard and gamepad mapping, sim controls, poke tools, readouts, recording,
  the input-track CSV format.
- `docs/scenario-editor.md`: the `inputs` key and the track plot.
- README: "Driving your robot in the browser" with a screenshot.
