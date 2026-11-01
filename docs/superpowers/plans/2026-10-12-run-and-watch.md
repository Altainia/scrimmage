# Run and Watch (sub-project 5c) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Drive tab in `scrimmage edit` that runs the robot program live: a Driver Station bar, gamepad or
keyboard driving, pause/step/speed, pokes, live readouts, a live 3D view, recording a drive into a scenario with
input tracks, and watching scenario runs live.

**Architecture:** A `LiveManager` (worker thread + asyncio loop, the `RunManager` pattern) drives one `Session`;
a `SimLock` keeps it and `RunManager` exclusive. A token-guarded `websockets` server (`StreamHub`) pushes binary
pose frames and JSON state and receives inputs and commands. The model view gains "movers" (bodies and movable
geoms) so three.js can animate them. Scenarios gain `inputs = "<file>.csv"` input tracks, written by a
`DriveRecorder` and replayed by the runner.

**Tech Stack:** Python 3.12, MuJoCo, asyncio, `websockets` (already a runtime dependency), tomlkit, stdlib
http.server; ES modules, vendored three.js 0.185.1, the browser Gamepad API; pytest, Playwright 1.63.

**Spec:** docs/superpowers/specs/2026-10-12-run-and-watch-design.md

## Global Constraints

- Everything from 5a/5b still holds: 127.0.0.1 only; `X-Scrimmage-Token` on every `/api/*` (403 otherwise); Host guard (127.0.0.1 or localhost); validate before write, atomic writes; error bodies `{ok: false, errors: [{field, message}]}`; no `innerHTML` for server strings; no CDN; no backward compatibility needed.
- "One robot step" is the config's `[lockstep] step` (`config.step`; 20 ms by default, 5 ms in `tests/fake_robot.toml`). Wherever the spec says 20 ms for stepping, recording or replay, use `config.step`. (Ruling: the spec assumed the default.)
- One simulation at a time: a live session or a scenario run, never both (`SimLock`); the refusal is 409 with "a scenario run is in progress" or "a drive session is live".
- WebSocket auth: first client message `{"token": "<session token>"}` within 5 s; `Origin` must be `http://127.0.0.1:<http port>` or `http://localhost:<http port>`; Host must be 127.0.0.1 or localhost (with the hub's port); otherwise close (code 1008) with nothing sent.
- Binary frame layout (little-endian, packed): `struct "<IBdH"` = `u32 seq, u8 source (0 live, 1 run), f64 t, u16 n`, then `n × 7 float32` = `x, y, z, qw, qx, qy, qz` per mover, in the view's `movers` order. At most one frame per 33 ms of wall time per source. (Ruling: the spec's frame gains the `source` byte so the page can tell live from run frames.)
- JSON state messages at most every 100 ms are full snapshots (latest wins; a slow client may miss intermediate ones); console is the last 200 lines plus a `console_seq` counter.
- Live inputs are rounded to 4 decimals before they reach the robot and the recorder, so a replay sends identical values.
- Input-track CSV: header `t` then `p<port>.axis<i>` (i from 0), `p<port>.button<i>` (i from 1, WPILib numbering), `p<port>.pov<i>` (i from 0); ports 0–5; every row has every column; `t` ≥ 0, strictly increasing, last `t` ≤ the step's duration; axes −1…1, buttons 0/1, POVs −1 or 0–359 integers.
- Safety: a port with no input message for 0.5 s of wall time is zeroed; when the last control socket closes, all ports are zeroed, the robot disabled (mode `disabled`) and the session paused until a page reconnects. (Ruling: "page closed" is detected by the socket closing, not by input silence, so clicking outside the page doesn't disable the robot; the page keeps sending inputs at 50 Hz while connected and clears keyboard state on blur.)
- `scrimmage run` and MCP behavior are unchanged except that scenarios may now use `inputs`.
- Python is `.venv/bin/python`; tests `SCRIMMAGE_SKIP_JAVA=1 .venv/bin/python -m pytest`. Browser tests marked `browser` reuse `tests/test_assembler_browser.py`'s fixtures (Chromium lookup, `Server`, `SCRIMMAGE_REQUIRE_BROWSER`).

## Review Focus

1. Closing the browser tab (socket closes) mid-drive must zero inputs and disable the robot within 0.5 s, and the session must remain stoppable. (Task 4 test with a fake client gone; Task 5 test closing the socket.)
2. A recording replayed must reproduce the robot's received inputs step for step, including buttons, POVs, and an input change on the same step as a mode change. (Task 4 round-trip test.)
3. Ending `scrimmage edit` during a live session, or during its robot start-up, must kill the robot program (no process with its run token survives). (Task 5.)
4. A socket with a wrong token, wrong Origin or wrong Host receives no bytes and its commands have no effect. (Task 5.)
5. Pressing Start and Run at the same moment must never run two simulations: exactly one wins, the other gets 409. (Task 4 race test with threads.)

---

### Task 1: Input tracks (format, loader, runner replay, fake robot driving)

**Files:** Create `scrimmage/tracks.py`, `tests/test_tracks.py`, `tests/scenarios/fake_track.toml`, `tests/scenarios/fake_track/drive.csv`; Modify `scrimmage/scenario.py`, `scrimmage/scenario_runner.py`, `tests/fake_robot.py`, `tests/fake_robot_main.py`, `tests/fake_robot.toml`; Test `tests/test_tracks.py`, `tests/test_runner.py`, `tests/test_scenario_validate.py`

**Interfaces:**
- `tracks.PortInputs(axes: list[float], buttons: list[bool], povs: list[int])` (dataclass, equality by value).
- `tracks.Sample(t: float, ports: dict[int, PortInputs])`.
- `tracks.Track(path: Path, samples: list[Sample])` with `at(elapsed: float) -> int` (index of the latest sample with `t <= elapsed + 1e-9`, or -1).
- `tracks.TrackError(ValueError)`; `parse_track(text: str, path: Path) -> Track`; `load_track(path: Path) -> Track` (file errors as `TrackError(f"file not found: {path}")` etc.); `write_track(path: Path, samples: list[Sample]) -> None` (header from the union of ports and the max widths seen; numbers written with `repr` of the 4-decimal-rounded float, buttons 0/1). Error messages: `f"{path.name}: line {n}: {problem}"` (n is the 1-based CSV line, header = 1).
- `scenario.Step.inputs: Track | None = None`; `STEP_KEYS` gains `"inputs"`. In `load_scenario_raw`, for a step with `inputs`: must be a string (`f"{sw}: inputs must be the path of a .csv file"`); not combined with axes/buttons/povs (`f"{sw}: inputs can't be combined with axes, buttons or povs in the same step"`); loaded relative to the scenario's folder (`f"{sw}: inputs: {TrackError}"`); last `t` > duration → `f"{sw}: inputs: {name}: the last sample (t = {t:g}) is after the step's {dur:g} s"`.
- Runner `_run_step`: with `step.inputs`, after `clear_joysticks()` apply sample `at(0)` (if any) to every port it names via `session.set_joystick(port, axes=, buttons=, povs=)`; in `on_step(t)` compute `k = track.at(t - start)` and apply when `k` changed. Hand-written steps unchanged.
- Fake robot: `FakeRobot(..., drive=False, input_log: Path | None = None)`; `--drive` (on each step request, when the latest DriverStation frame is enabled, send `PWM` devices `"0"` and `"1"` `{"<init": True, "<speed": -axes1}` where `axes1` is port 0's axis 1 (0 when absent); when disabled send speed 0); input log from env `SCRIMMAGE_FAKE_INPUT_LOG` (appends one JSON line per step request: `{"seq", "enabled", "auto", "test", "joysticks": {"<port>": {"axes", "buttons", "povs"}}}` with the latest frames received before that step request). `tests/fake_robot.toml` command gains `"--drive"`.

- [ ] **Step 1: failing tests** in `tests/test_tracks.py`: parse a good CSV (two ports, all three kinds); each error with its line number (bad header name, port 6, axis 1.5, button 2, pov 400, pov 12.5, non-increasing t, negative t, wrong column count, empty file, non-numeric cell); `write_track` → `parse_track` round trip; `at()` boundaries (before first sample → -1, exact t, between samples, after last). In `tests/test_scenario.py`-style tests (put them in `test_tracks.py`): `inputs` resolves relative to the scenario; combination error; last-t error; missing file error. In `tests/test_scenario_validate.py`: a missing CSV maps to field `step<i>/inputs`.
- [ ] **Step 2:** run `SCRIMMAGE_SKIP_JAVA=1 .venv/bin/python -m pytest tests/test_tracks.py tests/test_scenario_validate.py -q` → FAIL (no module).
- [ ] **Step 3:** implement `tracks.py` and the `scenario.py` key; check `scenario_validate._map_load_error` places the messages on `step<i>/inputs` (it uses `_key_in` on the tail, which works when the tail starts with `inputs`; adjust only if a test shows otherwise).
- [ ] **Step 4:** failing runner test in `tests/test_runner.py`: `tests/scenarios/fake_track.toml` (`config = "../fake_robot.toml"`; step "drive", mode "teleop", duration 0.1, `inputs = "fake_track/drive.csv"` with rows at t = 0, 0.02, 0.05 changing p0.axis1, p0.button1 and p0.pov0); run with `SCRIMMAGE_FAKE_INPUT_LOG` set (monkeypatch env) and assert that, for each logged enabled step, the joystick state equals the track sample in effect at that step's start (state the exact one-step alignment you observe in a comment; it must be the same rule for every row). Also: the robot moved (pose x changed) when axis1 = −1 is held, through `--drive`.
- [ ] **Step 5:** implement the runner replay and the fake robot options; run the test → PASS.
- [ ] **Step 6:** full suite `SCRIMMAGE_SKIP_JAVA=1 .venv/bin/python -m pytest -q` (the `--drive` change must not change any existing result); commit `"Scenario input tracks: CSV format, loader, runner replay"`.

---

### Task 2: Movers, model view by mover, Session live helpers

**Files:** Create `scrimmage/movers.py`, `tests/test_movers.py`; Modify `scrimmage/assembler/view.py`, `scrimmage/session.py`, `scrimmage/world.py`; Test `tests/test_movers.py`, `tests/test_assembler_core.py` (view tests), `tests/test_session.py`

**Interfaces:**
- `movers.Mover(kind: Literal["body", "geom"], name: str, id: int)`.
- `movers.movers(world) -> list[Mover]`: every body `b ≥ 1` with `model.body_weldid[b] != 0` in id order (the robot's bodies, free bodies such as game pieces, and their children; `name` from `mj_id2name`, or `f"body{b}"` if unnamed), then the named static geoms that `world.movable_objects()` lists (geoms on the world body), in name order.
- `movers.mover_poses(world, ms: list[Mover]) -> np.ndarray` shape `(n, 7)`, dtype `float32`, rows `x, y, z, qw, qx, qy, qz` (bodies from `data.xpos`/`data.xquat`; geoms from `data.geom_xpos` and `geom_xmat` → quat).
- `view.model_view(world, include_world=False)` adds `"movers": [{"kind", "name"}]`; every robot geom gains `"mover": int` and `"lpos": [3]`, `"lquat": [4]` (pose relative to its mover); every `world_geoms` entry gains `"mover": int` (-1 for static geoms) and `lpos`/`lquat` (identity for -1 and for geom movers). The existing `pos`/`quat` (world, start pose) stay, so the Robot tab and the field picker are unchanged. (Ruling: keeping world poses satisfies "the Robot tab looks unchanged" with no front-end change.) `include_world=True` also adds `"movable": world.movable_objects()`.
- `World.reset_robot()`: every joint whose body is in the robot's tree gets `qpos` from `model.qpos0` and zero `qvel`; then `mj_forward`. (Resets pose and mechanisms; field objects stay.)
- `Session.movers() -> list[Mover]` (cached after start), `Session.mover_poses() -> np.ndarray`, `Session.set_speed(speed: float | None) -> None` (validates like `--speed`: positive finite or None; refuses when `config.step / speed >= 1.0` with the bridge's message as `SessionError`; sets `self.speed` and `self.bridge.speed`), `Session.reset_robot() -> dict` (calls `World.reset_robot`, returns `state()`).

- [ ] **Step 1: failing tests**: movers of `tests/fake_robot.toml`'s world (chassis and its six wheels, in id order; no geoms); movers of a world with a free ball and a named static obstacle (reuse the world from `tests/test_world_pokes.py`): ball body present, obstacle geom last; `mover_poses` equals `data.xpos/xquat` rows; view: for every robot geom, composing mover pose ∘ (lpos, lquat) reproduces `pos`/`quat` to 1e-5; world geoms on the free ball have its mover index; `movable` listed. `World.reset_robot` after `set_robot_pose` and a few steps restores qpos0. `Session.set_speed(0.001)` with step 0.02 raises `SessionError`; `set_speed(None)` ok.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS; full suite.
- [ ] **Step 5:** commit `"Movers: animatable bodies and geoms in the model view; Session live helpers"`.

---

### Task 3: Drive recorder

**Files:** Create `scrimmage/assembler/recorder.py`, `tests/test_recorder.py`

**Interfaces:**
- `RecordingError(ValueError)`.
- `DriveRecorder(step: float)`: `recording: bool`; `start(t: float, mode: str, ports: dict[int, PortInputs])`; `sample(t: float, mode: str, ports: dict[int, PortInputs])` = "from sim time `t` on, the effective mode and inputs are these" (call it at every step boundary while recording; unchanged samples are dropped); `poke() -> None` (counts); `stop(t: float)`; `pokes: int`; `empty: bool`.
- `DriveRecorder.save(path: Path, config: Path, start_pose: tuple[float, float, float], overwrite: bool = False) -> dict` returning `{"path": str, "steps": int, "pokes": int}`. Writes, with `doc.atomic_write` style (temp + rename; see `scrimmage/assembler/doc.py`), a scenario TOML and `<stem>/step<k>.csv` (k from 1) beside it:
  - `# Recorded in the Drive tab on <YYYY-MM-DD HH:MM> from start pose x=<x:.3f>, y=<y:.3f>, heading=<h:.1f}°` comment, `description = "Recorded drive"`, `config = "<relative path from the scenario's folder to config, / separated; absolute if none>"`, one `[[step]]` per effective-mode segment: `name = "<mode> <k>"`, `mode`, `duration` (segment length rounded to a multiple of `step`, as a float with up to 6 decimals), `inputs = "<stem>/step<k>.csv"`. Zero-length segments are skipped.
  - Track rows: `t` relative to the segment start (rounded to 6 decimals); a `t = 0` row with the state at segment start; then a row at each change. A port seen anywhere in the segment is in every row (zeros / False / -1 where it was absent), widths = max seen in the segment.
  - Raises `RecordingError` for: nothing recorded (`"nothing was recorded"`), path not ending in `.toml`, path exists and not `overwrite` (`f"{name} already exists"`), the track folder exists and not `overwrite`.
- Saving the result must load with `load_scenario` (tracks validate).

- [ ] **Step 1: failing tests**: segments cut at mode changes (disabled → teleop → disabled gives 3 steps, durations multiples of step); rows only at changes; a port appearing mid-segment gets zero rows before; `load_scenario` of the saved file gives steps whose `inputs.samples` reproduce what was fed in; overwrite refusal (TOML and folder); relative `config`; poke count in the result; empty recording error.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS.
- [ ] **Step 5:** commit `"Drive recorder: segments to scenario steps with input tracks"`.

---

### Task 4: SimLock and LiveManager

**Files:** Create `scrimmage/assembler/simlock.py`, `scrimmage/assembler/live.py`, `tests/test_live.py`; Modify `scrimmage/assembler/runs.py`; Test `tests/test_live.py`, `tests/test_scenario_runs.py`

**Interfaces:**
- `simlock.SimBusy(Exception)`; `SimLock()` with `acquire(owner: Literal["run", "live"]) -> None` (atomic; raises `SimBusy("a scenario run is in progress")` when "run" holds it, `SimBusy("a drive session is live")` when "live" holds it), `release(owner) -> None` (no-op if not the holder), `holder -> str | None`.
- `RunManager(on_change, runs_dir="runs", simlock: SimLock | None = None, watch: Callable[[object, float], None] | None = None)`: `start` acquires `"run"` (a `SimBusy` is re-raised as `RunBusy(str(e))`), the worker releases it when it settles; `watch(session, t)` is passed to `run_scenario(..., watch=watch)`.
- `scenario_runner.run_scenario(..., watch: Callable[[Session, float], None] | None = None)`: called from `on_step` after every step (both in `_run_step` and wherever the runner advances); no effect when None.
- `live.LiveError(ValueError)`.
- `live.LiveManager(simlock: SimLock, publish: Callable[[str, object], None], *, frame_period=1/30, state_period=0.1, input_timeout=0.5)`:
  - `start(config_path: Path, speed: float | None = 1.0) -> None` — raises `SimBusy`, or `LiveError("a drive session is already live")`; acquires `"live"`; resets the recorder; worker thread runs `asyncio.run(self._main(...))`.
  - `stop() -> None` (also interrupts robot start-up, like `RunManager`), `join(timeout) -> bool`, `kill_robots() -> None` (as `RunManager.kill_robots`).
  - `status() -> dict`: `{"state": "idle"|"starting"|"running"|"paused"|"stopped"|"error", "error": str|None, "config": str|None, "t": float, "mode": str, "enabled": bool, "paused": bool, "speed": float|None, "speed_locked": str|None, "recording": bool, "pokes": int, "can_save": bool}` (`speed_locked` is the Phoenix reason when `bridge.phoenix`).
  - `command(cmd: dict) -> None`: validated synchronously (unknown `cmd`, bad types or ranges → `LiveError(message)`), then queued and applied on the session loop between steps. Commands: `mode {mode: auto|teleop|test}`, `enable`, `disable`, `pause`, `resume`, `step {n: 1..500}` (advances n robot steps, then stays paused), `speed {speed: number|"max"}`, `set_pose {x, y, heading_deg}`, `push {force_n, direction_deg, duration_s}` (applies `world.set_robot_force` and clears it from `on_step` once `duration_s` of sim time has passed — not `Session.push`, so frames and recording continue), `move_body {name, x, y, yaw_deg}`, `reset`, `record_start`, `record_stop`, `signals {names: [≤ 4 strings]}` (resolved with `checks.resolve_signal`; a bad name → a `notice`). Errors raised while applying (e.g. `SessionError`) are published as `("notice", {"kind": "error", "text": msg})`. Pokes while recording call `recorder.poke()`.
  - `input(ports: dict) -> None`: `{"<port>": {"axes": [...], "buttons": [...], "povs": [...]}}`, validated (ports 0–5, ranges as in the CSV rules), rounded to 4 decimals, latest wins per port, stamps each port's wall time.
  - `client_gone() -> None`: zero all ports, disable and pause (the hub calls it when the last control socket closes; the session stays for a reconnect).
  - `save_recording(path: Path, overwrite: bool) -> dict` → `DriveRecorder.save(path, config_path, start_pose, overwrite)`; `LiveError("nothing has been recorded")` when there is no recording. Allowed while live (stops recording first) or after the session ended (stopped or error).
  - Effective mode: `disabled` unless enabled, else the chosen mode (default `teleop`).
  - Main loop: while running and not paused: `await session.advance(0.1, on_step=self._on_step, stop=self._pending)`; when paused, wait on an `asyncio.Event` set (via `loop.call_soon_threadsafe`) by `command`/`stop`; `step {n}` does `advance(n * config.step, ...)`. `_pending()` is true when a command is queued, a pause was asked for, or stop.
  - `_on_step(t)`: zero ports idle for `input_timeout`; apply changed inputs with `session.set_joystick`; clear an expired push force; `recorder.sample(t, effective_mode, ports)` while recording; publish `("frame", bytes)` (layout in Global Constraints, `source=0`) at most every `frame_period` wall seconds and `("state", dict)` at most every `state_period` (`{"source": "live", **status(), "readouts": session.state(), "signals": {name: value}, "console": last 200 lines, "console_seq": int}`).
  - A `SessionError` from advance (crash, disconnect) → state `error` with the message, `console` kept, session stopped, lock released, recorder kept for saving.
  - Also publish a state message on every state transition.

- [ ] **Step 1: failing tests** (`tests/test_live.py`, fake robot via `tests/fake_robot.toml`, `NT_CONNECT_TIMEOUT` shortened as in `tests/test_scenario_runs.py`, publish collected into a list): start → running and frames decode (n = number of movers, t increasing); pause → sim time constant for 0.3 s wall; `step {n: 4}` advances exactly `4 * 0.005`; `speed` change reflected in status; `speed` too small refused; `set_pose` moves the robot; `push` moves it and the force clears; `move_body` unknown name → error notice; `reset`; input watchdog zeroes a port after 0.5 s; `client_gone` disables; `SimLock` both ways (live blocks `RunManager.start` with `RunBusy("a drive session is live")`, a run blocks `start` with `SimBusy`); **race**: two threads call `LiveManager.start` and `RunManager.start` together 10 times, exactly one wins each time; crash (`--exit-after` via a temp copy of `fake_robot.toml` with that argument) → `error` with the traceback in console; stop during start-up leaves no robot process (as in `test_scenario_runs.py`'s `robot_pids`).
- [ ] **Step 2: round-trip test**: with `SCRIMMAGE_FAKE_INPUT_LOG` = log A, start live, wait a few steps, `record_start`, then feed inputs over time including an axis change, a button, a POV, and a `mode`+`enable` change sent together with an input change; `record_stop`; `save_recording`; stop. Run the saved scenario with `run_scenario` and log B. The logged joystick + enabled + mode states from the first recorded step of A equal B's from its first step, for every step of the recording.
- [ ] **Step 3:** run → FAIL. **Step 4:** implement `simlock.py`, the runner `watch` hook, `RunManager` changes, `live.py`. **Step 5:** run → PASS; full suite.
- [ ] **Step 6:** commit `"Live drive sessions: LiveManager and the one-simulation lock"`.

---

### Task 5: Stream hub and server wiring

**Files:** Create `scrimmage/assembler/stream.py`, `tests/test_live_server.py`; Modify `scrimmage/assembler/server.py` (and its API docstring)

**Interfaces:**
- `stream.StreamHub(token: str, http_port: Callable[[], int], on_message: Callable[[dict], None], on_last_client_gone: Callable[[], None])`: `start() -> int` (its own thread + asyncio loop; `websockets` server on 127.0.0.1, port 0; returns the port), `port`, `publish(kind: str, payload) -> None` (thread-safe; `"frame"` bytes, `"state"` / `"notice"` dicts sent as JSON with `"type": kind`; per client, frames and states are latest-wins slots drained by a sender task, notices are queued), `close()`. Auth per Global Constraints (check Host and Origin in the handshake hook of the installed `websockets` version; token as first message). Inbound JSON messages from authed clients go to `on_message`; malformed ones are ignored. `on_last_client_gone` fires when the last authed client disconnects.
- `App` gains `self.simlock`, `self.hub`, `self.live`; `RunManager` gets `simlock` and `watch` (publishes `("frame", ...)` with `source=1` at most every 33 ms and `("state", {"source": "run", "t", "step_name", "mode"})` at most every 100 ms).
- Hub `on_message`: `{"type": "input", "ports": {...}}` → `live.input`; `{"type": "cmd", ...}` → `live.command` (a `LiveError` is sent back to everyone as a notice).
- Routes (token + Host as all `/api/*`):
  - `GET /api/live/info` → `{port, status: live.status(), run_active: bool}`.
  - `GET /api/live/view` → `model_view(world, include_world=True)` of the robot document's world, or `{}` when it doesn't load (cached like `scenario_view`).
  - `GET /api/live/signals` → `signals(world)` of the robot document, or `{}`.
  - `POST /api/live/start {speed?}` → `{ok: true}`; 200 `{ok: false, errors: [{field: "status", message}]}` when the robot is invalid, read-only, has no `[robot_program]` ("this robot config has no [robot_program] section: add one to drive it"), or the speed is bad; 409 on `SimBusy` / already live.
  - `POST /api/live/stop` → `{ok: true}`.
  - `POST /api/live/save_recording {path, overwrite?}` → `{ok: true, path, display_path, steps, pokes}`; `path` relative to the server's working directory (`~` expanded); refused (200 `{ok: false, errors}`) when the resolved path is under neither the working directory nor the robot config's folder, or on `RecordingError` / `LiveError`. An "already exists" refusal uses field `"save/exists"` so the page can offer overwrite.
  - `POST /api/scenario/open {path}` → opens that scenario as the App's scenario document (as if passed to `scrimmage edit`; the robot document follows its `config`); `{ok: true}`; 400 when not a scenario file. Refused with 409 while a run is active.
  - `/api/scenario/state` 404 rule unchanged until a scenario is open; `/api/robot/state` gains `scenario_open` true after `open`.
- `App.stop()`: also `live.stop()`, `live.join(30)` else `live.kill_robots()`, then `hub.close()`.

- [ ] **Step 1: failing tests** (`tests/test_live_server.py`, real `App` on a random port with `tests/fake_robot.toml` copied to tmp, `websockets.sync.client` or asyncio client): `/api/live/info` needs the token; WS with wrong token / wrong Origin / wrong Host gets closed and receives no bytes, and a `cmd` it sent has no effect; authed client: `POST /api/live/start`, frames arrive with increasing `t` and `n == len(view["movers"])`, a state arrives, `{"type": "cmd", "cmd": "pause"}` holds `t`; closing the socket → status shows disabled within 1 s; start while a scenario run is active → 409 (open `tests/scenarios/fake_pass.toml` in a second App, or start the run via `App.runs` directly); a scenario run publishes `source = 1` frames; `save_recording` outside the allowed folders refused, existing file → `save/exists`, success writes files and `POST /api/scenario/open` then serves `/api/scenario/state` for it; `App.stop()` during live start-up leaves no robot process.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement `stream.py` and the server changes; update the API docstring with the new routes and the socket protocol (auth, frame layout, message types). **Step 4:** run → PASS; full suite.
- [ ] **Step 5:** commit `"Live stream: token-guarded WebSocket hub and /api/live routes"`.

---

### Task 6: Input tracks in the Scenario editor (server side)

**Files:** Modify `scrimmage/assembler/scenario_edits.py`, `scrimmage/assembler/server.py`; Test `tests/test_scenario_edits.py`, `tests/test_scenario_server.py`

**Interfaces:**
- `scenario_edits.set_inputs_file(doc, i, path: str | None)`: a string sets `inputs` (inserted after `duration`, comment-aware via `tomlutil`) and removes the step's `axes`, `buttons` and `povs`; `None` removes `inputs`.
- `scenario_edits.trim_track(doc, i, start: float, end: float, scenario_path: Path) -> None`: reads the step's track, keeps `start ≤ t ≤ end` plus a `t = 0` row with the state in effect at `start`, shifts by `-start`, writes it to a **new** file next to the old one (`<old stem>.trim<N>.csv`, the first unused N ≥ 1; never overwrites, so undo of the TOML restores the old track), points `inputs` at it and sets `duration = round(end - start, 6)`; `EditError("step<i>/inputs", ...)` when `0 ≤ start < end ≤ duration` fails or the step has no `inputs`.
- `/api/scenario/edit` ops: `inputs_file {step, path}`, `trim_track {step, start, end}`.
- `/api/scenario/state` steps gain `inputs: str | null`.
- `GET /api/scenario/track?step=i` → `{path, duration, t: [...], series: {"p0.axis1": [...], ...}}` (columns as in the CSV; buttons 0/1), or 404 when the step has no `inputs` or it doesn't load.
- The run snapshot keeps working: tracks are read into memory when the run starts (test that rewriting the CSV after Run doesn't change that run).

- [ ] **Step 1: failing tests**: `set_inputs_file` removes axes/buttons/povs and keeps comments; `None` removes the key; `trim_track` new file name sequence, rows, duration, out-of-range errors; undo after trim restores the old `inputs`; API ops and `/api/scenario/track`; a bad CSV gives a `step<i>/inputs` error in state; a run's snapshot isn't affected by a later CSV change.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS; full suite.
- [ ] **Step 5:** commit `"Scenario editor: input-track edits and track data"`.

---

### Task 7: Front-end foundation: shared 3D builders, live view, stream client, gamepad/keyboard

**Files:** Create `scrimmage/assembler/static/scene3d.js`, `live3d.js`, `livestream.js`, `gamepad.js`, `tests/test_live_frontend.py`; Modify `scrimmage/assembler/static/view3d.js`

**Interfaces:**
- `scene3d.js` exports moved out of `view3d.js` unchanged in behavior: `decode(b64, Type)`, `meshGeometry(m)`, `primitive(type, size)`, `setPose(obj, pos, quat)`, `geomMaterial(rgba, isMesh)`, `buildFloor(f) -> {group, disposables}`. `view3d.js` imports them (no visible change; 5a browser tests still pass).
- `live3d.js`: `export class LiveView { constructor(container); setModel(view); applyFrame(frame); setCamera(mode); resize(); dispose() }`. `setModel` builds a static group (floor, world geoms with `mover == -1`) and one `THREE.Group` per mover holding its geoms at `lpos/lquat` (robot geoms and world geoms). `applyFrame({poses: Float32Array, n})` sets each group's pose (ignored if `n` differs from the mover count). Camera modes: `follow` (behind and above the robot's root mover, yaw-following, smoothed), `top` (straight down over the floor), `driver` (from the blue alliance wall at x = 0 looking +x, height 1.7 m), `free` (OrbitControls). Render on demand (after frames or camera moves). Test hook: `window.__scrimmage.live3d = {groups: count, lastSeq, camera}`.
- `livestream.js`: `export function decodeFrame(buf: ArrayBuffer) -> {seq, source, t, n, poses}`; `export class LiveStream extends EventTarget { constructor(port, token); send(obj); close() }`: connects `ws://<location.hostname>:<port>/`, sends `{token}` first, dispatches `frame` / `state` / `notice` / `open` / `close` events (`detail` = decoded payload), reconnects with backoff 0.5 s → 5 s while not closed.
- `gamepad.js`: `export const KEYBOARD = "keyboard"`; `export function keyboardInputs(pressed: Set<string>) -> {axes: [6], buttons: [10], povs: [-1]}` (W/S axis 1 with W = −1, A/D axis 0 with A = −1, I/K axis 5 with I = −1, J/L axis 4 with J = −1, keys 1–8 → buttons 1–8 i.e. indices 0–7; `KeyboardEvent.code` names: `KeyW`, `Digit1`, …); `export function gamepadInputs(gp: Gamepad) -> {axes, buttons, povs}` (standard mapping → WPILib XboxController: axes `[gp.axes[0], gp.axes[1], buttons[6].value, buttons[7].value, gp.axes[2], gp.axes[3]]`; buttons 1–10 = gamepad buttons `0, 1, 2, 3, 4, 5, 8, 9, 10, 11`; POV from d-pad buttons 12–15 (up 0, right 90, down 180, left 270, diagonals 45/135/225/315, none −1); a non-standard mapping passes `axes` and `buttons` through in browser order with POV −1); `export class InputRouter { assign(port, source); assignments(); poll() -> {"<port>": inputs} }` (sources: `KEYBOARD` or a gamepad index; listens to keydown/keyup on window, clears pressed keys on `blur`; a gamepad that disappears zeroes its port and dispatches a `gamepad-lost` event with the port).

- [ ] **Step 1: failing browser tests** (`tests/test_live_frontend.py`, `browser` marker, a page served by a real App on `tests/fake_robot.toml` but without starting a session): import the modules in `page.evaluate` (`await import('./livestream.js')`) and check `decodeFrame` on a buffer built in JS with the exact layout; `keyboardInputs(new Set(['KeyW','KeyD','Digit3']))` → axis1 −1, axis0 1, button index 2 true; `gamepadInputs` with a fake standard gamepad object (d-pad up+right → POV 45; triggers → axes 2 and 3); `LiveView.setModel` on `/api/live/view` makes one group per mover (via `__scrimmage.live3d.groups`); the Robot tab still renders (5a's browser tests run unchanged).
- [ ] **Step 2:** run `SCRIMMAGE_SKIP_JAVA=1 .venv/bin/python -m pytest -m browser tests/test_live_frontend.py -q` → FAIL. **Step 3:** implement. **Step 4:** run → PASS; run all browser tests.
- [ ] **Step 5:** commit `"Front end: shared 3D builders, live view, stream client, gamepad and keyboard input"`.

---

### Task 8: Drive tab: DS bar, sim controls, live view, readouts

**Files:** Create `scrimmage/assembler/static/drive.js`, `readouts.js`, `tests/test_drive_browser.py`; Modify `static/index.html`, `static/app.css`, `static/app.js`, `static/scenario.js`

**Interfaces / behavior:**
- `index.html`: a third tab button `#tab-btn-drive` ("Drive", always shown) and panel `#tab-drive` with: `#drive-banner` (why Start is disabled, with a Robot tab button), `#drive-ds` (Start/Stop `#drive-start`/`#drive-stop`; mode buttons `[data-key='mode-teleop|auto|test']`; `#drive-enable`, `#drive-disable`; joystick list `#drive-ports` with a source `<select id="drive-port-<p>">` per port 0–5 and live bars), `#drive-sim` (`#drive-pause`, `#drive-step`, `#drive-speed` select 0.25/0.5/1/2/max, a lock note `#drive-speed-lock`), `#drive-view` (the `LiveView` + HUD `#drive-hud`: sim time, mode, Enabled badge, real-time factor; camera select `#drive-camera`), `#drive-readouts` (pose, speed, battery, motor bars, joints, up to 4 signal pickers `#drive-signal-<k>` using `/api/live/signals` with plots over the last 10 s, console tail `#drive-console`), `#drive-status` (state and errors; Restart after `error`).
- `app.js`: `TABS = ["robot", "scenario", "drive"]`; the drive tab registers itself (`registerTab("drive", hooks)`); the top bar's Undo/Redo are disabled on the Drive tab.
- `drive.js`: `initDrive()` — fetches `/api/live/info` and `/api/live/view`, opens `LiveStream(info.port, token)`, wires the DS bar to `{"type": "cmd", ...}` messages, sends `{"type": "input", "ports": router.poll()}` every 20 ms while the socket is open, applies `frame` events with `source == 0` to the `LiveView`, renders `state` (source "live") into HUD, buttons, readouts. Keyboard shortcuts on the Drive tab: Enter = enable, Space = disable (preventDefault; ignored while typing in an input). Start posts `/api/live/start`; a 409/refusal shows its message. Default port assignment: the first connected gamepad on port 0, else the keyboard on port 0. Test hook `window.__scrimmage.live = {state, frames, lastFrame: {t, poses}}`.
- `readouts.js`: `export function renderReadouts(root, state)`; `export class Plot { constructor(canvas, seconds = 10); push(t, value); draw() }`.
- Scenario tab: a "Drive this robot" button in the scenario header switching to the Drive tab.

- [ ] **Step 1: failing browser tests** (`tests/test_drive_browser.py`, `browser` marker; fake robot config copied to tmp so `--drive` is used): open the Drive tab, Start, wait for `__scrimmage.live.state.state == 'running'`; click Teleop and Enable; hold `W` (`page.keyboard.down('w')`) for 1 s of wall time and assert the robot's x in `__scrimmage.live.state.readouts.pose.x` increased by > 0.05 m (fake robot drives forward on −axis1); release; Pause → `t` constant over 0.5 s; Step → `t` increases by exactly 0.005; speed select changes the status speed; a virtual gamepad (add an init script that defines `navigator.getGamepads` returning one standard-mapping pad whose `axes[1] = -1`) assigned to port 0 also drives; Space disables; stop the session; the Drive tab shows the `[robot_program]` banner for a config without one (`configs/kitbot.toml` copied to tmp).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS; all browser tests.
- [ ] **Step 5:** commit `"Drive tab: Driver Station bar, sim controls, live view and readouts"`.

---

### Task 9: Pokes, recording, track plot, watching runs

**Files:** Create `scrimmage/assembler/static/minifield.js`, `trackplot.js`, `tests/test_drive_record_browser.py`; Modify `static/fieldpick.js` (export its field drawing for reuse), `static/drive.js`, `static/step_editor.js`, `static/report.js` or `static/scenario.js` (run panel), `static/index.html`, `static/app.css`

**Interfaces / behavior:**
- `fieldpick.js` exports `fieldGeometry(view) -> {floor, outlines}` and `drawField(ctx, geometry, toCanvas)` (the drawing it already does), used by both the field picker and `minifield.js`; field picker behavior unchanged.
- `minifield.js`: `export class MiniField { constructor(canvas, view); setRobot(x, y, headingDeg); setObjects([{name, x, y}]); onSetPose(cb(x, y, heading)); onMoveObject(cb(name, x, y)) }` — drag from a point sets the pose (drag direction = heading; a click keeps the current heading); dragging a movable object's marker moves it.
- Drive tab poke panel `#drive-pokes`: the mini-field (robot from the latest frame; movable objects from the view's `movable` names and their mover poses); Push form (`#push-force` N, `#push-dir` °, `#push-duration` s, `#push-go`); `#drive-reset`. Each sends the matching `cmd`.
- Record: `#drive-record` toggle (`record_start` / `record_stop`; shows "● REC" and the poke count); `#drive-save` opens a dialog `#save-dialog` with `#save-path` (default `scenarios/drive-<YYYYMMDD-HHMM>.toml`), a poke warning ("N pokes happened while recording and are not in the scenario") and Save; a `save/exists` error shows "Overwrite?" with `#save-overwrite`; success posts `/api/scenario/open {path}` and switches to the Scenario tab.
- `app.js` / `scenario.js`: the Scenario tab can be registered after page load (when `/api/robot/state` says `scenario_open` becomes true, call `initScenario()` once).
- `trackplot.js`: `export async function trackPanel(stepIndex, step) -> HTMLElement` — fetches `/api/scenario/track?step=i`, draws axis traces (one color per column) and button lanes on a canvas, with `#trim-start`, `#trim-end` and "Trim" (`trim_track` op), "Replace with constant inputs" (`inputs_file` with `null`) and "Choose file…" (a text input with the path, `inputs_file`). `step_editor.js` shows it instead of the constant-input editor when `step.inputs` is set.
- Run panel: a `#run-watch` toggle while a run is `running` shows a `LiveView` (model from `/api/scenario/view`) fed by `frame` events with `source == 1`; hidden when the run ends.

- [ ] **Step 1: failing browser tests** (`tests/test_drive_record_browser.py`): set pose by dragging on the mini-field → the robot pose in `__scrimmage.live.state` is near the drop point; Reset returns it; record ~1 s of driving with W held and a mode change, Save to `scenarios/rec.toml` in tmp, the Scenario tab opens with steps whose `inputs` are set and a track canvas visible; Trim the first step and see the duration change; Run → passes (it has no checks) and the run panel's Watch shows frames (`__scrimmage.live3d.lastSeq` increases); saving again to the same path shows Overwrite.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS; all browser tests.
- [ ] **Step 5:** commit `"Drive tab: pokes, recording to scenarios, track plots, watching runs"`.

---

### Task 10: Docs and screenshots

**Files:** Create `docs/drive.md`, `docs/img/drive.png`; Modify `docs/scenario-editor.md`, `README.md`, `tests/test_drive_browser.py` (opt-in screenshot test, as 5b's `SCRIMMAGE_README_SHOT`)

- [ ] **Step 1:** `docs/drive.md`: starting a session; DS bar and keyboard shortcuts; the keyboard map table and the gamepad → XboxController table (from `gamepad.js`); sim controls (Phoenix 6 lock); pokes; readouts and signals; recording and saving; the input-track CSV format (header, numbering, ranges, hold-after-last-row, one robot step alignment); watching scenario runs; safety (inputs zeroed after 0.5 s, disabled when the page closes); one simulation at a time.
- [ ] **Step 2:** `docs/scenario-editor.md`: the `inputs` key and the track panel (trim writes a new file; undo). README: "Driving your robot in the browser" with `docs/img/drive.png`.
- [ ] **Step 3:** the opt-in screenshot test (`SCRIMMAGE_README_SHOT=docs/img/drive.png`) drives the fake robot for a second and saves the page; run it; check the image shows no username or home path.
- [ ] **Step 4:** full suite, including browser tests: `SCRIMMAGE_SKIP_JAVA=1 SCRIMMAGE_REQUIRE_BROWSER=1 .venv/bin/python -m pytest -q`.
- [ ] **Step 5:** commit `"Drive tab docs and screenshot"`.
